"""Reaction episodes: what happened after each failed action of a window.

A reactive event (``external_error``) is followed by a habit episode, a near
miss or a miss, and possibly by the program's slow path. Each reaction becomes
one entry with its typed context, the failed operation, whether that operation
was settled (derived from the journal, never from the runtime's label), the
local outcome of the habit episode and the slow path's typed steps, calls,
witnesses and evidence. The entry is addressed inside its case as ``(qid, step
range)``; it is the material of signals, cold anchors, the candidate pool and
boundary decisions.

Each slow-path call carries the origins of its arguments: which earlier answer
a value first appeared in (refinement §13). A learned habit's local outcome is
re-derived by running its frozen body with the executor over the recorded
answers of its episode, so the court and the runtime judge a stopped body by
one rule and neither reads the other's label.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..learning.behavior import SAME, run_recorded, typed_steps
from ..learning.composition import recorded_contingency
from ..learning.dependencies import Knowledge, origins
from ..learning.triggers import typed_context
from ..records import canonical
from ..tools.episodes import local_outcome, program_calls
from ..session import opening_of
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


class Recorded:
    """How the court reads a session's learned bodies: its tool contracts, frozen records and loaded parts.

    The parts are the learned habits the session loaded at its start (its
    opening records them): exactly the parts its runtime could call.
    """

    def __init__(self, configuration, frozen: Mapping[str, Any], loaded: set[str]) -> None:
        self.configuration, self.frozen, self.loaded = configuration, frozen, loaded

    def part(self, habit_id: str) -> Mapping[str, Any] | None:
        return self.frozen.get(habit_id) if habit_id in self.loaded else None

    def view(self, attempt, scope) -> dict[str, Any]:
        """The gateway's view of one recorded attempt, as the body received it, with its event fields."""
        payload = scope["payloads"].get(attempt["gw_seq"])
        contract = self.configuration.tools.tools.get(attempt["tool"])
        fields = {} if contract is None else contract.event_fields_of(
            attempt["transport"], attempt["op_result"], attempt["effect"], attempt["op_err"], payload)
        return {"ok": attempt["transport"] == "ok" and attempt["op_result"] == "ok", "tool": attempt["tool"],
                "op": attempt["op"], "attempt": attempt["attempt"], "transport": attempt["transport"],
                "op_result": attempt["op_result"], "op_err": attempt["op_err"], "effect": attempt["effect"],
                "payload": payload, "source": attempt["source"], "event_fields": fields}

    def rederive(self, habit, joins, error, attempts, scope) -> dict[str, Any] | None:
        """A body (with the joins of its composition) run by its executor over the recorded answers.

        ``None`` when the record does not hold the calls the body makes.
        """
        calls = iter([item for item in program_calls(attempts) if item["role"] == "action"])

        def answer(tool, arguments):
            attempt = next(calls, None)
            if attempt is None or attempt["tool"] != tool or canonical(attempt["args"]) != canonical(dict(arguments)):
                raise _Recorded(tool)
            return self.view(attempt, scope)

        contingency = recorded_contingency(habit["action_pattern"], joins, self.part, self.configuration) \
            if joins else None
        try:
            return run_recorded(habit["action_pattern"], habit["binding"], error, answer, contingency=contingency)
        except _Recorded:
            return None


class _Recorded(Exception):
    """A frozen body asked for a call its recorded episode does not hold."""


def _body_outcome(recorded: Recorded, habit, error, attempts, scope) -> tuple[str, dict | None]:
    """A learned body's local outcome, re-derived by its executor over the recorded answers."""
    joins = (habit.get("composition") or {}).get("joins") or []
    result = recorded.rederive(habit, joins, error, attempts, scope)
    if result is None:
        return local_outcome(attempts), {"reason": "record_incomplete"}
    return result["outcome"], result["detail"]


def _composition(recorded: Recorded, executed, error, habit_episode, slow_attempts, scope) -> dict[str, Any]:
    """A recovery the slow planner composed: its plan, and the composition re-derived from the record."""
    used = recorded.frozen.get(executed["used"]) if executed.get("used") else None
    prefix = habit_episode if executed.get("prefix") else []
    result = None if used is None else recorded.rederive(used["habit"], executed["joins"], error,
                                                          [*prefix, *slow_attempts], scope)
    return {"base": executed["base"], "used": executed.get("used"), "joins": executed["joins"],
            "prefix": executed.get("prefix", 0), "recorded": {key: executed.get(key) for key in (
                "outcome", "recovered", "parts")},
            "verified": result is not None and result["outcome"] == executed.get("outcome")
            and result["parts"] == executed.get("parts"),
            "outcome": None if result is None else result["outcome"],
            "parts": [] if result is None else result["parts"],
            "detail": None if result is None else result["detail"]}


def _slow_view(slow_event, attempts, scope, failed, failure, seconds, knowledge, error) -> dict[str, Any]:
    """The slow path of one reactive event, from its recorded attempts."""
    actions = [item for item in attempts if item["role"] == "action"]
    measured = [seconds.get(item["gw_seq"]) for item in attempts]
    called = program_calls(actions)
    return {
        "completed": bool(slow_event.get("completed")), "outcome": local_outcome(attempts),
        "steps": typed_steps(program_calls(attempts), failed),
        "calls": [{"tool": SAME if item["op"] == failed["op"] and item["tool"] == failed["tool"] else item["tool"],
                   "args": item["args"]} for item in called],
        "origins": origins([{"gw_seq": item["gw_seq"], "args": item["args"], "ok": item["op_result"] == "ok",
                             "payload": scope["payloads"].get(item["gw_seq"])} for item in called], knowledge, error),
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


def _reaction(kind, event, error, facts: SessionFacts, slow, verdicts, replay, cases, seconds, knowledge,
              recorded: Recorded, executed) -> dict[str, Any]:
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
             "habit_outcome": local_outcome(habit_episode) if kind == "habit_activated" else None, "habit_detail": None,
             "habit_steps": typed_steps(program_calls(habit_episode), failed) if kind == "habit_activated" else [],
             "habit_attempts": [item["gw_seq"] for item in habit_episode], "slow": None, "composition": None}
    learned = recorded.frozen.get(event.get("habit_id")) if kind == "habit_activated" and event.get("layer") == 2 \
        else None
    if learned is not None and scope is not None:
        entry["habit_outcome"], entry["habit_detail"] = _body_outcome(recorded, learned["habit"], error, habit_episode,
                                                                      scope)
    slow_event = slow.get(error["event_id"])
    if slow_event is not None:
        entry["slow"] = _slow_view(slow_event, slow_attempts, scope, failed, failure, seconds, knowledge, error)
    if executed is not None and executed.get("base") is not None and scope is not None:
        entry["composition"] = _composition(recorded, executed, error, habit_episode, slow_attempts, scope)
    return entry


def knowledge_of(facts: SessionFacts) -> Knowledge:
    """What the session knew before each recorded answer: its program, inputs and journal."""
    attempts, payloads = [], {}
    for scope in facts.scopes.values():
        attempts.extend(scope["attempts"])
        payloads.update(scope["payloads"])
    return Knowledge(source_code=facts.session["source_code"], inputs=facts.session["initial_bindings"],
                     attempts=attempts, payloads=payloads)


def build_reactions(facts: SessionFacts, verdicts, replay, cases, seconds, frozen,
                    configuration) -> list[tuple[str, dict, dict]]:
    """``(kind, reaction event, entry)`` for every reaction to a window's failed actions."""
    errors = {event["event_id"]: event for _, event in facts.found.get("external_error", [])}
    slow = {event["trigger_event_id"]: event for _, event in facts.found.get("slow_path_used", [])}
    executed = {event["trigger_event_id"]: event for _, event in facts.found.get("composition_executed", [])}
    opening = opening_of(facts.session["history"]) or {}
    recorded = Recorded(configuration, frozen, {item["habit_id"] for item in opening.get("learned") or []})
    knowledge = knowledge_of(facts)
    result = []
    for kind in REACTIONS:
        for _, event in facts.found.get(kind, []):
            error = errors.get(event.get("trigger_event_id"))
            if error is not None:
                result.append((kind, event, _reaction(kind, event, error, facts, slow, verdicts, replay, cases,
                                                      seconds, knowledge, recorded,
                                                      executed.get(error["event_id"]))))
    return result
