from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    STAGE4_CANONICAL_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    HistoryDomain,
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    create_common_envelope,
    create_history_anchor,
    compute_payload_sha256,
    compute_record_id,
)
from synapse.experiments.gold.knowledge import (
    commit_repository_knowledge_snapshot,
    create_snapshot_coordination_context,
)
from synapse.experiments.gold.knowledge_contracts import (
    ATOMIC_SNAPSHOT_BOUNDARY_SCHEMA_V1,
    REPOSITORY_KNOWLEDGE_SNAPSHOT_SCHEMA_V1,
    SNAPSHOT_MANIFEST_CORE_SCHEMA_V1,
    AtomicSnapshotBoundary,
    KnowledgeSnapshotFailureCode,
    KnowledgeSnapshotViolation,
    RepositoryKnowledgeSnapshot,
    SnapshotCompletenessObservation,
    SnapshotCompletenessStatus,
    SnapshotManifestCore,
    SnapshotViewEntry,
    SnapshotStoreSequence,
    atomic_snapshot_boundary_payload,
    configure_snapshot_completeness_evaluator,
    create_atomic_snapshot_boundary,
    create_knowledge_domain_envelope,
    create_repository_knowledge_snapshot,
    create_snapshot_manifest_core,
    create_snapshot_transaction_id,
    enveloped_artifact_ref,
    evaluate_snapshot_completeness,
    repository_knowledge_snapshot_payload,
    snapshot_context_fingerprint,
    snapshot_manifest_core_payload,
    validate_atomic_snapshot_boundary,
    validate_repository_knowledge_snapshot,
)
from synapse.experiments.gold.knowledge_store import (
    CommittedAtomicSnapshotBoundary,
    KnowledgeSnapshotStore,
    open_knowledge_snapshot_store,
    require_committed_atomic_snapshot_boundary,
)
from synapse.experiments.gold.coordination import require_coordinated_fence_lease

from tests.test_stage4_gold_authority_overlay import AuthorityBundle, build_authority_bundle


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)
REVISION = RepositoryRevision.git_commit("4" * 40)
POLICY_VERSION = "synapse.stage4.gold.snapshot-policy/v1"
ENVIRONMENT_PROFILE = "synapse.stage4.test.snapshot-environment/v1"
SNAPSHOT_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gold" / "snapshot_manifests_v1"


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


class SourceHeadReader:
    def __init__(
        self,
        heads: tuple[SnapshotStoreSequence, ...],
        *,
        drift_after_first_read: bool = False,
    ) -> None:
        self._heads = heads
        self._drift_after_first_read = drift_after_first_read
        self.read_count = 0

    def read_snapshot_heads(self, *, fence_lease) -> tuple[SnapshotStoreSequence, ...]:
        require_coordinated_fence_lease(
            fence_lease,
            expected_context=fence_lease.context,
        )
        self.read_count += 1
        if self._drift_after_first_read and self.read_count > 1:
            changed = list(self._heads)
            original = changed[2]
            changed[2] = SnapshotStoreSequence(
                original.schema_version,
                original.store_name,
                original.sequence + 1,
                f"{original.head_identity}-drifted",
            )
            return tuple(changed)
        return self._heads


@dataclass(frozen=True)
class SnapshotHarness:
    authority: AuthorityBundle
    store: KnowledgeSnapshotStore
    evaluator: object
    core: SnapshotManifestCore
    decision: object
    snapshot: RepositoryKnowledgeSnapshot
    boundary: AtomicSnapshotBoundary
    source_reader: SourceHeadReader
    trusted_clock: object


def _store_sequences() -> tuple[SnapshotStoreSequence, ...]:
    return tuple(
        SnapshotStoreSequence(
            "synapse.stage4.gold.snapshot-store-sequence/v1",
            name,
            0,
            f"{name}-genesis",
        )
        for name in (
            "admission",
            "compatibility",
            "library",
            "lifecycle",
            "provenance",
            "taint",
        )
    )


def _genesis_anchor(domain: HistoryDomain, configuration_id):
    return create_history_anchor(
        history_domain=domain,
        configuration_id=configuration_id,
        entry_sha256s=(),
        domain_heads=(),
    )


