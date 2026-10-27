from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import sys
import subprocess

from acceptance.agents.coding_agents import use_model_agent
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._completed_attempt import completed_attempt
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16._source_inputs import consumer_case, recall
from synapse.experiments.gold.project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROPOSAL_V1


def test_exact_cli_new_oracle_and_resume(tmp_path, monkeypatch):
    from synapse.experiments.gold import canonicalization as canonical
    assert Path(canonical.__file__).resolve().is_relative_to(Path.cwd().resolve())
    case, publication = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    claim = json.loads((tmp_path / "learn.json").read_text())["claim"]
    recall_code, recalled = recall(case.state_root, tmp_path, claim, scope=claim["sources"])
    assert recall_code == 0, recalled
    evidence = {"kind": "behavior", "scenario": "source", "publication_status": publication["status"],
        "recall_state": recalled["state"], "eligible_count": recalled["eligible_count"],
        "selected": [{"paths": item["match"]["paths"], "status": item["experience"]["status"],
                      "verification": item["experience"]["verification"], "execution": item["experience"]["execution"],
                      "sources": [{"path": source["path"], "sha256": source["ref"]["sha256"]}
                                  for source in item["experience"]["sources"]]} for item in recalled["selected"]]}
    assert evidence["publication_status"] in {"PUBLISHED", "ALREADY_KNOWN"}
    with open(os.environ["PR112_BENCH_TRACE"], "a") as stream:
        stream.write(json.dumps(evidence) + "\n")
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    path = Path(declaration['knowledge_path'])
    if not path.is_absolute():
        path = case.input_path.parent / path
    knowledge = json.loads(path.read_text())
    original = (case.repo / 'src/calc.py').read_bytes()
    create_executing_oracle(tmp_path / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=('src/calc.py',),
        command=(sys.executable, '-B', '-c', 'from src.calc import add; assert add(2,3)==5'))
    good = {'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': 'a + b'}]}
    command = LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1, 'alternatives': [good]})
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(commands=[command]) as (endpoint, requests):
        for name in ('learn', 'automatic'):
            current = replace(case, run_root=tmp_path / (name + '-run'), input_path=tmp_path / (name + '.json'))
            selected = tmp_path / (name + '-knowledge.json')
            selected.write_text(json.dumps({**knowledge, 'schema_version': PROJECT_KNOWLEDGE_INPUT_V4,
                                            'run_memory_selection': 'ALL'}))
            chosen = use_model_agent({**declaration, 'config': dict(declaration['config'])},
                                     tmp_path / (name + '-agent'), endpoint=endpoint, protocol=LOCAL_EDIT_PROFILE_V5)
            current.input_path.write_text(json.dumps({**chosen, 'run_id': name, 'knowledge_path': str(selected)}))
            code, pending = current.start()
            assert code == 3, pending
            code, result = current.approve(pending)
            assert code == 0 and result['outcome_status'] == 'FULL', result
            frozen, _, attempt, completed = completed_attempt(current)
            assert (case.repo / 'src/calc.py').read_bytes() == original
            local = completed.worker_result.diagnostics['local_edit_result']
            if name == 'learn':
                assert local['planning_route'] == 'MODEL'
                assert attempt.result.structured_outcome['payload']['verification']['payload']['c1']['oracle_resolved'] is True
                with open(os.environ['PR112_BENCH_TRACE'], 'a') as stream:
                    stream.write(json.dumps({'kind': 'behavior', 'scenario': 'initial', 'status': result['outcome_status'],
                        'route': local['planning_route'], 'patch_sha256': hashlib.sha256(local['diff_text'].encode()).hexdigest(),
                        'provider_calls': len(requests), 'oracle_resolved': True}) + '\n')
                if os.environ.get('PR112_BENCH_INITIAL_ONLY') == '1':
                    assert len(requests) == 1
                    rows = [json.loads(line) for line in (tmp_path / 'harness/oracle_observations.jsonl').read_bytes().splitlines()]
                    assert len(rows) == 1 and rows[0]['before_returncode'] != 0 and rows[0]['after_returncode'] == 0
                    assert subprocess.run(['git', 'status', '--porcelain'], cwd=case.repo, check=True, capture_output=True, text=True).stdout == ''
                    return
            if name == 'automatic':
                assert local['planning_route'] == 'EXACT_MEMORY'
                assert attempt.result.structured_outcome['payload']['verification']['payload']['c1']['oracle_resolved'] is True
                assert completed.delivery_receipt.transport_name == 'synapse.exact-memory/v1'
                assert not (current.run_root / 'stage10/agent-executions').exists()
                behavior = {'kind': 'behavior', 'scenario': 'reuse', 'status': result['outcome_status'], 'route': local['planning_route'],
                    'patch_sha256': hashlib.sha256(local['diff_text'].encode()).hexdigest(),
                    'provider_calls': len(requests),
                    'oracle_calls': len((tmp_path / 'harness/oracle_observations.jsonl').read_bytes().splitlines()),
                    'oracle_resolved': True, 'repository_restored': (case.repo / 'src/calc.py').read_bytes() == original,
                    'agent_executions': 0, 'recall_ok': recall_code == 0}
                with open(os.environ['PR112_BENCH_TRACE'], 'a') as stream:
                    stream.write(json.dumps(behavior) + '\n')
                assert len(requests) == 1
                observed = tmp_path / 'harness/oracle_observations.jsonl'
                before = observed.read_bytes()
                oracle_rows = [json.loads(line) for line in before.splitlines()]
                assert len(oracle_rows) == 2
                assert all(row["before_returncode"] != 0 and row["after_returncode"] == 0 for row in oracle_rows)
                code, resumed = current.cli('project', 'resume', '--run-dir', current.run_root)
                assert code == 0 and resumed == result and observed.read_bytes() == before and len(requests) == 1
                assert (current.run_root / 'experiment.json').read_bytes() == frozen.canonical_bytes
                code, repeated = current.cli('project', 'resume', '--run-dir', current.run_root)
                assert code == 0 and repeated == result and observed.read_bytes() == before and len(requests) == 1
                assert subprocess.run(['git', 'status', '--porcelain'], cwd=case.repo, check=True, capture_output=True, text=True).stdout == ''
