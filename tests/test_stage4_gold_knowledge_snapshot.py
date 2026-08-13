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
import shutil

import pytest

from synapse.experiments.gold import knowledge as K
from synapse.experiments.gold.admission_journal import FileAdmissionJournal, FileSnapshotFence
from tests.gold_store_fence import fence_for, quiet_fence
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    decode_stage4_canonical_bytes,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    ContractViolation,
    AuthorityIdentity,
    AuthorityRole,
    IdentityDomain,
    SnapshotCompletenessStatus,
    require_snapshot_status_admits_execution,
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
    authority: str = "platform-evaluator", root_fence=None,
) -> K.ConfiguredSnapshotEvaluator:
    return K.configure_snapshot_evaluator(
        authority_handle=authority_handle(),
        authority_identity=AuthorityIdentity(value=authority),
        authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
        trusted_clock=lambda: NOW,
        observed_roots_provider=observed or (lambda: make_roots()),
        root_fence=root_fence or quiet_fence(),
        ref_resolver=resolver or (lambda item: True),
        consumability_probe=probe or (lambda item: True),
        producer_actor=ActorIdentity(value=producer),
    )


def complete_decision(manifest: K.SnapshotManifest, **kwargs) -> K.SnapshotCompletenessDecision:
    evaluator = make_evaluator(observed=lambda: manifest.roots, **kwargs)
    return K.evaluate_snapshot_completeness(evaluator, manifest=manifest)


def admission_journal(root: Path) -> FileAdmissionJournal:
    """A real file-backed decision journal beside the boundary store.

    The boundary now records an admission root the journal has to confirm, so
    the suite has to have one. Round 12's ``FileAdmissionJournal`` satisfies the
    structural port by shape, which is the whole point of declaring the port
    structurally: neither §21 nor §22 names the other.
    """

    journal = FileAdmissionJournal(
        root.parent / "admission" / "decisions.journal", fence_for(root.parent)
    )
    if not journal.contains_record(hashlib.sha256(b"a committed gate decision").hexdigest()):
        journal.append_record(b"a committed gate decision")
    return journal


#: ``None`` is a value a caller may legitimately pass as the journal, so the
#: helper cannot use it to mean "build me a real one".
_DEFAULT_JOURNAL = object()


def commit(
    root: Path, manifest, decision, *, transaction_id="tx-1", start=1, commit_sequence=2,
    parent=None, journal=_DEFAULT_JOURNAL, admission_root=None,
):
    store = admission_journal(root) if journal is _DEFAULT_JOURNAL else journal
    return K.commit_atomic_snapshot_boundary(
        root,
        transaction_id=transaction_id,
        manifest=manifest,
        decision=decision,
        admission_root_sha256=store.current_anchor() if admission_root is None else admission_root,
        admission_journal=store,
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
    # The parent boundary is passed now, not just named in the manifest. This
    # test used to commit a child that declared ``first`` as its parent while
    # handing the commit no parent at all — a chain nothing verified, which is
    # the defect the lineage comparison closes.
    first_boundary = commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43), parent=first.snapshot_id)
    second_boundary = commit(
        root, second, complete_decision(second), transaction_id="tx-2", start=2, commit_sequence=3,
        parent=first_boundary,
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


# ---------------------------------------------------------------------------
# Round 15 — every ref collection is resolved, and the boundary's two roots
# ---------------------------------------------------------------------------


def evidence_manifest(context: K.KnowledgeContext) -> K.SnapshotManifest:
    """A manifest that actually carries both compatibility evidence collections.

    ``make_manifest`` leaves ``conflict_refs`` empty, so a test written against
    it cannot tell a collection that is resolved from one that is skipped — an
    empty tuple passes either way.
    """

    return K.create_snapshot_manifest(
        context=context,
        roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(ref(RefKind.GATE_DECISION, "retrieval-1"),),
        conflict_refs=(ref(RefKind.CONTRACT_CONDITION, "conflict-1"),),
        created_at_utc=NOW,
    )


@pytest.mark.parametrize("dangling", ["retrieval-1", "conflict-1"])
def test_a_dangling_compatibility_evidence_ref_blocks_completeness(context, dangling) -> None:
    """COMPLETE is the status that admits execution, so what it skips is executable.

    Both collections used to go unresolved. A snapshot whose retrieval decisions
    or conflict records did not exist was declared COMPLETE and passed
    ``require_snapshot_status_admits_execution`` — the manifest named evidence
    and nothing checked that the evidence was there.

    Blocking by ``ref_id`` rather than by kind is deliberate: several
    collections share a ``RefKind``, so a kind-shaped test cannot say which
    collection the evaluator actually consulted.
    """

    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots,
            resolver=lambda item: item.ref_id != dangling,
        ),
        manifest=manifest,
    )
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA
    assert decision.status is not SnapshotCompletenessStatus.COMPLETE


