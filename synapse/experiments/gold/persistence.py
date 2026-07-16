"""Durable filesystem primitives for the Stage 4 Gold behavior library.

The module owns byte-exact staged writes, non-overwriting immutable
publication, rebuildable metadata replacement, a framed append-only journal,
and the local single-writer lock.  It deliberately has no knowledge of
Behavior admission, lifecycle, retrieval, or execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from typing import BinaryIO, Iterator


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


def write_staged_bytes(
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


def publish_immutable(staged: StagedFile, destination: Path) -> None:
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


def move_immutable(source: Path, destination: Path, *, maximum_bytes: int) -> None:
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


def atomic_replace_metadata(
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
    staged = write_staged_bytes(
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


class ExclusiveStoreLock:
    """OS-released, non-blocking exclusive lock for one local store."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise _fail(PersistenceFailureCode.TYPE_MISMATCH, "lock path must be an exact Path")
        self._path = path
        self._stream: BinaryIO | None = None

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
                fd = os.open(self._path, flags, 0o600)
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

                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise _fail(PersistenceFailureCode.LOCK_BUSY, "store writer lock is busy") from exc
            elif os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise _fail(PersistenceFailureCode.LOCK_BUSY, "store writer lock is busy") from exc
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


def append_journal_payload(path: Path, payload: bytes) -> int:
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


__all__ = [
    "DurabilityProfile",
    "ExclusiveStoreLock",
    "IntegrityManifestDescriptor",
    "JOURNAL_FRAME_MAGIC_V1",
    "JournalFrame",
    "JournalScanResult",
    "LIBRARY_DURABILITY_PROFILE_V1",
    "LIBRARY_INTEGRITY_MANIFEST_V1",
    "MAX_JOURNAL_FRAMES_V1",
    "MAX_JOURNAL_PAYLOAD_BYTES_V1",
    "MAX_METADATA_BYTES_V1",
    "PersistenceFailureCode",
    "PersistenceViolation",
    "StagedFile",
    "active_durability_profile",
    "append_journal_payload",
    "atomic_replace_metadata",
    "encode_journal_frame",
    "ensure_directory",
    "initialize_journal",
    "iter_journal_frames",
    "move_immutable",
    "new_operation_id",
    "publish_immutable",
    "read_regular_bytes",
    "require_directory",
    "require_regular_file",
    "scan_journal",
    "truncate_journal_to_valid_prefix",
    "write_staged_bytes",
]
