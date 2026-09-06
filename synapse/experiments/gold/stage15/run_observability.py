"""Canonical run's recoverable observation suffix, after domain completion.

Only observation namespaces are written here. Domain records, physical call
receipts and producer outcomes remain immutable. The retained manifest binds
an exact capture cut, three independent reports, phase inventory and a DAG;
recovery may publish missing projection members without repeating any effect.
"""

from pathlib import Path
from decimal import Decimal
import json

from ..canonicalization import HashBoundRef
from ..contracts import LineageEdgeKind as Edge
from ..runner.records import RecordKind
from ..runner.run_recovery import PendingRunRecord
from ..runner.state_machine import load_run_state
from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from ..replay_store import FileReplayStore
from ..stage10.context_codec import decode_canonical
from ..stage14.graph import GraphBuilder, LineageGraph, LineageNodeClass as Node, LINEAGE_SCHEMA_V1
from ..stage14.sources import _reopen_location
from .artifact_reconciliation import reconcile_artifacts, reopen_attempt_verification_sources
from .capture_store import CaptureCut, inspect_capture
from .events import GoldEvent, EventType, EVENT_SCHEMA, EventStream
from .reconciliation import reconcile_run_telemetry, reconstruct_call_records
from .snapshot_reconciliation import reconcile_snapshot
from .telemetry import (CoreTelemetryEnvelope, Phase, Component, ReplayTelemetryRecord,
    VerificationTelemetryRecord, InfrastructureCostRecord, TELEMETRY_SCHEMA, identity, reference)

OBSERVABILITY_SCHEMA = "synapse.stage4.gold.observability-manifest/v1"


def _envelope(run_id, attempt_id, phase, component, ref):
    return CoreTelemetryEnvelope(run_id, attempt_id, phase, component, ref.sha256,
        identity("synapse.telemetry.trace/v1", run_id)[:32], identity("synapse.telemetry.span/v1", ref.to_dict())[:16],
        None, "retained-source-unmeasured", None, None, None, (ref,))


def _domain_observations(store, state):
    result = []
    for attempt in state.attempts:
        context = attempt.context
        catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key=str(attempt.attempt_index)).payload
        path, fence = _reopen_location(catalog["replay"])
        replay_ref = HashBoundRef.from_dict(catalog["replay_ref"])
        replay = FileReplayStore(path.parent, mutation_fence=fence, read_only=True).require_result(replay_ref)
        result.append(ReplayTelemetryRecord(
            _envelope(state.manifest.run_id.value, context.attempt_id.value, Phase.REPLAY, Component.COGNITIVE_VM, replay_ref),
            replay_ref, replay.request_ref, replay.knowledge_snapshot_id, replay.gas_consumed,
            len(replay.transition_hash_chain), len(replay.consumed_activity_identities), None, replay.status.value).to_dict())
        if attempt.result is None:
            continue
        verification = attempt.result.structured_outcome["payload"]["verification"]
        facts = verification["payload"]
        ref = HashBoundRef.from_dict(verification["verification_ref"])
        c1 = facts["c1"]
        evidence = reopen_attempt_verification_sources(run_root=store.record_root.parent, manifest=state.manifest, attempt=attempt)
        commands, duration, artifacts = [], None, ()
        if evidence is not None:
            retained = evidence.retained_artifacts()
            artifacts = tuple(ref for ref, raw in retained)
            for source_ref, raw in retained:
                if source_ref.to_dict() == c1["report_ref"]:
                    report = json.loads(raw)
                    for phase in report["phases"]:
                        if phase.get("command") is not None:
                            commands.append({"phase": phase["name"], "argv": phase["command"],
                                "status": phase["status"], "duration_ms": phase["duration_ms"]})
                elif source_ref.to_dict() == c1["oracle_result_ref"]:
                    value = json.loads(raw)["oracle_duration_seconds"]
                    if value is not None:
                        amount = Decimal(str(value))
                        if not amount.is_finite() or amount < 0:
                            raise ValueError("oracle source duration is invalid")
                        duration = str(amount)
        result.append(VerificationTelemetryRecord(
            _envelope(state.manifest.run_id.value, context.attempt_id.value, Phase.CONTROLLED_CHANGE, Component.VERIFICATION, ref),
            ref, None if c1 is None else HashBoundRef.from_dict(c1["c1_result_ref"]),
            None if c1 is None or c1["oracle_result_ref"] is None else HashBoundRef.from_dict(c1["oracle_result_ref"]),
            attempt.result.structured_outcome["payload"]["status"], tuple(commands), duration, artifacts).to_dict())
        # MechanismUseRecord already belongs to Stage 13. Retain its physical
        # reference through the original progress record; do not create another
        # owner or turn a declined C1 dispatch into saved provider tokens.
    return result


