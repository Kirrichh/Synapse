"""Stage 4 §23 — BehaviorReplayRequest, CognitiveVM integration, BehaviorReplayResult.

Behavior replay loads admitted behavior programs by content key, resolves their
exact bindings and capability profile, executes deterministic transitions in
CognitiveVM, and obtains external results only through governed activity
records. It reports reproducibility, transitions, observations and mismatches.

**It never asserts task correctness, and it cannot express an outcome verdict.**
That is not a convention here, it is the shape of the code: this module defines
no FULL, no completeness, no correctness, and no authority that could grant one.
§26 owns the outcome vocabulary; replay produces evidence that owner reads. A
successful replay says the behavior did what it did before, which is a different
claim from the behavior being right.

The status vocabulary is fixed by §23 and has exactly four members —
``REPLAY_IDENTICAL``, ``REPLAY_INCOMPATIBLE``, ``REPLAY_FAILED``,
``INFRA_ERROR``. Semantic equivalence is disabled until a formal relation is
approved, so ``REPLAY_IDENTICAL`` is the only success.

Four properties carry the guarantees.

*The machine is reached through one narrow adapter.* NR-03 permits a single
narrow typed adapter point into the protected core and forbids Stage 4 loading,
admission or authority logic inside ``cvm.py``; the §12 ownership map places
"CognitiveVM integration and ReplayResult" in this file. ``ReplayMachinePort``
names the operations a replay needs and ``CognitiveVMReplayAdapter`` is the one
implementation over the real machine. Nothing in ``cvm.py`` changes.

*Nothing external is re-executed.* Every effect-bearing opcode reaches
``RecordedActivityChannel``, which resolves a recorded result by activity
identity. There is no live producer behind it: an activity that was never
recorded fails the replay instead of quietly happening a second time.

*Every inadmissible state is typed and fail-closed.* Unknown host call, gas
exhaustion, program hash mismatch, activity substitution, missing activity
record, transition mismatch, snapshot tamper and compiler/ABI mismatch each have
a named reason, and none of them can produce ``REPLAY_IDENTICAL``.

*Resume verifies what it resumes from.* Program hash, snapshot digest and
activity history are all re-checked before a resumed replay takes a step, so a
continuation cannot be attached to a state it did not actually leave.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Protocol, runtime_checkable

from synapse.bytecode import BytecodeProgram
from synapse.cvm import CognitiveVM, VMState, encode_vm_value

from .activities import (
    ActivityFailureCode,
    ActivityInputs,
    ActivityKind,
    ActivityLedger,
    ActivityPosition,
    ActivityViolation,
    RecordedActivity,
    activity_inputs,
)
from .admission import (
    GateDecision,
    require_consumption_before_compilation,
)
from .behavior import (
    ReplayContract,
    SynapseBehaviorUnit,
    validate_compiler_binding_for_unit,
)
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    CompilerBinding,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_record_id,
    validate_record_id,
)

REPLAY_CAPABILITY_PROFILE_V1 = "synapse.stage4.gold.replay-capability-profile/v1"
_PROFILE_PREFIX = REPLAY_CAPABILITY_PROFILE_V1.encode("utf-8") + b"\x00"

_REQUEST_SEAL = object()
_RESULT_SEAL = object()
_OBSERVATION_SEAL = object()
_CHANNEL_SEAL = object()

_IDENTIFIER_MAX = 128
_SHA256_LENGTH = 64
_MAX_STEPS = 1_000_000
_MAX_SUBJECTS = 512
_MAX_BEHAVIORS = 64


# ---------------------------------------------------------------------------
# The allowed Gold replay host-call profile (§23, frozen decision)
# ---------------------------------------------------------------------------

#: Opcodes whose successor state is a function of the current state alone.
#: Replaying one re-derives its result; nothing needs to be recorded.
REPLAY_ADMISSIBLE_OPCODES = frozenset(
    {
        "LOAD_CONST", "LOAD_NAME", "LOAD_NONE", "LOAD_TRUE", "LOAD_FALSE",
        "STORE", "POP", "DUP", "SAVE_NAME", "RESTORE_NAME",
        "JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE", "CALL", "RETURN",
        "MAKE_FUNCTION", "HALT",
        "ADD", "SUB", "MUL", "DIV", "MOD",
        "EQ", "NEQ", "LT", "GT", "LTE", "GTE",
        "AND", "OR", "NOT", "UNARY_NEG",
        "BUILD_LIST", "BUILD_DICT", "INDEX", "MEMBER",
        "CONTEXT_ENTER", "CONTEXT_EXIT",
        "ACTOR_ENTER", "ACTOR_EXIT",
        "POLICY_ENTER", "POLICY_EXIT", "POLICY_RULE_ENTER", "POLICY_RULE_EXIT",
        "GUARD_ENTER", "GUARD_EXIT", "GUARD_CHECK_RESULT", "GUARD_VIOLATION_ACK",
        "CALL_METHOD",
        "RECEIVE_ENTER", "RECEIVE_EXIT",
    }
)

#: Opcodes whose successor state depends on something outside the machine. Each
#: occurrence must resolve to a recorded activity or the replay fails.
RECORDED_ONLY_OPCODES = frozenset(
    {
        "LLM_EVAL", "LLM_REQUEST", "LLM_RESUME", "PROMPT_BUILD",
        "DREAM", "IMPRINT", "RECALL",
        "AFFECT_EVENT", "AFFECT_STATE", "METRICS",
        "HOST_EVAL", "CALL_HOST", "FRACTURE_SELF",
        "HABIT_SUGGEST", "THRESHOLD_CHECK",
        "SEND", "RECEIVE", "MSG_SEND", "MSG_RECEIVE",
    }
)

#: The activity kind each effect-bearing opcode produces. Total over
#: ``RECORDED_ONLY_OPCODES``: an opcode with no kind could not be recorded, and
#: an unrecordable effect during replay is the hole this profile closes.
ACTIVITY_KIND_BY_OPCODE = {
    "LLM_EVAL": ActivityKind.LLM_CALL,
    "LLM_REQUEST": ActivityKind.LLM_CALL,
    "LLM_RESUME": ActivityKind.LLM_CALL,
    "PROMPT_BUILD": ActivityKind.LLM_CALL,
    "DREAM": ActivityKind.LLM_CALL,
    "IMPRINT": ActivityKind.MEMORY_WRITE,
    "RECALL": ActivityKind.MEMORY_READ,
    "AFFECT_EVENT": ActivityKind.AFFECT_EVENT,
    "AFFECT_STATE": ActivityKind.AFFECT_READ,
    "METRICS": ActivityKind.METRICS_EMIT,
    "HOST_EVAL": ActivityKind.HOST_DISPATCH,
    "CALL_HOST": ActivityKind.HOST_DISPATCH,
    "FRACTURE_SELF": ActivityKind.SELF_MODIFICATION,
    "HABIT_SUGGEST": ActivityKind.HABIT_SUGGESTION,
    "THRESHOLD_CHECK": ActivityKind.THRESHOLD_EVALUATION,
    "SEND": ActivityKind.MESSAGE_SEND,
    "MSG_SEND": ActivityKind.MESSAGE_SEND,
    "RECEIVE": ActivityKind.MESSAGE_RECEIVE,
    "MSG_RECEIVE": ActivityKind.MESSAGE_RECEIVE,
}


def capability_profile_digest() -> str:
    """A hash over the profile a request is executed under.

    The profile is a frozen decision, so a request records which one it ran
    against. Widening the admissible set later changes this digest, and a
    request pinned to the old one no longer matches — which is the point: a
    replay validated under one host-call profile is not evidence about another.
    """

    payload = _canonical(
        {
            "profile_id": REPLAY_CAPABILITY_PROFILE_V1,
            "admissible": sorted(REPLAY_ADMISSIBLE_OPCODES),
            "recorded_only": sorted(RECORDED_ONLY_OPCODES),
            "activity_kinds": {
                opcode: ACTIVITY_KIND_BY_OPCODE[opcode].value
                for opcode in sorted(ACTIVITY_KIND_BY_OPCODE)
            },
        }
    )
    return hashlib.sha256(_PROFILE_PREFIX + payload).hexdigest()


# ---------------------------------------------------------------------------
# §23 status vocabulary and failure semantics
# ---------------------------------------------------------------------------


class ReplayStatus(str, Enum):
    """The §23 status vocabulary. Four members, fixed, and not extended here.

    ``REPLAY_IDENTICAL`` is the only success. Semantic equivalence is disabled
    until a formal relation is approved, so there is no weaker success to fall
    back to — and none can be added without that relation, because no rule
    exists anywhere in the runtime for reconciling two differing chain hashes.
    """

    REPLAY_IDENTICAL = "REPLAY_IDENTICAL"
    REPLAY_INCOMPATIBLE = "REPLAY_INCOMPATIBLE"
    REPLAY_FAILED = "REPLAY_FAILED"
    INFRA_ERROR = "INFRA_ERROR"


class ReplayFailureReason(str, Enum):
    """Closed vocabulary for the inadmissible states §23 enumerates.

    Every member is fail-closed: none of them can accompany
    ``REPLAY_IDENTICAL``, and a reason nobody anticipated is not silently
    absent — a result without ``REPLAY_IDENTICAL`` must carry one.
    """

    PROGRAM_HASH_MISMATCH = "PROGRAM_HASH_MISMATCH"
    HOST_ABI_MISMATCH = "HOST_ABI_MISMATCH"
    CAPABILITY_PROFILE_MISMATCH = "CAPABILITY_PROFILE_MISMATCH"
    COMPILER_VERSION_MISMATCH = "COMPILER_VERSION_MISMATCH"
    SNAPSHOT_INCOMPATIBLE = "SNAPSHOT_INCOMPATIBLE"
    SNAPSHOT_TAMPERED = "SNAPSHOT_TAMPERED"
    ACTIVITY_HISTORY_MISMATCH = "ACTIVITY_HISTORY_MISMATCH"
    TRANSITION_MISMATCH = "TRANSITION_MISMATCH"
    MISSING_ACTIVITY_RECORD = "MISSING_ACTIVITY_RECORD"
    ACTIVITY_SUBSTITUTED = "ACTIVITY_SUBSTITUTED"
    FORBIDDEN_HOST_CALL = "FORBIDDEN_HOST_CALL"
    UNKNOWN_HOST_CALL = "UNKNOWN_HOST_CALL"
    SIDE_EFFECT_OUTSIDE_PLAN = "SIDE_EFFECT_OUTSIDE_PLAN"
    GAS_EXHAUSTED = "GAS_EXHAUSTED"
    COGNITIVE_BUDGET_EXHAUSTED = "COGNITIVE_BUDGET_EXHAUSTED"
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"
    MACHINE_FAULT = "MACHINE_FAULT"


#: Which status each reason produces. Incompatibility is a statement about the
#: execution contract, failure about the run, and INFRA_ERROR about the machine
#: — §26 keeps INFRA_ERROR distinct from a genuine failure, and so does this.
_STATUS_BY_REASON = {
    ReplayFailureReason.PROGRAM_HASH_MISMATCH: ReplayStatus.REPLAY_INCOMPATIBLE,
    ReplayFailureReason.HOST_ABI_MISMATCH: ReplayStatus.REPLAY_INCOMPATIBLE,
    ReplayFailureReason.CAPABILITY_PROFILE_MISMATCH: ReplayStatus.REPLAY_INCOMPATIBLE,
    ReplayFailureReason.COMPILER_VERSION_MISMATCH: ReplayStatus.REPLAY_INCOMPATIBLE,
    ReplayFailureReason.SNAPSHOT_INCOMPATIBLE: ReplayStatus.REPLAY_INCOMPATIBLE,
    ReplayFailureReason.SNAPSHOT_TAMPERED: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.TRANSITION_MISMATCH: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.MISSING_ACTIVITY_RECORD: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.ACTIVITY_SUBSTITUTED: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.FORBIDDEN_HOST_CALL: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.UNKNOWN_HOST_CALL: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.SIDE_EFFECT_OUTSIDE_PLAN: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.GAS_EXHAUSTED: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.COGNITIVE_BUDGET_EXHAUSTED: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.STEP_LIMIT_REACHED: ReplayStatus.REPLAY_FAILED,
    ReplayFailureReason.MACHINE_FAULT: ReplayStatus.INFRA_ERROR,
}


def status_for_reason(reason: ReplayFailureReason) -> ReplayStatus:
    if type(reason) is not ReplayFailureReason:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "reason must be an exact ReplayFailureReason")
    return _STATUS_BY_REASON[reason]


class ReplayFailureCode(str, Enum):
    """Typed contract failures — malformed inputs, not execution outcomes.

    An execution outcome is recorded in the result; only a request that cannot
    be executed at all raises. The distinction matters because NR-13 requires
    every attempt to be preserved: a run that raised on divergence would destroy
    the evidence of what it saw.
    """

    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_SHA256 = "MALFORMED_SHA256"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    MACHINE_PORT_INCOMPLETE = "MACHINE_PORT_INCOMPLETE"
    MACHINE_COUNT_MISMATCH = "MACHINE_COUNT_MISMATCH"
    LEDGER_NOT_BOUND = "LEDGER_NOT_BOUND"
    CHANNEL_CLOSED = "CHANNEL_CLOSED"
    OPCODE_NOT_CLASSIFIED = "OPCODE_NOT_CLASSIFIED"
    BEHAVIOR_SET_EMPTY = "BEHAVIOR_SET_EMPTY"
    DUPLICATE_BEHAVIOR = "DUPLICATE_BEHAVIOR"
    GAS_NOT_MONOTONE = "GAS_NOT_MONOTONE"
    STATUS_REASON_INCONSISTENT = "STATUS_REASON_INCONSISTENT"


class ReplayViolation(ValueError):
    """A typed, fail-closed replay error carrying no execution payload."""

    def __init__(self, failure_code: ReplayFailureCode, detail: str) -> None:
        if type(failure_code) is not ReplayFailureCode:
            raise TypeError("failure_code must be an exact ReplayFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ReplayFailureCode, detail: str) -> ReplayViolation:
    return ReplayViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID
    )


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(ReplayFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is invalid")
    if value.strip() != value:
        raise _fail(ReplayFailureCode.MALFORMED_IDENTIFIER, f"{field_name} has padding")
    return value


def _sha256(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != _SHA256_LENGTH:
        raise _fail(ReplayFailureCode.MALFORMED_SHA256, f"{field_name} is invalid")
    if any(character not in "0123456789abcdef" for character in value):
        raise _fail(ReplayFailureCode.MALFORMED_SHA256, f"{field_name} is not lowercase hex")
    return value


def _natural(value: object, field_name: str, *, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} must be a natural number")
    if value > maximum:
        raise _fail(ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED, f"{field_name} exceeds its limit")
    return value


def _ref(value: object, field_name: str, *, expected_kind: RefKind | None = None) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact HashBoundRef")
    if expected_kind is not None and value.kind is not expected_kind:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} has the wrong reference kind")
    return value


def classify_replay_opcode(opcode: str) -> str:
    """Return ``"admissible"`` or ``"recorded_only"``, or raise.

    There is no third answer and no default. An opcode the profile does not name
    has no determinism class, and executing under an unknown determinism class
    is what the profile exists to prevent.
    """

    _identifier(opcode, "opcode")
    if opcode in REPLAY_ADMISSIBLE_OPCODES:
        return "admissible"
    if opcode in RECORDED_ONLY_OPCODES:
        return "recorded_only"
    raise _fail(
        ReplayFailureCode.OPCODE_NOT_CLASSIFIED,
        f"{opcode} is in neither half of the replay capability profile",
    )


def activity_kind_for_opcode(opcode: str) -> ActivityKind:
    """Return the activity kind an effect-bearing opcode produces, or raise."""

    _identifier(opcode, "opcode")
    kind = ACTIVITY_KIND_BY_OPCODE.get(opcode)
    if kind is None:
        raise _fail(
            ReplayFailureCode.OPCODE_NOT_CLASSIFIED,
            f"{opcode} produces no activity kind and therefore cannot be recorded",
        )
    return kind


# ---------------------------------------------------------------------------
# The machine port — NR-03's narrow typed adapter boundary
# ---------------------------------------------------------------------------


@runtime_checkable
class ReplayMachinePort(Protocol):
    """The complete surface a governed replay needs from a virtual machine.

    Nine operations: eight reads and one write. The port exposes no way to set
    state, load a program or resume a paused host call, because a replay driver
    able to do any of those could arrange the answer it wanted.
    """

    def program_hash(self) -> str: ...

    def host_abi_version(self) -> str: ...

    def transition_hash(self) -> str: ...

    def instruction_pointer(self) -> int: ...

    def frame_depth(self) -> int: ...

    def gas_remaining(self) -> int: ...

    def is_halted(self) -> bool: ...

    def next_opcode(self) -> str | None: ...

    def snapshot_digest(self) -> str: ...

    def attach_channel(self, channel: RecordedActivityChannel) -> None:
        """Receive the channel this replay opened, before the first transition."""

    def step(self) -> None: ...


_MACHINE_PORT_OPERATIONS = (
    "program_hash", "host_abi_version", "transition_hash", "instruction_pointer",
    "frame_depth", "gas_remaining", "is_halted", "next_opcode", "snapshot_digest",
    "attach_channel", "step",
)


def require_machine_port(value: object) -> ReplayMachinePort:
    """Refuse a machine that cannot answer every question the profile asks."""

    missing = [name for name in _MACHINE_PORT_OPERATIONS if not callable(getattr(value, name, None))]
    if missing:
        raise _fail(
            ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
            f"machine port is missing {', '.join(missing[:4])}",
        )
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# RecordedActivityChannel — the only door to an external effect
# ---------------------------------------------------------------------------

#: How an activity refusal maps onto a §23 reason. An unmapped refusal becomes
#: the strictest reading: a refusal nobody anticipated is not evidence that the
#: activity was merely absent.
_ACTIVITY_REASONS = {
    "ACTIVITY_NOT_RECORDED": ReplayFailureReason.MISSING_ACTIVITY_RECORD,
    "ACTIVITY_SUBSTITUTED": ReplayFailureReason.ACTIVITY_SUBSTITUTED,
    "RESULT_HASH_MISMATCH": ReplayFailureReason.ACTIVITY_SUBSTITUTED,
    "FORBIDDEN_IN_REPLAY": ReplayFailureReason.FORBIDDEN_HOST_CALL,
    "FRESH_CALL_ATTEMPTED": ReplayFailureReason.FORBIDDEN_HOST_CALL,
    "UNKNOWN_ACTIVITY_KIND": ReplayFailureReason.UNKNOWN_HOST_CALL,
    "COGNITIVE_BUDGET_EXHAUSTED": ReplayFailureReason.COGNITIVE_BUDGET_EXHAUSTED,
}


def reason_for_activity_failure(exc: ActivityViolation) -> ReplayFailureReason:
    if type(exc) is not ActivityViolation:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact ActivityViolation is required")
    return _ACTIVITY_REASONS.get(exc.failure_code.value, ReplayFailureReason.FORBIDDEN_HOST_CALL)


class RecordedActivityChannel:
    """Resolves external effects from record, and records what it resolved.

    One channel serves one replay attempt. It is opened by ``execute_replay``,
    used by the adapter for the duration of the run, and closed when the run
    ends — so an adapter that kept a reference cannot keep drawing on it
    afterwards.

    The consumed identities are kept in order, and the order is evidence: two
    runs that consume the same activities in a different sequence are not the
    same run, and nothing else in the record would show it.

    ``cognitive_budget`` bounds how many recorded activities one replay may
    consume. It is a separate bound from gas because it limits a different
    thing: gas bounds machine work, this bounds reliance on external results.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _CHANNEL_SEAL or kwargs or len(args) != 2:
            raise TypeError("RecordedActivityChannel is created only by execute_replay")
        ledger, cognitive_budget = args
        if type(ledger) is not ActivityLedger:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a channel requires an exact ActivityLedger")
        self._ledger = ledger
        self._cognitive_budget = _natural(cognitive_budget, "cognitive_budget", maximum=_MAX_STEPS)
        self._consumed: list[str] = []
        self._keys: list[str] = []
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    def close(self) -> None:
        self._open = False

    def resolve(
        self,
        *,
        kind: ActivityKind,
        inputs: ActivityInputs,
        position: ActivityPosition,
    ) -> RecordedActivity:
        """Return the recorded result for this activity, or fail.

        Nothing live is reachable from here. The ledger holds recorded results
        only, so a miss stops the replay rather than repeating the effect.
        """

        if not self._open:
            raise _fail(
                ReplayFailureCode.CHANNEL_CLOSED,
                "the replay that opened this channel has ended",
            )
        if len(self._consumed) >= self._cognitive_budget:
            raise ActivityViolation(
                ActivityFailureCode.COGNITIVE_BUDGET_EXHAUSTED,
                "cognitive budget exhausted for this replay",
            )
        found = self._ledger.resolve(kind=kind, inputs=inputs, position=position)
        self._consumed.append(found.activity_identity)
        self._keys.append(found.idempotency_key)
        return found

    def consumed_identities(self) -> tuple[str, ...]:
        return tuple(self._consumed)

    def consumed_keys(self) -> tuple[str, ...]:
        """The result-bound keys of what was consumed, in consumption order."""

        return tuple(self._keys)


