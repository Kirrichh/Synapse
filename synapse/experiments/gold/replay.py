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
import inspect
import json
from typing import Protocol, runtime_checkable

from synapse.bytecode import BytecodeProgram
from synapse.cvm import (
    GAS_BACK_EDGE,
    GAS_COSTS,
    CognitiveVM,
    FunctionObject,
    VMState,
    decode_vm_value,
    encode_vm_value,
)

from .activities import (
    ACTIVITY_RESULT_CODEC_V1 as _ACTIVITY_RESULT_CODEC_V1,
    ActivityFailureCode,
    ActivityInputs,
    ActivityKind,
    ActivityLedger,
    ActivityPosition,
    ActivityViolation,
    RecordedActivity,
    activity_ref,
    activity_inputs,
    seal_activity_ledger,
)
from .behavior import (
    ReplayContract,
    SynapseBehaviorUnit,
    validate_behavior_unit,
    validate_compiler_binding_for_unit,
)
from .point_of_use import (
    CurrentAdmittedKnowledge,
    ProductionAuthorityBinding,
    admit_for_use_now,
    require_admitted_subjects,
    require_current_point_of_use_evidence,
    require_point_of_use_admission_request,
    validate_current_admitted_knowledge,
    validate_production_authority_binding,
)
from .admission import AdmissionFailureCode, AdmissionViolation, canonical_subject_refs
from .canonicalization import (
    GOLD_LIBRARY_SUBJECT_V1,
    content_key_digest,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    CompilerBinding,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    common_envelope_from_dict,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    validate_envelope_bound_record,
)

REPLAY_CAPABILITY_PROFILE_V1 = "synapse.stage4.gold.replay-capability-profile/v1"
_PROFILE_PREFIX = REPLAY_CAPABILITY_PROFILE_V1.encode("utf-8") + b"\x00"

_REQUEST_SEAL = object()
_RESULT_SEAL = object()
_OBSERVATION_SEAL = object()
_CHANNEL_SEAL = object()
_PRODUCTION_REPLAY_BINDING_SEAL = object()

_IDENTIFIER_MAX = 128
_SHA256_LENGTH = 64
_MAX_STEPS = 1_000_000
_MAX_SUBJECTS = 512
_MAX_BEHAVIORS = 64

#: Named once because every Stage 9 record carries it. §13 requires a versioned
#: platform component identity in the envelope, and three records produced by one
#: owner should not disagree about who produced them.
REPLAY_PRODUCER_COMPONENT_V1 = "synapse.stage4.gold.replay.v1"


class ProductionReplayBinding:
    """One sealed authority, policy entitlement and Stage 9 durability domain."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _PRODUCTION_REPLAY_BINDING_SEAL or kwargs or len(args) != 8:
            raise TypeError("ProductionReplayBinding is factory-created")
        (
            self.authority,
            self.initial_admission,
            self.final_admission,
            self.activity_policy_evaluator,
            self.activity_store,
            self.activity_policy_store,
            self.replay_store,
            self.executor_actor,
        ) = args
        self._configuration_snapshot = args
        self._trusted_seal = _PRODUCTION_REPLAY_BINDING_SEAL

    @property
    def fence(self):
        return self.authority.fence

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_trusted_seal", None) is _PRODUCTION_REPLAY_BINDING_SEAL:
            raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "production replay binding is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "production replay binding is immutable")


def create_production_replay_binding(
    *,
    authority: ProductionAuthorityBinding,
    initial_admission: object,
    final_admission: object,
    activity_policy_evaluator: object,
    activity_store: object,
    activity_policy_store: object,
    replay_store: object,
    executor_actor: ActorIdentity,
) -> ProductionReplayBinding:
    """Bind exact production types to the authority's exact coordinator."""

    from .activity_policy import (
        ConfiguredActivityPolicyEvaluator,
        require_activity_policy_execution_entitlement,
    )
    from .activity_policy_store import FileActivityPolicyStore
    from .activity_store import FileActivityStore
    from .replay_store import FileReplayStore

    validate_production_authority_binding(authority)
    initial = require_point_of_use_admission_request(initial_admission)
    final = require_point_of_use_admission_request(final_admission)
    if final.binding is not authority:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the final admission belongs to another production authority",
        )
    initial_authority = initial.binding
    if (
        initial_authority.fence is not authority.fence
        or any(
            getattr(initial_authority, name) is not getattr(authority, name)
            for name in (
                "lifecycle_store", "attestation_store", "taint_store",
                "admission_journal", "admission_causal_history",
                "compatibility_history", "knowledge_store",
            )
        )
    ):
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "initial and final admissions do not share one exact authority domain",
        )
    exact = (
        (activity_policy_evaluator, ConfiguredActivityPolicyEvaluator, "activity policy evaluator"),
        (activity_store, FileActivityStore, "activity store"),
        (activity_policy_store, FileActivityPolicyStore, "activity policy store"),
        (replay_store, FileReplayStore, "replay store"),
    )
    for value, expected, name in exact:
        if type(value) is not expected:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"production replay requires an exact {name}")
    require_activity_policy_execution_entitlement(
        activity_policy_evaluator,
        executor_actor=executor_actor,
    )
    if (
        activity_policy_evaluator._lifecycle_store is not authority.lifecycle_store
        or activity_policy_evaluator._taint_store is not authority.taint_store
    ):
        raise _fail(
            ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
            "the activity policy evaluator is bound to another authority state",
        )
    for store in (activity_store, activity_policy_store, replay_store):
        if store.mutation_fence is not authority.fence:
            raise _fail(
                ReplayFailureCode.ADMISSION_NOT_CURRENT,
                "all Stage 9 stores must share the exact authority coordinator",
            )
    return validate_production_replay_binding(
        ProductionReplayBinding(
            authority,
            initial,
            final,
            activity_policy_evaluator,
            activity_store,
            activity_policy_store,
            replay_store,
            executor_actor,
            _seal=_PRODUCTION_REPLAY_BINDING_SEAL,
        )
    )


def validate_production_replay_binding(value: object) -> ProductionReplayBinding:
    from .activity_policy import require_activity_policy_execution_entitlement

    if (
        type(value) is not ProductionReplayBinding
        or getattr(value, "_trusted_seal", None) is not _PRODUCTION_REPLAY_BINDING_SEAL
    ):
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "production replay binding is not sealed")
    current = (
        value.authority,
        value.initial_admission,
        value.final_admission,
        value.activity_policy_evaluator,
        value.activity_store,
        value.activity_policy_store,
        value.replay_store,
        value.executor_actor,
    )
    snapshot = getattr(value, "_configuration_snapshot", None)
    if type(snapshot) is not tuple or len(snapshot) != len(current) or any(
        actual is not configured for actual, configured in zip(current, snapshot)
    ):
        raise _fail(ReplayFailureCode.TRUSTED_OBJECT_FORGED, "production replay binding changed")
    validate_production_authority_binding(value.authority)
    initial = require_point_of_use_admission_request(value.initial_admission)
    if require_point_of_use_admission_request(value.final_admission).binding is not value.authority:
        raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "final admission authority changed")
    if (
        initial.binding.fence is not value.fence
        or any(
            getattr(initial.binding, name) is not getattr(value.authority, name)
            for name in (
                "lifecycle_store", "attestation_store", "taint_store",
                "admission_journal", "admission_causal_history",
                "compatibility_history", "knowledge_store",
            )
        )
    ):
        raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "initial authority domain changed")
    require_activity_policy_execution_entitlement(
        value.activity_policy_evaluator,
        executor_actor=value.executor_actor,
    )
    for store in (value.activity_store, value.activity_policy_store, value.replay_store):
        if store.mutation_fence is not value.fence:
            raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "a Stage 9 store changed coordinator")
        if store.mutation_fence.coordinator_id() != value.fence.coordinator_id():
            raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "a Stage 9 coordinator identity differs")
    return value


def _envelope_for(
    *,
    schema_version: SchemaVersion,
    identity_domain: IdentityDomain,
    payload: dict[str, object],
    admitted: CurrentAdmittedKnowledge,
    created_at_utc,
) -> tuple[CommonEnvelope, str]:
    """Wrap one Stage 9 payload in the §13 envelope, sourced from the admission.

    Every envelope field is taken from the admission this record exists under
    rather than from the caller. Run, attempt, repository revision, policy
    version and environment profile are the execution identity of the crossing
    that made the record permissible, and a record free to state a different run
    or a different environment than the admission it rests on would be a record
    whose envelope describes nothing in particular.

    ``lineage_parent_ids`` is deliberately empty. Restoring a parent reference
    requires the parent's exact canonical bytes, so an envelope carrying one
    could not be parsed without also holding the record above it; Stage 9 states
    its lineage in the domain payload instead — the result names its request, and
    a continuation names the result it continues — where a reference is a
    hash-bound ref that resolves on its own.
    """

    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=identity_domain,
        canonical_payload_bytes=_canonical(payload),
        run_id=admitted.envelope.run_id,
        attempt_id=admitted.envelope.attempt_id,
        created_at_utc=created_at_utc,
        producer_component=REPLAY_PRODUCER_COMPONENT_V1,
        repository_revision=admitted.envelope.repository_revision,
        policy_version=admitted.policy_version,
        environment_profile_id=admitted.envelope.environment_profile_id,
        lineage_parent_ids=(),
    )
    return envelope, compute_envelope_binding_sha256(envelope)


