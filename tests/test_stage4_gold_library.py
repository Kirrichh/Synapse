from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as canonicalization_module
from synapse.experiments.gold import library as library_module
from synapse.experiments.gold.behavior import (
    BehaviorBlob,
    BehaviorCore,
    BehaviorManifest,
    SynapseBehaviorUnit,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    LIBRARY_RETENTION_ROOTS_V1,
    BehaviorLibrary,
    LibraryFailureCode,
    LibraryObjectNamespace,
    LibraryObjectRef,
    LibraryViolation,
    PublisherIdentity,
    PutResult,
    PutStatus,
    RetentionRootKind,
    RetentionRootSet,
    SnapshotVerificationStatus,
    SnapshotVerification,
    VerifiedBehaviorRecord,
    validate_verified_behavior_record,
)
from synapse.experiments.gold.persistence import (
    JOURNAL_FRAME_MAGIC_V1,
    encode_journal_frame,
    scan_journal,
)


_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _publisher() -> PublisherIdentity:
    return PublisherIdentity(
        LIBRARY_PUBLISHER_IDENTITY_V1,
        "stage4-library-publisher",
        "synapse.stage4.gold.publisher-policy/v1",
    )


def _behavior(*, output_name: str = "result") -> tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest]:
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(vectors["vectors"][0]["core"])
    payload["output_contract"]["fields"][0]["name"] = output_name
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=None)
    return unit, blob, manifest


def _store(tmp_path: Path, publisher: PublisherIdentity) -> tuple[Path, BehaviorLibrary]:
    root = tmp_path / "library"
    root.mkdir()
    return root, BehaviorLibrary(root, publisher_identity=publisher)


def _object_path(root: Path, namespace: LibraryObjectNamespace, digest: str) -> Path:
    directory = "blobs" if namespace is LibraryObjectNamespace.BLOB else "manifests"
    return root / "objects" / directory / digest[:2] / digest[2:]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _failure(exc: pytest.ExceptionInfo[LibraryViolation]) -> LibraryFailureCode:
    return exc.value.failure_code


def _root_sets(
    selected_kind: RetentionRootKind,
    selected_refs: tuple[LibraryObjectRef, ...],
) -> tuple[RetentionRootSet, ...]:
    return tuple(
        RetentionRootSet(
            LIBRARY_RETENTION_ROOTS_V1,
            kind,
            selected_refs if kind is selected_kind else (),
        )
        for kind in RetentionRootKind
    )