# ---------------------------------------------------------------------------
# CognitiveVMReplayAdapter — the one adapter point into the protected core
# ---------------------------------------------------------------------------

_ADAPTER_PROFILE = b"synapse.stage4.gold.replay-machine-port/v1\x00"


def _machine_value_bytes(value: object) -> bytes:
    """Encode a VM value canonically without ever failing on an exotic object.

    ``default=str`` is the same fallback the machine's own transition hash uses.
    A value it cannot encode still contributes its repr, so an unencodable
    operand changes the digest rather than silently dropping out of it.
    """

    return json.dumps(
        encode_vm_value(value), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


class CognitiveVMReplayAdapter:
    """The narrow typed adapter NR-03 permits, and the only one.

    It owns a ``CognitiveVM``, answers the port's questions about it, and
    translates the machine's ``(opcode, a, b)`` host call into the ``(kind,
    inputs, position)`` an activity needs. That translation is the adapter's
    only real work, and it is written to lose nothing: both operands are hashed
    into the input vector, so two calls differing in either one cannot collide.

    No Stage 4 loading, admission or authority logic lives here or in
    ``cvm.py``. The adapter drives the machine; it decides nothing.
    """

    def __init__(
        self,
        program: BytecodeProgram,
        *,
        gas_budget: int,
        state: VMState | None = None,
    ) -> None:
        if type(program) is not BytecodeProgram:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact BytecodeProgram is required")
        _natural(gas_budget, "gas_budget", maximum=2**53)
        vm_state = state if state is not None else VMState(gas_remaining=gas_budget)
        if type(vm_state) is not VMState:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact VMState is required")
        self._vm = CognitiveVM(program, vm_state)
        self._channel: RecordedActivityChannel | None = None
        self._sequence = 0
        self._pending_ip = 0
        self._vm.host = self._host

    @classmethod
    def from_snapshot(cls, snapshot: dict, *, gas_budget: int) -> CognitiveVMReplayAdapter:
        """Rebuild an adapter from a machine snapshot, for a resumed replay.

        The snapshot is the machine's own format. Nothing is reinterpreted here:
        whether the resumed state is the one a continuation may attach to is
        decided by ``resume_replay``, against digests it holds independently.
        """

        if type(snapshot) is not dict:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a snapshot must be an exact dict")
        try:
            program = BytecodeProgram.from_dict(snapshot["program"])
            state = VMState.from_dict(snapshot["state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH, "snapshot is not a machine snapshot"
            ) from exc
        adapter = cls(program, gas_budget=gas_budget, state=state)
        adapter._vm.halted = bool(snapshot.get("halted", False))
        return adapter

    # --- channel wiring -----------------------------------------------------

    def attach_channel(self, channel: RecordedActivityChannel) -> None:
        """Receive the channel a replay opened. Called once, before the first step.

        A second attach is refused. Swapping the channel mid-run would let one
        replay's recorded results answer another replay's calls, which is the
        cross-run substitution the ledger binding exists to prevent.
        """

        if self._channel is not None:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "a replay channel is already attached to this adapter",
            )
        if type(channel) is not RecordedActivityChannel:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact channel is required")
        self._channel = channel

    # --- port operations ----------------------------------------------------

    def program_hash(self) -> str:
        return self._vm.program.program_hash

    def host_abi_version(self) -> str:
        return str(self._vm.program.host_abi_version)

    def transition_hash(self) -> str:
        return self._vm.state.transition_hash

    def instruction_pointer(self) -> int:
        return int(self._vm.state.ip)

    def frame_depth(self) -> int:
        return len(self._vm.state.call_stack)

    def gas_remaining(self) -> int:
        return int(self._vm.state.gas_remaining)

    def is_halted(self) -> bool:
        return bool(self._vm.halted)

    def next_opcode(self) -> str | None:
        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            return None
        return str(instructions[index].op)

    def machine_snapshot(self) -> dict:
        return self._vm.snapshot()

    def snapshot_digest(self) -> str:
        payload = json.dumps(
            self._vm.snapshot(), sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(_ADAPTER_PROFILE + payload).hexdigest()

    def step(self) -> None:
        # Captured before dispatch: the machine advances ``ip`` while executing,
        # so an activity positioned afterwards would carry the *next*
        # instruction's index and never resolve.
        self._pending_ip = self._vm.state.ip
        self._vm.step()

    # --- host routing -------------------------------------------------------

    def _host(self, opcode: str, a: object, b: object) -> dict:
        """The machine's only route to an external effect during a replay.

        With no channel attached this raises rather than returning the machine's
        built-in stub result. A stub would be a fresh value invented during a
        replay, which is exactly the unrecorded effect §23 forbids.
        """

        if self._channel is None:
            raise _fail(
                ReplayFailureCode.CHANNEL_CLOSED,
                f"{opcode} attempted an effect with no recorded-activity channel",
            )
        self._sequence += 1
        recorded = self._channel.resolve(
            kind=activity_kind_for_opcode(opcode),
            inputs=activity_inputs(
                opcode=opcode.encode("utf-8"),
                operand_a=_machine_value_bytes(a),
                operand_b=_machine_value_bytes(b),
            ),
            position=ActivityPosition(
                program_hash=self._vm.program.program_hash,
                instruction_pointer=int(self._pending_ip),
                frame_depth=len(self._vm.state.call_stack),
                sequence=self._sequence,
            ),
        )
        return {
            "op": opcode,
            "status": "replayed",
            "activity_identity": recorded.activity_identity,
            "idempotency_key": recorded.idempotency_key,
            "result_sha256": recorded.result_sha256,
            "from_cache": False,
        }


# ---------------------------------------------------------------------------
# ReplayProgramBinding — one entry of the ordered admitted set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayProgramBinding:
    """One admitted behavior and the compiler output bound to its content key."""

    behavior_content_key: str
    program_hash: str
    host_abi_version: str
    compiler_identity: str
    bytecode_version: str
    replay_contract: ReplayContract

    def __post_init__(self) -> None:
        _identifier(self.behavior_content_key, "behavior_content_key")
        _identifier(self.program_hash, "program_hash")
        _identifier(self.host_abi_version, "host_abi_version")
        _identifier(self.compiler_identity, "compiler_identity")
        _identifier(self.bytecode_version, "bytecode_version")
        if type(self.replay_contract) is not ReplayContract:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "replay contract must be exact")

    def to_dict(self) -> dict[str, object]:
        return {
            "behavior_content_key": self.behavior_content_key,
            "program_hash": self.program_hash,
            "host_abi_version": self.host_abi_version,
            "compiler_identity": self.compiler_identity,
            "bytecode_version": self.bytecode_version,
            "replay_contract": self.replay_contract.to_dict(),
        }


def replay_program_binding(
    *, unit: SynapseBehaviorUnit, binding: CompilerBinding
) -> ReplayProgramBinding:
    """Bind one compiled behavior for replay, revalidating the compiler output.

    The replay contract is taken from the unit rather than accepted separately.
    A caller able to supply its own contract could supply an empty one, and an
    empty contract is satisfied by an empty transcript — a replay that executed
    nothing would report a match.
    """

    if type(binding) is not CompilerBinding:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "compiler binding must be exact")
    validate_compiler_binding_for_unit(unit, binding)
    return ReplayProgramBinding(
        behavior_content_key=binding.behavior_content_key.value,
        program_hash=binding.actual_program_hash,
        host_abi_version=binding.host_abi_version,
        compiler_identity=binding.compiler_identity,
        bytecode_version=binding.bytecode_version,
        replay_contract=unit.core.replay_contract,
    )


