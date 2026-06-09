"""Git worktree, path, and candidate snapshot infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from .contract import TaskContractError, normalize_contract_repo_path, validate_git_observed_path

ZERO_OID = "0" * 40
REGULAR_BLOB_MODES = {"100644", "100755"}
REPRODUCTION_INPUT_MODES = {"100644", "100755", "120000"}


class GitWorkspaceError(RuntimeError):
    """Raised for git workspace failures."""


class ChangeKind(str, Enum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"
    COPIED = "COPIED"
    UNTRACKED = "UNTRACKED"
    TYPE_CHANGED = "TYPE_CHANGED"
    CONFLICTED = "CONFLICTED"


@dataclass(frozen=True)
class ChangedPath:
    index_status: str
    worktree_status: str
    kind: ChangeKind
    old_path: str | None
    new_path: str
    tracked: bool

    def affected_paths(self) -> tuple[str, ...]:
        return (self.old_path, self.new_path) if self.old_path else (self.new_path,)

    def to_json(self) -> dict[str, object]:
        return {
            "index_status": self.index_status,
            "worktree_status": self.worktree_status,
            "kind": self.kind.value,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "tracked": self.tracked,
        }


@dataclass(frozen=True)
class Worktree:
    repo_root: Path
    path: Path
    base_revision: str


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: str


@dataclass(frozen=True)
class CandidateSnapshotEntry:
    kind: ChangeKind
    index_status: str
    worktree_status: str
    old_path: str | None
    new_path: str
    tracked: bool
    object_kind: str
    content_sha256: str | None

    def canonical_ordering_key(self) -> tuple[str, str, str, str, str, bool, str]:
        return (
            self.new_path,
            self.old_path or "",
            self.kind.value,
            self.index_status,
            self.worktree_status,
            self.tracked,
            self.object_kind,
        )

    def transition_key(self) -> tuple[str | None, str, str]:
        return (self.old_path, self.new_path, self.kind.value)

    def status_key(self) -> tuple[str, str, bool]:
        return (self.index_status, self.worktree_status, self.tracked)

    def content_object_key(self) -> tuple[str, str | None]:
        return (self.object_kind, self.content_sha256)

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "index_status": self.index_status,
            "worktree_status": self.worktree_status,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "tracked": self.tracked,
            "object_kind": self.object_kind,
            "content_sha256": self.content_sha256,
        }


CANDIDATE_SNAPSHOT_ALGORITHM = "candidate_snapshot_sha256/v1"


@dataclass(frozen=True)
class CandidateSnapshot:
    entries: tuple[CandidateSnapshotEntry, ...]

    def paths(self) -> tuple[str, ...]:
        paths: list[str] = []
        for entry in self.entries:
            if entry.old_path:
                paths.append(entry.old_path)
            paths.append(entry.new_path)
        return tuple(paths)

    def summary(self) -> dict[str, object]:
        h = hashlib.sha256()
        ordered_entries = sorted(self.entries, key=lambda entry: entry.canonical_ordering_key())
        for entry in ordered_entries:
            payload = json.dumps(
                entry.to_json(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            h.update(payload + b"\0")
        return {
            "algorithm": CANDIDATE_SNAPSHOT_ALGORITHM,
            "entry_count": len(self.entries),
            "summary_sha256": h.hexdigest(),
            "paths": sorted(set(self.paths())),
        }


@dataclass(frozen=True)
class CandidateSnapshotChange:
    before: CandidateSnapshotEntry | None
    after: CandidateSnapshotEntry | None

    def path(self) -> str:
        entry = self.after or self.before
        return entry.new_path if entry else ""


@dataclass(frozen=True)
class CandidateSnapshotDiff:
    added: tuple[CandidateSnapshotEntry, ...]
    removed: tuple[CandidateSnapshotEntry, ...]
    changed: tuple[CandidateSnapshotChange, ...]
    renamed: tuple[CandidateSnapshotEntry, ...]
    status_changed: tuple[CandidateSnapshotChange, ...]

    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed or self.renamed or self.status_changed)

    def diagnostics(self) -> list[str]:
        diagnostics: list[str] = []
        diagnostics.extend(f"CANDIDATE_PATH_ADDED: {entry.new_path}" for entry in self.added)
        diagnostics.extend(f"CANDIDATE_PATH_REMOVED: {entry.new_path}" for entry in self.removed)
        diagnostics.extend(f"CANDIDATE_PATH_CHANGED: {change.path()}" for change in self.changed)
        diagnostics.extend(f"CANDIDATE_PATH_RENAMED: {entry.old_path} -> {entry.new_path}" for entry in self.renamed)
        diagnostics.extend(f"CANDIDATE_STATUS_CHANGED: {change.path()}" for change in self.status_changed)
        return diagnostics


def git(args: list[str], cwd: str | Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if check and completed.returncode != 0:
        raise GitWorkspaceError(
            f"git {' '.join(args)} failed with {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def git_binary(args: list[str], cwd: str | Path, *, check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(["git", *args], cwd=str(cwd), input=input_bytes, capture_output=True)
    if check and completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        stdout = completed.stdout.decode("utf-8", "replace").strip()
        raise GitWorkspaceError(f"git {' '.join(args)} failed with {completed.returncode}: {stderr or stdout}")
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


def commit_tree(repo_root: str | Path, revision: str) -> str:
    return git(["show", "-s", "--format=%T", revision], repo_root).stdout.strip()


def load_committed_bytes(repo_root: str | Path, base_revision: str, rel_path: str) -> bytes:
    safe_path = normalize_contract_repo_path(rel_path, "committed_path")
    return git_binary(["show", f"{base_revision}:{safe_path}"], repo_root).stdout


def load_committed_text(repo_root: str | Path, base_revision: str, rel_path: str) -> str:
    return load_committed_bytes(repo_root, base_revision, rel_path).decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ls_tree_entry(repo_root: str | Path, base_revision: str, rel_path: str) -> GitTreeEntry | None:
    safe_path = normalize_contract_repo_path(rel_path, "tree_path")
    completed = git_binary(["ls-tree", "-z", base_revision, "--", safe_path], repo_root)
    records = [record for record in completed.stdout.split(b"\0") if record]
    entries: list[GitTreeEntry] = []
    for record in records:
        try:
            meta, path_bytes = record.split(b"\t", 1)
            mode, object_type, object_id = meta.decode("ascii").split(" ", 2)
            path = validate_git_observed_path(path_bytes.decode("utf-8", "surrogateescape"), "ls_tree_path")
        except ValueError as exc:
            raise GitWorkspaceError(f"invalid ls-tree record for {safe_path!r}") from exc
        if path == safe_path:
            entries.append(GitTreeEntry(mode=mode, object_type=object_type, object_id=object_id, path=path))
    if not entries:
        return None
    if len(entries) != 1:
        raise GitWorkspaceError(f"ambiguous ls-tree result for {safe_path}")
    return entries[0]


def require_tree_mode(repo_root: str | Path, base_revision: str, rel_path: str, allowed_modes: set[str], diagnostic_code: str) -> GitTreeEntry:
    entry = ls_tree_entry(repo_root, base_revision, rel_path)
    if entry is None or entry.mode not in allowed_modes:
        raise TaskContractError(diagnostic_code, f"{rel_path} is missing or has unsupported Git mode")
    return entry


def verify_scaffold_committed(repo_root: str | Path, base_revision: str, paths: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for rel_path in paths:
        entry = ls_tree_entry(repo_root, base_revision, rel_path)
        if entry is None or entry.mode in {"040000", "160000"}:
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


def _decode_path(path: bytes) -> str:
    return validate_git_observed_path(path.decode("utf-8", "surrogateescape"), "git_status_path")


def _change_kind(index_status: str, worktree_status: str) -> ChangeKind:
    statuses = {index_status, worktree_status}
    if statuses == {"?"}:
        return ChangeKind.UNTRACKED
    if "U" in statuses or (index_status, worktree_status) in {("A", "A"), ("D", "D")}:
        return ChangeKind.CONFLICTED
    if "R" in statuses:
        return ChangeKind.RENAMED
    if "C" in statuses:
        return ChangeKind.COPIED
    if "D" in statuses:
        return ChangeKind.DELETED
    if "A" in statuses or "?" in statuses:
        return ChangeKind.ADDED
    if "T" in statuses:
        return ChangeKind.TYPE_CHANGED
    return ChangeKind.MODIFIED


def parse_status_z(output: bytes) -> tuple[ChangedPath, ...]:
    records = [record for record in output.split(b"\0") if record]
    changes: list[ChangedPath] = []
    idx = 0
    while idx < len(records):
        record = records[idx]
        if len(record) < 4 or record[2:3] != b" ":
            raise GitWorkspaceError("invalid git status --porcelain=v1 -z record")
        index_status = chr(record[0])
        worktree_status = chr(record[1])
        path = _decode_path(record[3:])
        kind = _change_kind(index_status, worktree_status)
        old_path: str | None = None
        if kind in {ChangeKind.RENAMED, ChangeKind.COPIED}:
            idx += 1
            if idx >= len(records):
                raise GitWorkspaceError("rename/copy status record missing source path")
            old_path = _decode_path(records[idx])
        changes.append(
            ChangedPath(
                index_status=index_status,
                worktree_status=worktree_status,
                kind=kind,
                old_path=old_path,
                new_path=path,
                tracked=kind is not ChangeKind.UNTRACKED,
            )
        )
        idx += 1
    return tuple(changes)


def changed_paths(cwd: str | Path) -> tuple[ChangedPath, ...]:
    completed = git_binary(["status", "--porcelain=v1", "-z"], cwd)
    return tuple(sorted(parse_status_z(completed.stdout), key=lambda change: (change.new_path, change.old_path or "", change.index_status, change.worktree_status)))


def changed_files(cwd: str | Path) -> list[str]:
    files: set[str] = set()
    for change in changed_paths(cwd):
        files.update(path for path in change.affected_paths() if path is not None)
    return sorted(files)


def _file_digest(path: Path) -> tuple[str, str | None]:
    h = hashlib.sha256()
    if path.is_symlink():
        target = os.readlink(path)
        h.update(target.encode("utf-8", "surrogateescape"))
        return "SYMLINK", h.hexdigest()
    if path.exists() and path.is_file():
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return "REGULAR_FILE", h.hexdigest()
    if not path.exists():
        return "DELETED", None
    return "OTHER", None


def build_candidate_snapshot(cwd: str | Path, changes: tuple[ChangedPath, ...]) -> CandidateSnapshot:
    root = Path(cwd)
    entries: list[CandidateSnapshotEntry] = []
    identities: set[tuple[str | None, str, str]] = set()
    for change in changes:
        object_kind, digest = _file_digest(root / change.new_path)
        entry = CandidateSnapshotEntry(
            kind=change.kind,
            index_status=change.index_status,
            worktree_status=change.worktree_status,
            old_path=change.old_path,
            new_path=change.new_path,
            tracked=change.tracked,
            object_kind=object_kind,
            content_sha256=digest,
        )
        identity = entry.transition_key()
        if identity in identities:
            raise GitWorkspaceError(f"DUPLICATE_CANDIDATE_IDENTITY: {identity}")
        identities.add(identity)
        entries.append(entry)
    return CandidateSnapshot(tuple(sorted(entries, key=lambda entry: entry.canonical_ordering_key())))


def capture_candidate_snapshot(cwd: str | Path) -> CandidateSnapshot:
    return build_candidate_snapshot(cwd, changed_paths(cwd))


def diff_candidate_snapshots(before: CandidateSnapshot, after: CandidateSnapshot) -> CandidateSnapshotDiff:
    before_map = {entry.transition_key(): entry for entry in before.entries}
    after_map = {entry.transition_key(): entry for entry in after.entries}
    transition_sort_key = lambda key: (key[1], key[0] or "", key[2])
    added = tuple(after_map[key] for key in sorted(after_map.keys() - before_map.keys(), key=transition_sort_key))
    removed = tuple(before_map[key] for key in sorted(before_map.keys() - after_map.keys(), key=transition_sort_key))
    changed: list[CandidateSnapshotChange] = []
    status_changed: list[CandidateSnapshotChange] = []
    for key in sorted(before_map.keys() & after_map.keys(), key=transition_sort_key):
        old = before_map[key]
        new = after_map[key]
        if old.content_object_key() != new.content_object_key():
            changed.append(CandidateSnapshotChange(old, new))
        if old.status_key() != new.status_key():
            status_changed.append(CandidateSnapshotChange(old, new))
    renamed = tuple(entry for entry in added if entry.kind is ChangeKind.RENAMED)
    return CandidateSnapshotDiff(added=added, removed=removed, changed=tuple(changed), renamed=renamed, status_changed=tuple(status_changed))


def assert_clean_worktree(cwd: str | Path) -> bool:
    return not changed_paths(cwd)
