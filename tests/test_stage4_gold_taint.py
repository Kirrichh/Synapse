from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    ContractViolation,
    HistoryDomain,
    ReasonCode,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
    create_independence_proof,
    history_anchor_from_dict,
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
    configure_taint_authority_evaluator,
    open_taint_history_store,
    reconstruct_effective_taint,
    require_taint_consumable,
    validate_source_taint_profile,
    validate_taint_derivation,
)
from synapse.experiments.gold.persistence import scan_journal
from synapse.experiments.gold.taint import TAINT_HISTORY_JOURNAL_NAME_V1


NOW = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


def _handle(*, classifier: str = "taint-classifier", reviewer: str = "independent-taint-reviewer"):
    return create_stage4_authority_handle(
        create_stage4_authority_configuration(
            platform_attester_actor=ActorIdentity("platform-attester"),
            builder_actor=ActorIdentity("platform-builder"),
            taint_classifier_authority=AuthorityIdentity(classifier),
            taint_reviewer_authority=AuthorityIdentity(reviewer),
            supersession_reviewer_authority=AuthorityIdentity("supersession-reviewer"),
            revocation_reviewer_authority=AuthorityIdentity("revocation-reviewer"),
            lifecycle_writer_actor=ActorIdentity("platform-writer"),
            governing_human_authority=AuthorityIdentity("governing-human"),
        )
    )


HANDLE = _handle()
EVALUATOR = configure_taint_authority_evaluator(
    authority_handle=HANDLE,
    policy_version="synapse.stage4.test.taint-policy/v1",
    trusted_clock=lambda: NOW,
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


def _profile(*, classifier: str = "taint-classifier", handle=None):
    handle = (_handle(classifier=classifier) if classifier != "taint-classifier" else HANDLE) if handle is None else handle
    return classify_source_taint(
        authority_handle=handle,
        subject_ref=_ref("subject", RefKind.ARTIFACT),
        taint_classes=(
            TaintClass.EXTERNAL_USER_CONTENT,
            TaintClass.UNVERIFIED_CLAIM,
            TaintClass.CONTAINS_INSTRUCTION_LIKE_TEXT,
        ),
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
        authority_handle=HANDLE,
        subject_ref=_ref("second-subject", RefKind.ARTIFACT),
        taint_classes=(TaintClass.REPOSITORY_CONTENT, TaintClass.UNVERIFIED_CODE),
        producer_actor_ids=(ActorIdentity("worker-002"),),
        source_actor_ids=(ActorIdentity("repository-source"),),
        admission_actor_ids=(),
        consumer_actor_ids=(),
    )
    derived = create_taint_derivation(
        authority_handle=HANDLE,
        subject_ref=_ref("derived-subject", RefKind.ARTIFACT),
        source_profiles=(first, second),
        transformation_actor=ActorIdentity("distillation-tool"),
        transformation_labels=(TaintClass.TOOL_GENERATED,),
    )
    assert set(derived.effective_taint_classes) == set(first.taint_classes) | set(second.taint_classes) | {TaintClass.TOOL_GENERATED}
    parsed = TaintDerivationRecord.from_dict(
        derived.to_dict(source_profiles=(first, second)),
        authority_handle=HANDLE,
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
        authority_handle=HANDLE,
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
            authority_handle=HANDLE,
            evaluator=EVALUATOR,
            proposal=proposal,
            decision_kind=TaintDecisionKind.RELAX_TAINT,
            removal_verifications=(_verification(TaintClass.UNVERIFIED_CLAIM),),
            policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        )
    assert exc.value.failure_code is TaintFailureCode.REMOVAL_EVIDENCE_MISSING
    decision = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=proposal,
        decision_kind=TaintDecisionKind.RELAX_TAINT,
        removal_verifications=tuple(_verification(item) for item in required),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    assert set(decision.effective_taint_classes) == set(proposed)
    assert {item.taint_class for item in decision.removal_verifications} == required
    assert TaintAuthorityDecision.from_dict(
        decision.to_dict(),
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        current_profile=profile,
    ).decision_id == decision.decision_id


def test_s4_p5_acc_taint_05_worker_cannot_self_approve_relaxation() -> None:
    profile = _profile()
    proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
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
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=(TaintClass.EXTERNAL_USER_CONTENT,),
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    decision = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=proposal,
        decision_kind=TaintDecisionKind.REJECT_RELAXATION,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    state = reconstruct_effective_taint(authority_handle=HANDLE, root_basis=profile, decisions=(decision,))
    assert state.taint_classes == profile.taint_classes
    assert not state.quarantined


def test_s4_p5_acc_taint_07_decision_chain_reconstructs_without_silent_removal() -> None:
    profile = _profile()
    first_proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=(*profile.taint_classes, TaintClass.TOOL_GENERATED),
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    first = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=first_proposal,
        decision_kind=TaintDecisionKind.ADD_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    second_proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=first.effective_taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=first.decision_id.record_id.value,
        decision_sequence=2,
        prior_decisions=(first,),
    )
    second = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=second_proposal,
        decision_kind=TaintDecisionKind.QUARANTINE,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    state = reconstruct_effective_taint(authority_handle=HANDLE, root_basis=profile, decisions=(first, second))
    assert TaintClass.TOOL_GENERATED in state.taint_classes
    assert state.quarantined
    with pytest.raises(TaintViolation) as exc:
        reconstruct_effective_taint(authority_handle=HANDLE, root_basis=profile, decisions=(second, first))
    assert exc.value.failure_code is TaintFailureCode.DECISION_CHAIN_MISMATCH


