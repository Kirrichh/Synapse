from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

from synapse.change import ControlledChangeRequest
from synapse.change.outcomes import APPLIED
from synapse.change.runner import execute_controlled_change


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "mini_capture"
TASK_PATH = "controlled_changes/tasks/mini_capture_bridge.json"
PATCH_PATH = "controlled_changes/patches/candidate.patch"
TARGET_REF = "refs/heads/controlled/verified"
NEW_FILE_PATCH_SECTION = "diff --git a/src/mul.py b/src/mul.py"


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pytest_command() -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "tests_local/test_calc.py",
    ]


def _mini_capture_task() -> dict[str, object]:
    command = _pytest_command()
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "mini-candidate-bridge-capture-001",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": TARGET_REF,
        "allowed_scope": {"exact": [], "prefixes": ["src"]},
        "patch_path": PATCH_PATH,
        "required_scaffold_paths": [
            TASK_PATH,
            PATCH_PATH,
            "src/calc.py",
            "tests_local/test_calc.py",
        ],
        "reproduction": {
            "command": command,
            "committed_inputs": ["src/calc.py", "tests_local/test_calc.py"],
            "before": {"expected_exit_codes": [1], "timeout_seconds": 30},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 30},
        },
        "baseline_commands": [],
        "acceptance_commands": [command],
        "full_suite_commands": [command],
        "commit_message": "Apply captured mini candidate",
    }


def _build_capture_001_repo(repo: Path) -> tuple[str, str]:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")

    _write(repo / "README.md", "captured mini candidate bridge fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")

    _write(repo / "src" / "calc.py", "def add(a, b):\n    return a - b  # bug\n")
    _write(
        repo / "tests_local" / "test_calc.py",
        "from src.calc import add\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
    )
    (repo / PATCH_PATH).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_ROOT / "capture_001" / "candidate.patch", repo / PATCH_PATH)
    _write(repo / TASK_PATH, json.dumps(_mini_capture_task(), indent=2, sort_keys=True) + "\n")

    git(repo, "add", ".")
    git(repo, "commit", "-m", "captured mini fixture base")
    return git(repo, "rev-parse", "HEAD"), TASK_PATH


def _read_report(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _phase_statuses(report: dict[str, object]) -> dict[str, str]:
    phases = report["phases"]
    assert isinstance(phases, list)
    return {phase["name"]: phase["status"] for phase in phases}


def _capture_001_replay_reached_applied(result) -> None:
    assert result.outcome == APPLIED
    assert result.exit_code == 0
    assert result.verified_commit is not None
    assert result.evidence_ref is not None
    assert result.report_path is not None
    assert result.application is not None
    assert result.application.status == APPLIED
    assert result.target_ref == TARGET_REF

    phase_status = {phase.name: phase.status for phase in result.phases}
    for phase_name in (
        "apply_patch_check",
        "apply_patch",
        "scope_check_after_patch",
        "reproduction_before",
        "reproduction_after",
        "acceptance_1",
        "full_suite_1",
        "candidate_integrity",
        "verified_commit",
        "evidence_ref",
    ):
        assert phase_status[phase_name] == "PASS"
    assert phase_status["application"] == APPLIED

    report = _read_report(result.report_path)
    report_phase_status = _phase_statuses(report)
    assert report["lifecycle"]["outcome"] == APPLIED
    assert report["verified_result"]["verified_commit"] == result.verified_commit
    assert report["evidence"]["evidence_ref"] == result.evidence_ref
    assert report["application"]["result_status"] == APPLIED
    assert report_phase_status["apply_patch_check"] == "PASS"
    assert report_phase_status["apply_patch"] == "PASS"
    assert report_phase_status["scope_check_after_patch"] == "PASS"
    assert report_phase_status["reproduction_before"] == "PASS"
    assert report_phase_status["reproduction_after"] == "PASS"
    assert report_phase_status["acceptance_1"] == "PASS"
    assert report_phase_status["full_suite_1"] == "PASS"
    assert report_phase_status["candidate_integrity"] == "PASS"
    assert report_phase_status["verified_commit"] == "PASS"
    assert report_phase_status["evidence_ref"] == "PASS"
    assert report_phase_status["application"] == APPLIED


def test_capture_001_modify_only_candidate_replays_through_controlled_change(tmp_path, monkeypatch):
    # This test replays frozen real mini output only. It does not call mini,
    # Ollama, provider APIs, Docker, SWE-bench, or production bridge logic.
    provenance = _read_json(FIXTURE_ROOT / "capture_001" / "provenance.json")
    patch_bytes = (FIXTURE_ROOT / "capture_001" / "candidate.patch").read_bytes()
    assert provenance["frozen_real_mini_output"] is True
    assert provenance["pytest_replay_must_not_call_mini"] is True
    assert provenance["cost_tracking_mode"]

    repo = tmp_path / "repo"
    base, task_path = _build_capture_001_repo(repo)
    report_dir = tmp_path / "reports"
    monkeypatch.chdir(repo)

    result = execute_controlled_change(
        ControlledChangeRequest(
            base=base,
            task_path=task_path,
            keep_worktree=True,
            report_dir=str(report_dir),
            environment_kind="TEST",
        )
    )

    try:
        if provenance["observed_candidate_committed_in_scratch"] is True and not patch_bytes:
            assert result.failure_code is not None
            assert result.failure_phase is not None
        _capture_001_replay_reached_applied(result)
        assert git(repo, "rev-parse", TARGET_REF) == result.verified_commit
        assert git(repo, "rev-parse", result.evidence_ref) == result.verified_commit
    finally:
        if result.worktree_path:
            run(["git", "worktree", "remove", "--force", result.worktree_path], repo)
            shutil.rmtree(Path(result.worktree_path).parent, ignore_errors=True)


def test_capture_002_documents_observed_new_file_representation() -> None:
    patch_text = (FIXTURE_ROOT / "capture_002" / "candidate.patch").read_text(encoding="utf-8")
    envelope = _read_json(FIXTURE_ROOT / "capture_002" / "envelope.json")
    provenance = _read_json(FIXTURE_ROOT / "capture_002" / "provenance.json")
    status = (FIXTURE_ROOT / "capture_002" / "git_status_short.txt").read_text(encoding="utf-8")
    log = (FIXTURE_ROOT / "capture_002" / "git_log_oneline_5.txt").read_text(encoding="utf-8")
    diagnostics = envelope["diagnostics"]
    assert isinstance(diagnostics, dict)

    observed_form = provenance["observed_new_file_representation_form"]
    assert observed_form in {
        "A_untracked",
        "B_staged_not_committed",
        "C_patch_contains_new_file",
        "D_committed_in_scratch",
    }
    assert provenance["git_status_short_captured_before_scratch_mutation"] is True
    assert provenance["git_log_oneline_5_captured_before_scratch_mutation"] is True

    patch_has_new_file = NEW_FILE_PATCH_SECTION in patch_text
    if observed_form == "A_untracked":
        untracked = diagnostics.get("untracked_files", [])
        assert "src/mul.py" in untracked or "src/mul.py" in status
        assert not patch_has_new_file
        assert provenance["candidate_patch_contains_src_mul_py_section"] is False
    elif observed_form == "B_staged_not_committed":
        assert "src/mul.py" in status
        assert any(line[:2].strip().startswith("A") and "src/mul.py" in line for line in status.splitlines())
        assert not patch_has_new_file
        assert provenance["candidate_patch_contains_src_mul_py_section"] is False
    elif observed_form == "C_patch_contains_new_file":
        assert patch_has_new_file
        assert provenance["candidate_patch_contains_src_mul_py_section"] is True
    elif observed_form == "D_committed_in_scratch":
        assert provenance["observed_candidate_committed_in_scratch"] is True
        assert provenance["candidate_commit_sha"] is not None
        assert (
            provenance["candidate_commit_sha"] in log
            or provenance["candidate_commit_log_subject"] in log
        )

    assert provenance["cost_tracking_mode"]
    assert provenance["cost_limit_note"]
