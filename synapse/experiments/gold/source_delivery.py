"""Project frozen source evidence into task-scoped worker information."""

import base64

from .source_verification import canonical
from .stage10.context_codec import SOURCE_DELIVERY_PROFILE_V2


def _information_item(value, *, role="REFERENCE"):
    encoded = base64.urlsafe_b64encode(canonical(value)).decode("ascii").rstrip("=")
    return {"role": role, "media_type": "application/json", "content_base64url": encoded}


def _patch_information(diff_text):
    encoded = base64.urlsafe_b64encode(diff_text.encode("utf-8")).decode("ascii").rstrip("=")
    return {"role": "REFERENCE", "media_type": "text/plain", "content_base64url": encoded}


def _adaptive_memory_items(frame):
    """Expose admitted historical patches as neutral, unverified-for-current-task data."""
    court = frame.get("court", {})
    patches = court.get("adaptive_patches", [])
    by_revision = {}
    items = []
    for patch in patches:
        digest, revision = patch["patch_sha256"], patch["repository_revision"]
        by_revision.setdefault(revision, []).append(digest)
        items.append(_information_item({"historical_patch_observation": {
            "repository_revision": revision, "patch_sha256": digest, "oracle_resolved": True}},
            role="HISTORICAL_EXECUTION_OBSERVATION"))
        items.append(_patch_information(patch["diff_text"]))
    for revision in sorted(by_revision):
        items.append(_information_item({"repository_revision": revision,
            "procedural_memory": {"profile": "adaptive-verified-patch-memory/v1",
                                  "patches": sorted(set(by_revision[revision])),
                                  "generalization": "NOT_ESTABLISHED"}}))
    return items


def _source_information(selected, *, historical=False):
    experience = selected["experience"]
    claim = experience["claim"]
    paths = set(claim["sources"] if historical else selected["match"]["paths"])
    retained = {item["ref"]["sha256"]: item["content_base64url"] for item in experience["retained"]}
    complete = historical or paths == set(claim["sources"])
    result = {"repository_revision": claim["revision"],
        "status": experience["status"], "verification": experience["verification"],
        "execution": experience["execution"], "applicability": "UNASSESSED",
        "checkpoint_complete": experience["checkpoint_complete"],
        "matched_paths": selected["match"]["paths"],
        "sources": [{"path": item["path"], "content_base64url": retained[item["ref"]["sha256"]]}
                    for item in experience["sources"] if item["path"] in paths],
        "symbols": [{key: item[key] for key in ("path", "qualname", "symbol_kind") if key in item}
                    for item in experience["bindings"] if item["path"] in paths],
        "uninterpreted": [item for item in experience["uninterpreted"] if item["path"] in paths],
        "uncaptured_sources": [path for path in experience["uncaptured_sources"] if path in paths],
        "declared_recipe": claim["recipe"] if complete else None,
        "command_result": None if not complete or experience["command_result"] is None else
            {key: item for key, item in experience["command_result"].items()
             if key in {"command", "argv", "returncode", "stdout", "stderr", "status", "diagnostics", "duration_ms"}},
        "worktree_clean": experience["worktree_clean"], "runtime_unchanged": experience["runtime_unchanged"]}
    if not complete and (claim["recipe"] is not None or experience["command_result"] is not None):
        result["operation_observation_scope"] = "OMITTED_UNSELECTED_SOURCE_PATHS"
    return result


def source_experience_delivery(value):
    """Deliver current task-scoped data without widening memory authority."""
    from .source_snapshot import SOURCE_SNAPSHOT_V3, memory_source_basis, source_snapshot_reference
    recall = value["recall"]
    items = [_information_item(_source_information(selected)) for selected in recall["selected"]]
    if value["schema_version"] == SOURCE_SNAPSHOT_V3:
        from .project_agents import read_active_memory
        frame = read_active_memory(value["project_memory"], source_snapshot=memory_source_basis(value),
            run_memory_selection=value["run_memory_selection"])["active_memory_frame"]
        items.append(_information_item(_memory_information(frame)))
        items.extend(_adaptive_memory_items(frame))
    items.append(_information_item({"selection_state": recall["state"], "eligible_count": recall["eligible_count"],
        "omitted_by_limit": recall["omitted_by_limit"], "applicability": "UNASSESSED"}))
    return {"projection_profile": SOURCE_DELIVERY_PROFILE_V2,
            "snapshot_ref": source_snapshot_reference(value).to_dict(),
            "information": {"schema_version": "synapse.worker.local-information-input/v1", "items": items}}


