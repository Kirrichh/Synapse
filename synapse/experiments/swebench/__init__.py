"""Stage 3A SWE-bench baseline experiment harness."""

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
from .mini_config import MiniInvocationConfig
from .oracle import CommandOracleRunner, OracleRunner

__all__ = [
    "ArtifactRef",
    "AttemptVerdict",
    "BaselineAttemptRecord",
    "BaselineRunRecord",
    "BaselineTask",
    "CommandOracleRunner",
    "ExperimentArm",
    "MiniInvocationConfig",
    "OracleResult",
    "OracleRunner",
    "PrimaryMetricStatus",
    "TokenAccountingRecord",
    "UsageConsistencyStatus",
    "UsageSource",
    "run_baseline_task",
]