def test_a_manifest_with_every_ref_resolvable_is_complete(context) -> None:
    """Guards the two tests above from passing because nothing can ever pass."""

    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots), manifest=manifest
    )
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_completeness_resolves_every_ref_collection_the_manifest_declares(context) -> None:
    """A seventh collection must not be able to land unresolved.

    Naming the collections in the evaluator only protects the ones someone
    remembered to name. This asks the manifest what it carries and asserts the
    evaluator consulted each one, so adding a field without wiring it fails here
    rather than silently widening what COMPLETE overlooks.
    """

    manifest = evidence_manifest(context)
    declared = tuple(
        name
        for name in manifest.to_dict()
        if name.endswith("_refs") and manifest.to_dict()[name]
    )
    assert len(declared) == 6, f"the manifest carries {declared}; keep this test in step"

    consulted: list[str] = []
    K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots,
            resolver=lambda item: consulted.append(item.ref_id) or True,
        ),
        manifest=manifest,
    )
    for name in declared:
        for entry in manifest.to_dict()[name]:
            assert entry["ref_id"] in consulted, (
                f"{name} was declared by the manifest and never resolved; a snapshot "
                "can reach COMPLETE with it dangling"
            )


def test_the_compatibility_evidence_root_is_derived_from_the_manifest(context, tmp_path) -> None:
    """Derived, not supplied — so there is no second source to disagree.

    The commit used to accept this root as an argument and check only that it
    looked like a sha256. The boundary therefore attested a fact nobody had
    established, and any digest at all would have been written into it.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots), manifest=manifest
    )
    boundary = commit(root, manifest, decision)
    assert boundary.compatibility_evidence_root_sha256 == K.compatibility_evidence_root(manifest)
    assert boundary.compatibility_evidence_root_sha256 != COMPAT_ROOT


def test_the_compatibility_root_moves_with_the_evidence_and_with_its_order(context) -> None:
    """Different evidence, and the same evidence in a different order, differ.

    Order matters because two snapshots holding the same records in a different
    order are different states; a root that collapsed them would let one be
    presented as the other.
    """

    base = evidence_manifest(context)
    without_conflict = K.create_snapshot_manifest(
        context=context,
        roots=base.roots,
        behavior_refs=base.behavior_refs,
        binding_refs=base.binding_refs,
        attestation_refs=base.attestation_refs,
        admission_refs=base.admission_refs,
        retrieval_decision_refs=base.retrieval_decision_refs,
        conflict_refs=(),
        created_at_utc=NOW,
    )
    assert K.compatibility_evidence_root(base) != K.compatibility_evidence_root(without_conflict)


def test_the_same_records_filed_the_other_way_round_are_a_different_state(context) -> None:
    """Order-sensitivity tested through the function, not beside it.

    An earlier version of this test built the chain by hand and compared two
    hand-built orders. That checks arithmetic, not the code: a mutant that sorts
    the evidence *inside* ``compatibility_evidence_root`` survived it, because
    the hand-built comparison never called the function.

    The separating case is the same two records filed the other way round. A
    record entered as a retrieval decision and a record entered as a conflict
    are different claims about the snapshot, so swapping them must move the
    root. Any implementation that sorts the evidence collapses the two into one
    value and lets either be presented as the other.
    """

    # Distinct payloads, and deliberately in the order sorting would reverse: a
    # first attempt gave both refs the default payload, so their digests were
    # equal, ``sorted`` was stable, and the mutant that sorts was indisponible
    # from the real code. The assertion that was supposed to establish
    # distinctness compared a pair with its own permutation and passed either
    # way — a check that checked nothing.
    left = ref(RefKind.GATE_DECISION, "evidence-a", b"evidence-a")
    right = ref(RefKind.GATE_DECISION, "evidence-b", b"evidence-b")
    assert left.sha256 > right.sha256, "the fixture must be in the order sorting would change"
    one = K.create_snapshot_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(left,), conflict_refs=(right,), created_at_utc=NOW,
    )
    other = K.create_snapshot_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(right,), conflict_refs=(left,), created_at_utc=NOW,
    )
    assert K.compatibility_evidence_root(one) != K.compatibility_evidence_root(other)


def test_an_admission_root_the_journal_does_not_confirm_is_refused(context, tmp_path) -> None:
    """The boundary must not attest an admission history that never existed."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, decision, admission_root=ADMISSION_ROOT)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ADMISSION_ROOT_UNCONFIRMED


