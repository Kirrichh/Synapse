"""Durable execution and recovery materialization for one Gold attempt.

This owner writes exact attempt phase boundaries, invokes the sealed Stage 10
and C1 boundaries, and reconstructs an interrupted attempt only from durable
checkpoint payloads.  It does not choose whether another attempt should run or
which terminal decision follows; those remain controller policy orchestration.
"""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage10.worker_context_adapter import (
    Stage10WorkerContextAdapter,
)

from .attempt_authority import (
    require_c1_receipt_authority,
    require_completed_delivery_authority,
    require_delivery_failure_authority,
)
from .attempt_delivery_failure import (
    attempt_delivery_failure_bytes,
    attempt_delivery_failure_ref,
    restore_attempt_delivery_failure,
)
from .attempt_inputs import PreparedAttemptInputs
from .c1_boundary import (
    C1AttemptBoundary,
    C1AuthorityReceipt,
    c1_authority_receipt_bytes,
    c1_authority_receipt_ref,
    classify_c1_authority_receipt,
    read_c1_authority_receipt,
    restore_c1_authority_receipt,
    run_c1_attempt,
)
from .completed_delivery_codec import (
    completed_worker_delivery_bytes,
    completed_worker_delivery_ref,
    restore_completed_worker_delivery,
)
from .delivery import (
    AttemptDeliveryRefusal,
    AttemptDeliveryUnavailable,
    CompletedWorkerDelivery,
    PreparedWorkerDelivery,
    dispatch_prepared_attempt,
    prepare_attempt_delivery,
    require_attempt_delivery_refusal,
    require_attempt_delivery_unavailable,
    require_completed_worker_delivery,
    require_prepared_worker_delivery,
)
from .models import (
    AttemptPhaseRefs,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldRunManifest,
)
from .attempt_knowledge_store import basis_record_key
from .records import RecordKind
from .run_progress import (
    AttemptProgress,
    AttemptProgressPhase,
    load_attempt_progress,
    progress_key,
    require_progress_payload,
)
from .run_recovery import PendingRunRecord, RunRecordSession
from .vocabulary import (
    AttemptOutcome,
    GoldRunFailureCode,
    GoldRunViolation,
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class AttemptPhaseMaterializer:
    """Execute or recover one attempt without owning run-level decisions."""

    __slots__ = (
        "_manifest",
        "_boundary",
        "_stage10_record_store",
        "_worker_adapter",
        "_run_root",
        "_identity_snapshot",
    )

    def __init__(
        self,
        *,
        manifest: GoldRunManifest,
        boundary: C1AttemptBoundary,
        stage10_record_store: FileStage10RecordStore,
        worker_adapter: Stage10WorkerContextAdapter,
        run_root: Path,
    ) -> None:
        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        manifest.validate_identity()
        if type(boundary) is not C1AttemptBoundary:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 boundary must be exact")
        if (
            type(stage10_record_store) is not FileStage10RecordStore
            or type(worker_adapter) is not Stage10WorkerContextAdapter
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt materializer requires exact Stage 10 adapters",
            )
        if type(run_root) is not type(Path()):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be exact")
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(self, "_boundary", boundary)
        object.__setattr__(self, "_stage10_record_store", stage10_record_store)
        object.__setattr__(self, "_worker_adapter", worker_adapter)
        object.__setattr__(self, "_run_root", run_root)
        object.__setattr__(
            self,
            "_identity_snapshot",
            (
                manifest,
                boundary,
                stage10_record_store,
                worker_adapter,
                worker_adapter.transport_binding,
                run_root,
                stage10_record_store.record_root,
                stage10_record_store.mutation_fence,
                stage10_record_store.coordinator_id,
            ),
        )
        self._revalidate_bindings()

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("AttemptPhaseMaterializer is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("AttemptPhaseMaterializer is immutable")

    def _revalidate_bindings(self) -> None:
        (
            manifest,
            boundary,
            stage10_store,
            worker_adapter,
            worker_transport,
            run_root,
            record_root,
            mutation_fence,
            coordinator_id,
        ) = self._identity_snapshot
        manifest.validate_identity()
        if (
            self._manifest is not manifest
            or self._boundary is not boundary
            or self._stage10_record_store is not stage10_store
            or self._worker_adapter is not worker_adapter
            or self._run_root is not run_root
            or worker_adapter.transport_binding is not worker_transport
            or stage10_store.record_root is not record_root
            or stage10_store.mutation_fence is not mutation_fence
            or stage10_store.coordinator_id != coordinator_id
            or mutation_fence.coordinator_id() != coordinator_id
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "attempt materializer binding changed after construction",
            )

    def execute_prepared_attempt(
        self,
        *,
        session: RunRecordSession,
        attempt_index: int,
        prepared_inputs: PreparedAttemptInputs,
    ) -> None:
        self._revalidate_bindings()
        prepared = prepare_attempt_delivery(
            manifest=self._manifest,
            attempt_index=attempt_index,
            inputs=prepared_inputs,
            record_store=self._stage10_record_store,
            worker_adapter=self._worker_adapter,
        )
        if type(prepared) is PreparedWorkerDelivery:
            self._execute_delivery_path(
                session=session,
                attempt_index=attempt_index,
                prepared_delivery=prepared,
            )
            return
        if type(prepared) is AttemptDeliveryRefusal:
            self._persist_delivery_failure(
                session=session,
                attempt_index=attempt_index,
                failure=prepared,
                unavailable=False,
            )
            return
        if type(prepared) is AttemptDeliveryUnavailable:
            self._persist_delivery_failure(
                session=session,
                attempt_index=attempt_index,
                failure=prepared,
                unavailable=True,
            )
            return
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery preparation returned an unknown sealed type",
        )

    def _execute_delivery_path(
        self,
        *,
        session: RunRecordSession,
        attempt_index: int,
        prepared_delivery: PreparedWorkerDelivery,
    ) -> None:
        delivery = require_prepared_worker_delivery(prepared_delivery)
        context = GoldAttemptContext.create(
            manifest=self._manifest,
            attempt_index=attempt_index,
            phase_refs=delivery.phase_refs,
        )
        started = AttemptProgress.create(
            manifest=self._manifest,
            context=context,
            phase=AttemptProgressPhase.DELIVERY_STARTED,
            predecessor=None,
        )
        session.put_many(
            self._initial_attempt_records(
                context=context,
                progress=(started,),
                basis=delivery.upstream.knowledge_basis,
            )
        )
        completed = dispatch_prepared_attempt(
            prepared=delivery,
            record_store=self._stage10_record_store,
            worker_adapter=self._worker_adapter,
        )
        require_completed_delivery_authority(context=context, completed=completed)
        completed_bytes = completed_worker_delivery_bytes(completed)
        completed_ref = completed_worker_delivery_ref(completed)
        worker_completed = AttemptProgress.create(
            manifest=self._manifest,
            context=context,
            phase=AttemptProgressPhase.WORKER_COMPLETED,
            predecessor=started,
            payload_ref=completed_ref,
            payload_bytes=completed_bytes,
        )
        session.put(
            self._record(
                kind=RecordKind.ATTEMPT_PROGRESS,
                key=progress_key(attempt_index, AttemptProgressPhase.WORKER_COMPLETED),
                payload=worker_completed.stored_dict(),
            )
        )
        self._run_or_recover_c1(
            session=session,
            context=context,
            completed=completed,
            predecessor=worker_completed,
        )

    def _persist_delivery_failure(
        self,
        *,
        session: RunRecordSession,
        attempt_index: int,
        failure: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
        unavailable: bool,
    ) -> None:
        checked = (
            require_attempt_delivery_unavailable(failure)
            if unavailable
            else require_attempt_delivery_refusal(failure)
        )
        context = GoldAttemptContext.create(
            manifest=self._manifest,
            attempt_index=attempt_index,
            phase_refs=AttemptPhaseRefs(
                knowledge_snapshot_ref=checked.upstream.knowledge_snapshot_ref,
                retrieval_ref=checked.upstream.retrieval_ref,
                replay_ref=checked.upstream.replay_ref,
                intent_ref=checked.upstream.intent_ref,
                plan_ref=checked.upstream.plan_ref,
                worker_context_id=None,
                worker_context_audit_sha256=None,
                knowledge_basis_sha256=checked.upstream.knowledge_basis_sha256,
            ),
        )
        payload_bytes = attempt_delivery_failure_bytes(checked)
        payload_ref = attempt_delivery_failure_ref(checked)
        progress = AttemptProgress.create(
            manifest=self._manifest,
            context=context,
            phase=(
                AttemptProgressPhase.DELIVERY_UNAVAILABLE
                if unavailable
                else AttemptProgressPhase.DELIVERY_REFUSED
            ),
            predecessor=None,
            payload_ref=payload_ref,
            payload_bytes=payload_bytes,
        )
        result = self._delivery_failure_result(context=context, failure=checked)
        records = self._initial_attempt_records(
            context=context,
            progress=(progress,),
            basis=checked.upstream.knowledge_basis,
        )
        session.put_many(
            records
            + (
                self._record(
                    kind=RecordKind.ATTEMPT_RESULT,
                    key=str(attempt_index),
                    payload=result.stored_dict(),
                ),
            )
        )

    def recover_unfinished_tail(
        self,
        session: RunRecordSession,
        state,
    ) -> None:
        """Dispatch the exact durable phase prefix to its sole recovery action.

        The closed phase map stays visible in one flat dispatcher; separate
        public recovery functions would allow callers to choose a phase rather
        than deriving it from the checkpoint chain.
        """

        self._revalidate_bindings()
        tail = state.attempts[-1]
        context = tail.context
        progress_state = load_attempt_progress(
            session.store,
            manifest=self._manifest,
            context=context,
        )
        latest = progress_state.latest
        if latest is None or latest.phase is AttemptProgressPhase.DELIVERY_STARTED:
            self._persist_interrupted_result(
                session=session,
                context=context,
                worker_result_ref=None,
            )
            return
        if latest.phase in (
            AttemptProgressPhase.DELIVERY_REFUSED,
            AttemptProgressPhase.DELIVERY_UNAVAILABLE,
        ):
            self._materialize_failure_from_progress(
                session=session,
                context=context,
                progress=latest,
            )
            return
        if latest.phase is AttemptProgressPhase.WORKER_COMPLETED:
            completed = self._restore_completed_delivery(
                context=context,
                progress=latest,
            )
            self._run_or_recover_c1(
                session=session,
                context=context,
                completed=completed,
                predecessor=latest,
            )
            return
        worker_progress = progress_state.get(AttemptProgressPhase.WORKER_COMPLETED)
        if worker_progress is None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "C1 recovery requires the completed worker delivery checkpoint",
            )
        if latest.phase is AttemptProgressPhase.C1_STARTED:
            self._recover_after_c1_started(
                session=session,
                context=context,
                worker_progress=worker_progress,
                c1_started=latest,
            )
            return
        if latest.phase is AttemptProgressPhase.C1_COMPLETED:
            self._materialize_c1_result_from_progress(
                session=session,
                context=context,
                worker_progress=worker_progress,
                c1_completed=latest,
            )
            return
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "attempt progress names an unknown recovery phase",
        )

    def _materialize_failure_from_progress(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        progress: AttemptProgress,
    ) -> None:
        payload_bytes, payload_ref = require_progress_payload(progress)
        failure = restore_attempt_delivery_failure(
            payload_bytes,
            expected_ref=payload_ref,
        )
        require_delivery_failure_authority(context=context, failure=failure)
        result = self._delivery_failure_result(context=context, failure=failure)
        session.put(
            self._record(
                kind=RecordKind.ATTEMPT_RESULT,
                key=str(context.attempt_index),
                payload=result.stored_dict(),
            )
        )

    def _run_or_recover_c1(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        completed: CompletedWorkerDelivery,
        predecessor: AttemptProgress,
    ) -> None:
        delivery = require_completed_worker_delivery(completed)
        require_completed_delivery_authority(context=context, completed=delivery)
        started = AttemptProgress.create(
            manifest=self._manifest,
            context=context,
            phase=AttemptProgressPhase.C1_STARTED,
            predecessor=predecessor,
        )
        session.put(
            self._record(
                kind=RecordKind.ATTEMPT_PROGRESS,
                key=progress_key(context.attempt_index, AttemptProgressPhase.C1_STARTED),
                payload=started.stored_dict(),
            )
        )
        execution = run_c1_attempt(
            self._boundary,
            gold_run_id=self._manifest.gold_run_id,
            attempt_id=context.attempt_id.value,
            delivery=delivery,
            run_root=self._run_root,
        )
        require_c1_receipt_authority(
            manifest=self._manifest,
            context=context,
            worker_delivery=delivery,
            receipt=execution.authority,
        )
        self._persist_c1_completion(
            session=session,
            context=context,
            worker_delivery=delivery,
            predecessor=started,
            receipt=execution.authority,
        )

    def _recover_after_c1_started(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        worker_progress: AttemptProgress,
        c1_started: AttemptProgress,
    ) -> None:
        completed = self._restore_completed_delivery(
            context=context,
            progress=worker_progress,
        )
        try:
            receipt = read_c1_authority_receipt(
                self._boundary,
                gold_run_id=self._manifest.gold_run_id,
                attempt_id=context.attempt_id.value,
            )
        except GoldRunViolation as exc:
            if exc.failure_code is GoldRunFailureCode.RECORD_MISSING:
                self._persist_interrupted_result(
                    session=session,
                    context=context,
                    worker_result_ref=completed_worker_delivery_ref(completed),
                )
                return
            raise
        require_c1_receipt_authority(
            manifest=self._manifest,
            context=context,
            worker_delivery=completed,
            receipt=receipt,
        )
        self._persist_c1_completion(
            session=session,
            context=context,
            worker_delivery=completed,
            predecessor=c1_started,
            receipt=receipt,
        )

    def _materialize_c1_result_from_progress(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        worker_progress: AttemptProgress,
        c1_completed: AttemptProgress,
    ) -> None:
        completed = self._restore_completed_delivery(
            context=context,
            progress=worker_progress,
        )
        payload_bytes, payload_ref = require_progress_payload(c1_completed)
        receipt = restore_c1_authority_receipt(
            payload_bytes,
            expected_ref=payload_ref,
        )
        require_c1_receipt_authority(
            manifest=self._manifest,
            context=context,
            worker_delivery=completed,
            receipt=receipt,
        )
        result = self._c1_result(
            context=context,
            worker_delivery=completed,
            receipt=receipt,
        )
        session.put(
            self._record(
                kind=RecordKind.ATTEMPT_RESULT,
                key=str(context.attempt_index),
                payload=result.stored_dict(),
            )
        )

    def _persist_c1_completion(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        worker_delivery: CompletedWorkerDelivery,
        predecessor: AttemptProgress,
        receipt: C1AuthorityReceipt,
    ) -> None:
        payload_bytes = c1_authority_receipt_bytes(receipt)
        payload_ref = c1_authority_receipt_ref(receipt)
        completed = AttemptProgress.create(
            manifest=self._manifest,
            context=context,
            phase=AttemptProgressPhase.C1_COMPLETED,
            predecessor=predecessor,
            payload_ref=payload_ref,
            payload_bytes=payload_bytes,
        )
        result = self._c1_result(
            context=context,
            worker_delivery=worker_delivery,
            receipt=receipt,
        )
        session.put_many(
            (
                self._record(
                    kind=RecordKind.ATTEMPT_PROGRESS,
                    key=progress_key(context.attempt_index, AttemptProgressPhase.C1_COMPLETED),
                    payload=completed.stored_dict(),
                ),
                self._record(
                    kind=RecordKind.ATTEMPT_RESULT,
                    key=str(context.attempt_index),
                    payload=result.stored_dict(),
                ),
            )
        )

    def _persist_interrupted_result(
        self,
        *,
        session: RunRecordSession,
        context: GoldAttemptContext,
        worker_result_ref: HashBoundRef | None,
    ) -> None:
        result = GoldAttemptResult.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=context.attempt_index,
            attempt_id=context.attempt_id,
            outcome=AttemptOutcome.CONTROLLER_INTERRUPTED,
            c1_status=None,
            oracle_invoked=False,
            oracle_resolved=None,
            worker_result_ref=worker_result_ref,
            c1_result_ref=None,
            oracle_result_ref=None,
            publication_refs=(),
            context_sha256=context.context_sha256,
        )
        session.put(
            self._record(
                kind=RecordKind.ATTEMPT_RESULT,
                key=str(context.attempt_index),
                payload=result.stored_dict(),
            )
        )

    def _delivery_failure_result(
        self,
        *,
        context: GoldAttemptContext,
        failure: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
    ) -> GoldAttemptResult:
        if type(failure) is AttemptDeliveryRefusal:
            outcome = AttemptOutcome.DELIVERY_REFUSED
        else:
            require_attempt_delivery_unavailable(failure)
            outcome = AttemptOutcome.DELIVERY_UNAVAILABLE
        return GoldAttemptResult.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=context.attempt_index,
            attempt_id=context.attempt_id,
            outcome=outcome,
            c1_status=None,
            oracle_invoked=False,
            oracle_resolved=None,
            worker_result_ref=None,
            c1_result_ref=None,
            oracle_result_ref=None,
            publication_refs=(),
            context_sha256=context.context_sha256,
        )

    def _c1_result(
        self,
        *,
        context: GoldAttemptContext,
        worker_delivery: CompletedWorkerDelivery,
        receipt: C1AuthorityReceipt,
    ) -> GoldAttemptResult:
        classification = classify_c1_authority_receipt(receipt)
        return GoldAttemptResult.create(
            run_id=self._manifest.run_id,
            gold_run_id=self._manifest.gold_run_id,
            attempt_index=context.attempt_index,
            attempt_id=context.attempt_id,
            outcome=classification.outcome,
            c1_status=classification.c1_status,
            oracle_invoked=classification.oracle_invoked,
            oracle_resolved=classification.oracle_resolved,
            worker_result_ref=completed_worker_delivery_ref(worker_delivery),
            c1_result_ref=receipt.c1_result_ref,
            oracle_result_ref=receipt.oracle_result_ref,
            publication_refs=(),
            context_sha256=context.context_sha256,
        )

    def _restore_completed_delivery(
        self,
        *,
        context: GoldAttemptContext,
        progress: AttemptProgress,
    ) -> CompletedWorkerDelivery:
        payload_bytes, payload_ref = require_progress_payload(progress)
        completed = restore_completed_worker_delivery(
            payload_bytes,
            expected_ref=payload_ref,
        )
        require_completed_delivery_authority(context=context, completed=completed)
        return completed

    def _initial_attempt_records(
        self,
        *,
        context: GoldAttemptContext,
        progress: tuple[AttemptProgress, ...],
        basis: object | None = None,
    ) -> tuple[PendingRunRecord, ...]:
        records = [
            self._record(
                kind=RecordKind.ATTEMPT_CONTEXT,
                key=str(context.attempt_index),
                payload=context.stored_dict(),
            )
        ]
        if basis is not None:
            #: Published in the same batch as the context that names it. Two
            #: batches would let a run hold a context pointing at a basis the
            #: store never received, and the next attempt would refuse to
            #: continue for a reason that is nobody's fault.
            records.append(
                self._record(
                    kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
                    key=basis_record_key(context.attempt_index),
                    payload=basis.payload(),
                )
            )
        for item in progress:
            records.append(
                self._record(
                    kind=RecordKind.ATTEMPT_PROGRESS,
                    key=progress_key(context.attempt_index, item.phase),
                    payload=item.stored_dict(),
                )
            )
        return tuple(records)

    def _record(
        self,
        *,
        kind: str,
        key: str,
        payload: dict[str, object],
    ) -> PendingRunRecord:
        return PendingRunRecord(kind=kind, key=key, payload=payload)


def require_attempt_phase_materializer(
    value: object,
    *,
    manifest: GoldRunManifest,
    run_root: Path,
) -> AttemptPhaseMaterializer:
    """Require one exact materializer bound to this manifest and run root."""

    if type(value) is not AttemptPhaseMaterializer:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "attempt materializer must be exact",
        )
    value._revalidate_bindings()
    if (
        type(manifest) is not GoldRunManifest
        or type(run_root) is not type(Path())
        or value._manifest.stored_dict() != manifest.stored_dict()
        or value._run_root != run_root
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "attempt materializer belongs to another run",
        )
    return value


__all__ = ["AttemptPhaseMaterializer", "require_attempt_phase_materializer"]
