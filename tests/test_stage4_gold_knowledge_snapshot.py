"""Stage 4 Patch 7 — RepositoryKnowledgeSnapshot and AtomicSnapshotBoundary.

Covers the §21 acceptance checks: atomic construction, missing store/ref,
rollback and mix-and-match, revoked-object exclusion, and restart/recovery of an
exact snapshot identity. The mandatory mutants for this stage each have a named
killing test at the end of the file.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import knowledge as K
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    decode_stage4_canonical_bytes,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    IdentityDomain,
    SnapshotCompletenessStatus,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.persistence import (
    ensure_directory,
    stage_snapshot_transaction,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "gold" / "snapshot_manifests_v1"

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
ADMISSION_ROOT = hashlib.sha256(b"admission-root").hexdigest()
COMPAT_ROOT = hashlib.sha256(b"compatibility-root").hexdigest()


def ref(kind: RefKind, name: str, payload: bytes = b"payload") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


@pytest.fixture
def context() -> K.KnowledgeContext:
    return K.create_knowledge_context(
        repository_revision="a" * 40,
        policy_version="policy-v1",
        environment_profile_id="env-1",
    )


def make_roots(
    *, library: int = 7, index: int = 7, lifecycle: int = 42,
    library_root: bytes = b"lib", index_root: bytes = b"idx", lifecycle_root: bytes = b"lc",
) -> K.SnapshotRootSet:
    return K.create_snapshot_root_set(
        library_root_sha256=hashlib.sha256(library_root).hexdigest(),
        library_generation=library,
        index_root_sha256=hashlib.sha256(index_root).hexdigest(),
        index_generation=index,
        lifecycle_root_sha256=hashlib.sha256(lifecycle_root).hexdigest(),
        lifecycle_record_count=lifecycle,
    )


def make_manifest(
    context: K.KnowledgeContext,
    *, roots: K.SnapshotRootSet | None = None, parent=None,
) -> K.SnapshotManifest:
    return K.create_snapshot_manifest(
        context=context,
        roots=roots or make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(ref(RefKind.ARTIFACT, "retrieval-1"),),
        conflict_refs=(),
        created_at_utc=NOW,
        parent_snapshot_id=parent,
    )


def authority_handle():
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="attester"),
        builder_actor=ActorIdentity(value="builder"),
        taint_classifier_authority=AuthorityIdentity(value="taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity(value="revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity(value="lifecycle-writer"),
        governing_human_authority=None,
    )
    return create_stage4_authority_handle(configuration)


def make_evaluator(
    *, observed=None, resolver=None, probe=None, producer: str = "snapshot-producer",
    authority: str = "platform-evaluator",
) -> K.ConfiguredSnapshotEvaluator:
    return K.configure_snapshot_evaluator(
        authority_handle=authority_handle(),
        authority_identity=AuthorityIdentity(value=authority),
        authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
        trusted_clock=lambda: NOW,
        observed_roots_provider=observed or (lambda: make_roots()),
        ref_resolver=resolver or (lambda item: True),
        consumability_probe=probe or (lambda item: True),
        producer_actor=ActorIdentity(value=producer),
    )


def complete_decision(manifest: K.SnapshotManifest, **kwargs) -> K.SnapshotCompletenessDecision:
    evaluator = make_evaluator(observed=lambda: manifest.roots, **kwargs)
    return K.evaluate_snapshot_completeness(evaluator, manifest=manifest)


def commit(root: Path, manifest, decision, *, transaction_id="tx-1", start=1, commit_sequence=2, parent=None):
    return K.commit_atomic_snapshot_boundary(
        root,
        transaction_id=transaction_id,
        manifest=manifest,
        decision=decision,
        admission_root_sha256=ADMISSION_ROOT,
        compatibility_evidence_root_sha256=COMPAT_ROOT,
        start_sequence=start,
        commit_sequence=commit_sequence,
        parent_boundary=parent,
    )


# ---------------------------------------------------------------------------
# Manifest identity and immutability
# ---------------------------------------------------------------------------


def test_manifest_identity_is_deterministic_over_selection(context) -> None:
    first = make_manifest(context)
    second = make_manifest(context)
    assert first.snapshot_id.value == second.snapshot_id.value
    assert first.snapshot_id.domain is IdentityDomain.KNOWLEDGE_SNAPSHOT


def test_manifest_identity_changes_with_any_selected_object(context) -> None:
    base = make_manifest(context)
    other = K.create_snapshot_manifest(
        context=context,
        roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-2"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(ref(RefKind.ARTIFACT, "retrieval-1"),),
        conflict_refs=(),
        created_at_utc=NOW,
    )
    assert base.snapshot_id.value != other.snapshot_id.value


def test_manifest_payload_carries_no_self_asserted_status(context) -> None:
    payload = decode_stage4_canonical_bytes(
        make_manifest(context).canonical_bytes(),
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    assert "completeness_status" not in payload
    assert "snapshot_id" not in payload


def test_manifest_is_frozen_after_construction(context) -> None:
    manifest = make_manifest(context)
    with pytest.raises((AttributeError, TypeError)):
        manifest.behavior_refs = ()  # type: ignore[misc]


def test_manifest_rejects_direct_construction(context) -> None:
    with pytest.raises(TypeError):
        K.SnapshotManifest()  # type: ignore[call-arg]


def test_manifest_rejects_unordered_and_duplicate_refs(context) -> None:
    duplicate = (ref(RefKind.BINDING, "binding-1"), ref(RefKind.BINDING, "binding-1"))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.create_snapshot_manifest(
            context=context, roots=make_roots(),
            behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
            binding_refs=duplicate, attestation_refs=(), admission_refs=(),
            retrieval_decision_refs=(), conflict_refs=(), created_at_utc=NOW,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DUPLICATE_REFERENCE


def test_manifest_requires_at_least_one_selected_object(context) -> None:
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.create_snapshot_manifest(
            context=context, roots=make_roots(), behavior_refs=(), binding_refs=(),
            attestation_refs=(), admission_refs=(), retrieval_decision_refs=(),
            conflict_refs=(), created_at_utc=NOW,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


# ---------------------------------------------------------------------------
# Completeness evaluation
# ---------------------------------------------------------------------------


def test_complete_status_requires_every_check_to_pass(context) -> None:
    decision = complete_decision(make_manifest(context))
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_evaluator_cannot_be_the_snapshot_producer() -> None:
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        make_evaluator(producer="same-actor", authority="same-actor")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_unavailable_store_is_not_an_optimistic_pass(context) -> None:
    def unavailable():
        raise OSError("store is down")

    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=unavailable), manifest=make_manifest(context)
    )
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE


@pytest.mark.parametrize(
    "blocked_kind,expected",
    [
        (RefKind.BINDING, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_BINDING),
        (RefKind.SOURCE_EVIDENCE, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION),
        (RefKind.GATE_DECISION, SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA),
    ],
)
def test_unresolved_reference_maps_to_its_exact_status(context, blocked_kind, expected) -> None:
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(resolver=lambda item: item.kind is not blocked_kind),
        manifest=make_manifest(context),
    )
    assert decision.status is expected


def test_unconsumable_object_blocks_completeness(context) -> None:
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(probe=lambda item: False), manifest=make_manifest(context)
    )
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE


def test_manifest_roots_must_match_observed_store_roots(context) -> None:
    manifest = make_manifest(context, roots=make_roots(library=7, index=7, lifecycle=42))
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: make_roots(library=7, index=7, lifecycle=43)),
        manifest=manifest,
    )
    assert decision.status is SnapshotCompletenessStatus.ROLLBACK_DETECTED


def test_decision_identity_binds_its_exact_payload(context) -> None:
    decision = complete_decision(make_manifest(context))
    K.validate_completeness_decision(decision)
    assert decision.decision_id.domain is IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION


def test_decision_rejects_direct_construction() -> None:
    with pytest.raises(TypeError):
        K.SnapshotCompletenessDecision()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Rollback and mix-and-match detection
# ---------------------------------------------------------------------------


def test_root_regression_is_detected_per_root() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    assert K.detect_root_regression(make_roots(library=6, index=7, lifecycle=42), prior=prior) == "library"
    assert K.detect_root_regression(make_roots(library=7, index=6, lifecycle=42), prior=prior) == "index"
    assert K.detect_root_regression(make_roots(library=7, index=7, lifecycle=41), prior=prior) == "lifecycle"
    assert K.detect_root_regression(make_roots(library=8, index=8, lifecycle=43), prior=prior) is None


def test_same_generation_with_different_root_is_a_fork() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    forked = make_roots(library=7, index=7, lifecycle=42, library_root=b"other-lib")
    assert K.detect_root_regression(forked, prior=prior) == "library"


def test_new_index_with_old_lifecycle_is_mix_and_match() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    mixed = make_roots(library=7, index=9, lifecycle=42)
    assert K.detect_mixed_generation(mixed, prior=prior) is not None
    assert K.detect_mixed_generation(make_roots(library=8, index=8, lifecycle=43), prior=prior) is None


def test_mixed_generation_is_reported_by_the_evaluator(context) -> None:
    mixed = make_roots(library=7, index=9, lifecycle=42)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: mixed),
        manifest=make_manifest(context, roots=mixed),
        prior_roots=make_roots(library=7, index=8, lifecycle=42),
    )
    assert decision.status is SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED


def test_component_from_another_context_is_mix_and_match(context) -> None:
    other = K.create_knowledge_context(
        repository_revision="b" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_same_context(other, context, subject="component")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


# ---------------------------------------------------------------------------
# Atomic commit and visibility
# ---------------------------------------------------------------------------


def test_commit_requires_a_complete_decision(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(probe=lambda item: False), manifest=manifest
    )
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED


def test_commit_rejects_a_decision_about_another_manifest(context, tmp_path) -> None:
    manifest = make_manifest(context)
    other = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    decision = complete_decision(other)
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_committed_snapshot_restores_with_an_identical_identity(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    boundary = commit(root, manifest, decision)

    restored = K.open_usable_snapshot(root, transaction_id="tx-1")
    assert restored.snapshot_id.value == manifest.snapshot_id.value
    assert restored.atomic_boundary_id.value == boundary.atomic_boundary_id.value
    assert restored.completeness_status is SnapshotCompletenessStatus.COMPLETE


def test_snapshot_is_invisible_before_the_terminal_commit_marker(context, tmp_path) -> None:
    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    stage_snapshot_transaction(
        root, transaction_id="tx-staged", members={K.MANIFEST_MEMBER_NAME: manifest.canonical_bytes()}
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-staged")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMIT_MARKER_ABSENT


def test_transaction_id_is_consumed_exactly_once(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    commit(root, manifest, decision)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TRANSACTION_ID_REUSED


def test_commit_sequence_must_advance(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision, start=5, commit_sequence=5)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC


def test_derived_snapshot_must_declare_its_parent(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    parent_boundary = commit(root, first, complete_decision(first))
    orphan = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, orphan, complete_decision(orphan),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


def test_commit_refuses_a_root_regression_against_the_parent(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    parent_boundary = commit(root, first, complete_decision(first))
    older = make_manifest(context, roots=make_roots(library=7, index=7, lifecycle=42), parent=first.snapshot_id)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, older, complete_decision(older),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ROLLBACK_DETECTED


# ---------------------------------------------------------------------------
# Restart, corruption and consumer revalidation
# ---------------------------------------------------------------------------


def test_restart_rejects_mutated_committed_bytes(context, tmp_path) -> None:
    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    commit(root, manifest, complete_decision(manifest))
    member = root / "tx-1" / K.MANIFEST_MEMBER_NAME
    member.chmod(0o600)
    member.write_bytes(b'{"tampered": true}')
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED


def test_one_attempt_consumes_one_boundary(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43), parent=first.snapshot_id)
    second_boundary = commit(
        root, second, complete_decision(second),
        transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
    )
    restored = K.open_usable_snapshot(root, transaction_id="tx-1")
    K.require_usable_snapshot(
        restored, attempt_boundary_id=first_boundary.atomic_boundary_id, expected_context=context
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_usable_snapshot(
            restored, attempt_boundary_id=second_boundary.atomic_boundary_id, expected_context=context
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_open_refuses_a_boundary_other_than_the_attempt_boundary(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43), parent=first.snapshot_id)
    second_boundary = commit(
        root, second, complete_decision(second), transaction_id="tx-2", start=2, commit_sequence=3
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(
            root, transaction_id="tx-1", expected_boundary_id=second_boundary.atomic_boundary_id
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_consumer_revalidation_rejects_a_foreign_context(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    restored = K.open_usable_snapshot(root, transaction_id="tx-1")
    foreign = K.create_knowledge_context(
        repository_revision="c" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_usable_snapshot(
            restored, attempt_boundary_id=boundary.atomic_boundary_id, expected_context=foreign
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


def test_revoked_object_leaves_the_executable_view_but_stays_in_audit(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    restored = K.open_usable_snapshot(root, transaction_id="tx-1")

    assert len(restored.executable_refs(consumability_probe=lambda item: True)) == 2
    executable = restored.executable_refs(consumability_probe=lambda item: item.kind is not RefKind.BINDING)
    assert len(executable) == 1
    assert all(item.kind is not RefKind.BINDING for item in executable)
    # The manifest is audit history and still records every selected object.
    assert len(restored.manifest.selected_refs()) == 2


def test_usable_snapshot_rejects_direct_construction() -> None:
    with pytest.raises(TypeError):
        K.UsableKnowledgeSnapshot()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Committed fixtures
# ---------------------------------------------------------------------------


def _fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_valid_fixture_manifest_restores(context) -> None:
    restored = K.snapshot_manifest_from_dict(_fixture("valid_manifest.json"))
    assert restored.snapshot_id.value == make_manifest(context).snapshot_id.value


@pytest.mark.parametrize("name", ["incomplete_manifest.json", "unknown_field_manifest.json"])
def test_partial_or_unknown_field_fixture_is_rejected(name: str) -> None:
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.snapshot_manifest_from_dict(_fixture(name))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


def test_rollback_fixture_is_detected_against_the_baseline() -> None:
    baseline = K.SnapshotRootSet.from_dict(_fixture("baseline_roots.json")["roots"])
    rollback = K.snapshot_manifest_from_dict(_fixture("rollback_manifest.json"))
    assert K.detect_root_regression(rollback.roots, prior=baseline) is not None


def test_mixed_generation_fixture_is_detected_against_the_baseline() -> None:
    baseline = K.SnapshotRootSet.from_dict(_fixture("baseline_roots.json")["roots"])
    mixed = K.snapshot_manifest_from_dict(_fixture("mixed_generation_manifest.json"))
    assert K.detect_mixed_generation(mixed.roots, prior=baseline) is not None


# ---------------------------------------------------------------------------
# Mandatory mutation killers for Patch 7
# ---------------------------------------------------------------------------


def test_mutant_partial_manifest_treated_as_a_snapshot(context, tmp_path) -> None:
    """S4-MUT-ATOMIC-SNAPSHOT-01: a partial manifest must never be a snapshot."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    stage_snapshot_transaction(
        root,
        transaction_id="tx-partial",
        members={
            K.MANIFEST_MEMBER_NAME: manifest.canonical_bytes(),
            K.DECISION_MEMBER_NAME: complete_decision(manifest).canonical_bytes(),
        },
    )
    with pytest.raises(K.KnowledgeViolation):
        K.open_usable_snapshot(root, transaction_id="tx-partial")
    with pytest.raises(K.KnowledgeViolation):
        K.snapshot_manifest_from_dict(_fixture("incomplete_manifest.json"))


