"""Public contracts for the Stage 3A SWE-bench baseline harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from synapse.worker import ExternalCodingWorkerResult


class ExperimentArm(str, Enum):
    BASELINE = "BASELINE"
    GOLD = "GOLD"


class AttemptVerdict(str, Enum):
    ORACLE_RESOLVED = "ORACLE_RESOLVED"
    ORACLE_UNRESOLVED = "ORACLE_UNRESOLVED"
    NO_CANDIDATE = "NO_CANDIDATE"
    INFRA_ERROR = "INFRA_ERROR"


class PrimaryMetricStatus(str, Enum):
    PRIMARY_USABLE = "PRIMARY_USABLE"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_USAGE_INCONSISTENT = "PROVIDER_USAGE_INCONSISTENT"


class UsageConsistencyStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    MISSING_TOTAL = "MISSING_TOTAL"
    COMPONENT_SUM_EXCEEDS_TOTAL = "COMPONENT_SUM_EXCEEDS_TOTAL"
    COMPONENTS_INCOMPLETE = "COMPONENTS_INCOMPLETE"


class UsageSource(str, Enum):
    PROVIDER_REPORTED_DIRECT = "PROVIDER_REPORTED_DIRECT"
    PROVIDER_REPORTED_VIA_TOOL_TRAJECTORY = "PROVIDER_REPORTED_VIA_TOOL_TRAJECTORY"
    TOOL_REPORTED = "TOOL_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    path: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class TokenAccountingRecord:
    arm: ExperimentArm
    usage_source: UsageSource
    primary_metric_status: PrimaryMetricStatus
    usage_consistency: UsageConsistencyStatus
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    thinking_included: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm.value,
            "usage_source": self.usage_source.value,
            "primary_metric_status": self.primary_metric_status.value,
            "usage_consistency": self.usage_consistency.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "total_tokens": self.total_tokens,
            "thinking_included": self.thinking_included,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class OracleResult:
    resolved: bool
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class BaselineTask:
    task_id: str
    instance_id: str
    statement: str
    allowed_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_scope", tuple(self.allowed_scope))

    def to_worker_payload(self, prompt: str) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "task": prompt,
            "allowed_scope": list(self.allowed_scope),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "statement": self.statement,
            "allowed_scope": list(self.allowed_scope),
        }


@dataclass(frozen=True)
class BaselineAttemptRecord:
    attempt_id: int
    arm: ExperimentArm
    verdict: AttemptVerdict
    worker_result: ExternalCodingWorkerResult
    token_accounting: TokenAccountingRecord
    oracle_result: OracleResult | None
    artifacts: tuple[ArtifactRef, ...]
    started_at_utc: str
    finished_at_utc: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "arm": self.arm.value,
            "verdict": self.verdict.value,
            "worker_result": self.worker_result.to_dict(),
            "token_accounting": self.token_accounting.to_dict(),
            "oracle_result": self.oracle_result.to_dict() if self.oracle_result else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class BaselineRunRecord:
    run_id: str
    task_id: str
    instance_id: str
    arm: ExperimentArm
    base_revision: str
    replicate_id: int
    max_attempts: int
    resolved: bool
    attempts: tuple[BaselineAttemptRecord, ...]
    total_provider_tokens: int | None
    primary_metric_usable: bool
    started_at_utc: str
    finished_at_utc: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempts", tuple(self.attempts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "arm": self.arm.value,
            "base_revision": self.base_revision,
            "replicate_id": self.replicate_id,
            "max_attempts": self.max_attempts,
            "resolved": self.resolved,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "total_provider_tokens": self.total_provider_tokens,
            "primary_metric_usable": self.primary_metric_usable,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "diagnostics": dict(self.diagnostics),
        }
