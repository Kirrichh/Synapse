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
names the operations a replay needs and the protected-core adapter is the one
implementation over the real machine. No Stage 4 policy or storage enters ``cvm.py``.

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
from datetime import datetime
import hashlib
import json
import unicodedata
from typing import Protocol, runtime_checkable

from synapse.bytecode import BytecodeProgram

from .activities import (
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
    CanonicalizationViolation,
    content_key_digest,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    CompilerBinding,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .replay_structural_history import (
    REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1,
)
from .replay_machine_binding import (
    ProductionReplayMachineFactory,
    ReplayMachineBindingViolation,
    require_production_replay_machine_factory,
)
from .replay_attempt_boundary import DurableReplayAttemptBoundary
from .replay_attempt_lifecycle import (
    ReplayAttemptFailureDomain,
    ReplayAttemptPhase,
)
from .contracts import (
    ActorIdentity,
    AttemptId,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    common_envelope_from_dict,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    record_id_reference_from_dict,
    validate_envelope_bound_record,
)

REPLAY_CAPABILITY_PROFILE_V1_E1 = "synapse.stage4.gold.replay-capability-profile-e1/v1"
_PROFILE_PREFIX = REPLAY_CAPABILITY_PROFILE_V1_E1.encode("utf-8") + b"\x00"

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

#: The profile an attempt's execution-spend identity is computed under. Named so
#: two builds cannot disagree about what "the same attempt" means while agreeing
#: on every field that goes into it.
REPLAY_EXECUTION_SPEND_PROFILE_V1 = "synapse.stage4.gold.replay-execution-spend/v1"

#: The one machine adapter a governed replay executes on, named as a value so the
#: execution identity binds it. The exact type is checked where it is defined,
#: when the production binding is assembled; this is how that choice reaches the
#: digest without the owner importing the adapter that implements it.
REPLAY_MACHINE_ADAPTER_ID_V1_E1 = "synapse.stage4.gold.cognitive-vm-replay-adapter-e1/v1"


class ProductionReplayBinding:
    """One sealed authority, policy entitlement and Stage 9 durability domain."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _PRODUCTION_REPLAY_BINDING_SEAL or kwargs or len(args) != 12:
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
            # The party this attempt is run *for*, as opposed to the party that
            # runs it. Both are actual identities and both go into the §9.4
            # consumption provenance below.
            self.consumer_actor,
            # The consumption phase of §9.4 provenance for this binding: who is
            # actually executing, which exact machine adapter they are executing
            # with, and who is consuming. Derived by the factory from the two
            # identities above rather than accepted, and snapshotted with the
            # rest — a binding whose provenance was swapped is not the binding
            # that was sealed.
            self.consumption_provenance,
            # Where an admitted behaviour's program bytes come from. Part of the
            # binding rather than an argument to whoever resolves, so a run
            # cannot be pointed at another store for its code than for its
            # records — and part of the configuration snapshot below, so a
            # binding that swapped it is no longer the binding that was sealed.
            self.artifact_resolver,
            # The sole protected-core construction path, supplied once by the
            # composition root and sealed with the production configuration.
            # A run never accepts a caller-provided machine or factory.
            self.machine_factory,
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


def _create_production_replay_binding(
    *,
    authority: ProductionAuthorityBinding,
    initial_admission: object,
    final_admission: object,
    activity_policy_evaluator: object,
    activity_store: object,
    activity_policy_store: object,
    replay_store: object,
    artifact_resolver: ArtifactProgramResolverPort,
    machine_factory: ProductionReplayMachineFactory,
) -> ProductionReplayBinding:
    """Bind exact production types to the authority's exact coordinator."""

    from .activity_policy import (
        ConfiguredActivityPolicyEvaluator,
        require_activity_policy_evaluator,
        require_activity_policy_execution_entitlement,
    )
    from .activity_provenance import record_activity_consumption_provenance
    from .activity_policy_store import FileActivityPolicyStore
    from .activity_store import FileActivityStore

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
    )
    for value, expected, name in exact:
        if type(value) is not expected:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"production replay requires an exact {name}")
    evaluator = require_activity_policy_evaluator(activity_policy_evaluator)
    executor_actor = evaluator.actor_set.replay_executor_actor
    consumer_actor = evaluator.actor_set.consumer_actor
    # Asked without an import, because this module's own adapter may not be
    # imported back into it. Exactness for this one is the composition root's to
    # assert — ``replay_store.require_production_replay_store`` — and what is
    # checked here is that the store holds this authority's exact coordinator.
    require_replay_history(replay_store, fence=authority.fence)
    # The consumption phase of §9.4 provenance, taken here because here is where
    # the parties that will consume actually exist: the executor that will run,
    # the exact adapter it will run with, and the consumer it runs for. The
    # entitlement check below is then made against actual identities rather than
    # against a set comparing itself.
    consumption = record_activity_consumption_provenance(
        evaluator.provenance_authority,
        machine_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    )
    require_activity_policy_execution_entitlement(
        activity_policy_evaluator,
        executor_actor=executor_actor,
        consumption=consumption,
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
    if not isinstance(artifact_resolver, ArtifactProgramResolverPort):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "production replay requires a resolver for admitted program artifacts",
        )
    try:
        machine_factory = require_production_replay_machine_factory(
            machine_factory, expected_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1
        )
    except ReplayMachineBindingViolation as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "production replay requires the composition-sealed machine factory",
        ) from exc
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
            consumer_actor,
            consumption,
            artifact_resolver,
            machine_factory,
            _seal=_PRODUCTION_REPLAY_BINDING_SEAL,
        )
    )


#: The operations a governed replay needs from its durable history.
#:
#: OD-10/V1 makes ``replay_store.py`` an adapter of this module, so this module
#: may not import it — an adapter its owner imports back is not an adapter. The
#: first attempt at that inversion was a registration slot the adapter filled on
#: import, and it was a first-writer hole: anything that registered a class
#: before ``replay_store`` was imported became the production store for the
#: process, and the real one was then refused as a forgery.
#:
#: So the exactness lives where the type does. ``replay_store`` exports
#: ``require_production_replay_store``, and the composition root that assembles a
#: binding calls it — that party imports both modules legitimately, which neither
#: of these two may do about the other.
#:
#: What this module checks is what it can check without knowing the type, and it
#: is not duck typing alone. The operations must be present *and* the store must
#: hold the authority's exact coordinator object. A double can be shaped like a
#: store; it cannot be handed the real coordinator, and without one it cannot
#: write anything a receipt will later find. That is the limit of what an owner
#: can verify about its own adapter from the inside, and it is stated here rather
#: than papered over.
_REPLAY_HISTORY_OPERATIONS = (
    "append_request", "append_result", "append_manifest", "require_manifest",
    "require_result", "request_record", "recorded_request_refs", "recorded_result_refs",
    "put_snapshot", "open_snapshot", "mutation_fence",
    "put_structural_history", "open_structural_history",
    "append_capture", "require_capture", "spend_execution", "spent_execution_identities",
    "append_incomplete_attempt", "recoverable_attempts", "unresolved_request_refs",
    "unresolved_execution_claims", "result_ref_for_request", "recorded_execution_claims",
)


def require_replay_history(value: object, *, fence: object) -> object:
    """Refuse a durable history this replay cannot have written through."""

    missing = [
        name for name in _REPLAY_HISTORY_OPERATIONS if not hasattr(value, name)
    ]
    if missing:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            f"the replay history is missing {', '.join(missing[:4])}",
        )
    if getattr(value, "mutation_fence", None) is not fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the replay history is not on this authority's coordinator",
        )
    return value


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
        value.consumer_actor,
        value.consumption_provenance,
        value.artifact_resolver,
        value.machine_factory,
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
        consumption=value.consumption_provenance,
    )
    for store in (value.activity_store, value.activity_policy_store, value.replay_store):
        if store.mutation_fence is not value.fence:
            raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "a Stage 9 store changed coordinator")
        if store.mutation_fence.coordinator_id() != value.fence.coordinator_id():
            raise _fail(ReplayFailureCode.ADMISSION_NOT_CURRENT, "a Stage 9 coordinator identity differs")
    try:
        require_production_replay_machine_factory(
            value.machine_factory, expected_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1
        )
    except ReplayMachineBindingViolation as exc:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "production replay machine factory binding changed",
        ) from exc
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
        "JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE",
        "MAKE_FUNCTION", "HALT",
        "ADD", "SUB", "MUL", "DIV", "MOD",
        "EQ", "NEQ", "LT", "GT", "LTE", "GTE",
        "AND", "OR", "NOT", "UNARY_NEG",
        "BUILD_LIST", "BUILD_DICT", "INDEX", "MEMBER",
        "PROMPT_BUILD",
        "GUARD_ENTER", "GUARD_EXIT", "GUARD_CHECK_RESULT", "GUARD_VIOLATION_ACK",
        "RECEIVE_ENTER", "RECEIVE_EXIT",
    }
)

#: OD-10/V1-E1. Structural commands are neither activities nor pure
#: instructions. Capture records their canonical history and replay exact-
#: matches it before the CVM transition. RETURN may resolve an atomic unwind
#: batch, or no command when no scope crosses the frame boundary.
REPLAY_RECORDED_STRUCTURAL_EFFECT_OPCODES = frozenset(
    {
        "CONTEXT_ENTER", "CONTEXT_EXIT",
        "ACTOR_ENTER", "ACTOR_EXIT",
        "POLICY_ENTER", "POLICY_EXIT",
        "POLICY_RULE_ENTER", "POLICY_RULE_EXIT",
        "RETURN",
    }
)

#: Per-occurrence preflight selects internal, governed host, or typed refusal.
DISPATCH_GUARDED_OPCODES = frozenset({"CALL", "CALL_METHOD"})

