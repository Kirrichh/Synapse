"""Sole governed-replay adapter over the protected CognitiveVM core."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json

from synapse.bytecode import BytecodeProgram
from synapse.cvm import (
    GAS_BACK_EDGE,
    GAS_COSTS,
    HOST_ABI_VERSION,
    CognitiveVM,
    FunctionObject,
    PendingHostCall,
    VMState,
    VMStatus,
    compute_call_id,
    compute_message_consumed_id,
    decode_vm_value,
    encode_vm_value,
)

from . import replay_vm_codec as _vm_codec
from .activities import ActivityKind, ActivityPosition
from .replay import (
    DISPATCH_GUARDED_OPCODES,
    RECORDED_ONLY_OPCODES,
    REPLAY_ADMISSIBLE_OPCODES,
    REPLAY_CAPABILITY_PROFILE_V1_E1,
    REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    REPLAY_RECORDED_STRUCTURAL_EFFECT_OPCODES,
    RecordedActivityChannelPort,
    ReplayFailureCode, ReplayViolation,
    ReplayMachineExecutionContext,
    ReplayMachinePort,
    _fail,
    _is_sealed_activity_channel,
    _natural,
    activity_inputs,
    activity_kind_for_opcode,
    capability_profile_digest,
    require_replay_machine_execution_context,
)
from .replay_structural_history import (
    ReplayStructuralCommand,
    ReplayStructuralHistory,
    StructuralHistoryViolation,
)


_EVENT_ID_PROFILE = b"synapse.stage4.gold.replay-machine-event/v1\x00"
_BACK_EDGE_OPCODES = frozenset({"JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE"})
_STATIC_MEMBER_MISSING = object()
_CALL_HOST_BUILTINS = frozenset({"print", "len", "str", "int", "float", "bool", "range", "abs", "assert_fail"})
_STRUCTURAL_OPCODE = {
    "CONTEXT_ENTER": ("SYS_CONTEXT_ENTER", "context", "enter", False),
    "CONTEXT_EXIT": ("SYS_CONTEXT_EXIT", "context", "exit", False),
    "ACTOR_ENTER": ("SYS_ACTOR_ENTER", "actor", "enter", True),
    "ACTOR_EXIT": ("SYS_ACTOR_EXIT", "actor", "exit", False),
    "POLICY_ENTER": ("SYS_POLICY_ENTER", "policy", "enter", True),
    "POLICY_EXIT": ("SYS_POLICY_EXIT", "policy", "exit", False),
    "POLICY_RULE_ENTER": ("SYS_POLICY_RULE_ENTER", "policy_rule", "enter", True),
    "POLICY_RULE_EXIT": ("SYS_POLICY_RULE_EXIT", "policy_rule", "exit", False),
}
_PENDING_HOST_CALL_FIELDS = frozenset("""pending_schema_version status call_id symbol args argc ip_after_call
program_hash transition_hash_at_call frame_depth_at_call agent_id required_capabilities host_abi_version
created_at_event_id determinism_class deterministic_call_id""".split())
_PENDING_MESSAGE_FIELDS = frozenset("""pending_schema_version status message_receive_id receiver_id
sender_var target_var created_at_event_id transition_hash_at_receive""".split())


def _execution_context_payload(context: ReplayMachineExecutionContext) -> dict[str, object]:
    context = require_replay_machine_execution_context(context)
    return {
        "run_id": context.run_id.to_dict(),
        "attempt_id": context.attempt_id.to_dict(),
        "repository_revision": context.repository_revision.to_dict(),
        "environment_profile_id": context.environment_profile_id,
        "policy_version": context.policy_version,
    }


def _deterministic_event_id(context: ReplayMachineExecutionContext, *, program_hash: str,
                            instruction_pointer: int, transition_hash: str,
                            frame_depth: int) -> str:
    if type(program_hash) is not str or not program_hash:
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "event identity lacks a program hash")
    if not _vm_codec.is_transition_id(transition_hash):
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "event identity lacks a transition hash")
    _natural(instruction_pointer, "instruction_pointer", maximum=2**53)
    _natural(frame_depth, "frame_depth", maximum=2**53)
    payload = {
        "execution_context": _execution_context_payload(context),
        "program_hash": program_hash,
        "instruction_pointer": instruction_pointer,
        "transition_hash": transition_hash,
        "frame_depth": frame_depth,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "evt--" + hashlib.sha256(_EVENT_ID_PROFILE + canonical).hexdigest()


@dataclasses.dataclass(frozen=True)
class _ValidatedPendingLLMCall:
    envelope: dict[str, object]
    call_id: str
    arguments: list[object]
    origin_ip: int
    frame_depth: int
    event_id: str


@dataclasses.dataclass(frozen=True)
class _ValidatedPendingMessage:
    envelope: dict[str, object]
    receive_id: str
    receiver_id: str
    sender_var: str
    target_var: str
    origin_ip: int
    event_id: str


@dataclasses.dataclass(frozen=True)
class _ResolvedActivity:
    route: str
    operand_a: bytes
    operand_b: bytes
    result: object


@dataclasses.dataclass(frozen=True)
class _StructuralExpectation:
    command: ReplayStructuralCommand
    operand_a: bytes


class CognitiveVMReplayAdapter:
    __slots__ = (
        "_vm",
        "_execution_context",
        "_channel",
        "_sequence",
        "_executing_ip",
        "_executing_opcode",
        "_activity",
        "_activity_consumed",
        "_structural",
        "_structural_cursor",
        "_structural_mismatch",
        "_structural_history",
    )

    def __init__(
        self,
        program: BytecodeProgram,
        *,
        gas_budget: int,
        execution_context: ReplayMachineExecutionContext,
        _state: VMState | None = None,
        _halted: bool = False,
        _activity_sequence: int = 0,
        expected_structural_history: bytes | None = None,
        _resolved_structural_history: bytes | None = None,
    ) -> None:
        if type(program) is not BytecodeProgram:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact BytecodeProgram is required")
        _vm_codec.require_canonical_vm_value(program.constants, field="program constants", data_only=True)
        _natural(gas_budget, "gas_budget", maximum=_vm_codec.MAX_SAFE_INTEGER)
        context = require_replay_machine_execution_context(execution_context)
        state = _state if _state is not None else VMState(gas_remaining=gas_budget)
        _vm_codec.require_canonical_vm_state(state, program=program)
        if type(_halted) is not bool:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot halted flag must be exact")
        self._vm = CognitiveVM(program, state)
        self._vm.halted = _halted
        self._execution_context = context
        self._channel: RecordedActivityChannelPort | None = None
        _natural(_activity_sequence, "activity_sequence", maximum=_vm_codec.MAX_SAFE_INTEGER)
        self._sequence = _activity_sequence
        self._executing_ip: int | None = None
        self._executing_opcode: str | None = None
        self._activity: _ResolvedActivity | None = None
        self._activity_consumed = False
        self._structural: tuple[_StructuralExpectation, ...] | None = None
        self._structural_cursor = 0
        self._structural_mismatch = False
        try:
            self._structural_history = ReplayStructuralHistory(
                profile_id=REPLAY_CAPABILITY_PROFILE_V1_E1, profile_digest=capability_profile_digest(),
                expected_bytes=expected_structural_history,
                resolved_bytes=_resolved_structural_history)
        except StructuralHistoryViolation as exc:
            raise _fail(
                ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                "expected structural history is invalid",
            ) from exc
        self._vm.host = self._host

    def attach_channel(self, channel: RecordedActivityChannelPort) -> None:
        if self._channel is not None:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a replay channel is already attached")
        if not _is_sealed_activity_channel(channel):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact channel is required")
        self._channel = channel

    def program_hash(self) -> str:
        return self._vm.program.program_hash

    def host_abi_version(self) -> str:
        return str(self._vm.program.host_abi_version)

    def transition_hash(self) -> str:
        return self._vm.state.transition_hash

    def gas_remaining(self) -> int:
        return int(self._vm.state.gas_remaining)

    def is_halted(self) -> bool:
        if (
            self._vm.state.pending_host_call is not None
            or self._vm.state.pending_message_receive is not None
        ):
            return False
        return bool(self._vm.halted)

    def next_opcode(self) -> str | None:
        if self._vm.state.pending_host_call is not None:
            self._pending_llm_call()
            return "LLM_RESUME"
        if self._vm.state.pending_message_receive is not None:
            self._pending_message()
            return "MSG_RECEIVE"
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            return None
        opcode = instructions[index].op
        if type(opcode) is not str:
            raise _fail(ReplayFailureCode.NON_CANONICAL_VM_VALUE, "instruction opcode is not exact")
        return opcode

    def next_step_gas_cost(self) -> int:
        if self._vm.state.pending_host_call is not None or self._vm.state.pending_message_receive is not None:
            self.next_opcode()
            return 0
        opcode = self.next_opcode()
        if opcode is None:
            return 0
        cost = GAS_COSTS.get(opcode, 1)
        if opcode in _BACK_EDGE_OPCODES and self._is_back_edge():
            cost += GAS_BACK_EDGE
        return cost

    def machine_snapshot(self) -> dict[str, object]:
        _vm_codec.require_canonical_vm_state(self._vm.state, program=self._vm.program)
        snapshot = self._vm.snapshot()
        if type(snapshot) is not dict or set(snapshot) != {"program", "state", "halted"}:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "CVM produced a malformed snapshot")
        return snapshot

    def snapshot_bytes(self) -> bytes:
        return _vm_codec.encode_adapter_snapshot(
            activity_sequence=self._sequence,
            machine=self.machine_snapshot(),
            resolved_structural_history=self.structural_history_bytes(),
        )

    def snapshot_digest(self) -> str:
        return _vm_codec.digest_adapter_snapshot(self.snapshot_bytes())

    def structural_history_bytes(self) -> bytes:
        return self._structural_history.canonical_bytes()

    def structural_history_complete(self) -> bool:
        return self._structural_history.is_complete()

    def step(self) -> None:
        pending_host = self._vm.state.pending_host_call is not None
        if pending_host or self._vm.state.pending_message_receive is not None:
            if pending_host:
                self._pending_llm_call()
            else:
                self._pending_message()
            _vm_codec.require_canonical_vm_state(self._vm.state, program=self._vm.program)
            checkpoint, was_halted = copy.deepcopy(self._vm.state), self._vm.halted
            try:
                if pending_host:
                    self._complete_pending_llm_request(checkpoint)
                else:
                    self._complete_pending_message_receive(checkpoint)
                _vm_codec.require_canonical_vm_state(self._vm.state, program=self._vm.program)
            except Exception:
                self._vm.state, self._vm.halted = checkpoint, was_halted
                raise
            return
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            self._step_protected_core()
            return
        opcode = self.next_opcode()
        assert opcode is not None
        self._executing_ip = index
        self._executing_opcode = opcode
        self._activity = None
        self._activity_consumed = False
        self._structural = None
        self._structural_cursor = 0
        self._structural_mismatch = False
        checkpoint: VMState | None = None
        was_halted = self._vm.halted
        structural_checkpoint: int | None = None
        try:
            if opcode in DISPATCH_GUARDED_OPCODES:
                self._prepare_dispatch(instructions[index], resolve=False)
            _vm_codec.require_canonical_vm_state(self._vm.state, program=self._vm.program)
            checkpoint = copy.deepcopy(self._vm.state)
            structural_checkpoint = self._structural_history.checkpoint()
            self._preflight_transition(instructions[index])
            self._step_protected_core()
            _vm_codec.require_canonical_vm_state(self._vm.state, program=self._vm.program)
            self._finish_transition(checkpoint)
        except Exception:
            if checkpoint is not None:
                self._vm.state, self._vm.halted = checkpoint, was_halted
            if structural_checkpoint is not None:
                self._structural_history.rollback(structural_checkpoint)
            raise
        finally:
            self._executing_ip = None
            self._executing_opcode = None
            self._activity = None
            self._structural = None

    def _step_protected_core(self) -> None:
        """Translate only an actual CognitiveVM transition crash to machine fault."""

        try:
            self._vm.step()
        except ReplayViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - this is the protected-core boundary
            raise _fail(
                ReplayFailureCode.MACHINE_EXECUTION_FAULT,
                "CognitiveVM raised while executing an admitted transition",
            ) from exc

    def _is_back_edge(self) -> bool:
        program = self._vm.program
        state = self._vm.state
        index = state.ip
        if index < 0 or index >= len(program.instructions):
            return False
        instruction = program.instructions[index]
        target = instruction.a
        if type(target) is not int or isinstance(target, bool) or target > index:
            return False
        opcode = instruction.op
        if opcode == "JUMP":
            return True
        if not state.stack:
            return False
        condition = state.stack[-1]
        _vm_codec.require_canonical_vm_value(condition, field="branch condition", program=program)
        return bool(not condition if opcode == "JUMP_IF_FALSE" else condition)
    def _preflight_transition(self, instruction: object) -> None:
        opcode = getattr(instruction, "op", None)
        if type(opcode) is not str:
            raise _fail(ReplayFailureCode.OPCODE_NOT_CLASSIFIED, "instruction opcode is not exact")
        if opcode in REPLAY_RECORDED_STRUCTURAL_EFFECT_OPCODES or opcode == "RETURN":
            self._prepare_structural(instruction)
            return
        if opcode == "MEMBER":
            self._prepare_member(instruction)
            return
        if opcode in REPLAY_ADMISSIBLE_OPCODES:
            return
        if opcode in DISPATCH_GUARDED_OPCODES:
            self._prepare_dispatch(instruction)
            return
        if opcode not in RECORDED_ONLY_OPCODES:
            raise _fail(ReplayFailureCode.OPCODE_NOT_CLASSIFIED, "opcode has no replay class")
        if opcode == "LLM_REQUEST" or (
            opcode == "MSG_RECEIVE" and not self._vm.state.mailbox_inbound
        ):
            return
        if opcode == "LLM_RESUME":
            raise _fail(ReplayFailureCode.INJECTION_PRIMITIVE_MISSING,
                        f"{opcode} has no safe recorded-result injection primitive")
        if opcode == "CALL_HOST":
            self._prepare_call_host(instruction)
        elif opcode == "MSG_SEND":
            self._prepare_msg_send()
        elif opcode == "MSG_RECEIVE":
            self._prepare_msg_receive(instruction)
        else:
            self._cache_activity(opcode, opcode, instruction.a, instruction.b)
    def _finish_transition(self, before: VMState) -> None:
        opcode, state = self._executing_opcode, self._vm.state
        if opcode in {"LLM_REQUEST", "MSG_RECEIVE"}:
            common = (
                state.ip == before.ip + 1
                and state.gas_remaining == before.gas_remaining - GAS_COSTS[opcode]
                and _vm_codec.is_transition_id(state.transition_hash)
                and state.transition_hash != before.transition_hash
            )
            if opcode == "LLM_REQUEST":
                self._pending_llm_call()
                valid = before.stack and state.stack == before.stack[:-1] and common and self._state_unchanged_except(
                    before, {"ip", "gas_remaining", "stack", "pending_host_call", "transition_hash"})
            elif self._activity is None:
                self._pending_message()
                valid = common and self._state_unchanged_except(
                    before, {"ip", "gas_remaining", "pending_message_receive", "transition_hash"})
            else:
                message = before.mailbox_inbound[0]
                expected_locals = dict(before.locals)
                sender_var = str(self._vm.program.instructions[before.ip].a)
                expected_locals[sender_var] = message.get(
                    "sender_id", message.get("sender")
                )
                expected_locals[str(self._vm.program.instructions[before.ip].b)] = message
                valid = (
                    common and not self._vm.halted and state.pending_message_receive is None
                    and state.mailbox_inbound == before.mailbox_inbound[1:]
                    and state.stack == before.stack + [message] and state.locals == expected_locals
                    and self._state_unchanged_except(before, {"ip", "gas_remaining", "stack", "locals",
                        "mailbox_inbound", "transition_hash"})
                )
            if not valid:
                raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, f"{opcode} successor differs")
        if type(self._activity_consumed) is ReplayFailureCode:
            raise _fail(self._activity_consumed, "CVM suppressed an activity-boundary violation")
        if self._activity is not None and not self._activity_consumed:
            raise _fail(ReplayFailureCode.INJECTION_PRIMITIVE_MISSING, "CVM did not invoke activity injection")
        if self._structural is not None and (
            self._structural_mismatch or self._structural_cursor != len(self._structural)
        ):
            raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH, "CVM structural callbacks differ")
    def _stack_arguments(self, argc: object, *, trailing: int = 0) -> list[object]:
        if type(argc) is not int or isinstance(argc, bool) or argc < 0:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "dispatch argc must be a natural number")
        stack = self._vm.state.stack
        if len(stack) < argc + trailing:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "dispatch stack does not contain its arguments")
        end = len(stack) - trailing if trailing else len(stack)
        arguments = list(stack[end - argc:end]) if argc else []
        _vm_codec.require_canonical_vm_value(arguments, field="dispatch arguments", program=self._vm.program)
        return arguments
    def _prepare_member(self, instruction: object) -> None:
        if not self._vm.state.stack:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "MEMBER stack lacks its subject")
        subject, name = self._vm.state.stack[-1], instruction.a
        _vm_codec.require_canonical_vm_value(subject, field="MEMBER subject", program=self._vm.program)
        if type(name) is not str:
            raise _fail(ReplayFailureCode.UNGOVERNED_DISPATCH, "MEMBER name is not exact")
        if type(subject) is dict:
            return
        try:
            member = inspect.getattr_static(subject, name)
        except (AttributeError, TypeError):
            return
        descriptor = inspect.getattr_static(type(member), "__get__", None) is not None
        if callable(member) or descriptor:
            raise _fail(ReplayFailureCode.UNGOVERNED_DISPATCH, "MEMBER reaches Python descriptor")
        _vm_codec.require_canonical_vm_value(member, field="MEMBER result", program=self._vm.program)
    def _prepare_dispatch(self, instruction: object, *, resolve: bool = True) -> None:
        stack = self._vm.state.stack
        if instruction.op == "CALL":
            arguments = self._stack_arguments(instruction.a, trailing=1)
            callee = stack[-1]
            if type(callee) is FunctionObject:
                _vm_codec.require_canonical_vm_value(callee, field="CALL function", program=self._vm.program)
                return
            if callable(callee):
                raise _fail(ReplayFailureCode.UNGOVERNED_DISPATCH, "CALL reaches Python callable")
            _vm_codec.require_canonical_vm_value(callee, field="CALL callee")
            if not resolve:
                return
            self._cache_activity("HOST_EVAL", "HOST_EVAL", callee, arguments)
            return
        arguments = self._stack_arguments(instruction.b, trailing=0)
        if len(stack) < len(arguments) + 1:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "CALL_METHOD stack lacks its subject")
        subject = stack[-(len(arguments) + 1)]
        _vm_codec.require_canonical_vm_value(subject, field="CALL_METHOD subject", program=self._vm.program)
        name = instruction.a
        if type(name) is not str:
            raise _fail(ReplayFailureCode.UNGOVERNED_DISPATCH, "CALL_METHOD member is not exact")
        try:
            member = inspect.getattr_static(subject, name)
        except AttributeError:
            member = _STATIC_MEMBER_MISSING
        binds_on_lookup = inspect.getattr_static(type(member), "__get__", None) is not None
        if member is not _STATIC_MEMBER_MISSING and (callable(member) or binds_on_lookup):
            raise _fail(ReplayFailureCode.UNGOVERNED_DISPATCH, "CALL_METHOD reaches Python callable")
        if member is _STATIC_MEMBER_MISSING and isinstance(subject, dict) and name in subject:
            return
        if not resolve:
            return
        self._cache_activity("HOST_EVAL", "HOST_EVAL", f"{subject}.{name}", arguments)
    def _prepare_call_host(self, instruction: object) -> None:
        symbol = instruction.a
        if type(symbol) is not str:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "CALL_HOST symbol must be exact")
        arguments = self._stack_arguments(instruction.b if instruction.b is not None else 0)
        if symbol in _CALL_HOST_BUILTINS:
            raise _fail(
                ReplayFailureCode.INJECTION_PRIMITIVE_MISSING,
                "VM-local CALL_HOST builtin has no recorded-result injection primitive",
            )
        result = self._cache_activity(
            "CALL_HOST", "CALL_HOST", {"symbol": symbol, "args": arguments}, None
        )
        if isinstance(result, dict) and result.get("status") == VMStatus.PAUSED_HOST_CALL:
            raise _fail(
                ReplayFailureCode.INJECTION_PRIMITIVE_MISSING,
                "generic pending CALL_HOST has no governed resume primitive",
            )
    def _prepare_msg_send(self) -> None:
        stack = self._vm.state.stack
        if len(stack) < 2:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "MSG_SEND stack is incomplete")
        payload, target = stack[-1], stack[-2]
        _vm_codec.require_canonical_vm_value([target, payload], field="MSG_SEND operands")
        sender = self._actor_id()
        message = {
            "msg_type": str(self._vm.program.instructions[self._require_executing_ip()].a),
            "method": str(self._vm.program.instructions[self._require_executing_ip()].a),
            "sender_id": sender, "sender": sender,
            "target_id": str(target), "receiver": str(target),
            "payload": payload, "payload_hash": self._payload_hash(payload),
        }
        self._cache_activity(
            "MSG_SEND", "CALL_HOST", {"symbol": "SYS_MSG_SEND", "args": [message]}, None
        )
    def _prepare_msg_receive(self, instruction: object) -> None:
        message = self._vm.state.mailbox_inbound[0]
        _vm_codec.require_canonical_vm_value(message, field="MSG_RECEIVE message")
        if type(message) is not dict:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "MSG_RECEIVE message must be exact mapping")
        receiver = self._actor_id()
        event_id = self._event_id(self._require_executing_ip(), self._vm.state.transition_hash)
        msg_type = str(message.get("msg_type") or message.get("method") or message.get("type") or "")
        sender = str(message.get("sender_id") or message.get("sender") or "")
        payload_hash = str(message.get("payload_hash") or self._payload_hash(message.get("payload", message)))
        event = {
            "type": "message_consumed",
            "message_consumed_id": compute_message_consumed_id(
                receiver, msg_type, sender, self._vm.state.transition_hash, event_id, payload_hash
            ),
            "receiver_id": receiver, "msg_type": msg_type, "sender_id": sender,
            "payload_hash": payload_hash, "message": encode_vm_value(message),
        }
        self._cache_activity(
            "MSG_RECEIVE", "CALL_HOST", {"symbol": "SYS_MSG_CONSUME", "args": [event]}, None
        )
    def _actor_id(self) -> str:
        state = self._vm.state
        return str(state.actor_stack[-1] if state.actor_stack else state.agent_id or "default_agent")
    def _payload_hash(self, payload: object) -> str:
        encoded = json.dumps(encode_vm_value(payload), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    def _event_id(self, instruction_pointer: int, transition_hash: str) -> str:
        return _deterministic_event_id(self._execution_context,
            program_hash=self._vm.program.program_hash, instruction_pointer=instruction_pointer,
            transition_hash=transition_hash, frame_depth=len(self._vm.state.call_stack))
    def _cache_activity(
        self, recorded_opcode: str, route: str, operand_a: object, operand_b: object,
        *, inputs: object | None = None,
    ) -> object:
        if self._channel is None:
            raise _fail(ReplayFailureCode.CHANNEL_CLOSED, "activity has no governed channel")
        if self._activity is not None:
            raise _fail(ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH, "second activity preflight")
        encoded_a = _vm_codec.machine_value_bytes(operand_a)
        encoded_b = _vm_codec.machine_value_bytes(operand_b)
        sequence = self._sequence + 1
        recorded = self._channel.resolve(kind=activity_kind_for_opcode(recorded_opcode),
            inputs=inputs if inputs is not None else activity_inputs(
                opcode=recorded_opcode.encode("utf-8"), operand_a=encoded_a, operand_b=encoded_b),
            position=ActivityPosition(
                program_hash=self._vm.program.program_hash, instruction_pointer=self._require_executing_ip(),
                frame_depth=len(self._vm.state.call_stack), sequence=sequence))
        self._sequence = sequence
        result = _vm_codec.decode_recorded_result(self._channel.open_result(recorded))
        self._activity = _ResolvedActivity(route, encoded_a, encoded_b, result)
        return result
    def _prepare_structural(self, instruction: object) -> None:
        opcode = instruction.op
        if opcode == "RETURN" and not self._vm.state.stack:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "RETURN stack is empty")
        expected: list[_StructuralExpectation] = []
        if opcode == "RETURN":
            if self._vm.state.call_stack:
                frame = self._vm.state.call_stack[-1]
                depth = len(self._vm.state.call_stack)
                groups = (
                    ("context", self._vm.state.context_stack, frame.context_stack_snapshot),
                    ("actor", self._vm.state.actor_stack, frame.actor_stack_snapshot),
                    ("policy", self._vm.state.policy_stack, frame.policy_stack_snapshot),
                )
                for scope, current, snapshot in groups:
                    if len(snapshot) > len(current) or current[: len(snapshot)] != snapshot:
                        raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                                    f"RETURN {scope} snapshot is not a stack prefix")
                    for label in reversed(current[len(snapshot) :]):
                        kind = "policy_rule" if scope == "policy" and ":rule:" in str(label) else scope
                        symbol = {
                            "context": "SYS_CONTEXT_EXIT", "actor": "SYS_ACTOR_EXIT",
                            "policy": "SYS_POLICY_EXIT", "policy_rule": "SYS_POLICY_RULE_EXIT",
                        }[kind]
                        expected.append(self._structural_command(
                            opcode="RETURN", symbol=symbol, scope=kind, label=label,
                            metadata={}, direction="exit", reason="function_return",
                            frame_depth=depth, arguments=[label, "function_return"],
                        ))
        else:
            symbol, scope, direction, has_metadata = _STRUCTURAL_OPCODE[opcode]
            label = str(instruction.a)
            metadata = instruction.b if has_metadata and type(instruction.b) is dict else {}
            if direction == "exit":
                stack = self._vm.state.policy_stack if scope.startswith("policy") else getattr(
                    self._vm.state, f"{scope}_stack"
                )
                if not stack or stack[-1] != label:
                    raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                                f"{opcode} does not match the active structural scope")
            arguments = [label, metadata] if has_metadata else [label]
            expected.append(self._structural_command(
                opcode=opcode, symbol=symbol, scope=scope, label=label,
                metadata=metadata, direction=direction, reason=None,
                frame_depth=len(self._vm.state.call_stack), arguments=arguments,
            ))
        size = len(expected)
        expected = [dataclasses.replace(item, command=dataclasses.replace(
            item.command, occurrence_index=index, occurrence_size=size))
            for index, item in enumerate(expected)]
        commands = tuple(item.command for item in expected)
        try:
            self._structural_history.resolve_batch(
                commands, program_hash=self._vm.program.program_hash,
                instruction_pointer=self._require_executing_ip(),
                frame_depth=len(self._vm.state.call_stack),
                pre_transition_hash=self._vm.state.transition_hash, opcode=opcode,
                host_abi_version=str(self._vm.program.host_abi_version),
            )
        except StructuralHistoryViolation as exc:
            raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                        "structural batch differs from the expected history") from exc
        self._structural = tuple(expected)
    def _structural_command(
        self, *, opcode: str, symbol: str, scope: str, label: object,
        metadata: dict[str, object], direction: str, reason: str | None,
        frame_depth: int, arguments: list[object],
    ) -> _StructuralExpectation:
        if type(label) is not str or not label or len(label) > 1024:
            raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH, "structural label is invalid")
        _vm_codec.require_canonical_vm_value(metadata, field="structural metadata")
        try:
            command = ReplayStructuralCommand(profile_id=REPLAY_CAPABILITY_PROFILE_V1_E1,
                profile_digest=capability_profile_digest(), program_hash=self._vm.program.program_hash,
                instruction_pointer=self._require_executing_ip(), frame_depth=frame_depth,
                pre_transition_hash=self._vm.state.transition_hash,
                occurrence_index=0, occurrence_size=1, opcode=opcode,
                sys_symbol=symbol, scope_kind=scope, label=label,
                metadata_digest=hashlib.sha256(_vm_codec.machine_value_bytes(metadata)).hexdigest(),
                direction=direction, unwind_reason=reason,
                host_abi_version=str(self._vm.program.host_abi_version))
        except StructuralHistoryViolation as exc:
            raise _fail(ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                        "structural command violates its canonical ABI") from exc
        payload = {"symbol": symbol, "args": arguments}
        return _StructuralExpectation(command, _vm_codec.machine_value_bytes(payload))
    def _consume_structural(self, route: str, operand_a: object, operand_b: object) -> None:
        expected = self._structural or ()
        if self._structural_cursor >= len(expected):
            self._structural_mismatch = True
            return None
        item = expected[self._structural_cursor]
        if (route != "CALL_HOST" or operand_b is not None
                or _vm_codec.machine_value_bytes(operand_a) != item.operand_a):
            self._structural_mismatch = True
            return None
        self._structural_cursor += 1
        return None
    def _host(self, opcode: str, a: object, b: object) -> object:
        if type(opcode) is not str:
            raise _fail(ReplayFailureCode.OPCODE_NOT_CLASSIFIED, "host route must be exact")
        if opcode == "HOST_STATUS":
            return self._host_status(a, b)
        if self._structural is not None:
            return self._consume_structural(opcode, a, b)
        cached = self._activity
        if cached is None:
            self._activity_consumed = ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH
            raise _fail(ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH,
                        f"{opcode} produced an activity where zero were admitted")
        if self._activity_consumed:
            self._activity_consumed = ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH
            raise _fail(ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH,
                        "one opcode attempted to consume a second recorded activity")
        if (
            opcode != cached.route
            or _vm_codec.machine_value_bytes(a) != cached.operand_a
            or _vm_codec.machine_value_bytes(b) != cached.operand_b
        ):
            self._activity_consumed = ReplayFailureCode.INJECTION_PRIMITIVE_MISSING
            raise _fail(ReplayFailureCode.INJECTION_PRIMITIVE_MISSING,
                        "CVM host callback does not match the pre-resolved activity")
        self._activity_consumed = True
        return cached.result
    def _host_status(self, request: object, unused: object) -> dict[str, str]:
        if type(request) is not dict or request != {"field": "last_event_id"} or unused is not None:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH,
                        "HOST_STATUS accepts only exact last_event_id request")
        instruction_pointer = self._require_executing_ip()
        event_id = _deterministic_event_id(self._execution_context,
            program_hash=self._vm.program.program_hash, instruction_pointer=instruction_pointer,
            transition_hash=self._vm.state.transition_hash,
            frame_depth=len(self._vm.state.call_stack))
        return {"last_event_id": event_id}
    def _require_executing_ip(self) -> int:
        if type(self._executing_ip) is not int:
            raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED,
                        "CVM requested host service outside an adapter-owned step")
        return self._executing_ip
    def _pending_llm_call(self) -> _ValidatedPendingLLMCall:
        pending = self._vm.state.pending_host_call
        if self._vm.state.pending_message_receive is not None or self._vm.halted or self._vm.state.error is not None:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM state is not isolated")
        if type(pending) is not dict and type(pending) is not PendingHostCall:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call must be exact")
        if set(pending) != _PENDING_HOST_CALL_FIELDS:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call schema is not exact")
        if pending["pending_schema_version"] != "1":
            raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "pending host call schema is unknown")
        if pending["status"] != VMStatus.PAUSED_HOST_CALL:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call has another status")
        if pending["symbol"] != "llm.request" or type(pending["argc"]) is not int or pending["argc"] != 4:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM request identity is invalid")
        if pending["host_abi_version"] != HOST_ABI_VERSION:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request uses another host ABI")
        if pending["determinism_class"] != "nondeterministic":
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request changed determinism class")
        if pending["required_capabilities"] != []:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request changed capabilities")
        if pending["program_hash"] != self._vm.program.program_hash:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request names another program")
        if pending["agent_id"] != self._vm.state.agent_id:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request names another agent")
        ip_after = pending["ip_after_call"]
        if type(ip_after) is not int or isinstance(ip_after, bool) or ip_after < 1:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM request has invalid origin")
        if self._vm.state.ip != ip_after:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request moved its instruction pointer")
        origin_ip = ip_after - 1
        instructions = self._vm.program.instructions
        if (
            origin_ip >= len(instructions)
            or instructions[origin_ip].op != "LLM_REQUEST"
        ):
            raise _fail(
                ReplayFailureCode.IDENTITY_MISMATCH, "pending call did not originate at LLM_REQUEST"
            )
        frame_depth = pending["frame_depth_at_call"]
        if type(frame_depth) is not int or isinstance(frame_depth, bool) or frame_depth < 0:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM request has invalid frame depth")
        if frame_depth != len(self._vm.state.call_stack):
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request changed frame depth")
        transition = pending["transition_hash_at_call"]
        if not _vm_codec.is_transition_id(transition):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM request lacks transition identity")
        event_id = _deterministic_event_id(
            self._execution_context,
            program_hash=self._vm.program.program_hash,
            instruction_pointer=origin_ip,
            transition_hash=transition,
            frame_depth=frame_depth,
        )
        if pending["created_at_event_id"] != event_id:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request changed event identity")
        expected_call_id = compute_call_id(
            self._vm.program.program_hash,
            origin_ip,
            transition,
            event_id,
            frame_depth,
        )
        if pending["deterministic_call_id"] != expected_call_id or pending["call_id"] != expected_call_id:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request changed call identity")
        try:
            arguments = decode_vm_value(pending["args"])
        except (TypeError, ValueError, KeyError) as exc:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM arguments are not decodable") from exc
        if type(arguments) is not list or len(arguments) != 4:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending LLM request requires four arguments")
        _vm_codec.require_canonical_vm_value(arguments, field="pending LLM arguments")
        prompt, schema_hash, engine_params, cache_policy = arguments
        if (
            type(prompt) is not dict
            or prompt.get("type") != "prompt_envelope"
            or type(schema_hash) is not str
            or type(engine_params) is not dict
            or type(cache_policy) is not str
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH, "pending LLM arguments do not match the frozen ABI"
            )
        if encode_vm_value(arguments) != pending["args"]:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM arguments are not canonical")
        return _ValidatedPendingLLMCall(
            envelope=pending,
            call_id=expected_call_id,
            arguments=arguments,
            origin_ip=origin_ip,
            frame_depth=frame_depth,
            event_id=event_id,
        )
    def _state_unchanged_except(self, before: VMState, fields: set[str]) -> bool:
        return all(getattr(self._vm.state, name) == getattr(before, name)
                   for name in _vm_codec.VM_STATE_FIELDS - fields)
    def _complete_pending_llm_request(self, before: VMState) -> None:
        if self._channel is None:
            raise _fail(
                ReplayFailureCode.CHANNEL_CLOSED,
                "LLM_REQUEST attempted an effect with no recorded-activity channel",
            )
        pending = self._pending_llm_call()
        sequence = self._sequence + 1
        recorded = self._channel.resolve(
            kind=ActivityKind.LLM_CALL,
            inputs=activity_inputs(
                opcode=b"LLM_REQUEST",
                symbol=b"llm.request",
                arguments=_vm_codec.machine_value_bytes(pending.arguments),
                argc=_vm_codec.machine_value_bytes(4),
                call_id=pending.call_id.encode("utf-8"),
                created_at_event_id=pending.event_id.encode("utf-8"),
            ),
            position=ActivityPosition(
                program_hash=self._vm.program.program_hash,
                instruction_pointer=pending.origin_ip,
                frame_depth=pending.frame_depth,
                sequence=sequence,
            ),
        )
        # Commit the position only after the channel resolves the exact record.
        self._sequence = sequence
        result = _vm_codec.decode_recorded_result(self._channel.open_result(recorded))
        if self._vm.state.pending_host_call is not pending.envelope:
            raise _fail(
                ReplayFailureCode.TRUSTED_OBJECT_FORGED,
                "pending LLM request changed while its recorded result was resolved",
            )
        try:
            self._vm.resume_host_call(pending.call_id, result)
        except ReplayViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - this is the protected-core boundary
            raise _fail(
                ReplayFailureCode.MACHINE_EXECUTION_FAULT,
                "CognitiveVM raised while resuming a recorded host call",
            ) from exc
        state = self._vm.state
        if (
            state.pending_host_call is not None or state.stack != before.stack + [result]
            or state.error is not None or self._vm.halted
            or not _vm_codec.is_transition_id(state.transition_hash)
            or state.transition_hash == before.transition_hash
            or not self._state_unchanged_except(
                before, {"stack", "pending_host_call", "error", "transition_hash"}
            )
        ):
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM resume successor differs")
    def _pending_message(self) -> _ValidatedPendingMessage:
        pending = self._vm.state.pending_message_receive
        if type(pending) is not dict or set(pending) != _PENDING_MESSAGE_FIELDS:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending message envelope is not exact")
        if self._vm.state.pending_host_call is not None or not self._vm.halted or self._vm.state.error is not None:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message state is not isolated")
        if pending["pending_schema_version"] != "1" or pending["status"] != VMStatus.PAUSED_MESSAGING:
            raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "pending message schema/status differs")
        origin = self._vm.state.ip - 1
        instructions = self._vm.program.instructions
        if origin < 0 or origin >= len(instructions) or instructions[origin].op != "MSG_RECEIVE":
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message origin differs")
        instruction = instructions[origin]
        sender_var, target_var = pending["sender_var"], pending["target_var"]
        if (
            type(sender_var) is not str or type(target_var) is not str
            or sender_var != str(instruction.a) or target_var != str(instruction.b)
        ):
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message bindings differ")
        receiver = self._actor_id()
        transition = pending["transition_hash_at_receive"]
        if not _vm_codec.is_transition_id(transition) or pending["receiver_id"] != receiver:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message subject differs")
        event_id = self._event_id(origin, transition)
        receive_id = "mr-" + hashlib.sha256(
            f"{receiver}|{sender_var}|{target_var}|{transition}|{event_id}".encode("utf-8")
        ).hexdigest()[:16]
        if pending["created_at_event_id"] != event_id or pending["message_receive_id"] != receive_id:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message identity differs")
        return _ValidatedPendingMessage(
            pending, receive_id, receiver, sender_var, target_var, origin, event_id
        )
    def _complete_pending_message_receive(self, before: VMState) -> None:
        if self._channel is None:
            raise _fail(ReplayFailureCode.CHANNEL_CLOSED, "MSG_RECEIVE has no governed channel")
        pending = self._pending_message()
        sequence = self._sequence + 1
        recorded = self._channel.resolve(
            kind=ActivityKind.MESSAGE_RECEIVE,
            inputs=activity_inputs(
                opcode=b"MSG_RECEIVE",
                receiver_id=pending.receiver_id.encode("utf-8"),
                sender_var=pending.sender_var.encode("utf-8"),
                target_var=pending.target_var.encode("utf-8"),
                message_receive_id=pending.receive_id.encode("utf-8"),
                created_at_event_id=pending.event_id.encode("utf-8"),
            ),
            position=ActivityPosition(
                program_hash=self._vm.program.program_hash,
                instruction_pointer=pending.origin_ip,
                frame_depth=len(self._vm.state.call_stack), sequence=sequence,
            ),
        )
        self._sequence = sequence
        message = _vm_codec.decode_recorded_result(self._channel.open_result(recorded))
        if type(message) is not dict:
            raise _fail(ReplayFailureCode.RESULT_NOT_DECODABLE, "recorded message must be exact mapping")
        if self._vm.state.pending_message_receive is not pending.envelope:
            raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "pending message changed during resolve")
        try:
            self._vm.resume_message_receive(message)
        except ReplayViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - this is the protected-core boundary
            raise _fail(
                ReplayFailureCode.MACHINE_EXECUTION_FAULT,
                "CognitiveVM raised while resuming a recorded message receive",
            ) from exc
        state = self._vm.state
        expected_locals = dict(before.locals)
        expected_locals[pending.sender_var] = message.get("sender_id", message.get("sender"))
        expected_locals[pending.target_var] = message
        if (
            state.pending_message_receive is not None or state.stack != before.stack + [message]
            or state.locals != expected_locals or self._vm.halted
            or not _vm_codec.is_transition_id(state.transition_hash)
            or state.transition_hash == before.transition_hash
            or not self._state_unchanged_except(
                before, {"stack", "locals", "pending_message_receive", "transition_hash"}
            )
        ):
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending message resume successor differs")
class CognitiveVMReplayMachineFactory:
    __slots__ = ()

    def adapter_id(self) -> str:
        return REPLAY_MACHINE_ADAPTER_ID_V1_E1

    def validate_recorded_result(self, raw: bytes) -> None:
        """Refuse bytes outside the exact recorded-result codec."""

        _vm_codec.decode_recorded_result(raw)

    def build(self, program: BytecodeProgram, *, gas_budget: int,
              execution_context: ReplayMachineExecutionContext,
              expected_structural_history: bytes | None = None) -> ReplayMachinePort:
        return CognitiveVMReplayAdapter(program, gas_budget=gas_budget, execution_context=execution_context,
            expected_structural_history=expected_structural_history)
    def restore(self, snapshot_bytes: bytes, *, gas_budget: int,
                execution_context: ReplayMachineExecutionContext,
                expected_structural_history: bytes | None = None) -> ReplayMachinePort:
        program, state, halted, sequence, structural_history = _vm_codec.decode_adapter_snapshot(
            snapshot_bytes
        )
        adapter = CognitiveVMReplayAdapter(
            program,
            gas_budget=gas_budget,
            execution_context=execution_context,
            _state=state,
            _halted=halted,
            _activity_sequence=sequence,
            expected_structural_history=expected_structural_history,
            _resolved_structural_history=structural_history,
        )
        if adapter.snapshot_bytes() != snapshot_bytes:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot does not round-trip exactly")
        if state.pending_host_call is not None:
            adapter._pending_llm_call()
        if state.pending_message_receive is not None:
            adapter._pending_message()
        return adapter