def test_an_admission_root_still_confirms_after_the_journal_grows(context, tmp_path) -> None:
    """Growth is legal; only a fork is not.

    The journal moves on between the moment a snapshot's admission evidence was
    gathered and the moment its boundary commits. Demanding equality would refuse
    every real commit that raced an ordinary append.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    journal = admission_journal(root)
    witnessed = journal.current_anchor()
    journal.append_record(b"a later unrelated decision")
    assert journal.current_anchor() != witnessed

    manifest = make_manifest(context)
    boundary = commit(
        root, manifest, complete_decision(manifest), journal=journal, admission_root=witnessed
    )
    assert boundary.admission_root_sha256 == witnessed


def test_a_port_that_reports_an_outage_keeps_its_classification(context, tmp_path) -> None:
    """A port speaking this owner's vocabulary is believed, not re-guessed."""

    class Unreachable:
        def current_anchor(self) -> str:
            raise K.KnowledgeViolation(
                K.KnowledgeFailureCode.STORE_UNAVAILABLE, "journal is down"
            )

        def extends(self, anchor: str) -> bool:
            raise K.KnowledgeViolation(
                K.KnowledgeFailureCode.STORE_UNAVAILABLE, "journal is down"
            )

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Unreachable(), admission_root=ADMISSION_ROOT,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.STORE_UNAVAILABLE


def test_a_failure_this_owner_cannot_recognise_is_not_called_an_outage(
    context, tmp_path
) -> None:
    """The regression this round reverses.

    §21 may not import the admission package, so it cannot recognise that
    package's exception types. Reporting an unrecognised failure as
    ``STORE_UNAVAILABLE`` told an operator the store was unreachable and the
    commit could be retried, when the truth might be a corrupt journal that no
    retry will fix — the substitution NR-10 exists to forbid.
    """

    class Foreign:
        def current_anchor(self) -> str:
            raise RuntimeError("something this owner has never heard of")

        def extends(self, anchor: str) -> bool:
            raise RuntimeError("something this owner has never heard of")

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Foreign(), admission_root=ADMISSION_ROOT,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED
    assert excinfo.value.failure_code is not K.KnowledgeFailureCode.STORE_UNAVAILABLE


@pytest.mark.parametrize(
    "arrange,expected",
    [
        ("corrupt", "ADMISSION_HISTORY_CORRUPT"),
        ("unreachable", "STORE_UNAVAILABLE"),
    ],
)
def test_the_wrapped_journal_reports_corruption_and_outage_apart(
    tmp_path, arrange, expected
) -> None:
    """`FileAdmissionJournal` keeps the two apart; the wrapper carries that across.

    Raw, its `JournalAdapterViolation` is a type §21 cannot name, so a corrupt
    store arrives unclassified. Wrapped, the distinction the journal took care to
    make survives the boundary between the two owners.
    """

    from synapse.experiments.gold.gate_findings import SnapshotAdmissionHistory

    if arrange == "corrupt":
        directory = tmp_path / "adm"
        directory.mkdir()
        (directory / "decisions.journal").write_bytes(b"somebody else's file entirely")
        path = directory / "decisions.journal"
    else:
        blocked = tmp_path / "blocked"
        blocked.write_bytes(b"not a directory")
        path = blocked / "adm" / "decisions.journal"

    # The fence is rooted outside the arranged damage on purpose. This test asks
    # how the *journal* reports corruption and an outage, and a fence that was
    # itself unreachable would make the refusal arrive for a reason the assertion
    # does not name.
    wrapped = SnapshotAdmissionHistory(FileAdmissionJournal(path, fence_for(tmp_path)))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        wrapped.current_anchor()
    assert excinfo.value.failure_code.value == expected


