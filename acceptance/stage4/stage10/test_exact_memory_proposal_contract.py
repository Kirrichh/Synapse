"""Automatic proposal boundaries over neutral data, without execution authority."""
from copy import deepcopy
import base64
import hashlib
import json

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import task, information, proposal, feedback, encoded
from synapse.canonical_values import canonical_json_bytes
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROFILE_V5, propose_local_edits,
    propose_verified_memory, validate_local_edit_result,
)


def remembered(*replacements, outcome=True, hint=True, source="def add(a, b):\n    return a - b\n"):
    patches = [propose_local_edits(task=task(), information=information(),
        proposal=proposal(("a - b", new)))["diff_text"] for new in (replacements or ("a + b",))]
    records = []
    for patch in patches:
        records.extend([feedback(patch, outcome),
            {"role": "REFERENCE", "media_type": "text/plain", "content_base64url": encoded(patch.encode())}])
    if hint:
        records.append({"role": "REFERENCE", "media_type": "application/json", "content_base64url": encoded(canonical_json_bytes({
            "repository_revision": task().to_dict()["repository_revision"],
            "procedural_memory": {"profile": "exact-verified-patch-memory/v1", "generalization": "NOT_ESTABLISHED",
                "patches": sorted(hashlib.sha256(patch.encode()).hexdigest() for patch in patches)}}))})
    return information(source=source, extra=records), patches


def test_unique_verified_memory_proposes_the_same_patch_as_slow_only():
    private, patches = remembered()
    fast = propose_verified_memory(task=task(), information=private)
    slow = propose_local_edits(task=task(), information=private, proposal=proposal(), profile=LOCAL_EDIT_PROFILE_V4)
    assert fast["planning_route"] == "EXACT_MEMORY"
    assert fast["diff_text"] == slow["diff_text"] == patches[0]
    assert fast["status"] == "UNVERIFIED_PATCH_PROPOSAL"
    assert fast["execution"] == "NO_REPOSITORY_EFFECTS"
    assert validate_local_edit_result(fast, task_sha256=fast["task_sha256"], information_sha256=private.sha256) == fast


@pytest.mark.parametrize("kwargs", [
    {"outcome": False}, {"outcome": None}, {"outcome": 1}, {"outcome": "true"},
    {"hint": False}, {"source": None}, {"source": "def add(a, b):\n    return b - a\n"},
])
def test_absence_uncertainty_and_changed_source_require_ordinary_planning(kwargs):
    private, _ = remembered(**kwargs)
    assert propose_verified_memory(task=task(), information=private) is None


def test_competing_positive_methods_and_conflicting_evidence_never_use_a_tie_break_for_automation():
    private, _ = remembered("a + b", "b + a")
    assert propose_verified_memory(task=task(), information=private) is None
    private, patches = remembered()
    rows = private.to_dict()["items"] + [feedback(patches[0], False)]
    conflicting = LocalInformationInput(canonical_json_bytes({**private.to_dict(), "items": rows}))
    assert propose_verified_memory(task=task(), information=conflicting) is None


@pytest.mark.parametrize("change", [
    lambda value: value.update(repository_revision="b" * 40),
    lambda value: value.update(allowed_scope=["unrelated"]),
    lambda value: value.update(capabilities=["repository.read"]),
    lambda value: value["effects"].append({"kind": "PATH_MODIFIED", "disposition": "EXPECTED", "subject_path": "src/other.py"}),
])
def test_a_memory_candidate_cannot_widen_or_only_partially_cover_the_task(change):
    private, _ = remembered()
    value = task().to_dict()
    change(value)
    assert propose_verified_memory(task=WorkerTaskInput(canonical_json_bytes(value)), information=private) is None


def test_duplicate_delivery_is_not_competition_or_additional_confidence():
    private, _ = remembered()
    duplicated = LocalInformationInput(canonical_json_bytes({**private.to_dict(), "items": private.to_dict()["items"] * 2}))
    original = propose_verified_memory(task=task(), information=private)
    repeated = propose_verified_memory(task=task(), information=duplicated)
    assert len(repeated["candidates"]) == 1
    assert repeated["diff_text"] == original["diff_text"]


def test_model_route_remains_available_when_memory_is_missing():
    result = propose_local_edits(task=task(), information=information(),
        proposal=proposal(("a - b", "a + b")), profile=LOCAL_EDIT_PROFILE_V5)
    assert result["planning_route"] == "MODEL" and result["diff_text"]


@pytest.mark.parametrize("revision, allowed", [
    ({"kind": "GIT_COMMIT", "git_sha": "a" * 40}, True),
    ({"kind": "GIT_COMMIT", "git_sha": "b" * 40}, False),
    ({"kind": "UNVERIFIED", "git_sha": "a" * 40}, False),
    ({"kind": "GIT_COMMIT", "git_sha": "a" * 40, "trusted": True}, False),
])
def test_element_memory_revision_is_translated_without_accepting_untyped_authority(revision, allowed):
    private, _ = remembered()
    value = private.to_dict()
    item = value["items"][-1]
    raw = item["content_base64url"]
    frame = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    frame["repository_revision"] = revision
    item["content_base64url"] = encoded(canonical_json_bytes(frame))
    result = propose_verified_memory(task=task(), information=LocalInformationInput(canonical_json_bytes(value)))
    assert (result is not None) is allowed


@pytest.mark.parametrize("change", [
    lambda value: value.update(planning_route="CONFIRMED_SUCCESS"),
    lambda value: value.update(planning_route=True),
    lambda value: value["proposal"].update(alternatives=proposal(("a - b", "a + b"))["alternatives"]),
    lambda value: value.update(selected_index=None),
])
def test_transport_cannot_forge_an_automatic_route(change):
    private, _ = remembered()
    result = propose_verified_memory(task=task(), information=private)
    changed = deepcopy(result)
    change(changed)
    with pytest.raises(ValueError):
        validate_local_edit_result(changed, task_sha256=result["task_sha256"], information_sha256=private.sha256)
