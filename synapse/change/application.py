"""Atomic local-ref application for controlled-change verified commits."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from .workspace import ZERO_OID, git, git_binary


@dataclass
class ApplicationResult:
    status: str
    application_scope: str
    remote_updated: bool
    expected_old_sha: str
    actual_old_sha: str | None
    verified_commit: str
    diagnostics: list[str]
    evidence_ref: str | None = None
    policy: str = "LOCAL_REF_CAS_ONLY"
    application_attempted: bool = False

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class WorktreeDiscoveryError(RuntimeError):
    """Raised when the set of checked-out refs cannot be determined.

    Unknown worktree state must FAIL CLOSED: if discovery is impossible,
    moving any ref is forbidden.
    """


def _parse_worktree_porcelain_z(data: bytes) -> dict[str, str]:
    """Parse `git worktree list --porcelain -z` output into {branch_ref: path}.

    Empirically (git 2.43.0) records are NUL-terminated attribute lines with
    an empty NUL-terminated line between records (double NUL). The framing
    is NOT treated as contractual: the parser is robust to every legal
    record-boundary interpretation:

        1. a new `worktree ` field flushes an already-started record;
        2. an empty token flushes the current record;
        3. a final flush runs after the data ends.

    A worktree pathname is a filesystem pathname and may legally contain
    spaces, Unicode, and newlines — NUL framing keeps it intact. Records
    without a `branch` field (detached, bare) are skipped explicitly.

    Fail-closed ambiguity: if one branch ref appears with two different
    worktree paths, the repository state is corrupted or ambiguous and a
    WorktreeDiscoveryError is raised instead of silently keeping either path.
    """
    refs: dict[str, str] = {}
    current_path: str | None = None
    current_branch: str | None = None

    def flush() -> None:
        nonlocal current_path, current_branch
        if current_branch and current_path is not None:
            existing = refs.get(current_branch)
            if existing is not None and existing != current_path:
                raise WorktreeDiscoveryError(
                    "WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH: branch "
                    f"{current_branch} reported in multiple worktrees: "
                    f"{existing!r} and {current_path!r}"
                )
            refs[current_branch] = current_path
        current_path = None
        current_branch = None

    for token in data.split(b"\x00"):
        if token == b"":
            flush()
            continue
        line = token.decode("utf-8", "replace")
        if line.startswith("worktree "):
            if current_path is not None or current_branch is not None:
                flush()
            current_path = line[len("worktree "):]
        elif line.startswith("branch "):
            current_branch = line[len("branch "):]
        # 'HEAD', 'detached', 'bare', 'locked', 'prunable' fields carry no
        # branch-pinning information and are intentionally ignored.
    flush()
    return refs


def checked_out_branch_refs(repo_root: str | Path) -> dict[str, str]:
    """Return {branch_ref: worktree_path} for every worktree of the repository.

    Raises WorktreeDiscoveryError if `git worktree list` fails for any
    reason (corrupted metadata, locks, permissions): the caller must treat
    unknown worktree state as a refusal, never as an empty set. The error
    message carries the detail only; the stable WORKTREE_DISCOVERY_FAILED
    code is attached once by the application boundary.
    """
    listing = git_binary(["worktree", "list", "--porcelain", "-z"], repo_root, check=False)
    if listing.returncode != 0:
        stderr = listing.stderr.decode("utf-8", "replace").strip()
        stdout = listing.stdout.decode("utf-8", "replace").strip()
        raise WorktreeDiscoveryError(
            f"git worktree list --porcelain -z exited {listing.returncode}: "
            f"{stderr or stdout or 'no output'}"
        )
    return _parse_worktree_porcelain_z(listing.stdout)


def _reject_if_target_checked_out(
    repo_root: str | Path,
    target_ref: str,
    *,
    base_revision: str,
    verified_commit: str,
    evidence_ref: str | None,
) -> ApplicationResult | None:
    """Single canonical owner of the checked-out-target policy.

    The target ref is unsafe to move if it is checked out in the MAIN
    worktree or in ANY linked worktree: `git update-ref`, unlike
    `git branch -f`, would silently move the branch out from under that
    worktree. Classification stays INTERNAL_ERROR +
    UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT until Patch 2c.

    If worktree discovery itself fails, the policy FAILS CLOSED with
    WORKTREE_DISCOVERY_FAILED: unknown worktree state forbids application.

    `actual_old_sha` is None in every refusal produced here: the CAS was
    never started, so no old value was observed.
    """
    try:
        refs = checked_out_branch_refs(repo_root)
    except WorktreeDiscoveryError as exc:
        return ApplicationResult(
            status="INTERNAL_ERROR",
            application_scope="LOCAL_REF_ONLY",
            remote_updated=False,
            expected_old_sha=base_revision,
            actual_old_sha=None,
            verified_commit=verified_commit,
            diagnostics=["WORKTREE_DISCOVERY_FAILED", str(exc)],
            evidence_ref=evidence_ref,
            application_attempted=False,
        )
    worktree_path = refs.get(target_ref)
    if worktree_path is None:
        return None
    return ApplicationResult(
        status="INTERNAL_ERROR",
        application_scope="LOCAL_REF_ONLY",
        remote_updated=False,
        expected_old_sha=base_revision,
        actual_old_sha=None,
        verified_commit=verified_commit,
        diagnostics=[
            "UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT",
            f"target ref is checked out in worktree: {worktree_path}",
        ],
        evidence_ref=evidence_ref,
        application_attempted=False,
    )


def apply_verified_commit(repo_root: str | Path, target_ref: str, base_revision: str, verified_commit: str, *, evidence_ref: str | None = None) -> ApplicationResult:
    rejection = _reject_if_target_checked_out(
        repo_root,
        target_ref,
        base_revision=base_revision,
        verified_commit=verified_commit,
        evidence_ref=evidence_ref,
    )
    if rejection is not None:
        return rejection

    current = git(["rev-parse", "--verify", target_ref], repo_root, check=False)
    if current.returncode != 0:
        actual_old_sha = None
        updated = git(["update-ref", target_ref, verified_commit, ZERO_OID], repo_root, check=False)
        expected = ZERO_OID
    else:
        actual_old_sha = current.stdout.strip()
        if actual_old_sha != base_revision:
            return ApplicationResult(
                status="APPLICATION_STALE_BASE",
                application_scope="LOCAL_REF_ONLY",
                remote_updated=False,
                expected_old_sha=base_revision,
                actual_old_sha=actual_old_sha,
                verified_commit=verified_commit,
                diagnostics=["target ref does not point at expected base revision"],
                evidence_ref=evidence_ref,
                application_attempted=False,
            )
        updated = git(["update-ref", target_ref, verified_commit, base_revision], repo_root, check=False)
        expected = base_revision

    if updated.returncode != 0:
        return ApplicationResult(
            status="APPLICATION_STALE_BASE",
            application_scope="LOCAL_REF_ONLY",
            remote_updated=False,
            expected_old_sha=expected,
            actual_old_sha=actual_old_sha,
            verified_commit=verified_commit,
            diagnostics=[updated.stderr.strip() or updated.stdout.strip() or "git update-ref CAS failed"],
            evidence_ref=evidence_ref,
            application_attempted=True,
        )

    return ApplicationResult(
        status="APPLIED",
        application_scope="LOCAL_REF_ONLY",
        remote_updated=False,
        expected_old_sha=expected,
        actual_old_sha=actual_old_sha,
        verified_commit=verified_commit,
        diagnostics=[],
        evidence_ref=evidence_ref,
        application_attempted=True,
    )
