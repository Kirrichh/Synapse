"""Result references between the steps of a learned body (refinement §13, D2).

A later call of a procedure may use a field of an earlier call's successful
answer. The court credits such a dependency only from the record:

* **origin** — an argument value is linked to a producer's answer only where
  the value appeared for the first time: it was not among the program's own
  literals, the session inputs, the reactive event, the failed action's
  arguments, or any argument or answer recorded before the producer answered.
  A value the driver or the program knew in advance is an *echo* and never a
  dependency, even when a later answer repeats it;
* **derivation** — a dependency is part of the binding only when every basis
  episode links the argument to the same producer field and the values differ
  between episodes (a value that never changes is a constant, not evidence of
  a data dependency);
* **resolution** — at execution the reference reads the producer's recorded
  answer: a failed or uncertain producer, a missing field, an empty value and a
  value of another kind are distinct refusals, and the dependent call is never
  made. A replay reads the same recorded answer, so it never produces a new
  external object.

Only answers of action calls expected to succeed can be producers; an error's
text may be read for diagnosis but never replaces a refusal.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping, Sequence

from synapse.habit_triggers import json_kind
from synapse.lexer import Lexer, TokenType

from ..records import canonical

#: How deep a path into an answer may reach.
MAX_DEPTH = 4
#: Values that carry no identity of their own and are never linked to a producer.
_ANONYMOUS = {canonical(value) for value in (None, True, False, "", [], {})}
REFUSALS = ("producer_failed", "producer_uncertain", "field_missing", "field_empty", "type_mismatch",
            "invalid_reference")


class DependencyUnavailable(ValueError):
    """A referenced producer answer does not provide the value its consumer needs."""

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause


def leaves(value: Any, path: tuple = (), depth: int = 0) -> Iterator[tuple[tuple, Any]]:
    """Every value inside a JSON value with its path of object keys (bounded)."""
    yield path, value
    if isinstance(value, dict) and depth < MAX_DEPTH:
        for key in sorted(value):
            yield from leaves(value[key], (*path, key), depth + 1)


def _canonical_leaves(value: Any, depth: int = 0) -> set[bytes]:
    """Canonical forms of a value and of everything inside it (objects and arrays, bounded)."""
    found = set()
    for _, item in leaves(value):
        found.add(canonical(item))
        if isinstance(item, list) and depth < MAX_DEPTH:
            for entry in item:
                found |= _canonical_leaves(entry, depth + 1)
    return found


def program_literals(source_code: str) -> set[bytes]:
    """The string and number literals of a program: values its author (or driver) knew in advance."""
    try:
        tokens = Lexer(source_code).scan_tokens()
    except SyntaxError:
        return set()
    return {canonical(token.value) for token in tokens if token.type in (TokenType.STRING, TokenType.NUMBER)}


class Knowledge:
    """What a session knew before each recorded answer: the reference point of first appearance."""

    def __init__(self, *, source_code: str, inputs: Any, attempts: Iterable[Mapping[str, Any]],
                 payloads: Mapping[int, Any]) -> None:
        self.base = program_literals(source_code) | _canonical_leaves(inputs)
        self.timeline = sorted(((item["gw_seq"], item) for item in attempts), key=lambda pair: pair[0])
        self.payloads = payloads

    def before_answer(self, gw_seq: int, extra: Iterable[Any] = ()) -> set[bytes]:
        """Values known before the answer of the attempt ``gw_seq``: its own arguments included."""
        known = set(self.base)
        for value in extra:
            known |= _canonical_leaves(value)
        for seq, attempt in self.timeline:
            if seq > gw_seq:
                break
            known |= _canonical_leaves(attempt["args"])
            if seq < gw_seq:
                known |= _canonical_leaves(self.payloads.get(seq))
        return known


def origins(calls: Sequence[Mapping[str, Any]], knowledge: Knowledge, event: Mapping[str, Any]) -> list[dict]:
    """Per call: each argument's producer links (first appearance) and echoes (known before).

    ``calls`` are the episode's program calls in order, each with ``gw_seq``,
    ``args``, ``ok`` and the recorded ``payload``.
    """
    context = [event.get("fields") or {}, (event.get("failed_action") or {}).get("args") or {}]
    known = [knowledge.before_answer(call["gw_seq"], context) for call in calls]
    found = []
    for index, call in enumerate(calls):
        entry = {}
        for name, value in sorted(call["args"].items()):
            key = canonical(value)
            if key in _ANONYMOUS:
                continue
            links, echoes = [], []
            for step in range(index):
                producer = calls[step]
                if not producer["ok"] or not isinstance(producer["payload"], dict):
                    continue
                for path, item in leaves(producer["payload"]):
                    if path and canonical(item) == key:
                        (echoes if key in known[step] else links).append({"step": step, "path": list(path)})
            if links or echoes:
                entry[name] = {"links": links, "echoes": echoes}
        found.append(entry)
    return found


def derive(values: Sequence[Any], per_episode: Sequence[Mapping[str, Any] | None]) -> dict[str, Any] | None:
    """The one producer field every basis episode links this argument to, if the evidence establishes it.

    ``values`` are the argument's values in each episode; ``per_episode`` its
    origin entries. Returns ``{"step", "path", "kind"}`` or ``None``.
    """
    if any(entry is None or not entry["links"] for entry in per_episode):
        return None
    shared = {canonical(link): link for link in per_episode[0]["links"]}
    for entry in per_episode[1:]:
        shared = {key: link for key, link in shared.items() if key in {canonical(item) for item in entry["links"]}}
    kinds = {json_kind(value) for value in values}
    if not shared or len({canonical(value) for value in values}) < 2 or len(kinds) != 1:
        return None
    link = shared[min(shared, key=lambda key: (shared[key]["step"], key))]
    return {"step": link["step"], "path": list(link["path"]), "kind": kinds.pop()}


def echoed(per_episode: Sequence[Mapping[str, Any] | None]) -> bool:
    """Whether the argument repeated a producer's value only because it was known in advance."""
    return any(entry is not None and entry["echoes"] and not entry["links"] for entry in per_episode)


