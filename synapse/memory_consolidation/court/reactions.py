"""Reaction episodes: what happened after each failed action of a window.

A reactive event (``external_error``) is followed by a habit episode, a near
miss or a miss, and possibly by the program's slow path. Each reaction becomes
one entry with its typed context, the failed operation, whether that operation
was settled (derived from the journal, never from the runtime's label), the
local outcome of the habit episode and the slow path's typed steps, calls,
witnesses and evidence. The entry is addressed inside its case as ``(qid, step
range)``; it is the material of signals, cold anchors, the candidate pool and
boundary decisions.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..learning.behavior import SAME, typed_steps
from ..learning.triggers import typed_context
from ..tools.episodes import local_outcome, program_calls
from .window import SessionFacts

REACTIONS = {"habit_activated": "activated", "habit_near_miss": "near_miss", "habit_miss": "miss"}


def episode_attempts(scope: Mapping[str, Any] | None, episode: str) -> list[dict[str, Any]]:
    return [] if scope is None else [item for item in scope["attempts"] if item["episode"] == episode]


def _tokens(scope, attempts) -> int:
    total = 0
    for item in attempts:
        payload = scope["payloads"].get(item["gw_seq"])
        if isinstance(payload, dict) and type(payload.get("tokens", 0)) is int:
            total += payload.get("tokens", 0)
    return total


def _witnesses(failure, attempts) -> list[dict[str, Any]]:
    """Witnesses of the slow path's claim: answers inside it that attest the failed operation's effect."""
    inside = {item["gw_seq"] for item in attempts}
    claim = None if failure is None else {"tool": failure["tool"], "op": failure["op"], "effect": "applied"}
    return [] if failure is None else [{"source": item["source"], "gw_seq": item["gw_seq"], "by": item["by"],
                                        "claim": claim} for item in failure["attestations"] if item["gw_seq"] in inside]


def _slow_view(slow_event, attempts, scope, failed, failure, seconds) -> dict[str, Any]:
    """The slow path of one reactive event, from its recorded attempts."""
    actions = [item for item in attempts if item["role"] == "action"]
    measured = [seconds.get(item["gw_seq"]) for item in attempts]
    return {
        "completed": bool(slow_event.get("completed")), "outcome": local_outcome(attempts),
        "steps": typed_steps(program_calls(attempts), failed),
        "calls": [{"tool": SAME if item["op"] == failed["op"] and item["tool"] == failed["tool"] else item["tool"],
                   "args": item["args"]} for item in program_calls(actions)],
        "witnesses": _witnesses(failure, attempts),
        "evidence": [item["evidence_ref"] for item in actions if item["evidence_ref"] is not None],
        "evidence_preexisting": any(item["evidence_preexisting"] for item in actions),
        "gw_seqs": [item["gw_seq"] for item in attempts],
        "seconds": None if not measured or any(value is None for value in measured) else sum(measured),
        "tokens": _tokens(scope, attempts)}


def _failure_of(scope, error) -> dict[str, Any] | None:
    if scope is None:
        return None
    return next((item for item in scope["failures"] if item["gw_seq"] == error["action_ref"]["gw_seq"]), None)


def _steps_range(scope, episode) -> list[int]:
    if scope is None or not episode:
        return [0, 0]
    order = [item["gw_seq"] for item in scope["attempts"]]
    return [order.index(episode[0]["gw_seq"]), order.index(episode[-1]["gw_seq"])]


def _reaction(kind, event, error, facts: SessionFacts, slow, verdicts, replay, cases, seconds) -> dict[str, Any]:
    failed = error["failed_action"]
    scope = facts.scopes.get(error["action_scope"])
    case = cases.get(error["action_scope"])
    failure = _failure_of(scope, error)
    habit_episode = episode_attempts(scope, f"{error['event_id']}|habit")
    slow_attempts = episode_attempts(scope, f"{error['event_id']}|slow")
    marker_id = error.get("segment_marker_id")
    verdict = verdicts.get(marker_id) if marker_id else None
    entry = {"event_id": error["event_id"], "run_id": facts.run, "task_id": error.get("task_id"),
             "segment_marker_id": marker_id, "off_plan": bool(error.get("off_plan")),
             "context": typed_context(error), "failed": failed, "reaction": REACTIONS[kind],
             "habit_id": event.get("habit_id"), "trigger_id": event.get("trigger_id"), "layer": event.get("layer"),
             "failed_condition": event.get("failed_condition"), "op_scope": error["action_scope"],
             "qid": None if case is None else case["quantum"]["id"],
             "steps_range": _steps_range(scope, slow_attempts or habit_episode),
             "segment_verdict": None if verdict is None else verdict["verdict"],
             "segment_flags": [] if verdict is None else verdict["flags"], "replay": replay,
             "anchored_evidence": bool(verdict and verdict["verdict"] == "confirmed" and verdict["stage"] == "1"
                                       and verdict["evidence"]),
             "failed_settled": None if failure is None else failure["settled"],
             "habit_outcome": local_outcome(habit_episode) if kind == "habit_activated" else None,
             "habit_steps": typed_steps(program_calls(habit_episode), failed) if kind == "habit_activated" else [],
             "habit_attempts": [item["gw_seq"] for item in habit_episode], "slow": None}
    slow_event = slow.get(error["event_id"])
    if slow_event is not None:
        entry["slow"] = _slow_view(slow_event, slow_attempts, scope, failed, failure, seconds)
    return entry


def build_reactions(facts: SessionFacts, verdicts, replay, cases, seconds) -> list[tuple[str, dict, dict]]:
    """``(kind, reaction event, entry)`` for every reaction to a window's failed actions."""
    errors = {event["event_id"]: event for _, event in facts.found.get("external_error", [])}
    slow = {event["trigger_event_id"]: event for _, event in facts.found.get("slow_path_used", [])}
    result = []
    for kind in REACTIONS:
        for _, event in facts.found.get(kind, []):
            error = errors.get(event.get("trigger_event_id"))
            if error is not None:
                result.append((kind, event, _reaction(kind, event, error, facts, slow, verdicts, replay, cases,
                                                      seconds)))
    return result
