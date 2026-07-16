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
from synapse.experiments.gold.contracts import ActorIdentity, AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.provenance import (
    BUILDER_RUNTIME_IDENTITY_V1,
    OBSERVED_EXTERNAL_INPUT_V1,
    ORACLE_OBSERVATION_V1,
    BehaviorAttestation,
    BuilderRuntimeIdentity,
    ExternalInputKind,
    ObservedExternalInput,
    OracleObservation,
    ProvenanceFailureCode,
    ProvenanceViolation,
    behavior_attestation_to_ref,
    configure_platform_attester,
    validate_behavior_attestation,
)


REVISION = RepositoryRevision.git_commit("1" * 40)
BASE_REVISION = RepositoryRevision.git_commit("2" * 40)


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


def _builder(revision: RepositoryRevision = REVISION) -> BuilderRuntimeIdentity:
    return BuilderRuntimeIdentity(
        BUILDER_RUNTIME_IDENTITY_V1,
        ActorIdentity("platform-builder"),
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


def _attestation() -> tuple[BehaviorAttestation, object, BuilderRuntimeIdentity, ActorIdentity]:
    key = _content_key()
    builder = _builder()
    attester_identity = ActorIdentity("platform-attester")
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    attester = configure_platform_attester(
        attester_identity=attester_identity,
        builder_runtime_identity=builder,
    )
    value = attester.attest(
        subject_content_key=key,
        producer_run_id=RunId("run-001"),
        producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("worker-001"),),
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(task_ref=task_ref),
        generated_at=datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc),
    )
    return value, key, builder, attester_identity


def test_s4_p5_acc_provenance_01_exact_attestation_roundtrip_and_reference() -> None:
    value, key, builder, attester_identity = _attestation()
    raw = value.to_dict()
    parsed = BehaviorAttestation.from_dict(
        raw,
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
    value, key, builder, attester_identity = _attestation()
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
            expected_subject_content_key=key,
            expected_builder_runtime_identity=builder,
            expected_attester_identity=attester_identity,
            expected_repository_revision=REVISION,
        )


def test_s4_p5_acc_provenance_03_missing_environment_or_tool_fails_closed() -> None:
    builder = _builder()
    attester = configure_platform_attester(attester_identity=ActorIdentity("platform-attester"), builder_runtime_identity=builder)
    common = dict(
        subject_content_key=_content_key(),
        producer_run_id=RunId("run-001"),
        producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("worker-001"),),
        repository_revision=REVISION,
        base_revision=BASE_REVISION,
        task_contract_ref=_ref("task-contract", RefKind.CONTRACT_CONDITION),
        policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
        tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(),
        generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    for missing in ("environment_inputs", "tool_inputs"):
        arguments = dict(common)
        arguments[missing] = ()
        with pytest.raises(ProvenanceViolation) as exc:
            attester.attest(**arguments)
        assert exc.value.failure_code is ProvenanceFailureCode.EXTERNAL_INPUT_MISSING


def test_s4_p5_acc_provenance_04_worker_cannot_choose_attester_builder_or_oracle() -> None:
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    worker_attester = configure_platform_attester(attester_identity=ActorIdentity("worker-001"), builder_runtime_identity=_builder())
    with pytest.raises(ProvenanceViolation) as exc:
        worker_attester.attest(
            subject_content_key=_content_key(),
            producer_run_id=RunId("run-001"),
            producer_attempt_id=AttemptId("attempt-001"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
            repository_revision=REVISION,
            base_revision=BASE_REVISION,
            task_contract_ref=task_ref,
            policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
            environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),),
            tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
            source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
            verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
            oracle_observation=_oracle(task_ref=task_ref),
            generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    assert exc.value.failure_code is ProvenanceFailureCode.ATTESTER_MISMATCH


def test_s4_p5_acc_provenance_05_builder_and_oracle_are_bound_to_verified_commit() -> None:
    other = RepositoryRevision.git_commit("3" * 40)
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    attester = configure_platform_attester(attester_identity=ActorIdentity("platform-attester"), builder_runtime_identity=_builder())
    common = dict(
        subject_content_key=_content_key(), producer_run_id=RunId("run-001"), producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("worker-001"),), repository_revision=REVISION, base_revision=BASE_REVISION,
        task_contract_ref=task_ref, policy_inputs=(_external(ExternalInputKind.POLICY, "policy"),),
        environment_inputs=(_external(ExternalInputKind.ENVIRONMENT, "environment"),), tool_inputs=(_external(ExternalInputKind.TOOL, "tool"),),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),), verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=_oracle(task_ref=task_ref), generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    with pytest.raises(ProvenanceViolation) as exc:
        attester.attest(**{**common, "repository_revision": other})
    assert exc.value.failure_code is ProvenanceFailureCode.BUILDER_MISMATCH
    with pytest.raises(ProvenanceViolation) as exc:
        attester.attest(**{**common, "oracle_observation": _oracle(revision=other, task_ref=task_ref)})
    assert exc.value.failure_code is ProvenanceFailureCode.ORACLE_BINDING_MISMATCH


def test_s4_p5_acc_provenance_06_consumer_recursively_revalidates_nested_identities() -> None:
    value, _, _, _ = _attestation()
    object.__setattr__(value.builder_runtime_identity.builder_actor_identity, "value", "bad identity with spaces")
    with pytest.raises((ProvenanceViolation, ValueError)):
        validate_behavior_attestation(value)
