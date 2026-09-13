"""A real verified success guides a fresh task, which C1 must execute again."""
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import (
    AttemptProgressPhase, load_attempt_progress, require_progress_payload,
)
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage13.rejected_patch_profile import VERIFIED_PATCH_GUARD_V1
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V2, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_verified_positive_is_selected_locally_and_independently_executed_again(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    case = replace(case, cli_timeout_seconds=1800)
    declaration = json.loads(case.input_path.read_text())
    original = (case.repo / 'src/calc.py').read_bytes()
    create_executing_oracle(tmp_path / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=('src/calc.py',),
        command=(sys.executable, '-B', '-c',
            'from src.calc import add; assert all(add(a,b) == expected for a,b,expected in '
            '[(2,3,5),(-2,3,1),(0,0,0),(4,3,7),(-4,-3,-7)])'))

    def command(replacements):
        return LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1,
            'alternatives': [{'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': value}]}
                             for value in replacements]})

    # First learn a genuine successful execution. In the fresh run the public
    # proposal puts an incorrect but applicable edit first. Positive experience
    # must change the local selection; the provider sees no memory or result.
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(commands=[command(['a + b']), command(['0', 'a + b'])]) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file()
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V2,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                           'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        code, first = case.approve(pending)
        assert code == 0 and first['outcome_status'] == 'FULL', first
        records = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        publication = records.get(kind=RecordKind.REUSABLE_CANDIDATE, key='1').payload
        assert publication['unit']['core']['verification_contract']['profile_id'] == VERIFIED_PATCH_GUARD_V1
        retained_inputs = (case.run_root / 'experiment.json').read_bytes()

        declaration['run_id'] = 'source-positive-replay'
        probe = replace(case, run_root=tmp_path / 'positive-run', input_path=tmp_path / 'positive.json')
        probe.input_path.write_text(json.dumps(declaration))
        code, pending = probe.start()
        assert code == 3, pending
        frozen = reopen_frozen_inputs(probe.run_root)
        assert len(frozen.data['knowledge']['candidates']) == 2
        code, second = probe.approve(pending)
        assert code == 0 and second['outcome_status'] == 'FULL', second
        records = RunRecordStore(probe.run_root, mutation_fence=FileSnapshotFence(probe.run_root / 'run-coordinator'))
        context = load_run_state(records).attempts[0].context
        progress = load_attempt_progress(records, manifest=frozen.manifest, context=context)
        raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
        completed = restore_completed_worker_delivery(raw, expected_ref=ref)
        local = completed.worker_result.diagnostics['local_edit_result']
        assert local['selected_index'] == 1
        assert [item['applicability'] for item in local['candidates']] == ['APPLICABLE', 'APPLICABLE']
        assert local['candidates'][1]['prior_outcome'] is True
        observation = observe_local_context_influence(receipt=completed.delivery_receipt,
            invocation=completed.invocation, worker_result=completed.worker_result)
        evidence = observation.payload(completed.delivery_receipt)
        removed = next(item for item in evidence['comparisons'] if item['removed_role'] == 'EXECUTION_OBSERVATION')
        assert removed['selection_changed'] is True
        stage10 = FileStage10RecordStore(probe.run_root / 'stage10/records',
            mutation_fence=FileSnapshotFence(probe.run_root / 'stage10/coordinator'), read_only=True)
        stage10.require_local_context_influence(receipt=completed.delivery_receipt, observation=observation)
        observed_path = tmp_path / 'harness/oracle_observations.jsonl'
        observed = [json.loads(line) for line in observed_path.read_text().splitlines()]
        # Positive memory must never enter the negative duplicate veto: two
        # genuine oracle evaluations, each starting from the unfixed commit.
        assert len(observed) == 2 and len(requests) == 2
        assert all(item['before_returncode'] != 0 and item['after_returncode'] == 0 for item in observed)
        assert (case.repo / 'src/calc.py').read_bytes() == original
        assert (case.run_root / 'experiment.json').read_bytes() == retained_inputs
        for private in ('verified-patch', 'revision_word_', 'task_word_', 'return a - b', 'local_edit_result'):
            assert private not in json.dumps(requests)
        before = observed_path.read_bytes()
        code, resumed = probe.cli('project', 'resume', '--run-dir', probe.run_root)
        assert code == 0 and resumed['outcome_status'] == 'FULL', resumed
        assert len(requests) == 2 and observed_path.read_bytes() == before
