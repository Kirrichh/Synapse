"""Requirement outcome of one completed run, derived only from sealed evidence.

Stage 12 already decides each attempt's status from independently verified C1
and oracle facts. This owner projects that decision into three separate facts
memory needs: what every attempt established, whether a later independent
attempt recovered an earlier one, and whether the declared requirement was
fulfilled. A worker message, command exit or operator label is not an input.
An unknown effect or an invalid proof stays uncertain: it is never a success
basis and never a failure basis.
"""
from .stage10.task_contract import GoverningTaskContract
from .stage12.outcome import FinalStatus, inspect_outcome

EPISODE_OUTCOME_V1 = "synapse.stage4.gold.episode-outcome/v1"
FULFILLED, PARTIAL, NOT_FULFILLED = "FULFILLED", "PARTIAL", "NOT_FULFILLED"
UNCERTAIN, UNVERIFIABLE = "UNCERTAIN", "UNVERIFIABLE"
REQUIREMENT_OUTCOMES = (FULFILLED, PARTIAL, NOT_FULFILLED, UNCERTAIN, UNVERIFIABLE)
# Independently established absence of fulfilment. Uncertain and unverifiable
# runs are not defects of an element; their evidence does not decide anything.
ESTABLISHED_NONFULFILMENT = frozenset({PARTIAL, NOT_FULFILLED})

_OUTCOME_OF_STATUS = {
    FinalStatus.FULL: FULFILLED,
    FinalStatus.VERIFIED_REUSABLE_PARTIAL: PARTIAL,
    FinalStatus.UNRESOLVED: NOT_FULFILLED,
    FinalStatus.NO_CANDIDATE: NOT_FULFILLED,
    FinalStatus.FAIL: NOT_FULFILLED,
    FinalStatus.INFRA_ERROR: UNCERTAIN,
    FinalStatus.INVALID_CONTRACT: UNVERIFIABLE,
}
_C1_EVIDENCE = (("c1_result_ref", "C1_RESULT"), ("report_ref", "COMMAND_REPORT"),
                ("evidence_ref", "C1_EVIDENCE"), ("oracle_result_ref", "INDEPENDENT_ORACLE"))


def observe_completed_run(*, frozen, state, outcome_ref):
    """Classify one physically reopened run whose identity the caller verified."""
    result = state.final_result
    if result is None:
        raise ValueError("an episode outcome requires a completed run")
    task = GoverningTaskContract.from_dict(frozen["declaration"]["task_contract"])
    run = inspect_outcome(result.structured_outcome)
    if run["scope"] != "RUN" or run["manifest_sha256"] != result.manifest_sha256:
        raise ValueError("an episode outcome needs the run's own terminal outcome")
    attempts = [_attempt(member, task) for member in run["attempt_outcomes"]]
    outcome = _OUTCOME_OF_STATUS[FinalStatus(run["status"])]
    uncertainties = [{"attempt_id": item["attempt_id"], "reasons": item["reasons"]}
                     for item in attempts if item["outcome"] == UNCERTAIN]
    if run["terminal_kind"] == "PREPARATION_FAILURE":
        uncertainties.append({"attempt_id": None, "reasons": ["PREPARATION_FAILURE"]})
    recovery = None
    if outcome == FULFILLED and len(attempts) > 1:
        # Every Gold attempt is a new operation; none repeats an earlier one.
        recovery = {"kind": "LATER_INDEPENDENT_ATTEMPT", "fulfilled_by": attempts[-1]["attempt_id"],
                    "after": [item["attempt_id"] for item in attempts[:-1]]}
    return {"schema_version": EPISODE_OUTCOME_V1, "outcome_ref": outcome_ref,
            "requirement": {"task_contract_ref": task.reference.to_dict(),
                            "repository_revision": task.repository_revision_sha256,
                            "outcome": outcome, "run_status": run["status"],
                            "controller_status": result.final_status.value,
                            "basis": _basis(outcome, attempts)},
            "attempts": attempts, "recovery": recovery, "uncertainties": uncertainties}


def _attempt(member, task):
    payload = member["outcome"]["payload"]
    facts = payload["verification"]["payload"]
    if facts["task_contract_ref"] != task.reference.to_dict():
        raise ValueError("an attempt verified another requirement")
    status = FinalStatus(payload["status"])
    c1 = facts["c1"]
    evidence = [{"role": "VERIFICATION", "ref": payload["verification"]["verification_ref"]}]
    if c1 is not None:
        evidence += [{"role": role, "ref": c1[field]} for field, role in _C1_EVIDENCE if c1[field] is not None]
    evidence += [{"role": "VERIFIED_PUBLICATION", "ref": ref} for ref in payload["publication_refs"]]
    candidate = None
    if c1 is not None and c1["verified_patch_sha256"] is not None:
        candidate = {"patch_sha256": c1["verified_patch_sha256"], "revision": c1["verified_revision"]}
    return {"attempt_id": facts["attempt_id"], "result_sha256": member["result_sha256"],
            "operation": "INDEPENDENT_ATTEMPT", "status": status.value, "outcome": _OUTCOME_OF_STATUS[status],
            "reasons": _reasons(status, facts), "oracle_resolved": None if c1 is None else c1["oracle_resolved"],
            "candidate": candidate, "evidence": evidence}


def _reasons(status, facts):
    """Diagnostic facts behind a Stage 12 status; the status itself is not re-decided."""
    c1 = facts["c1"]
    if status is FinalStatus.INVALID_CONTRACT:
        return sorted(facts["failure_codes"]) or ["VERIFICATION_EVIDENCE_ABSENT"]
    if status is FinalStatus.INFRA_ERROR:
        return [code for code, present in (("ATTEMPT_INTERRUPTED", facts["interrupted"]),
                ("VERIFICATION_INFRASTRUCTURE", c1 is not None and c1["infra_error"])) if present]
    if status is FinalStatus.UNRESOLVED:
        return ["REUSE_NOT_ESTABLISHED" if facts["mechanism_use"] is not None else "REQUIREMENT_NOT_ESTABLISHED"]
    return [{FinalStatus.FULL: "INDEPENDENTLY_VERIFIED", FinalStatus.VERIFIED_REUSABLE_PARTIAL: "VERIFIED_PART_ONLY",
             FinalStatus.NO_CANDIDATE: "NO_CANDIDATE", FinalStatus.FAIL: "REFUSED"}[status]]


def _basis(outcome, attempts):
    """Evidence that decided the requirement; uncertainty has no basis at all."""
    if outcome in {UNCERTAIN, UNVERIFIABLE} or not attempts:
        return []
    deciding = [item for item in attempts if item["outcome"] == PARTIAL] if outcome == PARTIAL else attempts[-1:]
    return [{"attempt_id": item["attempt_id"], **evidence} for item in deciding for evidence in item["evidence"]]
