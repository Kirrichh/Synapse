"""Durable resource evidence and reduction over the existing capture owner.

This adapter connects neutral operation observations to Gold source references.
CaptureStore remains the only journal/CAS writer. Readers reopen that physical
inventory, bind replay measurements to actual results, and preserve unfinished
operations instead of generating replacement measurements during recovery.
"""

from synapse.resource_usage import RESOURCE_PROFILE

from ..canonicalization import HashBoundRef
from .capture_store import CAPTURE_SCHEMA, CaptureCut, CaptureStore, inspect_capture
from .telemetry import (
    Component, CoreTelemetryEnvelope, InfrastructureCostRecord, Phase,
    SourceReconciliationReport, identity, reference,
)

RESOURCE_REPORT_SCHEMA = "synapse.stage4.gold.resource-reconciliation/v1"
_OPERATION_PHASES = {
    "runtime.execution": (Phase.RUN, Component.LIFECYCLE),
    "runtime.recovery": (Phase.RUN, Component.LIFECYCLE),
    "knowledge.prepare": (Phase.RUN, Component.ADMISSION),
    "knowledge.setup": (Phase.RUN, Component.ADMISSION),
    "knowledge.snapshot": (Phase.SNAPSHOT, Component.ADMISSION),
    "knowledge.retrieve": (Phase.RETRIEVAL, Component.RETRIEVAL),
    "knowledge.read": (Phase.RETRIEVAL, Component.RETRIEVAL),
    "replay.reference": (Phase.REPLAY, Component.COGNITIVE_VM),
    "replay.execute": (Phase.REPLAY, Component.COGNITIVE_VM),
    "replay.resume": (Phase.REPLAY, Component.COGNITIVE_VM),
    "plan.accept": (Phase.PLAN, Component.ADMISSION),
    "worker.delivery": (Phase.WORKER, Component.WORKER_SUBPROCESS),
    "provider.transport": (Phase.WORKER, Component.WORKER_SUBPROCESS),
    "verification.execute": (Phase.CONTROLLED_CHANGE, Component.VERIFICATION),
    "publication.commit": (Phase.PUBLICATION, Component.PUBLICATION),
    "publication.recover": (Phase.RUN, Component.LIFECYCLE),
    "lifecycle.append": (Phase.RUN, Component.LIFECYCLE),
    "observation.evaluate": (Phase.TELEMETRY, Component.RECONCILIATION),
    "observation.publish": (Phase.TELEMETRY, Component.RECONCILIATION),
}


class ResourceRecorder:
    def __init__(self, store: CaptureStore):
        if type(store) is not CaptureStore:
            raise TypeError("resource recording requires the capture owner")
        self.store = store
        self.failures = []

    def started(self, payload):
        self._record("RESOURCE_STARTED", payload)

    def finished(self, payload):
        self._record("RESOURCE_FINISHED", payload)

    def _record(self, kind, payload):
        if self.failures:
            return
        try:
            self.store.record_resource(kind, payload)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            # No observation failure authorizes replay of completed work. The
            # partial physical inventory and the terminal seal expose the gap.
            self.failures.append({"operation_id": payload["operation_id"], "kind": kind,
                                  "error": type(exc).__name__})

    def seal(self, outcome_ref):
        try:
            # A rejected/torn receipt may leave this owner's mutation interval
            # open. Its normal storage recovery settles that interval without
            # fabricating a completion sample for the observed operation.
            self.store.recover()
            self.store.seal_resources(outcome_ref=outcome_ref, recording_failures=self.failures)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self.failures.append({"operation_id": "run", "kind": "RESOURCE_SEALED", "error": type(exc).__name__})