def _complete_observation(
    core: SnapshotManifestCore,
    sequences: tuple[SnapshotStoreSequence, ...],
) -> SnapshotCompletenessObservation:
    return SnapshotCompletenessObservation(
        observed_store_sequences=sequences,
        resolved_ref_sha256s=tuple(
            sorted(
                item.sha256
                for collection in (
                    core.behavior_refs,
                    core.binding_refs,
                    core.attestation_refs,
                    core.historical_admission_refs,
                    core.historical_retrieval_refs,
                    core.conflict_refs,
                )
                for item in collection
            )
        ),
        unresolved_binding_sha256s=(),
        unresolved_attestation_sha256s=(),
        unresolved_lifecycle_identities=(),
        compatibility_data_missing=(),
        inconsistent_reference_identities=(),
        observed_context_fingerprints=(snapshot_context_fingerprint(core.envelope),),
        trusted_prior_sequences=(),
        integrity_failures=(),
    )


def build_snapshot_harness(
    tmp_path: Path,
    *,
    authority: AuthorityBundle | None = None,
    repository_revision: RepositoryRevision = REVISION,
    policy_version: str = POLICY_VERSION,
    environment_profile_id: str = ENVIRONMENT_PROFILE,
    library_root_sha256: str | None = None,
    index_root_sha256: str | None = None,
    lifecycle_root=None,
    provenance_root=None,
    taint_root=None,
    behavior_refs: tuple[HashBoundRef, ...] = (),
    binding_refs: tuple[HashBoundRef, ...] = (),
    attestation_refs: tuple[HashBoundRef, ...] = (),
    historical_admission_refs: tuple[HashBoundRef, ...] = (),
    historical_retrieval_refs: tuple[HashBoundRef, ...] = (),
    conflict_refs: tuple[HashBoundRef, ...] = (),
    executable_view: tuple[SnapshotViewEntry, ...] = (),
    audit_view: tuple[SnapshotViewEntry, ...] = (),
    store_sequences: tuple[SnapshotStoreSequence, ...] | None = None,
    observation_transform=None,
    drift_after_first_read: bool = False,
) -> SnapshotHarness:
    tmp_path.mkdir(parents=True, exist_ok=True)
    authority = build_authority_bundle() if authority is None else authority
    base = authority.base_handle.configuration
    overlay = authority.binding.knowledge_admission_authority_handle.configuration
    context = create_snapshot_coordination_context(
        coordination_root=(tmp_path / "coordination").resolve(),
        fence_identity="patch78-snapshot-fence",
        authority_binding=authority.binding,
    )
    store = open_knowledge_snapshot_store(
        root=(tmp_path / "knowledge-store").resolve(),
        authority_binding=authority.binding,
        coordination_context=context,
        allow_genesis=True,
    )
    sequences = _store_sequences() if store_sequences is None else store_sequences
    captured = NOW
    valid_until = NOW + timedelta(hours=1)
    roots = {
        "lifecycle_root": (
            _genesis_anchor(HistoryDomain.LIFECYCLE, base.configuration_id)
            if lifecycle_root is None
            else lifecycle_root
        ),
        "provenance_root": (
            _genesis_anchor(HistoryDomain.PROVENANCE, base.configuration_id)
            if provenance_root is None
            else provenance_root
        ),
        "taint_root": (
            _genesis_anchor(HistoryDomain.TAINT, base.configuration_id)
            if taint_root is None
            else taint_root
        ),
        "admission_root": _genesis_anchor(
            HistoryDomain.ADMISSION, overlay.configuration_id
        ),
        "compatibility_evidence_root": _genesis_anchor(
            HistoryDomain.COMPATIBILITY, overlay.configuration_id
        ),
    }
    payload = snapshot_manifest_core_payload(
        library_root_sha256=(library_root_sha256 or _sha("library-root")),
        index_root_sha256=(index_root_sha256 or _sha("index-root")),
        behavior_refs=behavior_refs,
        binding_refs=binding_refs,
        attestation_refs=attestation_refs,
        historical_admission_refs=historical_admission_refs,
        historical_retrieval_refs=historical_retrieval_refs,
        conflict_refs=conflict_refs,
        executable_view=executable_view,
        audit_view=audit_view,
        parent_snapshot_id=None,
        parent_boundary_id=None,
        store_sequences=sequences,
        captured_at_utc=captured,
        valid_until_utc=valid_until,
        **roots,
    )
    caller_payload = b"patch78-snapshot-caller"
    caller_envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMMON_RECORD,
        canonical_payload_bytes=caller_payload,
        run_id=RunId("run-p78-001"),
        attempt_id=AttemptId("attempt-p78-001"),
        created_at_utc=captured,
        producer_component="stage4-lifecycle-caller",
        repository_revision=repository_revision,
        policy_version=policy_version,
        environment_profile_id=environment_profile_id,
        lineage_parent_ids=(),
    )
    core_envelope = create_knowledge_domain_envelope(
        caller_envelope=caller_envelope,
        caller_canonical_payload_bytes=caller_payload,
        identity_domain=IdentityDomain.SNAPSHOT_MANIFEST_CORE,
        domain_payload=payload,
        producer_component=overlay.snapshot_coordinator_actor_identity.value,
        created_at_utc=captured,
        lineage_parent_ids=(),
    )
    core = create_snapshot_manifest_core(
        envelope=core_envelope,
        authority_binding=authority.binding,
        library_root_sha256=(library_root_sha256 or _sha("library-root")),
        index_root_sha256=(index_root_sha256 or _sha("index-root")),
        behavior_refs=behavior_refs,
        binding_refs=binding_refs,
        attestation_refs=attestation_refs,
        historical_admission_refs=historical_admission_refs,
        historical_retrieval_refs=historical_retrieval_refs,
        conflict_refs=conflict_refs,
        executable_view=executable_view,
        audit_view=audit_view,
        parent_snapshot_id=None,
        parent_boundary_id=None,
        store_sequences=sequences,
        captured_at_utc=captured,
        valid_until_utc=valid_until,
        **roots,
    )

    complete_observation = _complete_observation(core, sequences)
    observation = (
        complete_observation
        if observation_transform is None
        else observation_transform(complete_observation)
    )
    trusted_clock = lambda: NOW + timedelta(seconds=1)
    evaluator = configure_snapshot_completeness_evaluator(
        authority_binding=authority.binding,
        trusted_clock=trusted_clock,
        observation_provider=lambda _: observation,
    )
    coordinator = overlay.snapshot_coordinator_actor_identity
    decision = evaluate_snapshot_completeness(
        evaluator=evaluator,
        core=core,
        producer_actor_ids=(coordinator,),
        source_actor_ids=(ActorIdentity("snapshot-source"),),
        proposer_identity=coordinator,
        executor_identity=ActorIdentity("snapshot-executor"),
    )
    core_ref = enveloped_artifact_ref(envelope=core.envelope, payload=core.payload_dict())
    decision_ref = enveloped_artifact_ref(
        envelope=decision.envelope,
        payload=decision.payload_dict(),
    )
    executable_view_hash = hashlib.sha256(
        _canonical([item.to_dict() for item in executable_view])
    ).hexdigest()
    audit_view_hash = hashlib.sha256(
        _canonical([item.to_dict() for item in audit_view])
    ).hexdigest()
    snapshot_payload = repository_knowledge_snapshot_payload(
        core=core,
        core_artifact_ref=core_ref,
        decision=decision,
        decision_artifact_ref=decision_ref,
        parent_snapshot_id=None,
        executable_view_sha256=executable_view_hash,
        audit_view_sha256=audit_view_hash,
    )
    snapshot_envelope = create_knowledge_domain_envelope(
        caller_envelope=core.envelope,
        caller_canonical_payload_bytes=_canonical(core.payload_dict()),
        identity_domain=IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        domain_payload=snapshot_payload,
        producer_component=overlay.snapshot_coordinator_actor_identity.value,
        created_at_utc=NOW + timedelta(seconds=2),
        lineage_parent_ids=(
            LineageParentRef(core.core_id, LineageEdgeKind.REFERENCES),
            LineageParentRef(
                decision.decision_id.record_id,
                LineageEdgeKind.REFERENCES,
            ),
        ),
    )
    snapshot = create_repository_knowledge_snapshot(
        envelope=snapshot_envelope,
        evaluator=evaluator,
        core=core,
        completeness_decision=decision,
    )
    nonce = "snapshot-commit-nonce-001"
    transaction_id = create_snapshot_transaction_id(
        snapshot=snapshot,
        transaction_nonce=nonce,
    )
    boundary_payload = atomic_snapshot_boundary_payload(
        transaction_id=transaction_id,
        core=core,
        snapshot=snapshot,
        completeness_decision=decision,
        parent_boundary_id=None,
        start_sequence=0,
        commit_sequence=1,
        expected_commit_nonce=nonce,
    )
    boundary_envelope = create_knowledge_domain_envelope(
        caller_envelope=snapshot.envelope,
        caller_canonical_payload_bytes=_canonical(snapshot.payload_dict()),
        identity_domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        domain_payload=boundary_payload,
        producer_component=overlay.snapshot_coordinator_actor_identity.value,
        created_at_utc=NOW + timedelta(seconds=3),
        lineage_parent_ids=(
            LineageParentRef(snapshot.snapshot_id, LineageEdgeKind.REFERENCES),
        ),
    )
    boundary = create_atomic_snapshot_boundary(
        envelope=boundary_envelope,
        evaluator=evaluator,
        transaction_id=transaction_id,
        core=core,
        snapshot=snapshot,
        completeness_decision=decision,
        parent_boundary_id=None,
        start_sequence=0,
        commit_sequence=1,
        expected_commit_nonce=nonce,
    )
    return SnapshotHarness(
        authority,
        store,
        evaluator,
        core,
        decision,
        snapshot,
        boundary,
        SourceHeadReader(
            sequences,
            drift_after_first_read=drift_after_first_read,
        ),
        trusted_clock,
    )


