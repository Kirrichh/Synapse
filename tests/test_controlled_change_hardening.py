from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse.change import ControlledChangeRequest
from synapse.change.contract import (
    ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE,
    ALLOWED_SCOPE_DUPLICATE,
    ALLOWED_SCOPE_EMPTY,
    REPRODUCTION_INPUT_DUPLICATE,
    REPRODUCTION_INPUTS_MISSING,
    TASK_CONTRACT_SCHEMA_MISSING,
    TASK_CONTRACT_SCHEMA_UNSUPPORTED,
    parse_task_contract_text,
)
from synapse.change.runner import execute_controlled_change
from synapse.change.verification import PhaseResult
from synapse.change.workspace import (
    ChangeKind,
    ChangedPath,
    build_candidate_snapshot,
    capture_candidate_snapshot,
    changed_paths,
    diff_candidate_snapshots,
    parse_status_z,
)


def run(cmd, cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def task_dict(*, patch_path="patches/change.patch", allowed_scope=None, reproduction_inputs=None, baseline_commands=None):
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "sample-change",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": "refs/heads/controlled/verified",
        "allowed_scope": allowed_scope if allowed_scope is not None else ["app.txt"],
        "patch_path": patch_path,
        "required_scaffold_paths": ["tasks/task.json", patch_path],
        "reproduction": {
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "committed_inputs": [] if reproduction_inputs is None else reproduction_inputs,
            "before": {"expected_exit_codes": [0], "timeout_seconds": 10},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 10},
        },
        "baseline_commands": [] if baseline_commands is None else baseline_commands,
        "acceptance_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "full_suite_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "commit_message": "Apply controlled change",
    }


def init_repo(tmp_path: Path, *, task=None, patch_text=None, extra_files=None) -> tuple[Path, str]:
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")
    (repo / "app.txt").write_bytes(b"old\n")
    (repo / "tasks").mkdir()
    (repo / "patches").mkdir()
    if extra_files:
        for rel, data in extra_files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                path.write_bytes(data)
            else:
                path.write_text(data, encoding="utf-8")
    patch = patch_text or (
        "diff --git a/app.txt b/app.txt\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    (repo / "patches" / "change.patch").write_text(patch, encoding="utf-8")
    task_payload = task or task_dict()
    (repo / "tasks" / "task.json").write_text(json.dumps(task_payload, sort_keys=True), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def read_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_task_schema_required_and_unknown_schema_rejected():
    task = task_dict()
    task.pop("schema")
    with pytest.raises(Exception) as missing:
        parse_task_contract_text(json.dumps(task))
    assert TASK_CONTRACT_SCHEMA_MISSING in str(missing.value)

    task["schema"] = "synapse.controlled-change.task/v2"
    with pytest.raises(Exception) as unsupported:
        parse_task_contract_text(json.dumps(task))
    assert TASK_CONTRACT_SCHEMA_UNSUPPORTED in str(unsupported.value)


def test_committed_inputs_field_required_empty_allowed_and_duplicates_rejected():
    task = task_dict()
    task["reproduction"].pop("committed_inputs")
    with pytest.raises(Exception) as missing:
        parse_task_contract_text(json.dumps(task))
    assert REPRODUCTION_INPUTS_MISSING in str(missing.value)

    parsed = parse_task_contract_text(json.dumps(task_dict(reproduction_inputs=[])))
    assert parsed.reproduction.committed_inputs == ()

    task = task_dict(reproduction_inputs=["input.txt", "input.txt"])
    with pytest.raises(Exception) as duplicate:
        parse_task_contract_text(json.dumps(task))
    assert REPRODUCTION_INPUT_DUPLICATE in str(duplicate.value)

    task = task_dict(reproduction_inputs=["../input.txt"])
    with pytest.raises(Exception) as invalid:
        parse_task_contract_text(json.dumps(task))
    assert "REPRODUCTION_INPUTS_INVALID" in str(invalid.value)


def test_allowed_scope_legacy_explicit_prefix_and_duplicate_validation():
    legacy = parse_task_contract_text(json.dumps(task_dict(allowed_scope=["app.txt"])))
    assert legacy.allowed_scope.allows_path("app.txt")
    assert not legacy.allowed_scope.allows_path("app2.txt")

    explicit = parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": ["app.txt"], "prefixes": ["tests/change/"]})))
    assert explicit.allowed_scope.allows_path("tests/change/test_one.py")
    assert explicit.allowed_scope.allows_path("tests/change/sub/test_two.py")
    assert not explicit.allowed_scope.allows_path("tests/change_extra/test_one.py")

    with pytest.raises(Exception) as empty:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": [], "prefixes": []})))
    assert ALLOWED_SCOPE_EMPTY in str(empty.value)

    with pytest.raises(Exception) as duplicate:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope=["app.txt", "app.txt"])))
    assert ALLOWED_SCOPE_DUPLICATE in str(duplicate.value)

    with pytest.raises(Exception) as overlap:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": ["app.txt"], "prefixes": ["app.txt/"]})))
    assert ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE in str(overlap.value)


