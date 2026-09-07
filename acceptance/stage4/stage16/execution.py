"""External experiment bindings to the existing Baseline API and Gold CLI.

No controller, prompt renderer, oracle semantics or publication logic lives
here. Gold approvals use its ordinary operator action. The legacy Baseline
API has no durable resume, so an interrupted invocation is never repeated.
"""

from pathlib import Path
import json
import subprocess
import sys

from synapse.resource_usage import measure_operation, recording_resources
from synapse.experiments.gold.stage15.capture_store import CaptureStore
from synapse.experiments.gold.stage15.resource_accounting import ResourceRecorder
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.experiments.gold.stage15.worker_accounting import WorkerAccounting
from synapse.experiments.swebench.baseline import run_baseline_task
from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.experiments.swebench.paired_measurement import baseline_member_from_run
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig, SWEbenchHarnessOracleRunner,
    build_oracle_config_fingerprint_payload, build_oracle_environment_fingerprint_payload,
    compute_oracle_config_fingerprint, compute_oracle_environment_fingerprint,
    detect_swebench_version,
)
from synapse.worker.provider_transport import MiniProviderConfiguration, frozen_mini_runtime

from .protocol import canonical, digest, read_source, source, environment_identity


def oracle_configuration(value):
    value = dict(value)
    for name in ("python_executable", "swebench_work_dir"):
        value[name] = Path(value[name])
    return SWEbenchHarnessOracleConfig(**value)


def oracle_fingerprints(configuration):
    version = detect_swebench_version()
    return {"oracle_config": compute_oracle_config_fingerprint(
        build_oracle_config_fingerprint_payload(configuration, swebench_version=version)),
        "oracle_environment": compute_oracle_environment_fingerprint(
            build_oracle_environment_fingerprint_payload(configuration, swebench_version=version))}


def execute_baseline(slot, definition):
    environment = environment_identity()
    root = Path(definition["run_root"])
    root.mkdir(parents=True, exist_ok=False)
    task = BaselineTask(**definition["task"])
    if task.task_id != slot["task_id"]:
        raise ValueError("Baseline task differs from its preregistered allocation")
    mini = MiniInvocationConfig(**definition["mini"])
    runtime = frozen_mini_runtime([mini.executable])
    oracle = oracle_configuration(definition["oracle"])
    fingerprints = oracle_fingerprints(oracle)
    capture = CaptureStore(root / "capture", run_id=slot["slot_id"],
        manifest_ref=reference({"slot_id": slot["slot_id"], "input": slot["input_ref"]}, "synapse.acceptance.stage16.allocation/v1"))
    accounting = WorkerAccounting(store=capture, configuration=MiniProviderConfiguration(
        model=mini.model, **definition["provider_connection"]))
    recorder = ResourceRecorder(capture)
    with recording_resources(recorder), measure_operation("runtime.execution") as operation:
        run = run_baseline_task(task, repo_root=definition["repo_root"], base_revision=definition["base_revision"],
            replicate_id=slot["replicate_id"], max_attempts=definition["max_attempts"], mini=mini,
            oracle=SWEbenchHarnessOracleRunner(oracle), run_root=root / "runs", accounting=accounting)
        result = json.loads(canonical(run.to_dict()))
        result_ref = reference(result, "synapse.acceptance.stage16.baseline-result/v1")
        operation.bind_result(result_ref.to_dict())
    recorder.seal(result_ref)
    result_path = root / "result.json"
    result_path.write_bytes(canonical(result))
    member = baseline_member_from_run(run,
        oracle_config_fingerprint=fingerprints["oracle_config"],
        oracle_environment_fingerprint=fingerprints["oracle_environment"],
        environment_fingerprint=digest(environment))
    return {"terminal": True, "kind": "BASELINE", "result_ref": source(result_path),
        "capture_cut": capture.cut().to_dict(), "run_id": run.run_id, "member": member.to_dict(),
        "physical_sources": [source(path) for path in sorted((root / "runs" / run.run_id).rglob("*")) if path.is_file()],
        "runtime": runtime, "oracle_fingerprints": fingerprints, "environment": environment}


def execute_gold(slot, definition, *, repository, resume=False, approval=None):
    environment = environment_identity()
    declaration = read_source(definition["declaration_ref"])
    if declaration["config"]["task_id"] != slot["task_id"]:
        raise ValueError("Gold task differs from its preregistered allocation")
    root = Path(definition["run_root"])
    if approval is not None:
        request = Path(approval["request_path"]).resolve()
        if not request.is_relative_to(root.resolve()):
            raise ValueError("approval request escaped its allocated Gold run")
        args = ["approve", str(request), "--store", str(root / "approvals"), "--resume-run", str(root)]
    elif resume:
        args = ["project", "resume", "--run-dir", str(root)]
    else:
        if root.exists():
            raise ValueError("new Gold allocation has pre-existing run state")
        args = ["project", "run", "--state-dir", definition["state_root"],
            "--input", definition["declaration_ref"]["path"], "--run-dir", str(root)]
    command = [sys.executable, "-B", "-m", "synapse", *args]
    completed = subprocess.run(command, cwd=repository, capture_output=True, text=True,
        timeout=definition.get("cli_timeout_seconds", 600))
    try:
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        value = lines[-1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("canonical CLI did not return a machine-readable result") from exc
    if completed.returncode == 3:
        return {"terminal": False, "kind": "GOLD", "pending": value, "argv": command,
            "returncode": completed.returncode, "stdout_sha256": digest(lines), "environment": environment}
    if "result" not in value or "structured_outcome" not in value["result"]:
        raise RuntimeError(f"canonical Gold invocation failed: {value.get('status')}")
    return {"terminal": True, "kind": "GOLD", "result": value, "argv": command,
        "returncode": completed.returncode, "stdout_sha256": digest(lines), "environment": environment}