#: Opcodes whose successor state depends on something outside the machine. Each
#: occurrence must resolve to a recorded activity or the replay fails.
RECORDED_ONLY_OPCODES = frozenset(
    {
        "LLM_EVAL", "LLM_REQUEST", "LLM_RESUME",
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


#: The exact authority identifier required by each activity kind reachable from
#: an artifact program.  This belongs to the frozen replay profile: changing an
#: identifier changes what admission authorizes, even when the opcode and
#: activity-kind partitions themselves stay unchanged.
_CAPABILITY_BY_ACTIVITY_KIND = {
    ActivityKind.LLM_CALL: "capability.llm",
    ActivityKind.MEMORY_READ: "capability.memory.read",
    ActivityKind.MEMORY_WRITE: "capability.memory.write",
    ActivityKind.AFFECT_EVENT: "capability.affect",
    ActivityKind.AFFECT_READ: "capability.affect",
    ActivityKind.METRICS_EMIT: "capability.metrics",
    ActivityKind.HOST_DISPATCH: "capability.host",
    ActivityKind.SELF_MODIFICATION: "capability.self.modify",
    ActivityKind.HABIT_SUGGESTION: "capability.habit.suggest",
    ActivityKind.THRESHOLD_EVALUATION: "capability.affect.threshold.evaluate",
    ActivityKind.MESSAGE_SEND: "capability.message.send",
    ActivityKind.MESSAGE_RECEIVE: "capability.message.receive",
}


def _capability_for_activity_kind(kind: ActivityKind) -> str:
    capability = _CAPABILITY_BY_ACTIVITY_KIND.get(kind)
    if capability is None:
        raise _fail(
            ReplayFailureCode.CAPABILITY_NOT_CLASSIFIED,
            "an activity kind has no capability in the replay capability profile",
        )
    return _identifier(capability, "activity capability")


def capability_profile_digest() -> str:
    """A hash over the profile a request is executed under.

    The profile is a frozen decision, so a request records which one it ran
    against. Widening the admissible set later changes this digest, and a
    request pinned to the old one no longer matches — which is the point: a
    replay validated under one host-call profile is not evidence about another.
    """

    payload = _canonical(
        {
            "profile_id": REPLAY_CAPABILITY_PROFILE_V1_E1,
            "admissible": sorted(REPLAY_ADMISSIBLE_OPCODES),
            "recorded_only": sorted(RECORDED_ONLY_OPCODES),
            "dispatch_guarded": sorted(DISPATCH_GUARDED_OPCODES),
            "recorded_structural_effect": sorted(
                REPLAY_RECORDED_STRUCTURAL_EFFECT_OPCODES
            ),
            "activity_kinds": {
                opcode: ACTIVITY_KIND_BY_OPCODE[opcode].value
                for opcode in sorted(ACTIVITY_KIND_BY_OPCODE)
            },
            "activity_capabilities": {
                kind.value: _capability_for_activity_kind(kind)
                for kind in sorted(
                    _CAPABILITY_BY_ACTIVITY_KIND, key=lambda item: item.value
                )
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
    CAPABILITY_NOT_CLASSIFIED = "CAPABILITY_NOT_CLASSIFIED"
    ACTIVITY_CARDINALITY_MISMATCH = "ACTIVITY_CARDINALITY_MISMATCH"
    INJECTION_PRIMITIVE_MISSING = "INJECTION_PRIMITIVE_MISSING"
    STRUCTURAL_HISTORY_MISMATCH = "STRUCTURAL_HISTORY_MISMATCH"
    BEHAVIOR_SET_EMPTY = "BEHAVIOR_SET_EMPTY"
    DUPLICATE_BEHAVIOR = "DUPLICATE_BEHAVIOR"
    GAS_NOT_MONOTONE = "GAS_NOT_MONOTONE"
    STATUS_REASON_INCONSISTENT = "STATUS_REASON_INCONSISTENT"
    RESUME_LINEAGE_MISMATCH = "RESUME_LINEAGE_MISMATCH"
    #: A reference capture recorded an execution that departed from the
    #: behaviour's replay contract. The capture itself is kept — the run
    #: happened and the record is true — but it cannot become the manifest a
    #: later replay is measured against.
    CAPTURE_NOT_CONFORMANT = "CAPTURE_NOT_CONFORMANT"
    ADMISSION_NOT_CURRENT = "ADMISSION_NOT_CURRENT"
    SUBJECT_NOT_ADMITTED = "SUBJECT_NOT_ADMITTED"
    SNAPSHOT_BINDING_MISMATCH = "SNAPSHOT_BINDING_MISMATCH"
    UNGOVERNED_DISPATCH = "UNGOVERNED_DISPATCH"
    RESULT_NOT_DECODABLE = "RESULT_NOT_DECODABLE"
    ACTIVITY_NOT_GOVERNED = "ACTIVITY_NOT_GOVERNED"
    NON_CANONICAL_VM_VALUE = "NON_CANONICAL_VM_VALUE"
    MACHINE_EXECUTION_FAULT = "MACHINE_EXECUTION_FAULT"


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
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value) or unicodedata.normalize("NFC", value) != value:
        raise _fail(ReplayFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is not canonical Unicode")
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
    """Return one of the four total V1-E1 determinism classes, or refuse."""

    _identifier(opcode, "opcode")
    if opcode in REPLAY_ADMISSIBLE_OPCODES:
        return "admissible"
    if opcode in RECORDED_ONLY_OPCODES:
        return "recorded_only"
    if opcode in DISPATCH_GUARDED_OPCODES:
        return "dispatch_guarded"
    if opcode in REPLAY_RECORDED_STRUCTURAL_EFFECT_OPCODES:
        return "recorded_structural_effect"
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
# The artifact program endpoint — a behaviour whose code is a durable artifact
# ---------------------------------------------------------------------------

@runtime_checkable
class ArtifactProgramResolverPort(Protocol):
    """The durable store an admitted program artifact is read out of.

    One operation, and it takes a hash-bound reference. There is deliberately no
    way to ask for "the program of this behaviour" by name: a resolver that could
    be asked by name would decide which bytes a behaviour has, and that decision
    belongs to the admitted reference the behaviour itself carries.
    """

    def open_artifact(self, reference: HashBoundRef) -> bytes: ...


def capabilities_required_by(program: BytecodeProgram) -> tuple[str, ...]:
    """What this program's own instructions require permission to do.

    Read off the opcodes, in one place, so a behaviour cannot declare a narrower
    set than it uses. The classification is the replay capability profile's: an
    opcode outside the admissible vocabulary is refused before any capability is
    derived from it, because a program this replay cannot classify is not one it
    can state requirements for either.
    """

    required: set[str] = set()
    for instruction in program.instructions:
        opcode = instruction.op
        if type(opcode) is not str:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                "an instruction opcode must be an exact string",
            )
        determinism_class = classify_replay_opcode(opcode)
        if determinism_class == "dispatch_guarded":
            # Both guarded dispatch opcodes have a governed HOST_EVAL fallback.
            # Admission must authorize the maximum route the instruction can
            # take, rather than only the pure occurrence observed in one run.
            required.add(_capability_for_activity_kind(ActivityKind.HOST_DISPATCH))
        kind = ACTIVITY_KIND_BY_OPCODE.get(opcode)
        if kind is not None:
            required.add(_capability_for_activity_kind(kind))
    return tuple(sorted(required))


def resolve_artifact_program(
    unit: object,
    *,
    resolver: ArtifactProgramResolverPort,
) -> tuple[BytecodeProgram, CompilerBinding]:
    """Resolve an admitted behaviour's program from the store it names.

    Every step is checked against something the behaviour already carries. The
    bytes come back by hash-bound reference and must digest to what the reference
    says; the program they parse to must hash to what it declares; the host ABI
    and bytecode version must be the ones the artifact states; and the capability
    set derived from the instructions must equal — exactly, not merely cover —
    what the behaviour declared it requires. A behaviour that declared less than
    its code reaches for would be admitted for one thing and run another.

    Nothing here accepts a program from a caller, and nothing calls a compiler.
    The reference is the behaviour's own, resolution happens against the exact
    durable store the production binding holds, and what comes out is bound to
    the behaviour it was resolved for.
    """

    from .behavior import ArtifactProgram, validate_behavior_unit
    from .behavior_program_artifacts import bind_artifact_behavior_unit

    validate_behavior_unit(unit)
    program_form = unit.core.canonical_program
    if type(program_form) is not ArtifactProgram:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "this behaviour does not name its program as a durable artifact",
        )
    reference = program_form.artifact_ref
    _ref(reference, "artifact_ref")
    if reference.schema_id != SchemaVersion.REPLAY_ARTIFACT_PROGRAM_V1.value:
        # The other admissible artifact schema is canonical IR, which is a pure
        # language and compiles rather than resolves. Refusing it here keeps the
        # two apart: this endpoint reads a program, it does not compile one.
        raise _fail(
            ReplayFailureCode.UNKNOWN_SCHEMA_VERSION,
            "this behaviour's program artifact is not a replay bytecode artifact",
        )
    raw = resolver.open_artifact(reference)
    if type(raw) is not bytes or not raw:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a program artifact must be exact bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != reference.sha256 or len(raw) != reference.byte_length:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the program artifact does not match the reference that named it",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
        if type(payload) is not dict or payload.get("type") != "bytecode_program":
            raise ValueError("artifact payload is not an exact bytecode program")
        program = BytecodeProgram.from_dict(payload)
        if program.to_dict() != payload:
            raise ValueError("artifact program does not round-trip exactly")
        if _canonical(payload) != raw:
            raise ValueError("artifact transport is not canonical")
    except Exception as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a program artifact is not canonical bytecode JSON",
        ) from exc
    declared_hash = payload.get("program_hash")
    if type(declared_hash) is not str or program.program_hash != declared_hash:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the artifact's program hash is not the hash of the program it contains",
        )
    required = capabilities_required_by(program)
    if tuple(sorted(unit.core.capability_requirements)) != required:
        raise _fail(
            ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
            "the behaviour's declared capabilities are not the ones its program requires",
        )
    try:
        producer_binding = bind_artifact_behavior_unit(unit, program=program)
    except CanonicalizationViolation as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "the program artifact violates the admitted canonical program ABI",
        ) from exc
    return program, producer_binding


# ---------------------------------------------------------------------------
# The machine port — NR-03's narrow typed adapter boundary
# ---------------------------------------------------------------------------


@runtime_checkable
class RecordedActivityChannelPort(Protocol):
    """What a machine may do with the channel a replay opened for it.

    Declared by the owner and implemented by the execution adapter, because the
    dependency has to run one way: a machine port naming the concrete
    ``RecordedActivityChannel`` would make the contract depend on the file that
    implements it, and an owner that imports its own adapter is one module
    spread across two names.

    Two operations, and they are the two the adapter actually performs: resolve
    an effect from record, and read the exact bytes that record holds. A machine
    cannot open a channel or close one — those belong to the replay that owns the
    attempt, not to the machine running inside it.

    Both halves of that list were wrong at once, and in opposite directions. The
    port declared ``resolve`` and ``remaining_budget`` while the adapter called
    ``resolve`` and ``open_result``: one operation the contract required was
    implemented by nothing and called by nothing, and one the code depended on
    was in no contract at all. The ``runtime_checkable`` check could not tell —
    it looks for attribute presence on the object handed over, never at what the
    caller uses — which is exactly why a port has to be read against the code
    rather than trusted because a check passed.
    """

    def resolve(self, *args: object, **kwargs: object) -> object: ...

    def open_result(self, activity: object) -> bytes: ...


_REPLAY_MACHINE_CONTEXT_SEAL = object()


class ReplayMachineExecutionContext:
    """Immutable deterministic identity available to the protected-core adapter."""

    __slots__ = (
        "run_id",
        "attempt_id",
        "repository_revision",
        "environment_profile_id",
        "policy_version",
        "_identity_snapshot",
        "_seal",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        if (
            kwargs.pop("_seal", None) is not _REPLAY_MACHINE_CONTEXT_SEAL
            or kwargs
            or len(args) != 5
        ):
            raise TypeError("ReplayMachineExecutionContext is factory-created")
        run_id, attempt_id, repository_revision, environment_profile_id, policy_version = args
        if (
            type(run_id) is not RunId
            or type(attempt_id) is not AttemptId
            or type(repository_revision) is not RepositoryRevision
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "machine execution context requires exact execution identities",
            )
        run_id.to_dict()
        attempt_id.to_dict()
        repository_revision.to_dict()
        values = (
            run_id,
            attempt_id,
            repository_revision,
            _identifier(environment_profile_id, "environment_profile_id"),
            _identifier(policy_version, "policy_version"),
        )
        for name, value in zip(self.__slots__[:5], values):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_identity_snapshot", values)
        object.__setattr__(self, "_seal", _REPLAY_MACHINE_CONTEXT_SEAL)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_seal", None) is _REPLAY_MACHINE_CONTEXT_SEAL:
            raise _fail(
                ReplayFailureCode.TRUSTED_OBJECT_FORGED,
                "replay machine execution context is immutable",
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "replay machine execution context is immutable",
        )


def replay_machine_execution_context(
    *,
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: RepositoryRevision,
    environment_profile_id: str,
    policy_version: str,
) -> ReplayMachineExecutionContext:
    """Seal the admitted fields shared by capture, run and resume."""

    return ReplayMachineExecutionContext(
        run_id,
        attempt_id,
        repository_revision,
        environment_profile_id,
        policy_version,
        _seal=_REPLAY_MACHINE_CONTEXT_SEAL,
    )


def require_replay_machine_execution_context(
    value: object,
) -> ReplayMachineExecutionContext:
    """Refuse a context that was assembled or changed outside its owner."""

    if (
        type(value) is not ReplayMachineExecutionContext
        or getattr(value, "_seal", None) is not _REPLAY_MACHINE_CONTEXT_SEAL
        or getattr(value, "_identity_snapshot", None)
        != tuple(getattr(value, name, None) for name in ReplayMachineExecutionContext.__slots__[:5])
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "replay machine execution context is not sealed",
        )
    return value


@runtime_checkable
class ReplayMachinePort(Protocol):
    """The narrow state, evidence and transition surface of a replay machine."""

    def program_hash(self) -> str: ...

    def host_abi_version(self) -> str: ...

    def transition_hash(self) -> str: ...

    def gas_remaining(self) -> int: ...

    def is_halted(self) -> bool: ...

    def next_opcode(self) -> str | None: ...

    def next_step_gas_cost(self) -> int:
        """The exact cost the protected-core machine will charge next."""

        ...

    def snapshot_digest(self) -> str: ...

    def snapshot_bytes(self) -> bytes:
        """The exact canonical bytes this machine's state is stored as.

        Required of a port, not only of the adapter, because every attempt now
        makes its terminal state durable: a continuation restores those bytes
        rather than being handed a machine.
        """

    def structural_history_bytes(self) -> bytes: ...

    def structural_history_complete(self) -> bool: ...

    def attach_channel(self, channel: RecordedActivityChannelPort) -> None:
        """Receive the channel this replay opened, before the first transition."""

    def step(self) -> None: ...


@runtime_checkable
class ReplayMachineFactoryPort(Protocol):
    """Construction boundary for the single protected-core replay adapter."""

    def adapter_id(self) -> str: ...

    def build(
        self,
        program: BytecodeProgram,
        *,
        gas_budget: int,
        execution_context: ReplayMachineExecutionContext,
        expected_structural_history: bytes | None,
    ) -> ReplayMachinePort: ...

    def restore(
        self,
        snapshot_bytes: bytes,
        *,
        gas_budget: int,
        execution_context: ReplayMachineExecutionContext,
        expected_structural_history: bytes | None,
    ) -> ReplayMachinePort: ...


_MACHINE_FACTORY_OPERATIONS = ("adapter_id", "build", "restore")


_MACHINE_PORT_OPERATIONS = (
    "program_hash", "host_abi_version", "transition_hash", "gas_remaining",
    "is_halted", "next_opcode", "next_step_gas_cost", "snapshot_digest",
    "snapshot_bytes", "structural_history_bytes", "structural_history_complete",
    "attach_channel", "step",
)


_EXECUTION_PERMIT_SEAL = object()


class ReplayExecutionReceipt:
    """One-shot proof that a durable request reached the governed execution path.

    Spend performs the durable CAS while its live coordinator guard is held;
    transition entry then consumes this object once inside the process.
    """

    __slots__ = (
        "_seal", "_binding", "_request_ref", "_decision_refs", "_epoch",
        "_execution_identity", "_guard", "_spent", "_entered",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _EXECUTION_PERMIT_SEAL or kwargs or len(args) != 6:
            raise TypeError("ReplayExecutionReceipt is issued only by the governed replay path")
        self._seal = _EXECUTION_PERMIT_SEAL
        (
            self._binding,
            self._request_ref,
            self._decision_refs,
            self._epoch,
            self._execution_identity,
            self._guard,
        ) = args
        self._spent = False
        self._entered = False




def _require_durable_request_ref(
    request: BehaviorReplayRequest, *, binding: ProductionReplayBinding
) -> HashBoundRef:
    reference = replay_request_ref(request)
    recorded = {_ref_key(item) for item in binding.replay_store.recorded_request_refs()}
    if _ref_key(reference) not in recorded:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "a governed replay requires its exact durable request",
        )
    return reference


def _issue_execution_receipt(
    request: BehaviorReplayRequest,
    *,
    binding: ProductionReplayBinding,
    coordinator_guard: object,
) -> ReplayExecutionReceipt:
    """Issue only from durable lineage under the currently live writer guard."""

    from .admission_journal import require_live_guard

    require_live_guard(
        coordinator_guard, coordinator_id=binding.fence.coordinator_id()
    )
    settled_epoch = binding.fence.current_epoch()
    if type(settled_epoch) is not int or settled_epoch < 0 or settled_epoch % 2:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "an execution receipt requires a settled coordinator",
        )
    reference = _require_durable_request_ref(request, binding=binding)
    for decision_ref in request.activity_policy_decision_refs:
        binding.activity_policy_store.require_decision(
            decision_ref, evaluator=binding.activity_policy_evaluator
        )
    # The whole lineage is resolved here, out of the same store, and the identity
    # is computed from what came back rather than from what the request says
    # about itself. A request naming a manifest that is not there, or a manifest
    # that no longer projects its capture, gets no receipt.
    identity = _execution_identity_from_durable_lineage(request, binding=binding)
    if identity in binding.replay_store.spent_execution_identities():
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "this attempt's execution permission was already spent",
        )
    return ReplayExecutionReceipt(
        binding,
        _ref_key(reference),
        tuple(_ref_key(item) for item in request.activity_policy_decision_refs),
        int(settled_epoch),
        identity,
        coordinator_guard,
        _seal=_EXECUTION_PERMIT_SEAL,
    )


