"""Physical project history reaches Gold's private worker input and lineage."""
import hashlib
import json

import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case, learn
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.source_snapshot import read_source_snapshot, source_experience_delivery
from synapse.experiments.gold.stage10.context_codec import decode_base64url


def test_frozen_failed_experience_reaches_local_input_and_physical_lineage(tmp_path):
    case, _ = consumer_case(tmp_path, learn_recipe=True)
    failed = json.loads((tmp_path / 'learn.json').read_text())
    failed['claim']['operation_id'] = 'observed-failed-attempt'
    failed['claim']['recipe']['command'][-1] = 'import sys; print("retained-negative-example"); sys.exit(7)'
    path = tmp_path / 'failed.json'
    path.write_text(json.dumps(failed))
    code, result = learn(case.state_root, path)
    assert code == 2 and result['status'] == 'REJECTED', result
    corpus = json.loads(case.knowledge_path.read_text())
    case.knowledge_path.write_text(json.dumps({'schema_version': 'synapse.stage4.gold.knowledge-input/v3',
        'files': corpus['files'], 'experience_limit': 64}))
    code, pending = case.start()
    assert code == 3, pending
    frozen = reopen_frozen_inputs(case.run_root)
    snapshot = frozen.data['source_snapshot']
    original = read_source_snapshot(snapshot)
    local = source_experience_delivery(snapshot)
    records = [json.loads(decode_base64url(item['content_base64url'])) for item in local['information']['items']]
    failure = next(item for item in records if item.get('execution') == 'EXITED_NONZERO')
    assert failure['command_result']['returncode'] == 7
    assert failure['applicability'] == 'UNASSESSED'
    assert failure['sources'] and failure['symbols']
    assert all(item['role'] == 'REFERENCE' for item in local['information']['items'])
    assert 'operation_id' not in json.dumps(records) and 'transaction_id' not in json.dumps(records)
    # Later learning must neither enter this run nor invalidate its retained prefix.
    failed['claim']['operation_id'] = 'later-unselected-attempt'
    path.write_text(json.dumps(failed))
    assert learn(case.state_root, path)[0] == 2
    assert read_source_snapshot(snapshot) == original
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed['status'] == 'GOLD_STOPPED_NO_PROGRESS', completed
    task = json.loads(case.worker.read_text())
    assert task['schema_version'] == 'synapse.worker.task-input/v1'
    assert 'retained-negative-example' not in json.dumps(task)
    received = json.loads(case.worker.with_suffix('.information.json').read_text())
    assert all(item in received['items'] for item in local['information']['items'])
    from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.stage14.graph import LineageGraph
    from synapse.experiments.gold.stage14.sources import read_input_graph
    from synapse.experiments.gold.persistence import PersistenceViolation
    store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
    graph = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key='1').payload)
    roles = dict(graph.roles)
    ancestors = {node.node_id for node in graph.ancestors(roles['worker_context'])}
    assert roles['input.source_experience'] in ancestors
    assert roles['input.replay_result'] in ancestors
    assert any(node.reference.sha256 == local['snapshot_ref']['sha256'] for node in graph.ancestors(roles['worker_context']))
    catalog = store.get(kind=RecordKind.LINEAGE_SOURCES, key='1').payload
    operation = next(item for item in snapshot['operations'] if item['operation_key'] == hashlib.sha256(b'observed-failed-attempt').hexdigest())
    damaged = case.state_root / 'source-operations' / operation['operation_key'] / 'started' / 'record.json'
    raw = damaged.read_bytes()
    damaged.unlink()
    try:
        with pytest.raises((ValueError, OSError, PersistenceViolation)):
            read_input_graph(catalog)
    finally:
        damaged.write_bytes(raw)
    assert read_input_graph(catalog)
