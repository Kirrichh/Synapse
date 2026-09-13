"""Explicit operator selection of verified run-memory classes.

Selection changes the candidate universe, never the archive, publication proof,
compatibility gates or permissions. Every omitted class is declared in the
frozen source snapshot so a fresh run can be used as a reproducible comparison.
"""
from .stage13.rejected_patch_profile import (
    VERIFIED_PATCH_GUARD_V1, REJECTED_PATCH_GUARD_V3,
    REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5,
)

PROJECT_KNOWLEDGE_INPUT_V4 = "synapse.stage4.gold.knowledge-input/v4"
RUN_MEMORY_SELECTIONS = {"ALL", "SUCCESS_ONLY", "FAILURE_ONLY", "NONE"}


def require_run_memory_selection(value):
    if type(value) is not str or value not in RUN_MEMORY_SELECTIONS:
        raise ValueError("run memory selection is outside its declared profile")
    return value


def select_run_knowledge(knowledge, selection):
    require_run_memory_selection(selection)
    if selection == "ALL":
        return knowledge
    chosen = []
    for item in knowledge["candidates"]:
        profile = item["unit"]["core"]["verification_contract"]["profile_id"]
        if profile == VERIFIED_PATCH_GUARD_V1:
            category = "SUCCESS_ONLY"
        elif profile in {REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5}:
            category = "FAILURE_ONLY"
        else:
            raise ValueError("run memory has no classification for this verified profile")
        if selection == category:
            chosen.append(item)
    # All original physical origins/files remain retained for independent
    # reconstruction. Only eligible candidate inclusion changes.
    selected_keys = {item["unit"]["content_key"]["value"] for item in chosen}
    return {**knowledge, "candidates": chosen,
        "publications": {key: value for key, value in knowledge["publications"].items()
                         if key in selected_keys}}


def selected_episode(status, selection):
    require_run_memory_selection(selection)
    return selection == "ALL" or selection == ("SUCCESS_ONLY" if status == "FULL" else "FAILURE_ONLY")