def _events(state, run_graph, telemetry_ref):
    roles = dict(run_graph.roles)
    nodes = {n.node_id: n for n in run_graph.nodes}
    events = []

    def emit(attempt, phase, kind, subject, payload=None):
        events.append(GoldEvent(state.manifest.run_id.value, attempt, len(events) + 1, phase, kind,
            subject, subject if payload is None else payload, None if not events else events[-1].reference.sha256))

    def role(name):
        return None if name not in roles else nodes[roles[name]].reference

    emit("run", Phase.RUN, EventType.STARTED, role("run"))
    by_index = {a.attempt_index: a for a in state.attempts}
    last_index = len(state.attempts) + (1 if state.preparation_failure is not None else 0)
    for index in range(1, last_index + 1):
        attempt = by_index.get(index)
        prefix = ("attempt." if attempt is not None else "prepared.") + str(index) + "."
        fallback = role(prefix + "outcome") or role("run_result")
        facts = None if attempt is None or attempt.result is None else attempt.result.structured_outcome["payload"]["verification"]["payload"]
        aid = str(index) if attempt is None else attempt.context.attempt_id.value
        for phase, name in ((Phase.SNAPSHOT, "input.snapshot"), (Phase.RETRIEVAL, "input.retrieval"),
                            (Phase.REPLAY, "input.replay_result"), (Phase.INTENT, "intent"),
                            (Phase.PLAN, "plan"), (Phase.WORKER, "worker_result"),
                            (Phase.CONTROLLED_CHANGE, "c1"), (Phase.ORACLE, "oracle"),
                            (Phase.OUTCOME, "outcome"), (Phase.PUBLICATION, "publication")):
            subject = role(prefix + name)
            if subject is not None:
                kind = EventType.COMPLETED
            elif phase is Phase.PUBLICATION and facts is not None and not facts["reusable_candidates"] and facts["publication"] is None:
                kind, subject = EventType.NOT_REACHED, fallback
            elif phase is Phase.ORACLE and facts is not None and (facts["c1"] is None or facts["c1"]["oracle_result_ref"] is None):
                kind, subject = EventType.NOT_REACHED, fallback
            elif phase is Phase.CONTROLLED_CHANGE and facts is not None and facts["mechanism_use"] is not None:
                kind, subject = EventType.REFUSED, role(prefix + "mechanism_use") or fallback
            elif attempt is None and state.preparation_failure is not None:
                kind, subject = EventType.NOT_REACHED, fallback
            elif facts is not None and (facts["refused"] or facts["interrupted"]):
                kind, subject = EventType.NOT_REACHED, fallback
            else:
                kind, subject = EventType.GAP, fallback
            emit(aid, phase, kind, subject)
        emit(aid, Phase.TELEMETRY, EventType.COMPLETED, telemetry_ref)
    emit("run", Phase.RUN, EventType.COMPLETED, role("run_result"))
    emit("run", Phase.TELEMETRY, EventType.COMPLETED, telemetry_ref)
    return tuple(events)


