"""Long-term element owners: declared targets, verified state, development delta.

The product description (sections 6-8) binds every significant element to a
long-term owner agent and gives it three accumulated models: a Target State of
declarations, a Current State of verified facts and the Development Delta
between them. This module maps that onto the Gold memory doctrine instead of
importing a second memory system:

* Target State is the accumulated set of *declarations* bound to an element
  path: every court-judged episode's requirement plus the governing task's
  expected effects.
* Current State is the accumulated *verified* evidence: judged outcomes, their
  confirmed and refuted patch assertions, and the physical origins behind
  each assertion.
* Development Delta is the difference, per declaration, in explicit categories
  (verified earlier, partially verified, established defect, deferred without
  basis, awaiting fresh verification), each entry carrying its proof chain
  back to the deciding court decision.

The profile adds no writer. Everything is re-derived from the pinned court
decision, so the consolidation court stays the single writer of assessments
(invariant I3 of the agent memory specification), nothing is deleted (I8) and
an uncertain episode never becomes a fact (I7). Declared deviations from the
specification: the marker/reflex/journal formats are not introduced - Gold's
task contracts, publications and court events already carry that role - and
the owner granularity is the element path, because the court's requirement
records are path-scoped; the frame keeps its finer element identity and joins
the registry by path.
"""
import hashlib

from .project_episode_outcome import FULFILLED, NOT_FULFILLED, PARTIAL
from .source_verification import canonical
from .stage10.intent import EffectDisposition

OWNER_REGISTRY_V1 = "synapse.stage4.gold.element-owner-registry/v1"
ACTIVE_MEMORY_FRAME_V4 = "synapse.stage4.gold.active-memory-frame/v4"
MAX_REGISTRY_OWNERS = 1024
# Delta categories. A verified entry is a historical fact bound to its revision;
# it is never a claim about the current tree, which only a fresh verified run
# can establish (the frame's AWAITING_FRESH_VERIFICATION doctrine).
VERIFIED, PARTIAL_VERIFIED, DEFECT, DEFERRED = ("VERIFIED_EARLIER", "PARTIAL_VERIFIED_EARLIER",
                                                "DEFECT_ESTABLISHED", "DEFERRED_NO_BASIS")
CATEGORIES = (VERIFIED, PARTIAL_VERIFIED, DEFECT, DEFERRED)


def path_owner_identity(project_identity, path):
    """Stable identity of the long-term owner of one element path."""
    return "path-owner-" + hashlib.sha256(canonical([project_identity, path])).hexdigest()


def _declaration(item):
    """One accumulated Target declaration: a judged episode's requirement."""
    requirement = item["observation"]["requirement"]
    return {"task_contract_ref": requirement["task_contract_ref"],
            "repository_revision": requirement["repository_revision"],
            "run_id": item["observation"]["run_id"], "paths": list(requirement["paths"]),
            "decision": item["decision"], "outcome_event": item["outcome"]}


def _current_entry(item):
    """One accumulated Current fact with its full proof chain."""
    observation = item["observation"]
    requirement = observation["requirement"]
    assertions = sorted(({"assertion_id": assertion["assertion_id"], "status": assertion["status"],
                          "patch_sha256": assertion["claim"]["patch_sha256"], "origin": assertion["origin"]}
                         for assertion in item["learning"]["assertions"]), key=canonical)
    candidates = sorted({attempt["candidate"]["patch_sha256"] for attempt in observation["attempts"]
                         if attempt["candidate"] is not None})
    return {"run_id": observation["run_id"], "requirement_outcome": requirement["outcome"],
            "verdict": item["verdict"], "reasons": list(item["reasons"]),
            "controller_status": requirement["controller_status"],
            "repository_revision": requirement["repository_revision"],
            "assertions": assertions, "candidate_patches": candidates,
            "basis": list(requirement["basis"]), "recovery": observation["recovery"],
            "decision": item["decision"], "outcome_event": item["outcome"]}


def _category(item):
    """Policy v1 of the delta: only a counted, established outcome decides."""
    if item["verdict"] != "COUNTED":
        return DEFERRED
    outcome = item["observation"]["requirement"]["outcome"]
    if outcome == FULFILLED:
        return VERIFIED
    if outcome == PARTIAL:
        return PARTIAL_VERIFIED
    if outcome == NOT_FULFILLED:
        return DEFECT
    return DEFERRED  # UNCERTAIN and UNVERIFIABLE episodes have no basis at all.


def _delta_entry(entry):
    return {"run_id": entry["run_id"], "at_revision": entry["repository_revision"],
            "decision": entry["decision"], "verified_patches": [item["patch_sha256"] for item in entry["assertions"]
                                                                if item["status"] == "CONFIRMED"]}


def _defect_entry(entry):
    return {"run_id": entry["run_id"], "at_revision": entry["repository_revision"],
            "decision": entry["decision"], "requirement_outcome": entry["requirement_outcome"],
            "reasons": list(entry["reasons"])}


def _deferred_entry(entry):
    return {"run_id": entry["run_id"], "at_revision": entry["repository_revision"],
            "decision": entry["decision"], "requirement_outcome": entry["requirement_outcome"],
            "reasons": list(entry["reasons"])}


