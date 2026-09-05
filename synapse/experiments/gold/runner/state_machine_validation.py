"""Cross-record authority validation for one durable Gold run.

State reconstruction decodes records independently. This owner verifies the
relationships that exist only across records, including that every stored run
decision is the deterministic policy result of its durable continuation
evidence rather than an inference from the existence of a later attempt.
"""

from __future__ import annotations

from collections.abc import Callable
from synapse.experiments.gold.stage12.outcome import FinalStatus, controller_outcome, inspect_outcome

from .attempt_authority import (
    require_c1_receipt_authority,
    require_completed_delivery_authority,
    require_delivery_failure_authority,
)
from .attempt_delivery_failure import restore_attempt_delivery_failure
from .attempt_knowledge import ContinuationOutcome, KnowledgeContinuationEvidence
from .c1_boundary import classify_c1_authority_receipt, restore_c1_authority_receipt, verified_finding_sha256
from .completed_delivery_codec import completed_worker_delivery_ref, restore_completed_worker_delivery
from .delivery import AttemptDeliveryRefusal, AttemptDeliveryUnavailable
from .models import (
    AttemptPreparationFailure,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from .run_progress import AttemptProgress, AttemptProgressPhase, AttemptProgressState, require_progress_payload
from .stop_policy import (
    KnowledgeContinuationStatus,
    decide_dependency_unavailable,
    decide_next_attempt,
)
from .vocabulary import TERMINAL_DECISIONS, AttemptOutcome, GoldRunFailureCode, GoldRunViolation, TerminalDecisionKind


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _same_ref(left: object, right: object) -> bool:
    return left.to_dict() == right.to_dict()


def _restore_completed(*, context: GoldAttemptContext, progress: AttemptProgress):
    payload_bytes, payload_ref = require_progress_payload(progress)
    completed = restore_completed_worker_delivery(payload_bytes, expected_ref=payload_ref)
    return require_completed_delivery_authority(context=context, completed=completed)


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
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "delivery failure lacks its terminal checkpoint")
    payload_bytes, payload_ref = require_progress_payload(latest)
    failure = restore_attempt_delivery_failure(payload_bytes, expected_ref=payload_ref)
    expected_type = (
        AttemptDeliveryRefusal
        if latest.phase is AttemptProgressPhase.DELIVERY_REFUSED
        else AttemptDeliveryUnavailable
    )
    if type(failure) is not expected_type:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "delivery failure type differs from phase")
    require_delivery_failure_authority(context=context, failure=failure)
    expected_outcome = (
        AttemptOutcome.DELIVERY_REFUSED
        if latest.phase is AttemptProgressPhase.DELIVERY_REFUSED
        else AttemptOutcome.DELIVERY_UNAVAILABLE
    )
    if result.outcome is not expected_outcome:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "delivery result differs from progress path")
    if (
        result.worker_result_ref is not None
        or result.c1_result_ref is not None
        or result.oracle_result_ref is not None
        or result.c1_status is not None
        or result.oracle_invoked is not False
        or result.oracle_resolved is not None
        or result.publication_refs
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "delivery failure carries unreachable authority")


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
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "interrupted C1 lacks worker checkpoint")
        expected_worker_ref = completed_worker_delivery_ref(
            _restore_completed(context=context, progress=worker_progress)
        )
    else:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "controller interruption has invalid durable prefix")
    if (result.worker_result_ref is None) != (expected_worker_ref is None) or (
        expected_worker_ref is not None and not _same_ref(result.worker_result_ref, expected_worker_ref)
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "interrupted result differs from worker checkpoint")


