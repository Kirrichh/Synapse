"""Atomic local-ref application for controlled-change verified commits."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from .workspace import ZERO_OID, git


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


def reject_checked_out_target(repo_root: str | Path, target_ref: str) -> ApplicationResult | None:
    checkout = git(["symbolic-ref", "-q", "HEAD"], repo_root, check=False)
    checkout_ref = checkout.stdout.strip() if checkout.returncode == 0 else None
    if checkout_ref != target_ref:
        return None
    return ApplicationResult(
        status="INTERNAL_ERROR",
        application_scope="LOCAL_REF_ONLY",
        remote_updated=False,
        expected_old_sha="",
        actual_old_sha=checkout_ref,
        verified_commit="",
        diagnostics=["UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT"],
        application_attempted=False,
    )


def apply_verified_commit(repo_root: str | Path, target_ref: str, base_revision: str, verified_commit: str, *, evidence_ref: str | None = None) -> ApplicationResult:
    checkout = git(["symbolic-ref", "-q", "HEAD"], repo_root, check=False)
    checkout_ref = checkout.stdout.strip() if checkout.returncode == 0 else None
    if checkout_ref == target_ref:
        return ApplicationResult(
            status="INTERNAL_ERROR",
            application_scope="LOCAL_REF_ONLY",
            remote_updated=False,
            expected_old_sha=base_revision,
            actual_old_sha=checkout_ref,
            verified_commit=verified_commit,
            diagnostics=["UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT"],
            evidence_ref=evidence_ref,
            application_attempted=False,
        )

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