def _require_envelope_bound(
    *,
    envelope: object,
    envelope_binding_sha256: object,
    payload: dict[str, object],
    identity_domain: IdentityDomain,
    field_name: str,
) -> None:
    """Validate the v2 envelope against the exact payload it claims to bind."""

    if type(envelope) is not CommonEnvelope:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} must carry an exact envelope")
    _sha256(envelope_binding_sha256, f"{field_name} envelope binding")
    try:
        validate_envelope_bound_record(
            envelope=envelope,
            envelope_binding_sha256=envelope_binding_sha256,
            canonical_domain_payload_bytes=_canonical(payload),
            expected_identity_domain=identity_domain,
        )
    except ContractViolation as exc:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            f"{field_name} envelope does not bind this exact payload",
        ) from exc


# ---------------------------------------------------------------------------
# The allowed Gold replay host-call profile (§23, frozen decision)
# ---------------------------------------------------------------------------

#: Opcodes whose successor state is a function of the current state alone.
#: Replaying one re-derives its result; nothing needs to be recorded.
REPLAY_ADMISSIBLE_OPCODES = frozenset(
    {
        "LOAD_CONST", "LOAD_NAME", "LOAD_NONE", "LOAD_TRUE", "LOAD_FALSE",
        "STORE", "POP", "DUP", "SAVE_NAME", "RESTORE_NAME",
        "JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE", "RETURN",
        "MAKE_FUNCTION", "HALT",
        "ADD", "SUB", "MUL", "DIV", "MOD",
        "EQ", "NEQ", "LT", "GT", "LTE", "GTE",
        "AND", "OR", "NOT", "UNARY_NEG",
        "BUILD_LIST", "BUILD_DICT", "INDEX", "MEMBER",
        "CONTEXT_ENTER", "CONTEXT_EXIT",
        "ACTOR_ENTER", "ACTOR_EXIT",
        "POLICY_ENTER", "POLICY_EXIT", "POLICY_RULE_ENTER", "POLICY_RULE_EXIT",
        "GUARD_ENTER", "GUARD_EXIT", "GUARD_CHECK_RESULT", "GUARD_VIOLATION_ACK",
        "RECEIVE_ENTER", "RECEIVE_EXIT",
    }
)

#: Dispatch opcodes whose determinism is a property of the *occurrence*, not of
#: the instruction. They were in the admissible set, and that was wrong in a way
#: no table could fix by moving them: ``CALL`` executes ``fn(*args)`` directly
#: when the callee is an ordinary Python callable, and ``CALL_METHOD`` executes
#: ``getattr(obj, name)(*args)``. Neither reaches the machine's host routing, so
#: neither reaches the recorded-activity channel — arbitrary Python would run
#: inside an operation whose entire claim is that nothing unrecorded happens.
#:
#: They are also not simply effect-bearing: dispatching to a compiled Synapse
#: ``FunctionObject`` is an ordinary internal frame, and a dictionary member read
#: is a pure lookup. So the class is neither half of the binary, and the profile
#: has three members rather than two.
#:
#: What the adapter does with them is decide, per occurrence and *before* the
#: machine dispatches: an internal function or a member read proceeds, a call
#: that would reach the host proceeds through the governed channel, and a call
#: that would reach arbitrary Python is refused with no side effect. Refusing is
#: the only available answer for that last case rather than a chosen one — the
#: machine performs the call itself with no interception point, and NR-03 forbids
#: this stage to add one.
DISPATCH_GUARDED_OPCODES = frozenset({"CALL", "CALL_METHOD"})

#: Opcodes the machine may charge a back-edge for. Whether it does depends on the
#: jump target, and a ``ReplayMachinePort`` does not expose the target — so a
#: budget preflight charges the possibility. That can only stop a run early,
#: never let one run past its budget, which is the direction to be wrong in.
_BACK_EDGE_OPCODES = frozenset({"JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE"})

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
            "dispatch_guarded": sorted(DISPATCH_GUARDED_OPCODES),
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
    RESUME_LINEAGE_MISMATCH = "RESUME_LINEAGE_MISMATCH"
    ADMISSION_NOT_CURRENT = "ADMISSION_NOT_CURRENT"
    SUBJECT_NOT_ADMITTED = "SUBJECT_NOT_ADMITTED"
    SNAPSHOT_BINDING_MISMATCH = "SNAPSHOT_BINDING_MISMATCH"
    UNGOVERNED_DISPATCH = "UNGOVERNED_DISPATCH"
    RESULT_NOT_DECODABLE = "RESULT_NOT_DECODABLE"
    ACTIVITY_NOT_GOVERNED = "ACTIVITY_NOT_GOVERNED"
    NON_CANONICAL_VM_VALUE = "NON_CANONICAL_VM_VALUE"


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


def _ref_key(value: object) -> str:
    """A hash-bound reference compared by its whole content, never by ref_id.

    Two references can agree on ``ref_id`` and disagree on the schema, digest or
    length that make it mean something, so comparing the identity field alone
    would let a reference to one record stand in for a reference to another.
    """

    if type(value) is not HashBoundRef:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact HashBoundRef is required")
    return json.dumps(value.to_dict(), sort_keys=True, separators=(",", ":"))


def _ref(value: object, field_name: str, *, expected_kind: RefKind | None = None) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact HashBoundRef")
    if expected_kind is not None and value.kind is not expected_kind:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} has the wrong reference kind")
    return value


def classify_replay_opcode(opcode: str) -> str:
    """Return the determinism class of an opcode, or raise.

    Three classes, total and disjoint over the opcodes the machine can charge
    gas for: ``"admissible"`` is deterministic in the machine, ``"recorded_only"``
    is an effect that must resolve to a recorded activity, and
    ``"dispatch_guarded"`` is a dispatch whose class depends on what it is about
    to call and is therefore decided per occurrence, before the call.

    There is no default and no fourth answer. An opcode the profile does not name
    has no determinism class, and executing under an unknown determinism class is
    what the profile exists to prevent — so an unknown opcode is refused, rather
    than covered by an allowlist that grew until nothing was unknown.
    """

    _identifier(opcode, "opcode")
    if opcode in REPLAY_ADMISSIBLE_OPCODES:
        return "admissible"
    if opcode in RECORDED_ONLY_OPCODES:
        return "recorded_only"
    if opcode in DISPATCH_GUARDED_OPCODES:
        return "dispatch_guarded"
    raise _fail(
        ReplayFailureCode.OPCODE_NOT_CLASSIFIED,
        f"{opcode} has no determinism class in the replay capability profile",
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

    One channel serves one replay attempt. It is opened by ``run_governed_replay``,
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
        if kwargs.pop("_seal", None) is not _CHANNEL_SEAL or kwargs or len(args) != 3:
            raise TypeError("RecordedActivityChannel is created only by run_governed_replay")
        ledger, cognitive_budget, results = args
        if type(ledger) is not ActivityLedger:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a channel requires an exact ActivityLedger")
        if not callable(getattr(results, "open_result", None)):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "a channel requires the durable store the recorded results live in",
            )
        self._ledger = ledger
        self._results = results
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
        self._keys.append(found.lookup_key)
        return found

    def open_result(self, activity: RecordedActivity) -> bytes:
        """Return the exact bytes this activity recorded, verified on the way out.

        The store re-reads the blob and re-derives its digest, and this compares
        the answer against the record as well. Two checks of the same fact, from
        two directions: the store proves the bytes are the bytes its reference
        names, and this proves that reference is the one *this record* recorded.
        Either alone leaves a gap — a record could name someone else's intact
        blob, or a blob could be rewritten under a reference that still parses.
        """

        try:
            raw = self._results.open_result(activity.result_ref)
        except ActivityViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - the store speaks its own vocabulary
            # Translated rather than propagated, and translated by name rather
            # than by type: the channel must not import the store to know what
            # its refusals mean, and the two facts it needs to keep apart —
            # "there is no such blob" and "the blob is not what it claims" — are
            # exactly the two the store already distinguishes.
            code = getattr(getattr(exc, "failure_code", None), "value", "")
            raise ActivityViolation(
                ActivityFailureCode.ACTIVITY_NOT_RECORDED
                if code == "RESULT_UNAVAILABLE"
                else ActivityFailureCode.RESULT_HASH_MISMATCH,
                "the recorded result could not be produced from the durable store",
            ) from exc
        if hashlib.sha256(raw).hexdigest() != activity.result_sha256:
            raise ActivityViolation(
                ActivityFailureCode.RESULT_HASH_MISMATCH,
                "the stored bytes do not hash to what this activity recorded",
            )
        return raw

    def consumed_identities(self) -> tuple[str, ...]:
        return tuple(self._consumed)

    def consumed_lookup_keys(self) -> tuple[str, ...]:
        """The pre-result keys the run resolved by, in consumption order."""

        return tuple(self._keys)


# ---------------------------------------------------------------------------
# CognitiveVMReplayAdapter — the one adapter point into the protected core
# ---------------------------------------------------------------------------

_ADAPTER_PROFILE = b"synapse.stage4.gold.replay-machine-port/v1\x00"


#: The exact value types a governed replay may hold in machine state.
#:
#: Closed, and closed because of what the machine does with anything else. Both
#: ``encode_vm_value`` and ``_hash_transition`` fall back to ``repr(value)`` for a
#: value they do not recognise, and ``repr`` is *the value's own code*. A state
#: carrying a hostile object therefore runs that code inside ``snapshot_digest``
#: and inside every ``step`` — during the very measurements a replay's identity
#: is computed from, and while the replay's whole claim is that nothing
#: unrecorded happens. NR-03 forbids repairing those two functions from this
#: layer, so the answer is to never hand them a value they would have to guess
#: about.
#:
#: The set is deliberately *narrower* than what the encoder accepts. The encoder
#: tests with ``isinstance``, so a ``dict`` subclass with a hostile ``items`` or a
#: ``str`` subclass with a hostile ``__str__`` passes it; exact types are the only
#: form of this check that cannot be subclassed around.
CANONICAL_VM_SCALARS = (type(None), bool, int, float, str)

