"""§26 acceptance: the stop policy decides, and decides the same way twice.

The policy is the only place a run may conclude that another attempt should
follow. These checks pin the two rules that carry §26 — a continuation needs new
knowledge, and an explicit fallback is a Baseline arm rather than a Gold result —
and the fail-closed typing that keeps a caller from asking the question loosely.

Light by construction: pure decisions, no store, no world.
"""

from __future__ import annotations

import pytest

from synapse.experiments.gold.runner import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunViolation,
    RunFinalStatus,
    TerminalDecisionKind,
    decide_next_attempt,
    final_status_for_decision,
)

ARM = "run-baseline-arm"


def decide(outcome, *, used=1, limit=3, new_knowledge=True, fallback=FallbackPolicy.FORBIDDEN):
    return decide_next_attempt(
        outcome=outcome,
        attempts_used=used,
        max_attempts=limit,
        new_knowledge_available=new_knowledge,
        fallback_policy=fallback,
        fallback_arm_id=ARM,
    )


def test_a_resolved_attempt_ends_the_run() -> None:
    assert decide(AttemptOutcome.RESOLVED).decision is TerminalDecisionKind.STOP_SUCCESS


def test_a_continuation_requires_newly_admitted_knowledge() -> None:
    """§26: the next attempt may run only on new or revalidated knowledge."""

    with_knowledge = decide(AttemptOutcome.UNRESOLVED, new_knowledge=True)
    without = decide(AttemptOutcome.UNRESOLVED, new_knowledge=False)
    assert with_knowledge.decision is TerminalDecisionKind.CONTINUE
    assert without.decision is TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE


def test_the_budget_counts_every_started_attempt() -> None:
    assert decide(AttemptOutcome.UNRESOLVED, used=3, limit=3).decision is TerminalDecisionKind.STOP_LIMIT


def test_an_invalid_c1_result_stops_the_run_immediately() -> None:
    """A C1 result the boundary could not classify is never retried around."""

    decision = decide(AttemptOutcome.C1_RESULT_INVALID, used=1, limit=5, new_knowledge=True)
    assert decision.decision is TerminalDecisionKind.STOP_UNRECOVERABLE


def test_exhausted_infrastructure_falls_back_only_when_the_policy_allows_it() -> None:
    forbidden = decide(AttemptOutcome.INFRA_ERROR, used=2, limit=2)
    explicit = decide(
        AttemptOutcome.INFRA_ERROR, used=2, limit=2, fallback=FallbackPolicy.EXPLICIT_BASELINE_ARM
    )
    assert forbidden.decision is TerminalDecisionKind.STOP_UNRECOVERABLE
    assert forbidden.fallback_arm_id is None
    assert explicit.decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT
    assert explicit.fallback_arm_id == ARM


def test_an_explicit_fallback_is_not_a_gold_status() -> None:
    """NR-13: a fallback keeps its own identity and is never Gold execution."""

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
        {"new_knowledge": 1},
        {"fallback": "EXPLICIT_BASELINE_ARM"},
        {"used": 0},
    ],
)
def test_the_policy_refuses_inexact_inputs(kwargs: dict) -> None:
    """A loose call is a refusal, not a coerced answer."""

    base = {"outcome": AttemptOutcome.UNRESOLVED}
    base.update(kwargs)
    with pytest.raises(GoldRunViolation):
        decide(**base)
