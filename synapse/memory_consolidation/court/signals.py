"""Stage 2 of the court: the signal of one habit fire, or why it has none.

``signal = w_outcome · outcome + w_segment · segment`` from the local outcome
re-derived from the journal and the segment verdict. A fire off the plan, in an
environment failure or in a skipped segment is excluded and listed; a fire in
an uncertain segment, with an uncertain or unclear outcome, or in an emergency
consolidation is conserved as pending evidence. Nothing is silently dropped.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..policy import signal as signal_of


def _status(reaction: Mapping[str, Any]) -> tuple[str, str] | None:
    """Exclusion or conservation before a signal can be computed."""
    if reaction["off_plan"] or reaction["segment_marker_id"] is None:
        return "excluded", "off_plan"
    verdict = reaction["segment_verdict"]
    if verdict is None:
        return "pending", "segment_not_judged"
    if "environmental_failure" in reaction["segment_flags"]:
        return "excluded", "environmental_failure"
    if verdict == "skipped":
        return "excluded", "skipped"
    if verdict == "uncertain":
        return "pending", "segment_uncertain"
    if reaction["habit_outcome"] in {"uncertain", "unclear"}:
        return "pending", f"outcome_{reaction['habit_outcome']}"
    return None


def fire_signal(reaction: Mapping[str, Any], event: Mapping[str, Any], parameters, mode: str) -> dict[str, Any]:
    """Counted signal, conserved or excluded fire, with its reason."""
    base = {"event_id": event.get("trigger_event_id"), "run_id": reaction["run_id"], "habit_id": event["habit_id"],
            "trigger_id": event["trigger_id"], "task_id": reaction["task_id"], "outcome": reaction["habit_outcome"],
            "runtime_outcome": event.get("outcome"), "segment_marker_id": reaction["segment_marker_id"],
            "segment_verdict": reaction["segment_verdict"], "signal": None,
            "conflict_warning": bool(event.get("conflict_warning")),
            "runner_up_habit_id": event.get("runner_up_habit_id"),
            "semantic_score_micros": event.get("semantic_score_micros"), "context": reaction["context"],
            "steps": reaction["habit_steps"]}
    early = _status(reaction)
    if early is not None:
        return {**base, "status": early[0], "why": early[1]}
    value = signal_of(parameters, reaction["habit_outcome"], reaction["segment_verdict"])
    if value is None:
        return {**base, "status": "pending", "why": "signal_undecided"}
    if mode == "emergency":
        return {**base, "status": "pending", "signal": value, "why": "provisional"}
    return {**base, "status": "counted", "signal": value, "why": None}
