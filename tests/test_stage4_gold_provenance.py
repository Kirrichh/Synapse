from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest

from synapse.version import LANGUAGE_VERSION
from synapse.experiments.gold.canonicalization import (
    BEHAVIOR_CORE_SCHEMA_V1,
    CANONICAL_PROGRAM_IR_V1,
    COMPILER_ADAPTER_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    compute_content_key,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    RepositoryRevision,
    RunId,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.provenance import (
    BUILDER_RUNTIME_IDENTITY_V1,
    BEHAVIOR_ATTESTATION_JOURNAL_NAME_V1,
    OBSERVED_EXTERNAL_INPUT_V1,
    ORACLE_OBSERVATION_V1,
    BehaviorAttestation,
    BuilderRuntimeIdentity,
    ExternalInputKind,
    ObservedExternalInput,
    OracleObservation,
    ProvenanceFailureCode,
    ProvenanceViolation,
    open_behavior_attestation_store,
    require_behavior_attestation_consumable,
    behavior_attestation_to_ref,
    configure_platform_attester,
    validate_behavior_attestation,
)
from synapse.experiments.gold.lifecycle import (
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleScope,
    LifecycleState,
    SupersessionDecisionKind,
    configure_lifecycle_authority_evaluator,
    create_lifecycle_authority_proposal,
    create_revocation_decision,
    open_lifecycle_store,
)
from synapse.experiments.gold.contracts import LifecycleReasonCode
from synapse.experiments.gold.persistence import scan_journal


REVISION = RepositoryRevision.git_commit("1" * 40)
BASE_REVISION = RepositoryRevision.git_commit("2" * 40)
NOW = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


def _authority_handle(*, attester: str = "platform-attester"):
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(attester),
        builder_actor=ActorIdentity("platform-builder"),
        taint_classifier_authority=AuthorityIdentity("taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity("taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity("supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity("revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity("platform-writer"),
        governing_human_authority=AuthorityIdentity("governing-human"),
    )
    return create_stage4_authority_handle(configuration)


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


def _content_key(seed: bytes = b"behavior-one"):
    return compute_content_key(
        canonical_behavior_core_bytes=seed,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
        core_schema_id=BEHAVIOR_CORE_SCHEMA_V1,
        ir_schema_id=CANONICAL_PROGRAM_IR_V1,
        language_version=LANGUAGE_VERSION,
        compiler_adapter_profile=COMPILER_ADAPTER_PROFILE_V1,
    )


def _builder(
    revision: RepositoryRevision = REVISION,
    *,
    actor: str = "platform-builder",
) -> BuilderRuntimeIdentity:
    return BuilderRuntimeIdentity(
        BUILDER_RUNTIME_IDENTITY_V1,
        ActorIdentity(actor),
        revision,
        _ref("builder-binary", RefKind.ARTIFACT),
        "synapse.stage4.test.builder/v1",
    )


def _external(kind: ExternalInputKind, name: str) -> ObservedExternalInput:
    ref_kind = RefKind.CONTRACT_CONDITION if kind is ExternalInputKind.POLICY else RefKind.ARTIFACT
    return ObservedExternalInput(
        OBSERVED_EXTERNAL_INPUT_V1,
        kind,
        name,
        f"synapse.stage4.test.{name}/v1",
        _ref(name, ref_kind),
    )


def _oracle(revision: RepositoryRevision = REVISION, task_ref: HashBoundRef | None = None) -> OracleObservation:
    return OracleObservation(
        ORACLE_OBSERVATION_V1,
        ActorIdentity("trusted-oracle"),
        revision,
        _ref("task-contract", RefKind.CONTRACT_CONDITION) if task_ref is None else task_ref,
        _ref("oracle-result", RefKind.SOURCE_EVIDENCE),
    )


def _attestation(*, seed: bytes = b"behavior-one", run: str = "run-001", handle=None):
    handle = _authority_handle() if handle is None else handle
    key = _content_key(seed)
    builder = _builder()
    attester_identity = ActorIdentity("platform-attester")
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    attester = configure_platform_attester(
        authority_handle=handle,
        builder_runtime_identity=builder,
        trusted_clock=lambda: NOW,
    )
    observed = attester.observe(
        authority_handle=handle,
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(task_ref=task_ref),
    )
    value = attester.attest(
        authority_handle=handle,
        observed=observed,
        subject_content_key=key,
        producer_run_id=RunId(run),
        producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("worker-001"),),
    )
    return value, key, builder, attester_identity, handle, attester, observed


def test_s4_p5_acc_provenance_01_exact_attestation_roundtrip_and_reference() -> None:
    value, key, builder, attester_identity, handle, _, _ = _attestation()
    raw = value.to_dict()
    parsed = BehaviorAttestation.from_dict(
        raw,
        authority_handle=handle,
        expected_subject_content_key=key,
        expected_builder_runtime_identity=builder,
        expected_attester_identity=attester_identity,
        expected_repository_revision=REVISION,
    )
    assert parsed.to_dict() == raw
    assert parsed.attestation_id == value.attestation_id
    ref = behavior_attestation_to_ref(parsed)
    assert ref.kind is RefKind.SOURCE_EVIDENCE
    assert ref.schema_id == value.schema_version.value
    assert not ({"admitted", "approved", "correct", "executable", "trusted"} & set(raw))


@pytest.mark.parametrize("field", ["subject_content_key", "builder_runtime_identity", "oracle_observation", "source_refs", "verification_refs"])
def test_s4_p5_acc_provenance_02_content_substitution_breaks_identity(field: str) -> None:
    value, key, builder, attester_identity, handle, _, _ = _attestation()
    raw = deepcopy(value.to_dict())
    if field == "subject_content_key":
        raw[field] = _content_key(b"other").to_dict()
    elif field == "builder_runtime_identity":
        raw[field]["runtime_version"] = "synapse.stage4.test.other/v1"
    elif field == "oracle_observation":
        raw[field]["result_ref"] = _ref("other-oracle-result", RefKind.SOURCE_EVIDENCE).to_dict()
    else:
        raw[field][0] = _ref(f"other-{field}", RefKind.SOURCE_EVIDENCE).to_dict()
    with pytest.raises(ProvenanceViolation):
        BehaviorAttestation.from_dict(
            raw,
            authority_handle=handle,
            expected_subject_content_key=key,
            expected_builder_runtime_identity=builder,
            expected_attester_identity=attester_identity,
            expected_repository_revision=REVISION,
        )


def test_s4_p5_acc_provenance_03_missing_environment_or_tool_fails_closed() -> None:
    builder = _builder()
    handle = _authority_handle()
    attester = configure_platform_attester(authority_handle=handle, builder_runtime_identity=builder, trusted_clock=lambda: NOW)
    common = dict(
        authority_handle=handle,
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=_ref("task-contract", RefKind.CONTRACT_CONDITION),
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(),
    )
    for missing in ("environment_inputs", "tool_inputs"):
        arguments = dict(common)
        arguments[missing] = ()
        with pytest.raises(ProvenanceViolation) as exc:
            attester.observe(**arguments)
        assert exc.value.failure_code is ProvenanceFailureCode.EXTERNAL_INPUT_MISSING


def test_s4_p5_acc_provenance_04_worker_cannot_choose_attester_builder_or_oracle() -> None:
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    handle = _authority_handle(attester="worker-001")
    worker_attester = configure_platform_attester(authority_handle=handle, builder_runtime_identity=_builder(), trusted_clock=lambda: NOW)
    observed = worker_attester.observe(
        authority_handle=handle,
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(task_ref=task_ref),
    )
    with pytest.raises(ProvenanceViolation) as exc:
        worker_attester.attest(
            authority_handle=handle,
            observed=observed,
            subject_content_key=_content_key(),
            producer_run_id=RunId("run-001"),
            producer_attempt_id=AttemptId("attempt-001"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
        )
    assert exc.value.failure_code is ProvenanceFailureCode.ATTESTER_MISMATCH


def test_s4_p5_acc_provenance_05_builder_and_oracle_are_bound_to_verified_commit() -> None:
    other = RepositoryRevision.git_commit("3" * 40)
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    handle = _authority_handle()
    attester = configure_platform_attester(authority_handle=handle, builder_runtime_identity=_builder(), trusted_clock=lambda: NOW)
    common = dict(
        authority_handle=handle, repository_revision=REVISION, base_revision=BASE_REVISION,
        task_contract_ref=task_ref, policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),), tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),), verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(task_ref=task_ref),
    )
    observed_other = attester.observe(**{**common, "repository_revision": other, "oracle_observation": _oracle(revision=other, task_ref=task_ref)})
    with pytest.raises(ProvenanceViolation) as exc:
        attester.attest(
            authority_handle=handle,
            observed=observed_other,
            subject_content_key=_content_key(),
            producer_run_id=RunId("run-001"),
            producer_attempt_id=AttemptId("attempt-001"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
        )
    assert exc.value.failure_code is ProvenanceFailureCode.BUILDER_MISMATCH
    with pytest.raises(ProvenanceViolation) as exc:
        attester.observe(**{**common, "oracle_observation": _oracle(revision=other, task_ref=task_ref)})
    assert exc.value.failure_code is ProvenanceFailureCode.ORACLE_BINDING_MISMATCH


def test_s4_p5_acc_provenance_06_consumer_recursively_revalidates_nested_identities() -> None:
    value, _, _, _, _, _, _ = _attestation()
    object.__setattr__(value.builder_runtime_identity.builder_actor_identity, "value", "bad identity with spaces")
    with pytest.raises((ProvenanceViolation, ValueError)):
        validate_behavior_attestation(value)


def _append_lifecycle_to_admitted(store, handle, subject_ref, context) -> None:
    transitions = (
        (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
        (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
        (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
        (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
        (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
        (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    )
    predecessor = None
    for sequence, (state, reason) in enumerate(transitions, start=1):
        record = store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=context,
            to_state=state,
            reason_code=reason,
            evidence_refs=(_ref(f"lifecycle-{sequence}", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=predecessor,
            expected_subject_sequence=sequence,
        )
        predecessor = record.record_id.value


def test_s4_p5_followup_provenance_01_configured_authority_handle_controls_attester_builder_and_observed_fields() -> None:
    value, key, builder, _, handle, attester, observed = _attestation()
    equal_but_distinct = _authority_handle()
    assert equal_but_distinct.configuration_id == handle.configuration_id
    assert equal_but_distinct is not handle
    with pytest.raises(ProvenanceViolation) as exc:
        attester.observe(
            authority_handle=equal_but_distinct,
            repository_revision=REVISION,
            base_revision=BASE_REVISION,
            task_contract_ref=observed.task_contract_ref,
            policy_inputs=observed.policy_inputs,
            environment_inputs=observed.environment_inputs,
            tool_inputs=observed.tool_inputs,
            source_refs=observed.source_refs,
            verification_refs=observed.verification_refs,
            oracle_observation=observed.oracle_observation,
        )
    assert exc.value.failure_code is ProvenanceFailureCode.WRONG_AUTHORITY_HANDLE
    assert value.configuration_id == handle.configuration_id
    assert value.attester_identity == handle.configuration.platform_attester_actor
    assert value.builder_runtime_identity == builder
    assert value.generated_at == NOW
    assert value.repository_revision == observed.repository_revision
    assert value.task_contract_ref == observed.task_contract_ref
    with pytest.raises(TypeError):
        attester.attest(
            authority_handle=handle,
            observed=observed,
            subject_content_key=key,
            producer_run_id=RunId("run-worker-claim"),
            producer_attempt_id=AttemptId("attempt-worker-claim"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
            generated_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(TypeError):
        attester.attest(
            authority_handle=handle,
            observed=observed,
            subject_content_key=key,
            producer_run_id=RunId("run-worker-claim"),
            producer_attempt_id=AttemptId("attempt-worker-claim"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
            attester_identity=ActorIdentity("worker-selected-attester"),
        )
    with pytest.raises(TypeError):
        attester.attest(
            authority_handle=handle,
            observed=observed,
            subject_content_key=key,
            producer_run_id=RunId("run-worker-claim"),
            producer_attempt_id=AttemptId("attempt-worker-claim"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
            builder_runtime_identity=_builder(actor="worker-selected-builder"),
        )


def test_s4_p5_followup_provenance_02_attestation_log_requires_trusted_anchor_and_rejects_valid_prefix_rollback(tmp_path) -> None:
    handle = _authority_handle()
    first, _, _, _, _, attester, _ = _attestation(handle=handle)
    root = tmp_path / "attestations"
    store = open_behavior_attestation_store(root=root, authority_handle=handle, platform_attester=attester, allow_genesis=True)
    first_anchor = store.append(authority_handle=handle, attestation=first)
    with pytest.raises(ProvenanceViolation) as exc:
        open_behavior_attestation_store(root=root, authority_handle=handle, platform_attester=attester)
    assert exc.value.failure_code is ProvenanceFailureCode.HISTORY_ANCHOR_REQUIRED
    restarted = open_behavior_attestation_store(root=root, authority_handle=handle, platform_attester=attester, trusted_anchor=first_anchor)
    second, _, _, _, _, _, _ = _attestation(seed=b"behavior-two", run="run-002", handle=handle)
    latest_anchor = restarted.append(authority_handle=handle, attestation=second)
    journal = root / BEHAVIOR_ATTESTATION_JOURNAL_NAME_V1
    frames = scan_journal(journal).frames
    assert len(frames) == 2
    with journal.open("r+b") as stream:
        stream.truncate(frames[0].end_offset)
    with pytest.raises(ProvenanceViolation) as exc:
        open_behavior_attestation_store(root=root, authority_handle=handle, platform_attester=attester, trusted_anchor=latest_anchor)
    assert exc.value.failure_code is ProvenanceFailureCode.HISTORY_ROLLBACK


def test_s4_p5_followup_provenance_03_attestation_consumption_revalidates_current_lifecycle_and_blocks_revocation(tmp_path) -> None:
    value, key, _, _, handle, attester, _ = _attestation()
    provenance_store = open_behavior_attestation_store(
        root=tmp_path / "provenance",
        authority_handle=handle,
        platform_attester=attester,
        allow_genesis=True,
    )
    provenance_store.append(authority_handle=handle, attestation=value)
    lifecycle_store = open_lifecycle_store(root=tmp_path / "lifecycle", authority_handle=handle, allow_genesis=True)
    context = LifecycleContext("synapse.stage4.gold.lifecycle-context/v1", LifecycleScope.REVISION, "revision-001")
    with pytest.raises(ProvenanceViolation) as exc:
        require_behavior_attestation_consumable(
            attestation=value,
            expected_subject_content_key=key,
            authority_handle=handle,
            attestation_store=provenance_store,
            lifecycle_store=lifecycle_store,
            lifecycle_context=context,
        )
    assert exc.value.failure_code is ProvenanceFailureCode.ATTESTATION_NOT_ADMITTED
    subject_ref = behavior_attestation_to_ref(value)
    _append_lifecycle_to_admitted(lifecycle_store, handle, subject_ref, context)
    assert require_behavior_attestation_consumable(
        attestation=value,
        expected_subject_content_key=key,
        authority_handle=handle,
        attestation_store=provenance_store,
        lifecycle_store=lifecycle_store,
        lifecycle_context=context,
    ) is value
    proposal = create_lifecycle_authority_proposal(
        action=LifecycleAuthorityAction.REVOKE,
        subject_ref=subject_ref,
        replacement_ref=None,
        context=context,
        proposer_identity=ActorIdentity("revocation-proposer"),
        producer_actor_ids=(ActorIdentity("platform-writer"),),
        source_actor_ids=(ActorIdentity("revocation-source"),),
        evidence_refs=(_ref("revocation-evidence", RefKind.SOURCE_EVIDENCE),),
        compatibility_refs=(),
        policy_refs=(_ref("revocation-policy", RefKind.CONTRACT_CONDITION),),
        reason_codes=("REVOCATION_REQUIRED",),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    evaluator = configure_lifecycle_authority_evaluator(
        authority_handle=handle,
        policy_version="synapse.stage4.test.lifecycle-policy/v1",
        trusted_clock=lambda: NOW,
    )
    decision = create_revocation_decision(
        authority_handle=handle,
        evaluator=evaluator,
        proposal=proposal,
        executor_identity=ActorIdentity("lifecycle-executor"),
    )
    lifecycle_store.persist_authority_decision(authority_handle=handle, decision=decision)
    head = lifecycle_store.records()[-1]
    lifecycle_store.append(
        authority_handle=handle,
        subject_ref=subject_ref,
        context=context,
        to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
        evidence_refs=(_ref("revocation-transition", RefKind.SOURCE_EVIDENCE),),
        expected_predecessor_record_id=head.record_id.value,
        expected_subject_sequence=head.subject_sequence + 1,
        revocation_decision=decision,
    )
    with pytest.raises(ProvenanceViolation) as exc:
        require_behavior_attestation_consumable(
            attestation=value,
            expected_subject_content_key=key,
            authority_handle=handle,
            attestation_store=provenance_store,
            lifecycle_store=lifecycle_store,
            lifecycle_context=context,
        )
    assert exc.value.failure_code is ProvenanceFailureCode.ATTESTATION_REVOKED


def test_s4_p5_followup_provenance_04_oracle_observation_remains_bound_to_verified_commit_and_task() -> None:
    handle = _authority_handle()
    attester = configure_platform_attester(authority_handle=handle, builder_runtime_identity=_builder(), trusted_clock=lambda: NOW)
    task = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    other_task = _ref("other-task-contract", RefKind.CONTRACT_CONDITION)
    common = dict(
        authority_handle=handle,
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=task,
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
    )
    with pytest.raises(ProvenanceViolation) as commit_exc:
        attester.observe(**common, oracle_observation=_oracle(revision=RepositoryRevision.git_commit("3" * 40), task_ref=task))
    assert commit_exc.value.failure_code is ProvenanceFailureCode.ORACLE_BINDING_MISMATCH
    with pytest.raises(ProvenanceViolation) as task_exc:
        attester.observe(**common, oracle_observation=_oracle(task_ref=other_task))
    assert task_exc.value.failure_code is ProvenanceFailureCode.ORACLE_BINDING_MISMATCH