def test_s4_p5_acc_taint_08_direct_decision_construction_and_nested_mutation_fail() -> None:
    with pytest.raises(TypeError):
        TaintAuthorityDecision()
    profile = _profile()
    object.__setattr__(profile.classifier_identity, "value", "bad identity with spaces")
    with pytest.raises((TaintViolation, ValueError)):
        validate_source_taint_profile(profile)


def test_s4_p5_followup_taint_01_configured_classifier_and_reviewer_cannot_be_worker_selected() -> None:
    profile = _profile()
    assert profile.classifier_identity == HANDLE.configuration.taint_classifier_authority
    proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=profile.taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    decision = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=proposal,
        decision_kind=TaintDecisionKind.RETAIN_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        executor_identity=ActorIdentity("taint-executor"),
    )
    assert decision.independence_proof.authority_identity == HANDLE.configuration.taint_reviewer_authority
    worker_handle = _handle(reviewer="worker-001")
    with pytest.raises(TaintViolation) as exc:
        create_taint_authority_decision(
            authority_handle=worker_handle,
            evaluator=EVALUATOR,
            proposal=proposal,
            decision_kind=TaintDecisionKind.RETAIN_TAINT,
            removal_verifications=(),
            policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        )
    assert exc.value.failure_code is TaintFailureCode.WRONG_AUTHORITY_HANDLE
    with pytest.raises(TypeError):
        classify_source_taint(
            authority_handle=HANDLE,
            subject_ref=_ref("subject", RefKind.ARTIFACT),
            taint_classes=(TaintClass.UNVERIFIED_CLAIM,),
            classifier_identity=AuthorityIdentity("worker-001"),
            producer_actor_ids=(ActorIdentity("worker-001"),),
            source_actor_ids=(ActorIdentity("external-user"),),
            admission_actor_ids=(),
            consumer_actor_ids=(),
        )


