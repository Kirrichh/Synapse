"""Multi-attempt Gold run controller (Stage 4 §26).

The package boundary exports the §26 lifecycle records, the controller, the
delivery owner's plan type and the deterministic policy helpers — the surface a
composition root needs and nothing beyond it. Internal modules keep no public
contract of their own.

Production entry stays with the unified lifecycle (NR-01/NR-02): assembling a
run is ``runner_composition.py``'s job, and nothing here is an entrypoint.
"""

from synapse.experiments.gold.runner.c1_boundary import (
    AttemptClassification,
    C1AttemptBoundary,
    classify_c1_attempt,
    run_c1_attempt,
)
from synapse.experiments.gold.runner.controller import GoldRunController
from synapse.experiments.gold.runner.delivery import (
    AttemptDeliveryPlan,
    WorkerDelivery,
    deliver_attempt_context,
)
from synapse.experiments.gold.runner.models import (
    AttemptPhaseRefs,
    AttemptSummary,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunConfig,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from synapse.experiments.gold.runner.stop_policy import DecisionDraft, decide_next_attempt
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
    RunFinalStatus,
    TerminalDecisionKind,
    final_status_for_decision,
)

__all__ = [
    "AttemptClassification",
    "AttemptDeliveryPlan",
    "AttemptOutcome",
    "AttemptPhaseRefs",
    "AttemptSummary",
    "C1AttemptBoundary",
    "DecisionDraft",
    "FallbackPolicy",
    "GoldAttemptContext",
    "GoldAttemptResult",
    "GoldRunConfig",
    "GoldRunController",
    "GoldRunFailureCode",
    "GoldRunManifest",
    "GoldRunResult",
    "GoldRunViolation",
    "NextAttemptDecision",
    "RunFinalStatus",
    "TerminalDecisionKind",
    "WorkerDelivery",
    "classify_c1_attempt",
    "decide_next_attempt",
    "deliver_attempt_context",
    "final_status_for_decision",
    "run_c1_attempt",
]
