"""Durable element-owner jobs over the existing immutable snapshot primitive.

This coordinator owns only memory-maintenance records. Committed events are
immutable; an interrupted preparation remains unpublished and can be retried
under a fresh transaction identity. It grants no execution or admission power.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re

from .admission_journal import FileSnapshotFence, require_live_guard
from .memory_event_storage import MAX_EVENT_MEMBER_BYTES, MemoryEventStorage
from .persistence import (commit_snapshot_transaction, committed_transaction_exists,
    ensure_directory, ensure_durable_directory, new_operation_id,
    require_directory, stage_snapshot_transaction, store_transaction)
from .source_verification import canonical, source_ref
from .stage10.context_codec import decode_canonical

MEMORY_EVENT_V1 = "synapse.stage4.gold.element-owner-event/v1"
# Memory consolidation records share this journal and its owner session: the
# owner's bound memory configuration, each durable session's opening, a
# consolidation report (written before its JUDGED decision), the snapshot
# boundary built from an applied decision, each retention pass (its acts are
# recorded before any raw body leaves store D), a verified snapshot of the
# memory state at a known cut of the court chain and each recorded stand trial
# of two learned competitors.
EVENT_KINDS = {"REQUESTED", "STARTED", "FRAME_COMPLETED", "OUTCOME_RECORDED", "CONSOLIDATED", "OBSERVED", "JUDGED",
               "MEMORY_BOUND", "SESSION_OPENED", "CONSOLIDATION_REPORTED", "SNAPSHOT_BOUNDARY", "MEMORY_RETENTION",
               "MEMORY_STATE", "MEMORY_TRIAL"}


def memory_job_identity(project_identity, run_root, run_id):
    return hashlib.sha256(canonical([project_identity, str(run_root), run_id])).hexdigest()


@dataclass
class _OwnerSession:
    guard: object
    entries: dict[tuple[str, str], tuple[dict, dict]]


class ProjectMemoryStore:
    def __init__(self, state_root, *, read_only=False):
        self.root = state_root / "project-memory"
        self.events = self.root / "events"
        for path in (self.root, self.events):
            (require_directory if read_only else ensure_directory)(path)
        self.fence = FileSnapshotFence(self.root / "coordinator", read_only=read_only)
        self.storage = MemoryEventStorage(self.root)
        self.read_only = read_only
        self._owner_session = None
        # transaction id -> (sha256 of its bytes, the event those bytes were validated as)
        self._validated: dict[str, tuple[str, dict]] = {}

    def _read(self, transaction):
        marker, raw = self.storage.read(transaction)
        digest = hashlib.sha256(raw).hexdigest()
        # Every read hashes the bytes and checks the marker. Bytes this reader already proved canonical and
        # within the contract (the same transaction, the same digest) are not re-validated; any other bytes are.
        known = self._validated.get(transaction)
        if known is not None and known[0] == digest:
            event = deepcopy(known[1])
        else:
            event = decode_canonical(raw)
            if (type(event) is not dict or set(event) != {"schema_version", "kind", "job_key", "payload"}
                    or event["schema_version"] != MEMORY_EVENT_V1 or event["kind"] not in EVENT_KINDS
                    or type(event["job_key"]) is not str or re.fullmatch(r"[0-9a-f]{64}", event["job_key"]) is None
                    or type(event["payload"]) is not dict):
                raise ValueError("memory event differs from its immutable contract")
            self._validated[transaction] = (digest, deepcopy(event))
        if marker["marker_sha256"] != digest or marker["boundary_id"] != event["job_key"]:
            raise ValueError("memory event differs from its immutable contract")
        return event, {"transaction_id": transaction, "ref": source_ref(raw, MEMORY_EVENT_V1).to_dict()}

    def read(self, receipt):
        if type(receipt) is not dict or set(receipt) != {"transaction_id", "ref"}:
            raise ValueError("memory event requires its physical receipt")
        event, actual = self._read(receipt["transaction_id"])
        if actual != receipt:
            raise ValueError("memory event differs from its retained identity")
        return event

    def _scan_inventory(self):
        paths = sorted(self.events.iterdir())
        result, unique = [], {}
        for path in paths:
            require_directory(path)
            if not committed_transaction_exists(self.events, transaction_id=path.name):
                continue  # Prepared bytes have no reader-visible event.
            event, receipt = self._read(path.name)
            key = event["job_key"], event["kind"]
            if key in unique and unique[key] != event:
                raise ValueError("element-owner job has conflicting immutable results")
            if key not in unique:
                result.append((event, receipt))
                unique[key] = event
        return result

    def _held_inventory(self, guard):
        require_live_guard(guard, coordinator_id=self.fence.coordinator_id())
        session = self._owner_session
        if session is None or session.guard is not guard:
            raise ValueError("memory inventory requires its uninterrupted owner session")
        return session

    def inventory(self, *, kind=None, guard=None):
        """Detached records of the selected kind after validating the whole inventory."""
        if kind is not None and (type(kind) is not str or kind not in EVENT_KINDS):
            raise ValueError("unknown memory inventory event kind")
        if guard is None:
            entries = self._scan_inventory()
            return entries if kind is None else [item for item in entries if item[0]["kind"] == kind]
        session = self._held_inventory(guard)
        # Only the owner holding exclusion can reuse this view. Callers receive
        # detached data; physical reads and each new session still validate bytes.
        entries = (item for item in session.entries.values() if kind is None or item[0]["kind"] == kind)
        return deepcopy(sorted(entries, key=lambda item: item[1]["transaction_id"]))

    @contextmanager
    def session(self):
        if self.read_only:
            raise TypeError("a memory reader cannot start owner work")
        with self.fence.exclusive() as guard:
            # This dedicated coordinator has no other mutable stores or index.
            # Every visible object must validate before closing an abandoned
            # interval. Uncommitted snapshot transactions remain unpublished.
            events = self._scan_inventory()
            if self.fence.current_epoch() % 2:
                self.fence.recover_abandoned_interval(guard=guard)
            self._owner_session = _OwnerSession(guard,
                {(event["job_key"], event["kind"]): (event, receipt) for event, receipt in events})
            try:
                yield guard
            finally:
                self._owner_session = None

    def put(self, *, kind, job_key, payload, guard):
        if self.read_only:
            raise TypeError("a memory reader cannot publish owner work")
        event = {"schema_version": MEMORY_EVENT_V1, "kind": kind, "job_key": job_key, "payload": payload}
        if (kind not in EVENT_KINDS or type(job_key) is not str
                or re.fullmatch(r"[0-9a-f]{64}", job_key) is None or type(payload) is not dict):
            raise ValueError("unknown element-owner transition")
        session = self._held_inventory(guard)
        key = job_key, kind
        existing = session.entries.get(key)
        if existing is not None:
            saved, receipt = existing
            if saved != event:
                raise ValueError("element-owner transition cannot be rewritten")
            try:
                self.read(receipt)
            except BaseException:
                self._owner_session = None
                raise
            return deepcopy(receipt)
        raw = canonical(event)
        transaction = new_operation_id()
        try:
            with store_transaction(self.fence, guard=guard) as ticket:
                ensure_durable_directory(self.root, ticket=ticket)
                ensure_durable_directory(self.events, ticket=ticket)
                physical = self.storage.prepare(raw, ticket=ticket)
                members = stage_snapshot_transaction(self.events, transaction_id=transaction,
                    members=physical, maximum_bytes=MAX_EVENT_MEMBER_BYTES, ticket=ticket)
                commit_snapshot_transaction(self.events, transaction_id=transaction, members=members,
                    boundary_id=job_key, marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)
            saved, receipt = self._read(transaction)
        except BaseException:
            # A caught failure can leave a preparation or even a visible commit.
            # The same session must not continue from its earlier inventory.
            self._owner_session = None
            raise
        session.entries[key] = saved, receipt
        return deepcopy(receipt)

    def collect_unreferenced_blocks(self, *, guard):
        """Collect physical blocks that no committed event retains."""
        if self.read_only:
            raise TypeError("a memory reader cannot collect owner storage")
        self._held_inventory(guard)
        try:
            with store_transaction(self.fence, guard=guard) as ticket:
                return self.storage.collect_garbage(ticket=ticket, validate_inventory=self._scan_inventory)
        except BaseException:
            self._owner_session = None
            raise
