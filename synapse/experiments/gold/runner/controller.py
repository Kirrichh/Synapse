"""Driving one Gold run from its frozen manifest to a terminal result.

The controller owns sequence and nothing else. It freezes the manifest, creates
one immutable context per attempt, sends that context through the delivery owner
(which crosses the §22 barrier), delegates the attempt to the C1 boundary,
classifies the result with the boundary's own classifier, persists every attempt
and asks the stop policy what follows. Each of those decisions belongs to a
different module, and the controller's job is to ask them in the right order —
which is the one thing a controller is for.

It keeps no truth in memory between processes. Durable records are the state:
``run()`` reloads the whole record set on every iteration and after a crash, so
a resumed run reaches the same decisions as the run that stopped. An attempt
whose result was never written is recorded as interrupted rather than re-run,
because re-running it under the same identity is the hidden retry §26 forbids.

What it must never do is decide what an attempt *achieved*. The oracle verdict
belongs to C1, the admission verdict to §22, the outcome label to the C1
classifier, and the stop rule to the policy. A controller that could set any of
them would be able to declare its own run successful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from synapse.experiments.gold.persistence import (
    StoreMutationFencePort,
    require_store_mutation_fence,
    store_transaction,
)
from synapse.experiments.gold.runner.c1_boundary import (
    C1AttemptBoundary,
    classify_c1_attempt,
    run_c1_attempt,
)
from synapse.experiments.gold.runner.delivery import WorkerDelivery
from synapse.experiments.gold.runner.models import (
    AttemptPhaseRefs,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunManifest,
    GoldRunResult,
    NextAttemptDecision,
)
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore
from synapse.experiments.gold.runner.state_machine import build_run_result, load_run_state
from synapse.experiments.gold.runner.stop_policy import decide_next_attempt
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    GoldRunFailureCode,
    GoldRunViolation,
)

#: Upstream Stage 4 owners supply the phase refs frozen into one attempt context.
AttemptPhaseRefsSource = Callable[[int], AttemptPhaseRefs]
#: Delivers one attempt's context to the worker. Production binds this to
#: ``delivery.deliver_attempt_context`` in ``runner_composition.py``, which is
#: where the §22 barrier crossing lives; the controller only sequences it.
AttemptDeliveryPort = Callable[[GoldAttemptContext], WorkerDelivery]
#: The worker's produced candidate for a verified delivery. Its exact type is
#: checked inside the C1 boundary, which is the only module that names it.
WorkerResultSource = Callable[[WorkerDelivery], object]
#: §26: the next attempt may use only newly admitted or revalidated knowledge.
NewKnowledgePredicate = Callable[[int], bool]


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldRunController:
    """Deterministic multi-attempt controller whose state is its durable records."""

    def __init__(
        self,
        *,
        manifest: GoldRunManifest,
        boundary: C1AttemptBoundary,
        fence: StoreMutationFencePort,
        phase_refs_source: AttemptPhaseRefsSource,
        delivery_port: AttemptDeliveryPort,
        worker_result_source: WorkerResultSource,
        new_knowledge_available: NewKnowledgePredicate,
    ) -> None:
        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        manifest.validate_identity()
        if type(boundary) is not C1AttemptBoundary:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "c1 boundary must be exact")
        require_store_mutation_fence(fence)
        seams = (phase_refs_source, delivery_port, worker_result_source, new_knowledge_available)
        if not all(callable(seam) for seam in seams):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "controller seams must be callable")
        if boundary.environment_kind != manifest.config.environment_kind:
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "c1 environment differs from the frozen run config",
            )
        self._manifest = manifest
        self._boundary = boundary
        self._fence = fence
        self._phase_refs_source = phase_refs_source
        self._delivery_port = delivery_port
        self._worker_result_source = worker_result_source
        self._new_knowledge_available = new_knowledge_available

    # -- public lifecycle ------------------------------------------------

    def start(self, run_root: Path) -> GoldRunManifest:
        """Persist the frozen manifest; identical bytes make this idempotent."""

        store = self._store(run_root)
        record = store.get(kind=RecordKind.MANIFEST, key="manifest")
        if record is not None and record.payload.get("record_sha256") != self._manifest.manifest_sha256:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run root holds a different run manifest")
        if record is None:
            self._put(store, kind=RecordKind.MANIFEST, key="manifest", payload=self._manifest.stored_dict())
        return self._manifest

    def run(self, run_root: Path) -> GoldRunResult:
        """Drive the run to a terminal state; safe to call again after a crash."""

        self._recover_abandoned_fence()
        self.start(run_root)
        store = self._store(run_root)
        while True:
            state = load_run_state(store)
            if state.final_result is not None:
                return state.final_result
            decision = self._decision_for_last_finished(store, state)
            if decision is not None:
                if decision.terminal:
                    return self._finalize(store, state, decision)
                continue
            if state.interrupted_indexes:
                self._record_interrupted(store, state, state.interrupted_indexes[0])
                continue
            self._execute_next_attempt(store, run_root, state)

    def load_result(self, run_root: Path) -> GoldRunResult:
        """Consumer-side revalidation of a terminal run result."""

        state = load_run_state(self._store(run_root))
        if state.final_result is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "the run has no terminal result yet")
        return state.final_result

    # -- phases ----------------------------------------------------------

    def _execute_next_attempt(self, store: RunRecordStore, run_root: Path, state) -> None:
        next_index = state.next_index
        if next_index > self._manifest.config.max_attempts:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt budget exhausted without a terminal decision")
        phase_refs = self._phase_refs_source(next_index)
        if type(phase_refs) is not AttemptPhaseRefs:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "phase refs source returned an invalid record")
        context = GoldAttemptContext.create(
            manifest=self._manifest, attempt_index=next_index, phase_refs=phase_refs
        )
        self._put(store, kind=RecordKind.ATTEMPT_CONTEXT, key=str(next_index), payload=context.stored_dict())

        delivery = self._deliver(context)
        if delivery is None:
            self._record_result(
                store, context, outcome=AttemptOutcome.DELIVERY_REFUSED, c1_status=None,
                oracle_invoked=False, oracle_resolved=None,
            )
            return

        c1_result = run_c1_attempt(
            self._boundary,
            gold_run_id=self._manifest.gold_run_id,
            attempt_id=context.attempt_id.value,
            worker_result=self._worker_result_source(delivery),
            run_root=run_root,
        )
        classification = classify_c1_attempt(c1_result)
        self._record_result(
            store, context, outcome=classification.outcome, c1_status=classification.c1_status,
            oracle_invoked=classification.oracle_invoked, oracle_resolved=classification.oracle_resolved,
        )

    def _deliver(self, context: GoldAttemptContext) -> WorkerDelivery | None:
        """Take this attempt through the delivery owner, or record its refusal.

        A refused consumption is an attempt outcome, not an escaping error: the
        run records that this attempt was never delivered and the stop policy
        decides what follows. Any other violation is a defect and propagates.
        """

        try:
            delivery = self._delivery_port(context)
        except GoldRunViolation as violation:
            if violation.failure_code is GoldRunFailureCode.CONSUMPTION_REFUSED:
                return None
            raise
        if type(delivery) is not WorkerDelivery:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery port returned an invalid delivery")
        return delivery

    def _decision_for_last_finished(self, store: RunRecordStore, state):
        finished = [item for item in state.attempts if item.finished]
        if not finished:
            return None
        last = finished[-1]
        if state.decision_for(last.attempt_index) is not None:
            return None
        budget_left = len(state.attempts) < self._manifest.config.max_attempts
        new_knowledge = self._new_knowledge_available(len(state.attempts) + 1) if budget_left else True
        draft = decide_next_attempt(
            outcome=last.result.outcome,
            attempts_used=len(state.attempts),
            max_attempts=self._manifest.config.max_attempts,
            new_knowledge_available=new_knowledge,
            fallback_policy=self._manifest.config.fallback_policy,
            fallback_arm_id=self._fallback_arm_id(),
        )
        decision = NextAttemptDecision.create(
            run_id=self._manifest.run_id,
            attempt_index=last.attempt_index,
            decision=draft.decision,
            reason=draft.reason,
            fallback_arm_id=draft.fallback_arm_id,
        )
        self._put(store, kind=RecordKind.DECISION, key=str(last.attempt_index), payload=decision.stored_dict())
        return decision

    def _record_interrupted(self, store: RunRecordStore, state, attempt_index: int) -> None:
        context = state.attempts[attempt_index - 1].context
        if context is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "interrupted attempt has no persisted context")
        self._record_result(
            store, context, outcome=AttemptOutcome.CONTROLLER_INTERRUPTED, c1_status=None,
            oracle_invoked=False, oracle_resolved=None,
        )

    def _finalize(self, store: RunRecordStore, state, decision: NextAttemptDecision) -> GoldRunResult:
        run_result = build_run_result(
            manifest=self._manifest,
            attempts=tuple(item.result for item in state.attempts if item.finished),
            terminal_decision=decision,
        )
        self._put(store, kind=RecordKind.RUN_RESULT, key="final", payload=run_result.stored_dict())
        return run_result

    # -- durable helpers -------------------------------------------------

    def _record_result(
        self,
        store: RunRecordStore,
        context: GoldAttemptContext,
        *,
        outcome: AttemptOutcome,
        c1_status: str | None,
        oracle_invoked: bool,
        oracle_resolved: bool | None,
    ) -> None:
        result = GoldAttemptResult.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=context.attempt_index,
            attempt_id=context.attempt_id,
            outcome=outcome,
            c1_status=c1_status,
            oracle_invoked=oracle_invoked,
            oracle_resolved=oracle_resolved,
            context_sha256=context.context_sha256,
        )
        self._put(
            store, kind=RecordKind.ATTEMPT_RESULT, key=str(context.attempt_index),
            payload=result.stored_dict(),
        )

    def _store(self, run_root: Path) -> RunRecordStore:
        if not isinstance(run_root, Path):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be a Path")
        return RunRecordStore(run_root)

    def _put(self, store: RunRecordStore, *, kind: str, key: str, payload: dict[str, object]) -> None:
        with store_transaction(self._fence) as ticket:
            store.put(kind=kind, key=key, canonical_payload=payload, ticket=ticket)

    def _recover_abandoned_fence(self) -> None:
        """Close a mutation interval abandoned by a crashed writer.

        The epoch journal requires an explicit recovery party that has
        established the state of every store under this coordinator. Under this
        fence the run-record store is the only one, and ``run()`` revalidates
        the whole record set before its first write, failing closed on any
        inconsistency — so this controller is that party.
        """

        if self._fence.current_epoch() % 2:
            self._fence.recover_abandoned_interval()

    def _fallback_arm_id(self) -> str:
        """A new Baseline arm identity; never a Gold identity (NR-13)."""

        return f"{self._manifest.gold_run_id}-baseline-arm"