def commit_snapshot(harness: SnapshotHarness) -> CommittedAtomicSnapshotBoundary:
    return commit_repository_knowledge_snapshot(
        authority_binding=harness.authority.binding,
        coordination_context=harness.store.coordination_context,
        store=harness.store,
        evaluator=harness.evaluator,
        core=harness.core,
        decision=harness.decision,
        snapshot=harness.snapshot,
        boundary=harness.boundary,
        source_head_reader=harness.source_reader,
        trusted_clock=harness.trusted_clock,
    )


def test_s4_acc_atomic_snapshot_01_terminal_commit_controls_visibility_and_restart(
    tmp_path: Path,
) -> None:
    harness = build_snapshot_harness(tmp_path)

    assert harness.store.recover().trusted_head is None
    committed = commit_snapshot(harness)
    recovered = harness.store.recover()

    assert harness.source_reader.read_count == 2
    assert recovered.diagnostic is None
    assert recovered.trusted_head is not None
    assert recovered.trusted_head.boundary_id == harness.boundary.atomic_boundary_id
    assert recovered.trusted_head.snapshot_id == harness.snapshot.snapshot_id
    require_committed_atomic_snapshot_boundary(
        committed,
        evaluator=harness.evaluator,
        expected_store=harness.store,
        trusted_clock=harness.trusted_clock,
    )


