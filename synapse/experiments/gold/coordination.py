"""Cross-process coordination for Stage 4 durable stores."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Iterator

from .persistence import (
    ExclusiveStoreLock,
    PersistenceFailureCode,
    PersistenceViolation,
    append_journal_payload,
    ensure_directory,
    initialize_journal,
    new_operation_id,
    scan_journal,
)


SNAPSHOT_COORDINATION_FRAME_SCHEMA_V1 = (
    "synapse.stage4.gold.snapshot-coordination-frame/v1"
)

_SAFE_LEAF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_COORDINATION_CONTEXT_SEAL = object()
_COORDINATED_FENCE_SEAL = object()
_COORDINATED_LEASE_SEAL = object()


def _fail(
    code: PersistenceFailureCode,
    detail: str,
) -> PersistenceViolation:
    return PersistenceViolation(code, detail)


def _require_exact_dict(
    value: object,
    fields: tuple[str, ...],
    field_name: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(fields):
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} has an invalid shape",
        )
    return value

def _bounded_utf8_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} must be non-empty NUL-free text",
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} must be UTF-8 text",
        ) from exc
    if len(encoded) > 512:
        raise _fail(
            PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED,
            f"{field_name} exceeds its UTF-8 byte limit",
        )
    return value


def _normalized_absolute_path(value: object, field_name: str) -> Path:
    if not isinstance(value, Path):
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} must be a Path",
        )
    if not value.is_absolute():
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} must be absolute",
        )
    normalized = Path(os.path.normpath(str(value)))
    if os.path.normcase(str(normalized)) != os.path.normcase(str(value)):
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            f"{field_name} must be normalized",
        )
    return normalized


@dataclass(frozen=True, init=False)
class SnapshotCoordinationContext:
    coordination_root: Path
    fence_identity: str
    base_configuration_id_text: str
    knowledge_admission_configuration_id_text: str
    lock_path: Path
    journal_path: Path
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> SnapshotCoordinationContext:
        raise TypeError("SnapshotCoordinationContext is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_coordination_context(self)
        return {
            "coordination_root": str(self.coordination_root),
            "fence_identity": self.fence_identity,
            "base_configuration_id_text": self.base_configuration_id_text,
            "knowledge_admission_configuration_id_text": (
                self.knowledge_admission_configuration_id_text
            ),
            "lock_path": str(self.lock_path),
            "journal_path": str(self.journal_path),
        }


def _create_snapshot_coordination_context(
    *,
    coordination_root: Path,
    fence_identity: str,
    base_configuration_id_text: str,
    knowledge_admission_configuration_id_text: str,
    lock_path: Path,
    journal_path: Path,
) -> SnapshotCoordinationContext:
    root = _normalized_absolute_path(coordination_root, "coordination_root")
    lock = _normalized_absolute_path(lock_path, "lock_path")
    journal = _normalized_absolute_path(journal_path, "journal_path")
    if lock.parent != root or journal.parent != root:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination lock and journal must be direct children of the root",
        )
    aliases = {
        os.path.normcase(os.path.abspath(str(item)))
        for item in (root, lock, journal)
    }
    if len(aliases) != 3:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination paths alias one another",
        )
    result = object.__new__(SnapshotCoordinationContext)
    object.__setattr__(result, "coordination_root", root)
    object.__setattr__(
        result,
        "fence_identity",
        _bounded_utf8_text(fence_identity, "fence_identity"),
    )
    object.__setattr__(
        result,
        "base_configuration_id_text",
        _bounded_utf8_text(
            base_configuration_id_text,
            "base_configuration_id_text",
        ),
    )
    object.__setattr__(
        result,
        "knowledge_admission_configuration_id_text",
        _bounded_utf8_text(
            knowledge_admission_configuration_id_text,
            "knowledge_admission_configuration_id_text",
        ),
    )
    object.__setattr__(result, "lock_path", lock)
    object.__setattr__(result, "journal_path", journal)
    object.__setattr__(result, "_trusted_seal", _COORDINATION_CONTEXT_SEAL)
    validate_snapshot_coordination_context(result)
    return result


def validate_snapshot_coordination_context(
    value: SnapshotCoordinationContext,
) -> None:
    if (
        type(value) is not SnapshotCoordinationContext
        or getattr(value, "_trusted_seal", None) is not _COORDINATION_CONTEXT_SEAL
    ):
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "snapshot coordination context is not factory sealed",
        )
    root = _normalized_absolute_path(value.coordination_root, "coordination_root")
    lock = _normalized_absolute_path(value.lock_path, "lock_path")
    journal = _normalized_absolute_path(value.journal_path, "journal_path")
    _bounded_utf8_text(value.fence_identity, "fence_identity")
    _bounded_utf8_text(
        value.base_configuration_id_text,
        "base_configuration_id_text",
    )
    _bounded_utf8_text(
        value.knowledge_admission_configuration_id_text,
        "knowledge_admission_configuration_id_text",
    )
    if lock.parent != root or journal.parent != root:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination paths escaped the configured root",
        )
    if len(
        {
            os.path.normcase(os.path.abspath(str(item)))
            for item in (root, lock, journal)
        }
    ) != 3:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination paths alias one another",
        )
def _canonical_coordination_payload(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination payload is not canonical data",
        ) from exc


def _decode_coordination_payload(value: bytes) -> dict[str, object]:
    if type(value) is not bytes:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordination payload must be exact bytes",
        )
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
            "coordination journal payload is malformed",
        ) from exc
    if type(decoded) is not dict or _canonical_coordination_payload(decoded) != value:
        raise _fail(
            PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
            "coordination journal payload is not canonical",
        )
    return decoded


def _coordination_history(
    context: SnapshotCoordinationContext,
) -> tuple[tuple[str, int, str, str | None, int | None], ...]:
    validate_snapshot_coordination_context(context)
    scan = scan_journal(context.journal_path)
    if scan.torn_tail:
        raise _fail(
            PersistenceFailureCode.JOURNAL_TORN_TAIL,
            "coordination journal has a torn tail",
        )
    result: list[tuple[str, int, str, str | None, int | None]] = []
    current_epoch = 0
    current_lease = ""
    for frame in scan.frames:
        raw = _decode_coordination_payload(frame.payload)
        kind = raw.get("kind")
        if kind == "FENCE_EPOCH":
            data = _require_exact_dict(
                raw,
                (
                    "schema_version",
                    "kind",
                    "fence_identity",
                    "epoch",
                    "lease_id",
                ),
                "coordination_epoch",
            )
            if (
                data["schema_version"] != SNAPSHOT_COORDINATION_FRAME_SCHEMA_V1
                or type(data["schema_version"]) is not str
                or data["fence_identity"] != context.fence_identity
                or type(data["fence_identity"]) is not str
                or type(data["epoch"]) is not int
                or data["epoch"] != current_epoch + 1
                or type(data["lease_id"]) is not str
                or _OPERATION_ID_RE.fullmatch(data["lease_id"]) is None
            ):
                raise _fail(
                    PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
                    "coordination epoch frame is invalid",
                )
            current_epoch = data["epoch"]
            current_lease = data["lease_id"]
            result.append(
                ("FENCE_EPOCH", current_epoch, current_lease, None, None)
            )
            continue
        if kind == "STORE_MUTATION":
            data = _require_exact_dict(
                raw,
                (
                    "schema_version",
                    "kind",
                    "fence_identity",
                    "epoch",
                    "lease_id",
                    "store_name",
                    "head_identity",
                    "store_sequence",
                ),
                "coordination_mutation",
            )
            store_name = data["store_name"]
            head_identity = data["head_identity"]
            sequence = data["store_sequence"]
            if (
                data["schema_version"] != SNAPSHOT_COORDINATION_FRAME_SCHEMA_V1
                or type(data["schema_version"]) is not str
                or data["fence_identity"] != context.fence_identity
                or data["epoch"] != current_epoch
                or data["lease_id"] != current_lease
                or type(store_name) is not str
                or _SAFE_LEAF_RE.fullmatch(store_name) is None
                or type(head_identity) is not str
                or not head_identity
                or len(head_identity.encode("utf-8")) > 1024
                or "\x00" in head_identity
                or type(sequence) is not int
                or sequence < 0
            ):
                raise _fail(
                    PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
                    "coordination mutation frame is invalid",
                )
            result.append(
                (
                    store_name,
                    current_epoch,
                    current_lease,
                    head_identity,
                    sequence,
                )
            )
            continue
        raise _fail(
            PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
            "coordination journal frame kind is unknown",
        )
    return tuple(result)


@dataclass(frozen=True, init=False)
class CoordinatedFenceLease:
    context: SnapshotCoordinationContext
    epoch: int
    lease_id: str
    expires_at_monotonic: float
    _owner: CoordinatedSnapshotFence
    _instance_token: object
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> CoordinatedFenceLease:
        raise TypeError("CoordinatedFenceLease is created only by its fence")

    def store_lock(self, path: Path) -> ExclusiveStoreLock:
        require_coordinated_fence_lease(self, expected_context=self.context)
        return ExclusiveStoreLock(path)

    def record_store_mutation(
        self,
        *,
        store_name: str,
        head_identity: str,
        store_sequence: int,
    ) -> None:
        require_coordinated_fence_lease(self, expected_context=self.context)
        if (
            type(store_name) is not str
            or _SAFE_LEAF_RE.fullmatch(store_name) is None
        ):
            raise _fail(
                PersistenceFailureCode.TYPE_MISMATCH,
                "coordinated store name is invalid",
            )
        _bounded_utf8_text(head_identity, "head_identity")
        if type(store_sequence) is not int or store_sequence < 0:
            raise _fail(
                PersistenceFailureCode.TYPE_MISMATCH,
                "coordinated store sequence is invalid",
            )
        payload = {
            "schema_version": SNAPSHOT_COORDINATION_FRAME_SCHEMA_V1,
            "kind": "STORE_MUTATION",
            "fence_identity": self.context.fence_identity,
            "epoch": self.epoch,
            "lease_id": self.lease_id,
            "store_name": store_name,
            "head_identity": head_identity,
            "store_sequence": store_sequence,
        }
        append_journal_payload(
            self.context.journal_path,
            _canonical_coordination_payload(payload),
        )
        history = _coordination_history(self.context)
        if not history or history[-1] != (
            store_name,
            self.epoch,
            self.lease_id,
            head_identity,
            store_sequence,
        ):
            raise _fail(
                PersistenceFailureCode.FILESYSTEM_IO_FAILED,
                "coordinated store mutation was not durably reconstructed",
            )


class CoordinatedSnapshotFence:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _COORDINATED_FENCE_SEAL:
            raise TypeError("CoordinatedSnapshotFence is factory-opened")
        if kwargs or len(args) != 1:
            raise TypeError("CoordinatedSnapshotFence configuration is invalid")
        context = args[0]
        validate_snapshot_coordination_context(context)
        self._context = context
        self._active_token: object | None = None
        self._active_lease: CoordinatedFenceLease | None = None
        self._active_lease_snapshot: tuple[int, str, float, object] | None = None
        ensure_directory(context.coordination_root)
        initialize_journal(context.journal_path)
        _coordination_history(context)

    @property
    def context(self) -> SnapshotCoordinationContext:
        validate_snapshot_coordination_context(self._context)
        return self._context

    def acquire(
        self,
        *,
        lease_duration_seconds: int = 600,
    ) -> _CoordinatedFenceAcquisition:
        if (
            type(lease_duration_seconds) is not int
            or not 1 <= lease_duration_seconds <= 3600
        ):
            raise _fail(
                PersistenceFailureCode.TYPE_MISMATCH,
                "coordinated lease duration is invalid",
            )
        return _CoordinatedFenceAcquisition(self, lease_duration_seconds)

    def current_store_heads(
        self,
    ) -> tuple[tuple[str, str, int, int], ...]:
        heads: dict[str, tuple[str, int, int]] = {}
        for store_name, epoch, _, head_identity, sequence in _coordination_history(
            self._context
        ):
            if store_name == "FENCE_EPOCH":
                continue
            assert head_identity is not None and sequence is not None
            heads[store_name] = (head_identity, sequence, epoch)
        return tuple(
            (store_name, head, sequence, epoch)
            for store_name, (head, sequence, epoch) in sorted(heads.items())
        )

    def _require_active(self, lease: CoordinatedFenceLease) -> None:
        snapshot = self._active_lease_snapshot
        if (
            self._active_lease is not lease
            or self._active_token is None
            or snapshot is None
            or lease._instance_token is not self._active_token
            or snapshot
            != (
                lease.epoch,
                lease.lease_id,
                lease.expires_at_monotonic,
                lease._instance_token,
            )
            or time.monotonic() >= lease.expires_at_monotonic
        ):
            raise _fail(
                PersistenceFailureCode.LOCK_FAILED,
                "coordinated fence lease is released, expired, or foreign",
            )


class _CoordinatedFenceAcquisition:
    def __init__(
        self,
        fence: CoordinatedSnapshotFence,
        lease_duration_seconds: int,
    ) -> None:
        self._fence = fence
        self._lease_duration_seconds = lease_duration_seconds
        self._lock: ExclusiveStoreLock | None = None
        self._lease: CoordinatedFenceLease | None = None

    def __enter__(self) -> CoordinatedFenceLease:
        if self._fence._active_token is not None:
            raise _fail(
                PersistenceFailureCode.LOCK_FAILED,
                "coordinated fence does not allow nested acquisition",
            )
        lock = ExclusiveStoreLock(self._fence.context.lock_path)
        lock.__enter__()
        try:
            history = _coordination_history(self._fence.context)
            epoch = max((item[1] for item in history), default=0) + 1
            lease_id = new_operation_id()
            payload = {
                "schema_version": SNAPSHOT_COORDINATION_FRAME_SCHEMA_V1,
                "kind": "FENCE_EPOCH",
                "fence_identity": self._fence.context.fence_identity,
                "epoch": epoch,
                "lease_id": lease_id,
            }
            append_journal_payload(
                self._fence.context.journal_path,
                _canonical_coordination_payload(payload),
            )
            token = object()
            lease = object.__new__(CoordinatedFenceLease)
            object.__setattr__(lease, "context", self._fence.context)
            object.__setattr__(lease, "epoch", epoch)
            object.__setattr__(lease, "lease_id", lease_id)
            object.__setattr__(
                lease,
                "expires_at_monotonic",
                time.monotonic() + self._lease_duration_seconds,
            )
            object.__setattr__(lease, "_owner", self._fence)
            object.__setattr__(lease, "_instance_token", token)
            object.__setattr__(lease, "_trusted_seal", _COORDINATED_LEASE_SEAL)
            self._fence._active_token = token
            self._fence._active_lease = lease
            self._fence._active_lease_snapshot = (
                lease.epoch,
                lease.lease_id,
                lease.expires_at_monotonic,
                token,
            )
            require_coordinated_fence_lease(
                lease,
                expected_context=self._fence.context,
            )
        except BaseException:
            self._fence._active_lease = None
            self._fence._active_token = None
            self._fence._active_lease_snapshot = None
            lock.__exit__(None, None, None)
            raise
        self._lock = lock
        self._lease = lease
        return lease

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        lease = self._lease
        lock = self._lock
        self._lease = None
        self._lock = None
        if lease is not None:
            self._fence._active_lease = None
            self._fence._active_token = None
            self._fence._active_lease_snapshot = None
        if lock is not None:
            lock.__exit__(exc_type, exc, traceback)


def open_coordinated_snapshot_fence(
    context: SnapshotCoordinationContext,
) -> CoordinatedSnapshotFence:
    validate_snapshot_coordination_context(context)
    return CoordinatedSnapshotFence(
        context,
        _seal=_COORDINATED_FENCE_SEAL,
    )


def require_coordinated_fence_lease(
    value: CoordinatedFenceLease,
    *,
    expected_context: SnapshotCoordinationContext,
) -> int:
    validate_snapshot_coordination_context(expected_context)
    if (
        type(value) is not CoordinatedFenceLease
        or getattr(value, "_trusted_seal", None) is not _COORDINATED_LEASE_SEAL
        or type(getattr(value, "_instance_token", None)) is not object
        or type(getattr(value, "_owner", None)) is not CoordinatedSnapshotFence
    ):
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated fence lease is not factory sealed",
        )
    validate_snapshot_coordination_context(value.context)
    if value.context is not expected_context:
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated fence lease belongs to another context",
        )
    if type(value.epoch) is not int or value.epoch < 1:
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated fence epoch is invalid",
        )
    if type(value.lease_id) is not str or _OPERATION_ID_RE.fullmatch(
        value.lease_id
    ) is None:
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated fence lease identity is invalid",
        )
    if (
        type(value.expires_at_monotonic) is not float
        or value.expires_at_monotonic <= 0
    ):
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated fence lease expiry is invalid",
        )
    value._owner._require_active(value)
    return value.epoch


@contextmanager
def coordinated_store_write(
    *,
    fence: CoordinatedSnapshotFence,
    context: SnapshotCoordinationContext,
    store_lock_path: Path,
    fence_lease: CoordinatedFenceLease | None = None,
) -> Iterator[CoordinatedFenceLease]:
    if type(fence) is not CoordinatedSnapshotFence:
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "coordinated store fence is invalid",
        )
    validate_snapshot_coordination_context(context)
    if fence.context is not context:
        raise _fail(
            PersistenceFailureCode.LOCK_FAILED,
            "coordinated store fence belongs to another context",
        )
    if not isinstance(store_lock_path, Path):
        raise _fail(
            PersistenceFailureCode.TYPE_MISMATCH,
            "store lock path must be a Path",
        )
    if fence_lease is None:
        with fence.acquire() as acquired:
            with acquired.store_lock(store_lock_path):
                yield acquired
        return
    require_coordinated_fence_lease(
        fence_lease,
        expected_context=context,
    )
    with fence_lease.store_lock(store_lock_path):
        yield fence_lease
