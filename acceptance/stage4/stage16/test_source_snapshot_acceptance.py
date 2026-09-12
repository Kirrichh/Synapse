"""Frozen history preserves unknown outcomes and detects physical substitution."""
import copy
from dataclasses import replace
import json
import sys

import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn
from acceptance.stage4.stage10._builders import hash_ref
from synapse.experiments.gold import source_verification as verifier
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.source_snapshot import capture_project_source_snapshot, read_source_snapshot, source_experience_delivery
from synapse.experiments.gold.stage10.context_codec import decode_base64url
from synapse.experiments.gold.stage10.intent import AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract


def test_unknown_command_outcome_survives_freezing_without_reexecution(tmp_path, monkeypatch):
    _, state, path = prepare(tmp_path, execute=True)
    value = json.loads(path.read_text())
    counter = tmp_path / 'effects'
    value['claim'].update(kind='VERIFICATION_RECIPE', recipe={
        'command': [sys.executable, '-B', '-c', f'from pathlib import Path; Path({str(counter)!r}).write_text("once"); print("done")'],
        'expectation': {'expected_exit_codes': [0], 'expected_nonzero_exit': False,
            'combined_output_contains': ['done'], 'combined_output_not_contains': [], 'timeout_seconds': 10}})
    path.write_text(json.dumps(value))
    original = verifier.run_expected_command
    def die_after_effect(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('lost process before command-result retention')
    monkeypatch.setattr(verifier, 'run_expected_command', die_after_effect)
    with pytest.raises(SystemExit):
        execute_source_ingestion(state_root=state, input_path=path)
    condition = hash_ref(RefKind.CONTRACT_CONDITION, 'acceptance-condition')
    task = GoverningTaskContract(task_id='snapshot-task', task_statement='Check double value',
        repository_revision_sha256=value['claim']['revision'], allowed_scope=create_repository_scope(('calc.py',)),
        required_capabilities=('read',), target_bindings=(hash_ref(RefKind.BINDING, 'target'),),
        effects=(EffectConstraint('effect', EffectDisposition.EXPECTED, EffectKind.PATH_MODIFIED, 'calc.py', condition),),
        acceptance=(AcceptanceCriterion('acceptance', AcceptanceKind.CONTRACT_CONDITION, condition),))
    snapshot, knowledge, _ = capture_project_source_snapshot(project=open_gold_project(state), task=task, limit=64)
    assert not knowledge['candidates']  # Retention creates no executable behavior.
    actual = read_source_snapshot(snapshot, task=task)
    held = actual['selected'][0]['experience']
    assert held['execution'] == 'STARTED_UNKNOWN' and held['applicability'] == 'UNASSESSED'
    assert held['command_result'] is None and held['sources'] and held['bindings']
    local = source_experience_delivery(snapshot)['information']
    record = json.loads(decode_base64url(local['items'][0]['content_base64url']))
    assert record['execution'] == 'STARTED_UNKNOWN' and record['command_result'] is None
    assert counter.read_text() == 'once'
    with pytest.raises(ValueError, match='another task'):
        read_source_snapshot(snapshot, task=replace(task, task_statement='Another task'))
    forged = copy.deepcopy(snapshot)
    forged['recall']['selected'][0]['experience']['execution'] = 'EXITED_ZERO'
    with pytest.raises(ValueError, match='physical history'):
        read_source_snapshot(forged)
    assert learn(state, path)[1]['status'] == 'INTERRUPTED'
    assert read_source_snapshot(snapshot) == actual and counter.read_text() == 'once'
