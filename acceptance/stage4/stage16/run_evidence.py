"""Read physical arm evidence through its existing domain evaluators.

This boundary translates Baseline records and a canonical Gold run into
external observations. It never grants admission, rewrites outcomes or
reconstructs provider calls from aggregate token totals.
"""

import json
from decimal import Decimal
from pathlib import Path

from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.experiments.gold.stage15.artifact_reconciliation import reconcile_artifacts
from synapse.experiments.gold.stage15.capture_store import CaptureCut, inspect_capture, read_source as read_capture_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, reconstruct_call_records
from synapse.experiments.gold.stage15.resource_accounting import ResourceEvidence
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.experiments.gold.stage15.telemetry import reference, normalize_usage, UsageProfile
from synapse.experiments.swebench.contract import (BaselineRunRecord, BaselineAttemptRecord,
    OracleResult, TokenAccountingRecord, ArtifactRef, ExperimentArm, AttemptVerdict,
    UsageSource, PrimaryMetricStatus, UsageConsistencyStatus)
from synapse.experiments.swebench.paired_measurement import (PairedMeasurementMember, MeasurementMode, CarryState,
    ALL_ATTEMPTS_RECORDED, baseline_member_from_run)
from synapse.experiments.swebench.telemetry import attempt_record_for_jsonl, oracle_record_for_jsonl
from synapse.worker.contract import (ExternalCodingWorkerResult, ExternalWorkerStatus,
    ExternalWorkerUsage, ExternalWorkerTokenStatus, WorkerReport)

from .execution import oracle_configuration, oracle_fingerprints
from .protocol import canonical, digest, read_source, source


def _baseline_record(value):
    """Decode existing public dataclasses; C2 remains the status evaluator."""
    data = dict(value)
    attempts = []
    for raw in data.pop("attempts"):
        raw = dict(raw)
        worker = dict(raw.pop("worker_result"))
        usage = dict(worker.pop("usage"))
        usage["token_status"] = ExternalWorkerTokenStatus(usage["token_status"])
        worker["worker_status"] = ExternalWorkerStatus(worker["worker_status"])
        worker["worker_report"] = WorkerReport(**worker["worker_report"])
        accounting = dict(raw.pop("token_accounting"))
        for name, kind in (("arm", ExperimentArm), ("usage_source", UsageSource),
                ("primary_metric_status", PrimaryMetricStatus), ("usage_consistency", UsageConsistencyStatus)):
            accounting[name] = kind(accounting[name])
        raw["arm"], raw["verdict"] = ExperimentArm(raw["arm"]), AttemptVerdict(raw["verdict"])
        raw["oracle_result"] = None if raw["oracle_result"] is None else OracleResult(**raw["oracle_result"])
        raw["artifacts"] = tuple(ArtifactRef(**item) for item in raw["artifacts"])
        attempts.append(BaselineAttemptRecord(**raw, token_accounting=TokenAccountingRecord(**accounting),
            worker_result=ExternalCodingWorkerResult(**worker, usage=ExternalWorkerUsage(**usage))))
    data["arm"] = ExperimentArm(data["arm"])
    return BaselineRunRecord(**data, attempts=tuple(attempts))


def _physical(ref):
    if source(ref["path"]) != ref:
        raise ValueError("an arm's physical source is missing or changed")


def _provider_profiles(frames):
    return sorted({tuple(frame["payload"][key] for key in ("provider", "model", "worker_profile", "usage_profile"))
        for frame in frames if frame["kind"] == "INVOCATION_OPEN"})