def resolve(source: Mapping[str, Any], views: Sequence[Mapping[str, Any]]) -> Any:
    """The value a result reference reads from the recorded producer answer."""
    step = source["step"]
    if not 0 <= step < len(views):
        raise DependencyUnavailable("invalid_reference", f"step {step} has not answered")
    view = views[step]
    if not view.get("ok"):
        if view.get("effect") == "unknown":
            raise DependencyUnavailable("producer_uncertain", f"the answer of step {step} is unknown")
        raise DependencyUnavailable("producer_failed", f"step {step} did not succeed")
    value = view.get("payload")
    for key in source["path"]:
        if not isinstance(value, dict) or key not in value:
            raise DependencyUnavailable("field_missing", f"the answer of step {step} has no {'.'.join(source['path'])}")
        value = value[key]
    if canonical(value) in _ANONYMOUS:
        raise DependencyUnavailable("field_empty", f"the answer of step {step} gives an empty value")
    if json_kind(value) != source["kind"]:
        raise DependencyUnavailable("type_mismatch", f"the answer of step {step} gives a {json_kind(value)}, "
                                                     f"not a {source['kind']}")
    return value


def _schema_type(schema: Any, path: Sequence[str]) -> str | None:
    for key in path:
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict) or key not in properties:
            return None
        schema = properties[key]
    declared = schema.get("type") if isinstance(schema, dict) else None
    return {"string": "string", "integer": "number", "number": "number", "boolean": "bool", "object": "object",
            "array": "array", "null": "null"}.get(declared) if isinstance(declared, str) else None


def check_types(source: Mapping[str, Any], producer: Any, consumer: Any, argument: str) -> str | None:
    """Why a reference is incompatible with the declared tool schemas, if they declare the types."""
    produced = _schema_type(producer.output_schema, source["path"]) if producer is not None else None
    consumed = _schema_type(consumer.input_schema, [argument]) if consumer is not None else None
    for declared, side in ((produced, "producer"), (consumed, "consumer")):
        if declared is not None and declared != source["kind"]:
            return f"the {side} schema declares {declared} where the record shows {source['kind']}"
    return None
