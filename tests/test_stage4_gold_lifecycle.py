from __future__ import annotations

import hashlib
import json
import subprocess
import sys
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
    LifecycleReasonCode,
    ReasonCode,
    SchemaVersion,
    Stage4AuthorityHandle,
    create_history_anchor,
    create_independence_proof,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
    history_anchor_from_dict,
)
from synapse.experiments.gold.lifecycle import (
    LIFECYCLE_CONTEXT_V1,
    LIFECYCLE_JOURNAL_NAME_V1,
    ConfiguredLifecycleAuthorityEvaluator,
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleDecisionReason,
    LifecycleFailureCode,
    LifecycleScope,
    LifecycleSnapshot,
    LifecycleState,
    LifecycleViolation,
    SupersessionDecisionKind,
    configure_lifecycle_authority_evaluator,
    create_lifecycle_authority_proposal,
    create_revocation_decision,
    create_supersession_decision,
    open_lifecycle_store,
    validate_lifecycle_record,
    validate_lifecycle_history,
)
from synapse.experiments.gold.persistence import scan_journal


WRITER = ActorIdentity("platform-lifecycle-writer")
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def _authority_handle(*, governing_human: bool = True) -> Stage4AuthorityHandle:
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity("platform-attester"),
        builder_actor=ActorIdentity("platform-builder"),
        taint_classifier_authority=AuthorityIdentity("configured-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity("configured-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity("independent-lifecycle-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity("independent-revocation-reviewer"),
        lifecycle_writer_actor=WRITER,
        governing_human_authority=(
            AuthorityIdentity("governing-human") if governing_human else None
        ),
    )
    return create_stage4_authority_handle(configuration)


HANDLE = _authority_handle()
EVALUATOR = configure_lifecycle_authority_evaluator(
    authority_handle=HANDLE,
    policy_version="synapse.stage4.lifecycle-policy/v1",
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


def _context(scope: LifecycleScope = LifecycleScope.REVISION, context_id: str = "revision-001") -> LifecycleContext:
    return LifecycleContext(LIFECYCLE_CONTEXT_V1, scope, "GLOBAL" if scope is LifecycleScope.GLOBAL else context_id)


def _evidence(name: str = "lifecycle-evidence") -> tuple[HashBoundRef, ...]:
    return (_ref(name, RefKind.SOURCE_EVIDENCE),)


def _open_store(root, *, trusted_anchor=None, authority_handle: Stage4AuthorityHandle = HANDLE):
    return open_lifecycle_store(
        root=root,
        authority_handle=authority_handle,
        trusted_anchor=trusted_anchor,
        allow_genesis=trusted_anchor is None,
    )


def _append_path(
    store,
    subject: HashBoundRef,
    context: LifecycleContext,
    transitions,
    *,
    authority_handle: Stage4AuthorityHandle = HANDLE,
):
    predecessor = None
    sequence = 1
    records = []
    for state, reason in transitions:
        record = store.append(
            authority_handle=authority_handle,
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


def _authority_proposal(
    action: LifecycleAuthorityAction,
    subject: HashBoundRef,
    context: LifecycleContext,
    replacement: HashBoundRef | None = None,
    *,
    predecessor_decision_id: str | None = None,
    decision_sequence: int = 1,
):
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
        predecessor_decision_id=predecessor_decision_id,
        decision_sequence=decision_sequence,
    )


def _supersession_decision(
    proposal,
    *,
    decision_kind: SupersessionDecisionKind = SupersessionDecisionKind.SUPERSEDE,
    authority_handle: Stage4AuthorityHandle = HANDLE,
    evaluator: ConfiguredLifecycleAuthorityEvaluator = EVALUATOR,
):
    return create_supersession_decision(
        authority_handle=authority_handle,
        evaluator=evaluator,
        proposal=proposal,
        decision_kind=decision_kind,
    )


def _revocation_decision(
    proposal,
    *,
    authority_handle: Stage4AuthorityHandle = HANDLE,
    evaluator: ConfiguredLifecycleAuthorityEvaluator = EVALUATOR,
):
    return create_revocation_decision(
        authority_handle=authority_handle,
        evaluator=evaluator,
        proposal=proposal,
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
    store = _open_store(root)
    records = _append_path(store, subject, context, LINEAR_TRANSITIONS)
    assert len(records) == len(LINEAR_TRANSITIONS)
    assert records[-1].to_state is LifecycleState.OUTCOME_LINKED
    reopened = _open_store(root, trusted_anchor=store.current_anchor())
    assert [item.to_dict() for item in reopened.records()] == [item.to_dict() for item in records]
    assert reopened.require_consumable(subject_ref=subject, context=context).record_id == records[-1].record_id


def test_s4_p5_acc_lifecycle_02_unsupported_jump_wrong_reason_and_stale_recovery(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            authority_handle=HANDLE,
            subject_ref=subject, context=context, to_state=LifecycleState.ADMITTED,
            reason_code=LifecycleReasonCode.PUBLICATION_ADMITTED, evidence_refs=_evidence(),
            expected_predecessor_record_id=None, expected_subject_sequence=1,
        )
    assert exc.value.failure_code is LifecycleFailureCode.UNSUPPORTED_TRANSITION
    first = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.OBSERVED,
        reason_code=LifecycleReasonCode.PLATFORM_OBSERVATION, evidence_refs=_evidence(),
        expected_predecessor_record_id=None, expected_subject_sequence=1,
    )
    stale = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.STALE,
        reason_code=LifecycleReasonCode.STALE_CONTEXT, evidence_refs=_evidence("stale"),
        expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
    )
    recovered = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.REVALIDATED,
        reason_code=LifecycleReasonCode.REVALIDATION_RECOVERED, evidence_refs=_evidence("revalidated"),
        expected_predecessor_record_id=stale.record_id.value, expected_subject_sequence=3,
    )
    assert recovered.from_state is LifecycleState.STALE