def _execution_identity(
    request: BehaviorReplayRequest,
    *,
    binding: ProductionReplayBinding,
    manifest: ReplayExecutionManifest,
    capture: ReferenceReplayCapture,
) -> str:
    """The digest that names *this* attempt, and no other.

    Everything that makes the attempt what it is goes in: the request, the
    manifest it is measured against and the capture that manifest projects, the
    policy decisions it pinned, the exact execution configuration it runs under
    and the executor whose provenance it carries. Two different attempts cannot
    collide, and one attempt cannot be claimed twice under two receipts — which
    is the whole reason the claim is durable rather than a flag on an object.
    """

    payload = {
        "profile": REPLAY_EXECUTION_SPEND_PROFILE_V1,
        "request_ref": replay_request_ref(request).to_dict(),
        "manifest_ref": request.execution_manifest_ref.to_dict(),
        "capture_ref": manifest.source_capture_ref.to_dict(),
        "capture_id": capture.capture_id.to_dict(),
        "activity_policy_decision_refs": [
            item.to_dict() for item in request.activity_policy_decision_refs
        ],
        "capability_profile_digest": request.capability_profile_digest,
        "gas_budget": request.gas_budget,
        "cognitive_budget": request.cognitive_budget,
        "step_limit": request.step_limit,
        "executor_actor": binding.executor_actor.value,
        "adapter": REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _execution_identity_from_durable_lineage(
    request: BehaviorReplayRequest,
    *,
    binding: ProductionReplayBinding,
) -> str:
    """Resolve the claim identity before opening its narrow CAS transaction."""

    manifest = binding.replay_store.require_manifest(request.execution_manifest_ref)
    capture = binding.replay_store.require_capture(manifest.source_capture_ref)
    require_manifest_projects_capture(manifest, capture=capture)
    identity = _execution_identity(
        request, binding=binding, manifest=manifest, capture=capture
    )
    return identity


def _spend_execution_permit(
    permit: object,
    *,
    request: BehaviorReplayRequest,
    binding: ProductionReplayBinding,
) -> None:
    """Re-derive the receipt claims and durably spend this attempt once."""

    if (
        type(permit) is not ReplayExecutionReceipt
        or getattr(permit, "_seal", None) is not _EXECUTION_PERMIT_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "a replay body requires an execution receipt from the governed path",
        )
    if permit._spent:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "this execution receipt has already been spent",
        )
    if permit._binding is not binding:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "this execution receipt belongs to another production binding",
        )
    reference = _require_durable_request_ref(request, binding=binding)
    if permit._request_ref != _ref_key(reference):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "this execution receipt was issued for another request",
        )
    pinned = tuple(_ref_key(item) for item in request.activity_policy_decision_refs)
    if permit._decision_refs != pinned:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "this execution receipt was issued for another set of policy decisions",
        )
    for decision_ref in request.activity_policy_decision_refs:
        binding.activity_policy_store.require_decision(
            decision_ref, evaluator=binding.activity_policy_evaluator
        )
    current = binding.fence.current_epoch()
    if type(current) is not int or current % 2 or current != permit._epoch:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the coordinator moved between the receipt and this body",
        )
    from .coordination import settle_exclusive_mutation
    from .persistence import store_transaction

    with store_transaction(binding.fence, guard=permit._guard) as ticket:
        binding.replay_store.spend_execution(
            permit._execution_identity, request_ref=reference, ticket=ticket
        )
    settle_exclusive_mutation(
        fence=binding.fence,
        coordinator_id=binding.fence.coordinator_id(),
        entry_epoch=permit._epoch,
        own_intervals=1,
    )
    permit._spent = True


def _enter_execution_permit(
    permit: object,
    *,
    request: BehaviorReplayRequest,
    binding: ProductionReplayBinding,
) -> None:
    """Enter the transition body once under an already durable-spent receipt."""

    if (
        type(permit) is not ReplayExecutionReceipt
        or permit._seal is not _EXECUTION_PERMIT_SEAL
        or permit._binding is not binding
        or permit._request_ref != _ref_key(replay_request_ref(request))
        or not permit._spent
        or permit._entered
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "the replay transition body requires one unconsumed durable receipt",
        )
    from .admission_journal import JournalAdapterViolation, require_live_guard

    try:
        require_live_guard(
            permit._guard, coordinator_id=binding.fence.coordinator_id()
        )
    except JournalAdapterViolation as exc:
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "the execution receipt outlived its coordinator guard",
        ) from exc
    if binding.fence.current_epoch() != permit._epoch + 2:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the coordinator moved after the durable execution spend",
        )
    permit._entered = True


def require_machine_port(value: object) -> ReplayMachinePort:
    """Refuse a machine that cannot answer every question the profile asks."""

    missing = [name for name in _MACHINE_PORT_OPERATIONS if not callable(getattr(value, name, None))]
    if missing:
        raise _fail(
            ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
            f"machine port is missing {', '.join(missing[:4])}",
        )
    return value  # type: ignore[return-value]


def require_machine_factory_port(value: object) -> ReplayMachineFactoryPort:
    """Refuse any second or incomplete protected-core construction path."""

    missing = [
        name for name in _MACHINE_FACTORY_OPERATIONS
        if not callable(getattr(value, name, None))
    ]
    if missing or value.adapter_id() != REPLAY_MACHINE_ADAPTER_ID_V1_E1:
        raise _fail(
            ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
            "replay machine factory does not implement the frozen adapter contract",
        )
    return value  # type: ignore[return-value]


def _is_sealed_activity_channel(value: object) -> bool:
    """Whether this channel was opened by a governed replay.

    Asked of the *seal*, not of the type. The seal is the owner's and cannot be
    reached from outside the package; the class that carries it belongs to the
    execution adapter, and a machine adapter reaching for that class would be one
    adapter depending on another for something the owner can answer itself.

    Exactness of the concrete channel type is checked where that type is defined,
    when the production binding is assembled — which is the party that
    legitimately knows both.
    """

    return getattr(value, "_seal", None) is _CHANNEL_SEAL


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
    "UNKNOWN_ACTIVITY_KIND": ReplayFailureReason.UNKNOWN_HOST_CALL,
    "COGNITIVE_BUDGET_EXHAUSTED": ReplayFailureReason.COGNITIVE_BUDGET_EXHAUSTED,
}


def reason_for_activity_failure(exc: ActivityViolation) -> ReplayFailureReason:
    if type(exc) is not ActivityViolation:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "an exact ActivityViolation is required")
    return _ACTIVITY_REASONS.get(exc.failure_code.value, ReplayFailureReason.FORBIDDEN_HOST_CALL)


_MACHINE_REPLAY_REASONS = {
    ReplayFailureCode.CHANNEL_CLOSED: ReplayFailureReason.SIDE_EFFECT_OUTSIDE_PLAN,
    ReplayFailureCode.UNGOVERNED_DISPATCH: ReplayFailureReason.FORBIDDEN_HOST_CALL,
    ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH: ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH,
    ReplayFailureCode.RESULT_NOT_DECODABLE: ReplayFailureReason.ACTIVITY_SUBSTITUTED,
    ReplayFailureCode.INJECTION_PRIMITIVE_MISSING: ReplayFailureReason.FORBIDDEN_HOST_CALL,
    ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH: ReplayFailureReason.TRANSITION_MISMATCH,
    ReplayFailureCode.NON_CANONICAL_VM_VALUE: ReplayFailureReason.TRANSITION_MISMATCH,
    ReplayFailureCode.MACHINE_EXECUTION_FAULT: ReplayFailureReason.MACHINE_FAULT,
}