def inspect_baseline(slot, definition, receipt):
    data = read_source(receipt["result_ref"])
    run = _baseline_record(data)
    if (run.run_id != receipt["run_id"] or run.task_id != slot["task_id"] or run.replicate_id != slot["replicate_id"]
            or run.base_revision != definition["base_revision"] or run.max_attempts != definition["max_attempts"]
            or [a.attempt_id for a in run.attempts] != list(range(1, len(run.attempts) + 1))
            or not 1 <= len(run.attempts) <= run.max_attempts):
        raise ValueError("Baseline occurrence or complete attempt inventory differs")
    root = Path(definition["run_root"])
    run_dir = root / "runs" / run.run_id
    observed = {ref["path"] for ref in receipt["physical_sources"]}
    if observed != {str(path.absolute()) for path in run_dir.rglob("*") if path.is_file()}:
        raise ValueError("Baseline retained file inventory differs")
    for ref in receipt["physical_sources"]:
        _physical(ref)
    def rows(name):
        return [json.loads(line) for line in (run_dir / name).read_text().splitlines() if line.strip()]
    if canonical(rows("attempts.jsonl")) != canonical([attempt_record_for_jsonl(a) for a in run.attempts]):
        raise ValueError("Baseline result differs from the actual attempt writer")
    if canonical(rows("tokens.jsonl")) != canonical([a.token_accounting.to_dict() for a in run.attempts]):
        raise ValueError("Baseline token rows differ")
    if canonical(rows("oracle.jsonl")) != canonical([v for a in run.attempts if (v := oracle_record_for_jsonl(a)) is not None]):
        raise ValueError("Baseline oracle rows differ")
    fingerprints = oracle_fingerprints(oracle_configuration(definition["oracle"]))
    member = baseline_member_from_run(run, oracle_config_fingerprint=fingerprints["oracle_config"],
        oracle_environment_fingerprint=fingerprints["oracle_environment"],
        environment_fingerprint=digest(receipt["environment"]))
    if member.to_dict() != receipt["member"] or fingerprints != receipt["oracle_fingerprints"]:
        raise ValueError("Baseline C2 projection or oracle implementation changed")
    cut = CaptureCut.from_dict(receipt["capture_cut"])
    if cut.root != root / "capture" or cut.run_id != slot["slot_id"]:
        raise ValueError("Baseline capture belongs to another allocation")
    frames = inspect_capture(cut)
    expected = {f"{run.run_id}:attempt:{a.attempt_id}": str(a.attempt_id) for a in run.attempts}
    invocations = {f["payload"]["invocation_id"]: f["payload"]["attempt_id"] for f in frames if f["kind"] == "INVOCATION_OPEN"}
    if invocations != expected:
        raise ValueError("Baseline capture omits or substitutes an actual attempt")
    openings = {f["payload"]["invocation_id"]: f["payload"] for f in frames if f["kind"] == "INVOCATION_OPEN"}
    closures = {f["payload"]["invocation_id"]: f["payload"] for f in frames if f["kind"] == "INVOCATION_CLOSED"}
    for attempt in run.attempts:
        name = f"{run.run_id}:attempt:{attempt.attempt_id}"
        trajectory = json.loads(read_capture_source(cut.root, HashBoundRef.from_dict(closures[name]["trajectory_ref"])))
        usages = [normalize_usage(UsageProfile(openings[name]["usage_profile"]), m["extra"]["response"].get("usage"))
            for m in trajectory["messages"] if m.get("role") == "assistant" and "response" in m.get("extra", {})]
        total = sum(u.provider_total_tokens for u in usages) if usages and all(u.provider_total_tokens is not None for u in usages) else None
        if total != attempt.worker_result.usage.total_tokens:
            raise ValueError("Baseline aggregate differs from its actual retained worker trajectory")
    telemetry = reconcile_telemetry(cut).to_dict()
    resources = ResourceEvidence(cut=cut, run_id=cut.run_id,
        outcome_ref=reference(data, "synapse.acceptance.stage16.baseline-result/v1")).report().to_dict()
    config = definition["mini"]
    axes = {"task_id": run.task_id, "instance_id": run.instance_id, "base_revision": run.base_revision,
        "model": config["model"], "worker_runtime": receipt["runtime"],
        "environment": receipt["environment"], **fingerprints,
        "task": [definition["task"]["statement"], sorted(definition["task"]["allowed_scope"])],
        "provider_profiles": _provider_profiles(frames), "provider_endpoint": definition["provider_connection"]["endpoint"],
        "max_attempts": run.max_attempts, "worker_limits": [config["timeout_seconds"], config["step_limit"], str(Decimal(str(config["cost_limit"])).normalize())],
        "execution_policy": "stage3a-raw-carry-oracle/v1"}
    return {"run_id": run.run_id, "member": member, "axes": axes, "attempts": data["attempts"],
        "outcome": {"status": member.terminal_status, "task_resolved": member.resolved,
            "source_ref": receipt["result_ref"], "record": {"resolved": run.resolved,
                "final_attempt_verdict": run.attempts[-1].verdict.value}},
        "provider_calls": [call.to_dict() for call in reconstruct_call_records(cut, frames)],
        "telemetry": telemetry, "resources": resources, "discrepancies": [],
        "mechanisms": [], "result_identity": receipt["result_ref"]["sha256"],
        "capture_cut": cut.to_dict(), "legacy_token_total": run.total_provider_tokens}


