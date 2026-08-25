"""Library-owned immutable ProgramArtifact lifecycle adapter.

This module implements the program CAS behind ``BehaviorLibrary``'s injected
lifecycle port.  It imports the Library owner; the owner never imports this
adapter.  Replay consumes only ``BehaviorLibrary.open_artifact``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from synapse.bytecode import BytecodeProgram

from .behavior import ArtifactProgram, SynapseBehaviorUnit, validate_behavior_unit
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    REPLAY_ARTIFACT_PROGRAM_V1,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .library import (
    BehaviorLibrary,
    CorruptionDetectionSource,
    CorruptionReason,
    LibraryFailureCode,
    LibraryObjectNamespace,
    LibraryObjectRef,
    LibraryViolation,
    MAX_PROGRAM_ARTIFACT_BYTES_V1,
    PublisherIdentity,
    _fail,
)
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StagedFile,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    require_directory,
    require_regular_file,
    write_staged_bytes,
)


_PROGRAM_ARTIFACT_FIELDS = (
    "type",
    "version",
    "constants",
    "instructions",
    "host_abi_version",
    "program_hash",
    "guard_cleanup_table",
)
LIBRARY_PROGRAM_ARTIFACT_INGESTION_V1 = "synapse.stage4.gold.library-program-artifact-ingestion/v1"
_AUTHORITY_SEAL = object()
_INGESTION_SEAL = object()


@dataclass(frozen=True, init=False)
class ProgramArtifactWriteAuthority:
    library: BehaviorLibrary
    publisher_identity: PublisherIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ProgramArtifactWriteAuthority:
        raise TypeError("ProgramArtifactWriteAuthority is created only by its Library adapter")


def _make_write_authority(
    owner: BehaviorLibrary,
    publisher_identity: PublisherIdentity,
) -> ProgramArtifactWriteAuthority:
    if type(owner) is not BehaviorLibrary:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_WRITE_FORBIDDEN,
            "program artifact authority requires an exact BehaviorLibrary",
        )
    publisher = owner._require_publisher(publisher_identity)
    value = object.__new__(ProgramArtifactWriteAuthority)
    object.__setattr__(value, "library", owner)
    object.__setattr__(value, "publisher_identity", publisher)
    object.__setattr__(value, "_trusted_seal", _AUTHORITY_SEAL)
    return value


def _validate_write_authority(
    value: object,
    owner: BehaviorLibrary,
) -> ProgramArtifactWriteAuthority:
    if (
        type(value) is not ProgramArtifactWriteAuthority
        or getattr(value, "_trusted_seal", None) is not _AUTHORITY_SEAL
        or getattr(value, "library", None) is not owner
    ):
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_WRITE_FORBIDDEN,
            "program artifact write authority is foreign or untrusted",
        )
    try:
        owner._require_publisher(value.publisher_identity)
    except LibraryViolation as exc:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_WRITE_FORBIDDEN,
            "program artifact write authority is no longer configured",
        ) from exc
    return value


@dataclass(frozen=True, init=False)
class ProgramArtifactIngestion:
    schema_version: str
    reference: HashBoundRef
    object_ref: LibraryObjectRef
    operation_id: str
    deduplicated: bool
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ProgramArtifactIngestion:
        raise TypeError("ProgramArtifactIngestion is created only by its Library adapter")


def _make_ingestion(
    reference: HashBoundRef,
    operation_id: str,
    *,
    deduplicated: bool,
) -> ProgramArtifactIngestion:
    reference = _validate_reference(reference)
    if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, "program ingestion operation id is invalid")
    if type(deduplicated) is not bool:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "program ingestion status is invalid")
    value = object.__new__(ProgramArtifactIngestion)
    object.__setattr__(value, "schema_version", LIBRARY_PROGRAM_ARTIFACT_INGESTION_V1)
    object.__setattr__(value, "reference", reference)
    object.__setattr__(
        value,
        "object_ref",
        LibraryObjectRef(LibraryObjectNamespace.PROGRAM, reference.sha256),
    )
    object.__setattr__(value, "operation_id", operation_id)
    object.__setattr__(value, "deduplicated", deduplicated)
    object.__setattr__(value, "_trusted_seal", _INGESTION_SEAL)
    return value


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _decode(value: bytes) -> object:
    return decode_stage4_canonical_bytes(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _validate_reference(value: object) -> HashBoundRef:
    try:
        if type(value) is not HashBoundRef:
            raise TypeError
        value.to_dict()
    except Exception as exc:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
            "program artifact reference is malformed",
        ) from exc
    if (
        value.kind is not RefKind.PROGRAM_ARTIFACT
        or value.schema_id != REPLAY_ARTIFACT_PROGRAM_V1
        or value.media_type != "application/json"
        or value.ref_id != value.sha256
        or value.byte_length <= 0
        or value.byte_length > MAX_PROGRAM_ARTIFACT_BYTES_V1
    ):
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
            "program artifact reference is not exact",
        )
    return value


def _validate_bytes(reference: HashBoundRef, value: object) -> bytes:
    reference = _validate_reference(reference)
    if type(value) is not bytes:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
            "program artifact payload must be exact bytes",
        )
    if len(value) != reference.byte_length or hashlib.sha256(value).hexdigest() != reference.sha256:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
            "program artifact bytes do not match the exact reference",
        )
    try:
        decoded = _decode(value)
        if type(decoded) is not dict or set(decoded) != set(_PROGRAM_ARTIFACT_FIELDS):
            raise ValueError("program artifact fields are not exact")
        if any(type(key) is not str for key in decoded):
            raise ValueError("program artifact keys are invalid")
        program = BytecodeProgram.from_dict(decoded)
        if program.to_dict() != decoded or program.program_hash != decoded["program_hash"]:
            raise ValueError("program round trip mismatch")
        if _canonical(decoded) != value:
            raise ValueError("program bytes are not canonical")
    except Exception as exc:
        raise _fail(
            LibraryFailureCode.PROGRAM_ARTIFACT_NOT_CANONICAL,
            "program artifact canonical round trip failed",
        ) from exc
    return value


class LibraryProgramArtifactLifecycle:
    """Injected lifecycle implementation for exactly one ``BehaviorLibrary``."""

    _PORT_METHODS = (
        "create_write_authority",
        "validate_ingestion_result",
        "initialize",
        "recover_locked",
        "ingest",
        "promote_locked",
        "verify_unit_locked",
        "open",
        "extend_gc_graph_locked",
    )

    def create_write_authority(
        self,
        owner: BehaviorLibrary,
        publisher_identity: PublisherIdentity,
    ) -> ProgramArtifactWriteAuthority:
        return _make_write_authority(owner, publisher_identity)

    def validate_ingestion_result(self, value: object) -> None:
        if (
            type(value) is not ProgramArtifactIngestion
            or getattr(value, "_trusted_seal", None) is not _INGESTION_SEAL
            or value.schema_version != LIBRARY_PROGRAM_ARTIFACT_INGESTION_V1
            or type(value.reference) is not HashBoundRef
            or type(value.object_ref) is not LibraryObjectRef
            or value.object_ref.namespace is not LibraryObjectNamespace.PROGRAM
            or value.object_ref.digest_sha256 != value.reference.sha256
            or type(value.operation_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value.operation_id) is None
            or type(value.deduplicated) is not bool
        ):
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "program ingestion result is not adapter sealed")

    @staticmethod
    def _paths(owner: BehaviorLibrary) -> tuple[Path, Path]:
        if type(owner) is not BehaviorLibrary:
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "program lifecycle owner is invalid")
        return owner.root / "objects" / "programs", owner.root / "ingestion" / "programs"

    @staticmethod
    def _object_ref(reference: HashBoundRef) -> LibraryObjectRef:
        reference = _validate_reference(reference)
        return LibraryObjectRef(LibraryObjectNamespace.PROGRAM, reference.sha256)

    @classmethod
    def _path(
        cls,
        owner: BehaviorLibrary,
        reference: HashBoundRef,
        *,
        temporary: bool,
        create_shard: bool,
    ) -> Path:
        reference = _validate_reference(reference)
        if type(temporary) is not bool or type(create_shard) is not bool:
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "program CAS path mode is invalid")
        programs, ingestion = cls._paths(owner)
        base = ingestion if temporary else programs
        shard = base / reference.sha256[:2]
        if create_shard:
            owner._mutation_ticket()
            ensure_directory(shard)
        else:
            try:
                require_directory(shard)
            except PersistenceViolation as exc:
                raise _fail(
                    LibraryFailureCode.PROGRAM_ARTIFACT_MISSING,
                    "program artifact shard is unavailable",
                ) from exc
        return shard / reference.sha256[2:]

    @staticmethod
    def _reference_for_unit(unit: SynapseBehaviorUnit) -> HashBoundRef | None:
        validate_behavior_unit(unit)
        if type(unit.core.canonical_program) is not ArtifactProgram:
            return None
        return _validate_reference(unit.core.canonical_program.artifact_ref)

    def initialize(self, owner: BehaviorLibrary) -> None:
        programs, ingestion = self._paths(owner)
        ingestion_root = ingestion.parent
        try:
            for directory in (programs, ingestion_root, ingestion):
                ensure_directory(directory)
        except PersistenceViolation as exc:
            raise _fail(
                LibraryFailureCode.INVALID_STORE_ROOT,
                "program artifact layout initialization failed",
            ) from exc

    def _read_locked(
        self,
        owner: BehaviorLibrary,
        reference: HashBoundRef,
        path: Path,
        *,
        source: CorruptionDetectionSource,
    ) -> bytes:
        reference = _validate_reference(reference)
        object_ref = self._object_ref(reference)
        if object_ref in owner._quarantined:
            raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "program artifact is quarantined")
        try:
            raw = read_regular_bytes(path, maximum_bytes=MAX_PROGRAM_ARTIFACT_BYTES_V1)
        except PersistenceViolation as exc:
            if exc.failure_code in (
                PersistenceFailureCode.NON_REGULAR_ENTRY,
                PersistenceFailureCode.LINK_OR_REPARSE_POINT,
            ):
                owner._record_corruption_locked(
                    object_ref,
                    expected_sha256=reference.sha256,
                    actual_sha256=None,
                    source=source,
                    reason=CorruptionReason.NON_REGULAR_ENTRY,
                    existing_path=path,
                )
                raise _fail(
                    LibraryFailureCode.OBJECT_CORRUPT,
                    "program artifact entry is not a regular file",
                ) from exc
            raise _fail(
                LibraryFailureCode.PROGRAM_ARTIFACT_MISSING,
                "program artifact bytes are unavailable",
            ) from exc
        actual = hashlib.sha256(raw).hexdigest()
        if actual != reference.sha256:
            owner._record_corruption_locked(
                object_ref,
                expected_sha256=reference.sha256,
                actual_sha256=actual,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=path,
            )
            raise _fail(
                LibraryFailureCode.OBJECT_CORRUPT,
                "program artifact bytes differ from the exact reference",
            )
        if len(raw) != reference.byte_length:
            raise _fail(
                LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                "program artifact reference length differs from the stored object",
            )
        try:
            return _validate_bytes(reference, raw)
        except LibraryViolation as exc:
            owner._record_corruption_locked(
                object_ref,
                expected_sha256=reference.sha256,
                actual_sha256=actual,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=path,
            )
            raise _fail(
                LibraryFailureCode.OBJECT_CORRUPT,
                "program artifact canonical transport is invalid",
            ) from exc

    def _recover_namespace_locked(
        self,
        owner: BehaviorLibrary,
        base: Path,
    ) -> None:
        for shard in sorted(base.iterdir(), key=lambda item: item.name):
            try:
                require_directory(shard)
            except PersistenceViolation as exc:
                raise _fail(
                    LibraryFailureCode.INVALID_STORE_ENTRY,
                    "program CAS shard is not a directory",
                ) from exc
            if re.fullmatch(r"[0-9a-f]{2}", shard.name) is None:
                raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "program CAS shard name is invalid")
            for item in sorted(shard.iterdir(), key=lambda entry: entry.name):
                if re.fullmatch(r"[0-9a-f]{62}", item.name) is not None:
                    try:
                        require_regular_file(item)
                    except PersistenceViolation as exc:
                        raise _fail(
                            LibraryFailureCode.INVALID_STORE_ENTRY,
                            "program CAS object is not regular",
                        ) from exc
                    continue
                match = re.fullmatch(r"\.([0-9a-f]{62})\.stage-([0-9a-f]{32})", item.name)
                if match is None:
                    raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "program CAS entry name is invalid")
                digest = shard.name + match.group(1)
                object_ref = LibraryObjectRef(LibraryObjectNamespace.PROGRAM, digest)
                raw: bytes | None = None
                try:
                    raw = read_regular_bytes(item, maximum_bytes=MAX_PROGRAM_ARTIFACT_BYTES_V1)
                    reference = HashBoundRef(
                        kind=RefKind.PROGRAM_ARTIFACT,
                        ref_id=digest,
                        schema_id=REPLAY_ARTIFACT_PROGRAM_V1,
                        sha256=digest,
                        byte_length=len(raw),
                        media_type="application/json",
                    )
                    _validate_bytes(reference, raw)
                except Exception:
                    owner._record_corruption_locked(
                        object_ref,
                        expected_sha256=digest,
                        actual_sha256=None if raw is None else hashlib.sha256(raw).hexdigest(),
                        source=CorruptionDetectionSource.PROGRAM_RECOVERY,
                        reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                        existing_path=item,
                    )
                    continue
                destination = shard / match.group(1)
                if destination.exists() or destination.is_symlink():
                    observed = self._read_locked(
                        owner,
                        reference,
                        destination,
                        source=CorruptionDetectionSource.PROGRAM_RECOVERY,
                    )
                    if observed != raw:
                        owner._record_corruption_locked(
                            object_ref,
                            expected_sha256=digest,
                            actual_sha256=hashlib.sha256(observed).hexdigest(),
                            source=CorruptionDetectionSource.PROGRAM_RECOVERY,
                            reason=CorruptionReason.CONTENT_COLLISION,
                            existing_path=destination,
                            raw_evidence=raw,
                        )
                        raise _fail(
                            LibraryFailureCode.RECOVERY_FAILED,
                            "program CAS recovery collision",
                        )
                    owner._cleanup_stage_locked(item)
                else:
                    publish_immutable(
                        StagedFile(item, len(raw), digest),
                        destination,
                        ticket=owner._mutation_ticket(),
                    )

    def recover_locked(self, owner: BehaviorLibrary) -> None:
        programs, ingestion = self._paths(owner)
        self._recover_namespace_locked(owner, ingestion)
        self._recover_namespace_locked(owner, programs)

    def ingest(
        self,
        owner: BehaviorLibrary,
        authority: ProgramArtifactWriteAuthority,
        reference: HashBoundRef,
        canonical_bytes: bytes,
    ) -> ProgramArtifactIngestion:
        _validate_write_authority(authority, owner)
        reference = _validate_reference(reference)
        raw = _validate_bytes(reference, canonical_bytes)
        operation_id = new_operation_id()
        object_ref = self._object_ref(reference)
        with owner._transaction():
            owner._refresh_locked()
            if object_ref in owner._quarantined:
                raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "program artifact address is quarantined")
            final_path = self._path(owner, reference, temporary=False, create_shard=True)
            ingestion_path = self._path(owner, reference, temporary=True, create_shard=True)
            for path in (final_path, ingestion_path):
                if not (path.exists() or path.is_symlink()):
                    continue
                try:
                    observed = self._read_locked(
                        owner,
                        reference,
                        path,
                        source=CorruptionDetectionSource.PROGRAM_INGESTION,
                    )
                except LibraryViolation as exc:
                    if exc.failure_code is LibraryFailureCode.OBJECT_CORRUPT:
                        raise _fail(
                            LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                            "existing program artifact address contains different bytes",
                        ) from exc
                    raise
                if observed != raw:
                    raise _fail(
                        LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                        "existing program artifact bytes differ",
                    )
                if path == final_path and ingestion_path.exists():
                    self._read_locked(
                        owner,
                        reference,
                        ingestion_path,
                        source=CorruptionDetectionSource.PROGRAM_INGESTION,
                    )
                    owner._cleanup_stage_locked(ingestion_path)
                return _make_ingestion(
                    reference,
                    operation_id,
                    deduplicated=True,
                )
            staged = write_staged_bytes(
                ingestion_path.parent,
                final_name=ingestion_path.name,
                operation_id=operation_id,
                value=raw,
                maximum_bytes=MAX_PROGRAM_ARTIFACT_BYTES_V1,
                ticket=owner._mutation_ticket(),
            )
            publish_immutable(staged, ingestion_path, ticket=owner._mutation_ticket())
            return _make_ingestion(
                reference,
                operation_id,
                deduplicated=False,
            )

    def promote_locked(self, owner: BehaviorLibrary, unit: SynapseBehaviorUnit) -> None:
        reference = self._reference_for_unit(unit)
        if reference is None:
            return
        object_ref = self._object_ref(reference)
        if object_ref in owner._quarantined:
            raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "program artifact address is quarantined")
        final_path = self._path(owner, reference, temporary=False, create_shard=True)
        ingestion_path = self._path(owner, reference, temporary=True, create_shard=True)
        if final_path.exists() or final_path.is_symlink():
            try:
                self._read_locked(
                    owner,
                    reference,
                    final_path,
                    source=CorruptionDetectionSource.PROGRAM_PUBLICATION,
                )
            except LibraryViolation as exc:
                if exc.failure_code is LibraryFailureCode.OBJECT_CORRUPT:
                    raise _fail(
                        LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                        "program publication address collision",
                    ) from exc
                raise
            if ingestion_path.exists() or ingestion_path.is_symlink():
                self._read_locked(
                    owner,
                    reference,
                    ingestion_path,
                    source=CorruptionDetectionSource.PROGRAM_PUBLICATION,
                )
                owner._cleanup_stage_locked(ingestion_path)
            return
        raw = self._read_locked(
            owner,
            reference,
            ingestion_path,
            source=CorruptionDetectionSource.PROGRAM_PUBLICATION,
        )
        staged = write_staged_bytes(
            final_path.parent,
            final_name=final_path.name,
            operation_id=new_operation_id(),
            value=raw,
            maximum_bytes=MAX_PROGRAM_ARTIFACT_BYTES_V1,
            ticket=owner._mutation_ticket(),
        )
        publish_immutable(staged, final_path, ticket=owner._mutation_ticket())
        owner._cleanup_stage_locked(ingestion_path)
        self._read_locked(
            owner,
            reference,
            final_path,
            source=CorruptionDetectionSource.PROGRAM_PUBLICATION,
        )

    def verify_unit_locked(
        self,
        owner: BehaviorLibrary,
        unit: SynapseBehaviorUnit,
        source: CorruptionDetectionSource,
    ) -> None:
        reference = self._reference_for_unit(unit)
        if reference is None:
            return
        self._read_locked(
            owner,
            reference,
            self._path(owner, reference, temporary=False, create_shard=False),
            source=source,
        )

    def _retained_references_locked(
        self,
        owner: BehaviorLibrary,
        *,
        source: CorruptionDetectionSource,
    ) -> dict[LibraryObjectRef, HashBoundRef]:
        retained: dict[LibraryObjectRef, HashBoundRef] = {}
        for blob_digest, manifest_digest in sorted(owner._committed_pairs):
            blob_ref = LibraryObjectRef(LibraryObjectNamespace.BLOB, blob_digest)
            manifest_ref = LibraryObjectRef(LibraryObjectNamespace.MANIFEST, manifest_digest)
            try:
                pair = owner._load_pair_by_refs_locked(
                    blob_ref,
                    manifest_ref,
                    source=source,
                    require_committed=True,
                    verify_program=False,
                )
            except LibraryViolation:
                continue
            reference = self._reference_for_unit(pair.unit)
            if reference is not None:
                object_ref = self._object_ref(reference)
                previous = retained.get(object_ref)
                if previous is not None and previous != reference:
                    raise _fail(
                        LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                        "committed behaviors disagree on the exact program reference",
                    )
                retained[object_ref] = reference
        return retained

    def open(self, owner: BehaviorLibrary, reference: HashBoundRef) -> bytes:
        reference = _validate_reference(reference)
        object_ref = self._object_ref(reference)
        with owner._transaction():
            retained = self._retained_references_locked(
                owner,
                source=CorruptionDetectionSource.PROGRAM_READ,
            )
            committed_reference = retained.get(object_ref)
            if committed_reference is not None:
                if committed_reference != reference:
                    raise _fail(
                        LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                        "requested reference differs from the committed program reference",
                    )
                self._read_locked(
                    owner,
                    reference,
                    self._path(owner, reference, temporary=False, create_shard=False),
                    source=CorruptionDetectionSource.PROGRAM_READ,
                )
            owner._refresh_locked()
            if object_ref in owner._quarantined:
                raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "program artifact is quarantined")
            retained = self._retained_references_locked(
                owner,
                source=CorruptionDetectionSource.PROGRAM_READ,
            )
            committed_reference = retained.get(object_ref)
            if committed_reference is None:
                raise _fail(
                    LibraryFailureCode.PROGRAM_ARTIFACT_NOT_RETAINED,
                    "program artifact has no committed behavior retention edge",
                )
            if committed_reference != reference:
                raise _fail(
                    LibraryFailureCode.PROGRAM_ARTIFACT_MISMATCH,
                    "requested reference differs from the committed program reference",
                )
            return self._read_locked(
                owner,
                reference,
                self._path(owner, reference, temporary=False, create_shard=False),
                source=CorruptionDetectionSource.PROGRAM_READ,
            )

    def _object_refs_locked(self, owner: BehaviorLibrary) -> set[LibraryObjectRef]:
        refs: set[LibraryObjectRef] = set()
        for base in self._paths(owner):
            for shard in sorted(base.iterdir(), key=lambda item: item.name):
                try:
                    require_directory(shard)
                except PersistenceViolation as exc:
                    raise _fail(
                        LibraryFailureCode.INVALID_STORE_ENTRY,
                        "program CAS shard is not a directory",
                    ) from exc
                if re.fullmatch(r"[0-9a-f]{2}", shard.name) is None:
                    raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "program CAS shard name is invalid")
                for item in sorted(shard.iterdir(), key=lambda entry: entry.name):
                    if re.fullmatch(r"[0-9a-f]{62}", item.name) is None:
                        raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "program CAS entry name is invalid")
                    try:
                        require_regular_file(item)
                    except PersistenceViolation as exc:
                        raise _fail(
                            LibraryFailureCode.INVALID_STORE_ENTRY,
                            "program CAS object is not regular",
                        ) from exc
                    refs.add(
                        LibraryObjectRef(
                            LibraryObjectNamespace.PROGRAM,
                            shard.name + item.name,
                        )
                    )
        return refs

    def extend_gc_graph_locked(
        self,
        owner: BehaviorLibrary,
        known: set[LibraryObjectRef],
        graph: dict[LibraryObjectRef, set[LibraryObjectRef]],
    ) -> set[LibraryObjectRef]:
        program_refs = self._object_refs_locked(owner)
        known.update(program_refs)
        for ref in program_refs:
            graph.setdefault(ref, set())
        for entry in tuple(owner._index.values()):
            pair = owner._load_pair_by_refs_locked(
                entry.blob_ref,
                entry.manifest_ref,
                source=CorruptionDetectionSource.VERIFIED_READ,
                require_committed=True,
                verify_program=False,
            )
            reference = self._reference_for_unit(pair.unit)
            if reference is not None:
                program_ref = self._object_ref(reference)
                known.add(program_ref)
                graph.setdefault(entry.manifest_ref, set()).add(program_ref)
                graph.setdefault(program_ref, set())
        return program_refs


def create_library_program_artifact_lifecycle() -> LibraryProgramArtifactLifecycle:
    return LibraryProgramArtifactLifecycle()


__all__ = [
    "LIBRARY_PROGRAM_ARTIFACT_INGESTION_V1",
    "LibraryProgramArtifactLifecycle",
    "ProgramArtifactIngestion",
    "ProgramArtifactWriteAuthority",
    "create_library_program_artifact_lifecycle",
]
