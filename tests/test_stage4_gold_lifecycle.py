from __future__ import annotations

import hashlib

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    LifecycleReasonCode,
    ReasonCode,
    SchemaVersion,
    create_independence_proof,
)
from synapse.experiments.gold.lifecycle import (
    LIFECYCLE_CONTEXT_V1,
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleFailureCode,
    LifecycleScope,
    LifecycleSnapshot,
    LifecycleState,
    LifecycleViolation,
    create_lifecycle_authority_proposal,
    create_revocation_decision,
    create_supersession_decision,
    open_lifecycle_store,
    validate_lifecycle_history,
)


WRITER = ActorIdentity("platform-lifecycle-writer")


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


def _context(scope: LifecycleScope = LifecycleScope.REVISION, context_id: str = "revision-001") -> LifecycleContext:
    return LifecycleContext(LIFECYCLE_CONTEXT_V1, scope, "GLOBAL" if scope is LifecycleScope.GLOBAL else context_id)


def _evidence(name: str = "lifecycle-evidence") -> tuple[HashBoundRef, ...]:
    return (_ref(name, RefKind.SOURCE_EVIDENCE),)


def _append_path(store, subject: HashBoundRef, context: LifecycleContext, transitions):
    predecessor = None
    sequence = 1
    records = []
    for state, reason in transitions:
        record = store.append(
            subject_ref=subject,
            context=context,
            to_state=state,
            reason_code=reason,
            evidence_refs=_evidence(f"evidence-{sequence}"),
            expected_predecessor_record_id=predecessor,
            expected_subject_sequence=sequence,
        )
        records.append(record)
        predecessor = record.record_id.value
        sequence += 1
    return records


def _authority_proposal(action: LifecycleAuthorityAction, subject: HashBoundRef, context: LifecycleContext, replacement: HashBoundRef | None = None):
    return create_lifecycle_authority_proposal(
        action=action,
        subject_ref=subject,
        replacement_ref=replacement,
        context=context,
        proposer_identity=ActorIdentity("lifecycle-policy-proposer"),
        producer_actor_ids=(WRITER,),
        source_actor_ids=(ActorIdentity("behavior-producer"),),
        evidence_refs=_evidence("authority-evidence"),
        compatibility_refs=() if replacement is None else _evidence("compatibility-evidence"),
        policy_refs=(_ref("lifecycle-policy", RefKind.CONTRACT_CONDITION),),
        reason_codes=("governing-policy",),
        predecessor_decision_id=None,
        decision_sequence=1,
    )


def _proof(proposal, role: AuthorityRole, *, authority: str = "independent-lifecycle-reviewer"):
    reason = {
        AuthorityRole.SUPERSESSION_REVIEWER: ReasonCode.SUPERSESSION_REVIEW_INDEPENDENT,
        AuthorityRole.REVOCATION_REVIEWER: ReasonCode.REVOCATION_REVIEW_INDEPENDENT,
        AuthorityRole.GOVERNING_HUMAN: ReasonCode.GOVERNING_HUMAN_INDEPENDENT,
    }[role]
    return create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal.proposal_id,
        authority_identity=AuthorityIdentity(authority),
        authority_role=role,
        reason_code=reason,
        producer_actor_ids=proposal.producer_actor_ids,
        source_actor_ids=proposal.source_actor_ids,
        proposer_identity=proposal.proposer_identity,
        executor_identity=None,
        subject_derived_actor_ids=(),
        delegation_chain=(),
    )


