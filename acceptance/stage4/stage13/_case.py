"""External acceptance preparation: one retained C1 execution, real fresh stores."""

from types import SimpleNamespace

from acceptance.stage4.stage11._builders import run_world, _plan_profile
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt, publish_delivery_started, dispatch_and_publish_worker,
    publish_c1_started, invoke_c1_without_completion_checkpoint, publish_c1_completed,
)
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.knowledge_environment import (
    GoldProjectDeclaration, GoldProjectIdentities, GoldProjectEntitlements,
    connect_gold_project, open_gold_project, _builder_runtime_identity,
)
from synapse.experiments.gold.runner.c1_boundary import read_c1_verification_evidence
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage12.reusable import ReusableVerificationAuthority
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold.stage13.publication import PublicationAuthority
from synapse.experiments.gold.stage13.publication_store import PublicationStore


def negative_attempt(root):
    world = run_world(root, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(False, False)])
    world.attempt_inputs.plan_profile = _plan_profile(world.repo, world.manifest)
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    publish_c1_started(prefix)
    execution = invoke_c1_without_completion_checkpoint(prefix)
    publish_c1_completed(prefix)
    c1 = read_c1_verification_evidence(world.boundary, receipt=execution.authority,
                                      base_revision=world.manifest.config.base_revision, run_root=world.run_root)
    verification = verify_attempt(manifest=world.manifest, context=prefix.context,
        run_store=world.composition.record_store, boundary=world.boundary,
        record_store=world.stage10_composition.record_store,
        profile=world.attempt_inputs.plan_profile, run_root=world.run_root)
    return SimpleNamespace(world=world, prefix=prefix, c1=c1, verification=verification)


def publication_case(root, attempt):
    declaration = GoldProjectDeclaration(repo_root=attempt.world.repo, state_root=root,
        policy_version="publication-acceptance/v1", environment_profile_id="publication-test-env",
        identities=GoldProjectIdentities(
            ActorIdentity("publication-attester"), ActorIdentity("publication-builder"),
            AuthorityIdentity("publication-classifier"), AuthorityIdentity("publication-taint-reviewer"),
            AuthorityIdentity("publication-supersession"), AuthorityIdentity("publication-revocation"),
            ActorIdentity("publication-lifecycle"), AuthorityIdentity("publication-human")),
        entitlements=GoldProjectEntitlements(("src",), ("read",), ("swebench",)))
    connect_gold_project(declaration)
    project = open_gold_project(root)
    stores = ReusableVerificationAuthority(repository_root=attempt.world.repo,
        environment_profile_id=declaration.environment_profile_id, authority_handle=project.authority_handle,
        library=project.library, attestation_store=project.attestation_store,
        lifecycle_store=project.lifecycle_store, admission_journal=project.admission_journal, fence=project.fence,
        source_run_store=attempt.world.composition.record_store,
        compatibility_history=attempt.world.attempt_inputs.case.factory._stores.compatibility_history)
    authority = PublicationAuthority(stores, project.taint_store, _builder_runtime_identity(declaration), (ActorIdentity("worker"),))
    publisher = PublicationStore(root=root / "publications", authority=authority)
    request = authority.prepare(verification=attempt.verification, manifest=attempt.world.manifest,
                                context=attempt.prefix.context, c1=attempt.c1)
    assert request is not None
    return SimpleNamespace(project=project, publisher=publisher, request=request, attempt=attempt)
