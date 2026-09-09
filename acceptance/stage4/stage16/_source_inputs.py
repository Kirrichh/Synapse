"""Real committed source -> canonical CLI -> atomic publication -> new process.

The subprocess creates all verification/admission history itself. No test
publisher, forged prior Gold attempt or permissive gate is supplied.
"""

import json
from pathlib import Path
import subprocess
import sys

from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.compatibility import COMPATIBILITY_POLICY_V1
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V1
from synapse.experiments.gold.knowledge_environment import (
    GoldProjectDeclaration, GoldProjectEntitlements, GoldProjectIdentities, connect_gold_project, open_gold_project,
)
from synapse.experiments.gold.provenance import OBSERVED_EXTERNAL_INPUT_V1, ExternalInputKind, ObservedExternalInput
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V1, canonical, source_ref
from synapse.experiments.gold.source_ingestion import SOURCE_INGESTION_V1


def prepare(root, *, candidate_repo=False, execute=False, extra_sources=None):
    repo, state = root / "repo", root / "state"
    if candidate_repo:
        from tests.test_swebench_gold_runner import build_candidate_repo
        revision, _ = build_candidate_repo(repo)
        source_path, module, qualname = "src/calc.py", "src.calc", "add"
    else:
        repo.mkdir()
        for command in (("init", "-q"), ("config", "user.name", "Acceptance"),
                        ("config", "user.email", "acceptance@example.invalid")):
            subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)
        (repo / "calc.py").write_text("def double(value):\n    return value * 2\n", encoding="utf-8")
        for name, content in (extra_sources or {}).items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "source"], cwd=repo, check=True, capture_output=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        source_path, module, qualname = "calc.py", "calc", "double"
    identities = GoldProjectIdentities(ActorIdentity("source-attester"), ActorIdentity("source-builder"),
        AuthorityIdentity("source-classifier"), AuthorityIdentity("source-reviewer"), AuthorityIdentity("source-supersession"),
        AuthorityIdentity("source-revocation"), ActorIdentity("source-lifecycle"), AuthorityIdentity("source-operator"))
    connect_gold_project(GoldProjectDeclaration(repo, state, "source-policy/v1", "source-env", identities,
        GoldProjectEntitlements(tuple(sorted((source_path, *(extra_sources or {})))),
            ("execute", "read") if execute else ("read",), ("source-verifier", "swebench"))))
    claim = {"schema_version": SOURCE_CLAIM_V1, "operation_id": "learn-calculation", "revision": revision,
        "kind": "REPOSITORY_FACT_CHECK", "sources": [source_path], "replay_gas_budget": 10_000,
        "symbols": [{"path": source_path, "module": module, "qualname": qualname,
                     "symbol_kind": "FUNCTION", "contract_version": BINDING_CONTRACT_VERSION_V1}], "recipe": None}
    files = []
    for field, kind, name, version, details in (
        ("policy_inputs", ExternalInputKind.POLICY, "compatibility-policy", COMPATIBILITY_POLICY_V1, {"read_only": True}),
        ("environment_inputs", ExternalInputKind.ENVIRONMENT, "host-abi", "synapse.stage4.host-abi/v1", {}),
        ("environment_inputs", ExternalInputKind.ENVIRONMENT, "runtime-environment", "synapse.stage4.environment/v1", {"python": sys.version}),
        ("tool_inputs", ExternalInputKind.TOOL, "compiler", "synapse.stage4.compiler/v1", {}),
    ):
        raw = canonical({"name": name, "version": version, "details": details})
        ref = source_ref(raw, version, RefKind.CONTRACT_CONDITION if kind is ExternalInputKind.POLICY else RefKind.ARTIFACT)
        path = root / (name + ".json")
        path.write_bytes(raw)
        files.append({"ref": ref.to_dict(), "path": str(path)})
        claim.setdefault(field, []).append(ObservedExternalInput(OBSERVED_EXTERNAL_INPUT_V1, kind, name,
                                              version, ref).to_dict())
    input_path = root / "learn.json"
    input_path.write_text(json.dumps({"schema_version": SOURCE_INGESTION_V1, "claim": claim, "files": files}), encoding="utf-8")
    return repo, state, input_path