def test_s4_p5_acc_lifecycle_03_compare_and_swap_rejects_concurrent_predecessor(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    first = _append_path(store, subject, context, LINEAR_TRANSITIONS[:1])[0]
    second = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.EXTRACTED,
        reason_code=LifecycleReasonCode.EXTRACTION_COMPLETED, evidence_refs=_evidence("second"),
        expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
    )
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            authority_handle=HANDLE,
            subject_ref=subject, context=context, to_state=LifecycleState.EXTRACTED,
            reason_code=LifecycleReasonCode.EXTRACTION_COMPLETED, evidence_refs=_evidence("racing"),
            expected_predecessor_record_id=first.record_id.value, expected_subject_sequence=2,
        )
    assert exc.value.failure_code is LifecycleFailureCode.CONCURRENT_UPDATE
    assert store.records()[-1].record_id == second.record_id


def test_s4_p5_acc_lifecycle_04_revocation_is_independent_terminal_and_blocks_consumption(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    admitted = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])[-1]
    assert store.require_consumable(subject_ref=subject, context=context).to_state is LifecycleState.ADMITTED
    proposal = _authority_proposal(LifecycleAuthorityAction.REVOKE, subject, context)
    decision = _revocation_decision(proposal)
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    revoked = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED, evidence_refs=_evidence("revocation-record"),
        expected_predecessor_record_id=admitted.record_id.value, expected_subject_sequence=7,
        revocation_decision=decision,
    )
    assert revoked.authority_identity.value == "independent-revocation-reviewer"
    with pytest.raises(LifecycleViolation) as exc:
        store.require_consumable(subject_ref=subject, context=context)
    assert exc.value.failure_code is LifecycleFailureCode.RECORD_NOT_CONSUMABLE
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            authority_handle=HANDLE,
            subject_ref=subject, context=context, to_state=LifecycleState.ADMITTED,
            reason_code=LifecycleReasonCode.PUBLICATION_ADMITTED, evidence_refs=_evidence("illegal-readmit"),
            expected_predecessor_record_id=revoked.record_id.value, expected_subject_sequence=8,
        )
    assert exc.value.failure_code is LifecycleFailureCode.UNSUPPORTED_TRANSITION


