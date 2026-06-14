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
    TASK_CONTRACT_UNKNOWN_FIELD,
    GIT_OBSERVED_PATH_INVALID,
    TaskContractError,
    normalize_contract_repo_path,
    parse_task_contract_text,
    validate_git_observed_path,
)
from synapse.change.runner import _scope_diagnostics, execute_controlled_change
from synapse.change.verification import PhaseResult
from synapse.change.workspace import (
    ChangeKind,
    ChangedPath,
    CandidateSnapshot,
    CandidateSnapshotEntry,
    build_candidate_snapshot,
    capture_candidate_snapshot,
    changed_paths,
    diff_candidate_snapshots,
    parse_status_z,
    GitWorkspaceError,
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
    if isinstance(patch, bytes):
        (repo / "patches" / "change.patch").write_bytes(patch)
    else:
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
    with pytest.raises(TaskContractError) as missing:
        parse_task_contract_text(json.dumps(task))
    assert missing.value.code == TASK_CONTRACT_SCHEMA_MISSING

    task["schema"] = "synapse.controlled-change.task/v2"
    with pytest.raises(TaskContractError) as unsupported:
        parse_task_contract_text(json.dumps(task))
    assert unsupported.value.code == TASK_CONTRACT_SCHEMA_UNSUPPORTED


def test_committed_inputs_field_required_empty_allowed_and_duplicates_rejected():
    task = task_dict()
    task["reproduction"].pop("committed_inputs")
    with pytest.raises(TaskContractError) as missing:
        parse_task_contract_text(json.dumps(task))
    assert missing.value.code == REPRODUCTION_INPUTS_MISSING

    parsed = parse_task_contract_text(json.dumps(task_dict(reproduction_inputs=[])))
    assert parsed.reproduction.committed_inputs == ()

    task = task_dict(reproduction_inputs=["input.txt", "input.txt"])
    with pytest.raises(TaskContractError) as duplicate:
        parse_task_contract_text(json.dumps(task))
    assert duplicate.value.code == REPRODUCTION_INPUT_DUPLICATE

    task = task_dict(reproduction_inputs=["../input.txt"])
    with pytest.raises(TaskContractError) as invalid:
        parse_task_contract_text(json.dumps(task))
    assert invalid.value.code == "REPRODUCTION_INPUTS_INVALID"


def test_allowed_scope_legacy_explicit_prefix_and_duplicate_validation():
    legacy = parse_task_contract_text(json.dumps(task_dict(allowed_scope=["app.txt"])))
    assert legacy.allowed_scope.allows_path("app.txt")
    assert not legacy.allowed_scope.allows_path("app2.txt")

    explicit = parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": ["app.txt"], "prefixes": ["tests/change/"]})))
    assert explicit.allowed_scope.allows_path("tests/change/test_one.py")
    assert explicit.allowed_scope.allows_path("tests/change/sub/test_two.py")
    assert not explicit.allowed_scope.allows_path("tests/change_extra/test_one.py")

    with pytest.raises(TaskContractError) as empty:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": [], "prefixes": []})))
    assert empty.value.code == ALLOWED_SCOPE_EMPTY

    with pytest.raises(TaskContractError) as duplicate:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope=["app.txt", "app.txt"])))
    assert duplicate.value.code == ALLOWED_SCOPE_DUPLICATE

    with pytest.raises(TaskContractError) as overlap:
        parse_task_contract_text(json.dumps(task_dict(allowed_scope={"exact": ["app.txt"], "prefixes": ["app.txt/"]})))
    assert overlap.value.code == ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE


def test_baseline_commands_default_empty_and_preserve_order():
    parsed = parse_task_contract_text(json.dumps(task_dict()))
    assert parsed.baseline_commands == ()
    parsed = parse_task_contract_text(json.dumps(task_dict(baseline_commands=[["python", "-V"], ["python", "-c", "print(1)"]])))
    assert parsed.baseline_commands == (("python", "-V"), ("python", "-c", "print(1)"))


