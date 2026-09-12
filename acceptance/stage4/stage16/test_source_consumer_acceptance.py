"""A new task receives verified source knowledge after a process restart."""

import json
import base64
from dataclasses import replace

import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case


def test_source_recipe_reaches_actual_replay_and_worker_without_rewriting_its_origin(tmp_path):
    case, published = consumer_case(tmp_path, learn_recipe=True, include_fact=True)
    original = published['knowledge']['candidates'][0]['attestation']
    consumer_ref = json.loads(case.input_path.read_text())['observation']['task_contract_ref']
    declaration = json.loads(case.input_path.read_text())
    assert declaration['task_contract']['schema_version'] == 'synapse.stage4.gold.governing-task/v2'
    assert 'behavior_refs' not in declaration['task_contract']
    assert original['task_contract_ref'] != consumer_ref
    code, pending = case.start()
    assert code == 3, pending
    # The real frozen source corpus can be enumerated in any order without
    # turning that order into a knowledge relevance signal.
    from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
    from synapse.experiments.gold.run_knowledge import RunKnowledge
    from synapse.experiments.gold.knowledge_environment import open_gold_project
    from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
    from synapse.experiments.gold.canonicalization import RefKind
    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id
    from acceptance.stage4.stage10._builders import hash_ref
    frozen = reopen_frozen_inputs(case.run_root)
    task = GoverningTaskContract.from_dict(declaration['task_contract'])
    project = open_gold_project(case.state_root)
    knowledge = RunKnowledge(inputs=frozen, project=project, task=task)
    descriptors = tuple(item[1].descriptor_id for item in knowledge.candidates)
    assert len(descriptors) == 2
    query = compute_record_id(domain=IdentityDomain.RETRIEVAL_QUERY, canonical_bytes=b'{"purpose":"ranking-order"}')
    inputs = {item: knowledge.ranking_input_ref(query, item) for item in descriptors}
    scores = {item: knowledge.score(query, item, inputs[item]) for item in descriptors}
    assert set(scores.values()) == {1_000_000}
    knowledge.candidates = tuple(reversed(knowledge.candidates))
    assert scores == {item: knowledge.score(query, item, inputs[item]) for item in descriptors}
    with pytest.raises(ValueError):
        knowledge.score(query, descriptors[0], inputs[descriptors[1]])
    knowledge.task = replace(task, target_bindings=(hash_ref(RefKind.BINDING, 'unrelated-target'),))
    with pytest.raises(ValueError):
        knowledge.score(query, descriptors[0], inputs[descriptors[0]])
    assert knowledge.score(query, descriptors[0], knowledge.ranking_input_ref(query, descriptors[0])) == 0
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed['status'] == 'GOLD_STOPPED_NO_PROGRESS', completed
    assert completed['result']['structured_outcome']['payload']['status'] == 'NO_CANDIDATE'
    prompt = case.worker.read_text()
    assert json.loads(prompt)['schema_version'] == 'synapse.worker.task-input/v1'
    assert 'observed-add -1' not in prompt
    assert 'accepted_plan' not in prompt
    information = json.loads(case.worker.with_suffix('.information.json').read_text())
    local_records = [json.loads(base64.urlsafe_b64decode(item['content_base64url'] + '=' * (-len(item['content_base64url']) % 4)))
                     for item in information['items']]
    knowledge = [item for item in local_records if item.get('schema_version') == 'synapse.stage4.gold.source-knowledge/v1']
    assert len(knowledge) == 2
    assert not any(item.get('schema_version') == 'synapse.stage4.gold.source-procedure/v1' for item in local_records)
    recipe = next(item for item in knowledge if item['kind'] == 'VERIFICATION_RECIPE')
    assert recipe['sources'][0]['path'] == 'src/calc.py'
    assert recipe['recipe']['command']
    content = '\n'.join(base64.urlsafe_b64decode(item['content_base64url'] + '=' * (-len(item['content_base64url']) % 4)).decode()
                        for item in information['items'])
    assert 'observed-add -1' in content
    assert 'historically-observed-command-contract' in content
    assert 'transcript_matched":true' in content
    assert original in [item['attestation'] for item in json.loads(case.knowledge_path.read_text())['candidates']]

    from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.stage14.graph import LineageGraph
    from synapse.experiments.gold.stage14.sources import read_input_graph
    from synapse.experiments.gold.persistence import PersistenceViolation
    from synapse.experiments.gold.runner.state_machine import load_run_state
    from synapse.experiments.gold.runner.attempt_knowledge import basis_from_payload
    from synapse.experiments.gold.runner.attempt_knowledge_store import basis_record_key
    from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
    store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
    context = load_run_state(store).attempts[0].context
    from synapse.experiments.gold.replay_store import FileReplayStore
    from synapse.experiments.gold.replay_vm_adapter import read_replayed_return_value
    from synapse.experiments.gold.source_verification import source_ref, canonical, SOURCE_KNOWLEDGE_V1
    replay_store = FileReplayStore(case.run_root / 'replay' / 'records',
        mutation_fence=project.fence)
    replay = replay_store.require_result(context.phase_refs.replay_ref)
    actual_returns = [read_replayed_return_value(item, replay_store.open_snapshot(item.terminal_snapshot_ref))
                      for item in replay.observations]
    # Newly learned sources compute applicability from this invocation's task
    # bindings. Existing v1 source records still have their identity-only ABI.
    assert actual_returns == [[1, len(task.target_bindings), 0]] * len(knowledge)
    assert all(not item.consumed_activity_identities for item in replay.observations)
    basis_record = store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=basis_record_key(1))
    basis = basis_from_payload(basis_record.payload)
    assert context.phase_refs.knowledge_basis_sha256 == basis_record.sha256
    records = FileStage10RecordStore(case.run_root / 'stage10' / 'records',
        mutation_fence=FileSnapshotFence(case.run_root / 'stage10' / 'coordinator'), read_only=True)
    intent, accepted, _ = records.read_plan_bundle(
        intent_ref=context.phase_refs.intent_ref, accepted_plan_ref=context.phase_refs.plan_ref)
    assert intent.task_contract_ref.to_dict() == consumer_ref
    assert len(intent.behavior_refs) == 2
    assert intent.behavior_refs == basis.admitted_subject_refs
    assert basis.point_of_use_admitted
    assert set(accepted.candidate.operations[0].input_refs) == set(task.target_bindings + intent.behavior_refs)
    graph = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key='1').payload)
    roles = dict(graph.roles)
    ancestors = {node.node_id for node in graph.ancestors(roles['worker_context'])}
    for role in ('source_producer.0.source_operation', 'source_producer.0.source.0',
                 'source_producer.0.publication', 'input.replay_result'):
        assert roles[role] in ancestors, role
    catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key='1').payload
    physical = next(item for item in published['knowledge']['files']
                    if item['ref']['schema_id'] == 'synapse.stage4.gold.repository-source/v1')
    from pathlib import Path
    path = Path(physical['path'])
    raw = path.read_bytes()
    path.unlink()
    try:
        with pytest.raises((ValueError, OSError, PersistenceViolation)):
            read_input_graph(catalog)
    finally:
        path.write_bytes(raw)
    assert read_input_graph(catalog)