#: A value graph wider or deeper than this is refused rather than walked. Both
#: limits are fail-closed: the encoder would recurse just as far, so a value this
#: validator cannot afford to check is a value the machine cannot afford to hash.
_MAX_VM_VALUE_DEPTH = 64
_MAX_VM_VALUE_NODES = 8192


def require_canonical_vm_value(value: object, *, field: str = "value") -> None:
    """Refuse a machine value whose serialization would run its own code.

    Raises ``NON_CANONICAL_VM_VALUE`` for anything outside the closed vocabulary.
    Containers are checked to the leaves, because ``repr`` of a list is the
    ``repr`` of its elements — a canonical wrapper around a hostile object is
    still a hostile object.
    """

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
            walk(node.closure, depth + 1)
            return
        if kind is dict:
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


#: The parts of a machine state that can hold a value the machine did not make.
#: Everything else in ``VMState`` is the machine's own bookkeeping, serialized by
#: its own ``to_dict`` rather than through the opaque fallback.
_VM_VALUE_BEARING_FIELDS = ("stack", "locals")


def require_canonical_vm_state(state: VMState) -> VMState:
    """Refuse a machine state carrying values outside the closed vocabulary.

    Applied where a state crosses into this adapter from outside — construction
    and snapshot restore — because that is where a value the machine never
    produced can appear. A state assembled by the machine itself out of program
    constants, ``MAKE_FUNCTION`` and decoded recorded results is canonical by
    construction; one handed in is a claim.
    """

    if type(state) is not VMState:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact VMState is required")
    for name in _VM_VALUE_BEARING_FIELDS:
        require_canonical_vm_value(getattr(state, name, None), field=f"state.{name}")
    for index, frame in enumerate(getattr(state, "call_stack", ()) or ()):
        require_canonical_vm_value(
            getattr(frame, "locals_snapshot", None), field=f"state.call_stack[{index}].locals"
        )
    return state


#: The codec recorded result bytes are canonical under. Named and versioned
#: because a reference is only hash-bound if the reader and the writer agree on
#: what the bytes *are*: identical bytes read under a different codec are a
#: different value, and a replay that injected one for the other would be exactly
#: the substitution the digest was meant to prevent.
#: Re-exported rather than redeclared. ``activities.py`` declares the codec
#: beside the blob schema it qualifies, because that pair is what a result
#: reference means; this module implements it. Two independent spellings of one
#: identifier is how a codec silently forks.
ACTIVITY_RESULT_CODEC_V1 = _ACTIVITY_RESULT_CODEC_V1


