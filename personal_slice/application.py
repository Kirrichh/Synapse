"""Atomic local-ref application for Personal Slice verified commits."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from .git_workspace import checked_out_branch_refs, git

ZERO_OID = "0" * 40


@dataclass
class ApplicationResult:
    status: str
    application_scope: str
    remote_updated: bool
    expected_old_sha: str
    actual_old_sha: str | None
    verified_commit: str
    diagnostics: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _result(status: str, base_revision: str, actual_old_sha: str | None, verified_commit: str, diagnostics: list[str]) -> ApplicationResult:
    return ApplicationResult(
        status=status,
        application_scope="LOCAL_REF_ONLY",
        remote_updated=False,
        expected_old_sha=base_revision,
        actual_old_sha=actual_old_sha,
        verified_commit=verified_commit,
        diagnostics=diagnostics,
    )


def apply_verified_commit(repo_root: str | Path, target_ref: str, base_revision: str, verified_commit: str) -> ApplicationResult:
    if target_ref in checked_out_branch_refs(repo_root):
        return _result(
            "INTERNAL_ERROR",
            base_revision,
            target_ref,
            verified_commit,
            ["UNSAFE_TARGET_REF_CHECKED_OUT_IN_WORKTREE"],
        )

    current = git(["rev-parse", "--verify", target_ref], repo_root, check=False)
    if current.returncode != 0:
        created = git(["update-ref", target_ref, base_revision, ZERO_OID], repo_root, check=False)
        if created.returncode != 0:
            reread = git(["rev-parse", "--verify", target_ref], repo_root, check=False)
            if reread.returncode != 0:
                return _result(
                    "INTERNAL_ERROR",
                    base_revision,
                    None,
                    verified_commit,
                    [created.stderr.strip() or created.stdout.strip() or "git update-ref create CAS failed"],
                )
            actual_old_sha = reread.stdout.strip()
        else:
            actual_old_sha = base_revision
    else:
        actual_old_sha = current.stdout.strip()

    if actual_old_sha != base_revision:
        return _result(
            "APPLICATION_STALE_BASE",
            base_revision,
            actual_old_sha,
            verified_commit,
            ["target ref does not point at expected base revision"],
        )

    updated = git(["update-ref", target_ref, verified_commit, base_revision], repo_root, check=False)
    if updated.returncode != 0:
        reread = git(["rev-parse", "--verify", target_ref], repo_root, check=False)
        fresh = reread.stdout.strip() if reread.returncode == 0 else actual_old_sha
        return _result(
            "APPLICATION_STALE_BASE",
            base_revision,
            fresh,
            verified_commit,
            [updated.stderr.strip() or updated.stdout.strip() or "git update-ref CAS failed"],
        )

    return _result("APPLIED", base_revision, actual_old_sha, verified_commit, [])