def test_trusted_hashes_and_working_tree_poisoning_are_ignored(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(reproduction_inputs=["input.bin"]), extra_files={"input.bin": b"abc\x00def"})
    task_bytes = (repo / "tasks" / "task.json").read_bytes()
    blob_oid = git(repo, "rev-parse", f"{base}:patches/change.patch")
    committed_patch = subprocess.run(
        ["git", "cat-file", "blob", blob_oid],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    expected_patch_hash = hashlib.sha256(committed_patch).hexdigest()
    expected_repro = hashlib.sha256(b"input.bin\0" + b"abc\x00def" + b"\0").hexdigest()
    (repo / "tasks" / "task.json").write_text("poison", encoding="utf-8")
    (repo / "patches" / "change.patch").write_text("poison", encoding="utf-8")
    (repo / "input.bin").write_bytes(b"poison")
    poisoned_patch_hash = hashlib.sha256((repo / "patches" / "change.patch").read_bytes()).hexdigest()
    assert expected_patch_hash != poisoned_patch_hash
    monkeypatch.chdir(repo)

    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))

    assert result.outcome == "APPLIED"
    assert result.trusted_inputs
    assert result.trusted_inputs.task_contract_sha256 == hashlib.sha256(task_bytes).hexdigest()
    assert result.trusted_inputs.patch_sha256 == expected_patch_hash
    assert result.trusted_inputs.patch_sha256 != poisoned_patch_hash
    assert result.trusted_inputs.reproduction_sha256 == expected_repro
    report = read_report(result.report_path)
    assert report["trusted_inputs"]["task_contract_sha256"] == hashlib.sha256(task_bytes).hexdigest()
    assert report["trusted_inputs"]["patch_sha256"] == expected_patch_hash
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
    task = task_dict(allowed_scope={"exact": ["old.txt", "new.txt"], "prefixes": ["src"]})
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
    assert [entry.canonical_ordering_key() for entry in snap.entries] == sorted(entry.canonical_ordering_key() for entry in snap.entries)

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
    with pytest.raises(GitWorkspaceError) as exc:
        build_candidate_snapshot(repo, duplicate)
    assert "DUPLICATE_CANDIDATE_IDENTITY" in str(exc.value)


def test_report_v050_contains_provenance_and_phase_state_consistency(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(reproduction_inputs=[]))
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    report = read_report(result.report_path)
    assert report["schema"] == "personal_slice.report/v0.5.0"
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
    assert report["lifecycle"]["outcome"] == report["outcome"] == result.outcome
    assert report["lifecycle"]["exit_code"] == result.exit_code
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


def test_contract_path_rejects_platform_absolute_and_drive_qualified_paths():
    rejected = [
        "/absolute/path",
        "C:/outside/path",
        r"C:\outside\path",
        r"C:relative-but-drive-qualified",
        "c:/outside/path",
        r"\\server\share\path",
        "//server/share/path",
        "../outside",
        "a/../../outside",
        "a/../outside",
    ]
    for raw_path in rejected:
        with pytest.raises(TaskContractError):
            normalize_contract_repo_path(raw_path, "contract_path")


def test_contract_path_normalizes_backslashes_for_repository_contract():
    assert normalize_contract_repo_path("tasks/task.json", "contract_path") == "tasks/task.json"
    assert normalize_contract_repo_path(r"tasks\task.json", "contract_path") == "tasks/task.json"
    assert normalize_contract_repo_path("dir/sub/file.txt", "contract_path") == "dir/sub/file.txt"
    assert normalize_contract_repo_path(r"dir\sub\file.txt", "contract_path") == "dir/sub/file.txt"


def test_base_revision_is_required_even_when_resolved_override_is_supplied():
    task = task_dict()
    task.pop("base_revision")
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task), base_revision="0123456789abcdef0123456789abcdef01234567")
    assert exc.value.code == "BASE_REVISION_INVALID"


def test_empty_base_revision_is_rejected_even_when_override_is_supplied():
    task = task_dict()
    task["base_revision"] = ""
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task), base_revision="0123456789abcdef0123456789abcdef01234567")
    assert exc.value.code == "BASE_REVISION_INVALID"


def test_declared_base_revision_is_validated_before_effective_override():
    task = task_dict()
    task["base_revision"] = "HEAD"
    parsed = parse_task_contract_text(json.dumps(task), base_revision="0123456789abcdef0123456789abcdef01234567")
    assert parsed.base_revision == "0123456789abcdef0123456789abcdef01234567"