# ---------------------------------------------------------------------------
# BehaviorReplayRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class BehaviorReplayRequest:
    """The §23 request: what is replayed, under which contract, with what budget."""

    schema_version: SchemaVersion
    replay_id: RecordId
    knowledge_snapshot_id: str
    bindings: tuple[ReplayProgramBinding, ...]
    capability_profile: str
    capability_profile_digest: str
    recorded_activity_refs: tuple[HashBoundRef, ...]
    activity_idempotency_keys: tuple[str, ...]
    ledger: ActivityLedger
    knowledge_subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    gas_budget: int
    cognitive_budget: int
    step_limit: int
    expected_transcript_root: str | None
    expected_terminal_snapshot_digests: tuple[str, ...] | None
    executor_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorReplayRequest:
        raise TypeError("BehaviorReplayRequest is created only by create_replay_request")

    @property
    def behavior_content_keys(self) -> tuple[str, ...]:
        return tuple(item.behavior_content_key for item in self.bindings)

    @property
    def program_hashes(self) -> tuple[str, ...]:
        return tuple(item.program_hash for item in self.bindings)

    def to_dict(self) -> dict[str, object]:
        validate_replay_request(self)
        return {**_request_payload(self), "replay_id": self.replay_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_replay_request(self)
        return _canonical(_request_payload(self))


def _request_payload(value: BehaviorReplayRequest) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "knowledge_snapshot_id": value.knowledge_snapshot_id,
        "bindings": [item.to_dict() for item in value.bindings],
        "capability_profile": value.capability_profile,
        "capability_profile_digest": value.capability_profile_digest,
        "recorded_activity_refs": [item.to_dict() for item in value.recorded_activity_refs],
        "activity_idempotency_keys": list(value.activity_idempotency_keys),
        "ledger_root": value.ledger.ledger_root(),
        "knowledge_subject_refs": [item.to_dict() for item in value.knowledge_subject_refs],
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "policy_version": value.policy_version,
        "gas_budget": value.gas_budget,
        "cognitive_budget": value.cognitive_budget,
        "step_limit": value.step_limit,
        "expected_transcript_root": value.expected_transcript_root,
        "expected_terminal_snapshot_digests": (
            None if value.expected_terminal_snapshot_digests is None
            else list(value.expected_terminal_snapshot_digests)
        ),
        "executor_actor": value.executor_actor.value,
    }