def _evaluate_observation_cut(*, store, publication_root: Path | None, capture_cut: CaptureCut | None):
    """One physical evaluator shared by publication and read-only revalidation."""
    run_root = store.record_root.parent
    state = load_run_state(store)
    if state.final_result is None:
        raise ValueError("a terminal observation cut requires the durable domain result")
    run_id = state.manifest.run_id.value
    manifest_ref = reference(state.manifest.stored_dict(), state.manifest.payload()["schema_version"])
    result_ref = reference(state.final_result.stored_dict(), state.final_result.payload()["schema_version"])
    artifact = reconcile_artifacts(run_root=run_root, manifest_ref=manifest_ref, publication_root=publication_root)
    telemetry = reconcile_run_telemetry(run_root=run_root, cut=capture_cut)
    snapshots = []
    for key in sorted(store.iter_keys(kind=RecordKind.LINEAGE_SOURCES), key=int):
        catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key=key).payload
        snapshots.append(reconcile_snapshot(run_root=run_root, attempt_index=int(key), catalog_ref=reference(catalog, catalog["schema_version"])))
    reports = [telemetry, artifact, *snapshots]
    observations = [report.to_dict() for report in reports]
    observation_gaps = []
    try:
        observations.extend(_domain_observations(store, state))
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
        observation_gaps.append({"code": "domain_measurement_source_unavailable", "subject": type(exc).__name__})
    calls, retained_bytes = (), None
    if capture_cut is not None:
        try:
            frames = inspect_capture(capture_cut)
            calls = reconstruct_call_records(capture_cut, frames)
            refs = {HashBoundRef.from_dict(r["payload"][name]) for r in frames
                for name in ("invocation_ref", "request_ref", "response_ref", "trajectory_ref") if name in r["payload"]}
            retained_bytes = sum(ref.byte_length for ref in refs)
        except (ValueError, TypeError, OSError, RuntimeError):
            observation_gaps.append({"code": "call_measurement_source_unavailable"})
    for bucket in ("C_WRITE", "C_READ", "C_USE", "LIFECYCLE"):
        observations.append(InfrastructureCostRecord(
            _envelope(run_id, "run", Phase.TELEMETRY, Component.RECONCILIATION, result_ref), bucket,
            (result_ref,), retained_bytes if bucket == "C_WRITE" else None).to_dict())
    stored_graph = store.get(kind=RecordKind.RUN_LINEAGE, key="final")
    if stored_graph is None:
        raise ValueError("event projection requires the retained domain graph")
    run_graph = LineageGraph.from_dict(stored_graph.payload)
    events = _events(state, run_graph, telemetry.reference)
    missing_phases = [{"attempt_id": event.attempt_id, "phase": event.phase.value} for event in events if event.event_type is EventType.GAP]
    body = {"schema_version": OBSERVABILITY_SCHEMA, "run_id": run_id, "run_manifest_ref": manifest_ref.to_dict(),
        "run_result_ref": result_ref.to_dict(), "capture_cut": None if capture_cut is None else capture_cut.to_dict(),
        "telemetry_report_ref": telemetry.reference.to_dict(), "artifact_report_ref": artifact.reference.to_dict(),
        "snapshot_report_refs": [r.reference.to_dict() for r in snapshots],
        "telemetry_status": telemetry.status, "artifact_status": artifact.status,
        "snapshot_statuses": [r.status for r in snapshots],
        "observation_refs": [reference(value, value["schema_version"]).to_dict() for value in observations],
        "call_record_refs": [reference(call.to_dict(), TELEMETRY_SCHEMA).to_dict() for call in calls],
        "event_inventory": [event.to_dict() for event in events],
        "completeness_manifest": {"schema_version": "synapse.stage4.gold.completeness-manifest/v1",
            "expected_phases": [phase.value for phase in Phase], "missing_phases": missing_phases,
            "measurement_gaps": observation_gaps, "expected_worker_invocations": telemetry.to_dict()["sources"]["expected_worker_invocations"],
            "observed_worker_invocations": telemetry.to_dict()["sources"]["observed_worker_invocations"],
            "provider_call_accounting_complete": telemetry.status == "COMPLETE", "money_status": "UNAVAILABLE",
            "infrastructure_cost_status": "PARTIAL" if retained_bytes is not None else "UNAVAILABLE"},
        "decision_authority": "synapse.stage4.observation-cut/v1",
        "consumer_revalidation": "reopen-reports-capture-events-and-observability-dag/v1",
        "retention_policy": "run-records-and-capture-sources-retained-with-run;library-lineage-roots/v1",
        "economic_claims": "STAGE16_NOT_EVALUATED"}
    assessment_ref = reference(body, OBSERVABILITY_SCHEMA)
    graph = build_observation_graph(run_graph=run_graph, observations=observations, calls=calls,
                                    events=events, assessment_ref=assessment_ref)
    return body, observations, calls, events, graph