def _recursive_taint_graph():
    first = _profile()
    second = classify_source_taint(
        authority_handle=HANDLE,
        subject_ref=_ref("second-source", RefKind.ARTIFACT),
        taint_classes=(TaintClass.REPOSITORY_CONTENT, TaintClass.UNVERIFIED_CODE),
        producer_actor_ids=(ActorIdentity("worker-002"),),
        source_actor_ids=(ActorIdentity("repository-source"),),
        admission_actor_ids=(),
        consumer_actor_ids=(),
    )
    third = classify_source_taint(
        authority_handle=HANDLE,
        subject_ref=_ref("third-source", RefKind.ARTIFACT),
        taint_classes=(TaintClass.DOCUMENT_CONTENT,),
        producer_actor_ids=(ActorIdentity("worker-003"),),
        source_actor_ids=(ActorIdentity("document-source"),),
        admission_actor_ids=(),
        consumer_actor_ids=(),
    )
    first_derivation = create_taint_derivation(
        authority_handle=HANDLE,
        subject_ref=_ref("derived-one", RefKind.ARTIFACT),
        source_profiles=(first, second),
        transformation_actor=ActorIdentity("transform-one"),
        transformation_labels=(TaintClass.TOOL_GENERATED,),
    )
    root = create_taint_derivation(
        authority_handle=HANDLE,
        subject_ref=_ref("derived-root", RefKind.ARTIFACT),
        source_profiles=(third,),
        source_derivations=(first_derivation,),
        transformation_actor=ActorIdentity("transform-two"),
        transformation_labels=(TaintClass.WORKER_GENERATED,),
    )
    return (first, second, third), (first_derivation, root), root


def test_s4_p5_followup_taint_02_consumption_reconstructs_complete_derivation_and_decision_chain(tmp_path) -> None:
    profiles, derivations, root = _recursive_taint_graph()
    proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=root,
        source_profiles=profiles,
        source_derivations=derivations,
        proposed_taint_classes=root.effective_taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    decision = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=proposal,
        decision_kind=TaintDecisionKind.RETAIN_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    store = open_taint_history_store(
        root=tmp_path / "taint-history",
        authority_handle=HANDLE,
        allow_genesis=True,
    )
    for profile in profiles:
        store.append_profile(authority_handle=HANDLE, profile=profile)
    store.append_derivation(
        authority_handle=HANDLE,
        derivation=derivations[0],
        source_profiles=profiles[:2],
    )
    store.append_derivation(
        authority_handle=HANDLE,
        derivation=root,
        source_profiles=(profiles[2],),
        source_derivations=(derivations[0],),
    )
    store.append_decision(authority_handle=HANDLE, decision=decision)
    state = require_taint_consumable(
        authority_handle=HANDLE,
        root_basis=root,
        source_profiles=profiles,
        derivations=derivations,
        decisions=(decision,),
        history_store=store,
    )
    assert set(state.taint_classes) == set(root.effective_taint_classes)
    with pytest.raises(TaintViolation) as unanchored:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=root,
            source_profiles=profiles,
            derivations=derivations,
            decisions=(decision,),
            history_store=None,
        )
    assert unanchored.value.failure_code is TaintFailureCode.HISTORY_ANCHOR_REQUIRED
    with pytest.raises(TaintViolation) as missing:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=root,
            source_profiles=(profiles[0], profiles[2]),
            derivations=derivations,
            decisions=(decision,),
            history_store=store,
        )
    assert missing.value.failure_code is TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE
    substituted = classify_source_taint(
        authority_handle=HANDLE,
        subject_ref=_ref("substituted", RefKind.ARTIFACT),
        taint_classes=profiles[1].taint_classes,
        producer_actor_ids=profiles[1].producer_actor_ids,
        source_actor_ids=profiles[1].source_actor_ids,
        admission_actor_ids=profiles[1].admission_actor_ids,
        consumer_actor_ids=profiles[1].consumer_actor_ids,
    )
    with pytest.raises(TaintViolation):
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=root,
            source_profiles=(profiles[0], substituted, profiles[2]),
            derivations=derivations,
            decisions=(decision,),
            history_store=store,
        )
    original_parents = derivations[0].source_derivation_ids
    object.__setattr__(derivations[0], "source_derivation_ids", (root.derivation_id,))
    try:
        with pytest.raises(TaintViolation) as cycle:
            require_taint_consumable(
                authority_handle=HANDLE,
                root_basis=root,
                source_profiles=profiles,
                derivations=derivations,
                decisions=(decision,),
                history_store=store,
            )
        assert cycle.value.failure_code is TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE
    finally:
        object.__setattr__(derivations[0], "source_derivation_ids", original_parents)
    with pytest.raises(TaintViolation):
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=profiles[0],
            decisions=(decision,),
            history_store=store,
        )