@pytest.mark.parametrize("substitute", [None, object(), "journal"])
def test_a_commit_requires_something_shaped_like_an_admission_journal(
    context, tmp_path, substitute
) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=substitute, admission_root=ADMISSION_ROOT,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def test_a_journal_answering_extends_with_the_wrong_type_is_a_broken_adapter(
    context, tmp_path
) -> None:
    """A truthy non-bool must not be read as confirmation."""

    class Sloppy:
        def current_anchor(self) -> str:
            return ADMISSION_ROOT

        def extends(self, anchor: str) -> object:
            return "yes"

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Sloppy(), admission_root=ADMISSION_ROOT,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def test_a_sequence_gap_between_boundaries_is_refused(context, tmp_path) -> None:
    """A child begins exactly where its parent ended.

    This test previously asserted the opposite, on my reasoning that lineage
    travels in ``parent_boundary_digest`` so the numbers need only order. That is
    wrong in the part that matters: the digest proves *which* parent, not that
    nothing went unrecorded in between. §21 specifies a monotonic transaction
    range with "gaps/forks/rollback detected", and a gap that is accepted is a
    gap that is not detected.

    Both directions are refused here — a gap forward and a step backward — so the
    rule is pinned as equality rather than as an inequality that happens to hold.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    parent = commit(root, manifest, complete_decision(manifest))

    child_manifest = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )

    for label, start, commit_sequence in (
        ("a gap forward", 1000, 1001),
        ("a step backward", 0, 1),
        ("one past the parent", parent.commit_sequence + 1, parent.commit_sequence + 2),
    ):
        with pytest.raises(K.KnowledgeViolation) as excinfo:
            commit(
                root, child_manifest, complete_decision(child_manifest),
                transaction_id=f"tx-{start}", start=start,
                commit_sequence=commit_sequence, parent=parent,
            )
        assert excinfo.value.failure_code is K.KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC, label

    # The contiguous child commits, so the three refusals above are not a
    # blanket refusal of every derived boundary.
    child = commit(
        root, child_manifest, complete_decision(child_manifest),
        transaction_id="tx-next", start=parent.commit_sequence,
        commit_sequence=parent.commit_sequence + 1, parent=parent,
    )
    assert child.start_sequence == parent.commit_sequence
    assert child.parent_boundary_digest == parent.atomic_boundary_id.digest_sha256


# ---------------------------------------------------------------------------
# Round 16 — the on-disk adversary, and the §22 boundary probe
# ---------------------------------------------------------------------------


MARKER_NAME = "commit-marker.json"


def rewrite_marker(root: Path, transaction_id: str, member: str, payload: bytes) -> None:
    """Replace a committed member *and* the digest the marker records for it.

    The adversary who only edits a member is already refused: the marker pins a
    digest per member and the mismatch is corruption. This is the next adversary
    along — one who can rewrite the marker too — and everything §21 checks after
    persistence is satisfied has to stand on its own against them.
    """

    directory = root / transaction_id
    path = directory / member
    path.chmod(0o600)
    path.write_bytes(payload)
    marker_path = directory / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    for entry in marker["members"]:
        if entry["member_name"] == member:
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["byte_length"] = len(payload)
    marker_path.chmod(0o600)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")


def test_a_rewritten_manifest_with_a_matching_marker_is_still_refused(context, tmp_path) -> None:
    """Persistence satisfied, and the snapshot still does not open.

    The existing mutated-bytes test stops at the layer below: it edits a member
    and the recorded digest catches it. That proves persistence works, not that
    §21 does. Here the marker is rewritten to agree with the tampered bytes, so
    every integrity check passes and §21's own bindings are the only thing left
    standing between the adversary and a usable snapshot.

    The binding that catches it is the decision's: a completeness decision names
    the manifest it was reached over, and the planted manifest is not that one.
    That happens at decode, before the marker is consulted at all — which is why
    this test cannot also stand in for the marker check, and why the marker gets
    a test of its own below.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))

    other = K.create_snapshot_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-9"),),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    rewrite_marker(root, "tx-1", K.MANIFEST_MEMBER_NAME, other.canonical_bytes())

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_a_boundary_moved_into_another_transaction_is_refused(context, tmp_path) -> None:
    """Two committed snapshots, and one's boundary planted in the other.

    Every byte is genuine — the boundary really was committed, just not here.
    Without the transaction-id binding the planted boundary would verify against
    its own manifest hash and marker, and the two transactions would become
    interchangeable.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    commit(root, second, complete_decision(second), transaction_id="tx-2", start=3, commit_sequence=4)

    planted = (root / "tx-2" / K.BOUNDARY_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.BOUNDARY_MEMBER_NAME, planted)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH


def test_a_decision_swapped_after_the_marker_is_refused(context, tmp_path) -> None:
    """A decision that describes another snapshot is refused as it is decoded."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    other = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    commit(root, other, complete_decision(other), transaction_id="tx-2", start=3, commit_sequence=4)

    planted = (root / "tx-2" / K.DECISION_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.DECISION_MEMBER_NAME, planted)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_an_untouched_committed_snapshot_still_opens(context, tmp_path) -> None:
    """Guards the three adversaries above from passing because nothing opens."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    restored = K.open_usable_snapshot(root, transaction_id="tx-1")
    assert restored.boundary.atomic_boundary_id.value == boundary.atomic_boundary_id.value


def test_a_boundary_is_named_by_its_own_identity(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    reference = K.atomic_boundary_ref(boundary)
    assert reference.kind is RefKind.ATOMIC_BOUNDARY
    assert reference.ref_id == boundary.atomic_boundary_id.digest_sha256
    assert reference.sha256 == hashlib.sha256(boundary.canonical_bytes()).hexdigest()


def boundary_probe_for(root: Path, boundary, context):
    from synapse.experiments.gold.gate_findings import configured_boundary_probe

    return configured_boundary_probe(
        root=root,
        transaction_id=boundary.transaction_id,
        attempt_boundary_id=boundary.atomic_boundary_id,
        expected_context=context,
    )


def test_the_boundary_probe_confirms_the_committed_boundary(context, tmp_path) -> None:
    """The §22 gate's boundary answer now comes from a committed §21 snapshot.

    ``configure_gate_controller`` took a ``boundary_probe`` and every caller
    supplied a callable of its own, while ``open_usable_snapshot`` and
    ``require_usable_snapshot`` had no caller outside the tests. The gate asked
    *something* whether a boundary was committed and nothing required that
    something to have read one.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    assert probe(K.atomic_boundary_ref(boundary)) is True


