"""Applied verification: Baseline and Gold attempts judged by the independent change oracle."""

from .baseline import run_baseline_task
from .contract import (
    ArtifactRef,
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
    OracleResult,
    PrimaryMetricStatus,
    TokenAccountingRecord,
    UsageConsistencyStatus,
    UsageSource,
)
from .oracle import CommandOracleRunner, OracleRunner

__all__ = [
    "ArtifactRef",
    "AttemptVerdict",
    "BaselineAttemptRecord",
    "BaselineRunRecord",
    "BaselineTask",
    "CommandOracleRunner",
    "ExperimentArm",
    "OracleResult",
    "OracleRunner",
    "PrimaryMetricStatus",
    "TokenAccountingRecord",
    "UsageConsistencyStatus",
    "UsageSource",
    "run_baseline_task",
]
