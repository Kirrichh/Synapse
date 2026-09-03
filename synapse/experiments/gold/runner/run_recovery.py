"""Exclusive recovery and mutation session for one Gold run-record store.

The run-record coordinator is dedicated to this store. Recovery may roll back
only the one record shape that cannot be resumed: a tail knowledge basis made
visible before its attempt context during an abandoned mutation interval. Every
prefix that already contains a context is preserved and resumed by the normal
attempt materializer, so recovery never replays snapshot, retrieval or worker
work merely to clean storage.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from synapse.experiments.gold.admission_journal import CoordinatorGuard, FileSnapshotFence
from synapse.experiments.gold.persistence import store_transaction

from .attempt_knowledge_store import basis_record_key
from .records import RecordKind, RunRecordStore
from .state_machine import load_run_state
from .vocabulary import GoldRunFailureCode, GoldRunViolation


_SESSION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class PendingRunRecord:
    kind: str
    key: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.kind not in RecordKind.ALL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "pending record kind is unknown")
        if type(self.key) is not str or not self.key:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "pending record key is invalid")
        if type(self.payload) is not dict:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "pending record payload must be exact")


class RunRecordSession:
    """Live exclusive capability for reading and publishing one run."""

    __slots__ = ("_store", "_fence", "_guard", "_trusted_seal")

    def __new__(cls, *args: object, **kwargs: object) -> "RunRecordSession":
        raise TypeError("RunRecordSession is recovery-owner created")

    @property
    def store(self) -> RunRecordStore:
        self._require_live()
        return self._store

    def put(self, record: PendingRunRecord) -> str:
        return self.put_many((record,))[0]

    def put_many(self, records: tuple[PendingRunRecord, ...]) -> tuple[str, ...]:
        """Publish one related record set inside a visible mutation interval."""

        self._require_live()
        if type(records) is not tuple or not records:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record batch must be non-empty")
        if any(type(item) is not PendingRunRecord for item in records):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record batch members must be exact")
        digests: list[str] = []
        with store_transaction(self._fence, guard=self._guard) as ticket:
            for item in records:
                digests.append(self._store.put(
                    kind=item.kind,
                    key=item.key,
                    canonical_payload=item.payload,
                    ticket=ticket,
                ))
        return tuple(digests)

    def _require_live(self) -> None:
        if (
            getattr(self, "_trusted_seal", None) is not _SESSION_SEAL
            or getattr(self._guard, "live", None) is not True
        ):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run-record session is not live")


class RunRecordRecovery:
    """Own explicit recovery for one exact store/coordinator binding."""

    __slots__ = ("_store", "_fence", "_identity_snapshot")

    def __init__(self, *, store: RunRecordStore, fence: FileSnapshotFence) -> None:
        if type(store) is not RunRecordStore or type(fence) is not FileSnapshotFence:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run recovery requires exact store and file coordinator")
        if store.mutation_fence is not fence or store.coordinator_id != fence.coordinator_id():
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "run store and recovery coordinator differ")
        self._store = store
        self._fence = fence
        self._identity_snapshot = (store, fence, store.record_root, store.coordinator_id)

    @property
    def store(self) -> RunRecordStore:
        self._validate_binding()
        return self._store

    @property
    def fence(self) -> FileSnapshotFence:
        self._validate_binding()
        return self._fence

    @contextmanager
    def session(self) -> Iterator[RunRecordSession]:
        """Recover an abandoned prefix, then hold exclusion for the run drive."""

        self._validate_binding()
        with self._fence.exclusive() as guard:
            epoch = self._fence.current_epoch()
            if epoch % 2:
                self._repair_abandoned_prefix(epoch=epoch)
                self._store.audit_recoverable_state()
                self._fence.recover_abandoned_interval(guard=guard)
            self._audit_visible_state()
            session = object.__new__(RunRecordSession)
            object.__setattr__(session, "_store", self._store)
            object.__setattr__(session, "_fence", self._fence)
            object.__setattr__(session, "_guard", guard)
            object.__setattr__(session, "_trusted_seal", _SESSION_SEAL)
            yield session

    def _repair_abandoned_prefix(self, *, epoch: int) -> None:
        """Remove only a basis-only next-attempt prefix from the open interval."""

        context_keys = self._store.iter_keys(kind=RecordKind.ATTEMPT_CONTEXT)
        basis_keys = self._store.iter_keys(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS)
        context_indexes = self._numeric_indexes(context_keys, prefix="")
        basis_indexes = self._numeric_indexes(basis_keys, prefix="attempt-")
        if basis_indexes == context_indexes:
            return
        expected_extra = len(context_indexes) + 1
        if basis_indexes != context_indexes + (expected_extra,):
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "abandoned basis prefix is not a single run tail")
        if self._has_attempt_records(expected_extra):
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "basis-only rollback would discard a visible attempt")
        key = basis_record_key(expected_extra)
        record = self._store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=key)
        if record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "abandoned basis disappeared during recovery")
        self._store.recover_abandoned_record(
            kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
            key=key,
            expected_sha256=record.sha256,
            abandoned_epoch=epoch,
        )

    def _has_attempt_records(self, attempt_index: int) -> bool:
        numeric_key = str(attempt_index)
        if self._store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=numeric_key) is not None:
            return True
        if self._store.get(kind=RecordKind.ATTEMPT_RESULT, key=numeric_key) is not None:
            return True
        if self._store.get(kind=RecordKind.CONTINUATION_EVIDENCE, key=numeric_key) is not None:
            return True
        if self._store.get(kind=RecordKind.DECISION, key=numeric_key) is not None:
            return True
        prefix = f"{attempt_index}."
        return any(key.startswith(prefix) for key in self._store.iter_keys(kind=RecordKind.ATTEMPT_PROGRESS))

    @staticmethod
    def _numeric_indexes(keys: tuple[str, ...], *, prefix: str) -> tuple[int, ...]:
        indexes: list[int] = []
        for key in keys:
            suffix = key[len(prefix):] if prefix and key.startswith(prefix) else key
            if prefix and not key.startswith(prefix):
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "recovery record key is not canonical")
            if not suffix.isascii() or not suffix.isdecimal() or str(int(suffix)) != suffix:
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "recovery record key is not canonical")
            indexes.append(int(suffix))
        return tuple(sorted(indexes))

    def _audit_visible_state(self) -> None:
        self._store.audit_recoverable_state()
        manifest_keys = self._store.iter_keys(kind=RecordKind.MANIFEST)
        non_manifest_keys = tuple(
            key
            for kind in RecordKind.ALL
            if kind != RecordKind.MANIFEST
            for key in self._store.iter_keys(kind=kind)
        )
        if not manifest_keys:
            if non_manifest_keys:
                raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run records exist without their manifest")
            return
        load_run_state(self._store)

    def _validate_binding(self) -> None:
        store, fence, record_root, coordinator_id = self._identity_snapshot
        if (
            store is not self._store
            or fence is not self._fence
            or self._store.record_root is not record_root
            or self._store.mutation_fence is not self._fence
            or self._store.coordinator_id != coordinator_id
            or self._fence.coordinator_id() != coordinator_id
        ):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "run recovery binding changed after construction")


__all__ = ["PendingRunRecord", "RunRecordRecovery", "RunRecordSession"]
