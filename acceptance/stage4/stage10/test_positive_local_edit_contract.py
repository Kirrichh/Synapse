"""Positive preference is local, scoped, versioned and independently rechecked."""
from copy import deepcopy

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import task, information, proposal, feedback
from synapse.worker.local_edits import (
    LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROFILE_V2, propose_local_edits, validate_local_edit_result,
)


def observed_case(*outcomes):
    variants = proposal(("a - b", "0"), ("a - b", "a + b"))
    good = propose_local_edits(task=task(), information=information(),
        proposal=proposal(("a - b", "a + b")))["diff_text"]
    private = information(extra=[feedback(good, outcome) for outcome in outcomes])
    return variants, private


def test_positive_evidence_changes_selection_only_in_the_new_explicit_profile():
    variants, private = observed_case(True)
    legacy = propose_local_edits(task=task(), information=private, proposal=variants, profile=LOCAL_EDIT_PROFILE_V1)
    current = propose_local_edits(task=task(), information=private, proposal=variants, profile=LOCAL_EDIT_PROFILE_V2)
    assert legacy["selected_index"] == 0
    assert current["selected_index"] == 1
    assert current["candidates"][1]["prior_outcome"] is True
    assert current["status"] == "UNVERIFIED_PATCH_PROPOSAL"
    assert validate_local_edit_result(current, task_sha256=current["task_sha256"], information_sha256=private.sha256) == current
    without = propose_local_edits(task=task(), information=information(), proposal=variants, profile=LOCAL_EDIT_PROFILE_V2)
    assert without["selected_index"] == 0 and without["diff_text"] != current["diff_text"]


@pytest.mark.parametrize("outcomes", [(None,), (False, True), (1,), ("true",)])
def test_unknown_conflicting_or_untyped_outcomes_never_create_positive_preference(outcomes):
    variants, private = observed_case(*outcomes)
    result = propose_local_edits(task=task(), information=private, proposal=variants, profile=LOCAL_EDIT_PROFILE_V2)
    assert result["selected_index"] == 0
    assert result["candidates"][1]["prior_outcome"] is None


def test_positive_evidence_cannot_supply_missing_source_or_override_current_applicability():
    variants, private = observed_case(True)
    observations = [item for item in private.to_dict()["items"] if item["role"] == "EXECUTION_OBSERVATION"]
    changed = information(source="def add(a,b):\n    return unknown\n", extra=observations)
    result = propose_local_edits(task=task(), information=changed, proposal=variants, profile=LOCAL_EDIT_PROFILE_V2)
    assert result["diff_text"] is None and result["selected_index"] is None


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(profile=LOCAL_EDIT_PROFILE_V1),
    lambda value: value.update(selected_index=0),
    lambda value: value["candidates"][1].update(prior_outcome=1),
    lambda value: value["candidates"][1].update(prior_outcome=False),
    lambda value: value["candidates"][1].update(feedback="CONFLICTING"),
])
def test_transport_cannot_change_profile_priority_or_exact_outcome_types(mutate):
    variants, private = observed_case(True)
    value = propose_local_edits(task=task(), information=private, proposal=variants, profile=LOCAL_EDIT_PROFILE_V2)
    changed = deepcopy(value)
    mutate(changed)
    with pytest.raises(ValueError):
        validate_local_edit_result(changed, task_sha256=value["task_sha256"], information_sha256=private.sha256)
