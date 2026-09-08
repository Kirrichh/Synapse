"""Pinned Astropy task preparation and calibration of the existing SWE-bench oracle.

This module prepares external experiment data and invokes canonical source
ingestion. It never supplies admission decisions, model responses or task
outcomes. Calibration patches belong to the evaluator, outside worker inputs.
"""

from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig, SWEbenchHarnessOracleRunner,
)
from synapse.worker.provider_transport import GEMINI_CHAT_ENDPOINT

from .protocol import canonical, source, read_source


INSTANCE = "astropy__astropy-12907"
BASE = "d16bfe05a744909de4b27f5875fe0d4ed41ce607"
DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
DATASET_SHA256 = "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
DATASET_URL = ("https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/"
               + DATASET_REVISION + "/data/test-00000-of-00001.parquet")
SCOPE = "astropy/modeling/separable.py"
SWEBENCH_VERSION = "4.1.0"


def prepare_astropy(root: Path) -> dict:
    """Fetch hash-pinned source data; leave all live allocations unexecuted."""
    import pyarrow.parquet as parquet

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    parquet_path = root / "verified.parquet"
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:
        raw = response.read(7_000_000)
    if hashlib.sha256(raw).hexdigest() != DATASET_SHA256:
        raise ValueError("SWE-bench bytes differ from the frozen dataset")
    parquet_path.write_bytes(raw)
    rows = parquet.read_table(parquet_path, filters=[("instance_id", "=", INSTANCE)]).to_pylist()
    if len(rows) != 1 or rows[0]["base_commit"] != BASE or rows[0]["repo"] != "astropy/astropy":
        raise ValueError("the selected task does not match the frozen source revision")
    row = rows[0]
    if not row["FAIL_TO_PASS"] or not row["PASS_TO_PASS"]:
        raise ValueError("the task lacks one of its independent oracle test groups")
    evaluator = root / "evaluator"
    evaluator.mkdir()
    dataset = evaluator / "instance.json"
    dataset.write_bytes(canonical([row]))
    public_task = root / "task.json"
    public_task.write_bytes(canonical({"task_id": INSTANCE, "instance_id": INSTANCE,
        "statement": row["problem_statement"], "allowed_scope": [SCOPE]}))
    repo = root / "base-repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "--depth=1",
                    "https://github.com/astropy/astropy.git", BASE], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "-q", "FETCH_HEAD"], check=True)
    if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != BASE:
        raise ValueError("Astropy checkout differs from the frozen base")
    value = {"schema_version": "synapse.acceptance.stage16.astropy-preparation/v1",
        "status": "PREPARED", "instance_id": INSTANCE, "base_revision": BASE,
        "dataset_revision": DATASET_REVISION, "dataset_ref": source(parquet_path),
        "evaluator_input_ref": source(dataset), "task_ref": source(public_task), "repo_root": str(repo),
        "fail_to_pass": row["FAIL_TO_PASS"], "pass_to_pass": row["PASS_TO_PASS"],
        "swebench_version": SWEBENCH_VERSION,
        "proposed_runs": {"pairs": 1, "max_attempts": 3, "model": "gemini-3.1-flash-lite",
            "endpoint": GEMINI_CHAT_ENDPOINT, "initial_order_seed": 17},
        "execution_status": "NOT_STARTED",
        "gold_precondition": "canonical source ingestion and verified worker commands before preregistration"}
    (root / "preparation.json").write_bytes(canonical(value))
    return value