def source_experience_matches_recorded(value, recorded):
    """Verify a physically retained delivery using its recorded projection contract."""
    if type(recorded) is not dict:
        return False
    actual = source_experience_delivery(value)
    if "projection_profile" in recorded:
        return recorded == actual
    if set(recorded) != {"snapshot_ref", "information"}:
        return False
    # A legacy record is verification input only. Live delivery has no switch
    # back to this format, including when resuming an older frozen snapshot.
    del actual["projection_profile"]
    legacy = [_information_item(_source_information(selected, historical=True))
              for selected in value["recall"]["selected"]]
    actual["information"]["items"][:len(legacy)] = legacy
    return recorded == actual


def _memory_information(frame):
    """Project verified owner state into neutral worker data, without Gold refs."""
    elements = []
    for item in frame["elements"]:
        memory = item["memory"]
        effects = [{key: effect[key] for key in ("kind", "disposition", "subject_path")}
                   for effect in memory["Goal"]["expected_effects"]]
        entry = {"path": item["path"], "name": memory["Identity"]["name"],
            "goal": {"expected_effects": effects, "status": "DECLARED"},
            "context": {"repository_revision": memory["Context"]["repository_revision"],
                        "allowed_scope": list(memory["Context"]["allowed_scope"]["entries"])},
            "development_delta": {"expected_effects": effects, "status": item["development_delta"]["status"]},
            "episodes": [_episode_information(entry) for entry in memory["Episodic"]["task_outcomes"]],
            "source_outcomes": memory["Episodic"]["source_outcomes"],
            "defects": memory["Defect"]["nonzero_source_exits"]}
        if "owner_summary" in item["development_delta"]:
            # The long-term owner's delta, as neutral counts only: no receipts,
            # no revisions, no proof identities cross the worker port.
            summary = item["development_delta"]["owner_summary"]
            entry["owner_profile"] = {"lifecycle": "LONG_TERM_DERIVED",
                "declarations_seen": summary["declarations"], "verified_earlier": summary["verified"],
                "partial_earlier": summary["partial"], "defects_established": summary["defects"],
                "tasks_failed_no_evidence": summary["task_failures"],
                "deferred_no_basis": summary["deferred"],
                "awaiting_fresh_verification": summary["awaiting_fresh_verification"]}
        elements.append(entry)
    result = {"memory_kinds": list(frame["memory_kinds"]),
              "repository_revision": frame["repository_revision"], "elements": elements}
    if "court" in frame:
        # Only the pinned court decision admits an exact patch for an automatic
        # proposal. Uncounted history is stated as a limit, never guessed.
        court = frame["court"]
        patches = court["automatic_patches"]
        result["episode_coverage"] = {"judged": court["judged_episodes"], "omitted": court["omitted_episodes"],
                                      "pending": len(court["pending"])}
    elif "layers" in frame:
        # Historical v2 frames keep their original rule: an exact assertion
        # confirmed and never refuted within the selected learning.
        statuses = {}
        for item in frame["layers"]["learned"]:
            statuses.setdefault(item["claim"]["patch_sha256"], set()).add(item["status"])
        patches = sorted(digest for digest, states in statuses.items() if states == {"CONFIRMED"})
    if "layers" in frame:
        # A proposal hint, never a substitute for the current admitted replay's
        # EXECUTION_OBSERVATION. No publication IDs, proof or confidence vote
        # crosses the worker port.
        result["procedural_memory"] = {"profile": "exact-verified-patch-memory/v1", "patches": patches,
                                       "generalization": "NOT_ESTABLISHED"}
    return result


def _episode_information(entry):
    value = {key: entry[key] for key in ("run_id", "status", "repository_revision")}
    if "observation" in entry:
        # Only the typed outcome class crosses the worker port, never its evidence refs.
        value["requirement_outcome"] = entry["observation"]["requirement"]["outcome"]
    return value