def test_mutant_recovery_substitutes_an_older_root(context, tmp_path) -> None:
    """S4-MUT-ATOMIC-SNAPSHOT-02: an older root must never be selected."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    newer = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=50))
    parent_boundary = commit(root, newer, complete_decision(newer))
    older = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=49), parent=newer.snapshot_id)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, older, complete_decision(older),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ROLLBACK_DETECTED


def test_mutant_new_index_mixed_with_old_lifecycle(context) -> None:
    """A new index root beside an old lifecycle root must be refused."""

    mixed = make_roots(library=7, index=12, lifecycle=42)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: mixed),
        manifest=make_manifest(context, roots=mixed),
        prior_roots=make_roots(library=7, index=8, lifecycle=42),
    )
    assert decision.status is SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED


def test_mutant_object_added_after_freeze(context, tmp_path) -> None:
    """An object added after the freeze must not reach a committed snapshot."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    commit(root, manifest, decision)

    with pytest.raises((AttributeError, TypeError)):
        manifest.behavior_refs = manifest.behavior_refs + (ref(RefKind.ARTIFACT, "late"),)  # type: ignore[misc]

    restored = K.open_usable_snapshot(root, transaction_id="tx-1")
    assert restored.manifest.snapshot_id.value == manifest.snapshot_id.value
    assert len(restored.manifest.selected_refs()) == 2

    # A manifest that really does gain an object is a different snapshot, and a
    # decision made over the frozen bytes does not describe it.
    extended = K.create_snapshot_manifest(
        context=context,
        roots=manifest.roots,
        behavior_refs=tuple(sorted(
            manifest.behavior_refs + (ref(RefKind.ARTIFACT, "late"),),
            key=lambda item: f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}",
        )),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    assert extended.snapshot_id.value != manifest.snapshot_id.value
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, extended, decision, transaction_id="tx-2", start=2, commit_sequence=3)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH
