from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

from synapse.change import ControlledChangeRequest
from synapse.change.outcomes import APPLIED
from synapse.change.runner import execute_controlled_change


OLD_SOURCE = 'def bridge_value():\n    return "external-agent-artifact"\n'
NEW_SOURCE = 'def bridge_value():\n    return "canonical-controlled-change"\n'
TASK_PATH = "controlled_changes/tasks/bridge_smoke.json"
PATCH_PATH = "controlled_changes/patches/bridge_smoke.patch"
TARGET_REF = "refs/heads/controlled/verified"


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bridge_task() -> dict[str, object]:
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "bridge-smoke",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": TARGET_REF,
        "allowed_scope": ["src/example.py"],
        "patch_path": PATCH_PATH,
        "required_scaffold_paths": [
            TASK_PATH,
            PATCH_PATH,
            "src/example.py",
            "tests/test_example.py",
        ],
        "reproduction": {
            "command": [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('src/example.py').read_text(encoding='utf-8'))",
            ],
            "committed_inputs": ["src/example.py", "tests/test_example.py"],
            "before": {
                "expected_exit_codes": [0],
                "combined_output_contains": ["external-agent-artifact"],
                "timeout_seconds": 10,
            },
            "after": {
                "expected_exit_codes": [0],
                "combined_output_contains": ["canonical-controlled-change"],
                "timeout_seconds": 10,
            },
        },
        "baseline_commands": [],
        "acceptance_commands": [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "assert 'canonical-controlled-change' in Path('src/example.py').read_text(encoding='utf-8')",
            ]
        ],
        "full_suite_commands": [[sys.executable, "tests/test_example.py"]],
        "commit_message": "Apply bridge smoke patch",
    }


def _build_bridge_fixture(repo: Path, *, commit_patch: bool) -> tuple[str, str]:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")

    _write(repo / "README.md", "controlled-change bridge spike fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")

    _write(repo / "src" / "example.py", OLD_SOURCE)
    _write(
        repo / "tests" / "test_example.py",
        "from pathlib import Path\n\n"
        "\n"
        "def test_bridge_value():\n"
        '    assert "canonical-controlled-change" in Path("src/example.py").read_text(encoding="utf-8")\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    test_bridge_value()\n",
    )
    git(repo, "add", "src/example.py", "tests/test_example.py")
    git(repo, "commit", "-m", "pre-patch source")

    _write(repo / "src" / "example.py", NEW_SOURCE)
    patch_text = run(["git", "diff", "--", "src/example.py"], repo, check=True).stdout
    assert patch_text
    _write(repo / "src" / "example.py", OLD_SOURCE)

    if commit_patch:
        _write(repo / PATCH_PATH, patch_text)
    _write(repo / TASK_PATH, json.dumps(_bridge_task(), indent=2, sort_keys=True) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "bridge fixture base")
    base = git(repo, "rev-parse", "HEAD")

    if not commit_patch:
        _write(repo / PATCH_PATH, patch_text)

    return base, TASK_PATH


def _read_report(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _phase_statuses(report: dict[str, object]) -> dict[str, str]:
    phases = report["phases"]
    assert isinstance(phases, list)
    return {phase["name"]: phase["status"] for phase in phases}


def test_committed_patch_artifact_bridges_into_controlled_change_lifecycle(tmp_path, monkeypatch):
    # This test proves controlled-change bridge feasibility only. It does not
    # implement Gold execution, paired pilots, SWE-bench validation, captured
    # mini-diff compatibility, or economic telemetry.
    repo = tmp_path / "repo"
    base, task_path = _build_bridge_fixture(repo, commit_patch=True)
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
        assert result.outcome == APPLIED
        assert result.exit_code == 0
        assert result.verified_commit is not None
        assert result.evidence_ref is not None
        assert result.report_path is not None
        assert result.application is not None
        assert result.application.status == APPLIED
        assert Path(result.report_path).parent == report_dir
        assert Path(result.report_path).is_file()
        assert result.target_ref == TARGET_REF

        assert result.trusted_inputs is not None
        assert result.trusted_inputs.task_path == task_path
        assert result.trusted_inputs.patch_path == PATCH_PATH
        assert result.trusted_inputs.patch_bytes

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
        assert report["lifecycle"]["outcome"] == APPLIED
        assert report["verified_result"]["verified_commit"] == result.verified_commit
        assert report["evidence"]["evidence_ref"] == result.evidence_ref
        assert report["application"]["result_status"] == APPLIED
        report_phase_status = _phase_statuses(report)
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

        assert git(repo, "rev-parse", TARGET_REF) == result.verified_commit
        assert git(repo, "rev-parse", result.evidence_ref) == result.verified_commit

        executed_commands = [
            " ".join(phase.command)
            for phase in result.phases
            if phase.command is not None
        ]
        forbidden_markers = ("swebench", "mini", "synapse.llm", "GEMINI", "LLM")
        assert not any(
            marker in command
            for command in executed_commands
            for marker in forbidden_markers
        )
    finally:
        if result.worktree_path:
            run(["git", "worktree", "remove", "--force", result.worktree_path], repo)
            shutil.rmtree(Path(result.worktree_path).parent, ignore_errors=True)


def test_uncommitted_task_patch_path_is_rejected_by_committed_blob_gate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    base, task_path = _build_bridge_fixture(repo, commit_patch=False)
    report_dir = tmp_path / "reports"
    assert (repo / PATCH_PATH).is_file()
    assert run(["git", "cat-file", "-e", f"{base}:{PATCH_PATH}"], repo).returncode != 0
    monkeypatch.chdir(repo)

    result = execute_controlled_change(
        ControlledChangeRequest(
            base=base,
            task_path=task_path,
            report_dir=str(report_dir),
            environment_kind="TEST",
        )
    )

    assert result.failure_code == "PATCH_PATH_NOT_REGULAR_FILE"
    assert result.failure_phase == "task_contract"
    assert result.verified_commit is None
    assert result.evidence_ref is None
    assert result.report_path is not None
    assert Path(result.report_path).is_file()
    assert any("TASK_CONTRACT_ERROR" in item for item in result.diagnostics)