def inspect_mechanisms(*, run_root):
    """Physical reuse proof, independently of whether accounting is complete."""
    root = Path(run_root)
    store = RunRecordStore(root, mutation_fence=FileSnapshotFence(root / "run-coordinator", read_only=True), read_only=True)
    state = load_run_state(store)
    frozen = reopen_frozen_inputs(root)
    artifact = reconcile_artifacts(run_root=root,
        manifest_ref=reference(state.manifest.stored_dict(), state.manifest.payload()["schema_version"]),
        publication_root=Path(frozen.data["project_state_root"]) / "publications")
    if artifact.status != "COMPLETE":
        return {"status": "INCOMPLETE", "proofs": [], "artifact_report": artifact.to_dict()}
    proofs = []
    for attempt in state.attempts:
        if attempt.result is None or not attempt.result.structured_outcome["payload"]["observed_reuse"]:
            continue
        progress = load_attempt_progress(store, manifest=state.manifest, context=attempt.context)
        record = progress.get(AttemptProgressPhase.REUSE_GUARD_COMPLETED)
        if record is None:
            raise ValueError("reported reuse lacks its physical mechanism record")
        raw, ref = require_progress_payload(record)
        use = json.loads(raw)
        graph = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key=str(attempt.attempt_index)).payload)
        nodes, roles = {n.node_id: n for n in graph.nodes}, dict(graph.roles)
        required = ("producer.publication", "input.retrieval", "input.snapshot", "input.replay_result",
            "worker_consumption_gate", "worker_context", "worker_result", "mechanism_use", "promotion", "verification", "outcome")
        if any(role not in roles for role in required):
            raise ValueError("reuse lost an intermediate physical stage")
        if nodes[roles["mechanism_use"]].reference != ref or use["run_id"] != state.manifest.run_id.value:
            raise ValueError("reuse proof names another physical occurrence")
        promotion = store.get(kind=RecordKind.REUSE_PROMOTION, key=str(attempt.attempt_index)).payload
        if promotion["mechanism_use_ref"] != ref.to_dict() or promotion["to_state"] != "OBSERVED_USEFUL_REUSE":
            raise ValueError("reuse promotion differs from retained mechanism evidence")
        proofs.append({"mechanism_ref": ref.to_dict(), "behavior_ref": use["behavior_ref"],
            "producer_outcome_ref": use["producer_outcome_ref"], "consumer_outcome_ref": attempt.result.structured_outcome["outcome_ref"],
            "effect": use["effect"], "avoided_dispatches": use["avoided_dispatches"],
            "stages": {role: nodes[roles[role]].reference.to_dict() for role in required}})
    return {"status": "ACTIVATED" if proofs else "MECHANISM_NOT_ACTIVATED", "proofs": proofs,
        "artifact_report": artifact.to_dict()}