def test_baseline_commands_default_empty_and_preserve_order():
    parsed = parse_task_contract_text(json.dumps(task_dict()))
    assert parsed.baseline_commands == ()
    parsed = parse_task_contract_text(json.dumps(task_dict(baseline_commands=[["python", "-V"], ["python", "-c", "print(1)"]])))
    assert parsed.baseline_commands == (("python", "-V"), ("python", "-c", "print(1)"))


def test_trusted_hashes_and_working_tree_poisoning_are_ignored(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(reproduction_inputs=["input.bin"]), extra_files={"input.bin": b"abc\x00def"})
    task_bytes = (repo / "tasks" / "task.json").read_bytes()
    patch_bytes = (repo / "patches" / "change.patch").read_bytes()
    expected_repro = hashlib.sha256(b"input.bin\0" + b"abc\x00def" + b"\0").hexdigest()
    (repo / "tasks" / "task.json").write_text("poison", encoding="utf-8")
    (repo / "patches" / "change.patch").write_text("poison", encoding="utf-8")
    (repo / "input.bin").write_bytes(b"poison")
    monkeypatch.chdir(repo)

    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))

    assert result.outcome == "APPLIED"
    assert result.trusted_inputs
    assert result.trusted_inputs.task_contract_sha256 == hashlib.sha256(task_bytes).hexdigest()
    assert result.trusted_inputs.patch_sha256 == hashlib.sha256(patch_bytes).hexdigest()
    assert result.trusted_inputs.reproduction_sha256 == expected_repro
    report = read_report(result.report_path)
    assert report["trusted_inputs"]["task_contract_sha256"] == hashlib.sha256(task_bytes).hexdigest()
    assert report["trusted_inputs"]["reproduction_sha256"] == expected_repro


