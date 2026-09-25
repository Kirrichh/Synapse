"""Typed triggers: generalization, coverage inclusion and boundary changes.

A trigger is generalized from the typed contexts of its basis episodes by
least general generalization (anti-unification): a field with one value in
every episode becomes a ``when`` condition, a field whose values differ is
dropped, and context labels shared by every episode remain required.
``not_when`` is empty at birth and appears only through a successor (spec
part 1 §6.4). Generalization is deterministic and uses no model.

Coverage inclusion decides key habits (spec part 2 §4.6): a habit is key when no
other executable habit with the same expected outcome covers its coverage. The
check is sufficient, never approximate: an inclusion it cannot prove is
reported as absent, which keeps the habit key and therefore keeps it loaded.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from synapse.habit_triggers import TypedTrigger, typed_condition_holds
from synapse.memory_points import TypedCondition

from .. import records


def condition_key(trigger: Mapping[str, Any]) -> dict[str, Any]:
    """The applicability of a trigger record, without its origin."""
    return {"event_types": list(trigger["event_types"]), "context": trigger["context"],
            "when": list(trigger["when"]), "not_when": list(trigger["not_when"])}


def runtime_trigger(trigger: Mapping[str, Any]) -> TypedTrigger:
    """The registry's typed trigger for one trigger record."""
    context = () if trigger["context"] == "any" else tuple(trigger["context"])
    return TypedTrigger(trigger["id"], tuple(trigger["event_types"]), context,
                        tuple(TypedCondition(**item) for item in trigger["when"]),
                        tuple(TypedCondition(**item) for item in trigger["not_when"]))


def typed_context(event: Mapping[str, Any]) -> dict[str, Any]:
    """The typed context of one reactive event: its type, fields and context labels."""
    fields = {key: value for key, value in sorted((event.get("fields") or {}).items())
              if isinstance(value, (str, int, float, bool)) and value is not None}
    return {"event_type": event["type"], "fields": fields, "labels": sorted(set(event.get("context_labels") or ()))}


def anti_unify(contexts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Least general generalization of typed contexts (one event type)."""
    contexts = list(contexts)
    if not contexts or len({item["event_type"] for item in contexts}) != 1:
        raise ValueError("generalization needs typed contexts of one event type")
    shared = set(contexts[0]["fields"])
    for item in contexts[1:]:
        shared &= set(item["fields"])
    when = []
    for name in sorted(shared):
        values = [item["fields"][name] for item in contexts]
        if all(type(value) is type(values[0]) and value == values[0] for value in values):
            when.append({"field": name, "op": "==", "value": values[0]})
    labels = set(contexts[0]["labels"])
    for item in contexts[1:]:
        labels &= set(item["labels"])
    return {"event_types": [contexts[0]["event_type"]], "context": sorted(labels) or "any",
            "when": when, "not_when": []}


def context_template(generalized: Mapping[str, Any], contexts: Iterable[Mapping[str, Any]]) -> str:
    """Template text of the typed fields, for a configured similarity scorer only."""
    names = sorted({name for item in contexts for name in item["fields"]})
    head = generalized["event_types"][0]
    return head + ": " + ", ".join(f"{name} {{{name}}}" for name in names)


def render_template(template: str, event: Mapping[str, Any]) -> str:
    fields = event.get("fields") or {}
    text = template
    for name, value in sorted(fields.items()):
        text = text.replace("{" + name + "}", str(value))
    return text


def make_trigger(condition: Mapping[str, Any], *, template: str, born_from: str,
                 source_episodes: list[Mapping[str, Any]]) -> dict[str, Any]:
    return records.make("habit_trigger", event_types=list(condition["event_types"]), context=condition["context"],
                        when=list(condition["when"]), not_when=list(condition["not_when"]),
                        context_template=template, born_from=born_from,
                        source_episodes=sorted(source_episodes, key=lambda item: (item["qid"], item["steps"])))


def matches(trigger: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[str, dict | None]:
    """Status of one typed context under a trigger record (runtime semantics)."""
    event = {"type": context["event_type"], "fields": dict(context["fields"]), "context_labels": list(context["labels"])}
    return runtime_trigger({**trigger, "id": trigger.get("id", "trg_probe")}).match(event)


def _implied(condition: Mapping[str, Any], facts: list[Mapping[str, Any]]) -> bool:
    """Whether ``condition`` holds for every event that satisfies all ``facts``."""
    for fact in facts:
        if fact == condition:
            return True
        if fact["field"] == condition["field"] and fact["op"] == "==":
            if typed_condition_holds(TypedCondition(**condition), {fact["field"]: fact["value"]}) is True:
                return True
    return False


def _excluded(condition: Mapping[str, Any], facts: list[Mapping[str, Any]], forbidden: list) -> bool:
    """Whether ``condition`` is false for every event satisfying ``facts`` and none of ``forbidden``."""
    if condition in forbidden:
        return True
    for fact in facts:
        if fact["field"] == condition["field"] and fact["op"] == "==":
            if typed_condition_holds(TypedCondition(**condition), {fact["field"]: fact["value"]}) is False:
                return True
    return False


def covers(wider: Mapping[str, Any], narrower: Mapping[str, Any]) -> bool:
    """Provable inclusion: every event the narrower trigger admits, the wider admits."""
    if not set(narrower["event_types"]) <= set(wider["event_types"]):
        return False
    wide_context = [] if wider["context"] == "any" else list(wider["context"])
    narrow_context = [] if narrower["context"] == "any" else list(narrower["context"])
    if not set(wide_context) <= set(narrow_context):
        return False
    if any(not _implied(item, list(narrower["when"])) for item in wider["when"]):
        return False
    return all(_excluded(item, list(narrower["when"]), list(narrower["not_when"])) for item in wider["not_when"])


def narrowed(trigger: Mapping[str, Any], subcontext: Mapping[str, Any]) -> dict[str, Any]:
    """Successor applicability that forbids one failing subcontext."""
    condition = condition_key(trigger)
    if subcontext in condition["not_when"]:
        raise ValueError("the subcontext is already forbidden")
    return {**condition, "not_when": [*condition["not_when"], dict(subcontext)]}


def widened(trigger: Mapping[str, Any], failed: Mapping[str, Any]) -> dict[str, Any]:
    """Successor applicability without the one condition its near misses failed."""
    condition = condition_key(trigger)
    probe = {key: failed[key] for key in ("field", "op", "value")}
    if failed.get("forbidden"):
        remaining = [item for item in condition["not_when"] if item != probe]
        if len(remaining) == len(condition["not_when"]):
            raise ValueError("the failed condition is not a condition of this trigger")
        return {**condition, "not_when": remaining}
    if failed.get("field") == "context":
        return {**condition, "context": "any"}
    remaining = [item for item in condition["when"] if item != probe]
    if len(remaining) == len(condition["when"]):
        raise ValueError("the failed condition is not a condition of this trigger")
    return {**condition, "when": remaining}
