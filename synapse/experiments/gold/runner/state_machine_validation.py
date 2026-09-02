"""Cross-record authority validation for one durable Gold run.

State reconstruction decodes each record independently.  This owner verifies
the relationships that exist only across records: phase payloads must resolve
to the exact worker or C1 authority named by the attempt result, interruption
may stop only at a recoverable prefix, and a stored stop decision must be the
closed policy result for the preceding attempt.
"""

from __future__ import annotations

from collections.abc import Callable

from .attempt_authority import (
    require_c1_receipt_authority,
    require_completed_delivery_authority,
    require_delivery_failure_authority,
)
from .attempt_delivery_failure import restore_attempt_delivery_failure
from .c1_boundary import (
    classify_c1_authority_receipt,
    restore_c1_authority_receipt,
)
from .completed_delivery_codec import (
    completed_worker_delivery_ref,
    restore_completed_worker_delivery,
)
from .delivery import AttemptDeliveryRefusal, AttemptDeliveryUnavailable
from .models import (
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from .run_progress import (
    AttemptProgress,
    AttemptProgressPhase,
    AttemptProgressState,
    require_progress_payload,
)
from .stop_policy import (
    REASON_KNOWLEDGE_DEPENDENCY_EXPLICIT_FALLBACK,
    REASON_KNOWLEDGE_DEPENDENCY_UNAVAILABLE,
    REASON_STOP_NO_PROGRESS,
    KnowledgeContinuationStatus,
    decide_next_attempt,
)
from .vocabulary import (
    TERMINAL_DECISIONS,
    AttemptOutcome,
    GoldRunFailureCode,
    GoldRunViolation,
    TerminalDecisionKind,
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _same_ref(left: object, right: object) -> bool:
    return left.to_dict() == right.to_dict()


def _restore_completed(
    *,
    context: GoldAttemptContext,
    progress: AttemptProgress,
):
    payload_bytes, payload_ref = require_progress_payload(progress)
    completed = restore_completed_worker_delivery(
        payload_bytes,
        expected_ref=payload_ref,
    )
    return require_completed_delivery_authority(
        context=context,
        completed=completed,
    )


def _validate_delivery_failure(
    *,
    context: GoldAttemptContext,
    result: GoldAttemptResult,
    progress: AttemptProgressState,
) -> None:
    latest = progress.latest
    if latest is None or latest.phase not in (
        AttemptProgressPhase.DELIVERY_REFUSED,
        AttemptProgressPhase.DELIVERY_UNAVAILABLE,
    ):
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "delivery failure lacks its terminal progress checkpoint",
        )
    payload_bytes, payload_ref = require_progress_payload(latest)
    failure = restore_attempt_delivery_failure(
        payload_bytes,
        expected_ref=payload_ref,
    )
    expected_type = (
        AttemptDeliveryRefusal
        if latest.phase is AttemptProgressPhase.DELIVERY_REFUSED
        else AttemptDeliveryUnavailable
    )
    if type(failure) is not expected_type:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "delivery failure payload type differs from its terminal phase",
        )
    require_delivery_failure_authority(context=context, failure=failure)
    expected_outcome = (
        AttemptOutcome.DELIVERY_REFUSED
        if latest.phase is AttemptProgressPhase.DELIVERY_REFUSED
        else AttemptOutcome.DELIVERY_UNAVAILABLE
    )
    if result.outcome is not expected_outcome:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "delivery failure result differs from its progress path",
        )
    if (
        result.worker_result_ref is not None
        or result.c1_result_ref is not None
        or result.oracle_result_ref is not None
        or result.c1_status is not None
        or result.oracle_invoked is not False
        or result.oracle_resolved is not None
        or result.publication_refs
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "delivery failure result carries unreachable authority",
        )


