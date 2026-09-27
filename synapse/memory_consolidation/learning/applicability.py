"""Applicability of a learned procedure from contrasting episodes (refinement §12, D1).

The positive episodes of a candidate show where its procedure works; a
verified failure of the same procedure (the same typed steps, a failed
segment) shows where it does not. Generalization is deterministic and uses no
model:

* the least general generalization of the positives is the base: a field with
  one value in every positive is a ``==`` condition, a field whose values
  differ is dropped, shared context labels stay required;
* each contrast is compared with the base alone, so the result never depends
  on the order the court saw them: its discriminators are the base conditions
  it violates and the varying fields whose value no positive had. A contrast
  with exactly one discriminator is near: that field is **essential**. A
  contrast inside the base gets a boundary drawn from the positives' values
  (a number beyond one side of their range bounds that side, anything else
  becomes the set of values seen); several discriminators bound every one of
  them without proving which matters. A contrast without any discriminator is
  an unexplained contradiction and blocks the birth;
* a field the binding reads whose value does not matter keeps an ``is``
  condition of its one JSON kind, so an absent field or another kind never
  lets the procedure run;
* the explanation lists essential fields with their contrasts, bounded,
  irrelevant (proven by variation), unproven (constant, never contrasted) and
  required fields. It is part of the trigger's identity.

A boundary moves only by evidence: recovered near misses extend the admitted
values of the condition they failed — never remove it — and no extension may
admit a value a recorded contrast failed at.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from synapse.habit_triggers import json_kind, typed_condition_holds
from synapse.memory_points import TypedCondition

from ..records import canonical
from .triggers import anti_unify, sort_conditions, sort_values

APPLICABILITY_V1 = "synapse.memory.applicability/v1"
_BOUNDS = ("<=", ">=")


class BoundaryUnavailable(ValueError):
    """No condition can admit the positives while excluding a recorded contrast."""


def _number(value: Any) -> bool:
    return json_kind(value) == "number"


def _values(positives: Sequence[Mapping[str, Any]], name: str) -> list[Any]:
    return sort_values(item["context"]["fields"][name] for item in positives)


def _shared(positives: Sequence[Mapping[str, Any]]) -> set[str]:
    names = set(positives[0]["context"]["fields"])
    for item in positives[1:]:
        names &= set(item["context"]["fields"])
    return names


def _holds(condition: Mapping[str, Any], fields: Mapping[str, Any]) -> bool:
    return typed_condition_holds(TypedCondition(**condition), dict(fields)) is True


def _discriminators(base, varying, positives, contrast) -> list[str]:
    """Base conditions the contrast violates and varying fields with a value no positive had."""
    fields, labels = contrast["context"]["fields"], set(contrast["context"]["labels"])
    found = []
    if base["context"] != "any" and not set(base["context"]) <= labels:
        found.append("context")
    found += [item["field"] for item in base["when"] if not _holds(item, fields)]
    for name in sorted(varying):
        seen = {canonical(value) for value in _values(positives, name)}
        if name in fields and canonical(fields[name]) not in seen:
            found.append(name)
    return sorted(set(found))


def _boundary(values: list[Any], contrast_value: Any) -> dict[str, Any]:
    """The condition that admits every positive value and excludes the contrast's."""
    if all(_number(value) for value in values) and _number(contrast_value):
        if contrast_value > max(values):
            return {"op": "<=", "value": max(values)}
        if contrast_value < min(values):
            return {"op": ">=", "value": min(values)}
    return {"op": "in", "value": values}


