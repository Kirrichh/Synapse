"""Projection of domain evidence into the typed findings the §22 gates read.

This is an adapter attached to the admission owner, and it exists to fix a
dependency that ran the wrong way.

The four gates need compatibility and taint stated in their own narrow
vocabulary — ``CompatibilityFinding``, ``TaintFinding`` — and something has to
convert the domain owners' rich records into those. An earlier revision put the
conversion inside the domain owners themselves, so ``compatibility.py`` and
``taint.py`` each imported ``admission``. That inverts the adapter-first
direction: stage 6 compatibility and stage 5 taint are earlier owners, and
making them import the stage 8 consumer means an earlier contour cannot be
built, reasoned about or changed without the later one's vocabulary. It also
put a latent cycle one import away.

The conversion belongs to whoever needs the conversion. So it lives here: this
module imports ``compatibility``, ``taint`` and ``admission``, and neither
domain owner imports the gates. The direction is now what the model asks for —
the late layer depends on the early owners, not the reverse.

Nothing here decides anything. The domain owners establish compatibility and
effective taint; the gates decide admission; this module only restates the
former in the vocabulary of the latter, losing detail in one direction and
adding no authority in either.
"""

from __future__ import annotations

from .canonicalization import HashBoundRef
from .admission import CompatibilityFinding, TaintFinding
from .compatibility import (
    CompatibilityConflictScan,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityFailureCode,
    CompatibilityRevalidationRecord,
    ConflictDecisionKind,
    EvidenceCompleteness,
    RevalidationOutcome,
    RevalidationStage,
    validate_compatibility_conflict_scan,
    validate_compatibility_decision,
    validate_compatibility_revalidation_record,
)
from .taint import EffectiveTaint, TaintFailureCode, effective_taint_blocks

#: The non-public surface this adapter takes from the owners it projects
#: between. Declared for the same reason ``point_of_use.ADAPTER_PRIVATE_SEAM``
#: is: a shared internal is a decision, and a decision that is written down can
#: be reviewed when it changes.
ADAPTER_PRIVATE_SEAM = ("_context", "_evaluator")


def _compat_fail(code: CompatibilityFailureCode, detail: str) -> Exception:
    from .compatibility import CompatibilityViolation

    return CompatibilityViolation(code, detail)


def _taint_fail(code: TaintFailureCode, detail: str) -> Exception:
    from .taint import TaintViolation

    return TaintViolation(code, detail)


def consumption_finding_from_revalidation(
    value: CompatibilityRevalidationRecord,
    *,
    decision: CompatibilityDecision,
    conflict_scan: CompatibilityConflictScan,
    subject_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
) -> CompatibilityFinding:
    """State a compatibility revalidation in the vocabulary the gates read.

    The projection is deliberately lossy in one direction only: it exposes
    whether the evidence is complete, whether the subject is compatible, whether
    state drifted since the original decision and whether a conflict remains
    unresolved, and it exposes nothing that would let a gate infer an admission
    from a stale status.

    A revalidation that did not reach the consumption stage is refused rather
    than read as "not drifted": absence of a fresh check is not evidence of
    stability.

    ``conflict_scan`` is required for the same reason, and it used to default to
    ``None``. That default was a fail-open — a caller that ran no scan produced
    ``conflicts_unresolved=False``, so *missing evidence* and *evidence of no
    conflict* became one value. Conflict is a dimension the consumption gate
    declares it checked, and NR-10 forbids exactly this substitution: absent,
    unknown and false are not interchangeable. A caller with nothing to scan
    says so with a scan whose decision kind says so; it cannot say it by
    omission.

    The finding also names the subject and the frozen consumer context it is
    about, so the gate can check that the answer belongs to the question.
    """

    validate_compatibility_revalidation_record(value)
    if value.stage is not RevalidationStage.BEFORE_CONSUMPTION:
        raise _compat_fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "consumption evidence requires a stage-3 revalidation record",
        )
    if type(decision) is not CompatibilityDecision:
        raise _compat_fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "consumption evidence requires an exact decision",
        )
    validate_compatibility_decision(
        decision, evaluator=decision._evaluator, context=value._context, descriptor=None
    )
    if value.original_decision_id != decision.decision_id:
        raise _compat_fail(
            CompatibilityFailureCode.AUTHORITY_DECISION_INVALID,
            "revalidation does not belong to the supplied decision",
        )
    if type(conflict_scan) is not CompatibilityConflictScan:
        raise _compat_fail(
            CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE,
            "consumption evidence requires an exact conflict scan",
        )
    validate_compatibility_conflict_scan(conflict_scan, evaluator=decision._evaluator)
    for ref, name in ((subject_ref, "subject_ref"), (consumer_context_ref, "consumer_context_ref")):
        if type(ref) is not HashBoundRef:
            raise _compat_fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                f"consumption evidence requires an exact {name}",
            )
    return CompatibilityFinding(
        compatible=decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE,
        evidence_complete=decision.evidence.completeness is EvidenceCompleteness.COMPLETE,
        drifted=value.outcome is not RevalidationOutcome.PASSED,
        conflicts_unresolved=conflict_scan.decision_kind is not ConflictDecisionKind.NO_CONFLICT_FOUND,
        subject_ref=subject_ref,
        consumer_context_ref=consumer_context_ref,
    )


def consumption_finding_from_effective_taint(value: EffectiveTaint) -> TaintFinding:
    """State a reconstructed effective taint in the vocabulary the gates read.

    Completeness is read off the record rather than taken as an argument. A
    profile is only meaningful together with the evidence that it is the *whole*
    chain, and the record already carries whether that was established: an
    ``EffectiveTaint`` from ``reconstruct_effective_taint`` says no, one from
    ``require_taint_consumable`` says yes, because only the latter consulted the
    anchored history store.

    A reduced profile presented without its complete source/derivation/authority
    closure therefore reaches the gate as incomplete rather than as permissive,
    and the gate refuses it instead of believing it — with no caller having had
    the opportunity to assert otherwise.
    """

    if type(value) is not EffectiveTaint:
        raise _taint_fail(TaintFailureCode.TYPE_MISMATCH, "effective taint must be an exact record")
    if type(value.chain_complete) is not bool:
        raise _taint_fail(TaintFailureCode.TYPE_MISMATCH, "chain_complete must be an exact bool")
    blocks_consumption, blocks_publication = effective_taint_blocks(value)
    return TaintFinding(
        consumable=not blocks_consumption and not value.quarantined,
        chain_complete=value.chain_complete,
        quarantined=bool(value.quarantined),
        blocks_publication=blocks_publication,
    )


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "consumption_finding_from_effective_taint",
    "consumption_finding_from_revalidation",
]
