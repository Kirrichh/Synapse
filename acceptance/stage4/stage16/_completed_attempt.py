"""Physical readback of a completed single-attempt acceptance run.

The CLI has already finished before this reader opens frozen inputs, run
records and the independent local-influence proof. It produces no history,
dispatches no work and grants no authority.
"""
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner.records import RunRecordStore
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import (
    AttemptProgressPhase, load_attempt_progress, require_progress_payload,
)
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore


def completed_attempt(case):
    frozen = reopen_frozen_inputs(case.run_root)
    records = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
    attempt, = load_run_state(records).attempts
    progress = load_attempt_progress(records, manifest=frozen.manifest, context=attempt.context)
    raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
    completed = restore_completed_worker_delivery(raw, expected_ref=ref)
    observation = observe_local_context_influence(receipt=completed.delivery_receipt,
        invocation=completed.invocation, worker_result=completed.worker_result)
    stage10 = FileStage10RecordStore(case.run_root / 'stage10/records',
        mutation_fence=FileSnapshotFence(case.run_root / 'stage10/coordinator'), read_only=True)
    stage10.require_local_context_influence(receipt=completed.delivery_receipt, observation=observation)
    return frozen, records, attempt, completed
