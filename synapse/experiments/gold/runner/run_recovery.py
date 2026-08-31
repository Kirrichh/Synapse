"""Exclusive recovery and mutation session for one Gold run-record store.

The run-record coordinator is deliberately dedicated to this store.  Closing an
abandoned mutation interval is a statement that every store under that
coordinator has been inspected, so sharing it with Stage 10 or an admission
journal would make local recovery an invalid global claim.  This owner performs
that inspection, holds exclusion for the complete controller drive, and exposes
only fenced immutable publications to the controller.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from synapse.experiments.gold.admission_journal import (
    CoordinatorGuard,
    FileSnapshotFence,
)
from synapse.experiments.gold.persistence import store_transaction

from .records import RecordKind, RunRecordStore
from .state_machine import load_run_state
from .vocabulary import GoldRunFailureCode, GoldRunViolation


_SESSION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class PendingRunRecord:
    """One immutable publication requested inside a run-store transaction."""

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
        """Publish one immutable record under this session's coordinator."""

        return self.put_many((record,))[0]

    def put_many(self, records: tuple[PendingRunRecord, ...]) -> tuple[str, ...]:
        """Publish a related record set inside one mutation interval.

        The files remain individually immutable.  The shared interval is what
        makes a crash between them visible, so recovery can inspect the partial
        prefix before declaring the coordinator settled again.
        """

        self._require_live()
        if type(records) is not tuple or not records:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record batch must be non-empty")
        if any(type(item) is not PendingRunRecord for item in records):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "record batch members must be exact")
        digests: list[str] = []
        with store_transaction(self._fence, guard=self._guard) as ticket:
            for item in records:
                digests.append(
                    self._store.put(
                        kind=item.kind,
                        key=item.key,
                        canonical_payload=item.payload,
                        ticket=ticket,
                    )
                )
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
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "run recovery requires the exact store and file coordinator",
            )
        if store.mutation_fence is not fence or store.coordinator_id != fence.coordinator_id():
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "run store and recovery coordinator differ",
            )
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
        """Audit/recover if necessary, then hold exclusion for the run drive."""

        self._validate_binding()
        with self._fence.exclusive() as guard:
            self._audit_visible_state()
            if self._fence.current_epoch() % 2:
                self._fence.recover_abandoned_interval(guard=guard)
                self._audit_visible_state()
            session = object.__new__(RunRecordSession)
            object.__setattr__(session, "_store", self._store)
            object.__setattr__(session, "_fence", self._fence)
            object.__setattr__(session, "_guard", guard)
            object.__setattr__(session, "_trusted_seal", _SESSION_SEAL)
            yield session

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
                raise _fail(
                    GoldRunFailureCode.RECORD_CONFLICT,
                    "run records exist without their manifest",
                )
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
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "run recovery binding changed after construction",
            )


__all__ = ["PendingRunRecord", "RunRecordRecovery", "RunRecordSession"]