def test_s4_p5_acc_lifecycle_05_supersession_retains_old_history_and_never_selects_silent_latest(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("subject", RefKind.ARTIFACT)
    replacement = _ref("replacement", RefKind.ARTIFACT)
    context = _context()
    admitted_records = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])
    proposal = _authority_proposal(LifecycleAuthorityAction.SUPERSEDE, subject, context, replacement)
    decision = _supersession_decision(proposal)
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    superseded = store.append(
        authority_handle=HANDLE,
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
    handle_without_human = _authority_handle(governing_human=False)
    evaluator_without_human = configure_lifecycle_authority_evaluator(
        authority_handle=handle_without_human,
        policy_version="synapse.stage4.lifecycle-policy/v1",
        trusted_clock=lambda: NOW,
    )
    with pytest.raises(LifecycleViolation) as exc:
        create_supersession_decision(
            authority_handle=handle_without_human,
            evaluator=evaluator_without_human,
            proposal=_authority_proposal(
                LifecycleAuthorityAction.SUPERSEDE,
                subject,
                context,
                _ref("replacement", RefKind.ARTIFACT),
            ),
            decision_kind=SupersessionDecisionKind.SUPERSEDE,
        )
    assert exc.value.failure_code is LifecycleFailureCode.GOVERNING_HUMAN_UNAVAILABLE
    decision = _revocation_decision(proposal)
    store = _open_store(tmp_path / "lifecycle")
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    record = store.append(
        authority_handle=HANDLE,
        subject_ref=subject, context=context, to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED, evidence_refs=_evidence("global-revocation"),
        expected_predecessor_record_id=None, expected_subject_sequence=1, revocation_decision=decision,
    )
    assert record.context.scope is LifecycleScope.GLOBAL


def test_s4_p5_acc_lifecycle_07_snapshot_rejects_rollback_and_non_prefix_history(tmp_path) -> None:
    subject = _ref("subject", RefKind.ARTIFACT)
    context = _context()
    first_store = _open_store(tmp_path / "first")
    first = _append_path(first_store, subject, context, LINEAR_TRANSITIONS[:1])[0]
    snapshot_one = first_store.snapshot()
    first_store.append(
        authority_handle=HANDLE,
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
    short_store = _open_store(tmp_path / "short")
    _append_path(short_store, subject, context, LINEAR_TRANSITIONS[:1])
    with pytest.raises(LifecycleViolation) as exc:
        short_store.snapshot(trusted_prior=snapshot_two)
    assert exc.value.failure_code is LifecycleFailureCode.HISTORY_ROLLBACK


def test_s4_p5_acc_lifecycle_08_history_validator_rejects_duplicate_or_forked_record(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    record = _append_path(store, _ref("subject", RefKind.ARTIFACT), _context(), LINEAR_TRANSITIONS[:1])[0]
    with pytest.raises(LifecycleViolation) as exc:
        validate_lifecycle_history(
            (record, record),
            expected_platform_writer=WRITER,
            expected_configuration_id=HANDLE.configuration_id,
            authority_handle=HANDLE,
        )
    assert exc.value.failure_code is LifecycleFailureCode.HISTORY_FORK


def test_s4_p5_acc_lifecycle_09_worker_or_writer_cannot_self_approve_exception() -> None:
    with pytest.raises(ValueError):
        create_stage4_authority_configuration(
            platform_attester_actor=ActorIdentity("platform-attester"),
            builder_actor=ActorIdentity("platform-builder"),
            taint_classifier_authority=AuthorityIdentity("configured-taint-classifier"),
            taint_reviewer_authority=AuthorityIdentity("configured-taint-reviewer"),
            supersession_reviewer_authority=AuthorityIdentity("independent-lifecycle-reviewer"),
            revocation_reviewer_authority=AuthorityIdentity(WRITER.value),
            lifecycle_writer_actor=WRITER,
            governing_human_authority=AuthorityIdentity("governing-human"),
        )


def test_s4_p5_followup_lifecycle_01_complete_transition_matrix(tmp_path) -> None:
    linear_store = _open_store(tmp_path / "linear")
    linear_records = _append_path(
        linear_store,
        _ref("linear-subject", RefKind.ARTIFACT),
        _context(),
        LINEAR_TRANSITIONS,
    )
    assert tuple(record.to_state for record in linear_records) == tuple(
        state for state, _ in LINEAR_TRANSITIONS
    )

    exception_transitions = (
        (LifecycleState.REJECTED, LifecycleReasonCode.POLICY_REJECTED),
        (LifecycleState.CONFLICTING, LifecycleReasonCode.CONFLICT_DETECTED),
        (LifecycleState.STALE, LifecycleReasonCode.STALE_CONTEXT),
        (LifecycleState.INCOMPATIBLE, LifecycleReasonCode.CONTEXT_INCOMPATIBLE),
        (LifecycleState.QUARANTINED, LifecycleReasonCode.CORRUPTION_QUARANTINE),
    )
    observed_states = []
    for index, (state, reason) in enumerate(exception_transitions):
        store = _open_store(tmp_path / f"exception-{index}")
        record = store.append(
            authority_handle=HANDLE,
            subject_ref=_ref(f"exception-subject-{index}", RefKind.ARTIFACT),
            context=_context(),
            to_state=state,
            reason_code=reason,
            evidence_refs=_evidence(f"exception-evidence-{index}"),
            expected_predecessor_record_id=None,
            expected_subject_sequence=1,
        )
        observed_states.append(record.to_state)
    assert tuple(observed_states) == tuple(state for state, _ in exception_transitions)

    stale_store = _open_store(tmp_path / "stale-recovery")
    stale_subject = _ref("stale-subject", RefKind.ARTIFACT)
    stale = stale_store.append(
        authority_handle=HANDLE,
        subject_ref=stale_subject,
        context=_context(),
        to_state=LifecycleState.STALE,
        reason_code=LifecycleReasonCode.STALE_CONTEXT,
        evidence_refs=_evidence("stale-matrix"),
        expected_predecessor_record_id=None,
        expected_subject_sequence=1,
    )
    recovered = stale_store.append(
        authority_handle=HANDLE,
        subject_ref=stale_subject,
        context=_context(),
        to_state=LifecycleState.REVALIDATED,
        reason_code=LifecycleReasonCode.REVALIDATION_RECOVERED,
        evidence_refs=_evidence("recovery-matrix"),
        expected_predecessor_record_id=stale.record_id.value,
        expected_subject_sequence=2,
    )
    assert recovered.from_state is LifecycleState.STALE
    assert recovered.to_state is LifecycleState.REVALIDATED


def test_s4_p5_followup_lifecycle_02_valid_prefix_rollback_is_rejected_against_trusted_anchor(tmp_path) -> None:
    root = tmp_path / "lifecycle"
    store = _open_store(root)
    subject = _ref("rollback-subject", RefKind.ARTIFACT)
    _append_path(store, subject, _context(), LINEAR_TRANSITIONS[:2])
    trusted_anchor = store.current_anchor()
    assert trusted_anchor.history_domain is HistoryDomain.LIFECYCLE
    assert trusted_anchor.entry_count == 2
    anchor_transport = json.loads(json.dumps(trusted_anchor.to_dict()))
    restored_anchor = history_anchor_from_dict(
        anchor_transport,
        expected_history_domain=HistoryDomain.LIFECYCLE,
        expected_configuration_id=HANDLE.configuration_id,
    )
    assert restored_anchor is not trusted_anchor
    assert restored_anchor.to_dict() == trusted_anchor.to_dict()
    assert _open_store(root, trusted_anchor=restored_anchor).current_anchor().to_dict() == trusted_anchor.to_dict()
    tampered_transports = []
    for field, replacement in (
        ("ordered_log_root_sha256", "0" * 64),
        ("entry_count", trusted_anchor.entry_count + 1),
        ("history_domain", HistoryDomain.PROVENANCE.value),
        ("configuration_id", _authority_handle(governing_human=False).configuration_id.to_dict()),
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
                expected_history_domain=HistoryDomain.LIFECYCLE,
                expected_configuration_id=HANDLE.configuration_id,
            )
    configuration_path = tmp_path / "authority-configuration.json"
    anchor_path = tmp_path / "history-anchor.json"
    configuration_path.write_text(
        json.dumps(HANDLE.configuration.to_dict()),
        encoding="utf-8",
    )
    anchor_path.write_text(json.dumps(anchor_transport), encoding="utf-8")
    child_code = """
import json
import sys
from pathlib import Path
from synapse.experiments.gold.contracts import (
    HistoryDomain,
    create_stage4_authority_handle,
    history_anchor_from_dict,
    stage4_authority_configuration_from_dict,
)
from synapse.experiments.gold.lifecycle import open_lifecycle_store

root = Path(sys.argv[1])
configuration = stage4_authority_configuration_from_dict(
    json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
)
handle = create_stage4_authority_handle(configuration)
anchor = history_anchor_from_dict(
    json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")),
    expected_history_domain=HistoryDomain.LIFECYCLE,
    expected_configuration_id=handle.configuration_id,
)
reopened = open_lifecycle_store(
    root=root,
    authority_handle=handle,
    trusted_anchor=anchor,
)
assert len(reopened.records()) == 2
assert reopened.current_anchor().to_dict() == anchor.to_dict()
"""
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            child_code,
            str(root),
            str(configuration_path),
            str(anchor_path),
        ],
        timeout=30,
        check=True,
    )
    journal = root / LIFECYCLE_JOURNAL_NAME_V1
    frames = scan_journal(journal).frames
    assert len(frames) == 2
    with journal.open("r+b") as stream:
        stream.truncate(frames[0].end_offset)
    with pytest.raises(LifecycleViolation) as exc:
        _open_store(root, trusted_anchor=restored_anchor)
    assert exc.value.failure_code is LifecycleFailureCode.HISTORY_ROLLBACK


def test_s4_p5_followup_lifecycle_03_supersession_decision_schema_and_negative_outcomes_are_persisted(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("negative-subject", RefKind.ARTIFACT)
    replacement = _ref("negative-replacement", RefKind.ARTIFACT)
    context = _context()
    rejected_proposal = _authority_proposal(
        LifecycleAuthorityAction.SUPERSEDE,
        subject,
        context,
        replacement,
    )
    rejected = _supersession_decision(
        rejected_proposal,
        decision_kind=SupersessionDecisionKind.REJECT_SUPERSESSION,
    )
    store.persist_authority_decision(authority_handle=HANDLE, decision=rejected)
    review_proposal = _authority_proposal(
        LifecycleAuthorityAction.SUPERSEDE,
        subject,
        context,
        replacement,
        predecessor_decision_id=rejected.decision_id.record_id.value,
        decision_sequence=2,
    )
    needs_human = _supersession_decision(
        review_proposal,
        decision_kind=SupersessionDecisionKind.REQUIRE_HUMAN_REVIEW,
    )
    anchor = store.persist_authority_decision(
        authority_handle=HANDLE,
        decision=needs_human,
    )
    assert rejected.schema_version is SchemaVersion.SUPERSESSION_AUTHORITY_DECISION_V1
    assert rejected.reason_code is LifecycleDecisionReason.SUPERSESSION_REJECTED
    assert needs_human.reason_code is LifecycleDecisionReason.HUMAN_REVIEW_REQUIRED
    assert rejected.configuration_id == HANDLE.configuration_id
    assert rejected.policy_version == "synapse.stage4.lifecycle-policy/v1"
    assert store.records() == ()
    assert store.authority_decisions() == (rejected, needs_human)
    assert anchor.entry_count == 2


def test_s4_p5_followup_lifecycle_04_supersession_preserves_history_and_is_scoped(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("scoped-subject", RefKind.ARTIFACT)
    first_context = _context(context_id="revision-a")
    second_context = _context(context_id="revision-b")
    first_history = _append_path(store, subject, first_context, LINEAR_TRANSITIONS[:6])
    second_history = _append_path(store, subject, second_context, LINEAR_TRANSITIONS[:6])
    replacement = _ref("scoped-replacement", RefKind.ARTIFACT)
    proposal = _authority_proposal(
        LifecycleAuthorityAction.SUPERSEDE,
        subject,
        first_context,
        replacement,
    )
    decision = _supersession_decision(proposal)
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    superseded = store.append(
        authority_handle=HANDLE,
        subject_ref=subject,
        context=first_context,
        to_state=LifecycleState.SUPERSEDED,
        reason_code=LifecycleReasonCode.SUPERSESSION_APPROVED,
        evidence_refs=_evidence("scoped-supersession"),
        expected_predecessor_record_id=first_history[-1].record_id.value,
        expected_subject_sequence=7,
        supersession_decision=decision,
    )
    records = store.records()
    assert records[:6] == tuple(first_history)
    assert records[6:12] == tuple(second_history)
    assert records[-1] == superseded
    with pytest.raises(LifecycleViolation) as exc:
        store.require_consumable(subject_ref=subject, context=first_context)
    assert exc.value.failure_code is LifecycleFailureCode.RECORD_NOT_CONSUMABLE
    assert store.require_consumable(subject_ref=subject, context=second_context).to_state is LifecycleState.ADMITTED


def test_s4_p5_followup_lifecycle_05_consumer_revalidates_nested_decision_before_transition_dispatch(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("nested-subject", RefKind.ARTIFACT)
    context = _context()
    admitted = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])[-1]
    proposal = _authority_proposal(
        LifecycleAuthorityAction.SUPERSEDE,
        subject,
        context,
        _ref("nested-replacement", RefKind.ARTIFACT),
    )
    decision = _supersession_decision(proposal)
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    record = store.append(
        authority_handle=HANDLE,
        subject_ref=subject,
        context=context,
        to_state=LifecycleState.SUPERSEDED,
        reason_code=LifecycleReasonCode.SUPERSESSION_APPROVED,
        evidence_refs=_evidence("nested-decision"),
        expected_predecessor_record_id=admitted.record_id.value,
        expected_subject_sequence=7,
        supersession_decision=decision,
    )
    nested = record.supersession_decision
    assert nested is not None
    original_configuration_id = nested.configuration_id
    object.__setattr__(nested, "configuration_id", record.record_id)
    try:
        with pytest.raises(LifecycleViolation) as exc:
            validate_lifecycle_record(
                record,
                expected_configuration_id=HANDLE.configuration_id,
                expected_platform_writer=WRITER,
                authority_handle=HANDLE,
            )
        assert exc.value.failure_code is LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH
    finally:
        object.__setattr__(nested, "configuration_id", original_configuration_id)


def test_s4_p5_followup_lifecycle_06_same_revoked_subject_cannot_be_readmitted(tmp_path) -> None:
    store = _open_store(tmp_path / "lifecycle")
    subject = _ref("revoked-subject", RefKind.ARTIFACT)
    context = _context()
    admitted = _append_path(store, subject, context, LINEAR_TRANSITIONS[:6])[-1]
    proposal = _authority_proposal(LifecycleAuthorityAction.REVOKE, subject, context)
    decision = _revocation_decision(proposal)
    store.persist_authority_decision(authority_handle=HANDLE, decision=decision)
    revoked = store.append(
        authority_handle=HANDLE,
        subject_ref=subject,
        context=context,
        to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
        evidence_refs=_evidence("revoked-terminal"),
        expected_predecessor_record_id=admitted.record_id.value,
        expected_subject_sequence=7,
        revocation_decision=decision,
    )
    with pytest.raises(LifecycleViolation) as exc:
        store.append(
            authority_handle=HANDLE,
            subject_ref=subject,
            context=context,
            to_state=LifecycleState.ADMITTED,
            reason_code=LifecycleReasonCode.PUBLICATION_ADMITTED,
            evidence_refs=_evidence("forbidden-readmission"),
            expected_predecessor_record_id=revoked.record_id.value,
            expected_subject_sequence=8,
        )
    assert exc.value.failure_code is LifecycleFailureCode.UNSUPPORTED_TRANSITION
    assert store.current_state(subject_ref=subject, context=context) is LifecycleState.REVOKED


def test_s4_p5_followup_lifecycle_07_concurrent_fork_never_selects_latest(tmp_path) -> None:
    root = tmp_path / "lifecycle"
    bootstrap = _open_store(root)
    subject = _ref("fork-subject", RefKind.ARTIFACT)
    context = _context()
    first = _append_path(bootstrap, subject, context, LINEAR_TRANSITIONS[:1])[0]
    anchor = bootstrap.current_anchor()
    first_writer = _open_store(root, trusted_anchor=anchor)
    stale_writer = _open_store(root, trusted_anchor=anchor)
    winner = first_writer.append(
        authority_handle=HANDLE,
        subject_ref=subject,
        context=context,
        to_state=LifecycleState.STALE,
        reason_code=LifecycleReasonCode.STALE_CONTEXT,
        evidence_refs=_evidence("fork-winner"),
        expected_predecessor_record_id=first.record_id.value,
        expected_subject_sequence=2,
    )
    with pytest.raises(LifecycleViolation) as exc:
        stale_writer.append(
            authority_handle=HANDLE,
            subject_ref=subject,
            context=context,
            to_state=LifecycleState.CONFLICTING,
            reason_code=LifecycleReasonCode.CONFLICT_DETECTED,
            evidence_refs=_evidence("fork-loser"),
            expected_predecessor_record_id=first.record_id.value,
            expected_subject_sequence=2,
        )
    assert exc.value.failure_code is LifecycleFailureCode.CONCURRENT_UPDATE
    assert stale_writer.records()[-1].record_id == winner.record_id
    assert stale_writer.current_state(subject_ref=subject, context=context) is LifecycleState.STALE
