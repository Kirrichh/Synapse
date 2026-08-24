"""Stage 4 §22 point of use — revalidating an admission at the moment of delivery.

``admission`` owns the four gate decisions, the durable journal and the
``AdmittedKnowledgeHandle`` a completed chain mints. This module owns the last
step: what happens when a consumer is about to *act* on that handle.

It exists as a separate owner for two reasons.

The first is the repository's size rule. ``admission.py`` had reached the point
where further nodes had to attach through an adapter rather than grow the file,
and this node is a clean seam: everything here runs after a handle exists, and
nothing in the gate evaluators depends on it. The dependency runs one way — this
module imports ``admission``, ``admission`` imports nothing from here — so the
seam cannot become a cycle.

The second is what the node actually asserts. A handle records that an admission
happened; it cannot record that the admission is *still true*, and a consumer
holding one has no obligation in the type system to look again. So the barrier
is placed at the point of use, and its result is a sealed
``CurrentAdmittedKnowledge`` that names the subject set, consumer context,
boundary, policy, decision, receipt and journal position it just verified. A
delivery owner that accepts this object cannot be handed knowledge no gate
admitted, and cannot substitute other refs afterwards, because the refs it is
permitted to use are the ones the record carries.

The adapter surface is declared rather than discovered: ``ADAPTER_PRIVATE_SEAM``
below lists every non-public name this module takes from ``admission``, and a
tripwire fails if the list and the imports drift apart. Sharing those validators
is deliberate — reimplementing digest, timestamp and subject checks here is how
two owners end up disagreeing about what a valid record is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .canonicalization import HashBoundRef, RefKind
from .contracts import (
    AttemptId,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    validate_envelope_bound_record,
    validate_record_id,
)
from .authority_config import (
    SnapshotActorSet,
    SnapshotEvaluatorDeclaration,
    SnapshotIndependenceProof,
    validate_snapshot_actor_set,
    validate_snapshot_evaluator_declaration,
    validate_snapshot_independence_proof,
)
from .knowledge import require_current_snapshot_evaluator_entitlement
from .admission import (
    AdmissionFailureCode,
    AdmissionViolation,
    AdmittedKnowledgeHandle,
    AuthorityHeadSet,
    ConfiguredGateController,
    DecisionCommitReceipt,
    DecisionJournalPort,
    GateDecision,
    GateDecisionChain,
    GateDependencyUnavailable,
    require_committed_decision,
    require_entitled_chain,
    configure_gate_controller,
    require_configured_gate_controller,
    require_consumption_admitted,
    validate_admitted_handle,
    validate_authority_head_set,
    validate_commit_receipt,
    validate_gate_decision,
)
from .admission import (
    UTC_TIMESTAMP_FORMAT as _UTC_TIMESTAMP_FORMAT,
    _require_audit_heads_unchanged,
    _canonical,
    _fail,
    _identifier,
    _sha256_text,
    _subject_key,
    _subjects,
    _timestamp,
)

#: The exact non-public surface this adapter is permitted to take from the
#: admission owner. Shared so the two owners cannot disagree about what a valid
#: digest, timestamp, identifier or subject tuple is; declared so the seam
#: cannot widen unnoticed. ``tests/test_stage4_gold_dependency_direction.py``
#: fails if the imports above and this tuple stop matching.
ADAPTER_PRIVATE_SEAM = (
    "UTC_TIMESTAMP_FORMAT",
    "_require_audit_heads_unchanged",
    "_canonical",
    "_fail",
    "_identifier",
    "_sha256_text",
    "_subject_key",
    "_subjects",
    "_timestamp",
)

_CURRENT_KNOWLEDGE_SEAL = object()
_PRODUCTION_BINDING_SEAL = object()
_ADMISSION_REQUEST_SEAL = object()


class ProductionAuthorityBinding:
    """Sealed production participants for one exact committed boundary."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _PRODUCTION_BINDING_SEAL or kwargs or len(args) != 13:
            raise TypeError("ProductionAuthorityBinding is factory-created")
        (
            self.controller,
            self.lifecycle_store,
            self.attestation_store,
            self.taint_store,
            self.admission_journal,
            self.admission_causal_history,
            self.compatibility_history,
            self.compatibility_probe,
            self.knowledge_store,
            self.snapshot_attempt_id,
            self.snapshot_evaluator_declaration,
            self.snapshot_actor_set,
            self.snapshot_independence_proof,
        ) = args
        self._trusted_seal = _PRODUCTION_BINDING_SEAL
        self._configuration_snapshot = args

    @property
    def fence(self):
        return self.admission_journal.mutation_fence

    @property
    def participants(self) -> tuple[object, ...]:
        return (
            self.lifecycle_store,
            self.attestation_store,
            self.taint_store,
            self.admission_journal,
            self.admission_causal_history,
            self.compatibility_history,
            self.knowledge_store,
        )

    def cursors(self) -> dict[str, tuple[str, int]]:
        lifecycle = self.lifecycle_store.current_anchor()
        provenance = self.attestation_store.current_anchor()
        taint = self.taint_store.current_anchor()
        # Cursors are readable only by a reader the snapshot authority entitles;
        # the opened snapshot itself is not part of the answer.
        self._open_attempt_snapshot()
        return {
            "boundary": (
                self.knowledge_store.current_anchor(),
                self.knowledge_store.current_sequence(),
            ),
            "lifecycle": (lifecycle.ordered_log_root_sha256, lifecycle.entry_count),
            "provenance": (provenance.ordered_log_root_sha256, provenance.entry_count),
            "taint": (taint.ordered_log_root_sha256, taint.entry_count),
            "admission_decision": (
                self.admission_journal.current_anchor(),
                len(self.admission_journal._digests()),
            ),
            "retrieval_causal": (
                self.admission_causal_history.current_anchor(),
                self.admission_causal_history.current_sequence(),
            ),
            "compatibility": (
                self.compatibility_history.current_anchor(),
                self.compatibility_history.current_sequence(),
            ),
        }

    def open_current_snapshot(self):
        snapshot = self._open_attempt_snapshot()
        if self.knowledge_store.current_boundary_id() != snapshot.boundary.atomic_boundary_id.digest_sha256:
            raise _fail(
                AdmissionFailureCode.HEAD_OBSERVATION_STALE,
                "attempt boundary is no longer the authoritative current head",
            )
        return snapshot

    def _require_snapshot_entitlement(self, snapshot):
        require_current_snapshot_evaluator_entitlement(
            snapshot.decision,
            declaration=self.snapshot_evaluator_declaration,
            actor_set=self.snapshot_actor_set,
            independence_proof=self.snapshot_independence_proof,
        )
        return snapshot

    def _open_attempt_snapshot(self):
        return self._require_snapshot_entitlement(
            self.knowledge_store.open_for_attempt(self.snapshot_attempt_id)
        )


