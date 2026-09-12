"""Memory selection is a projection of already verified records, not admission."""
from copy import deepcopy

import pytest

from synapse.experiments.gold.project_memory_selection import select_run_knowledge
from synapse.experiments.gold.stage13.rejected_patch_profile import (
    VERIFIED_PATCH_GUARD_V1, REJECTED_PATCH_GUARD_V3,
    REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5,
)


def inventory():
    profiles = [VERIFIED_PATCH_GUARD_V1, REJECTED_PATCH_GUARD_V3,
                REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5]
    candidates = [{"unit": {"content_key": {"value": str(index)},
        "core": {"verification_contract": {"profile_id": profile}}}}
        for index, profile in enumerate(profiles)]
    return {"candidates": candidates, "origins": [{"archive": "retained"}],
        "files": [{"evidence": "retained"}],
        "publications": {str(index): {"original": item} for index, item in enumerate(candidates)}}


@pytest.mark.parametrize("selection,indices", [
    ("ALL", [0, 1, 2, 3]), ("SUCCESS_ONLY", [0]),
    ("FAILURE_ONLY", [1, 2, 3]), ("NONE", []),
])
def test_selection_keeps_exact_candidate_publication_pairs_and_the_complete_archive(selection, indices):
    original = inventory()
    before = deepcopy(original)
    selected = select_run_knowledge(original, selection)
    assert selected["candidates"] == [original["candidates"][index] for index in indices]
    assert selected["publications"] == {str(index): original["publications"][str(index)] for index in indices}
    assert selected["origins"] == original["origins"]
    assert selected["files"] == original["files"]
    assert original == before


@pytest.mark.parametrize("selection", ["SUCCESS_ONLY", "FAILURE_ONLY", "NONE"])
def test_restricted_selection_requires_a_known_verified_profile(selection):
    original = inventory()
    original["candidates"][0]["unit"]["core"]["verification_contract"]["profile_id"] = "unclassified"
    with pytest.raises(ValueError, match="classification"):
        select_run_knowledge(original, selection)
    assert select_run_knowledge(original, "ALL") == original


@pytest.mark.parametrize("selection", [None, True, 1, "success", ""])
def test_selection_is_an_exact_operator_declared_profile(selection):
    with pytest.raises(ValueError, match="declared profile"):
        select_run_knowledge(inventory(), selection)