def learn(state, input_path):
    result = subprocess.run([sys.executable, "-B", "-m", "synapse", "project", "learn",
        "--state-dir", str(state), "--input", str(input_path)],
        cwd=Path(__file__).resolve().parents[3], text=True, capture_output=True, timeout=180)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout.splitlines()[-1])


def recall(state, root, claim, *, statement="calculate double value", scope=None, limit=64):
    from synapse.experiments.gold.source_experience import SOURCE_RECALL_QUERY_V1
    query_path = root / "recall.json"
    query_path.write_text(json.dumps({"schema_version": SOURCE_RECALL_QUERY_V1, "statement": statement,
        "revision": claim["revision"], "scope": sorted(scope or claim["sources"]), "limit": limit}))
    result = subprocess.run([sys.executable, "-B", "-m", "synapse", "project", "recall",
        "--state-dir", str(state), "--input", str(query_path)],
        cwd=Path(__file__).resolve().parents[3], text=True, capture_output=True, timeout=180)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout.splitlines()[-1])



def consumer_case(root, *, learn_recipe=False, include_fact=False):
    """Create task evidence after CLI publication, with no seeded task history."""
    from dataclasses import asdict, replace
    from acceptance.stage4.stage11._project_inputs import ProjectInputCase
    from acceptance.stage4.stage11._builders import manifest_for
    from synapse.experiments.gold.bindings import binding_from_dict, binding_to_ref
    from synapse.experiments.gold.contracts import RepositoryRevision
    from synapse.experiments.gold.knowledge_environment import _builder_runtime_identity
    from synapse.experiments.gold.provenance import OracleObservation, ORACLE_OBSERVATION_V1
    from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V1
    from synapse.experiments.gold.runner.c1_boundary import command_policy_reference
    from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
    from synapse.experiments.gold.stage10.intent import AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind
    from synapse.experiments.gold.stage10.planning import CAPABILITY_BY_OPERATION, OperationKind
    from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
    from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
    from synapse.experiments.swebench.swebench_harness_oracle import SWEbenchHarnessOracleConfig
    from tests.test_swebench_gold_runner import policy

    repo, state, source_input = prepare(root, candidate_repo=True, execute=learn_recipe)
    source = json.loads(source_input.read_text())
    if learn_recipe:
        source['claim']['kind'] = 'VERIFICATION_RECIPE'
        source['claim']['recipe'] = {
            'command': [sys.executable, '-B', '-c', 'from src.calc import add; print("observed-add", add(2, 3))'],
            'expectation': {'expected_exit_codes': [0], 'expected_nonzero_exit': False,
                'combined_output_contains': ['observed-add -1'], 'combined_output_not_contains': [], 'timeout_seconds': 10}}
        source_input.write_text(json.dumps(source))
    code, publication = learn(state, source_input)
    assert code == 0, publication
    corpus = publication['knowledge']
    if include_fact:
        fact = json.loads(json.dumps(source))
        fact['claim'].update(operation_id='learn-source-facts', kind='REPOSITORY_FACT_CHECK', recipe=None)
        fact_path = root / 'learn-facts.json'
        fact_path.write_text(json.dumps(fact))
        code, second = learn(state, fact_path)
        assert code == 0, second
        corpus = second['knowledge']
    candidate = publication['knowledge']['candidates'][0]
    revision = RepositoryRevision.git_commit(source['claim']['revision'])
    target = binding_from_dict(candidate['bindings'][0], repo_root=repo, consumer_revision=revision)
    manifest = manifest_for(repo, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN, run_id='source-consumer')
    command_policy = replace(policy(), allowed_scope=('src/calc.py',))
    condition = command_policy_reference(command_policy)
    task = GoverningTaskContract(task_id=manifest.config.task_id,
        task_statement=command_policy.statement,
        repository_revision_sha256=revision.git_sha, allowed_scope=create_repository_scope(command_policy.allowed_scope),
        required_capabilities=(CAPABILITY_BY_OPERATION[OperationKind.EDIT_CONTROLLED_CHANGE],),
        target_bindings=(binding_to_ref(target),),
        effects=(EffectConstraint('effect-main', EffectDisposition.EXPECTED, EffectKind.PATH_MODIFIED, 'src/calc.py', condition),),
        acceptance=(AcceptanceCriterion('acceptance-main', AcceptanceKind.CONTRACT_CONDITION, condition),))
    # Observe the consumer's own precondition after the source publication.
    observed = subprocess.run([sys.executable, '-B', '-c', 'from src.calc import add; print(add(2, 3)); assert add(2, 3) == 5'],
        cwd=repo, capture_output=True, text=True, timeout=10)
    assert observed.returncode != 0 and observed.stdout.strip() == '-1'
    raw = canonical({'task_contract_ref': task.reference.to_dict(), 'returncode': observed.returncode,
        'stdout': observed.stdout, 'stderr': observed.stderr, 'meaning': 'consumer-base-reproduction'})
    observation_ref = source_ref(raw, 'source-consumer-precondition/v1')
    for ref, data in ((observation_ref, raw), (task.reference, task.canonical_bytes())):
        path = root / ref.sha256
        path.write_bytes(data)
        corpus['files'].append({'ref': ref.to_dict(), 'path': str(path)})
    knowledge_path = root / 'knowledge.json'
    knowledge_path.write_text(json.dumps(corpus))
    oracle = OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity('source-consumer-oracle'), revision,
        task.reference, observation_ref)
    prompt_path = root / 'worker-prompt.txt'
    worker_path = root / 'worker.py'
    worker_path.write_text('import json, os, sys\nfrom pathlib import Path\n'
        'Path(sys.argv[1]).write_text(sys.argv[sys.argv.index("-t") + 1])\n'
        'Path(sys.argv[1]).with_suffix(".information.json").write_bytes(Path(os.environ["SYNAPSE_MINI_INFORMATION_PATH"]).read_bytes())\n'
        'print(json.dumps({"usage": {"total_tokens": 0}}))\n')
    config = replace(manifest.config, oracle_name='synapse.experiments.swebench.gold_oracle_binding.GoldSWEbenchOracleBinding')
    oracle_config = SWEbenchHarnessOracleConfig(python_executable=Path(sys.executable), swebench_work_dir=root / 'harness',
        dataset_name='acceptance', split='test', instance_timeout_seconds=10, max_workers=1)
    project = open_gold_project(state)
    input_path = root / 'experiment.json'
    input_path.write_text(json.dumps({'schema_version': EXPERIMENT_INPUT_SCHEMA_V1, 'run_id': manifest.run_id.value,
        'config': config.to_dict(), 'versions': manifest.versions.to_dict(), 'task_contract': task.to_dict(),
        'target_records': [target.to_dict()], 'command_policy': asdict(command_policy), 'actor_namespace': 'source-consumer',
        'worker': {'provider': 'mini', 'command': [sys.executable, str(worker_path), str(prompt_path)], 'model': config.model,
                   'timeout_seconds': 30, 'max_steps': 5, 'cost_limit': '0'},
        'oracle': asdict(oracle_config), 'replay_profile': 'pure-cvm/v1', 'knowledge_path': str(knowledge_path),
        'observation': {'builder': _builder_runtime_identity(project.declaration).to_dict(),
            'base_revision': revision.to_dict(), 'task_contract_ref': task.reference.to_dict(),
            **{field: source['claim'][field] for field in ('policy_inputs', 'environment_inputs', 'tool_inputs')},
            'source_refs': [observation_ref.to_dict()], 'verification_refs': [observation_ref.to_dict()],
            'oracle_observation': oracle.to_dict()}}, default=str))
    # Two source publications cross the real replay, context and lineage
    # readers. Their preparation alone exceeded 430 s on the acceptance host;
    # leave time for dispatch and independent verification as well. This is an
    # outer subprocess watchdog, not a larger worker or Gold execution budget.
    return ProjectInputCase(repo, state, root / 'run', input_path, knowledge_path, prompt_path, 900), publication
