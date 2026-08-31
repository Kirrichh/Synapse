"""The sole Stage 10-backed delivery path for a Gold-run attempt.

The run persists its accepted plan, crosses the fresh point-of-use gate, builds
and persists the exact worker context, obtains first-side-effect authority, and
delegates dispatch to the composition-bound Stage 10 adapters.  It never
constructs a raw invocation, invokes a transport, or verifies delivery itself.

Cohesion review: this file remains one owner although it is above 700 eLOC.
Preparation and completion share the same sealed upstream identity, private
constructors, Stage 10 store binding, and substitution checks.  Splitting them
would expose that private state across owners or require a forwarding/re-export
layer while neither half is a valid delivery path on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from synapse.experiments.gold.admission import (
    AdmissionFailureCode,
    AdmissionViolation,
    gate_decision_ref,
    validate_gate_decision,
)
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import AttemptId
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.point_of_use import (
    CurrentAdmittedKnowledge,
    admit_for_use_now,
    require_current_point_of_use_evidence,
    validate_current_admitted_knowledge,
)
from synapse.experiments.gold.replay import replay_result_ref
from synapse.experiments.gold.retrieval import retrieval_causal_record_ref
from synapse.experiments.gold.stage10.context import (
    ContextPersistenceEvidence,
    WorkerContextRecord,
    build_worker_context,
    validate_context_persistence_evidence,
    validate_worker_context,
)
from synapse.experiments.gold.stage10.delivery_verification import (
    DeliveryReceipt,
    validate_delivery_receipt,
)
from synapse.experiments.gold.stage10.plan_revalidation import (
    PlanPersistenceEvidence,
    SideEffectAuthorization,
    authorize_first_side_effect,
    validate_plan_persistence_evidence,
    validate_side_effect_authorization,
)
from synapse.experiments.gold.stage10.retrieval_adapter import (
    context_knowledge_selection,
)
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage10.worker_context_adapter import (
    Stage10WorkerContextAdapter,
    require_worker_dispatch_result,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateResult,
    WorkerInvocation,
)
from .attempt_inputs import PreparedAttemptInputs
from .models import AttemptPhaseRefs, GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


_PREPARED_SEAL = object()
_COMPLETED_SEAL = object()
_UPSTREAM_SEAL = object()
_REFUSAL_SEAL = object()
_UNAVAILABLE_SEAL = object()
_UNAVAILABLE_ADMISSION_FAILURES = frozenset(
    {
        AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
        AdmissionFailureCode.JOURNAL_UNAVAILABLE,
        AdmissionFailureCode.RESOURCE_LIMIT_EXCEEDED,
    }
)
ADAPTER_PRIVATE_EXPORTS = {
    "synapse.experiments.gold.runner.attempt_delivery_failure": frozenset(
        {"_make_attempt_delivery_failure", "_make_attempt_upstream_evidence_from_refs"}
    ),
    "synapse.experiments.gold.runner.completed_delivery_codec": frozenset(
        {"_make_completed_worker_delivery", "_make_attempt_upstream_evidence_from_refs"}
    ),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _same_ref(left: HashBoundRef, right: HashBoundRef) -> bool:
    return left.to_dict() == right.to_dict()


def _bounded_detail(value: object) -> str:
    if type(value) is not str or not value:
        return "delivery owner returned no diagnostic"
    return value[:256]


@dataclass(frozen=True, init=False)
class AttemptUpstreamEvidence:
    """Actual durable phase identities established before worker dispatch."""

    knowledge_snapshot_ref: HashBoundRef
    retrieval_ref: HashBoundRef
    retrieval_decision_ref: HashBoundRef
    replay_ref: HashBoundRef
    intent_ref: HashBoundRef
    plan_ref: HashBoundRef
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptUpstreamEvidence:
        raise TypeError("AttemptUpstreamEvidence is delivery-owner created")

    def phase_refs(
        self,
        *,
        worker_context_id: str | None = None,
        worker_context_audit_sha256: str | None = None,
    ) -> AttemptPhaseRefs:
        require_attempt_upstream_evidence(self)
        return AttemptPhaseRefs(
            knowledge_snapshot_ref=self.knowledge_snapshot_ref,
            retrieval_ref=self.retrieval_ref,
            replay_ref=self.replay_ref,
            intent_ref=self.intent_ref,
            plan_ref=self.plan_ref,
            worker_context_id=worker_context_id,
            worker_context_audit_sha256=worker_context_audit_sha256,
        )


def _make_upstream_evidence(
    *,
    inputs: PreparedAttemptInputs,
    plan_persistence: PlanPersistenceEvidence,
) -> AttemptUpstreamEvidence:
    result = object.__new__(AttemptUpstreamEvidence)
    fields = {
        "knowledge_snapshot_ref": inputs.accepted_plan.candidate.knowledge_snapshot_ref,
        "retrieval_ref": retrieval_causal_record_ref(
            inputs.retrieval_causal_record
        ),
        "retrieval_decision_ref": inputs.retrieval_causal_record.retrieval_decision_ref,
        "replay_ref": replay_result_ref(inputs.replay_result),
        "intent_ref": plan_persistence.intent_store_ref,
        "plan_ref": plan_persistence.accepted_plan_store_ref,
        "_trusted_seal": _UPSTREAM_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_attempt_upstream_evidence(result)


def require_attempt_upstream_evidence(value: object) -> AttemptUpstreamEvidence:
    if (
        type(value) is not AttemptUpstreamEvidence
        or getattr(value, "_trusted_seal", None) is not _UPSTREAM_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "upstream evidence is not delivery-owner sealed",
        )
    if (
        type(value.knowledge_snapshot_ref) is not HashBoundRef
        or value.knowledge_snapshot_ref.kind is not RefKind.KNOWLEDGE_SNAPSHOT
    ):
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "upstream snapshot ref has the wrong kind",
        )
    for item in (
        value.retrieval_ref,
        value.retrieval_decision_ref,
        value.replay_ref,
        value.intent_ref,
        value.plan_ref,
    ):
        if type(item) is not HashBoundRef or item.kind is not RefKind.ARTIFACT:
            raise _fail(
                GoldRunFailureCode.IDENTITY_MISMATCH,
                "upstream artifact ref is invalid",
            )
    return value


@dataclass(frozen=True, init=False)
class AttemptDeliveryRefusal:
    upstream: AttemptUpstreamEvidence
    admission_failure_code: AdmissionFailureCode
    detail: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptDeliveryRefusal:
        raise TypeError("AttemptDeliveryRefusal is delivery-owner created")


@dataclass(frozen=True, init=False)
class AttemptDeliveryUnavailable:
    upstream: AttemptUpstreamEvidence
    admission_failure_code: AdmissionFailureCode
    detail: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptDeliveryUnavailable:
        raise TypeError("AttemptDeliveryUnavailable is delivery-owner created")


def _admission_failure(
    exc: AdmissionViolation,
    *,
    upstream: AttemptUpstreamEvidence,
) -> AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    return _make_attempt_delivery_failure(
        upstream=upstream,
        failure_code=exc.failure_code,
        detail=exc.detail,
        unavailable=exc.failure_code in _UNAVAILABLE_ADMISSION_FAILURES,
    )


def _make_attempt_delivery_failure(
    *, upstream: AttemptUpstreamEvidence, failure_code: AdmissionFailureCode,
    detail: str, unavailable: bool,
) -> AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    cls = AttemptDeliveryUnavailable if unavailable else AttemptDeliveryRefusal
    result = object.__new__(cls)
    for name, item in {
        "upstream": upstream,
        "admission_failure_code": failure_code,
        "detail": _bounded_detail(detail),
        "_trusted_seal": _UNAVAILABLE_SEAL if unavailable else _REFUSAL_SEAL,
    }.items():
        object.__setattr__(result, name, item)
    if unavailable:
        return require_attempt_delivery_unavailable(result)
    return require_attempt_delivery_refusal(result)


def _make_attempt_upstream_evidence_from_refs(
    **refs: HashBoundRef,
) -> AttemptUpstreamEvidence:
    result = object.__new__(AttemptUpstreamEvidence)
    for name, item in refs.items():
        object.__setattr__(result, name, item)
    object.__setattr__(result, "_trusted_seal", _UPSTREAM_SEAL)
    return require_attempt_upstream_evidence(result)


def require_attempt_delivery_refusal(value: object) -> AttemptDeliveryRefusal:
    if (
        type(value) is not AttemptDeliveryRefusal
        or getattr(value, "_trusted_seal", None) is not _REFUSAL_SEAL
        or type(value.admission_failure_code) is not AdmissionFailureCode
        or value.admission_failure_code in _UNAVAILABLE_ADMISSION_FAILURES
        or type(value.detail) is not str
        or not value.detail
        or len(value.detail) > 256
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery refusal is not delivery-owner sealed",
        )
    require_attempt_upstream_evidence(value.upstream)
    return value


def require_attempt_delivery_unavailable(
    value: object,
) -> AttemptDeliveryUnavailable:
    if (
        type(value) is not AttemptDeliveryUnavailable
        or getattr(value, "_trusted_seal", None) is not _UNAVAILABLE_SEAL
        or value.admission_failure_code not in _UNAVAILABLE_ADMISSION_FAILURES
        or type(value.detail) is not str
        or not value.detail
        or len(value.detail) > 256
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery unavailability is not delivery-owner sealed",
        )
    require_attempt_upstream_evidence(value.upstream)
    return value


@dataclass(frozen=True, init=False)
class PreparedWorkerDelivery:
    upstream: AttemptUpstreamEvidence
    admitted: CurrentAdmittedKnowledge
    context: WorkerContextRecord
    context_persistence: ContextPersistenceEvidence
    plan_persistence: PlanPersistenceEvidence
    authorization: SideEffectAuthorization
    phase_refs: AttemptPhaseRefs
    worktree: Path
    _record_store: FileStage10RecordStore
    _worker_adapter: Stage10WorkerContextAdapter
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PreparedWorkerDelivery:
        raise TypeError("PreparedWorkerDelivery is delivery-owner created")


def require_prepared_worker_delivery(value: object) -> PreparedWorkerDelivery:
    if (
        type(value) is not PreparedWorkerDelivery
        or getattr(value, "_trusted_seal", None) is not _PREPARED_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "prepared delivery is not delivery-owner sealed",
        )
    require_attempt_upstream_evidence(value.upstream)
    validate_current_admitted_knowledge(value.admitted)
    validate_worker_context(value.context)
    validate_context_persistence_evidence(
        value.context_persistence,
        context=value.context,
    )
    validate_plan_persistence_evidence(
        value.plan_persistence,
        intent=value.context.intent,
        accepted_plan=value.context.accepted_plan,
    )
    validate_side_effect_authorization(value.authorization)
    if (
        value.context.admitted_knowledge is not value.admitted
        or type(value.worktree) is not type(Path())
        or value.phase_refs.worker_context_id != value.context.context_id
        or value.phase_refs.worker_context_audit_sha256 != value.context.audit_sha256
        or not _same_ref(value.phase_refs.retrieval_ref, value.upstream.retrieval_ref)
        or type(value._record_store) is not FileStage10RecordStore
        or type(value._worker_adapter) is not Stage10WorkerContextAdapter
    ):
        raise _fail(
            GoldRunFailureCode.DELIVERY_MISMATCH,
            "prepared delivery bindings differ",
        )
    return value


def _require_phase_envelopes(
    *,
    manifest: GoldRunManifest,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
) -> None:
    expected_attempt = AttemptId(str(attempt_index))
    causal = inputs.retrieval_causal_record.envelope
    replay = inputs.replay_result.envelope
    for name, envelope in (("retrieval", causal), ("replay", replay)):
        if (
            envelope.run_id != manifest.run_id
            or envelope.attempt_id != expected_attempt
            or envelope.repository_revision.git_sha != manifest.config.base_revision
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                f"{name} evidence belongs to another run, attempt, or revision",
            )
    if (
        causal.policy_version != replay.policy_version
        or causal.environment_profile_id != replay.environment_profile_id
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "retrieval and replay execution contexts differ",
        )
    if not _same_ref(
        gate_decision_ref(inputs.retrieval_gate_decision),
        inputs.retrieval_causal_record.retrieval_gate_decision_ref,
    ):
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "retrieval gate differs from its durable causal evidence",
        )


def _require_admitted_envelope(
    admitted: CurrentAdmittedKnowledge,
    *,
    manifest: GoldRunManifest,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
) -> None:
    envelope = admitted.envelope
    causal = inputs.retrieval_causal_record.envelope
    if (
        envelope.run_id != manifest.run_id
        or envelope.attempt_id != AttemptId(str(attempt_index))
        or envelope.repository_revision.git_sha != manifest.config.base_revision
        or envelope.policy_version != causal.policy_version
        or envelope.environment_profile_id != causal.environment_profile_id
        or not _same_ref(
            admitted.boundary_ref,
            inputs.retrieval_causal_record.boundary_ref,
        )
        or not _same_ref(
            admitted.consumer_context_ref,
            inputs.retrieval_causal_record.consumer_context_ref,
        )
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "fresh admission differs from the attempt execution context",
        )


def _require_preparation_inputs(
    *,
    manifest: GoldRunManifest,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
    record_store: FileStage10RecordStore,
    worker_adapter: Stage10WorkerContextAdapter,
) -> None:
    if type(manifest) is not GoldRunManifest:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
    manifest.validate_identity()
    if (
        type(attempt_index) is not int
        or not 1 <= attempt_index <= manifest.config.max_attempts
    ):
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "attempt index is outside the manifest",
        )
    if type(inputs) is not PreparedAttemptInputs:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "prepared attempt inputs must be exact",
        )
    if (
        type(record_store) is not FileStage10RecordStore
        or type(worker_adapter) is not Stage10WorkerContextAdapter
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery requires the exact composition-bound Stage 10 adapters",
        )
    validate_gate_decision(inputs.retrieval_gate_decision)
    _require_phase_envelopes(
        manifest=manifest,
        attempt_index=attempt_index,
        inputs=inputs,
    )


def _persist_plan_bundle(
    *,
    inputs: PreparedAttemptInputs,
    record_store: FileStage10RecordStore,
) -> PlanPersistenceEvidence:
    with store_transaction(record_store.mutation_fence) as ticket:
        return record_store.persist_plan_bundle(
            intent=inputs.intent,
            accepted_plan=inputs.accepted_plan,
            ticket=ticket,
        )


def _admit_prepared_inputs(
    *,
    manifest: GoldRunManifest,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
    upstream: AttemptUpstreamEvidence,
) -> CurrentAdmittedKnowledge | AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    request = inputs.admission_request
    try:
        admitted = admit_for_use_now(
            request.handle,
            binding=request.binding,
            chain=request.chain,
            evidence=request.evidence,
            entitlements=request.entitlements,
            requested=request.requested,
        )
    except AdmissionViolation as exc:
        return _admission_failure(exc, upstream=upstream)
    _require_admitted_envelope(
        admitted,
        manifest=manifest,
        attempt_index=attempt_index,
        inputs=inputs,
    )
    return admitted


def _build_and_persist_worker_context(
    *,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
    admitted: CurrentAdmittedKnowledge,
    record_store: FileStage10RecordStore,
) -> tuple[WorkerContextRecord, ContextPersistenceEvidence]:
    selection = context_knowledge_selection(
        retrieval_decision=inputs.retrieval_gate_decision,
        admitted_knowledge=admitted,
    )
    context = build_worker_context(
        intent=inputs.intent,
        accepted_plan=inputs.accepted_plan,
        attempt_id=AttemptId(str(attempt_index)),
        admitted_knowledge=admitted,
        knowledge_selection=selection,
        knowledge_items=inputs.knowledge_items,
        replay_observations=inputs.replay_result.observations,
        excluded_refs=inputs.excluded_refs,
        budget=inputs.context_budget,
    )
    with store_transaction(record_store.mutation_fence) as ticket:
        persistence = record_store.persist_worker_context(context, ticket=ticket)
    return context, persistence


def _authorize_worker_context(
    *,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
    admitted: CurrentAdmittedKnowledge,
    context: WorkerContextRecord,
    plan_persistence: PlanPersistenceEvidence,
) -> SideEffectAuthorization:
    request = inputs.admission_request

    def read_current_state():
        return inputs.current_plan_state_reader.read_current_plan_state(
            admitted_knowledge=admitted,
        )

    return authorize_first_side_effect(
        accepted_plan=inputs.accepted_plan,
        intent=inputs.intent,
        authority=inputs.plan_authority,
        attempt_id=AttemptId(str(attempt_index)),
        current_state_reader=read_current_state,
        admission_freshness_validator=lambda value: require_current_point_of_use_evidence(
            value,
            binding=request.binding,
        ),
        context_id=context.context_id,
        context_audit_sha256=context.audit_sha256,
        delivery_envelope_sha256=context.delivery_envelope.envelope_sha256,
        plan_bundle_sha256=plan_persistence.bundle_sha256,
    )


def _make_prepared_worker_delivery(
    *,
    inputs: PreparedAttemptInputs,
    upstream: AttemptUpstreamEvidence,
    admitted: CurrentAdmittedKnowledge,
    context: WorkerContextRecord,
    context_persistence: ContextPersistenceEvidence,
    plan_persistence: PlanPersistenceEvidence,
    authorization: SideEffectAuthorization,
    record_store: FileStage10RecordStore,
    worker_adapter: Stage10WorkerContextAdapter,
) -> PreparedWorkerDelivery:
    """Seal the explicit authority fragments for one prepared dispatch.

    The parameters stay explicit because introducing an unsealed aggregate only
    to cross this private constructor would create a second delivery model.
    """

    result = object.__new__(PreparedWorkerDelivery)
    fields = {
        "upstream": upstream,
        "admitted": admitted,
        "context": context,
        "context_persistence": context_persistence,
        "plan_persistence": plan_persistence,
        "authorization": authorization,
        "phase_refs": upstream.phase_refs(
            worker_context_id=context.context_id,
            worker_context_audit_sha256=context.audit_sha256,
        ),
        "worktree": inputs.worker_worktree,
        "_record_store": record_store,
        "_worker_adapter": worker_adapter,
        "_trusted_seal": _PREPARED_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_prepared_worker_delivery(result)


def prepare_attempt_delivery(
    *,
    manifest: GoldRunManifest,
    attempt_index: int,
    inputs: PreparedAttemptInputs,
    record_store: FileStage10RecordStore,
    worker_adapter: Stage10WorkerContextAdapter,
) -> PreparedWorkerDelivery | AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    """Persist the plan, cross §22, and prepare the sole Stage 10 dispatch."""

    _require_preparation_inputs(
        manifest=manifest,
        attempt_index=attempt_index,
        inputs=inputs,
        record_store=record_store,
        worker_adapter=worker_adapter,
    )
    plan_persistence = _persist_plan_bundle(
        inputs=inputs,
        record_store=record_store,
    )
    upstream = _make_upstream_evidence(
        inputs=inputs,
        plan_persistence=plan_persistence,
    )
    admitted = _admit_prepared_inputs(
        manifest=manifest,
        attempt_index=attempt_index,
        inputs=inputs,
        upstream=upstream,
    )
    if type(admitted) is not CurrentAdmittedKnowledge:
        return admitted
    context, context_persistence = _build_and_persist_worker_context(
        attempt_index=attempt_index,
        inputs=inputs,
        admitted=admitted,
        record_store=record_store,
    )
    authorization = _authorize_worker_context(
        attempt_index=attempt_index,
        inputs=inputs,
        admitted=admitted,
        context=context,
        plan_persistence=plan_persistence,
    )
    return _make_prepared_worker_delivery(
        inputs=inputs,
        upstream=upstream,
        admitted=admitted,
        context=context,
        context_persistence=context_persistence,
        plan_persistence=plan_persistence,
        authorization=authorization,
        record_store=record_store,
        worker_adapter=worker_adapter,
    )


@dataclass(frozen=True, init=False)
class CompletedWorkerDelivery:
    upstream: AttemptUpstreamEvidence
    worker_context_id: str
    worker_context_audit_sha256: str
    worker_context_audit_ref: HashBoundRef
    delivery_envelope_ref: HashBoundRef
    plan_bundle_sha256: str
    invocation: WorkerInvocation
    worker_result: WorkerCandidateResult
    delivery_receipt: DeliveryReceipt
    delivery_receipt_ref: HashBoundRef
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompletedWorkerDelivery:
        raise TypeError("CompletedWorkerDelivery is delivery-owner created")


def _make_completed_worker_delivery(
    *,
    upstream: AttemptUpstreamEvidence,
    worker_context_id: str,
    worker_context_audit_sha256: str,
    worker_context_audit_ref: HashBoundRef,
    delivery_envelope_ref: HashBoundRef,
    plan_bundle_sha256: str,
    invocation: WorkerInvocation,
    worker_result: WorkerCandidateResult,
    delivery_receipt: DeliveryReceipt,
    delivery_receipt_ref: HashBoundRef,
) -> CompletedWorkerDelivery:
    """Seal the exact worker completion fields owned by delivery.

    This private seam is also used by the exact recovery codec; an intermediate
    parameter object would duplicate the authoritative completion model.
    """

    result = object.__new__(CompletedWorkerDelivery)
    fields = {
        "upstream": upstream,
        "worker_context_id": worker_context_id,
        "worker_context_audit_sha256": worker_context_audit_sha256,
        "worker_context_audit_ref": worker_context_audit_ref,
        "delivery_envelope_ref": delivery_envelope_ref,
        "plan_bundle_sha256": plan_bundle_sha256,
        "invocation": invocation,
        "worker_result": worker_result,
        "delivery_receipt": delivery_receipt,
        "delivery_receipt_ref": delivery_receipt_ref,
        "_trusted_seal": _COMPLETED_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_completed_worker_delivery(result)


def require_completed_worker_delivery(value: object) -> CompletedWorkerDelivery:
    if (
        type(value) is not CompletedWorkerDelivery
        or getattr(value, "_trusted_seal", None) is not _COMPLETED_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "completed delivery is not delivery-owner sealed",
        )
    require_attempt_upstream_evidence(value.upstream)
    if (
        type(value.invocation) is not WorkerInvocation
        or type(value.worker_result) is not WorkerCandidateResult
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "completed delivery contains foreign worker records",
        )
    validate_delivery_receipt(value.delivery_receipt)
    evidence = value.worker_result.delivery_evidence
    if (
        value.worker_context_id != value.invocation.context_id
        or value.delivery_receipt.invocation_id != value.invocation.invocation_id
        or value.delivery_receipt.attempt_id != value.invocation.attempt_id
        or value.delivery_receipt.context_id != value.invocation.context_id
        or value.delivery_receipt.envelope_sha256 != value.invocation.envelope_sha256
        or value.delivery_receipt.prompt_sha256 != value.invocation.payload_sha256
        or value.delivery_receipt.prompt_byte_length != value.invocation.payload_byte_length
        or evidence.invocation_id != value.invocation.invocation_id
        or evidence.context_id != value.invocation.context_id
        or evidence.envelope_sha256 != value.invocation.envelope_sha256
        or evidence.payload_sha256 != value.invocation.payload_sha256
        or evidence.payload_byte_length != value.invocation.payload_byte_length
        or evidence.status is not value.delivery_receipt.delivery_status
        or evidence.transport_name != value.delivery_receipt.transport_name
        or type(value.worker_context_audit_ref) is not HashBoundRef
        or type(value.delivery_envelope_ref) is not HashBoundRef
        or type(value.delivery_receipt_ref) is not HashBoundRef
        or any(
            item.kind is not RefKind.ARTIFACT
            for item in (
                value.worker_context_audit_ref,
                value.delivery_envelope_ref,
                value.delivery_receipt_ref,
            )
        )
        or _SHA256_RE.fullmatch(value.worker_context_audit_sha256) is None
        or _SHA256_RE.fullmatch(value.plan_bundle_sha256) is None
    ):
        raise _fail(
            GoldRunFailureCode.DELIVERY_MISMATCH,
            "completed delivery identities differ",
        )
    return value


def dispatch_prepared_attempt(
    *,
    prepared: PreparedWorkerDelivery,
    record_store: FileStage10RecordStore,
    worker_adapter: Stage10WorkerContextAdapter,
) -> CompletedWorkerDelivery:
    """Dispatch once through the exact adapters sealed during preparation."""

    checked = require_prepared_worker_delivery(prepared)
    if (
        type(record_store) is not FileStage10RecordStore
        or type(worker_adapter) is not Stage10WorkerContextAdapter
        or checked._record_store is not record_store
        or checked._worker_adapter is not worker_adapter
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "delivery adapter binding was substituted",
        )
    dispatch = worker_adapter.dispatch(
        worktree_path=checked.worktree,
        context=checked.context,
        persistence=checked.context_persistence,
        plan_persistence=checked.plan_persistence,
        authorization=checked.authorization,
    )
    require_worker_dispatch_result(dispatch)
    store = record_store
    with store_transaction(store.mutation_fence) as ticket:
        receipt_ref = store.persist_delivery_receipt(
            dispatch.delivery_receipt,
            ticket=ticket,
        )
    return _make_completed_worker_delivery(
        upstream=checked.upstream,
        worker_context_id=checked.context.context_id,
        worker_context_audit_sha256=checked.context.audit_sha256,
        worker_context_audit_ref=checked.context_persistence.audit_store_ref,
        delivery_envelope_ref=checked.context_persistence.delivery_store_ref,
        plan_bundle_sha256=checked.plan_persistence.bundle_sha256,
        invocation=dispatch.invocation,
        worker_result=dispatch.worker_result,
        delivery_receipt=dispatch.delivery_receipt,
        delivery_receipt_ref=receipt_ref,
    )


__all__ = [
    "AttemptDeliveryRefusal",
    "AttemptDeliveryUnavailable",
    "AttemptUpstreamEvidence",
    "CompletedWorkerDelivery",
    "PreparedWorkerDelivery",
    "dispatch_prepared_attempt",
    "prepare_attempt_delivery",
    "require_attempt_delivery_refusal",
    "require_attempt_delivery_unavailable",
    "require_attempt_upstream_evidence",
    "require_completed_worker_delivery",
    "require_prepared_worker_delivery",
]
