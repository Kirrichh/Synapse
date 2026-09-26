"""Composition of learned procedures (refinement §14).

A part is an admitted learned recovery procedure. Its interface is read from
its frozen records and the admitted tool contracts, never from free text:

* applicability — its typed trigger (conditions and limits, spec part 1 §6);
* inputs — the failed action's arguments and the event fields its binding
  reads, with the kinds its trigger requires;
* guarantee and termination — the repeat of the failed operation succeeds
  (``failed_operation_recovered``): the caller knows when to resume;
* effects — the tools its body calls and what their contracts say
  (idempotent, compensates).

A composition is hierarchical: a base procedure runs, and at a step that
failed otherwise than its basis a part may be joined for that step's failure
event, as an HTN method refines a task or a Soar subgoal resolves an impasse.
A part joins only when its trigger applies to that failure (the conditions of
every component stay in force and are checked again each time), its inputs
bind from that event, it terminates by repeating the failed operation, and
neither its effects nor the base's remaining steps compensate an effect the
other produced (an undo is a conflict, as in a saga). When the part's repeat
succeeds, the step's answer is that repeat and the base resumes.

The slow planner forms such a join as a plan hypothesis; only verified
compositions are born as habits, and a born composite re-checks each join:
a part that is not admitted now is not called.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from ..records import digest
from .behavior import SAME, BindingUnavailable, bind_arguments, event_fields_read, result_class
from .triggers import condition_key, matches, typed_context

COMPOSITION_V1 = "synapse.memory.composition/v1"
RECOVERS = "failed_operation_recovered"


def _calls(pattern) -> list[Mapping[str, Any]]:
    return [step for step in pattern if step["step"] == "call"]


def interface(part: Mapping[str, Any], configuration) -> dict[str, Any]:
    """The typed interface of one part: applicability, inputs, guarantee and effects."""
    habit, trigger = part["habit"], part["trigger"]
    failed_args = sorted({source["failed_arg"] for item in habit["binding"]
                          for source in (item.get("args") or {}).values() if "failed_arg" in source})
    explanation = trigger.get("applicability") or {}
    kinds = {item["field"]: item["kind"] for item in explanation.get("required", [])}
    effects = []
    for step in _calls(habit["action_pattern"]):
        contract = configuration.tools.tools.get(step["tool"]) if step["tool"] != SAME else None
        effects.append({"tool": step["tool"], "idempotent": None if contract is None else contract.idempotent,
                        "compensates": None if contract is None else contract.compensates})
    return {"habit_id": habit["id"], "applies_to": condition_key(trigger),
            "inputs": {"failed_args": failed_args,
                       "event_fields": [{"field": name, "kind": kinds.get(name)} for name in
                                        event_fields_read(habit["binding"])]},
            "guarantee": habit["expected_outcome"], "terminates": _terminates(habit),
            "effects": effects, "limits": {"context": trigger["context"], "not_when": list(trigger["not_when"])}}


def _terminates(habit) -> bool:
    """A part's termination: it repeats the failed operation, so a caller knows when to resume."""
    return habit["expected_outcome"] == RECOVERS and any(step["tool"] == SAME for step in _calls(habit["action_pattern"]))


def _conflicts(part, configuration, done: Iterable[str], remaining: Iterable[str]) -> list[str]:
    """Undo conflicts: an effect one side produced that the other side's contract compensates."""
    tools = configuration.tools.tools
    own = [step["tool"] for step in _calls(part["habit"]["action_pattern"]) if step["tool"] != SAME]
    found = []
    for name in own:
        contract = tools.get(name)
        if contract is not None and contract.compensates in set(done):
            found.append(f"{name} compensates {contract.compensates}, which the procedure already did")
    for name in remaining:
        contract = tools.get(name)
        if contract is not None and contract.compensates in set(own):
            found.append(f"the procedure's later {name} compensates the part's {contract.compensates}")
    return found


def check_join(part: Mapping[str, Any], failure: Mapping[str, Any], configuration, *, done: Iterable[str],
               remaining: Iterable[str]) -> list[str]:
    """Why a part cannot be joined for this failure; empty when it can."""
    habit, trigger = part["habit"], part["trigger"]
    reasons = []
    if not _terminates(habit):
        reasons.append("no_termination: the part does not repeat the failed operation")
    status, failed = matches(trigger, typed_context(failure))
    if status != "applicable":
        reasons.append(f"conditions_not_met: {failed['field'] if failed else 'more than one condition'}")
    try:
        for rule in habit["binding"]:
            bind_arguments(rule, failure, None)
    except BindingUnavailable as exc:
        reasons.append(f"inputs_unavailable: {exc}")
    reasons += [f"effect_conflict: {item}" for item in _conflicts(part, configuration, done, remaining)]
    return reasons


def join_identity(joins: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The joins of a composition in their canonical form."""
    return sorted(({"at": item["at"], "on": item["on"], "part": item["part"]} for item in joins),
                  key=lambda item: (item["at"], item["on"]))


def composition_key(base: str, joins: Iterable[Mapping[str, Any]]) -> str:
    return "cmp:" + digest({"schema_version": COMPOSITION_V1, "base": base, "joins": join_identity(joins)})


def _tools_around(pattern, index, views) -> tuple[list[str], list[str]]:
    """Tools the base already called with an effect before ``index``, and the tools it will call after."""
    calls = _calls(pattern)
    done = [calls[step]["tool"] for step in range(index) if views[step].get("ok")]
    return done, [step["tool"] for step in calls[index + 1:]]


def _joined(part_id, part) -> dict[str, Any]:
    return {"part": {"habit_id": part_id, "pattern": part["habit"]["action_pattern"],
                     "binding": part["habit"]["binding"]}}


def recorded_contingency(pattern, joins, lookup: Callable[[str], Mapping[str, Any] | None], configuration):
    """The contingency of a recorded composition: each join, its part re-checked when it is needed."""
    by_step = {(item["at"], item["on"]): item["part"] for item in joins}

    def contingency(index, views, failure):
        part_id = by_step.get((index, result_class(views[index])))
        if part_id is None:
            return None
        part = lookup(part_id)
        if part is None:
            return {"refused": {"reason": "component_unavailable", "part": part_id}}
        done, remaining = _tools_around(pattern, index, views)
        reasons = check_join(part, failure, configuration, done=done, remaining=remaining)
        if reasons:
            return {"refused": {"reason": "component_refused", "part": part_id, "reasons": reasons}}
        return _joined(part_id, part)

    return contingency


def planning_contingency(pattern, joins, parts: list[Mapping[str, Any]], configuration, record):
    """The slow planner: known joins first; at a new impasse the first part that may join, in rank order.

    ``parts`` are admitted parts in rank order; ``record(entry)`` receives
    every hypothesis the planner forms, with the parts it considered.
    """
    lookup = {part["habit"]["id"]: part for part in parts}
    known = recorded_contingency(pattern, joins, lookup.get, configuration)

    def contingency(index, views, failure):
        decision = known(index, views, failure)
        if decision is not None and "part" in decision:
            return decision
        done, remaining = _tools_around(pattern, index, views)
        considered = []
        for part in parts:
            reasons = check_join(part, failure, configuration, done=done, remaining=remaining)
            considered.append({"part": part["habit"]["id"], "joined": not reasons, "reasons": reasons})
            if not reasons:
                join = {"at": index, "on": result_class(views[index]), "part": part["habit"]["id"]}
                record({"join": join, "considered": considered, "failure": canonical_failure(failure)})
                return _joined(join["part"], part)
        record({"join": None, "at": index, "on": result_class(views[index]), "considered": considered,
                "failure": canonical_failure(failure)})
        return {"refused": {"reason": "no_part_joins", "considered": considered}}

    return contingency


def canonical_failure(failure: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a failure event a hypothesis records: its tool, fields and failed arguments."""
    return {"tool": failure["failed_action"]["tool"], "fields": dict(failure["fields"]),
            "args": dict(failure["failed_action"]["args"])}