def test_git_observed_backslash_path_is_not_normalized():
    assert validate_git_observed_path(r"allowed\file.txt", "git_path") == r"allowed\file.txt"
    assert validate_git_observed_path(r"allowed\file.txt", "git_path") != "allowed/file.txt"
    parsed = parse_task_contract_text(json.dumps(task_dict(allowed_scope=[r"allowed\file.txt"])))
    assert parsed.allowed_scope.exact == ("allowed/file.txt",)
    assert not parsed.allowed_scope.allows_path(r"allowed\file.txt")
    with pytest.raises(TaskContractError) as invalid:
        validate_git_observed_path("/absolute.txt", "git_path")
    assert invalid.value.code == GIT_OBSERVED_PATH_INVALID
    with pytest.raises(TaskContractError) as nul:
        validate_git_observed_path("bad\0path", "git_path")
    assert nul.value.code == GIT_OBSERVED_PATH_INVALID
    with pytest.raises(TaskContractError) as traversal:
        validate_git_observed_path("a/../b", "git_path")
    assert traversal.value.code == GIT_OBSERVED_PATH_INVALID


def test_unknown_root_task_field_is_rejected():
    task = task_dict()
    task["unexpected"] = True
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task))
    assert exc.value.code == TASK_CONTRACT_UNKNOWN_FIELD
    assert "task" in str(exc.value)
    assert "unexpected" in str(exc.value)


def test_unknown_allowed_scope_field_is_rejected():
    task = task_dict(allowed_scope={"exact": ["app.txt"], "prefixes": [], "extra": []})
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task))
    assert exc.value.code == TASK_CONTRACT_UNKNOWN_FIELD
    assert "allowed_scope" in str(exc.value)
    assert "extra" in str(exc.value)


def test_unknown_reproduction_field_is_rejected():
    task = task_dict()
    task["reproduction"]["extra"] = True
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task))
    assert exc.value.code == TASK_CONTRACT_UNKNOWN_FIELD
    assert "reproduction" in str(exc.value)
    assert "extra" in str(exc.value)


def test_unknown_before_field_is_rejected():
    task = task_dict()
    task["reproduction"]["before"]["extra"] = True
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task))
    assert exc.value.code == TASK_CONTRACT_UNKNOWN_FIELD
    assert "reproduction.before" in str(exc.value)
    assert "extra" in str(exc.value)


def test_unknown_after_field_is_rejected():
    task = task_dict()
    task["reproduction"]["after"]["extra"] = True
    with pytest.raises(TaskContractError) as exc:
        parse_task_contract_text(json.dumps(task))
    assert exc.value.code == TASK_CONTRACT_UNKNOWN_FIELD
    assert "reproduction.after" in str(exc.value)
    assert "extra" in str(exc.value)


