"""Which thing a record, a claim or a hypothesis is about (review R4).

Search normalization finds candidates; it never decides identity. Folding
case, dropping short words or reordering them makes ``account-1`` and
``account-2`` one set of search tokens — and two different accounts. Identity
is decided here, and only here, by exact rules:

* An entity reference is a string — an identifier in the default namespace —
  or an object ``{"namespace", "type", "id", "uid"}`` (every field but ``id``
  optional). ``namespace`` and ``type`` are names; ``id`` is a string, an
  integer or a boolean, compared with its type (``"1"``, ``1`` and ``true``
  are three identifiers); ``uid`` names one incarnation of the object.
* Two references are the same entity when their namespace, type and id are
  equal. Strings are compared in Unicode NFC; case is folded only in a
  namespace the operator declares case-insensitive (``identity.namespaces``
  of the memory configuration) — never by a general search rule.
* A name outlives the object it names: an object deleted and created again
  under the same name is another incarnation (Kubernetes distinguishes an
  object's name from its UID for this reason). When either reference names an
  incarnation, both must name the same one; a reference without one cannot
  be matched with one that has it.
* An alias is never inferred from similarity. It is a separate link a
  confirmed hypothesis of aspect ``entity`` establishes (its subject is one
  reference, its statement ``{"same_as": <rendered other reference>}``), with
  that hypothesis's scope and source version; when the hypothesis is no longer
  confirmed, the alias no longer holds.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, NamedTuple
import re
import unicodedata

_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_MAX_ID = 512
CASE_RULES = ("sensitive", "insensitive")


class EntityViolation(ValueError):
    """An entity reference or an identity rule is outside its declared shape."""


class EntityRef(NamedTuple):
    namespace: str | None
    type: str | None
    id: Any
    uid: str | None


def _name(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _NAME_RE.fullmatch(value) is None:
        raise EntityViolation(f"an entity {what} is a bounded name")
    return value


def _identifier(value: Any) -> Any:
    if type(value) is str:
        normalized = unicodedata.normalize("NFC", value)
        if not normalized.strip() or len(normalized) > _MAX_ID:
            raise EntityViolation("an entity identifier is a non-empty bounded string")
        return normalized
    if type(value) in (int, bool):
        return value
    raise EntityViolation("an entity identifier is a string, an integer or a boolean")


def parse(value: Any) -> EntityRef:
    """The reference a string or an object names."""
    if type(value) is str:
        return EntityRef(None, None, _identifier(value), None)
    if type(value) is not dict or "id" not in value or set(value) - {"namespace", "type", "id", "uid"}:
        raise EntityViolation("an entity is an identifier or {namespace, type, id, uid}")
    uid = value.get("uid")
    if uid is not None and (type(uid) is not str or not uid.strip() or len(uid) > _MAX_ID):
        raise EntityViolation("an entity incarnation (uid) is a non-empty bounded string")
    return EntityRef(_name(value.get("namespace"), "namespace"), _name(value.get("type"), "type"),
                     _identifier(value["id"]), uid)


def canonical(value: Any) -> Any:
    """The reference in its stored form: the identifier itself for a plain name, an object otherwise."""
    ref = parse(value)
    if ref.namespace is None and ref.type is None and ref.uid is None and type(ref.id) is str:
        return ref.id
    return {key: item for key, item in ref._asdict().items() if item is not None}


def render(value: Any) -> str:
    """One text for a reference: a plain name is itself, anything else its canonical JSON."""
    form = canonical(value)
    return form if type(form) is str else json.dumps(form, sort_keys=True, ensure_ascii=False,
                                                     separators=(",", ":"))


def parse_rules(value: Any) -> dict[str, str]:
    """The operator's case rule of each namespace (``{"<namespace>": {"case": "insensitive"}}``)."""
    if value is None:
        return {}
    if type(value) is not dict or set(value) != {"namespaces"} or type(value["namespaces"]) is not dict:
        raise EntityViolation("identity declares its namespaces")
    rules = {}
    for namespace, rule in sorted(value["namespaces"].items()):
        _name(namespace, "namespace")
        if type(rule) is not dict or set(rule) != {"case"} or rule["case"] not in CASE_RULES:
            raise EntityViolation(f"namespace {namespace!r} declares its case rule: sensitive or insensitive")
        rules[namespace] = rule["case"]
    return rules


def _key(ref: EntityRef, rules: Mapping[str, str]) -> Any:
    if type(ref.id) is str and rules.get(ref.namespace or "") == "insensitive":
        return ref.id.casefold()
    return ref.id


def compare(left: Any, right: Any, rules: Mapping[str, str] | None = None) -> str | None:
    """``None`` when both references name the same entity, otherwise why they do not."""
    rules = rules or {}
    try:
        a, b = parse(left), parse(right)
    except EntityViolation:
        return "entity_unreadable"
    if a.namespace != b.namespace:
        return "another_namespace"
    if a.type != b.type:
        return "another_type"
    if type(a.id) is not type(b.id) or _key(a, rules) != _key(b, rules):
        return "another_entity"
    if a.uid != b.uid:
        return "incarnation_unknown" if a.uid is None or b.uid is None else "another_incarnation"
    return None


def alias_holds(hypothesis: Mapping[str, Any] | None, left: Any, right: Any,
                rules: Mapping[str, str] | None = None) -> str | None:
    """``None`` when a confirmed entity hypothesis links ``left`` to ``right``, otherwise why not."""
    if hypothesis is None:
        return "alias_unknown"
    if hypothesis.get("aspect") != "entity" or set(hypothesis.get("statement") or {}) != {"same_as"}:
        return "alias_not_an_identity_link"
    if hypothesis.get("status") != "confirmed":
        return f"alias_not_established:{hypothesis.get('status')}"
    subject, other = hypothesis.get("subject"), hypothesis["statement"]["same_as"]
    for one, two in ((left, right), (right, left)):
        if compare(subject, one, rules) is None and type(other) is str and compare(_linked(other), two, rules) is None:
            return None
    return "alias_links_other_entities"


def _linked(text: str) -> Any:
    """A rendered reference back to its form (a plain name, or canonical JSON of an object)."""
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text