def create_production_authority_binding(
    *,
    controller: ConfiguredGateController,
    lifecycle_store: object,
    attestation_store: object,
    taint_store: object,
    admission_journal: object,
    admission_causal_history: object,
    compatibility_history: object,
    compatibility_probe: object,
    knowledge_store: object,
    snapshot_attempt_id: AttemptId,
    snapshot_evaluator_declaration: SnapshotEvaluatorDeclaration,
    snapshot_actor_set: SnapshotActorSet,
    snapshot_independence_proof: SnapshotIndependenceProof,
) -> ProductionAuthorityBinding:
    """Bind real stores and rebuild the gate controller from those stores."""

    from .admission_journal import FileAdmissionJournal, FileSnapshotFence
    from .admission_store import FileAdmissionCausalStore
    from .compatibility import validate_compatibility_subject_evidence
    from .compatibility_store import FileCompatibilityStore
    from .gate_findings import (
        DurableConsumptionRevalidationProbe,
        consumption_finding_from_effective_taint,
        require_durable_revalidation_probe,
    )
    from .knowledge import atomic_boundary_ref
    from .knowledge_store import AuthoritativeKnowledgeStore
    from .lifecycle import LifecycleStore
    from .provenance import (
        BehaviorAttestationStore,
        require_behavior_attestation_consumable,
    )
    from .taint import TaintHistoryStore, require_taint_consumable

    require_configured_gate_controller(controller)
    exact = (
        (lifecycle_store, LifecycleStore, "lifecycle"),
        (attestation_store, BehaviorAttestationStore, "provenance"),
        (taint_store, TaintHistoryStore, "taint"),
        (admission_journal, FileAdmissionJournal, "admission"),
        (admission_causal_history, FileAdmissionCausalStore, "retrieval causal"),
        (compatibility_history, FileCompatibilityStore, "compatibility"),
        (compatibility_probe, DurableConsumptionRevalidationProbe, "Stage 3"),
        (knowledge_store, AuthoritativeKnowledgeStore, "knowledge"),
    )
    for value, expected, name in exact:
        if type(value) is not expected:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                f"the production binding requires an exact {name} participant",
            )
    durable_probe = require_durable_revalidation_probe(compatibility_probe)
    if durable_probe.history is not compatibility_history:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "the Stage 3 probe is attached to another compatibility history",
        )
    compatibility_evaluator = durable_probe.evaluator
    if (
        compatibility_evaluator.lifecycle_store is not lifecycle_store
        or compatibility_evaluator._attestation_store is not attestation_store
        or compatibility_evaluator._taint_store is not taint_store
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "the Stage 3 evaluator is not bound to the exact production stores",
        )
    fence = admission_journal.mutation_fence
    if type(fence) is not FileSnapshotFence:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "production requires FileSnapshotFence")
    for participant in (
        lifecycle_store,
        attestation_store,
        taint_store,
        admission_causal_history,
        compatibility_history,
        knowledge_store,
    ):
        if participant.mutation_fence is not fence:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "all production authority stores must share the exact coordinator",
            )
    authority_handle = controller._authority_handle
    lifecycle_store.require_handle(authority_handle)
    attestation_store.require_handle(authority_handle)
    taint_store.require_handle(authority_handle)
    if type(snapshot_attempt_id) is not AttemptId:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "production binding requires an exact attempt id")
    try:
        validate_snapshot_evaluator_declaration(snapshot_evaluator_declaration)
        validate_snapshot_actor_set(snapshot_actor_set)
        validate_snapshot_independence_proof(snapshot_independence_proof)
    except Exception as exc:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "production binding requires exact snapshot entitlement records") from exc
    snapshot = knowledge_store.open_for_attempt(snapshot_attempt_id)
    require_current_snapshot_evaluator_entitlement(
        snapshot.decision,
        declaration=snapshot_evaluator_declaration,
        actor_set=snapshot_actor_set,
        independence_proof=snapshot_independence_proof,
    )
    if knowledge_store.current_boundary_id() != snapshot.boundary.atomic_boundary_id.digest_sha256:
        raise _fail(AdmissionFailureCode.HEAD_OBSERVATION_STALE, "attempt boundary is not current authority")
    by_subject = {
        _subject_key(item.subject_ref): item for item in durable_probe.bindings
    }

    def evidence_for(subject_ref: HashBoundRef):
        bound = by_subject.get(_subject_key(subject_ref))
        if bound is None:
            raise GateDependencyUnavailable("the production binding has no subject evidence")
        evidence = durable_probe._evaluator._evidence_resolver(bound.descriptor)
        validate_compatibility_subject_evidence(evidence, descriptor=bound.descriptor)
        return bound, evidence

    def lifecycle_probe(subject_ref: HashBoundRef) -> bool:
        bound, _ = evidence_for(subject_ref)
        lifecycle_store.require_consumable(
            subject_ref=bound.descriptor.lifecycle_subject_ref,
            context=bound.descriptor.lifecycle_context,
        )
        return True

    def provenance_probe(subject_ref: HashBoundRef) -> bool:
        bound, evidence = evidence_for(subject_ref)
        require_behavior_attestation_consumable(
            attestation=evidence.attestation,
            expected_subject_content_key=bound.descriptor.content_key,
            authority_handle=authority_handle,
            attestation_store=attestation_store,
            lifecycle_store=lifecycle_store,
            lifecycle_context=bound.descriptor.lifecycle_context,
        )
        return True

    def taint_probe(subject_ref: HashBoundRef):
        bound, evidence = evidence_for(subject_ref)
        effective = require_taint_consumable(
            authority_handle=authority_handle,
            root_basis=evidence.taint_root_basis,
            source_profiles=evidence.taint_source_profiles,
            derivations=evidence.taint_derivations,
            decisions=evidence.taint_decisions,
            history_store=taint_store,
        )
        return consumption_finding_from_effective_taint(effective)

    def boundary_probe(candidate: HashBoundRef) -> bool:
        current = knowledge_store.open_current()
        require_current_snapshot_evaluator_entitlement(
            current.decision,
            declaration=snapshot_evaluator_declaration,
            actor_set=snapshot_actor_set,
            independence_proof=snapshot_independence_proof,
        )
        expected = atomic_boundary_ref(current.boundary)
        return candidate.to_dict() == expected.to_dict()

    def head_reader():
        lifecycle = lifecycle_store.current_anchor()
        provenance = attestation_store.current_anchor()
        taint = taint_store.current_anchor()
        current = knowledge_store.open_current()
        require_current_snapshot_evaluator_entitlement(
            current.decision,
            declaration=snapshot_evaluator_declaration,
            actor_set=snapshot_actor_set,
            independence_proof=snapshot_independence_proof,
        )
        return {
            "boundary_ref": atomic_boundary_ref(current.boundary),
            "heads": {
                "lifecycle": {"anchor_sha256": lifecycle.ordered_log_root_sha256, "sequence": lifecycle.entry_count},
                "provenance": {"anchor_sha256": provenance.ordered_log_root_sha256, "sequence": provenance.entry_count},
                "taint": {"anchor_sha256": taint.ordered_log_root_sha256, "sequence": taint.entry_count},
                "admission_decision": {"anchor_sha256": admission_journal.current_anchor(), "sequence": len(admission_journal._digests())},
                "retrieval_causal": {"anchor_sha256": admission_causal_history.current_anchor(), "sequence": admission_causal_history.current_sequence()},
                "compatibility": {"anchor_sha256": compatibility_history.current_anchor(), "sequence": compatibility_history.current_sequence()},
                "boundary": {"anchor_sha256": knowledge_store.current_anchor(), "sequence": knowledge_store.current_sequence()},
            },
        }

    actors = controller._source_actors
    production_controller = configure_gate_controller(
        declaration=controller.declaration,
        policy_version=controller.policy_version,
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        repository_revision=controller.repository_revision,
        environment_profile_id=controller.environment_profile_id,
        trusted_clock=controller._trusted_clock,
        taint_probe=taint_probe,
        provenance_probe=provenance_probe,
        lifecycle_probe=lifecycle_probe,
        compatibility_probe=durable_probe,
        boundary_probe=boundary_probe,
        grant_probe=controller._grant_probe,
        head_reader=head_reader,
        producer_actor=actors[0],
        retriever_actor=actors[1],
        consumer_actor=actors[2],
    )
    binding = ProductionAuthorityBinding(
        production_controller,
        lifecycle_store,
        attestation_store,
        taint_store,
        admission_journal,
        admission_causal_history,
        compatibility_history,
        durable_probe,
        knowledge_store,
        snapshot_attempt_id,
        snapshot_evaluator_declaration,
        snapshot_actor_set,
        snapshot_independence_proof,
        _seal=_PRODUCTION_BINDING_SEAL,
    )
    return validate_production_authority_binding(binding)