def test_real_git_ls_tree_z_modes_and_exact_paths(tmp_path):
    from synapse.change.workspace import REGULAR_BLOB_MODES, REPRODUCTION_INPUT_MODES, ls_tree_entry, require_tree_mode

    repo = tmp_path / "lstree"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "regular.txt").write_text("regular", encoding="utf-8")
    (repo / "exec.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(repo / "exec.sh", 0o755)
    os.symlink("regular.txt", repo / "link.txt")
    (repo / "dir").mkdir()
    (repo / "dir" / "nested.txt").write_text("nested", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "modes")
    base = git(repo, "rev-parse", "HEAD")

    assert require_tree_mode(repo, base, "regular.txt", REGULAR_BLOB_MODES, "REGULAR").mode == "100644"
    assert require_tree_mode(repo, base, "exec.sh", REGULAR_BLOB_MODES, "REGULAR").mode == "100755"
    assert ls_tree_entry(repo, base, "link.txt").mode == "120000"
    assert require_tree_mode(repo, base, "link.txt", REPRODUCTION_INPUT_MODES, "REPRO").mode == "120000"
    with pytest.raises(TaskContractError) as symlink_rejected:
        require_tree_mode(repo, base, "link.txt", REGULAR_BLOB_MODES, "PATCH_PATH_NOT_REGULAR_FILE")
    assert symlink_rejected.value.code == "PATCH_PATH_NOT_REGULAR_FILE"
    assert ls_tree_entry(repo, base, "dir").mode == "040000"
    with pytest.raises(TaskContractError) as dir_rejected:
        require_tree_mode(repo, base, "dir", REGULAR_BLOB_MODES, "TASK_PATH_NOT_REGULAR_FILE")
    assert dir_rejected.value.code == "TASK_PATH_NOT_REGULAR_FILE"


def test_real_git_status_z_preserves_special_pathnames_and_backslash(tmp_path):
    repo = tmp_path / "special-status"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    names = ["space name.txt", "tab\tname.txt", "line\nname.txt", r"allowed\file.txt", "файл.txt"]
    for name in names:
        (repo / name).write_text("x", encoding="utf-8")
    changes = changed_paths(repo)
    observed = {change.new_path for change in changes}
    assert set(names) <= observed
    assert r"allowed\file.txt" in observed
    assert "allowed/file.txt" not in observed



def test_backslash_path_created_by_patch_is_out_of_scope(tmp_path, monkeypatch):
    patch = (
        "diff --git a/allowed\\file.txt b/allowed\\file.txt\n"
        "new file mode 100644\n"
        "index 0000000..45b983b\n"
        "--- /dev/null\n"
        "+++ b/allowed\\file.txt\n"
        "@@ -0,0 +1 @@\n"
        "+x\n"
    )
    task = task_dict(allowed_scope=["allowed/file.txt"])
    repo, base = init_repo(tmp_path, task=task, patch_text=patch)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    if os.name == "nt":
        assert result.outcome == "PATCH_REJECTED"
        assert result.failure_phase == "apply_patch_check"
        phase = next(phase for phase in result.phases if phase.name == "apply_patch_check")
        assert phase.status == "FAIL"
        assert phase.diagnostics
        assert any(r"allowed\file.txt" in diagnostic for diagnostic in [*phase.diagnostics, phase.stderr])
        assert all(phase.name != "scope_check_after_patch" for phase in result.phases)
    elif os.name == "posix":
        assert result.outcome == "VERIFICATION_FAILED"
        assert result.failure_phase == "scope_check_after_patch"
        assert any(r"allowed\file.txt" in phase.diagnostics[0] for phase in result.phases if phase.name == "scope_check_after_patch")
    else:
        raise AssertionError(f"unsupported os.name: {os.name}")


def test_scope_diagnostics_treats_backslash_path_as_distinct_from_slash_path():
    task = parse_task_contract_text(json.dumps(task_dict(allowed_scope=["allowed/file.txt"])))
    change_kind = ChangeKind.ADDED
    backslash_change = ChangedPath("?", "?", change_kind, None, r"allowed\file.txt", False)
    slash_change = ChangedPath("?", "?", change_kind, None, "allowed/file.txt", False)

    assert _scope_diagnostics((backslash_change,), task) == [
        fr"OUT_OF_SCOPE_PATH: {change_kind.value}: allowed\file.txt"
    ]
    assert _scope_diagnostics((slash_change,), task) == []


def test_candidate_snapshot_digest_is_canonical_and_stable_across_order_and_hash_seed(tmp_path):
    repo = tmp_path / "digest"
    repo.mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    changes1 = (
        ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "b.txt", False),
        ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "a.txt", False),
    )
    changes2 = tuple(reversed(changes1))
    summary1 = build_candidate_snapshot(repo, changes1).summary()
    summary2 = build_candidate_snapshot(repo, changes2).summary()
    assert summary1["algorithm"] == "candidate_snapshot_sha256/v1"
    assert summary1["summary_sha256"] == summary2["summary_sha256"]

    env = os.environ.copy()
    code = (
        "from pathlib import Path; "
        "from synapse.change.workspace import ChangedPath, ChangeKind, build_candidate_snapshot; "
        "repo=Path('.'); "
        "print(build_candidate_snapshot(repo, (ChangedPath('?', '?', ChangeKind.UNTRACKED, None, 'b.txt', False), ChangedPath('?', '?', ChangeKind.UNTRACKED, None, 'a.txt', False))).summary()['summary_sha256'])"
    )
    values = []
    for seed in ("1", "98765"):
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        completed = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, text=True, capture_output=True, check=True)
        values.append(completed.stdout.strip())
    assert values == [summary1["summary_sha256"], summary1["summary_sha256"]]


def test_candidate_snapshot_digest_changes_for_content_status_path_and_object_kind(tmp_path):
    repo = tmp_path / "digest-change"
    repo.mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    base = build_candidate_snapshot(repo, (ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "a.txt", False),)).summary()["summary_sha256"]
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    content = build_candidate_snapshot(repo, (ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "a.txt", False),)).summary()["summary_sha256"]
    assert content != base
    status = build_candidate_snapshot(repo, (ChangedPath("A", " ", ChangeKind.ADDED, None, "a.txt", True),)).summary()["summary_sha256"]
    assert status != content
    (repo / "b.txt").write_text("changed", encoding="utf-8")
    path_digest = build_candidate_snapshot(repo, (ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "b.txt", False),)).summary()["summary_sha256"]
    assert path_digest != content
    (repo / "link.txt").symlink_to("a.txt")
    object_kind = build_candidate_snapshot(repo, (ChangedPath("?", "?", ChangeKind.UNTRACKED, None, "link.txt", False),)).summary()["summary_sha256"]
    assert object_kind != path_digest


