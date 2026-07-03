"""Evidence validator for executable GOLD-success claims.

This module validates unsigned, in-toto-style bindings to a real
controlled-change report and evidence ref. It does not write evidence, run
workers, run SWE-bench, or construct experiment arms.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib
import json
import re
import subprocess
from typing import Any


EVIDENCE_REF_NAMESPACE = "refs/synapse/change/evidence/"

GOLD_EVIDENCE_MISSING = "GOLD_EVIDENCE_MISSING"
GOLD_EVIDENCE_STRUCTURAL_INVALID = "GOLD_EVIDENCE_STRUCTURAL_INVALID"
GOLD_EVIDENCE_REPORT_UNREADABLE = "GOLD_EVIDENCE_REPORT_UNREADABLE"
GOLD_EVIDENCE_REPORT_HASH_MISMATCH = "GOLD_EVIDENCE_REPORT_HASH_MISMATCH"
GOLD_EVIDENCE_REPORT_BINDING_MISMATCH = "GOLD_EVIDENCE_REPORT_BINDING_MISMATCH"
GOLD_EVIDENCE_NOT_CREATED = "GOLD_EVIDENCE_NOT_CREATED"
GOLD_EVIDENCE_REF_UNRESOLVABLE = "GOLD_EVIDENCE_REF_UNRESOLVABLE"
GOLD_EVIDENCE_REF_MISMATCH = "GOLD_EVIDENCE_REF_MISMATCH"
GOLD_EVIDENCE_APPLICATION_NOT_APPLIED = "GOLD_EVIDENCE_APPLICATION_NOT_APPLIED"
GOLD_EVIDENCE_LIFECYCLE_NOT_APPLIED = "GOLD_EVIDENCE_LIFECYCLE_NOT_APPLIED"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MISSING = object()


@dataclass(frozen=True)
class GoldEvidence:
    evidence_ref: str
    verified_commit: str
    report_path: str
    report_sha256: str
    base_sha: str
    task_contract_sha256: str
    patch_sha256: str


@dataclass(frozen=True)
class GoldEvidenceValidationResult:
    ok: bool
    failure_code: str | None
    detail: str | None


def _ok() -> GoldEvidenceValidationResult:
    return GoldEvidenceValidationResult(ok=True, failure_code=None, detail=None)


def _fail(code: str, detail: str) -> GoldEvidenceValidationResult:
    return GoldEvidenceValidationResult(ok=False, failure_code=code, detail=detail)


def _structural_validation(evidence: GoldEvidence) -> GoldEvidenceValidationResult | None:
    for field in fields(evidence):
        value = getattr(evidence, field.name)
        if not value:
            return _fail(GOLD_EVIDENCE_MISSING, f"{field.name} is required")

    for field_name in ("report_sha256", "task_contract_sha256", "patch_sha256"):
        value = getattr(evidence, field_name)
        if not _SHA256_RE.fullmatch(value):
            return _fail(
                GOLD_EVIDENCE_STRUCTURAL_INVALID,
                f"{field_name} must be 64 lowercase hex characters",
            )

    for field_name in ("verified_commit", "base_sha"):
        value = getattr(evidence, field_name)
        if not _COMMIT_RE.fullmatch(value):
            return _fail(
                GOLD_EVIDENCE_STRUCTURAL_INVALID,
                f"{field_name} must be 40 lowercase hex characters",
            )

    if not evidence.evidence_ref.startswith(EVIDENCE_REF_NAMESPACE):
        return _fail(
            GOLD_EVIDENCE_STRUCTURAL_INVALID,
            "evidence_ref must be under refs/synapse/change/evidence/",
        )

    report_path = evidence.report_path
    if "\x00" in report_path:
        return _fail(GOLD_EVIDENCE_STRUCTURAL_INVALID, "report_path contains NUL")
    if (
        Path(report_path).is_absolute()
        or PureWindowsPath(report_path).is_absolute()
        or PurePosixPath(report_path).is_absolute()
    ):
        return _fail(GOLD_EVIDENCE_STRUCTURAL_INVALID, "report_path must be relative")
    parts = report_path.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return _fail(GOLD_EVIDENCE_STRUCTURAL_INVALID, "report_path contains traversal")
    if any(part == "" for part in parts):
        return _fail(GOLD_EVIDENCE_STRUCTURAL_INVALID, "report_path contains empty segment")

    return None


def _resolve_report_file(report_base: Path, report_path: str) -> Path | None:
    try:
        base = report_base.resolve()
        candidate = (base / report_path).resolve()
        candidate.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _read_report(report_file: Path) -> tuple[bytes, Any] | GoldEvidenceValidationResult:
    try:
        report_bytes = report_file.read_bytes()
        report_text = report_bytes.decode("utf-8")
        return report_bytes, json.loads(report_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _fail(
            GOLD_EVIDENCE_REPORT_UNREADABLE,
            f"report_path unreadable or invalid JSON: {exc}",
        )


def _get(report: Any, *path: str) -> Any:
    current = report
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _binding_check(report: Any, path: tuple[str, ...], expected: str) -> GoldEvidenceValidationResult | None:
    actual = _get(report, *path)
    if actual != expected:
        return _fail(
            GOLD_EVIDENCE_REPORT_BINDING_MISMATCH,
            f"{'.'.join(path)} does not match GoldEvidence",
        )
    return None


def _resolve_evidence_ref(repo_root: Path, evidence_ref: str) -> str | GoldEvidenceValidationResult:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{evidence_ref}^{{commit}}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return _fail(
            GOLD_EVIDENCE_REF_UNRESOLVABLE,
            f"evidence_ref is not resolvable: {evidence_ref}",
        )
    return completed.stdout.strip()


def validate_gold_evidence(
    evidence: GoldEvidence,
    *,
    repo_root: str | Path,
    report_root: str | Path | None = None,
) -> GoldEvidenceValidationResult:
    """Validate a GOLD evidence attestation against explicit repo/report roots."""

    structural_error = _structural_validation(evidence)
    if structural_error is not None:
        return structural_error

    repo = Path(repo_root)
    report_base = Path(report_root) if report_root is not None else repo
    report_file = _resolve_report_file(report_base, evidence.report_path)
    if report_file is None:
        return _fail(
            GOLD_EVIDENCE_REPORT_UNREADABLE,
            "report_path could not be safely resolved under report_root",
        )

    read_result = _read_report(report_file)
    if isinstance(read_result, GoldEvidenceValidationResult):
        return read_result
    report_bytes, report = read_result

    actual_report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if actual_report_sha256 != evidence.report_sha256:
        return _fail(
            GOLD_EVIDENCE_REPORT_HASH_MISMATCH,
            "report_sha256 does not match report file bytes",
        )

    for path, expected in (
        (("verified_result", "verified_commit"), evidence.verified_commit),
        (("trusted_inputs", "base_commit"), evidence.base_sha),
        (("trusted_inputs", "task_contract_sha256"), evidence.task_contract_sha256),
        (("trusted_inputs", "patch_sha256"), evidence.patch_sha256),
        (("evidence", "evidence_ref"), evidence.evidence_ref),
    ):
        binding_error = _binding_check(report, path, expected)
        if binding_error is not None:
            return binding_error

    if _get(report, "evidence", "result_status") != "EVIDENCE_CREATED":
        return _fail(
            GOLD_EVIDENCE_NOT_CREATED,
            "evidence.result_status is not EVIDENCE_CREATED",
        )

    if _get(report, "application", "result_status") != "APPLIED":
        return _fail(
            GOLD_EVIDENCE_APPLICATION_NOT_APPLIED,
            "application.result_status is not APPLIED",
        )

    if _get(report, "lifecycle", "outcome") != "APPLIED":
        return _fail(
            GOLD_EVIDENCE_LIFECYCLE_NOT_APPLIED,
            "lifecycle.outcome is not APPLIED",
        )

    resolved = _resolve_evidence_ref(repo, evidence.evidence_ref)
    if isinstance(resolved, GoldEvidenceValidationResult):
        return resolved
    if resolved != evidence.verified_commit:
        return _fail(
            GOLD_EVIDENCE_REF_MISMATCH,
            "evidence_ref does not resolve to verified_commit",
        )

    return _ok()