def encode_recorded_result(value: object) -> bytes:
    """Encode a machine value as the exact bytes an activity record stores."""

    return json.dumps(
        encode_vm_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def decode_recorded_result(raw: bytes) -> object:
    """Decode stored bytes back into the machine value that was recorded.

    This is what makes "replay injects the recorded result" true rather than
    described. An earlier revision answered the machine with a dictionary built
    during the run — the opcode, a status string, the identity and the digest —
    every field of which was accurate and none of which was the result. The
    machine pushed that description onto its stack and carried on, and no reader
    downstream could tell, because the actual bytes were nowhere.
    """

    if type(raw) is not bytes:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a recorded result must be exact bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            "the recorded result is not canonical under the activity result codec",
        ) from exc
    value = decode_vm_value(decoded)
    # Parsing is not the check. JSON has many spellings of one value — b" 1 ",
    # b'{"b":1,"a":2}', b"[1,  2]" all parse — and every one of them is a
    # different byte string with a different digest and therefore a different
    # activity identity. Accepting them would mean two identities naming the same
    # injected value, which is exactly the collision identity exists to prevent,
    # running the other way. So the codec is enforced rather than declared: the
    # bytes must be the ones this codec would have produced for this value.
    if encode_recorded_result(value) != raw:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            f"the recorded result is not canonical under {ACTIVITY_RESULT_CODEC_V1}",
        )
    # The decoded value goes onto the machine's stack, so it is subject to the
    # same closed vocabulary as any other machine value. ``decode_vm_value``
    # cannot currently produce anything outside it, and that is a property of the
    # decoder rather than a promise of the codec — asserted here so a decoder
    # that gains a new type does not silently gain a new hazard.
    require_canonical_vm_value(value, field="recorded result")
    return value


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
        require_canonical_vm_state(vm_state)
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
        # ``cls`` validates the state's value vocabulary. It matters more here
        # than at an ordinary construction: a snapshot arrives as bytes from a
        # store, and ``VMState.from_dict`` will happily rebuild whatever those
        # bytes describe.
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
        # Checked before the machine serializes itself, not after. The encoder's
        # fallback for an unrecognised value is ``repr(value)``, so a hostile
        # object would run its own code *inside* the digest a replay's identity
        # is measured by — and a refusal afterwards would come one execution too
        # late. ``default=str`` below is the same hazard one layer up and is now
        # unreachable for the same reason.
        require_canonical_vm_state(self._vm.state)
        payload = json.dumps(
            self._vm.snapshot(), sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(_ADAPTER_PROFILE + payload).hexdigest()

    def step(self) -> None:
        # Captured before dispatch: the machine advances ``ip`` while executing,
        # so an activity positioned afterwards would carry the *next*
        # instruction's index and never resolve.
        self._pending_ip = self._vm.state.ip
        # Dispatch first, value vocabulary second, and the order carries meaning
        # rather than convenience. A Python callable about to be called is both
        # an ungoverned dispatch and a non-canonical value; the first names what
        # was about to happen and the second only names what it was made of, so
        # the specific refusal has to win. Neither check runs user code, so
        # asking them in this order costs nothing.
        self._require_dispatch_is_governed()
        # ``_hash_transition`` reprs the top of the stack on every transition, so
        # the hazard ``snapshot_digest`` guards against is present once per step
        # as well. Only the top is checked, because only the top is hashed.
        if self._vm.state.stack:
            require_canonical_vm_value(self._vm.state.stack[-1], field="stack top")
        self._vm.step()

    def _require_dispatch_is_governed(self) -> None:
        """Refuse a dispatch that would reach arbitrary Python — before it does.

        ``CALL`` and ``CALL_METHOD`` are the two instructions whose determinism
        is not a property of the instruction. The machine executes an ordinary
        Python callable inline for both, without passing through host routing, so
        the recorded-activity channel never sees it: a replay could run
        uninstrumented code in the middle of an operation whose whole claim is
        that nothing unrecorded happens.

        Three outcomes, decided here from the operand stack while the machine has
        not yet moved:

        * a compiled Synapse ``FunctionObject``, or a dictionary member read, is
          an internal transition and proceeds;
        * anything the machine would route to its host proceeds too, because that
          path ends at the governed channel;
        * an ordinary Python callable is refused, with the stack untouched and no
          call performed.

        The third outcome is a refusal rather than a recorded activity because
        the machine performs that call itself and offers no interception point,
        and NR-03 does not permit this stage to add one. A behavior needing such
        a call must express it as a host symbol, which is governed.
        """

        instructions = self._vm.program.instructions
        index = self._vm.state.ip
        if index < 0 or index >= len(instructions):
            return
        instruction = instructions[index]
        opcode = str(instruction.op)
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
        if not isinstance(argc, int) or argc < 0 or len(stack) < argc + 1:
            return
        subject = stack[-(argc + 1)]
        # Two rules, and the order between them is the fix.
        #
        # An earlier revision asked ``getattr(subject, name, None)`` here. That
        # is an ordinary attribute lookup, so a subject with its own
        # ``__getattribute__``, a property or a descriptor ran *its* code before
        # this function reached the refusal — the guard against executing
        # ungoverned code executed ungoverned code to decide. Confirmed by
        # reproduction: a subject that recorded a side effect from
        # ``__getattribute__`` recorded it, and only then was the dispatch
        # refused.
        #
        # So the subject must first be a canonical machine value, which none of
        # those hooks can be attached to, and the member is then read with
        # ``getattr_static`` — a lookup that walks the type's ``__dict__`` and
        # never invokes the descriptor protocol.
        require_canonical_vm_value(subject, field="CALL_METHOD subject")
        try:
            member = inspect.getattr_static(subject, str(instruction.a))
        except AttributeError:
            return
        if callable(member) and not isinstance(member, FunctionObject):
            raise _fail(
                ReplayFailureCode.UNGOVERNED_DISPATCH,
                "CALL_METHOD would execute an ordinary Python callable during a replay",
            )

    # --- host routing -------------------------------------------------------

    def _host(self, opcode: str, a: object, b: object) -> object:
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
        return decode_recorded_result(self._channel.open_result(recorded))


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
    #: The §13 envelope. It ties this record to the run, attempt, repository
    #: revision, policy version and environment profile of the admission it was
    #: produced under, and its record id *is* the replay id — an identity
    #: computed by the platform from canonical bytes, never chosen.
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    replay_id: RecordId
    #: §21 identity of the RepositoryKnowledgeSnapshot manifest this replay reads.
    #: Not the boundary: §21 gives the manifest and the committed transaction that
    #: publishes it separate identities, and a field that carried the boundary
    #: under a snapshot name would leave the request unable to say *which*
    #: selected knowledge state it ran over.
    knowledge_snapshot_id: str
    #: The exact manifest reference, resolved from the committed boundary rather
    #: than supplied. ``run_governed_replay`` re-reads the live boundary and compares,
    #: so this is a checkable claim about the world, not a label.
    snapshot_manifest_ref: HashBoundRef
    bindings: tuple[ReplayProgramBinding, ...]
    capability_profile: str
    capability_profile_digest: str
    recorded_activity_refs: tuple[HashBoundRef, ...]
    #: OD-10. One decision per recorded activity, named by hash-bound reference.
    #: A replay that consumed a recorded result without one would be answering
    #: "may this be used" by using it.
    activity_policy_decision_refs: tuple[HashBoundRef, ...]
    #: The §23 activity identities this run is pinned to. Result-bound: a
    #: substituted result keeps its lookup key and loses its identity, so the
    #: swap is visible here without re-reading the ledger.
    activity_identities: tuple[str, ...]
    ledger: ActivityLedger
    knowledge_subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    #: The present-time admission this request rests on, named by identity.
    #: A request that carried only refs would say which subjects were used and
    #: nothing about *which* revalidation admitted them, so a second, older
    #: admission over the same refs would satisfy every field here.
    admitted_knowledge_id: RecordId
    consumption_decision_id: RecordId
    policy_version: str
    gas_budget: int
    cognitive_budget: int
    step_limit: int
    expected_transcript_root: str | None
    expected_terminal_snapshot_digests: tuple[str, ...] | None
    #: The exact earlier result this request continues, or ``None`` for a fresh
    #: replay. Declared in the request rather than supplied to ``resume_replay``
    #: as a second argument: lineage stated at call time is a pairing the caller
    #: chooses, while lineage inside the request is part of its identity and
    #: cannot be re-pointed at another result afterwards.
    resumed_from_result_ref: HashBoundRef | None
    executor_actor: ActorIdentity
    #: The present-time admission this request was built under, carried as the
    #: object rather than as its identity alone. Two things need it and neither
    #: can be satisfied by a copied field: the validator resolves every authority
    #: field against it, and ``run_governed_replay`` re-checks it against the live
    #: authority state before the first transition. It is unforgeable —
    #: ``CurrentAdmittedKnowledge.__new__`` refuses — so a request cannot claim
    #: an admission that was never minted.
    #:
    #: It is deliberately outside the canonical payload. Identity is over what
    #: the request *says*; the admission is what makes saying it permissible, and
    #: hashing a revalidation result into a request would make the request's own
    #: identity depend on the moment it was admitted.
    admitted: CurrentAdmittedKnowledge
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
        """The durable form. Runtime capability is deliberately not in it.

        The sealed ledger and the minted admission live on the object and never
        in the payload: both are present-tense authority, and a persisted record
        that carried them would be offering to restore an authority from bytes.
        What the payload carries instead is what a reader can check — durable
        identities and hash-bound references, including the ledger's root.
        """

        validate_replay_request(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _request_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_replay_request(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_request_payload(self),
        )


def _request_payload(value: BehaviorReplayRequest) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "knowledge_snapshot_id": value.knowledge_snapshot_id,
        "snapshot_manifest_ref": value.snapshot_manifest_ref.to_dict(),
        "bindings": [item.to_dict() for item in value.bindings],
        "capability_profile": value.capability_profile,
        "capability_profile_digest": value.capability_profile_digest,
        "recorded_activity_refs": [item.to_dict() for item in value.recorded_activity_refs],
        "activity_policy_decision_refs": [
            item.to_dict() for item in value.activity_policy_decision_refs
        ],
        "activity_identities": list(value.activity_identities),
        "ledger_root": value.ledger.ledger_root(),
        "knowledge_subject_refs": [item.to_dict() for item in value.knowledge_subject_refs],
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "admitted_knowledge_id": value.admitted_knowledge_id.to_dict(),
        "consumption_decision_id": value.consumption_decision_id.to_dict(),
        "policy_version": value.policy_version,
        "gas_budget": value.gas_budget,
        "cognitive_budget": value.cognitive_budget,
        "step_limit": value.step_limit,
        "expected_transcript_root": value.expected_transcript_root,
        "expected_terminal_snapshot_digests": (
            None if value.expected_terminal_snapshot_digests is None
            else list(value.expected_terminal_snapshot_digests)
        ),
        "resumed_from_result_ref": (
            None if value.resumed_from_result_ref is None
            else value.resumed_from_result_ref.to_dict()
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
    for item in value.activity_policy_decision_refs:
        _ref(item, "activity policy decision ref")
    if len(value.activity_policy_decision_refs) != len(value.recorded_activity_refs):
        raise _fail(
            ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
            "every recorded activity needs exactly one activity policy decision",
        )
    for item in value.activity_identities:
        _sha256(item, "activity identity")
    if value.recorded_activity_refs != value.ledger.activity_refs():
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "recorded_activity_refs do not describe the sealed ledger",
        )
    if value.activity_identities != value.ledger.activity_identities():
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "the pinned activity identities do not describe the sealed ledger",
        )
    _ref(value.consumer_context_ref, "consumer_context_ref")
    _ref(value.boundary_ref, "boundary_ref", expected_kind=RefKind.ATOMIC_BOUNDARY)
    _ref(value.snapshot_manifest_ref, "snapshot_manifest_ref")
    for field_name in ("admitted_knowledge_id", "consumption_decision_id"):
        if type(getattr(value, field_name)) is not RecordId:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                f"{field_name} must be an exact record identity",
            )
    # §21 gives the selected knowledge state and the transaction that published
    # it two identities, and a replay needs both: the manifest says *what* was
    # selected, the boundary says that selection is committed and visible. An
    # earlier revision set the snapshot id to the boundary's, which made the
    # field a second name for the boundary and left the request unable to name
    # the manifest at all. The manifest reference is resolved from the committed
    # boundary at creation and re-read from the live boundary before execution,
    # so neither half is a caller's word.
    if value.knowledge_snapshot_id != value.snapshot_manifest_ref.ref_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "knowledge_snapshot_id is not the identity of the named snapshot manifest",
        )
    if _ref_key(value.boundary_ref) == _ref_key(value.snapshot_manifest_ref):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the snapshot manifest and its committed boundary cannot be one reference",
        )

    # Every authority field is resolved against the admission that produced it.
    # Without this the fields are assertions a restored, forged or mutated
    # request can make about itself: recomputing ``replay_id`` over a rewritten
    # payload is exactly what any restoration path does, so identity alone
    # refuses nothing. ``CurrentAdmittedKnowledge`` cannot be minted outside
    # ``admit_for_use_now``, which is what makes the comparison mean something.
    admitted = value.admitted
    if type(admitted) is not CurrentAdmittedKnowledge:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a replay request must carry the admission it was built under",
        )
    validate_current_admitted_knowledge(admitted)
    if value.admitted_knowledge_id.digest_sha256 != admitted.knowledge_id.digest_sha256:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "admitted_knowledge_id does not name the admission this request carries",
        )
    if (
        value.consumption_decision_id.digest_sha256
        != admitted.consumption_decision_id.digest_sha256
    ):
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "consumption_decision_id is not the decision that admission rests on",
        )
    if value.policy_version != admitted.policy_version:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "policy_version is not the one this replay was admitted under",
        )
    if _ref_key(value.consumer_context_ref) != _ref_key(admitted.consumer_context_ref):
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "consumer_context_ref is not the context this replay was admitted for",
        )
    if _ref_key(value.boundary_ref) != _ref_key(admitted.boundary_ref):
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "boundary_ref is not the boundary this replay was admitted against",
        )
    # The subject *set* is deliberately not compared here. The sealed ledger
    # stores the admitted refs and ``require_bound_to`` below compares the
    # request against them, so a second comparison would be one rule written
    # twice — the shape a mutant survives by, since removing either copy changes
    # nothing observable. What the ledger cannot say on its own is *which*
    # admission it was sealed under, so that is what is tied here.
    if value.ledger.admitted_knowledge_id.digest_sha256 != admitted.knowledge_id.digest_sha256:
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "the ledger was sealed under a different admission than this request",
        )
    # And the programs about to run are the admitted subjects. The factory ties
    # each reference to the unit it names, but a factory check protects only
    # objects that went through the factory: a restored or mutated request can
    # carry admitted references beside another behavior's compiled binding, and
    # until this comparison existed nothing downstream would notice. The tie is
    # checkable without a library lookup because a library subject reference is
    # content-addressed by the behavior's content-key digest.
    admitted_digests = frozenset(item.ref_id for item in admitted.subject_refs)
    binding_digests = [content_key_digest(item.behavior_content_key) for item in value.bindings]
    if len(binding_digests) != len(admitted_digests) or frozenset(binding_digests) != admitted_digests:
        raise _fail(
            ReplayFailureCode.SUBJECT_NOT_ADMITTED,
            "the compiled behaviors are not exactly the admitted subject set",
        )
    if value.resumed_from_result_ref is not None:
        lineage = _ref(value.resumed_from_result_ref, "resumed_from_result_ref")
        if lineage.schema_id != SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1.value:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "resumed_from_result_ref must name a replay result",
            )
    if value.ledger.policy_version != value.policy_version:
        raise _fail(
            ReplayFailureCode.LEDGER_NOT_BOUND,
            "the ledger was sealed under another policy version",
        )
    value.ledger.require_bound_to(
        consumer_context_ref=value.consumer_context_ref,
        boundary_ref=value.boundary_ref,
        knowledge_subject_refs=value.knowledge_subject_refs,
    )
    _require_envelope_bound(
        envelope=value.envelope,
        envelope_binding_sha256=value.envelope_binding_sha256,
        payload=_request_payload(value),
        identity_domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
        field_name="replay request",
    )
    if value.replay_id != value.envelope.record_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "replay_id is not the identity its envelope computed",
        )