def test_s4_p4_acc_library_01_put_get_deduplicate_and_restart_return_reverified_exact_bytes(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()

    stored = library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    assert stored.status is PutStatus.STORED
    blob_path = _object_path(root, LibraryObjectNamespace.BLOB, unit.content_key.digest_sha256)
    manifest_path = _object_path(root, LibraryObjectNamespace.MANIFEST, manifest.manifest_id.digest_sha256)
    blob_bytes = blob_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    journal_bytes = (root / "journal" / "library.v1").read_bytes()

    loaded = library.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert loaded.unit.to_dict() == unit.to_dict()
    assert loaded.blob.canonical_core_bytes == blob.canonical_core_bytes == blob_bytes
    assert loaded.manifest.to_dict(unit=loaded.unit, blob=loaded.blob) == manifest.to_dict(unit=unit, blob=blob)

    duplicate = library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    assert duplicate.status is PutStatus.DEDUPLICATED
    assert blob_path.read_bytes() == blob_bytes
    assert manifest_path.read_bytes() == manifest_bytes
    assert (root / "journal" / "library.v1").read_bytes() == journal_bytes

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    reopened_record = reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert reopened_record.blob.canonical_core_bytes == blob_bytes
    assert reopened_record.manifest.manifest_id.value == manifest.manifest_id.value


def test_s4_p4_acc_library_02_only_the_configured_platform_publisher_instance_can_write(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    _, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    before = _tree_bytes(library.root)

    with pytest.raises(LibraryViolation) as exc:
        library.put_behavior(unit, blob, manifest, publisher_identity=object())  # type: ignore[arg-type]
    assert _failure(exc) is LibraryFailureCode.WORKER_WRITE_FORBIDDEN
    assert _tree_bytes(library.root) == before

    equal_but_untrusted = PublisherIdentity.from_dict(publisher.to_dict())
    assert equal_but_untrusted == publisher and equal_but_untrusted is not publisher
    with pytest.raises(LibraryViolation) as exc:
        library.put_behavior(unit, blob, manifest, publisher_identity=equal_but_untrusted)
    assert _failure(exc) is LibraryFailureCode.PUBLISHER_MISMATCH
    assert _tree_bytes(library.root) == before


def test_s4_p4_acc_library_03_verified_get_recomputes_content_identity_with_a_valid_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _publisher()
    _, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    actual_compute = library_module.compute_content_key
    calls = 0

    def observed_compute(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return actual_compute(**kwargs)

    monkeypatch.setattr(library_module, "compute_content_key", observed_compute)
    loaded = library.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert loaded.blob.canonical_core_bytes == blob.canonical_core_bytes
    assert calls >= 1


def test_s4_p4_acc_library_04_verified_results_cannot_be_forged_by_direct_construction(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    _, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    stored = library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    verified = library.get_verified_behavior(unit.content_key, manifest.manifest_id)

    with pytest.raises(TypeError):
        PutResult(PutStatus.STORED, unit.content_key, manifest.manifest_id, stored.operation_id)
    with pytest.raises(TypeError):
        VerifiedBehaviorRecord(unit, blob, manifest)
    with pytest.raises(TypeError):
        SnapshotVerification(SnapshotVerificationStatus.UNANCHORED, library.current_snapshot().snapshot)
    with pytest.raises(TypeError):
        replace(stored, status=PutStatus.DEDUPLICATED)

    forged = object.__new__(VerifiedBehaviorRecord)
    object.__setattr__(forged, "unit", verified.unit)
    object.__setattr__(forged, "blob", verified.blob)
    object.__setattr__(forged, "manifest", verified.manifest)
    with pytest.raises(LibraryViolation) as exc:
        validate_verified_behavior_record(forged)
    assert _failure(exc) is LibraryFailureCode.TYPE_MISMATCH


def test_s4_p4_acc_library_05_poisoned_index_is_discarded_and_rebuilt_from_verified_objects(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    expected_blob = blob.canonical_core_bytes

    index_path = root / "metadata" / "index.v1"
    poisoned = canonicalize_stage4_payload(
        {
            "schema_version": "synapse.stage4.gold.library-index/v1",
            "index_version": 1,
            "generation": 999,
            "entries": [],
        },
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    index_path.write_bytes(poisoned)

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    loaded = reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert loaded.blob.canonical_core_bytes == expected_blob
    assert len(reopened.search_index()) == 1
    assert index_path.read_bytes() != poisoned

    index_path.write_bytes(b"not-canonical-index-data")
    reopened_again = BehaviorLibrary(root, publisher_identity=publisher)
    assert (
        reopened_again.get_verified_behavior(unit.content_key, manifest.manifest_id).blob.canonical_core_bytes
        == expected_blob
    )
    assert len(reopened_again.search_index()) == 1


def test_s4_p4_acc_library_06_corrupted_blob_is_quarantined_and_never_consumed(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, _, manifest = _behavior()
    library.put_behavior(unit, create_behavior_blob(unit), manifest, publisher_identity=publisher)
    trusted_before_corruption = library.current_snapshot().snapshot
    blob_path = _object_path(root, LibraryObjectNamespace.BLOB, unit.content_key.digest_sha256)
    corrupt_bytes = b"corrupted canonical core"
    blob_path.write_bytes(corrupt_bytes)

    with pytest.raises(LibraryViolation) as exc:
        library.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert _failure(exc) is LibraryFailureCode.OBJECT_QUARANTINED
    assert not blob_path.exists()
    quarantine_payloads = [
        path.read_bytes()
        for path in (root / "quarantine" / "payloads").rglob("*")
        if path.is_file()
    ]
    quarantine_records = [path for path in (root / "quarantine" / "records").rglob("*") if path.is_file()]
    assert corrupt_bytes in quarantine_payloads
    assert quarantine_records
    assert (
        library.current_snapshot(trusted_prior=trusted_before_corruption).status
        is SnapshotVerificationStatus.VERIFIED_FORWARD
    )

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    with pytest.raises(LibraryViolation) as exc:
        reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert _failure(exc) is LibraryFailureCode.OBJECT_QUARANTINED


def test_s4_p4_acc_library_07_raw_journal_hash_catches_substitution_even_under_content_key_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior(output_name="original")
    _, substituted_blob, _ = _behavior(output_name="substituted")
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    blob_path = _object_path(root, LibraryObjectNamespace.BLOB, unit.content_key.digest_sha256)
    manifest_path = _object_path(root, LibraryObjectNamespace.MANIFEST, manifest.manifest_id.digest_sha256)
    blob_path.write_bytes(substituted_blob.canonical_core_bytes)
    monkeypatch.setattr(
        canonicalization_module,
        "_content_digest",
        lambda preimage: unit.content_key.digest_sha256,
    )

    with pytest.raises(LibraryViolation) as exc:
        library.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert _failure(exc) is LibraryFailureCode.OBJECT_QUARANTINED
    assert not blob_path.exists()
    assert manifest_path.exists()


def test_s4_p4_acc_library_08_existing_key_with_different_bytes_is_not_overwritten(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    blob_path = _object_path(root, LibraryObjectNamespace.BLOB, unit.content_key.digest_sha256)
    blob_path.parent.mkdir()
    existing = b"pre-existing different bytes"
    blob_path.write_bytes(existing)

    with pytest.raises(LibraryViolation) as exc:
        library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    assert _failure(exc) is LibraryFailureCode.EXISTING_OBJECT_MISMATCH
    assert not blob_path.exists()
    assert library.search_index() == ()
    evidence = [path.read_bytes() for path in (root / "quarantine" / "payloads").rglob("*") if path.is_file()]
    assert existing in evidence
    assert blob.canonical_core_bytes in evidence


def test_s4_p4_acc_library_09_gc_is_planning_only_and_preserves_every_root_category_transitively(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit_a, blob_a, manifest_a = _behavior(output_name="result_a")
    unit_b, blob_b, manifest_b = _behavior(output_name="result_b")
    library.put_behavior(unit_a, blob_a, manifest_a, publisher_identity=publisher)
    library.put_behavior(unit_b, blob_b, manifest_b, publisher_identity=publisher)
    entries = {entry.content_key: entry for entry in library.search_index()}
    retained_entry = entries[unit_a.content_key.value]
    candidate_entry = entries[unit_b.content_key.value]
    before = _tree_bytes(root / "objects")

    for root_kind in RetentionRootKind:
        plan = library.plan_garbage_collection(_root_sets(root_kind, (retained_entry.manifest_ref,)))
        assert set(plan.retained_refs) == {retained_entry.manifest_ref, retained_entry.blob_ref}
        assert set(plan.deletion_candidates) == {candidate_entry.manifest_ref, candidate_entry.blob_ref}
        assert plan.plan_sha256
        assert _tree_bytes(root / "objects") == before


def test_s4_p4_acc_library_10_snapshot_requires_a_trusted_prior_for_same_forward_and_rollback_claims(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    initial = library.current_snapshot()
    assert initial.status is SnapshotVerificationStatus.UNANCHORED
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)

    forward = library.current_snapshot(trusted_prior=initial.snapshot)
    assert forward.status is SnapshotVerificationStatus.VERIFIED_FORWARD
    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    same = reopened.current_snapshot(trusted_prior=forward.snapshot)
    assert same.status is SnapshotVerificationStatus.VERIFIED_SAME

    future = replace(
        forward.snapshot,
        generation=forward.snapshot.generation + 1,
        committed_journal_sequence=forward.snapshot.committed_journal_sequence + 1,
    )
    with pytest.raises(LibraryViolation) as exc:
        reopened.current_snapshot(trusted_prior=future)
    assert _failure(exc) is LibraryFailureCode.SNAPSHOT_ROLLBACK

    different_root = "f" * 64 if forward.snapshot.index_sha256 != "f" * 64 else "e" * 64
    mixed = replace(forward.snapshot, index_sha256=different_root)
    with pytest.raises(LibraryViolation) as exc:
        reopened.current_snapshot(trusted_prior=mixed)
    assert _failure(exc) is LibraryFailureCode.SNAPSHOT_MIXED_ROOTS


def test_s4_p4_acc_library_11_missing_referenced_blob_never_leaves_a_searchable_manifest(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    blob_path = _object_path(root, LibraryObjectNamespace.BLOB, unit.content_key.digest_sha256)
    blob_path.unlink()

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    assert reopened.search_index() == ()
    with pytest.raises(LibraryViolation) as exc:
        reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert _failure(exc) is LibraryFailureCode.BLOB_MISSING


def test_s4_p4_acc_library_12_restart_repairs_only_a_torn_journal_tail(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    journal = root / "journal" / "library.v1"
    valid_length = journal.stat().st_size
    torn = b"\x00\x00\x00\x00\x00"
    with journal.open("ab") as stream:
        stream.write(torn)

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    assert (
        reopened.get_verified_behavior(unit.content_key, manifest.manifest_id).blob.canonical_core_bytes
        == blob.canonical_core_bytes
    )
    assert scan_journal(journal).torn_tail == b""
    assert journal.stat().st_size == valid_length
    assert torn in [path.read_bytes() for path in (root / "quarantine" / "payloads").rglob("*") if path.is_file()]


def test_s4_p4_acc_library_13_persisted_journal_cannot_change_platform_publisher(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    journal = root / "journal" / "library.v1"
    forged_frames: list[bytes] = []
    for frame in scan_journal(journal).frames:
        record = decode_stage4_canonical_bytes(
            frame.payload,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
        assert type(record) is dict
        record["publisher_component_id"] = "worker"
        forged_frames.append(
            encode_journal_frame(
                canonicalize_stage4_payload(
                    record,
                    profile_id=STAGE4_CANONICAL_PROFILE_V1,
                    codec_id=STABLE_CANONICAL_CODEC_ID,
                )
            )
        )
    journal.write_bytes(JOURNAL_FRAME_MAGIC_V1 + b"".join(forged_frames))

    with pytest.raises(LibraryViolation) as exc:
        BehaviorLibrary(root, publisher_identity=publisher)
    assert _failure(exc) is LibraryFailureCode.PUBLISHER_MISMATCH


@pytest.mark.parametrize(
    ("function_name", "crash_after_call", "must_be_visible"),
    (
        ("write_staged_bytes", 1, False),
        ("write_staged_bytes", 2, True),
        ("publish_immutable", 1, True),
        ("publish_immutable", 2, True),
        ("atomic_replace_metadata", 1, True),
        ("append_journal_payload", 6, True),
        ("append_journal_payload", 7, True),
    ),
)
def test_s4_p4_acc_library_14_restart_after_each_durable_phase_has_one_admissible_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    crash_after_call: int,
    must_be_visible: bool,
) -> None:
    publisher = _publisher()
    root, library = _store(tmp_path, publisher)
    unit, blob, manifest = _behavior()
    original = getattr(library_module, function_name)
    call_count = 0

    def crash_after_durable_action(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        result = original(*args, **kwargs)
        call_count += 1
        if call_count == crash_after_call:
            raise SystemExit("simulated process termination after durable action")
        return result

    monkeypatch.setattr(library_module, function_name, crash_after_durable_action)
    with pytest.raises(SystemExit):
        library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    monkeypatch.setattr(library_module, function_name, original)

    reopened = BehaviorLibrary(root, publisher_identity=publisher)
    if must_be_visible:
        record = reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
        assert record.blob.canonical_core_bytes == blob.canonical_core_bytes
        snapshot = reopened.current_snapshot().snapshot
        again = BehaviorLibrary(root, publisher_identity=publisher)
        assert again.current_snapshot(trusted_prior=snapshot).status is SnapshotVerificationStatus.VERIFIED_SAME
    else:
        assert reopened.search_index() == ()
        result = reopened.put_behavior(unit, blob, manifest, publisher_identity=publisher)
        assert result.status is PutStatus.STORED
    assert not [path for path in root.rglob("*stage-*") if path.exists()]
