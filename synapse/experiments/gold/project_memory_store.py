"""Durable element-owner jobs over the existing immutable snapshot primitive.

This coordinator owns only memory-maintenance records. Committed events are
immutable; an interrupted preparation remains unpublished and can be retried
under a fresh transaction identity. It grants no execution or admission power.
"""
from contextlib import contextmanager
import hashlib
import re

from .admission_journal import FileSnapshotFence
from .persistence import (commit_snapshot_transaction, committed_transaction_exists,
    ensure_directory, new_operation_id, read_committed_snapshot_transaction,
    require_directory, stage_snapshot_transaction, store_transaction)
from .source_verification import canonical, source_ref
from .stage10.context_codec import decode_canonical

MEMORY_EVENT_V1 = "synapse.stage4.gold.element-owner-event/v1"
MAX_MEMORY_EVENT_BYTES = 16 * 1024 * 1024
MAX_MEMORY_EVENTS = 4096
EVENT_KINDS = {"REQUESTED", "STARTED", "FRAME_COMPLETED", "OUTCOME_RECORDED"}


def memory_job_identity(project_identity, run_root, run_id):
    return hashlib.sha256(canonical([project_identity, str(run_root), run_id])).hexdigest()


class ProjectMemoryStore:
    def __init__(self, state_root, *, read_only=False):
        self.root = state_root / "project-memory"
        self.events = self.root / "events"
        for path in (self.root, self.events):
            (require_directory if read_only else ensure_directory)(path)
        self.fence = FileSnapshotFence(self.root / "coordinator", read_only=read_only)
        self.read_only = read_only

    def _read(self, transaction):
        marker, members = read_committed_snapshot_transaction(self.events, transaction_id=transaction)
        if set(members) != {"event.json"}:
            raise ValueError("memory event has undeclared members")
        raw = members["event.json"]
        event = decode_canonical(raw)
        if (len(raw) > MAX_MEMORY_EVENT_BYTES or canonical(event) != raw
                or set(event) != {"schema_version", "kind", "job_key", "payload"}
                or event["schema_version"] != MEMORY_EVENT_V1 or event["kind"] not in EVENT_KINDS
                or type(event["job_key"]) is not str or re.fullmatch(r"[0-9a-f]{64}", event["job_key"]) is None
                or type(event["payload"]) is not dict
                or marker["marker_sha256"] != hashlib.sha256(raw).hexdigest()
                or marker["boundary_id"] != event["job_key"]):
            raise ValueError("memory event differs from its immutable contract")
        return event, {"transaction_id": transaction, "ref": source_ref(raw, MEMORY_EVENT_V1).to_dict()}

    def read(self, receipt):
        if type(receipt) is not dict or set(receipt) != {"transaction_id", "ref"}:
            raise ValueError("memory event requires its physical receipt")
        event, actual = self._read(receipt["transaction_id"])
        if actual != receipt:
            raise ValueError("memory event differs from its retained identity")
        return event

    def inventory(self):
        paths = sorted(self.events.iterdir())
        if len(paths) > MAX_MEMORY_EVENTS:
            raise ValueError("project memory exceeds its explicit event budget")
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

    @contextmanager
    def session(self):
        if self.read_only:
            raise TypeError("a memory reader cannot start owner work")
        with self.fence.exclusive() as guard:
            # This dedicated coordinator has no other mutable stores or index.
            # Every visible object must validate before closing an abandoned
            # interval. Uncommitted snapshot transactions remain unpublished.
            self.inventory()
            if self.fence.current_epoch() % 2:
                self.fence.recover_abandoned_interval(guard=guard)
            yield guard

    def put(self, *, kind, job_key, payload, guard):
        if self.read_only:
            raise TypeError("a memory reader cannot publish owner work")
        event = {"schema_version": MEMORY_EVENT_V1, "kind": kind, "job_key": job_key, "payload": payload}
        if (kind not in EVENT_KINDS or type(job_key) is not str
                or re.fullmatch(r"[0-9a-f]{64}", job_key) is None or type(payload) is not dict):
            raise ValueError("unknown element-owner transition")
        for saved, receipt in self.inventory():
            if (saved["job_key"], saved["kind"]) == (job_key, kind):
                if saved != event:
                    raise ValueError("element-owner transition cannot be rewritten")
                return receipt
        if len(tuple(self.events.iterdir())) >= MAX_MEMORY_EVENTS:
            raise ValueError("project memory exceeds its explicit event budget")
        raw = canonical(event)
        if len(raw) > MAX_MEMORY_EVENT_BYTES:
            raise ValueError("memory event exceeds its retained byte budget")
        transaction = new_operation_id()
        with store_transaction(self.fence, guard=guard) as ticket:
            members = stage_snapshot_transaction(self.events, transaction_id=transaction,
                members={"event.json": raw}, maximum_bytes=MAX_MEMORY_EVENT_BYTES, ticket=ticket)
            commit_snapshot_transaction(self.events, transaction_id=transaction, members=members,
                boundary_id=job_key, marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)
        return self._read(transaction)[1]
