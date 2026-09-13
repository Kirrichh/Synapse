"""A learned patch absent from the public proposal traverses the real Gold loop."""
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import pytest

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import (
    AttemptProgressPhase, load_attempt_progress, require_progress_payload,
)
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage13.publication_store import PublicationResult
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V3, LOCAL_EDIT_PROPOSAL_V1, propose_local_edits,
)
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_mini_proposes_retained_verified_patch_without_a_matching_model_alternative(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    # The external watchdog covers physical producer-lineage reconstruction as
    # well as execution. The 1800 s run reached actual C1/oracle success but was
    # killed while processing retained proof. Model/CVM/C1 budgets are unchanged.
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    original = (case.repo / 'src/calc.py').read_bytes()
    create_executing_oracle(tmp_path / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=('src/calc.py',),
        command=(sys.executable, '-B', '-c',
            'from src.calc import add; assert all(add(a,b) == expected for a,b,expected in '
            '[(2,3,5),(-2,3,1),(0,0,0),(4,3,7),(-4,-3,-7)])'))

    def command(replacement):
        return LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1,
            'alternatives': [{'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': replacement}]}]})

    # The second provider response never proposes addition. Mini must derive
    # that edit from independently retained local experience, then C1 must
    # actually execute it again from the same unfixed project commit.
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(commands=[command('a + b'), command('0')]) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file()
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V3,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                           'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        code, first = case.approve(pending)
        assert code == 0 and first['outcome_status'] == 'FULL', first
        records = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        publication = records.get(kind=RecordKind.REUSABLE_CANDIDATE, key='1').payload
        patch_ref, = [ref for ref in publication['unit']['core']['artifact_refs']
                      if ref['schema_id'] == 'synapse.stage4.gold.c1-patch-bytes/v1']
        assert patch_ref['sha256'] == publication['domain']['patch_sha256']
        retained_inputs = (case.run_root / 'experiment.json').read_bytes()

        declaration['run_id'] = 'retained-patch-replay'
        probe = replace(case, run_root=tmp_path / 'retained-patch-run', input_path=tmp_path / 'retained-patch.json')
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
        assert local['proposal']['alternatives'][0]['edits'][0]['new'] == '0'
        assert len(local['proposal']['alternatives']) == 1
        assert local['candidate_origins'][local['selected_index']] == {
            'kind': 'LOCAL_MEMORY', 'patch_sha256': patch_ref['sha256']}
        assert hashlib.sha256(local['diff_text'].encode()).hexdigest() == patch_ref['sha256']
        information = LocalInformationInput(completed.invocation.information_text.encode())
        items = information.to_dict()['items']
        patch_items = [item for item in items if item['role'] == 'REFERENCE' and
            base64.urlsafe_b64decode(item['content_base64url'] + '=' * (-len(item['content_base64url']) % 4))
            == local['diff_text'].encode()]
        assert len(patch_items) == 1
        # Remove only the patch bytes, retaining source and positive feedback.
        # A same-input local counterfactual then selects the incorrect public
        # proposal: a successful label alone did not supply this solution.
        without = LocalInformationInput(canonical_json_bytes({**information.to_dict(),
            'items': [item for item in items if item not in patch_items]}))
        changed = propose_local_edits(task=WorkerTaskInput(completed.invocation.payload_text.encode()),
            information=without, proposal=local['proposal'], profile=LOCAL_EDIT_PROFILE_V3)
        assert changed['diff_text'] != local['diff_text']
        assert changed['candidate_origins'] == [{'kind': 'PUBLIC_PROPOSAL', 'index': 0}]
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
        assert len(observed) == 2 and len(requests) == 2
        assert all(item['before_returncode'] != 0 and item['after_returncode'] == 0 for item in observed)
        assert (case.repo / 'src/calc.py').read_bytes() == original
        assert (case.run_root / 'experiment.json').read_bytes() == retained_inputs
        for private in ('verified-patch', patch_ref['sha256'], 'revision_word_', 'task_word_',
                        'return a - b', 'local_edit_result', 'LOCAL_MEMORY'):
            assert private not in json.dumps(requests)
        before = observed_path.read_bytes()
        code, resumed = probe.cli('project', 'resume', '--run-dir', probe.run_root)
        assert code == 0 and resumed['outcome_status'] == 'FULL', resumed
        assert len(requests) == 2 and observed_path.read_bytes() == before
        # Historical evidence must still be physically checked after successful
        # replay, publication and resume. No cached positive label can hide a
        # modified retained patch on a later read.
        transaction = publication['publication_transaction']['transaction_id']
        patch_path = case.state_root / 'publications/prepared' / transaction / patch_ref['sha256']
        retained = patch_path.read_bytes()
        try:
            patch_path.write_bytes(retained[:-1] + b'!')
            with pytest.raises((PersistenceViolation, ValueError)):
                PublicationResult(case.state_root / 'publications', transaction).payload()
        finally:
            patch_path.write_bytes(retained)
