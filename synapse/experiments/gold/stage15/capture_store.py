"""OBS-03/04: durable physical-call inventory, receipts and source retention.

The capture coordinator is separate from the run coordinator: a provider
request may arrive while the run controller holds its lock waiting for Mini.
Only this owner repairs its own torn append suffix. Inspection uses existing
read-only persistence primitives and never initializes or repairs a store.
All source bytes remain retained for the lifetime of the run, including failed
and in-flight calls. There is no independent telemetry garbage collector.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import secrets
import time

from synapse.llm.capture import CaptureUnavailable

from ..admission_journal import FileSnapshotFence, FENCE_IDENTITY_NAME
from ..canonicalization import HashBoundRef, RefKind
from ..persistence import (
    append_journal_payload, encode_journal_frame, ensure_directory,
    initialize_journal, iter_journal_frames, new_operation_id, publish_immutable,
    read_regular_bytes, require_directory, scan_journal,
    store_transaction, truncate_journal_to_valid_prefix, write_staged_bytes,
)
from .telemetry import (
    TELEMETRY_SCHEMA, canonical, exact_fields, identifier, identity, reference,
    UsageProfile, call_record_from_capture,
)

CAPTURE_SCHEMA = "synapse.stage4.gold.provider-capture/v1"
CAPTURE_CUT_SCHEMA = "synapse.stage4.gold.capture-cut/v1"
_MAX_SOURCE = 16 * 1024 * 1024
_MAX_LEDGER = 128 * 1024 * 1024
_KINDS = {"RUN_OPEN", "INVOCATION_OPEN", "LOGICAL_OPEN", "CALL_STARTED", "CALL_RESPONSE",
          "CALL_FAILED", "LOGICAL_CLOSED", "INVOCATION_CLOSED"}


@dataclass(frozen=True)
class CaptureCut:
    root: Path
    run_id: str
    coordinator_id: str
    ledger_ref: HashBoundRef
    sequence: int
    last_record_hash: str

    def __post_init__(self):
        identifier(self.run_id)
        if (type(self.root) is not type(Path()) or not self.root.is_absolute()
                or type(self.sequence) is not int or not 1 <= self.sequence <= 1_000_000
                or type(self.coordinator_id) is not str or len(self.coordinator_id) != 32
                or any(c not in "0123456789abcdef" for c in self.coordinator_id)
                or type(self.last_record_hash) is not str or len(self.last_record_hash) != 64
                or any(c not in "0123456789abcdef" for c in self.last_record_hash)
                or type(self.ledger_ref) is not HashBoundRef or self.ledger_ref.kind is not RefKind.SOURCE_EVIDENCE
                or self.ledger_ref.schema_id != "synapse.capture-journal/v1"
                or self.ledger_ref.byte_length > _MAX_LEDGER):
            raise CaptureUnavailable("invalid capture prefix identity")

    def to_dict(self) -> dict:
        return {"schema_version": CAPTURE_CUT_SCHEMA, "root": str(self.root), "run_id": self.run_id,
            "coordinator_id": self.coordinator_id, "ledger_ref": self.ledger_ref.to_dict(),
            "sequence": self.sequence, "last_record_hash": self.last_record_hash,
            "retention_policy": "retain-with-run/v1"}

    @classmethod
    def from_dict(cls, value: object):
        v = exact_fields(value, {"schema_version", "root", "run_id", "coordinator_id", "ledger_ref",
                                "sequence", "last_record_hash", "retention_policy"})
        cut = cls(Path(v["root"]), v["run_id"], v["coordinator_id"], HashBoundRef.from_dict(v["ledger_ref"]),
                  v["sequence"], v["last_record_hash"])
        if cut.to_dict() != v or not cut.root.is_absolute() or type(cut.sequence) is not int or cut.sequence < 1:
            raise CaptureUnavailable("invalid retained capture cut")
        return cut


def read_source(root: Path, ref: HashBoundRef) -> bytes:
    """Physical CAS inspection; a reference is not proof that bytes exist."""
    raw = read_regular_bytes(root / "sources" / ref.sha256, maximum_bytes=_MAX_SOURCE)
    if hashlib.sha256(raw).hexdigest() != ref.sha256 or len(raw) != ref.byte_length:
        raise CaptureUnavailable("retained capture source hash or length changed")
    return raw


def inspect_capture(cut: CaptureCut) -> tuple[dict, ...]:
    """Read exactly a retained prefix, without acquiring a mutating fence."""
    if type(cut) is not CaptureCut:
        raise CaptureUnavailable("capture inspection requires an exact cut")
    require_directory(cut.root)
    coordinator = read_regular_bytes(cut.root / "coordinator" / FENCE_IDENTITY_NAME, maximum_bytes=32).decode("ascii")
    if coordinator != cut.coordinator_id:
        raise CaptureUnavailable("capture coordinator changed")
    raw = read_regular_bytes(cut.root / "calls.v1", maximum_bytes=_MAX_LEDGER)
    prefix = raw[:cut.ledger_ref.byte_length]
    if len(prefix) != cut.ledger_ref.byte_length or hashlib.sha256(prefix).hexdigest() != cut.ledger_ref.sha256:
        raise CaptureUnavailable("capture inventory prefix is absent or changed")
    # A later unrelated append cannot change the identity of this settled cut.
    frames = tuple(iter_journal_frames(cut.root / "calls.v1", prefix_length=cut.ledger_ref.byte_length))
    selected = tuple(frame for frame in frames if frame.end_offset <= cut.ledger_ref.byte_length)
    if len(selected) != cut.sequence or not selected or selected[-1].end_offset != cut.ledger_ref.byte_length:
        raise CaptureUnavailable("capture cut does not end at its expected frame")
    records = _decode_frames(selected)
    if records[0]["payload"]["run_id"] != cut.run_id or records[-1]["record_hash"] != cut.last_record_hash:
        raise CaptureUnavailable("capture cut identifies another run or suffix")
    for record in records:
        for name in ("invocation_ref", "request_ref", "response_ref", "trajectory_ref"):
            if name in record["payload"]:
                read_source(cut.root, HashBoundRef.from_dict(record["payload"][name]))
    _validate_history(records)
    return records


def _decode_frames(frames) -> tuple[dict, ...]:
    result = []
    previous = None
    for sequence, frame in enumerate(frames, 1):
        value = json.loads(frame.payload)
        exact_fields(value, {"schema_version", "sequence", "kind", "previous_record_hash", "payload", "record_hash"})
        payload = {key: value[key] for key in value if key != "record_hash"}
        if (value["schema_version"] != CAPTURE_SCHEMA or value["sequence"] != sequence
                or value["kind"] not in _KINDS or value["previous_record_hash"] != previous
                or value["record_hash"] != identity("synapse.capture.frame/v1", payload)
                or canonical(value) != frame.payload):
            raise CaptureUnavailable("capture sequence, identity or schema changed")
        previous = value["record_hash"]
        result.append(value)
    return tuple(result)


def _validate_history(records) -> None:
    invocations, logical, calls = {}, {}, {}
    logical_closed, invocation_closed, call_closed = set(), set(), set()
    for r in records:
        kind, p = r["kind"], r["payload"]
        fields = {
            "RUN_OPEN": {"run_id", "manifest_ref", "coordinator_id", "retention_policy"},
            "INVOCATION_OPEN": {"invocation_id", "attempt_id", "provider", "model", "usage_profile", "worker_profile",
                "clock_domain", "started_unix_ns", "started_monotonic_ns", "invocation_ref"},
            "LOGICAL_OPEN": {"invocation_id", "logical_call_id", "request_identity"},
            "CALL_STARTED": {"call_id", "logical_call_id", "invocation_id", "clock_domain", "started_unix_ns", "started_monotonic_ns", "request_ref"},
            "CALL_RESPONSE": {"call_id", "clock_domain", "ended_monotonic_ns", "status_code", "provider_request_id", "response_ref"},
            "CALL_FAILED": {"call_id", "clock_domain", "ended_monotonic_ns", "error_code"},
            "LOGICAL_CLOSED": {"logical_call_id", "status"},
            "INVOCATION_CLOSED": {"invocation_id", "process_status", "clock_domain", "ended_monotonic_ns"},
        }[kind]
        if kind == "INVOCATION_CLOSED" and "trajectory_ref" in p:
            fields = fields | {"trajectory_ref"}
        exact_fields(p, fields)
        for field in ("invocation_id", "logical_call_id", "call_id", "attempt_id", "run_id", "provider", "model", "worker_profile", "clock_domain", "error_code"):
            if field in p:
                identifier(p[field])
        for field in ("started_unix_ns", "started_monotonic_ns", "ended_monotonic_ns"):
            if field in p and (type(p[field]) is not str or not p[field].isdigit()
                    or str(int(p[field])) != p[field] or not 0 <= int(p[field]) <= 2**63 - 1):
                raise CaptureUnavailable("capture clock is not an exact bounded sample")
        for field in ("manifest_ref", "invocation_ref", "request_ref", "response_ref", "trajectory_ref"):
            if field in p:
                HashBoundRef.from_dict(p[field])
        if kind == "INVOCATION_OPEN":
            UsageProfile(p["usage_profile"])
        if kind == "CALL_RESPONSE" and (type(p["status_code"]) is not int or not 100 <= p["status_code"] <= 599):
            raise CaptureUnavailable("capture response status is invalid")
        if kind == "LOGICAL_CLOSED" and p["status"] not in {"COMPLETED", "FAILED"}:
            raise CaptureUnavailable("capture logical status is invalid")
        if kind == "INVOCATION_CLOSED" and p["process_status"] not in {"EXITED", "TIMEOUT", "INTERRUPTED", "NOT_STARTED"}:
            raise CaptureUnavailable("capture process status is invalid")
        if kind == "RUN_OPEN":
            if r["sequence"] != 1:
                raise CaptureUnavailable("duplicate capture run header")
        elif kind == "INVOCATION_OPEN":
            name = p["invocation_id"]
            if name in invocations:
                raise CaptureUnavailable("invocation was registered twice")
            invocations[name] = p
        elif kind == "LOGICAL_OPEN":
            name = p["logical_call_id"]
            if name in logical or p["invocation_id"] not in invocations or p["invocation_id"] in invocation_closed:
                raise CaptureUnavailable("logical call lies outside its invocation")
            logical[name] = p
        elif kind == "CALL_STARTED":
            name, owner = p["call_id"], p["logical_call_id"]
            if (name in calls or owner not in logical or owner in logical_closed
                    or p["invocation_id"] != logical[owner]["invocation_id"]
                    or p["invocation_id"] in invocation_closed):
                raise CaptureUnavailable("physical call lies outside its registered logical call")
            calls[name] = p
        elif kind in ("CALL_RESPONSE", "CALL_FAILED"):
            name = p["call_id"]
            if name not in calls or name in call_closed:
                raise CaptureUnavailable("physical call has duplicate or orphan terminal evidence")
            if p["clock_domain"] != calls[name]["clock_domain"] or int(p["ended_monotonic_ns"]) < int(calls[name]["started_monotonic_ns"]):
                raise CaptureUnavailable("physical call clock interval changed")
            if calls[name]["logical_call_id"] in logical_closed or calls[name]["invocation_id"] in invocation_closed:
                raise CaptureUnavailable("physical response arrived outside its owning lifetime")
            call_closed.add(name)
        elif kind == "LOGICAL_CLOSED":
            name = p["logical_call_id"]
            if name not in logical or name in logical_closed:
                raise CaptureUnavailable("duplicate or orphan logical completion")
            logical_closed.add(name)
        elif kind == "INVOCATION_CLOSED":
            name = p["invocation_id"]
            if name not in invocations or name in invocation_closed:
                raise CaptureUnavailable("duplicate or orphan worker completion")
            if (p["clock_domain"] != invocations[name]["clock_domain"]
                    or int(p["ended_monotonic_ns"]) < int(invocations[name]["started_monotonic_ns"])):
                raise CaptureUnavailable("worker completion changed clock domain")
            invocation_closed.add(name)
    if not records or records[0]["kind"] != "RUN_OPEN":
        raise CaptureUnavailable("missing capture run header")


class CaptureStore:
    """The only writer of this run's capture inventory and retained raw sources."""

    def __init__(self, root: Path, *, run_id: str, manifest_ref: HashBoundRef):
        self.root = root.absolute()
        if self.root.exists() and not (self.root / "calls.v1").is_file():
            raise CaptureUnavailable("existing capture store lost its inventory; it may not be initialized again")
        self.run_id = identifier(run_id)
        self.fence = FileSnapshotFence(self.root / "coordinator")
        self.clock_domain = "capture-process-" + secrets.token_hex(16)
        ensure_directory(self.root)
        ensure_directory(self.root / "sources")
        self.coordinator_id = self.fence.coordinator_id()
        initialize_journal(self.root / "calls.v1")
        self.recover()
        with self.fence.exclusive() as guard:
            records = self._records()
            expected = {"run_id": self.run_id, "manifest_ref": manifest_ref.to_dict(),
                        "coordinator_id": self.coordinator_id, "retention_policy": "retain-with-run/v1"}
            if records:
                if records[0]["payload"] != expected:
                    raise CaptureUnavailable("capture store belongs to another frozen run")
            else:
                self._append("RUN_OPEN", expected, guard=guard)

    def _records(self):
        return _decode_frames(tuple(iter_journal_frames(self.root / "calls.v1")))

    def recover(self) -> None:
        """Settle storage only. Never replay an HTTP request or worker process."""
        with self.fence.exclusive() as guard:
            epoch = self.fence.current_epoch()
            scanned = scan_journal(self.root / "calls.v1")
            if scanned.torn_tail:
                if epoch % 2 == 0:
                    raise CaptureUnavailable("capture inventory was damaged outside a write interval")
                truncate_journal_to_valid_prefix(self.root / "calls.v1", scanned.valid_prefix_length)
            records = self._records()
            if records:
                _validate_history(records)
            if epoch % 2:
                self.fence.recover_abandoned_interval(guard=guard)

    def _put_source(self, raw: bytes, schema: str, ticket) -> HashBoundRef:
        if type(raw) is not bytes or len(raw) > _MAX_SOURCE:
            raise CaptureUnavailable("raw capture exceeds its retained source contract")
        digest = hashlib.sha256(raw).hexdigest()
        ref = HashBoundRef(RefKind.SOURCE_EVIDENCE, digest, schema, digest, len(raw), "application/json")
        destination = self.root / "sources" / digest
        if destination.exists() or destination.is_symlink():
            read_source(self.root, ref)
        else:
            staged = write_staged_bytes(destination.parent, final_name=digest,
                operation_id=new_operation_id(), value=raw, maximum_bytes=_MAX_SOURCE, ticket=ticket)
            publish_immutable(staged, destination, ticket=ticket)
        return ref

    def _append(self, kind, payload, *, guard, sources=()):
        records = self._records()
        with store_transaction(self.fence, guard=guard) as ticket:
            payload = dict(payload)
            for name, raw, schema in sources:
                payload[name] = self._put_source(raw, schema, ticket).to_dict()
            value = {"schema_version": CAPTURE_SCHEMA, "sequence": len(records) + 1, "kind": kind,
                     "previous_record_hash": records[-1]["record_hash"] if records else None, "payload": payload}
            value["record_hash"] = identity("synapse.capture.frame/v1", value)
            _validate_history((*records, value))
            if kind in {"CALL_STARTED", "CALL_RESPONSE", "CALL_FAILED"}:
                all_records = (*records, value)
                start = value if kind == "CALL_STARTED" else next(r for r in records
                    if r["kind"] == "CALL_STARTED" and r["payload"]["call_id"] == payload["call_id"])
                invocation = next(r["payload"] for r in all_records if r["kind"] == "INVOCATION_OPEN"
                    and r["payload"]["invocation_id"] == start["payload"]["invocation_id"])
                response = None if "response_ref" not in payload else read_source(self.root, HashBoundRef.from_dict(payload["response_ref"]))
                observation = call_record_from_capture(run_id=self.run_id, invocation=invocation,
                    started=start["payload"], terminal=None if kind == "CALL_STARTED" else payload,
                    response=response, capture_ref=reference(value, CAPTURE_SCHEMA))
                # Raw source, canonical observation and its inventory frame
                # share one mutation interval. Dispatch/delivery follows close.
                self._put_source(canonical(observation.to_dict()), TELEMETRY_SCHEMA, ticket)
            raw = canonical(value)
            if (self.root / "calls.v1").stat().st_size + len(encode_journal_frame(raw)) > _MAX_LEDGER:
                raise CaptureUnavailable("retained capture inventory capacity is exhausted")
            append_journal_payload(self.root / "calls.v1", raw, ticket=ticket)
        return value

    def open_invocation(self, *, invocation_id: str, attempt_id: str, invocation_payload: dict,
                        provider: str, model: str, profile: UsageProfile, worker_profile: str):
        for value in (invocation_id, attempt_id, provider, model, worker_profile):
            identifier(value)
        if type(profile) is not UsageProfile or type(invocation_payload) is not dict:
            raise CaptureUnavailable("invocation accounting profile must be frozen and exact")
        with self.fence.exclusive() as guard:
            if any(r["kind"] == "INVOCATION_OPEN" and r["payload"]["invocation_id"] == invocation_id for r in self._records()):
                raise CaptureUnavailable("an existing invocation may not repeat external work")
            self._append("INVOCATION_OPEN", {"invocation_id": invocation_id, "attempt_id": attempt_id,
                "provider": provider, "model": model,
                "usage_profile": profile.value, "worker_profile": worker_profile,
                "clock_domain": self.clock_domain, "started_unix_ns": str(time.time_ns()),
                "started_monotonic_ns": str(time.monotonic_ns())}, guard=guard,
                sources=(("invocation_ref", canonical(invocation_payload), "synapse.worker.invocation-binding/v1"),))
        return InvocationCapture(self, invocation_id)

    def cut(self) -> CaptureCut:
        with self.fence.exclusive():
            if self.fence.current_epoch() % 2:
                raise CaptureUnavailable("capture mutation has not settled")
            records = self._records()
            _validate_history(records)
            raw = read_regular_bytes(self.root / "calls.v1", maximum_bytes=_MAX_LEDGER)
            digest = hashlib.sha256(raw).hexdigest()
            ref = HashBoundRef(RefKind.SOURCE_EVIDENCE, digest, "synapse.capture-journal/v1",
                               digest, len(raw), "application/octet-stream")
            return CaptureCut(self.root, self.run_id, self.coordinator_id, ref,
                              len(records), records[-1]["record_hash"])