def test_the_boundary_probe_denies_a_reference_to_another_boundary(context, tmp_path) -> None:
    """Verified, and simply not the boundary this attempt is bound to."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    boundary = commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    other = commit(root, second, complete_decision(second), transaction_id="tx-2", start=3, commit_sequence=4)

    probe = boundary_probe_for(root, boundary, context)
    assert probe(K.atomic_boundary_ref(other)) is False


def test_the_boundary_probe_reads_the_store_at_the_moment_it_is_asked(context, tmp_path) -> None:
    """Wiring-time verification would describe a moment that has passed.

    The probe is built while the snapshot is intact and asked after the committed
    bytes have been rewritten under it, marker and all. A probe that verified once
    at construction would still answer yes.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    assert probe(K.atomic_boundary_ref(boundary)) is True

    other = K.create_snapshot_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-9"),),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    rewrite_marker(root, "tx-1", K.MANIFEST_MEMBER_NAME, other.canonical_bytes())

    with pytest.raises(K.KnowledgeViolation):
        probe(K.atomic_boundary_ref(boundary))


def test_a_damaged_snapshot_is_not_reported_as_a_different_boundary(context, tmp_path) -> None:
    """Damage and mismatch are different facts and must not share an answer."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)

    member = root / "tx-1" / K.MANIFEST_MEMBER_NAME
    member.chmod(0o600)
    member.write_bytes(b'{"tampered": true}')
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(K.atomic_boundary_ref(boundary))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED


def test_an_unreadable_boundary_store_is_declared_as_a_gate_outage(context, tmp_path) -> None:
    """The gate classifies by exception type, so an outage must arrive as one."""

    from synapse.experiments.gold.admission import GateDependencyUnavailable

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)

    # The whole committed transaction goes away, which is the store being
    # unreadable rather than the snapshot being wrong. Answering False here
    # would tell the gate "that is a different boundary", which is a claim
    # nobody is in a position to make.
    shutil.rmtree(root / "tx-1")
    with pytest.raises(GateDependencyUnavailable):
        probe(K.atomic_boundary_ref(boundary))


def test_the_boundary_probe_refuses_a_foreign_consumer_context(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    foreign = K.create_knowledge_context(
        repository_revision="b" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    probe = boundary_probe_for(root, boundary, foreign)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(K.atomic_boundary_ref(boundary))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


@pytest.mark.parametrize("substitute", [None, object(), "boundary"])
def test_the_boundary_probe_requires_an_exact_reference(context, tmp_path, substitute) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(substitute)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def edit_marker(root: Path, transaction_id: str, **fields) -> None:
    """Change marker fields while leaving the member digests correct.

    The member digests are what persistence checks; these fields are what §21
    checks. Editing them separately is how each §21 binding gets a case that
    reaches it, instead of a case that trips whichever guard happens to run
    first.
    """

    marker_path = root / transaction_id / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker.update(fields)
    marker_path.chmod(0o600)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")


def test_a_marker_naming_another_boundary_is_refused(context, tmp_path) -> None:
    """Isolated: only the marker's boundary id is wrong."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    other = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    elsewhere = commit(
        root, other, complete_decision(other), transaction_id="tx-2", start=3, commit_sequence=4
    )
    edit_marker(root, "tx-1", boundary_id=elsewhere.atomic_boundary_id.value)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "names a different boundary" in excinfo.value.detail


