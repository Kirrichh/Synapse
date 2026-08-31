"""Drive one immutable Gold run through delivery, C1, recovery, and stop policy.

The controller owns only orchestration. It validates one sealed construction,
uses one exclusive run-record session, writes phase boundaries before and after
external effects, and rebuilds any unfinished tail from durable records rather
than from memory. Admission, Stage 10 dispatch, C1 execution, record codecs,
and retry policy stay in their own owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.retrieval import retrieval_causal_record_ref

from .attempt_inputs import (
    AttemptInputsPort,
    KnowledgeDependencyUnavailable,
    NoNewKnowledge,
    PreparedAttemptInputs,
    require_attempt_inputs_port,
)
from .controller_recovery import (
    AttemptPhaseMaterializer,
    require_attempt_phase_materializer,
)
from .models import (
    GoldAttemptContext,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from .records import RecordKind
from .run_recovery import PendingRunRecord, RunRecordRecovery, RunRecordSession
from .state_machine import build_run_result, load_run_state
from .stop_policy import KnowledgeContinuationStatus, decide_next_attempt
from .vocabulary import (
    AttemptOutcome,
    GoldRunFailureCode,
    GoldRunViolation,
    TerminalDecisionKind,
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _same_ref(left: HashBoundRef, right: HashBoundRef) -> bool:
    return left.to_dict() == right.to_dict()


@dataclass(frozen=True)
class _DecisionPreparation:
    knowledge_status: KnowledgeContinuationStatus
    prepared_inputs: PreparedAttemptInputs | None
    next_retrieval_ref: HashBoundRef | None


class GoldRunController:
    """One sealed production controller bound to one exact run."""

    __slots__ = (
        "_manifest",
        "_record_recovery",
        "_attempt_inputs",
        "_run_root",
        "_attempt_materializer",
        "_identity_snapshot",
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
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "run record recovery must be exact",
        )
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
            (
                manifest,
                record_recovery,
                inputs,
                run_root,
                materializer,
                record_recovery.store,
                record_recovery.fence,
            ),
        )
        self._revalidate_bindings()

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldRunController is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldRunController is immutable")

    def execute(self) -> GoldRunResult:
        """Drive the bound run to a durable terminal result.

        The public entry point owns the exclusive session; its private driver
        cannot advance the state machine without that fenced authority.
        """

        self._revalidate_bindings()
        with self._record_recovery.session() as session:
            return self._drive_session(session)

    def _drive_session(self, session: RunRecordSession) -> GoldRunResult:
        """Advance only from state reloaded inside one active fenced session.

        The recovered-state loop remains one private driver so every branch
        starts from the same durable reload. Splitting individual phases into
        callable drivers would create alternate state-advance routes.
        """

        while True:
            state = self._load_started_state(session)
            if state is None:
                session.put(
                    self._record(
                        kind=RecordKind.MANIFEST,
                        key="manifest",
                        payload=self._manifest.stored_dict(),
                    )
                )
                continue
            if state.final_result is not None:
                return state.final_result
            terminal = self._tail_terminal_decision(state)
            if terminal is not None:
                return self._finalize(session, state, terminal)
            if state.interrupted_indexes:
                self._attempt_materializer.recover_unfinished_tail(session, state)
                continue
            if not state.attempts:
                prepared = self._bootstrap_first_attempt()
                self._attempt_materializer.execute_prepared_attempt(
                    session=session,
                    attempt_index=1,
                    prepared_inputs=prepared,
                )
                continue
            tail = state.attempts[-1]
            if tail.result is None:
                raise _fail(
                    GoldRunFailureCode.PHASE_INVALID,
                    "tail attempt lost its result",
                )
            decision = state.decision_for(tail.attempt_index)
            if decision is None:
                persisted, prepared = self._record_tail_decision(session, state)
                if persisted.terminal:
                    return self._finalize(session, state, persisted)
                if prepared is None:
                    raise _fail(
                        GoldRunFailureCode.PHASE_INVALID,
                        "CONTINUE decision lost its prepared next inputs",
                    )
                self._attempt_materializer.execute_prepared_attempt(
                    session=session,
                    attempt_index=tail.attempt_index + 1,
                    prepared_inputs=prepared,
                )
                continue
            if decision.decision is not TerminalDecisionKind.CONTINUE:
                return self._finalize(session, state, decision)
            prepared = self._reprepare_continued_attempt(
                attempt_index=tail.attempt_index + 1,
                previous_context=tail.context,
                decision=decision,
            )
            self._attempt_materializer.execute_prepared_attempt(
                session=session,
                attempt_index=tail.attempt_index + 1,
                prepared_inputs=prepared,
            )

    def load_result(self) -> GoldRunResult:
        """Reload the exact terminal result already persisted for this run."""

        self._revalidate_bindings()
        with self._record_recovery.session() as session:
            state = self._load_started_state(session)
            if state is None or state.final_result is None:
                raise _fail(
                    GoldRunFailureCode.PHASE_INVALID,
                    "the run has no terminal result yet",
                )
            return state.final_result

    def _load_started_state(self, session: RunRecordSession):
        record = session.store.get(kind=RecordKind.MANIFEST, key="manifest")
        if record is None:
            return None
        state = load_run_state(session.store)
        self._require_manifest_match(state.manifest)
        return state

    def _bootstrap_first_attempt(self) -> PreparedAttemptInputs:
        prepared = self._prepare_inputs(
            attempt_index=1,
            previous_context=None,
        )
        if type(prepared) is not PreparedAttemptInputs:
            raise _fail(
                GoldRunFailureCode.CONSUMPTION_REFUSED,
                "the first attempt requires prepared inputs after the durable manifest",
            )
        return prepared

    def _prepare_inputs(
        self,
        *,
        attempt_index: int,
        previous_context: GoldAttemptContext | None,
    ) -> PreparedAttemptInputs | NoNewKnowledge | KnowledgeDependencyUnavailable:
        value = self._attempt_inputs.prepare(
            manifest=self._manifest,
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        if type(value) is PreparedAttemptInputs:
            return value
        if type(value) is NoNewKnowledge:
            if value.attempt_index != attempt_index:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "no-new-knowledge response names another attempt",
                )
            if previous_context is None or not _same_ref(
                value.previous_retrieval_ref,
                previous_context.phase_refs.retrieval_ref,
            ):
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "no-new-knowledge response is not bound to the previous retrieval",
                )
            return value
        if type(value) is KnowledgeDependencyUnavailable:
            if value.attempt_index != attempt_index:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "knowledge-unavailable response names another attempt",
                )
            return value
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "attempt inputs returned an unknown availability type",
        )

    def _reprepare_continued_attempt(
        self,
        *,
        attempt_index: int,
        previous_context: GoldAttemptContext,
        decision: NextAttemptDecision,
    ) -> PreparedAttemptInputs:
        if decision.next_retrieval_causal_ref is None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "CONTINUE decision has no next retrieval authority",
            )
        prepared = self._prepare_inputs(
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        if type(prepared) is not PreparedAttemptInputs:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "CONTINUE decision could not be re-prepared exactly on restart",
            )
        retrieval_ref = retrieval_causal_record_ref(prepared.retrieval_causal_record)
        if not _same_ref(retrieval_ref, decision.next_retrieval_causal_ref):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "CONTINUE decision names different next-attempt retrieval authority",
            )
        return prepared

    def _record_tail_decision(
        self,
        session: RunRecordSession,
        state,
    ) -> tuple[NextAttemptDecision, PreparedAttemptInputs | None]:
        tail = state.attempts[-1]
        result = tail.result
        if result is None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "decision requires a finished attempt",
            )
        prep = self._prepare_decision_inputs(state)
        draft = decide_next_attempt(
            outcome=result.outcome,
            attempts_used=state.attempts_used,
            max_attempts=self._manifest.config.max_attempts,
            knowledge_status=prep.knowledge_status,
            fallback_policy=self._manifest.config.fallback_policy,
            fallback_arm_id=self._fallback_arm_id(),
        )
        if (
            draft.decision is TerminalDecisionKind.CONTINUE
            and prep.prepared_inputs is None
        ):
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "CONTINUE decision requires prepared next inputs",
            )
        decision = NextAttemptDecision.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=tail.attempt_index,
            attempt_result_sha256=result.result_sha256,
            decision=draft.decision,
            reason=draft.reason,
            fallback_arm_id=draft.fallback_arm_id,
            next_retrieval_causal_ref=prep.next_retrieval_ref,
        )
        session.put(
            self._record(
                kind=RecordKind.DECISION,
                key=str(tail.attempt_index),
                payload=decision.stored_dict(),
            )
        )
        return decision, prep.prepared_inputs

    def _prepare_decision_inputs(self, state) -> _DecisionPreparation:
        tail = state.attempts[-1]
        result = tail.result
        if result is None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "decision preparation requires a finished attempt",
            )
        if (
            result.outcome in (AttemptOutcome.RESOLVED, AttemptOutcome.C1_RESULT_INVALID)
            or state.attempts_used >= self._manifest.config.max_attempts
        ):
            return _DecisionPreparation(
                knowledge_status=KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED,
                prepared_inputs=None,
                next_retrieval_ref=None,
            )
        availability = self._prepare_inputs(
            attempt_index=tail.attempt_index + 1,
            previous_context=tail.context,
        )
        if type(availability) is PreparedAttemptInputs:
            next_ref = retrieval_causal_record_ref(availability.retrieval_causal_record)
            return _DecisionPreparation(
                knowledge_status=KnowledgeContinuationStatus.NEWLY_ADMITTED_OR_REVALIDATED,
                prepared_inputs=availability,
                next_retrieval_ref=next_ref,
            )
        if type(availability) is NoNewKnowledge:
            return _DecisionPreparation(
                knowledge_status=KnowledgeContinuationStatus.NO_NEW_KNOWLEDGE,
                prepared_inputs=None,
                next_retrieval_ref=None,
            )
        return _DecisionPreparation(
            knowledge_status=KnowledgeContinuationStatus.DEPENDENCY_UNAVAILABLE,
            prepared_inputs=None,
            next_retrieval_ref=None,
        )

    def _finalize(
        self,
        session: RunRecordSession,
        state,
        terminal_decision: NextAttemptDecision,
    ) -> GoldRunResult:
        attempts = tuple(item.result for item in state.attempts)
        if any(item is None for item in attempts):
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "finalization requires every attempt result to be durable",
            )
        final = build_run_result(
            manifest=self._manifest,
            attempts=attempts,
            terminal_decision=terminal_decision,
        )
        session.put(
            self._record(
                kind=RecordKind.RUN_RESULT,
                key="final",
                payload=final.stored_dict(),
            )
        )
        return final

    def _tail_terminal_decision(self, state) -> NextAttemptDecision | None:
        if not state.attempts:
            return None
        decision = state.decision_for(state.attempts[-1].attempt_index)
        if decision is None or not decision.terminal:
            return None
        return decision

    def _record(
        self,
        *,
        kind: str,
        key: str,
        payload: dict[str, object],
    ) -> PendingRunRecord:
        return PendingRunRecord(kind=kind, key=key, payload=payload)

    def _fallback_arm_id(self) -> str:
        return f"baseline-explicit-{self._manifest.manifest_sha256[:32]}"

    def _require_manifest_match(self, manifest: GoldRunManifest) -> None:
        if (
            type(manifest) is not GoldRunManifest
            or manifest.stored_dict() != self._manifest.stored_dict()
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "durable manifest differs from the controller binding",
            )

    def _revalidate_bindings(self) -> None:
        snapshot = self._identity_snapshot
        if type(snapshot) is not tuple or len(snapshot) != 7:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "controller identity snapshot is malformed",
            )
        (
            manifest,
            record_recovery,
            attempt_inputs,
            run_root,
            materializer,
            recovery_store,
            recovery_fence,
        ) = snapshot
        manifest.validate_identity()
        require_attempt_inputs_port(attempt_inputs)
        require_attempt_phase_materializer(
            materializer,
            manifest=manifest,
            run_root=run_root,
        )
        if (
            self._manifest is not manifest
            or self._record_recovery is not record_recovery
            or self._attempt_inputs is not attempt_inputs
            or self._run_root is not run_root
            or self._attempt_materializer is not materializer
            or record_recovery.store is not recovery_store
            or record_recovery.fence is not recovery_fence
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "controller binding changed after construction",
            )