def _owner(project_identity, path, history, task):
    entries = {category: [] for category in CATEGORIES}
    for item in history:
        entries[_category(item)].append(_current_entry(item))
    awaiting = [{"constraint_id": effect.constraint_id, "kind": effect.kind.value,
                 "subject_path": effect.subject_path}
                for effect in task.effects
                if effect.subject_path == path and effect.disposition is EffectDisposition.EXPECTED]
    verified, partial = entries[VERIFIED], entries[PARTIAL_VERIFIED]
    defects, deferred = entries[DEFECT], entries[DEFERRED]
    return {"path": path, "owner_id": path_owner_identity(project_identity, path),
            "lifecycle": "LONG_TERM_DERIVED",
            "target_state": {"declarations": [_declaration(item) for item in history],
                             "current_task": {"task_contract_ref": task.reference.to_dict(),
                                              "expected_effects": awaiting} if awaiting else None},
            "current_state": {"verified": verified, "partial": partial, "defects": defects,
                              "deferred": deferred, "episodes_seen": len(history)},
            "development_delta": {
                "declarations_verified_earlier": [_delta_entry(entry) for entry in verified],
                "declarations_partial_earlier": [_delta_entry(entry) for entry in partial],
                "defects_established": [_defect_entry(entry) for entry in defects],
                "deferred_no_basis": [_deferred_entry(entry) for entry in deferred],
                "awaiting_fresh_verification": awaiting,
                "summary": {"declarations": len(history), "verified": len(verified), "partial": len(partial),
                            "defects": len(defects), "deferred": len(deferred),
                            "awaiting_fresh_verification": len(awaiting)}}}


def _subsystem_summary(owners):
    """Roll owners up to their top-level directory (the subsystem grain)."""
    subsystems = {}
    for owner in owners:
        path = owner["path"]
        grain = path.rsplit("/", 1)[0] if "/" in path else "(project-root)"
        summary = owner["development_delta"]["summary"]
        section = subsystems.setdefault(grain, {"subsystem": grain, "paths": 0, "declarations": 0,
                                                "verified": 0, "defects": 0})
        section["paths"] += 1
        section["declarations"] += summary["declarations"]
        section["verified"] += summary["verified"]
        section["defects"] += summary["defects"]
    return sorted(subsystems.values(), key=lambda item: item["subsystem"])


def build_owner_registry(*, project_identity, court, shown, omitted, task, active_paths):
    """Derive the long-term owner roster from the pinned court decision.

    ``shown`` is the bounded cut of ``court['judged']`` the frame itself reads;
    the registry keeps the same cut so the roster and the frame can never
    disagree about history. ``active_paths`` guarantees every active element
    finds its owner even before its first judged episode.
    """
    if court.get("decision") is None:
        raise ValueError("an owner registry needs a pinned court decision")
    paths = set(active_paths)
    for item in shown:
        paths |= set(item["observation"]["requirement"]["paths"])
    if not paths or len(paths) > MAX_REGISTRY_OWNERS:
        raise ValueError("owner registry exceeds its explicit path budget")
    owners = [_owner(project_identity, path, [item for item in shown
                                              if path in item["observation"]["requirement"]["paths"]], task)
              for path in sorted(paths)]
    judged = len(court["judged"])
    return {"schema_version": OWNER_REGISTRY_V1, "project_identity": project_identity,
            "decision": court["decision"], "judged_episodes": judged, "shown_episodes": len(shown),
            "omitted_episodes": omitted,
            "system": {"owner": "system-owner", "judged_episodes": judged, "shown_episodes": len(shown),
                       "omitted_episodes": omitted, "pending_outcomes": len(court["pending"]),
                       "admitted_subjects": sum(1 for subject in court["subjects"].values()
                                                if subject["state"] == "ADMITTED")},
            "subsystems": _subsystem_summary(owners), "owners": owners}


def apply_owner_profiles(frame, registry):
    """Enrich the three element states of a v4 frame from the long-term registry."""
    if (frame.get("schema_version") != ACTIVE_MEMORY_FRAME_V4
            or registry.get("schema_version") != OWNER_REGISTRY_V1):
        raise ValueError("owner profiles need their v4 frame and owner registry")
    by_path = {owner["path"]: owner for owner in registry["owners"]}
    for element in frame["elements"]:
        owner = by_path.get(element["path"])
        if owner is None:
            raise ValueError("active memory element lacks its long-term owner")
        current, delta = owner["current_state"], owner["development_delta"]
        element["target_state"] = {**element["target_state"],
                                   "declared_history": owner["target_state"]["declarations"]}
        element["current_state"] = {**element["current_state"], "episodes_seen": current["episodes_seen"],
                                    "verified_history": current["verified"], "partial_history": current["partial"],
                                    "defect_history": current["defects"], "deferred_history": current["deferred"]}
        element["development_delta"] = {**element["development_delta"], "owner_summary": delta["summary"],
                                        "declarations_verified_earlier": delta["declarations_verified_earlier"],
                                        "declarations_partial_earlier": delta["declarations_partial_earlier"],
                                        "defects_established": delta["defects_established"],
                                        "deferred_no_basis": delta["deferred_no_basis"]}
    return frame
