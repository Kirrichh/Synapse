"""Continuation acceptance: progress is semantic, not attempt-record identity."""

from __future__ import annotations

from types import SimpleNamespace

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.runner.attempt_knowledge import (
    ContinuationBasisKind,
    ContinuationOutcome,
    create_attempt_knowledge_basis,
    decide_completed_attempt_continuation,
    prior_attempt_evidence_from_result,
)


def _ref(name: str, digest: str) -> HashBoundRef:
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=name,
        schema_id="synapse.stage4.gold.acceptance-artifact/v1",
        sha256=digest,
        byte_length=1,
        media_type="application/json",
    )


def _basis(
    attempt_index: int,
    *,
    subject: HashBoundRef,
    revalidation_sha256: str,
    admitted: bool = True,
):
    return create_attempt_knowledge_basis(
        run_id="continuation-basis-run",
        attempt_id=str(attempt_index),
        attempt_index=attempt_index,
        admitted_subject_refs=(subject,),
        retrieval_gate_decision_ref=_ref(f"gate-{attempt_index}", f"{attempt_index:064x}"),
        consumer_context_ref=_ref("consumer-context", "c" * 64),
        boundary_ref=_ref("boundary", "b" * 64),
        policy_version="policy-v1",
        point_of_use_admitted=admitted,
        revalidation_semantic_sha256s=(revalidation_sha256,) if admitted else (),
    )


def _finding(attempt_index: int, *, result_sha256: str):
    result = SimpleNamespace(
        result_sha256=result_sha256,
        worker_result_ref=_ref(f"worker-{attempt_index}", "1" * 64),
        c1_result_ref=_ref(f"c1-{attempt_index}", "2" * 64),
        oracle_result_ref=None,
        c1_status="NO_CANDIDATE",
        oracle_invoked=False,
        oracle_resolved=None,
    )
    return prior_attempt_evidence_from_result(
        result,
        attempt_index=attempt_index,
        accepted_plan_ref=_ref(f"plan-{attempt_index}", "3" * 64),
        plan_semantic_sha256="4" * 64,
    )


def _decide(previous, current, *, previous_finding, current_finding):
    return decide_completed_attempt_continuation(
        run_id="continuation-basis-run",
        attempt_index=current.attempt_index,
        current_basis=current,
        current_basis_sha256=current.digest(),
        current_finding=current_finding,
        previous_basis=previous,
        previous_basis_sha256=previous.digest(),
        previous_finding=previous_finding,
    )


def test_same_subject_same_stage3_semantics_is_not_new_knowledge() -> None:
    subject = _ref("subject", "5" * 64)
    previous = _basis(1, subject=subject, revalidation_sha256="6" * 64)
    current = _basis(2, subject=subject, revalidation_sha256="6" * 64)

    evidence = _decide(
        previous,
        current,
        previous_finding=_finding(1, result_sha256="7" * 64),
        current_finding=_finding(2, result_sha256="8" * 64),
    )

    assert evidence.outcome is ContinuationOutcome.NO_CONTINUATION_BASIS
    assert evidence.basis_kinds == (ContinuationBasisKind.NONE.value,)


def test_same_subject_changed_stage3_semantics_is_revalidated_knowledge() -> None:
    subject = _ref("subject", "5" * 64)
    previous = _basis(1, subject=subject, revalidation_sha256="6" * 64)
    current = _basis(2, subject=subject, revalidation_sha256="9" * 64)

    evidence = _decide(
        previous,
        current,
        previous_finding=_finding(1, result_sha256="7" * 64),
        current_finding=_finding(2, result_sha256="8" * 64),
    )

    assert evidence.outcome is ContinuationOutcome.CONTINUATION_BASIS
    assert evidence.basis_kinds == (
        ContinuationBasisKind.NEW_REVALIDATED_KNOWLEDGE.value,
    )
    assert evidence.added_subject_refs == ()


def test_failed_point_of_use_does_not_promote_subject_to_new_knowledge() -> None:
    subject = _ref("subject", "5" * 64)
    previous = _basis(1, subject=subject, revalidation_sha256="6" * 64)
    current = _basis(
        2,
        subject=_ref("new-subject", "a" * 64),
        revalidation_sha256="9" * 64,
        admitted=False,
    )

    evidence = _decide(
        previous,
        current,
        previous_finding=_finding(1, result_sha256="7" * 64),
        current_finding=_finding(2, result_sha256="8" * 64),
    )

    assert evidence.outcome is ContinuationOutcome.NO_CONTINUATION_BASIS
    assert ContinuationBasisKind.NEW_ADMITTED_KNOWLEDGE.value not in evidence.basis_kinds
    assert ContinuationBasisKind.NEW_REVALIDATED_KNOWLEDGE.value not in evidence.basis_kinds
