"""Statements: what a session read from a recorded observation (refinement §15).

A statement says one thing about one subject — an entity reference of
``synapse.entity_identity``, compared exactly, never by search tokens: a
property, its value, whether the source asserts or denies it (polarity), the
conditions it holds under and the interval of the world it is about (valid
time). Its source is a recorded observation of the same session — the tool,
the arguments and the recorded answer's address — so the address is the
version of the source the statement was read from, and the statement is a
content-addressed record: the same statement from another source version is
another record. When the memory learned it (transaction time) is not part of
the statement: the court records it when it folds the statement into memory.

A statement is never an established fact. Its surface text serves search only;
admission reads its structured fields (refinement §11).

Valid times are ISO 8601 dates or date-times, kept in one canonical UTC form
so they order as text. How long a property stays true is declared per
property by the operator (``knowledge.properties``), after the event calculus's
common-sense law of inertia (Kowalski and Sergot): a ``state`` holds from its
start until a later statement of the same slot terminates it; a ``bounded``
state holds for its declared number of days and is of unknown currency after
that; an ``event`` is true of its moment and never goes stale. A property
nobody declared has unknown currency.
"""
from __future__ import annotations

from typing import Any, Mapping

from synapse import entity_identity
from synapse.palace_admission import instant as canonical_instant

from .. import records
from ..records import digest

FRESHNESS = ("state", "bounded", "event")
_MAX_CONDITIONS = 16
_MAX_TEXT = 1024


class StatementViolation(ValueError):
    """A statement is outside its declared shape."""


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def instant(value: Any) -> str | None:
    """One valid time in its canonical form (``YYYY-MM-DDTHH:MM:SSZ``), or ``None``: admission's own form."""
    if value is not None and type(value) is not str:
        raise StatementViolation("a valid time is an ISO 8601 date or date-time")
    try:
        return canonical_instant(value)
    except ValueError as exc:
        raise StatementViolation(str(exc)) from None


def _valid(value: Any) -> dict[str, str | None]:
    if value is None:
        return {"from": None, "until": None}
    if type(value) is not dict or not set(value) <= {"from", "until"}:
        raise StatementViolation("a statement's valid time names its start and end")
    start, end = instant(value.get("from")), instant(value.get("until"))
    if start is not None and end is not None and end <= start:
        raise StatementViolation("a statement's validity ends after it starts")
    return {"from": start, "until": end}


def declare(statement: Any, source: Mapping[str, Any], source_ref: str) -> dict[str, Any]:
    """The statement record read from the recorded answer ``source_ref`` of ``source`` (tool and arguments)."""
    allowed = {"subject", "property", "value", "polarity", "conditions", "valid", "text", "source"}
    if type(statement) is not dict or not {"subject", "property", "value", "text", "source"} <= set(statement) \
            or set(statement) - allowed:
        raise StatementViolation("a statement names subject, property, value, text and source "
                                 "(polarity, conditions and valid time optional)")
    try:
        subject = entity_identity.canonical(statement["subject"])
    except entity_identity.EntityViolation as exc:
        raise StatementViolation(f"a statement's subject is an entity: {exc}") from None
    if type(statement["property"]) is not str or not statement["property"].strip():
        raise StatementViolation("a statement's property is named")
    if not _scalar(statement["value"]):
        raise StatementViolation("a statement's value is a scalar")
    polarity = statement.get("polarity", True)
    if type(polarity) is not bool:
        raise StatementViolation("a statement's polarity asserts (true) or denies (false)")
    conditions = statement.get("conditions") or {}
    if (type(conditions) is not dict or len(conditions) > _MAX_CONDITIONS
            or any(type(key) is not str or not _scalar(value) for key, value in conditions.items())):
        raise StatementViolation("a statement's conditions are a small object of scalar fields")
    text = statement["text"]
    if type(text) is not str or not text.strip() or len(text) > _MAX_TEXT:
        raise StatementViolation("a statement's text is a bounded non-empty string")
    return records.make("statement", subject=subject, property=statement["property"],
                        value=statement["value"], polarity=polarity, conditions=dict(sorted(conditions.items())),
                        valid=_valid(statement.get("valid")), text=text,
                        source={"tool": source["tool"], "args": dict(source["args"]), "ref": source_ref})


def slot(record: Mapping[str, Any]) -> str:
    """What a statement is about: its subject, property and conditions (the fluent it concerns)."""
    return digest({"subject": record["subject"], "property": record["property"],
                   "conditions": record["conditions"]})


def source_identity(record: Mapping[str, Any]) -> str:
    """The source a statement was read from, without the answer's version."""
    return digest({"tool": record["source"]["tool"], "args": record["source"]["args"]})


def freshness_of(policy, prop: str) -> dict[str, Any] | None:
    """The operator's currency rule for one property, or ``None`` when nobody declared it."""
    return None if policy is None else policy.properties.get(prop)