def _reason_for_machine_failure(exc: ReplayViolation) -> ReplayFailureReason:
    """Translate a typed machine-port refusal into the durable result vocabulary."""

    return _MACHINE_REPLAY_REASONS.get(exc.failure_code, ReplayFailureReason.MACHINE_FAULT)


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
        # Kept, not merely checked. The machine adapter has to be able to ask
        # whether a channel was opened by a governed replay, and it may not do so
        # by naming this class — that would be one adapter depending on another
        # for something the owner can answer. So the seal stays reachable to the
        # owner's own predicate and to nothing else: it is a module-private
        # object, so holding it proves the channel came from here.
        self._seal = _CHANNEL_SEAL
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
            if code == "RESULT_UNAVAILABLE":
                raise ActivityViolation(
                    ActivityFailureCode.ACTIVITY_NOT_RECORDED,
                    "the recorded result is proved absent from the durable store",
                ) from exc
            if code in ("RESULT_CORRUPTED", "RESULT_REF_MISMATCH"):
                raise ActivityViolation(
                    ActivityFailureCode.RESULT_HASH_MISMATCH,
                    "the durable result is proved inconsistent with its record",
                ) from exc
            if code == "BACKEND_UNAVAILABLE":
                raise ActivityViolation(
                    ActivityFailureCode.BACKEND_UNAVAILABLE,
                    "the durable activity backend could not complete the read",
                ) from exc
            raise
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
    """Bind one producer-bound behavior for replay, revalidating its evidence.

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
    #: The manifest this attempt is measured against, named in the request
    #: itself. Without it the chain has a gap exactly where it matters: a result
    #: names its request, and the request named the admission and the activities
    #: but not the statement of what the run was supposed to reach, so a reader
    #: holding the durable record could not get from an outcome back to the
    #: observation it was compared with.
    execution_manifest_ref: HashBoundRef
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
        "execution_manifest_ref": value.execution_manifest_ref.to_dict(),
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
    _ref(value.execution_manifest_ref, "execution_manifest_ref")
    _identifier(value.knowledge_snapshot_id, "knowledge_snapshot_id")
    _identifier(value.policy_version, "policy_version")
    if value.capability_profile != REPLAY_CAPABILITY_PROFILE_V1_E1:
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


_PREPARED_REPLAY_SEAL = object()


@dataclass(frozen=True, init=False)
class _PreparedReplay:
    """What one admission established, sealed so nobody can assemble another.

    A frozen dataclass anyone could construct was enough while this object only
    travelled between two private functions. It stopped being enough when the
    reference capture began taking one: a caller holding a hand-built
    ``_PreparedReplay`` could name any admitted set, any ledger and any programs,
    and the capture would faithfully observe a run over them.

    It is sealed and bound to the exact production binding it was prepared
    against, and it carries the *compiled programs* rather than only their
    hashes. Carrying them is the point: they were compiled once, before the final
    revalidation, and every later phase runs the programs that admission covered.
    Compiling again afterwards would run whatever the compiler returned the second
    time — outside the barrier the first compilation crossed.
    """

    binding: object
    admitted: CurrentAdmittedKnowledge
    snapshot_manifest_ref: HashBoundRef
    bindings: tuple[ReplayProgramBinding, ...]
    programs: tuple[object, ...]
    ledger: ActivityLedger
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> _PreparedReplay:
        raise TypeError("_PreparedReplay is produced only by _prepare_replay")


def require_prepared_replay(value: object, *, binding: object) -> _PreparedReplay:
    """Refuse a prepared replay this binding did not prepare."""

    if (
        type(value) is not _PreparedReplay
        or getattr(value, "_trusted_seal", None) is not _PREPARED_REPLAY_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "a prepared replay is produced only by the governed preparation path",
        )
    if value.binding is not binding:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "this prepared replay belongs to another production binding",
        )
    return value


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
        binding.activity_policy_store.require_production_provenance_for_activity(
            record.production_provenance_ref,
            evaluator=binding.activity_policy_evaluator,
            activity=record,
        )
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

    from .behavior import ArtifactProgram

    # Two forms of admitted program, resolved once each, here, inside the
    # barrier. Inline IR is compiled; a program named as a durable artifact is
    # *resolved* — read out of the exact store this binding holds and checked
    # against the reference, hash and capability set the behaviour carries. The
    # outputs are kept on the prepared object so no later phase has to ask again:
    # a second compilation would return whatever the compiler returns the second
    # time, which is not what this admission covered.
    programs: list[object] = []
    compiled_bindings: list[ReplayProgramBinding] = []
    for item in subjects:
        if type(item.unit.core.canonical_program) is ArtifactProgram:
            program, producer_binding = resolve_artifact_program(
                item.unit, resolver=binding.artifact_resolver
            )
            programs.append(program)
            compiled_bindings.append(
                replay_program_binding(unit=item.unit, binding=producer_binding)
            )
            continue
        if not callable(compiler):
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a replay needs a callable compiler")
        output = compiler(item.unit)
        programs.append(output.program)
        compiled_bindings.append(replay_program_binding(unit=item.unit, binding=output))
    compiled = tuple(compiled_bindings)

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
    prepared = object.__new__(_PreparedReplay)
    object.__setattr__(prepared, "binding", binding)
    object.__setattr__(prepared, "admitted", final)
    object.__setattr__(prepared, "snapshot_manifest_ref", snapshot_manifest_ref)
    object.__setattr__(prepared, "bindings", compiled)
    object.__setattr__(prepared, "programs", tuple(programs))
    object.__setattr__(prepared, "ledger", ledger)
    object.__setattr__(prepared, "_trusted_seal", _PREPARED_REPLAY_SEAL)
    return prepared


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
            production = (
                binding.activity_policy_store.require_production_provenance_for_activity(
                    activity.production_provenance_ref,
                    evaluator=binding.activity_policy_evaluator,
                    activity=activity,
                )
            )
            decision = evaluate_activity_policy(
                binding.activity_policy_evaluator,
                activity=activity,
                consumer_context_ref=prepared.admitted.consumer_context_ref,
                boundary_ref=prepared.admitted.boundary_ref,
                run_id=prepared.admitted.envelope.run_id,
                attempt_id=prepared.admitted.envelope.attempt_id,
                environment_profile_id=prepared.admitted.envelope.environment_profile_id,
                capability_profile_digest=capability_profile_digest(),
                production=production,
                consumption=binding.consumption_provenance,
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
                production=production,
                consumption=binding.consumption_provenance,
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
    execution_manifest_ref: HashBoundRef,
    expected_transcript_root: str | None = None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None = None,
    resumed_from_result_ref: HashBoundRef | None = None,
) -> BehaviorReplayRequest:
    """Seal the request from final admission and already evaluated decisions.

    ``execution_manifest_ref`` is mandatory and is not decoration. It is the
    link that makes the chain resolvable in one direction: a result names its
    request, the request names the manifest it was measured against, and the
    manifest names the capture it projects. Without it a reader holding a result
    can say what was expected of the run but not where that expectation came
    from, which is exactly the gap a caller-stated expected value used to sit in.
    """

    admitted = prepared.admitted
    ledger = prepared.ledger
    payload = object.__new__(BehaviorReplayRequest)
    object.__setattr__(payload, "schema_version", SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1)
    object.__setattr__(payload, "knowledge_snapshot_id", prepared.snapshot_manifest_ref.ref_id)
    object.__setattr__(payload, "snapshot_manifest_ref", prepared.snapshot_manifest_ref)
    object.__setattr__(payload, "bindings", prepared.bindings)
    object.__setattr__(payload, "capability_profile", REPLAY_CAPABILITY_PROFILE_V1_E1)
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
    object.__setattr__(
        payload, "execution_manifest_ref", _ref(execution_manifest_ref, "execution_manifest_ref")
    )
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
# ReplayExecutionManifest — what a replay is supposed to reach, stated in advance
# ---------------------------------------------------------------------------

#: The schema and media type a durable VM snapshot is stored and named under.
REPLAY_VM_SNAPSHOT_MEDIA_TYPE = "application/json"

#: The largest machine snapshot this replay will make durable. Declared by the
#: owner rather than by the store: what a snapshot may be is a property of the
#: machine integration, and a store that chose the ceiling itself could accept
#: a state the executor would refuse to produce.
MAX_MACHINE_SNAPSHOT_BYTES_V1_E1 = 8 * 1024 * 1024
# E1 embeds up to one full structural-history object beside the CVM snapshot.
MAX_SNAPSHOT_BYTES_V1_E1 = 2 * 8 * 1024 * 1024 + 64 * 1024

_MANIFEST_SEAL = object()
_CAPTURE_SEAL = object()
_CAPTURE_AUTHORITY_SEAL = object()


@dataclass(frozen=True)
class ReplayRecordContext:
    """The §13 execution identity a manifest is stamped with.

    A manifest is written *before* the run it describes, so it cannot take its
    envelope from an admission the way a request and a result do — the crossing
    has not happened yet. It is derived instead from the capture the manifest
    projects, by ``record_context_of_capture``: the reference execution already
    happened under an attempt, that attempt already stamped the capture, and a
    projection of an observation belongs to the same attempt as the observation.

    It used to arrive as a free argument, which put the five fields under the
    caller's control at exactly the moment nothing had checked them yet — a
    manifest could name any run and any attempt it liked, and the disagreement
    would only surface later, at ``_require_manifest_describes``, as a refusal
    about something the caller had chosen. Now there is nothing to choose.
    """

    run_id: RunId
    attempt_id: AttemptId
    repository_revision: RepositoryRevision
    environment_profile_id: str
    policy_version: str
    created_at_utc: datetime


def record_context_of_capture(capture: ReferenceReplayCapture) -> ReplayRecordContext:
    """The §13 identity a manifest issued from this capture must carry."""

    validate_reference_capture(capture)
    envelope = capture.envelope
    return ReplayRecordContext(
        run_id=envelope.run_id,
        attempt_id=envelope.attempt_id,
        repository_revision=envelope.repository_revision,
        environment_profile_id=envelope.environment_profile_id,
        policy_version=envelope.policy_version,
        created_at_utc=envelope.created_at_utc,
    )


@dataclass(frozen=True, init=False)
class ReplayExecutionManifest:
    """The capture-derived initial state and exact outcome a replay must reach."""

    schema_version: SchemaVersion
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    manifest_id: RecordId
    #: The reference capture every expected value below was read out of. Not
    #: optional and not decorative: without it a manifest is a set of numbers
    #: whose origin nobody can check, which is what it was when a caller supplied
    #: them. A reader resolves this reference in the same store and compares.
    source_capture_ref: HashBoundRef
    #: The behaviours, in execution order, this manifest describes.
    behavior_content_keys: tuple[str, ...]
    program_hashes: tuple[str, ...]
    host_abi_versions: tuple[str, ...]
    #: Where each machine's starting state lives, and what it must digest to.
    initial_snapshot_refs: tuple[HashBoundRef, ...]
    initial_snapshot_digests: tuple[str, ...]
    expected_structural_history_refs: tuple[HashBoundRef, ...]
    #: The order-sensitive fold of the transcript the run must reproduce.
    expected_transcript_root: str
    #: Where each machine must end. Never optional: a run with no expected
    #: terminal state has nothing to be identical *to*.
    expected_terminal_snapshot_digests: tuple[str, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ReplayExecutionManifest:
        raise TypeError(
            "ReplayExecutionManifest is issued only from a capture, by its authority"
        )

    def to_dict(self) -> dict[str, object]:
        validate_replay_manifest(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _manifest_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_replay_manifest(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_manifest_payload(self),
        )


def _manifest_payload(value: ReplayExecutionManifest) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "source_capture_ref": value.source_capture_ref.to_dict(),
        "behavior_content_keys": list(value.behavior_content_keys),
        "program_hashes": list(value.program_hashes),
        "host_abi_versions": list(value.host_abi_versions),
        "initial_snapshot_refs": [item.to_dict() for item in value.initial_snapshot_refs],
        "initial_snapshot_digests": list(value.initial_snapshot_digests),
        "expected_structural_history_refs": [
            item.to_dict() for item in value.expected_structural_history_refs
        ],
        "expected_transcript_root": value.expected_transcript_root,
        "expected_terminal_snapshot_digests": list(value.expected_terminal_snapshot_digests),
    }


_MANIFEST_PAYLOAD_FIELDS_V1_E1 = frozenset({
        "schema_version", "source_capture_ref", "behavior_content_keys",
        "program_hashes", "host_abi_versions", "initial_snapshot_refs",
        "initial_snapshot_digests", "expected_structural_history_refs",
        "expected_transcript_root", "expected_terminal_snapshot_digests",
})


def validate_replay_manifest(value: object) -> ReplayExecutionManifest:
    if (
        type(value) is not ReplayExecutionManifest
        or getattr(value, "_trusted_seal", None) is not _MANIFEST_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED, "replay manifest is not factory sealed"
        )
    if value.schema_version is not SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "replay manifest schema is unknown")
    count = len(value.behavior_content_keys)
    if not count or count > _MAX_BEHAVIORS:
        raise _fail(ReplayFailureCode.BEHAVIOR_SET_EMPTY, "a manifest describes at least one behavior")
    for name in (
        "program_hashes", "host_abi_versions", "initial_snapshot_refs",
        "initial_snapshot_digests", "expected_structural_history_refs",
        "expected_terminal_snapshot_digests",
    ):
        column = getattr(value, name)
        if type(column) is not tuple or len(column) != count:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                f"manifest column {name} does not describe every behavior",
            )
    for reference in value.initial_snapshot_refs:
        _ref(reference, "initial_snapshot_ref")
        if reference.schema_id != SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value:
            raise _fail(
                ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
                "an initial snapshot reference does not name a machine snapshot",
            )
    for reference in value.expected_structural_history_refs:
        _ref(reference, "expected_structural_history_ref")
        if reference.schema_id != REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1:
            raise _fail(
                ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                "manifest structural history uses another schema",
            )
    for digest in (
        *value.initial_snapshot_digests, *value.expected_terminal_snapshot_digests
    ):
        _sha256(digest, "snapshot_digest")
    _sha256(value.expected_transcript_root, "expected_transcript_root")
    _ref(value.source_capture_ref, "source_capture_ref")
    if value.source_capture_ref.schema_id != SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1.value:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "a manifest must name the reference capture it was issued from",
        )
    _require_envelope_bound(
        envelope=value.envelope,
        envelope_binding_sha256=value.envelope_binding_sha256,
        payload=_manifest_payload(value),
        identity_domain=IdentityDomain.REPLAY_EXECUTION_MANIFEST,
        field_name="replay manifest",
    )
    # The envelope binds the payload, and this binds the *name* to the envelope.
    # Without it a manifest could be stored, resolved and executed under an id
    # that names nothing — every other check would pass, because every other
    # check is about the payload.
    if value.manifest_id != value.envelope.record_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the manifest id does not name the envelope it was issued under",
        )
    return value


@dataclass(frozen=True, init=False)
class ReferenceReplayCapture:
    """The exact inputs and observed evidence of one reference execution."""

    schema_version: SchemaVersion
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    capture_id: RecordId
    #: The admission and boundary this capture was taken under.
    knowledge_snapshot_id: str
    snapshot_manifest_ref: HashBoundRef
    boundary_ref: HashBoundRef
    admitted_knowledge_id: RecordId
    #: The behaviours, in execution order.
    behavior_content_keys: tuple[str, ...]
    program_hashes: tuple[str, ...]
    host_abi_versions: tuple[str, ...]
    #: Where each machine started, and what that state digests to.
    initial_snapshot_refs: tuple[HashBoundRef, ...]
    initial_snapshot_digests: tuple[str, ...]
    #: The activity history that was resolvable during the reference run.
    recorded_activity_refs: tuple[HashBoundRef, ...]
    activity_identities: tuple[str, ...]
    observed_structural_history_refs: tuple[HashBoundRef, ...]
    #: The vocabulary the run was classified under, and what it was allowed.
    capability_profile_digest: str
    gas_budget: int
    cognitive_budget: int
    step_limit: int
    #: Observed. Never supplied.
    observed_transcript_root: str
    observed_terminal_snapshot_digests: tuple[str, ...]
    #: Whether the reference execution reproduced the behaviour's replay
    #: contract. A capture that did not is still a true record of what running
    #: this program produced, and it is kept: an execution that departs from its
    #: contract is evidence, not an infrastructure failure. What it may not do is
    #: become a manifest — see ``publish_replay_manifest``.
    contract_matched: bool
    contract_failure_reason: ReplayFailureReason | None
    #: The result this capture continues, if it continues one. Lineage only: the
    #: starting state is restored from durable snapshot bytes, never by re-running
    #: what came before. It also decides whether the replay contract applies —
    #: a continuation starts from a terminal state and executes the tail of a
    #: behaviour, so measuring its transcript against a contract describing the
    #: whole behaviour would refuse every continuation ever taken.
    capture_resumed_from_result_ref: HashBoundRef | None
    #: The activity policy decisions this reference run was permitted under,
    #: taken freshly rather than inherited: a record already in the store is not
    #: thereby consumable now.
    activity_policy_decision_refs: tuple[HashBoundRef, ...]
    #: Who ran it, taken from the production binding. §9.4 names seven actors
    #: and a reference run introduces no eighth: the same ``replay_executor_actor``
    #: performs the reference execution and the run it will later be measured
    #: against, and the two are told apart by phase and by record identity.
    #:
    #: There is deliberately no authority actor beside it. The party that seals a
    #: capture is a *position* — the exact production binding and the coordinator
    #: it writes through — not a name a caller supplies, and a name in this record
    #: would be the caller-declared identity §9.4 refuses.
    replay_executor_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ReferenceReplayCapture:
        raise TypeError("ReferenceReplayCapture is produced only by capture_reference_replay")

    def to_dict(self) -> dict[str, object]:
        validate_reference_capture(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _capture_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_reference_capture(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_capture_payload(self),
        )


def _capture_payload(value: ReferenceReplayCapture) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "knowledge_snapshot_id": value.knowledge_snapshot_id,
        "snapshot_manifest_ref": value.snapshot_manifest_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "admitted_knowledge_id": value.admitted_knowledge_id.to_dict(),
        "behavior_content_keys": list(value.behavior_content_keys),
        "program_hashes": list(value.program_hashes),
        "host_abi_versions": list(value.host_abi_versions),
        "initial_snapshot_refs": [item.to_dict() for item in value.initial_snapshot_refs],
        "initial_snapshot_digests": list(value.initial_snapshot_digests),
        "recorded_activity_refs": [item.to_dict() for item in value.recorded_activity_refs],
        "activity_identities": list(value.activity_identities),
        "observed_structural_history_refs": [
            item.to_dict() for item in value.observed_structural_history_refs
        ],
        "capability_profile_digest": value.capability_profile_digest,
        "gas_budget": value.gas_budget,
        "cognitive_budget": value.cognitive_budget,
        "step_limit": value.step_limit,
        "observed_transcript_root": value.observed_transcript_root,
        "observed_terminal_snapshot_digests": list(value.observed_terminal_snapshot_digests),
        "contract_matched": value.contract_matched,
        "capture_resumed_from_result_ref": (
            None if value.capture_resumed_from_result_ref is None
            else value.capture_resumed_from_result_ref.to_dict()
        ),
        "contract_failure_reason": (
            None if value.contract_failure_reason is None
            else value.contract_failure_reason.value
        ),
        "activity_policy_decision_refs": [
            item.to_dict() for item in value.activity_policy_decision_refs
        ],
        "replay_executor_actor": value.replay_executor_actor.to_dict(),
    }


_CAPTURE_PAYLOAD_FIELDS_V1_E1 = frozenset({
        "schema_version", "knowledge_snapshot_id", "snapshot_manifest_ref",
        "boundary_ref", "admitted_knowledge_id", "behavior_content_keys",
        "program_hashes", "host_abi_versions", "initial_snapshot_refs",
        "initial_snapshot_digests", "recorded_activity_refs", "activity_identities",
        "observed_structural_history_refs", "capability_profile_digest", "gas_budget",
        "cognitive_budget", "step_limit", "observed_transcript_root",
        "observed_terminal_snapshot_digests", "contract_matched",
        "capture_resumed_from_result_ref", "contract_failure_reason",
        "activity_policy_decision_refs", "replay_executor_actor",
})


def validate_reference_capture(value: object) -> ReferenceReplayCapture:
    if (
        type(value) is not ReferenceReplayCapture
        or getattr(value, "_trusted_seal", None) is not _CAPTURE_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED, "reference capture is not factory sealed"
        )
    if value.schema_version is not SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "reference capture schema is unknown")
    count = len(value.behavior_content_keys)
    if not count or count > _MAX_BEHAVIORS:
        raise _fail(ReplayFailureCode.BEHAVIOR_SET_EMPTY, "a capture describes at least one behavior")
    for name in (
        "program_hashes", "host_abi_versions", "initial_snapshot_refs",
        "initial_snapshot_digests", "observed_structural_history_refs",
        "observed_terminal_snapshot_digests",
    ):
        column = getattr(value, name)
        if type(column) is not tuple or len(column) != count:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                f"capture column {name} does not describe every behavior",
            )
    for reference in value.initial_snapshot_refs:
        _ref(reference, "initial_snapshot_ref")
        if reference.schema_id != SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value:
            raise _fail(
                ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
                "an initial snapshot reference does not name a machine snapshot",
            )
    for reference in value.observed_structural_history_refs:
        _ref(reference, "observed_structural_history_ref")
        if reference.schema_id != REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1:
            raise _fail(
                ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                "capture structural history uses another schema",
            )
    for digest in (
        *value.initial_snapshot_digests, *value.observed_terminal_snapshot_digests
    ):
        _sha256(digest, "snapshot_digest")
    _sha256(value.observed_transcript_root, "observed_transcript_root")
    _sha256(value.capability_profile_digest, "capability_profile_digest")
    _sha256(value.knowledge_snapshot_id, "knowledge_snapshot_id")
    for name in ("gas_budget", "cognitive_budget", "step_limit"):
        amount = getattr(value, name)
        if type(amount) is not int or amount < 0:
            raise _fail(ReplayFailureCode.TYPE_MISMATCH, f"capture {name} must be a non-negative int")
    if type(value.replay_executor_actor) is not ActorIdentity:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "replay_executor_actor must be exact")
    if type(value.contract_matched) is not bool:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "contract_matched must be an exact bool")
    if value.contract_failure_reason is not None and (
        type(value.contract_failure_reason) is not ReplayFailureReason
    ):
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "contract_failure_reason must be exact")
    if value.contract_matched and value.contract_failure_reason is not None:
        raise _fail(
            ReplayFailureCode.STATUS_REASON_INCONSISTENT,
            "a capture cannot both match its contract and carry a reason for not matching",
        )
    for reference in value.activity_policy_decision_refs:
        _ref(reference, "activity_policy_decision_ref")
    if value.capture_resumed_from_result_ref is not None:
        _ref(value.capture_resumed_from_result_ref, "capture_resumed_from_result_ref")
    _require_envelope_bound(
        envelope=value.envelope,
        envelope_binding_sha256=value.envelope_binding_sha256,
        payload=_capture_payload(value),
        identity_domain=IdentityDomain.REFERENCE_REPLAY_CAPTURE,
        field_name="reference capture",
    )
    if value.capture_id != value.envelope.record_id:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the capture id is not the identity its envelope computed",
        )
    return value


def reference_capture_ref(value: ReferenceReplayCapture) -> HashBoundRef:
    validate_reference_capture(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.capture_id.digest_sha256,
        schema_id=SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


@dataclass(frozen=True, init=False)
class ReferenceCaptureAuthority:
    """The platform authority that may seal a capture and issue a manifest from it.

    Declared by the owner, and not by the adapter that runs a reference
    execution. Who may seal a capture and what makes one publishable are
    normative rules about a replay record; an adapter that held them would be
    deciding policy on the owner's behalf, and a reader looking for the rule
    would find it in a file whose subject is driving a machine.

    It declares no actor. An earlier revision took a ``capture_authority_actor``
    from its caller and checked only that the name differed from the executor's,
    which made the authority a string the caller chose — the same
    caller-declared-identity defect the actor set exists to prevent, arriving
    through the preparation phase.

    What an authority is here is a *position*: it holds the exact production
    binding, the coordinator that binding writes through, and the execution
    domain those two define. It is minted from a validated binding and from
    nothing else, so there is no name to forge and nothing to compare against an
    actor set. §9.4 is untouched: no eighth role is introduced, and the same
    ``replay_executor_actor`` performs both phases.
    """

    binding: ProductionReplayBinding
    authority: ProductionAuthorityBinding
    fence: object
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ReferenceCaptureAuthority:
        raise TypeError("ReferenceCaptureAuthority is issued only by its factory")


def create_reference_capture_authority(
    *, binding: ProductionReplayBinding
) -> ReferenceCaptureAuthority:
    """Take the authority position this binding defines.

    Nothing is supplied but the binding, and the binding is revalidated here
    rather than trusted from whenever it was assembled.
    """

    binding = validate_production_replay_binding(binding)
    validate_production_authority_binding(binding.authority)
    payload = object.__new__(ReferenceCaptureAuthority)
    object.__setattr__(payload, "binding", binding)
    object.__setattr__(payload, "authority", binding.authority)
    object.__setattr__(payload, "fence", binding.fence)
    object.__setattr__(payload, "_trusted_seal", _CAPTURE_AUTHORITY_SEAL)
    return payload


def require_reference_capture_authority(
    value: object, *, binding: ProductionReplayBinding
) -> ReferenceCaptureAuthority:
    """Refuse an authority that is not sealed, or belongs to another binding."""

    if (
        type(value) is not ReferenceCaptureAuthority
        or getattr(value, "_trusted_seal", None) is not _CAPTURE_AUTHORITY_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "a reference capture requires a sealed capture authority",
        )
    if value.binding is not binding or value.fence is not binding.fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "this capture authority belongs to another production binding",
        )
    return value


def seal_reference_capture(
    *,
    prepared: _PreparedReplay,
    binding: ProductionReplayBinding,
    runs: tuple[_TransitionRun, ...],
    machines: tuple[ReplayMachinePort, ...],
    snapshot_refs: tuple[HashBoundRef, ...],
    initial_digests: tuple[str, ...],
    structural_history_refs: tuple[HashBoundRef, ...],
    decision_refs: tuple[HashBoundRef, ...],
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    resumed_from_result_ref: HashBoundRef | None,
) -> tuple[ReferenceReplayCapture, bool]:
    """Seal observed execution facts and report whether capture was incomplete."""

    prepared = require_prepared_replay(prepared, binding=binding)
    binding = validate_production_replay_binding(binding)
    bindings = prepared.bindings
    if (
        len(machines) != len(bindings)
        or len(runs) > len(bindings)
        or len(structural_history_refs) != len(bindings)
    ):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "a reference capture describes one machine per admitted behavior",
        )

    transitions: list[str] = []
    activities: list[str] = []
    terminal_digests: list[str] = []
    contract_matched = True
    contract_failure_reason: ReplayFailureReason | None = None
    incomplete = False
    for run in runs:
        if run.failure_reason is not None and (
            status_for_reason(run.failure_reason) is not ReplayStatus.REPLAY_INCOMPATIBLE
            and run.failure_reason is not ReplayFailureReason.TRANSITION_MISMATCH
        ):
            # A transcript that does not match the behaviour's contract is a fact
            # about the behaviour: the run completed, it simply did not do what
            # the contract said. So is an incompatibility — a program hash, a
            # host ABI or a capability profile that is not the bound one is a
            # statement about *which behaviour this is*, and §23 names it as an
            # outcome rather than as a breakdown. Both are recorded as a contract
            # departure, and a departure may not be published: only a
            # continuation, whose predecessor resolved in this same store, is
            # exempt, and it is exempt because the contract describes a whole
            # behaviour while a continuation executes a tail.
            #
            # Anything else — a fault, an exhausted budget, a forbidden call —
            # means the reference execution did not complete, and there is
            # nothing for an expected outcome to be.
            incomplete = True
            contract_matched = False
            contract_failure_reason = run.failure_reason
            terminal_digests.append(run.terminal_snapshot_digest)
            break
        if not run.transcript_matched:
            contract_matched = False
            contract_failure_reason = (
                run.failure_reason or ReplayFailureReason.TRANSITION_MISMATCH
            )
        transitions.extend(run.transition_hash_chain)
        activities.extend(run.consumed_activity_identities)
        terminal_digests.append(run.terminal_snapshot_digest)

    # A run that stopped early still has to describe every behaviour: the record
    # states where each machine ended, and a machine that never ran ended where
    # it started. Reporting fewer terminal states than behaviours would make the
    # capture unreadable rather than merely unsuccessful.
    while len(terminal_digests) < len(bindings):
        terminal_digests.append(machines[len(terminal_digests)].snapshot_digest())

    payload = object.__new__(ReferenceReplayCapture)
    object.__setattr__(payload, "schema_version", SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1)
    object.__setattr__(payload, "knowledge_snapshot_id", prepared.snapshot_manifest_ref.ref_id)
    object.__setattr__(payload, "snapshot_manifest_ref", prepared.snapshot_manifest_ref)
    object.__setattr__(payload, "boundary_ref", prepared.admitted.boundary_ref)
    object.__setattr__(payload, "admitted_knowledge_id", prepared.admitted.knowledge_id)
    object.__setattr__(
        payload, "behavior_content_keys", tuple(item.behavior_content_key for item in bindings)
    )
    object.__setattr__(payload, "program_hashes", tuple(item.program_hash for item in bindings))
    object.__setattr__(
        payload, "host_abi_versions", tuple(item.host_abi_version for item in bindings)
    )
    object.__setattr__(payload, "initial_snapshot_refs", snapshot_refs)
    object.__setattr__(payload, "initial_snapshot_digests", initial_digests)
    object.__setattr__(payload, "recorded_activity_refs", prepared.ledger.activity_refs())
    object.__setattr__(payload, "activity_identities", prepared.ledger.activity_identities())
    object.__setattr__(
        payload, "observed_structural_history_refs", structural_history_refs
    )
    object.__setattr__(payload, "capability_profile_digest", capability_profile_digest())
    object.__setattr__(payload, "gas_budget", int(gas_budget))
    object.__setattr__(payload, "cognitive_budget", int(cognitive_budget))
    object.__setattr__(payload, "step_limit", int(step_limit))
    object.__setattr__(
        payload,
        "observed_transcript_root",
        transcript_root(transitions=tuple(transitions), activities=tuple(activities)),
    )
    object.__setattr__(payload, "observed_terminal_snapshot_digests", tuple(terminal_digests))
    object.__setattr__(payload, "contract_matched", contract_matched)
    object.__setattr__(payload, "contract_failure_reason", contract_failure_reason)
    object.__setattr__(payload, "capture_resumed_from_result_ref", resumed_from_result_ref)
    object.__setattr__(payload, "activity_policy_decision_refs", decision_refs)
    object.__setattr__(payload, "replay_executor_actor", binding.executor_actor)
    object.__setattr__(payload, "_trusted_seal", _CAPTURE_SEAL)
    envelope, envelope_binding = _envelope_for(
        schema_version=SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1,
        identity_domain=IdentityDomain.REFERENCE_REPLAY_CAPTURE,
        payload=_capture_payload(payload),
        admitted=prepared.admitted,
        created_at_utc=prepared.admitted.verified_at_utc,
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", envelope_binding)
    object.__setattr__(payload, "capture_id", envelope.record_id)
    validate_reference_capture(payload)
    return payload, incomplete


def require_publishable_capture(
    capture: ReferenceReplayCapture,
    *,
    binding: ProductionReplayBinding,
    continuation: BehaviorReplayResult | None,
) -> None:
    """Whether this capture may become the manifest a run is measured by.

    Three rules, and all three are the owner's because all three are statements
    about what a replay record means.

    A capture that departed from its behaviour's ``ReplayContract`` is kept — it
    is a true record of what running that program produced — and may not be
    published. Publishing one would make an execution that already departs from
    the contract into the expected outcome, and every later replay reproducing
    that departure would be reported as identical to it.

    A continuation is exempt from that comparison, and the exemption rests on a
    *resolved* predecessor rather than on the presence of a field. "Any non-None
    ``resumed_from`` disables the check" would have let a capture naming an
    unresolvable predecessor publish a non-conformant run: the field was the
    caller's, the exemption was automatic, and nothing looked at what it pointed
    to. The exemption is earned by a record that exists — hence ``continuation``,
    which the caller must have resolved in this exact store.

    The exemption is also narrow. A continuation starts from a terminal state
    and executes the tail of a behaviour, while the contract describes the
    behaviour whole — so the comparison is not a weaker test of the same thing,
    it is a test of something the record cannot be about.

    And the capture must belong to this execution domain: taken under this
    binding's replay executor, and under the capability profile this build
    classifies opcodes with.
    """

    validate_reference_capture(capture)
    binding = validate_production_replay_binding(binding)
    if not capture.contract_matched and continuation is None:
        reason = capture.contract_failure_reason
        raise _fail(
            ReplayFailureCode.CAPTURE_NOT_CONFORMANT,
            "a capture that departed from its replay contract cannot become a manifest"
            + ("" if reason is None else f": {reason.value}"),
        )
    if capture.replay_executor_actor != binding.executor_actor:
        raise _fail(
            ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
            "the capture was taken under another replay executor",
        )
    if capture.capability_profile_digest != capability_profile_digest():
        raise _fail(
            ReplayFailureCode.CAPABILITY_PROFILE_MISMATCH,
            "the capture was taken under another capability profile",
        )


def _issue_manifest_from_capture(
    *,
    authority: ProductionAuthorityBinding,
    capture: ReferenceReplayCapture,
    capture_ref: HashBoundRef,
) -> ReplayExecutionManifest:
    """Issue the exact manifest projection of a durable capture."""

    validate_production_authority_binding(authority)
    validate_reference_capture(capture)
    context = record_context_of_capture(capture)
    if _ref_key(reference_capture_ref(capture)) != _ref_key(capture_ref):
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the capture reference does not name the capture it was resolved from",
        )
    payload = object.__new__(ReplayExecutionManifest)
    object.__setattr__(payload, "schema_version", SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1)
    object.__setattr__(payload, "source_capture_ref", capture_ref)
    object.__setattr__(payload, "behavior_content_keys", capture.behavior_content_keys)
    object.__setattr__(payload, "program_hashes", capture.program_hashes)
    object.__setattr__(payload, "host_abi_versions", capture.host_abi_versions)
    object.__setattr__(payload, "initial_snapshot_refs", capture.initial_snapshot_refs)
    object.__setattr__(payload, "initial_snapshot_digests", capture.initial_snapshot_digests)
    object.__setattr__(
        payload,
        "expected_structural_history_refs",
        capture.observed_structural_history_refs,
    )
    object.__setattr__(payload, "expected_transcript_root", capture.observed_transcript_root)
    object.__setattr__(
        payload,
        "expected_terminal_snapshot_digests",
        capture.observed_terminal_snapshot_digests,
    )
    object.__setattr__(payload, "_trusted_seal", _MANIFEST_SEAL)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.REPLAY_EXECUTION_MANIFEST,
        canonical_payload_bytes=_canonical(_manifest_payload(payload)),
        run_id=context.run_id,
        attempt_id=context.attempt_id,
        created_at_utc=context.created_at_utc,
        producer_component=REPLAY_PRODUCER_COMPONENT_V1,
        repository_revision=context.repository_revision,
        policy_version=context.policy_version,
        environment_profile_id=context.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(payload, "manifest_id", envelope.record_id)
    return validate_replay_manifest(payload)


def require_manifest_projects_capture(
    manifest: ReplayExecutionManifest,
    *,
    capture: ReferenceReplayCapture,
) -> None:
    """Require every expected value to be the capture's exact observation."""

    validate_replay_manifest(manifest)
    validate_reference_capture(capture)
    mismatched = [
        name
        for name, left, right in (
            ("behavior_content_keys", manifest.behavior_content_keys, capture.behavior_content_keys),
            ("program_hashes", manifest.program_hashes, capture.program_hashes),
            ("host_abi_versions", manifest.host_abi_versions, capture.host_abi_versions),
            (
                "initial_snapshot_refs",
                tuple(item.to_dict() for item in manifest.initial_snapshot_refs),
                tuple(item.to_dict() for item in capture.initial_snapshot_refs),
            ),
            (
                "initial_snapshot_digests",
                manifest.initial_snapshot_digests,
                capture.initial_snapshot_digests,
            ),
            (
                "expected_structural_history_refs",
                tuple(item.to_dict() for item in manifest.expected_structural_history_refs),
                tuple(item.to_dict() for item in capture.observed_structural_history_refs),
            ),
            (
                "expected_transcript_root",
                manifest.expected_transcript_root,
                capture.observed_transcript_root,
            ),
            (
                "expected_terminal_snapshot_digests",
                manifest.expected_terminal_snapshot_digests,
                capture.observed_terminal_snapshot_digests,
            ),
        )
        if left != right
    ]
    if mismatched:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            f"the manifest does not project its capture: {', '.join(mismatched[:3])}",
        )


