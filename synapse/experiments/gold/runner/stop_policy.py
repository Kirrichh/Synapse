"""Whether a finished attempt is followed by another one, and why.

§26 requires the retry decision to be frozen policy rather than a judgement made
at the call site, so this module is a pure function of four inputs: how the
attempt ended, how much budget the run has left, the typed availability of
newly admitted or revalidated knowledge, and what the run's fallback policy
allows. It holds no state, performs no I/O and imports nothing outside
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
from enum import Enum

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
REASON_STOP_NO_PROGRESS = "NO_CONTINUATION_BASIS_FOR_NEXT_ATTEMPT"
REASON_C1_RESULT_INVALID = "C1_RESULT_INVALID_FAIL_CLOSED"
REASON_INFRA_EXHAUSTED = "INFRA_EXHAUSTED_RUN_UNAVAILABLE"
REASON_INFRA_EXPLICIT_FALLBACK = "INFRA_EXHAUSTED_EXPLICIT_BASELINE_ARM"
REASON_KNOWLEDGE_DEPENDENCY_UNAVAILABLE = "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"
REASON_KNOWLEDGE_DEPENDENCY_EXPLICIT_FALLBACK = (
    "KNOWLEDGE_DEPENDENCY_UNAVAILABLE_EXPLICIT_BASELINE_ARM"
)


class KnowledgeContinuationStatus(str, Enum):
    """Authoritative availability of different input for a later attempt."""

    NEWLY_ADMITTED_OR_REVALIDATED = "NEWLY_ADMITTED_OR_REVALIDATED"
    NO_CONTINUATION_BASIS = "NO_CONTINUATION_BASIS"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


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
    unavailable_reason: str = REASON_INFRA_EXHAUSTED,
    fallback_reason: str = REASON_INFRA_EXPLICIT_FALLBACK,
) -> DecisionDraft:
    """A required dependency is unavailable: fall back explicitly, or stop."""

    if fallback_policy is FallbackPolicy.EXPLICIT_BASELINE_ARM:
        return DecisionDraft(
            TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT,
            fallback_reason,
            fallback_arm_id,
        )
    return DecisionDraft(TerminalDecisionKind.STOP_UNRECOVERABLE, unavailable_reason, None)


def decide_dependency_unavailable(
    *,
    fallback_policy: FallbackPolicy,
    fallback_arm_id: str,
) -> DecisionDraft:
    """Classify failed next-attempt preparation without fabricating an attempt.

    Preparation can fail only after a previous ``CONTINUE`` has already become
    durable. That decision remains historical truth. This policy answer is the
    terminal authority for the run-level preparation failure that followed it.
    """

    if type(fallback_policy) is not FallbackPolicy:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "fallback_policy must be exact")
    if type(fallback_arm_id) is not str or not fallback_arm_id or len(fallback_arm_id) > 128:
        raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "fallback arm id must be bounded")
    return _fallback_or_unavailable(
        fallback_policy=fallback_policy,
        fallback_arm_id=fallback_arm_id,
        unavailable_reason=REASON_KNOWLEDGE_DEPENDENCY_UNAVAILABLE,
        fallback_reason=REASON_KNOWLEDGE_DEPENDENCY_EXPLICIT_FALLBACK,
    )


def decide_next_attempt(
    *,
    outcome: AttemptOutcome,
    attempts_used: int,
    max_attempts: int,
    knowledge_status: KnowledgeContinuationStatus,
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
    if type(knowledge_status) is not KnowledgeContinuationStatus:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge_status must be exact")
    if type(fallback_policy) is not FallbackPolicy:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "fallback_policy must be exact")

    budget_left = attempts_used < max_attempts
    if outcome is AttemptOutcome.RESOLVED:
        return DecisionDraft(TerminalDecisionKind.STOP_SUCCESS, REASON_RESOLVED_RUN_COMPLETE, None)
    if outcome is AttemptOutcome.C1_RESULT_INVALID:
        return DecisionDraft(TerminalDecisionKind.STOP_UNRECOVERABLE, REASON_C1_RESULT_INVALID, None)
    if knowledge_status is KnowledgeContinuationStatus.DEPENDENCY_UNAVAILABLE:
        return decide_dependency_unavailable(
            fallback_policy=fallback_policy,
            fallback_arm_id=fallback_arm_id,
        )
    if knowledge_status is KnowledgeContinuationStatus.NO_CONTINUATION_BASIS:
        return DecisionDraft(
            TerminalDecisionKind.STOP_NO_PROGRESS, REASON_STOP_NO_PROGRESS, None
        )
    if not budget_left:
        if outcome in (AttemptOutcome.INFRA_ERROR, AttemptOutcome.DELIVERY_UNAVAILABLE, AttemptOutcome.CONTROLLER_INTERRUPTED):
            return _fallback_or_unavailable(
                fallback_policy=fallback_policy, fallback_arm_id=fallback_arm_id
            )
        return DecisionDraft(TerminalDecisionKind.STOP_LIMIT, REASON_STOP_LIMIT, None)
    return DecisionDraft(TerminalDecisionKind.CONTINUE, REASON_RETRY_NEW_KNOWLEDGE, None)
