"""Durable, immutable, content-addressed storage for Gold run records.

Every record (manifest, attempt context, attempt result, next-attempt
decision, run result) is published as one canonical JSON file whose name
binds the record key and the sha256 of its exact bytes. Publication goes
through the shared mutation-fence primitives, so a run record exists
durably or not at all — the property crash recovery depends on. Records
are never rewritten; a different payload under an already-recorded key is
a fail-closed conflict rather than an overwrite (NR-13).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re

from synapse.experiments.gold.persistence import (
    ensure_directory,
    publish_immutable,
    read_regular_bytes,
    require_directory,
    write_staged_bytes,
    new_operation_id,
    StoreMutationTicket,
)
from synapse.experiments.gold.runner.models import canonical_run_bytes
from synapse.experiments.gold.runner.vocabulary import (
    GoldRunFailureCode,
    GoldRunViolation,
)

RUN_RECORDS_DIRECTORY = "run-records"
_MAX_RECORD_BYTES = 1024 * 1024
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RecordKind:
    """Closed record-kind vocabulary; directory names are part of the format."""

    MANIFEST = "run-manifest"
    ATTEMPT_CONTEXT = "attempt-context"
    ATTEMPT_RESULT = "attempt-result"
    DECISION = "run-decision"
    RUN_RESULT = "run-result"

    ALL = (MANIFEST, ATTEMPT_CONTEXT, ATTEMPT_RESULT, DECISION, RUN_RESULT)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _record_key(value: object) -> str:
    if type(value) is not str or _SAFE_KEY_RE.fullmatch(value) is None:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "record key is malformed")
    return value


@dataclass(frozen=True)
class StoredRunRecord:
    kind: str
    key: str
    payload: dict[str, object]
    sha256: str


class RunRecordStore:
    """Content-addressed run-record store under ``<run_root>/run-records``."""

    def __init__(self, run_root: Path) -> None:
        if not isinstance(run_root, Path):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be a Path")
        self._root = run_root / RUN_RECORDS_DIRECTORY
        ensure_directory(self._root)
        for kind in RecordKind.ALL:
            ensure_directory(self._root / kind)

    def put(
        self,
        *,
        kind: str,
        key: str,
        canonical_payload: dict[str, object],
        ticket: StoreMutationTicket,
    ) -> str:
        """Publish one record; idempotent for identical bytes, conflict otherwise."""

        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        checked_key = _record_key(key)
        if type(ticket) is not StoreMutationTicket:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record publication requires an exact mutation ticket")
        expected = canonical_run_bytes(canonical_payload)
        if not expected or len(expected) > _MAX_RECORD_BYTES:
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
        # A record is written once. A second payload under a key that already
        # has one is a divergence, and it is refused here rather than at the
        # next read: a run root holding two results for one attempt has already
        # lost the property the reader would be checking.
        rival = self._existing_digest(directory, key=checked_key)
        if rival is not None and rival != digest:
            raise _fail(
                GoldRunFailureCode.RECORD_CONFLICT,
                "the key already names a record with different bytes",
            )
        staged = write_staged_bytes(
            directory,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=expected,
            maximum_bytes=_MAX_RECORD_BYTES * 2,
            ticket=ticket,
        )
        try:
            publish_immutable(staged, destination, ticket=ticket)
        except Exception as exc:
            if destination.exists():
                existing = self._read_path(destination, kind=kind, key=checked_key)
                if existing.sha256 == digest:
                    return existing.sha256
            raise
        return digest

    def _existing_digest(self, directory: Path, *, key: str) -> str | None:
        """The digest already recorded under this key, if any."""

        prefix = f"{key}."
        for item in directory.iterdir():
            if item.name.startswith(prefix) and item.name.endswith(".json"):
                return item.name[: -len(".json")].rsplit(".", 1)[-1]
        return None

    def get(self, *, kind: str, key: str) -> StoredRunRecord | None:
        """Return the unique record for ``(kind, key)`` or None; conflicts fail closed."""

        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        checked_key = _record_key(key)
        directory = self._root / kind
        require_directory(directory)
        prefix = f"{checked_key}."
        matches = [item for item in directory.iterdir() if item.name.startswith(prefix) and item.name.endswith(".json")]
        if not matches:
            return None
        if len(matches) > 1:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "record key resolves to several stored payloads")
        return self._read_path(matches[0], kind=kind, key=checked_key)

    def iter_keys(self, *, kind: str) -> tuple[str, ...]:
        """All stored keys of one kind, sorted; duplicate digests per key fail later."""

        if kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record kind is unknown")
        directory = self._root / kind
        require_directory(directory)
        keys: set[str] = set()
        for item in directory.iterdir():
            if not item.name.endswith(".json"):
                continue
            key_part = item.name[: -len(".json")].rsplit(".", 1)[0]
            keys.add(key_part)
        return tuple(sorted(keys))

    def _read_path(self, path: Path, *, kind: str, key: str) -> StoredRunRecord:
        try:
            raw = read_regular_bytes(path, maximum_bytes=_MAX_RECORD_BYTES * 2)
        except Exception as exc:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "run record is unreadable") from exc
        stem = path.name[: -len(".json")]
        name_key, name_digest = stem.split(".", 1)
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
