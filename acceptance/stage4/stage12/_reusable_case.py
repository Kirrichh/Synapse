"""A real unresolved attempt and independently admitted negative knowledge."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from acceptance.stage4.stage11._builders import run_world, _plan_profile
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt, publish_delivery_started, dispatch_and_publish_worker,
    publish_c1_started, invoke_c1_without_completion_checkpoint, publish_c1_completed,
)
from synapse.experiments.gold import admission as A, library_admission as LA
from synapse.experiments.gold.authority_config import create_gate_evaluator_declaration
from synapse.experiments.gold.behavior import create_behavior_blob, create_behavior_manifest, compile_behavior_unit
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity, AuthorityRole, GateKind, RepositoryRevision
from synapse.experiments.gold.knowledge_environment import (
    GoldProjectDeclaration, GoldProjectIdentities, GoldProjectEntitlements,
    connect_gold_project, open_gold_project, _builder_runtime_identity,
)
from synapse.experiments.gold.lifecycle import LifecycleContext, LifecycleScope, LIFECYCLE_CONTEXT_V1
from synapse.experiments.gold.provenance import (
    configure_platform_attester, OracleObservation, ORACLE_OBSERVATION_V1,
    ExternalInputKind, behavior_attestation_to_ref,
)
from synapse.experiments.gold.runner.c1_boundary import read_c1_verification_evidence
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.runner_composition import create_gold_run_composition
from synapse.experiments.gold.stage12.reusable import create_rejected_patch_guard, rejected_patch_domain, ReusableVerificationAuthority
from tests.test_stage4_gold_compatibility import _external, _append_admitted


def reusable_case(root):
    world = run_world(root, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(False, False)])
    declaration = GoldProjectDeclaration(
        repo_root=world.repo, state_root=root / "project", policy_version="reusable-admission/v1",
        environment_profile_id="reusable-test-env",
        identities=GoldProjectIdentities(
            ActorIdentity("candidate-attester"), ActorIdentity("candidate-builder"),
            AuthorityIdentity("candidate-classifier"), AuthorityIdentity("candidate-reviewer"),
            AuthorityIdentity("candidate-supersession"), AuthorityIdentity("candidate-revocation"),
            ActorIdentity("candidate-lifecycle"), AuthorityIdentity("candidate-human"),
        ), entitlements=GoldProjectEntitlements(("src",), ("read",), ("swebench",)),
    )
    connect_gold_project(declaration)
    project = open_gold_project(declaration.state_root)
    world.attempt_inputs.plan_profile = _plan_profile(world.repo, world.manifest)
    verification_authority = ReusableVerificationAuthority(
        repository_root=world.repo, environment_profile_id=declaration.environment_profile_id,
        authority_handle=project.authority_handle, library=project.library,
        attestation_store=project.attestation_store, lifecycle_store=project.lifecycle_store,
        admission_journal=project.admission_journal, fence=project.fence,
    )
    world.composition = create_gold_run_composition(
        run_root=world.run_root, manifest=world.manifest, c1_boundary=world.boundary,
        run_record_fence=world.run_record_fence, attempt_inputs=world.attempt_inputs,
        stage10_composition=world.stage10_composition,
        verification_profile=world.attempt_inputs.plan_profile, reusable_authority=verification_authority,
    )
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    publish_c1_started(prefix)
    execution = invoke_c1_without_completion_checkpoint(prefix)
    publish_c1_completed(prefix)
    c1 = read_c1_verification_evidence(world.boundary, receipt=execution.authority,
                                      base_revision=world.manifest.config.base_revision, run_root=world.run_root)
    task_ref = world.attempt_inputs.plan_profile.task_contract.reference
    unit = create_rejected_patch_guard(manifest=world.manifest, task_contract_ref=task_ref, c1=c1)
    blob = create_behavior_blob(unit)
    behavior_manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
    domain, domain_ref = rejected_patch_domain(manifest=world.manifest, task_contract_ref=task_ref, c1=c1)
    lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256)
    facts = c1.payload()
    revision = RepositoryRevision.git_commit(facts["verified_revision"])
    report = replace(HashBoundRef.from_dict(facts["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
    oracle = replace(HashBoundRef.from_dict(facts["oracle_result_ref"]), kind=RefKind.SOURCE_EVIDENCE)
    clock = lambda: datetime.now(timezone.utc)
    attester = configure_platform_attester(authority_handle=project.authority_handle,
        builder_runtime_identity=replace(_builder_runtime_identity(declaration), repository_revision=revision), trusted_clock=clock)
    observation = attester.observe(
        authority_handle=project.authority_handle, repository_revision=revision,
        base_revision=RepositoryRevision.git_commit(world.manifest.config.base_revision), task_contract_ref=task_ref,
        policy_inputs=(_external(ExternalInputKind.POLICY, "guard-policy", "guard-policy/v1"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "guard-env", "guard-env/v1"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "guard-compiler", "guard-compiler/v1"),),
        source_refs=(report,), verification_refs=(report,),
        oracle_observation=OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity(world.manifest.config.oracle_name), revision, task_ref, oracle),
    )
    attestation = attester.attest(authority_handle=project.authority_handle, observed=observation,
        subject_content_key=unit.content_key, producer_run_id=world.manifest.run_id,
        producer_attempt_id=prefix.context.attempt_id, producer_actor_ids=(ActorIdentity("candidate-producer"),))
    project.attestation_store.append(authority_handle=project.authority_handle, attestation=attestation)
    attestation_ref = behavior_attestation_to_ref(attestation)
    _append_admitted(project.lifecycle_store, project.authority_handle, attestation_ref, lifecycle_context)
    subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=behavior_manifest.manifest_id)
    evaluator = create_gate_evaluator_declaration(
        authority_handle=project.authority_handle, evaluator_identity=AuthorityIdentity("independent-admitter"),
        evaluator_component_id="reusable-admission", evaluator_component_version="reusable-admission/v1",
        gate_roles={GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
                    GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
                    GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
                    GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR},
        policy_version=world.manifest.versions.policy_version, trusted_clock=clock,
    )
    granted = A.GrantEnvelope((domain_ref.sha256,), (), (), world.manifest.versions.policy_version)
    gates = A.configure_gate_controller(
        declaration=evaluator, policy_version=world.manifest.versions.policy_version, run_id=world.manifest.run_id,
        attempt_id=prefix.context.attempt_id, repository_revision=facts["verified_revision"],
        environment_profile_id=declaration.environment_profile_id, trusted_clock=clock,
        taint_probe=lambda ref: A.TaintFinding(consumable=ref == subject, chain_complete=ref == subject,
                                             quarantined=False, blocks_publication=ref != subject),
        provenance_probe=lambda ref: ref == subject and project.attestation_store.contains(
            authority_handle=project.authority_handle, attestation=attestation),
        lifecycle_probe=lambda ref: ref == subject and project.lifecycle_store.require_consumable(
            subject_ref=attestation_ref, context=lifecycle_context) is not None,
        compatibility_probe=lambda ref, ctx: A.CompatibilityFinding(
            compatible=False, evidence_complete=False, drifted=False, conflicts_unresolved=False,
            subject_ref=ref, consumer_context_ref=ctx),
        boundary_probe=lambda ref: False, grant_probe=lambda: granted,
        head_reader=lambda: {"boundary_ref": None, "heads": {}},
        producer_actor=ActorIdentity("candidate-producer"), retriever_actor=ActorIdentity("candidate-retriever"),
        consumer_actor=ActorIdentity("candidate-consumer"),
    )
    authority = LA.create_production_write_authority_binding(
        gates, library=project.library, publisher_identity=project.library._publisher_identity,
        journal=project.admission_journal, fence=project.fence,
    )
    write = LA.admit_library_write(authority, unit=unit, blob=blob, manifest=behavior_manifest,
                                  requested=A.RequestedEnvelope((domain_ref.sha256,), (), ()))
    return SimpleNamespace(world=world, project=project, authority=verification_authority, prefix=prefix, c1=c1, task_ref=task_ref,
                           unit=unit, behavior_manifest=behavior_manifest, attestation=attestation,
                           write=write, domain=domain, domain_ref=domain_ref)