@dataclass(frozen=True)
class ReplaySubject:
    """One behavior a replay will run, named as the §22 gates know it.

    The unit is *uncompiled*. A replay is about a program, but a gate decides
    about a library subject, and the two are joined here rather than left to the
    caller to assert: ``subject_ref`` is the reference the consumption gate
    admitted, ``unit`` is the behavior that reference denotes, and
    ``create_replay_request`` compiles the second only after the first has been
    admitted at the point of use.
    """

    subject_ref: HashBoundRef
    unit: SynapseBehaviorUnit


def _require_subject_names_unit(value: object) -> ReplaySubject:
    """Refuse a gate reference that does not name this exact behavior.

    Pairing an admitted reference with a behavior would otherwise be an
    assertion the caller makes: hand in the reference the gate admitted next to
    a different unit, and the admitted-subject comparison passes while an
    unadmitted program is what gets compiled. The tie is checkable without any
    library lookup because a library subject reference is content-addressed —
    its ``ref_id`` is the behavior blob digest — so the reference and the unit
    are compared on the one value that no two behaviors share: a library subject
    reference carries the behavior's content-key digest as its ``ref_id``, and a
    content key is derived from the canonical core, so two different behaviors
    cannot share one.
    """

    if type(value) is not ReplaySubject:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "replay subjects must be exact")
    _ref(value.subject_ref, "replay subject ref")
    if value.subject_ref.schema_id != GOLD_LIBRARY_SUBJECT_V1:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a replay subject must be named by a library subject ref",
        )
    validate_behavior_unit(value.unit)
    if value.subject_ref.ref_id != value.unit.content_key.digest_sha256:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the admitted subject ref does not name this behavior unit",
        )
    return value


def replay_subject(*, subject_ref: HashBoundRef, unit: SynapseBehaviorUnit) -> ReplaySubject:
    """Pair an admitted library subject with the behavior it names."""

    return _require_subject_names_unit(ReplaySubject(subject_ref=subject_ref, unit=unit))


@dataclass(frozen=True)
class _PreparedReplay:
    admitted: CurrentAdmittedKnowledge
    snapshot_manifest_ref: HashBoundRef
    bindings: tuple[ReplayProgramBinding, ...]
    ledger: ActivityLedger


def _admit_now(value: object) -> CurrentAdmittedKnowledge:
    request = require_point_of_use_admission_request(value)
    return admit_for_use_now(
        request.handle,
        binding=request.binding,
        chain=request.chain,
        evidence=request.evidence,
        entitlements=request.entitlements,
        requested=request.requested,
    )


def _require_subject_set(
    subjects: tuple[ReplaySubject, ...], admitted: CurrentAdmittedKnowledge
) -> None:
    if type(subjects) is not tuple or not subjects:
        raise _fail(ReplayFailureCode.BEHAVIOR_SET_EMPTY, "a replay needs at least one behavior")
    if len(subjects) > _MAX_BEHAVIORS:
        raise _fail(ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED, "behavior set exceeds the limit")
    for item in subjects:
        _require_subject_names_unit(item)
    require_admitted_subjects(
        admitted,
        subject_refs=canonical_subject_refs(tuple(item.subject_ref for item in subjects)),
        consumer_context_ref=admitted.consumer_context_ref,
    )


def _resolve_durable_activities(
    binding: ProductionReplayBinding,
    references: tuple[HashBoundRef, ...],
) -> tuple[RecordedActivity, ...]:
    validate_production_replay_binding(binding)
    if type(references) is not tuple:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "activity refs must be an exact tuple")
    resolved: list[RecordedActivity] = []
    for reference in references:
        record = binding.activity_store.require_record(
            _ref(reference, "recorded activity ref")
        )
        if activity_ref(record).to_dict() != reference.to_dict():
            raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "durable activity identity changed")
        binding.activity_store.open_result(record.result_ref)
        resolved.append(record)
    if len({item.activity_identity for item in resolved}) != len(resolved):
        raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "durable activity selection repeats a record")
    return tuple(resolved)


def _prepare_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    activity_refs: tuple[HashBoundRef, ...],
) -> _PreparedReplay:
    """Resolve durable facts, admit, compile, then independently admit again."""

    binding = validate_production_replay_binding(binding)
    initial_request = require_point_of_use_admission_request(admission)
    if initial_request is not binding.initial_admission:
        raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "initial admission is not the sealed request")
    if initial_request is binding.final_admission:
        raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "final revalidation needs distinct evidence input")

    activities = _resolve_durable_activities(binding, activity_refs)
    first = _admit_now(initial_request)
    _require_subject_set(subjects, first)

    if not callable(compiler):
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a replay needs a callable compiler")
    compiled = tuple(
        replay_program_binding(unit=item.unit, binding=compiler(item.unit))
        for item in subjects
    )

    final = _admit_now(binding.final_admission)
    if (
        final.knowledge_id == first.knowledge_id
        or final.fresh_commit_receipt_ref.to_dict()
        == first.fresh_commit_receipt_ref.to_dict()
    ):
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the post-compilation revalidation did not create distinct durable evidence",
        )
    _require_subject_set(subjects, final)
    current = binding.authority.open_current_snapshot().boundary
    snapshot_manifest_ref = current.manifest_ref
    if type(snapshot_manifest_ref) is not HashBoundRef:
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the committed boundary does not name a snapshot manifest",
        )
    ledger = seal_activity_ledger(activities=activities, admitted=final)
    return _PreparedReplay(
        admitted=final,
        snapshot_manifest_ref=snapshot_manifest_ref,
        bindings=compiled,
        ledger=ledger,
    )


def _evaluate_governed_activities(
    prepared: _PreparedReplay,
    *,
    binding: ProductionReplayBinding,
) -> tuple[object, ...]:
    from .activity_policy import (
        ActivityPolicyViolation,
        evaluate_activity_policy,
        require_consumable_activity_decision,
    )

    decisions: list[object] = []
    for activity in prepared.ledger.recorded():
        try:
            decision = evaluate_activity_policy(
                binding.activity_policy_evaluator,
                activity=activity,
                consumer_context_ref=prepared.admitted.consumer_context_ref,
                boundary_ref=prepared.admitted.boundary_ref,
                run_id=prepared.admitted.envelope.run_id,
                attempt_id=prepared.admitted.envelope.attempt_id,
                environment_profile_id=prepared.admitted.envelope.environment_profile_id,
                capability_profile_digest=capability_profile_digest(),
            )
            require_consumable_activity_decision(
                decision,
                evaluator=binding.activity_policy_evaluator,
                activity=activity,
                consumer_context_ref=prepared.admitted.consumer_context_ref,
                boundary_ref=prepared.admitted.boundary_ref,
                run_id=prepared.admitted.envelope.run_id,
                attempt_id=prepared.admitted.envelope.attempt_id,
                environment_profile_id=prepared.admitted.envelope.environment_profile_id,
                capability_profile_digest=capability_profile_digest(),
            )
        except ActivityPolicyViolation as exc:
            raise _fail(
                ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
                "a durable activity is not consumable under the bound policy",
            ) from exc
        decisions.append(decision)
    return tuple(decisions)


def _create_replay_request(
    *,
    prepared: _PreparedReplay,
    decision_refs: tuple[HashBoundRef, ...],
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    executor_actor: ActorIdentity,
    expected_transcript_root: str | None = None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None = None,
    resumed_from_result_ref: HashBoundRef | None = None,
) -> BehaviorReplayRequest:
    """Seal the request from final admission and already evaluated decisions."""

    admitted = prepared.admitted
    ledger = prepared.ledger
    payload = object.__new__(BehaviorReplayRequest)
    object.__setattr__(payload, "schema_version", SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1)
    object.__setattr__(payload, "knowledge_snapshot_id", prepared.snapshot_manifest_ref.ref_id)
    object.__setattr__(payload, "snapshot_manifest_ref", prepared.snapshot_manifest_ref)
    object.__setattr__(payload, "bindings", prepared.bindings)
    object.__setattr__(payload, "capability_profile", REPLAY_CAPABILITY_PROFILE_V1)
    object.__setattr__(payload, "capability_profile_digest", capability_profile_digest())
    object.__setattr__(payload, "recorded_activity_refs", ledger.activity_refs())
    object.__setattr__(payload, "activity_policy_decision_refs", decision_refs)
    object.__setattr__(payload, "activity_identities", ledger.activity_identities())
    object.__setattr__(payload, "ledger", ledger)
    object.__setattr__(payload, "knowledge_subject_refs", admitted.subject_refs)
    object.__setattr__(payload, "consumer_context_ref", admitted.consumer_context_ref)
    object.__setattr__(payload, "boundary_ref", admitted.boundary_ref)
    object.__setattr__(payload, "admitted_knowledge_id", admitted.knowledge_id)
    object.__setattr__(payload, "consumption_decision_id", admitted.consumption_decision_id)
    object.__setattr__(payload, "policy_version", admitted.policy_version)
    object.__setattr__(payload, "gas_budget", gas_budget)
    object.__setattr__(payload, "cognitive_budget", cognitive_budget)
    object.__setattr__(payload, "step_limit", step_limit)
    object.__setattr__(payload, "expected_transcript_root", expected_transcript_root)
    object.__setattr__(
        payload, "expected_terminal_snapshot_digests", expected_terminal_snapshot_digests
    )
    object.__setattr__(payload, "resumed_from_result_ref", resumed_from_result_ref)
    object.__setattr__(payload, "executor_actor", executor_actor)
    object.__setattr__(payload, "admitted", admitted)
    object.__setattr__(payload, "_trusted_seal", _REQUEST_SEAL)
    envelope, binding = _envelope_for(
        schema_version=SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1,
        identity_domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
        payload=_request_payload(payload),
        admitted=admitted,
        created_at_utc=admitted.verified_at_utc,
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", binding)
    object.__setattr__(payload, "replay_id", envelope.record_id)
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
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    observation_id: RecordId
    behavior_content_key: str
    program_hash: str
    host_abi_version: str
    transition_hash_chain: tuple[str, ...]
    consumed_activity_identities: tuple[str, ...]
    consumed_lookup_keys: tuple[str, ...]
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
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _observation_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_replay_observation(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_observation_payload(self),
        )


def _observation_payload(value: ReplayObservation) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "behavior_content_key": value.behavior_content_key,
        "program_hash": value.program_hash,
        "host_abi_version": value.host_abi_version,
        "transition_hash_chain": list(value.transition_hash_chain),
        "consumed_activity_identities": list(value.consumed_activity_identities),
        "consumed_lookup_keys": list(value.consumed_lookup_keys),
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
    for name in ("transition_hash_chain", "consumed_activity_identities", "consumed_lookup_keys"):
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
    _require_envelope_bound(
        envelope=value.envelope,
        envelope_binding_sha256=value.envelope_binding_sha256,
        payload=_observation_payload(value),
        identity_domain=IdentityDomain.REPLAY_OBSERVATION,
        field_name="replay observation",
    )
    if value.observation_id != value.envelope.record_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "observation_id is not the identity its envelope computed",
        )