def validate_production_authority_binding(
    value: ProductionAuthorityBinding,
) -> ProductionAuthorityBinding:
    if (
        type(value) is not ProductionAuthorityBinding
        or getattr(value, "_trusted_seal", None) is not _PRODUCTION_BINDING_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "production authority binding is not factory sealed",
        )
    current = (
        value.controller,
        value.lifecycle_store,
        value.attestation_store,
        value.taint_store,
        value.admission_journal,
        value.admission_causal_history,
        value.compatibility_history,
        value.compatibility_probe,
        value.knowledge_store,
        value.snapshot_attempt_id,
        value.snapshot_evaluator_declaration,
        value.snapshot_actor_set,
        value.snapshot_independence_proof,
    )
    snapshot = getattr(value, "_configuration_snapshot", None)
    if type(snapshot) is not tuple or len(snapshot) != len(current) or any(
        actual is not configured for actual, configured in zip(current, snapshot)
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "production authority binding participants changed after configuration",
        )
    require_configured_gate_controller(value.controller)
    from .coordination import require_same_coordinator

    require_same_coordinator(value.fence, participants=value.participants)
    return value


@dataclass(frozen=True, init=False)
class CurrentAdmittedKnowledge:
    """A revalidation result that names the knowledge it revalidated.

    The distinction this type exists to make is narrow and load-bearing. An
    earlier revision had ``require_current_admitted_handle`` return only the
    fresh ``AuthorityHeadSet``, and the module claimed on that basis that a
    consumer contract taking the *result* could not bypass the gate. It could:
    a head set says "the world had not moved at time T" and says nothing at all
    about *which* subjects were admitted, for which consumer, under which
    boundary or decision. A caller could revalidate one handle and then compile,
    replay or execute over an entirely different subject set, and every type in
    the signature would still be satisfied.

    So the revalidation result carries the binding as well as the freshness:
    the exact subject set, consumer context, boundary and policy version that
    were checked; the consumption decision and durable receipt they rest on; the
    journal anchor observed at the moment of the check; and the fresh head
    observation. A consumer that accepts this object cannot be handed knowledge
    that no gate admitted, and cannot silently substitute other refs afterwards,
    because the refs it is allowed to use are the ones stored here.
    """

    schema_version: SchemaVersion
    knowledge_id: RecordId
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    handle_id: RecordId
    subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    consumption_decision_id: RecordId
    commit_receipt: DecisionCommitReceipt
    observed_head_set: AuthorityHeadSet
    journal_anchor_sha256: str
    #: The coordinator epoch this result describes: even, and settled *after* any
    #: append this revalidation made itself. Recorded rather than implied, because
    #: "the world had not moved" and "the world had moved by exactly my own write"
    #: are different claims and only the second is true of `admit_for_use_now`.
    observed_epoch: int
    gate_chain_decision_refs: tuple[HashBoundRef, ...]
    gate_chain_receipt_refs: tuple[HashBoundRef, ...]
    chain_evidence_sha256: str
    fresh_consumption_decision_ref: HashBoundRef
    fresh_commit_receipt_ref: HashBoundRef
    verified_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CurrentAdmittedKnowledge:
        raise TypeError(
            "CurrentAdmittedKnowledge is produced only by admit_for_use_now"
        )

    def to_dict(self) -> dict[str, object]:
        validate_current_admitted_knowledge(self)
        payload = _current_knowledge_payload(self)
        if self.schema_version is SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1:
            return payload | {"knowledge_id": self.knowledge_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": payload,
        }

    def canonical_bytes(self) -> bytes:
        validate_current_admitted_knowledge(self)
        if self.schema_version is SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1:
            return _canonical(_current_knowledge_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_current_knowledge_payload(self),
        )


