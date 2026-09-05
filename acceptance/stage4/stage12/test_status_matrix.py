"""Cheap OD-13 policy matrix; these facts never cross the authority factory."""

import pytest
from itertools import product

from synapse.experiments.gold.stage12.outcome import FinalStatus, _status, _run_status


def full_facts():
    return {
        "failure_codes": [], "interrupted": False, "refused": False,
        "c1": {"infra_error": False, "no_candidate": False, "refused": False,
               "c1_status": "GOLD_APPLIED_WITH_EVIDENCE", "evidence_ref": "sealed-evidence",
               "commands_complete": True, "task_ref": "task", "oracle_result_ref": "oracle",
               "oracle_resolved": True},
        "plan": "accepted", "resolved_bindings": ["binding"], "reusable_candidates": [],
        "obligations": [{"discharged": True, "evidence_ref": "report"}],
    }


@pytest.mark.parametrize("field,value", [
    ("evidence_ref", None), ("commands_complete", False), ("task_ref", None),
    ("oracle_result_ref", None), ("oracle_resolved", False), ("oracle_resolved", None),
    ("c1_status", "GOLD_ORACLE_UNRESOLVED"),
])
def test_every_full_c1_conjunct_is_required(field, value):
    facts = full_facts()
    assert _status(facts) is FinalStatus.FULL
    facts["c1"][field] = value
    assert _status(facts) is FinalStatus.UNRESOLVED


@pytest.mark.parametrize("field,value", [
    ("plan", None), ("resolved_bindings", []), ("obligations", []),
    ("obligations", [{"discharged": False, "evidence_ref": None}]),
    ("obligations", [{"discharged": True, "evidence_ref": None}]),
])
def test_every_full_plan_conjunct_is_required(field, value):
    facts = full_facts()
    facts[field] = value
    assert _status(facts) is FinalStatus.UNRESOLVED


@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize("failure,interrupted,no_candidate,refused,expected", [
    (True, True, True, True, "INVALID_CONTRACT"),
    (False, True, True, True, "INFRA_ERROR"),
    (False, False, True, True, "NO_CANDIDATE"),
    (False, False, False, True, "FAIL"),
    (False, False, False, False, "UNRESOLVED"),
])
def test_partial_requires_verified_admission_and_obeys_failure_precedence(reusable, failure, interrupted, no_candidate, refused, expected):
    facts = full_facts()
    facts["c1"].update(oracle_resolved=False, infra_error=interrupted, no_candidate=no_candidate, refused=refused)
    facts["failure_codes"] = ["C1_PROOF_INVALID"] if failure else []
    # A synthetic policy-channel value is not a candidate or verifier seal.
    facts["reusable_candidates"] = ["verified-and-admitted"] if reusable else []
    if reusable and expected in {"FAIL", "UNRESOLVED"}:
        expected = "VERIFIED_REUSABLE_PARTIAL"
    assert _status(facts).value == expected


@pytest.mark.parametrize("status", list(FinalStatus))
def test_run_aggregation_uses_the_verified_child_and_preparation_boundary(status):
    members = [{"outcome": {"payload": {"status": status.value}}}]
    assert _run_status(members, "ATTEMPT_DECISION") is status
    assert _run_status(members, "PREPARATION_FAILURE") is (
        FinalStatus.INVALID_CONTRACT if status is FinalStatus.INVALID_CONTRACT else FinalStatus.INFRA_ERROR)


def test_later_unresolved_attempt_does_not_erase_already_verified_reusable_output():
    members = [{"outcome": {"payload": {"status": status}}}
               for status in ("VERIFIED_REUSABLE_PARTIAL", "UNRESOLVED")]
    assert _run_status(members, "ATTEMPT_DECISION") is FinalStatus.VERIFIED_REUSABLE_PARTIAL
    members[-1]["outcome"]["payload"]["status"] = "FULL"
    assert _run_status(members, "ATTEMPT_DECISION") is FinalStatus.FULL


def test_complete_od13_policy_truth_table():
    """Exhaust the 9 completion and 5 precedence channels without executions."""
    c1_fields = ("evidence_ref", "task_ref", "oracle_result_ref", "oracle_resolved", "commands_complete")
    seen = set()
    for completion in product((False, True), repeat=9):
        for invalid, infra, absent, refused, reusable in product((False, True), repeat=5):
            facts = full_facts()
            for field, present in zip(c1_fields, completion):
                if not present:
                    facts["c1"][field] = False if field in {"oracle_resolved", "commands_complete"} else None
            if not completion[5]:
                facts["plan"] = None
            if not completion[6]:
                facts["resolved_bindings"] = []
            if not completion[7]:
                facts["obligations"] = []
            if not completion[8]:
                facts["c1"]["c1_status"] = "GOLD_ORACLE_UNRESOLVED"
            facts["failure_codes"] = ["C1_PROOF_INVALID"] if invalid else []
            facts["c1"].update(infra_error=infra, no_candidate=absent, refused=refused)
            facts["reusable_candidates"] = ["verified-and-admitted"] if reusable else []
            # Frozen normative precedence expressed independently as a table.
            applicable = (
                ("INVALID_CONTRACT", invalid), ("INFRA_ERROR", infra), ("NO_CANDIDATE", absent),
                ("FULL", all(completion)), ("VERIFIED_REUSABLE_PARTIAL", reusable),
                ("FAIL", refused), ("UNRESOLVED", True),
            )
            expected = next(name for name, applies in applicable if applies)
            actual = _status(facts).value
            assert actual == expected, (completion, applicable, actual)
            seen.add(actual)
    assert seen == {status.value for status in FinalStatus}