def _seal_observation(*, admitted: CurrentAdmittedKnowledge, **fields: object) -> ReplayObservation:
    payload = object.__new__(ReplayObservation)
    object.__setattr__(payload, "schema_version", SchemaVersion.REPLAY_OBSERVATION_V1)
    for name, item in fields.items():
        object.__setattr__(payload, name, item)
    object.__setattr__(payload, "_trusted_seal", _OBSERVATION_SEAL)
    envelope, binding = _envelope_for(
        schema_version=SchemaVersion.REPLAY_OBSERVATION_V1,
        identity_domain=IdentityDomain.REPLAY_OBSERVATION,
        payload=_observation_payload(payload),
        admitted=admitted,
        created_at_utc=admitted.verified_at_utc,
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", binding)
    object.__setattr__(payload, "observation_id", envelope.record_id)
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
    envelope: CommonEnvelope
    envelope_binding_sha256: str
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
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _result_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_replay_result(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_result_payload(self),
        )

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
    _require_envelope_bound(
        envelope=value.envelope,
        envelope_binding_sha256=value.envelope_binding_sha256,
        payload=_result_payload(value),
        identity_domain=IdentityDomain.BEHAVIOR_REPLAY_RESULT,
        field_name="replay result",
    )
    if value.result_id != value.envelope.record_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "result_id is not the identity its envelope computed",
        )


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
    _envelope, _binding = _envelope_for(
        schema_version=SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1,
        identity_domain=IdentityDomain.BEHAVIOR_REPLAY_RESULT,
        payload=_result_payload(payload),
        admitted=request.admitted,
        created_at_utc=request.admitted.verified_at_utc,
    )
    object.__setattr__(payload, "envelope", _envelope)
    object.__setattr__(payload, "envelope_binding_sha256", _binding)
    object.__setattr__(payload, "result_id", _envelope.record_id)
    validate_replay_result(payload)
    return payload


def _envelope_from_dict(value: object, *, payload: dict[str, object], field_name: str):
    if type(value) is not dict or set(value) != {"envelope", "envelope_binding_sha256", "payload"}:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"{field_name} record has an unexpected shape")
    try:
        envelope = common_envelope_from_dict(
            value["envelope"], canonical_payload_bytes=_canonical(payload)
        )
    except ContractViolation as exc:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            f"{field_name} envelope does not bind the payload it was stored with",
        ) from exc
    return envelope


def _reason_from_value(value: object) -> ReplayFailureReason | None:
    if value is None:
        return None
    for item in ReplayFailureReason:
        if item.value == value:
            return item
    raise _fail(ReplayFailureCode.TYPE_MISMATCH, "unknown replay failure reason")


