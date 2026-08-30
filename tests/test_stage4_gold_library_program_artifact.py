"""Acceptance and falsification tests for the Library-owned program CAS.

These cases deliberately enter through ``BehaviorLibrary`` and the real §22
publication helper.  A dictionary-backed resolver would make the important
properties here (authority, immutable publication, restart recovery and GC
reachability) impossible to observe.
"""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.experiments.gold import library_program_artifacts as program_artifact_module
from synapse.experiments.gold.behavior import (
    BehaviorBlob,
    BehaviorCore,
    BehaviorManifest,
    SynapseBehaviorUnit,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import SchemaVersion
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    LIBRARY_RETENTION_ROOTS_V1,
    BehaviorLibrary,
    LibraryFailureCode,
    LibraryObjectNamespace,
    LibraryObjectRef,
    LibraryViolation,
    PublisherIdentity,
    RetentionRootKind,
    RetentionRootSet,
    create_program_artifact_write_authority,
)
from synapse.experiments.gold.library_composition import (
    create_program_artifact_behavior_library,
)
from synapse.experiments.gold.library_program_artifacts import (
    create_library_program_artifact_reader,
    create_library_program_artifact_lifecycle,
    validate_library_program_artifact_reader,
)
from synapse.experiments.gold.persistence import StagedFile, StoreMutationTicket
from tests.gold_store_fence import fence_for
from tests.gold_write_admission import gate_history as _gate_history, publish_behavior


_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _publisher() -> PublisherIdentity:
    return PublisherIdentity(
        LIBRARY_PUBLISHER_IDENTITY_V1,
        "stage4-program-publisher",
        "synapse.stage4.gold.program-publisher-policy/v1",
    )


def _store(
    tmp_path: Path,
    publisher: PublisherIdentity,
    *,
    name: str = "case",
) -> tuple[Path, Path, BehaviorLibrary]:
    gate_root = tmp_path / name
    gate_root.mkdir()
    root = gate_root / "library"
    root.mkdir()
    return gate_root, root, create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )


def _program_bytes(label: str = "program") -> bytes:
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("POP"),
            Instruction("HALT"),
        ],
        constants=[label],
    )
    return json.dumps(
        program.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _program_ref(raw: bytes) -> HashBoundRef:
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.PROGRAM_ARTIFACT,
        ref_id=digest,
        schema_id=SchemaVersion.REPLAY_ARTIFACT_PROGRAM_V1.value,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def _artifact_behavior(
    reference: HashBoundRef,
    *,
    output_name: str = "result",
) -> tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest]:
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(vectors["vectors"][0]["core"])
    payload["output_contract"]["fields"][0]["name"] = output_name
    payload["canonical_program"] = {
        "form": "ARTIFACT_REF_V1",
        "artifact_ref": reference.to_dict(),
    }
    payload["capability_requirements"] = []
    # This is the durable retention edge.  Merely placing the reference in the
    # canonical_program field must not let publication or GC infer an unstored,
    # caller-only edge.
    payload["artifact_refs"] = [reference.to_dict()]
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


def _publish(
    library: BehaviorLibrary,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
    *,
    publisher: PublisherIdentity,
    gate_root: Path,
) -> None:
    publish_behavior(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        journal_root=gate_root,
    )


def _program_path(root: Path, reference: HashBoundRef, *, temporary: bool) -> Path:
    base = root / ("ingestion" if temporary else "objects") / "programs"
    return base / reference.sha256[:2] / reference.sha256[2:]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


class _ProtocolShapedArtifactReader:
    def __init__(self, library: BehaviorLibrary) -> None:
        self._library = library

    @property
    def mutation_fence(self) -> object:
        return self._library.mutation_fence

    def open_artifact(self, reference: HashBoundRef) -> bytes:
        return self._library.open_artifact(reference)


class _ProtocolShapedProgramLifecycle:
    def _unused(self, *args: object, **kwargs: object) -> None:
        return None

    create_write_authority = _unused
    validate_ingestion_result = _unused
    initialize = _unused
    recover_locked = _unused
    ingest = _unused
    promote_locked = _unused
    verify_unit_locked = _unused
    open = _unused
    extend_gc_graph_locked = _unused


