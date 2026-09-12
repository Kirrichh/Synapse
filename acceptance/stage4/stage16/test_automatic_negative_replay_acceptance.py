"""Real failed patch -> retained guard -> automatic next-run retrieval/replay.

Model proposals are controlled at the external endpoint. C1 and the separate
executable oracle determine outcomes from actual source and patches. No manual
knowledge list, target binding, recorder worker, or prescribed verdict is used.
"""
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.replay_store import FileReplayStore
from synapse.experiments.gold.replay_vm_adapter import read_replayed_return_value
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.run_progress import (AttemptProgressPhase,
    load_attempt_progress, require_progress_payload)
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.stage13.rejected_patch_profile import REJECTED_PATCH_GUARD_V4
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore, Stage10RecordKind
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_failed_patch_is_automatically_replayed_and_not_executed_again(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    # This scenario reconstructs a producer's full execution proof inside the
    # new consumer's proof. With both independently published outcomes, the
    # third run exceeded a 1800 s outer watchdog while still verifying/replaying
    # its inputs. This deadline covers that complete external CLI call; it is
    # not a model, CVM or execution budget and asserts no performance target.
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    declaration['config']['max_attempts'] = 2
    original = (case.repo / 'src/calc.py').read_bytes()
    create_executing_oracle(tmp_path / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=('src/calc.py',),
        command=(sys.executable, '-B', '-c',
            'from src.calc import add; assert all(add(a,b) == expected for a,b,expected in '
            '[(2,3,5),(-2,3,1),(0,0,0),(4,3,7),(-4,-3,-7)])'))
    # Identical public proposals in every attempt: only actual local experience
    # can change which applicable edit Mini selects.
    command = LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1,
        'alternatives': [{'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': replacement}]}
                         for replacement in ('5', 'a + b')]})
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(commands=[command, command, command]) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file()
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V1,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                           'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        code, result = case.approve(pending)
        assert code == 0 and result['outcome_status'] == 'FULL', result
        assert len(requests) == 2
        store = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        registration = store.get(kind=RecordKind.REUSABLE_CANDIDATE, key='1').payload
        assert registration['unit']['core']['verification_contract']['profile_id'] == REJECTED_PATCH_GUARD_V4
        observations_path = tmp_path / 'harness/oracle_observations.jsonl'
        observations = [json.loads(line) for line in observations_path.read_text().splitlines()]
        assert len(observations) == 2
        assert all(item['before_returncode'] != 0 for item in observations)
        assert observations[0]['after_returncode'] != 0 and observations[1]['after_returncode'] == 0
        def local_result(run_root, index):
            frozen = reopen_frozen_inputs(run_root)
            store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / 'run-coordinator'))
            context = load_run_state(store).attempts[index - 1].context
            progress = load_attempt_progress(store, manifest=frozen.manifest, context=context)
            raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
            completed = restore_completed_worker_delivery(raw, expected_ref=ref)
            influence = observe_local_context_influence(receipt=completed.delivery_receipt,
                invocation=completed.invocation, worker_result=completed.worker_result)
            evidence_store = FileStage10RecordStore(run_root / 'stage10/records',
                mutation_fence=FileSnapshotFence(run_root / 'stage10/coordinator'), read_only=True)
            refs = evidence_store.require_local_context_influence(receipt=completed.delivery_receipt, observation=influence)
            assessment = decode_canonical(evidence_store.get(kind=Stage10RecordKind.INFLUENCE_EVIDENCE,
                                                             ref=refs['assessment_ref']).payload)
            assert assessment['payload']['stage'] == 'INFLUENCED_PROVEN'
            observed = influence.payload(completed.delivery_receipt)
            assert observed['claim'] == 'LOCAL_SELECTION_DEPENDENCE_ONLY'
            changed = {item['removed_role'] for item in observed['comparisons'] if item['selection_changed']}
            assert 'REFERENCE' in changed
            local = completed.worker_result.diagnostics['local_edit_result']
            assert ('EXECUTION_OBSERVATION' in changed) == (local['selected_index'] == 1)
            graph = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key=str(index)).payload)
            roles = dict(graph.roles)
            assert {'context_influence', 'local_selection', 'influence_proof'} <= set(roles)
            assert roles['context_influence'] in {node.node_id for node in graph.ancestors(roles['verification'])}
            # A worker self-report cannot rewrite the independently observed
            # selection, even if its receipt and transport bindings are valid.
            forged = deepcopy(local)
            forged['selected_index'] = 1 - local['selected_index']
            result = replace(completed.worker_result, diagnostics={**completed.worker_result.diagnostics,
                                                                   'local_edit_result': forged})
            with pytest.raises(ValueError):
                observe_local_context_influence(receipt=completed.delivery_receipt,
                    invocation=completed.invocation, worker_result=result)
            return local
        assert local_result(case.run_root, 1)['selected_index'] == 0
        assert local_result(case.run_root, 2)['selected_index'] == 1
        assert local_result(case.run_root, 2)['candidates'][0]['reason'] == 'EXACT_VERIFIED_PATCH_REJECTED'
        frozen_original = (case.run_root / 'experiment.json').read_bytes()
        # A fresh run of the same original task must discover the newly learned
        # negative method without inserting its reference into an input file.
        declaration['run_id'] = 'source-negative-replay'
        declaration['config']['max_attempts'] = 1
        probe = replace(case, run_root=tmp_path / 'probe-run', input_path=tmp_path / 'probe.json')
        probe.input_path.write_text(json.dumps(declaration))
        code, pending = probe.start()
        assert code == 3, pending
        frozen = reopen_frozen_inputs(probe.run_root)
        assert frozen.data['source_snapshot']['run_publications']
        # The unsuccessful and successful attempts retain distinct claims.
        assert len(frozen.data['knowledge']['candidates']) == 3
        code, outcome = probe.approve(pending)
        assert code == 0 and outcome['outcome_status'] == 'FULL', outcome
        assert len(requests) == 3
        observations = [json.loads(line) for line in observations_path.read_text().splitlines()]
        assert len(observations) == 3 and observations[2]['before_returncode'] != 0
        assert observations[2]['after_returncode'] == 0
        local = local_result(probe.run_root, 1)
        assert local['selected_index'] == 1
        assert local['candidates'][0]['reason'] == 'EXACT_VERIFIED_PATCH_REJECTED'
        assert (case.repo / 'src/calc.py').read_bytes() == original
        assert (case.run_root / 'experiment.json').read_bytes() == frozen_original
        project = open_gold_project(case.state_root)
        records = RunRecordStore(probe.run_root, mutation_fence=FileSnapshotFence(probe.run_root / 'run-coordinator'))
        context = load_run_state(records).attempts[0].context
        replays = FileReplayStore(probe.run_root / 'replay/records', mutation_fence=project.fence)
        replay = replays.require_result(context.phase_refs.replay_ref)
        guard, = [item for item in replay.observations
                  if item.behavior_content_key == registration['unit']['content_key']['value']]
        assert guard.steps_executed > 10
        assert read_replayed_return_value(guard, replays.open_snapshot(guard.terminal_snapshot_ref))
        for private in ('rejected-patch', 'revision_word_', 'task_word_', 'return a - b', 'local_edit_result'):
            assert private not in json.dumps(requests)
        before = observations_path.read_bytes()
        code, resumed = probe.cli('project', 'resume', '--run-dir', probe.run_root)
        assert code == 0 and resumed['outcome_status'] == outcome['outcome_status'], resumed
        assert len(requests) == 3 and observations_path.read_bytes() == before
