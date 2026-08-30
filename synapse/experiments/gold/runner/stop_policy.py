"""Whether a finished attempt is followed by another one, and why.

§26 requires the retry decision to be frozen policy rather than a judgement made
at the call site, so this module is a pure function of four inputs: how the
attempt ended, how much budget the run has left, whether newly admitted or
revalidated knowledge exists for the next attempt, and what the run's fallback
policy allows. It holds no state, performs no I/O and imports nothing outside
this package — in particular it never learns the C1 status vocabulary, because a
stop rule that reads C1 labels directly is a stop rule that changes whenever C1
adds a status.

Two rules here are the ones a reviewer should check first. Continuation always
requires new knowledge: repeating an attempt against the same knowledge is a
hidden retry, and §26 forbids it. And an explicit Baseline fallback mints a new
arm identity and is reported as a Baseline status, never as a Gold one — a
fallback counted as Gold execution is exactly the claim NR-13 refuses.
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
    TerminalDecisionKind,
)

#: Closed reason vocabulary persisted inside NextAttemptDecision.
REASON_RESOLVED_RUN_COMPLETE = "ATTEMPT_RESOLVED_RUN_COMPLETE"
REASON_RETRY_NEW_KNOWLEDGE = "RETRY_ALLOWED_WITH_NEW_KNOWLEDGE"
REASON_STOP_LIMIT = "ATTEMPT_LIMIT_REACHED"
REASON_STOP_NO_NEW_KNOWLEDGE = "NO_NEW_KNOWLEDGE_FOR_NEXT_ATTEMPT"
REASON_C1_RESULT_INVALID = "C1_RESULT_INVALID_FAIL_CLOSED"
REASON_INFRA_EXHAUSTED = "INFRA_EXHAUSTED_RUN_UNAVAILABLE"
REASON_INFRA_EXPLICIT_FALLBACK = "INFRA_EXHAUSTED_EXPLICIT_BASELINE_ARM"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class DecisionDraft:
    """The stop policy's answer before any record is persisted."""

    decision: TerminalDecisionKind
    reason: str
    fallback_arm_id: str | None


def _fallback_or_unavailable(
    *,
    fallback_policy: FallbackPolicy,
    fallback_arm_id: str,
) -> DecisionDraft:
    """Infrastructure is exhausted: fall back explicitly, or stop."""

    if fallback_policy is FallbackPolicy.EXPLICIT_BASELINE_ARM:
        return DecisionDraft(
            TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT,
            REASON_INFRA_EXPLICIT_FALLBACK,
            fallback_arm_id,
        )
    return DecisionDraft(TerminalDecisionKind.STOP_UNRECOVERABLE, REASON_INFRA_EXHAUSTED, None)


def decide_next_attempt(
    *,
    outcome: AttemptOutcome,
    attempts_used: int,
    max_attempts: int,
    new_knowledge_available: bool,
    fallback_policy: FallbackPolicy,
    fallback_arm_id: str,
) -> DecisionDraft:
    """Decide deterministically what follows one finished attempt.

    The same function serves live execution and crash recovery, which is what
    makes a resumed run reach the same decision as the run that crashed. The
    budget counts every started attempt, interrupted ones included, so a crash
    loop cannot buy extra attempts by leaving results unwritten.
    """

    if type(outcome) is not AttemptOutcome:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "outcome must be exact")
    if type(attempts_used) is not int or type(max_attempts) is not int:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt counters must be exact integers")
    if attempts_used < 1 or max_attempts < 1:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "attempt counters must be positive")
    if type(new_knowledge_available) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "new_knowledge_available must be an exact bool")
    if type(fallback_policy) is not FallbackPolicy:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "fallback_policy must be exact")

    budget_left = attempts_used < max_attempts
    if outcome is AttemptOutcome.RESOLVED:
        return DecisionDraft(TerminalDecisionKind.STOP_SUCCESS, REASON_RESOLVED_RUN_COMPLETE, None)
    if outcome is AttemptOutcome.C1_RESULT_INVALID:
        return DecisionDraft(TerminalDecisionKind.STOP_UNRECOVERABLE, REASON_C1_RESULT_INVALID, None)
    if not budget_left:
        if outcome in (AttemptOutcome.INFRA_ERROR, AttemptOutcome.CONTROLLER_INTERRUPTED):
            return _fallback_or_unavailable(
                fallback_policy=fallback_policy, fallback_arm_id=fallback_arm_id
            )
        return DecisionDraft(TerminalDecisionKind.STOP_LIMIT, REASON_STOP_LIMIT, None)
    if not new_knowledge_available:
        return DecisionDraft(
            TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE, REASON_STOP_NO_NEW_KNOWLEDGE, None
        )
    return DecisionDraft(TerminalDecisionKind.CONTINUE, REASON_RETRY_NEW_KNOWLEDGE, None)