def test_s4_p5_followup_taint_03_quarantine_is_sticky_for_same_subject_identity(tmp_path) -> None:
    profile = _profile()
    retain_proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=profile.taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    retain = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=retain_proposal,
        decision_kind=TaintDecisionKind.RETAIN_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    quarantine_proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=profile.taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=retain.decision_id.record_id.value,
        decision_sequence=2,
        prior_decisions=(retain,),
    )
    quarantine = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=quarantine_proposal,
        decision_kind=TaintDecisionKind.QUARANTINE,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    store = open_taint_history_store(
        root=tmp_path / "current-head",
        authority_handle=HANDLE,
        allow_genesis=True,
    )
    store.append_profile(authority_handle=HANDLE, profile=profile)
    store.append_decision(authority_handle=HANDLE, decision=retain)
    store.append_decision(authority_handle=HANDLE, decision=quarantine)
    with pytest.raises(TaintViolation) as empty_chain:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=profile,
            decisions=(),
            history_store=store,
        )
    assert empty_chain.value.failure_code is TaintFailureCode.HISTORY_ROLLBACK
    with pytest.raises(TaintViolation) as stale_prefix:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=profile,
            decisions=(retain,),
            history_store=store,
        )
    assert stale_prefix.value.failure_code is TaintFailureCode.HISTORY_ROLLBACK
    with pytest.raises(TaintViolation) as reordered:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=profile,
            decisions=(quarantine, retain),
            history_store=store,
        )
    assert reordered.value.failure_code is TaintFailureCode.DECISION_CHAIN_MISMATCH
    with pytest.raises(TaintViolation) as current_quarantine:
        require_taint_consumable(
            authority_handle=HANDLE,
            root_basis=profile,
            decisions=(retain, quarantine),
            history_store=store,
        )
    assert current_quarantine.value.failure_code is TaintFailureCode.STICKY_QUARANTINE
    cases = (
        (TaintDecisionKind.RETAIN_TAINT, profile.taint_classes, ()),
        (TaintDecisionKind.ADD_TAINT, (*profile.taint_classes, TaintClass.TOOL_GENERATED), ()),
        (
            TaintDecisionKind.RELAX_TAINT,
            tuple(item for item in profile.taint_classes if item is not TaintClass.UNVERIFIED_CLAIM),
            (_verification(TaintClass.UNVERIFIED_CLAIM),),
        ),
    )
    for kind, proposed, verifications in cases:
        proposal = create_taint_change_proposal(
            authority_handle=HANDLE,
            current_profile=profile,
            proposed_taint_classes=proposed,
            proposer_identity=ActorIdentity("policy-proposer"),
            predecessor_decision_id=quarantine.decision_id.record_id.value,
            decision_sequence=3,
            prior_decisions=(retain, quarantine),
        )
        decision = create_taint_authority_decision(
            authority_handle=HANDLE,
            evaluator=EVALUATOR,
            proposal=proposal,
            decision_kind=kind,
            removal_verifications=verifications,
            policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
        )
        case_store = open_taint_history_store(
            root=tmp_path / f"sticky-{kind.value.lower()}",
            authority_handle=HANDLE,
            allow_genesis=True,
        )
        case_store.append_profile(authority_handle=HANDLE, profile=profile)
        case_store.append_decision(authority_handle=HANDLE, decision=retain)
        case_store.append_decision(authority_handle=HANDLE, decision=quarantine)
        case_store.append_decision(authority_handle=HANDLE, decision=decision)
        with pytest.raises(TaintViolation) as exc:
            require_taint_consumable(
                authority_handle=HANDLE,
                root_basis=profile,
                decisions=(retain, quarantine, decision),
                history_store=case_store,
            )
        assert exc.value.failure_code is TaintFailureCode.STICKY_QUARANTINE


