from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from personal_slice.application import ZERO_OID, apply_verified_commit
from personal_slice.git_workspace import changed_files, git

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, cwd: Path, *, check=True, env=None):
    merged = os.environ.copy()
    merged.setdefault("PYTHONPATH", str(ROOT))
    if env:
        merged.update(env)
    cp = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, env=merged)
    if check and cp.returncode != 0:
        raise AssertionError(f"{cmd} failed {cp.returncode}\nSTDOUT:{cp.stdout}\nSTDERR:{cp.stderr}")
    return cp


def g(repo: Path, *args: str, check=True):
    return run(["git", *args], repo, check=check)


def init_repo(tmp_path: Path, *, identity: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    g(repo, "init")
    if identity:
        g(repo, "config", "user.name", "Test User")
        g(repo, "config", "user.email", "test@example.invalid")
    return repo


def commit_all(repo: Path, message="commit") -> str:
    g(repo, "add", "-A")
    g(repo, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid", "commit", "-m", message)
    return g(repo, "rev-parse", "HEAD").stdout.strip()


def write_basic_task(repo: Path, *, target="refs/heads/personal-slice/verified", extra=None, scope=None, patch_text=None):
    (repo / "tasks").mkdir(exist_ok=True)
    (repo / "patches").mkdir(exist_ok=True)
    (repo / "value.txt").write_text("old\n", encoding="utf-8")
    patch = patch_text or """diff --git a/value.txt b/value.txt
index 3b18e51..3e75765 100644
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-old
+new
"""
    (repo / "patches" / "change.patch").write_text(patch, encoding="utf-8")
    data = {
        "task_id": "ps-test",
        "task_class": "TEST",
        "target_ref": target,
        "allowed_scope": scope or {"exact": ["value.txt"], "prefixes": []},
        "patch_path": "patches/change.patch",
        "required_scaffold_paths": ["tasks/task.json", "patches/change.patch"],
        "reproduction": {
            "command": [sys.executable, "-c", "from pathlib import Path; import sys; sys.exit(0 if Path('value.txt').read_text().strip()==sys.argv[1] else 1)", "old"],
            "before": {"expected_exit_codes": [0], "timeout_seconds": 30},
            "after": {"expected_exit_codes": [1], "timeout_seconds": 30},
        },
        "acceptance_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('value.txt').read_text().strip()=='new'"]],
        "full_suite_commands": [[sys.executable, "-c", "pass"]],
        "commit_message": "Apply test change",
    }
    if extra:
        data.update(extra)
    (repo / "tasks" / "task.json").write_text(json.dumps(data), encoding="utf-8")


def run_slice(repo: Path, *args: str):
    return run([sys.executable, "-m", "personal_slice", "run", *args], repo, check=False, env={"PYTHONPATH": str(ROOT)})


def latest_report(repo: Path) -> dict:
    reports = sorted((repo / ".git" / "personal-slice" / "reports").glob("*.json"))
    assert reports
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def test_task_loaded_from_base_commit_ignores_local_modification(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo)
    base = commit_all(repo)
    # Poison the local task after base; runner must ignore this uncommitted file.
    task = json.loads((repo / "tasks" / "task.json").read_text())
    task["target_ref"] = "refs/heads/poisoned"
    (repo / "tasks" / "task.json").write_text(json.dumps(task), encoding="utf-8")
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 0, cp.stderr + cp.stdout
    assert g(repo, "rev-parse", "--verify", "refs/heads/personal-slice/verified").returncode == 0
    assert g(repo, "rev-parse", "--verify", "refs/heads/poisoned", check=False).returncode != 0


@pytest.mark.parametrize("task_arg", ["/abs/task.json", "../task.json", "tasks/../task.json"])
def test_task_path_rejects_absolute_and_traversal(tmp_path, task_arg):
    repo = init_repo(tmp_path)
    write_basic_task(repo)
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", task_arg)
    assert cp.returncode == 30
    report = latest_report(repo)
    assert report["outcome"] == "INTERNAL_ERROR"


def test_final_scope_check_blocks_verification_created_out_of_scope_file(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo, extra={"acceptance_commands": [[sys.executable, "-c", "from pathlib import Path; Path('leak.txt').write_text('oops')"]]})
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 12
    report = latest_report(repo)
    assert report["outcome"] == "VERIFICATION_FAILED"
    assert any(p["name"] == "scope_before_commit" and p["status"] == "FAIL" for p in report["phases"])
    assert g(repo, "rev-parse", "--verify", "refs/heads/personal-slice/verified", check=False).returncode != 0


