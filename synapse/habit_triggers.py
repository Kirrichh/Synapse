"""Typed habit triggers (memory spec part 1 §6): deterministic applicability.

A typed trigger subscribes to event types and states context labels and
``{field, op, value}`` conditions over the event's typed fields. Matching is
exact and ordered: all conditions hold — applicable; exactly one fails — a near
miss that names the failed condition; more fail — the candidate drops. A
missing field or a value of another JSON kind is undecidable and never counts
as a match: automation is not granted by missing information.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .memory_points import TypedCondition

TYPED_CONDITION_OPS = ("==", "!=", ">", ">=", "<", "<=", "in")


def _json_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def typed_condition_holds(condition: TypedCondition, fields: Dict[str, Any]) -> Optional[bool]:
    """Whether one typed condition holds; ``None`` when it cannot be decided.

    An absent field or a value of another JSON kind is undecidable, never a
    match: automation is not granted by missing information.
    """
    if condition.field not in fields:
        return None
    actual = fields[condition.field]
    expected = condition.value
    if condition.op == "in":
        if not isinstance(expected, list) or any(_json_kind(item) != _json_kind(actual) for item in expected):
            return None
        return actual in expected
    if _json_kind(actual) != _json_kind(expected):
        return None
    if condition.op == "==":
        return actual == expected
    if condition.op == "!=":
        return actual != expected
    if _json_kind(actual) not in {"number", "string"}:
        return None
    return {">": actual > expected, ">=": actual >= expected,
            "<": actual < expected, "<=": actual <= expected}[condition.op]


@dataclass(frozen=True)
class TypedTrigger:
    """Typed applicability of a habit: event types, context labels, conditions."""

    trigger_id: str
    event_types: Tuple[str, ...]
    context: Tuple[str, ...] = ()
    when: Tuple[TypedCondition, ...] = ()
    not_when: Tuple[TypedCondition, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_types or any(type(item) is not str or not item for item in self.event_types):
            raise ValueError("a typed trigger subscribes to named event types")
        for condition in self.when + self.not_when:
            if type(condition) is not TypedCondition or condition.op not in TYPED_CONDITION_OPS:
                raise ValueError("typed trigger condition is outside its closed vocabulary")

    def canonical(self) -> Dict[str, Any]:
        return {"event_types": list(self.event_types), "context": list(self.context) or "any",
                "when": [item.to_dict() for item in self.when],
                "not_when": [item.to_dict() for item in self.not_when]}

    def match(self, event: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
        """``applicable``, ``near_miss`` with its one failed condition, or ``drop``.

        Conditions are checked in their declared order, so the named failure is
        reproducible. An undecidable condition counts as failed.
        """
        if event.get("type") not in self.event_types:
            return "drop", None
        fields = event.get("fields") or {}
        failed: List[Dict[str, Any]] = []
        missing = sorted(set(self.context) - set(event.get("context_labels") or ()))
        if missing:
            failed.append({"field": "context", "op": "subset", "value": list(self.context),
                           "actual": list(event.get("context_labels") or ())})
        for condition in self.when:
            if typed_condition_holds(condition, fields) is not True:
                failed.append({**condition.to_dict(), "actual": fields.get(condition.field)})
        for condition in self.not_when:
            if typed_condition_holds(condition, fields) is not False:
                failed.append({**condition.to_dict(), "actual": fields.get(condition.field), "forbidden": True})
        if not failed:
            return "applicable", None
        if len(failed) == 1:
            return "near_miss", failed[0]
        return "drop", None


def declared_identity(canonical: Dict[str, Any]) -> str:
    """Identity of a program-declared habit or trigger: its canonical form."""
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
