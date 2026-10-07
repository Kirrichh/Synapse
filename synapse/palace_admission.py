"""Palace candidates and fact admission are two different acts (refinement §11).

``recall`` returns candidates: records whose content shares normalized tokens
with the query, scored only by that lexical overlap (scorer
``palace-lexical/v2``). A record's confidence, its insertion time and its
metadata never enter the score; zero overlap is no lexical evidence and no
candidate; equal scores are ordered by record identity. Search tokens find
candidates; they never decide which entity a record is about.

``admit(candidates, claim)`` decides whether one candidate may be used as an
established fact for the claim. Every check is separate and reported:

* the entity is the claim's entity by the exact identity rules of
  ``synapse.entity_identity`` (namespace, type, typed identifier,
  incarnation) — or linked to it by an alias a confirmed entity hypothesis
  establishes (the claim's ``identified_by``); the attribute and the kind of
  information are the claim's;
* the scope and the time of validity cover the claim; unknown freshness of a
  time-bound record is no admission;
* the record names its provenance;
* it asserts what the claim asks — a denial (``polarity`` false) never answers
  an assertion — under the claim's conditions, and a record of unknown
  currency (``freshness`` unknown or undeclared) is no admission;
* the authority to treat it as established is never the record's own: a
  ``status`` the content states describes that content and grants nothing
  (review R3). The basis is a hypothesis of the verification journal — the one
  the record or the claim (``verified_by``) names — whose live status is
  ``confirmed`` and which states this very statement: a content hypothesis
  about the same entity, stating the record's attribute with the same typed
  value (or, for a boolean denial, its negation), under the same conditions,
  for the claim's scope, read from the source the record names (its name or
  its tool) and, when the record names the source version it was read from,
  for that version. A confirmation of anything else — another
  entity, property, value, type, polarity, scope or version — confirms nothing;
  a basis that is no longer confirmed no longer admits. Outside a memory
  session there is no journal, so nothing is admitted;
* every candidate whose named basis the journal knows carries its
  attestation (``ATTESTATION_V1``, after the in-toto discipline of binding a
  subject, a predicate type and its verifier): the statement (kind, exact
  entity, property, typed value, polarity, conditions, scope), its version
  (source version, validity period, the instant asked about), the
  verification (method and rule of the check, the contract it ran under, the
  recorded observations it read, the session and court window that decided
  it), the provenance (the record, its source, the identity link used, the
  copies of the same statement), the outcome for this very statement
  (``confirmed``, ``refuted`` or ``undecided`` — a check no longer fresh
  decides nothing, it never refutes) and the dependencies whose revocation
  or change requires admitting again. The admitted fact's attestation is the
  decision's; a hash or a source name alone attests nothing;
* at least two distinct normalized key tokens of the claim occur in the record
  and its lexical score reaches the threshold — a filter on candidates, never
  a proof of content;
* admitted candidates that state different values for the same entity and
  attribute are a conflict: nothing is admitted, and neither the later nor the
  more confident one wins. Copies of one statement count once.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Iterable, Mapping

from . import entity_identity

SCORER_VERSION = "palace-lexical/v2"
ATTESTATION_V1 = "synapse.memory.admission-attestation/v1"
MIN_KEY_TOKENS = 2
DEFAULT_THRESHOLD = 0.5
_TOKEN = re.compile(r"\w+", re.UNICODE)
#: Fields that describe a record rather than state its content.
METADATA_FIELDS = frozenset({
    "id", "trace_id", "created_at", "confidence", "room", "palace", "score", "matched_tokens", "scorer",
    "source", "status", "hypothesis", "kind", "valid_from", "valid_until", "scope", "source_room",
    "routing_tags", "affective_tag", "affective_tag_id", "affective_tag_snapshot", "affective_expires_at_event",
    "affective_decay", "affective_decay_original", "affective_expired",
    # Semantic knowledge candidates: how the statement is held and found, never what it says.
    "polarity", "conditions", "freshness", "known_from", "known_until", "rank", "found_by"})


def holds_at(freshness: Any, start: Any, end: Any, at: Any) -> bool:
    """Whether a statement valid over ``[start, end)`` holds at the instant ``at`` (canonical forms): an event
    holds exactly at its moment and nowhere else (no end makes it a state); anything else holds from its start
    until its end. ``at`` is a time: an event placed nowhere holds at none. The one rule admission and the
    knowledge timeline place a time with."""
    if freshness == "event":
        return at == start
    return (start is None or start <= at) and (end is None or at < end)


def instant(value: Any) -> Any:
    """A validity time in the one form admission compares: ISO 8601 dates and date-times become
    ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, ordering as text); a number stays a number; ``None`` stays ``None``."""
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return value
    if type(value) is not str:
        raise ValueError("a validity time is an ISO 8601 date or date-time, or a number")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("a validity time is an ISO 8601 date or date-time, or a number") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tokens(text: str) -> frozenset[str]:
    """Normalized tokens: case-folded words of two characters or more."""
    return frozenset(token for token in _TOKEN.findall(text.casefold()) if len(token) > 1)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def content_tokens(record: Mapping[str, Any]) -> frozenset[str]:
    """Tokens of a record's content fields only."""
    found: set[str] = set()
    for key, value in record.items():
        if key not in METADATA_FIELDS:
            for text in _strings(value):
                found |= tokens(text)
    return frozenset(found)