def validate_replay_request(value: BehaviorReplayRequest) -> None:
    if type(value) is not BehaviorReplayRequest or getattr(value, "_trusted_seal", None) is not _REQUEST_SEAL:
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "replay request is not factory sealed")
    if value.schema_version is not SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "replay request schema is unknown")
    if type(value.bindings) is not tuple or not value.bindings:
        raise _fail(ReplayFailureCode.BEHAVIOR_SET_EMPTY, "a replay needs at least one behavior")
    if len(value.bindings) > _MAX_BEHAVIORS:
        raise _fail(ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED, "behavior set exceeds the limit")
    for item in value.bindings:
        if type(item) is not ReplayProgramBinding:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "bindings must be exact")
    if len(set(value.behavior_content_keys)) != len(value.bindings):
        raise _fail(ReplayFailureCode.DUPLICATE_BEHAVIOR, "a behavior appears twice in one replay")
    if type(value.ledger) is not ActivityLedger:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "activity ledger must be exact")
    if type(value.executor_actor) is not ActorIdentity:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "executor actor must be exact")
    _identifier(value.knowledge_snapshot_id, "knowledge_snapshot_id")
    _identifier(value.policy_version, "policy_version")
    if value.capability_profile != REPLAY_CAPABILITY_PROFILE_V1:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "capability profile is not the frozen one")
    _sha256(value.capability_profile_digest, "capability_profile_digest")
    _natural(value.gas_budget, "gas_budget", maximum=2**53)
    _natural(value.cognitive_budget, "cognitive_budget", maximum=_MAX_STEPS)
    _natural(value.step_limit, "step_limit", maximum=_MAX_STEPS)
    if value.expected_transcript_root is not None:
        _sha256(value.expected_transcript_root, "expected_transcript_root")
    if value.expected_terminal_snapshot_digests is not None:
        if type(value.expected_terminal_snapshot_digests) is not tuple:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "terminal digests must be a tuple")
        if len(value.expected_terminal_snapshot_digests) != len(value.bindings):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "one terminal snapshot digest is required per behavior",
            )
        for item in value.expected_terminal_snapshot_digests:
            _sha256(item, "expected_terminal_snapshot_digest")
    if type(value.knowledge_subject_refs) is not tuple or not value.knowledge_subject_refs:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "knowledge subject refs must be a non-empty tuple")
    if len(value.knowledge_subject_refs) > _MAX_SUBJECTS:
        raise _fail(ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED, "knowledge subject refs exceed the limit")
    for item in value.knowledge_subject_refs:
        _ref(item, "knowledge subject ref")
    for item in value.recorded_activity_refs:
        _ref(item, "recorded activity ref")
    for item in value.activity_idempotency_keys:
        _sha256(item, "activity idempotency key")
    if value.recorded_activity_refs != value.ledger.activity_refs():
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "recorded_activity_refs do not describe the sealed ledger",
        )
    if value.activity_idempotency_keys != value.ledger.idempotency_keys():
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "the pinned idempotency keys do not describe the sealed ledger",
        )
    _ref(value.consumer_context_ref, "consumer_context_ref")
    _ref(value.boundary_ref, "boundary_ref", expected_kind=RefKind.ATOMIC_BOUNDARY)
    if value.ledger.policy_version != value.policy_version:
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "the ledger was sealed under another policy version",
        )
    value.ledger.require_bound_to(
        consumer_context_ref=value.consumer_context_ref, boundary_ref=value.boundary_ref
    )
    try:
        validate_record_id(value.replay_id, canonical_bytes=_canonical(_request_payload(value)))
    except ContractViolation as exc:
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "replay_id does not match its payload") from exc


