"""§§30, 33: actual Baseline attempts use the existing physical call owner."""

import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, TelemetryStatus
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.experiments.gold.stage15.worker_accounting import WorkerAccounting
from synapse.experiments.swebench.baseline import run_baseline_task
from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.experiments.swebench.oracle import CommandOracleRunner
from synapse.worker.provider_transport import MiniProviderConfiguration
from tests.test_swebench_gold_runner import build_candidate_repo


def test_raw_carry_attempts_capture_real_requests_and_trajectories(tmp_path):
    repo = tmp_path / "repo"
    base, _ = build_candidate_repo(repo)
    task = BaselineTask("paired-task", "paired-instance", "Inspect src/calc.py.", ("src/calc.py",))
    capture = CaptureStore(tmp_path / "capture", run_id="baseline-slot",
        manifest_ref=reference(task.to_dict(), "acceptance.stage16.task/v1"))
    mini = MiniInvocationConfig(executable=str(Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")),
        model="gpt-4o-mini", cost_limit=1, step_limit=3, timeout_seconds=60)
    with provider_endpoint() as (endpoint, requests):
        run = run_baseline_task(task, repo_root=repo, base_revision=base, replicate_id=0,
            max_attempts=2, mini=mini, oracle=CommandOracleRunner((sys.executable, "-c", "raise SystemExit(1)")),
            run_root=tmp_path / "baseline", accounting=WorkerAccounting(store=capture,
                configuration=MiniProviderConfiguration("gpt-4o-mini", "acceptance-only", endpoint, 10)))
    assert len(run.attempts) == len(requests) == 2
    assert [attempt.verdict.value for attempt in run.attempts] == ["NO_CANDIDATE", "NO_CANDIDATE"]
    assert run.resolved is False
    frames = inspect_capture(capture.cut())
    openings = [frame["payload"] for frame in frames if frame["kind"] == "INVOCATION_OPEN"]
    assert [item["attempt_id"] for item in openings] == ["1", "2"]
    assert [item["invocation_id"] for item in openings] == [f"{run.run_id}:attempt:1", f"{run.run_id}:attempt:2"]
    assert sum(frame["kind"] == "INVOCATION_CLOSED" for frame in frames) == 2
    assert requests[0]["messages"] != requests[1]["messages"]
    assert "RAW BASELINE RETRY CONTEXT" in json.dumps(requests[1])
    assert "RAW BASELINE RETRY CONTEXT" not in json.dumps(requests[0])
    report = reconcile_telemetry(capture.cut())
    assert report.status is TelemetryStatus.COMPLETE, report.to_dict()
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] == 36
    # Preserve the legacy writer. A physical inventory is additional evidence,
    # never a reconstruction of calls from its aggregate token field.
    rows = (tmp_path / "baseline" / run.run_id / "attempts.jsonl").read_text().splitlines()
    assert [json.loads(row)["attempt_id"] for row in rows] == [1, 2]