def replay_observation_from_dict(value: object) -> ReplayObservation:
    """Rebuild one observation from its stored record, recomputing its identity.

    Restoration is not deserialisation here. Every field is re-read, the payload
    is re-canonicalised, the envelope is re-derived from those exact bytes and
    the record id is recomputed — so a stored record whose bytes were edited does
    not come back as a record, it fails the checks a genuine one passes.
    """

    if type(value) is not dict or "payload" not in value:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an observation record has an unexpected shape")
    data = value["payload"]
    if type(data) is not dict:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an observation payload must be an exact dict")
    payload = object.__new__(ReplayObservation)
    object.__setattr__(payload, "schema_version", SchemaVersion.REPLAY_OBSERVATION_V1)
    for name in ("behavior_content_key", "program_hash", "host_abi_version",
                 "initial_snapshot_digest", "terminal_snapshot_digest"):
        object.__setattr__(payload, name, data[name])
    for name in ("transition_hash_chain", "consumed_activity_identities", "consumed_lookup_keys"):
        object.__setattr__(payload, name, tuple(data[name]))
    for name in ("steps_executed", "gas_consumed", "first_unexpected_index"):
        object.__setattr__(payload, name, data[name])
    object.__setattr__(payload, "transcript_matched", data["transcript_matched"])
    object.__setattr__(payload, "failure_reason", _reason_from_value(data["failure_reason"]))
    object.__setattr__(payload, "_trusted_seal", _OBSERVATION_SEAL)
    envelope = _envelope_from_dict(
        value, payload=_observation_payload(payload), field_name="replay observation"
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", value["envelope_binding_sha256"])
    object.__setattr__(payload, "observation_id", envelope.record_id)
    validate_replay_observation(payload)
    return payload


def replay_result_from_dict(value: object) -> BehaviorReplayResult:
    """Rebuild a replay result from its stored record, or refuse.

    A result carries no runtime capability — no ledger, no admission — so unlike
    a request it restores completely, and that asymmetry is the point. The record
    that says what happened is durable and readable by anyone; the object that
    says what may happen next is not restorable from bytes at all.
    """

    if type(value) is not dict or "payload" not in value:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a result record has an unexpected shape")
    data = value["payload"]
    if type(data) is not dict:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a result payload must be an exact dict")
    payload = object.__new__(BehaviorReplayResult)
    object.__setattr__(payload, "schema_version", SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1)
    object.__setattr__(payload, "request_ref", HashBoundRef.from_dict(data["request_ref"]))
    object.__setattr__(payload, "knowledge_snapshot_id", data["knowledge_snapshot_id"])
    status = next((item for item in ReplayStatus if item.value == data["status"]), None)
    if status is None:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "unknown replay status")
    object.__setattr__(payload, "status", status)
    object.__setattr__(payload, "failure_reason", _reason_from_value(data["failure_reason"]))
    object.__setattr__(
        payload,
        "observations",
        tuple(replay_observation_from_dict(item) for item in data["observations"]),
    )
    object.__setattr__(payload, "transition_hash_chain", tuple(data["transition_hash_chain"]))
    object.__setattr__(
        payload,
        "recorded_activity_refs",
        tuple(HashBoundRef.from_dict(item) for item in data["recorded_activity_refs"]),
    )
    object.__setattr__(
        payload, "consumed_activity_identities", tuple(data["consumed_activity_identities"])
    )
    object.__setattr__(payload, "observed_transcript_root", data["observed_transcript_root"])
    object.__setattr__(payload, "expected_transcript_root", data["expected_transcript_root"])
    object.__setattr__(
        payload, "terminal_snapshot_digests", tuple(data["terminal_snapshot_digests"])
    )
    object.__setattr__(payload, "steps_executed", data["steps_executed"])
    object.__setattr__(payload, "gas_consumed", data["gas_consumed"])
    object.__setattr__(payload, "executor_actor", ActorIdentity(data["executor_actor"]))
    object.__setattr__(payload, "_trusted_seal", _RESULT_SEAL)
    envelope = _envelope_from_dict(
        value, payload=_result_payload(payload), field_name="replay result"
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", value["envelope_binding_sha256"])
    object.__setattr__(payload, "result_id", envelope.record_id)
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

    The machine's gas pool is deliberately *not* checked here. An earlier attempt
    at the audit's "machine gas is unrelated to request gas" finding refused a
    machine whose pool was below the admitted budget, which is right for a fresh
    run and wrong for a continuation: a resumed machine carries whatever its
    predecessor left, and a fresh budget larger than that remainder is the normal
    case rather than an incompatibility. The finding is answered in the per-step
    preflight instead, where both limits apply to the same question — can this
    replay afford the next transition — and both answer ``GAS_EXHAUSTED``.
    """

    loaded = machine.program_hash()
    if type(loaded) is not str or loaded != binding.program_hash:
        return ReplayFailureReason.PROGRAM_HASH_MISMATCH
    abi = machine.host_abi_version()
    if type(abi) is not str or abi != binding.host_abi_version:
        return ReplayFailureReason.HOST_ABI_MISMATCH
    return None


def _require_current_admission(
    request: BehaviorReplayRequest,
    *,
    authority: ProductionAuthorityBinding,
) -> None:
    """Re-check, here and now, that this replay is still admitted to run.

    §22 places the consumption gate *immediately before* replay and requires a
    fresh effective check even when a previous decision is still structurally
    valid; its fail-closed list names "stale decision reused against new state"
    outright. A request satisfies neither requirement on its own. It is an
    immutable record of an admission that held when it was built, and between
    that moment and this one the behavior can be revoked, its taint escalated,
    the boundary replaced or the admission superseded — none of which changes a
    single byte of the request.

    So the request is not the authority to replay, and this is the call that
    makes that structural rather than stated. It needs the production binding,
    which is the only object that can read the live authority state, and it
    fails closed on three questions:

    * does the admission still describe the current world — same coordinator
      epoch, same authority heads, same committed boundary as the head;
    * is the boundary the replay was admitted against still the current one;
    * does that boundary still publish the exact snapshot manifest the request
      names, so the knowledge being read has not been re-published underneath it.

    A stale admission raises rather than returning a result. Every *execution*
    outcome is preserved as evidence under NR-13, but this run never began: the
    machines have not been touched, no transition has been taken, and recording
    a refusal as a replay attempt would put an authority failure into the
    vocabulary §23 reserves for execution.
    """

    validate_replay_request(request)
    # The binding is checked for what it is before it is asked anything. NR-10
    # keeps failure kinds apart, and a forged or malformed binding is a
    # different fact about the world from an admission that has gone stale —
    # reporting the first as the second is the status relabelling §2 forbids.
    validate_production_authority_binding(authority)
    try:
        require_current_point_of_use_evidence(request.admitted, binding=authority)
    except AdmissionViolation as exc:
        # Only the head observation going stale is an admission that no longer
        # holds. Every other refusal keeps its own code: an unavailable store, a
        # forged object and a rolled-back journal are distinct, and flattening
        # them into one reason would make the strictest reading of a refusal
        # indistinguishable from the mildest.
        if exc.failure_code is not AdmissionFailureCode.HEAD_OBSERVATION_STALE:
            raise
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the admission this replay rests on no longer holds at the point of use",
        ) from exc
    current = authority.open_current_snapshot().boundary
    if _ref_key(current.manifest_ref) != _ref_key(request.snapshot_manifest_ref):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the current boundary no longer publishes the snapshot this replay names",
        )
    return None


def _execute_replay_body(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    activity_store: object,
) -> BehaviorReplayResult:
    """Run one governed replay attempt over the ordered admitted behavior set.

    One machine per behavior, in the request's order. Every stopping condition
    produces a result rather than an exception: a replay that raised on
    divergence would lose the evidence of what it saw, and NR-13 requires all
    attempts to be preserved, not only the successful ones. Only a request that
    cannot be executed at all — a malformed one, the wrong number of machines,
    or one whose admission no longer holds — raises.

    ``authority`` is the production binding the request was admitted through. It
    is required, and it is required *here* rather than trusted from creation
    time, because §22 asks whether this knowledge may be consumed **now**. See
    ``_require_current_admission``.
    """

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
        request.ledger, request.cognitive_budget, activity_store, _seal=_CHANNEL_SEAL
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
        # Preflight, not post-mortem. The earlier form compared gas *already*
        # spent against the budget, so a single expensive opcode could execute
        # past the budget and be noticed only on the next iteration — and if it
        # was the last instruction, never noticed at all. The budget is a
        # promise about what this replay may consume, and a promise checked
        # after the fact is a description.
        #
        # The cost is read from the machine's own table so the two cannot
        # disagree, and a jump is charged the back-edge as well: whether the
        # jump goes backwards depends on its target, which the port does not
        # expose, so the possibility is charged. Over-charging is fail-closed
        # here — it can only stop a run early, never let one run long.
        remaining_pool = machine.gas_remaining()
        spent = gas_start - remaining_pool
        cost = GAS_COSTS.get(opcode, 1)
        if opcode in _BACK_EDGE_OPCODES:
            cost += GAS_BACK_EDGE
        # Two limits, one question: can this replay afford the next transition.
        # The budget is what the request was admitted with; the pool is what the
        # machine actually holds, which for a continuation is whatever its
        # predecessor left. Either can bind first, and neither is a machine
        # defect — a machine allowed to run dry would raise ``OutOfEnergy`` and
        # be recorded as ``MACHINE_FAULT``, reporting an exhausted budget as
        # broken infrastructure. That was the audit's "machine gas is unrelated
        # to request gas": not that the two should be equal, but that whichever
        # runs out should be named for what it is.
        if spent + cost > request.gas_budget or (
            type(remaining_pool) is int and remaining_pool < cost
        ):
            reason = ReplayFailureReason.GAS_EXHAUSTED
            break

        try:
            machine.step()
        except ActivityViolation as exc:
            reason = reason_for_activity_failure(exc)
            break
        except ReplayViolation as exc:
            # Three different refusals from the adapter, and they are not the
            # same fact: an effect with no channel or a closed one is a side
            # effect outside the plan, a dispatch that would reach arbitrary
            # Python is a forbidden host call refused before it happened, and
            # anything else is the machine itself misbehaving.
            if exc.failure_code is ReplayFailureCode.CHANNEL_CLOSED:
                reason = ReplayFailureReason.SIDE_EFFECT_OUTSIDE_PLAN
            elif exc.failure_code is ReplayFailureCode.UNGOVERNED_DISPATCH:
                reason = ReplayFailureReason.FORBIDDEN_HOST_CALL
            else:
                reason = ReplayFailureReason.MACHINE_FAULT
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
    keys = channel.consumed_lookup_keys()[consumed_before:]
    matched = reason is None and _transcript_matches(
        binding.replay_contract, transitions=chain, activities=consumed
    )
    if reason is None and not matched:
        reason = ReplayFailureReason.TRANSITION_MISMATCH

    observation = _seal_observation(
        admitted=request.admitted,
        behavior_content_key=binding.behavior_content_key,
        program_hash=binding.program_hash,
        host_abi_version=binding.host_abi_version,
        transition_hash_chain=chain,
        consumed_activity_identities=consumed,
        consumed_lookup_keys=keys,
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


def _resume_replay_body(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    resumed_from: BehaviorReplayResult,
    activity_store: object,
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

    Before any of that, the *lineage* is checked, and it is checked against the
    request rather than against the call. A resuming request declares the exact
    result it continues; a caller that pairs a request with some other result is
    refused outright, because that pairing is not an execution outcome to be
    recorded — it is a request that was never for this continuation. The earlier
    revision took the predecessor only as an argument, so the same request could
    be resumed from any result whose program hashes happened to match, across
    knowledge snapshots and boundaries alike.
    """

    validate_replay_result(resumed_from)
    if request.resumed_from_result_ref is None:
        raise _fail(
            ReplayFailureCode.RESUME_LINEAGE_MISMATCH,
            "a resuming request must name the result it continues",
        )
    if request.resumed_from_result_ref.to_dict() != replay_result_ref(resumed_from).to_dict():
        raise _fail(
            ReplayFailureCode.RESUME_LINEAGE_MISMATCH,
            "the request continues another replay result",
        )
    if request.knowledge_snapshot_id != resumed_from.knowledge_snapshot_id:
        raise _fail(
            ReplayFailureCode.RESUME_LINEAGE_MISMATCH,
            "a continuation cannot cross a knowledge snapshot boundary",
        )
    if type(machines) is not tuple or len(machines) != len(request.bindings):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "one machine is required for each admitted behavior",
        )
    for machine in machines:
        require_machine_port(machine)

    def _refused(status: ReplayStatus, reason: ReplayFailureReason) -> BehaviorReplayResult:
        """A continuation that got this far is an attempt, so it is recorded.

        The request goes down before the refusal is sealed, in the same order the
        ordinary path uses: authority, then the record, then whatever the run
        turns out to be. NR-13 keeps every attempt, and a continuation refused
        for resuming the wrong state is one.
        """

        return _seal_result(
            request=request, status=status, failure_reason=reason, observations=()
        )

    if request.program_hashes != tuple(item.program_hash for item in resumed_from.observations):
        return _refused(
            ReplayStatus.REPLAY_INCOMPATIBLE, ReplayFailureReason.PROGRAM_HASH_MISMATCH
        )
    if request.recorded_activity_refs != resumed_from.recorded_activity_refs:
        return _refused(
            ReplayStatus.REPLAY_FAILED, ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH
        )
    observed = tuple(machine.snapshot_digest() for machine in machines)
    if observed != resumed_from.terminal_snapshot_digests:
        return _refused(
            ReplayStatus.REPLAY_INCOMPATIBLE, ReplayFailureReason.SNAPSHOT_INCOMPATIBLE
        )
    return _execute_replay_body(
        request,
        machines=machines,
        activity_store=activity_store,
    )


# ---------------------------------------------------------------------------
# The production lifecycle path — the only way a governed replay runs
# ---------------------------------------------------------------------------


def _require_durable_policy_decisions(
    request: BehaviorReplayRequest,
    *,
    binding: ProductionReplayBinding,
) -> None:
    from .activity_policy import require_consumable_activity_decision

    activities = request.ledger.recorded()
    if len(activities) != len(request.activity_policy_decision_refs):
        raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "policy decision set is incomplete")
    for activity, reference in zip(activities, request.activity_policy_decision_refs):
        decision = binding.activity_policy_store.require_decision(
            reference,
            evaluator=binding.activity_policy_evaluator,
        )
        require_consumable_activity_decision(
            decision,
            evaluator=binding.activity_policy_evaluator,
            activity=activity,
            consumer_context_ref=request.consumer_context_ref,
            boundary_ref=request.boundary_ref,
            run_id=request.envelope.run_id,
            attempt_id=request.envelope.attempt_id,
            environment_profile_id=request.envelope.environment_profile_id,
            capability_profile_digest=request.capability_profile_digest,
        )


def _persist_authority_and_request(
    request: BehaviorReplayRequest,
    *,
    decisions: tuple[object, ...],
    binding: ProductionReplayBinding,
    ticket: object,
) -> None:
    if len(decisions) != len(request.activity_policy_decision_refs):
        raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "policy decision set is incomplete")
    for decision, expected in zip(decisions, request.activity_policy_decision_refs):
        actual = binding.activity_policy_store.append_decision(
            decision,
            evaluator=binding.activity_policy_evaluator,
            ticket=ticket,
        )
        if actual.to_dict() != expected.to_dict():
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "durable policy decision changed identity")
    actual_request = binding.replay_store.append_request(request, ticket=ticket)
    if actual_request.to_dict() != replay_request_ref(request).to_dict():
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "durable replay request changed identity")


