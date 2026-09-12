"""Separate heavy shard: canonical learning/run/resume with no caller target refs.

The recording worker observes the two neutral inputs. This checks target
selection, real CVM replay, authority and recovery, not a completed code repair.
"""
import copy
import json

import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.run_inputs import FrozenGoldInputs, reopen_frozen_inputs, FROZEN_INPUT_SCHEMA_V6
from synapse.experiments.gold.stage10.context_codec import encode_canonical


def test_ordinary_task_resolves_project_targets_through_canonical_run_and_resume(tmp_path):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    declared = json.loads(case.input_path.read_text())
    assert 'target_records' not in declared
    assert 'target_bindings' not in declared['task_contract']
    code, pending = case.start()
    assert code == 3, pending
    frozen = reopen_frozen_inputs(case.run_root)
    assert frozen.data['schema_version'] == FROZEN_INPUT_SCHEMA_V6
    assert frozen.data['declaration']['task_contract'] == declared['task_contract']
    assert {item.qualname for item in frozen.resolve_targets()} == {'src.calc', 'add'}
    damaged = copy.deepcopy(frozen.data)
    damaged['target_resolution']['elements'].pop()
    with pytest.raises(ValueError, match='committed project evidence'):
        FrozenGoldInputs(encode_canonical(damaged)).verify_runtime(case.run_root)
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed['status'] == 'GOLD_STOPPED_NO_PROGRESS', completed
    public = json.loads(case.worker.read_text())
    assert public['statement'] == declared['task_contract']['task_statement']
    assert 'target_bindings' not in public and 'target_resolution' not in public
    assert json.loads(case.worker.with_suffix('.information.json').read_text())['items']
    before = {path.relative_to(case.run_root): path.read_bytes()
              for path in case.run_root.rglob('*') if path.is_file() and path.suffix == '.json'}
    code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
    assert code == 0 and resumed['status'] == completed['status'], resumed
    assert reopen_frozen_inputs(case.run_root).canonical_bytes == frozen.canonical_bytes
    for relative, raw in before.items():
        assert (case.run_root / relative).read_bytes() == raw
