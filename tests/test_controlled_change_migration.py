from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import personal_slice.cli as compatibility_cli
import synapse.cli as canonical_cli
from synapse.change import ControlledChangeRequest
from synapse.change.runner import execute_controlled_change

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd, cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def init_controlled_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")
    (repo / "app.txt").write_text("old\n", encoding="utf-8")
    (repo / "tasks").mkdir()
    (repo / "patches").mkdir()
    (repo / "patches" / "change.patch").write_text(
        "diff --git a/app.txt b/app.txt\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    task = {
        "task_id": "sample-change",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": "refs/heads/controlled/verified",
        "allowed_scope": ["app.txt"],
        "patch_path": "patches/change.patch",
        "required_scaffold_paths": ["tasks/task.json", "patches/change.patch"],
        "reproduction": {
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "before": {"expected_exit_codes": [0], "timeout_seconds": 10},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 10},
        },
        "acceptance_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "full_suite_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "commit_message": "Apply controlled change",
    }
    (repo / "tasks" / "task.json").write_text(json.dumps(task), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    return repo, base


def test_canonical_cli_handler_builds_request(monkeypatch):
    captured = {}

    def fake_execute(request):
        captured["request"] = request
        return canonical_cli.ControlledChangeResult(
            outcome="APPLIED",
            exit_code=0,
            report_path="report.json",
            verified_commit="commit",
            verified_tree="tree",
            evidence_ref="refs/synapse/change/evidence/run",
            application=None,
            cleanup_status="REMOVED",
            worktree_path=None,
        )

    monkeypatch.setattr(canonical_cli, "execute_controlled_change", fake_execute)
    code = canonical_cli.handle_change_apply(
        canonical_cli.argparse.Namespace(
            base="HEAD",
            task="tasks/task.json",
            keep_worktree=True,
            report_dir="reports",
            environment_kind="TEST",
        )
    )
    assert code == 0
    assert captured["request"] == ControlledChangeRequest(
        base="HEAD",
        task_path="tasks/task.json",
        keep_worktree=True,
        report_dir="reports",
        environment_kind="TEST",
    )


def test_compatibility_cli_handler_builds_request(monkeypatch):
    captured = {}

    def fake_execute(request):
        captured["request"] = request
        return compatibility_cli.ControlledChangeResult(
            outcome="APPLIED",
            exit_code=0,
            report_path="report.json",
            verified_commit=None,
            verified_tree=None,
            evidence_ref=None,
            application=None,
            cleanup_status="REMOVED",
            worktree_path=None,
        )

    monkeypatch.setattr(compatibility_cli, "execute_controlled_change", fake_execute)
    code = compatibility_cli.handle_run(
        compatibility_cli.argparse.Namespace(
            base="HEAD",
            task="tasks/task.json",
            task_json=None,
            keep_worktree=False,
            report_dir=None,
            environment_kind="UNSPECIFIED",
        )
    )
    assert code == 0
    assert captured["request"] == ControlledChangeRequest(base="HEAD", task_path="tasks/task.json")


def read_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def report_path_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Report: "):
            return line.split(": ", 1)[1]
    raise AssertionError(output)


def test_committed_task_loading_ignores_local_poisoning(tmp_path, monkeypatch):
    repo, base = init_controlled_repo(tmp_path)
    poisoned = json.loads((repo / "tasks" / "task.json").read_text(encoding="utf-8"))
    poisoned["allowed_scope"] = ["other.txt"]
    (repo / "tasks" / "task.json").write_text(json.dumps(poisoned), encoding="utf-8")
    monkeypatch.chdir(repo)

    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))

    assert result.outcome == "APPLIED"
    assert result.verified_commit
    assert git(repo, "rev-parse", "refs/heads/controlled/verified") == result.verified_commit
    assert git(repo, "show", "-s", "--format=%P", result.verified_commit) == base


def test_cli_semantic_equivalence(tmp_path):
    canonical_repo, canonical_base = init_controlled_repo(tmp_path / "canonical")
    compatibility_repo, compatibility_base = init_controlled_repo(tmp_path / "compatibility")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    canonical = run(
        [sys.executable, "-m", "synapse.cli", "change", "apply", "--base", canonical_base, "--task", "tasks/task.json", "--environment-kind", "TEST"],
        canonical_repo,
        env=env,
    )
    compatibility = run(
        [sys.executable, "-m", "personal_slice", "run", "--base", compatibility_base, "--task", "tasks/task.json", "--environment-kind", "TEST"],
        compatibility_repo,
        env=env,
    )
    assert canonical.returncode == compatibility.returncode == 0
    canonical_report = read_report(report_path_from_output(canonical.stdout))
    compatibility_report = read_report(report_path_from_output(compatibility.stdout))

    assert canonical_report["outcome"] == compatibility_report["outcome"] == "APPLIED"
    assert canonical_report["verified_tree"] == compatibility_report["verified_tree"]
    assert git(canonical_repo, "show", "-s", "--format=%P", canonical_report["verified_commit"]) == canonical_base
    assert git(compatibility_repo, "show", "-s", "--format=%P", compatibility_report["verified_commit"]) == compatibility_base
    assert git(canonical_repo, "show", "-s", "--format=%s", canonical_report["verified_commit"]) == git(compatibility_repo, "show", "-s", "--format=%s", compatibility_report["verified_commit"])
    assert [p["name"] for p in canonical_report["phases"]] == [p["name"] for p in compatibility_report["phases"]]
    assert [p["status"] for p in canonical_report["phases"]] == [p["status"] for p in compatibility_report["phases"]]
    assert canonical_report["application"]["status"] == compatibility_report["application"]["status"] == "APPLIED"
    assert canonical_report["application"]["policy"] == compatibility_report["application"]["policy"]
    assert canonical_report["schema"] == compatibility_report["schema"] == "personal_slice.report/v0.3.2"
    assert canonical_report["task_id"] == compatibility_report["task_id"]
    assert canonical_report["evidence_ref"].startswith("refs/synapse/change/evidence/")
    assert compatibility_report["evidence_ref"].startswith("refs/synapse/change/evidence/")
    assert git(canonical_repo, "rev-parse", canonical_report["evidence_ref"]) == canonical_report["verified_commit"]
    assert git(compatibility_repo, "rev-parse", compatibility_report["evidence_ref"]) == compatibility_report["verified_commit"]