def _current_knowledge_payload(value: CurrentAdmittedKnowledge) -> dict[str, object]:
    payload = {
        "schema_version": value.schema_version.value,
        "handle_id": value.handle_id.to_dict(),
        "subject_refs": [item.to_dict() for item in value.subject_refs],
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "policy_version": value.policy_version,
        "consumption_decision_id": value.consumption_decision_id.to_dict(),
        "commit_receipt": value.commit_receipt.to_dict(),
        "observed_head_set": value.observed_head_set.to_dict(),
        "journal_anchor_sha256": value.journal_anchor_sha256,
        "observed_epoch": value.observed_epoch,
        "verified_at_utc": value.verified_at_utc.strftime(_UTC_TIMESTAMP_FORMAT),
    }
    if value.schema_version is SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V2:
        payload.update(
            {
                "gate_chain_decision_refs": [
                    item.to_dict() for item in value.gate_chain_decision_refs
                ],
                "gate_chain_receipt_refs": [
                    item.to_dict() for item in value.gate_chain_receipt_refs
                ],
                "chain_evidence_sha256": value.chain_evidence_sha256,
                "fresh_consumption_decision_ref": value.fresh_consumption_decision_ref.to_dict(),
                "fresh_commit_receipt_ref": value.fresh_commit_receipt_ref.to_dict(),
            }
        )
    return payload


def _commit_receipt_ref(value: DecisionCommitReceipt) -> HashBoundRef:
    validate_commit_receipt(value)
    if value.receipt_id is None:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION,
            "current point-of-use evidence requires a v2 commit receipt",
        )
    canonical = value.canonical_bytes()
    import hashlib

    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.receipt_id.digest_sha256,
        schema_id=value.schema_version.value,
        sha256=hashlib.sha256(canonical).hexdigest(),
        byte_length=len(canonical),
        media_type="application/json",
    )


def validate_current_admitted_knowledge(value: CurrentAdmittedKnowledge) -> None:
    """Refuse anything that is not a sealed, self-consistent revalidation result."""

    if (
        type(value) is not CurrentAdmittedKnowledge
        or getattr(value, "_trusted_seal", None) is not _CURRENT_KNOWLEDGE_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "current admitted knowledge is not factory sealed",
        )
    if value.schema_version not in {
        SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1,
        SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V2,
    }:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION,
            "current admitted knowledge schema is unknown",
        )
    _subjects(value.subject_refs)
    if type(value.consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "revalidated knowledge requires an exact consumer context",
        )
    if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            "revalidated knowledge requires an exact committed boundary ref",
        )
    _identifier(value.policy_version, "policy_version")
    validate_commit_receipt(value.commit_receipt)
    validate_authority_head_set(value.observed_head_set)
    _sha256_text(value.journal_anchor_sha256, "journal_anchor_sha256")
    if type(value.observed_epoch) is not int or value.observed_epoch < 0 or value.observed_epoch % 2:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "a revalidation result binds to an exact settled coordinator epoch",
        )
    _timestamp(value.verified_at_utc, "verified_at_utc")
    if _subject_key(value.observed_head_set.boundary_ref) != _subject_key(value.boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the fresh observation belongs to another boundary",
        )
    if value.commit_receipt.gate_decision_id.digest_sha256 != value.consumption_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the revalidation receipt belongs to another consumption decision",
        )
    try:
        if value.schema_version is SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1:
            validate_record_id(
                value.knowledge_id, canonical_bytes=_canonical(_current_knowledge_payload(value))
            )
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 point-of-use envelope is absent")
            if (
                len(value.gate_chain_decision_refs) != 4
                or len(value.gate_chain_receipt_refs) != 4
                or any(type(item) is not HashBoundRef for item in value.gate_chain_decision_refs)
                or any(type(item) is not HashBoundRef for item in value.gate_chain_receipt_refs)
                or type(value.fresh_consumption_decision_ref) is not HashBoundRef
                or type(value.fresh_commit_receipt_ref) is not HashBoundRef
            ):
                raise _fail(AdmissionFailureCode.CHAIN_NOT_DURABLE, "v2 point-of-use chain references are incomplete")
            _sha256_text(value.chain_evidence_sha256, "chain_evidence_sha256")
            if (
                value.fresh_consumption_decision_ref.ref_id
                != value.consumption_decision_id.digest_sha256
                or value.fresh_commit_receipt_ref.to_dict()
                != _commit_receipt_ref(value.commit_receipt).to_dict()
                or value.observed_head_set.schema_version
                is not SchemaVersion.AUTHORITY_HEAD_SET_V2
                or value.commit_receipt.schema_version
                is not SchemaVersion.DECISION_COMMIT_RECEIPT_V2
                or value.observed_head_set.envelope is None
                or value.commit_receipt.envelope is None
                or value.envelope.run_id != value.observed_head_set.envelope.run_id
                or value.envelope.attempt_id != value.observed_head_set.envelope.attempt_id
                or value.envelope.run_id != value.commit_receipt.envelope.run_id
                or value.envelope.attempt_id != value.commit_receipt.envelope.attempt_id
            ):
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 point-of-use bindings differ")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_current_knowledge_payload(value)),
                expected_identity_domain=IdentityDomain.CURRENT_ADMITTED_KNOWLEDGE_V2,
            )
            if value.knowledge_id != value.envelope.record_id:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "point-of-use identity differs from envelope")
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "knowledge_id does not match its payload",
        ) from exc