def _execute_prepared(
    prepared: _PreparedReplay,
    *,
    binding: ProductionReplayBinding,
    machines: tuple[ReplayMachinePort, ...],
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    expected_transcript_root: str | None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None,
    resumed_from: BehaviorReplayResult | None = None,
) -> BehaviorReplayResult:
    """Commit policy and request, transition once, then commit the result."""

    from .activity_policy import activity_policy_decision_ref
    from .coordination import settle_exclusive_mutation
    from .persistence import store_transaction

    binding = validate_production_replay_binding(binding)
    fence = binding.fence
    with fence.exclusive() as coordinator_guard:
        entry_epoch = fence.current_epoch()
        if type(entry_epoch) is not int or entry_epoch < 0 or entry_epoch % 2:
            raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "coordinator is not settled")

        # Resolve again inside the execution guard. The first resolution happened
        # before compilation; this one prevents a removed record or blob from
        # surviving the interval between compilation and the first transition.
        resolved = _resolve_durable_activities(binding, prepared.ledger.activity_refs())
        if tuple(activity_ref(item).to_dict() for item in resolved) != tuple(
            item.to_dict() for item in prepared.ledger.activity_refs()
        ):
            raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "durable activity set changed")

        decisions = _evaluate_governed_activities(prepared, binding=binding)
        decision_refs = tuple(activity_policy_decision_ref(item) for item in decisions)
        request = _create_replay_request(
            prepared=prepared,
            decision_refs=decision_refs,
            gas_budget=gas_budget,
            cognitive_budget=cognitive_budget,
            step_limit=step_limit,
            executor_actor=binding.executor_actor,
            expected_transcript_root=expected_transcript_root,
            expected_terminal_snapshot_digests=expected_terminal_snapshot_digests,
            resumed_from_result_ref=(
                None if resumed_from is None else replay_result_ref(resumed_from)
            ),
        )

        # This check is after every authority callback and immediately before the
        # single coordinator transaction that makes the decisions and request
        # durable. No caller-controlled code runs between it and the first VM
        # transition.
        _require_current_admission(request, authority=binding.authority)
        with store_transaction(fence, guard=coordinator_guard) as ticket:
            _persist_authority_and_request(
                request,
                decisions=decisions,
                binding=binding,
                ticket=ticket,
            )
        settle_exclusive_mutation(
            fence=fence,
            coordinator_id=fence.coordinator_id(),
            entry_epoch=entry_epoch,
            own_intervals=1,
        )
        _require_durable_policy_decisions(request, binding=binding)

        # From here the request is durable, so this attempt happened and NR-13
        # requires it to be findable with an outcome attached. The store says the
        # same thing from the other side: it holds every attempt, not every
        # success. Both are broken by a raise between the two appends — the
        # history then shows a run that started and, as far as any later reader
        # can tell, is still running.
        #
        # So execution is finalised rather than allowed to escape. What is caught
        # is execution: a machine that misbehaved, a request the executor could
        # not carry out, anything the body did not already turn into a typed
        # result. What is *not* caught is persistence — the append below, and the
        # coordinator around it. A store that cannot record the outcome cannot
        # record an INFRA_ERROR about being unable to record the outcome either,
        # and reporting a persistence failure as an execution one would be the
        # NR-10 reclassification in its purest form.
        try:
            if resumed_from is None:
                result = _execute_replay_body(
                    request,
                    machines=machines,
                    activity_store=binding.activity_store,
                )
            else:
                result = _resume_replay_body(
                    request,
                    machines=machines,
                    resumed_from=resumed_from,
                    activity_store=binding.activity_store,
                )
        except (KeyboardInterrupt, SystemExit):
            # A process being terminated is not an execution outcome, and the
            # store is not the place to claim it was one.
            raise
        except BaseException as exc:  # noqa: BLE001 - the attempt is recorded, then re-raised
            failed = _seal_result(
                request=request,
                status=ReplayStatus.INFRA_ERROR,
                failure_reason=ReplayFailureReason.MACHINE_FAULT,
                observations=(),
            )
            with store_transaction(fence, guard=coordinator_guard) as ticket:
                binding.replay_store.append_result(failed, ticket=ticket)
            settle_exclusive_mutation(
                fence=fence,
                coordinator_id=fence.coordinator_id(),
                entry_epoch=entry_epoch,
                own_intervals=2,
            )
            # Re-raised, not returned. The caller asked for a run and did not get
            # one; handing back an INFRA_ERROR result would make an executor
            # defect indistinguishable from a machine that faulted mid-transcript
            # and was recorded normally. The record exists either way.
            raise exc

        with store_transaction(fence, guard=coordinator_guard) as ticket:
            binding.replay_store.append_result(result, ticket=ticket)
        settle_exclusive_mutation(
            fence=fence,
            coordinator_id=fence.coordinator_id(),
            entry_epoch=entry_epoch,
            own_intervals=2,
        )
        return result


def _resume_history(
    binding: ProductionReplayBinding,
    reference: HashBoundRef,
) -> tuple[BehaviorReplayResult, tuple[HashBoundRef, ...]]:
    """Resolve predecessor result, request, activities and policy after restart."""

    resumed = binding.replay_store.require_result(
        _ref(reference, "resumed_from_result_ref")
    )
    record = binding.replay_store.request_record(resumed.request_ref)
    if type(record) is not dict or set(record) != {"envelope", "envelope_binding_sha256", "payload"}:
        raise _fail(ReplayFailureCode.RESUME_LINEAGE_MISMATCH, "predecessor request record is malformed")
    payload = record["payload"]
    if type(payload) is not dict:
        raise _fail(ReplayFailureCode.RESUME_LINEAGE_MISMATCH, "predecessor request payload is malformed")
    try:
        activity_refs = tuple(HashBoundRef.from_dict(item) for item in payload["recorded_activity_refs"])
        policy_refs = tuple(
            HashBoundRef.from_dict(item) for item in payload["activity_policy_decision_refs"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _fail(ReplayFailureCode.RESUME_LINEAGE_MISMATCH, "predecessor references are malformed") from exc
    if activity_refs != resumed.recorded_activity_refs or len(activity_refs) != len(policy_refs):
        raise _fail(ReplayFailureCode.RESUME_LINEAGE_MISMATCH, "predecessor histories disagree")
    activities = _resolve_durable_activities(binding, activity_refs)
    for activity, policy_ref in zip(activities, policy_refs):
        decision = binding.activity_policy_store.require_decision(
            policy_ref,
            evaluator=binding.activity_policy_evaluator,
        )
        if decision.activity_identity != activity.activity_identity:
            raise _fail(ReplayFailureCode.ACTIVITY_NOT_GOVERNED, "predecessor policy names another activity")
    return resumed, activity_refs


def run_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    activity_refs: tuple[HashBoundRef, ...],
    machines: tuple[ReplayMachinePort, ...],
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    expected_transcript_root: str | None = None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None = None,
) -> BehaviorReplayResult:
    """Resolve, admit, compile, re-admit, durably govern and execute."""

    prepared = _prepare_replay(
        admission=admission,
        binding=binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
    )
    return _execute_prepared(
        prepared,
        binding=binding,
        machines=machines,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
        expected_transcript_root=expected_transcript_root,
        expected_terminal_snapshot_digests=expected_terminal_snapshot_digests,
    )


def resume_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    machines: tuple[ReplayMachinePort, ...],
    resumed_from_result_ref: HashBoundRef,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    expected_transcript_root: str | None = None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None = None,
) -> BehaviorReplayResult:
    """Continue only from exact predecessor histories resolved after restart."""

    binding = validate_production_replay_binding(binding)
    resumed, activity_refs = _resume_history(binding, resumed_from_result_ref)
    prepared = _prepare_replay(
        admission=admission,
        binding=binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
    )
    return _execute_prepared(
        prepared,
        binding=binding,
        machines=machines,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
        expected_transcript_root=expected_transcript_root,
        expected_terminal_snapshot_digests=expected_terminal_snapshot_digests,
        resumed_from=resumed,
    )


__all__ = [
    "ACTIVITY_KIND_BY_OPCODE",
    "DISPATCH_GUARDED_OPCODES",
    "RECORDED_ONLY_OPCODES",
    "REPLAY_ADMISSIBLE_OPCODES",
    "REPLAY_CAPABILITY_PROFILE_V1",
    "BehaviorReplayRequest",
    "BehaviorReplayResult",
    "CognitiveVMReplayAdapter",
    "ProductionReplayBinding",
    "RecordedActivityChannel",
    "ReplayFailureCode",
    "ReplayFailureReason",
    "ReplayMachinePort",
    "ReplayObservation",
    "ReplayProgramBinding",
    "ReplayStatus",
    "ReplaySubject",
    "ReplayViolation",
    "activity_kind_for_opcode",
    "capability_profile_digest",
    "classify_replay_opcode",
    "create_production_replay_binding",
    "reason_for_activity_failure",
    "replay_program_binding",
    "replay_request_ref",
    "replay_observation_from_dict",
    "replay_result_from_dict",
    "replay_result_ref",
    "replay_subject",
    "resume_governed_replay",
    "run_governed_replay",
    "require_machine_port",
    "status_for_reason",
    "transcript_root",
    "validate_replay_observation",
    "validate_production_replay_binding",
    "validate_replay_request",
    "validate_replay_result",
]
