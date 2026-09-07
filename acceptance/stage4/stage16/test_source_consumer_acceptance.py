"""A new task receives verified source knowledge after a process restart."""

import json

from acceptance.stage4.stage16._source_inputs import consumer_case


def test_source_recipe_reaches_actual_replay_and_worker_without_rewriting_its_origin(tmp_path):
    case, published = consumer_case(tmp_path, learn_recipe=True, include_fact=True)
    original = published['knowledge']['candidates'][0]['attestation']
    consumer_ref = json.loads(case.input_path.read_text())['observation']['task_contract_ref']
    assert original['task_contract_ref'] != consumer_ref
    code, pending = case.start()
    assert code == 3, pending
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed['status'] == 'GOLD_STOPPED_NO_PROGRESS', completed
    assert completed['result']['structured_outcome']['payload']['status'] == 'NO_CANDIDATE'
    prompt = case.worker.read_text()
    assert 'Quoted historical knowledge' in prompt
    assert 'observed-add -1' in prompt
    assert 'historically-observed-command-contract' in prompt
    assert 'transcript_matched":true' in prompt
    assert original in [item['attestation'] for item in json.loads(case.knowledge_path.read_text())['candidates']]

    from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.stage14.graph import LineageGraph
    from synapse.experiments.gold.stage14.sources import read_input_graph
    from synapse.experiments.gold.persistence import PersistenceViolation
    import pytest
    store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
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
