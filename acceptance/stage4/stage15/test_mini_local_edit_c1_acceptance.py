"""An actual Mini proposal passes through unchanged C1 and a fresh oracle.

This boundary acceptance does not replace the Gold controller, plan authority,
replay or publication, and does not claim a complete Gold run.
"""

import hashlib
import subprocess
import sys
import time

from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.swebench.contract import OracleResult
from synapse.experiments.swebench.gold_attempt_writer import GoldAttemptWriter
from synapse.experiments.swebench.gold_runner import (
    GoldRunnerCommandExpectation, GoldRunnerCommandPolicy, run_gold_attempt,
)
from synapse.worker.contract import ExternalCodingWorkerResult, ExternalWorkerStatus, ExternalWorkerUsage, ExternalWorkerTokenStatus
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND

from acceptance.stage4.stage10.test_local_edit_contract import information, proposal, feedback
from acceptance.stage4.stage15.test_mini_local_edit_acceptance import SOURCE, repository, invoke
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint


class ArithmeticOracle:
    def __init__(self):
        self.observations = []

    def verify(self, worktree_path, task):
        started = time.perf_counter()
        completed = subprocess.run([sys.executable, "-B", "-c",
            "from src.calc import add; assert all(add(a, b) == expected for a, b, expected in "
            "[(2, 3, 5), (-2, 3, 1), (0, 0, 0), (4, 3, 7)])"], cwd=worktree_path, capture_output=True, text=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_path, capture_output=True, text=True, check=True).stdout.strip()
        self.observations.append((head, completed.returncode))
        return OracleResult(resolved=completed.returncode == 0, returncode=completed.returncode,
            stdout=completed.stdout, stderr=completed.stderr, duration_seconds=time.perf_counter() - started,
            diagnostics={"infra_error": False, "task_id": task.task_id})


def test_real_c1_rejection_drives_another_mini_proposal_then_c1_confirms_success(tmp_path):
    repo, public = repository(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Acceptance"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "acceptance@example.invalid"], check=True)
    command = (sys.executable, "-B", "-c", "from src.calc import add; assert add(4, 3) == 7")
    policy = GoldRunnerCommandPolicy(task_id="local-edit", instance_id="local-edit-1", statement=public.to_dict()["statement"],
        allowed_scope=("src",), reproduction_command=command, reproduction_committed_inputs=("src/calc.py",),
        reproduction_before=GoldRunnerCommandExpectation(expected_exit_codes=(1,), timeout_seconds=10),
        reproduction_after=GoldRunnerCommandExpectation(expected_exit_codes=(0,), timeout_seconds=10),
        baseline_commands=((sys.executable, "-B", "-c", "pass"),), acceptance_commands=(command,), full_suite_commands=(command,),
        commit_message="Verify local Mini proposal", required_scaffold_paths=("src/calc.py",), task_class="TEST")
    variants = proposal(("a - b", "7"), ("a - b", "a + b"))
    action = LOCAL_EDIT_COMMAND + canonical_json_bytes(variants).decode()
    local_feedback, results = [], []
    oracle = ArithmeticOracle()
    run_root = tmp_path / "c1"
    writer = GoldAttemptWriter(run_root, repo_root=repo, report_root=run_root / "controlled-change-reports")
    for index in range(2):
        invocation_root = tmp_path / f"mini-{index}"
        private = information(source=SOURCE, revision=public.to_dict()["repository_revision"], extra=local_feedback)
        with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                               command=action) as (endpoint, requests):
            candidate, trajectory = invoke(invocation_root, repo, public, private, endpoint)
        assert candidate.status.value == "PROPOSED_PATCH", candidate
        assert trajectory["info"]["local_edit_result"]["selected_index"] == index
        # Preserve the real worker's bytes and usage at the existing C1 input.
        usage = candidate.usage
        worker = ExternalCodingWorkerResult(worker_status=ExternalWorkerStatus(candidate.status.value),
            diff_text=candidate.diff_text, touched_files=candidate.touched_files, diagnostics=candidate.diagnostics,
            usage=ExternalWorkerUsage(token_status=ExternalWorkerTokenStatus(usage.token_status.value),
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, thinking_tokens=usage.thinking_tokens,
                total_tokens=usage.total_tokens, thinking_included=usage.thinking_included, diagnostics=usage.diagnostics))
        result = run_gold_attempt(repo_root=repo, gold_run_id="local-edit", attempt_id=str(index + 1), worker_result=worker,
            command_policy=policy, oracle=oracle, writer=writer, run_root=run_root, environment_kind="TEST")
        results.append(result)
        assert result.gold_evidence is not None, result.payload
        assert result.gold_evidence.patch_sha256 == hashlib.sha256(candidate.diff_text.encode()).hexdigest()
        assert result.payload["oracle_invoked"] is True
        assert result.payload["oracle_resolved"] is (index == 1)
        assert oracle.observations[index][0] == result.controlled_change_result.verified_commit
        assert result.write_result.ok
        assert len(requests) == 1
        if index == 0:
            assert result.status == "GOLD_ORACLE_UNRESOLVED", result.payload
            # This is a real independently checked rejection, not a source
            # recipe exit code, worker opinion, timeout or synthetic verdict.
            local_feedback = [feedback(candidate.diff_text, result.payload["oracle_resolved"])]
        else:
            assert trajectory["info"]["local_edit_result"]["candidates"][0]["reason"] == "EXACT_VERIFIED_PATCH_REJECTED"
            assert result.status == "GOLD_APPLIED_WITH_EVIDENCE", result.payload
    assert [code for _, code in oracle.observations] == [1, 0]
    assert results[0].gold_evidence.patch_sha256 != results[1].gold_evidence.patch_sha256