def replay_snapshot_ref(snapshot: bytes) -> HashBoundRef:
    """The content address a durable machine snapshot is named by.

    Minted here rather than in the store, for the same reason the activity
    result reference is minted by ``activities`` rather than by its store: what a
    snapshot *is* belongs to the module that reads and writes machine state, and
    where the bytes live belongs to the adapter. A store that named its own blobs
    could name them anything.
    """

    if type(snapshot) is not bytes:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a machine snapshot must be exact bytes")
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=hashlib.sha256(snapshot).hexdigest(),
        schema_id=SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value,
        sha256=hashlib.sha256(snapshot).hexdigest(),
        byte_length=len(snapshot),
        media_type=REPLAY_VM_SNAPSHOT_MEDIA_TYPE,
    )


def replay_manifest_ref(value: ReplayExecutionManifest) -> HashBoundRef:
    validate_replay_manifest(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.manifest_id.digest_sha256,
        schema_id=SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def replay_manifest_from_dict(value: object) -> ReplayExecutionManifest:
    """Rebuild a manifest from its exact canonical dictionary."""

    if type(value) is not dict or set(value) != {
        "envelope", "envelope_binding_sha256", "payload"
    }:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a manifest record has an unexpected shape")
    stored_envelope = value["envelope"]
    stored_binding = value["envelope_binding_sha256"]
    body = value["payload"]
    if type(body) is not dict or set(body) != _MANIFEST_PAYLOAD_FIELDS_V1_E1:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a manifest payload must have the exact E1 shape")
    if body["schema_version"] != SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1.value:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "replay manifest schema is unknown")
    payload = object.__new__(ReplayExecutionManifest)
    object.__setattr__(payload, "schema_version", SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1)
    object.__setattr__(
        payload, "source_capture_ref", HashBoundRef.from_dict(body["source_capture_ref"])
    )
    object.__setattr__(payload, "behavior_content_keys", tuple(body["behavior_content_keys"]))
    object.__setattr__(payload, "program_hashes", tuple(body["program_hashes"]))
    object.__setattr__(payload, "host_abi_versions", tuple(body["host_abi_versions"]))
    object.__setattr__(
        payload,
        "initial_snapshot_refs",
        tuple(HashBoundRef.from_dict(item) for item in body["initial_snapshot_refs"]),
    )
    object.__setattr__(
        payload, "initial_snapshot_digests", tuple(body["initial_snapshot_digests"])
    )
    object.__setattr__(
        payload,
        "expected_structural_history_refs",
        tuple(
            HashBoundRef.from_dict(item)
            for item in body["expected_structural_history_refs"]
        ),
    )
    object.__setattr__(payload, "expected_transcript_root", body["expected_transcript_root"])
    object.__setattr__(
        payload,
        "expected_terminal_snapshot_digests",
        tuple(body["expected_terminal_snapshot_digests"]),
    )
    object.__setattr__(payload, "_trusted_seal", _MANIFEST_SEAL)
    try:
        envelope = common_envelope_from_dict(
            stored_envelope, canonical_payload_bytes=_canonical(_manifest_payload(payload))
        )
    except ContractViolation as exc:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the stored envelope does not bind the manifest it was stored with",
        ) from exc
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", stored_binding)
    object.__setattr__(payload, "manifest_id", envelope.record_id)
    return validate_replay_manifest(payload)