def create_replay_request(
    *,
    knowledge_snapshot_id: str,
    behaviors: tuple[tuple[SynapseBehaviorUnit, CompilerBinding], ...],
    ledger: ActivityLedger,
    consumption_decision: GateDecision,
    knowledge_subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    executor_actor: ActorIdentity,
    expected_transcript_root: str | None = None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None = None,
) -> BehaviorReplayRequest:
    """Admit, then bind, then request — in that order, which is the whole point.

    The composition is ``consumption gate ≺ compiler binding ≺ first
    transition``, and each step is a hard gate on the next:

    1. the §22 consumption barrier decides the knowledge this replay will use,
       against the boundary and consumer context the run will actually have —
       and it is asked through ``require_consumption_before_compilation``, which
       refuses to admit anything once compilation has happened;
    2. each compiled program is revalidated against its behavior unit, so the
       bytecode about to run is the bytecode that was verified;
    3. only then does a request exist, and ``execute_replay`` re-checks every
       binding against the live machines before the first transition.

    Reordering these is not a style question. Admitting after compilation would
    let an unadmitted object influence what gets compiled, and binding after the
    first transition would let a different program produce the transcript.
    """

    # 1. Admission, asserted to precede compilation.
    require_consumption_before_compilation(
        consumption_decision,
        subject_refs=knowledge_subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy_version=policy_version,
        compiled=False,
    )

    # 2. Program binding, one entry per admitted behavior, in order.
    if type(behaviors) is not tuple or not behaviors:
        raise _fail(ReplayFailureCode.BEHAVIOR_SET_EMPTY, "a replay needs at least one behavior")
    bindings = tuple(
        replay_program_binding(unit=unit, binding=binding) for unit, binding in behaviors
    )

    # 3. The request itself.
    payload = object.__new__(BehaviorReplayRequest)
    object.__setattr__(payload, "schema_version", SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1)
    object.__setattr__(payload, "knowledge_snapshot_id", knowledge_snapshot_id)
    object.__setattr__(payload, "bindings", bindings)
    object.__setattr__(payload, "capability_profile", REPLAY_CAPABILITY_PROFILE_V1)
    object.__setattr__(payload, "capability_profile_digest", capability_profile_digest())
    object.__setattr__(payload, "recorded_activity_refs", ledger.activity_refs())
    object.__setattr__(payload, "activity_idempotency_keys", ledger.idempotency_keys())
    object.__setattr__(payload, "ledger", ledger)
    object.__setattr__(payload, "knowledge_subject_refs", knowledge_subject_refs)
    object.__setattr__(payload, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(payload, "boundary_ref", boundary_ref)
    object.__setattr__(payload, "policy_version", policy_version)
    object.__setattr__(payload, "gas_budget", gas_budget)
    object.__setattr__(payload, "cognitive_budget", cognitive_budget)
    object.__setattr__(payload, "step_limit", step_limit)
    object.__setattr__(payload, "expected_transcript_root", expected_transcript_root)
    object.__setattr__(
        payload, "expected_terminal_snapshot_digests", expected_terminal_snapshot_digests
    )
    object.__setattr__(payload, "executor_actor", executor_actor)
    object.__setattr__(payload, "_trusted_seal", _REQUEST_SEAL)
    object.__setattr__(
        payload,
        "replay_id",
        compute_record_id(
            domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
            canonical_bytes=_canonical(_request_payload(payload)),
        ),
    )
    validate_replay_request(payload)
    return payload


def replay_request_ref(value: BehaviorReplayRequest) -> HashBoundRef:
    validate_replay_request(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.replay_id.digest_sha256,
        schema_id=SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


def transcript_root(*, transitions: tuple[str, ...], activities: tuple[str, ...]) -> str:
    """Fold an ordered transcript into one root.

    Order-sensitive by construction: each element is folded into the running
    digest, so a permutation of the same transitions yields a different root.
    That is the property ``ReplayContract`` cannot supply — it stores expected
    ids as a sorted set — and it is why a pinned root, not a set comparison, is
    what establishes ``REPLAY_IDENTICAL``.
    """

    root = hashlib.sha256(_PROFILE_PREFIX).digest()
    for label, items in (("t", transitions), ("a", activities)):
        for item in items:
            root = hashlib.sha256(
                _PROFILE_PREFIX + root + label.encode("ascii") + b"\x00" + item.encode("utf-8")
            ).digest()
    return root.hex()


# ---------------------------------------------------------------------------
# ReplayObservation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class ReplayObservation:
    """Typed output of replay for one behavior. Not task authority.

    §23 is explicit that replay observations do not gain instruction or
    task-success authority, so this record states what happened and nothing
    about whether it was right. There is no correctness field, no verdict field,
    and no method that returns one — a reader wanting a verdict has to go to the
    owner that computes it from far more than this.
    """

    schema_version: SchemaVersion
    observation_id: RecordId
    behavior_content_key: str
    program_hash: str
    host_abi_version: str
    transition_hash_chain: tuple[str, ...]
    consumed_activity_identities: tuple[str, ...]
    consumed_activity_keys: tuple[str, ...]
    initial_snapshot_digest: str
    terminal_snapshot_digest: str
    steps_executed: int
    gas_consumed: int
    transcript_matched: bool
    first_unexpected_index: int | None
    failure_reason: ReplayFailureReason | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ReplayObservation:
        raise TypeError("ReplayObservation is produced only by execute_replay")

    def to_dict(self) -> dict[str, object]:
        validate_replay_observation(self)
        return {**_observation_payload(self), "observation_id": self.observation_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_replay_observation(self)
        return _canonical(_observation_payload(self))


def _observation_payload(value: ReplayObservation) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "behavior_content_key": value.behavior_content_key,
        "program_hash": value.program_hash,
        "host_abi_version": value.host_abi_version,
        "transition_hash_chain": list(value.transition_hash_chain),
        "consumed_activity_identities": list(value.consumed_activity_identities),
        "consumed_activity_keys": list(value.consumed_activity_keys),
        "initial_snapshot_digest": value.initial_snapshot_digest,
        "terminal_snapshot_digest": value.terminal_snapshot_digest,
        "steps_executed": value.steps_executed,
        "gas_consumed": value.gas_consumed,
        "transcript_matched": value.transcript_matched,
        "first_unexpected_index": value.first_unexpected_index,
        "failure_reason": None if value.failure_reason is None else value.failure_reason.value,
    }


def validate_replay_observation(value: ReplayObservation) -> None:
    if type(value) is not ReplayObservation or getattr(value, "_trusted_seal", None) is not _OBSERVATION_SEAL:
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "observation is not factory sealed")
    if value.schema_version is not SchemaVersion.REPLAY_OBSERVATION_V1:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "observation schema is unknown")
    _identifier(value.behavior_content_key, "behavior_content_key")
    _identifier(value.program_hash, "program_hash")
    _identifier(value.host_abi_version, "host_abi_version")
    for name in ("transition_hash_chain", "consumed_activity_identities", "consumed_activity_keys"):
        items = getattr(value, name)
        if type(items) is not tuple or any(type(item) is not str for item in items):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{name} must be a tuple of strings")
    _sha256(value.initial_snapshot_digest, "initial_snapshot_digest")
    _sha256(value.terminal_snapshot_digest, "terminal_snapshot_digest")
    _natural(value.steps_executed, "steps_executed", maximum=_MAX_STEPS)
    _natural(value.gas_consumed, "gas_consumed", maximum=2**53)
    if type(value.transcript_matched) is not bool:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "transcript_matched must be an exact bool")
    if value.first_unexpected_index is not None:
        _natural(value.first_unexpected_index, "first_unexpected_index", maximum=_MAX_STEPS)
    if value.failure_reason is not None and type(value.failure_reason) is not ReplayFailureReason:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "failure_reason must be exact")
    if value.transcript_matched and value.failure_reason is not None:
        raise _fail(
            ReplayFailureCode.STATUS_REASON_INCONSISTENT,
            "an observation cannot both match and carry a failure reason",
        )
    try:
        validate_record_id(
            value.observation_id, canonical_bytes=_canonical(_observation_payload(value))
        )
    except ContractViolation as exc:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH, "observation_id does not match its payload"
        ) from exc