def require_current_point_of_use_evidence(
    value: CurrentAdmittedKnowledge,
    *,
    binding: ProductionAuthorityBinding,
) -> None:
    """Re-check whether completed audit evidence still describes the current world."""

    from .coordination import read_current_authority_state
    from .knowledge import atomic_boundary_ref

    validate_current_admitted_knowledge(value)
    validate_production_authority_binding(binding)
    if value.schema_version is not SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V2:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION,
            "v1 point-of-use evidence is audit-readable but never current authority",
        )
    fresh = read_current_authority_state(
        binding.controller,
        fence=binding.fence,
        participants=binding.participants,
    )
    current_snapshot = binding.open_current_snapshot()
    if (
        fresh.exit_epoch != value.observed_epoch
        or _subject_key(fresh.head_set.boundary_ref) != _subject_key(value.boundary_ref)
        or _subject_key(atomic_boundary_ref(current_snapshot.boundary))
        != _subject_key(value.boundary_ref)
        or tuple(item.to_dict() for item in fresh.head_set.observations)
        != tuple(item.to_dict() for item in value.observed_head_set.observations)
    ):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "point-of-use evidence no longer describes the current authority state",
        )
    return None


def require_admitted_subjects(
    value: CurrentAdmittedKnowledge,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
) -> None:
    """Audit that a subject set matches completed point-of-use evidence.

    Holding a valid revalidation result is not the same as acting on it. A
    consumer that receives ``CurrentAdmittedKnowledge`` for subjects A and B and
    then compiles over B and C has satisfied every type in its signature while
    using an object no gate admitted. This is the call that closes that gap, and
    The function returns no refs and confers no present-tense authority. A future
    consumer must perform its own fenced point-of-use validation immediately
    before use; this Patch 7+8 audit comparison cannot be carried as a capability.
    """

    validate_current_admitted_knowledge(value)
    if type(consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "a use site must name the exact consumer context it is acting for",
        )
    if _subject_key(consumer_context_ref) != _subject_key(value.consumer_context_ref):
        raise _fail(
            AdmissionFailureCode.STALE_DECISION,
            "the consumer context is not the one this knowledge was admitted for",
        )
    if tuple(_subject_key(item) for item in _subjects(subject_refs)) != tuple(
        _subject_key(item) for item in value.subject_refs
    ):
        raise _fail(
            AdmissionFailureCode.SUBJECT_MISMATCH,
            "the subjects about to be used are not the admitted subject set",
        )
    return None


def require_current_admitted_handle(
    handle: AdmittedKnowledgeHandle,
    *,
    controller: ConfiguredGateController,
    journal: DecisionJournalPort,
    consumption_decision: GateDecision,
    fence: object,
) -> None:
    """Audit a cached handle without creating present-tense use authority.

    ``admit_for_consumption`` records the settled fenced observation supplied at
    mint, and that is all its handle can prove. Between minting and use the
    behavior can be revoked, its taint escalated, its admission superseded or
    the boundary replaced — and a merely *structurally* valid handle would be a
    cached boolean in a typed wrapper, which is precisely the defect §22's
    time-of-use requirement exists to prevent.

    So everything is re-asserted here against the world as it is now: the
    handle's own identity, the exact decision it names, that decision's durable
    inclusion and un-rolled-back history, and a fresh coherent observation of
    the boundary and every authority head.

    This cached path returns ``None`` after the audit. It cannot create a sealed
    ``CurrentAdmittedKnowledge`` because it does not perform and durably commit
    fresh Stage 3 revalidation. Only ``admit_for_use_now`` may create that
    present-tense authority through the sealed production binding.

    ``fence`` is required here for the same reason it is required next door.
    This function's claim is "a fresh *coherent* observation of the boundary and
    every authority head", and it used to establish coherence by reading six
    stores through one call — which is one call, not one instant. The epoch it
    now brackets the read with is the same epoch the result binds to, so the
    record states which moment it describes instead of implying one.

    A caller may use this function for audit, but a consumer must obtain the
    result of ``admit_for_use_now`` rather than treating this audit or the cached
    handle as authority.
    """

    from .coordination import (
        read_current_authority_state,
        require_snapshot_fence,
        settle_after_own_mutation,
    )

    validate_admitted_handle(handle)
    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    validate_gate_decision(consumption_decision)
    if consumption_decision.gate_decision_id.digest_sha256 != handle.consumption_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the supplied decision is not the one this handle was minted from",
        )
    require_consumption_admitted(
        consumption_decision,
        subject_refs=handle.subject_refs,
        consumer_context_ref=handle.consumer_context_ref,
        boundary_ref=handle.boundary_ref,
        policy_version=handle.policy_version,
    )
    require_committed_decision(
        handle.commit_receipt, decision=consumption_decision, journal=journal
    )
    fenced = read_current_authority_state(controller, fence=fence, participants=(journal,))
    observed = _require_audit_heads_unchanged(
        handle.head_set, observed=fenced.head_set
    )
    # Nothing here writes, so "settled after zero of my own intervals" is the
    # plain no-movement question — asked through the same function as the writing
    # path so the two cannot drift into disagreeing about what settled means.
    observed_epoch = settle_after_own_mutation(fenced, fence=fence, own_intervals=0)
    try:
        anchor = journal.current_anchor()
    except AdmissionViolation:
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "the decision journal could not report its current anchor",
        ) from exc

    # A stored decision can still be audited, but it cannot answer whether Stage
    # 3 passes now. Only admit_for_use_now performs and durably records that
    # fresh evaluation, so the cached path intentionally returns no capability.
    del observed, observed_epoch, anchor
    return None


