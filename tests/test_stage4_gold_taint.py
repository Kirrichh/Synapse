from __future__ import annotations

import hashlib

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    ReasonCode,
    SchemaVersion,
    create_independence_proof,
)
from synapse.experiments.gold.taint import (
    TAINT_REMOVAL_VERIFICATION_V1,
    TaintAuthorityDecision,
    TaintClass,
    TaintDecisionKind,
    TaintDerivationRecord,
    TaintFailureCode,
    TaintRemovalReason,
    TaintRemovalVerification,
    TaintViolation,
    classify_source_taint,
    create_taint_authority_decision,
    create_taint_change_proposal,
    create_taint_derivation,
    reconstruct_effective_taint,
    validate_source_taint_profile,
    validate_taint_derivation,
)


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


def _profile(*, classifier: str = "taint-classifier"):
    return classify_source_taint(
        subject_ref=_ref("subject", RefKind.ARTIFACT),
        taint_classes=(
            TaintClass.EXTERNAL_USER_CONTENT,
            TaintClass.UNVERIFIED_CLAIM,
            TaintClass.CONTAINS_INSTRUCTION_LIKE_TEXT,
        ),
        classifier_identity=AuthorityIdentity(classifier),
        producer_actor_ids=(ActorIdentity("worker-001"),),
        source_actor_ids=(ActorIdentity("external-user"),),
        admission_actor_ids=(ActorIdentity("admission-evaluator"),),
        consumer_actor_ids=(ActorIdentity("retrieval-consumer"),),
    )


def _proof(proposal, *, authority: str = "independent-taint-reviewer"):
    return create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal.proposal_id,
        authority_identity=AuthorityIdentity(authority),
        authority_role=AuthorityRole.TAINT_REVIEWER,
        reason_code=ReasonCode.TAINT_REVIEW_INDEPENDENT,
        producer_actor_ids=proposal.producer_actor_ids,
        source_actor_ids=proposal.source_actor_ids,
        proposer_identity=proposal.proposer_identity,
        executor_identity=None,
        subject_derived_actor_ids=(),
        delegation_chain=(),
    )


def _verification(taint_class: TaintClass) -> TaintRemovalVerification:
    return TaintRemovalVerification(
        TAINT_REMOVAL_VERIFICATION_V1,
        taint_class,
        _ref(f"verify-contract-{taint_class.value.lower()}", RefKind.CONTRACT_CONDITION),
        _ref(f"verify-result-{taint_class.value.lower()}", RefKind.SOURCE_EVIDENCE),
        TaintRemovalReason.VERIFIED_CLAIM,
    )


def test_s4_p5_acc_taint_01_normative_class_registry_and_profile_identity() -> None:
    assert {item.value for item in TaintClass} == {
        "TRUSTED_PLATFORM_DERIVED", "HUMAN_APPROVED_POLICY", "ORACLE_DERIVED", "TOOL_GENERATED",
        "REPOSITORY_CONTENT", "DOCUMENT_CONTENT", "EXTERNAL_USER_CONTENT", "WORKER_GENERATED",
        "LLM_GENERATED", "UNVERIFIED_CODE", "UNVERIFIED_CLAIM", "CONTAINS_INSTRUCTION_LIKE_TEXT",
        "CONTAINS_SECRET_LIKE_DATA", "CONTAINS_EXECUTABLE_CONTENT",
    }
    profile = _profile()
    validate_source_taint_profile(profile, expected_subject_ref=_ref("subject", RefKind.ARTIFACT))
    assert profile.profile_id.domain.value.endswith("taint-profile-record/v1")


@pytest.mark.parametrize("actor", ["worker-001", "external-user", "admission-evaluator", "retrieval-consumer"])
def test_s4_p5_acc_taint_02_classifier_is_separate_from_every_participant(actor: str) -> None:
    with pytest.raises(TaintViolation) as exc:
        _profile(classifier=actor)
    assert exc.value.failure_code is TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT


def test_s4_p5_acc_taint_03_derivation_is_exact_monotone_union() -> None:
    first = _profile()
    second = classify_source_taint(
        subject_ref=_ref("second-subject", RefKind.ARTIFACT),
        taint_classes=(TaintClass.REPOSITORY_CONTENT, TaintClass.UNVERIFIED_CODE),
        classifier_identity=AuthorityIdentity("second-classifier"),
        producer_actor_ids=(ActorIdentity("worker-002"),),
        source_actor_ids=(ActorIdentity("repository-source"),),
        admission_actor_ids=(),
        consumer_actor_ids=(),
    )
    derived = create_taint_derivation(
        subject_ref=_ref("derived-subject", RefKind.ARTIFACT),
        source_profiles=(first, second),
        transformation_actor=ActorIdentity("distillation-tool"),
        transformation_labels=(TaintClass.TOOL_GENERATED,),
    )
    assert set(derived.effective_taint_classes) == set(first.taint_classes) | set(second.taint_classes) | {TaintClass.TOOL_GENERATED}
    parsed = TaintDerivationRecord.from_dict(
        derived.to_dict(source_profiles=(first, second)),
        source_profiles=(first, second),
        expected_subject_ref=_ref("derived-subject", RefKind.ARTIFACT),
    )
    assert parsed.derivation_id == derived.derivation_id
    object.__setattr__(derived, "effective_taint_classes", tuple(item for item in derived.effective_taint_classes if item is not TaintClass.UNVERIFIED_CODE))
    with pytest.raises(TaintViolation) as exc:
        validate_taint_derivation(derived, source_profiles=(first, second))
    assert exc.value.failure_code is TaintFailureCode.TAINT_REMOVAL_FORBIDDEN