def reference_capture_from_dict(value: object) -> ReferenceReplayCapture:
    """Rebuild a reference capture from its exact canonical dictionary."""

    if type(value) is not dict or set(value) != {
        "envelope", "envelope_binding_sha256", "payload"
    }:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a capture record has an unexpected shape")
    stored_envelope = value["envelope"]
    stored_binding = value["envelope_binding_sha256"]
    body = value["payload"]
    if type(body) is not dict or set(body) != _CAPTURE_PAYLOAD_FIELDS_V1_E1:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a capture payload must have the exact E1 shape")
    if body["schema_version"] != SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1.value:
        raise _fail(ReplayFailureCode.UNKNOWN_SCHEMA_VERSION, "reference capture schema is unknown")
    payload = object.__new__(ReferenceReplayCapture)
    object.__setattr__(payload, "schema_version", SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1)
    object.__setattr__(payload, "knowledge_snapshot_id", body["knowledge_snapshot_id"])
    for name in ("snapshot_manifest_ref", "boundary_ref"):
        object.__setattr__(payload, name, HashBoundRef.from_dict(body[name]))
    object.__setattr__(
        payload, "admitted_knowledge_id", record_id_reference_from_dict(body["admitted_knowledge_id"])
    )
    for name in (
        "behavior_content_keys", "program_hashes", "host_abi_versions",
        "initial_snapshot_digests", "activity_identities",
        "observed_terminal_snapshot_digests",
    ):
        object.__setattr__(payload, name, tuple(body[name]))
    for name in ("initial_snapshot_refs", "recorded_activity_refs"):
        object.__setattr__(
            payload, name, tuple(HashBoundRef.from_dict(item) for item in body[name])
        )
    object.__setattr__(
        payload,
        "observed_structural_history_refs",
        tuple(
            HashBoundRef.from_dict(item)
            for item in body["observed_structural_history_refs"]
        ),
    )
    object.__setattr__(payload, "capability_profile_digest", body["capability_profile_digest"])
    for name in ("gas_budget", "cognitive_budget", "step_limit"):
        object.__setattr__(payload, name, body[name])
    object.__setattr__(payload, "observed_transcript_root", body["observed_transcript_root"])
    object.__setattr__(payload, "contract_matched", body["contract_matched"])
    object.__setattr__(
        payload,
        "contract_failure_reason",
        None if body["contract_failure_reason"] is None
        else ReplayFailureReason(body["contract_failure_reason"]),
    )
    object.__setattr__(
        payload,
        "capture_resumed_from_result_ref",
        None if body["capture_resumed_from_result_ref"] is None
        else HashBoundRef.from_dict(body["capture_resumed_from_result_ref"]),
    )
    object.__setattr__(
        payload,
        "activity_policy_decision_refs",
        tuple(HashBoundRef.from_dict(item) for item in body["activity_policy_decision_refs"]),
    )
    object.__setattr__(
        payload, "replay_executor_actor", ActorIdentity.from_dict(body["replay_executor_actor"])
    )
    object.__setattr__(payload, "_trusted_seal", _CAPTURE_SEAL)
    try:
        envelope = common_envelope_from_dict(
            stored_envelope, canonical_payload_bytes=_canonical(_capture_payload(payload))
        )
    except ContractViolation as exc:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the stored envelope does not bind the capture it was stored with",
        ) from exc
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", stored_binding)
    object.__setattr__(payload, "capture_id", envelope.record_id)
    return validate_reference_capture(payload)


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
    #: Two different proofs about the state this behaviour ended in, and both are
    #: required. The digest says *which VM state* it was, under this adapter's
    #: profile; the reference says *where the exact canonical bytes live*. A
    #: digest alone cannot be restored from — it is not the blob's content
    #: address — so a continuation had nothing durable to attach to and had to be
    #: handed its starting machine by a caller, which is the moment the state
    #: stopped being evidence. Neither field is optional and neither substitutes
    #: for the other.
    terminal_snapshot_digest: str
    terminal_snapshot_ref: HashBoundRef
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
        "terminal_snapshot_ref": value.terminal_snapshot_ref.to_dict(),
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
    _ref(value.terminal_snapshot_ref, "terminal_snapshot_ref")
    if value.terminal_snapshot_ref.schema_id != SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value:
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the terminal snapshot reference does not name a machine snapshot",
        )
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
    object.__setattr__(
        payload, "terminal_snapshot_ref", HashBoundRef.from_dict(data["terminal_snapshot_ref"])
    )
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
    require_current_admitted_knowledge(
        request.admitted,
        snapshot_manifest_ref=request.snapshot_manifest_ref,
        authority=authority,
    )


def require_settled_execution_world(
    admitted: object,
    *,
    snapshot_manifest_ref: HashBoundRef,
    authority: ProductionAuthorityBinding,
) -> None:
    """The last check before an exact execution, made *after* its own writes.

    A reference capture writes durable snapshots and durable policy decisions
    before it takes a single transition, and each of those writes opens a
    mutation interval and moves the head the admission was taken against. So the
    full point-of-use re-check cannot be made here: it would refuse every
    capture, and it would refuse them for the capture's own writes rather than
    for anything that went wrong.

    What this checks instead is what those writes must *not* have changed. The
    coordinator has to be settled — an odd epoch means an interval is still
    open and no execution may begin over a half-written store. The committed
    boundary has to be the one the admission crossed. And that boundary has to
    still publish the exact snapshot manifest this attempt names, so the
    knowledge about to be executed against has not been re-published underneath
    it. A capture whose own writes are the only thing that moved passes; a
    capture whose world moved does not.
    """

    validate_production_authority_binding(authority)
    epoch = authority.fence.current_epoch()
    if type(epoch) is not int or epoch < 0 or epoch % 2:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the coordinator is mid-interval and no exact execution may begin",
        )
    from .knowledge import atomic_boundary_ref

    current = authority.open_current_snapshot().boundary
    if _ref_key(current.manifest_ref) != _ref_key(snapshot_manifest_ref):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the current boundary no longer publishes the snapshot this replay names",
        )
    if _ref_key(admitted.boundary_ref) != _ref_key(atomic_boundary_ref(current)):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the committed boundary changed between admission and execution",
        )


def require_current_admitted_knowledge(
    admitted: object,
    *,
    snapshot_manifest_ref: HashBoundRef,
    authority: ProductionAuthorityBinding,
) -> None:
    """The §22 point-of-use re-check, without a request to read it from.

    Extracted because the governed run is not the only phase that needs it, and
    because naming it separately from ``require_settled_execution_world`` keeps
    the two apart: this one asks whether the admission still holds, which is
    answerable only before the attempt writes anything of its own.
    """

    validate_production_authority_binding(authority)
    try:
        require_current_point_of_use_evidence(admitted, binding=authority)
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
    if _ref_key(current.manifest_ref) != _ref_key(snapshot_manifest_ref):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the current boundary no longer publishes the snapshot this replay names",
        )
    return None


def _incompatible_profile_result(
    request: BehaviorReplayRequest,
) -> BehaviorReplayResult | None:
    """The sealed refusal for a request pinned to another capability profile.

    A rule about a *record*, named separately from the body that applies it. The
    request carries the profile digest it was created under; if the profile has
    moved since, the record is evidence about a vocabulary this build no longer
    has, and no machine should be consulted about it. Returning the sealed result
    rather than raising is deliberate — an incompatible replay is an outcome
    §23 records, not an executor defect.
    """

    if request.capability_profile_digest == capability_profile_digest():
        return None
    return _seal_result(
        request=request,
        status=ReplayStatus.REPLAY_INCOMPATIBLE,
        failure_reason=ReplayFailureReason.CAPABILITY_PROFILE_MISMATCH,
        observations=(),
    )


