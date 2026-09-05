"""Operator input files for the canonical CLI, with a previously published seed.

The producer fixture creates history before the run. The subprocess receives
only persisted data and uses no test module, factory, probe or replay callback.
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from synapse.experiments.gold import library_admission as LA
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.compatibility import COMPATIBILITY_POLICY_V1
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity, AttemptId, RunId, RepositoryRevision
from synapse.experiments.gold.knowledge_environment import (
    GoldProjectDeclaration, GoldProjectEntitlements, GoldProjectIdentities,
    connect_gold_project, open_gold_project, _builder_runtime_identity,
)
from synapse.experiments.gold.lifecycle import LifecycleContext, LifecycleScope, LIFECYCLE_CONTEXT_V1
from synapse.experiments.gold.provenance import (
    ExternalInputKind, OracleObservation, ORACLE_OBSERVATION_V1,
    configure_platform_attester, behavior_attestation_to_ref,
)
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V1
from synapse.experiments.gold.run_knowledge import KNOWLEDGE_INPUT_SCHEMA_V1
from synapse.experiments.gold.runner.c1_boundary import command_policy_reference
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.taint import TaintClass, classify_source_taint
from synapse.experiments.swebench.swebench_harness_oracle import SWEbenchHarnessOracleConfig
from tests.gold_write_admission import write_gate_controller
from tests.test_stage4_gold_compatibility import _behavior, _append_admitted, _ref, _external
from tests.test_swebench_gold_runner import build_candidate_repo, NEW_SOURCE, policy
from acceptance.stage4.stage11._builders import _replayed_core, _plan_profile, manifest_for
from acceptance.stage4.stage11._worker_process import create_worker_process


@dataclass(frozen=True)
class ProjectInputCase:
    repo: Path
    state_root: Path
    run_root: Path
    input_path: Path
    knowledge_path: Path
    worker: object

    def cli(self, *arguments):
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "synapse", *map(str, arguments)],
            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=180,
        )
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        assert lines, completed.stderr
        return completed.returncode, lines[-1]

    def start(self):
        return self.cli("project", "run", "--state-dir", self.state_root,
                        "--input", self.input_path, "--run-dir", self.run_root)

    def approve(self, pending):
        return self.cli("approve", pending["request_path"], "--store", self.run_root / "approvals",
                        "--resume-run", self.run_root)


def project_input_case(root: Path, *, max_attempts=1, outcomes=("NO_PATCH",)) -> ProjectInputCase:
    repo, state_root, run_root = root / "repo", root / "project", root / "run"
    base, _ = build_candidate_repo(repo)
    declaration = GoldProjectDeclaration(
        repo_root=repo, state_root=state_root,
        policy_version="synapse.stage4.gold.project-policy/v1", environment_profile_id="cli-test-env",
        identities=GoldProjectIdentities(
            ActorIdentity("project-attester"), ActorIdentity("project-builder"),
            AuthorityIdentity("project-taint-classifier"), AuthorityIdentity("project-taint-reviewer"),
            AuthorityIdentity("project-supersession"), AuthorityIdentity("project-revocation"),
            ActorIdentity("project-lifecycle-writer"), AuthorityIdentity("project-human"),
        ),
        entitlements=GoldProjectEntitlements(("src",), ("read",), ("swebench",)),
    )
    connect_gold_project(declaration)
    project = open_gold_project(state_root)
    unit, blob, manifest = _behavior(core_payload=_replayed_core())
    publisher = project.library._publisher_identity
    controller, requested = write_gate_controller()
    write_authority = LA.create_production_write_authority_binding(
        controller, library=project.library, publisher_identity=publisher,
        journal=project.admission_journal, fence=project.fence,
    )
    LA.admit_library_write(write_authority, unit=unit, blob=blob, manifest=manifest, requested=requested)

    run_manifest = manifest_for(repo, max_attempts=max_attempts, fallback_policy=FallbackPolicy.FORBIDDEN,
                                run_id="cli-gold-run")
    profile = _plan_profile(repo, run_manifest)
    command_policy = replace(policy(), allowed_scope=("src/calc.py",))
    condition = command_policy_reference(command_policy)
    task = replace(
        profile.task_contract, allowed_scope=create_repository_scope(command_policy.allowed_scope),
        effects=(replace(profile.task_contract.effects[0], verification_ref=condition),),
        acceptance=(replace(profile.task_contract.acceptance[0], condition_ref=condition),),
    )

    revision = RepositoryRevision.git_commit(base)
    builder = _builder_runtime_identity(declaration)
    attester = configure_platform_attester(authority_handle=project.authority_handle,
                                          builder_runtime_identity=builder,
                                          trusted_clock=lambda: datetime.now(timezone.utc))
    task_ref = task.reference
    policy_input = _external(ExternalInputKind.POLICY, "compatibility-policy", COMPATIBILITY_POLICY_V1)
    environment = (
        _external(ExternalInputKind.ENVIRONMENT, "host-abi", "synapse.stage4.host-abi/v1"),
        _external(ExternalInputKind.ENVIRONMENT, "runtime-environment", "synapse.stage4.environment/v1"),
    )
    tool_input = _external(ExternalInputKind.TOOL, "compiler", "synapse.stage4.compiler/v1")
    source_ref, verification_ref = (_ref(name, RefKind.SOURCE_EVIDENCE) for name in ("source", "verification"))
    oracle = OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity("seed-oracle"), revision,
                               task_ref, _ref("oracle-result", RefKind.SOURCE_EVIDENCE))
    observed = attester.observe(
        authority_handle=project.authority_handle, repository_revision=revision, base_revision=revision,
        task_contract_ref=task_ref, policy_inputs=(policy_input,), environment_inputs=environment,
        tool_inputs=(tool_input,), source_refs=(source_ref,), verification_refs=(verification_ref,),
        oracle_observation=oracle,
    )
    attestation = attester.attest(
        authority_handle=project.authority_handle, observed=observed, subject_content_key=unit.content_key,
        producer_run_id=RunId("seed-run"), producer_attempt_id=AttemptId("seed-attempt"),
        producer_actor_ids=(ActorIdentity("seed-producer"),),
    )
    project.attestation_store.append(authority_handle=project.authority_handle, attestation=attestation)
    subject_ref = behavior_attestation_to_ref(attestation)
    context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, "seed-revision")
    _append_admitted(project.lifecycle_store, project.authority_handle, subject_ref, context)
    taint = classify_source_taint(
        authority_handle=project.authority_handle, subject_ref=subject_ref,
        taint_classes=(TaintClass.WORKER_GENERATED,), producer_actor_ids=(ActorIdentity("seed-producer"),),
        source_actor_ids=(ActorIdentity("seed-source"),), admission_actor_ids=(ActorIdentity("seed-admitter"),),
        consumer_actor_ids=(ActorIdentity("seed-consumer"),),
    )
    project.taint_store.append_profile(authority_handle=project.authority_handle, profile=taint)
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    files = []
    for ref in (task_ref, policy_input.ref, *(item.ref for item in environment), tool_input.ref,
                source_ref, verification_ref, oracle.result_ref):
        path = evidence_root / ref.sha256
        path.write_bytes(task.canonical_bytes() if ref == task_ref else ref.ref_id.removeprefix("test.").encode())
        files.append({"ref": ref.to_dict(), "path": str(path)})
    knowledge_path = root / "knowledge.json"
    knowledge_path.write_text(json.dumps({
        "schema_version": KNOWLEDGE_INPUT_SCHEMA_V1, "files": files, "conflicts": [],
        "candidates": [{
            "unit": unit.to_dict(), "manifest_id": manifest.manifest_id.to_dict(),
            "attestation": attestation.to_dict(), "bindings": [], "lifecycle_context": context.to_dict(),
            "taint": {"profiles": [taint.to_dict()], "derivations": [], "decisions": [], "root_id": taint.profile_id.value},
        }],
    }))
    worker = create_worker_process(root / "worker", outcomes=outcomes, patch_source=NEW_SOURCE)
    config = replace(run_manifest.config,
        oracle_name="synapse.experiments.swebench.gold_oracle_binding.GoldSWEbenchOracleBinding")
    oracle_config = SWEbenchHarnessOracleConfig(
        python_executable=Path(sys.executable), swebench_work_dir=root / "harness",
        dataset_name="acceptance", split="test", instance_timeout_seconds=10, max_workers=1,
    )
    worker_config = worker.config(model=config.model)
    input_path = root / "input.json"
    input_path.write_text(json.dumps({
        "schema_version": EXPERIMENT_INPUT_SCHEMA_V1, "run_id": run_manifest.run_id.value,
        "config": config.to_dict(), "versions": run_manifest.versions.to_dict(),
        "task_contract": task.to_dict(),
        "target_records": [item.to_dict() for item in profile.target_records],
        "command_policy": asdict(command_policy), "actor_namespace": "cli-gold",
        "worker": {"provider": "mini", "command": list(worker_config.command), "model": config.model,
                   "timeout_seconds": 30, "max_steps": 5, "cost_limit": "0"},
        "oracle": asdict(oracle_config), "replay_profile": "pure-cvm/v1", "knowledge_path": str(knowledge_path),
        "observation": {
            "builder": builder.to_dict(), "base_revision": revision.to_dict(), "task_contract_ref": task_ref.to_dict(),
            "policy_inputs": [policy_input.to_dict()], "environment_inputs": [item.to_dict() for item in environment],
            "tool_inputs": [tool_input.to_dict()], "source_refs": [source_ref.to_dict()],
            "verification_refs": [verification_ref.to_dict()], "oracle_observation": oracle.to_dict(),
        },
    }, default=str))
    return ProjectInputCase(repo, state_root, run_root, input_path, knowledge_path, worker)
