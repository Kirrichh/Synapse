"""Durable, immutable, content-addressed storage for Gold run records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re

from synapse.experiments.gold.persistence import (
    StoreMutationFencePort,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    require_directory,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    write_staged_bytes,
    StoreMutationTicket,
)
from synapse.experiments.gold.runner.models import canonical_run_bytes
from synapse.experiments.gold.runner.vocabulary import GoldRunFailureCode, GoldRunViolation

RUN_RECORDS_DIRECTORY = "run-records"
_MAX_RECORD_BYTES = 1024 * 1024
_MAX_ATTEMPT_PROGRESS_BYTES = 128 * 1024 * 1024
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGE_OPERATION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class RecordKind:
    """Closed record-kind vocabulary; directory names are part of the format."""

    MANIFEST = "run-manifest"
    ATTEMPT_CONTEXT = "attempt-context"
    ATTEMPT_KNOWLEDGE_BASIS = "attempt-knowledge-basis"
    ATTEMPT_RESULT = "attempt-result"
    ATTEMPT_PROGRESS = "attempt-progress"
    CONTINUATION_EVIDENCE = "continuation-evidence"
    DECISION = "run-decision"
    RUN_RESULT = "run-result"

    ALL = (
        MANIFEST,
        ATTEMPT_CONTEXT,
        ATTEMPT_KNOWLEDGE_BASIS,
        ATTEMPT_RESULT,
        ATTEMPT_PROGRESS,
        CONTINUATION_EVIDENCE,
        DECISION,
        RUN_RESULT,
    )


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _record_key(value: object) -> str:
    if type(value) is not str or _SAFE_KEY_RE.fullmatch(value) is None:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "record key is malformed")
    return value


def _record_limit(kind: str) -> int:
    return _MAX_ATTEMPT_PROGRESS_BYTES if kind == RecordKind.ATTEMPT_PROGRESS else _MAX_RECORD_BYTES


def _visible_record_name(name: str) -> tuple[str, str]:
    if type(name) is not str or not name.endswith(".json"):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record has an unknown visible name")
    stem = name[:-5]
    try:
        key, digest = stem.rsplit(".", 1)
    except ValueError as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record name is malformed") from exc
    _record_key(key)
    if _DIGEST_RE.fullmatch(digest) is None:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record name has a malformed digest")
    return key, digest


def _staged_final_name(name: str) -> str | None:
    if not name.startswith(".") or ".stage-" not in name:
        return None
    final_name, operation_id = name[1:].rsplit(".stage-", 1)
    if _STAGE_OPERATION_RE.fullmatch(operation_id) is None:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "staged run record has a malformed operation id")
    _visible_record_name(final_name)
    return final_name


@dataclass(frozen=True)
class StoredRunRecord:
    kind: str
    key: str
    payload: dict[str, object]
    sha256: str


class RunRecordStore:
    """Content-addressed run-record store under ``<run_root>/run-records``."""

    def __init__(self, run_root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(run_root, Path):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be a Path")
        try:
            fence = require_store_mutation_fence(mutation_fence)
            coordinator_id = fence.coordinator_id()
        except (TypeError, ValueError) as exc:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run record store requires a valid mutation fence") from exc
        if type(coordinator_id) is not str or not coordinator_id:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run record mutation fence has no exact coordinator identity")
        self._root = run_root / RUN_RECORDS_DIRECTORY
        self._mutation_fence = fence
        self._coordinator_id = coordinator_id
        ensure_directory(self._root)
        for kind in RecordKind.ALL:
            ensure_directory(self._root / kind)

    @property
    def record_root(self) -> Path:
        return self._root

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    def put(self, *, kind: str, key: str, canonical_payload: dict[str, object], ticket: StoreMutationTicket) -> str:
        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        checked_key = _record_key(key)
        require_ticket_of_coordinator(ticket, coordinator_id=self._coordinator_id)
        expected = canonical_run_bytes(canonical_payload)
        maximum_bytes = _record_limit(kind)
        if not expected or len(expected) > maximum_bytes:
            raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "record payload exceeds store bounds")
        digest = hashlib.sha256(expected).hexdigest()
        directory = self._root / kind
        require_directory(directory)
        destination = directory / f"{checked_key}.{digest}.json"
        if destination.exists() or destination.is_symlink():
            existing = self._read_path(destination, kind=kind, key=checked_key)
            if existing.sha256 != digest:
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "record destination conflicts with stored bytes")
            return existing.sha256
        rival = self._existing_digest(directory, key=checked_key)
        if rival is not None and rival != digest:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "the key already names a record with different bytes")
        staged = write_staged_bytes(
            directory,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=expected,
            maximum_bytes=maximum_bytes,
            ticket=ticket,
        )
        try:
            publish_immutable(staged, destination, ticket=ticket)
        except Exception:
            if destination.exists():
                existing = self._read_path(destination, kind=kind, key=checked_key)
                if existing.sha256 == digest:
                    return existing.sha256
            raise
        return digest

    def _existing_digest(self, directory: Path, *, key: str) -> str | None:
        matches = self._matching_visible_paths(directory, key=key)
        if not matches:
            return None
        if len(matches) > 1:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "record key resolves to several stored payloads")
        return _visible_record_name(matches[0].name)[1]

    @staticmethod
    def _matching_visible_paths(directory: Path, *, key: str) -> tuple[Path, ...]:
        matches: list[Path] = []
        for item in directory.iterdir():
            if item.is_symlink():
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record directory contains a link")
            if _staged_final_name(item.name) is not None:
                continue
            if not item.is_file():
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "visible run record is not a regular file")
            item_key, _ = _visible_record_name(item.name)
            if item_key == key:
                matches.append(item)
        return tuple(matches)

    def get(self, *, kind: str, key: str) -> StoredRunRecord | None:
        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        checked_key = _record_key(key)
        directory = self._root / kind
        require_directory(directory)
        matches = self._matching_visible_paths(directory, key=checked_key)
        if not matches:
            return None
        if len(matches) > 1:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "record key resolves to several stored payloads")
        return self._read_path(matches[0], kind=kind, key=checked_key)

    def iter_keys(self, *, kind: str) -> tuple[str, ...]:
        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        directory = self._root / kind
        require_directory(directory)
        keys: set[str] = set()
        for item in directory.iterdir():
            if item.is_symlink():
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record directory contains a link")
            if _staged_final_name(item.name) is not None:
                continue
            if not item.is_file():
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "visible run record is not a regular file")
            key_part, _ = _visible_record_name(item.name)
            if key_part in keys:
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "record key resolves to several stored payloads")
            keys.add(key_part)
        return tuple(sorted(keys))

    def audit_recoverable_state(self) -> None:
        require_directory(self._root)
        actual: set[str] = set()
        for item in self._root.iterdir():
            if item.is_symlink() or not item.is_dir():
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record root contains an unknown entry")
            actual.add(item.name)
        if actual != set(RecordKind.ALL):
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record directories differ from the closed format")
        for kind in RecordKind.ALL:
            directory = self._root / kind
            maximum_bytes = _record_limit(kind)
            for key in self.iter_keys(kind=kind):
                if self.get(kind=kind, key=key) is None:
                    raise _fail(GoldRunFailureCode.RECORD_MISSING, "visible run record disappeared during audit")
            for item in directory.iterdir():
                if _staged_final_name(item.name) is None:
                    continue
                if item.is_symlink() or not item.is_file():
                    raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "staged run record is not a regular file")
                read_regular_bytes(item, maximum_bytes=maximum_bytes)

    def _read_path(self, path: Path, *, kind: str, key: str) -> StoredRunRecord:
        try:
            raw = read_regular_bytes(path, maximum_bytes=_record_limit(kind))
        except Exception as exc:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "run record is unreadable") from exc
        name_key, name_digest = _visible_record_name(path.name)
        if name_key != key:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored record key differs from its name")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record is not canonical JSON") from exc
        if type(payload) is not dict:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record payload must be a JSON object")
        try:
            canonical = canonical_run_bytes(payload)
        except Exception as exc:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run record payload is not canonical") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if canonical != raw:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored run record bytes are not canonical")
        if name_digest != digest:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored record name is not content-addressed")
        return StoredRunRecord(kind, key, payload, digest)
