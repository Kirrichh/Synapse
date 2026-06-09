"""Git worktree, path, and candidate snapshot infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile

from .contract import validate_repo_relative_path

ZERO_OID = "0" * 40


class GitWorkspaceError(RuntimeError):
    """Raised for git workspace failures."""


@dataclass(frozen=True)
class Worktree:
    repo_root: Path
    path: Path
    base_revision: str


@dataclass(frozen=True)
class CandidateSnapshot:
    entries: tuple[tuple[str, str, str], ...]

    def paths(self) -> tuple[str, ...]:
        return tuple(entry[0] for entry in self.entries)


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
    return Path(git(["rev-parse", "--git-dir"], repo_root).stdout.strip())


def resolve_revision(repo_root: str | Path, revision: str) -> str:
    completed = git(["rev-parse", "--verify", f"{revision}^{{commit}}"], repo_root)
    return completed.stdout.strip()


def load_committed_text(repo_root: str | Path, base_revision: str, rel_path: str) -> str:
    safe_path = validate_repo_relative_path(rel_path, "task_path")
    return git(["show", f"{base_revision}:{safe_path}"], repo_root).stdout


def verify_scaffold_committed(repo_root: str | Path, base_revision: str, paths: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for rel_path in paths:
        completed = git(["cat-file", "-e", f"{base_revision}:{rel_path}"], repo_root, check=False)
        if completed.returncode != 0:
            missing.append(rel_path)
    return missing


def create_detached_worktree(repo_root: str | Path, base_revision: str) -> Worktree:
    root = Path(repo_root)
    temp_root = Path(tempfile.mkdtemp(prefix="synapse-change-"))
    worktree_path = temp_root / "worktree"
    git(["worktree", "add", "--detach", str(worktree_path), base_revision], root)
    return Worktree(repo_root=root, path=worktree_path, base_revision=base_revision)


def cleanup_worktree(worktree: Worktree | None, keep: bool) -> str:
    if worktree is None:
        return "NO_WORKTREE_CREATED"
    if keep:
        return "PRESERVED_FOR_INSPECTION"
    git(["worktree", "remove", "--force", str(worktree.path)], worktree.repo_root)
    parent = worktree.path.parent
    if parent.exists():
        shutil.rmtree(parent, ignore_errors=True)
    return "REMOVED"


def changed_files(cwd: str | Path) -> list[str]:
    completed = git(["status", "--porcelain=v1"], cwd)
    files: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(set(files))


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def capture_candidate_snapshot(cwd: str | Path) -> CandidateSnapshot:
    root = Path(cwd)
    completed = git(["status", "--porcelain=v1"], root)
    entries: list[tuple[str, str, str]] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        rel = line[3:]
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        file_path = root / rel
        if file_path.exists() and file_path.is_file():
            digest = _file_digest(file_path)
        else:
            digest = "<deleted>"
        entries.append((rel, status, digest))
    return CandidateSnapshot(tuple(sorted(entries)))


def assert_clean_worktree(cwd: str | Path) -> bool:
    return not changed_files(cwd)
