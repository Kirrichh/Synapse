from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.version import LANGUAGE_VERSION
from synapse.experiments.gold.behavior import (
    BehaviorBlob,
    BehaviorCore,
    BehaviorManifest,
    SynapseBehaviorUnit,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import (
    COMPILER_ADAPTER_PROFILE_V1,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
)
from synapse.experiments.gold.compatibility import (
    COMPATIBILITY_POLICY_V1,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityDimension,
    CompatibilityFailureCode,
    CompatibilitySubjectDescriptor,
    CompatibilitySubjectEvidence,
    CompatibilityViolation,
    ConflictDecisionKind,
    ConflictKind,
    DimensionResult,
    EvidenceCompleteness,
    RevalidationOutcome,
    RevalidationStage,
    create_compatibility_context,
    create_compatibility_evaluator_declaration,
    create_compatibility_subject_descriptor,
    create_conflict_evidence_proposal,
    configure_compatibility_evaluator,
    evaluate_compatibility,
    evaluate_conflicts,
    revalidate_before_consumption,
    revalidate_before_loading,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_subject_descriptor,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    LifecycleReasonCode,
    ReasonCode,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    BehaviorLibrary,
    PublisherIdentity,
)
from synapse.experiments.gold.lifecycle import (
    LIFECYCLE_CONTEXT_V1,
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleScope,
    LifecycleState,
    configure_lifecycle_authority_evaluator,
    create_lifecycle_authority_proposal,
    create_revocation_decision,
    open_lifecycle_store,
)
from synapse.experiments.gold.provenance import (
    BUILDER_RUNTIME_IDENTITY_V1,
    OBSERVED_EXTERNAL_INPUT_V1,
    ORACLE_OBSERVATION_V1,
    BehaviorAttestation,
    BuilderRuntimeIdentity,
    ExternalInputKind,
    ObservedExternalInput,
    OracleObservation,
    behavior_attestation_to_ref,
    configure_platform_attester,
    open_behavior_attestation_store,
)
from synapse.experiments.gold.taint import (
    SourceTaintProfile,
    TaintClass,
    classify_source_taint,
    open_taint_history_store,
)


NOW = datetime(2026, 3, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
REVISION = RepositoryRevision.git_commit("1" * 40)
BASE_REVISION = RepositoryRevision.git_commit("2" * 40)
_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _ref(name: str, kind: RefKind) -> HashBoundRef:
    raw = name.encode("utf-8")
    return HashBoundRef(
        kind=kind,
        ref_id=f"test.{name}",
        schema_id=f"synapse.stage4.test.{name}/v1",
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type="application/octet-stream",
    )


def _external(kind: ExternalInputKind, name: str, version: str) -> ObservedExternalInput:
    ref_kind = RefKind.CONTRACT_CONDITION if kind is ExternalInputKind.POLICY else RefKind.ARTIFACT
    return ObservedExternalInput(
        OBSERVED_EXTERNAL_INPUT_V1,
        kind,
        name,
        version,
        _ref(name, ref_kind),
    )


def _behavior(output_name: str = "result") -> tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest]:
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(vectors["vectors"][0]["core"])
    payload["output_contract"]["fields"][0]["name"] = output_name.replace("-", "_")
    payload["binding_refs"] = []
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    blob = create_behavior_blob(unit)
    return unit, blob, create_behavior_manifest(unit, blob, compiler_binding=None)


def _append_admitted(store, handle, subject_ref: HashBoundRef, context: LifecycleContext):
    transitions = (
        (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
        (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
        (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
        (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
        (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
        (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    )
    predecessor = None
    head = None
    for sequence, (state, reason) in enumerate(transitions, start=1):
        head = store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=context,
            to_state=state,
            reason_code=reason,
            evidence_refs=(_ref(f"lifecycle-{sequence}", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=predecessor,
            expected_subject_sequence=sequence,
        )
        predecessor = head.record_id.value
    assert head is not None
    return head


@dataclass
class _Harness:
    root: Path
    handle: object
    library: BehaviorLibrary
    publisher: PublisherIdentity
    lifecycle_store: object
    attestation_store: object
    taint_store: object
    observation: object
    attestation: BehaviorAttestation
    taint_profile: SourceTaintProfile
    lifecycle_context: LifecycleContext
    unit: SynapseBehaviorUnit
    manifest: BehaviorManifest
    entry: object
    declaration: object
    evaluator: object
    context: object
    descriptor: CompatibilitySubjectDescriptor
    decision: CompatibilityDecision
    catalog: dict[str, CompatibilitySubjectEvidence]

    def publish_extra(self, name: str = "extra") -> None:
        unit, blob, manifest = _behavior(name)
        self.library.put_behavior(unit, blob, manifest, publisher_identity=self.publisher)


def _make_harness(
    tmp_path: Path,
    *,
    revoked: bool = False,
    extra_unresolved: int = 0,
) -> _Harness:
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity("platform-attester"),
        builder_actor=ActorIdentity("platform-builder"),
        taint_classifier_authority=AuthorityIdentity("taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity("taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity("supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity("revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity("platform-writer"),
        governing_human_authority=AuthorityIdentity("governing-human"),
    )
    handle = create_stage4_authority_handle(configuration)
    publisher = PublisherIdentity(
        LIBRARY_PUBLISHER_IDENTITY_V1,
        "stage4-library-publisher",
        "synapse.stage4.gold.publisher-policy/v1",
    )
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    library = BehaviorLibrary(library_root, publisher_identity=publisher)
    unit, blob, manifest = _behavior()
    library.put_behavior(unit, blob, manifest, publisher_identity=publisher)
    entry = next(item for item in library.search_index() if item.content_key == unit.content_key.value)
    for index in range(extra_unresolved):
        extra_unit, extra_blob, extra_manifest = _behavior(f"unresolved-{index}")
        library.put_behavior(extra_unit, extra_blob, extra_manifest, publisher_identity=publisher)

    builder = BuilderRuntimeIdentity(
        BUILDER_RUNTIME_IDENTITY_V1,
        configuration.builder_actor,
        REVISION,
        _ref("builder-binary", RefKind.ARTIFACT),
        "synapse.stage4.test.builder/v1",
    )
    attester = configure_platform_attester(
        authority_handle=handle,
        builder_runtime_identity=builder,
        trusted_clock=lambda: NOW,
    )
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    policy = _external(ExternalInputKind.POLICY, "compatibility-policy", COMPATIBILITY_POLICY_V1)
    host = _external(ExternalInputKind.ENVIRONMENT, "host-abi", "synapse.stage4.host-abi/v1")
    compiler = _external(ExternalInputKind.TOOL, "compiler", "synapse.stage4.compiler/v1")
    oracle = OracleObservation(
        ORACLE_OBSERVATION_V1,
        ActorIdentity("trusted-oracle"),
        REVISION,
        task_ref,
        _ref("oracle-result", RefKind.SOURCE_EVIDENCE),
    )
    observation = attester.observe(
        authority_handle=handle,
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(policy,),
        environment_inputs=(host,),
        tool_inputs=(compiler,),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=oracle,
    )
    attestation = attester.attest(
        authority_handle=handle,
        observed=observation,
        subject_content_key=unit.content_key,
        producer_run_id=RunId("run-001"),
        producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("candidate-producer"),),
    )
    attestation_store = open_behavior_attestation_store(
        root=tmp_path / "attestations",
        authority_handle=handle,
        platform_attester=attester,
        allow_genesis=True,
    )
    attestation_store.append(authority_handle=handle, attestation=attestation)
    subject_ref = behavior_attestation_to_ref(attestation)
    lifecycle_store = open_lifecycle_store(
        root=tmp_path / "lifecycle",
        authority_handle=handle,
        allow_genesis=True,
    )
    lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, "revision-1")
    head = _append_admitted(lifecycle_store, handle, subject_ref, lifecycle_context)
    if revoked:
        proposal = create_lifecycle_authority_proposal(
            action=LifecycleAuthorityAction.REVOKE,
            subject_ref=subject_ref,
            replacement_ref=None,
            context=lifecycle_context,
            proposer_identity=ActorIdentity("revocation-proposer"),
            producer_actor_ids=(configuration.lifecycle_writer_actor,),
            source_actor_ids=(ActorIdentity("revocation-source"),),
            evidence_refs=(_ref("revocation-evidence", RefKind.SOURCE_EVIDENCE),),
            compatibility_refs=(),
            policy_refs=(_ref("revocation-policy", RefKind.CONTRACT_CONDITION),),
            reason_codes=("REVOCATION_REQUIRED",),
            predecessor_decision_id=None,
            decision_sequence=1,
        )
        lifecycle_evaluator = configure_lifecycle_authority_evaluator(
            authority_handle=handle,
            policy_version="synapse.stage4.test.lifecycle-policy/v1",
            trusted_clock=lambda: NOW,
        )
        revocation = create_revocation_decision(
            authority_handle=handle,
            evaluator=lifecycle_evaluator,
            proposal=proposal,
            executor_identity=ActorIdentity("lifecycle-executor"),
        )
        lifecycle_store.persist_authority_decision(authority_handle=handle, decision=revocation)
        head = lifecycle_store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=lifecycle_context,
            to_state=LifecycleState.REVOKED,
            reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
            evidence_refs=(_ref("revocation-transition", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=head.record_id.value,
            expected_subject_sequence=head.subject_sequence + 1,
            revocation_decision=revocation,
        )

    taint_store = open_taint_history_store(
        root=tmp_path / "taint",
        authority_handle=handle,
        allow_genesis=True,
    )
    taint_profile = classify_source_taint(
        authority_handle=handle,
        subject_ref=subject_ref,
        taint_classes=(TaintClass.WORKER_GENERATED,),
        producer_actor_ids=(ActorIdentity("candidate-producer"),),
        source_actor_ids=(ActorIdentity("candidate-source"),),
        admission_actor_ids=(ActorIdentity("admission-actor"),),
        consumer_actor_ids=(ActorIdentity("retrieval-consumer"),),
    )
    taint_store.append_profile(authority_handle=handle, profile=taint_profile)

    declaration = create_compatibility_evaluator_declaration(
        authority_handle=handle,
        evaluator_identity=AuthorityIdentity("compatibility-evaluator"),
        evaluator_component_id="compatibility-evaluator",
        evaluator_component_version="synapse.stage4.compatibility-evaluator/v1",
        active_policy_input=policy,
        allowed_behavior_kinds=(unit.core.behavior_kind,),
        allowed_binding_kinds=(),
        allowed_capabilities=unit.core.capability_requirements,
        allowed_scope=("src/a.py",),
        selected_set_ceiling=10,
        trusted_clock=lambda: NOW,
    )
    catalog: dict[str, CompatibilitySubjectEvidence] = {}
    evaluator = configure_compatibility_evaluator(
        authority_handle=handle,
        declaration=declaration,
        evaluator_component_id=declaration.evaluator_component_id,
        evaluator_component_version=declaration.evaluator_component_version,
        trusted_clock=lambda: NOW,
        library=library,
        lifecycle_store=lifecycle_store,
        attestation_store=attestation_store,
        taint_store=taint_store,
        evidence_resolver=lambda descriptor: catalog[descriptor.descriptor_id.value],
        retriever_actor=ActorIdentity("retriever"),
        consumer_actor=ActorIdentity("retrieval-consumer"),
        score_provider_actor=ActorIdentity("score-provider"),
    )
    library_snapshot = library.current_snapshot().snapshot
    lifecycle_snapshot = lifecycle_store.snapshot()
    context = create_compatibility_context(
        evaluator=evaluator,
        authority_handle=handle,
        observation=observation,
        library_snapshot=library_snapshot,
        lifecycle_snapshot=lifecycle_snapshot,
        consumer_actor=evaluator.consumer_actor,
    )
    descriptor = create_compatibility_subject_descriptor(
        content_key=unit.content_key,
        manifest_id=manifest.manifest_id,
        blob_ref=entry.blob_ref,
        manifest_ref=entry.manifest_ref,
        behavior_kind=unit.core.behavior_kind,
        behavior_schema_version=SchemaVersion.BEHAVIOR_UNIT_V1.value,
        language_version=LANGUAGE_VERSION,
        canonical_profile=STAGE4_CANONICAL_PROFILE_V1,
        compiler_profile=COMPILER_ADAPTER_PROFILE_V1,
        compiler_version=compiler.version,
        program_sha256=hashlib.sha256(blob.canonical_core_bytes).hexdigest(),
        host_abi=host.version,
        required_capabilities=unit.core.capability_requirements,
        repository_revision=REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(policy,),
        environment_inputs=(host,),
        tool_inputs=(compiler,),
        oracle_binding=oracle,
        binding_refs=unit.core.binding_refs,
        allowed_scope=("src/a.py",),
        attestation_ref=subject_ref,
        lifecycle_subject_ref=subject_ref,
        lifecycle_context=lifecycle_context,
        lifecycle_head_record_id=head.record_id.value,
        lifecycle_snapshot_id=lifecycle_snapshot.snapshot_id,
        taint_subject_ref=subject_ref,
        taint_profile_id=taint_profile.profile_id,
        taint_history_anchor_id=taint_store.current_anchor().anchor_id,
    )
    catalog[descriptor.descriptor_id.value] = CompatibilitySubjectEvidence(
        descriptor_id=descriptor.descriptor_id,
        attestation=attestation,
        bindings=(),
        taint_root_basis=taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_context=lifecycle_context,
    )
    decision = evaluate_compatibility(
        evaluator=evaluator,
        context=context,
        descriptor=descriptor,
        index_entry=entry,
    )
    return _Harness(
        tmp_path,
        handle,
        library,
        publisher,
        lifecycle_store,
        attestation_store,
        taint_store,
        observation,
        attestation,
        taint_profile,
        lifecycle_context,
        unit,
        manifest,
        entry,
        declaration,
        evaluator,
        context,
        descriptor,
        decision,
        catalog,
    )


def _descriptor_variant(
    harness: _Harness,
    *,
    repository_revision: RepositoryRevision | None = None,
    allowed_scope: tuple[str, ...] | None = None,
) -> CompatibilitySubjectDescriptor:
    descriptor = harness.descriptor
    return create_compatibility_subject_descriptor(
        content_key=descriptor.content_key,
        manifest_id=descriptor.manifest_id,
        blob_ref=descriptor.blob_ref,
        manifest_ref=descriptor.manifest_ref,
        behavior_kind=descriptor.behavior_kind,
        behavior_schema_version=descriptor.behavior_schema_version,
        language_version=descriptor.language_version,
        canonical_profile=descriptor.canonical_profile,
        compiler_profile=descriptor.compiler_profile,
        compiler_version=descriptor.compiler_version,
        program_sha256=descriptor.program_sha256,
        host_abi=descriptor.host_abi,
        required_capabilities=descriptor.required_capabilities,
        repository_revision=descriptor.repository_revision if repository_revision is None else repository_revision,
        task_contract_ref=descriptor.task_contract_ref,
        policy_inputs=descriptor.policy_inputs,
        environment_inputs=descriptor.environment_inputs,
        tool_inputs=descriptor.tool_inputs,
        oracle_binding=descriptor.oracle_binding,
        binding_refs=descriptor.binding_refs,
        allowed_scope=descriptor.allowed_scope if allowed_scope is None else allowed_scope,
        attestation_ref=descriptor.attestation_ref,
        lifecycle_subject_ref=descriptor.lifecycle_subject_ref,
        lifecycle_context=descriptor.lifecycle_context,
        lifecycle_head_record_id=descriptor.lifecycle_head_record_id,
        lifecycle_snapshot_id=descriptor.lifecycle_snapshot_id,
        taint_subject_ref=descriptor.taint_subject_ref,
        taint_profile_id=descriptor.taint_profile_id,
        taint_history_anchor_id=descriptor.taint_history_anchor_id,
        migration_relation_refs=descriptor.migration_relation_refs,
    )


def test_s4_p6_acc_compat_evidence_01_compatible_requires_complete_evidence_and_all_dimensions(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    validate_compatibility_decision(
        harness.decision,
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
    )
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    assert harness.decision.evidence.completeness is EvidenceCompleteness.COMPLETE
    assert tuple(item.dimension for item in harness.decision.evidence.dimensions) == tuple(CompatibilityDimension)
    assert len(harness.decision.evidence.dimensions) == 12
    assert all(item.result is DimensionResult.PASS for item in harness.decision.evidence.dimensions)
    assert harness.decision.evidence.authority_decision_id == harness.decision.decision_id
    assert harness.decision.independence_proof.authority_role is AuthorityRole.COMPATIBILITY_EVALUATOR
    assert harness.decision.independence_proof.reason_code is ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT


def test_s4_p6_acc_compat_toctou_01_state_drift_after_ranking_blocks_loading(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    harness.publish_extra()
    result = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert result.stage is RevalidationStage.BEFORE_LOADING
    assert result.outcome is RevalidationOutcome.FAILED
    assert result.failure_code is CompatibilityFailureCode.SNAPSHOT_DRIFT


def test_s4_p6_acc_compat_toctou_02_state_drift_after_loading_blocks_consumption(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    before_loading = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert before_loading.outcome is RevalidationOutcome.PASSED
    harness.publish_extra()
    result = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
    )
    assert result.stage is RevalidationStage.BEFORE_CONSUMPTION
    assert result.prior_revalidation_id == before_loading.revalidation_id
    assert result.outcome is RevalidationOutcome.FAILED
    assert result.failure_code is CompatibilityFailureCode.SNAPSHOT_DRIFT


def test_s4_p6_acc_compat_context_01_is_observation_derived_and_exact_capability_bound(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    validate_compatibility_context(harness.context, evaluator=harness.evaluator)
    assert harness.context.repository_revision == harness.observation.repository_revision
    assert harness.context.policy_inputs == harness.observation.policy_inputs
    assert harness.context.environment_inputs == harness.observation.environment_inputs
    assert harness.context.tool_inputs == harness.observation.tool_inputs
    equal_handle = create_stage4_authority_handle(harness.handle.configuration)
    assert equal_handle.configuration_id == harness.handle.configuration_id
    assert equal_handle is not harness.handle
    with pytest.raises((CompatibilityViolation, ValueError)):
        create_compatibility_context(
            evaluator=harness.evaluator,
            authority_handle=equal_handle,
            observation=harness.observation,
            library_snapshot=harness.context.library_snapshot,
            lifecycle_snapshot=harness.context.lifecycle_snapshot,
            consumer_actor=harness.evaluator.consumer_actor,
        )


def test_s4_p6_acc_compat_descriptor_01_strict_transport_and_nested_tamper_fail_closed(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    raw = harness.descriptor.to_dict()
    assert "payload" not in raw
    assert "program" not in raw
    object.__setattr__(harness.descriptor, "program_sha256", "0" * 64)
    with pytest.raises(CompatibilityViolation):
        validate_compatibility_subject_descriptor(harness.descriptor)


@pytest.mark.parametrize(
    ("attribute", "replacement", "expected_kind"),
    (
        ("repository_revision", RepositoryRevision.git_commit("3" * 40), CompatibilityDecisionKind.INCOMPATIBLE_REVISION),
        ("allowed_scope", ("src/a.py", "src/b.py"), CompatibilityDecisionKind.INCOMPATIBLE_SCOPE),
    ),
)
def test_s4_p6_acc_compat_dimensions_01_substitutions_are_typed(
    tmp_path: Path,
    attribute: str,
    replacement: object,
    expected_kind: CompatibilityDecisionKind,
) -> None:
    harness = _make_harness(tmp_path)
    values = harness.descriptor.to_dict()
    values.pop("descriptor_id")
    if attribute == "repository_revision":
        values[attribute] = replacement.to_dict()
    else:
        values[attribute] = list(replacement)
    descriptor = create_compatibility_subject_descriptor(
        content_key=harness.descriptor.content_key,
        manifest_id=harness.descriptor.manifest_id,
        blob_ref=harness.descriptor.blob_ref,
        manifest_ref=harness.descriptor.manifest_ref,
        behavior_kind=harness.descriptor.behavior_kind,
        behavior_schema_version=values["behavior_schema_version"],
        language_version=values["language_version"],
        canonical_profile=values["canonical_profile"],
        compiler_profile=values["compiler_profile"],
        compiler_version=values["compiler_version"],
        program_sha256=values["program_sha256"],
        host_abi=values["host_abi"],
        required_capabilities=tuple(values["required_capabilities"]),
        repository_revision=RepositoryRevision.from_dict(values["repository_revision"]),
        task_contract_ref=HashBoundRef.from_dict(values["task_contract_ref"]),
        policy_inputs=tuple(ObservedExternalInput.from_dict(item) for item in values["policy_inputs"]),
        environment_inputs=tuple(ObservedExternalInput.from_dict(item) for item in values["environment_inputs"]),
        tool_inputs=tuple(ObservedExternalInput.from_dict(item) for item in values["tool_inputs"]),
        oracle_binding=OracleObservation.from_dict(values["oracle_binding"]),
        binding_refs=tuple(HashBoundRef.from_dict(item) for item in values["binding_refs"]),
        allowed_scope=tuple(values["allowed_scope"]),
        attestation_ref=harness.descriptor.attestation_ref,
        lifecycle_subject_ref=harness.descriptor.lifecycle_subject_ref,
        lifecycle_context=harness.lifecycle_context,
        lifecycle_head_record_id=harness.descriptor.lifecycle_head_record_id,
        lifecycle_snapshot_id=harness.context.lifecycle_snapshot.snapshot_id,
        taint_subject_ref=harness.descriptor.taint_subject_ref,
        taint_profile_id=harness.taint_profile.profile_id,
        taint_history_anchor_id=harness.taint_store.current_anchor().anchor_id,
    )
    harness.catalog[descriptor.descriptor_id.value] = CompatibilitySubjectEvidence(
        descriptor.descriptor_id,
        harness.attestation,
        (),
        harness.taint_profile,
        (),
        (),
        (),
        harness.lifecycle_context,
    )
    decision = evaluate_compatibility(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=descriptor,
        index_entry=harness.entry,
    )
    assert decision.decision_kind is expected_kind
    assert decision.decision_kind is not CompatibilityDecisionKind.MIGRATION_REQUIRED


def test_s4_p6_acc_compat_lifecycle_01_revoked_remains_distinct(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, revoked=True)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.REVOKED
    lifecycle_dimension = next(
        item for item in harness.decision.evidence.dimensions if item.dimension is CompatibilityDimension.LIFECYCLE
    )
    assert lifecycle_dimension.result is DimensionResult.FAIL
    assert harness.decision.decision_kind is not CompatibilityDecisionKind.STALE
    assert harness.decision.decision_kind is not CompatibilityDecisionKind.QUARANTINED


def test_s4_p6_acc_compat_conflict_01_proposals_are_non_authoritative_and_full_scan_blocks(tmp_path: Path) -> None:
    first = _make_harness(tmp_path)
    second_descriptor = _descriptor_variant(first, allowed_scope=("src/other.py",))
    assert second_descriptor.descriptor_id != first.descriptor.descriptor_id
    proposal = create_conflict_evidence_proposal(
        conflict_kind=ConflictKind.CONTRADICTORY_EVIDENCE,
        proposer_actor=ActorIdentity("conflict-proposer"),
        left_descriptor_id=first.descriptor.descriptor_id,
        right_descriptor_id=second_descriptor.descriptor_id,
        scope=("src/a.py",),
        binding_targets=("target-a",),
        evidence_refs=(_ref("conflict", RefKind.SOURCE_EVIDENCE),),
    )
    assert "winner" not in proposal.to_dict()
    assert "complete" not in proposal.to_dict()
    with pytest.raises(CompatibilityViolation) as exc:
        evaluate_conflicts(
            evaluator=first.evaluator,
            context=first.context,
            decisions=(first.decision,),
            descriptors=(first.descriptor, second_descriptor),
            proposals=(proposal,),
        )
    assert exc.value.failure_code in {
        CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE,
        CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
    }


def test_s4_p6_acc_compat_conflict_02_no_conflict_scan_is_authority_bound(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    scan = evaluate_conflicts(
        evaluator=harness.evaluator,
        context=harness.context,
        decisions=(harness.decision,),
        descriptors=(harness.descriptor,),
        proposals=(),
    )
    assert scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    assert scan.independence_proof.authority_identity == harness.declaration.evaluator_identity
    assert scan.request.compatible_candidate_ids == (harness.descriptor.descriptor_id,)


def test_s4_p6_acc_compat_absence_01_boolean_is_not_evidence(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    with pytest.raises((TypeError, CompatibilityViolation, AttributeError)):
        validate_compatibility_decision(True, evaluator=harness.evaluator)
    with pytest.raises((TypeError, CompatibilityViolation, AttributeError)):
        harness.catalog[harness.descriptor.descriptor_id.value] = True  # type: ignore[assignment]
        evaluate_compatibility(
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
            index_entry=harness.entry,
        )