LINEAR_TRANSITIONS = (
    (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
    (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
    (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
    (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
    (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
    (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    (LifecycleState.INDEXED, LifecycleReasonCode.INDEX_COMMITTED),
    (LifecycleState.RETRIEVED, LifecycleReasonCode.RETRIEVAL_SELECTED),
    (LifecycleState.REVALIDATED, LifecycleReasonCode.REVALIDATION_PASSED),
    (LifecycleState.REPLAYED, LifecycleReasonCode.REPLAY_COMPLETED),
    (LifecycleState.CONSUMED, LifecycleReasonCode.CONSUMPTION_COMPLETED),
    (LifecycleState.OUTCOME_LINKED, LifecycleReasonCode.OUTCOME_LINKED),
)


def test_s4_p5_acc_lifecycle_01_complete_transition_path_is_durable_and_reconstructable(tmp_path) -> None:
    root = tmp_path / "lifecycle"
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    store = open_lifecycle_store(root=root, platform_writer_identity=WRITER)
    records = _append_path(store, subject, context, LINEAR_TRANSITIONS)
    assert len(records) == len(LINEAR_TRANSITIONS)
    assert records[-1].to_state is LifecycleState.OUTCOME_LINKED
    reopened = open_lifecycle_store(root=root, platform_writer_identity=WRITER)
    assert [item.to_dict() for item in reopened.records()] == [item.to_dict() for item in records]
    assert reopened.require_consumable(subject_ref=subject, context=context).record_id == records[-1].record_id


def test_s4_p5_acc_lifecycle_02_unsupported_jump_wrong_reason_and_stale_recovery(tmp_path) -> None:
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            subject_ref=subject, context=context, to_state=LifecycleState.ADMITTED,
            reason_code=LifecycleReasonCode.PUBLICATION_ADMITTED, evidence_refs=_evidence(),
            expected_predecessor_record_id=None, expected_subject_sequence=1,
        )
    assert exc.value.failure_code is LifecycleFailureCode.UNSUPPORTED_TRANSITION
    first = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.OBSERVED,
        reason_code=LifecycleReasonCode.PLATFORM_OBSERVATION, evidence_refs=_evidence(),
        expected_predecessor_record_id=None, expected_subject_sequence=1,
    )
    stale = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.STALE,
        reason_code=LifecycleReasonCode.STALE_CONTEXT, evidence_refs=_evidence("stale"),
        expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
    )
    recovered = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.REVALIDATED,
        reason_code=LifecycleReasonCode.REVALIDATION_RECOVERED, evidence_refs=_evidence("revalidated"),
        expected_predecessor_record_id=stale.record_id.value, expected_subject_sequence=3,
    )
    assert recovered.from_state is LifecycleState.STALE


def test_s4_p5_acc_lifecycle_03_compare_and_swap_rejects_concurrent_predecessor(tmp_path) -> None:
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    first = _append_path(store, subject, context, LINEAR_TRANSITIONS[:1])[0]
    second = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.EXTRACTED,
        reason_code=LifecycleReasonCode.EXTRACTION_COMPLETED, evidence_refs=_evidence("second"),
        expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
    )
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            subject_ref=subject, context=context, to_state=LifecycleState.EXTRACTED,
            reason_code=LifecycleReasonCode.EXTRACTION_COMPLETED, evidence_refs=_evidence("racing"),
            expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
        )
    assert exc.value.failure_code is LifecycleFailureCode.CONCURRENT_UPDATE
    assert store.records()[-1].record_id == second.record_id


def test_s4_p5_acc_lifecycle_04_revocation_is_independent_terminal_and_blocks_consumption(tmp_path) -> None:
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    admitted = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])[-1]
    assert store.require_consumable(subject_ref=subject, context=context).to_state is LifecycleState.ADMITTED
    proposal = _authority_proposal(LifecycleAuthorityAction.REVOKE, subject, context)
    decision = create_revocation_decision(proposal=proposal, independence_proof=_proof(proposal, AuthorityRole.REVOCATION_REVIEWER))
    revoked = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED, evidence_refs=_evidence("revocation-record"),
        expected_predecessor_record_id=admitted.record_id.value, expected_subject_sequence=7,
        revocation_decision=decision,
    )
    assert revoked.authority_identity.value == "independent-lifecycle-reviewer"
    with pytest.raises(LifecycleViolation) as exc:
        store.require_consumable(subject_ref=subject, context=context)
    assert exc.value.failure_code is LifecycleFailureCode.RECORD_NOT_CONSUMABLE
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            subject_ref=subject, context=context, to_state=LifecycleState.ADMITTED,
            reason_code=LifecycleReasonCode.PUBLICATION_ADMITTED, evidence_refs=_evidence("illegal-readmit"),
            expected_predecessor_record_id=revoked.record_id.value, expected_subject_sequence=8,
        )
    assert exc.value.failure_code is LifecycleFailureCode.UNSUPPORTED_TRANSITION