def test_s4_acc_atomic_snapshot_second_read_rejects_source_head_drift(
    tmp_path: Path,
) -> None:
    harness = build_snapshot_harness(tmp_path, drift_after_first_read=True)

    with pytest.raises(KnowledgeSnapshotViolation) as raised:
        commit_snapshot(harness)

    assert raised.value.failure_code is KnowledgeSnapshotFailureCode.SOURCE_HEAD_DRIFT
    recovery = harness.store.recover()
    assert recovery.trusted_head is None
    assert recovery.diagnostic is KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED


@pytest.mark.parametrize(
    "case",
    json.loads(
        (SNAPSHOT_FIXTURE_ROOT / "completeness_cases_v1.json").read_text(
            encoding="utf-8"
        )
    )["cases"],
    ids=lambda case: case["id"],
)
def test_s4_acc_snapshot_completeness_has_exact_fail_closed_status_mapping(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    harness = build_snapshot_harness(tmp_path)
    field = case["field"]
    raw_value = case["value"]
    value = (
        (_sha(raw_value),)
        if field in ("unresolved_binding_sha256s", "unresolved_attestation_sha256s")
        else (raw_value,)
    )
    observation = replace(
        _complete_observation(harness.core, harness.core.store_sequences),
        **{field: value},
    )
    evaluator = configure_snapshot_completeness_evaluator(
        authority_binding=harness.authority.binding,
        trusted_clock=harness.trusted_clock,
        observation_provider=lambda _: observation,
    )
    coordinator = (
        harness.authority.binding.knowledge_admission_authority_handle.configuration
        .snapshot_coordinator_actor_identity
    )
    decision = evaluate_snapshot_completeness(
        evaluator=evaluator,
        core=harness.core,
        producer_actor_ids=(coordinator,),
        source_actor_ids=(ActorIdentity("snapshot-source"),),
        proposer_identity=coordinator,
        executor_identity=ActorIdentity("snapshot-executor"),
    )

    assert decision.completeness_status is SnapshotCompletenessStatus[case["expected"]]
    with pytest.raises(KnowledgeSnapshotViolation) as raised:
        create_repository_knowledge_snapshot(
            envelope=harness.snapshot.envelope,
            evaluator=evaluator,
            core=harness.core,
            completeness_decision=decision,
        )
    assert (
        raised.value.failure_code
        is KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED
    )


def test_s4_acc_snapshot_records_reject_constructor_replace_and_tampering(
    tmp_path: Path,
) -> None:
    harness = build_snapshot_harness(tmp_path)

    with pytest.raises(TypeError):
        RepositoryKnowledgeSnapshot()
    with pytest.raises(TypeError):
        replace(harness.snapshot, audit_view_sha256="0" * 64)

    object.__setattr__(harness.boundary, "commit_sequence", 2)
    with pytest.raises(KnowledgeSnapshotViolation):
        validate_atomic_snapshot_boundary(
            harness.boundary,
            evaluator=harness.evaluator,
        )


def test_s4_acc_atomic_snapshot_genesis_sequence_is_exact(tmp_path: Path) -> None:
    harness = build_snapshot_harness(tmp_path)
    payload = atomic_snapshot_boundary_payload(
        transaction_id=harness.boundary.transaction_id,
        core=harness.core,
        snapshot=harness.snapshot,
        completeness_decision=harness.decision,
        parent_boundary_id=None,
        start_sequence=0,
        commit_sequence=2,
        expected_commit_nonce=harness.boundary.expected_commit_nonce,
    )
    envelope = create_knowledge_domain_envelope(
        caller_envelope=harness.snapshot.envelope,
        caller_canonical_payload_bytes=_canonical(harness.snapshot.payload_dict()),
        identity_domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        domain_payload=payload,
        producer_component=harness.boundary.envelope.producer_component,
        created_at_utc=harness.boundary.envelope.created_at_utc,
        lineage_parent_ids=harness.boundary.envelope.lineage_parent_ids,
    )

    with pytest.raises(KnowledgeSnapshotViolation) as raised:
        create_atomic_snapshot_boundary(
            envelope=envelope,
            evaluator=harness.evaluator,
            transaction_id=harness.boundary.transaction_id,
            core=harness.core,
            snapshot=harness.snapshot,
            completeness_decision=harness.decision,
            parent_boundary_id=None,
            start_sequence=0,
            commit_sequence=2,
            expected_commit_nonce=harness.boundary.expected_commit_nonce,
        )
    assert (
        raised.value.failure_code
        is KnowledgeSnapshotFailureCode.GENESIS_SEQUENCE_INVALID
    )


def test_snapshot_module_schema_constants_are_exact() -> None:
    assert SNAPSHOT_MANIFEST_CORE_SCHEMA_V1 == "synapse.stage4.gold.snapshot-manifest-core/v1"
    assert REPOSITORY_KNOWLEDGE_SNAPSHOT_SCHEMA_V1 == "synapse.stage4.gold.repository-knowledge-snapshot/v1"
    assert ATOMIC_SNAPSHOT_BOUNDARY_SCHEMA_V1 == "synapse.stage4.gold.atomic-snapshot-boundary/v1"


def test_snapshot_manifest_identity_vector_uses_exact_non_ascii_bytes() -> None:
    vector = json.loads(
        (SNAPSHOT_FIXTURE_ROOT / "identity_vector_v1.json").read_text(
            encoding="utf-8"
        )
    )
    canonical_bytes = bytes.fromhex(vector["canonical_payload_bytes_hex"])

    assert compute_payload_sha256(canonical_bytes=canonical_bytes) == vector["expected_payload_sha256"]
    assert compute_record_id(
        domain=IdentityDomain.SNAPSHOT_MANIFEST_CORE,
        canonical_bytes=canonical_bytes,
    ).value == vector["expected_record_id"]
