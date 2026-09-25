"""Palace candidates and fact admission are two different acts (refinement §11).

``recall`` returns candidates: records whose content shares normalized tokens
with the query, scored only by that lexical overlap (scorer
``palace-lexical/v2``). A record's confidence, its insertion time and its
metadata never enter the score; zero overlap is no lexical evidence and no
candidate; equal scores are ordered by record identity.

``admit(candidates, claim)`` decides whether one candidate may be used as an
established fact for the claim. Every check is separate and reported:

* the entity, the attribute and the kind of information are those of the claim
  — similar words about another entity never answer for it;
* the scope and the time of validity cover the claim; unknown freshness of a
  time-bound record is no admission;
* the record names its provenance;
* its status is ``confirmed``: the live status of the hypothesis it names when
  a memory session knows it, otherwise the status the record states (reported
  as self-declared);
* at least two distinct normalized key tokens of the claim occur in the record
  and its lexical score reaches the threshold — a filter on candidates, never
  a proof of content;
* admitted candidates that state different values for the same entity and
  attribute are a conflict: nothing is admitted, and neither the later nor the
  more confident one wins. Copies of one statement count once.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Mapping

SCORER_VERSION = "palace-lexical/v2"
MIN_KEY_TOKENS = 2
DEFAULT_THRESHOLD = 0.5
_TOKEN = re.compile(r"\w+", re.UNICODE)
#: Fields that describe a record rather than state its content.
METADATA_FIELDS = frozenset({
    "id", "trace_id", "created_at", "confidence", "room", "palace", "score", "matched_tokens", "scorer",
    "source", "status", "hypothesis", "kind", "valid_from", "valid_until", "scope", "source_room",
    "routing_tags", "affective_tag", "affective_tag_id", "affective_tag_snapshot", "affective_expires_at_event",
    "affective_decay", "affective_decay_original", "affective_expired"})


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
            "kind", "scope", "at", "threshold"}:
        raise ValueError("an admission claim names entity, attribute and keys (kind, scope, at, threshold optional)")
    keys = value["keys"]
    if type(keys) is not list or any(type(item) is not str for item in keys):
        raise ValueError("admission keys are strings")
    threshold = value.get("threshold", DEFAULT_THRESHOLD)
    if type(threshold) not in {int, float} or not 0 < threshold <= 1:
        raise ValueError("an admission threshold is in (0, 1]")
    return {"entity": str(value["entity"]), "attribute": str(value["attribute"]), "kind": value.get("kind", "fact"),
            "scope": value.get("scope"), "at": value.get("at"), "keys": keys, "threshold": float(threshold)}


def _checks(record, claim, key_tokens, status_of) -> tuple[list[str], dict[str, Any]]:
    reasons = []
    if tokens(str(record.get("entity", ""))) != tokens(claim["entity"]):
        reasons.append("another_entity")
    if record.get("attribute") != claim["attribute"]:
        reasons.append("another_attribute")
    if record.get("kind", "fact") != claim["kind"]:
        reasons.append("another_kind")
    if "value" not in record:
        reasons.append("states_no_value")
    scope = record.get("scope")
    if claim["scope"] is not None and scope not in (None, "any", claim["scope"]):
        reasons.append("another_scope")
    bounded = record.get("valid_from") is not None or record.get("valid_until") is not None
    if bounded and claim["at"] is None:
        reasons.append("freshness_unknown")
    elif bounded and ((record.get("valid_from") is not None and claim["at"] < record["valid_from"])
                      or (record.get("valid_until") is not None and claim["at"] > record["valid_until"])):
        reasons.append("outside_validity")
    if not record.get("source"):
        reasons.append("no_provenance")
    status, basis = record.get("status"), "self_declared"
    if record.get("hypothesis") is not None:
        known = status_of(record["hypothesis"]) if status_of is not None else None
        status, basis = (known, "hypothesis") if known is not None else (None, "hypothesis_unknown")
    if status != "confirmed":
        reasons.append(f"not_confirmed:{status}")
    matched = sorted(key_tokens & content_tokens(record))
    scored = candidate_score(" ".join(claim["keys"]), record) or {"score": 0.0}
    if len(matched) < MIN_KEY_TOKENS:
        reasons.append("too_few_key_tokens")
    if scored["score"] < claim["threshold"]:
        reasons.append("below_threshold")
    return reasons, {"status": status, "status_basis": basis, "matched_keys": matched, "score": scored["score"]}


def admit(candidates: Iterable[Mapping[str, Any]], claim: Any,
          status_of: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """Admit at most one candidate as an established fact for ``claim``, with every reason."""
    claim = _claim(claim)
    key_tokens = frozenset().union(*(tokens(item) for item in claim["keys"])) if claim["keys"] else frozenset()
    checked, passing = [], {}
    for record in candidates:
        reasons, facts = _checks(record, claim, key_tokens, status_of)
        checked.append({"id": record.get("id"), "reasons": reasons, **facts})
        if not reasons:
            statement = _canonical(record["value"])
            passing.setdefault(statement, record)  # Copies of one statement count once.
    decision = {"scorer": SCORER_VERSION, "claim": claim, "checked": checked, "fact": None,
                "conflict": sorted(passing) if len(passing) > 1 else []}
    if len(passing) == 1:
        decision.update(decision="admitted", fact=dict(next(iter(passing.values()))))
    elif len(passing) > 1:
        decision["decision"] = "conflict"
    else:
        decision["decision"] = "abstained"
    return decision
