"""Fast positive and negative contract matrix for adaptive memory reuse."""
from copy import deepcopy
import hashlib

import pytest

from acceptance.stage4.stage10.test_exact_memory_proposal_contract import remembered
from acceptance.stage4.stage10.test_local_edit_contract import feedback, information, task
from synapse.canonical_values import canonical_json_bytes
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import propose_verified_memory, validate_local_edit_result


OLD_SOURCE = "def add(a, b):\n    return a - b\n"
NEW_REVISION = "b" * 40


def adaptive_inputs(source="# outside\n" + OLD_SOURCE, *, replacements=("a + b",), current_revision=NEW_REVISION):
    old_information, patches = remembered(*replacements, source=OLD_SOURCE)
    # Keep the old feedback, retained patches and old-revision memory hints, but
    # deliver only current-revision source bytes as the applicable inventory.
    memory_items = old_information.to_dict()["items"][1:]
    current = information(source=source, revision=current_revision, extra=memory_items)
    return task(revision=current_revision), current, patches


def test_outside_revision_change_reanchors_without_inheriting_old_success():
    public, private, patches = adaptive_inputs()
    result = propose_verified_memory(task=public, information=private)
    assert result["planning_route"] == "ADAPT_MEMORY"
    assert result["diff_text"] != patches[0]
    assert result["candidate_origins"] == [{"kind": "LOCAL_ADAPTIVE_MEMORY",
        "patch_sha256": hashlib.sha256(patches[0].encode()).hexdigest()}]
    assert result["candidates"][0]["prior_outcome"] is None
    current_source = "# outside\n" + OLD_SOURCE
    assert result["candidates"][0]["source_bindings"] == [{
        "path": "src/calc.py",
        "source_sha256": hashlib.sha256(current_source.encode()).hexdigest(),
        "result_sha256": hashlib.sha256(current_source.replace("a - b", "a + b").encode()).hexdigest(),
    }]
    assert result["status"] == "UNVERIFIED_PATCH_PROPOSAL"
    assert result["execution"] == "NO_REPOSITORY_EFFECTS"
    assert validate_local_edit_result(result, task_sha256=result["task_sha256"],
                                      information_sha256=private.sha256) == result


def test_metamorphic_outside_prefix_and_statement_changes_preserve_the_physical_fix():
    public, private, _ = adaptive_inputs()
    first = propose_verified_memory(task=public, information=private)
    prefixed, prefixed_information, _ = adaptive_inputs("# one\n# two\n" + OLD_SOURCE)
    second = propose_verified_memory(task=prefixed, information=prefixed_information)
    changed_statement = public.to_dict()
    changed_statement["statement"] = "A paraphrase grants no additional authority."
    third = propose_verified_memory(task=WorkerTaskInput(canonical_json_bytes(changed_statement)),
                                    information=private)
    for result, source in ((second, "# one\n# two\n" + OLD_SOURCE),
                           (third, "# outside\n" + OLD_SOURCE)):
        assert result["planning_route"] == "ADAPT_MEMORY"
        edit = result["effective_proposal"]["alternatives"][0]["edits"][0]
        assert edit["old"] == source
        assert edit["new"] == source.replace("return a - b", "return a + b")
    assert "-    return a - b\n+    return a + b\n" in first["diff_text"]


@pytest.mark.parametrize("source", [
    "def add(a, b):\n    return b - a\n",  # target changed
    OLD_SOURCE + OLD_SOURCE,             # old block ambiguous
    "def add(a, b):\n    # changed context\n    return a - b\n",
    "def add(a, b):\n    return a - b",  # unsupported ending
])
def test_changed_ambiguous_or_invalid_target_fails_closed(source):
    public, private, _ = adaptive_inputs(source)
    assert propose_verified_memory(task=public, information=private) is None


def test_current_negative_oracle_observation_vetoes_the_adapted_patch():
    public, private, _ = adaptive_inputs()
    proposed = propose_verified_memory(task=public, information=private)
    rejected = LocalInformationInput(canonical_json_bytes({**private.to_dict(),
        "items": private.to_dict()["items"] + [feedback(proposed["diff_text"], False)]}))
    assert propose_verified_memory(task=public, information=rejected) is None


def test_competing_positive_methods_do_not_get_an_automatic_tie_break():
    public, private, _ = adaptive_inputs(replacements=("a + b", "b + a"))
    assert propose_verified_memory(task=public, information=private) is None


def test_current_source_scope_and_revision_bindings_remain_mandatory():
    public, private, _ = adaptive_inputs()
    assert propose_verified_memory(task=task(), information=private) is None
    outside = public.to_dict()
    outside["allowed_scope"] = ["unrelated"]
    assert propose_verified_memory(task=WorkerTaskInput(canonical_json_bytes(outside)),
                                   information=private) is None
    old_sources_only = information(source="# outside\n" + OLD_SOURCE,
        extra=private.to_dict()["items"][1:])
    assert propose_verified_memory(task=public, information=old_sources_only) is None


@pytest.mark.parametrize("change", [
    lambda value: value.update(planning_route="EXACT_MEMORY"),
    lambda value: value["candidate_origins"][0].update(kind="LOCAL_MEMORY"),
    lambda value: value["candidate_origins"][0].update(patch_sha256=value["candidates"][0]["patch_sha256"]),
    lambda value: value["candidates"][0].update(prior_outcome=True),
    lambda value: value.update(selected_index=None),
])
def test_transport_cannot_forge_adaptive_provenance_or_success(change):
    public, private, _ = adaptive_inputs()
    result = propose_verified_memory(task=public, information=private)
    changed = deepcopy(result)
    change(changed)
    with pytest.raises(ValueError):
        validate_local_edit_result(changed, task_sha256=result["task_sha256"],
                                   information_sha256=private.sha256)
