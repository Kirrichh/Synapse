"""Cases of a window: one case per operation scope, with its content identity.

A case is one execution of a plan segment (or the off-plan line of a run) with
every reaction to its failed actions. Its quantum identity covers the task
contract, marker, program, executor, the boundary pinned at session start, the
ordered recorded results, the trace of its own recorded events and its outcome
class. The material here is what substage 7a weighs, tiers and retains.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..learning.triggers import typed_context
from ..quanta import case_quantum, trace_ref
from .window import SessionFacts

_REACTIVE = ("habit_activated", "habit_near_miss", "habit_miss", "slow_path_used")


def _case_events(found, scope_name) -> list[tuple[int, Mapping[str, Any]]]:
    """The recorded events of one case, in history order."""
    events = [(position, event) for position, event in found.get("external_action", [])
              if event["request"].get("op_scope") == scope_name]
    events += [(position, event) for position, event in found.get("external_error", [])
               if event.get("action_scope") == scope_name]
    errors = {event["event_id"] for _, event in events if event["type"] == "external_error"}
    for kind in _REACTIVE:
        events += [(position, event) for position, event in found.get(kind, [])
                   if event.get("trigger_event_id") in errors]
    return sorted(events, key=lambda item: item[0])


def _context(events) -> dict[str, Any]:
    for _, event in events:
        if event["type"] == "external_error":
            return typed_context(event)["fields"]
    return {}


def _measured(scope, seconds) -> float | None:
    values = [seconds.get(item["gw_seq"]) for item in scope["attempts"]]
    return None if any(value is None for value in values) else sum(values)


def _steps(scope) -> list[dict[str, Any]]:
    return [{"step": "call", "tool": item["tool"], "args_ref": "sha256:" + item["args_canon"],
             "result_class": "ok" if item["op_result"] == "ok" else (item["op_err"] or item["op_result"])}
            for item in scope["attempts"] if item["role"] == "action"]


def _case(facts: SessionFacts, scope, scope_name, verdicts, seconds, replay) -> dict[str, Any]:
    events = _case_events(facts.found, scope_name)
    marker_id = scope["segment"]
    marker, plan = facts.markers.get(marker_id, (None, None)) if marker_id else (None, None)
    verdict = verdicts.get(marker_id) if marker_id else None
    contract_ref = None if plan is None else plan["task_contract_ref"]
    quantum = case_quantum(task_contract_ref=contract_ref, segment_marker_id=marker_id,
                           program_ref=facts.session["source_hash"], start_snapshot_ref=facts.session.get("pinned"),
                           recorded_results=scope["evidence_refs"], trace=trace_ref(event for _, event in events),
                           outcome_class=scope["outcome"])
    return {"quantum": quantum, "run_id": facts.run, "op_scope": scope_name, "marker_id": marker_id,
            "task_id": scope["task_id"], "element": None if plan is None else plan["element"],
            "element_part": None if marker is None else marker["element_part"],
            "recorded_results": scope["evidence_refs"], "outcome": scope["outcome"],
            "verdict": None if verdict is None else verdict["verdict"],
            "anchored_confirmed": bool(verdict and verdict["verdict"] == "confirmed" and marker
                                       and marker["external_anchor"] is not None),
            "steps": _steps(scope),
            "habits": sorted({event["habit_id"] for _, event in events if event["type"] == "habit_activated"}),
            "reactions": sum(1 for _, event in events if event["type"] in {"habit_activated", "slow_path_used"}),
            "defects": [{"kind": "Defect", "tool": item["tool"], "op_err": item["op_err"], "gw_seq": item["gw_seq"],
                         "verified": replay == "replay_verified"} for item in scope["failures"] if not item["settled"]],
            "cost": scope["cost"], "seconds": _measured(scope, seconds), "replay": replay,
            "evidence_refs": scope["evidence_refs"], "context": _context(events)}


def build_cases(facts: SessionFacts, verdicts, seconds, replay: str | None) -> dict[str, dict[str, Any]]:
    """Every case of one session's window, by operation scope."""
    return {name: _case(facts, scope, name, verdicts, seconds, replay)
            for name, scope in sorted(facts.scopes.items())}
