"""The sole Stage 4 adapter from governed replay to :mod:`synapse.cvm`.

The replay owner declares the machine and factory ports.  This module implements
those ports over the protected core and contains no admission, policy, storage or
composition logic.  In particular, a recorded activity channel is the only route
from a replaying VM to an external result.

``LLM_REQUEST`` is a two-transition lifecycle.  The first CVM step creates the
durable pending envelope.  The next adapter step resolves exactly one recorded
``LLM_CALL``, decodes its canonical result and resumes that exact call id without
executing another instruction.  A restored pending snapshot follows the same
path, while a snapshot whose pending envelope is already clear cannot inject the
result again.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json

from synapse.bytecode import BytecodeProgram
from synapse.cvm import (
    GAS_BACK_EDGE,
    GAS_COSTS,
    HOST_ABI_VERSION,
    CallFrame,
    CognitiveVM,
    FunctionObject,
    GuardFrame,
    PendingHostCall,
    VMState,
    VMStatus,
    compute_call_id,
    decode_vm_value,
    encode_vm_value,
)

from .activities import ACTIVITY_RESULT_CODEC_V1, ActivityKind, ActivityPosition
from .replay import (
    DISPATCH_GUARDED_OPCODES,
    RECORDED_ONLY_OPCODES,
    REPLAY_MACHINE_ADAPTER_ID_V1,
    RecordedActivityChannelPort,
    ReplayFailureCode,
    ReplayMachineExecutionContext,
    ReplayMachinePort,
    _fail,
    _is_sealed_activity_channel,
    _natural,
    activity_inputs,
    activity_kind_for_opcode,
    require_replay_machine_execution_context,
)


_SNAPSHOT_DIGEST_PROFILE = b"synapse.stage4.gold.replay-machine-port/v1\x00"
_ADAPTER_SNAPSHOT_SCHEMA_V1 = "synapse.stage4.gold.replay-vm-adapter-snapshot/v1"
_EVENT_ID_PROFILE = b"synapse.stage4.gold.replay-machine-event/v1\x00"
_BACK_EDGE_OPCODES = frozenset({"JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE"})
_MAX_VM_VALUE_DEPTH = 64
_MAX_VM_VALUE_NODES = 8192

CANONICAL_VM_SCALARS = (type(None), bool, int, float, str)
_FRAME_FIELDS = {
    CallFrame: tuple(field.name for field in dataclasses.fields(CallFrame)),
    GuardFrame: tuple(field.name for field in dataclasses.fields(GuardFrame)),
}
_NON_VALUE_VM_FIELDS = frozenset({"ip", "gas_remaining", "transition_hash"})

_PENDING_HOST_CALL_FIELDS = frozenset(
    {
        "pending_schema_version",
        "status",
        "call_id",
        "symbol",
        "args",
        "argc",
        "ip_after_call",
        "program_hash",
        "transition_hash_at_call",
        "frame_depth_at_call",
        "agent_id",
        "required_capabilities",
        "host_abi_version",
        "created_at_event_id",
        "determinism_class",
        "deterministic_call_id",
    }
)


def require_canonical_vm_value(value: object, *, field: str = "value") -> None:
    """Refuse values whose VM serialization could execute value-owned code."""

    budget = [_MAX_VM_VALUE_NODES]

    def walk(node: object, depth: int) -> None:
        if depth > _MAX_VM_VALUE_DEPTH:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} nests deeper than a governed replay will serialize",
            )
        budget[0] -= 1
        if budget[0] < 0:
            raise _fail(
                ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
                f"{field} holds more values than a governed replay will serialize",
            )
        kind = type(node)
        if kind in CANONICAL_VM_SCALARS:
            return
        if kind is FunctionObject:
            walk(node.name, depth + 1)
            walk(list(node.params), depth + 1)
            walk(node.body_ip, depth + 1)
            walk(node.program_hash, depth + 1)
            walk(node.closure, depth + 1)
            return
        if kind is CallFrame or kind is GuardFrame:
            for frame_field in _FRAME_FIELDS[kind]:
                walk(getattr(node, frame_field), depth + 1)
            return
        if kind is dict or kind is PendingHostCall:
            for key, item in node.items():
                if type(key) is not str:
                    raise _fail(
                        ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                        f"{field} has a mapping key the machine would stringify",
                    )
                walk(item, depth + 1)
            return
        if kind is list or kind is tuple:
            for item in node:
                walk(item, depth + 1)
            return
        raise _fail(
            ReplayFailureCode.NON_CANONICAL_VM_VALUE,
            f"{field} is not a canonical machine value and would be serialized by repr",
        )

    walk(value, 0)


def require_canonical_vm_state(state: VMState) -> VMState:
    """Validate every value-bearing field of an exact protected-core state."""

    if type(state) is not VMState:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact VMState is required")
    for name in sorted(vars(state)):
        if name not in _NON_VALUE_VM_FIELDS:
            require_canonical_vm_value(getattr(state, name), field=f"state.{name}")
    return state


def encode_recorded_result(value: object) -> bytes:
    """Encode the exact machine value stored by an activity result reference."""

    require_canonical_vm_value(value, field="recorded result")
    return json.dumps(
        encode_vm_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def decode_recorded_result(raw: bytes) -> object:
    """Decode only exact canonical bytes under the frozen activity codec."""

    if type(raw) is not bytes:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a recorded result must be exact bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"))
        value = decode_vm_value(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            "the recorded result is not canonical under the activity result codec",
        ) from exc
    require_canonical_vm_value(value, field="recorded result")
    if encode_recorded_result(value) != raw:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            f"the recorded result is not canonical under {ACTIVITY_RESULT_CODEC_V1}",
        )
    return value


def _machine_value_bytes(value: object) -> bytes:
    """Encode one activity input without a user-code fallback."""

    require_canonical_vm_value(value, field="host call operand")
    return json.dumps(
        encode_vm_value(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _snapshot_bytes(snapshot: dict[str, object]) -> bytes:
    return json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _execution_context_payload(
    context: ReplayMachineExecutionContext,
) -> dict[str, object]:
    context = require_replay_machine_execution_context(context)
    return {
        "run_id": context.run_id.to_dict(),
        "attempt_id": context.attempt_id.to_dict(),
        "repository_revision": context.repository_revision.to_dict(),
        "environment_profile_id": context.environment_profile_id,
        "policy_version": context.policy_version,
    }


def _deterministic_event_id(
    context: ReplayMachineExecutionContext,
    *,
    program_hash: str,
    instruction_pointer: int,
    transition_hash: str,
    frame_depth: int,
) -> str:
    """Derive the CVM service cursor solely from sealed execution state."""

    if type(program_hash) is not str or not program_hash:
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "event identity lacks a program hash")
    if type(transition_hash) is not str or not transition_hash:
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
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "evt--" + hashlib.sha256(_EVENT_ID_PROFILE + canonical).hexdigest()


@dataclasses.dataclass(frozen=True)
class _ValidatedPendingLLMCall:
    envelope: dict[str, object]
    call_id: str
    arguments: list[object]
    origin_ip: int
    transition_hash_at_call: str
    frame_depth: int
    event_id: str


class CognitiveVMReplayAdapter:
    """Exact ``ReplayMachinePort`` implementation over ``CognitiveVM``."""

    __slots__ = (
        "_vm",
        "_execution_context",
        "_channel",
        "_sequence",
        "_executing_ip",
        "_executing_opcode",
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
    ) -> None:
        if type(program) is not BytecodeProgram:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact BytecodeProgram is required")
        _natural(gas_budget, "gas_budget", maximum=2**53)
        context = require_replay_machine_execution_context(execution_context)
        state = _state if _state is not None else VMState(gas_remaining=gas_budget)
        require_canonical_vm_state(state)
        if type(_halted) is not bool:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot halted flag must be exact")
        self._vm = CognitiveVM(program, state)
        self._vm.halted = _halted
        self._execution_context = context
        self._channel: RecordedActivityChannelPort | None = None
        _natural(_activity_sequence, "activity_sequence", maximum=2**53)
        self._sequence = _activity_sequence
        self._executing_ip: int | None = None
        self._executing_opcode: str | None = None
        self._vm.host = self._host

    def attach_channel(self, channel: RecordedActivityChannelPort) -> None:
        if self._channel is not None:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "a replay channel is already attached to this adapter",
            )
        if not _is_sealed_activity_channel(channel):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact channel is required")
        self._channel = channel

    def program_hash(self) -> str:
        return self._vm.program.program_hash

    def host_abi_version(self) -> str:
        return str(self._vm.program.host_abi_version)

    def transition_hash(self) -> str:
        return self._vm.state.transition_hash

    def instruction_pointer(self) -> int:
        if self._vm.state.pending_host_call is not None:
            return self._pending_llm_call().origin_ip
        return int(self._vm.state.ip)

    def frame_depth(self) -> int:
        return len(self._vm.state.call_stack)

    def gas_remaining(self) -> int:
        return int(self._vm.state.gas_remaining)

    def is_halted(self) -> bool:
        if self._vm.state.pending_host_call is not None:
            return False
        return bool(self._vm.halted)

    def next_opcode(self) -> str | None:
        if self._vm.state.pending_host_call is not None:
            self._pending_llm_call()
            return "LLM_RESUME"
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            return None
        opcode = instructions[index].op
        if type(opcode) is not str:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                "an instruction opcode must be an exact string",
            )
        return opcode

    def next_step_gas_cost(self) -> int:
        """Return exactly what the protected core will charge for the next step."""

        if self._vm.state.pending_host_call is not None:
            self._pending_llm_call()
            return 0
        opcode = self.next_opcode()
        if opcode is None:
            return 0
        cost = GAS_COSTS.get(opcode, 1)
        if opcode in _BACK_EDGE_OPCODES and self._is_back_edge():
            cost += GAS_BACK_EDGE
        return cost

    def machine_snapshot(self) -> dict[str, object]:
        require_canonical_vm_state(self._vm.state)
        snapshot = self._vm.snapshot()
        if type(snapshot) is not dict or set(snapshot) != {"program", "state", "halted"}:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "CVM produced a malformed snapshot")
        return snapshot

    def snapshot_bytes(self) -> bytes:
        return _snapshot_bytes(
            {
                "schema_version": _ADAPTER_SNAPSHOT_SCHEMA_V1,
                "adapter_id": REPLAY_MACHINE_ADAPTER_ID_V1,
                "activity_sequence": self._sequence,
                "machine": self.machine_snapshot(),
            }
        )

    def snapshot_digest(self) -> str:
        return hashlib.sha256(
            _SNAPSHOT_DIGEST_PROFILE + self.snapshot_bytes()
        ).hexdigest()

    def step(self) -> None:
        """Execute one CVM or pending-resume transition, never both at once."""

        if self._vm.state.pending_host_call is not None:
            self._complete_pending_llm_request()
            return
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            self._vm.step()
            return
        opcode = self.next_opcode()
        assert opcode is not None
        self._executing_ip = index
        self._executing_opcode = opcode
        try:
            self._require_dispatch_is_governed()
            if self._vm.state.stack:
                require_canonical_vm_value(self._vm.state.stack[-1], field="stack top")
            self._vm.step()
        finally:
            self._executing_ip = None
            self._executing_opcode = None

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
        require_canonical_vm_value(condition, field="branch condition")
        return bool(not condition if opcode == "JUMP_IF_FALSE" else condition)

    def _require_dispatch_is_governed(self) -> None:
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            return
        instruction = instructions[index]
        opcode = instruction.op
        if type(opcode) is not str:
            raise _fail(
                ReplayFailureCode.OPCODE_NOT_CLASSIFIED,
                "an instruction carries an opcode that is not an exact name",
            )
        if opcode not in DISPATCH_GUARDED_OPCODES:
            return
        stack = self._vm.state.stack
        if opcode == "CALL":
            if not stack:
                return
            callee = stack[-1]
            if isinstance(callee, FunctionObject):
                return
            if callable(callee):
                raise _fail(
                    ReplayFailureCode.UNGOVERNED_DISPATCH,
                    "CALL would execute an ordinary Python callable during a replay",
                )
            return
        argc = instruction.b if instruction.b is not None else 0
        if type(argc) is not int or isinstance(argc, bool) or argc < 0 or len(stack) < argc + 1:
            return
        subject = stack[-(argc + 1)]
        require_canonical_vm_value(subject, field="CALL_METHOD subject")
        member_name = instruction.a
        if type(member_name) is not str:
            raise _fail(
                ReplayFailureCode.UNGOVERNED_DISPATCH,
                "CALL_METHOD names a member this replay cannot resolve statically",
            )
        try:
            member = inspect.getattr_static(subject, member_name)
        except AttributeError:
            return
        if callable(member) and not isinstance(member, FunctionObject):
            raise _fail(
                ReplayFailureCode.UNGOVERNED_DISPATCH,
                "CALL_METHOD would execute an ordinary Python callable during a replay",
            )

    def _host(self, opcode: str, a: object, b: object) -> object:
        """Serve CVM status locally or resolve one exact recorded activity."""

        if type(opcode) is not str:
            raise _fail(ReplayFailureCode.OPCODE_NOT_CLASSIFIED, "host route must be exact")
        if opcode == "HOST_STATUS":
            return self._host_status(a, b)
        if self._channel is None:
            raise _fail(
                ReplayFailureCode.CHANNEL_CLOSED,
                f"{opcode} attempted an effect with no recorded-activity channel",
            )
        recorded_opcode = self._recorded_opcode_for_route(opcode)
        sequence = self._sequence + 1
        recorded = self._channel.resolve(
            kind=activity_kind_for_opcode(recorded_opcode),
            inputs=activity_inputs(
                opcode=recorded_opcode.encode("utf-8"),
                operand_a=_machine_value_bytes(a),
                operand_b=_machine_value_bytes(b),
            ),
            position=ActivityPosition(
                program_hash=self._vm.program.program_hash,
                instruction_pointer=self._require_executing_ip(),
                frame_depth=len(self._vm.state.call_stack),
                sequence=sequence,
            ),
        )
        # ``resolve`` is the commit point: the sealed channel records this
        # identity in its consumed transcript before returning. Keep the
        # adapter cursor in the same state even if the immutable result blob is
        # subsequently unavailable or non-canonical.
        self._sequence = sequence
        result = decode_recorded_result(self._channel.open_result(recorded))
        return result

    def _host_status(self, request: object, unused: object) -> dict[str, str]:
        if type(request) is not dict or request != {"field": "last_event_id"} or unused is not None:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "HOST_STATUS accepts only the exact last_event_id service request",
            )
        instruction_pointer = self._require_executing_ip()
        event_id = _deterministic_event_id(
            self._execution_context,
            program_hash=self._vm.program.program_hash,
            instruction_pointer=instruction_pointer,
            transition_hash=self._vm.state.transition_hash,
            frame_depth=len(self._vm.state.call_stack),
        )
        return {"last_event_id": event_id}

    def _recorded_opcode_for_route(self, route: str) -> str:
        origin = self._executing_opcode
        if origin in {"MSG_SEND", "MSG_RECEIVE"}:
            return origin
        if origin in RECORDED_ONLY_OPCODES and origin not in {"LLM_REQUEST", "LLM_RESUME"}:
            return origin
        return route

    def _require_executing_ip(self) -> int:
        if type(self._executing_ip) is not int:
            raise _fail(
                ReplayFailureCode.TRUSTED_OBJECT_FORGED,
                "CVM requested host service outside an adapter-owned step",
            )
        return self._executing_ip

    def _pending_llm_call(self) -> _ValidatedPendingLLMCall:
        pending = self._vm.state.pending_host_call
        if type(pending) is not dict and type(pending) is not PendingHostCall:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call must be exact")
        if set(pending) != _PENDING_HOST_CALL_FIELDS:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call schema is not exact")
        if pending["pending_schema_version"] != "1":
            raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "pending host call schema is unknown")
        if pending["status"] != VMStatus.PAUSED_HOST_CALL:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "pending host call has another status")
        if pending["symbol"] != "llm.request" or pending["argc"] != 4:
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
        if type(transition) is not str or not transition:
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
        require_canonical_vm_value(arguments, field="pending LLM arguments")
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
            transition_hash_at_call=transition,
            frame_depth=frame_depth,
            event_id=event_id,
        )

    def _complete_pending_llm_request(self) -> None:
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
                arguments=_machine_value_bytes(pending.arguments),
                argc=_machine_value_bytes(4),
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
        # Successful resolution has already appended the activity to the
        # channel transcript. Commit the matching position cursor at the same
        # boundary; result verification and injection remain fail-closed.
        self._sequence = sequence
        result = decode_recorded_result(self._channel.open_result(recorded))
        if self._vm.state.pending_host_call is not pending.envelope:
            raise _fail(
                ReplayFailureCode.TRUSTED_OBJECT_FORGED,
                "pending LLM request changed while its recorded result was resolved",
            )
        self._vm.resume_host_call(pending.call_id, result)
        if self._vm.state.pending_host_call is not None:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "pending LLM request did not clear")


class CognitiveVMReplayMachineFactory:
    """Stateless exact factory used by the production composition root."""

    __slots__ = ()

    def adapter_id(self) -> str:
        return REPLAY_MACHINE_ADAPTER_ID_V1

    def build(
        self,
        program: BytecodeProgram,
        *,
        gas_budget: int,
        execution_context: ReplayMachineExecutionContext,
    ) -> ReplayMachinePort:
        return CognitiveVMReplayAdapter(
            program,
            gas_budget=gas_budget,
            execution_context=execution_context,
        )

    def restore(
        self,
        snapshot_bytes: bytes,
        *,
        gas_budget: int,
        execution_context: ReplayMachineExecutionContext,
    ) -> ReplayMachinePort:
        if type(snapshot_bytes) is not bytes or not snapshot_bytes:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a snapshot must be exact non-empty bytes")
        try:
            snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a durable snapshot is not JSON") from exc
        if type(snapshot) is not dict or set(snapshot) != {
            "schema_version",
            "adapter_id",
            "activity_sequence",
            "machine",
        }:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a durable adapter snapshot is not exact")
        if _snapshot_bytes(snapshot) != snapshot_bytes:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a durable snapshot is not canonical")
        if snapshot["schema_version"] != _ADAPTER_SNAPSHOT_SCHEMA_V1:
            raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "adapter snapshot schema is unknown")
        if snapshot["adapter_id"] != REPLAY_MACHINE_ADAPTER_ID_V1:
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "adapter snapshot names another machine")
        sequence = _natural(
            snapshot["activity_sequence"], "activity_sequence", maximum=2**53
        )
        machine_snapshot = snapshot["machine"]
        if type(machine_snapshot) is not dict or set(machine_snapshot) != {
            "program",
            "state",
            "halted",
        }:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "embedded CVM snapshot is not exact")
        if type(machine_snapshot["halted"]) is not bool:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot halted flag must be exact")
        try:
            program = BytecodeProgram.from_dict(machine_snapshot["program"])
            state = VMState.from_dict(machine_snapshot["state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot payload is malformed") from exc
        adapter = CognitiveVMReplayAdapter(
            program,
            gas_budget=gas_budget,
            execution_context=execution_context,
            _state=state,
            _halted=machine_snapshot["halted"],
            _activity_sequence=sequence,
        )
        if adapter.snapshot_bytes() != snapshot_bytes:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "snapshot does not round-trip exactly")
        if state.pending_host_call is not None:
            adapter._pending_llm_call()
        return adapter


__all__ = [
    "CognitiveVMReplayAdapter",
    "CognitiveVMReplayMachineFactory",
    "decode_recorded_result",
    "encode_recorded_result",
    "require_canonical_vm_state",
    "require_canonical_vm_value",
]