def _validate_c1_classified(
    *,
    manifest: GoldRunManifest,
    context: GoldAttemptContext,
    result: GoldAttemptResult,
    progress: AttemptProgressState,
) -> None:
    expected_phases = (
        AttemptProgressPhase.DELIVERY_STARTED,
        AttemptProgressPhase.WORKER_COMPLETED,
        AttemptProgressPhase.C1_STARTED,
        AttemptProgressPhase.C1_COMPLETED,
    )
    if tuple(item.phase for item in progress.records) != expected_phases:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "C1 result lacks exact progress chain")
    worker_progress = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
    c1_progress = progress.get(AttemptProgressPhase.C1_COMPLETED)
    if worker_progress is None or c1_progress is None:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "C1 result lacks payload checkpoints")
    completed = _restore_completed(context=context, progress=worker_progress)
    expected_worker_ref = completed_worker_delivery_ref(completed)
    if result.worker_result_ref is None or not _same_ref(result.worker_result_ref, expected_worker_ref):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "worker result differs from checkpoint")
    payload_bytes, payload_ref = require_progress_payload(c1_progress)
    receipt = restore_c1_authority_receipt(payload_bytes, expected_ref=payload_ref)
    require_c1_receipt_authority(
        manifest=manifest,
        context=context,
        worker_delivery=completed,
        receipt=receipt,
    )
    classification = classify_c1_authority_receipt(receipt)
    structured = inspect_outcome(result.structured_outcome)
    status = FinalStatus(structured["status"])
    facts = structured["verification"]["payload"]
    valid = status is not FinalStatus.INVALID_CONTRACT
    if (facts["c1_receipt_ref"] != payload_ref.to_dict()
            or facts["worker_result_ref"] != expected_worker_ref.to_dict()):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "outcome refers to different execution evidence")
    if facts["c1"] is not None and (
        facts["c1"]["c1_result_ref"] != receipt.c1_result_ref.to_dict()
        or facts["c1"]["oracle_result_ref"] != (None if receipt.oracle_result_ref is None else receipt.oracle_result_ref.to_dict())
        or facts["c1"]["c1_status"] != receipt.c1_status
        or facts["c1"]["oracle_resolved"] is not receipt.oracle_resolved
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "outcome contradicts durable C1 authority")
    if (
        result.outcome is not controller_outcome(status, c1_outcome=classification.outcome)
        or result.verified_finding_sha256 != (verified_finding_sha256(receipt) if valid else None)
        or result.verified_patch_sha256 != (receipt.verified_patch_sha256 if valid else None)
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
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt result differs from durable C1 authority")


def _knowledge_status(evidence: KnowledgeContinuationEvidence) -> KnowledgeContinuationStatus:
    if type(evidence) is not KnowledgeContinuationEvidence:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "continuation evidence must be exact")
    if evidence.outcome is ContinuationOutcome.CONTINUATION_BASIS:
        return KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED
    if evidence.outcome is ContinuationOutcome.NO_CONTINUATION_BASIS:
        return KnowledgeContinuationStatus.NO_CONTINUATION_BASIS
    raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "continuation evidence outcome is unknown")


def _validate_decision(
    *,
    manifest: GoldRunManifest,
    attempts_used: int,
    result: GoldAttemptResult,
    decision: NextAttemptDecision,
    evidence: KnowledgeContinuationEvidence,
) -> None:
    if decision.continuation_evidence_sha256 != evidence.digest():
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "decision names different continuation evidence")
    fallback_input = decision.fallback_arm_id or f"recomputed-{manifest.manifest_sha256[:16]}"
    recomputed = decide_next_attempt(
        outcome=result.outcome,
        attempts_used=attempts_used,
        max_attempts=manifest.config.max_attempts,
        knowledge_status=_knowledge_status(evidence),
        fallback_policy=manifest.config.fallback_policy,
        fallback_arm_id=fallback_input,
    )
    if (
        decision.decision is not recomputed.decision
        or decision.reason != recomputed.reason
        or decision.fallback_arm_id != recomputed.fallback_arm_id
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "stored decision is not deterministic policy result")


def _validate_attempt_state(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    position: int,
    decision_map: dict[int, NextAttemptDecision],
    evidence_map: dict[int, KnowledgeContinuationEvidence],
    progress: dict[int, AttemptProgressState],
) -> None:
    attempt = attempts[position - 1]
    context = attempt.context
    result = attempt.result
    is_last = position == len(attempts)
    decision = decision_map.get(position)
    evidence = evidence_map.get(position)
    if result is None:
        if not is_last or decision is not None or evidence is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "only the run tail may be unfinished")
        return
    attempt_progress = progress[position]
    try:
        structured = inspect_outcome(result.structured_outcome)
        facts = structured["verification"]["payload"]
        if (
            structured["scope"] != "ATTEMPT"
            or facts["manifest_sha256"] != manifest.manifest_sha256
            or facts["run_id"] != manifest.run_id.value
            or facts["attempt_id"] != context.attempt_id.value
            or facts["context_sha256"] != context.context_sha256
            or facts["phase_refs"] != context.phase_refs.to_dict()
            or facts["progress_sha256"] != (None if attempt_progress.latest is None else attempt_progress.latest.progress_sha256)
        ):
            raise ValueError("outcome references another attempt")
    except (ValueError, TypeError, KeyError) as exc:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "structured outcome is invalid") from exc
    if result.outcome in (AttemptOutcome.DELIVERY_REFUSED, AttemptOutcome.DELIVERY_UNAVAILABLE):
        _validate_delivery_failure(context=context, result=result, progress=attempt_progress)
    elif result.outcome is AttemptOutcome.CONTROLLER_INTERRUPTED:
        _validate_interrupted(context=context, result=result, progress=attempt_progress)
    else:
        _validate_c1_classified(
            manifest=manifest,
            context=context,
            result=result,
            progress=attempt_progress,
        )
    if not is_last and (decision is None or decision.decision is not TerminalDecisionKind.CONTINUE):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "every earlier finished attempt requires CONTINUE")
    if decision is None:
        if evidence is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "continuation evidence exists without decision")
        return
    if evidence is None:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "decision has no continuation evidence")
    _validate_decision(
        manifest=manifest,
        attempts_used=position,
        result=result,
        decision=decision,
        evidence=evidence,
    )
    if decision.decision in TERMINAL_DECISIONS and not is_last:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "terminal decision has a later attempt")