def _require_resume_lineage(
    request: BehaviorReplayRequest,
    *,
    resumed_from: BehaviorReplayResult,
) -> None:
    """Refuse a continuation that is not this request's continuation. §23.

    Three ways a pair can fail to be a lineage, all of them properties of the two
    records rather than of the run: the request does not declare a predecessor at
    all, it declares a different one, or it declares one from another knowledge
    snapshot. None of them can be discovered by executing — a continuation
    attached to the wrong predecessor would produce a perfectly consistent
    transcript of the wrong thing — so all three are settled before the body
    looks at a machine.
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


def _execute_replay_body(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    activity_store: object,
    permit: ReplayExecutionReceipt,
    binding: ProductionReplayBinding,
    store_snapshot: object,
    attempt_boundary: DurableReplayAttemptBoundary,
) -> BehaviorReplayResult:
    """Spend this attempt's durable permission, then enter its body once."""

    _spend_execution_permit(permit, request=request, binding=binding)
    attempt_boundary.reconcile_execution_claim()
    attempt_boundary.entering(
        ReplayAttemptPhase.EXECUTION,
        ReplayAttemptFailureDomain.MACHINE_ADAPTER,
    )
    return _execute_replay_transitions(
        request,
        machines=machines,
        activity_store=activity_store,
        permit=permit,
        binding=binding,
        store_snapshot=store_snapshot,
    )


def _execute_replay_transitions(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    activity_store: object,
    permit: ReplayExecutionReceipt,
    binding: ProductionReplayBinding,
    store_snapshot: object,
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

    _enter_execution_permit(permit, request=request, binding=binding)
    if type(machines) is not tuple:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "machines must be an exact tuple")
    if len(machines) != len(request.bindings):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "one machine is required for each admitted behavior",
        )
    for machine in machines:
        require_machine_port(machine)

    refusal = _incompatible_profile_result(request)
    if refusal is not None:
        return refusal

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
                request,
                binding=binding,
                machine=machine,
                channel=channel,
                store_snapshot=store_snapshot,
            )
            observations.append(observation)
            if failure_reason is not None:
                break
    finally:
        channel.close()

    sealed = tuple(observations)
    status, verdict_reason = replay_verdict(
        observations=sealed,
        stopping_reason=failure_reason,
        expected_transcript_root=request.expected_transcript_root,
        expected_terminal_snapshot_digests=request.expected_terminal_snapshot_digests,
    )
    return _seal_result(
        request=request,
        status=status,
        failure_reason=verdict_reason,
        observations=sealed,
    )


def replay_verdict(
    *,
    observations: tuple[ReplayObservation, ...],
    stopping_reason: ReplayFailureReason | None,
    expected_transcript_root: str | None,
    expected_terminal_snapshot_digests: tuple[str, ...] | None,
) -> tuple[ReplayStatus, ReplayFailureReason | None]:
    """Decide the §23 status from what was observed and what was expected.

    A pure rule, separated from the orchestration that feeds it. Everything it
    reads is a fact — observations produced by the driver, expectations resolved
    out of a durable manifest — and everything it returns is a verdict. Nothing
    here opens a store, takes an admission or writes a record, which is what
    makes it answerable on its own rather than only as a side effect of a run.

    The order of the tests is the order of the claims, from strongest evidence
    of failure to weakest evidence of success: a run that stopped for a reason
    is that reason; a run whose transitions departed from the transcript failed
    whatever else it reached; a terminal state that is not the expected one is a
    tamper even when every transition matched; and identity is granted last and
    only to a run whose root was pinned in advance and equals what it folded.
    """

    if stopping_reason is not None:
        return status_for_reason(stopping_reason), stopping_reason

    # Every behavior ran to its natural end. Identity still has to be earned.
    if any(not item.transcript_matched for item in observations):
        return ReplayStatus.REPLAY_FAILED, ReplayFailureReason.TRANSITION_MISMATCH
    if expected_terminal_snapshot_digests is not None:
        observed = tuple(item.terminal_snapshot_digest for item in observations)
        if observed != expected_terminal_snapshot_digests:
            return ReplayStatus.REPLAY_FAILED, ReplayFailureReason.SNAPSHOT_TAMPERED
    root = transcript_root(
        transitions=tuple(item for obs in observations for item in obs.transition_hash_chain),
        activities=tuple(
            item for obs in observations for item in obs.consumed_activity_identities
        ),
    )
    if expected_transcript_root is None or expected_transcript_root != root:
        # Matching the contract's sorted sets is not identity: a transcript that
        # visits the same transitions in another order satisfies them and is a
        # different execution. Without a root pinned in advance there is nothing
        # that could distinguish the two, so identity is not established.
        return ReplayStatus.REPLAY_FAILED, ReplayFailureReason.TRANSITION_MISMATCH
    return ReplayStatus.REPLAY_IDENTICAL, None


def _snapshot_bytes_of(machine: ReplayMachinePort) -> bytes:
    """The machine's terminal state as exact bytes, refusing anything else.

    A port answers this about itself, so the answer is checked before it becomes
    the blob a continuation will later restore from: bytes that were not bytes,
    or that do not digest to what the same machine reports, would make the
    reference and the digest two statements about two different things.
    """

    raw = machine.snapshot_bytes()
    if type(raw) is not bytes or not raw:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a machine's terminal snapshot must be exact non-empty bytes",
        )
    if len(raw) > MAX_SNAPSHOT_BYTES_V1_E1:
        raise _fail(
            ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "a machine's terminal snapshot exceeds the durable snapshot ceiling",
        )
    return raw


@dataclass(frozen=True)
class _TransitionRun:
    """One driver's unsealed facts, shared by capture and governed replay."""

    transition_hash_chain: tuple[str, ...]
    consumed_activity_identities: tuple[str, ...]
    consumed_lookup_keys: tuple[str, ...]
    initial_snapshot_digest: str
    terminal_snapshot_digest: str
    #: Exact terminal state, made durable before anything names it.
    terminal_snapshot_bytes: bytes
    steps_executed: int
    gas_consumed: int
    transcript_matched: bool
    first_unexpected_index: int | None
    failure_reason: ReplayFailureReason | None


def refused_transition_run(
    reason: ReplayFailureReason, *, machine: ReplayMachinePort
) -> _TransitionRun:
    """Record a pre-transition execution-contract refusal as durable evidence."""

    if type(reason) is not ReplayFailureReason:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "a refusal names an exact reason")
    digest = machine.snapshot_digest()
    return _TransitionRun(
        transition_hash_chain=(),
        consumed_activity_identities=(),
        consumed_lookup_keys=(),
        initial_snapshot_digest=digest,
        terminal_snapshot_digest=digest,
        terminal_snapshot_bytes=_snapshot_bytes_of(machine),
        steps_executed=0,
        gas_consumed=0,
        transcript_matched=False,
        first_unexpected_index=None,
        failure_reason=reason,
    )


def _drive_one_behavior(
    *,
    binding: ReplayProgramBinding,
    machine: ReplayMachinePort,
    channel: RecordedActivityChannel,
    gas_budget: int,
    step_limit: int,
) -> _TransitionRun:
    """Drive capture and replay through their single shared transition loop."""

    initial_digest = _sha256(machine.snapshot_digest(), "initial_snapshot_digest")
    consumed_before = len(channel.consumed_identities())
    transitions: list[str] = []
    gas_start = gas_previous = 0
    steps = 0
    reason: ReplayFailureReason | None = None
    port_contract_broken = False

    try:
        reported_gas = machine.gas_remaining()
        if type(reported_gas) is not int:
            port_contract_broken = True
            raise _fail(
                ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
                "machine returned a non-integer gas remainder",
            )
        gas_start = gas_previous = reported_gas
        while True:
            halted = machine.is_halted()
            if type(halted) is not bool:
                port_contract_broken = True
                raise _fail(
                    ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
                    "machine returned a non-boolean halted state",
                )
            if halted:
                break
            if steps >= step_limit:
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
            # Refuse before the transition if either admitted budget or the
            # machine's continuation pool cannot pay its exact next cost.
            remaining_pool = machine.gas_remaining()
            if type(remaining_pool) is not int or remaining_pool > gas_previous:
                port_contract_broken = True
                raise _fail(
                    ReplayFailureCode.GAS_NOT_MONOTONE,
                    "machine gas is invalid or increased before a replay transition",
                )
            spent = gas_start - remaining_pool
            cost = machine.next_step_gas_cost()
            if type(cost) is not int or isinstance(cost, bool) or cost < 0:
                port_contract_broken = True
                raise _fail(
                    ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
                    "machine returned an invalid next-step gas cost",
                )
            if cost > 2**53:
                port_contract_broken = True
                raise _fail(
                    ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
                    "machine gas cost is too large",
                )
            if spent + cost > gas_budget or (
                type(remaining_pool) is int and remaining_pool < cost
            ):
                reason = ReplayFailureReason.GAS_EXHAUSTED
                break
            machine.step()
            observed = machine.transition_hash()
            if type(observed) is not str or not observed:
                reason = ReplayFailureReason.MACHINE_FAULT
                break
            transitions.append(observed)
            steps += 1
            remaining = machine.gas_remaining()
            if type(remaining) is not int or remaining > gas_previous:
                port_contract_broken = True
                raise _fail(
                    ReplayFailureCode.GAS_NOT_MONOTONE,
                    "gas increased during a replay, so this is not the modelled cost function",
                )
            gas_previous = remaining
        structural_complete = machine.structural_history_complete()
        if type(structural_complete) is not bool:
            port_contract_broken = True
            raise _fail(
                ReplayFailureCode.MACHINE_PORT_INCOMPLETE,
                "machine returned a non-boolean structural-history state",
            )
        if reason is None and not structural_complete:
            reason = ReplayFailureReason.TRANSITION_MISMATCH
    except ActivityViolation as exc:
        if exc.failure_code is ActivityFailureCode.BACKEND_UNAVAILABLE:
            raise
        reason = reason or reason_for_activity_failure(exc)
    except ReplayViolation as exc:
        if port_contract_broken:
            raise
        reason = reason or _reason_for_machine_failure(exc)
    chain = tuple(transitions)
    consumed = channel.consumed_identities()[consumed_before:]
    keys = channel.consumed_lookup_keys()[consumed_before:]
    matched = reason is None and _transcript_matches(
        binding.replay_contract, transitions=chain, activities=consumed
    )
    if reason is None and not matched:
        reason = ReplayFailureReason.TRANSITION_MISMATCH

    return _TransitionRun(
        transition_hash_chain=chain,
        consumed_activity_identities=consumed,
        consumed_lookup_keys=keys,
        initial_snapshot_digest=initial_digest,
        terminal_snapshot_digest=_sha256(machine.snapshot_digest(), "terminal_snapshot_digest"),
        terminal_snapshot_bytes=_snapshot_bytes_of(machine),
        steps_executed=steps,
        gas_consumed=max(0, gas_start - gas_previous),
        transcript_matched=matched,
        first_unexpected_index=_first_unexpected_index(binding.replay_contract, chain),
        failure_reason=reason,
    )


def _replay_one_behavior(
    request: BehaviorReplayRequest,
    *,
    binding: ReplayProgramBinding,
    machine: ReplayMachinePort,
    channel: RecordedActivityChannel,
    store_snapshot: object,
) -> tuple[ReplayObservation, ReplayFailureReason | None]:
    """Seal one behaviour's drive as an observation bound to this admission.

    The terminal state is made durable *before* the observation that names it is
    sealed, and the reference comes back from that write rather than being
    computed alongside it. The order is the point: an observation carrying a
    reference to bytes nobody stored would be a record promising a state that
    cannot be produced, and a continuation attaching to it would fail at restore
    time with the attempt already recorded as complete.
    """

    run = _drive_one_behavior(
        binding=binding,
        machine=machine,
        channel=channel,
        gas_budget=request.gas_budget,
        step_limit=request.step_limit,
    )
    terminal_ref = store_snapshot(run.terminal_snapshot_bytes)
    observation = _seal_observation(
        admitted=request.admitted,
        behavior_content_key=binding.behavior_content_key,
        program_hash=binding.program_hash,
        host_abi_version=binding.host_abi_version,
        transition_hash_chain=run.transition_hash_chain,
        consumed_activity_identities=run.consumed_activity_identities,
        consumed_lookup_keys=run.consumed_lookup_keys,
        initial_snapshot_digest=run.initial_snapshot_digest,
        terminal_snapshot_digest=run.terminal_snapshot_digest,
        terminal_snapshot_ref=terminal_ref,
        steps_executed=run.steps_executed,
        gas_consumed=run.gas_consumed,
        transcript_matched=run.transcript_matched,
        first_unexpected_index=run.first_unexpected_index,
        failure_reason=run.failure_reason,
    )
    return observation, run.failure_reason


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def _resume_replay_body(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    resumed_from: BehaviorReplayResult,
    activity_store: object,
    permit: ReplayExecutionReceipt,
    binding: ProductionReplayBinding,
    store_snapshot: object,
    initial_snapshot_refs: tuple[HashBoundRef, ...],
    attempt_boundary: DurableReplayAttemptBoundary,
) -> BehaviorReplayResult:
    """Spend once, validate continuation lineage, then enter shared transitions."""

    _spend_execution_permit(permit, request=request, binding=binding)
    attempt_boundary.reconcile_execution_claim()
    attempt_boundary.entering(
        ReplayAttemptPhase.EXECUTION,
        ReplayAttemptFailureDomain.MACHINE_ADAPTER,
    )
    try:
        return _resume_replay_after_spend(
            request,
            machines=machines,
            resumed_from=resumed_from,
            activity_store=activity_store,
            permit=permit,
            binding=binding,
            store_snapshot=store_snapshot,
            initial_snapshot_refs=initial_snapshot_refs,
        )
    finally:
        if not permit._entered:
            permit._entered = True