def _seal_observation(**fields: object) -> ReplayObservation:
    payload = object.__new__(ReplayObservation)
    object.__setattr__(payload, "schema_version", SchemaVersion.REPLAY_OBSERVATION_V1)
    for name, item in fields.items():
        object.__setattr__(payload, name, item)
    object.__setattr__(payload, "_trusted_seal", _OBSERVATION_SEAL)
    object.__setattr__(
        payload,
        "observation_id",
        compute_record_id(
            domain=IdentityDomain.REPLAY_OBSERVATION,
            canonical_bytes=_canonical(_observation_payload(payload)),
        ),
    )
    validate_replay_observation(payload)
    return payload


# ---------------------------------------------------------------------------
# BehaviorReplayResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class BehaviorReplayResult:
    """What one replay attempt established about reproducibility. Nothing more.

    There is no outcome field here, no completeness, no correctness and no
    verdict — §26 owns those and computes them from far more than a replay.
    ``REPLAY_IDENTICAL`` means the behavior did what it did before; it does not
    mean the behavior is right, and this record offers no way to say that it is.
    """

    schema_version: SchemaVersion
    result_id: RecordId
    request_ref: HashBoundRef
    knowledge_snapshot_id: str
    status: ReplayStatus
    failure_reason: ReplayFailureReason | None
    observations: tuple[ReplayObservation, ...]
    transition_hash_chain: tuple[str, ...]
    recorded_activity_refs: tuple[HashBoundRef, ...]
    consumed_activity_identities: tuple[str, ...]
    observed_transcript_root: str
    expected_transcript_root: str | None
    terminal_snapshot_digests: tuple[str, ...]
    steps_executed: int
    gas_consumed: int
    executor_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorReplayResult:
        raise TypeError("BehaviorReplayResult is produced only by execute_replay")

    def to_dict(self) -> dict[str, object]:
        validate_replay_result(self)
        return {**_result_payload(self), "result_id": self.result_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_replay_result(self)
        return _canonical(_result_payload(self))

    @property
    def root_matches_expectation(self) -> bool:
        """Whether a root was pinned before the run *and* the observed root equals it."""

        return (
            self.expected_transcript_root is not None
            and self.expected_transcript_root == self.observed_transcript_root
        )


def _result_payload(value: BehaviorReplayResult) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "request_ref": value.request_ref.to_dict(),
        "knowledge_snapshot_id": value.knowledge_snapshot_id,
        "status": value.status.value,
        "failure_reason": None if value.failure_reason is None else value.failure_reason.value,
        "observations": [item.to_dict() for item in value.observations],
        "transition_hash_chain": list(value.transition_hash_chain),
        "recorded_activity_refs": [item.to_dict() for item in value.recorded_activity_refs],
        "consumed_activity_identities": list(value.consumed_activity_identities),
        "observed_transcript_root": value.observed_transcript_root,
        "expected_transcript_root": value.expected_transcript_root,
        "terminal_snapshot_digests": list(value.terminal_snapshot_digests),
        "steps_executed": value.steps_executed,
        "gas_consumed": value.gas_consumed,
        "executor_actor": value.executor_actor.value,
    }