def _validate_interrupted(
    *,
    context: GoldAttemptContext,
    result: GoldAttemptResult,
    progress: AttemptProgressState,
) -> None:
    phases = tuple(item.phase for item in progress.records)
    if phases in ((), (AttemptProgressPhase.DELIVERY_STARTED,)):
        expected_worker_ref = None
    elif phases == (
        AttemptProgressPhase.DELIVERY_STARTED,
        AttemptProgressPhase.WORKER_COMPLETED,
        AttemptProgressPhase.C1_STARTED,
    ):
        worker_progress = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
        if worker_progress is None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "interrupted C1 recovery is missing its worker checkpoint",
            )
        expected_worker_ref = completed_worker_delivery_ref(
            _restore_completed(context=context, progress=worker_progress)
        )
    else:
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "controller interruption has an invalid durable phase prefix",
        )
    if (result.worker_result_ref is None) != (expected_worker_ref is None) or (
        expected_worker_ref is not None
        and not _same_ref(result.worker_result_ref, expected_worker_ref)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "interrupted result differs from its durable worker checkpoint",
        )


def _validate_c1_classified(
    *,
    manifest: GoldRunManifest,
    context: GoldAttemptContext,
    result: GoldAttemptResult,
    progress: AttemptProgressState,
) -> None:
    """Compare one C1 result against its complete four-checkpoint authority.

    The final predicate is intentionally flat and indivisible so no caller can
    validate only worker, C1, oracle, or publication refs in isolation.
    """

    expected_phases = (
        AttemptProgressPhase.DELIVERY_STARTED,
        AttemptProgressPhase.WORKER_COMPLETED,
        AttemptProgressPhase.C1_STARTED,
        AttemptProgressPhase.C1_COMPLETED,
    )
    if tuple(item.phase for item in progress.records) != expected_phases:
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "C1-classified result lacks the exact execution progress chain",
        )
    worker_progress = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
    c1_progress = progress.get(AttemptProgressPhase.C1_COMPLETED)
    if worker_progress is None or c1_progress is None:
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "C1-classified result is missing durable payload checkpoints",
        )
    completed = _restore_completed(context=context, progress=worker_progress)
    expected_worker_ref = completed_worker_delivery_ref(completed)
    if result.worker_result_ref is None or not _same_ref(
        result.worker_result_ref,
        expected_worker_ref,
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "attempt result names a worker result different from its checkpoint",
        )
    payload_bytes, payload_ref = require_progress_payload(c1_progress)
    receipt = restore_c1_authority_receipt(
        payload_bytes,
        expected_ref=payload_ref,
    )
    require_c1_receipt_authority(
        manifest=manifest,
        context=context,
        worker_delivery=completed,
        receipt=receipt,
    )
    classification = classify_c1_authority_receipt(receipt)
    if (
        result.outcome is not classification.outcome
        or result.c1_status != classification.c1_status
        or result.oracle_invoked is not classification.oracle_invoked
        or result.oracle_resolved is not classification.oracle_resolved
        or result.c1_result_ref is None
        or not _same_ref(result.c1_result_ref, receipt.c1_result_ref)
        or (result.oracle_result_ref is None) != (receipt.oracle_result_ref is None)
        or (
            result.oracle_result_ref is not None
            and not _same_ref(result.oracle_result_ref, receipt.oracle_result_ref)
        )
        or result.publication_refs
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "attempt result differs from durable C1 authority",
        )


def _knowledge_status(
    *,
    decision: NextAttemptDecision,
    next_context: GoldAttemptContext | None,
) -> KnowledgeContinuationStatus:
    if next_context is not None or decision.decision is TerminalDecisionKind.CONTINUE:
        return KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED
    if decision.reason == REASON_STOP_NO_PROGRESS:
        return KnowledgeContinuationStatus.NO_CONTINUATION_BASIS
    if decision.reason in {
        REASON_KNOWLEDGE_DEPENDENCY_UNAVAILABLE,
        REASON_KNOWLEDGE_DEPENDENCY_EXPLICIT_FALLBACK,
    }:
        return KnowledgeContinuationStatus.DEPENDENCY_UNAVAILABLE
    return KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED


def _validate_decision(
    *,
    manifest: GoldRunManifest,
    attempts_used: int,
    result: GoldAttemptResult,
    decision: NextAttemptDecision,
    next_context: GoldAttemptContext | None,
) -> None:
    fallback_input = decision.fallback_arm_id or (
        f"recomputed-{manifest.manifest_sha256[:16]}"
    )
    recomputed = decide_next_attempt(
        outcome=result.outcome,
        attempts_used=attempts_used,
        max_attempts=manifest.config.max_attempts,
        knowledge_status=_knowledge_status(
            decision=decision,
            next_context=next_context,
        ),
        fallback_policy=manifest.config.fallback_policy,
        fallback_arm_id=fallback_input,
    )
    if (
        decision.decision is not recomputed.decision
        or decision.reason != recomputed.reason
        or decision.fallback_arm_id != recomputed.fallback_arm_id
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "stored stop decision is not the deterministic policy result",
        )
    if decision.decision is TerminalDecisionKind.CONTINUE:
        if next_context is not None and not _same_ref(
            decision.next_retrieval_causal_ref,
            next_context.phase_refs.retrieval_ref,
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "CONTINUE decision does not bind the next retrieval authority",
            )
    elif decision.next_retrieval_causal_ref is not None:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "terminal decision carries next-attempt retrieval authority",
        )


def _validate_attempt_state(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    position: int,
    decision_map: dict[int, NextAttemptDecision],
    progress: dict[int, AttemptProgressState],
) -> None:
    attempt = attempts[position - 1]
    context = attempt.context
    result = attempt.result
    is_last = position == len(attempts)
    decision = decision_map.get(position)
    if result is None:
        if not is_last or decision is not None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "only the last attempt may be unfinished",
            )
        return
    attempt_progress = progress[position]
    if result.outcome in (
        AttemptOutcome.DELIVERY_REFUSED,
        AttemptOutcome.DELIVERY_UNAVAILABLE,
    ):
        _validate_delivery_failure(
            context=context,
            result=result,
            progress=attempt_progress,
        )
    elif result.outcome is AttemptOutcome.CONTROLLER_INTERRUPTED:
        _validate_interrupted(
            context=context,
            result=result,
            progress=attempt_progress,
        )
    else:
        _validate_c1_classified(
            manifest=manifest,
            context=context,
            result=result,
            progress=attempt_progress,
        )
    if not is_last and (
        decision is None or decision.decision is not TerminalDecisionKind.CONTINUE
    ):
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "every earlier finished attempt requires CONTINUE",
        )
    if decision is not None:
        _validate_decision(
            manifest=manifest,
            attempts_used=position,
            result=result,
            decision=decision,
            next_context=None if is_last else attempts[position].context,
        )


def _validate_final_result(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    terminal: tuple[NextAttemptDecision, ...],
    final_result: GoldRunResult | None,
    build_run_result: Callable[..., GoldRunResult],
) -> None:
    if final_result is None:
        return
    if not terminal:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result exists without terminal decision")
    if (
        final_result.run_id != manifest.run_id
        or final_result.gold_run_id != manifest.gold_run_id
        or final_result.manifest_sha256 != manifest.manifest_sha256
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "run result names another manifest")
    finished = tuple(attempt.result for attempt in attempts if attempt.result is not None)
    expected = build_run_result(
        manifest=manifest,
        attempts=finished,
        terminal_decision=terminal[0],
    )
    if (
        final_result.result_sha256 != expected.result_sha256
        or final_result.payload() != expected.payload()
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run result is not the exact terminal projection",
        )


def validate_state_consistency(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    decisions: tuple[NextAttemptDecision, ...],
    final_result: GoldRunResult | None,
    progress: dict[int, AttemptProgressState],
    build_run_result: Callable[..., GoldRunResult],
) -> None:
    """Reject any cross-record state that no valid controller run can emit."""

    decision_map = {item.attempt_index: item for item in decisions}
    terminal = tuple(item for item in decisions if item.decision in TERMINAL_DECISIONS)
    if len(terminal) > 1:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run has several terminal decisions")
    for position in range(1, len(attempts) + 1):
        _validate_attempt_state(
            manifest=manifest,
            attempts=attempts,
            position=position,
            decision_map=decision_map,
            progress=progress,
        )

    if terminal and terminal[0].attempt_index != len(attempts):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "terminal decision is not the run tail")
    _validate_final_result(
        manifest=manifest,
        attempts=attempts,
        terminal=terminal,
        final_result=final_result,
        build_run_result=build_run_result,
    )


__all__ = ["validate_state_consistency"]
