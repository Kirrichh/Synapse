"""One heavy shard: automatic task -> learned source -> real Mini -> real C1.

Only the external model response is controlled. The independent acceptance
oracle executes the patch; it never receives a prescribed success flag. Source
identity replay remains such, and this test does not close procedural planning
or successful-method publication requirements.
"""
import hashlib
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.runner.records import RunRecordStore
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_automatically_resolved_task_runs_mini_and_independent_executable_verification(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    declaration = json.loads(case.input_path.read_text())
    base = declaration['config']['base_revision']
    original = (case.repo / 'src/calc.py').read_bytes()
    create_executing_oracle(tmp_path / 'harness', repo=case.repo, base_revision=base,
        target_paths=('src/calc.py',),
        command=(sys.executable, '-B', '-c',
            'from src.calc import add; assert all(add(a,b) == expected for a,b,expected in '
            '[(2,3,5),(-2,3,1),(0,0,0),(4,3,7),(-4,-3,-7)]); print("independent matrix passed")'))
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    proposal = {'schema_version': LOCAL_EDIT_PROPOSAL_V1, 'alternatives': [
        {'edits': [{'path': 'src/calc.py', 'old': 'not_present_in_source', 'new': 'replacement'}]},
        {'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': 'a + b'}]}]}
    with provider_endpoint(command=LOCAL_EDIT_COMMAND + json.dumps(proposal)) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file(), 'the acceptance environment must install pinned Mini'
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V1,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                           'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        frozen = reopen_frozen_inputs(case.run_root)
        assert frozen.data['worker_runtime']
        assert {item.qualname for item in frozen.resolve_targets()} == {'src.calc', 'add'}
        code, completed = case.approve(pending)
        assert code == 0, completed
        assert completed['status'] == 'GOLD_RESOLVED', completed
        assert completed['outcome_status'] == 'FULL', completed
        assert len(requests) == 1
        for private in ('source-knowledge/v1', 'source_bindings', 'target_resolution', 'local_edit_result', 'return a - b'):
            assert private not in json.dumps(requests)
        assert (case.repo / 'src/calc.py').read_bytes() == original
        observations = [json.loads(line) for line in (tmp_path / 'harness/oracle_observations.jsonl').read_text().splitlines()]
        observed, = observations
        assert observed['before_returncode'] != 0 and observed['after_returncode'] == 0
        assert observed['base_revision'] == base
        rows = [json.loads(line) for line in (case.run_root / 'gold_attempts.jsonl').read_text().splitlines()]
        row, = rows
        assert row['payload']['oracle_invoked'] and row['payload']['oracle_resolved'] is True
        code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
        assert code == 0 and resumed['status'] == completed['status'], resumed
        assert len(requests) == 1
        assert (tmp_path / 'harness/oracle_observations.jsonl').read_text().count('\n') == 1
        # The neutral local result binds source-dependent selection to the
        # actual candidate independently checked by C1 and the external oracle.
        store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        context = load_run_state(store).attempts[0].context
        progress = load_attempt_progress(store, manifest=frozen.manifest, context=context)
        raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
        delivery = restore_completed_worker_delivery(raw, expected_ref=ref)
        local = delivery.worker_result.diagnostics['local_edit_result']
        assert local['selected_index'] == 1
        assert local['candidates'][0]['reason'] == 'OLD_TEXT_NOT_UNIQUE'
        assert local['candidates'][1]['source_bindings'][0]['source_sha256'] == hashlib.sha256(original).hexdigest()
        assert local['candidates'][1]['patch_sha256'] == hashlib.sha256(delivery.worker_result.diff_text.encode()).hexdigest()
        # C1 rebuilds the oracle diff from the verified commit pair, including
        # Git metadata. Different patch encodings must produce the same bytes.
        binding, = local['candidates'][1]['source_bindings']
        assert binding['result_sha256'] == observed['result_sources'][binding['path']]
        assert hashlib.sha256(original).hexdigest() not in json.dumps(requests)
