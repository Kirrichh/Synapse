from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from synapse.change import ControlledChangeRequest
from synapse.change.report import default_report_dir
from synapse.change.runner import execute_controlled_change
import synapse.change.workspace as workspace
from synapse.change.workspace import GitWorkspaceError, find_repo_root, git, git_dir


def _init_git_repo(repo: Path) -> str:
    repo.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "seed")
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)


def _unicode_repo(tmp_path: Path) -> Path:
    return tmp_path / "Кирилл" / "репозиторий с пробелом"


def test_git_decodes_utf8_stdout_without_mojibake(monkeypatch, tmp_path):
    cwd = tmp_path / "repo"
    expected = "C:\\Users\\Кирилл\\репозиторий"
    calls: list[tuple[list[str], str, dict[str, object]]] = []

    def fake_run(argv, cwd, **kwargs):
        calls.append((argv, cwd, kwargs))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=expected.encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    completed = git(["rev-parse", "--show-toplevel"], cwd)

    assert isinstance(completed, subprocess.CompletedProcess)
    assert isinstance(completed.stdout, str)
    assert completed.stdout == expected
    assert "Рљ" not in completed.stdout
    assert completed.args == ["git", "rev-parse", "--show-toplevel"]
    assert calls == [(["git", "rev-parse", "--show-toplevel"], str(cwd), {"capture_output": True})]
    assert "text" not in calls[0][2]
    assert "universal_newlines" not in calls[0][2]


def test_git_stdout_uses_surrogateescape(monkeypatch, tmp_path):
    def fake_run(argv, cwd, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"path-\xff", stderr=b"")

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    completed = git(["status"], tmp_path)

    assert completed.stdout == "path-\udcff"
    assert "\ufffd" not in completed.stdout


def test_git_text_preserves_universal_newline_semantics(monkeypatch, tmp_path):
    def fake_run(argv, cwd, **kwargs):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=b"first\r\nsecond\rthird\n",
            stderr=b"warning\r\nnext\rline\n",
        )

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    completed = git(["status"], tmp_path)

    assert completed.stdout == "first\nsecond\nthird\n"
    assert completed.stderr == "warning\nnext\nline\n"


def test_git_check_false_returns_decoded_failure(monkeypatch, tmp_path):
    def fake_run(argv, cwd, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=128, stdout=b"out-\xff", stderr="ошибка".encode("utf-8"))

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    completed = git(["status"], tmp_path, check=False)

    assert completed.returncode == 128
    assert completed.args == ["git", "status"]
    assert isinstance(completed.stdout, str)
    assert isinstance(completed.stderr, str)
    assert completed.stdout == "out-\udcff"
    assert completed.stderr == "ошибка"


def test_git_check_true_raises_existing_error_type(monkeypatch, tmp_path):
    def fake_run(argv, cwd, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=128, stdout=b"", stderr="ошибка".encode("utf-8"))

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    with pytest.raises(GitWorkspaceError) as exc_info:
        git(["status"], tmp_path)

    message = str(exc_info.value)
    assert "git status failed with 128" in message
    assert "ошибка" in message


def test_find_repo_root_handles_unicode_repository_path(tmp_path):
    repo = _unicode_repo(tmp_path)
    _init_git_repo(repo)
    nested = repo / "nested" / "child"
    nested.mkdir(parents=True)

    found = find_repo_root(nested)

    assert found.samefile(repo)
    assert "Рљ" not in str(found)
    assert "Рё" not in str(found)
    assert "СЂ" not in str(found)


def test_git_dir_and_default_report_dir_work_in_unicode_repository(tmp_path):
    repo = _unicode_repo(tmp_path)
    _init_git_repo(repo)

    observed_git_dir = git_dir(repo)
    actual_git_dir = observed_git_dir if observed_git_dir.is_absolute() else repo / observed_git_dir

    assert actual_git_dir.exists()
    assert actual_git_dir.is_dir()

    reports_dir = default_report_dir(repo)
    report_file = reports_dir / "diagnostic.json"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps({"diagnostic": True}) + "\n", encoding="utf-8")

    assert json.loads(report_file.read_text(encoding="utf-8")) == {"diagnostic": True}


def test_controlled_change_smoke_in_unicode_repository_root(tmp_path, monkeypatch):
    repo = _unicode_repo(tmp_path)
    base = _init_controlled_change_repo(repo)
    nested = repo / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)

    found = find_repo_root(nested)
    result = execute_controlled_change(
        ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST")
    )

    assert found.samefile(repo)
    assert "Рљ" not in str(found)
    assert result.outcome == "APPLIED"
    assert result.failure_code is None
    assert result.report_path is not None
    report_path = Path(result.report_path)
    assert report_path.exists()
    assert default_report_dir(repo) in (report_path.parent, report_path.parent.resolve())
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "personal_slice.report/v0.4.0"


def _init_controlled_change_repo(repo: Path) -> str:
    _init_git_repo(repo)
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
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "unicode-change",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": "refs/heads/controlled/verified",
        "allowed_scope": ["app.txt"],
        "patch_path": "patches/change.patch",
        "required_scaffold_paths": ["tasks/task.json", "patches/change.patch"],
        "reproduction": {
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "committed_inputs": [],
            "before": {"expected_exit_codes": [0], "timeout_seconds": 10},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 10},
        },
        "baseline_commands": [],
        "acceptance_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "full_suite_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "commit_message": "Apply controlled change",
    }
    (repo / "tasks" / "task.json").write_text(json.dumps(task, sort_keys=True), encoding="utf-8")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "base")
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()