def test_target_ref_creation_uses_zero_oid_cas(monkeypatch, tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("a")
    base = commit_all(repo)
    verified = base
    calls = []
    from personal_slice import application
    real_git = application.git

    def spy(args, cwd, *, check=True):
        calls.append(args)
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(application, "git", spy)
    result = apply_verified_commit(repo, "refs/heads/new-target", base, verified)
    assert result.status == "APPLIED"
    assert ["update-ref", "refs/heads/new-target", base, ZERO_OID] in calls


def test_race_creating_target_ref_does_not_overwrite(monkeypatch, tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("a")
    base = commit_all(repo, "base")
    (repo / "a.txt").write_text("b")
    other = commit_all(repo, "other")
    from personal_slice import application
    real_git = application.git
    raced = {"done": False}

    def racing_git(args, cwd, *, check=True):
        if args[:3] == ["update-ref", "refs/heads/race", base] and args[3] == ZERO_OID and not raced["done"]:
            raced["done"] = True
            real_git(["update-ref", "refs/heads/race", other, ZERO_OID], cwd)
            return real_git(args, cwd, check=False)
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(application, "git", racing_git)
    result = apply_verified_commit(repo, "refs/heads/race", base, base)
    assert result.status == "APPLICATION_STALE_BASE"
    assert g(repo, "rev-parse", "refs/heads/race").stdout.strip() == other


def test_target_ref_checked_out_in_linked_worktree_is_refused(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("a")
    base = commit_all(repo)
    g(repo, "branch", "target", base)
    linked = tmp_path / "linked"
    g(repo, "worktree", "add", str(linked), "target")
    result = apply_verified_commit(repo, "refs/heads/target", base, base)
    assert result.status == "INTERNAL_ERROR"
    assert "UNSAFE_TARGET_REF_CHECKED_OUT_IN_WORKTREE" in result.diagnostics


def test_evidence_ref_survives_stale_base_and_cleanup(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo)
    base = commit_all(repo, "base")
    g(repo, "update-ref", "refs/heads/personal-slice/verified", base, ZERO_OID)
    (repo / "unrelated.txt").write_text("advance")
    advanced = commit_all(repo, "advance")
    g(repo, "update-ref", "refs/heads/personal-slice/verified", advanced, base)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 20, cp.stdout + cp.stderr
    report = latest_report(repo)
    assert report["outcome"] == "APPLICATION_STALE_BASE"
    assert g(repo, "cat-file", "-e", f"{report['evidence_ref']}^{{commit}}").returncode == 0
    assert not Path(report["worktree_path"]).exists()


def test_verified_commit_works_without_global_identity(tmp_path):
    repo = init_repo(tmp_path, identity=False)
    write_basic_task(repo)
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    report = latest_report(repo)
    assert report["verified_commit"]


def test_reports_default_under_git_dir_and_source_worktree_clean(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo)
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 0
    reports = list((repo / ".git" / "personal-slice" / "reports").glob("*.json"))
    assert reports
    assert not (repo / "personal_slice").exists()
    assert g(repo, "status", "--porcelain").stdout == ""


def test_changed_files_handles_spaces_unicode_rename_and_untracked(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "space name.txt").write_text("a")
    (repo / "unicodé.txt").write_text("u")
    base = commit_all(repo)
    g(repo, "mv", "space name.txt", "renamed space.txt")
    (repo / "unicodé.txt").write_text("v")
    (repo / "untracked file.txt").write_text("x")
    files = changed_files(repo, base)
    assert "renamed space.txt" in files
    assert "space name.txt" in files
    assert "unicodé.txt" in files
    assert "untracked file.txt" in files


def test_baseline_failure_classified_and_patch_not_applied(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo, extra={"baseline_commands": [[sys.executable, "-c", "import sys; sys.exit(7)"]]})
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json")
    assert cp.returncode == 13
    report = latest_report(repo)
    assert report["outcome"] == "BASELINE_PREEXISTING_FAILURE"
    assert all(p["name"] != "apply_patch" for p in report["phases"])
    assert g(repo, "rev-parse", "--verify", "refs/heads/personal-slice/verified", check=False).returncode != 0


def test_historical_task_marked_not_rerunnable():
    readme = (ROOT / "personal_slice" / "examples" / "missing_method" / "README.md").read_text(encoding="utf-8")
    main_readme = (ROOT / "personal_slice" / "README.md").read_text(encoding="utf-8")
    assert "HISTORICAL_EVIDENCE" in readme
    assert "NOT RERUNNABLE AGAINST CURRENT HEAD" in readme
    assert "python -m personal_slice run personal_slice/task.json" not in main_readme


def test_report_contains_v03_evidence_fields(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo)
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json", "--environment-kind", "LOCAL")
    assert cp.returncode == 0
    report = latest_report(repo)
    for key in [
        "task_path", "task_contract_sha256", "patch_sha256", "reproduction_sha256",
        "base_commit", "base_tree", "verified_commit", "verified_tree", "evidence_ref",
        "environment_kind", "worktree_durability", "application_scope", "remote_updated",
    ]:
        assert key in report
    assert report["schema"] == "personal_slice.report/v0.3"
    assert report["environment_kind"] == "LOCAL"
    assert report["application_scope"] == "LOCAL_REF_ONLY"
    assert report["remote_updated"] is False


def test_keep_worktree_preserves_real_worktree_and_cleanup_removes_without_keep(tmp_path):
    repo = init_repo(tmp_path)
    write_basic_task(repo, target="refs/heads/keep-target")
    base = commit_all(repo)
    cp = run_slice(repo, "--base", base, "--task", "tasks/task.json", "--keep-worktree")
    assert cp.returncode == 0
    report = latest_report(repo)
    assert report["worktree_durability"] == "PROCESS_ENVIRONMENT_PRESERVED"
    assert Path(report["worktree_path"]).exists()

    repo2 = init_repo(tmp_path / "second")
    write_basic_task(repo2)
    base2 = commit_all(repo2)
    cp2 = run_slice(repo2, "--base", base2, "--task", "tasks/task.json")
    assert cp2.returncode == 0
    report2 = latest_report(repo2)
    assert report2["worktree_durability"] == "REMOVED"
    assert not Path(report2["worktree_path"]).exists()
    assert g(repo2, "cat-file", "-e", f"{report2['evidence_ref']}^{{commit}}").returncode == 0
