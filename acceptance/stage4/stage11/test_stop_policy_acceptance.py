"""§26 acceptance for deterministic continuation, budget, and fallback policy."""

from __future__ import annotations

import pytest

from synapse.experiments.gold.runner.stop_policy import (
    KnowledgeContinuationStatus,
    decide_next_attempt,
)
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunViolation,
    RunFinalStatus,
    TerminalDecisionKind,
    final_status_for_decision,
)


ARM = "run-baseline-arm"


def decide(
    outcome,
    *,
    used=1,
    limit=3,
    knowledge=KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED,
    fallback=FallbackPolicy.FORBIDDEN,
):
    return decide_next_attempt(
        outcome=outcome,
        attempts_used=used,
        max_attempts=limit,
        knowledge_status=knowledge,
        fallback_policy=fallback,
        fallback_arm_id=ARM,
    )


def test_a_resolved_attempt_ends_the_run() -> None:
    assert decide(AttemptOutcome.RESOLVED).decision is TerminalDecisionKind.STOP_SUCCESS


def test_a_continuation_requires_newly_admitted_or_revalidated_knowledge() -> None:
    available = decide(AttemptOutcome.UNRESOLVED)
    absent = decide(
        AttemptOutcome.UNRESOLVED,
        knowledge=KnowledgeContinuationStatus.NO_NEW_KNOWLEDGE,
    )
    assert available.decision is TerminalDecisionKind.CONTINUE
    assert absent.decision is TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE


def test_the_budget_counts_every_started_attempt() -> None:
    assert decide(AttemptOutcome.UNRESOLVED, used=3, limit=3).decision is TerminalDecisionKind.STOP_LIMIT


def test_an_invalid_c1_result_stops_immediately() -> None:
    decision = decide(AttemptOutcome.C1_RESULT_INVALID, used=1, limit=5)
    assert decision.decision is TerminalDecisionKind.STOP_UNRECOVERABLE


def test_delivery_unavailability_is_infrastructure_not_a_consumption_refusal() -> None:
    unavailable = decide(AttemptOutcome.DELIVERY_UNAVAILABLE, used=1, limit=1)
    explicit = decide(
        AttemptOutcome.DELIVERY_UNAVAILABLE,
        used=1,
        limit=1,
        fallback=FallbackPolicy.EXPLICIT_BASELINE_ARM,
    )
    assert unavailable.decision is TerminalDecisionKind.STOP_UNRECOVERABLE
    assert explicit.decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT
    assert explicit.fallback_arm_id == ARM


def test_an_unavailable_knowledge_dependency_stops_or_falls_back_explicitly() -> None:
    unavailable = decide(
        AttemptOutcome.UNRESOLVED,
        knowledge=KnowledgeContinuationStatus.DEPENDENCY_UNAVAILABLE,
    )
    explicit = decide(
        AttemptOutcome.UNRESOLVED,
        knowledge=KnowledgeContinuationStatus.DEPENDENCY_UNAVAILABLE,
        fallback=FallbackPolicy.EXPLICIT_BASELINE_ARM,
    )
    assert unavailable.decision is TerminalDecisionKind.STOP_UNRECOVERABLE
    assert explicit.decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT


def test_explicit_fallback_never_projects_to_a_gold_status() -> None:
    status = final_status_for_decision(TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT)
    assert status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT
    assert not status.value.startswith("GOLD")


def test_a_non_terminal_decision_has_no_final_status() -> None:
    with pytest.raises(GoldRunViolation):
        final_status_for_decision(TerminalDecisionKind.CONTINUE)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outcome": "ATTEMPT_RESOLVED"},
        {"knowledge": "NEWLY_ADMITTED_OR_REVALIDATED"},
        {"fallback": "EXPLICIT_BASELINE_ARM"},
        {"used": 0},
    ],
)
def test_inexact_policy_inputs_are_refused(kwargs: dict) -> None:
    arguments = {"outcome": AttemptOutcome.UNRESOLVED}
    arguments.update(kwargs)
    with pytest.raises(GoldRunViolation):
        decide(**arguments)
