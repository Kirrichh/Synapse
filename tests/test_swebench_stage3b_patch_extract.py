"""Stage 3B worktree patch extraction tests."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from synapse.experiments.swebench import patch_extract
from synapse.experiments.swebench.patch_extract import (
    extract_model_patch_from_worktree,
    normalize_repo_relative_path,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.py").write_text("value = 'base'\n", encoding="utf-8")
    (repo / "delete_me.py").write_text("delete me\n", encoding="utf-8")
    (repo / "old_name.py").write_text("rename me\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def test_extract_model_patch_covers_tracked_deletion_rename_and_untracked_text_without_mutating_index(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.py").write_text("value = 'changed'\n", encoding="utf-8")
    (repo / "delete_me.py").unlink()
    (repo / "old_name.py").rename(repo / "new_name.py")
    (repo / "new_file.py").write_text("created\n", encoding="utf-8")

    result = extract_model_patch_from_worktree(repo)

    assert result.model_patch is not None
    assert "diff --git a/tracked.py b/tracked.py" in result.model_patch
    assert "deleted file mode" in result.model_patch
    assert "rename from old_name.py" in result.model_patch
    assert "new file mode" in result.model_patch
    assert set(result.diagnostics["changed_paths"]) == {
        "delete_me.py",
        "new_file.py",
        "new_name.py",
        "tracked.py",
    }
    assert result.diagnostics["patch_source"] == "worktree_git_diff"
    assert result.diagnostics["scope_observation_only"] is True
    assert result.diagnostics["model_patch_bytes"] == len(result.model_patch.encode("utf-8"))
    assert _git(repo, "diff", "--cached", "--name-only").stdout == ""


def test_empty_diff_is_candidate_invalid_not_infra(tmp_path):
    repo = _repo(tmp_path)

    result = extract_model_patch_from_worktree(repo)

    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_empty"
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["candidate_invalid"] is True


def test_git_failure_is_infra_error(tmp_path):
    not_repo = tmp_path / "not-repo"
    not_repo.mkdir()

    result = extract_model_patch_from_worktree(not_repo)

    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_git_failed"
    assert result.diagnostics["infra_error"] is True


def test_undecodable_diff_is_infra_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_run_git(worktree_path, args, *, env=None):
        calls.append(args)
        if args[:3] == ("ls-files", "--others", "--exclude-standard"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=b"", stderr=b"")
        if args == ("read-tree", "HEAD") or args == ("add", "-A", "--", "."):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=b"", stderr=b"")
        if args[:4] == ("diff", "--cached", "--binary", "--find-renames"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=b"\xff", stderr=b"")
        raise AssertionError(args)

    monkeypatch.setattr(patch_extract, "_run_git", fake_run_git)

    result = extract_model_patch_from_worktree(repo)

    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_decode_failed"
    assert result.diagnostics["infra_error"] is True
    assert calls


def test_untracked_binary_file_is_unsupported_candidate_not_infra(tmp_path):
    repo = _repo(tmp_path)
    (repo / "binary.dat").write_bytes(b"abc\x00def")

    result = extract_model_patch_from_worktree(repo)

    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_untracked_file_unsupported"
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["candidate_invalid"] is True


def test_untracked_symlink_is_rejected_before_target_read(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    reads: list[Path] = []

    def fake_run_git(worktree_path, args, *, env=None):
        if args[:3] == ("ls-files", "--others", "--exclude-standard"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=b"external-link\0", stderr=b"")
        raise AssertionError("later git commands should not run")

    def fake_is_symlink(self):
        return self.name == "external-link"

    def fake_read_bytes(self):
        reads.append(self)
        raise AssertionError("symlink target must not be read")

    monkeypatch.setattr(patch_extract, "_run_git", fake_run_git)
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)

    result = extract_model_patch_from_worktree(repo)

    assert reads == []
    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_untracked_file_unsupported"
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["candidate_invalid"] is True
    assert result.diagnostics["unsupported_reason"] == "symlink"


def test_path_normalization_rejects_absolute_and_traversal_paths():
    assert normalize_repo_relative_path("pkg/file.py") == "pkg/file.py"
    assert normalize_repo_relative_path("pkg\\file.py") == "pkg/file.py"
    for bad in ("/abs.py", "C:/abs.py", "../escape.py", "pkg/../escape.py", "pkg//file.py"):
        with pytest.raises(ValueError, match="candidate_patch_path_malformed"):
            normalize_repo_relative_path(bad)


def test_malformed_untracked_path_is_candidate_invalid(monkeypatch, tmp_path):
    repo = _repo(tmp_path)

    def fake_run_git(worktree_path, args, *, env=None):
        if args[:3] == ("ls-files", "--others", "--exclude-standard"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=b"../escape.py\0", stderr=b"")
        raise AssertionError("later git commands should not run")

    monkeypatch.setattr(patch_extract, "_run_git", fake_run_git)

    result = extract_model_patch_from_worktree(repo)

    assert result.model_patch is None
    assert result.diagnostics["failure_reason"] == "candidate_patch_path_malformed"
    assert result.diagnostics["infra_error"] is False