def _merge(name: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One field's boundary conditions: a value set subsumes the range bounds of the same values."""
    ops = {item["op"]: item["value"] for item in found}
    if "in" in ops:
        return [{"field": name, "op": "in", "value": ops["in"]}]
    return [{"field": name, "op": op, "value": ops[op]} for op in _BOUNDS if op in ops]


def _reference(item: Mapping[str, Any], name: str | None = None) -> dict[str, Any]:
    ref = {"qid": item["qid"], "steps": list(item["steps"]), "run_id": item["run_id"], "event_id": item["event_id"]}
    if name is not None:
        fields = item["context"]["fields"]
        ref["value"] = fields.get(name) if name != "context" else item["context"]["labels"]
    return ref


def _required(names: Iterable[str], positives, conditions) -> list[dict[str, Any]]:
    """``is`` conditions for the fields the binding reads and no condition already pins."""
    pinned = {item["field"] for item in conditions}
    found = []
    for name in sorted(set(names) - pinned):
        kinds = {json_kind(item["context"]["fields"].get(name)) for item in positives
                 if name in item["context"]["fields"]}
        if len(kinds) != 1 or any(name not in item["context"]["fields"] for item in positives):
            raise BoundaryUnavailable(f"the body reads field {name!r}, which the positives do not carry with one kind")
        found.append({"field": name, "op": "is", "value": kinds.pop()})
    return found


def generalize(positives: Sequence[Mapping[str, Any]], contrasts: Sequence[Mapping[str, Any]],
               required: Iterable[str] = ()) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """The applicability condition, its explanation and the contrasts it cannot explain.

    ``positives`` and ``contrasts`` are pool episodes (typed ``context`` and
    their address); ``required`` names the event fields the binding reads.
    """
    base = anti_unify(item["context"] for item in positives)
    shared = _shared(positives)
    varying = {name for name in shared if len(_values(positives, name)) > 1}
    essential: dict[str, list] = {}
    bounded: set[str] = set()
    boundaries: dict[str, list] = {}
    unexplained = []
    for contrast in sorted(contrasts, key=lambda item: canonical(_reference(item))):
        found = _discriminators(base, varying, positives, contrast)
        if not found:
            unexplained.append(_reference(contrast))
            continue
        inside = [name for name in found if name in varying]
        if len(found) == 1:
            essential.setdefault(found[0], []).append(_reference(contrast, found[0]))
        if len(inside) == len(found):
            # Inside the base: only a drawn boundary excludes it.
            for name in inside:
                boundaries.setdefault(name, []).append(
                    _boundary(_values(positives, name), contrast["context"]["fields"][name]))
            if len(inside) > 1:
                bounded.update(inside)
    added = [condition for name in sorted(boundaries) for condition in _merge(name, boundaries[name])]
    typed = _required(required, positives, [*base["when"], *added])
    condition = {**base, "when": sort_conditions([*base["when"], *added, *typed]),
                 "not_when": sort_conditions(base["not_when"])}
    constant = sorted(item["field"] for item in base["when"])
    explanation = {
        "schema_version": APPLICABILITY_V1,
        "essential": [{"field": name, "contrasts": sorted(refs, key=canonical)}
                      for name, refs in sorted(essential.items())],
        "bounded": sorted(bounded - set(essential)),
        "irrelevant": [{"field": name, "values": len(_values(positives, name))}
                       for name in sorted(varying - set(boundaries))],
        "unproven": [name for name in constant if name not in essential],
        "required": [{"field": item["field"], "kind": item["value"]} for item in typed],
        "basis": len(positives),
    }
    return condition, explanation, unexplained


def explanation_of(trigger: Mapping[str, Any]) -> dict[str, Any]:
    """A trigger's applicability explanation; a trigger of schema 1.1 was born without one."""
    return dict(trigger.get("applicability") or {"schema_version": APPLICABILITY_V1, "essential": [],
                                                 "bounded": [], "irrelevant": [], "unproven": [],
                                                 "required": [], "basis": None})


def _contrast_values(explanation: Mapping[str, Any], name: str) -> list[Any]:
    return [ref["value"] for entry in explanation["essential"] if entry["field"] == name
            for ref in entry["contrasts"]]


def _extended(condition: Mapping[str, Any], values: list[Any]) -> dict[str, Any] | None:
    """The same field condition admitting ``values`` as well, or ``None`` when it cannot."""
    kind = {json_kind(value) for value in values}
    op, current = condition["op"], condition["value"]
    if op == "is" or len(kind) != 1:
        return None
    if op in _BOUNDS:
        if not all(_number(value) for value in values):
            return None
        edge = max([current, *values]) if op == "<=" else min([current, *values])
        return {**condition, "value": edge}
    admitted = [current] if op == "==" else list(current) if op == "in" else None
    if admitted is None or {json_kind(value) for value in admitted} != kind:
        return None
    return {"field": condition["field"], "op": "in", "value": sort_values([*admitted, *values])}


def widen(trigger: Mapping[str, Any], failed: Mapping[str, Any], recovered: list[Any]) -> tuple[dict, dict]:
    """Successor applicability admitting the values its recovered near misses failed at.

    Raises ``BoundaryUnavailable`` when the condition cannot be extended or the
    extension would admit a value a recorded contrast failed at.
    """
    probe = {key: failed[key] for key in ("field", "op", "value")}
    conditions = [dict(item) for item in trigger["when"]]
    if probe not in conditions:
        raise BoundaryUnavailable("the failed condition is not a condition of this trigger")
    extended = _extended(probe, recovered)
    if extended is None:
        raise BoundaryUnavailable(f"condition {probe['field']} {probe['op']} cannot admit the recovered values")
    explanation = explanation_of(trigger)
    blocking = [value for value in _contrast_values(explanation, probe["field"])
                if _holds(extended, {probe["field"]: value})]
    if blocking:
        raise BoundaryUnavailable(f"widening {probe['field']} would admit a recorded contrast")
    conditions[conditions.index(probe)] = extended
    condition = {"event_types": list(trigger["event_types"]), "context": trigger["context"],
                 "when": sort_conditions(conditions), "not_when": list(trigger["not_when"])}
    return condition, explanation


def narrow_explanation(trigger: Mapping[str, Any], subcontext: Mapping[str, Any],
                       failures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The explanation of a narrowed successor: its verified failures are contrasts of that field."""
    explanation = explanation_of(trigger)
    name = subcontext["field"]
    refs = [{"run_id": item.get("run_id"), "event_id": item.get("event_id"), "value": item["fields"].get(name)}
            for item in failures]
    essential = {entry["field"]: list(entry["contrasts"]) for entry in explanation["essential"]}
    essential[name] = sorted([*essential.get(name, []), *refs], key=canonical)
    explanation["essential"] = [{"field": field, "contrasts": refs} for field, refs in sorted(essential.items())]
    explanation["unproven"] = [field for field in explanation["unproven"] if field != name]
    return explanation