def _validate_preparation_failure(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    decision_map: dict[int, NextAttemptDecision],
    evidence_map: dict[int, KnowledgeContinuationEvidence],
    failure: AttemptPreparationFailure | None,
) -> None:
    if failure is None:
        return
    if type(failure) is not AttemptPreparationFailure:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "preparation failure must be exact")
    failure.validate_identity()
    if (
        failure.run_id != manifest.run_id
        or failure.gold_run_id != manifest.gold_run_id
        or failure.manifest_sha256 != manifest.manifest_sha256
        or failure.target_attempt_index > manifest.config.max_attempts
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "preparation failure names another run boundary")
    draft = decide_dependency_unavailable(
        fallback_policy=manifest.config.fallback_policy,
        fallback_arm_id=failure.fallback_arm_id or f"recomputed-{manifest.manifest_sha256[:16]}",
        detail_code=failure.detail_code,
    )
    if (
        failure.terminal_decision is not draft.decision
        or failure.reason != draft.reason
        or failure.fallback_arm_id != draft.fallback_arm_id
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "preparation failure is not the frozen policy result")
    if failure.source_attempt_index is None:
        if attempts or decision_map or evidence_map or failure.target_attempt_index != 1:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "initial preparation failure has predecessor history")
        return
    source_index = failure.source_attempt_index
    if not attempts or source_index != len(attempts):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "preparation failure is not attached to the run tail")
    source = attempts[-1]
    decision = decision_map.get(source_index)
    evidence = evidence_map.get(source_index)
    if source.result is None or decision is None or evidence is None:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "preparation failure lacks its source authority")
    if decision.decision is not TerminalDecisionKind.CONTINUE:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "only a durable CONTINUE may authorise target preparation")
    if (
        failure.source_attempt_result_sha256 != source.result.result_sha256
        or failure.source_decision_sha256 != decision.decision_sha256
        or failure.continuation_evidence_sha256 != evidence.digest()
        or decision.continuation_evidence_sha256 != evidence.digest()
        or failure.target_attempt_index != source_index + 1
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "preparation failure differs from source continuation")


def _validate_final_result(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    terminal: tuple[NextAttemptDecision, ...],
    preparation_failure: AttemptPreparationFailure | None,
    final_result: GoldRunResult | None,
    build_run_result: Callable[..., GoldRunResult],
) -> None:
    if final_result is None:
        return
    if preparation_failure is not None:
        terminal_authority: NextAttemptDecision | AttemptPreparationFailure = preparation_failure
    elif terminal:
        terminal_authority = terminal[0]
    else:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result exists without terminal authority")
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
        terminal_decision=terminal_authority,
    )
    if final_result.result_sha256 != expected.result_sha256 or final_result.payload() != expected.payload():
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "run result is not exact terminal projection")


def validate_state_consistency(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[object, ...],
    decisions: tuple[NextAttemptDecision, ...],
    continuation_evidence: tuple[KnowledgeContinuationEvidence, ...],
    preparation_failure: AttemptPreparationFailure | None,
    final_result: GoldRunResult | None,
    progress: dict[int, AttemptProgressState],
    build_run_result: Callable[..., GoldRunResult],
) -> None:
    """Reject any cross-record state that no valid controller run can emit."""

    decision_map = {item.attempt_index: item for item in decisions}
    evidence_map = {item.attempt_index: item for item in continuation_evidence}
    if len(decision_map) != len(decisions) or len(evidence_map) != len(continuation_evidence):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision or evidence index is duplicated")
    if set(decision_map) != set(evidence_map):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "decision and continuation-evidence namespaces differ")
    terminal = tuple(item for item in decisions if item.decision in TERMINAL_DECISIONS)
    if len(terminal) > 1:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run has several terminal decisions")
    if terminal and preparation_failure is not None:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run has competing terminal authorities")
    for position in range(1, len(attempts) + 1):
        _validate_attempt_state(
            manifest=manifest,
            attempts=attempts,
            position=position,
            decision_map=decision_map,
            evidence_map=evidence_map,
            progress=progress,
        )
    if terminal and terminal[0].attempt_index != len(attempts):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "terminal decision is not the run tail")
    _validate_preparation_failure(
        manifest=manifest,
        attempts=attempts,
        decision_map=decision_map,
        evidence_map=evidence_map,
        failure=preparation_failure,
    )
    _validate_final_result(
        manifest=manifest,
        attempts=attempts,
        terminal=terminal,
        preparation_failure=preparation_failure,
        final_result=final_result,
        build_run_result=build_run_result,
    )


__all__ = ["validate_state_consistency"]