def test_report_compatibility_projections_match_structured_sections(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path, task=task_dict(reproduction_inputs=[]))
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    report = read_report(result.report_path)
    assert report["run_id"] == report["run"]["run_id"] == result.run_id
    assert report["task_id"] == report["task"]["task_id"]
    assert report["task_class"] == report["task"]["task_class"]
    assert report["target_ref"] == report["task"]["target_ref"] == result.target_ref
    assert report["base_revision"] == report["trusted_inputs"]["base_commit"] == result.base_revision
    assert report["environment_kind"] == report["run"]["environment_kind"] == result.environment_kind
    assert report["verified_commit"] == report["verified_result"]["verified_commit"] == result.verified_commit
    assert report["verified_tree"] == report["verified_result"]["verified_tree"] == result.verified_tree
    assert report["evidence_ref"] == report["evidence"]["evidence_ref"] == result.evidence_ref == result.evidence.evidence_ref
    assert report["outcome"] == report["lifecycle"]["outcome"] == result.outcome
    assert report["lifecycle"]["exit_code"] == result.exit_code
    assert report["cleanup_status"] == report["cleanup"]["cleanup_status"] == result.cleanup_status


def _entry(
    *,
    path="app.txt",
    old_path=None,
    kind=ChangeKind.MODIFIED,
    index_status="M",
    worktree_status=" ",
    tracked=True,
    object_kind="REGULAR_FILE",
    content_sha256="aaa",
):
    return CandidateSnapshotEntry(
        kind=kind,
        index_status=index_status,
        worktree_status=worktree_status,
        old_path=old_path,
        new_path=path,
        tracked=tracked,
        object_kind=object_kind,
        content_sha256=content_sha256,
    )


def test_direct_candidate_snapshot_rejects_duplicate_transition_identity():
    entry_a = _entry(
        path="app.txt",
        kind=ChangeKind.MODIFIED,
        index_status="M",
        worktree_status=" ",
        tracked=True,
        object_kind="REGULAR_FILE",
        content_sha256="aaa",
    )
    entry_b = _entry(
        path="app.txt",
        kind=ChangeKind.MODIFIED,
        index_status=" ",
        worktree_status="M",
        tracked=True,
        object_kind="REGULAR_FILE",
        content_sha256="bbb",
    )
    with pytest.raises(GitWorkspaceError) as exc:
        CandidateSnapshot((entry_a, entry_b))
    message = str(exc.value)
    assert "DUPLICATE_CANDIDATE_IDENTITY" in message
    assert "old_path=None" in message
    assert "new_path='app.txt'" in message
    assert "kind='MODIFIED'" in message


def test_direct_candidate_snapshot_allows_distinct_transition_identities():
    snapshot = CandidateSnapshot((
        _entry(path="app.txt", kind=ChangeKind.MODIFIED),
        _entry(path="other.txt", kind=ChangeKind.MODIFIED, content_sha256="bbb"),
        _entry(path="new.txt", old_path="old.txt", kind=ChangeKind.RENAMED, content_sha256="ccc"),
    ))
    assert tuple(entry.transition_key() for entry in snapshot.entries) == (
        (None, "app.txt", "MODIFIED"),
        (None, "other.txt", "MODIFIED"),
        ("old.txt", "new.txt", "RENAMED"),
    )


def test_candidate_snapshot_summary_is_independent_of_direct_constructor_order():
    entry_a = _entry(path="a.txt", content_sha256="a")
    entry_b = _entry(path="b.txt", content_sha256="b")
    summary_a = CandidateSnapshot((entry_a, entry_b)).summary()
    summary_b = CandidateSnapshot((entry_b, entry_a)).summary()
    assert summary_a["algorithm"] == summary_b["algorithm"] == "candidate_snapshot_sha256/v1"
    assert summary_a["entry_count"] == summary_b["entry_count"] == 2
    assert summary_a["paths"] == summary_b["paths"] == ["a.txt", "b.txt"]
    assert summary_a["summary_sha256"] == summary_b["summary_sha256"]


def test_candidate_diff_content_only_change_is_changed_not_status_changed():
    before = CandidateSnapshot((_entry(content_sha256="old"),))
    after = CandidateSnapshot((_entry(content_sha256="new"),))
    diff = diff_candidate_snapshots(before, after)
    assert len(diff.changed) == 1
    assert diff.status_changed == ()