def candidate_score(query: str, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The lexical score of one record for a query, or ``None`` when it shares no token."""
    wanted = tokens(query or "")
    if not wanted:
        return {"score": 0.0, "matched_tokens": [], "scorer": SCORER_VERSION}
    matched = sorted(wanted & content_tokens(record))
    if not matched:
        return None
    return {"score": round(len(matched) / len(wanted), 6), "matched_tokens": matched, "scorer": SCORER_VERSION}


def rank(records: Iterable[Mapping[str, Any]], query: str, threshold: float) -> list[dict[str, Any]]:
    """Candidates at or above ``threshold``, by score and then identity."""
    ranked = []
    for record in records:
        scored = candidate_score(query, record)
        if scored is not None and scored["score"] >= threshold:
            ranked.append({**dict(record), **scored})
    return sorted(ranked, key=lambda item: (-item["score"], str(item.get("id", ""))))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _claim(value: Any) -> dict[str, Any]:
    required = {"entity", "attribute", "keys"}
    if type(value) is not dict or not required <= set(value) or set(value) - required - {
            "kind", "scope", "at", "threshold", "polarity", "conditions", "verified_by", "identified_by"}:
        raise ValueError("an admission claim names entity, attribute and keys (kind, scope, at, threshold, "
                         "polarity, conditions, verified_by and identified_by optional)")
    if type(value.get("polarity", True)) is not bool or type(value.get("conditions") or {}) is not dict:
        raise ValueError("an admission claim's polarity is a boolean and its conditions an object")
    keys = value["keys"]
    if type(keys) is not list or any(type(item) is not str for item in keys):
        raise ValueError("admission keys are strings")
    threshold = value.get("threshold", DEFAULT_THRESHOLD)
    if type(threshold) not in {int, float} or not 0 < threshold <= 1:
        raise ValueError("an admission threshold is in (0, 1]")
    return {"entity": entity_identity.canonical(value["entity"]), "attribute": str(value["attribute"]),
            "kind": value.get("kind", "fact"), "scope": value.get("scope"), "at": instant(value.get("at")),
            "keys": keys, "threshold": float(threshold), "polarity": value.get("polarity", True),
            "conditions": dict(value.get("conditions") or {}), "verified_by": value.get("verified_by"),
            "identified_by": value.get("identified_by")}


def record_version(record: Mapping[str, Any]) -> Any:
    """The source version a record says it was read from, if it names one."""
    if record.get("version") is not None:
        return record["version"]
    source = record.get("source")
    return source.get("ref") if isinstance(source, dict) else None


def basis_gaps(hypothesis: Mapping[str, Any], record: Mapping[str, Any], claim: Mapping[str, Any],
               rules: Mapping[str, str] | None = None) -> list[str]:
    """Why a confirmed hypothesis does not establish this record's statement (empty when it does)."""
    gaps = []
    if hypothesis.get("aspect", "content") != "content":
        gaps.append("basis_not_about_content")
    if entity_identity.compare(hypothesis.get("subject"), record.get("entity"), rules) is not None:
        gaps.append("basis_about_another_entity")
    statement = hypothesis.get("statement") or {}
    attribute = record.get("attribute")
    if attribute not in statement:
        gaps.append("basis_about_another_property")
    else:
        stated, value = _canonical(statement[attribute]), _canonical(record.get("value"))
        if record.get("polarity", True) is True:
            holds = stated == value
        else:  # A boolean denial is entailed by the negated value; nothing else denies by equality.
            holds = type(record.get("value")) is bool and stated == _canonical(not record["value"])
        if not holds:
            gaps.append("basis_states_another_value")
    if _canonical(hypothesis.get("conditions") or {}) != _canonical(record.get("conditions") or {}):
        gaps.append("basis_under_other_conditions")
    scope = record.get("scope")
    if (scope not in (None, "any") and hypothesis.get("scope") != scope) or (
            claim["scope"] is not None and hypothesis.get("scope") != claim["scope"]):
        gaps.append("basis_for_another_scope")
    version = record_version(record)
    if version is not None and hypothesis.get("source_ref") != version:
        gaps.append("basis_for_another_version")
    if not _same_source(record.get("source"), hypothesis.get("source") or {}):
        gaps.append("basis_from_another_source")
    return gaps


def _same_source(stated: Any, read: Mapping[str, Any]) -> bool:
    """The record names the source the hypothesis read its claim from: the source's name, or the tool."""
    if isinstance(stated, dict):
        return stated.get("tool") == read.get("tool") or (
            stated.get("tool") is None and stated.get("name") is not None and stated.get("name") == read.get("name"))
    return stated is not None and stated in {read.get("name"), read.get("tool")}


def _value_type(value: Any) -> str:
    return {bool: "boolean", int: "integer", float: "number", str: "string", type(None): "null",
            dict: "object", list: "array"}.get(type(value), type(value).__name__)


def _outcome(status: str | None, gaps: list[str]) -> str:
    """What the basis says about this very statement: only a check of this statement decides it."""
    if gaps or status not in {"confirmed", "refuted"}:
        return "undecided"
    return status


def attestation(record: Mapping[str, Any], claim: Mapping[str, Any], named: str, known: Mapping[str, Any],
                gaps: list[str], alias: str | None) -> dict[str, Any]:
    """The admission basis of one candidate: what is stated, of which version, verified how, from where,
    with which outcome, and what it depends on."""
    verification = dict(known.get("verification") or {})
    observations = verification.get("observations") or {}
    check = observations.get("check")
    dependencies = [{"kind": "hypothesis", "ref": named}]
    if alias is not None:
        dependencies.append({"kind": "identity_link", "ref": alias})
    if record_version(record) is not None:
        dependencies.append({"kind": "source_version", "ref": record_version(record)})
    if isinstance(check, dict) and check.get("evidence") is not None:
        dependencies.append({"kind": "observation", "ref": check["evidence"]})
    if verification.get("contract_ref") is not None:
        dependencies.append({"kind": "check_contract", "ref": verification["contract_ref"]})
    return {
        "schema_version": ATTESTATION_V1,
        "statement": {"kind": record.get("kind", "fact"), "entity": record.get("entity"),
                      "attribute": record.get("attribute"), "value": record.get("value"),
                      "value_type": _value_type(record.get("value")), "polarity": record.get("polarity", True),
                      "conditions": dict(record.get("conditions") or {}), "scope": record.get("scope")},
        "version": {"source_version": record_version(record), "valid_from": instant(record.get("valid_from")),
                    "valid_until": instant(record.get("valid_until")), "at": claim["at"],
                    "known_from": record.get("known_from"), "known_until": record.get("known_until")},
        "verification": {"hypothesis": named, "status": known.get("status"), **verification},
        "provenance": {"record": record.get("id"), "source": record.get("source"), "identity_link": alias,
                       "copies": []},
        "outcome": _outcome(known.get("status"), gaps),
        "dependencies": dependencies}


def _identity(record, claim, hypothesis_of, rules) -> tuple[list[str], str | None]:
    reason = entity_identity.compare(record.get("entity"), claim["entity"], rules)
    if reason is None:
        return [], None
    if claim["identified_by"] is None:
        return ["another_entity" if reason == "entity_unreadable" else reason], None
    link = hypothesis_of(claim["identified_by"]) if hypothesis_of is not None else None
    failure = entity_identity.alias_holds(link, record.get("entity"), claim["entity"], rules)
    return ([reason, failure], None) if failure is not None else ([], claim["identified_by"])


def _checks(record, claim, key_tokens, hypothesis_of, rules) -> tuple[list[str], dict[str, Any]]:
    reasons, alias = _identity(record, claim, hypothesis_of, rules)
    if record.get("attribute") != claim["attribute"]:
        reasons.append("another_attribute")
    if record.get("kind", "fact") != claim["kind"]:
        reasons.append("another_kind")
    if "value" not in record:
        reasons.append("states_no_value")
    scope = record.get("scope")
    if claim["scope"] is not None and scope not in (None, "any", claim["scope"]):
        reasons.append("another_scope")
    start, end = instant(record.get("valid_from")), instant(record.get("valid_until"))
    # An event is a moment: it never holds without one (review AUD-2), however the candidate was found.
    timed = start is not None or end is not None or record.get("freshness") == "event"
    if timed and claim["at"] is None:
        reasons.append("freshness_unknown")
    elif timed and not holds_at(record.get("freshness"), start, end, claim["at"]):
        reasons.append("outside_validity")
    if not record.get("source"):
        reasons.append("no_provenance")
    if record.get("polarity", True) != claim["polarity"]:
        reasons.append("another_polarity")
    conditions = record.get("conditions") or {}
    if _canonical(conditions) != _canonical(claim["conditions"]):
        reasons.append("other_conditions")
    if record.get("freshness") in {"unknown", "undeclared"}:
        reasons.append("freshness_unknown")
    # The record's own status describes its content; the basis is the verification journal's alone.
    status, named, attested = None, record.get("hypothesis") or claim["verified_by"], None
    if named is None:
        reasons.append("no_verified_basis")
    else:
        known = hypothesis_of(named) if hypothesis_of is not None else None
        if known is None:
            reasons.append("basis_unknown")
        else:
            status = known["status"]
            if status != "confirmed":
                reasons.append(f"not_confirmed:{status}")
            gaps = basis_gaps(known, record, claim, rules)
            reasons.extend(gaps)
            attested = attestation(record, claim, named, known, gaps, alias)
    matched = sorted(key_tokens & content_tokens(record))
    scored = candidate_score(" ".join(claim["keys"]), record) or {"score": 0.0}
    if len(matched) < MIN_KEY_TOKENS:
        reasons.append("too_few_key_tokens")
    if scored["score"] < claim["threshold"]:
        reasons.append("below_threshold")
    return reasons, {"status": status, "basis": named, "alias": alias, "stated_status": record.get("status"),
                     "matched_keys": matched, "score": scored["score"], "attestation": attested}


def admit(candidates: Iterable[Mapping[str, Any]], claim: Any,
          hypothesis_of: Callable[[str], Mapping[str, Any] | None] | None = None,
          identity_rules: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Admit at most one candidate as an established fact for ``claim``, with every reason.

    ``hypothesis_of`` reads the verification journal of the bound memory session (``None`` outside one);
    ``identity_rules`` are the operator's namespace rules."""
    claim = _claim(claim)
    key_tokens = frozenset().union(*(tokens(item) for item in claim["keys"])) if claim["keys"] else frozenset()
    checked, passing, attested = [], {}, {}
    for record in candidates:
        reasons, facts = _checks(record, claim, key_tokens, hypothesis_of, identity_rules)
        checked.append({"id": record.get("id"), "reasons": reasons, **facts})
        if not reasons:
            statement = _canonical(record["value"])
            if statement in passing:  # Copies of one statement count once; the first stands for them.
                attested[statement]["provenance"]["copies"].append(record.get("id"))
            else:
                passing[statement], attested[statement] = record, facts["attestation"]
    decision = {"scorer": SCORER_VERSION, "claim": claim, "checked": checked, "fact": None, "attestation": None,
                "conflict": sorted(passing) if len(passing) > 1 else []}
    if len(passing) == 1:
        statement = next(iter(passing))
        decision.update(decision="admitted", fact=dict(passing[statement]), attestation=attested[statement])
    elif len(passing) > 1:
        decision["decision"] = "conflict"
    else:
        decision["decision"] = "abstained"
    return decision