def validate_replay_result(value: BehaviorReplayResult) -> None:
    if type(value) is not BehaviorReplayResult or getattr(value, "_trusted_seal", None) is not _RESULT_SEAL:
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "replay result is not factory sealed")
    if value.schema_version is not SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "replay result schema is unknown")
    if type(value.status) is not ReplayStatus:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "status must be an exact ReplayStatus")
    if type(value.executor_actor) is not ActorIdentity:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "executor actor must be exact")
    _ref(value.request_ref, "request_ref")
    _identifier(value.knowledge_snapshot_id, "knowledge_snapshot_id")
    for name in ("observations", "recorded_activity_refs", "terminal_snapshot_digests"):
        if type(getattr(value, name)) is not tuple:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{name} must be an exact tuple")
    for item in value.observations:
        validate_replay_observation(item)
    for item in value.recorded_activity_refs:
        _ref(item, "recorded activity ref")
    for name in ("transition_hash_chain", "consumed_activity_identities"):
        items = getattr(value, name)
        if type(items) is not tuple or any(type(item) is not str for item in items):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{name} must be a tuple of strings")
    _sha256(value.observed_transcript_root, "observed_transcript_root")
    if value.expected_transcript_root is not None:
        _sha256(value.expected_transcript_root, "expected_transcript_root")
    for item in value.terminal_snapshot_digests:
        _sha256(item, "terminal_snapshot_digest")
    _natural(value.steps_executed, "steps_executed", maximum=_MAX_STEPS)
    _natural(value.gas_consumed, "gas_consumed", maximum=2**53)

    # Status and reason must agree, in both directions. A REPLAY_IDENTICAL that
    # carries a reason, or a non-identical status with no reason at all, would
    # let a failure be recorded as an absence.
    if value.status is ReplayStatus.REPLAY_IDENTICAL:
        if value.failure_reason is not None:
            raise _fail(
                ReplayFailureCode.STATUS_REASON_INCONSISTENT,
                "REPLAY_IDENTICAL cannot carry a failure reason",
            )
        if not value.root_matches_expectation:
            raise _fail(
                ReplayFailureCode.STATUS_REASON_INCONSISTENT,
                "REPLAY_IDENTICAL requires a transcript root pinned before the run",
            )
        if any(item.failure_reason is not None or not item.transcript_matched for item in value.observations):
            raise _fail(
                ReplayFailureCode.STATUS_REASON_INCONSISTENT,
                "REPLAY_IDENTICAL requires every observation to have matched",
            )
    else:
        if type(value.failure_reason) is not ReplayFailureReason:
            raise _fail(
                ReplayFailureCode.STATUS_REASON_INCONSISTENT,
                "a non-identical replay must name a typed failure reason",
            )
        if status_for_reason(value.failure_reason) is not value.status:
            raise _fail(
                ReplayFailureCode.STATUS_REASON_INCONSISTENT,
                "the recorded status is not the one this reason produces",
            )

    expected_root = transcript_root(
        transitions=value.transition_hash_chain,
        activities=value.consumed_activity_identities,
    )
    if value.observed_transcript_root != expected_root:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the transcript root does not fold the recorded transcript",
        )
    try:
        validate_record_id(value.result_id, canonical_bytes=_canonical(_result_payload(value)))
    except ContractViolation as exc:
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "result_id does not match its payload") from exc


def _seal_result(
    *,
    request: BehaviorReplayRequest,
    status: ReplayStatus,
    failure_reason: ReplayFailureReason | None,
    observations: tuple[ReplayObservation, ...],
) -> BehaviorReplayResult:
    transitions: list[str] = []
    activities: list[str] = []
    digests: list[str] = []
    steps = 0
    gas = 0
    for item in observations:
        transitions.extend(item.transition_hash_chain)
        activities.extend(item.consumed_activity_identities)
        digests.append(item.terminal_snapshot_digest)
        steps += item.steps_executed
        gas += item.gas_consumed

    payload = object.__new__(BehaviorReplayResult)
    object.__setattr__(payload, "schema_version", SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1)
    object.__setattr__(payload, "request_ref", replay_request_ref(request))
    object.__setattr__(payload, "knowledge_snapshot_id", request.knowledge_snapshot_id)
    object.__setattr__(payload, "status", status)
    object.__setattr__(payload, "failure_reason", failure_reason)
    object.__setattr__(payload, "observations", observations)
    object.__setattr__(payload, "transition_hash_chain", tuple(transitions))
    object.__setattr__(payload, "recorded_activity_refs", request.recorded_activity_refs)
    object.__setattr__(payload, "consumed_activity_identities", tuple(activities))
    object.__setattr__(
        payload,
        "observed_transcript_root",
        transcript_root(transitions=tuple(transitions), activities=tuple(activities)),
    )
    object.__setattr__(payload, "expected_transcript_root", request.expected_transcript_root)
    object.__setattr__(payload, "terminal_snapshot_digests", tuple(digests))
    object.__setattr__(payload, "steps_executed", steps)
    object.__setattr__(payload, "gas_consumed", gas)
    object.__setattr__(payload, "executor_actor", request.executor_actor)
    object.__setattr__(payload, "_trusted_seal", _RESULT_SEAL)
    object.__setattr__(
        payload,
        "result_id",
        compute_record_id(
            domain=IdentityDomain.BEHAVIOR_REPLAY_RESULT,
            canonical_bytes=_canonical(_result_payload(payload)),
        ),
    )
    validate_replay_result(payload)
    return payload


def replay_result_ref(value: BehaviorReplayResult) -> HashBoundRef:
    validate_replay_result(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.result_id.digest_sha256,
        schema_id=SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# execute_replay
# ---------------------------------------------------------------------------


def _first_unexpected_index(contract: ReplayContract, transitions: tuple[str, ...]) -> int | None:
    expected = frozenset(contract.expected_transition_ids)
    for index, item in enumerate(transitions):
        if item not in expected:
            return index
    return None


def _transcript_matches(
    contract: ReplayContract, *, transitions: tuple[str, ...], activities: tuple[str, ...]
) -> bool:
    """Compare a transcript against its contract by set *and* count.

    Either check alone is defeatable: a count check passes when one transition
    is swapped for another, and a set check passes when a duplicate hides an
    omission. A missing transition is a mismatch, never a silence.
    """

    expected_transitions = frozenset(contract.expected_transition_ids)
    if len(transitions) != len(expected_transitions):
        return False
    if frozenset(transitions) != expected_transitions:
        return False
    expected_activities = frozenset(contract.expected_activity_ids)
    if len(activities) != len(expected_activities):
        return False
    return frozenset(activities) == expected_activities


def _check_execution_contract(
    binding: ReplayProgramBinding, machine: ReplayMachinePort
) -> ReplayFailureReason | None:
    """The exact execution contract §23 requires, checked before any transition.

    This is the last point at which a substituted program is still invisible in
    the transcript.
    """

    loaded = machine.program_hash()
    if type(loaded) is not str or loaded != binding.program_hash:
        return ReplayFailureReason.PROGRAM_HASH_MISMATCH
    abi = machine.host_abi_version()
    if type(abi) is not str or abi != binding.host_abi_version:
        return ReplayFailureReason.HOST_ABI_MISMATCH
    return None


def execute_replay(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
) -> BehaviorReplayResult:
    """Run one governed replay attempt over the ordered admitted behavior set.

    One machine per behavior, in the request's order. Every stopping condition
    produces a result rather than an exception: a replay that raised on
    divergence would lose the evidence of what it saw, and NR-13 requires all
    attempts to be preserved, not only the successful ones. Only a request that
    cannot be executed at all — a malformed one, or the wrong number of machines
    — raises.
    """

    validate_replay_request(request)
    if type(machines) is not tuple:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "machines must be an exact tuple")
    if len(machines) != len(request.bindings):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "one machine is required for each admitted behavior",
        )
    for machine in machines:
        require_machine_port(machine)

    if request.capability_profile_digest != capability_profile_digest():
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_INCOMPATIBLE,
            failure_reason=ReplayFailureReason.CAPABILITY_PROFILE_MISMATCH,
            observations=(),
        )

    channel = RecordedActivityChannel(
        request.ledger, request.cognitive_budget, _seal=_CHANNEL_SEAL
    )
    observations: list[ReplayObservation] = []
    failure_reason: ReplayFailureReason | None = None
    try:
        for binding, machine in zip(request.bindings, machines):
            incompatible = _check_execution_contract(binding, machine)
            if incompatible is not None:
                failure_reason = incompatible
                break
            # Only now, once the execution contract holds. A machine running a
            # program other than the bound one must never see the channel.
            machine.attach_channel(channel)
            observation, failure_reason = _replay_one_behavior(
                request, binding=binding, machine=machine, channel=channel
            )
            observations.append(observation)
            if failure_reason is not None:
                break
    finally:
        channel.close()

    sealed = tuple(observations)
    if failure_reason is not None:
        return _seal_result(
            request=request,
            status=status_for_reason(failure_reason),
            failure_reason=failure_reason,
            observations=sealed,
        )

    # Every behavior ran to its natural end. Identity still has to be earned.
    if any(not item.transcript_matched for item in sealed):
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_FAILED,
            failure_reason=ReplayFailureReason.TRANSITION_MISMATCH,
            observations=sealed,
        )
    if request.expected_terminal_snapshot_digests is not None:
        observed = tuple(item.terminal_snapshot_digest for item in sealed)
        if observed != request.expected_terminal_snapshot_digests:
            return _seal_result(
                request=request,
                status=ReplayStatus.REPLAY_FAILED,
                failure_reason=ReplayFailureReason.SNAPSHOT_TAMPERED,
                observations=sealed,
            )
    root = transcript_root(
        transitions=tuple(item for obs in sealed for item in obs.transition_hash_chain),
        activities=tuple(item for obs in sealed for item in obs.consumed_activity_identities),
    )
    if request.expected_transcript_root is None or request.expected_transcript_root != root:
        # Matching the contract's sorted sets is not identity: a transcript that
        # visits the same transitions in another order satisfies them and is a
        # different execution. Without a root pinned in advance there is nothing
        # that could distinguish the two, so identity is not established.
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_FAILED,
            failure_reason=ReplayFailureReason.TRANSITION_MISMATCH,
            observations=sealed,
        )
    return _seal_result(
        request=request,
        status=ReplayStatus.REPLAY_IDENTICAL,
        failure_reason=None,
        observations=sealed,
    )


