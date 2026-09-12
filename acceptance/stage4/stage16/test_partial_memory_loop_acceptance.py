"""One real task loop: failed partial -> composed repair -> positive reuse.

Only the provider's proposed edits are controlled. Ordinary product entrypoints
own learning, retrieval, replay, Mini, C1, the independent executing oracle and
publication. This file is acceptance infrastructure, never a product path.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import AttemptProgressPhase, load_attempt_progress, require_progress_payload
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage13.rejected_patch_profile import REJECTED_PATCH_GUARD_V5
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROPOSAL_V1, propose_local_edits
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


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


def test_checked_partial_survives_failure_and_composes_before_positive_reuse(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True,
        extra_sources={'src/scale.py': 'def double(value):\n    return value\n'},
        task_statement='Fix add(a, b) in src/calc.py and double(value) in src/scale.py.')
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    originals = {path: (case.repo / path).read_bytes() for path in ('src/calc.py', 'src/scale.py')}
    create_executing_oracle(tmp_path / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=tuple(originals),
        command=(sys.executable, '-B', '-c',
            'from src.calc import add; from src.scale import double; '
            'assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(4,3),(0,0)]); '
            'assert all(double(x)==2*x for x in [-2,0,7])'))
    calc = {'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': 'a + b'}]}
    scale = {'edits': [{'path': 'src/scale.py', 'old': 'return value', 'new': 'return value * 2'}]}
    commands = [LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1,
        'alternatives': alternatives}) for alternatives in ([calc], [calc, scale], [])]
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(commands=commands) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file()
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V4,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        code, first = case.approve(pending)
        assert code == 0 and first['outcome_status'] == 'VERIFIED_REUSABLE_PARTIAL', first
        frozen, records, attempt, completed = completed_attempt(case)
        first_verification = attempt.result.structured_outcome['payload']['verification']['payload']
        assert first_verification['c1']['oracle_resolved'] is False
        assert first_verification['c1']['commands_complete'] is True
        assert not any(item['discharged'] for item in first_verification['obligations'])
        partial = records.get(kind=RecordKind.REUSABLE_CANDIDATE, key='1').payload
        contract = partial['unit']['core']['verification_contract']
        assert contract['profile_id'] == REJECTED_PATCH_GUARD_V5
        assert set(contract['expected_claims']) == {'exact-patch-did-not-resolve-task', 'exact-patch-passed-c1-contract'}
        patch_ref, = [ref for ref in partial['unit']['core']['artifact_refs']
            if ref['schema_id'] == 'synapse.stage4.gold.c1-patch-bytes/v1']
        first_inputs = (case.run_root / 'experiment.json').read_bytes()
        observations_path = tmp_path / 'harness/oracle_observations.jsonl'
        first_observation, = [json.loads(line) for line in observations_path.read_text().splitlines()]
        assert first_observation['before_returncode'] != 0 and first_observation['after_returncode'] != 0

        declaration['run_id'] = 'partial-composition'
        second_case = replace(case, run_root=tmp_path / 'composed-run', input_path=tmp_path / 'composed.json')
        second_case.input_path.write_text(json.dumps(declaration))
        code, pending = second_case.start()
        assert code == 3, pending
        assert len(reopen_frozen_inputs(second_case.run_root).data['knowledge']['candidates']) == 2
        code, second = second_case.approve(pending)
        assert code == 0 and second['outcome_status'] == 'FULL', second
        _, records, attempt, completed = completed_attempt(second_case)
        local = completed.worker_result.diagnostics['local_edit_result']
        selected = local['candidate_origins'][local['selected_index']]
        assert selected == {'kind': 'LOCAL_PARTIAL_MEMORY', 'patch_sha256': patch_ref['sha256'], 'index': 1}
        assert local['candidates'][local['selected_index']]['prior_outcome'] is None
        assert local['touched_files'] == sorted(originals)
        # The repeated original failure remains an exact early exclusion.
        original_index = local['candidate_origins'].index({'kind': 'PUBLIC_PROPOSAL', 'index': 0})
        assert local['candidates'][original_index]['reason'] == 'EXACT_VERIFIED_PATCH_REJECTED'
        information = LocalInformationInput(completed.invocation.information_text.encode())
        without_feedback = information.to_dict()
        without_feedback['items'] = [item for item in without_feedback['items'] if item['role'] != 'EXECUTION_OBSERVATION']
        ablated = propose_local_edits(task=WorkerTaskInput(completed.invocation.payload_text.encode()),
            information=LocalInformationInput(canonical_json_bytes(without_feedback)), proposal=local['proposal'], profile=LOCAL_EDIT_PROFILE_V4)
        assert ablated['candidate_origins'][ablated['selected_index']] == {'kind': 'PUBLIC_PROPOSAL', 'index': 0}
        assert ablated['touched_files'] == ['src/calc.py']
        proof = attempt.result.structured_outcome['payload']['verification']['payload']
        assert proof['c1']['oracle_resolved'] is True
        assert [item['operation_id'] for item in proof['obligations']] == [
            'operation-edit-1', 'operation-edit-2', 'operation-check-1', 'operation-check-2']
        assert all(item['discharged'] is True for item in proof['obligations'])
        solved_patch = hashlib.sha256(local['diff_text'].encode()).hexdigest()
        second_inputs = (second_case.run_root / 'experiment.json').read_bytes()

        declaration['run_id'] = 'positive-after-partial'
        third_case = replace(case, run_root=tmp_path / 'positive-run', input_path=tmp_path / 'positive.json')
        third_case.input_path.write_text(json.dumps(declaration))
        code, pending = third_case.start()
        assert code == 3, pending
        assert len(reopen_frozen_inputs(third_case.run_root).data['knowledge']['candidates']) == 3
        code, third = third_case.approve(pending)
        assert code == 0 and third['outcome_status'] == 'FULL', third
        _, _, _, completed = completed_attempt(third_case)
        local = completed.worker_result.diagnostics['local_edit_result']
        assert local['proposal']['alternatives'] == []
        assert local['candidate_origins'][local['selected_index']] == {'kind': 'LOCAL_MEMORY', 'patch_sha256': solved_patch}
        assert hashlib.sha256(local['diff_text'].encode()).hexdigest() == solved_patch
        information = LocalInformationInput(completed.invocation.information_text.encode())
        empty = LocalInformationInput(canonical_json_bytes({**information.to_dict(), 'items': [
            item for item in information.to_dict()['items'] if item['role'] != 'EXECUTION_OBSERVATION']}))
        assert propose_local_edits(task=WorkerTaskInput(completed.invocation.payload_text.encode()), information=empty,
            proposal=local['proposal'], profile=LOCAL_EDIT_PROFILE_V4)['status'] == 'NO_APPLICABLE_PROPOSAL'
        observed = [json.loads(line) for line in observations_path.read_text().splitlines()]
        assert len(observed) == 3 and len(requests) == 3
        assert all(item['before_returncode'] != 0 for item in observed)
        assert [item['after_returncode'] == 0 for item in observed] == [False, True, True]
        for private in ('local_edit_result', 'source_bindings', 'LOCAL_PARTIAL_MEMORY',
                'exact-patch-passed-c1-contract', patch_ref['sha256'], solved_patch, 'revision_word_', 'return a - b'):
            assert private not in json.dumps(requests)
        assert {path: (case.repo / path).read_bytes() for path in originals} == originals
        assert (case.run_root / 'experiment.json').read_bytes() == first_inputs
        assert (second_case.run_root / 'experiment.json').read_bytes() == second_inputs
        before = observations_path.read_bytes()
        code, resumed = third_case.cli('project', 'resume', '--run-dir', third_case.run_root)
        assert code == 0 and resumed['outcome_status'] == 'FULL', resumed
        assert len(requests) == 3 and observations_path.read_bytes() == before
        (tmp_path / 'loop-evidence.json').write_text(json.dumps({
            'outcomes': [first['outcome_status'], second['outcome_status'], third['outcome_status']],
            'partial_patch': patch_ref['sha256'], 'composed_patch': solved_patch,
            'actual_oracle_observations': observed, 'provider_calls': len(requests),
            'resume_repeated_effects': False, 'unchanged_original_sources': True}, indent=2))
