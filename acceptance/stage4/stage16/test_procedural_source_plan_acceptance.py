"""Heavy shard: canonical source learning drives a retained method decision."""
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.procedural_observations import read_procedural_observations
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage10.planning_basis import read_planning_basis


def test_canonical_learning_replay_and_saved_plan_share_physical_method_evidence(tmp_path):
    case, _ = consumer_case(tmp_path, learn_recipe=True, include_fact=True, automatic_targets=True)
    code, pending = case.start()
    assert code == 3, pending
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed["status"] == "GOLD_STOPPED_NO_PROGRESS", completed
    store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / "run-coordinator"))
    context = load_run_state(store).attempts[0].context
    records = FileStage10RecordStore(case.run_root / "stage10" / "records",
        mutation_fence=FileSnapshotFence(case.run_root / "stage10" / "coordinator"), read_only=True)
    intent, accepted, _ = records.read_plan_bundle(
        intent_ref=context.phase_refs.intent_ref, accepted_plan_ref=context.phase_refs.plan_ref)
    basis = read_planning_basis(accepted.candidate.planning_basis)
    assert len(basis["alternatives"]) == 2
    assert len(basis["choices"]) == 1
    assert basis["uncovered_paths"] == []
    assert all(item["coverage"][0] == 1 for item in basis["alternatives"])
    chosen = basis["alternatives"][basis["choices"][0]["alternative_index"]]["subject_ref"]
    assert chosen in [ref.to_dict() for ref in accepted.candidate.operations[0].input_refs]
    frozen = reopen_frozen_inputs(case.run_root)
    catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key="1").payload
    assert read_procedural_observations(catalog=catalog, target_records=frozen.resolve_targets(),
        selected_subject_refs=intent.behavior_refs) == accepted.candidate.planning_basis
    before = accepted.candidate.canonical_bytes()
    code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
    assert code == 0 and resumed == completed
    assert records.read_plan_bundle(intent_ref=context.phase_refs.intent_ref,
        accepted_plan_ref=context.phase_refs.plan_ref)[1].candidate.canonical_bytes() == before
