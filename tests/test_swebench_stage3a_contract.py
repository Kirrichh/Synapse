"""Stage 3A SWE-bench contract tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from synapse.experiments.swebench import baseline as baseline_module
from synapse.experiments.swebench import run_baseline_task
from synapse.experiments.swebench.contract import (
    AttemptVerdict,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
)
from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.worker import ExternalWorkerStatus


class NeverOracle:
    def verify(self, worktree_path: Path, task: BaselineTask):
        raise AssertionError("oracle should not run")


def test_experiment_arm_schema_values_are_explicit():
    assert {arm.value for arm in ExperimentArm} == {"BASELINE", "GOLD"}


def test_attempt_verdict_is_not_worker_status_or_success():
    verdict_values = {verdict.value for verdict in AttemptVerdict}

    assert "PROPOSED_PATCH" not in verdict_values
    assert "ERROR" not in verdict_values
    assert "TIMEOUT" not in verdict_values
    assert "SUCCESS" not in verdict_values
    assert ExternalWorkerStatus.PROPOSED_PATCH.value == "PROPOSED_PATCH"


def test_baseline_run_record_to_dict_is_json_serializable():
    run = BaselineRunRecord(
        run_id="run",
        task_id="task",
        instance_id="instance",
        arm=ExperimentArm.BASELINE,
        base_revision="abc123",
        replicate_id=1,
        max_attempts=3,
        resolved=False,
        attempts=(),
        total_provider_tokens=None,
        primary_metric_usable=False,
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:01Z",
        diagnostics={"stage": "3A"},
    )

    json.dumps(run.to_dict(), sort_keys=True)


def test_gold_is_schema_only_and_not_executable(tmp_path):
    task = BaselineTask("task", "instance", "statement", ("file.py",))

    with pytest.raises(ValueError, match="stage3a: unsupported_arm"):
        run_baseline_task(
            task,
            repo_root=tmp_path,
            base_revision="HEAD",
            replicate_id=1,
            mini=MiniInvocationConfig(),
            oracle=NeverOracle(),
            run_root=tmp_path / "runs",
            arm=ExperimentArm.GOLD,
        )


def test_no_auto_selection_api_is_exposed():
    forbidden = {
        "ComplexityClassifier",
        "DifficultyClassifier",
        "ArmRouter",
        "RoutingPolicy",
        "AutoArmSelection",
        "choose_arm",
        "select_arm",
        "auto_choose_arm",
        "route_task_to_arm",
    }
    package_names = set(dir(baseline_module))

    assert package_names.isdisjoint(forbidden)
    source = inspect.getsource(baseline_module)
    assert "apply_verified_commit" not in source
    assert "execute_controlled_change" not in source