def _resume_replay_after_spend(
    request: BehaviorReplayRequest,
    *,
    machines: tuple[ReplayMachinePort, ...],
    resumed_from: BehaviorReplayResult,
    activity_store: object,
    permit: ReplayExecutionReceipt,
    binding: ProductionReplayBinding,
    store_snapshot: object,
    initial_snapshot_refs: tuple[HashBoundRef, ...],
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

    _require_resume_lineage(request, resumed_from=resumed_from)
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
    # Two proofs about the starting state, checked together and answered the way
    # their neighbours are: with a typed refusal, not an exception. A continuation
    # that attaches to the wrong state is an attempt, and NR-13 keeps attempts.
    #
    # The reference half matters on its own. A digest that agreed would still
    # leave the starting bytes to be chosen by whoever assembled the manifest,
    # and "equal by content" is exactly what a caller can manufacture; requiring
    # the predecessor's own ``terminal_snapshot_ref`` means the continuation
    # attaches to the record rather than to a lookalike. It is also order
    # sensitive, which is the point: the same programs in the wrong places are
    # not the state that result left.
    declared = tuple(item.to_dict() for item in initial_snapshot_refs)
    recorded = tuple(
        item.terminal_snapshot_ref.to_dict() for item in resumed_from.observations
    )
    if declared != recorded:
        return _refused(
            ReplayStatus.REPLAY_INCOMPATIBLE, ReplayFailureReason.SNAPSHOT_INCOMPATIBLE
        )
    observed = tuple(machine.snapshot_digest() for machine in machines)
    if observed != resumed_from.terminal_snapshot_digests:
        return _refused(
            ReplayStatus.REPLAY_INCOMPATIBLE, ReplayFailureReason.SNAPSHOT_INCOMPATIBLE
        )
    return _execute_replay_transitions(
        request,
        machines=machines,
        activity_store=activity_store,
        permit=permit,
        binding=binding,
        store_snapshot=store_snapshot,
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
        production = (
            binding.activity_policy_store.require_production_provenance_for_activity(
                activity.production_provenance_ref,
                evaluator=binding.activity_policy_evaluator,
                activity=activity,
            )
        )
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
            production=production,
            consumption=binding.consumption_provenance,
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
            consumption=binding.consumption_provenance,
            ticket=ticket,
        )
        if actual.to_dict() != expected.to_dict():
            raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "durable policy decision changed identity")
    actual_request = binding.replay_store.append_request(request, ticket=ticket)
    if actual_request.to_dict() != replay_request_ref(request).to_dict():
        raise _fail(ReplayFailureCode.IDENTITY_MISMATCH, "durable replay request changed identity")


def _require_continuation_of(
    manifest: ReplayExecutionManifest,
    *,
    resumed_from: BehaviorReplayResult,
) -> None:
    """A continuation starts from the exact state its predecessor ended in.

    Not a state that digests the same — the exact one, by reference. The digest
    is checked too, in the resume body, and the two are not redundant: a digest
    that agreed would still leave the starting bytes to be chosen by whoever
    built the manifest, and "equal by content" is precisely the property a caller
    can manufacture. Requiring the predecessor's own ``terminal_snapshot_ref``
    means the continuation attaches to the record rather than to a lookalike.

    Cheap and total: nothing is opened here. ``_machines_from_manifest`` then
    resolves those same references out of the store and makes each restored
    machine recompute its digest, so the state is proved three ways — the store
    re-derives the content address, the manifest's digest must match, and the
    predecessor's record must be what the manifest named.
    """

    recorded = tuple(item.terminal_snapshot_ref.to_dict() for item in resumed_from.observations)
    declared = tuple(item.to_dict() for item in manifest.initial_snapshot_refs)
    if declared != recorded:
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "a continuation must start from the exact terminal state its predecessor recorded",
        )


def _require_manifest_describes(
    manifest: ReplayExecutionManifest,
    *,
    prepared: _PreparedReplay,
) -> None:
    """The manifest must be about the behaviours that were actually admitted.

    A manifest resolved from a store is only authority-resolved evidence if it
    describes *this* run. One naming other behaviours, or the right ones in
    another order, would supply expected values for a different execution — which
    is the same defect as taking them from the caller, reached by a longer route.
    """

    keys = tuple(item.behavior_content_key for item in prepared.bindings)
    if manifest.behavior_content_keys != keys:
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the manifest describes another behavior set or another order",
        )
    if manifest.program_hashes != tuple(item.program_hash for item in prepared.bindings):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the manifest was written for other programs",
        )
    if manifest.host_abi_versions != tuple(
        item.host_abi_version for item in prepared.bindings
    ):
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the manifest was written for another host ABI",
        )
    # And about this *attempt*, not merely these behaviours. A manifest names the
    # execution identity it was issued for; the admission names the one the run
    # is actually crossing under. A reproduction accepted a manifest carrying a
    # foreign run, attempt, environment and policy, because nothing compared the
    # two — the programs matched, and the programs were all that was asked.
    admitted = prepared.admitted
    envelope = manifest.envelope
    if (
        envelope.run_id != admitted.envelope.run_id
        or envelope.attempt_id != admitted.envelope.attempt_id
        or envelope.repository_revision != admitted.envelope.repository_revision
        or envelope.environment_profile_id != admitted.envelope.environment_profile_id
        or envelope.policy_version != admitted.policy_version
    ):
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the manifest was issued for another execution identity",
        )


def _machines_from_manifest(
    manifest: ReplayExecutionManifest,
    *,
    binding: ProductionReplayBinding,
    gas_budget: int,
    attempt_boundary: DurableReplayAttemptBoundary,
) -> tuple[ReplayMachinePort, ...]:
    """Restore exact machine state and its per-behaviour structural history."""

    context = replay_machine_execution_context(
        run_id=manifest.envelope.run_id,
        attempt_id=manifest.envelope.attempt_id,
        repository_revision=manifest.envelope.repository_revision,
        environment_profile_id=manifest.envelope.environment_profile_id,
        policy_version=manifest.envelope.policy_version,
    )
    factory = require_machine_factory_port(binding.machine_factory)
    machines: list[ReplayMachinePort] = []
    for reference, expected, structural_ref in zip(
        manifest.initial_snapshot_refs,
        manifest.initial_snapshot_digests,
        manifest.expected_structural_history_refs,
    ):
        attempt_boundary.entering(
            ReplayAttemptPhase.SNAPSHOT_RESTORE,
            ReplayAttemptFailureDomain.REPLAY_STORE,
        )
        raw = binding.replay_store.open_snapshot(reference)
        structural = binding.replay_store.open_structural_history(structural_ref)
        attempt_boundary.entering(
            ReplayAttemptPhase.MACHINE_CONSTRUCTION,
            ReplayAttemptFailureDomain.MACHINE_ADAPTER,
        )
        machine = require_machine_port(
            factory.restore(
                raw,
                gas_budget=gas_budget,
                execution_context=context,
                expected_structural_history=structural,
            )
        )
        if machine.snapshot_digest() != expected:
            raise _fail(
                ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
                "a restored machine is not the state the manifest recorded",
            )
        machines.append(machine)
    return tuple(machines)


def _execute_prepared(
    prepared: _PreparedReplay,
    *,
    binding: ProductionReplayBinding,
    manifest: ReplayExecutionManifest,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    resumed_from: BehaviorReplayResult | None = None,
) -> BehaviorReplayResult:
    """Commit one request, execute only restored machines, then commit its result."""

    validate_replay_manifest(manifest)
    _require_manifest_describes(manifest, prepared=prepared)
    expected_transcript_root = manifest.expected_transcript_root
    expected_terminal_snapshot_digests = manifest.expected_terminal_snapshot_digests

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
        for structural_ref in manifest.expected_structural_history_refs:
            binding.replay_store.open_structural_history(structural_ref)
        request = _create_replay_request(
            prepared=prepared,
            decision_refs=decision_refs,
            gas_budget=gas_budget,
            cognitive_budget=cognitive_budget,
            step_limit=step_limit,
            executor_actor=binding.executor_actor,
            # Derived from the manifest this executor resolved out of the store,
            # not from whatever reference a caller passed alongside it: the two
            # agree only because ``require_manifest`` already made them agree.
            execution_manifest_ref=replay_manifest_ref(manifest),
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
        request_ref = replay_request_ref(request)
        attempt_boundary = DurableReplayAttemptBoundary(
            store=binding.replay_store,
            fence=fence,
            coordinator_guard=coordinator_guard,
            settle=settle_exclusive_mutation,
            request_ref=request_ref,
            entry_epoch=entry_epoch,
        )

        # Everything after this point is protected by a durable lifecycle. A
        # failure remains its original typed failure; it is never rewritten as
        # a machine verdict merely because the request already exists.
        try:
            attempt_boundary.entering(
                ReplayAttemptPhase.SETTLEMENT,
                ReplayAttemptFailureDomain.COORDINATOR,
            )
            settle_exclusive_mutation(
                fence=fence,
                coordinator_id=fence.coordinator_id(),
                entry_epoch=entry_epoch,
                own_intervals=1,
            )
            _require_durable_request_ref(request, binding=binding)

            running = _machines_from_manifest(
                manifest,
                binding=binding,
                gas_budget=gas_budget,
                attempt_boundary=attempt_boundary,
            )
            attempt_boundary.entering(
                ReplayAttemptPhase.DURABLE_POLICY_REREAD,
                ReplayAttemptFailureDomain.POLICY_AUTHORITY,
            )
            _require_durable_policy_decisions(request, binding=binding)
            attempt_boundary.entering(
                ReplayAttemptPhase.RECEIPT_ISSUE,
                ReplayAttemptFailureDomain.POLICY_AUTHORITY,
            )
            receipt = _issue_execution_receipt(
                request, binding=binding, coordinator_guard=coordinator_guard
            )
            attempt_boundary.bind_execution_identity(receipt._execution_identity)
            attempt_boundary.entering(
                ReplayAttemptPhase.EXECUTION_CLAIM,
                ReplayAttemptFailureDomain.REPLAY_STORE,
            )
            if resumed_from is None:
                result = _execute_replay_body(
                    request,
                    machines=running,
                    activity_store=binding.activity_store,
                    permit=receipt,
                    binding=binding,
                    store_snapshot=attempt_boundary.store_terminal_snapshot,
                    attempt_boundary=attempt_boundary,
                )
            else:
                result = _resume_replay_body(
                    request,
                    machines=running,
                    resumed_from=resumed_from,
                    activity_store=binding.activity_store,
                    permit=receipt,
                    binding=binding,
                    store_snapshot=attempt_boundary.store_terminal_snapshot,
                    initial_snapshot_refs=manifest.initial_snapshot_refs,
                    attempt_boundary=attempt_boundary,
                )
            attempt_boundary.complete(result)
            return result
        except ActivityViolation as exc:
            if exc.failure_code is ActivityFailureCode.BACKEND_UNAVAILABLE:
                attempt_boundary.entering(
                    ReplayAttemptPhase.ACTIVITY_STORE_READ,
                    ReplayAttemptFailureDomain.ACTIVITY_STORE,
                )
            attempt_boundary.record_incomplete()
            raise
        except BaseException:
            attempt_boundary.record_incomplete()
            raise


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


def _run_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    activity_refs: tuple[HashBoundRef, ...],
    manifest_ref: HashBoundRef,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
) -> BehaviorReplayResult:
    """Resolve, admit, compile, re-admit, durably govern and execute."""

    binding = validate_production_replay_binding(binding)
    manifest = binding.replay_store.require_manifest(_ref(manifest_ref, "manifest_ref"))
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
        manifest=manifest,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
    )


def _resume_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    manifest_ref: HashBoundRef,
    resumed_from_result_ref: HashBoundRef,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
) -> BehaviorReplayResult:
    """Continue only from exact predecessor histories resolved after restart.

    The starting state is durable and named by this continuation's own manifest,
    and it is checked against the terminal state the predecessor recorded. Before
    this, the caller brought the machine — so a continuation could attach to any
    state whose digest happened to match, and after a restart the state had to
    come from somewhere outside the system entirely.
    """

    binding = validate_production_replay_binding(binding)
    manifest = binding.replay_store.require_manifest(_ref(manifest_ref, "manifest_ref"))
    resumed, activity_refs = _resume_history(binding, resumed_from_result_ref)
    if manifest.initial_snapshot_digests != resumed.terminal_snapshot_digests:
        raise _fail(
            ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH,
            "the continuation starts from a state its predecessor did not reach",
        )
    # And by reference, not only by digest: see ``_require_continuation_of``. The
    # check is made here as well as inside the executor because this is the door
    # a continuation comes through, and a refusal at the door costs nothing.
    _require_continuation_of(manifest, resumed_from=resumed)
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
        manifest=manifest,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
        resumed_from=resumed,
    )


__all__ = [
    "ACTIVITY_KIND_BY_OPCODE",
    "DISPATCH_GUARDED_OPCODES",
    "RECORDED_ONLY_OPCODES",
    "REPLAY_ADMISSIBLE_OPCODES",
    "REPLAY_CAPABILITY_PROFILE_V1_E1",
    "BehaviorReplayRequest",
    "BehaviorReplayResult",
    "REPLAY_MACHINE_ADAPTER_ID_V1_E1",
    "ReplayMachineExecutionContext",
    "ReplayMachineFactoryPort",
    "ReplayMachinePort",
    "ProductionReplayBinding",
    "RecordedActivityChannel",
    "ReplayFailureCode",
    "ReplayFailureReason",
    "ReplayObservation",
    "ReplayProgramBinding",
    "ReplayRecordContext",
    "record_context_of_capture",
    "ReferenceCaptureAuthority",
    "create_reference_capture_authority",
    "require_current_admitted_knowledge",
    "require_publishable_capture",
    "seal_reference_capture",
    "require_reference_capture_authority",
    "require_settled_execution_world",
    "ReplayStatus",
    "ReplaySubject",
    "ReplayViolation",
    "activity_kind_for_opcode",
    "capability_profile_digest",
    "classify_replay_opcode",
    "replay_machine_execution_context",
    "require_machine_factory_port",
    "require_replay_machine_execution_context",
    "reason_for_activity_failure",
    "replay_program_binding",
    "replay_request_ref",
    "replay_observation_from_dict",
    "replay_result_from_dict",
    "replay_result_ref",
    "replay_subject",
    "refused_transition_run",
    "replay_verdict",
    "status_for_reason",
    "transcript_root",
    "validate_replay_observation",
    "validate_production_replay_binding",
    "validate_replay_request",
    "validate_replay_result",
]