class InvocationCapture:
    """Translate one frozen worker scope into the neutral physical HTTP port."""

    def __init__(self, store: CaptureStore, invocation_id: str):
        self._store, self.invocation_id = store, invocation_id

    def register_logical_call(self, *, request_identity: str) -> str:
        identifier(request_identity)
        call_id = "logical-" + secrets.token_hex(16)
        with self._store.fence.exclusive() as guard:
            self._store._append("LOGICAL_OPEN", {"invocation_id": self.invocation_id,
                "logical_call_id": call_id, "request_identity": request_identity}, guard=guard)
        return call_id

    def before_request(self, *, logical_call_id: str, request: bytes) -> str:
        call_id = "call-" + secrets.token_hex(16)
        with self._store.fence.exclusive() as guard:
            self._require_logical(logical_call_id)
            self._store._append("CALL_STARTED", {"call_id": call_id, "logical_call_id": logical_call_id,
                "invocation_id": self.invocation_id, "clock_domain": self._store.clock_domain,
                "started_unix_ns": str(time.time_ns()), "started_monotonic_ns": str(time.monotonic_ns())},
                guard=guard, sources=(("request_ref", request, "synapse.raw.provider-request/v1"),))
        return call_id

    def _require_logical(self, call_id):
        records = self._store._records()
        if not any(r["kind"] == "LOGICAL_OPEN" and r["payload"]["logical_call_id"] == call_id
                   and r["payload"]["invocation_id"] == self.invocation_id for r in records):
            raise CaptureUnavailable("logical request is not registered for this worker")

    def after_response(self, *, call_id: str, status_code: int, response: bytes,
                       provider_request_id: str | None) -> None:
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise CaptureUnavailable("invalid provider HTTP status")
        if provider_request_id is not None and (type(provider_request_id) is not str or len(provider_request_id) > 256):
            raise CaptureUnavailable("invalid provider request identity")
        self._end_call("CALL_RESPONSE", call_id, {"status_code": status_code,
            "provider_request_id": provider_request_id},
            sources=(("response_ref", response, "synapse.raw.provider-response/v1"),))

    def request_failed(self, *, call_id: str, error_code: str) -> None:
        self._end_call("CALL_FAILED", call_id, {"error_code": identifier(error_code)})

    def _end_call(self, kind, call_id, extra, *, sources=()):
        with self._store.fence.exclusive() as guard:
            if not any(r["kind"] == "CALL_STARTED" and r["payload"]["call_id"] == call_id
                       and r["payload"]["invocation_id"] == self.invocation_id for r in self._store._records()):
                raise CaptureUnavailable("response does not belong to this worker")
            self._store._append(kind, {"call_id": call_id, "clock_domain": self._store.clock_domain,
                "ended_monotonic_ns": str(time.monotonic_ns()), **extra}, guard=guard, sources=sources)

    def finish_logical_call(self, *, logical_call_id: str, status: str) -> None:
        if status not in ("COMPLETED", "FAILED"):
            raise CaptureUnavailable("invalid logical query completion")
        with self._store.fence.exclusive() as guard:
            self._require_logical(logical_call_id)
            self._store._append("LOGICAL_CLOSED", {"logical_call_id": logical_call_id, "status": status}, guard=guard)

    def retain_trajectory(self, *, raw: bytes | None, process_status: str) -> None:
        if process_status not in ("EXITED", "TIMEOUT", "INTERRUPTED", "NOT_STARTED"):
            raise CaptureUnavailable("invalid worker process completion")
        sources = () if raw is None else (("trajectory_ref", raw, "synapse.raw.mini-trajectory/v1"),)
        with self._store.fence.exclusive() as guard:
            self._store._append("INVOCATION_CLOSED", {"invocation_id": self.invocation_id,
                "process_status": process_status, "clock_domain": self._store.clock_domain,
                "ended_monotonic_ns": str(time.monotonic_ns())}, guard=guard, sources=sources)