def test_candidate_diff_status_only_change_is_status_changed_not_changed():
    before = CandidateSnapshot((_entry(index_status="M", worktree_status=" ", tracked=True),))
    after = CandidateSnapshot((_entry(index_status=" ", worktree_status="M", tracked=True),))
    diff = diff_candidate_snapshots(before, after)
    assert diff.changed == ()
    assert len(diff.status_changed) == 1


def test_candidate_diff_object_kind_only_change_is_changed_not_status_changed():
    before = CandidateSnapshot((_entry(object_kind="REGULAR_FILE", content_sha256="same"),))
    after = CandidateSnapshot((_entry(object_kind="SYMLINK", content_sha256="same"),))
    diff = diff_candidate_snapshots(before, after)
    assert len(diff.changed) == 1
    assert diff.status_changed == ()


def test_candidate_diff_combined_status_and_content_object_change_reports_both():
    before = CandidateSnapshot((_entry(index_status="M", worktree_status=" ", object_kind="REGULAR_FILE", content_sha256="old"),))
    after = CandidateSnapshot((_entry(index_status=" ", worktree_status="M", object_kind="SYMLINK", content_sha256="new"),))
    diff = diff_candidate_snapshots(before, after)
    assert len(diff.changed) == 1
    assert len(diff.status_changed) == 1


def test_gitlink_mode_160000_is_rejected_by_regular_and_reproduction_boundaries(tmp_path):
    from synapse.change.workspace import REGULAR_BLOB_MODES, REPRODUCTION_INPUT_MODES, ls_tree_entry, require_tree_mode

    subrepo = tmp_path / "subrepo"
    subrepo.mkdir()
    git(subrepo, "init")
    git(subrepo, "config", "user.email", "test@example.com")
    git(subrepo, "config", "user.name", "Test User")
    (subrepo / "README.md").write_text("sub\n", encoding="utf-8")
    git(subrepo, "add", "README.md")
    git(subrepo, "commit", "-m", "sub")
    sub_commit = git(subrepo, "rev-parse", "HEAD")

    repo = tmp_path / "gitlink-repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_commit},deps/submodule")
    git(repo, "commit", "-m", "gitlink")
    base = git(repo, "rev-parse", "HEAD")

    assert ls_tree_entry(repo, base, "deps/submodule").mode == "160000"
    with pytest.raises(TaskContractError) as task_rejected:
        require_tree_mode(repo, base, "deps/submodule", REGULAR_BLOB_MODES, "TASK_PATH_NOT_REGULAR_FILE")
    assert task_rejected.value.code == "TASK_PATH_NOT_REGULAR_FILE"
    with pytest.raises(TaskContractError) as patch_rejected:
        require_tree_mode(repo, base, "deps/submodule", REGULAR_BLOB_MODES, "PATCH_PATH_NOT_REGULAR_FILE")
    assert patch_rejected.value.code == "PATCH_PATH_NOT_REGULAR_FILE"
    with pytest.raises(TaskContractError) as repro_rejected:
        require_tree_mode(repo, base, "deps/submodule", REPRODUCTION_INPUT_MODES, "REPRODUCTION_INPUTS_INVALID")
    assert repro_rejected.value.code == "REPRODUCTION_INPUTS_INVALID"


def test_real_git_backslash_patch_is_applied_then_rejected_by_scope(tmp_path, monkeypatch):
    patch_repo = tmp_path / "patch-source"
    patch_repo.mkdir()
    git(patch_repo, "init")
    git(patch_repo, "config", "user.email", "test@example.com")
    git(patch_repo, "config", "user.name", "Test User")
    (patch_repo / r"allowed\file.txt").write_text("x\n", encoding="utf-8")
    git(patch_repo, "add", r"allowed\file.txt")
    patch = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=patch_repo, capture_output=True, check=True).stdout
    assert b"allowed" in patch and b"file.txt" in patch

    task = task_dict(allowed_scope=["allowed/file.txt"])
    repo, base = init_repo(tmp_path / "controlled", task=task, patch_text=patch)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == "VERIFICATION_FAILED"
    assert result.failure_phase == "scope_check_after_patch"
    scope_phase = next(phase for phase in result.phases if phase.name == "scope_check_after_patch")
    assert any(r"allowed\file.txt" in diagnostic for diagnostic in scope_phase.diagnostics)
