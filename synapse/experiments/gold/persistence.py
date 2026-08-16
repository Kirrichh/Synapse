"""Durable filesystem primitives for the Stage 4 Gold behavior library.

The module owns byte-exact staged writes, non-overwriting immutable
publication, rebuildable metadata replacement, a framed append-only journal,
and the local single-writer lock.  It deliberately has no knowledge of
Behavior admission, lifecycle, retrieval, or execution semantics.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import BinaryIO, Iterator, Protocol, runtime_checkable


LIBRARY_DURABILITY_PROFILE_V1 = "synapse.stage4.gold.library-durability-profile/v1"
LIBRARY_INTEGRITY_MANIFEST_V1 = "synapse.stage4.gold.library-integrity-manifest/v1"
JOURNAL_FRAME_MAGIC_V1 = b"SYNAPSE-S4-GOLD-JOURNAL\x00\x01"

MAX_JOURNAL_PAYLOAD_BYTES_V1 = 1_048_576
MAX_METADATA_BYTES_V1 = 16_777_216
MAX_JOURNAL_FRAMES_V1 = 1_000_000

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_LEAF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_WINDOWS_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_BINARY_FLAG = getattr(os, "O_BINARY", 0)
_NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)


class DurabilityProfile(str, Enum):
    POSIX_LINK_DIRECTORY_FSYNC_V1 = (
        "synapse.stage4.gold.library-durability-profile/posix-link-directory-fsync/v1"
    )
    WINDOWS_RENAME_FILE_FSYNC_V1 = (
        "synapse.stage4.gold.library-durability-profile/windows-rename-file-fsync/v1"
    )


class PersistenceFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    INVALID_PATH = "INVALID_PATH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    NON_REGULAR_ENTRY = "NON_REGULAR_ENTRY"
    LINK_OR_REPARSE_POINT = "LINK_OR_REPARSE_POINT"
    DESTINATION_EXISTS = "DESTINATION_EXISTS"
    UNSUPPORTED_DURABILITY_PRIMITIVE = "UNSUPPORTED_DURABILITY_PRIMITIVE"
    FILESYSTEM_IO_FAILED = "FILESYSTEM_IO_FAILED"
    LOCK_BUSY = "LOCK_BUSY"
    LOCK_FAILED = "LOCK_FAILED"
    JOURNAL_MAGIC_MISMATCH = "JOURNAL_MAGIC_MISMATCH"
    JOURNAL_TORN_TAIL = "JOURNAL_TORN_TAIL"
    JOURNAL_CHECKSUM_MISMATCH = "JOURNAL_CHECKSUM_MISMATCH"
    JOURNAL_FRAME_LIMIT_EXCEEDED = "JOURNAL_FRAME_LIMIT_EXCEEDED"
    INTEGRITY_MANIFEST_MALFORMED = "INTEGRITY_MANIFEST_MALFORMED"
    #: The transaction's writes are durable and its mutation interval could not be
    #: closed. Kept apart from FILESYSTEM_IO_FAILED because the two ask for
    #: opposite things: a write that failed left nothing behind and may be
    #: retried, while this left durable records and a retry would write them
    #: twice. Readers are safe either way — the interval stays open, so the epoch
    #: stays odd and every reader refuses — but the *caller* must not retry.
    FENCE_NOT_ADVANCED = "FENCE_NOT_ADVANCED"
    #: An authority mutation was attempted with no open interval to attribute it
    #: to. This is the bypass condition: the write itself might have succeeded
    #: perfectly, and no reader would ever have been able to tell it happened.
    MUTATION_NOT_FENCED = "MUTATION_NOT_FENCED"
    #: A ticket was presented after its interval closed. Distinct from
    #: MUTATION_NOT_FENCED (NR-10): the protocol was followed once and the caller
    #: is now holding a stale capability, which is a lifetime defect rather than
    #: an absent one, and points at a different line of code.
    MUTATION_INTERVAL_CLOSED = "MUTATION_INTERVAL_CLOSED"
    #: A ticket minted by one coordinator was offered to a store attached to
    #: another. Its interval says nothing about this store's readers.
    MUTATION_COORDINATOR_MISMATCH = "MUTATION_COORDINATOR_MISMATCH"


class PersistenceViolation(RuntimeError):
    """Typed, fail-closed persistence error without payload/path disclosure."""

    def __init__(self, failure_code: PersistenceFailureCode, detail: str) -> None:
        if type(failure_code) is not PersistenceFailureCode:
            raise TypeError("failure_code must be an exact PersistenceFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: PersistenceFailureCode, detail: str) -> PersistenceViolation:
    return PersistenceViolation(code, detail)


def _require_exact_int(value: object, field_name: str, *, lower: int = 0) -> int:
    if type(value) is not int or value < lower:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, f"{field_name} is invalid")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, f"{field_name} is invalid")
    return value


def _require_exact_dict(value: object, fields: tuple[str, ...], field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact dict")
    if set(value) != set(fields) or any(type(key) is not str for key in value):
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, f"{field_name} fields are invalid")
    return value


@dataclass(frozen=True)
class IntegrityManifestDescriptor:
    schema_version: str
    generation: int
    committed_journal_sequence: int
    index_sha256: str
    blob_store_root_sha256: str
    manifest_store_root_sha256: str
    durability_profile: DurabilityProfile

    def __post_init__(self) -> None:
        _validate_integrity_descriptor(self)

    def to_payload(self) -> dict[str, object]:
        _validate_integrity_descriptor(self)
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "committed_journal_sequence": self.committed_journal_sequence,
            "index_sha256": self.index_sha256,
            "blob_store_root_sha256": self.blob_store_root_sha256,
            "manifest_store_root_sha256": self.manifest_store_root_sha256,
            "durability_profile": self.durability_profile.value,
        }

    @classmethod
    def from_payload(cls, value: object) -> IntegrityManifestDescriptor:
        data = _require_exact_dict(
            value,
            (
                "schema_version",
                "generation",
                "committed_journal_sequence",
                "index_sha256",
                "blob_store_root_sha256",
                "manifest_store_root_sha256",
                "durability_profile",
            ),
            "integrity_manifest",
        )
        try:
            profile = DurabilityProfile(data["durability_profile"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED,
                "durability profile is unknown",
            ) from exc
        return cls(
            schema_version=data["schema_version"],
            generation=data["generation"],
            committed_journal_sequence=data["committed_journal_sequence"],
            index_sha256=data["index_sha256"],
            blob_store_root_sha256=data["blob_store_root_sha256"],
            manifest_store_root_sha256=data["manifest_store_root_sha256"],
            durability_profile=profile,
        )


def _validate_integrity_descriptor(value: IntegrityManifestDescriptor) -> None:
    if type(value) is not IntegrityManifestDescriptor:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "integrity descriptor type is invalid")
    if value.schema_version != LIBRARY_INTEGRITY_MANIFEST_V1 or type(value.schema_version) is not str:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "integrity schema is unknown")
    _require_exact_int(value.generation, "generation")
    _require_exact_int(value.committed_journal_sequence, "committed_journal_sequence")
    _require_sha256(value.index_sha256, "index_sha256")
    _require_sha256(value.blob_store_root_sha256, "blob_store_root_sha256")
    _require_sha256(value.manifest_store_root_sha256, "manifest_store_root_sha256")
    if type(value.durability_profile) is not DurabilityProfile:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "durability profile type is invalid")


#: Held by nothing outside this module. A ticket carrying it was produced by an
#: interval this module opened; one carrying anything else is a forgery, however
#: well its fields are filled in.
_TICKET_SEAL = object()


@dataclass(frozen=True, init=False)
class StoreMutationTicket:
    """Evidence that an authority mutation interval is open — this one, now.

    Every primitive below that changes authoritative state requires one. That is
    a deliberate escalation from the earlier design, where a single *fenced*
    wrapper sat beside the unfenced primitive it wrapped: the wrapper was
    correct, and it did nothing whatever to stop the five other primitives from
    being called directly, which is precisely what four of the six mutation sites
    did. A bypass that a tripwire has to go looking for is a bypass. Making the
    ticket a required argument removes the unfenced call from the language
    (NR-09) rather than from a checklist.

    ``interval_epoch`` is the odd value the coordinator's counter holds for as
    long as this ticket is valid. It is recorded so that a ticket says *which*
    interval it belongs to and not merely that one existed, and ``_open`` is
    flipped when the interval closes so a ticket outliving its interval is
    refused instead of quietly re-authorising a later write.
    """

    coordinator_id: str
    interval_epoch: int
    _open: bool
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> StoreMutationTicket:
        raise TypeError("StoreMutationTicket is produced only by an open mutation interval")


def _mint_store_mutation_ticket(*, coordinator_id: str, interval_epoch: int) -> StoreMutationTicket:
    """Private on purpose: only the coordinator adapter opens intervals.

    The tripwire in the dependency-direction suite holds this to a single
    importer, for the same reason the library's private write-capability factory
    is held to one — a second caller would be a second authority to open an
    interval, and the counter cannot tell them apart.
    """

    if type(coordinator_id) is not str or not coordinator_id:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "a mutation ticket names its coordinator")
    if type(interval_epoch) is not int or interval_epoch < 0 or interval_epoch % 2 == 0:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "a mutation ticket belongs to an odd, in-flight interval",
        )
    ticket = object.__new__(StoreMutationTicket)
    object.__setattr__(ticket, "coordinator_id", coordinator_id)
    object.__setattr__(ticket, "interval_epoch", interval_epoch)
    object.__setattr__(ticket, "_open", True)
    object.__setattr__(ticket, "_trusted_seal", _TICKET_SEAL)
    return ticket


def _close_store_mutation_ticket(ticket: StoreMutationTicket) -> None:
    """End a ticket's validity. Called by the interval that minted it, always —
    on the closing mark and on abandonment alike, because a ticket that survives
    an aborted interval is the most dangerous kind."""

    if type(ticket) is not StoreMutationTicket:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "only a mutation ticket can be closed")
    object.__setattr__(ticket, "_open", False)


def require_open_mutation_ticket(value: object) -> StoreMutationTicket:
    """Refuse anything that is not an open, module-minted ticket.

    The three refusals are kept distinct because they ask for different fixes: an
    absent ticket is a missing transaction, a closed one is a lifetime bug, and a
    forged one is an attempt to write without a coordinator at all.
    """

    if type(value) is not StoreMutationTicket:
        raise _fail(
            PersistenceFailureCode.MUTATION_NOT_FENCED,
            "an authority mutation needs an open mutation interval",
        )
    if getattr(value, "_trusted_seal", None) is not _TICKET_SEAL:
        raise _fail(
            PersistenceFailureCode.MUTATION_NOT_FENCED,
            "the mutation ticket was not produced by a coordinator interval",
        )
    if value._open is not True:
        raise _fail(
            PersistenceFailureCode.MUTATION_INTERVAL_CLOSED,
            "the mutation interval this ticket belongs to has already closed",
        )
    return value


def require_ticket_of_coordinator(value: object, *, coordinator_id: str) -> StoreMutationTicket:
    """As above, and belonging to the coordinator this store is attached to.

    A store handed a foreign coordinator's ticket is being told that *someone's*
    readers are protected. Not its own.
    """

    ticket = require_open_mutation_ticket(value)
    if type(coordinator_id) is not str or not coordinator_id:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "a coordinator identity is required")
    if ticket.coordinator_id != coordinator_id:
        raise _fail(
            PersistenceFailureCode.MUTATION_COORDINATOR_MISMATCH,
            "the mutation interval belongs to a different coordinator",
        )
    return ticket


@dataclass(frozen=True)
class StagedFile:
    path: Path
    byte_length: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "staged path must be an exact Path")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "staged byte length is invalid")
        if type(self.sha256) is not str or _SHA256_RE.fullmatch(self.sha256) is None:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "staged digest is invalid")


@dataclass(frozen=True)
class JournalFrame:
    payload: bytes
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if type(self.payload) is not bytes:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal payload must be exact bytes")
        if type(self.start_offset) is not int or type(self.end_offset) is not int:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal offsets are invalid")
        if self.start_offset < len(JOURNAL_FRAME_MAGIC_V1) or self.end_offset <= self.start_offset:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal frame bounds are invalid")


@dataclass(frozen=True)
class JournalScanResult:
    frames: tuple[JournalFrame, ...]
    valid_prefix_length: int
    torn_tail: bytes

    def __post_init__(self) -> None:
        if type(self.frames) is not tuple or any(type(frame) is not JournalFrame for frame in self.frames):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal frames are invalid")
        if type(self.valid_prefix_length) is not int or self.valid_prefix_length < len(JOURNAL_FRAME_MAGIC_V1):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal prefix length is invalid")
        if type(self.torn_tail) is not bytes:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal torn tail must be exact bytes")


def active_durability_profile() -> DurabilityProfile:
    if os.name == "posix":
        return DurabilityProfile.POSIX_LINK_DIRECTORY_FSYNC_V1
    if os.name == "nt":
        return DurabilityProfile.WINDOWS_RENAME_FILE_FSYNC_V1
    raise _fail(
        PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
        "operating system has no frozen durability profile",
    )


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "filesystem stat failed") from exc


def _is_reparse(result: os.stat_result) -> bool:
    attributes = getattr(result, "st_file_attributes", 0)
    return bool(attributes & _WINDOWS_REPARSE_ATTRIBUTE)


def _reject_link_or_reparse(path: Path, result: os.stat_result) -> None:
    if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
        raise _fail(PersistenceFailureCode.LINK_OR_REPARSE_POINT, "link or reparse point is forbidden")


def require_directory(path: Path) -> None:
    if not isinstance(path, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "directory path must be an exact Path")
    result = _lstat(path)
    _reject_link_or_reparse(path, result)
    if not stat.S_ISDIR(result.st_mode):
        raise _fail(PersistenceFailureCode.NON_REGULAR_ENTRY, "expected directory is not a directory")


def ensure_directory(path: Path) -> None:
    if not isinstance(path, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "directory path must be an exact Path")
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "directory creation failed") from exc
    require_directory(path)


def require_regular_file(path: Path) -> os.stat_result:
    if not isinstance(path, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "file path must be an exact Path")
    result = _lstat(path)
    _reject_link_or_reparse(path, result)
    if not stat.S_ISREG(result.st_mode):
        raise _fail(PersistenceFailureCode.NON_REGULAR_ENTRY, "expected file is not regular")
    return result


def _validate_leaf(value: str, field_name: str) -> str:
    if type(value) is not str or _SAFE_LEAF_RE.fullmatch(value) is None or value in (".", ".."):
        raise _fail(PersistenceFailureCode.INVALID_PATH, f"{field_name} is invalid")
    return value


def _validate_operation_id(value: str) -> str:
    if type(value) is not str or _OPERATION_ID_RE.fullmatch(value) is None:
        raise _fail(PersistenceFailureCode.INVALID_PATH, "operation id is invalid")
    return value


def _open_no_follow(path: Path, flags: int) -> int:
    before = require_regular_file(path)
    try:
        fd = os.open(path, flags | _BINARY_FLAG | _NOFOLLOW_FLAG)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise _fail(PersistenceFailureCode.LINK_OR_REPARSE_POINT, "linked file is forbidden") from exc
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "file open failed") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise _fail(PersistenceFailureCode.NON_REGULAR_ENTRY, "opened entry is not regular")
        if _is_reparse(opened):
            raise _fail(PersistenceFailureCode.LINK_OR_REPARSE_POINT, "opened reparse point is forbidden")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "file changed during verified open")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_no_follow_read(path: Path) -> int:
    return _open_no_follow(path, os.O_RDONLY)


def read_regular_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "read limit is invalid")
    fd = _open_no_follow_read(path)
    try:
        opened = os.fstat(fd)
        if opened.st_size > maximum_bytes:
            raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "file exceeds byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "file exceeds byte limit")
        return b"".join(chunks)
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "file read failed") from exc
    finally:
        os.close(fd)


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    offset = 0
    try:
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "write made no progress")
            offset += written
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "file write failed") from exc


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        raise _fail(
            PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
            "directory fsync primitive is unavailable",
        )
    flags = os.O_RDONLY | os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "directory fsync failed") from exc


def _write_staged_bytes_unfenced(
    directory: Path,
    *,
    final_name: str,
    operation_id: str,
    value: bytes,
    maximum_bytes: int,
) -> StagedFile:
    require_directory(directory)
    _validate_leaf(final_name, "final name")
    _validate_operation_id(operation_id)
    if type(value) is not bytes:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "staged value must be exact bytes")
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "staged byte limit is invalid")
    if len(value) > maximum_bytes:
        raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "staged value exceeds byte limit")
    stage_name = f".{final_name}.stage-{operation_id}"
    _validate_leaf(stage_name[1:], "stage name")
    stage_path = directory / stage_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_FLAG
    try:
        fd = os.open(stage_path, flags, 0o600)
    except FileExistsError as exc:
        raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "staged destination already exists") from exc
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "staged file creation failed") from exc
    try:
        _write_all(fd, value)
        os.fsync(fd)
    except BaseException:
        try:
            os.close(fd)
        finally:
            try:
                stage_path.unlink()
            except OSError:
                pass
        raise
    else:
        os.close(fd)
    require_regular_file(stage_path)
    return StagedFile(stage_path, len(value), hashlib.sha256(value).hexdigest())


def write_staged_bytes(
    directory: Path,
    *,
    final_name: str,
    operation_id: str,
    value: bytes,
    maximum_bytes: int,
    ticket: StoreMutationTicket,
) -> StagedFile:
    """Stage bytes under a hidden name, inside an open interval.

    Staging writes a name no reader resolves, so on its own it changes nothing
    observable — and it still requires the ticket. Not because the stage file is
    authoritative, but because staging is the first step of a transaction that
    will be, and an interval that starts at the last possible moment leaves each
    primitive deciding where "authoritative" begins. That decision belongs to the
    transaction boundary, once.
    """

    require_open_mutation_ticket(ticket)
    return _write_staged_bytes_unfenced(
        directory,
        final_name=final_name,
        operation_id=operation_id,
        value=value,
        maximum_bytes=maximum_bytes,
    )


def _publish_by_platform(source: Path, destination: Path) -> None:
    if os.name == "posix":
        try:
            os.link(source, destination, follow_symlinks=False)
        except FileExistsError as exc:
            raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "immutable destination already exists") from exc
        except (AttributeError, NotImplementedError) as exc:
            raise _fail(
                PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
                "non-overwriting hard-link publication is unavailable",
            ) from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "immutable destination already exists") from exc
            raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "immutable link publication failed") from exc
        _sync_directory(destination.parent)
        try:
            source.unlink()
        except OSError as exc:
            raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "staged unlink failed") from exc
        _sync_directory(destination.parent)
        return
    if os.name == "nt":
        try:
            os.rename(source, destination)
        except FileExistsError as exc:
            raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "immutable destination already exists") from exc
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.EACCES):
                if destination.exists():
                    raise _fail(
                        PersistenceFailureCode.DESTINATION_EXISTS,
                        "immutable destination already exists",
                    ) from exc
            raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "immutable rename publication failed") from exc
        return
    raise _fail(
        PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
        "immutable publication primitive is unavailable",
    )


def publish_immutable(staged: StagedFile, destination: Path, *, ticket: StoreMutationTicket) -> None:
    require_open_mutation_ticket(ticket)
    if type(staged) is not StagedFile or not isinstance(destination, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "immutable publication arguments are invalid")
    if staged.path.parent != destination.parent:
        raise _fail(PersistenceFailureCode.INVALID_PATH, "immutable stage and destination must share a directory")
    _validate_leaf(destination.name, "immutable destination")
    require_directory(destination.parent)
    raw = read_regular_bytes(staged.path, maximum_bytes=staged.byte_length)
    if len(raw) != staged.byte_length or hashlib.sha256(raw).hexdigest() != staged.sha256:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "staged bytes changed before publication")
    if destination.exists() or destination.is_symlink():
        raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "immutable destination already exists")
    _publish_by_platform(staged.path, destination)
    require_regular_file(destination)


def move_immutable(
    source: Path, destination: Path, *, maximum_bytes: int, ticket: StoreMutationTicket
) -> None:
    require_open_mutation_ticket(ticket)
    if not isinstance(source, Path) or not isinstance(destination, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "immutable move arguments are invalid")
    if source.parent == destination.parent:
        raise _fail(PersistenceFailureCode.INVALID_PATH, "quarantine destination must use a separate namespace")
    require_directory(source.parent)
    require_directory(destination.parent)
    _validate_leaf(destination.name, "quarantine destination")
    read_regular_bytes(source, maximum_bytes=maximum_bytes)
    if destination.exists() or destination.is_symlink():
        raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "quarantine destination already exists")
    _publish_by_platform(source, destination)
    if os.name == "posix":
        _sync_directory(source.parent)
    require_regular_file(destination)


def _atomic_replace_metadata_unfenced(
    directory: Path,
    *,
    final_name: str,
    value: bytes,
    maximum_bytes: int = MAX_METADATA_BYTES_V1,
) -> None:
    require_directory(directory)
    _validate_leaf(final_name, "metadata name")
    if type(value) is not bytes:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "metadata value must be exact bytes")
    if type(maximum_bytes) is not int or maximum_bytes < 0 or len(value) > maximum_bytes:
        raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "metadata exceeds byte limit")
    operation_id = secrets.token_hex(16)
    staged = _write_staged_bytes_unfenced(
        directory,
        final_name=f"{final_name}.replace",
        operation_id=operation_id,
        value=value,
        maximum_bytes=maximum_bytes,
    )
    destination = directory / final_name
    try:
        os.replace(staged.path, destination)
        if os.name == "posix":
            _sync_directory(directory)
        elif os.name != "nt":
            raise _fail(
                PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
                "metadata replacement profile is unavailable",
            )
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "metadata replacement failed") from exc
    finally:
        if staged.path.exists():
            try:
                staged.path.unlink()
            except OSError:
                pass
    if read_regular_bytes(destination, maximum_bytes=maximum_bytes) != value:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "metadata replacement bytes mismatch")


def atomic_replace_metadata(
    directory: Path,
    *,
    final_name: str,
    value: bytes,
    maximum_bytes: int = MAX_METADATA_BYTES_V1,
    ticket: StoreMutationTicket,
) -> None:
    """Replace a rebuildable metadata file inside an open interval.

    This is the primitive that writes ``index.v1``, and ``index.v1`` is one of the
    §21 roots. It used to be called with no interval at all, which meant an
    authority root could be replaced while the coordinator reported a settled even
    epoch — a reader could see the new index against the old journal and have no
    way to know.
    """

    require_open_mutation_ticket(ticket)
    _atomic_replace_metadata_unfenced(
        directory,
        final_name=final_name,
        value=value,
        maximum_bytes=maximum_bytes,
    )


def create_coordinator_metadata_once(
    directory: Path,
    *,
    final_name: str,
    value: bytes,
    maximum_bytes: int = MAX_METADATA_BYTES_V1,
) -> bytes:
    """Publish the coordinator's identity exactly once, and return what persisted.

    Same exemption as ``append_coordinator_epoch_frame``: this writes the file a
    ticket's coordinator identity is read from, so requiring a ticket would be a
    cycle rather than a safeguard.

    What it must *not* be is a replace. The previous version asked
    ``if not path.exists()`` and then replaced, which is check-then-act across
    two processes: both find the file missing, both mint a random identity, and
    the second overwrites the first. One directory then answered with two
    identities, and every coordinator comparison built on top of that was
    comparing against whichever value happened to land last.

    So the create is the decision. ``O_CREAT|O_EXCL`` makes exactly one caller
    the author; every other caller loses the race, reads, and adopts the identity
    that is actually there. The bytes returned are always the persisted bytes,
    never the caller's own proposal, so a loser cannot go on believing it minted
    the identity it is using.
    """

    require_directory(directory)
    _validate_leaf(final_name, "metadata name")
    if type(value) is not bytes or not value:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "metadata value must be non-empty bytes")
    if type(maximum_bytes) is not int or maximum_bytes < 0 or len(value) > maximum_bytes:
        raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "metadata exceeds byte limit")
    destination = directory / final_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_FLAG
    try:
        fd = os.open(destination, flags, 0o600)
    except FileExistsError:
        # Lost the race, which is an ordinary outcome and not an error: the
        # identity exists and this caller adopts it.
        return read_regular_bytes(destination, maximum_bytes=maximum_bytes)
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "metadata creation failed") from exc
    try:
        _write_all(fd, value)
        os.fsync(fd)
    except BaseException:
        try:
            os.close(fd)
        finally:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
    else:
        os.close(fd)
    if os.name == "posix":
        _sync_directory(directory)
    # The winner returns what it wrote. A read-back stood here, and the campaign
    # showed it could not fail: `_write_all` and `fsync` raise on a short or
    # failed write, so there is no path where the file differs from `value` at
    # this point. It read as a further safeguard and was a condition no input
    # could reach. The *loser* still reads, because there the bytes genuinely are
    # somebody else's.
    return value


class ExclusiveStoreLock:
    """OS-released exclusive lock for one local store, optionally waiting.

    ``wait_seconds`` is what separates two different questions asked of the same
    lock. Zero asks "is this held *right now*" and is what a barrier check wants:
    an immediate ``LOCK_BUSY`` is the answer, not a failure. A positive value
    asks "let me have it when it is free", which is what two legitimate writers
    want — they should queue rather than error, and a coordinator that failed
    every concurrent write would push callers straight into the retry loops that
    hide the races this lock exists to prevent.

    Bounded either way. An unbounded wait would turn one abandoned holder into a
    hang with no diagnosis, so the wait expires and reports ``LOCK_BUSY`` like
    any other refusal.
    """

    def __init__(self, path: Path, *, wait_seconds: float = 0.0) -> None:
        if not isinstance(path, Path):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "lock path must be an exact Path")
        if type(wait_seconds) not in (int, float) or wait_seconds < 0:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "lock wait must be a non-negative number")
        self._path = path
        self._wait_seconds = float(wait_seconds)
        self._stream: BinaryIO | None = None

    def _take(self, acquire) -> None:
        """Try until the deadline, then report the lock busy.

        Built on the non-blocking primitive rather than on a blocking one so the
        deadline is this module's own and identical on every platform: a blocking
        ``flock`` cannot be given a timeout portably, and a signal-based alarm
        would be a second concurrency mechanism to reason about.
        """

        deadline = time.monotonic() + self._wait_seconds
        while True:
            try:
                acquire()
                return
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise _fail(
                        PersistenceFailureCode.LOCK_BUSY, "store writer lock is busy"
                    ) from exc
                time.sleep(0.002)

    def __enter__(self) -> ExclusiveStoreLock:
        require_directory(self._path.parent)
        _validate_leaf(self._path.name, "lock name")
        try:
            if self._path.exists() or self._path.is_symlink():
                require_regular_file(self._path)
                flags = os.O_RDWR | _BINARY_FLAG | _NOFOLLOW_FLAG
                fd = os.open(self._path, flags)
            else:
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | _BINARY_FLAG
                try:
                    fd = os.open(self._path, flags, 0o600)
                except FileExistsError:
                    # Two processes contending for a lock that does not exist yet
                    # both find it absent and both try to create it exclusively.
                    # One wins; the loser is not failing to lock, it has simply
                    # been told the file it wanted now exists — which is the
                    # normal case one line above. Reporting LOCK_FAILED here made
                    # first contention on a fresh store look like a broken lock.
                    require_regular_file(self._path)
                    fd = os.open(self._path, os.O_RDWR | _BINARY_FLAG | _NOFOLLOW_FLAG)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or _is_reparse(opened):
                    raise _fail(PersistenceFailureCode.NON_REGULAR_ENTRY, "opened lock entry is not regular")
                path_result = require_regular_file(self._path)
                if (opened.st_dev, opened.st_ino) != (path_result.st_dev, path_result.st_ino):
                    raise _fail(PersistenceFailureCode.LOCK_FAILED, "lock file changed during verified open")
            except BaseException:
                os.close(fd)
                raise
            try:
                stream = os.fdopen(fd, "r+b", buffering=0)
            except BaseException:
                os.close(fd)
                raise
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\x00")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            if os.name == "posix":
                import fcntl

                self._take(lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB))
            elif os.name == "nt":
                import msvcrt

                def _lock_windows() -> None:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        # Normalised so the deadline loop above sees the same
                        # "not now" signal on both platforms.
                        raise BlockingIOError(str(exc)) from exc

                self._take(_lock_windows)
            else:
                raise _fail(
                    PersistenceFailureCode.UNSUPPORTED_DURABILITY_PRIMITIVE,
                    "store lock primitive is unavailable",
                )
        except PersistenceViolation:
            if "stream" in locals():
                stream.close()
            raise
        except OSError as exc:
            if "stream" in locals():
                stream.close()
            raise _fail(PersistenceFailureCode.LOCK_FAILED, "store lock acquisition failed") from exc
        self._stream = stream
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "posix":
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError as release_error:
            if exc is None:
                raise _fail(PersistenceFailureCode.LOCK_FAILED, "store lock release failed") from release_error
        finally:
            stream.close()


def initialize_journal(path: Path) -> None:
    if not isinstance(path, Path):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal path must be an exact Path")
    require_directory(path.parent)
    _validate_leaf(path.name, "journal name")
    if path.exists() or path.is_symlink():
        require_regular_file(path)
        fd = _open_no_follow_read(path)
        try:
            magic = b""
            while len(magic) < len(JOURNAL_FRAME_MAGIC_V1):
                chunk = os.read(fd, len(JOURNAL_FRAME_MAGIC_V1) - len(magic))
                if not chunk:
                    break
                magic += chunk
        except OSError as exc:
            raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal magic read failed") from exc
        finally:
            os.close(fd)
        if magic != JOURNAL_FRAME_MAGIC_V1:
            raise _fail(PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH, "journal magic is invalid")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_FLAG
    try:
        fd = os.open(path, flags, 0o600)
        try:
            _write_all(fd, JOURNAL_FRAME_MAGIC_V1)
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.name == "posix":
            _sync_directory(path.parent)
    except FileExistsError:
        initialize_journal(path)
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal initialization failed") from exc


def encode_journal_frame(payload: bytes) -> bytes:
    if type(payload) is not bytes:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal payload must be exact bytes")
    if len(payload) > MAX_JOURNAL_PAYLOAD_BYTES_V1:
        raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "journal payload exceeds byte limit")
    return len(payload).to_bytes(8, "big", signed=False) + payload + hashlib.sha256(payload).digest()


def _append_journal_frame_unfenced(path: Path, payload: bytes) -> int:
    initialize_journal(path)
    frame = encode_journal_frame(payload)
    flags = os.O_WRONLY | os.O_APPEND
    try:
        fd = _open_no_follow(path, flags)
        try:
            start = os.lseek(fd, 0, os.SEEK_END)
            _write_all(fd, frame)
            os.fsync(fd)
            return start + len(frame)
        finally:
            os.close(fd)
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal append failed") from exc


def append_journal_payload(path: Path, payload: bytes, *, ticket: StoreMutationTicket) -> int:
    """Append one framed record to an authority journal inside an open interval."""

    require_open_mutation_ticket(ticket)
    return _append_journal_frame_unfenced(path, payload)


def append_coordinator_epoch_frame(path: Path, payload: bytes) -> int:
    """The one append that cannot hold a ticket, named so the exemption is visible.

    This writes the coordinator's *own* epoch journal — the frames whose count is
    the epoch. It cannot require a ticket because it is what opening an interval
    consists of, and it does not need one because it is not an authority store: no
    §12 owner reads it for content, and no decision depends on it. The exemption is
    a separate public name rather than a flag on ``append_journal_payload`` so that
    "who is allowed to write unfenced" is answered by the import graph, and the
    dependency tripwire holds this name to the coordinator adapter alone.
    """

    return _append_journal_frame_unfenced(path, payload)


@runtime_checkable
class StoreMutationFencePort(Protocol):
    """What a store must be handed so its mutations are visible to a fenced read.

    Declared here rather than in each owner because all five mutating owners
    already depend on this module for the append itself, and five copies of one
    protocol is five places for them to drift apart.

    **`coordinator_id` is why this is not satisfied by shape alone.** Two objects
    that both know how to mark a mutation are not the same coordinator, and the
    defect that made this necessary was exactly that: a reader was handed one
    fence while the store it read held another, and every structural check passed.
    A reader compares this id against the stores it is about to read and refuses a
    stranger.

    **`mutating` replaces the earlier `bump`.** A counter advanced *after* an
    append cannot make a torn read detectable: a reader can take its entry epoch,
    observe the appended record, take its exit epoch and see no change, because
    the bump has not happened yet. Reversing the order opens the symmetric window.
    Marking the *interval* is what closes both — the count is odd for exactly as
    long as a write is in flight, so a reader that sees an odd count, or a count
    that changed, knows it looked while something was moving.
    """

    def coordinator_id(self) -> str: ...

    def current_epoch(self) -> int: ...

    def mutating(self): ...


def require_store_mutation_fence(value: object) -> StoreMutationFencePort:
    if not isinstance(value, StoreMutationFencePort):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "store fence cannot mark a mutation interval")
    for name in ("coordinator_id", "current_epoch", "mutating"):
        if not callable(getattr(value, name, None)):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, f"store fence is missing {name}")
    return value


@contextmanager
def store_transaction(
    fence: StoreMutationFencePort, *, guard: object = None
) -> Iterator[StoreMutationTicket]:
    """Open one mutation interval for one whole store transaction.

    This replaces ``append_journal_payload_fenced``, and the replacement is the
    correction rather than a rename. That helper marked an interval around a
    *single journal frame*, which is the wrong unit: a library publication writes
    object bytes, rewrites ``index.v1`` — a §21 root — and appends several journal
    records, and between those steps the store is not self-consistent. Marking
    each frame left the counter even, and therefore the store advertised as
    settled, in exactly the gaps that matter.

    So the interval belongs to the transaction. Callers open it once, at the same
    boundary they take their exclusive store lock, and pass the ticket down. A
    nested writer that already has one does not open a second interval — it is
    handed the outer ticket, because two intervals for one transaction would make
    the counter even in the middle of it, which is the same defect written twice.

    ``guard`` is for the caller that already holds the coordinator's writer lock.
    Without one the coordinator takes the lock itself for the whole interval, so
    an ordinary writer needs to know nothing about exclusion; with one it does not
    take a non-recursive lock a second time and refuse itself.
    """

    require_store_mutation_fence(fence)
    # `guard` is opaque here and is handed straight to the coordinator. This owner
    # has no coordinator and must not acquire one: whether a lock is held, and by
    # whom, is a question only the coordinator can answer.
    interval = fence.mutating() if guard is None else fence.mutating(guard=guard)
    # Driven by hand rather than with `with`, because the two ends of the
    # interval fail in ways that are not interchangeable (NR-10). A failure while
    # *opening* means nothing was written and a retry is safe, so it travels
    # untouched. A failure while *closing* means the transaction's writes are
    # already durable and only the mark is missing — a retry there writes
    # everything a second time. The `with` form cannot tell those apart, because
    # both arrive as an exception from the same statement.
    ticket = interval.__enter__()
    try:
        yield require_open_mutation_ticket(ticket)
    except BaseException as exc:
        interval.__exit__(type(exc), exc, exc.__traceback__)
        raise
    try:
        interval.__exit__(None, None, None)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise _fail(
            PersistenceFailureCode.FENCE_NOT_ADVANCED,
            "the transaction is durable and its mutation interval could not be closed",
        ) from exc


def _read_exact_or_eof(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_journal_frames(path: Path) -> Iterator[JournalFrame]:
    try:
        stream = os.fdopen(_open_no_follow_read(path), "rb", buffering=0)
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal open failed") from exc
    with stream:
        magic = _read_exact_or_eof(stream, len(JOURNAL_FRAME_MAGIC_V1))
        if magic != JOURNAL_FRAME_MAGIC_V1:
            raise _fail(PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH, "journal magic is invalid")
        count = 0
        while True:
            start = stream.tell()
            header = _read_exact_or_eof(stream, 8)
            if not header:
                return
            if len(header) != 8:
                raise _fail(PersistenceFailureCode.JOURNAL_TORN_TAIL, "journal length header is torn")
            payload_length = int.from_bytes(header, "big", signed=False)
            if payload_length > MAX_JOURNAL_PAYLOAD_BYTES_V1:
                raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "journal frame exceeds byte limit")
            payload = _read_exact_or_eof(stream, payload_length)
            checksum = _read_exact_or_eof(stream, 32)
            if len(payload) != payload_length or len(checksum) != 32:
                raise _fail(PersistenceFailureCode.JOURNAL_TORN_TAIL, "journal frame is torn")
            if hashlib.sha256(payload).digest() != checksum:
                raise _fail(PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH, "journal frame checksum mismatch")
            count += 1
            if count > MAX_JOURNAL_FRAMES_V1:
                raise _fail(PersistenceFailureCode.JOURNAL_FRAME_LIMIT_EXCEEDED, "journal frame count exceeds limit")
            yield JournalFrame(payload, start, stream.tell())


def scan_journal(path: Path) -> JournalScanResult:
    initialize_journal(path)
    require_regular_file(path)
    frames: list[JournalFrame] = []
    valid_prefix = len(JOURNAL_FRAME_MAGIC_V1)
    try:
        with os.fdopen(_open_no_follow_read(path), "rb", buffering=0) as stream:
            magic = _read_exact_or_eof(stream, len(JOURNAL_FRAME_MAGIC_V1))
            if magic != JOURNAL_FRAME_MAGIC_V1:
                raise _fail(PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH, "journal magic is invalid")
            while True:
                start = stream.tell()
                header = _read_exact_or_eof(stream, 8)
                if not header:
                    return JournalScanResult(tuple(frames), valid_prefix, b"")
                if len(header) != 8:
                    return JournalScanResult(tuple(frames), valid_prefix, header)
                payload_length = int.from_bytes(header, "big", signed=False)
                if payload_length > MAX_JOURNAL_PAYLOAD_BYTES_V1:
                    raise _fail(PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED, "journal frame exceeds byte limit")
                payload = _read_exact_or_eof(stream, payload_length)
                checksum = _read_exact_or_eof(stream, 32)
                if len(payload) != payload_length or len(checksum) != 32:
                    return JournalScanResult(tuple(frames), valid_prefix, header + payload + checksum)
                if hashlib.sha256(payload).digest() != checksum:
                    raise _fail(PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH, "journal frame checksum mismatch")
                frame = JournalFrame(payload, start, stream.tell())
                frames.append(frame)
                if len(frames) > MAX_JOURNAL_FRAMES_V1:
                    raise _fail(
                        PersistenceFailureCode.JOURNAL_FRAME_LIMIT_EXCEEDED,
                        "journal frame count exceeds limit",
                    )
                valid_prefix = frame.end_offset
    except PersistenceViolation:
        raise
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal scan failed") from exc


def truncate_journal_to_valid_prefix(path: Path, valid_prefix_length: int) -> None:
    if type(valid_prefix_length) is not int or valid_prefix_length < len(JOURNAL_FRAME_MAGIC_V1):
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "journal prefix length is invalid")
    try:
        with os.fdopen(_open_no_follow(path, os.O_RDWR), "r+b", buffering=0) as stream:
            stream.truncate(valid_prefix_length)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            _sync_directory(path.parent)
    except OSError as exc:
        raise _fail(PersistenceFailureCode.FILESYSTEM_IO_FAILED, "journal tail repair failed") from exc


def new_operation_id() -> str:
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------
# Stage 4 §21 durable snapshot-boundary commit and integrity verification
#
# Two-phase durable commit for one atomic snapshot boundary. Phase one stages
# every member of the transaction as an immutable file; phase two writes a
# single terminal commit marker. A transaction directory without that marker is
# not a boundary, so a crash between phases leaves an invisible transaction
# rather than a partially visible snapshot. This is the commit-marker half of
# the contract; the domain owner (knowledge.py) decides what may be committed.
# ---------------------------------------------------------------------------

SNAPSHOT_COMMIT_MARKER_V1 = "synapse.stage4.gold.snapshot-commit-marker/v1"
_COMMIT_MARKER_NAME = "commit-marker.json"
_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MEMBER_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class SnapshotTransactionMember:
    """One immutable staged file participating in a boundary transaction."""

    member_name: str
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if type(self.member_name) is not str or _MEMBER_NAME_RE.fullmatch(self.member_name) is None:
            raise _fail(PersistenceFailureCode.INVALID_PATH, "member name is invalid")
        _require_sha256(self.sha256, "member.sha256")
        _require_exact_int(self.byte_length, "member.byte_length")

    def to_payload(self) -> dict[str, object]:
        return {
            "member_name": self.member_name,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
        }

    @classmethod
    def from_payload(cls, value: object) -> SnapshotTransactionMember:
        data = _require_exact_dict(value, ("member_name", "sha256", "byte_length"), "transaction member")
        return cls(data["member_name"], data["sha256"], data["byte_length"])


def _transaction_directory(root: Path, transaction_id: str) -> Path:
    if type(transaction_id) is not str or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None:
        raise _fail(PersistenceFailureCode.INVALID_PATH, "transaction id is invalid")
    return root / transaction_id


def stage_snapshot_transaction(
    root: Path,
    *,
    transaction_id: str,
    members: dict[str, bytes],
    maximum_bytes: int = MAX_METADATA_BYTES_V1,
    ticket: StoreMutationTicket,
) -> tuple[SnapshotTransactionMember, ...]:
    """Stage every transaction member immutably; write no commit marker.

    Re-using a transaction id is refused: a boundary transaction identity is
    consumed exactly once, so a replayed or forked build cannot overwrite an
    earlier one.
    """

    require_open_mutation_ticket(ticket)
    require_directory(root)
    directory = _transaction_directory(root, transaction_id)
    if directory.exists():
        raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "transaction id is already staged")
    if type(members) is not dict or not members:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "transaction members must be a non-empty exact dict")
    ensure_directory(directory)
    staged: list[SnapshotTransactionMember] = []
    for member_name in sorted(members):
        value = members[member_name]
        if type(member_name) is not str or _MEMBER_NAME_RE.fullmatch(member_name) is None:
            raise _fail(PersistenceFailureCode.INVALID_PATH, "member name is invalid")
        if member_name == _COMMIT_MARKER_NAME:
            raise _fail(PersistenceFailureCode.INVALID_PATH, "member name is reserved for the commit marker")
        if type(value) is not bytes:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "member value must be exact bytes")
        file = write_staged_bytes(
            directory,
            final_name=member_name,
            operation_id=new_operation_id(),
            value=value,
            maximum_bytes=maximum_bytes,
            ticket=ticket,
        )
        publish_immutable(file, directory / member_name, ticket=ticket)
        staged.append(
            SnapshotTransactionMember(
                member_name,
                hashlib.sha256(value).hexdigest(),
                len(value),
            )
        )
    return tuple(staged)


def commit_snapshot_transaction(
    root: Path,
    *,
    transaction_id: str,
    members: tuple[SnapshotTransactionMember, ...],
    boundary_id: str,
    marker_sha256: str,
    ticket: StoreMutationTicket,
) -> None:
    """Write the terminal commit marker that makes a staged transaction exist."""

    require_open_mutation_ticket(ticket)
    directory = _transaction_directory(root, transaction_id)
    require_directory(directory)
    if type(members) is not tuple or not members:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "committed members must be a non-empty exact tuple")
    if type(boundary_id) is not str or not boundary_id or len(boundary_id) > 256:
        raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "boundary id is invalid")
    _require_sha256(marker_sha256, "marker_sha256")
    if (directory / _COMMIT_MARKER_NAME).exists():
        raise _fail(PersistenceFailureCode.DESTINATION_EXISTS, "transaction is already committed")
    for member in members:
        if type(member) is not SnapshotTransactionMember:
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "committed member type is invalid")
        observed = read_regular_bytes(directory / member.member_name, maximum_bytes=member.byte_length)
        if hashlib.sha256(observed).hexdigest() != member.sha256 or len(observed) != member.byte_length:
            raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "staged member bytes do not match")
    payload = {
        "schema_version": SNAPSHOT_COMMIT_MARKER_V1,
        "transaction_id": transaction_id,
        "boundary_id": boundary_id,
        "marker_sha256": marker_sha256,
        "members": [member.to_payload() for member in sorted(members, key=lambda item: item.member_name)],
    }
    value = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    file = write_staged_bytes(
        directory,
        final_name=_COMMIT_MARKER_NAME,
        operation_id=new_operation_id(),
        value=value,
        maximum_bytes=MAX_METADATA_BYTES_V1,
        ticket=ticket,
    )
    publish_immutable(file, directory / _COMMIT_MARKER_NAME, ticket=ticket)


def read_committed_snapshot_transaction(
    root: Path,
    *,
    transaction_id: str,
) -> tuple[dict[str, object], dict[str, bytes]]:
    """Return the commit marker and verified member bytes, or fail closed.

    A staged transaction without a terminal marker does not exist: recovery
    reports an absent commit marker instead of assembling a snapshot from the
    partial records it can still see on disk.
    """

    directory = _transaction_directory(root, transaction_id)
    require_directory(directory)
    marker_path = directory / _COMMIT_MARKER_NAME
    if not marker_path.exists():
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker is absent")
    raw = read_regular_bytes(marker_path, maximum_bytes=MAX_METADATA_BYTES_V1)
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker is unreadable") from exc
    marker = _require_exact_dict(
        marker,
        ("schema_version", "transaction_id", "boundary_id", "marker_sha256", "members"),
        "commit marker",
    )
    if marker["schema_version"] != SNAPSHOT_COMMIT_MARKER_V1:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker schema is unknown")
    if marker["transaction_id"] != transaction_id:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker transaction id differs")
    _require_sha256(marker["marker_sha256"], "commit marker.marker_sha256")
    raw_members = marker["members"]
    if type(raw_members) is not list or not raw_members:
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker members are invalid")
    members = tuple(SnapshotTransactionMember.from_payload(item) for item in raw_members)
    names = [member.member_name for member in members]
    if names != sorted(names) or len(set(names)) != len(names):
        raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "commit marker members are unordered")
    observed: dict[str, bytes] = {}
    for member in members:
        value = read_regular_bytes(directory / member.member_name, maximum_bytes=member.byte_length)
        if len(value) != member.byte_length or hashlib.sha256(value).hexdigest() != member.sha256:
            raise _fail(PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED, "committed member bytes do not match")
        observed[member.member_name] = value
    return marker, observed


def committed_transaction_exists(root: Path, *, transaction_id: str) -> bool:
    """Return whether a terminal commit marker exists for ``transaction_id``."""

    directory = _transaction_directory(root, transaction_id)
    return directory.is_dir() and (directory / _COMMIT_MARKER_NAME).is_file()


__all__ = [
    "active_durability_profile",
    "append_journal_payload",
    "append_coordinator_epoch_frame",
    "atomic_replace_metadata",
    "commit_snapshot_transaction",
    "committed_transaction_exists",
    "DurabilityProfile",
    "encode_journal_frame",
    "ensure_directory",
    "ExclusiveStoreLock",
    "initialize_journal",
    "IntegrityManifestDescriptor",
    "iter_journal_frames",
    "JOURNAL_FRAME_MAGIC_V1",
    "JournalFrame",
    "JournalScanResult",
    "LIBRARY_DURABILITY_PROFILE_V1",
    "LIBRARY_INTEGRITY_MANIFEST_V1",
    "MAX_JOURNAL_FRAMES_V1",
    "MAX_JOURNAL_PAYLOAD_BYTES_V1",
    "MAX_METADATA_BYTES_V1",
    "move_immutable",
    "new_operation_id",
    "PersistenceFailureCode",
    "PersistenceViolation",
    "create_coordinator_metadata_once",
    "publish_immutable",
    "read_committed_snapshot_transaction",
    "read_regular_bytes",
    "require_directory",
    "require_regular_file",
    "require_open_mutation_ticket",
    "require_store_mutation_fence",
    "require_ticket_of_coordinator",
    "scan_journal",
    "SNAPSHOT_COMMIT_MARKER_V1",
    "SnapshotTransactionMember",
    "stage_snapshot_transaction",
    "StagedFile",
    "StoreMutationFencePort",
    "StoreMutationTicket",
    "store_transaction",
    "truncate_journal_to_valid_prefix",
    "write_staged_bytes",
]
