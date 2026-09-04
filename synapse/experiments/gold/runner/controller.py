"""Drive one immutable Gold run through attempt execution and durable decisions.

The controller owns run-level sequencing only. A completed attempt produces
recomputable continuation evidence and a durable decision before any world,
snapshot or retrieval for the next attempt may be materialized.
"""

from __future__ import annotations

from pathlib import Path

from .attempt_knowledge import (
    ContinuationOutcome,
    basis_from_payload,
    decide_completed_attempt_continuation,
    prior_attempt_evidence_from_result,
)
from .attempt_knowledge_store import basis_record_key
from .attempt_inputs import (
    AttemptInputAvailability,
    AttemptInputsPort,
    KnowledgeDependencyUnavailable,
    PreparedAttemptInputs,
    require_attempt_inputs_port,
)
from .controller_recovery import AttemptPhaseMaterializer, require_attempt_phase_materializer
from .models import (
    AttemptPreparationFailure,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from .records import RecordKind
from .run_recovery import PendingRunRecord, RunRecordRecovery, RunRecordSession
from .state_machine import build_run_result, load_run_state
from .stop_policy import (
    KnowledgeContinuationStatus,
    decide_dependency_unavailable,
    decide_next_attempt,
)
from .vocabulary import GoldRunFailureCode, GoldRunViolation, TerminalDecisionKind


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldRunController:
    """One sealed production controller bound to one exact run."""

    __slots__ = (
        "_manifest", "_record_recovery", "_attempt_inputs", "_run_root",
        "_attempt_materializer", "_identity_snapshot",
    )

    def __init__(
        self,
        *,
        manifest: GoldRunManifest,
        record_recovery: RunRecordRecovery,
        attempt_inputs: AttemptInputsPort,
        attempt_materializer: AttemptPhaseMaterializer,
        run_root: Path,
    ) -> None:
        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        manifest.validate_identity()
        if type(record_recovery) is not RunRecordRecovery:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run record recovery must be exact")
        inputs = require_attempt_inputs_port(attempt_inputs)
        if type(run_root) is not type(Path()):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be exact")
        materializer = require_attempt_phase_materializer(
            attempt_materializer,
            manifest=manifest,
            run_root=run_root,
        )
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(self, "_record_recovery", record_recovery)
        object.__setattr__(self, "_attempt_inputs", inputs)
        object.__setattr__(self, "_run_root", run_root)
        object.__setattr__(self, "_attempt_materializer", materializer)
        object.__setattr__(
            self,
            "_identity_snapshot",
            (manifest, record_recovery, inputs, run_root, materializer, record_recovery.store, record_recovery.fence),
        )
        self._revalidate_bindings()

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldRunController is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldRunController is immutable")

    def execute(self) -> GoldRunResult:
        self._revalidate_bindings()
        with self._record_recovery.session() as session:
            return self._drive_session(session)

    def _drive_session(self, session: RunRecordSession) -> GoldRunResult:
        while True:
            state = self._load_started_state(session)
            if state is None:
                session.put(self._record(
                    kind=RecordKind.MANIFEST,
                    key="manifest",
                    payload=self._manifest.stored_dict(),
                ))
                continue
            if state.final_result is not None:
                return state.final_result
            if state.preparation_failure is not None:
                return self._finalize(session, state, state.preparation_failure)
            terminal = self._tail_terminal_decision(state)
            if terminal is not None:
                return self._finalize(session, state, terminal)
            if state.interrupted_indexes:
                self._attempt_materializer.recover_unfinished_tail(session, state)
                continue
            if not state.attempts:
                prepared = self._prepare_attempt(attempt_index=1, previous_context=None)
                if type(prepared) is KnowledgeDependencyUnavailable:
                    return self._record_preparation_failure(session, state, prepared)
                self._attempt_materializer.execute_prepared_attempt(
                    session=session,
                    attempt_index=1,
                    prepared_inputs=prepared,
                )
                continue

            tail = state.attempts[-1]
            if tail.result is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "tail attempt lost its result")
            decision = state.decision_for(tail.attempt_index)
            if decision is None:
                decision = self._record_tail_decision(session, state)
                if decision.terminal:
                    return self._finalize(session, state, decision)
            if decision.decision is not TerminalDecisionKind.CONTINUE:
                return self._finalize(session, state, decision)

            prepared = self._prepare_attempt(
                attempt_index=tail.attempt_index + 1,
                previous_context=tail.context,
            )
            if type(prepared) is KnowledgeDependencyUnavailable:
                return self._record_preparation_failure(session, state, prepared)
            self._attempt_materializer.execute_prepared_attempt(
                session=session,
                attempt_index=tail.attempt_index + 1,
                prepared_inputs=prepared,
            )

    def load_result(self) -> GoldRunResult:
        self._revalidate_bindings()
        with self._record_recovery.session() as session:
            state = self._load_started_state(session)
            if state is None or state.final_result is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "the run has no terminal result yet")
            return state.final_result

    def _load_started_state(self, session: RunRecordSession):
        if session.store.get(kind=RecordKind.MANIFEST, key="manifest") is None:
            return None
        state = load_run_state(session.store)
        self._require_manifest_match(state.manifest)
        return state

    def _prepare_attempt(
        self,
        *,
        attempt_index: int,
        previous_context: object | None,
    ) -> AttemptInputAvailability:
        value = self._attempt_inputs.prepare(
            manifest=self._manifest,
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        if type(value) is PreparedAttemptInputs:
            return value
        if type(value) is KnowledgeDependencyUnavailable:
            if value.attempt_index != attempt_index:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "unavailable input names another attempt")
            return value
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt inputs returned an unknown type")

    def _record_preparation_failure(
        self,
        session: RunRecordSession,
        state,
        unavailable: KnowledgeDependencyUnavailable,
    ) -> GoldRunResult:
        draft = decide_dependency_unavailable(
            fallback_policy=self._manifest.config.fallback_policy,
            fallback_arm_id=self._fallback_arm_id(),
        )
        if state.attempts:
            tail = state.attempts[-1]
            decision = state.decision_for(tail.attempt_index)
            evidence = state.continuation_for(tail.attempt_index)
            if (
                tail.result is None
                or decision is None
                or decision.decision is not TerminalDecisionKind.CONTINUE
                or evidence is None
            ):
                raise _fail(
                    GoldRunFailureCode.PHASE_INVALID,
                    "continued preparation failure lacks durable CONTINUE authority",
                )
            source_attempt_index = tail.attempt_index
            source_attempt_result_sha256 = tail.result.result_sha256
            source_decision_sha256 = decision.decision_sha256
            continuation_evidence_sha256 = evidence.digest()
        else:
            source_attempt_index = None
            source_attempt_result_sha256 = None
            source_decision_sha256 = None
            continuation_evidence_sha256 = None
        failure = AttemptPreparationFailure.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            manifest_sha256=self._manifest.manifest_sha256,
            target_attempt_index=unavailable.attempt_index,
            source_attempt_index=source_attempt_index,
            source_attempt_result_sha256=source_attempt_result_sha256,
            source_decision_sha256=source_decision_sha256,
            continuation_evidence_sha256=continuation_evidence_sha256,
            terminal_decision=draft.decision,
            reason=draft.reason,
            detail_code=unavailable.detail_code,
            fallback_arm_id=draft.fallback_arm_id,
        )
        session.put(self._record(
            kind=RecordKind.PREPARATION_FAILURE,
            key="final",
            payload=failure.stored_dict(),
        ))
        return self._finalize(session, state, failure)

    def _record_tail_decision(self, session: RunRecordSession, state) -> NextAttemptDecision:
        tail = state.attempts[-1]
        result = tail.result
        if result is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "decision requires a finished attempt")
        evidence = self._continuation_evidence(session, state)
        knowledge_status = (
            KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED
            if evidence.outcome is ContinuationOutcome.CONTINUATION_BASIS
            else KnowledgeContinuationStatus.NO_CONTINUATION_BASIS
        )
        draft = decide_next_attempt(
            outcome=result.outcome,
            attempts_used=state.attempts_used,
            max_attempts=self._manifest.config.max_attempts,
            knowledge_status=knowledge_status,
            fallback_policy=self._manifest.config.fallback_policy,
            fallback_arm_id=self._fallback_arm_id(),
        )
        evidence_sha256 = evidence.digest()
        decision = NextAttemptDecision.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=tail.attempt_index,
            attempt_result_sha256=result.result_sha256,
            decision=draft.decision,
            reason=draft.reason,
            fallback_arm_id=draft.fallback_arm_id,
            continuation_evidence_sha256=evidence_sha256,
        )
        session.put_many((
            self._record(
                kind=RecordKind.CONTINUATION_EVIDENCE,
                key=str(tail.attempt_index),
                payload=evidence.payload(),
            ),
            self._record(
                kind=RecordKind.DECISION,
                key=str(tail.attempt_index),
                payload=decision.stored_dict(),
            ),
        ))
        return decision

    def _continuation_evidence(self, session: RunRecordSession, state):
        tail = state.attempts[-1]
        current_basis, current_sha = self._load_basis(session, tail)
        current_finding = prior_attempt_evidence_from_result(
            tail.result,
            attempt_index=tail.attempt_index,
            accepted_plan_ref=tail.context.phase_refs.plan_ref,
            plan_semantic_sha256=tail.context.phase_refs.plan_semantic_sha256,
        )
        previous_basis = None
        previous_sha = None
        previous_finding = None
        if len(state.attempts) > 1:
            previous = state.attempts[-2]
            previous_basis, previous_sha = self._load_basis(session, previous)
            if previous.result is not None:
                previous_finding = prior_attempt_evidence_from_result(
                    previous.result,
                    attempt_index=previous.attempt_index,
                    accepted_plan_ref=previous.context.phase_refs.plan_ref,
                    plan_semantic_sha256=previous.context.phase_refs.plan_semantic_sha256,
                )
        return decide_completed_attempt_continuation(
            run_id=self._manifest.run_id.value,
            attempt_index=tail.attempt_index,
            current_basis=current_basis,
            current_basis_sha256=current_sha,
            current_finding=current_finding,
            previous_basis=previous_basis,
            previous_basis_sha256=previous_sha,
            previous_finding=previous_finding,
        )

    def _load_basis(self, session: RunRecordSession, attempt):
        named = attempt.context.phase_refs.knowledge_basis_sha256
        if type(named) is not str or not named:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt context names no knowledge basis")
        stored = session.store.get(
            kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
            key=basis_record_key(attempt.attempt_index),
        )
        if stored is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt knowledge basis is missing")
        basis = basis_from_payload(stored.payload)
        if stored.sha256 != named or basis.digest() != named:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt knowledge basis differs from context")
        if basis.run_id != self._manifest.run_id.value or basis.attempt_index != attempt.attempt_index:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt knowledge basis belongs to another attempt")
        return basis, named

    def _finalize(
        self,
        session: RunRecordSession,
        state,
        terminal_authority: NextAttemptDecision | AttemptPreparationFailure,
    ) -> GoldRunResult:
        attempts = tuple(item.result for item in state.attempts)
        if any(item is None for item in attempts):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "finalization requires every attempt result")
        final = build_run_result(
            manifest=self._manifest,
            attempts=attempts,
            terminal_decision=terminal_authority,
        )
        session.put(self._record(kind=RecordKind.RUN_RESULT, key="final", payload=final.stored_dict()))
        return final

    def _tail_terminal_decision(self, state) -> NextAttemptDecision | None:
        if not state.attempts:
            return None
        decision = state.decision_for(state.attempts[-1].attempt_index)
        return decision if decision is not None and decision.terminal else None

    def _record(self, *, kind: str, key: str, payload: dict[str, object]) -> PendingRunRecord:
        return PendingRunRecord(kind=kind, key=key, payload=payload)

    def _fallback_arm_id(self) -> str:
        return f"baseline-explicit-{self._manifest.manifest_sha256[:32]}"

    def _require_manifest_match(self, manifest: GoldRunManifest) -> None:
        if type(manifest) is not GoldRunManifest or manifest.stored_dict() != self._manifest.stored_dict():
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "durable manifest differs from controller binding")

    def _revalidate_bindings(self) -> None:
        snapshot = self._identity_snapshot
        if type(snapshot) is not tuple or len(snapshot) != 7:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "controller identity snapshot is malformed")
        manifest, recovery, inputs, run_root, materializer, recovery_store, recovery_fence = snapshot
        manifest.validate_identity()
        require_attempt_inputs_port(inputs)
        require_attempt_phase_materializer(materializer, manifest=manifest, run_root=run_root)
        if (
            self._manifest is not manifest
            or self._record_recovery is not recovery
            or self._attempt_inputs is not inputs
            or self._run_root is not run_root
            or self._attempt_materializer is not materializer
            or recovery.store is not recovery_store
            or recovery.fence is not recovery_fence
        ):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "controller binding changed after construction")
