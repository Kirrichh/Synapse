"""The one place Stage 4 knows the C1 attempt boundary exists.

NR-05 keeps ``swebench/gold_runner.py`` a single-attempt C1 adapter that Stage 4
calls rather than absorbs, and NR-07 refuses a pass-through wrapper as an
implementation. This module is the boundary that satisfies both: it calls
``run_gold_attempt()`` directly with the C1 arguments, and it converts the C1
result into the controller's own vocabulary — which is work, not forwarding,
because the controller's ``AttemptOutcome`` is not the C1 status set and must
not be.

Concentrating the edge here is deliberate. Every other module in this package is
free of any swebench import, so the stop policy cannot start branching on a C1
label, the records cannot start storing a C1 payload, and the dependency
tripwire has exactly one module to keep narrow.

The classification is defensive on purpose. C1 owns whether an attempt applied
and whether its oracle resolved; this module owns whether that combination may
be called *resolved* at the run level. An ``APPLIED_WITH_EVIDENCE`` status whose
oracle was never invoked, or whose oracle did not resolve, is refused as
``C1_RESULT_INVALID`` rather than promoted — the run never treats a missing
oracle as a success, even if a mutated boundary hands it one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    GoldRunFailureCode,
    GoldRunViolation,
)
from synapse.experiments.swebench.gold_attempt_writer import GoldAttemptWriter
from synapse.experiments.swebench.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_EVIDENCE_REJECTED,
    GOLD_INFRA_ERROR,
    GOLD_NO_CANDIDATE,
    GOLD_ORACLE_UNRESOLVED,
    GoldOracle,
    GoldRunnerCommandPolicy,
    GoldRunnerResult,
    run_gold_attempt,
)
from synapse.worker.contract import ExternalCodingWorkerResult

_C1_ACCEPTED_STATUSES = frozenset(
    {
        GOLD_NO_CANDIDATE,
        GOLD_INFRA_ERROR,
        GOLD_ORACLE_UNRESOLVED,
        GOLD_APPLIED_WITH_EVIDENCE,
        GOLD_EVIDENCE_REJECTED,
    }
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class AttemptClassification(NamedTuple):
    """What the controller concluded about one finished C1 attempt."""

    outcome: AttemptOutcome
    c1_status: str
    oracle_invoked: bool
    oracle_resolved: bool | None
    write_ok: bool


@dataclass(frozen=True)
class C1AttemptBoundary:
    """The exact C1 dependencies one run delegates every attempt to."""

    repo_root: Path
    command_policy: GoldRunnerCommandPolicy
    oracle: GoldOracle
    writer: GoldAttemptWriter
    environment_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.repo_root, Path):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "repo_root must be a Path")
        if type(self.command_policy) is not GoldRunnerCommandPolicy:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "command_policy must be the exact C1 policy")
        if type(self.environment_kind) is not str or not self.environment_kind:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "environment_kind must be a non-empty string")


def run_c1_attempt(
    boundary: C1AttemptBoundary,
    *,
    gold_run_id: str,
    attempt_id: str,
    worker_result: ExternalCodingWorkerResult,
    run_root: Path,
) -> GoldRunnerResult:
    """Delegate one attempt to the unchanged C1 boundary.

    The worker result arrives already obtained, exactly as ``run_gold_attempt``
    requires: this module does not run a worker, and the controller does not
    reach past it into C1.
    """

    if type(boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "c1 boundary must be exact")
    if type(worker_result) is not ExternalCodingWorkerResult:
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "worker result must be the exact worker contract type")
    if not isinstance(run_root, Path):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be a Path")
    return run_gold_attempt(
        repo_root=boundary.repo_root,
        gold_run_id=gold_run_id,
        attempt_id=attempt_id,
        worker_result=worker_result,
        command_policy=boundary.command_policy,
        oracle=boundary.oracle,
        writer=boundary.writer,
        run_root=run_root,
        environment_kind=boundary.environment_kind,
    )


def classify_c1_attempt(result: GoldRunnerResult) -> AttemptClassification:
    """Translate a C1 result into the controller's outcome vocabulary."""

    if type(result) is not GoldRunnerResult:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "c1 result must be an exact GoldRunnerResult")
    status = result.status
    payload = result.payload
    write_ok = bool(result.write_result.ok) if result.write_result is not None else False
    oracle_invoked = payload.get("oracle_invoked") is True
    if oracle_invoked:
        raw = payload.get("oracle_resolved")
        oracle_resolved: bool | None = raw if type(raw) is bool else None
    else:
        oracle_resolved = None

    if status not in _C1_ACCEPTED_STATUSES:
        return AttemptClassification(
            AttemptOutcome.C1_RESULT_INVALID, status, oracle_invoked, oracle_resolved, write_ok
        )
    if not write_ok:
        return AttemptClassification(AttemptOutcome.INFRA_ERROR, status, oracle_invoked, oracle_resolved, write_ok)
    if status == GOLD_APPLIED_WITH_EVIDENCE:
        if not oracle_invoked or result.oracle_result is None or oracle_resolved is not True:
            return AttemptClassification(
                AttemptOutcome.C1_RESULT_INVALID, status, oracle_invoked, oracle_resolved, write_ok
            )
        return AttemptClassification(AttemptOutcome.RESOLVED, status, oracle_invoked, oracle_resolved, write_ok)
    if status == GOLD_NO_CANDIDATE:
        return AttemptClassification(AttemptOutcome.NO_CANDIDATE, status, oracle_invoked, oracle_resolved, write_ok)
    if status == GOLD_INFRA_ERROR:
        return AttemptClassification(AttemptOutcome.INFRA_ERROR, status, oracle_invoked, oracle_resolved, write_ok)
    return AttemptClassification(AttemptOutcome.UNRESOLVED, status, oracle_invoked, oracle_resolved, write_ok)