def test_s4_p5_acc_lifecycle_05_supersession_retains_old_history_and_never_selects_silent_latest(tmp_path) -> None:
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    subject = _ref("subject", RefKind.ARTIFACT)
    replacement = _ref("replacement", RefKind.ARTIFACT)
    context = _context()
    admitted_records = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])
    proposal = _authority_proposal(LifecycleAuthorityAction.SUPERSEDE, subject, context, replacement)
    decision = create_supersession_decision(proposal=proposal, independence_proof=_proof(proposal, AuthorityRole.SUPERSESSION_REVIEWER))
    superseded = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.SUPERSEDED,
        reason_code=LifecycleReasonCode.SUPERSESSION_APPROVED, evidence_refs=_evidence("supersession-record"),
        expected_predecessor_record_id=admitted_records[-1].record_id.value, expected_subject_sequence=7,
        supersession_decision=decision,
    )
    history = store.records()
    assert len(history) == 7
    assert history[:6] == tuple(admitted_records)
    assert superseded.supersession_decision.proposal.replacement_ref == replacement
    with pytest.raises(LifecycleViolation):
        store.require_consumable(subject_ref=subject, context=context)


def test_s4_p5_acc_lifecycle_06_global_decision_requires_governing_human_authority(tmp_path) -> None:
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context(LifecycleScope.GLOBAL)
    proposal = _authority_proposal(LifecycleAuthorityAction.REVOKE, subject, context)
    with pytest.raises(LifecycleViolation) as exc:
        create_revocation_decision(proposal=proposal, independence_proof=_proof(proposal, AuthorityRole.REVOCATION_REVIEWER))
    assert exc.value.failure_code is LifecycleFailureCode.GLOBAL_HUMAN_AUTHORITY_REQUIRED
    decision = create_revocation_decision(
        proposal=proposal,
        independence_proof=_proof(proposal, AuthorityRole.GOVERNING_HUMAN, authority="governing-human"),
    )
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    record = store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED, evidence_refs=_evidence("global-revocation"),
        expected_predecessor_record_id=None, expected_subject_sequence=1, revocation_decision=decision,
    )
    assert record.context.scope is LifecycleScope.GLOBAL


def test_s4_p5_acc_lifecycle_07_snapshot_rejects_rollback_and_non_prefix_history(tmp_path) -> None:
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    first_store = open_lifecycle_store(root=tmp_path / "first", platform_writer_identity=WRITER)
    first = _append_path(first_store, subject, context, LINEAR_TRANSITIONS[:1])[0]
    snapshot_one = first_store.snapshot()
    first_store.append(
        subject_ref=subject, context=context, to_state=LifecycleState.EXTRACTED,
        reason_code=LifecycleReasonCode.EXTRACTION_COMPLETED, evidence_refs=_evidence("second"),
        expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
    )
    snapshot_two = first_store.snapshot(trusted_prior=snapshot_one)
    assert snapshot_two.record_count == 2
    assert LifecycleSnapshot.from_dict(
        snapshot_two.to_dict(),
        records=first_store.records(),
        expected_platform_writer=WRITER,
        trusted_prior=snapshot_one,
    ).snapshot_id == snapshot_two.snapshot_id
    short_store = open_lifecycle_store(root=tmp_path / "short", platform_writer_identity=WRITER)
    _append_path(short_store, subject, context, LINEAR_TRANSITIONS[:1])
    with pytest.raises(LifecycleViolation) as exc:
        short_store.snapshot(trusted_prior=snapshot_two)
    assert exc.value.failure_code is LifecycleFailureCode.HISTORY_ROLLBACK


def test_s4_p5_acc_lifecycle_08_history_validator_rejects_duplicate_or_forked_record(tmp_path) -> None:
    store = open_lifecycle_store(root=tmp_path / "lifecycle", platform_writer_identity=WRITER)
    record = _append_path(store, _ref("subject", RefKind.ARTIFACT), _context(), LINEAR_TRANSITIONS[:1])[0]
    with pytest.raises(LifecycleViolation) as exc:
        validate_lifecycle_history((record, record), expected_platform_writer=WRITER)
    assert exc.value.failure_code is LifecycleFailureCode.HISTORY_FORK


def test_s4_p5_acc_lifecycle_09_worker_or_writer_cannot_self_approve_exception() -> None:
    proposal = _authority_proposal(LifecycleAuthorityAction.REVOKE, _ref("subject", RefKind.ARTIFACT), _context())
    with pytest.raises(ValueError):
        _proof(proposal, AuthorityRole.REVOCATION_REVIEWER, authority=WRITER.value)
