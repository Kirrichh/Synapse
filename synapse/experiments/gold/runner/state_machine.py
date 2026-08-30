"""Run state machine: phase boundaries recovered from durable records.

The controller keeps no in-memory truth between processes. The durable
run records are the state machine: a run is described by which records
exist, and recovery classifies each attempt from its persisted context
and result. Resume happens only from verified phase boundaries — an
attempt whose controller result is missing is interrupted, never
re-executed under the same attempt id (NR-13).
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse.experiments.gold.runner.models import (
    GOLD_RUN_RESULT_SCHEMA_V1,
    AttemptPhaseRefs,
    AttemptSummary,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunConfig,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from synapse.experiments.gold.runner.vocabulary import (
    TERMINAL_DECISIONS,
    AttemptOutcome,
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
    RunFinalStatus,
    TerminalDecisionKind,
    final_status_for_decision,
)
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _stored_envelope(payload: object) -> tuple[str, dict[str, object]]:
    """Unwrap the stored record envelope and return (record_sha256, inner)."""

    if type(payload) is not dict or set(payload) != {"record_sha256", "payload"}:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored record has an unknown shape")
    inner = payload["payload"]
    digest = payload["record_sha256"]
    if type(inner) is not dict or type(digest) is not str:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored record envelope is malformed")
    return digest, inner


def _manifest_from_payload(stored: dict[str, object]) -> GoldRunManifest:

    digest_value, payload = _stored_envelope(stored)
    if payload.get("schema_version") != "synapse.stage4.gold.run-manifest/v1":
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest schema is unknown")
    run_id_payload = payload.get("run_id")
    config_payload = payload.get("config")
    if type(run_id_payload) is not dict or type(config_payload) is not dict:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest payload is malformed")
    restored_config = dict(config_payload)
    restored_config["fallback_policy"] = FallbackPolicy(restored_config["fallback_policy"])
    run_id = RunId.from_dict(run_id_payload)
    config = GoldRunConfig(**restored_config)  # type: ignore[arg-type]
    manifest = GoldRunManifest(
        run_id=run_id,
        gold_run_id=payload.get("gold_run_id"),  # type: ignore[arg-type]
        config=config,
        manifest_sha256=digest_value,
    )
    manifest.validate_identity()
    return manifest


def _attempt_context_from_payload(stored: dict[str, object]) -> GoldAttemptContext:

    digest_value, payload = _stored_envelope(stored)
    if payload.get("schema_version") != "synapse.stage4.gold.attempt-context/v1":
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt context schema is unknown")
    run_id_payload = payload.get("run_id")
    attempt_id_payload = payload.get("attempt_id")
    phase_refs_payload = payload.get("phase_refs")
    if type(run_id_payload) is not dict or type(attempt_id_payload) is not dict or type(phase_refs_payload) is not dict:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt context payload is malformed")
    restored_refs = dict(phase_refs_payload)
    for name in ("knowledge_snapshot_ref", "retrieval_ref", "replay_ref", "intent_ref", "plan_ref"):
        restored_refs[name] = HashBoundRef.from_dict(restored_refs[name])
    context = GoldAttemptContext(
        run_id=RunId.from_dict(run_id_payload),
        gold_run_id=payload.get("gold_run_id"),  # type: ignore[arg-type]
        attempt_index=payload.get("attempt_index"),  # type: ignore[arg-type]
        attempt_id=AttemptId.from_dict(attempt_id_payload),
        phase_refs=AttemptPhaseRefs(**restored_refs),  # type: ignore[arg-type]
        context_sha256=digest_value,
    )
    context.validate_identity()
    return context


def _attempt_result_from_payload(stored: dict[str, object]) -> GoldAttemptResult:

    digest_value, payload = _stored_envelope(stored)
    if payload.get("schema_version") != "synapse.stage4.gold.attempt-result/v1":
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt result schema is unknown")
    run_id_payload = payload.get("run_id")
    attempt_id_payload = payload.get("attempt_id")
    if type(run_id_payload) is not dict or type(attempt_id_payload) is not dict:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt result payload is malformed")
    outcome_raw = payload.get("outcome")
    if type(outcome_raw) is not str:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt outcome must be a string value")
    result = GoldAttemptResult(
        run_id=RunId.from_dict(run_id_payload),
        gold_run_id=payload.get("gold_run_id"),  # type: ignore[arg-type]
        attempt_index=payload.get("attempt_index"),  # type: ignore[arg-type]
        attempt_id=AttemptId.from_dict(attempt_id_payload),
        outcome=AttemptOutcome(outcome_raw),
        c1_status=payload.get("c1_status"),  # type: ignore[arg-type]
        oracle_invoked=payload.get("oracle_invoked"),  # type: ignore[arg-type]
        oracle_resolved=payload.get("oracle_resolved"),  # type: ignore[arg-type]
        context_sha256=payload.get("context_sha256"),  # type: ignore[arg-type]
        result_sha256=digest_value,
    )
    result.validate_identity()
    return result


def _decision_from_payload(stored: dict[str, object]) -> NextAttemptDecision:

    digest_value, payload = _stored_envelope(stored)
    if payload.get("schema_version") != "synapse.stage4.gold.run-decision/v1":
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision schema is unknown")
    run_id_payload = payload.get("run_id")
    if type(run_id_payload) is not dict:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision payload is malformed")
    decision_raw = payload.get("decision")
    if type(decision_raw) is not str:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision kind must be a string value")
    decision = NextAttemptDecision(
        run_id=RunId.from_dict(run_id_payload),
        attempt_index=payload.get("attempt_index"),  # type: ignore[arg-type]
        decision=TerminalDecisionKind(decision_raw),
        reason=payload.get("reason"),  # type: ignore[arg-type]
        fallback_arm_id=payload.get("fallback_arm_id"),  # type: ignore[arg-type]
        decision_sha256=digest_value,
    )
    decision.validate_identity()
    return decision


def restore_manifest(store: RunRecordStore) -> GoldRunManifest:
    """Load and fully revalidate the persisted run manifest."""

    record = store.get(kind=RecordKind.MANIFEST, key="manifest")
    if record is None:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "run manifest is not persisted")
    return _manifest_from_payload(record.payload)


@dataclass(frozen=True)
class AttemptState:
    """Durable truth about one attempt index."""

    attempt_index: int
    context: GoldAttemptContext | None
    result: GoldAttemptResult | None

    @property
    def started(self) -> bool:
        return self.context is not None

    @property
    def finished(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class RunState:
    """Recovered run state; the durable records are the state machine."""

    manifest: GoldRunManifest
    attempts: tuple[AttemptState, ...]
    decisions: tuple[NextAttemptDecision, ...]
    final_result: GoldRunResult | None

    @property
    def completed_indexes(self) -> tuple[int, ...]:
        return tuple(item.attempt_index for item in self.attempts if item.finished)

    @property
    def interrupted_indexes(self) -> tuple[int, ...]:
        """Started attempts without a controller result; classified on resume."""

        return tuple(
            item.attempt_index
            for item in self.attempts
            if item.started and not item.finished
        )

    @property
    def attempts_used(self) -> int:
        return len(self.attempts)

    @property
    def next_index(self) -> int:
        return self.attempts_used + 1

    def last_result(self) -> GoldAttemptResult | None:
        finished = [item.result for item in self.attempts if item.finished]
        return finished[-1] if finished else None

    def decision_for(self, attempt_index: int) -> NextAttemptDecision | None:
        for item in self.decisions:
            if item.attempt_index == attempt_index:
                return item
        return None


def load_run_state(store: RunRecordStore) -> RunState:
    """Scan the store and rebuild the exact run state; conflicts fail closed."""

    manifest = restore_manifest(store)
    max_attempts = manifest.config.max_attempts
    attempt_states: list[AttemptState] = []
    for index in range(1, max_attempts + 1):
        context_record = store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(index))
        result_record = store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(index))
        context = None if context_record is None else _attempt_context_from_payload(context_record.payload)
        result = None if result_record is None else _attempt_result_from_payload(result_record.payload)
        if context is None and result is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "an attempt result exists without its context")
        if result is not None and context is not None and result.context_sha256 != context.context_sha256:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt result is bound to another context")
        if context is not None or result is not None:
            attempt_states.append(AttemptState(index, context, result))
    decisions: list[NextAttemptDecision] = []
    for key in store.iter_keys(kind=RecordKind.DECISION):
        record = store.get(kind=RecordKind.DECISION, key=key)
        if record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "decision record disappeared during scan")
        decisions.append(_decision_from_payload(record.payload))
    final_record = store.get(kind=RecordKind.RUN_RESULT, key="final")
    final_result = None if final_record is None else _run_result_from_payload(final_record.payload)
    state = RunState(
        manifest=manifest,
        attempts=tuple(attempt_states),
        decisions=tuple(sorted(decisions, key=lambda item: item.attempt_index)),
        final_result=final_result,
    )
    _check_state_consistency(state)
    return state


def _check_state_consistency(state: RunState) -> None:
    indexes = [item.attempt_index for item in state.attempts]
    if indexes != list(range(1, len(indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt records are not a gapless prefix")
    for item in state.attempts:
        if item.result is not None:
            continue
        if state.decision_for(item.attempt_index) is not None:
            # A decision is always written after its result; one without a
            # result means the store was corrupted or mixed between runs.
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "a decision exists for an attempt without a result")
    # A finished attempt without a persisted decision is the verified crash
    # boundary between result and decision; the decision is deterministic and
    # is recomputed on resume, so it is deliberately not an inconsistency.
    if state.final_result is not None:
        terminal = [item for item in state.decisions if item.decision in TERMINAL_DECISIONS]
        if not terminal:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "a run result exists without a terminal decision")


def _run_result_from_payload(stored: dict[str, object]) -> GoldRunResult:

    digest_value, payload = _stored_envelope(stored)
    if payload.get("schema_version") != GOLD_RUN_RESULT_SCHEMA_V1:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run result schema is unknown")
    run_id_payload = payload.get("run_id")
    attempts_payload = payload.get("attempts")
    if type(run_id_payload) is not dict or type(attempts_payload) is not list:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run result payload is malformed")
    final_status_raw = payload.get("final_status")
    terminal_decision_raw = payload.get("terminal_decision")
    if type(final_status_raw) is not str or type(terminal_decision_raw) is not str:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run result enums must be string values")
    summaries = []
    for item in attempts_payload:
        if type(item) is not dict:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run attempt summary is malformed")
        outcome_raw = item.get("outcome")
        if type(outcome_raw) is not str:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "summary outcome must be a string value")
        summaries.append(
            AttemptSummary(
                attempt_index=item.get("attempt_index"),  # type: ignore[arg-type]
                attempt_id=item.get("attempt_id"),  # type: ignore[arg-type]
                outcome=AttemptOutcome(outcome_raw),
                c1_status=item.get("c1_status"),  # type: ignore[arg-type]
                result_sha256=item.get("result_sha256"),  # type: ignore[arg-type]
            )
        )
    result = GoldRunResult(
        run_id=RunId.from_dict(run_id_payload),
        gold_run_id=payload.get("gold_run_id"),  # type: ignore[arg-type]
        manifest_sha256=payload.get("manifest_sha256"),  # type: ignore[arg-type]
        final_status=RunFinalStatus(final_status_raw),
        terminal_decision=TerminalDecisionKind(terminal_decision_raw),
        attempts=tuple(summaries),
        resolved_attempt_index=payload.get("resolved_attempt_index"),  # type: ignore[arg-type]
        fallback_arm_id=payload.get("fallback_arm_id"),  # type: ignore[arg-type]
        result_sha256=digest_value,
    )
    result.validate_identity()
    return result


def build_run_result(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[GoldAttemptResult, ...],
    terminal_decision: NextAttemptDecision,
) -> GoldRunResult:
    """Assemble the final run result from the complete attempt set.

    The result carries every attempt in order; dropping, reordering or
    cherry-picking attempts makes construction fail rather than produce a
    selective success report.
    """

    if not attempts:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "a run result requires at least one attempt record")
    summaries = tuple(
        AttemptSummary(
            attempt_index=item.attempt_index,
            attempt_id=item.attempt_id.value,
            outcome=item.outcome,
            c1_status=item.c1_status,
            result_sha256=item.result_sha256,
        )
        for item in attempts
    )
    indexes = [item.attempt_index for item in summaries]
    if indexes != list(range(1, len(indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run attempts must form a gapless ordered prefix")
    resolved = [item.attempt_index for item in summaries if item.outcome is AttemptOutcome.RESOLVED]
    if terminal_decision.decision is TerminalDecisionKind.STOP_SUCCESS:
        if len(resolved) != 1 or resolved[0] != indexes[-1]:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "success requires the terminal attempt to be resolved")
    elif resolved:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "a resolved attempt must terminate the run as a success")
    final_status = final_status_for_decision(terminal_decision.decision)
    return GoldRunResult.create(
        run_id=manifest.run_id,
        gold_run_id=manifest.gold_run_id,
        manifest_sha256=manifest.manifest_sha256,
        final_status=final_status,
        terminal_decision=terminal_decision.decision,
        attempts=summaries,
        resolved_attempt_index=resolved[0] if resolved else None,
        fallback_arm_id=terminal_decision.fallback_arm_id,
    )
