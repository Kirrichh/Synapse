"""Pure memory projections: repetitions cannot turn observations into authority."""
from copy import deepcopy
from dataclasses import replace
import pytest

from acceptance.stage4.stage10._builders import plan_world
from synapse.experiments.gold.project_learning import EPISODE_LEARNING_V1, build_memory_layers


def layers(*observations, task=None, selection="ALL"):
    task = task or plan_world()[3].task_contract
    return build_memory_layers(task=task, source_snapshot={"recall": {"selected": []}}, episodes=[], learning=[{
        "schema_version": EPISODE_LEARNING_V1, "outcome_ref": {"test": "pure-data"},
        "assertions": list(observations), "unresolved": []}], run_memory_selection=selection)


def observation(*, status="CONFIRMED", origin="one"):
    task = plan_world()[3].task_contract
    return {"assertion_id": "a" * 64, "claim": {"repository_revision": task.repository_revision_sha256,
        "task_contract_ref": task.reference.to_dict(), "patch_sha256": "b" * 64},
        "status": status, "origin": {"primary": origin}, "generalization": "NOT_ESTABLISHED"}


def test_copies_do_not_create_independent_confirmations_or_a_generalized_habit():
    item = observation()
    once = layers(item)
    repeated = layers(*[deepcopy(item) for _ in range(100)])
    assert repeated == once
    candidate, = once["learned"]
    assert candidate["generalization"] == "NOT_ESTABLISHED"
    assert len(candidate["observations"]) == 1
    assert once["working"]["status"] == "AWAITING_FRESH_VERIFICATION"


def test_contradiction_survives_any_number_of_positive_repetitions():
    positive = observation()
    negative = observation(status="REFUTED", origin="independent-result")
    result = layers(*([positive] * 50), negative)
    candidate, = result["learned"]
    assert candidate["status"] == "CONFLICTED"
    assert len(candidate["observations"]) == 2


def test_retained_nonconfirmation_does_not_manufacture_a_refutation():
    result = layers()
    assert result["learned"] == []
    assert result["working"]["status"] == "AWAITING_FRESH_VERIFICATION"


def test_an_exact_assertion_does_not_transfer_to_another_task_or_revision():
    original = plan_world()[3].task_contract
    for task in (replace(original, task_statement="Another requirement"),
                 replace(original, repository_revision_sha256="d" * 40)):
        assert layers(observation(), task=task)["learned"] == []


@pytest.mark.parametrize("selection, statuses", [("ALL", ["CONFIRMED", "REFUTED"]),
    ("SUCCESS_ONLY", ["CONFIRMED"]), ("FAILURE_ONLY", ["REFUTED"]), ("NONE", [])])
def test_selection_is_per_assertion_even_inside_a_multi_attempt_episode(selection, statuses):
    result = layers(observation(), observation(status="REFUTED", origin="second"), selection=selection)
    actual = [row["status"] for item in result["learned"] for row in item["observations"]]
    assert sorted(actual) == statuses


def test_one_origin_cannot_silently_change_status_and_unknown_status_is_not_a_fact():
    with pytest.raises(ValueError, match="incompatible facts"):
        layers(observation(), observation(status="REFUTED"))
    with pytest.raises(ValueError, match="unsupported status"):
        layers(observation(status="PROVISIONAL"))
