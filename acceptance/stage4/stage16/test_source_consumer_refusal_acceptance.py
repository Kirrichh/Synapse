"""Previously admitted source knowledge cannot inherit a changed environment."""

import json

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.source_verification import canonical, source_ref
from synapse.experiments.gold.canonicalization import RefKind


def test_changed_consumer_environment_prevents_worker_delivery(tmp_path):
    case, _ = consumer_case(tmp_path)
    declaration = json.loads(case.input_path.read_text())
    environment = declaration['observation']['environment_inputs'][1]
    original = environment['ref']
    raw = canonical({'python': 'another-runtime', 'meaning': 'explicitly changed consumer conditions'})
    ref = source_ref(raw, original['schema_id'], RefKind.ARTIFACT)
    path = tmp_path / 'changed-environment.json'
    path.write_bytes(raw)
    environment['ref'] = ref.to_dict()
    case.input_path.write_text(json.dumps(declaration))
    knowledge = json.loads(case.knowledge_path.read_text())
    knowledge['files'].append({'path': str(path), 'ref': ref.to_dict()})
    case.knowledge_path.write_text(json.dumps(knowledge))
    code, pending = case.start()
    assert code == 3, pending
    code, completed = case.approve(pending)
    assert code != 0, completed
    assert not case.worker.exists()
    assert completed['status'] == 'GOLD_UNAVAILABLE' and 'NOT_ADMITTED' in completed['detail'], completed
    # The producer remains the original historical observation, not the consumer's version.
    assert json.loads(case.knowledge_path.read_text())['candidates'][0]['attestation']['environment_inputs'][1]['ref'] == original