def inspect_gold(slot, definition, receipt):
    root = Path(definition["run_root"])
    inputs = reopen_frozen_inputs(root)
    if inputs.data["declaration"] != read_source(definition["declaration_ref"]):
        raise ValueError("Gold used different inputs from its preregistration")
    store = RunRecordStore(root, mutation_fence=FileSnapshotFence(root / "run-coordinator", read_only=True), read_only=True)
    state = load_run_state(store)
    result = state.final_result
    if result is None or result.payload() != receipt["result"]["result"]:
        raise ValueError("Gold CLI receipt differs from its durable domain outcome")
    config = state.manifest.config
    if config.task_id != slot["task_id"] or inputs.data["repo_root"] != definition["repo_root"]:
        raise ValueError("Gold allocation has different task or repository")
    declaration = inputs.data["declaration"]
    fingerprints = oracle_fingerprints(oracle_configuration(declaration["oracle"]))
    member = PairedMeasurementMember(mode=MeasurementMode.GOLD_WITH_CARRY, run_id=result.run_id.value,
        task_id=config.task_id, instance_id=config.instance_id, base_revision=config.base_revision,
        replicate_id=slot["replicate_id"], resolved=result.structured_outcome["payload"]["status"] == "FULL",
        infra_error=result.structured_outcome["payload"]["status"] == "INFRA_ERROR",
        terminal_status=result.structured_outcome["payload"]["status"], attempt_count=len(state.attempts),
        oracle_config_fingerprint=fingerprints["oracle_config"], oracle_environment_fingerprint=fingerprints["oracle_environment"],
        environment_fingerprint=digest(receipt["environment"]), carry_state=CarryState.GOLD_WITH_CARRY,
        source_record_kind="synapse.stage4.durable-gold-run",
        diagnostics={"attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
            "attempts_observed_count": len(state.attempts), "selected_attempt_count": len(state.attempts)})
    worker = declaration["worker"]
    axes = {"task_id": config.task_id, "instance_id": config.instance_id, "base_revision": config.base_revision,
        "model": config.model, "worker_runtime": inputs.data.get("worker_runtime"),
        "environment": receipt["environment"], **fingerprints,
        "task": [declaration["task_contract"]["task_statement"], sorted(declaration["task_contract"]["allowed_scope"]["entries"])],
        "provider_profiles": None,
        "provider_endpoint": worker["accounting"]["endpoint"],
        "max_attempts": config.max_attempts, "worker_limits": [worker["timeout_seconds"], worker["max_steps"], str(Decimal(worker["cost_limit"]).normalize())],
        "execution_policy": "stage4-accepted-plan-controlled-change-oracle/v1"}
    # Domain result and measurements have different failure semantics (§35).
    # Losing a measurement source must not erase an independently retained
    # outcome, nor may that outcome make the missing measurement complete.
    view = {"run_id": result.run_id.value, "member": member, "axes": axes,
        "outcome": {"status": result.structured_outcome["payload"]["status"],
            "task_resolved": None if member.infra_error else member.resolved,
            "source_ref": result.structured_outcome["outcome_ref"],
            "record": result.structured_outcome, "terminal_decision": result.terminal_decision.value},
        "provider_calls": None, "telemetry": None, "resources": None,
        "attempts": [attempt.result.payload() if attempt.result is not None else {"attempt_index": attempt.attempt_index} for attempt in state.attempts],
        "discrepancies": [], "mechanisms": [], "result_identity": result.result_sha256}
    try:
        key = receipt["result"]["observability"].get("assessment_key")
        if key is None:
            raise ValueError("Gold retained observation cut is unavailable")
        observed = inspect_observability(run_root=root, assessment_key=key)
        manifest_record = store.get(kind=RecordKind.OBSERVABILITY_MANIFEST, key=key)
        if manifest_record is None:
            raise ValueError("Gold observation manifest is unavailable")
        manifest = manifest_record.payload
        cut = CaptureCut.from_dict(manifest["capture_cut"])
        view.update(telemetry=observed["telemetry_report"],
            provider_calls=[call.to_dict() for call in reconstruct_call_records(cut)],
            artifact_report=observed["artifact_report"], snapshot_reports=observed["snapshot_reports"],
            capture_cut=manifest["capture_cut"])
        axes["provider_profiles"] = _provider_profiles(inspect_capture(cut))
        view["discrepancies"].extend(observed["discrepancies"])
        for report in [observed["artifact_report"], *observed["snapshot_reports"]]:
            if report["status"] != "COMPLETE":
                view["discrepancies"].append({"code": "DOMAIN_SOURCE_RECONCILIATION_INCOMPLETE", "report": report})
        resource_ref = observed["resource_measurement"]["report_ref"] if observed["resource_measurement"] else manifest["resource_report_ref"]
        resource = store.get(kind=RecordKind.OBSERVATION, key=resource_ref["sha256"])
        if resource is None or reference(resource.payload, resource.payload["schema_version"]).to_dict() != resource_ref:
            raise ValueError("Gold resource report is missing or changed")
        view["resources"] = resource.payload
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        view["discrepancies"].append({"code": "MEASUREMENT_SOURCE_UNAVAILABLE_OR_CHANGED", "detail": str(exc)[:400]})
    try:
        mechanism = inspect_mechanisms(run_root=root)
        view["mechanisms"] = mechanism["proofs"]
        if mechanism["status"] == "INCOMPLETE":
            view["discrepancies"].append({"code": "MECHANISM_SOURCE_INCOMPLETE"})
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        view["discrepancies"].append({"code": "MECHANISM_SOURCE_UNAVAILABLE_OR_CHANGED", "detail": str(exc)[:400]})
    return view