def test_a_marker_hash_that_differs_from_the_boundary_is_refused(context, tmp_path) -> None:
    """Isolated: only the marker's own hash is wrong."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    edit_marker(root, "tx-1", marker_sha256=hashlib.sha256(b"not the marker").hexdigest())

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "commit marker hash differs" in excinfo.value.detail


def test_a_boundary_from_another_transaction_is_refused_even_with_a_matching_marker(
    context, tmp_path
) -> None:
    """The adversary controls the marker completely, and the id still refuses.

    Everything is made consistent: the planted boundary's own id and marker hash
    are written into the marker, so the two checks before this one pass by
    construction. What the planted boundary cannot change is which transaction it
    was committed under.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    other = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    elsewhere = commit(
        root, other, complete_decision(other), transaction_id="tx-2", start=3, commit_sequence=4
    )

    planted = (root / "tx-2" / K.BOUNDARY_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.BOUNDARY_MEMBER_NAME, planted)
    edit_marker(
        root, "tx-1",
        boundary_id=elsewhere.atomic_boundary_id.value,
        marker_sha256=elsewhere.commit_marker,
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "transaction id differs" in excinfo.value.detail


def test_a_consistent_pair_from_another_snapshot_breaks_the_commit_binding(
    context, tmp_path
) -> None:
    """The case only the commit marker catches.

    Both members are replaced together by another transaction's genuine pair, so
    the decision does describe its manifest and the decode-time binding is
    satisfied. The boundary is untouched, so its id and marker hash still agree
    with the marker. The one thing left that can tell the truth is the marker the
    boundary recorded over the bytes it actually committed.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    other = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=99))
    commit(root, other, complete_decision(other), transaction_id="tx-2", start=3, commit_sequence=4)

    rewrite_marker(
        root, "tx-1", K.MANIFEST_MEMBER_NAME,
        (root / "tx-2" / K.MANIFEST_MEMBER_NAME).read_bytes(),
    )
    rewrite_marker(
        root, "tx-1", K.DECISION_MEMBER_NAME,
        (root / "tx-2" / K.DECISION_MEMBER_NAME).read_bytes(),
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_usable_snapshot(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "not the ones this boundary marked" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 18 — minting the frozen candidate set from a committed snapshot
#
# `gate_findings.frozen_candidates_from_snapshot` is what turns a committed §21
# boundary into the set retrieval is allowed to consider. Three mutants in the
# round-18 campaign survived because nothing here exercised the refusal side:
# the mint skipping `require_usable_snapshot` entirely, a manifest whose behavior
# refs are not library subject names being accepted, and a snapshot that selected
# no behavior being reported as constraining something.
# ---------------------------------------------------------------------------


def subject_ref(name: str) -> HashBoundRef:
    """A §22 library subject name, built without a library.

    ``library_subject_ref`` needs only the four identity values, which is the
    property that lets the write side compute the name the read side will compute
    later. It also lets this suite build a snapshot that genuinely constrains
    retrieval without paying for a real library and a real git repository.
    """

    from synapse.experiments.gold.canonicalization import library_subject_ref

    blob = hashlib.sha256(f"frozen-blob-{name}".encode()).hexdigest()
    manifest = hashlib.sha256(f"frozen-manifest-{name}".encode()).hexdigest()
    return library_subject_ref(
        content_key=f"synapse.stage4.gold.content-key/v1:{blob}",
        manifest_id=f"BEHAVIOR_MANIFEST:{manifest}",
        blob_digest_sha256=blob,
        manifest_digest_sha256=manifest,
    )


def _canonically_ordered(refs) -> tuple[HashBoundRef, ...]:
    return tuple(
        sorted(refs, key=lambda item: f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}")
    )


def subject_manifest(
    context: K.KnowledgeContext,
    *,
    subjects: tuple[str, ...] = ("alpha", "beta"),
    behavior_refs=None,
    binding_refs=(),
    roots: K.SnapshotRootSet | None = None,
) -> K.SnapshotManifest:
    """A manifest whose behavior refs are library subject names."""

    return K.create_snapshot_manifest(
        context=context,
        roots=roots or make_roots(),
        behavior_refs=(
            _canonically_ordered(subject_ref(name) for name in subjects)
            if behavior_refs is None
            else behavior_refs
        ),
        binding_refs=binding_refs,
        attestation_refs=(),
        admission_refs=(),
        retrieval_decision_refs=(),
        conflict_refs=(),
        created_at_utc=NOW,
    )


def _committed(root: Path, manifest, *, transaction_id="tx-frozen", start=1, commit_sequence=2):
    ensure_directory(root)
    commit(
        root, manifest, complete_decision(manifest),
        transaction_id=transaction_id, start=start, commit_sequence=commit_sequence,
    )
    return K.open_usable_snapshot(root, transaction_id=transaction_id)


def _mint(snapshot, *, context, boundary_id=None):
    from synapse.experiments.gold import gate_findings as GF

    return GF.frozen_candidates_from_snapshot(
        snapshot,
        attempt_boundary_id=(
            snapshot.boundary.atomic_boundary_id if boundary_id is None else boundary_id
        ),
        expected_context=context,
        frozen_at_utc=NOW,
    )


def test_a_frozen_set_names_exactly_the_subjects_the_snapshot_froze(context, tmp_path) -> None:
    """The set is derived from the manifest, not supplied alongside it."""

    manifest = subject_manifest(context)
    snapshot = _committed(tmp_path / "boundaries", manifest)
    frozen = _mint(snapshot, context=context)

    expected = tuple(
        sorted(
            f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}"
            for item in manifest.behavior_refs
        )
    )
    assert frozen.subject_ref_keys == expected
    assert frozen.boundary_id_sha256 == snapshot.boundary.atomic_boundary_id.digest_sha256
    assert frozen.snapshot_id_sha256 == manifest.snapshot_id.digest_sha256
    assert expected[0] in frozen


def test_a_frozen_set_cannot_be_minted_for_another_attempt_boundary(context, tmp_path) -> None:
    """One attempt consumes one boundary, and the mint is where that is checked.

    Minting without re-verifying would let an attempt bound to one boundary
    receive the frozen world of another — a silent substitution of what the run
    is allowed to know, with every individual record still valid.
    """

    root = tmp_path / "boundaries"
    first = _committed(root, subject_manifest(context))
    second = _committed(
        root,
        subject_manifest(context, subjects=("gamma",), roots=make_roots(library=9, index=9)),
        transaction_id="tx-frozen-2", start=3, commit_sequence=4,
    )
    assert first.boundary.atomic_boundary_id.value != second.boundary.atomic_boundary_id.value

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(first, context=context, boundary_id=second.boundary.atomic_boundary_id)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_a_frozen_set_cannot_be_minted_against_a_foreign_consumer_context(context, tmp_path) -> None:
    """A snapshot taken of one repository state does not constrain another."""

    snapshot = _committed(tmp_path / "boundaries", subject_manifest(context))
    foreign = K.create_knowledge_context(
        repository_revision="b" * 40,
        policy_version="policy-v1",
        environment_profile_id="env-1",
    )
    assert foreign.repository_revision != context.repository_revision

    with pytest.raises(K.KnowledgeViolation):
        _mint(snapshot, context=foreign)


def test_a_frozen_set_cannot_be_minted_from_an_unsealed_snapshot(context, tmp_path) -> None:
    """Re-verification happens at the mint, not once when the snapshot was opened.

    The record here is a genuinely committed snapshot whose seal was removed
    afterwards — every field still verifies, so only the freshness check the mint
    performs stands between it and a frozen set.
    """

    snapshot = _committed(tmp_path / "boundaries", subject_manifest(context))
    object.__setattr__(snapshot, "_trusted_seal", object())

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(snapshot, context=context)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TRUSTED_OBJECT_FORGED


def test_a_snapshot_whose_behavior_refs_are_not_library_subjects_constrains_nothing(
    context, tmp_path
) -> None:
    """Refused at the point of use, not accepted with a set that matches nothing.

    `behavior_refs` is kind-constrained but not schema-constrained, so a manifest
    may carry refs that are not library subject names. Such a snapshot cannot
    constrain retrieval at all — every live entry would fail to match — and
    accepting it would silently permit everything instead of refusing once.
    """

    manifest = make_manifest(context)
    assert all(
        item.schema_id != "synapse.stage4.gold.library-subject/v1"
        for item in manifest.behavior_refs
    )
    snapshot = _committed(tmp_path / "boundaries", manifest)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(snapshot, context=context)
    assert "library subject names" in excinfo.value.detail


def test_a_snapshot_that_selected_no_behavior_constrains_nothing(context, tmp_path) -> None:
    """An empty behavior selection is a refusal, not an empty frozen set.

    A manifest is valid with no behavior refs as long as it selects some binding,
    and a frozen set built from *bindings* would name objects the library index
    cannot offer while claiming to constrain the run.
    """

    manifest = subject_manifest(
        context, behavior_refs=(), binding_refs=(ref(RefKind.BINDING, "binding-1"),)
    )
    assert manifest.behavior_refs == ()
    snapshot = _committed(tmp_path / "boundaries", manifest)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(snapshot, context=context)
    assert "no selected behavior" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 19 — a boundary names the parent it actually extends
# ---------------------------------------------------------------------------


def test_a_child_declaring_another_lineage_is_refused(context, tmp_path) -> None:
    """Declaring *a* parent is not descending from *this* one.

    Everything else about this commit is in order — the contexts match, no root
    regresses, and round 17's contiguity rule lines the sequence numbers up
    exactly. Only the declared parent belongs to a different lineage, and until
    now nothing compared it to the boundary being extended. That is the fork §21
    claims to detect, and the contiguity rule made it harder to see rather than
    closing it: the numbers now agree while the identities need not.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))

    stranger = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    assert stranger.snapshot_id.value != first.snapshot_id.value

    child = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=44), parent=stranger.snapshot_id
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, child, complete_decision(child),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.LINEAGE_MISMATCH
    assert "other than the boundary it extends" in excinfo.value.detail


