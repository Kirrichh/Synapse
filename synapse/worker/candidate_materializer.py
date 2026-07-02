"""Materialize external worker candidates from scratch repository state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import subprocess
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from synapse.change.contract import AllowedScope, validate_repo_relative_path

from .contract import ExternalCodingWorkerResult


class MaterializationStatus(str, Enum):
    MATERIALIZED = "MATERIALIZED"
    REJECTED_SCOPE_VIOLATION = "REJECTED_SCOPE_VIOLATION"
    NO_CANDIDATE = "NO_CANDIDATE"
    UNSUPPORTED_BINARY = "UNSUPPORTED_BINARY"
    UNSUPPORTED_COMBINATION = "UNSUPPORTED_COMBINATION"
    GIT_ERROR = "GIT_ERROR"


@dataclass(frozen=True)
class MaterializedCandidate:
    status: MaterializationStatus
    patch_text: str | None
    touched_files: tuple[str, ...]
    scope_violations: tuple[str, ...]
    source_forms: tuple[str, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "touched_files", tuple(self.touched_files))
        object.__setattr__(self, "scope_violations", tuple(self.scope_violations))
        object.__setattr__(self, "source_forms", tuple(self.source_forms))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(frozen=True)
class _PatchSource:
    form: str
    patch_text: str
    paths: tuple[str, ...]


def _git_text(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=check,
    )


def _git_stdout(repo: Path, *args: str) -> str:
    return _git_text(repo, *args).stdout


def _split_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in text.splitlines():
        if raw:
            paths.append(validate_repo_relative_path(raw.replace("\\", "/"), "git_path"))
    return tuple(dict.fromkeys(paths))


def _parse_diff_paths(patch_text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        for token in parts[2:4]:
            if token.startswith("a/") or token.startswith("b/"):
                path = token[2:]
                if path != "/dev/null":
                    paths.append(validate_repo_relative_path(path, "diff_path"))
    return tuple(dict.fromkeys(paths))


def _normalize_scope(allowed_scope: Sequence[str] | AllowedScope) -> AllowedScope:
    if isinstance(allowed_scope, AllowedScope):
        if not allowed_scope.exact and not allowed_scope.prefixes:
            raise ValueError("allowed_scope must not be empty")
        return allowed_scope
    if not allowed_scope:
        raise ValueError("allowed_scope must not be empty")
    prefixes = tuple(
        validate_repo_relative_path(str(path).rstrip("/"), "allowed_scope[]")
        for path in allowed_scope
    )
    return AllowedScope(exact=(), prefixes=prefixes)


def _scope_violations(paths: Sequence[str], scope: AllowedScope) -> tuple[str, ...]:
    violations: list[str] = []
    for path in paths:
        normalized = validate_repo_relative_path(path, "candidate_path")
        if not scope.allows_path(normalized):
            violations.append(normalized)
    return tuple(dict.fromkeys(violations))


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _new_file_patch(repo: Path, path: str) -> tuple[str | None, dict[str, Any]]:
    file_path = repo / path
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return None, {"unsupported_reason": "read_failed", "exception": type(exc).__name__, "message": str(exc)}
    if b"\0" in data:
        return None, {"unsupported_reason": "binary_nul"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, {"unsupported_reason": "decode_failed", "exception": type(exc).__name__, "message": str(exc)}
    blob_hash = subprocess.run(
        ["git", "hash-object", "--stdin"],
        cwd=repo,
        input=data,
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()
    lines = text.splitlines(keepends=True)
    hunk: list[str] = [
        f"diff --git a/{path} b/{path}\n",
        "new file mode 100644\n",
        f"index 0000000..{blob_hash[:7]}\n",
        "--- /dev/null\n",
        f"+++ b/{path}\n",
        f"@@ -0,0 +1,{len(lines)} @@\n",
    ]
    if not lines:
        hunk[-1] = "@@ -0,0 +1,0 @@\n"
    for line in lines:
        hunk.append("+" + line)
    if text and not text.endswith("\n"):
        hunk.append("\n\\ No newline at end of file\n")
    return "".join(hunk), {}


def _untracked_source(repo: Path, untracked_paths: tuple[str, ...]) -> tuple[_PatchSource | None, dict[str, Any] | None]:
    patches: list[str] = []
    diagnostics: dict[str, Any] = {}
    for path in untracked_paths:
        patch, file_diagnostics = _new_file_patch(repo, path)
        if patch is None:
            diagnostics["unsupported_untracked_file"] = path
            diagnostics.update(file_diagnostics)
            return None, diagnostics
        patches.append(_ensure_trailing_newline(patch))
    return _PatchSource("untracked_files", "".join(patches), untracked_paths), None


def _diff_source(repo: Path, form: str, diff_args: tuple[str, ...], name_args: tuple[str, ...]) -> _PatchSource | None:
    patch = _git_stdout(repo, *diff_args)
    paths = _split_paths(_git_stdout(repo, *name_args))
    if not patch.strip() and not paths:
        return None
    return _PatchSource(form, patch, paths)


def _scratch_commit_source(repo: Path, base_commit: str | None) -> tuple[_PatchSource | None, tuple[str, ...]]:
    if base_commit is None:
        return None, ()
    head = _git_stdout(repo, "rev-parse", "HEAD").strip()
    base = _git_stdout(repo, "rev-parse", base_commit).strip()
    if head == base:
        return None, ()
    patch = _git_stdout(repo, "diff", "--binary", f"{base}..HEAD")
    paths = _split_paths(_git_stdout(repo, "diff", "--name-only", f"{base}..HEAD"))
    commits = tuple(
        line.strip()
        for line in _git_stdout(repo, "rev-list", "--reverse", f"{base}..HEAD").splitlines()
        if line.strip()
    )
    if not patch.strip() and not paths:
        return None, commits
    return _PatchSource("scratch_commits", patch, paths), commits


def _worker_source(worker_result: ExternalCodingWorkerResult) -> _PatchSource | None:
    patch = worker_result.diff_text or ""
    if not patch.strip():
        return None
    paths = tuple(dict.fromkeys((*worker_result.touched_files, *_parse_diff_paths(patch))))
    return _PatchSource("worker_diff_text", patch, paths)


def _source_by_form(sources: Sequence[_PatchSource], form: str) -> _PatchSource | None:
    for source in sources:
        if source.form == form:
            return source
    return None


def _combine_sources(sources: Sequence[_PatchSource]) -> tuple[str, dict[str, Any]]:
    seen: dict[str, str] = {}
    diagnostics: dict[str, Any] = {}
    chunks: list[str] = []
    for source in sources:
        duplicate_paths = [path for path in source.paths if path in seen]
        if duplicate_paths:
            diagnostics.setdefault("duplicate_paths", []).extend(duplicate_paths)
            diagnostics.setdefault("duplicate_path_sources", []).append(
                {"form": source.form, "existing_forms": [seen[path] for path in duplicate_paths]}
            )
            continue
        for path in source.paths:
            seen[path] = source.form
        chunks.append(_ensure_trailing_newline(source.patch_text))
    return "".join(chunks), diagnostics


def materialize_worker_candidate(
    *,
    worktree_path: str | Path,
    worker_result: ExternalCodingWorkerResult,
    allowed_scope: Sequence[str] | AllowedScope,
    base_commit: str | None = None,
) -> MaterializedCandidate:
    """Convert worker output and scratch repo state into a candidate patch."""

    repo = Path(worktree_path)
    scope = _normalize_scope(allowed_scope)
    diagnostics: dict[str, Any] = {
        "worker_status": worker_result.worker_status.value,
        "base_commit": base_commit,
        "worker_diff_text_diverges_from_worktree": False,
        "usage": worker_result.usage.to_dict(),
    }
    try:
        worker_source = _worker_source(worker_result)
        unstaged_source = _diff_source(
            repo,
            "unstaged_diff",
            ("diff", "--binary"),
            ("diff", "--name-only"),
        )
        staged_source = _diff_source(
            repo,
            "staged_diff",
            ("diff", "--cached", "--binary"),
            ("diff", "--cached", "--name-only"),
        )
        scratch_source, candidate_commits = _scratch_commit_source(repo, base_commit)
        untracked_paths = _split_paths(_git_stdout(repo, "ls-files", "--others", "--exclude-standard"))
        untracked_source = _PatchSource("untracked_files", "", untracked_paths) if untracked_paths else None
    except subprocess.CalledProcessError as exc:
        diagnostics["git_command_error"] = {
            "command": exc.cmd,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
        return MaterializedCandidate(
            status=MaterializationStatus.GIT_ERROR,
            patch_text=None,
            touched_files=(),
            scope_violations=(),
            source_forms=(),
            diagnostics=diagnostics,
        )

    sources = [
        source
        for source in (worker_source, unstaged_source, staged_source, untracked_source, scratch_source)
        if source is not None
    ]

    if worker_source and unstaged_source:
        diagnostics["worker_diff_text_diverges_from_worktree"] = (
            worker_source.patch_text != unstaged_source.patch_text
        )
        if diagnostics["worker_diff_text_diverges_from_worktree"]:
            sources = [source for source in sources if source.form != "worker_diff_text"]

    source_forms = tuple(source.form for source in sources)
    touched_files = tuple(dict.fromkeys(path for source in sources for path in source.paths))
    diagnostics.update(
        {
            "source_forms": list(source_forms),
            "touched_files": list(touched_files),
            "tracked_files": list(
                dict.fromkeys(
                    path
                    for source in sources
                    if source.form in {"worker_diff_text", "unstaged_diff"}
                    for path in source.paths
                )
            ),
            "staged_files": list(_source_by_form(sources, "staged_diff").paths)
            if _source_by_form(sources, "staged_diff")
            else [],
            "untracked_files": list(untracked_paths),
            "committed_files": list(_source_by_form(sources, "scratch_commits").paths)
            if _source_by_form(sources, "scratch_commits")
            else [],
            "candidate_commit_shas": list(candidate_commits),
        }
    )

    if not sources:
        return MaterializedCandidate(
            status=MaterializationStatus.NO_CANDIDATE,
            patch_text=None,
            touched_files=(),
            scope_violations=(),
            source_forms=(),
            diagnostics=diagnostics,
        )

    violations = _scope_violations(touched_files, scope)
    diagnostics["scope_violations"] = list(violations)
    if violations:
        return MaterializedCandidate(
            status=MaterializationStatus.REJECTED_SCOPE_VIOLATION,
            patch_text=None,
            touched_files=touched_files,
            scope_violations=violations,
            source_forms=source_forms,
            diagnostics=diagnostics,
        )

    if untracked_paths:
        materialized_untracked, untracked_diagnostics = _untracked_source(repo, untracked_paths)
        if materialized_untracked is None:
            diagnostics["unsupported_combination_reason"] = untracked_diagnostics
            return MaterializedCandidate(
                status=MaterializationStatus.UNSUPPORTED_BINARY,
                patch_text=None,
                touched_files=touched_files,
                scope_violations=(),
                source_forms=source_forms,
                diagnostics=diagnostics,
            )
        sources = [
            materialized_untracked if source.form == "untracked_files" else source
            for source in sources
        ]

    patch_text, combine_diagnostics = _combine_sources(sources)
    if combine_diagnostics.get("duplicate_paths"):
        diagnostics["unsupported_combination_reason"] = "duplicate changed paths across source forms"
        diagnostics.update(combine_diagnostics)
        return MaterializedCandidate(
            status=MaterializationStatus.UNSUPPORTED_COMBINATION,
            patch_text=None,
            touched_files=touched_files,
            scope_violations=(),
            source_forms=source_forms,
            diagnostics=diagnostics,
        )

    return MaterializedCandidate(
        status=MaterializationStatus.MATERIALIZED,
        patch_text=patch_text,
        touched_files=touched_files,
        scope_violations=(),
        source_forms=source_forms,
        diagnostics=diagnostics,
    )