def _mint_current_knowledge(
    *,
    handle: AdmittedKnowledgeHandle,
    decision: GateDecision,
    receipt: DecisionCommitReceipt,
    observed: AuthorityHeadSet,
    journal: DecisionJournalPort,
    controller: ConfiguredGateController,
    chain: GateDecisionChain,
    evidence: object,
    observed_epoch: int,
    anchor: str | None = None,
) -> CurrentAdmittedKnowledge:
    """Seal the result of the single fresh point-of-use authority path."""

    if anchor is None:
        try:
            anchor = journal.current_anchor()
        except AdmissionViolation:
            raise
        except GateDependencyUnavailable as exc:
            raise _fail(
                AdmissionFailureCode.JOURNAL_UNAVAILABLE,
                "the decision journal could not report its current anchor",
            ) from exc

    from .admission import gate_decision_ref
    import hashlib

    receipts = getattr(evidence, "receipts", None)
    if type(receipts) is not tuple or len(receipts) != 4 or not callable(getattr(evidence, "to_dict", None)):
        raise _fail(AdmissionFailureCode.CHAIN_NOT_DURABLE, "point-of-use evidence requires the exact committed chain")
    decisions = (chain.ingestion, chain.publication, chain.retrieval, chain.consumption)
    knowledge = object.__new__(CurrentAdmittedKnowledge)
    object.__setattr__(knowledge, "schema_version", SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V2)
    object.__setattr__(knowledge, "handle_id", handle.handle_id)
    object.__setattr__(knowledge, "subject_refs", handle.subject_refs)
    object.__setattr__(knowledge, "consumer_context_ref", handle.consumer_context_ref)
    object.__setattr__(knowledge, "boundary_ref", handle.boundary_ref)
    object.__setattr__(knowledge, "policy_version", handle.policy_version)
    object.__setattr__(knowledge, "consumption_decision_id", decision.gate_decision_id)
    object.__setattr__(knowledge, "commit_receipt", receipt)
    object.__setattr__(knowledge, "observed_head_set", observed)
    object.__setattr__(knowledge, "journal_anchor_sha256", _sha256_text(anchor, "journal_anchor"))
    object.__setattr__(knowledge, "observed_epoch", observed_epoch)
    object.__setattr__(
        knowledge,
        "gate_chain_decision_refs",
        tuple(gate_decision_ref(item) for item in decisions),
    )
    object.__setattr__(
        knowledge,
        "gate_chain_receipt_refs",
        tuple(_commit_receipt_ref(item) for item in receipts),
    )
    object.__setattr__(
        knowledge,
        "chain_evidence_sha256",
        hashlib.sha256(_canonical(evidence.to_dict())).hexdigest(),
    )
    object.__setattr__(knowledge, "fresh_consumption_decision_ref", gate_decision_ref(decision))
    object.__setattr__(knowledge, "fresh_commit_receipt_ref", _commit_receipt_ref(receipt))
    object.__setattr__(
        knowledge,
        "verified_at_utc",
        _timestamp(controller._trusted_clock(), "verified_at_utc"),
    )
    object.__setattr__(knowledge, "_trusted_seal", _CURRENT_KNOWLEDGE_SEAL)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.CURRENT_ADMITTED_KNOWLEDGE_V2,
        canonical_payload_bytes=_canonical(_current_knowledge_payload(knowledge)),
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        created_at_utc=knowledge.verified_at_utc,
        producer_component=controller.declaration.evaluator_component_id,
        repository_revision=decision.envelope.repository_revision,
        policy_version=controller.policy_version,
        environment_profile_id=controller.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(knowledge, "envelope", envelope)
    object.__setattr__(knowledge, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(knowledge, "knowledge_id", envelope.record_id)
    validate_current_admitted_knowledge(knowledge)
    return knowledge


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "CurrentAdmittedKnowledge",
    "PointOfUseAdmissionRequest",
    "ProductionAuthorityBinding",
    "admit_for_use_now",
    "create_point_of_use_admission_request",
    "create_production_authority_binding",
    "require_admitted_subjects",
    "require_point_of_use_admission_request",
    "require_current_admitted_handle",
    "require_current_point_of_use_evidence",
    "validate_current_admitted_knowledge",
    "validate_production_authority_binding",
]


# ---------------------------------------------------------------------------
# Re-deciding at the point of use, rather than re-reading anchors
# ---------------------------------------------------------------------------



def _require_chain_is_this_handles(
    handle: AdmittedKnowledgeHandle, *, chain: GateDecisionChain, evidence: object
) -> None:
    """Prove the chain and evidence are the ones that produced *this* handle.

    Everything downstream checked that the chain was internally valid, durably
    committed, and about the same subjects, consumer context, boundary and policy
    as the handle. All of those are *values*, and values are exactly what a second
    legitimate chain shares. So a handle minted from chain A could be presented
    with a valid chain B from another authority and another journal: the audit
    reproduced it, the original history was deleted, and a fresh admission came
    out resting on B.

    Identity is what separates them, and it is already recorded on both sides —
    the handle carries the consumption decision id it was minted from and the
    receipt that committed it. Comparing those is the whole check, and it runs
    before the durable recovery so that a substituted chain cannot append its
    fresh verdict to journal B on the way to being refused.
    """

    if type(chain) is not GateDecisionChain:
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "the point of use requires an exact gate decision chain",
        )
    # There is deliberately no separate comparison of the chain's consumption
    # decision id against the handle's. A campaign mutant removed one and nothing
    # failed, which is what an unenforceable rule looks like from outside — and it
    # is unenforceable because it is implied. The receipt below ties the evidence
    # to the handle, and `recover_chain_evidence` further down ties the evidence
    # to the chain; a chain that is not the handle's therefore cannot survive both.
    # Keeping the comparison would leave two statements of one fact, and removing
    # either would still look guarded in review.
    receipts = getattr(evidence, "receipts", None)
    if type(receipts) is not tuple or not receipts:
        raise _fail(
            AdmissionFailureCode.CHAIN_NOT_DURABLE,
            "the point of use requires the commit evidence of this handle's chain",
        )
    final = receipts[-1]
    validate_commit_receipt(final)
    if (
        final.decision_digest != handle.commit_receipt.decision_digest
        or final.gate_decision_id.value != handle.commit_receipt.gate_decision_id.value
    ):
        raise _fail(
            AdmissionFailureCode.CHAIN_NOT_DURABLE,
            "the commit evidence does not end in the receipt this handle carries",
        )
    # The chain's own consumption verdict must still be an ADMIT about exactly the
    # handle's subjects, context, boundary and policy. That is the value half of
    # the binding, kept because identity alone would not notice a handle whose
    # decision was later re-read against a different context.
    require_consumption_admitted(
        chain.consumption,
        subject_refs=handle.subject_refs,
        consumer_context_ref=handle.consumer_context_ref,
        boundary_ref=handle.boundary_ref,
        policy_version=handle.policy_version,
    )


def admit_for_use_now(
    handle: AdmittedKnowledgeHandle,
    *,
    binding: ProductionAuthorityBinding,
    chain: GateDecisionChain,
    evidence: object,
    entitlements: object,
    requested: object,
) -> CurrentAdmittedKnowledge:
    """Re-run the consumption gate here, now, and admit only on the fresh verdict.

    ``require_current_admitted_handle`` re-reads the authority heads and proves
    the stored decision is still durable. That is necessary and it is not
    sufficient, and the gap is narrow enough to be worth stating exactly.

    An authority head answers "has this store moved". Applicability answers "is
    this object usable in this exact frozen context *now*", and the two are not
    the same question. Compatibility depends on the live environment, tool and
    policy observation as much as on stored records: a compiler upgrade or a
    changed environment version makes an admitted behavior inapplicable without
    writing anything to the compatibility store, so its head anchor is unmoved
    and a head comparison sees a quiet world. §22 is explicit that a stored
    compatibility status does not substitute for a fresh check, and comparing an
    anchor *is* relying on the stored status — one indirection removed.

    So this does not compare; it decides. The world is captured under a fence,
    the consumption gate is evaluated again from scratch — which re-runs the
    compatibility probe against the exact consumer context, along with taint,
    lifecycle, provenance, boundary and grant — the fresh verdict is committed
    durably, and the returned knowledge names *that* decision. The earlier
    consumption ADMIT is not carried forward; it is superseded.

    A blocked fresh verdict yields nothing. There is no path here that falls
    back on the older decision, because "it was admissible ten minutes ago" is
    the precise claim §22's time-of-use requirement exists to refuse.

    **All of it is one transaction of one coordinator.** Six ordered steps —
    bind, read, decide, append, re-settle, mint — under this coordinator's
    exclusive lock, because the sequence reads the world and then writes to it.
    Detection alone cannot carry a read-decide-write: it can report interference
    forever without the work ever completing, and worse, between the fresh verdict
    and the append another writer can land the very change that would have blocked
    it.

    Two consequences worth stating, because both were wrong before. The append is
    made with the transaction's *own* ticket rather than by opening a second
    interval, so the epoch does not fall back to even in the middle of the
    transaction. And the result binds to the **final** even epoch, after that
    append: the entry epoch describes a world this function has since changed
    itself, and binding to it would attest a moment that no longer exists. What
    the closing check refuses is movement that is *not* this transaction's own.
    """

    from .admission import (
        GateDecisionKind,
        commit_gate_decision,
        evaluate_consumption_gate,
        require_gate_predecessor,
    )
    from .admission_store import recover_chain_evidence, require_admission_history
    from .coordination import (
        read_current_authority_state,
        require_exact_history_advancements,
        require_snapshot_fence,
        settle_after_own_mutation,
    )
    from .contracts import GateKind
    from .knowledge import atomic_boundary_ref
    from .persistence import store_transaction

    validate_production_authority_binding(binding)
    controller = binding.controller
    journal = binding.admission_journal
    fence = binding.fence
    validate_admitted_handle(handle)
    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    current_snapshot = binding.open_current_snapshot()
    if _subject_key(handle.boundary_ref) != _subject_key(
        atomic_boundary_ref(current_snapshot.boundary)
    ):
        raise _fail(
            AdmissionFailureCode.STALE_DECISION,
            "the handle names another committed boundary",
        )
    if {_subject_key(item) for item in handle.subject_refs} != {
        _subject_key(item.subject_ref) for item in binding.compatibility_probe.bindings
    }:
        raise _fail(
            AdmissionFailureCode.SUBJECT_MISMATCH,
            "the production binding does not cover the exact admitted subject set",
        )
    _require_chain_is_this_handles(handle, chain=chain, evidence=evidence)
    # The consumer is the last verifier and the one with the most to lose, so it
    # re-establishes entitlement from its own copies rather than inheriting the
    # builder's or the minter's conclusion. Same computation, different holder —
    # which is the whole content of the claim.
    require_entitled_chain(chain, entitlements=entitlements)

    # The chain that justified this handle must still be in the durable record,
    # and this is where that is asked. Minting demanded four committed receipts;
    # nothing re-asked at the moment of use, so deleting every decision from the
    # journal left this function happily minting `CurrentAdmittedKnowledge` behind
    # which history held only the consumption record it had just written itself.
    #
    # `recover_chain_evidence` rather than four `require_committed_decision`
    # calls: the latter asks `contains_record`, and membership is the check a
    # reordered history defeats while keeping every record — the repository has a
    # test saying exactly that. This asks for membership, each link to its
    # predecessor, contiguous positions and the witnessed anchor still being a
    # prefix of committed history.
    recover_chain_evidence(evidence, chain=chain, store=require_admission_history(journal))

    with fence.exclusive() as coordinator_guard:
        before = binding.cursors()
        # One coherent read of the world, or nothing. Everything below decides
        # against this observation, so a torn one must not reach the evaluation.
        fenced = read_current_authority_state(
            controller, fence=fence, participants=binding.participants
        )

        require_gate_predecessor(
            chain.retrieval, expected_gate=GateKind.RETRIEVAL, subject_refs=handle.subject_refs
        )
        # A refusal the evaluation reached before writing anything. Held rather
        # than raised, because a refusal and a torn write are not the same fact
        # and the fence cannot tell them apart from inside.
        #
        # An abandoned interval says "the store's last write neither completed
        # nor rolled back", and the coordinator then stays closed until an
        # explicit recovery has looked. That is the right answer for a torn write
        # and the wrong one for a verdict: §22 exists so that a world which has
        # drifted since preparation is *refused*, and a refusal that permanently
        # closed the coordinator would make the gate usable exactly once — the
        # first environment drift would take the world down with it. An
        # evaluation that raised before its first append leaves no unknown state,
        # so the interval that covered no write closes honestly and the refusal
        # is re-raised on the other side of it.
        #
        # Only that case. A refusal after the Stage 3 probe has appended belongs
        # to a transaction that did write, and it keeps the fail-closed answer.
        refusal: Exception | None = None
        # One interval, opened here and passed down. The journal would otherwise
        # open its own, which would take the epoch back to even in the middle of
        # this transaction — a reader arriving at that instant would see a settled
        # world holding a decision this function has not yet finished making.
        #
        # The guard travels with it because the lock is already held by this
        # transaction, and it is not recursive: without the guard the interval
        # would try to take it a second time and this function would refuse
        # itself.
        with store_transaction(fence, guard=coordinator_guard) as ticket:
            with binding.compatibility_probe.active(ticket):
                try:
                    fresh = evaluate_consumption_gate(
                        controller,
                        subject_refs=handle.subject_refs,
                        consumer_context_ref=handle.consumer_context_ref,
                        boundary_ref=handle.boundary_ref,
                        requested=requested,
                        predecessor=chain.retrieval,
                    )
                except Exception as exc:
                    if binding.compatibility_probe.receipts:
                        raise
                    refusal = exc
                if refusal is None:
                    receipts = binding.compatibility_probe.receipts
                    records = binding.compatibility_probe.records
                    if len(receipts) != len(handle.subject_refs) or len(records) != len(
                        handle.subject_refs
                    ):
                        raise _fail(
                            AdmissionFailureCode.DECISION_NOT_DURABLE,
                            "the consumption verdict lacks one durable Stage 3 record per subject",
                        )
                    receipt = commit_gate_decision(
                        fresh,
                        journal=journal,
                        trusted_clock=controller._trusted_clock,
                        ticket=ticket,
                    )

        if refusal is not None:
            # The interval above closed on its own terms and the coordinator is
            # settled again. What the caller sees is the refusal the evaluation
            # reached, with its own code, and not the fence reporting a mutation
            # it never covered.
            raise refusal

        # The world must still be the one that was decided against, plus exactly
        # this transaction's own append and nothing else.
        final_epoch = settle_after_own_mutation(fenced, fence=fence, own_intervals=1)
        after = binding.cursors()
        require_exact_history_advancements(
            before,
            after,
            expected_advances={
                "boundary": 0,
                "lifecycle": 0,
                "provenance": 0,
                "taint": 0,
                "admission_decision": 1,
                "retrieval_causal": 0,
                "compatibility": len(handle.subject_refs),
            },
            entry_epoch=fenced.window.entry_epoch,
            final_epoch=final_epoch,
        )
        receipts = binding.compatibility_probe.receipts
        if (
            receipts[0].parent_anchor != before["compatibility"][0]
            or receipts[-1].history_anchor != after["compatibility"][0]
            or tuple(item.sequence for item in receipts)
            != tuple(range(before["compatibility"][1] + 1, after["compatibility"][1] + 1))
            or receipt.journal_anchor != after["admission_decision"][0]
            or receipt.gate_decision_id.digest_sha256 != fresh.gate_decision_id.digest_sha256
        ):
            raise _fail(
                AdmissionFailureCode.DECISION_NOT_DURABLE,
                "point-of-use receipts do not belong to the exact records just appended",
            )
        post_fenced = read_current_authority_state(
            controller, fence=fence, participants=binding.participants
        )
        if post_fenced.window.entry_epoch != final_epoch:
            raise _fail(
                AdmissionFailureCode.HEAD_OBSERVATION_STALE,
                "the final authority heads are not the completed interval's post-state",
            )
        head_set = post_fenced.head_set
        if fresh.decision_kind is not GateDecisionKind.ADMIT:
            raise _fail(
                AdmissionFailureCode.NOT_ADMITTED,
                f"the fresh consumption verdict is {fresh.decision_kind.value}",
            )

        minted = _mint_current_knowledge(
            handle=handle,
            decision=fresh,
            receipt=receipt,
            observed=head_set,
            journal=journal,
            controller=controller,
            chain=chain,
            evidence=evidence,
            observed_epoch=final_epoch,
            anchor=after["admission_decision"][0],
        )
    return minted


# ---------------------------------------------------------------------------
# The inputs a delivery owner must present to cross the barrier
# ---------------------------------------------------------------------------


class PointOfUseAdmissionRequest:
    """Everything ``admit_for_use_now`` needs, carried as one sealed object.

    Not a convenience wrapper. Two delivery owners — the replay owner and the
    activity-ledger owner — must each cross the §22 barrier at the moment of
    use, and each needs the same six inputs. Spelling those six out twice in two
    signatures is one rule written twice: the two lists could drift, and a
    signature that lost one of them would still type-check at every call site.

    What it deliberately does **not** do is admit anything. The call stays at the
    use site and is written there in full, because ``admit_for_use_now`` is the
    barrier the dependency tripwire looks for by name — a wrapper around it would
    let a delivery owner satisfy the tripwire by importing something that merely
    promises to admit. This object carries arguments; the decision is taken where
    the knowledge is used.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _ADMISSION_REQUEST_SEAL or kwargs or len(args) != 6:
            raise TypeError(
                "PointOfUseAdmissionRequest is created only by "
                "create_point_of_use_admission_request"
            )
        (
            self.handle,
            self.binding,
            self.chain,
            self.evidence,
            self.entitlements,
            self.requested,
        ) = args
        self._trusted_seal = _ADMISSION_REQUEST_SEAL

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_trusted_seal", None) is _ADMISSION_REQUEST_SEAL:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "a sealed point-of-use admission request is immutable",
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "a sealed point-of-use admission request is immutable",
        )


def create_point_of_use_admission_request(
    *,
    handle: AdmittedKnowledgeHandle,
    binding: ProductionAuthorityBinding,
    chain: GateDecisionChain,
    evidence: object,
    entitlements: object,
    requested: object,
) -> PointOfUseAdmissionRequest:
    """Bind the six inputs, checking the two that have exact types here.

    The handle and the production binding are validated now so that a malformed
    request is refused where it is built rather than deep inside a delivery
    owner. The chain, its recovered evidence, the verifier's entitlements and the
    requested envelope are checked by ``admit_for_use_now`` itself against each
    other and against the live stores, which is the only place those checks mean
    anything.
    """

    validate_admitted_handle(handle)
    validate_production_authority_binding(binding)
    if type(chain) is not GateDecisionChain:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "a point-of-use admission request needs an exact gate decision chain",
        )
    return PointOfUseAdmissionRequest(
        handle, binding, chain, evidence, entitlements, requested,
        _seal=_ADMISSION_REQUEST_SEAL,
    )


def require_point_of_use_admission_request(
    value: object,
) -> PointOfUseAdmissionRequest:
    """Refuse anything but a sealed request built by the factory above."""

    if (
        type(value) is not PointOfUseAdmissionRequest
        or getattr(value, "_trusted_seal", None) is not _ADMISSION_REQUEST_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "point-of-use admission requires an exact sealed request",
        )
    validate_admitted_handle(value.handle)
    validate_production_authority_binding(value.binding)
    return value