def test_the_same_child_commits_when_it_names_the_boundary_it_extends(context, tmp_path) -> None:
    """The control. Without it, the refusal above proves only that something said no.

    Identical in every respect to the previous commit except the declared
    parent, so the refusal there is shown to follow from the lineage comparison
    and from nothing else in the fixture.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))

    child = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=44), parent=first.snapshot_id
    )
    boundary = commit(
        root, child, complete_decision(child),
        transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
    )
    assert boundary.parent_boundary_digest == first_boundary.atomic_boundary_id.digest_sha256
    assert child.parent_snapshot_digest == first_boundary.manifest_ref.ref_id


def test_a_genesis_commit_refuses_a_manifest_that_claims_descent(context, tmp_path) -> None:
    """The other half of the same rule.

    A manifest claiming a parent while being committed as genesis would put an
    unverifiable ancestor into the permanent record: nothing at this point can
    confirm that snapshot was ever committed, and a lineage that cannot be
    walked is not a lineage. `PARTIAL_MANIFEST` would be the wrong answer — the
    manifest omitted nothing, it named something.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    orphan = make_manifest(context)
    claimant = make_manifest(
        context, roots=make_roots(library=8, index=8, lifecycle=43), parent=orphan.snapshot_id
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, claimant, complete_decision(claimant))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.LINEAGE_MISMATCH
    assert "without one" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 19 — the roots are observed as one moment or not at all