def test_missing_patch_and_reproduction_input_are_rejected_independently(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(patch_path="patches/missing.patch"))
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "INTERNAL_ERROR"
    assert result.failure_code == "PATCH_PATH_NOT_REGULAR_FILE"

    repo2, base2 = init_repo(tmp_path / "repo2", task=task_dict(reproduction_inputs=["missing.txt"]))
    monkeypatch.chdir(repo2)
    result = execute_controlled_change(ControlledChangeRequest(base=base2, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "INTERNAL_ERROR"
    assert result.failure_code == "REPRODUCTION_INPUTS_INVALID"


def test_task_and_patch_symlinks_rejected_and_reproduction_symlink_hashes_link_target(tmp_path, monkeypatch):
    repo = tmp_path / "task-link"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "tasks").mkdir()
    (repo / "real.json").write_text(json.dumps(task_dict()), encoding="utf-8")
    os.symlink("../real.json", repo / "tasks" / "task.json")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.failure_code == "TASK_PATH_NOT_REGULAR_FILE"

    repo2, base2 = init_repo(tmp_path / "patch-link")
    (repo2 / "patches" / "real.patch").write_text((repo2 / "patches" / "change.patch").read_text(encoding="utf-8"), encoding="utf-8")
    (repo2 / "patches" / "change.patch").unlink()
    os.symlink("real.patch", repo2 / "patches" / "change.patch")
    git(repo2, "add", "patches")
    git(repo2, "commit", "-m", "patch symlink")
    base2 = git(repo2, "rev-parse", "HEAD")
    monkeypatch.chdir(repo2)
    result = execute_controlled_change(ControlledChangeRequest(base=base2, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.failure_code == "PATCH_PATH_NOT_REGULAR_FILE"

    repo3, base3 = init_repo(tmp_path / "repro-link", task=task_dict(reproduction_inputs=["input.link"]))
    os.symlink("target.txt", repo3 / "input.link")
    git(repo3, "add", "input.link")
    git(repo3, "commit", "-m", "symlink input")
    base3 = git(repo3, "rev-parse", "HEAD")
    monkeypatch.chdir(repo3)
    result = execute_controlled_change(ControlledChangeRequest(base=base3, task_path="tasks/task.json", environment_kind="TEST"))
    expected = hashlib.sha256(b"input.link\0target.txt\0").hexdigest()
    assert result.trusted_inputs.reproduction_sha256 == expected


def test_changed_path_parser_preserves_spaces_tabs_newlines_unicode_and_synthetic_copy():
    output = b"?? space name.txt\0?? tab\tname.txt\0?? line\nname.txt\0?? \xd1\x84.txt\0C  copy-dest.txt\0copy-source.txt\0"
    changes = parse_status_z(output)
    assert [change.new_path for change in changes[:4]] == ["space name.txt", "tab\tname.txt", "line\nname.txt", "ф.txt"]
    assert changes[4].kind is ChangeKind.COPIED
    assert changes[4].old_path == "copy-source.txt"
    assert changes[4].new_path == "copy-dest.txt"


def test_real_git_rename_layout_tracks_old_and_new_paths(tmp_path):
    repo = tmp_path / "rename"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "old.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "old.txt")
    git(repo, "commit", "-m", "base")
    git(repo, "mv", "old.txt", "new.txt")
    changes = changed_paths(repo)
    assert len(changes) == 1
    assert changes[0].kind is ChangeKind.RENAMED
    assert changes[0].old_path == "old.txt"
    assert changes[0].new_path == "new.txt"


def test_changed_path_deleted_untracked_staged_unstaged_and_type_changed(tmp_path):
    repo = tmp_path / "status"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "file.txt").write_text("x\n", encoding="utf-8")
    (repo / "type.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    (repo / "file.txt").unlink()
    (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    (repo / "staged.txt").write_text("s\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "type.txt").unlink()
    os.symlink("target", repo / "type.txt")
    git(repo, "add", "type.txt")
    changes = {change.new_path: change for change in changed_paths(repo)}
    assert changes["file.txt"].kind is ChangeKind.DELETED
    assert changes["untracked.txt"].kind is ChangeKind.UNTRACKED
    assert changes["staged.txt"].index_status == "A"
    assert changes["type.txt"].kind is ChangeKind.TYPE_CHANGED


def test_scope_exact_prefix_rename_delete_and_copy_semantics(tmp_path, monkeypatch):
    rename_patch = (
        "diff --git a/old.txt b/new.txt\n"
        "similarity index 100%\n"
        "rename from old.txt\n"
        "rename to new.txt\n"
    )
    task = task_dict(patch_text if False else None) if False else task_dict(
        allowed_scope={"exact": ["old.txt", "new.txt"], "prefixes": ["src"]},
    )
    task["patch_path"] = "patches/change.patch"
    task["required_scaffold_paths"] = ["tasks/task.json", "patches/change.patch"]
    repo, base = init_repo(tmp_path, task=task, patch_text=rename_patch, extra_files={"old.txt": "same\n"})
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "APPLIED"

    changes = (
        ChangedPath("C", " ", ChangeKind.COPIED, "src/source.txt", "src/dest.txt", True),
        ChangedPath("D", " ", ChangeKind.DELETED, None, "old.txt", True),
    )
    from synapse.change.runner import _scope_diagnostics
    parsed = parse_task_contract_text(json.dumps(task))
    assert _scope_diagnostics(changes, parsed) == []

    bad = (ChangedPath("R", " ", ChangeKind.RENAMED, "secret.txt", "new.txt", True),)
    assert any("OUT_OF_SCOPE_RENAME_SOURCE" in diag for diag in _scope_diagnostics(bad, parsed))


def test_baseline_failures_short_circuit_cleanup_and_report(tmp_path, monkeypatch):
    task = task_dict(baseline_commands=[[sys.executable, "-c", "raise SystemExit(3)"]])
    repo, base = init_repo(tmp_path, task=task)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    names = [phase.name for phase in result.phases]
    assert result.outcome == "BASELINE_PREEXISTING_FAILURE"
    assert "baseline_1" in names
    assert "apply_patch_check" not in names
    assert result.cleanup_status == "REMOVED"
    report = read_report(result.report_path)
    assert report["failure"]["failure_phase"] == "baseline_1"
    assert report["cleanup"]["cleanup_status"] == "REMOVED"


def test_baseline_mutations_for_tracked_staged_and_untracked(tmp_path, monkeypatch):
    cases = {
        "tracked": "from pathlib import Path; Path('app.txt').write_text('mutated\\n')",
        "staged": "from pathlib import Path; import subprocess; Path('staged.txt').write_text('s\\n'); subprocess.run(['git','add','staged.txt'], check=True)",
        "untracked": "from pathlib import Path; Path('untracked.txt').write_text('u\\n')",
    }
    for name, script in cases.items():
        repo, base = init_repo(tmp_path / name, task=task_dict(baseline_commands=[[sys.executable, "-c", script]]))
        monkeypatch.chdir(repo)
        result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
        assert result.outcome == "BASELINE_MUTATED_WORKTREE"
        assert result.failure_phase == "baseline_integrity"
        assert result.baseline["mutation_paths"]


def test_initial_dirty_worktree_returns_internal_error(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    import synapse.change.runner as runner
    real_create = runner.create_detached_worktree

    def dirty_create(repo_root, base_revision):
        worktree = real_create(repo_root, base_revision)
        (worktree.path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        return worktree

    monkeypatch.setattr(runner, "create_detached_worktree", dirty_create)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "INTERNAL_ERROR"
    assert result.failure_code == "INITIAL_WORKTREE_NOT_CLEAN"


def test_baseline_timeout_maps_to_preexisting_failure(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(baseline_commands=[[sys.executable, "-c", "print('baseline')"]]))
    import synapse.change.runner as runner

    real_run_command = runner.run_command

    def timeout_run(command, cwd, phase_name, **kwargs):
        if phase_name.startswith("baseline_"):
            return PhaseResult(phase_name, "FAIL", list(command), None, "", "", 1, ["timeout after 60s"])
        return real_run_command(command, cwd, phase_name, **kwargs)

    monkeypatch.setattr(runner, "run_command", timeout_run)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "BASELINE_PREEXISTING_FAILURE"
    assert result.failure_phase == "baseline_1"


def test_reproduction_before_expectation_failure_returns_preexisting_failure(tmp_path, monkeypatch):
    task = task_dict()
    task["reproduction"]["before"] = {"expected_exit_codes": [7], "timeout_seconds": 10}
    repo, base = init_repo(tmp_path, task=task)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "BASELINE_PREEXISTING_FAILURE"
    assert result.failure_phase == "reproduction_before"


def test_candidate_snapshot_regular_binary_symlink_deleted_order_and_diff(tmp_path):
    repo = tmp_path / "snapshot"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "regular.txt").write_bytes(b"old")
    (repo / "binary.bin").write_bytes(b"\x00\xff")
    (repo / "delete.txt").write_text("bye", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    (repo / "regular.txt").write_bytes(b"new")
    (repo / "binary.bin").write_bytes(b"\x00\xfe")
    (repo / "delete.txt").unlink()
    os.symlink("external-target", repo / "link.txt")
    snap = capture_candidate_snapshot(repo)
    kinds = {entry.new_path: entry.object_kind for entry in snap.entries}
    assert kinds["regular.txt"] == "REGULAR_FILE"
    assert kinds["binary.bin"] == "REGULAR_FILE"
    assert kinds["delete.txt"] == "DELETED"
    assert kinds["link.txt"] == "SYMLINK"
    assert [entry.canonical_key() for entry in snap.entries] == sorted(entry.canonical_key() for entry in snap.entries)

    before = build_candidate_snapshot(repo, (ChangedPath("M", " ", ChangeKind.MODIFIED, None, "regular.txt", True),))
    (repo / "regular.txt").write_text("changed again", encoding="utf-8")
    after = build_candidate_snapshot(repo, (ChangedPath("M", " ", ChangeKind.MODIFIED, None, "regular.txt", True), ChangedPath("A", " ", ChangeKind.ADDED, None, "added.txt", True)))
    diff = diff_candidate_snapshots(before, after)
    assert diff.added
    assert diff.changed
    assert any("CANDIDATE_PATH_ADDED" in diag for diag in diff.diagnostics())
    assert any("CANDIDATE_PATH_CHANGED" in diag for diag in diff.diagnostics())


def test_symlink_candidate_digest_uses_link_target_not_external_contents(tmp_path):
    repo = tmp_path / "symlink-snapshot"
    repo.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("secret-v1", encoding="utf-8")
    os.symlink(str(external), repo / "link.txt")
    change = ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "link.txt", False)
    snap1 = build_candidate_snapshot(repo, (change,))
    external.write_text("secret-v2", encoding="utf-8")
    snap2 = build_candidate_snapshot(repo, (change,))
    assert snap1.entries[0].content_sha256 == snap2.entries[0].content_sha256
    assert snap1.entries[0].content_sha256 == hashlib.sha256(str(external).encode()).hexdigest()


def test_duplicate_candidate_identity_is_rejected(tmp_path):
    repo = tmp_path / "duplicate"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    duplicate = (
        ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "a.txt", False),
        ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "a.txt", False),
    )
    with pytest.raises(Exception) as exc:
        build_candidate_snapshot(repo, duplicate)
    assert "DUPLICATE_CANDIDATE_IDENTITY" in str(exc.value)


def test_report_v040_contains_provenance_and_phase_state_consistency(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(reproduction_inputs=[]))
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    report = read_report(result.report_path)
    assert report["schema"] == "personal_slice.report/v0.4.0"
    assert report["task"]["task_schema"] == "synapse.controlled-change.task/v1"
    assert report["task"]["task_path"] == "tasks/task.json"
    assert report["trusted_inputs"]["patch_sha256"] == result.trusted_inputs.patch_sha256
    assert report["trusted_inputs"]["reproduction_sha256"] == hashlib.sha256(b"").hexdigest()
    assert report["trusted_inputs"]["base_tree"]
    assert report["baseline"]["initial_integrity_status"] == "PASS"
    assert report["baseline"]["status"] == "PASS"
    assert report["candidate"]["integrity_status"] == "PASS"
    assert report["verified_result"]["status"] == "COMPLETED_LEGACY_SEMANTICS"
    assert report["evidence"]["status"] == "COMPLETED_LEGACY_SEMANTICS"
    assert report["application"]["status"] == "COMPLETED_LEGACY_SEMANTICS"
    assert report["verified_result"]["verified_commit"] == result.verified_commit
    assert report["failure"]["failure_phase"] is None


def test_report_not_attempted_for_skipped_phases_after_baseline_failure(tmp_path, monkeypatch):
    task = task_dict(baseline_commands=[[sys.executable, "-c", "raise SystemExit(3)"]])
    repo, base = init_repo(tmp_path, task=task)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    report = read_report(result.report_path)
    assert report["verified_result"]["status"] == "NOT_ATTEMPTED"
    assert report["evidence"]["status"] == "NOT_ATTEMPTED"
    assert report["application"]["status"] == "NOT_ATTEMPTED"
    assert report["failure"]["failure_phase"] == "baseline_1"