def observe_completed_run(*, session, publication_root: Path | None, capture_cut: CaptureCut | None):
    body, observations, calls, events, graph = _evaluate_observation_cut(
        store=session.store, publication_root=publication_root, capture_cut=capture_cut)
    assessment_ref = reference(body, OBSERVABILITY_SCHEMA)
    pending = [PendingRunRecord(RecordKind.OBSERVATION, reference(v, v["schema_version"]).sha256, v) for v in observations]
    # The manifest is an expected inventory, not a commit assertion. Readers
    # verify its graph and every event. A crash exposes gaps until this same
    # deterministic suffix is republished by the run owner.
    pending.append(PendingRunRecord(RecordKind.OBSERVABILITY_MANIFEST, assessment_ref.sha256, body))
    pending.extend(PendingRunRecord(RecordKind.GOLD_EVENT, event.event_id, event.to_dict()) for event in events)
    pending.append(PendingRunRecord(RecordKind.OBSERVABILITY_LINEAGE, assessment_ref.sha256, graph.to_dict()))
    session.put_many(tuple(pending))
    return {"assessment_ref": assessment_ref.to_dict(), "assessment_key": assessment_ref.sha256,
        "telemetry_status": body["telemetry_status"], "artifact_status": body["artifact_status"],
        "snapshot_statuses": body["snapshot_statuses"],
        "missing_phases": body["completeness_manifest"]["missing_phases"],
        "measurement_gaps": body["completeness_manifest"]["measurement_gaps"],
        "economic_claims": "STAGE16_NOT_EVALUATED"}


def build_observation_graph(*, run_graph, observations, calls, events, assessment_ref):
    run_id = run_graph.run_id
    graph = GraphBuilder("observability/v1", run_id, "run")
    graph.merge("", run_graph)
    for index, value in enumerate(observations):
        name = "observation." + str(index)
        graph.add(name, Node.TELEMETRY_RECORD, reference(value, value["schema_version"]))
        graph.link("run_result", Edge.MEASURED_BY, name)
    for call in calls:
        name = "call." + call.llm_call_id
        graph.add(name, Node.TELEMETRY_RECORD, reference(call.to_dict(), TELEMETRY_SCHEMA), attempt_id=call.envelope.attempt_id)
        role = next((r for r, node in graph.roles.items() if r.endswith(".worker_result")
                     and graph.nodes[node].attempt_id == call.envelope.attempt_id), None)
        graph.link("run_result" if role is None else role, Edge.MEASURED_BY, name)
    for event in events:
        name = "event." + str(event.sequence)
        graph.add(name, Node.GOLD_EVENT, event.reference, attempt_id=event.attempt_id)
        subject = next((r for r, node in graph.roles.items() if graph.nodes[node].reference == event.subject_ref and r != name), None)
        if subject is None:
            raise ValueError("event subject is outside the physical observation graph")
        graph.link(subject, Edge.OBSERVED_AS, name)
    graph.add("assessment", Node.TELEMETRY_RECORD, assessment_ref)
    graph.link("run_result", Edge.MEASURED_BY, "assessment")
    for role in tuple(graph.roles):
        if role.startswith(("observation.", "call.", "event.")):
            graph.link(role, Edge.DERIVED_FROM, "assessment")
    graph.link_roles()
    graph = graph.finish()
    return graph