def calibrate_astropy(root: Path) -> dict:
    """Require a real negative and positive result from the product oracle adapter."""
    from swebench.harness.test_spec.test_spec import make_test_spec

    root = root.resolve()
    prepared = json.loads((root / "preparation.json").read_bytes())
    if (prepared["schema_version"] != "synapse.acceptance.stage16.astropy-preparation/v1"
            or prepared["base_revision"] != BASE or prepared["instance_id"] != INSTANCE):
        raise ValueError("unknown Astropy preparation")
    if version("swebench") != SWEBENCH_VERSION or prepared["swebench_version"] != SWEBENCH_VERSION:
        raise ValueError("the existing oracle command requires the pinned SWE-bench profile")
    row, = read_source(prepared["evaluator_input_ref"])
    task = BaselineTask(**read_source(prepared["task_ref"]))
    repo = Path(prepared["repo_root"])
    if (subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != BASE
            or subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])):
        raise ValueError("calibration base is no longer clean and frozen")
    # Resolve the upstream image once, then use an immutable local tag for both
    # calibrations and the eventual experiment instead of pulling 'latest' again.
    image_name = make_test_spec(row, namespace="swebench").instance_image_key
    subprocess.run(["docker", "pull", image_name], check=True)
    image, = json.loads(subprocess.check_output(["docker", "image", "inspect", image_name]))
    tag = "synapse-" + image["Id"].removeprefix("sha256:")
    frozen_image = image_name.rsplit(":", 1)[0] + ":" + tag
    subprocess.run(["docker", "tag", image["Id"], frozen_image], check=True)
    calibration = root / "calibration"
    calibration.mkdir(exist_ok=False)
    results = []
    for label, expected in (("unchanged-behavior", False), ("reference-fix", True)):
        case = calibration / label
        case.mkdir()
        checkout = case / "repo"
        subprocess.run(["git", "clone", "--no-local", "-q", str(repo), str(checkout)], check=True)
        if expected:
            subprocess.run(["git", "-C", str(checkout), "apply", "-"], input=row["patch"].encode(), check=True)
        else:
            # The legacy oracle refuses an empty candidate without executing
            # tests. A comment-only patch exercises its real negative verdict.
            with (checkout / SCOPE).open("a") as stream:
                stream.write("\n# Synapse oracle calibration: original behavior remains unchanged.\n")
        patch_path = case / "candidate.patch"
        patch_path.write_bytes(subprocess.check_output(["git", "-C", str(checkout), "diff", "HEAD"]))
        config = SWEbenchHarnessOracleConfig(python_executable=Path(sys.executable),
            swebench_work_dir=case / "harness", dataset_name=prepared["evaluator_input_ref"]["path"],
            split="test", instance_timeout_seconds=600, process_timeout_seconds=900,
            max_workers=1, instance_image_tag=tag, model_name_or_path="synapse-astropy-calibration")
        observed = SWEbenchHarnessOracleRunner(config).verify(checkout, task)
        result_path = case / "result.json"
        result_path.write_bytes(canonical({"expected_resolved": expected, "actual": observed.to_dict(),
            "candidate_ref": source(patch_path)}))
        valid = (observed.resolved is expected and not observed.diagnostics.get("infra_error", True)
                 and bool(observed.diagnostics.get("oracle_managed_artifacts")))
        results.append({"case": label, "valid": valid, "result_ref": source(result_path),
                        "oracle_configuration": json.loads(json.dumps(asdict(config), default=str))})
    success = all(item["valid"] for item in results)
    result = {"schema_version": "synapse.acceptance.stage16.astropy-calibration/v1",
        "status": "CALIBRATED" if success else "CALIBRATION_FAILED",
        "preparation_ref": source(root / "preparation.json"), "image_id": image["Id"],
        "image_repo_digests": image.get("RepoDigests", []), "frozen_image": frozen_image,
        "swebench_version": version("swebench"), "cases": results, "live_model_calls": 0,
        "gold_execution_status": "NOT_STARTED"}
    (calibration / "result.json").write_bytes(canonical(result))
    return result


