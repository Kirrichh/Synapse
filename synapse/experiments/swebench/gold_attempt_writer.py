"""Write-boundary gate for GOLD attempt records.

This module only writes GOLD attempt JSONL records. It does not execute GOLD,
run oracles, call SWE-bench, or decide FULL verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import json

from synapse.experiments.swebench.contract import ExperimentArm
from synapse.experiments.swebench.gold_evidence import (
    GOLD_EVIDENCE_MISSING,
    GoldEvidence,
    validate_gold_evidence,
)


SCHEMA = "synapse.experiments.swebench.gold_attempt/v1"

GOLD_ATTEMPT_WRITTEN = "GOLD_ATTEMPT_WRITTEN"
GOLD_EVIDENCE_REJECTED = "GOLD_EVIDENCE_REJECTED"
GOLD_ATTEMPT_WRITE_FAILED = "GOLD_ATTEMPT_WRITE_FAILED"
GOLD_ORACLE_BINDING_REQUIRED = "GOLD_ORACLE_BINDING_REQUIRED"
GOLD_ATTEMPT_STATUS_INVALID = "GOLD_ATTEMPT_STATUS_INVALID"
GOLD_ATTEMPT_JSONL_WRITE_FAILED = "GOLD_ATTEMPT_JSONL_WRITE_FAILED"

EVIDENCE_ONLY_GOLD_STATUSES = frozenset({
    "GOLD_APPLIED_WITH_EVIDENCE",
})

RESERVED_FULL_GOLD_STATUSES = frozenset({
    "GOLD_FULL_VERIFIED",
})

NON_SUCCESS_GOLD_STATUSES = frozenset({
    "GOLD_EVIDENCE_REJECTED",
    "GOLD_ORACLE_UNRESOLVED",
    "GOLD_NO_CANDIDATE",
    "GOLD_INFRA_ERROR",
})


@dataclass(frozen=True)
class GoldAttemptWriteResult:
    ok: bool
    status: str
    path: str | None
    failure_code: str | None
    detail: str | None


def _gold_evidence_json(evidence: GoldEvidence) -> dict[str, str]:
    return {
        "evidence_ref": evidence.evidence_ref,
        "verified_commit": evidence.verified_commit,
        "report_path": evidence.report_path,
        "report_sha256": evidence.report_sha256,
        "base_sha": evidence.base_sha,
        "task_contract_sha256": evidence.task_contract_sha256,
        "patch_sha256": evidence.patch_sha256,
    }


class GoldAttemptWriter:
    def __init__(
        self,
        run_root: str | Path,
        *,
        repo_root: str | Path,
        report_root: str | Path | None = None,
    ) -> None:
        self.run_root = Path(run_root)
        self.repo_root = Path(repo_root)
        self.report_root = Path(report_root) if report_root is not None else None
        self.path = self.run_root / "gold_attempts.jsonl"

    def write_attempt(
        self,
        *,
        attempt_id: str,
        run_id: str,
        status: str,
        gold_evidence: GoldEvidence | None,
        payload: Mapping[str, Any] | None = None,
    ) -> GoldAttemptWriteResult:
        payload_value: Mapping[str, Any] = {} if payload is None else payload
        if not status.startswith("GOLD_"):
            return GoldAttemptWriteResult(
                ok=False,
                status=GOLD_ATTEMPT_WRITE_FAILED,
                path=None,
                failure_code=GOLD_ATTEMPT_STATUS_INVALID,
                detail=f"status is not a GOLD attempt status: {status}",
            )

        if status in RESERVED_FULL_GOLD_STATUSES:
            return self._write_rejected(
                attempt_id=attempt_id,
                run_id=run_id,
                requested_status=status,
                failure_code=GOLD_ORACLE_BINDING_REQUIRED,
                detail="oracle binding is required before GOLD_FULL_VERIFIED can be written",
                payload=payload_value,
            )

        evidence_required = status not in NON_SUCCESS_GOLD_STATUSES
        if gold_evidence is None:
            if evidence_required:
                return self._write_rejected(
                    attempt_id=attempt_id,
                    run_id=run_id,
                    requested_status=status,
                    failure_code=GOLD_EVIDENCE_MISSING,
                    detail="gold_evidence is required for success-like GOLD status",
                    payload=payload_value,
                )
            return self._write_record(
                {
                    "schema": SCHEMA,
                    "attempt_id": attempt_id,
                    "run_id": run_id,
                    "arm": ExperimentArm.GOLD.value,
                    "status": status,
                    "requested_status": status,
                    "gold_evidence": None,
                    "payload": payload_value,
                    "writer": {"validation": "GOLD_EVIDENCE_NOT_REQUIRED"},
                },
                success_status=GOLD_ATTEMPT_WRITTEN,
            )

        validation = validate_gold_evidence(
            gold_evidence,
            repo_root=self.repo_root,
            report_root=self.report_root,
        )
        if not validation.ok:
            return self._write_rejected(
                attempt_id=attempt_id,
                run_id=run_id,
                requested_status=status,
                failure_code=validation.failure_code or GOLD_EVIDENCE_REJECTED,
                detail=validation.detail or "gold_evidence validation failed",
                payload=payload_value,
            )

        return self._write_record(
            {
                "schema": SCHEMA,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "arm": ExperimentArm.GOLD.value,
                "status": status,
                "requested_status": status,
                "gold_evidence": _gold_evidence_json(gold_evidence),
                "payload": payload_value,
                "writer": {"validation": "GOLD_EVIDENCE_VALIDATED"},
            },
            success_status=GOLD_ATTEMPT_WRITTEN,
        )

    def _write_rejected(
        self,
        *,
        attempt_id: str,
        run_id: str,
        requested_status: str,
        failure_code: str,
        detail: str,
        payload: Mapping[str, Any],
    ) -> GoldAttemptWriteResult:
        return self._write_record(
            {
                "schema": SCHEMA,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "arm": ExperimentArm.GOLD.value,
                "status": GOLD_EVIDENCE_REJECTED,
                "requested_status": requested_status,
                "gold_evidence": None,
                "failure_code": failure_code,
                "detail": detail,
                "payload": payload,
                "writer": {"validation": "GOLD_EVIDENCE_REJECTED"},
            },
            success_status=GOLD_EVIDENCE_REJECTED,
            failure_code=failure_code,
            detail=detail,
        )

    def _write_record(
        self,
        record: Mapping[str, Any],
        *,
        success_status: str,
        failure_code: str | None = None,
        detail: str | None = None,
    ) -> GoldAttemptWriteResult:
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except (OSError, TypeError, ValueError) as exc:
            return GoldAttemptWriteResult(
                ok=False,
                status=GOLD_ATTEMPT_WRITE_FAILED,
                path=None,
                failure_code=GOLD_ATTEMPT_JSONL_WRITE_FAILED,
                detail=f"failed to write gold attempt record: {exc}",
            )
        return GoldAttemptWriteResult(
            ok=success_status == GOLD_ATTEMPT_WRITTEN,
            status=success_status,
            path=str(self.path),
            failure_code=failure_code,
            detail=detail,
        )