def _replay_one_behavior(
    request: BehaviorReplayRequest,
    *,
    binding: ReplayProgramBinding,
    machine: ReplayMachinePort,
    channel: RecordedActivityChannel,
) -> tuple[ReplayObservation, ReplayFailureReason | None]:
    """Execute one behavior and return its observation plus any stopping reason."""

    initial_digest = _sha256(machine.snapshot_digest(), "initial_snapshot_digest")
    consumed_before = len(channel.consumed_identities())
    transitions: list[str] = []
    gas_start = machine.gas_remaining()
    gas_previous = gas_start
    steps = 0
    reason: ReplayFailureReason | None = None

    while True:
        if machine.is_halted():
            break
        if steps >= request.step_limit:
            reason = ReplayFailureReason.STEP_LIMIT_REACHED
            break
        opcode = machine.next_opcode()
        if opcode is None:
            break
        try:
            classify_replay_opcode(opcode)
        except ReplayViolation:
            reason = ReplayFailureReason.UNKNOWN_HOST_CALL
            break
        if gas_start - machine.gas_remaining() >= request.gas_budget:
            reason = ReplayFailureReason.GAS_EXHAUSTED
            break

        try:
            machine.step()
        except ActivityViolation as exc:
            reason = reason_for_activity_failure(exc)
            break
        except ReplayViolation as exc:
            # The adapter refusing an effect with no channel, or a closed one.
            reason = (
                ReplayFailureReason.SIDE_EFFECT_OUTSIDE_PLAN
                if exc.failure_code is ReplayFailureCode.CHANNEL_CLOSED
                else ReplayFailureReason.MACHINE_FAULT
            )
            break
        except Exception:  # noqa: BLE001 - a faulting machine is evidence, not a crash
            reason = ReplayFailureReason.MACHINE_FAULT
            break

        observed = machine.transition_hash()
        if type(observed) is not str or not observed:
            reason = ReplayFailureReason.MACHINE_FAULT
            break
        transitions.append(observed)
        steps += 1

        remaining = machine.gas_remaining()
        if type(remaining) is not int or remaining > gas_previous:
            raise _fail(
                ReplayFailureCode.GAS_NOT_MONOTONE,
                "gas increased during a replay, so this is not the modelled cost function",
            )
        gas_previous = remaining

    chain = tuple(transitions)
    consumed = channel.consumed_identities()[consumed_before:]
    keys = channel.consumed_keys()[consumed_before:]
    matched = reason is None and _transcript_matches(
        binding.replay_contract, transitions=chain, activities=consumed
    )
    if reason is None and not matched:
        reason = ReplayFailureReason.TRANSITION_MISMATCH

    observation = _seal_observation(
        behavior_content_key=binding.behavior_content_key,
        program_hash=binding.program_hash,
        host_abi_version=binding.host_abi_version,
        transition_hash_chain=chain,
        consumed_activity_identities=consumed,
        consumed_activity_keys=keys,
        initial_snapshot_digest=initial_digest,
        terminal_snapshot_digest=_sha256(machine.snapshot_digest(), "terminal_snapshot_digest"),
        steps_executed=steps,
        gas_consumed=max(0, gas_start - gas_previous),
        transcript_matched=matched,
        first_unexpected_index=_first_unexpected_index(binding.replay_contract, chain),
        failure_reason=reason,
    )
    return observation, reason


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def resume_replay(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    resumed_from: BehaviorReplayResult,
) -> BehaviorReplayResult:
    """Continue a replay from a recorded result, verifying what it resumes from.

    §23 requires program hash, snapshot and activity history to be verified on
    resume. All three are checked here against the earlier result, before a
    single transition is taken:

    * the program hashes must be the ones that result was produced under, or
      the continuation is attached to different code;
    * each machine's snapshot digest must be the terminal digest that result
      recorded, or the continuation starts from a state the earlier run never
      reached;
    * the recorded activity refs must be the same set, or the continuation is
      drawing on an activity history the earlier run did not have.

    A mismatch produces a typed, fail-closed result rather than a resumed run.
    """

    validate_replay_request(request)
    validate_replay_result(resumed_from)
    if type(machines) is not tuple or len(machines) != len(request.bindings):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "one machine is required for each admitted behavior",
        )
    for machine in machines:
        require_machine_port(machine)

    if request.program_hashes != tuple(item.program_hash for item in resumed_from.observations):
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_INCOMPATIBLE,
            failure_reason=ReplayFailureReason.PROGRAM_HASH_MISMATCH,
            observations=(),
        )
    if request.recorded_activity_refs != resumed_from.recorded_activity_refs:
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_FAILED,
            failure_reason=ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH,
            observations=(),
        )
    observed = tuple(machine.snapshot_digest() for machine in machines)
    if observed != resumed_from.terminal_snapshot_digests:
        return _seal_result(
            request=request,
            status=ReplayStatus.REPLAY_INCOMPATIBLE,
            failure_reason=ReplayFailureReason.SNAPSHOT_INCOMPATIBLE,
            observations=(),
        )
    return execute_replay(request, machines=machines)


__all__ = [
    "ACTIVITY_KIND_BY_OPCODE",
    "RECORDED_ONLY_OPCODES",
    "REPLAY_ADMISSIBLE_OPCODES",
    "REPLAY_CAPABILITY_PROFILE_V1",
    "BehaviorReplayRequest",
    "BehaviorReplayResult",
    "CognitiveVMReplayAdapter",
    "RecordedActivityChannel",
    "ReplayFailureCode",
    "ReplayFailureReason",
    "ReplayMachinePort",
    "ReplayObservation",
    "ReplayProgramBinding",
    "ReplayStatus",
    "ReplayViolation",
    "activity_kind_for_opcode",
    "capability_profile_digest",
    "classify_replay_opcode",
    "create_replay_request",
    "execute_replay",
    "reason_for_activity_failure",
    "replay_program_binding",
    "replay_request_ref",
    "replay_result_ref",
    "require_machine_port",
    "resume_replay",
    "status_for_reason",
    "transcript_root",
    "validate_replay_observation",
    "validate_replay_request",
    "validate_replay_result",
]