def prepare_astropy_pair(root: Path, *, repository: Path) -> dict:
    """Prepare one live pair through real source ingestion and the existing harness.

    Both workers edit a clean clone. Their documented test command mounts only
    the permitted file into the calibrated image, preserving its Python 3.9
    dependencies. The reference correction and evaluator tests stay outside
    this command and outside the knowledge corpus.
    """
    import time
    from synapse.change.contract import CommandExpectation
    from synapse.change.verification import run_expected_command
    from synapse.experiments.gold.bindings import (BINDING_CONTRACT_VERSION_V1, binding_from_dict, binding_to_ref)
    from synapse.experiments.gold.canonicalization import RefKind
    from synapse.experiments.gold.compatibility import COMPATIBILITY_POLICY_V1
    from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity, RepositoryRevision
    from synapse.experiments.gold.knowledge_environment import (GoldProjectDeclaration, GoldProjectIdentities,
        GoldProjectEntitlements, connect_gold_project, open_gold_project, _builder_runtime_identity)
    from synapse.experiments.gold.provenance import (ObservedExternalInput, ExternalInputKind, OBSERVED_EXTERNAL_INPUT_V1,
        OracleObservation, ORACLE_OBSERVATION_V1)
    from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V1, canonical as source_canonical, source_ref
    from synapse.experiments.gold.source_ingestion import SOURCE_INGESTION_V1
    from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V2, runtime_source_digest
    from synapse.experiments.gold.runner.c1_boundary import command_policy_reference
    from synapse.experiments.gold.runner.models import GoldRunConfig, GoldRunBudgets, GoldRunVersions, GoldReplicatePolicy
    from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
    from synapse.experiments.gold.stage10.intent import (AcceptanceCriterion, AcceptanceKind, EffectConstraint,
        EffectDisposition, EffectKind)
    from synapse.experiments.gold.stage10.planning import OperationKind, CAPABILITY_BY_OPERATION
    from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
    from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
    from synapse.experiments.swebench.gold_runner import GoldRunnerCommandPolicy, GoldRunnerCommandExpectation
    from synapse.experiments.swebench.mini_config import MiniInvocationConfig
    from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE
    from .protocol import preregister

    root, repository = root.resolve(), repository.resolve()
    prepared = json.loads((root / 'preparation.json').read_bytes())
    calibration = json.loads((root / 'calibration/result.json').read_bytes())
    if calibration['status'] != 'CALIBRATED' or calibration['preparation_ref'] != source(root / 'preparation.json'):
        raise ValueError('the live pair requires the retained successful calibration')
    image, = json.loads(subprocess.check_output(['docker', 'image', 'inspect', calibration['frozen_image']]))
    if image['Id'] != calibration['image_id']:
        raise ValueError('the calibrated Docker image was replaced')
    pair_root = root / 'pair'
    pair_root.mkdir(exist_ok=False)
    started = time.monotonic_ns()
    repos = {}
    for arm in ('baseline', 'gold'):
        directory = pair_root / arm
        directory.mkdir()
        repos[arm] = directory / 'repo'
        subprocess.run(['git', 'clone', '--no-local', '-q', prepared['repo_root'], str(repos[arm])], check=True)
        for key, value in (('user.name', 'Synapse experiment'), ('user.email', 'experiment@example.invalid')):
            subprocess.run(['git', '-C', str(repos[arm]), 'config', key, value], check=True)
    regression = (
        'import numpy as np; from astropy.modeling import models as m; '
        'from astropy.modeling.separable import separability_matrix; '
        'actual=separability_matrix(m.Pix2Sky_TAN() & (m.Linear1D(10) & m.Linear1D(5))); '
        'expected=np.array([[1,1,0,0],[1,1,0,0],[0,0,1,0],[0,0,0,1]],dtype=bool); '
        'ok=bool(np.array_equal(actual, expected)); print("nested_separability_ok="+str(ok)); assert ok'
    )
    def command(arguments):
        # A declared verification command, executed by the existing C1 verifier.
        # Only the allowed file is overlaid; no evaluator test patch is mounted.
        script = ('from pathlib import Path; import subprocess,sys; '
            f'path=Path.cwd()/{SCOPE!r}; '
            'assert path.is_file() and not path.is_symlink(); '
            'args=["docker","run","--rm","--network","none","--workdir","/testbed",'
            '"--mount","type=bind,src="+str(path)+",dst=/testbed/'+SCOPE+',readonly",'
            '"--entrypoint","/opt/miniconda3/envs/testbed/bin/python",'
            + repr(image['Id']) + ']+ ' + repr(arguments) + '; '
            'sys.exit(subprocess.run(args).returncode)')
        return (sys.executable, '-B', '-c', script)
    reproduction = command(['-B', '-c', regression])
    preservation = command(['-B', '-m', 'pytest', '-p', 'no:cacheprovider', 'astropy/modeling/tests/test_separable.py', '-q'])
    observation_paths = []
    for arm, repo in repos.items():
        observed = run_expected_command(reproduction, repo,
            CommandExpectation(expected_exit_codes=(1,), combined_output_contains=('nested_separability_ok=False',), timeout_seconds=120),
            'worker-environment-reproduction')
        preserved = run_expected_command(preservation, repo,
            CommandExpectation(expected_exit_codes=(0,), combined_output_contains=('passed',), timeout_seconds=180),
            'worker-environment-preservation')
        path = pair_root / arm / 'environment-check.json'
        path.write_bytes(canonical({'reproduction': observed.to_json(), 'preservation': preserved.to_json(), 'image_id': image['Id']}))
        observation_paths.append(source(path))
        if observed.status != 'PASS' or preserved.status != 'PASS':
            raise ValueError('the actual worker test environment failed calibration; no live pair is ready')
    public = read_source(prepared['task_ref'])
    statement = public['statement'] + '\n\nEnvironment: edit only ' + SCOPE + '. '
    statement += 'The host Python is not the Astropy test environment. The calibrated test environment is Docker. '
    statement += 'From the repository root, reproduce the issue with this exact argument vector:\n' + json.dumps(reproduction)
    statement += '\nIts expected result before the fix is nested_separability_ok=False and exit 1; after the fix, True and exit 0.'
    statement += '\nExisting preservation tests use this argument vector:\n' + json.dumps(preservation)
    policy = GoldRunnerCommandPolicy(task_id=INSTANCE, instance_id=INSTANCE, statement=statement, allowed_scope=(SCOPE,),
        reproduction_command=reproduction, reproduction_committed_inputs=(SCOPE, 'astropy/modeling/tests/test_separable.py'),
        reproduction_before=GoldRunnerCommandExpectation(expected_exit_codes=(1,), combined_output_contains=('nested_separability_ok=False',), timeout_seconds=120),
        reproduction_after=GoldRunnerCommandExpectation(expected_exit_codes=(0,), combined_output_contains=('nested_separability_ok=True',), timeout_seconds=120),
        baseline_commands=(preservation,), acceptance_commands=(reproduction,), full_suite_commands=(preservation,),
        commit_message='Fix nested model separability', required_scaffold_paths=(SCOPE, 'astropy/modeling/tests/test_separable.py'))
    policy_bytes = canonical(asdict(policy))
    policy_path = pair_root / 'command-policy.json'
    policy_path.write_bytes(policy_bytes)
    module = SCOPE.removesuffix('.py').replace('/', '.')
    state = pair_root / 'gold/project'
    identities = GoldProjectIdentities(ActorIdentity('pilot-attester'), ActorIdentity('pilot-builder'),
        AuthorityIdentity('pilot-classifier'), AuthorityIdentity('pilot-reviewer'), AuthorityIdentity('pilot-supersession'),
        AuthorityIdentity('pilot-revocation'), ActorIdentity('pilot-lifecycle'), AuthorityIdentity('pilot-operator'))
    connect_gold_project(GoldProjectDeclaration(repos['gold'], state, 'astropy-pilot/v1', 'astropy-pinned-image', identities,
        GoldProjectEntitlements((SCOPE,), ('execute', 'read'), ('swebench',))))
    external, files = {}, []
    for field, kind, name, ver, payload in (
        ('policy_inputs', ExternalInputKind.POLICY, 'compatibility-policy', COMPATIBILITY_POLICY_V1,
            {'policy': COMPATIBILITY_POLICY_V1, 'command_policy_sha256': hashlib.sha256(policy_bytes).hexdigest()}),
        ('environment_inputs', ExternalInputKind.ENVIRONMENT, 'host-abi', 'synapse.stage4.host-abi/v1', {'host_abi': 'synapse.stage4.host-abi/v1'}),
        ('environment_inputs', ExternalInputKind.ENVIRONMENT, 'runtime-environment', 'synapse.stage4.environment/v1',
            {'python': sys.version, 'test_image_id': image['Id']}),
        ('tool_inputs', ExternalInputKind.TOOL, 'compiler', 'synapse.stage4.compiler/v1', {'runtime_source_sha256': runtime_source_digest()}),
    ):
        raw = source_canonical(payload)
        ref = source_ref(raw, ver, RefKind.CONTRACT_CONDITION if kind is ExternalInputKind.POLICY else RefKind.ARTIFACT)
        path = pair_root / (name+'.json')
        path.write_bytes(raw)
        files.append({'ref': ref.to_dict(), 'path': str(path)})
        external.setdefault(field, []).append(ObservedExternalInput(OBSERVED_EXTERNAL_INPUT_V1, kind, name, ver, ref).to_dict())
    learning = []
    for name, kind, symbol, recipe in (
        ('repository-structure', 'REPOSITORY_FACT_CHECK', '_cstack', None),
        ('reproduction-procedure', 'VERIFICATION_RECIPE', 'separability_matrix', {'command': list(reproduction),
            'expectation': {'expected_exit_codes': [1], 'expected_nonzero_exit': True,
                'combined_output_contains': ['nested_separability_ok=False'], 'combined_output_not_contains': [], 'timeout_seconds': 120}}),
    ):
        claim = {'schema_version': SOURCE_CLAIM_V1, 'operation_id': name, 'revision': BASE,
            'kind': kind, 'sources': [SCOPE], 'symbols': [{'path': SCOPE, 'module': module, 'qualname': symbol,
                'symbol_kind': 'FUNCTION', 'contract_version': BINDING_CONTRACT_VERSION_V1}],
            'recipe': recipe, 'replay_gas_budget': 10_000, **external}
        path = pair_root / ('learn-'+name+'.json')
        path.write_bytes(canonical({'schema_version': SOURCE_INGESTION_V1, 'claim': claim, 'files': files}))
        call_start = time.monotonic_ns()
        completed = subprocess.run([sys.executable, '-B', '-m', 'synapse', 'project', 'learn', '--state-dir', str(state), '--input', str(path)],
            cwd=repository, text=True, capture_output=True, timeout=240)
        receipt = pair_root / ('learn-'+name+'-result.json')
        result = json.loads(completed.stdout.splitlines()[-1]) if completed.stdout.strip() else {'status': 'NO_RESULT'}
        receipt.write_bytes(canonical({'result': result, 'returncode': completed.returncode, 'stderr': completed.stderr,
                                      'elapsed_ns': time.monotonic_ns()-call_start}))
        learning.append(source(receipt))
        if completed.returncode != 0 or result['status'] not in ('PUBLISHED', 'ALREADY_KNOWN'):
            raise ValueError('canonical source ingestion failed; its original receipt is retained')
        corpus = result['knowledge']
    targets = []
    revision = RepositoryRevision.git_commit(BASE)
    for candidate in corpus['candidates']:
        for raw_binding in candidate['bindings']:
            if raw_binding['qualname'] == '_cstack':
                targets.append(binding_from_dict(raw_binding, repo_root=repos['gold'], consumer_revision=revision))
    target, = targets
    condition = command_policy_reference(policy)
    task = GoverningTaskContract(task_id=INSTANCE, task_statement=statement, repository_revision_sha256=BASE,
        allowed_scope=create_repository_scope((SCOPE,)), required_capabilities=(CAPABILITY_BY_OPERATION[OperationKind.EDIT_CONTROLLED_CHANGE],),
        target_bindings=(binding_to_ref(target),),
        effects=(EffectConstraint('nested-separability', EffectDisposition.EXPECTED, EffectKind.PATH_MODIFIED, SCOPE, condition),),
        acceptance=(AcceptanceCriterion('verified-c1', AcceptanceKind.CONTRACT_CONDITION, condition),))
    actual = run_expected_command(reproduction, repos['gold'], CommandExpectation(expected_exit_codes=(1,),
        combined_output_contains=('nested_separability_ok=False',), timeout_seconds=120), 'consumer-base-observation')
    if actual.status != 'PASS':
        raise ValueError('consumer precondition changed after learning')
    raw = source_canonical({'task_contract_ref': task.reference.to_dict(), 'observation': actual.to_json(), 'image_id': image['Id']})
    result_ref = source_ref(raw, 'synapse.acceptance.stage16.astropy-precondition/v1')
    for ref, content in ((result_ref, raw), (task.reference, task.canonical_bytes())):
        path = pair_root / ref.sha256
        path.write_bytes(content)
        corpus['files'].append({'ref': ref.to_dict(), 'path': str(path)})
    knowledge_path = pair_root / 'gold/knowledge.json'
    knowledge_path.write_bytes(canonical(corpus))
    oracle_observation = OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity('astropy-precondition-verifier'), revision, task.reference, result_ref)
    model = prepared['proposed_runs']['model']
    mini_path = str(Path(sys.executable).parent / 'mini')
    mini = MiniInvocationConfig(executable=mini_path, agent_class=None, model=model,
        cost_limit=0.5, step_limit=12, timeout_seconds=600)
    specification = source(repository / 'docs/GOLD_KNOWLEDGE_INGESTION.md')
    config = GoldRunConfig(task_id=INSTANCE, instance_id=INSTANCE, base_revision=BASE, provider='mini', model=model,
        oracle_name='synapse.experiments.swebench.gold_oracle_binding.GoldSWEbenchOracleBinding', environment_kind='SWE_BENCH',
        budgets=GoldRunBudgets(maximum_wall_clock_seconds=1800, maximum_worker_tokens=100_000, replay_gas_budget=10_000, replay_cognitive_budget=8),
        max_attempts=3, replicate_policy=GoldReplicatePolicy(group_id='astropy-live-pilot', replicate_count=1, replicate_index=1),
        fallback_policy=FallbackPolicy.FORBIDDEN)
    code_revision = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
    versions = GoldRunVersions(specification_version='source-ingestion-pilot/v1', specification_sha256=specification['sha256'],
        implementation_revision=code_revision, policy_version='astropy-pilot.v1', policy_sha256=hashlib.sha256(policy_bytes).hexdigest())
    oracle_config = {**calibration['cases'][0]['oracle_configuration'], 'swebench_work_dir': str(pair_root / 'gold/harness')}
    project = open_gold_project(state)
    input_path = pair_root / 'gold/input.json'
    input_path.write_bytes(canonical({'schema_version': EXPERIMENT_INPUT_SCHEMA_V2, 'run_id': 'astropy-live-gold',
        'config': config.to_dict(), 'versions': versions.to_dict(), 'task_contract': task.to_dict(), 'target_records': [target.to_dict()],
        'command_policy': asdict(policy), 'actor_namespace': 'astropy-live', 'worker': {
            'provider': 'mini', 'command': list(mini.command_prefix()), 'model': model, 'timeout_seconds': mini.timeout_seconds,
            'max_steps': mini.step_limit, 'cost_limit': '0.5', 'accounting': {'profile': MINI_ACCOUNTING_PROFILE,
                'endpoint': GEMINI_CHAT_ENDPOINT, 'credential_env': 'GEMINI_API_KEY'}},
        'oracle': oracle_config, 'replay_profile': 'pure-cvm/v1', 'knowledge_path': str(knowledge_path),
        'observation': {'builder': _builder_runtime_identity(project.declaration).to_dict(), 'base_revision': revision.to_dict(),
            'task_contract_ref': task.reference.to_dict(), **external, 'source_refs': [result_ref.to_dict()],
            'verification_refs': [result_ref.to_dict()], 'oracle_observation': oracle_observation.to_dict()}}))
    definitions = {'GOLD': {'arm': 'GOLD', 'repo_root': str(repos['gold']), 'state_root': str(state),
        'run_root': str(pair_root / 'gold/run'), 'declaration_ref': source(input_path), 'cli_timeout_seconds': 2400},
        'BASELINE': {'arm': 'BASELINE', 'repo_root': str(repos['baseline']), 'run_root': str(pair_root / 'baseline/run'),
            'base_revision': BASE, 'max_attempts': 3, 'task': {**public, 'statement': statement}, 'mini': asdict(mini),
            'oracle': {**oracle_config, 'swebench_work_dir': str(pair_root / 'baseline/harness')},
            'provider_connection': {'credential_env': 'GEMINI_API_KEY', 'endpoint': GEMINI_CHAT_ENDPOINT, 'timeout_seconds': 60}}}
    inputs = {}
    for arm, definition in definitions.items():
        path = pair_root / (arm.lower()+'-definition.json')
        path.write_bytes(canonical(definition))
        inputs[arm] = source(path)
    protocol = preregister(experiment_id='astropy-live-pilot', pairs=[{'pair_id': 'astropy-r0', 'task_id': INSTANCE,
        'replicate_id': 0, 'inputs': inputs}], repository=repository, specification={'version': 'source-ingestion-pilot/v1',
        'sha256': specification['sha256']}, seed=17)
    protocol_path = pair_root / 'protocol.json'
    protocol_path.write_bytes(protocol.raw)
    result = {'status': 'PAIR_PREPARED', 'protocol_ref': source(protocol_path), 'learning_receipts': learning,
        'worker_environment_checks': observation_paths, 'preparation_elapsed_ns': time.monotonic_ns()-started,
        'live_model_calls_during_preparation': 0, 'limitations': ['EXECUTION_POLICIES_DIFFER', 'SOURCE_DELIVERY_IS_NOT_PROOF_OF_USEFUL_REUSE']}
    (pair_root / 'preparation-result.json').write_bytes(canonical(result))
    return result