def test_s4_p5_followup_taint_04_history_store_requires_anchor_and_rejects_rollback_or_fork(tmp_path) -> None:
    profile = _profile()
    root = tmp_path / "taint-history"
    store = open_taint_history_store(root=root, authority_handle=HANDLE, allow_genesis=True)
    store.append_profile(authority_handle=HANDLE, profile=profile)
    proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=profile.taint_classes,
        proposer_identity=ActorIdentity("policy-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    decision = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=proposal,
        decision_kind=TaintDecisionKind.RETAIN_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    latest_anchor = store.append_decision(authority_handle=HANDLE, decision=decision)
    anchor_transport = json.loads(json.dumps(latest_anchor.to_dict()))
    restored_anchor = history_anchor_from_dict(
        anchor_transport,
        expected_history_domain=HistoryDomain.TAINT,
        expected_configuration_id=HANDLE.configuration_id,
    )
    assert restored_anchor is not latest_anchor
    assert restored_anchor.to_dict() == latest_anchor.to_dict()
    reopened = open_taint_history_store(
        root=root,
        authority_handle=HANDLE,
        trusted_anchor=restored_anchor,
    )
    assert reopened.current_anchor().to_dict() == latest_anchor.to_dict()
    tampered_transports = []
    for field, replacement in (
        ("ordered_log_root_sha256", "0" * 64),
        ("entry_count", latest_anchor.entry_count + 1),
        ("history_domain", HistoryDomain.LIFECYCLE.value),
        ("configuration_id", _handle(classifier="other-classifier").configuration_id.to_dict()),
    ):
        tampered = deepcopy(anchor_transport)
        tampered[field] = replacement
        tampered_transports.append(tampered)
    tampered_head = deepcopy(anchor_transport)
    tampered_head["domain_heads"][0] = f"{tampered_head['domain_heads'][0]}-changed"
    tampered_transports.append(tampered_head)
    tampered_id = deepcopy(anchor_transport)
    tampered_id["anchor_id"]["digest_sha256"] = "0" * 64
    tampered_transports.append(tampered_id)
    for tampered in tampered_transports:
        with pytest.raises(ContractViolation):
            history_anchor_from_dict(
                tampered,
                expected_history_domain=HistoryDomain.TAINT,
                expected_configuration_id=HANDLE.configuration_id,
            )
    with pytest.raises(TaintViolation) as missing_anchor:
        open_taint_history_store(root=root, authority_handle=HANDLE)
    assert missing_anchor.value.failure_code is TaintFailureCode.HISTORY_ANCHOR_REQUIRED
    fork_proposal = create_taint_change_proposal(
        authority_handle=HANDLE,
        current_profile=profile,
        proposed_taint_classes=profile.taint_classes,
        proposer_identity=ActorIdentity("different-proposer"),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    fork = create_taint_authority_decision(
        authority_handle=HANDLE,
        evaluator=EVALUATOR,
        proposal=fork_proposal,
        decision_kind=TaintDecisionKind.RETAIN_TAINT,
        removal_verifications=(),
        policy_refs=(_ref("taint-policy", RefKind.CONTRACT_CONDITION),),
    )
    with pytest.raises(TaintViolation) as fork_exc:
        store.append_decision(authority_handle=HANDLE, decision=fork)
    assert fork_exc.value.failure_code is TaintFailureCode.AUTHORITY_HISTORY_FORK
    journal = root / TAINT_HISTORY_JOURNAL_NAME_V1
    frames = scan_journal(journal).frames
    assert len(frames) == 2
    with journal.open("r+b") as stream:
        stream.truncate(frames[0].end_offset)
    with pytest.raises(TaintViolation) as rollback:
        open_taint_history_store(
            root=root,
            authority_handle=HANDLE,
            trusted_anchor=restored_anchor,
        )
    assert rollback.value.failure_code is TaintFailureCode.HISTORY_ROLLBACK
