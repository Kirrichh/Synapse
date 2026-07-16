"""Immutable Stage 4 Gold Behavior Library.

The library stores validated Behavior core bytes and manifest envelopes in
separate immutable CAS namespaces.  Its index is derived metadata only: every
load revalidates the stored bytes, ContentKey, manifest identity, compiler
binding, committed journal transaction, and quarantine state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Iterable

from synapse.version import LANGUAGE_VERSION

from .behavior import (
    BehaviorBlob,
    BehaviorManifest,
    SynapseBehaviorUnit,
    behavior_manifest_from_dict,
    behavior_unit_from_dict,
    compiler_binding_from_dict_for_unit,
    compiler_binding_to_dict_for_unit,
    create_behavior_blob,
    validate_behavior_blob,
    validate_behavior_unit,
)
from .canonicalization import (
    BEHAVIOR_CORE_SCHEMA_V1,
    CANONICAL_PROGRAM_IR_V1,
    COMPILER_ADAPTER_PROFILE_V1,
    CONTENT_KEY_TEXT_PREFIX,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    CanonicalizationViolation,
    ContentKey,
    canonicalize_stage4_payload,
    compute_content_key,
    decode_stage4_canonical_bytes,
)
from .contracts import IdentityDomain, RecordId, SchemaVersion
from .persistence import (
    IntegrityManifestDescriptor,
    PersistenceFailureCode,
    PersistenceViolation,
    DurabilityProfile,
    ExclusiveStoreLock,
    LIBRARY_INTEGRITY_MANIFEST_V1,
    MAX_METADATA_BYTES_V1,
    StagedFile,
    active_durability_profile,
    append_journal_payload,
    atomic_replace_metadata,
    ensure_directory,
    initialize_journal,
    move_immutable,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    require_directory,
    require_regular_file,
    scan_journal,
    truncate_journal_to_valid_prefix,
    write_staged_bytes,
)


LIBRARY_MANIFEST_OBJECT_V1 = "synapse.stage4.gold.library-manifest-object/v1"
LIBRARY_INDEX_V1 = "synapse.stage4.gold.library-index/v1"
LIBRARY_INDEX_ENTRY_V1 = "synapse.stage4.gold.library-index-entry/v1"
LIBRARY_SNAPSHOT_V1 = "synapse.stage4.gold.library-snapshot/v1"
LIBRARY_CORRUPTION_RECORD_V1 = "synapse.stage4.gold.library-corruption-record/v1"
LIBRARY_PUBLISHER_IDENTITY_V1 = "synapse.stage4.gold.library-publisher-identity/v1"
LIBRARY_RETENTION_ROOTS_V1 = "synapse.stage4.gold.library-retention-roots/v1"
LIBRARY_GC_PLAN_V1 = "synapse.stage4.gold.library-gc-plan/v1"
LIBRARY_JOURNAL_RECORD_V1 = "synapse.stage4.gold.library-journal-record/v1"

MAX_BLOB_OBJECT_BYTES_V1 = 4_194_304
MAX_MANIFEST_OBJECT_BYTES_V1 = 4_194_304
MAX_INDEX_ENTRIES_V1 = 100_000
MAX_GC_REFS_V1 = 100_000

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_COMPONENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSION_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z")
_OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_CONTENT_KEY_RE = re.compile(re.escape(CONTENT_KEY_TEXT_PREFIX) + r"[0-9a-f]{64}\Z")
_MANIFEST_ID_PREFIX = IdentityDomain.BEHAVIOR_MANIFEST.value + ":"
_MANIFEST_ID_RE = re.compile(re.escape(_MANIFEST_ID_PREFIX) + r"[0-9a-f]{64}\Z")
_TRUSTED_LIBRARY_SEAL = object()


class LibraryFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_ENUM_VALUE = "UNKNOWN_ENUM_VALUE"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_STORE_ROOT = "INVALID_STORE_ROOT"
    INVALID_STORE_ENTRY = "INVALID_STORE_ENTRY"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    PUBLISHER_MISMATCH = "PUBLISHER_MISMATCH"
    WORKER_WRITE_FORBIDDEN = "WORKER_WRITE_FORBIDDEN"
    CONTENT_KEY_MISMATCH = "CONTENT_KEY_MISMATCH"
    MANIFEST_ID_MISMATCH = "MANIFEST_ID_MISMATCH"
    MANIFEST_BLOB_MISMATCH = "MANIFEST_BLOB_MISMATCH"
    EXISTING_OBJECT_MISMATCH = "EXISTING_OBJECT_MISMATCH"
    INDEX_MALFORMED = "INDEX_MALFORMED"
    INDEX_STALE = "INDEX_STALE"
    INDEX_POISONED = "INDEX_POISONED"
    BLOB_MISSING = "BLOB_MISSING"
    ORPHAN_MANIFEST = "ORPHAN_MANIFEST"
    OBJECT_CORRUPT = "OBJECT_CORRUPT"
    OBJECT_QUARANTINED = "OBJECT_QUARANTINED"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    JOURNAL_SEQUENCE_MISMATCH = "JOURNAL_SEQUENCE_MISMATCH"
    JOURNAL_TRANSITION_INVALID = "JOURNAL_TRANSITION_INVALID"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    SNAPSHOT_UNANCHORED = "SNAPSHOT_UNANCHORED"
    SNAPSHOT_ROLLBACK = "SNAPSHOT_ROLLBACK"
    SNAPSHOT_MIXED_ROOTS = "SNAPSHOT_MIXED_ROOTS"
    GC_ROOT_INVALID = "GC_ROOT_INVALID"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


class LibraryViolation(RuntimeError):
    """Typed fail-closed library error with non-payload detail."""

    def __init__(self, failure_code: LibraryFailureCode, detail: str) -> None:
        if type(failure_code) is not LibraryFailureCode:
            raise TypeError("failure_code must be an exact LibraryFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: LibraryFailureCode, detail: str) -> LibraryViolation:
    return LibraryViolation(code, detail)


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


def _exact_dict(value: object, fields: tuple[str, ...], field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact dict")
    if any(type(key) is not str for key in value) or set(value) != set(fields):
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, f"{field_name} fields are invalid")
    return value


def _exact_list(value: object, field_name: str, *, limit: int = MAX_GC_REFS_V1) -> list[object]:
    if type(value) is not list:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact list")
    if len(value) > limit:
        raise _fail(LibraryFailureCode.RESOURCE_LIMIT_EXCEEDED, f"{field_name} exceeds item limit")
    return value


def _sha256(value: object, field_name: str, *, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, f"{field_name} is not lowercase SHA-256")
    return value


def _safe_id(value: object, field_name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, f"{field_name} is invalid")
    return value


def _component_id(value: object, field_name: str) -> str:
    if type(value) is not str or _COMPONENT_ID_RE.fullmatch(value) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, f"{field_name} is invalid")
    return value


def _version_id(value: object, field_name: str) -> str:
    if type(value) is not str or _VERSION_ID_RE.fullmatch(value) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, f"{field_name} is invalid")
    return value


def _positive_int(value: object, field_name: str, *, allow_zero: bool = False) -> int:
    lower = 0 if allow_zero else 1
    if type(value) is not int or value < lower:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, f"{field_name} is invalid")
    return value


def _enum(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    if type(value) is not str:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, f"{field_name} is unknown") from exc


@dataclass(frozen=True)
class PublisherIdentity:
    schema_version: str
    component_id: str
    policy_version: str

    def __post_init__(self) -> None:
        _validate_publisher(self)

    def to_dict(self) -> dict[str, str]:
        _validate_publisher(self)
        return {
            "schema_version": self.schema_version,
            "component_id": self.component_id,
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> PublisherIdentity:
        data = _exact_dict(value, ("schema_version", "component_id", "policy_version"), "publisher_identity")
        return cls(data["schema_version"], data["component_id"], data["policy_version"])


def _validate_publisher(value: PublisherIdentity) -> None:
    if type(value) is not PublisherIdentity:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "publisher must be exact PublisherIdentity")
    if value.schema_version != LIBRARY_PUBLISHER_IDENTITY_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "publisher schema is unknown")
    _component_id(value.component_id, "publisher.component_id")
    _version_id(value.policy_version, "publisher.policy_version")


class LibraryObjectNamespace(str, Enum):
    BLOB = "BLOB"
    MANIFEST = "MANIFEST"


@dataclass(frozen=True, order=True)
class LibraryObjectRef:
    namespace: LibraryObjectNamespace
    digest_sha256: str

    def __post_init__(self) -> None:
        _validate_object_ref(self)

    def to_dict(self) -> dict[str, str]:
        _validate_object_ref(self)
        return {"namespace": self.namespace.value, "digest_sha256": self.digest_sha256}

    @classmethod
    def from_dict(cls, value: object) -> LibraryObjectRef:
        data = _exact_dict(value, ("namespace", "digest_sha256"), "library_object_ref")
        namespace = _enum(data["namespace"], LibraryObjectNamespace, "object_ref.namespace")
        assert isinstance(namespace, LibraryObjectNamespace)
        return cls(namespace, data["digest_sha256"])


def _validate_object_ref(value: LibraryObjectRef) -> None:
    if type(value) is not LibraryObjectRef:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "object ref must be exact LibraryObjectRef")
    if type(value.namespace) is not LibraryObjectNamespace:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "object namespace is invalid")
    _sha256(value.digest_sha256, "object_ref.digest_sha256")


@dataclass(frozen=True)
class LifecyclePointer:
    schema_id: str
    record_id: str
    sha256: str

    def __post_init__(self) -> None:
        _version_id(self.schema_id, "lifecycle.schema_id")
        _safe_id(self.record_id, "lifecycle.record_id")
        _sha256(self.sha256, "lifecycle.sha256")

    def to_dict(self) -> dict[str, str]:
        return {"schema_id": self.schema_id, "record_id": self.record_id, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: object) -> LifecyclePointer:
        data = _exact_dict(value, ("schema_id", "record_id", "sha256"), "lifecycle_pointer")
        return cls(data["schema_id"], data["record_id"], data["sha256"])


@dataclass(frozen=True)
class IndexEntry:
    schema_version: str
    index_version: int
    content_key: str
    manifest_id: str
    behavior_kind: str
    blob_ref: LibraryObjectRef
    manifest_ref: LibraryObjectRef
    lifecycle_pointer: LifecyclePointer | None

    def __post_init__(self) -> None:
        _validate_index_entry(self)

    def to_dict(self) -> dict[str, object]:
        _validate_index_entry(self)
        return {
            "schema_version": self.schema_version,
            "index_version": self.index_version,
            "content_key": self.content_key,
            "manifest_id": self.manifest_id,
            "behavior_kind": self.behavior_kind,
            "blob_ref": self.blob_ref.to_dict(),
            "manifest_ref": self.manifest_ref.to_dict(),
            "lifecycle_pointer": None if self.lifecycle_pointer is None else self.lifecycle_pointer.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> IndexEntry:
        data = _exact_dict(
            value,
            (
                "schema_version",
                "index_version",
                "content_key",
                "manifest_id",
                "behavior_kind",
                "blob_ref",
                "manifest_ref",
                "lifecycle_pointer",
            ),
            "index_entry",
        )
        lifecycle = (
            None
            if data["lifecycle_pointer"] is None
            else LifecyclePointer.from_dict(data["lifecycle_pointer"])
        )
        return cls(
            data["schema_version"],
            data["index_version"],
            data["content_key"],
            data["manifest_id"],
            data["behavior_kind"],
            LibraryObjectRef.from_dict(data["blob_ref"]),
            LibraryObjectRef.from_dict(data["manifest_ref"]),
            lifecycle,
        )


def _validate_index_entry(value: IndexEntry) -> None:
    if type(value) is not IndexEntry:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "index entry type is invalid")
    if value.schema_version != LIBRARY_INDEX_ENTRY_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "index entry schema is unknown")
    if value.index_version != 1 or type(value.index_version) is not int:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "index version is unknown")
    if type(value.content_key) is not str or _CONTENT_KEY_RE.fullmatch(value.content_key) is None:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index content key is malformed")
    if type(value.manifest_id) is not str or _MANIFEST_ID_RE.fullmatch(value.manifest_id) is None:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index manifest id is malformed")
    _safe_id(value.behavior_kind, "index.behavior_kind")
    _validate_object_ref(value.blob_ref)
    _validate_object_ref(value.manifest_ref)
    if value.blob_ref.namespace is not LibraryObjectNamespace.BLOB:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index blob namespace is invalid")
    if value.manifest_ref.namespace is not LibraryObjectNamespace.MANIFEST:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index manifest namespace is invalid")
    if value.content_key[len(CONTENT_KEY_TEXT_PREFIX) :] != value.blob_ref.digest_sha256:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index blob key/ref mismatch")
    if value.manifest_id[len(_MANIFEST_ID_PREFIX) :] != value.manifest_ref.digest_sha256:
        raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index manifest id/ref mismatch")
    if value.lifecycle_pointer is not None and type(value.lifecycle_pointer) is not LifecyclePointer:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "lifecycle pointer type is invalid")


@dataclass(frozen=True)
class LibrarySnapshot:
    schema_version: str
    generation: int
    committed_journal_sequence: int
    blob_store_root_sha256: str
    manifest_store_root_sha256: str
    index_sha256: str
    integrity_manifest_sha256: str

    def __post_init__(self) -> None:
        _validate_snapshot(self)

    def to_dict(self) -> dict[str, object]:
        _validate_snapshot(self)
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "committed_journal_sequence": self.committed_journal_sequence,
            "blob_store_root_sha256": self.blob_store_root_sha256,
            "manifest_store_root_sha256": self.manifest_store_root_sha256,
            "index_sha256": self.index_sha256,
            "integrity_manifest_sha256": self.integrity_manifest_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> LibrarySnapshot:
        data = _exact_dict(
            value,
            (
                "schema_version",
                "generation",
                "committed_journal_sequence",
                "blob_store_root_sha256",
                "manifest_store_root_sha256",
                "index_sha256",
                "integrity_manifest_sha256",
            ),
            "library_snapshot",
        )
        return cls(
            data["schema_version"],
            data["generation"],
            data["committed_journal_sequence"],
            data["blob_store_root_sha256"],
            data["manifest_store_root_sha256"],
            data["index_sha256"],
            data["integrity_manifest_sha256"],
        )


def _validate_snapshot(value: LibrarySnapshot) -> None:
    if type(value) is not LibrarySnapshot:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "snapshot type is invalid")
    if value.schema_version != LIBRARY_SNAPSHOT_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "snapshot schema is unknown")
    _positive_int(value.generation, "snapshot.generation", allow_zero=True)
    _positive_int(value.committed_journal_sequence, "snapshot.committed_journal_sequence", allow_zero=True)
    _sha256(value.blob_store_root_sha256, "snapshot.blob_store_root_sha256")
    _sha256(value.manifest_store_root_sha256, "snapshot.manifest_store_root_sha256")
    _sha256(value.index_sha256, "snapshot.index_sha256")
    _sha256(value.integrity_manifest_sha256, "snapshot.integrity_manifest_sha256")


class SnapshotVerificationStatus(str, Enum):
    UNANCHORED = "UNANCHORED"
    VERIFIED_SAME = "VERIFIED_SAME"
    VERIFIED_FORWARD = "VERIFIED_FORWARD"


@dataclass(frozen=True, init=False)
class SnapshotVerification:
    status: SnapshotVerificationStatus
    snapshot: LibrarySnapshot
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotVerification:
        raise TypeError("SnapshotVerification is created only by BehaviorLibrary.current_snapshot")


def _validate_snapshot_verification(value: SnapshotVerification) -> None:
    if type(value) is not SnapshotVerification or getattr(value, "_trusted_seal", None) is not _TRUSTED_LIBRARY_SEAL:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "snapshot verification is not library sealed")
    if type(value.status) is not SnapshotVerificationStatus or type(value.snapshot) is not LibrarySnapshot:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "snapshot verification is invalid")
    _validate_snapshot(value.snapshot)


def _make_snapshot_verification(
    status: SnapshotVerificationStatus,
    snapshot: LibrarySnapshot,
) -> SnapshotVerification:
    value = object.__new__(SnapshotVerification)
    object.__setattr__(value, "status", status)
    object.__setattr__(value, "snapshot", snapshot)
    object.__setattr__(value, "_trusted_seal", _TRUSTED_LIBRARY_SEAL)
    _validate_snapshot_verification(value)
    return value


def validate_snapshot_verification(value: SnapshotVerification) -> None:
    _validate_snapshot_verification(value)


class CorruptionDetectionSource(str, Enum):
    PUT_EXISTING = "PUT_EXISTING"
    VERIFIED_READ = "VERIFIED_READ"
    INDEX_REBUILD = "INDEX_REBUILD"
    RECOVERY = "RECOVERY"


class CorruptionReason(str, Enum):
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    CONTENT_COLLISION = "CONTENT_COLLISION"
    MANIFEST_HASH_MISMATCH = "MANIFEST_HASH_MISMATCH"
    MANIFEST_BLOB_MISMATCH = "MANIFEST_BLOB_MISMATCH"
    NON_REGULAR_ENTRY = "NON_REGULAR_ENTRY"


class QuarantineAction(str, Enum):
    MOVED_PAYLOAD = "MOVED_PAYLOAD"
    LOGICAL_BLOCK = "LOGICAL_BLOCK"


@dataclass(frozen=True)
class CorruptionRecord:
    schema_version: str
    object_ref: LibraryObjectRef
    expected_sha256: str
    actual_sha256: str | None
    detection_source: CorruptionDetectionSource
    reason: CorruptionReason
    quarantine_action: QuarantineAction
    journal_sequence: int

    def __post_init__(self) -> None:
        _validate_corruption_record(self)

    def to_dict(self) -> dict[str, object]:
        _validate_corruption_record(self)
        return {
            "schema_version": self.schema_version,
            "object_ref": self.object_ref.to_dict(),
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "detection_source": self.detection_source.value,
            "reason": self.reason.value,
            "quarantine_action": self.quarantine_action.value,
            "journal_sequence": self.journal_sequence,
        }

    @classmethod
    def from_dict(cls, value: object) -> CorruptionRecord:
        data = _exact_dict(
            value,
            (
                "schema_version",
                "object_ref",
                "expected_sha256",
                "actual_sha256",
                "detection_source",
                "reason",
                "quarantine_action",
                "journal_sequence",
            ),
            "corruption_record",
        )
        source = _enum(data["detection_source"], CorruptionDetectionSource, "corruption.detection_source")
        reason = _enum(data["reason"], CorruptionReason, "corruption.reason")
        action = _enum(data["quarantine_action"], QuarantineAction, "corruption.quarantine_action")
        assert isinstance(source, CorruptionDetectionSource)
        assert isinstance(reason, CorruptionReason)
        assert isinstance(action, QuarantineAction)
        return cls(
            data["schema_version"],
            LibraryObjectRef.from_dict(data["object_ref"]),
            data["expected_sha256"],
            data["actual_sha256"],
            source,
            reason,
            action,
            data["journal_sequence"],
        )


def _validate_corruption_record(value: CorruptionRecord) -> None:
    if type(value) is not CorruptionRecord:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "corruption record type is invalid")
    if value.schema_version != LIBRARY_CORRUPTION_RECORD_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "corruption record schema is unknown")
    _validate_object_ref(value.object_ref)
    _sha256(value.expected_sha256, "corruption.expected_sha256")
    _sha256(value.actual_sha256, "corruption.actual_sha256", allow_none=True)
    if type(value.detection_source) is not CorruptionDetectionSource:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "corruption source is invalid")
    if type(value.reason) is not CorruptionReason:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "corruption reason is invalid")
    if type(value.quarantine_action) is not QuarantineAction:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "quarantine action is invalid")
    _positive_int(value.journal_sequence, "corruption.journal_sequence", allow_zero=True)


class RetentionRootKind(str, Enum):
    EVIDENCE = "EVIDENCE"
    LINEAGE = "LINEAGE"
    SNAPSHOT = "SNAPSHOT"
    LIFECYCLE = "LIFECYCLE"
    LEGAL_RETENTION = "LEGAL_RETENTION"


@dataclass(frozen=True)
class RetentionRootSet:
    schema_version: str
    root_kind: RetentionRootKind
    object_refs: tuple[LibraryObjectRef, ...]

    def __post_init__(self) -> None:
        _validate_retention_roots(self)

    def to_dict(self) -> dict[str, object]:
        _validate_retention_roots(self)
        return {
            "schema_version": self.schema_version,
            "root_kind": self.root_kind.value,
            "object_refs": [ref.to_dict() for ref in self.object_refs],
        }

    @classmethod
    def from_dict(cls, value: object) -> RetentionRootSet:
        data = _exact_dict(value, ("schema_version", "root_kind", "object_refs"), "retention_roots")
        kind = _enum(data["root_kind"], RetentionRootKind, "retention.root_kind")
        assert isinstance(kind, RetentionRootKind)
        refs = tuple(
            LibraryObjectRef.from_dict(item)
            for item in _exact_list(data["object_refs"], "retention.object_refs")
        )
        return cls(data["schema_version"], kind, refs)


def _validate_retention_roots(value: RetentionRootSet) -> None:
    if type(value) is not RetentionRootSet:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "retention roots type is invalid")
    if value.schema_version != LIBRARY_RETENTION_ROOTS_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "retention roots schema is unknown")
    if type(value.root_kind) is not RetentionRootKind:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "retention root kind is invalid")
    if type(value.object_refs) is not tuple or len(value.object_refs) > MAX_GC_REFS_V1:
        raise _fail(LibraryFailureCode.RESOURCE_LIMIT_EXCEEDED, "retention roots exceed limit")
    if any(type(ref) is not LibraryObjectRef for ref in value.object_refs):
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "retention object ref type is invalid")
    if tuple(sorted(value.object_refs)) != value.object_refs or len(set(value.object_refs)) != len(value.object_refs):
        raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "retention roots must be sorted and unique")


@dataclass(frozen=True)
class GarbageCollectionPlan:
    schema_version: str
    retained_refs: tuple[LibraryObjectRef, ...]
    deletion_candidates: tuple[LibraryObjectRef, ...]
    canonical_bytes: bytes
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != LIBRARY_GC_PLAN_V1 or type(self.schema_version) is not str:
            raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "GC plan schema is unknown")
        for refs, field_name in (
            (self.retained_refs, "retained_refs"),
            (self.deletion_candidates, "deletion_candidates"),
        ):
            if type(refs) is not tuple or any(type(ref) is not LibraryObjectRef for ref in refs):
                raise _fail(LibraryFailureCode.TYPE_MISMATCH, f"GC {field_name} is invalid")
            if tuple(sorted(refs)) != refs or len(set(refs)) != len(refs):
                raise _fail(LibraryFailureCode.GC_ROOT_INVALID, f"GC {field_name} must be sorted and unique")
        if set(self.retained_refs) & set(self.deletion_candidates):
            raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "GC retained and candidate sets overlap")
        if type(self.canonical_bytes) is not bytes:
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "GC canonical bytes must be exact bytes")
        _sha256(self.plan_sha256, "gc.plan_sha256")
        payload = {
            "schema_version": self.schema_version,
            "retained_refs": [ref.to_dict() for ref in self.retained_refs],
            "deletion_candidates": [ref.to_dict() for ref in self.deletion_candidates],
        }
        expected = _canonical(payload)
        if expected != self.canonical_bytes or hashlib.sha256(expected).hexdigest() != self.plan_sha256:
            raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "GC plan identity mismatch")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "retained_refs": [ref.to_dict() for ref in self.retained_refs],
            "deletion_candidates": [ref.to_dict() for ref in self.deletion_candidates],
            "plan_sha256": self.plan_sha256,
        }


class PutStatus(str, Enum):
    STORED = "STORED"
    DEDUPLICATED = "DEDUPLICATED"


@dataclass(frozen=True, init=False)
class PutResult:
    status: PutStatus
    content_key: ContentKey
    manifest_id: RecordId
    operation_id: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PutResult:
        raise TypeError("PutResult is created only by a committed BehaviorLibrary transaction")


def _validate_put_result(value: PutResult) -> None:
    if type(value) is not PutResult or getattr(value, "_trusted_seal", None) is not _TRUSTED_LIBRARY_SEAL:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "put result is not library sealed")
    if type(value.status) is not PutStatus:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "put status type is invalid")
    if type(value.content_key) is not ContentKey or type(value.manifest_id) is not RecordId:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "put identities are invalid")
    if type(value.operation_id) is not str or _OPERATION_ID_RE.fullmatch(value.operation_id) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, "put operation id is invalid")
    value.content_key.to_dict()
    value.manifest_id.to_dict()


def _make_put_result(
    status: PutStatus,
    content_key: ContentKey,
    manifest_id: RecordId,
    operation_id: str,
) -> PutResult:
    value = object.__new__(PutResult)
    object.__setattr__(value, "status", status)
    object.__setattr__(value, "content_key", content_key)
    object.__setattr__(value, "manifest_id", manifest_id)
    object.__setattr__(value, "operation_id", operation_id)
    object.__setattr__(value, "_trusted_seal", _TRUSTED_LIBRARY_SEAL)
    _validate_put_result(value)
    return value


def validate_put_result(value: PutResult) -> None:
    _validate_put_result(value)


@dataclass(frozen=True, init=False)
class VerifiedBehaviorRecord:
    unit: SynapseBehaviorUnit
    blob: BehaviorBlob
    manifest: BehaviorManifest
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> VerifiedBehaviorRecord:
        raise TypeError("VerifiedBehaviorRecord is created only by a verified BehaviorLibrary read")


def _validate_verified_behavior_record(value: VerifiedBehaviorRecord) -> None:
    if type(value) is not VerifiedBehaviorRecord or getattr(value, "_trusted_seal", None) is not _TRUSTED_LIBRARY_SEAL:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "verified behavior record is not library sealed")
    validate_behavior_unit(value.unit)
    validate_behavior_blob(value.blob, unit=value.unit)
    value.manifest.to_dict(unit=value.unit, blob=value.blob)


def _make_verified_behavior_record(
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
) -> VerifiedBehaviorRecord:
    value = object.__new__(VerifiedBehaviorRecord)
    object.__setattr__(value, "unit", unit)
    object.__setattr__(value, "blob", blob)
    object.__setattr__(value, "manifest", manifest)
    object.__setattr__(value, "_trusted_seal", _TRUSTED_LIBRARY_SEAL)
    _validate_verified_behavior_record(value)
    return value


def validate_verified_behavior_record(value: VerifiedBehaviorRecord) -> None:
    _validate_verified_behavior_record(value)


class LibraryJournalPhase(str, Enum):
    BEGIN = "BEGIN"
    BLOB_STAGED = "BLOB_STAGED"
    MANIFEST_STAGED = "MANIFEST_STAGED"
    BLOB_PUBLISHED = "BLOB_PUBLISHED"
    MANIFEST_PUBLISHED = "MANIFEST_PUBLISHED"
    METADATA_PUBLISHED = "METADATA_PUBLISHED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    CLEANED = "CLEANED"


_ALLOWED_TRANSITIONS: dict[LibraryJournalPhase, frozenset[LibraryJournalPhase]] = {
    LibraryJournalPhase.BEGIN: frozenset((LibraryJournalPhase.BLOB_STAGED, LibraryJournalPhase.ABORTED)),
    LibraryJournalPhase.BLOB_STAGED: frozenset((LibraryJournalPhase.MANIFEST_STAGED, LibraryJournalPhase.ABORTED)),
    LibraryJournalPhase.MANIFEST_STAGED: frozenset((LibraryJournalPhase.BLOB_PUBLISHED, LibraryJournalPhase.ABORTED)),
    LibraryJournalPhase.BLOB_PUBLISHED: frozenset(
        (LibraryJournalPhase.MANIFEST_PUBLISHED, LibraryJournalPhase.ABORTED)
    ),
    LibraryJournalPhase.MANIFEST_PUBLISHED: frozenset(
        (LibraryJournalPhase.METADATA_PUBLISHED, LibraryJournalPhase.ABORTED)
    ),
    LibraryJournalPhase.METADATA_PUBLISHED: frozenset((LibraryJournalPhase.COMMITTED, LibraryJournalPhase.ABORTED)),
    LibraryJournalPhase.COMMITTED: frozenset((LibraryJournalPhase.CLEANED,)),
    LibraryJournalPhase.ABORTED: frozenset((LibraryJournalPhase.CLEANED,)),
    LibraryJournalPhase.CLEANED: frozenset(),
}


@dataclass(frozen=True)
class LibraryJournalRecord:
    schema_version: str
    sequence: int
    operation_id: str
    phase: LibraryJournalPhase
    blob_ref: LibraryObjectRef
    manifest_ref: LibraryObjectRef
    blob_sha256: str
    manifest_sha256: str
    publisher_component_id: str
    publisher_policy_version: str

    def __post_init__(self) -> None:
        _validate_journal_record(self)

    def to_dict(self) -> dict[str, object]:
        _validate_journal_record(self)
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "operation_id": self.operation_id,
            "phase": self.phase.value,
            "blob_ref": self.blob_ref.to_dict(),
            "manifest_ref": self.manifest_ref.to_dict(),
            "blob_sha256": self.blob_sha256,
            "manifest_sha256": self.manifest_sha256,
            "publisher_component_id": self.publisher_component_id,
            "publisher_policy_version": self.publisher_policy_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> LibraryJournalRecord:
        data = _exact_dict(
            value,
            (
                "schema_version",
                "sequence",
                "operation_id",
                "phase",
                "blob_ref",
                "manifest_ref",
                "blob_sha256",
                "manifest_sha256",
                "publisher_component_id",
                "publisher_policy_version",
            ),
            "library_journal_record",
        )
        phase = _enum(data["phase"], LibraryJournalPhase, "journal.phase")
        assert isinstance(phase, LibraryJournalPhase)
        return cls(
            data["schema_version"],
            data["sequence"],
            data["operation_id"],
            phase,
            LibraryObjectRef.from_dict(data["blob_ref"]),
            LibraryObjectRef.from_dict(data["manifest_ref"]),
            data["blob_sha256"],
            data["manifest_sha256"],
            data["publisher_component_id"],
            data["publisher_policy_version"],
        )


def _validate_journal_record(value: LibraryJournalRecord) -> None:
    if type(value) is not LibraryJournalRecord:
        raise _fail(LibraryFailureCode.TYPE_MISMATCH, "journal record type is invalid")
    if value.schema_version != LIBRARY_JOURNAL_RECORD_V1 or type(value.schema_version) is not str:
        raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "journal record schema is unknown")
    _positive_int(value.sequence, "journal.sequence")
    if type(value.operation_id) is not str or _OPERATION_ID_RE.fullmatch(value.operation_id) is None:
        raise _fail(LibraryFailureCode.INVALID_IDENTIFIER, "journal operation id is invalid")
    if type(value.phase) is not LibraryJournalPhase:
        raise _fail(LibraryFailureCode.UNKNOWN_ENUM_VALUE, "journal phase is invalid")
    _validate_object_ref(value.blob_ref)
    _validate_object_ref(value.manifest_ref)
    if value.blob_ref.namespace is not LibraryObjectNamespace.BLOB:
        raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "journal blob namespace is invalid")
    if value.manifest_ref.namespace is not LibraryObjectNamespace.MANIFEST:
        raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "journal manifest namespace is invalid")
    _sha256(value.blob_sha256, "journal.blob_sha256")
    _sha256(value.manifest_sha256, "journal.manifest_sha256")
    _component_id(value.publisher_component_id, "journal.publisher_component_id")
    _version_id(value.publisher_policy_version, "journal.publisher_policy_version")


@dataclass
class _OperationState:
    template: LibraryJournalRecord
    phase: LibraryJournalPhase


def _same_operation(left: LibraryJournalRecord, right: LibraryJournalRecord) -> bool:
    return (
        left.operation_id == right.operation_id
        and left.blob_ref == right.blob_ref
        and left.manifest_ref == right.manifest_ref
        and left.blob_sha256 == right.blob_sha256
        and left.manifest_sha256 == right.manifest_sha256
        and left.publisher_component_id == right.publisher_component_id
        and left.publisher_policy_version == right.publisher_policy_version
    )


def _empty_root() -> str:
    return hashlib.sha256(_canonical([])).hexdigest()


class BehaviorLibrary:
    """One locally serialized immutable Behavior store."""

    def __init__(self, root: Path, *, publisher_identity: PublisherIdentity) -> None:
        if not isinstance(root, Path):
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "library root must be a Path")
        _validate_publisher(publisher_identity)
        self._root = root
        self._publisher_identity = publisher_identity
        self._objects = root / "objects"
        self._blobs = self._objects / "blobs"
        self._manifests = self._objects / "manifests"
        self._metadata = root / "metadata"
        self._journal_directory = root / "journal"
        self._journal_path = self._journal_directory / "library.v1"
        self._lock_path = self._journal_directory / "writer.lock"
        self._quarantine = root / "quarantine"
        self._quarantine_records = self._quarantine / "records"
        self._quarantine_payloads = self._quarantine / "payloads"
        self._index: dict[str, IndexEntry] = {}
        self._records: list[LibraryJournalRecord] = []
        self._operations: dict[str, _OperationState] = {}
        self._committed_pairs: dict[tuple[str, str], str] = {}
        self._quarantined: set[LibraryObjectRef] = set()
        self._generation = 0
        self._snapshot = LibrarySnapshot(
            LIBRARY_SNAPSHOT_V1,
            0,
            0,
            _empty_root(),
            _empty_root(),
            hashlib.sha256(
                _canonical(
                    {
                        "schema_version": LIBRARY_INDEX_V1,
                        "index_version": 1,
                        "generation": 0,
                        "entries": [],
                    }
                )
            ).hexdigest(),
            hashlib.sha256(b"").hexdigest(),
        )
        self._initialize_layout()
        with self._lock():
            self._load_quarantine_locked()
            self._load_journal_locked(repair_torn=True)
            metadata_valid = self._load_metadata_locked()
            recovery_changed = self._recover_locked()
            self._rebuild_index_locked(
                write_metadata=True,
                force_metadata=(not metadata_valid or recovery_changed),
            )

    @property
    def root(self) -> Path:
        return self._root

    def _lock(self) -> ExclusiveStoreLock:
        return ExclusiveStoreLock(self._lock_path)

    def _initialize_layout(self) -> None:
        try:
            require_directory(self._root)
            for directory in (
                self._objects,
                self._blobs,
                self._manifests,
                self._metadata,
                self._journal_directory,
                self._quarantine,
                self._quarantine_records,
                self._quarantine_payloads,
            ):
                ensure_directory(directory)
            initialize_journal(self._journal_path)
        except PersistenceViolation as exc:
            raise _fail(LibraryFailureCode.INVALID_STORE_ROOT, "library layout initialization failed") from exc

    def _require_publisher(self, provided: object) -> PublisherIdentity:
        if type(provided) is not PublisherIdentity:
            raise _fail(LibraryFailureCode.WORKER_WRITE_FORBIDDEN, "write requires configured publisher handle")
        _validate_publisher(provided)
        if provided is not self._publisher_identity:
            raise _fail(LibraryFailureCode.PUBLISHER_MISMATCH, "publisher handle is not configured instance")
        return provided

    def _object_base(self, namespace: LibraryObjectNamespace) -> Path:
        if namespace is LibraryObjectNamespace.BLOB:
            return self._blobs
        if namespace is LibraryObjectNamespace.MANIFEST:
            return self._manifests
        raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "object namespace is unknown")

    def _path_for_ref(self, ref: LibraryObjectRef, *, create_shard: bool) -> Path:
        _validate_object_ref(ref)
        base = self._object_base(ref.namespace)
        shard = base / ref.digest_sha256[:2]
        if create_shard:
            ensure_directory(shard)
        else:
            try:
                require_directory(shard)
            except PersistenceViolation as exc:
                code = (
                    LibraryFailureCode.BLOB_MISSING
                    if ref.namespace is LibraryObjectNamespace.BLOB
                    else LibraryFailureCode.ORPHAN_MANIFEST
                )
                raise _fail(code, "referenced object shard is unavailable") from exc
        return shard / ref.digest_sha256[2:]

    @staticmethod
    def _ref_for(content_key: ContentKey, manifest_id: RecordId) -> tuple[LibraryObjectRef, LibraryObjectRef]:
        content_key.to_dict()
        manifest_id.to_dict()
        return (
            LibraryObjectRef(LibraryObjectNamespace.BLOB, content_key.digest_sha256),
            LibraryObjectRef(LibraryObjectNamespace.MANIFEST, manifest_id.digest_sha256),
        )

    def _quarantine_path(self, base: Path, digest: str, *, create_shard: bool) -> Path:
        _sha256(digest, "quarantine.digest")
        shard = base / digest[:2]
        if create_shard:
            ensure_directory(shard)
        else:
            require_directory(shard)
        return shard / digest[2:]

    def _put_raw_immutable(self, base: Path, raw: bytes) -> Path:
        if type(raw) is not bytes:
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "immutable raw value must be exact bytes")
        digest = hashlib.sha256(raw).hexdigest()
        path = self._quarantine_path(base, digest, create_shard=True)
        if path.exists() or path.is_symlink():
            observed = read_regular_bytes(path, maximum_bytes=max(len(raw), 1))
            if observed != raw:
                raise _fail(LibraryFailureCode.EXISTING_OBJECT_MISMATCH, "immutable evidence address collision")
            return path
        staged = write_staged_bytes(
            path.parent,
            final_name=path.name,
            operation_id=new_operation_id(),
            value=raw,
            maximum_bytes=max(len(raw), 1),
        )
        publish_immutable(staged, path)
        return path

    def _load_quarantine_locked(self) -> None:
        quarantined: set[LibraryObjectRef] = set()
        for shard in sorted(self._quarantine_records.iterdir(), key=lambda item: item.name):
            require_directory(shard)
            if re.fullmatch(r"[0-9a-f]{2}", shard.name) is None:
                raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "quarantine shard name is invalid")
            for item in sorted(shard.iterdir(), key=lambda entry: entry.name):
                if re.fullmatch(r"[0-9a-f]{62}", item.name) is None:
                    raise _fail(LibraryFailureCode.INVALID_STORE_ENTRY, "quarantine record name is invalid")
                raw = read_regular_bytes(item, maximum_bytes=MAX_METADATA_BYTES_V1)
                if hashlib.sha256(raw).hexdigest() != shard.name + item.name:
                    raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "quarantine record address mismatch")
                record = CorruptionRecord.from_dict(_decode(raw))
                quarantined.add(record.object_ref)
        self._quarantined = quarantined

    def _record_corruption_locked(
        self,
        ref: LibraryObjectRef,
        *,
        expected_sha256: str,
        actual_sha256: str | None,
        source: CorruptionDetectionSource,
        reason: CorruptionReason,
        existing_path: Path | None = None,
        raw_evidence: bytes | None = None,
    ) -> CorruptionRecord:
        action = QuarantineAction.LOGICAL_BLOCK
        if raw_evidence is not None:
            self._put_raw_immutable(self._quarantine_payloads, raw_evidence)
        if existing_path is not None and (existing_path.exists() or existing_path.is_symlink()):
            try:
                observed = read_regular_bytes(
                    existing_path,
                    maximum_bytes=(
                        MAX_BLOB_OBJECT_BYTES_V1
                        if ref.namespace is LibraryObjectNamespace.BLOB
                        else MAX_MANIFEST_OBJECT_BYTES_V1
                    ),
                )
                digest = hashlib.sha256(observed).hexdigest()
                destination = self._quarantine_path(self._quarantine_payloads, digest, create_shard=True)
                if destination.exists():
                    if read_regular_bytes(destination, maximum_bytes=max(len(observed), 1)) == observed:
                        action = QuarantineAction.LOGICAL_BLOCK
                    else:
                        raise _fail(LibraryFailureCode.EXISTING_OBJECT_MISMATCH, "quarantine collision")
                else:
                    move_immutable(existing_path, destination, maximum_bytes=max(len(observed), 1))
                    action = QuarantineAction.MOVED_PAYLOAD
            except (PersistenceViolation, LibraryViolation):
                action = QuarantineAction.LOGICAL_BLOCK
        record = CorruptionRecord(
            LIBRARY_CORRUPTION_RECORD_V1,
            ref,
            expected_sha256,
            actual_sha256,
            source,
            reason,
            action,
            self._records[-1].sequence if self._records else 0,
        )
        self._put_raw_immutable(self._quarantine_records, _canonical(record.to_dict()))
        self._quarantined.add(ref)
        self._index = {
            key: entry
            for key, entry in self._index.items()
            if entry.blob_ref != ref and entry.manifest_ref != ref
        }
        return record

    def _manifest_envelope(
        self,
        unit: SynapseBehaviorUnit,
        blob: BehaviorBlob,
        manifest: BehaviorManifest,
    ) -> bytes:
        validate_behavior_unit(unit)
        validate_behavior_blob(blob, unit=unit)
        manifest_transport = manifest.to_dict(unit=unit, blob=blob)
        binding_transport = (
            None
            if manifest.compiler_binding is None
            else compiler_binding_to_dict_for_unit(unit, manifest.compiler_binding)
        )
        behavior_manifest_from_dict(
            manifest_transport,
            unit=unit,
            blob=blob,
            compiler_binding=manifest.compiler_binding,
            binding_refs=manifest.binding_refs,
        )
        return _canonical(
            {
                "schema_version": LIBRARY_MANIFEST_OBJECT_V1,
                "manifest": manifest_transport,
                "compiler_binding": binding_transport,
            }
        )

    @staticmethod
    def _recompute_content_key(blob_bytes: bytes) -> ContentKey:
        return compute_content_key(
            canonical_behavior_core_bytes=blob_bytes,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
            core_schema_id=BEHAVIOR_CORE_SCHEMA_V1,
            ir_schema_id=CANONICAL_PROGRAM_IR_V1,
            language_version=LANGUAGE_VERSION,
            compiler_adapter_profile=COMPILER_ADAPTER_PROFILE_V1,
        )

    def _validate_write_inputs(
        self,
        unit: object,
        blob: object,
        manifest: object,
        publisher: object,
    ) -> tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest, PublisherIdentity, bytes]:
        configured = self._require_publisher(publisher)
        if (
            type(unit) is not SynapseBehaviorUnit
            or type(blob) is not BehaviorBlob
            or type(manifest) is not BehaviorManifest
        ):
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "write inputs must be exact Behavior objects")
        validate_behavior_unit(unit)
        validate_behavior_blob(blob, unit=unit)
        recomputed = self._recompute_content_key(blob.canonical_core_bytes)
        if (
            recomputed.value != unit.content_key.value
            or recomputed.value != blob.content_key.value
            or blob.canonical_core_bytes != unit.canonical_core.canonical_bytes
        ):
            raise _fail(LibraryFailureCode.CONTENT_KEY_MISMATCH, "write bytes and ContentKey mismatch")
        envelope = self._manifest_envelope(unit, blob, manifest)
        if len(blob.canonical_core_bytes) > MAX_BLOB_OBJECT_BYTES_V1:
            raise _fail(LibraryFailureCode.RESOURCE_LIMIT_EXCEEDED, "blob exceeds byte limit")
        if len(envelope) > MAX_MANIFEST_OBJECT_BYTES_V1:
            raise _fail(LibraryFailureCode.RESOURCE_LIMIT_EXCEEDED, "manifest exceeds byte limit")
        return unit, blob, manifest, configured, envelope

    def _decode_manifest_envelope(self, raw: bytes, unit: SynapseBehaviorUnit, blob: BehaviorBlob) -> BehaviorManifest:
        decoded = _exact_dict(
            _decode(raw),
            ("schema_version", "manifest", "compiler_binding"),
            "manifest_object",
        )
        if decoded["schema_version"] != LIBRARY_MANIFEST_OBJECT_V1 or type(decoded["schema_version"]) is not str:
            raise _fail(LibraryFailureCode.UNKNOWN_SCHEMA_VERSION, "manifest object schema is unknown")
        binding = (
            None
            if decoded["compiler_binding"] is None
            else compiler_binding_from_dict_for_unit(unit, decoded["compiler_binding"])
        )
        manifest_data = decoded["manifest"]
        if type(manifest_data) is not dict or "binding_refs" not in manifest_data:
            raise _fail(LibraryFailureCode.MANIFEST_BLOB_MISMATCH, "manifest transport is malformed")
        return behavior_manifest_from_dict(
            manifest_data,
            unit=unit,
            blob=blob,
            compiler_binding=binding,
            binding_refs=manifest_data["binding_refs"],
        )

    def _expected_object_bytes_sha256(
        self,
        blob_ref: LibraryObjectRef,
        manifest_ref: LibraryObjectRef,
        namespace: LibraryObjectNamespace,
    ) -> str:
        for record in reversed(self._records):
            if record.blob_ref == blob_ref and record.manifest_ref == manifest_ref:
                return record.blob_sha256 if namespace is LibraryObjectNamespace.BLOB else record.manifest_sha256
        return blob_ref.digest_sha256 if namespace is LibraryObjectNamespace.BLOB else manifest_ref.digest_sha256

    def _load_pair_by_refs_locked(
        self,
        blob_ref: LibraryObjectRef,
        manifest_ref: LibraryObjectRef,
        *,
        source: CorruptionDetectionSource,
        require_committed: bool,
    ) -> VerifiedBehaviorRecord:
        if blob_ref in self._quarantined or manifest_ref in self._quarantined:
            raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "requested object is quarantined")
        if require_committed and (blob_ref.digest_sha256, manifest_ref.digest_sha256) not in self._committed_pairs:
            raise _fail(LibraryFailureCode.INDEX_STALE, "requested pair has no committed library transaction")
        blob_path = self._path_for_ref(blob_ref, create_shard=False)
        manifest_path = self._path_for_ref(manifest_ref, create_shard=False)
        try:
            blob_raw = read_regular_bytes(blob_path, maximum_bytes=MAX_BLOB_OBJECT_BYTES_V1)
        except PersistenceViolation as exc:
            if exc.failure_code in (
                PersistenceFailureCode.NON_REGULAR_ENTRY,
                PersistenceFailureCode.LINK_OR_REPARSE_POINT,
            ):
                self._record_corruption_locked(
                    blob_ref,
                    expected_sha256=self._expected_object_bytes_sha256(
                        blob_ref,
                        manifest_ref,
                        LibraryObjectNamespace.BLOB,
                    ),
                    actual_sha256=None,
                    source=source,
                    reason=CorruptionReason.NON_REGULAR_ENTRY,
                    existing_path=blob_path,
                )
                raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "blob store entry is not a regular file") from exc
            raise _fail(LibraryFailureCode.BLOB_MISSING, "referenced blob is unavailable") from exc
        actual_blob_hash = hashlib.sha256(blob_raw).hexdigest()
        expected_blob_hash = self._expected_object_bytes_sha256(
            blob_ref,
            manifest_ref,
            LibraryObjectNamespace.BLOB,
        )
        if actual_blob_hash != expected_blob_hash:
            self._record_corruption_locked(
                blob_ref,
                expected_sha256=expected_blob_hash,
                actual_sha256=actual_blob_hash,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=blob_path,
            )
            raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "blob bytes do not match durable journal hash")
        try:
            content_key = self._recompute_content_key(blob_raw)
        except Exception as exc:
            self._record_corruption_locked(
                blob_ref,
                expected_sha256=expected_blob_hash,
                actual_sha256=actual_blob_hash,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=blob_path,
            )
            raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "blob content identity cannot be recomputed") from exc
        if content_key.digest_sha256 != blob_ref.digest_sha256:
            self._record_corruption_locked(
                blob_ref,
                expected_sha256=expected_blob_hash,
                actual_sha256=actual_blob_hash,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=blob_path,
            )
            raise _fail(LibraryFailureCode.CONTENT_KEY_MISMATCH, "blob bytes do not match requested ContentKey")
        try:
            core = _decode(blob_raw)
            unit = behavior_unit_from_dict(
                {
                    "schema_version": SchemaVersion.BEHAVIOR_UNIT_V1.value,
                    "core": core,
                    "content_key": content_key.to_dict(),
                }
            )
            blob = create_behavior_blob(unit)
            if blob.canonical_core_bytes != blob_raw:
                raise _fail(LibraryFailureCode.CONTENT_KEY_MISMATCH, "reconstructed blob bytes mismatch")
        except LibraryViolation:
            raise
        except Exception as exc:
            self._record_corruption_locked(
                blob_ref,
                expected_sha256=expected_blob_hash,
                actual_sha256=actual_blob_hash,
                source=source,
                reason=CorruptionReason.CONTENT_HASH_MISMATCH,
                existing_path=blob_path,
            )
            raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "blob canonical transport is invalid") from exc
        try:
            manifest_raw = read_regular_bytes(manifest_path, maximum_bytes=MAX_MANIFEST_OBJECT_BYTES_V1)
        except PersistenceViolation as exc:
            if exc.failure_code in (
                PersistenceFailureCode.NON_REGULAR_ENTRY,
                PersistenceFailureCode.LINK_OR_REPARSE_POINT,
            ):
                self._record_corruption_locked(
                    manifest_ref,
                    expected_sha256=self._expected_object_bytes_sha256(
                        blob_ref,
                        manifest_ref,
                        LibraryObjectNamespace.MANIFEST,
                    ),
                    actual_sha256=None,
                    source=source,
                    reason=CorruptionReason.NON_REGULAR_ENTRY,
                    existing_path=manifest_path,
                )
                raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "manifest store entry is not a regular file") from exc
            raise _fail(LibraryFailureCode.ORPHAN_MANIFEST, "referenced manifest is unavailable") from exc
        actual_manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
        expected_manifest_hash = self._expected_object_bytes_sha256(
            blob_ref,
            manifest_ref,
            LibraryObjectNamespace.MANIFEST,
        )
        if actual_manifest_hash != expected_manifest_hash:
            self._record_corruption_locked(
                manifest_ref,
                expected_sha256=expected_manifest_hash,
                actual_sha256=actual_manifest_hash,
                source=source,
                reason=CorruptionReason.MANIFEST_HASH_MISMATCH,
                existing_path=manifest_path,
            )
            raise _fail(LibraryFailureCode.OBJECT_CORRUPT, "manifest bytes do not match durable journal hash")
        try:
            manifest = self._decode_manifest_envelope(manifest_raw, unit, blob)
        except Exception as exc:
            self._record_corruption_locked(
                manifest_ref,
                expected_sha256=expected_manifest_hash,
                actual_sha256=actual_manifest_hash,
                source=source,
                reason=CorruptionReason.MANIFEST_BLOB_MISMATCH,
                existing_path=manifest_path,
            )
            raise _fail(LibraryFailureCode.MANIFEST_BLOB_MISMATCH, "manifest envelope is invalid") from exc
        if manifest.manifest_id.digest_sha256 != manifest_ref.digest_sha256:
            self._record_corruption_locked(
                manifest_ref,
                expected_sha256=expected_manifest_hash,
                actual_sha256=actual_manifest_hash,
                source=source,
                reason=CorruptionReason.MANIFEST_HASH_MISMATCH,
                existing_path=manifest_path,
            )
            raise _fail(LibraryFailureCode.MANIFEST_ID_MISMATCH, "manifest bytes do not match requested identity")
        return _make_verified_behavior_record(unit, blob, manifest)

    def _parse_journal_records(self, payloads: Iterable[bytes]) -> list[LibraryJournalRecord]:
        records: list[LibraryJournalRecord] = []
        operations: dict[str, _OperationState] = {}
        for expected_sequence, payload in enumerate(payloads, start=1):
            try:
                record = LibraryJournalRecord.from_dict(_decode(payload))
            except Exception as exc:
                raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "journal payload is invalid") from exc
            if record.sequence != expected_sequence:
                raise _fail(LibraryFailureCode.JOURNAL_SEQUENCE_MISMATCH, "journal sequence is not contiguous")
            if (
                record.publisher_component_id != self._publisher_identity.component_id
                or record.publisher_policy_version != self._publisher_identity.policy_version
            ):
                raise _fail(
                    LibraryFailureCode.PUBLISHER_MISMATCH,
                    "journal publisher does not match configured authority",
                )
            previous = operations.get(record.operation_id)
            if previous is None:
                if record.phase is not LibraryJournalPhase.BEGIN:
                    raise _fail(LibraryFailureCode.JOURNAL_TRANSITION_INVALID, "operation does not begin with BEGIN")
                operations[record.operation_id] = _OperationState(record, record.phase)
            else:
                if not _same_operation(previous.template, record):
                    raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "operation identity changed across phases")
                if record.phase not in _ALLOWED_TRANSITIONS[previous.phase]:
                    raise _fail(LibraryFailureCode.JOURNAL_TRANSITION_INVALID, "journal phase transition is invalid")
                previous.phase = record.phase
            records.append(record)
        return records

    def _rebuild_operation_state(self) -> None:
        operations: dict[str, _OperationState] = {}
        committed: dict[tuple[str, str], str] = {}
        for record in self._records:
            state = operations.get(record.operation_id)
            if state is None:
                state = _OperationState(record, record.phase)
                operations[record.operation_id] = state
            else:
                state.phase = record.phase
            if record.phase is LibraryJournalPhase.COMMITTED:
                committed[(record.blob_ref.digest_sha256, record.manifest_ref.digest_sha256)] = record.operation_id
        self._operations = operations
        self._committed_pairs = committed

    def _load_journal_locked(self, *, repair_torn: bool) -> None:
        try:
            scan = scan_journal(self._journal_path)
            if scan.torn_tail:
                if not repair_torn:
                    raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "journal contains torn trailing frame")
                self._put_raw_immutable(self._quarantine_payloads, scan.torn_tail)
                truncate_journal_to_valid_prefix(self._journal_path, scan.valid_prefix_length)
            self._records = self._parse_journal_records(frame.payload for frame in scan.frames)
            self._rebuild_operation_state()
        except LibraryViolation:
            raise
        except PersistenceViolation as exc:
            raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "journal framing or checksum is invalid") from exc

    def _append_phase_locked(
        self,
        template: LibraryJournalRecord,
        phase: LibraryJournalPhase,
    ) -> LibraryJournalRecord:
        previous = self._operations.get(template.operation_id)
        if previous is None:
            if phase is not LibraryJournalPhase.BEGIN:
                raise _fail(LibraryFailureCode.JOURNAL_TRANSITION_INVALID, "new operation must begin with BEGIN")
        elif phase not in _ALLOWED_TRANSITIONS[previous.phase]:
            raise _fail(LibraryFailureCode.JOURNAL_TRANSITION_INVALID, "journal phase transition is invalid")
        sequence = len(self._records) + 1
        record = LibraryJournalRecord(
            LIBRARY_JOURNAL_RECORD_V1,
            sequence,
            template.operation_id,
            phase,
            template.blob_ref,
            template.manifest_ref,
            template.blob_sha256,
            template.manifest_sha256,
            template.publisher_component_id,
            template.publisher_policy_version,
        )
        try:
            append_journal_payload(self._journal_path, _canonical(record.to_dict()))
        except PersistenceViolation as exc:
            raise _fail(LibraryFailureCode.PERSISTENCE_FAILED, "journal append failed") from exc
        self._records.append(record)
        if previous is None:
            self._operations[record.operation_id] = _OperationState(record, phase)
        else:
            previous.phase = phase
        if phase is LibraryJournalPhase.COMMITTED:
            pair = (record.blob_ref.digest_sha256, record.manifest_ref.digest_sha256)
            self._committed_pairs[pair] = record.operation_id
        return record

    def _stage_path(self, ref: LibraryObjectRef, operation_id: str) -> Path:
        final = self._path_for_ref(ref, create_shard=True)
        return final.parent / f".{final.name}.stage-{operation_id}"

    def _advance_recovery_phase_locked(self, template: LibraryJournalRecord, target: LibraryJournalPhase) -> None:
        order = (
            LibraryJournalPhase.BEGIN,
            LibraryJournalPhase.BLOB_STAGED,
            LibraryJournalPhase.MANIFEST_STAGED,
            LibraryJournalPhase.BLOB_PUBLISHED,
            LibraryJournalPhase.MANIFEST_PUBLISHED,
            LibraryJournalPhase.METADATA_PUBLISHED,
            LibraryJournalPhase.COMMITTED,
            LibraryJournalPhase.CLEANED,
        )
        current = self._operations[template.operation_id].phase
        if current in (LibraryJournalPhase.ABORTED, LibraryJournalPhase.CLEANED):
            return
        current_index = order.index(current)
        target_index = order.index(target)
        for phase in order[current_index + 1 : target_index + 1]:
            self._append_phase_locked(template, phase)

    def _predicted_commit_sequence(self, operation_id: str) -> int:
        order = (
            LibraryJournalPhase.BEGIN,
            LibraryJournalPhase.BLOB_STAGED,
            LibraryJournalPhase.MANIFEST_STAGED,
            LibraryJournalPhase.BLOB_PUBLISHED,
            LibraryJournalPhase.MANIFEST_PUBLISHED,
            LibraryJournalPhase.METADATA_PUBLISHED,
            LibraryJournalPhase.COMMITTED,
            LibraryJournalPhase.CLEANED,
        )
        current = self._operations[operation_id].phase
        if current in (LibraryJournalPhase.COMMITTED, LibraryJournalPhase.CLEANED):
            committed = [
                record.sequence
                for record in self._records
                if record.operation_id == operation_id and record.phase is LibraryJournalPhase.COMMITTED
            ]
            if not committed:
                raise _fail(LibraryFailureCode.JOURNAL_CORRUPT, "committed operation lacks commit record")
            return committed[-1]
        if current is LibraryJournalPhase.ABORTED:
            raise _fail(LibraryFailureCode.JOURNAL_TRANSITION_INVALID, "aborted operation has no commit sequence")
        return len(self._records) + (order.index(LibraryJournalPhase.COMMITTED) - order.index(current))

    def _cleanup_stage_locked(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            try:
                require_regular_file(path)
                path.unlink()
            except OSError as exc:
                raise _fail(LibraryFailureCode.RECOVERY_FAILED, "stage cleanup failed") from exc
            except PersistenceViolation as exc:
                raise _fail(LibraryFailureCode.RECOVERY_FAILED, "stage residue is not regular") from exc

    def _recover_operation_locked(self, state: _OperationState) -> None:
        template = state.template
        if state.phase is LibraryJournalPhase.CLEANED:
            return
        blob_final = self._path_for_ref(template.blob_ref, create_shard=True)
        manifest_final = self._path_for_ref(template.manifest_ref, create_shard=True)
        blob_stage = self._stage_path(template.blob_ref, template.operation_id)
        manifest_stage = self._stage_path(template.manifest_ref, template.operation_id)
        if state.phase is LibraryJournalPhase.ABORTED:
            self._cleanup_stage_locked(blob_stage)
            self._cleanup_stage_locked(manifest_stage)
            self._append_phase_locked(template, LibraryJournalPhase.CLEANED)
            return
        try:
            if not blob_final.exists():
                if not blob_stage.exists():
                    raise _fail(LibraryFailureCode.RECOVERY_FAILED, "blob stage and destination are missing")
                blob_raw = read_regular_bytes(blob_stage, maximum_bytes=MAX_BLOB_OBJECT_BYTES_V1)
                if hashlib.sha256(blob_raw).hexdigest() != template.blob_sha256:
                    raise _fail(LibraryFailureCode.RECOVERY_FAILED, "blob stage hash mismatch")
                self._advance_recovery_phase_locked(template, LibraryJournalPhase.MANIFEST_STAGED)
                staged = StagedFile(blob_stage, len(blob_raw), hashlib.sha256(blob_raw).hexdigest())
                publish_immutable(staged, blob_final)
            blob_raw = read_regular_bytes(blob_final, maximum_bytes=MAX_BLOB_OBJECT_BYTES_V1)
            if hashlib.sha256(blob_raw).hexdigest() != template.blob_sha256:
                raise _fail(LibraryFailureCode.RECOVERY_FAILED, "published blob hash mismatch")
            self._advance_recovery_phase_locked(template, LibraryJournalPhase.BLOB_PUBLISHED)
            if not manifest_final.exists():
                if not manifest_stage.exists():
                    raise _fail(LibraryFailureCode.RECOVERY_FAILED, "manifest stage and destination are missing")
                manifest_raw = read_regular_bytes(manifest_stage, maximum_bytes=MAX_MANIFEST_OBJECT_BYTES_V1)
                if hashlib.sha256(manifest_raw).hexdigest() != template.manifest_sha256:
                    raise _fail(LibraryFailureCode.RECOVERY_FAILED, "manifest stage hash mismatch")
                staged = StagedFile(manifest_stage, len(manifest_raw), hashlib.sha256(manifest_raw).hexdigest())
                publish_immutable(staged, manifest_final)
            manifest_raw = read_regular_bytes(manifest_final, maximum_bytes=MAX_MANIFEST_OBJECT_BYTES_V1)
            if hashlib.sha256(manifest_raw).hexdigest() != template.manifest_sha256:
                raise _fail(LibraryFailureCode.RECOVERY_FAILED, "published manifest hash mismatch")
            self._advance_recovery_phase_locked(template, LibraryJournalPhase.MANIFEST_PUBLISHED)
            pair = self._load_pair_by_refs_locked(
                template.blob_ref,
                template.manifest_ref,
                source=CorruptionDetectionSource.RECOVERY,
                require_committed=False,
            )
            self._index[template.manifest_ref.digest_sha256] = self._index_entry(pair)
            expected_commit = self._predicted_commit_sequence(template.operation_id)
            self._write_metadata_locked(expected_commit_sequence=expected_commit)
            self._advance_recovery_phase_locked(template, LibraryJournalPhase.METADATA_PUBLISHED)
            self._advance_recovery_phase_locked(template, LibraryJournalPhase.COMMITTED)
            self._cleanup_stage_locked(blob_stage)
            self._cleanup_stage_locked(manifest_stage)
            self._advance_recovery_phase_locked(template, LibraryJournalPhase.CLEANED)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            current = self._operations[template.operation_id].phase
            if current not in (
                LibraryJournalPhase.ABORTED,
                LibraryJournalPhase.COMMITTED,
                LibraryJournalPhase.CLEANED,
            ):
                self._append_phase_locked(template, LibraryJournalPhase.ABORTED)
            self._cleanup_stage_locked(blob_stage)
            self._cleanup_stage_locked(manifest_stage)
            current = self._operations[template.operation_id].phase
            if current is LibraryJournalPhase.ABORTED:
                self._append_phase_locked(template, LibraryJournalPhase.CLEANED)

    def _recover_locked(self) -> bool:
        changed = False
        for operation_id in tuple(self._operations):
            state = self._operations[operation_id]
            if state.phase is not LibraryJournalPhase.CLEANED:
                changed = True
                self._recover_operation_locked(state)
        self._rebuild_operation_state()
        return changed

    @staticmethod
    def _index_entry(record: VerifiedBehaviorRecord) -> IndexEntry:
        blob_ref, manifest_ref = BehaviorLibrary._ref_for(record.unit.content_key, record.manifest.manifest_id)
        return IndexEntry(
            LIBRARY_INDEX_ENTRY_V1,
            1,
            record.unit.content_key.value,
            record.manifest.manifest_id.value,
            record.unit.core.behavior_kind.value,
            blob_ref,
            manifest_ref,
            None,
        )

    def _root_for_refs(self, refs: Iterable[LibraryObjectRef]) -> str:
        payload = [ref.to_dict() for ref in sorted(set(refs))]
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def _index_bytes(self, generation: int) -> bytes:
        entries = [self._index[key].to_dict() for key in sorted(self._index)]
        if len(entries) > MAX_INDEX_ENTRIES_V1:
            raise _fail(LibraryFailureCode.RESOURCE_LIMIT_EXCEEDED, "index exceeds entry limit")
        return _canonical(
            {
                "schema_version": LIBRARY_INDEX_V1,
                "index_version": 1,
                "generation": generation,
                "entries": entries,
            }
        )

    def _load_metadata_locked(self) -> bool:
        index_path = self._metadata / "index.v1"
        integrity_path = self._metadata / "integrity.v1"
        if not index_path.exists() and not integrity_path.exists():
            self._index = {}
            self._generation = 0
            return False
        if not index_path.exists() or not integrity_path.exists():
            self._index = {}
            self._generation = 0
            return False
        try:
            index_bytes = read_regular_bytes(index_path, maximum_bytes=MAX_METADATA_BYTES_V1)
            integrity_bytes = read_regular_bytes(integrity_path, maximum_bytes=MAX_METADATA_BYTES_V1)
            index_data = _exact_dict(
                _decode(index_bytes),
                ("schema_version", "index_version", "generation", "entries"),
                "library_index",
            )
            if index_data["schema_version"] != LIBRARY_INDEX_V1 or type(index_data["schema_version"]) is not str:
                raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index schema is unknown")
            if index_data["index_version"] != 1 or type(index_data["index_version"]) is not int:
                raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index version is unknown")
            generation = _positive_int(index_data["generation"], "index.generation", allow_zero=True)
            raw_entries = _exact_list(index_data["entries"], "index.entries", limit=MAX_INDEX_ENTRIES_V1)
            entries = tuple(IndexEntry.from_dict(item) for item in raw_entries)
            if tuple(sorted(entries, key=lambda entry: entry.manifest_ref.digest_sha256)) != entries:
                raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index entries are not sorted")
            if len({entry.manifest_ref.digest_sha256 for entry in entries}) != len(entries):
                raise _fail(LibraryFailureCode.INDEX_MALFORMED, "index contains duplicate manifest")
            descriptor = IntegrityManifestDescriptor.from_payload(_decode(integrity_bytes))
            if descriptor.generation != generation:
                raise _fail(LibraryFailureCode.INDEX_STALE, "index and integrity generations differ")
            if descriptor.index_sha256 != hashlib.sha256(index_bytes).hexdigest():
                raise _fail(LibraryFailureCode.INDEX_POISONED, "integrity index hash mismatch")
            blob_root = self._root_for_refs(entry.blob_ref for entry in entries)
            manifest_root = self._root_for_refs(entry.manifest_ref for entry in entries)
            if (
                descriptor.blob_store_root_sha256 != blob_root
                or descriptor.manifest_store_root_sha256 != manifest_root
            ):
                raise _fail(LibraryFailureCode.INDEX_POISONED, "integrity store roots mismatch")
            self._index = {entry.manifest_ref.digest_sha256: entry for entry in entries}
            self._generation = generation
            self._snapshot = LibrarySnapshot(
                LIBRARY_SNAPSHOT_V1,
                generation,
                descriptor.committed_journal_sequence,
                blob_root,
                manifest_root,
                descriptor.index_sha256,
                hashlib.sha256(integrity_bytes).hexdigest(),
            )
            return True
        except (CanonicalizationViolation, LibraryViolation, PersistenceViolation, ValueError, TypeError):
            self._index = {}
            self._generation = 0
            return False

    def _write_metadata_locked(self, *, expected_commit_sequence: int) -> None:
        durable_generation_floor = expected_commit_sequence + len(self._quarantined)
        generation = max(self._generation + 1, durable_generation_floor, 1)
        index_bytes = self._index_bytes(generation)
        committed_entries = tuple(self._index.values())
        blob_root = self._root_for_refs(entry.blob_ref for entry in committed_entries)
        manifest_root = self._root_for_refs(entry.manifest_ref for entry in committed_entries)
        descriptor = IntegrityManifestDescriptor(
            LIBRARY_INTEGRITY_MANIFEST_V1,
            generation,
            expected_commit_sequence,
            hashlib.sha256(index_bytes).hexdigest(),
            blob_root,
            manifest_root,
            active_durability_profile(),
        )
        integrity_bytes = _canonical(descriptor.to_payload())
        try:
            atomic_replace_metadata(self._metadata, final_name="index.v1", value=index_bytes)
            atomic_replace_metadata(self._metadata, final_name="integrity.v1", value=integrity_bytes)
        except PersistenceViolation as exc:
            raise _fail(LibraryFailureCode.PERSISTENCE_FAILED, "derived metadata replacement failed") from exc
        self._generation = generation
        self._snapshot = LibrarySnapshot(
            LIBRARY_SNAPSHOT_V1,
            generation,
            expected_commit_sequence,
            blob_root,
            manifest_root,
            hashlib.sha256(index_bytes).hexdigest(),
            hashlib.sha256(integrity_bytes).hexdigest(),
        )

    def _rebuild_index_locked(self, *, write_metadata: bool, force_metadata: bool = False) -> None:
        previous_index = self._index
        rebuilt: dict[str, IndexEntry] = {}
        for (blob_digest, manifest_digest), _operation in sorted(self._committed_pairs.items()):
            blob_ref = LibraryObjectRef(LibraryObjectNamespace.BLOB, blob_digest)
            manifest_ref = LibraryObjectRef(LibraryObjectNamespace.MANIFEST, manifest_digest)
            if blob_ref in self._quarantined or manifest_ref in self._quarantined:
                continue
            try:
                pair = self._load_pair_by_refs_locked(
                    blob_ref,
                    manifest_ref,
                    source=CorruptionDetectionSource.INDEX_REBUILD,
                    require_committed=True,
                )
            except LibraryViolation:
                continue
            rebuilt[manifest_digest] = self._index_entry(pair)
        self._index = rebuilt
        committed_sequence = max(
            (record.sequence for record in self._records if record.phase is LibraryJournalPhase.COMMITTED),
            default=0,
        )
        metadata_drifted = (
            rebuilt != previous_index
            or self._snapshot.committed_journal_sequence != committed_sequence
        )
        if write_metadata and (force_metadata or metadata_drifted):
            self._write_metadata_locked(expected_commit_sequence=committed_sequence)

    def _refresh_locked(self) -> None:
        self._load_journal_locked(repair_torn=True)
        self._load_quarantine_locked()
        metadata_valid = self._load_metadata_locked()
        recovery_changed = self._recover_locked()
        self._rebuild_index_locked(
            write_metadata=True,
            force_metadata=(not metadata_valid or recovery_changed),
        )

    def put_behavior(
        self,
        unit: SynapseBehaviorUnit,
        blob: BehaviorBlob,
        manifest: BehaviorManifest,
        *,
        publisher_identity: PublisherIdentity,
    ) -> PutResult:
        unit, blob, manifest, publisher, manifest_raw = self._validate_write_inputs(
            unit,
            blob,
            manifest,
            publisher_identity,
        )
        blob_raw = blob.canonical_core_bytes
        blob_ref, manifest_ref = self._ref_for(unit.content_key, manifest.manifest_id)
        pair_key = (blob_ref.digest_sha256, manifest_ref.digest_sha256)
        operation_id = new_operation_id()
        template = LibraryJournalRecord(
            LIBRARY_JOURNAL_RECORD_V1,
            1,
            operation_id,
            LibraryJournalPhase.BEGIN,
            blob_ref,
            manifest_ref,
            hashlib.sha256(blob_raw).hexdigest(),
            hashlib.sha256(manifest_raw).hexdigest(),
            publisher.component_id,
            publisher.policy_version,
        )
        with self._lock():
            self._refresh_locked()
            if blob_ref in self._quarantined or manifest_ref in self._quarantined:
                raise _fail(LibraryFailureCode.OBJECT_QUARANTINED, "write address is quarantined")
            if pair_key in self._committed_pairs:
                existing = self._load_pair_by_refs_locked(
                    blob_ref,
                    manifest_ref,
                    source=CorruptionDetectionSource.PUT_EXISTING,
                    require_committed=True,
                )
                if (
                    existing.blob.canonical_core_bytes != blob_raw
                    or self._manifest_envelope(existing.unit, existing.blob, existing.manifest) != manifest_raw
                ):
                    raise _fail(LibraryFailureCode.EXISTING_OBJECT_MISMATCH, "committed object bytes differ")
                return _make_put_result(PutStatus.DEDUPLICATED, unit.content_key, manifest.manifest_id, operation_id)
            blob_path = self._path_for_ref(blob_ref, create_shard=True)
            manifest_path = self._path_for_ref(manifest_ref, create_shard=True)
            blob_stage: StagedFile | None = None
            manifest_stage: StagedFile | None = None
            stored = False
            self._append_phase_locked(template, LibraryJournalPhase.BEGIN)
            try:
                if blob_path.exists() or blob_path.is_symlink():
                    try:
                        observed = read_regular_bytes(blob_path, maximum_bytes=MAX_BLOB_OBJECT_BYTES_V1)
                    except PersistenceViolation as exc:
                        reason = (
                            CorruptionReason.NON_REGULAR_ENTRY
                            if exc.failure_code
                            in (
                                PersistenceFailureCode.NON_REGULAR_ENTRY,
                                PersistenceFailureCode.LINK_OR_REPARSE_POINT,
                            )
                            else CorruptionReason.CONTENT_COLLISION
                        )
                        self._record_corruption_locked(
                            blob_ref,
                            expected_sha256=hashlib.sha256(blob_raw).hexdigest(),
                            actual_sha256=None,
                            source=CorruptionDetectionSource.PUT_EXISTING,
                            reason=reason,
                            existing_path=blob_path,
                            raw_evidence=blob_raw,
                        )
                        raise _fail(
                            LibraryFailureCode.EXISTING_OBJECT_MISMATCH,
                            "existing blob entry is not regular",
                        ) from exc
                    if observed != blob_raw:
                        self._record_corruption_locked(
                            blob_ref,
                            expected_sha256=hashlib.sha256(blob_raw).hexdigest(),
                            actual_sha256=hashlib.sha256(observed).hexdigest(),
                            source=CorruptionDetectionSource.PUT_EXISTING,
                            reason=CorruptionReason.CONTENT_COLLISION,
                            existing_path=blob_path,
                            raw_evidence=blob_raw,
                        )
                        raise _fail(LibraryFailureCode.EXISTING_OBJECT_MISMATCH, "existing blob bytes differ")
                else:
                    blob_stage = write_staged_bytes(
                        blob_path.parent,
                        final_name=blob_path.name,
                        operation_id=operation_id,
                        value=blob_raw,
                        maximum_bytes=MAX_BLOB_OBJECT_BYTES_V1,
                    )
                    stored = True
                self._append_phase_locked(template, LibraryJournalPhase.BLOB_STAGED)
                if manifest_path.exists() or manifest_path.is_symlink():
                    try:
                        observed = read_regular_bytes(manifest_path, maximum_bytes=MAX_MANIFEST_OBJECT_BYTES_V1)
                    except PersistenceViolation as exc:
                        reason = (
                            CorruptionReason.NON_REGULAR_ENTRY
                            if exc.failure_code
                            in (
                                PersistenceFailureCode.NON_REGULAR_ENTRY,
                                PersistenceFailureCode.LINK_OR_REPARSE_POINT,
                            )
                            else CorruptionReason.CONTENT_COLLISION
                        )
                        self._record_corruption_locked(
                            manifest_ref,
                            expected_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                            actual_sha256=None,
                            source=CorruptionDetectionSource.PUT_EXISTING,
                            reason=reason,
                            existing_path=manifest_path,
                            raw_evidence=manifest_raw,
                        )
                        raise _fail(
                            LibraryFailureCode.EXISTING_OBJECT_MISMATCH,
                            "existing manifest entry is not regular",
                        ) from exc
                    if observed != manifest_raw:
                        self._record_corruption_locked(
                            manifest_ref,
                            expected_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                            actual_sha256=hashlib.sha256(observed).hexdigest(),
                            source=CorruptionDetectionSource.PUT_EXISTING,
                            reason=CorruptionReason.CONTENT_COLLISION,
                            existing_path=manifest_path,
                            raw_evidence=manifest_raw,
                        )
                        raise _fail(LibraryFailureCode.EXISTING_OBJECT_MISMATCH, "existing manifest bytes differ")
                else:
                    manifest_stage = write_staged_bytes(
                        manifest_path.parent,
                        final_name=manifest_path.name,
                        operation_id=operation_id,
                        value=manifest_raw,
                        maximum_bytes=MAX_MANIFEST_OBJECT_BYTES_V1,
                    )
                    stored = True
                self._append_phase_locked(template, LibraryJournalPhase.MANIFEST_STAGED)
                if blob_stage is not None:
                    publish_immutable(blob_stage, blob_path)
                self._append_phase_locked(template, LibraryJournalPhase.BLOB_PUBLISHED)
                if manifest_stage is not None:
                    publish_immutable(manifest_stage, manifest_path)
                self._append_phase_locked(template, LibraryJournalPhase.MANIFEST_PUBLISHED)
                verified = self._load_pair_by_refs_locked(
                    blob_ref,
                    manifest_ref,
                    source=CorruptionDetectionSource.PUT_EXISTING,
                    require_committed=False,
                )
                self._index[manifest_ref.digest_sha256] = self._index_entry(verified)
                expected_commit = len(self._records) + 2
                self._write_metadata_locked(expected_commit_sequence=expected_commit)
                self._append_phase_locked(template, LibraryJournalPhase.METADATA_PUBLISHED)
                self._append_phase_locked(template, LibraryJournalPhase.COMMITTED)
                self._cleanup_stage_locked(self._stage_path(blob_ref, operation_id))
                self._cleanup_stage_locked(self._stage_path(manifest_ref, operation_id))
                self._append_phase_locked(template, LibraryJournalPhase.CLEANED)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                phase = self._operations[operation_id].phase
                if phase not in (
                    LibraryJournalPhase.ABORTED,
                    LibraryJournalPhase.COMMITTED,
                    LibraryJournalPhase.CLEANED,
                ):
                    self._append_phase_locked(template, LibraryJournalPhase.ABORTED)
                for staged in (blob_stage, manifest_stage):
                    if staged is not None:
                        self._cleanup_stage_locked(staged.path)
                if self._operations[operation_id].phase is LibraryJournalPhase.ABORTED:
                    self._append_phase_locked(template, LibraryJournalPhase.CLEANED)
                raise
            return _make_put_result(
                PutStatus.STORED if stored else PutStatus.DEDUPLICATED,
                unit.content_key,
                manifest.manifest_id,
                operation_id,
            )

    def get_verified_behavior(self, content_key: ContentKey, manifest_id: RecordId) -> VerifiedBehaviorRecord:
        if type(content_key) is not ContentKey or type(manifest_id) is not RecordId:
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "verified load requires exact trusted identities")
        content_key.to_dict()
        manifest_id.to_dict()
        if manifest_id.domain is not IdentityDomain.BEHAVIOR_MANIFEST:
            raise _fail(LibraryFailureCode.MANIFEST_ID_MISMATCH, "manifest identity domain is invalid")
        blob_ref, manifest_ref = self._ref_for(content_key, manifest_id)
        with self._lock():
            self._refresh_locked()
            pair = self._load_pair_by_refs_locked(
                blob_ref,
                manifest_ref,
                source=CorruptionDetectionSource.VERIFIED_READ,
                require_committed=True,
            )
            entry = self._index.get(manifest_ref.digest_sha256)
            expected = self._index_entry(pair)
            if entry != expected:
                self._rebuild_index_locked(write_metadata=True)
                entry = self._index.get(manifest_ref.digest_sha256)
                if entry != expected:
                    raise _fail(LibraryFailureCode.INDEX_POISONED, "derived index does not match verified objects")
            return pair

    def search_index(self, *, behavior_kind: str | None = None) -> tuple[IndexEntry, ...]:
        if behavior_kind is not None:
            _safe_id(behavior_kind, "behavior_kind")
        with self._lock():
            self._refresh_locked()
            entries = tuple(
                entry
                for _, entry in sorted(self._index.items())
                if behavior_kind is None or entry.behavior_kind == behavior_kind
            )
            return entries

    def rebuild_index(self) -> tuple[IndexEntry, ...]:
        with self._lock():
            self._refresh_locked()
            return tuple(self._index[key] for key in sorted(self._index))

    def current_snapshot(self, *, trusted_prior: LibrarySnapshot | None = None) -> SnapshotVerification:
        with self._lock():
            self._refresh_locked()
            current = self._snapshot
            if trusted_prior is None:
                return _make_snapshot_verification(SnapshotVerificationStatus.UNANCHORED, current)
            _validate_snapshot(trusted_prior)
            if (
                current.generation < trusted_prior.generation
                or current.committed_journal_sequence < trusted_prior.committed_journal_sequence
            ):
                raise _fail(LibraryFailureCode.SNAPSHOT_ROLLBACK, "library snapshot regressed behind trusted head")
            current_roots = (
                current.blob_store_root_sha256,
                current.manifest_store_root_sha256,
                current.index_sha256,
                current.integrity_manifest_sha256,
            )
            prior_roots = (
                trusted_prior.blob_store_root_sha256,
                trusted_prior.manifest_store_root_sha256,
                trusted_prior.index_sha256,
                trusted_prior.integrity_manifest_sha256,
            )
            if current.generation == trusted_prior.generation:
                if (
                    current_roots != prior_roots
                    or current.committed_journal_sequence != trusted_prior.committed_journal_sequence
                ):
                    raise _fail(LibraryFailureCode.SNAPSHOT_MIXED_ROOTS, "same generation has different roots")
                return _make_snapshot_verification(SnapshotVerificationStatus.VERIFIED_SAME, current)
            return _make_snapshot_verification(SnapshotVerificationStatus.VERIFIED_FORWARD, current)

    def plan_garbage_collection(
        self,
        roots: tuple[RetentionRootSet, ...] | list[RetentionRootSet],
    ) -> GarbageCollectionPlan:
        if type(roots) not in (tuple, list):
            raise _fail(LibraryFailureCode.TYPE_MISMATCH, "GC roots must be a tuple or list")
        if len(roots) != len(RetentionRootKind):
            raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "GC requires one root set for each retention category")
        by_kind: dict[RetentionRootKind, RetentionRootSet] = {}
        for root_set in roots:
            if type(root_set) is not RetentionRootSet:
                raise _fail(LibraryFailureCode.TYPE_MISMATCH, "GC root set type is invalid")
            _validate_retention_roots(root_set)
            if root_set.root_kind in by_kind:
                raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "duplicate GC root category")
            by_kind[root_set.root_kind] = root_set
        if set(by_kind) != set(RetentionRootKind):
            raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "GC root categories are incomplete")
        with self._lock():
            self._refresh_locked()
            known: set[LibraryObjectRef] = set()
            graph: dict[LibraryObjectRef, set[LibraryObjectRef]] = {}
            for entry in self._index.values():
                known.add(entry.blob_ref)
                known.add(entry.manifest_ref)
                graph.setdefault(entry.manifest_ref, set()).add(entry.blob_ref)
                graph.setdefault(entry.blob_ref, set())
            seeds: set[LibraryObjectRef] = set()
            for root_set in by_kind.values():
                for ref in root_set.object_refs:
                    if ref not in known:
                        raise _fail(LibraryFailureCode.GC_ROOT_INVALID, "GC root references unknown object")
                    seeds.add(ref)
            retained: set[LibraryObjectRef] = set()
            pending = list(sorted(seeds, reverse=True))
            while pending:
                current = pending.pop()
                if current in retained:
                    continue
                retained.add(current)
                pending.extend(sorted(graph.get(current, ()), reverse=True))
            candidates = {
                ref
                for ref in known - retained
                if ref not in self._quarantined
                and (
                    (
                        ref.namespace is LibraryObjectNamespace.BLOB
                        and any(entry.blob_ref == ref for entry in self._index.values())
                    )
                    or (ref.namespace is LibraryObjectNamespace.MANIFEST and ref.digest_sha256 in self._index)
                )
            }
            retained_tuple = tuple(sorted(retained))
            candidates_tuple = tuple(sorted(candidates))
            payload = {
                "schema_version": LIBRARY_GC_PLAN_V1,
                "retained_refs": [ref.to_dict() for ref in retained_tuple],
                "deletion_candidates": [ref.to_dict() for ref in candidates_tuple],
            }
            encoded = _canonical(payload)
            return GarbageCollectionPlan(
                LIBRARY_GC_PLAN_V1,
                retained_tuple,
                candidates_tuple,
                encoded,
                hashlib.sha256(encoded).hexdigest(),
            )


__all__ = [
    "BehaviorLibrary",
    "CorruptionDetectionSource",
    "CorruptionReason",
    "CorruptionRecord",
    "GarbageCollectionPlan",
    "IndexEntry",
    "LIBRARY_CORRUPTION_RECORD_V1",
    "LIBRARY_GC_PLAN_V1",
    "LIBRARY_INDEX_ENTRY_V1",
    "LIBRARY_INDEX_V1",
    "LIBRARY_JOURNAL_RECORD_V1",
    "LIBRARY_MANIFEST_OBJECT_V1",
    "LIBRARY_PUBLISHER_IDENTITY_V1",
    "LIBRARY_RETENTION_ROOTS_V1",
    "LIBRARY_SNAPSHOT_V1",
    "LibraryFailureCode",
    "LibraryJournalPhase",
    "LibraryJournalRecord",
    "LibraryObjectNamespace",
    "LibraryObjectRef",
    "LibrarySnapshot",
    "LibraryViolation",
    "LifecyclePointer",
    "MAX_BLOB_OBJECT_BYTES_V1",
    "MAX_GC_REFS_V1",
    "MAX_INDEX_ENTRIES_V1",
    "MAX_MANIFEST_OBJECT_BYTES_V1",
    "PublisherIdentity",
    "PutResult",
    "PutStatus",
    "QuarantineAction",
    "RetentionRootKind",
    "RetentionRootSet",
    "SnapshotVerification",
    "SnapshotVerificationStatus",
    "VerifiedBehaviorRecord",
    "validate_put_result",
    "validate_snapshot_verification",
    "validate_verified_behavior_record",
]
