"""Worktree patch extraction for Stage 3B SWE-bench oracle input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import hashlib
import os
import re
import subprocess
import tempfile


PATCH_SOURCE = "worktree_git_diff"


@dataclass(frozen=True)
class CandidatePatchExtractionResult:
    model_patch: str | None
    diagnostics: Mapping[str, object]


def _failure(reason: str, *, infra_error: bool, **extra: object) -> CandidatePatchExtractionResult:
    return CandidatePatchExtractionResult(
        model_patch=None,
        diagnostics={
            "patch_source": PATCH_SOURCE,
            "failure_reason": reason,
            "infra_error": infra_error,
            "candidate_invalid": not infra_error,
            **extra,
        },
    )


def normalize_repo_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or "//" in normalized
    ):
        raise ValueError("candidate_patch_path_malformed")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("candidate_patch_path_malformed")
    return normalized


def _run_git(
    worktree_path: Path,
    args: tuple[str, ...],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
    )


def _decode_git_output(data: bytes) -> str:
    return data.decode("utf-8", errors="strict")


def _decode_z_paths(data: bytes) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        paths.append(normalize_repo_relative_path(_decode_git_output(raw)))
    return tuple(paths)


def _check_untracked_text_files(worktree_path: Path) -> CandidatePatchExtractionResult | None:
    completed = _run_git(worktree_path, ("ls-files", "--others", "--exclude-standard", "-z"))
    if completed.returncode != 0:
        return _failure(
            "candidate_patch_git_failed",
            infra_error=True,
            git_command="git ls-files --others --exclude-standard -z",
            git_returncode=completed.returncode,
            git_stderr=completed.stderr.decode("utf-8", errors="replace"),
        )
    try:
        untracked_paths = _decode_z_paths(completed.stdout)
    except UnicodeDecodeError:
        return _failure("candidate_patch_decode_failed", infra_error=True)
    except ValueError:
        return _failure("candidate_patch_path_malformed", infra_error=False)

    for path in untracked_paths:
        file_path = worktree_path / path
        try:
            data = file_path.read_bytes()
        except OSError:
            return _failure(
                "candidate_patch_untracked_file_unsupported",
                infra_error=False,
                unsupported_path=path,
            )
        if b"\0" in data:
            return _failure(
                "candidate_patch_untracked_file_unsupported",
                infra_error=False,
                unsupported_path=path,
            )
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _failure(
                "candidate_patch_untracked_file_unsupported",
                infra_error=False,
                unsupported_path=path,
            )
    return None


def extract_model_patch_from_worktree(worktree_path: Path) -> CandidatePatchExtractionResult:
    worktree_path = Path(worktree_path)
    untracked_failure = _check_untracked_text_files(worktree_path)
    if untracked_failure is not None:
        return untracked_failure

    index_file = tempfile.NamedTemporaryFile(prefix="synapse-stage3b-index-", delete=False)
    index_path = Path(index_file.name)
    index_file.close()
    env = {"GIT_INDEX_FILE": str(index_path)}
    try:
        for args in (("read-tree", "HEAD"), ("add", "-A", "--", ".")):
            completed = _run_git(worktree_path, args, env=env)
            if completed.returncode != 0:
                return _failure(
                    "candidate_patch_git_failed",
                    infra_error=True,
                    git_command="git " + " ".join(args),
                    git_returncode=completed.returncode,
                    git_stderr=completed.stderr.decode("utf-8", errors="replace"),
                )

        diff = _run_git(
            worktree_path,
            ("diff", "--cached", "--binary", "--find-renames", "HEAD"),
            env=env,
        )
        if diff.returncode != 0:
            return _failure(
                "candidate_patch_git_failed",
                infra_error=True,
                git_command="git diff --cached --binary --find-renames HEAD",
                git_returncode=diff.returncode,
                git_stderr=diff.stderr.decode("utf-8", errors="replace"),
            )
        try:
            model_patch = _decode_git_output(diff.stdout)
        except UnicodeDecodeError:
            return _failure("candidate_patch_decode_failed", infra_error=True)
        if not model_patch.strip():
            return _failure("candidate_patch_empty", infra_error=False)

        names = _run_git(
            worktree_path,
            ("diff", "--cached", "--name-only", "-z", "--find-renames", "HEAD"),
            env=env,
        )
        if names.returncode != 0:
            return _failure(
                "candidate_patch_git_failed",
                infra_error=True,
                git_command="git diff --cached --name-only -z --find-renames HEAD",
                git_returncode=names.returncode,
                git_stderr=names.stderr.decode("utf-8", errors="replace"),
            )
        try:
            changed_paths = _decode_z_paths(names.stdout)
        except UnicodeDecodeError:
            return _failure("candidate_patch_decode_failed", infra_error=True)
        except ValueError:
            return _failure("candidate_patch_path_malformed", infra_error=False)

        data = model_patch.encode("utf-8")
        return CandidatePatchExtractionResult(
            model_patch=model_patch,
            diagnostics={
                "patch_source": PATCH_SOURCE,
                "model_patch_sha256": hashlib.sha256(data).hexdigest(),
                "model_patch_bytes": len(data),
                "changed_paths": list(changed_paths),
                "scope_observation_source": "worktree_patch_paths",
                "scope_observation_only": True,
                "infra_error": False,
                "candidate_invalid": False,
            },
        )
    finally:
        try:
            index_path.unlink()
        except FileNotFoundError:
            pass