def inspect_observability(*, run_root: Path, assessment_key: str):
    """Current physical revalidation, with no writer or repair capability.

    Returns new findings alongside the unchanged historical assessment. A lost
    source blocks current completeness; it never rewrites the producer outcome.
    """
    from ..admission_journal import FileSnapshotFence
    from ..runner.records import RunRecordStore
    from .capture_store import read_source
    from .events import open_event_stream
    from .telemetry import LLMCallRecord
    store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / "run-coordinator", read_only=True), read_only=True)
    stored = store.get(kind=RecordKind.OBSERVABILITY_MANIFEST, key=assessment_key)
    if stored is None or reference(stored.payload, OBSERVABILITY_SCHEMA).sha256 != assessment_key:
        raise ValueError("the exact retained assessment is unavailable")
    body = stored.payload
    cut = None if body["capture_cut"] is None else CaptureCut.from_dict(body["capture_cut"])
    observations, gaps = [], []
    for item in body["observation_refs"]:
        ref = HashBoundRef.from_dict(item)
        record = store.get(kind=RecordKind.OBSERVATION, key=ref.sha256)
        if record is None:
            gaps.append({"code": "missing_observation", "ref": item})
        elif reference(record.payload, record.payload["schema_version"]) != ref:
            gaps.append({"code": "observation_identity_mismatch", "ref": item})
        else:
            observations.append(record.payload)
    calls = []
    for item in body["call_record_refs"]:
        try:
            if cut is None:
                raise ValueError("call has no capture source")
            call = LLMCallRecord.from_dict(decode_canonical(read_source(cut.root, HashBoundRef.from_dict(item))))
            if reference(call.to_dict(), TELEMETRY_SCHEMA).to_dict() != item:
                raise ValueError("call observation identity changed")
            calls.append(call)
        except (ValueError, TypeError, RuntimeError, OSError):
            gaps.append({"code": "call_observation_missing_or_changed", "ref": item})
    telemetry = reconcile_run_telemetry(run_root=run_root, cut=cut)
    saved_artifact = next((v for v in observations if v["schema_version"] == "synapse.stage4.gold.artifact-reconciliation/v1"), None)
    publication_root = None if saved_artifact is None else saved_artifact["sources"]["publication_root"]
    artifact = reconcile_artifacts(run_root=run_root, manifest_ref=HashBoundRef.from_dict(body["run_manifest_ref"]),
        publication_root=None if publication_root is None else Path(publication_root))
    snapshots = []
    for key in sorted(store.iter_keys(kind=RecordKind.LINEAGE_SOURCES), key=int):
        catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key=key).payload
        snapshots.append(reconcile_snapshot(run_root=run_root, attempt_index=int(key), catalog_ref=reference(catalog, catalog["schema_version"])))
    current_reports = [telemetry, artifact, *snapshots]
    for report in current_reports:
        if report.reference.to_dict() not in body["observation_refs"]:
            gaps.append({"code": "physical_report_changed", "schema": report.to_dict()["schema_version"], "status": report.status})
    try:
        physical_body, _, _, _, _ = _evaluate_observation_cut(store=store, capture_cut=cut,
            publication_root=None if publication_root is None else Path(publication_root))
        if physical_body != body:
            gaps.append({"code": "assessment_inventory_differs_from_physical_sources"})
    except (ValueError, TypeError, RuntimeError, OSError, KeyError):
        gaps.append({"code": "assessment_inventory_cannot_be_reconstructed"})
    stream = open_event_stream(run_root=run_root, assessment_key=assessment_key)
    projection = stream.projection()
    if projection["missing_sequences"] or projection["missing_phases"]:
        gaps.append({"code": "event_phase_gap", "sequences": projection["missing_sequences"], "phases": projection["missing_phases"]})
    graph_record = store.get(kind=RecordKind.OBSERVABILITY_LINEAGE, key=assessment_key)
    try:
        original = store.get(kind=RecordKind.RUN_LINEAGE, key="final")
        expected = build_observation_graph(run_graph=LineageGraph.from_dict(original.payload), observations=observations,
            calls=calls, events=stream.expected, assessment_ref=reference(body, OBSERVABILITY_SCHEMA))
        if graph_record is None or LineageGraph.from_dict(graph_record.payload).to_dict() != expected.to_dict():
            gaps.append({"code": "observability_lineage_missing_or_changed"})
    except (ValueError, TypeError, RuntimeError, OSError):
        gaps.append({"code": "observability_lineage_cannot_be_reconstructed"})
    return {"assessment_ref": reference(body, OBSERVABILITY_SCHEMA).to_dict(),
        "telemetry_report": telemetry.to_dict(), "artifact_report": artifact.to_dict(),
        "snapshot_reports": [report.to_dict() for report in snapshots],
        "event_projection": projection, "discrepancies": gaps,
        "economic_claims": "STAGE16_NOT_EVALUATED"}