def test_program_artifact_is_temporary_until_the_behavior_and_retention_edge_commit(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )

    ingested = library.ingest_program_artifact(authority, reference, raw)

    assert ingested.reference == reference
    assert ingested.object_ref.namespace is LibraryObjectNamespace.PROGRAM
    assert ingested.object_ref.digest_sha256 == reference.sha256
    assert not ingested.deduplicated
    assert _program_path(root, reference, temporary=True).read_bytes() == raw
    assert not _program_path(root, reference, temporary=False).exists()
    with pytest.raises(LibraryViolation) as exc:
        library.open_artifact(reference)
    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_NOT_RETAINED

    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )

    final_path = _program_path(root, reference, temporary=False)
    assert final_path.read_bytes() == raw
    assert not _program_path(root, reference, temporary=True).exists()
    assert library.open_artifact(reference) == raw

    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )
    assert reopened.open_artifact(reference) == raw
    loaded = reopened.get_verified_behavior(unit.content_key, manifest.manifest_id)
    assert reference in loaded.manifest.artifact_refs


def test_sealed_exact_reader_rejects_surrogates_and_preserves_library_truth_across_restart(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher, name="sealed-reader")
    raw = _program_bytes("sealed-reader")
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )

    reader = create_library_program_artifact_reader(library)
    assert reader.open_artifact(reference) == raw
    with pytest.raises(LibraryViolation) as fake_reader:
        validate_library_program_artifact_reader(
            _ProtocolShapedArtifactReader(library)
        )
    assert fake_reader.value.failure_code is LibraryFailureCode.TYPE_MISMATCH

    fake_gate = tmp_path / "fake-lifecycle"
    fake_gate.mkdir()
    fake_root = fake_gate / "library"
    fake_root.mkdir()
    fake_library = BehaviorLibrary(
        fake_root,
        publisher_identity=publisher,
        mutation_fence=fence_for(fake_gate),
        write_history=_gate_history(fake_gate),
        program_artifact_lifecycle=_ProtocolShapedProgramLifecycle(),
    )
    with pytest.raises(LibraryViolation) as fake_lifecycle:
        create_library_program_artifact_reader(fake_library)
    assert fake_lifecycle.value.failure_code is LibraryFailureCode.TYPE_MISMATCH

    lifecycle = create_library_program_artifact_lifecycle()
    owner_gate = tmp_path / "lifecycle-owner"
    owner_gate.mkdir()
    owner_root = owner_gate / "library"
    owner_root.mkdir()
    BehaviorLibrary(
        owner_root,
        publisher_identity=publisher,
        mutation_fence=fence_for(owner_gate),
        write_history=_gate_history(owner_gate),
        program_artifact_lifecycle=lifecycle,
    )
    foreign_gate = tmp_path / "foreign-lifecycle"
    foreign_gate.mkdir()
    foreign_root = foreign_gate / "library"
    foreign_root.mkdir()
    with pytest.raises(LibraryViolation) as reused_lifecycle:
        BehaviorLibrary(
            foreign_root,
            publisher_identity=publisher,
            mutation_fence=fence_for(foreign_gate),
            write_history=_gate_history(foreign_gate),
            program_artifact_lifecycle=lifecycle,
        )
    assert reused_lifecycle.value.failure_code is LibraryFailureCode.TYPE_MISMATCH

    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )
    reopened_reader = create_library_program_artifact_reader(reopened)
    assert reopened_reader.open_artifact(reference) == raw

    _program_path(root, reference, temporary=False).write_bytes(
        b"tampered retained program"
    )
    with pytest.raises(LibraryViolation) as corrupt:
        reopened_reader.open_artifact(reference)
    assert corrupt.value.failure_code is LibraryFailureCode.OBJECT_CORRUPT

    quarantined = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )
    quarantined_reader = create_library_program_artifact_reader(quarantined)
    with pytest.raises(LibraryViolation) as durable_quarantine:
        quarantined_reader.open_artifact(reference)
    assert (
        durable_quarantine.value.failure_code
        is LibraryFailureCode.OBJECT_QUARANTINED
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ref_id", "0" * 64),
        ("sha256", "0" * 64),
        ("byte_length", 1),
        ("media_type", "application/octet-stream"),
        ("schema_id", SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value),
        ("kind", RefKind.ARTIFACT),
    ),
)
def test_program_artifact_ingestion_rejects_every_inexact_reference_field_without_a_write(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    publisher = _publisher()
    _, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = replace(_program_ref(raw), **{field: value})
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    before = _tree_bytes(root)

    with pytest.raises(LibraryViolation) as exc:
        library.ingest_program_artifact(authority, reference, raw)

    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH
    assert _tree_bytes(root) == before


def test_program_artifact_ingestion_rejects_noncanonical_program_bytes(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    _, root, library = _store(tmp_path, publisher)
    canonical = _program_bytes()
    noncanonical = json.dumps(
        json.loads(canonical), sort_keys=True, indent=2
    ).encode("utf-8")
    reference = _program_ref(noncanonical)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    before = _tree_bytes(root)

    with pytest.raises(LibraryViolation) as exc:
        library.ingest_program_artifact(authority, reference, noncanonical)

    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_NOT_CANONICAL
    assert _tree_bytes(root) == before


def test_program_artifact_write_authority_is_library_bound_and_not_forgeable(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    _, left_root, left = _store(tmp_path, publisher, name="left")
    _, right_root, right = _store(tmp_path, publisher, name="right")
    raw = _program_bytes()
    reference = _program_ref(raw)
    left_authority = create_program_artifact_write_authority(
        left, publisher_identity=publisher
    )

    with pytest.raises(LibraryViolation) as forged:
        right.ingest_program_artifact(object(), reference, raw)  # type: ignore[arg-type]
    assert (
        forged.value.failure_code
        is LibraryFailureCode.PROGRAM_ARTIFACT_WRITE_FORBIDDEN
    )
    with pytest.raises(LibraryViolation) as foreign:
        right.ingest_program_artifact(left_authority, reference, raw)
    assert (
        foreign.value.failure_code
        is LibraryFailureCode.PROGRAM_ARTIFACT_WRITE_FORBIDDEN
    )
    assert _tree_bytes(left_root / "ingestion") == {}
    assert _tree_bytes(right_root / "ingestion") == {}

    equal_but_untrusted = PublisherIdentity.from_dict(publisher.to_dict())
    with pytest.raises(LibraryViolation) as wrong_publisher:
        create_program_artifact_write_authority(
            left, publisher_identity=equal_but_untrusted
        )
    assert wrong_publisher.value.failure_code is LibraryFailureCode.PUBLISHER_MISMATCH


def test_program_artifact_collision_is_quarantined_and_behavior_never_becomes_visible(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    final_path = _program_path(root, reference, temporary=False)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    collision = b"different bytes already occupying the immutable address"
    final_path.write_bytes(collision)
    unit, blob, manifest = _artifact_behavior(reference)

    with pytest.raises(LibraryViolation) as exc:
        _publish(
            library,
            unit,
            blob,
            manifest,
            publisher=publisher,
            gate_root=gate_root,
        )

    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH
    assert library.search_index() == ()
    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )
    with pytest.raises(LibraryViolation) as quarantined:
        reopened.open_artifact(reference)
    assert quarantined.value.failure_code is LibraryFailureCode.OBJECT_QUARANTINED


def test_program_artifact_tamper_is_quarantined_durably_on_exact_read(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    final_path = _program_path(root, reference, temporary=False)
    corrupt = b"corrupt retained program"
    final_path.write_bytes(corrupt)

    with pytest.raises(LibraryViolation) as first:
        library.open_artifact(reference)
    assert first.value.failure_code is LibraryFailureCode.OBJECT_CORRUPT

    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence_for(gate_root),
        write_history=_gate_history(gate_root),
    )
    with pytest.raises(LibraryViolation) as again:
        reopened.open_artifact(reference)
    assert again.value.failure_code is LibraryFailureCode.OBJECT_QUARANTINED


def test_gc_reaches_program_only_through_a_committed_behavior_manifest(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, _, library = _store(tmp_path, publisher)
    retained_raw = _program_bytes("retained")
    candidate_raw = _program_bytes("candidate")
    retained_ref = _program_ref(retained_raw)
    candidate_ref = _program_ref(candidate_raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    retained_ingestion = library.ingest_program_artifact(
        authority, retained_ref, retained_raw
    )
    candidate_ingestion = library.ingest_program_artifact(
        authority, candidate_ref, candidate_raw
    )
    retained_unit, retained_blob, retained_manifest = _artifact_behavior(
        retained_ref, output_name="retained"
    )
    candidate_unit, candidate_blob, candidate_manifest = _artifact_behavior(
        candidate_ref, output_name="candidate"
    )
    _publish(
        library,
        retained_unit,
        retained_blob,
        retained_manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    _publish(
        library,
        candidate_unit,
        candidate_blob,
        candidate_manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    entries = {entry.content_key: entry for entry in library.search_index()}
    retained_entry = entries[retained_unit.content_key.value]
    candidate_entry = entries[candidate_unit.content_key.value]

    plan = library.plan_garbage_collection(
        _root_sets(RetentionRootKind.EVIDENCE, (retained_entry.manifest_ref,))
    )
    assert set(plan.retained_refs) == {
        retained_entry.manifest_ref,
        retained_entry.blob_ref,
        retained_ingestion.object_ref,
    }
    assert set(plan.deletion_candidates) == {
        candidate_entry.manifest_ref,
        candidate_entry.blob_ref,
        candidate_ingestion.object_ref,
    }


def test_restart_recovers_a_durable_program_ingestion_stage_and_can_publish_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    original = program_artifact_module.write_staged_bytes

    def crash_after_stage(
        directory: Path,
        *,
        final_name: str,
        operation_id: str,
        value: bytes,
        maximum_bytes: int,
        ticket: StoreMutationTicket,
    ) -> StagedFile:
        staged = original(
            directory,
            final_name=final_name,
            operation_id=operation_id,
            value=value,
            maximum_bytes=maximum_bytes,
            ticket=ticket,
        )
        raise SystemExit("simulated crash after durable program stage")

    monkeypatch.setattr(program_artifact_module, "write_staged_bytes", crash_after_stage)
    with pytest.raises(SystemExit):
        library.ingest_program_artifact(authority, reference, raw)
    monkeypatch.setattr(program_artifact_module, "write_staged_bytes", original)

    fence = fence_for(gate_root)
    assert fence.current_epoch() % 2 == 1
    fence.recover_abandoned_interval()
    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence,
        write_history=_gate_history(gate_root),
    )
    recovered_authority = create_program_artifact_write_authority(
        reopened, publisher_identity=publisher
    )
    recovered = reopened.ingest_program_artifact(
        recovered_authority, reference, raw
    )
    assert recovered.deduplicated
    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        reopened,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    assert reopened.open_artifact(reference) == raw


def test_crash_after_program_promotion_never_exposes_a_behavior_without_its_edge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _publisher()
    gate_root, root, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    unit, blob, manifest = _artifact_behavior(reference)
    original = program_artifact_module.publish_immutable

    def crash_after_program_publish(
        staged: StagedFile,
        destination: Path,
        *,
        ticket: StoreMutationTicket,
    ) -> None:
        original(staged, destination, ticket=ticket)
        if destination == _program_path(root, reference, temporary=False):
            raise SystemExit("simulated crash after program promotion")

    monkeypatch.setattr(
        program_artifact_module, "publish_immutable", crash_after_program_publish
    )
    with pytest.raises(SystemExit):
        _publish(
            library,
            unit,
            blob,
            manifest,
            publisher=publisher,
            gate_root=gate_root,
        )
    monkeypatch.setattr(program_artifact_module, "publish_immutable", original)

    fence = fence_for(gate_root)
    assert fence.current_epoch() % 2 == 1
    fence.recover_abandoned_interval()
    reopened = create_program_artifact_behavior_library(
        root,
        publisher_identity=publisher,
        mutation_fence=fence,
        write_history=_gate_history(gate_root),
    )
    assert reopened.search_index() == (), "the behavior commit never began"
    with pytest.raises(LibraryViolation) as unretained:
        reopened.open_artifact(reference)
    assert (
        unretained.value.failure_code
        is LibraryFailureCode.PROGRAM_ARTIFACT_NOT_RETAINED
    )

    retry_authority = create_program_artifact_write_authority(
        reopened, publisher_identity=publisher
    )
    reopened.ingest_program_artifact(retry_authority, reference, raw)
    _publish(
        reopened,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    assert reopened.open_artifact(reference) == raw


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ref_id", "0" * 64),
        ("sha256", "0" * 64),
        ("byte_length", 1),
        ("media_type", "application/octet-stream"),
        ("schema_id", SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value),
        ("kind", RefKind.ARTIFACT),
    ),
)
def test_exact_reader_rejects_a_substituted_reference_without_quarantining_valid_bytes(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    publisher = _publisher()
    gate_root, _, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    substituted = replace(reference, **{field: value})

    with pytest.raises(LibraryViolation) as exc:
        library.open_artifact(substituted)

    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH
    assert library.open_artifact(reference) == raw



def test_publication_with_substituted_length_does_not_poison_valid_program_bytes(
    tmp_path: Path,
) -> None:
    publisher = _publisher()
    gate_root, _, library = _store(tmp_path, publisher)
    raw = _program_bytes()
    reference = _program_ref(raw)
    authority = create_program_artifact_write_authority(
        library, publisher_identity=publisher
    )
    library.ingest_program_artifact(authority, reference, raw)
    unit, blob, manifest = _artifact_behavior(reference)
    _publish(
        library,
        unit,
        blob,
        manifest,
        publisher=publisher,
        gate_root=gate_root,
    )
    substituted = replace(
        reference,
        byte_length=reference.byte_length + 1,
    )
    bad_unit, bad_blob, bad_manifest = _artifact_behavior(
        substituted,
        output_name="substituted",
    )

    with pytest.raises(LibraryViolation) as exc:
        _publish(
            library,
            bad_unit,
            bad_blob,
            bad_manifest,
            publisher=publisher,
            gate_root=gate_root,
        )

    assert exc.value.failure_code is LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH
