"""Gold front end of the change oracle.

This adapter receives the clean detached verified worktree created by the C1
GOLD runner and derives the candidate ``model_patch`` from the verified commit
pair, never from dirty worktree state, before the change oracle compares the
base revision with that patch. It does not run workers, controlled-change,
telemetry, paired measurement, carry admission, or FULL verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import subprocess
import time

from synapse.experiments.applied_verification.change_oracle import (
    ChangeOracleConfig,
    evaluate_change,
    failure_result,
    oracle_base_diagnostics,
)
from synapse.experiments.applied_verification.contract import BaselineTask, OracleResult


ADAPTER_VERSION = "synapse.applied_verification.gold_change_oracle_binding/v1"
PATCH_SOURCE = "verified_commit_pair_git_diff"
BASE_SOURCE = "head_parent_single_parent_assumed"


def _run_git(worktree_path: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _decode_strict(data: bytes) -> str:
    return data.decode("utf-8", errors="strict")


def _decode_replace(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _normalize_repo_relative_path(path: str) -> str:
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


def _decode_z_paths(data: bytes) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        paths.append(_normalize_repo_relative_path(_decode_strict(raw)))
    return tuple(dict.fromkeys(paths))


@dataclass(frozen=True)
class _CommitPair:
    verified_commit: str
    base_sha: str
    parent_count: int


@dataclass(frozen=True)
class GoldChangeOracleBinding:
    config: ChangeOracleConfig

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        started = time.perf_counter()
        worktree = Path(worktree_path)
        diagnostics = oracle_base_diagnostics(self.config, task, adapter=ADAPTER_VERSION,
                                              invocation_root=".synapse_gold_oracle")
        diagnostics["patch_source"] = PATCH_SOURCE

        pair = self._resolve_commit_pair(worktree, diagnostics=diagnostics)
        if isinstance(pair, OracleResult):
            return pair

        diagnostics.update(
            {
                "base_sha": pair.base_sha,
                "base_source": BASE_SOURCE,
                "verified_commit": pair.verified_commit,
                "verified_commit_parent_count": pair.parent_count,
            }
        )

        extraction = self._model_patch_from_pair(worktree, pair, diagnostics=diagnostics)
        if isinstance(extraction, OracleResult):
            return extraction
        model_patch, changed_paths = extraction
        patch_bytes = model_patch.encode("utf-8")
        diagnostics.update(
            {
                "model_patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
                "model_patch_bytes": len(patch_bytes),
                "changed_paths": list(changed_paths),
                "scope_observation_source": "verified_commit_pair_patch_paths",
                "scope_observation_only": True,
            }
        )
        return evaluate_change(self.config, repo=worktree, base_sha=pair.base_sha, model_patch=model_patch,
                               task=task, diagnostics=diagnostics, started=started)

    def _resolve_commit_pair(self, worktree: Path, *, diagnostics: dict[str, object]) -> _CommitPair | OracleResult:
        started = time.perf_counter()
        head = _run_git(worktree, ("rev-parse", "HEAD"))
        if head.returncode != 0:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            verified_commit = _decode_strict(head.stdout).strip()
        except UnicodeDecodeError:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )

        parents = _run_git(worktree, ("rev-list", "--parents", "-n", "1", "HEAD"))
        if parents.returncode != 0:
            diagnostics["verified_commit"] = verified_commit
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            parent_line = _decode_strict(parents.stdout).strip()
        except UnicodeDecodeError:
            diagnostics["verified_commit"] = verified_commit
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        parts = parent_line.split()
        if not parts or parts[0] != verified_commit:
            diagnostics["verified_commit"] = verified_commit
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        parent_count = len(parts) - 1
        diagnostics["verified_commit"] = verified_commit
        diagnostics["verified_commit_parent_count"] = parent_count
        if parent_count == 0:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_missing",
                infra_error=True,
                candidate_invalid=False,
            )
        if parent_count >= 2:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_merge_unsupported",
                infra_error=True,
                candidate_invalid=False,
            )
        parent = parts[1]
        parent_resolve = _run_git(worktree, ("rev-parse", "--verify", f"{parent}^{{commit}}"))
        if parent_resolve.returncode != 0:
            diagnostics["base_sha"] = parent
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        return _CommitPair(verified_commit=verified_commit, base_sha=parent, parent_count=parent_count)

    def _model_patch_from_pair(
        self,
        worktree: Path,
        pair: _CommitPair,
        *,
        diagnostics: dict[str, object],
    ) -> tuple[str, tuple[str, ...]] | OracleResult:
        started = time.perf_counter()
        range_spec = f"{pair.base_sha}..{pair.verified_commit}"
        diff = _run_git(worktree, ("diff", "--binary", "--find-renames", range_spec))
        if diff.returncode != 0:
            diagnostics.update(
                {
                    "git_command": f"git diff --binary --find-renames {range_spec}",
                    "git_returncode": diff.returncode,
                    "git_stderr": _decode_replace(diff.stderr),
                }
            )
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_git_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            model_patch = _decode_strict(diff.stdout)
        except UnicodeDecodeError:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_decode_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        names = _run_git(worktree, ("diff", "--name-only", "-z", "--find-renames", range_spec))
        if names.returncode != 0:
            diagnostics.update(
                {
                    "git_command": f"git diff --name-only -z --find-renames {range_spec}",
                    "git_returncode": names.returncode,
                    "git_stderr": _decode_replace(names.stderr),
                }
            )
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_git_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            changed_paths = _decode_z_paths(names.stdout)
        except UnicodeDecodeError:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_decode_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        except ValueError:
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_path_malformed",
                infra_error=False,
                candidate_invalid=True,
            )
        if not model_patch.strip():
            diagnostics["changed_paths"] = list(changed_paths)
            return failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_empty",
                infra_error=False,
                candidate_invalid=True,
            )
        return model_patch, changed_paths