# ---------------------------------------------------------------------------


def test_a_root_observation_torn_by_a_concurrent_mutation_is_refused(context, tmp_path) -> None:
    """One call is not one instant, and this is the case that proves it.

    The three roots come from three stores. Before this round they were read
    through one callable and the result was treated as an instant, so a library
    root from before a publish could sit beside a lifecycle root from after it —
    a set describing no world that ever existed, in which every individual value
    validates. That is why validating the result cannot detect it and why the
    detection has to happen while the read is in progress.

    Here a real store mutation lands mid-read: the provider bumps the shared fence
    while it is producing the roots, exactly as a concurrent publisher would. The
    roots it returns are the ones the manifest declares, so nothing downstream has
    anything to object to — only the epoch says the observation is not of one
    moment.
    """

    fence = FileSnapshotFence(tmp_path / "torn-fence")
    manifest = make_manifest(context)

    def mutating_provider():
        # A store advancing the shared epoch is precisely what a concurrent write
        # does now that every owner is fenced.
        fence.bump()
        return manifest.roots

    evaluator = make_evaluator(observed=mutating_provider, root_fence=fence)
    decision = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    assert decision.status is SnapshotCompletenessStatus.OBSERVATION_TORN
    assert "while the roots were being observed" in decision.detail
    with pytest.raises(ContractViolation):
        require_snapshot_status_admits_execution(decision.status)


def test_the_same_roots_read_without_interference_are_complete(context, tmp_path) -> None:
    """The control, and it is not optional.

    Without it the refusal above shows only that something said no. The roots,
    the manifest and the evaluator are identical; the single difference is that
    nothing mutates during the read.
    """

    fence = FileSnapshotFence(tmp_path / "quiet-fence")
    manifest = make_manifest(context)
    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=fence)

    decision = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_a_torn_observation_is_not_reported_as_an_unreachable_store(context, tmp_path) -> None:
    """Two conditions, two answers, and the operator actions differ.

    An unreachable store is an outage: retry it. A torn observation means every
    store answered and the answers disagree about when — retrying is right, but
    the fault is contention rather than availability, and an operator told
    "unreachable" would go looking at the wrong thing. NR-10 forbids the
    substitution in either direction, so both statuses are asserted against the
    same fixture rather than one being assumed to imply the other.
    """

    manifest = make_manifest(context)

    def unavailable_provider():
        raise RuntimeError("the store is not reachable")

    unreachable = K.evaluate_snapshot_completeness(
        make_evaluator(observed=unavailable_provider), manifest=manifest
    )
    assert unreachable.status is SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE

    fence = FileSnapshotFence(tmp_path / "torn")

    def mutating_provider():
        fence.bump()
        return manifest.roots

    torn = K.evaluate_snapshot_completeness(
        make_evaluator(observed=mutating_provider, root_fence=fence), manifest=manifest
    )
    assert torn.status is SnapshotCompletenessStatus.OBSERVATION_TORN
    assert torn.status is not unreachable.status


def test_an_evaluator_cannot_be_configured_without_a_root_fence() -> None:
    """An optional fence is the bypass, so the barrier lives in the signature."""

    with pytest.raises(TypeError):
        K.configure_snapshot_evaluator(
            authority_handle=authority_handle(),
            authority_identity=AuthorityIdentity(value="platform-evaluator"),
            authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
            trusted_clock=lambda: NOW,
            observed_roots_provider=lambda: make_roots(),
            ref_resolver=lambda item: True,
            consumability_probe=lambda item: True,
            producer_actor=ActorIdentity(value="snapshot-producer"),
        )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        make_evaluator(root_fence=object())
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH
