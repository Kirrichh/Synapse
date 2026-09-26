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
neither its effects nor the remaining steps of the procedures around it
compensate an effect already produced (an undo is a conflict, as in a saga).
When the part's repeat succeeds, the step's answer is that repeat and the
base resumes.

* Alternatives: a join lists its parts in order. When one does not recover
  the step, the next admissible one is tried against the step's current
  failure, with every effect already applied counted for conflicts, as a BDI
  agent tries the next applicable plan or an acting engine retries another
  method; a part is tried once per impasse, and nothing is tried over an
  unknown effect.
* Nesting: a part's own impasses are joined the same way (its own
  composition first), and a part never joins inside itself or a procedure it
  is part of, so every composition is finite.
* Sequence: a procedure whose repeat of its operation is refused for another
  reason ends in that new failure; a part whose trigger applies to it joins
  at that repeat, so the first procedure's output — the typed failure event —
  is the next one's input, and the first resumes after it.

A composition's identity is its base and its joins: each join's step, the
class it failed with, and its alternatives in order, each with the joins
inside it. The slow planner forms joins as plan hypotheses; only verified
compositions are born as habits, and a born composite re-checks each join
when it is needed: a part that is not admitted now is not called.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from ..records import digest
from .behavior import SAME, BindingUnavailable, bind_arguments, event_fields_read
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


def specificity(trigger: Mapping[str, Any]) -> int:
    """How much a trigger requires: its conditions and context labels."""
    labels = 0 if trigger["context"] == "any" else len(trigger["context"])
    return len(trigger["when"]) + len(trigger["not_when"]) + labels


def ranked(parts: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Parts in the order the planner tries them: context trust, then the more specific trigger (as a
    production system resolves a conflict by specificity), then identity."""
    return sorted(parts, key=lambda item: (-float(item["context_trust"]), -specificity(item["trigger"]),
                                           item["habit_id"]))


# -- identity ------------------------------------------------------------------
def _node(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"part": item["part"], "joins": join_identity(item.get("joins") or [])}


def join_identity(joins: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The joins of a composition in their canonical form: by step and class, alternatives in order."""
    return sorted(({"at": item["at"], "on": item["on"], "alternatives": [_node(node) for node in item["alternatives"]]}
                   for item in joins), key=lambda item: (item["at"], item["on"]))


def composition_key(base: str, joins: Iterable[Mapping[str, Any]]) -> str:
    return "cmp:" + digest({"schema_version": COMPOSITION_V1, "base": base, "joins": join_identity(joins)})


def joins_of(parts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The joins an execution made: the parts it tried at each impasse, in order, with their own joins."""
    found: dict[tuple, list] = {}
    for item in parts:
        found.setdefault((item["at"], item["on"]), []).append(
            {"part": item["habit_id"], "joins": joins_of(item.get("parts") or [])})
    return join_identity({"at": at, "on": on, "alternatives": alternatives}
                         for (at, on), alternatives in found.items())


def merge_joins(known: Iterable[Mapping[str, Any]], found: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Known joins extended by the ones an execution found: new alternatives after the known ones."""
    merged = {(item["at"], item["on"]): [_node(node) for node in item["alternatives"]] for item in known}
    for item in join_identity(found):
        alternatives = merged.setdefault((item["at"], item["on"]), [])
        for node in item["alternatives"]:
            same = next((index for index, known_node in enumerate(alternatives)
                         if known_node["part"] == node["part"]), None)
            if same is None:
                alternatives.append(node)
            else:
                alternatives[same] = {"part": node["part"],
                                      "joins": merge_joins(alternatives[same]["joins"], node["joins"])}
    return join_identity({"at": at, "on": on, "alternatives": alternatives} for (at, on), alternatives in merged.items())


def parts_of(joins: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every part a composition names, nested ones included, with the base step its join hangs on."""
    found = []
    for item in join_identity(joins):
        for node in item["alternatives"]:
            found.append({"part": node["part"], "at": item["at"]})
            found += [{"part": inner["part"], "at": item["at"]} for inner in parts_of(node["joins"])]
    return found


def stack_of(part: Mapping[str, Any]) -> tuple[str, ...]:
    """The procedures a part's execution stands for: itself, and the base it composes."""
    composition = part["habit"].get("composition")
    return (part["habit"]["id"],) + (() if composition is None else (composition["base"],))


# -- contingencies -----------------------------------------------------------
def _remaining(pattern, index) -> list[str]:
    """The tools a body will still call after step ``index`` (its repeats of a failed operation aside)."""
    return [step["tool"] for step in _calls(pattern)[index + 1:] if step["tool"] != SAME]


class _Planner:
    """The slow planner's view: admitted parts in rank order, and where its hypotheses go."""

    def __init__(self, parts: list[Mapping[str, Any]], record: Callable[[dict], None], path=()) -> None:
        self.parts, self.record, self.path = parts, record, tuple(path)

    def inside(self, at: int, part_id: str) -> "_Planner":
        return _Planner(self.parts, self.record, (*self.path, {"at": at, "part": part_id}))


def _contingency(pattern, joins, lookup, configuration, stack, outer, planner: _Planner | None):
    """At an impasse: the next admissible alternative of its join, then (planning) the first admissible part."""
    by_step = {(item["at"], item["on"]): item["alternatives"] for item in join_identity(joins)}

    def decide(impasse):
        tried = {item["part"] for item in impasse["tried"]}
        remaining = [*_remaining(pattern, impasse["at"]), *outer]
        considered, refusals = [], []

        def admissible(part_id, part) -> list[str]:
            reasons = check_join(part, impasse["failure"], configuration, done=impasse["applied"], remaining=remaining)
            considered.append({"part": part_id, "joined": not reasons, "reasons": reasons})
            return reasons

        def chosen(part_id, part, nested):
            if planner is not None:
                planner.record({"path": list(planner.path), "join": {"at": impasse["at"], "on": impasse["on"],
                                                                     "part": part_id},
                                "considered": considered, "failure": canonical_failure(impasse["failure"])})
            own = (part["habit"].get("composition") or {}).get("joins") or []
            inner = _contingency(part["habit"]["action_pattern"], merge_joins(own, nested), lookup, configuration,
                                 (*stack, *stack_of(part)), remaining,
                                 None if planner is None else planner.inside(impasse["at"], part_id))
            return {"part": {"habit_id": part_id, "pattern": part["habit"]["action_pattern"],
                             "binding": part["habit"]["binding"], "contingency": inner}}

        known = by_step.get((impasse["at"], impasse["on"])) or []
        for node in known:
            if node["part"] in tried:
                continue
            part = lookup(node["part"])
            if part is None:
                refusals.append({"part": node["part"], "reason": "component_unavailable"})
            elif set(stack_of(part)) & set(stack):
                refusals.append({"part": node["part"], "reason": "component_cycle"})
            else:
                reasons = admissible(node["part"], part)
                if not reasons:
                    return chosen(node["part"], part, node["joins"])
                refusals.append({"part": node["part"], "reason": "component_refused", "reasons": reasons})
        if planner is not None:
            named = {node["part"] for node in known}
            for part in planner.parts:
                part_id = part["habit"]["id"]
                if part_id in tried or part_id in named or set(stack_of(part)) & set(stack):
                    continue
                if not admissible(part_id, part):
                    return chosen(part_id, part, [])
            if tried and not considered and not refusals:
                return None  # Every part that could join was tried here.
            planner.record({"path": list(planner.path), "join": None, "at": impasse["at"], "on": impasse["on"],
                            "considered": considered, "failure": canonical_failure(impasse["failure"])})
            return {"refused": {"reason": "no_part_joins", "considered": considered,
                                **({"refusals": refusals} if refusals else {})}}
        if not refusals:
            return None
        return {"refused": {"reason": "alternatives_exhausted", "refusals": refusals}}

    return decide


def recorded_contingency(pattern, joins, lookup: Callable[[str], Mapping[str, Any] | None], configuration, *,
                         stack: Iterable[str] = ()):
    """The contingency of a recorded composition: each join's alternatives, re-checked when they are needed."""
    return _contingency(pattern, joins, lookup, configuration, tuple(stack), (), None)


def planning_contingency(pattern, joins, parts: list[Mapping[str, Any]], configuration, record, *,
                         stack: Iterable[str] = ()):
    """The slow planner: known joins first; at a new impasse the first part that may join, in rank order.

    ``parts`` are admitted parts in rank order; ``record(entry)`` receives
    every hypothesis the planner forms (where, which part joined or none, and
    every part it considered with its reasons).
    """
    lookup = {part["habit"]["id"]: part for part in parts}
    return _contingency(pattern, joins, lookup.get, configuration, tuple(stack), (), _Planner(parts, record))


def canonical_failure(failure: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a failure event a hypothesis records: its tool, fields and failed arguments."""
    return {"tool": failure["failed_action"]["tool"], "fields": dict(failure["fields"]),
            "args": dict(failure["failed_action"]["args"])}
