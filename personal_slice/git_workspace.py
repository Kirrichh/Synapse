"""Git worktree management for Personal Slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile


class GitWorkspaceError(RuntimeError):
    """Raised for git workspace failures."""


@dataclass(frozen=True)
class Worktree:
    repo_root: Path
    path: Path
    base_revision: str


def git(args: list[str], cwd: str | Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if check and completed.returncode != 0:
        raise GitWorkspaceError(
            f"git {' '.join(args)} failed with {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def find_repo_root(start: str | Path | None = None) -> Path:
    cwd = Path(start or Path.cwd())
    completed = git(["rev-parse", "--show-toplevel"], cwd)
    return Path(completed.stdout.strip())


def git_dir(repo_root: str | Path) -> Path:
    completed = git(["rev-parse", "--git-dir"], repo_root)
    raw = Path(completed.stdout.strip())
    return raw if raw.is_absolute() else Path(repo_root) / raw


def resolve_revision(repo_root: str | Path, revision: str) -> str:
    completed = git(["rev-parse", "--verify", f"{revision}^{{commit}}"], repo_root)
    return completed.stdout.strip()


def resolve_tree(repo_root: str | Path, revision: str) -> str:
    completed = git(["rev-parse", "--verify", f"{revision}^{{tree}}"], repo_root)
    return completed.stdout.strip()


def validate_repo_relative_path(relative_path: str, *, field: str = "path") -> str:
    if not isinstance(relative_path, str) or not relative_path:
        raise GitWorkspaceError(f"{field} must be a non-empty repo-relative path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise GitWorkspaceError(f"{field} must be a repo-relative path without traversal")
    return path.as_posix()


def validate_repo_relative_prefix(prefix: str, *, field: str = "prefix") -> str:
    normalized = validate_repo_relative_path(prefix.rstrip("/"), field=field)
    return f"{normalized}/"


def read_committed_text(repo_root: Path, revision: str, relative_path: str) -> str:
    rel = validate_repo_relative_path(relative_path, field="relative_path")
    result = git(["show", f"{revision}:{rel}"], repo_root)
    return result.stdout


def read_committed_bytes(repo_root: Path, revision: str, relative_path: str) -> bytes:
    rel = validate_repo_relative_path(relative_path, field="relative_path")
    completed = subprocess.run(
        ["git", "show", f"{revision}:{rel}"], cwd=str(repo_root), capture_output=True
    )
    if completed.returncode != 0:
        raise GitWorkspaceError(
            f"git show {revision}:{rel} failed with {completed.returncode}: "
            f"{completed.stderr.decode(errors='replace').strip() or completed.stdout.decode(errors='replace').strip()}"
        )
    return completed.stdout


def committed_path_exists(repo_root: str | Path, revision: str, relative_path: str) -> bool:
    rel = validate_repo_relative_path(relative_path, field="relative_path")
    completed = git(["cat-file", "-e", f"{revision}:{rel}"], repo_root, check=False)
    return completed.returncode == 0


def verify_scaffold_committed(repo_root: str | Path, base_revision: str, paths: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for rel_path in paths:
        if not committed_path_exists(repo_root, base_revision, rel_path):
            missing.append(rel_path)
    return missing


def create_detached_worktree(repo_root: str | Path, base_revision: str) -> Worktree:
    root = Path(repo_root)
    temp_root = Path(tempfile.mkdtemp(prefix="personal-slice-"))
    worktree_path = temp_root / "worktree"
    git(["worktree", "add", "--detach", str(worktree_path), base_revision], root)
    return Worktree(repo_root=root, path=worktree_path, base_revision=base_revision)


def cleanup_worktree(worktree: Worktree | None, keep: bool) -> str:
    if worktree is None:
        return "NO_WORKTREE_CREATED"
    if keep:
        return "PROCESS_ENVIRONMENT_PRESERVED"
    git(["worktree", "remove", "--force", str(worktree.path)], worktree.repo_root)
    parent = worktree.path.parent
    if parent.exists():
        shutil.rmtree(parent, ignore_errors=True)
    return "REMOVED"


def _parse_porcelain_z(stdout: str) -> list[str]:
    entries = stdout.split("\0")
    files: list[str] = []
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if not entry:
            continue
        if len(entry) < 3:
            continue
        status = entry[:2]
        path = entry[3:]
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            # In porcelain v1 -z, rename/copy records are: "XY new\0old\0".
            files.append(path)
            if idx < len(entries):
                old = entries[idx]
                idx += 1
                if old:
                    files.append(old)
            continue
        files.append(path)
    return files


def changed_files(cwd: str | Path, base_revision: str | None = None) -> list[str]:
    """Return tracked and untracked paths that could be staged by ``git add -A``."""
    files: set[str] = set()
    completed = git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd)
    files.update(_parse_porcelain_z(completed.stdout))
    if base_revision is not None:
        diff = git(["diff", "--name-only", "-z", base_revision], cwd)
        files.update(path for path in diff.stdout.split("\0") if path)
        others = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
        files.update(path for path in others.stdout.split("\0") if path)
    return sorted(files)


def checked_out_branch_refs(repo_root: str | Path) -> set[str]:
    completed = git(["worktree", "list", "--porcelain"], repo_root)
    refs: set[str] = set()
    for line in completed.stdout.splitlines():
        if line.startswith("branch "):
            ref = line.split(" ", 1)[1].strip()
            if ref.startswith("refs/heads/"):
                refs.add(ref)
    return refs
