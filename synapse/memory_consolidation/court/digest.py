"""The session digest: the structured starting point of the next session.

Not a retelling: per task, the completed segments with their quanta, the open
segments with a resume point (``qid`` and step) and their status — to rejudge,
to replan, blocked by the environment, not reached — plus the boundary it
belongs to and the slow-only triggers. Every reference resolves (И4).
"""
from __future__ import annotations

import copy
from typing import Any

from .. import records

DIGEST_V1 = "synapse.memory.session-digest/v1"


def _status(verdict) -> dict[str, Any]:
    if "environmental_failure" in verdict["flags"]:
        return {"status": "blocked", "blocked_by": "environmental_failure", "criterion": verdict["criterion"]}
    return {"status": {"uncertain": "rejudge_next_window", "failed": "needs_replanning",
                       "skipped": "not_reached"}.get(verdict["verdict"], "in_progress")}


def _task_of(verdict, cases, reactions) -> str | None:
    if cases:
        return cases[-1]["task_id"]
    return next((reaction["task_id"] for reaction in reactions
                 if reaction["segment_marker_id"] == verdict["marker_id"]), None)


def _segment(verdict, cases) -> tuple[bool, dict[str, Any]]:
    last = cases[-1] if cases else None
    qid = last["quantum"]["id"] if last else None
    if verdict["verdict"] == "confirmed":
        return True, {"marker_id": verdict["marker_id"], "verdict": "confirmed", "qid": qid}
    entry = {"marker_id": verdict["marker_id"], "verdict": verdict["verdict"], "qid": qid, **_status(verdict)}
    if last is not None:
        entry["resume_from"] = {"qid": qid, "step": len(last["steps"])}
    return False, entry


def digest_record(draft, boundary_id: str, slow_only, consolidation_id: str) -> dict[str, Any]:
    """The content-addressed digest of one consolidation."""
    by_marker: dict[str, list] = {}
    for case in draft["cases"]:
        if case["marker_id"] is not None:
            by_marker.setdefault(case["marker_id"], []).append(case)
    tasks: dict[str, dict[str, Any]] = {}
    for verdict in draft["verdicts"]:
        cases = by_marker.get(verdict["marker_id"], [])
        task_id = _task_of(verdict, cases, draft["reactions"])
        task = tasks.setdefault(task_id or "off_plan", {"task_id": task_id, "completed_segments": [],
                                                         "open_segments": []})
        completed, entry = _segment(verdict, cases)
        task["completed_segments" if completed else "open_segments"].append(entry)
    for task in tasks.values():
        task["status"] = "in_progress" if task["open_segments"] else "completed"
    return records.make("session_digest", digest={
        "schema_version": DIGEST_V1, "based_on": consolidation_id, "task_state": [tasks[key] for key in sorted(tasks)],
        "snapshot_boundary": boundary_id, "slow_only_triggers": copy.deepcopy(slow_only)})