class ResourceEvidence:
    """One re-opened measurement cut, with no execution or storage capability."""

    def __init__(self, *, cut: CaptureCut | None, run_id: str, outcome_ref: HashBoundRef,
                 replay_refs=(), expected_operations=()):
        self.run_id, self.cut = run_id, cut
        self.expected_operations = tuple(expected_operations)
        self.starts, self.ends = {}, {}
        self.gaps, self.compared = [], []
        self.retained_source_bytes = None
        seal = None
        if cut is None:
            self.gaps.append({"code": "resource_sources_unavailable", "subject": "run"})
        else:
            try:
                if cut.run_id != run_id:
                    raise ValueError("resource cut belongs to another run")
                frames = inspect_capture(cut)
                retained = {HashBoundRef.from_dict(frame["payload"][key]) for frame in frames
                    for key in ("invocation_ref", "request_ref", "response_ref", "trajectory_ref") if key in frame["payload"]}
                self.retained_source_bytes = sum(ref.byte_length for ref in retained)
                for frame in frames:
                    kind, data = frame["kind"], frame["payload"]
                    if kind == "RESOURCE_STARTED":
                        self.starts[data["operation_id"]] = frame
                    elif kind == "RESOURCE_FINISHED":
                        self.ends[data["operation_id"]] = frame
                    elif kind == "RESOURCE_SEALED":
                        seal = data
                    if kind.startswith("RESOURCE_"):
                        self.compared.append(reference(frame, CAPTURE_SCHEMA).to_dict())
                expected_transport = sum(frame["kind"] in {"LOGICAL_OPEN", "CALL_STARTED", "LOGICAL_CLOSED"} for frame in frames)
                observed_transport = sum(frame["payload"]["name"] == "provider.transport" for frame in self.starts.values())
                if observed_transport < expected_transport:
                    self.gaps.append({"code": "provider_transport_measurement_missing", "expected": expected_transport,
                                      "observed": observed_transport})
                if seal is None:
                    self.gaps.append({"code": "resource_execution_seal_missing", "subject": "run"})
                if seal is not None and (seal["outcome_ref"] != outcome_ref.to_dict() or seal["profile"] != RESOURCE_PROFILE):
                    raise ValueError("resource seal differs from the durable domain result")
                if seal is not None:
                    self.gaps.extend({"code": "resource_recording_failed", **item} for item in seal["recording_failures"])
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                self.gaps.append({"code": "resource_physical_source_invalid", "subject": type(exc).__name__})
        for operation_id, start in self.starts.items():
            if operation_id not in self.ends:
                self.gaps.append({"code": "resource_operation_unfinished", "subject": operation_id,
                                  "operation": start["payload"]["name"]})
        if not any(s["payload"]["name"] == "runtime.execution" for s in self.starts.values()):
            self.gaps.append({"code": "runtime_resource_measurement_missing", "subject": "run"})
        if not any(start["payload"]["name"] == "runtime.execution" and key in self.ends
                   and outcome_ref.to_dict() in self.ends[key]["payload"]["result_refs"]
                   for key, start in self.starts.items()):
            self.gaps.append({"code": "runtime_result_measurement_missing", "subject": outcome_ref.sha256})
        for replay_ref in replay_refs:
            matches = self.replay_measurements(replay_ref)
            if not matches:
                self.gaps.append({"code": "replay_resource_measurement_missing", "subject": replay_ref.sha256})
        for requirement in self.expected_operations:
            names, minimum = requirement["operations"], requirement["minimum"]
            observed = sum(frame["payload"]["name"] in names for frame in self.starts.values())
            if observed < minimum:
                self.gaps.append({"code": "domain_resource_measurement_missing", "operations": list(names),
                                  "expected": minimum, "observed": observed})

    def replay_measurements(self, replay_ref):
        return tuple(key for key, frame in self.starts.items()
            if frame["payload"]["name"] in {"replay.execute", "replay.resume"}
            and key in self.ends and replay_ref.to_dict() in self.ends[key]["payload"]["result_refs"])

    def envelope(self, operation_id):
        start = self.starts[operation_id]
        end = self.ends.get(operation_id)
        s, e = start["payload"], None if end is None else end["payload"]
        phase, component = _OPERATION_PHASES[s["name"]]
        refs = (reference(start, CAPTURE_SCHEMA),) + (() if end is None else (reference(end, CAPTURE_SCHEMA),))
        return CoreTelemetryEnvelope(self.run_id, s["attempt_id"], phase, component, operation_id,
            identity("synapse.telemetry.trace/v1", self.run_id)[:32],
            identity("synapse.telemetry.span/v1", operation_id)[:16],
            None if s["parent_id"] is None else identity("synapse.telemetry.span/v1", s["parent_id"])[:16],
            s["clock_domain"], int(s["started_unix_ns"]), int(s["started_monotonic_ns"]),
            None if e is None else int(e["ended_monotonic_ns"]), refs)

    def infrastructure_records(self):
        records = []
        for operation_id, start in self.starts.items():
            s = start["payload"]
            e = None if operation_id not in self.ends else self.ends[operation_id]["payload"]
            envelope = self.envelope(operation_id)
            records.append(InfrastructureCostRecord(
                envelope=envelope, bucket=s["bucket"], source_refs=envelope.lineage_refs,
                operation_id=operation_id, operation=s["name"], parent_operation_id=s["parent_id"],
                result_class="UNKNOWN" if e is None else e["status"],
                cpu_ns=None if e is None else int(e["ended_cpu_ns"]) - int(s["started_cpu_ns"]) - int(e["children_cpu_ns"]),
                wall_ns=None if e is None else int(e["ended_monotonic_ns"]) - int(s["started_monotonic_ns"]) - int(e["children_wall_ns"]),
                io_read_bytes=None if e is None else int(e["io_read_bytes"]),
                io_write_bytes=None if e is None else int(e["io_write_bytes"])).to_dict())
        return records

    def report(self):
        records = self.infrastructure_records()
        totals = {}
        for bucket in ("C_WRITE", "C_READ", "C_USE", "LIFECYCLE"):
            selected = [r for r in records if r["bucket"] == bucket]
            totals[bucket] = {"operations": len(selected)}
            for axis in ("cpu_ns", "wall_ns", "io_read_bytes", "io_write_bytes", "measurement_storage_bytes"):
                values = [r[axis] for r in selected]
                totals[bucket][axis] = str(sum(int(v) for v in values)) if not self.gaps and all(v is not None for v in values) else None
            # Across threads, overlapping waits are one elapsed interval. CPU
            # and byte counters are exclusive observations and remain additive.
            totals[bucket]["wall_ns"] = None if self.gaps else str(self._wall_union(bucket))
        return SourceReconciliationReport.evaluated(schema=RESOURCE_REPORT_SCHEMA,
            source={"run_id": self.run_id, "capture_cut": None if self.cut is None else self.cut.to_dict(),
                "profile": RESOURCE_PROFILE, "cpu_scope": "measured-owner-threads;child-processes-excluded",
                "io_scope": "actual-bytes-returned-by-Gold-persistence-primitives;not-disk-traffic",
                "duration_scope": "exclusive-nesting;union-per-clock-domain-and-bucket;cross-bucket-waits-not-additive;root-receipt-seal-excluded",
                "expected_operations": list(self.expected_operations),
                "storage": {"inventory_prefix_bytes": None if self.cut is None else self.cut.ledger_ref.byte_length,
                    "distinct_raw_source_bytes": self.retained_source_bytes,
                    "scope": "physical-cut-reachable-capture-sources;not-total-run-or-disk-allocation"},
                "money_scope": "provider-prices-and-economic-estimates-not-resource-counters"},
            status="INCOMPLETE" if self.gaps else "COMPLETE", discrepancies=self.gaps,
            compared=self.compared, authority="synapse.stage4.physical-resource-evaluator/v1",
            precedence=["INCOMPLETE"], totals=totals)

    def _wall_union(self, bucket):
        domains = {}
        for key, frame in self.starts.items():
            s, e = frame["payload"], self.ends[key]["payload"]
            if s["bucket"] != bucket:
                continue
            intervals = domains.setdefault(s["clock_domain"], [])
            begin = int(s["started_monotonic_ns"])
            for child in e["children"]:
                child_start, child_end = self.starts[child]["payload"], self.ends[child]["payload"]
                intervals.append((begin, int(child_start["started_monotonic_ns"])))
                begin = int(child_end["ended_monotonic_ns"])
            intervals.append((begin, int(e["ended_monotonic_ns"])))
        total = 0
        for intervals in domains.values():
            covered_end = 0
            for start, end in sorted(intervals):
                total += max(0, end - max(start, covered_end))
                covered_end = max(covered_end, end)
        return total