def test_s4_p5_acc_taint_04_relaxation_requires_independent_proof_and_evidence_per_removed_class() -> None:
    profile = _profile()
    proposed = (TaintClass.EXTERNAL_USER_CONTENT,)
    proposal = create_taint_change_proposal(
        current_profile=profile,
        proposed_taint_classes=proposed,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    required = {
        TaintClass.UNVERIFIED_CLAIM,
        TaintClass.CONTAINS_INSTRUCTION_LIKE_TEXT,
    }
    with pytest.raises(TaintViolation) as exc:
        create_taint_authority_decision(
            proposal=proposal,
            decision_kind=TaintDecisionKind.RELAX_TAINT,
            removal_verifications=(_verification(TaintClass.UNVERIFIED_CLAIM),),
            policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
            independence_proof=_proof(proposal),
        )
    assert exc.value.failure_code is TaintFailureCode.REMOVAL_EVIDENCE_MISSING
    decision = create_taint_authority_decision(
        proposal=proposal,
        decision_kind=TaintDecisionKind.RELAX_TAINT,
        removal_verifications=tuple(_verification(item) for item in required),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        independence_proof=_proof(proposal),
    )
    assert set(decision.effective_taint_classes) == set(proposed)
    assert {item.taint_class for item in decision.removal_verifications} == required
    assert TaintAuthorityDecision.from_dict(decision.to_dict(), current_profile=profile).decision_id == decision.decision_id


def test_s4_p5_acc_taint_05_worker_cannot_self_approve_relaxation() -> None:
    profile = _profile()
    proposal = create_taint_change_proposal(
        current_profile=profile,
        proposed_taint_classes=(TaintClass.EXTERNAL_USER_CONTENT,),
        proposer_identity=ActorIdentity("worker-001"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    with pytest.raises(ValueError):
        _proof(proposal, authority="worker-001")


def test_s4_p5_acc_taint_06_rejected_relaxation_retains_all_current_taint() -> None:
    profile = _profile()
    proposal = create_taint_change_proposal(
        current_profile=profile,
        proposed_taint_classes=(TaintClass.EXTERNAL_USER_CONTENT,),
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    decision = create_taint_authority_decision(
        proposal=proposal,
        decision_kind=TaintDecisionKind.REJECT_RELAXATION,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        independence_proof=_proof(proposal),
    )
    state = reconstruct_effective_taint(source_profile=profile, decisions=(decision,))
    assert state.taint_classes == profile.taint_classes
    assert not state.quarantined


def test_s4_p5_acc_taint_07_decision_chain_reconstructs_without_silent_removal() -> None:
    profile = _profile()
    first_proposal = create_taint_change_proposal(
        current_profile=profile,
        proposed_taint_classes=(*profile.taint_classes, TaintClass.TOOL_GENERATED),
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    first = create_taint_authority_decision(
        proposal=first_proposal,
        decision_kind=TaintDecisionKind.ADD_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        independence_proof=_proof(first_proposal),
    )
    second_proposal = create_taint_change_proposal(
        current_profile=profile,
        current_effective_taint_classes=first.effective_taint_classes,
        proposed_taint_classes=first.effective_taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=first.decision_id.record_id.value,
        decision_sequence=2,
    )
    second = create_taint_authority_decision(
        proposal=second_proposal,
        decision_kind=TaintDecisionKind.QUARANTINE,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        independence_proof=_proof(second_proposal),
    )
    state = reconstruct_effective_taint(source_profile=profile, decisions=(first, second))
    assert TaintClass.TOOL_GENERATED in state.taint_classes
    assert state.quarantined
    with pytest.raises(TaintViolation) as exc:
        reconstruct_effective_taint(source_profile=profile, decisions=(second, first))
    assert exc.value.failure_code is TaintFailureCode.DECISION_CHAIN_MISMATCH


def test_s4_p5_acc_taint_08_direct_decision_construction_and_nested_mutation_fail() -> None:
    with pytest.raises(TypeError):
        TaintAuthorityDecision()
    profile = _profile()
    object.__setattr__(profile.classifier_identity, "value", "bad identity with spaces")
    with pytest.raises((TaintViolation, ValueError)):
        validate_source_taint_profile(profile)
