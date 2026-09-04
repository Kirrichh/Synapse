"""One heavy shard: user approval resumes the existing two-attempt runtime."""

from dataclasses import replace
import json

import pytest

from synapse.cli import main
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus
from synapse.experiments.gold.stage10.approval import ApprovalRequired, RunApprovalPolicy
from acceptance.stage4.stage11._builders import _plan_profile, run_world


def test_one_operator_command_resumes_without_repreparing_or_reapproving(tmp_path, capsys):
    world = run_world(
        tmp_path, max_attempts=2, fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        worker_outcomes=("PATCH", "PATCH"), run_id="operator-approved-run",
    )
    profile = _plan_profile()
    approvals = RunApprovalPolicy(
        tmp_path / "operator-approvals", world.manifest.manifest_sha256,
        profile.governing_human_authority,
    )
    world.attempt_inputs.plan_profile = replace(profile, approval_policy=approvals)
    with pytest.raises(ApprovalRequired) as pending:
        world.execute()
    assert world.composition.record_store.get(kind=RecordKind.PREPARATION_STARTED, key="1") is None
    assert world.attempt_inputs._cached_source is None
    assert world.worker_process.calls == world.oracle.calls == 0

    assert main(["approve", str(pending.value.request_path), "--store", str(approvals.store_root)]) == 0
    grant = json.loads(capsys.readouterr().out)["grant_ref"]
    result = world.execute()

    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert world.worker_process.calls == world.oracle.calls == 2
    for prepared in world.attempt_inputs.prepared.values():
        receipt = prepared.accepted_plan.decision.human_approval_ref
        stored = json.loads((approvals.store_root / "receipts" / (receipt.sha256 + ".json")).read_bytes())
        assert stored["grant_ref"] == grant
    assert world.execute() == result
    assert world.worker_process.calls == world.oracle.calls == 2
