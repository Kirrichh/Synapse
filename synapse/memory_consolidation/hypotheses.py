"""Testable hypotheses: from assumption to commitment (refinement §10).

A hypothesis states one claim — about an entity, a content or a version, never
two at once — with its scope, its source and the permitted check. Its source is
a recorded observation of the same session (the tool, the arguments and the
recorded result's address), so the address is the version of the source the
claim was read from. The claim, source and check form a content-addressed
record: another source version is another hypothesis.

A hypothesis starts ``provisional``. Only its declared check decides it, through
the gateway, and only when the checking source is independent of the claim's
source in the declared provenance graph: every stated field present and equal
confirms it, a present and different field refutes it, anything else — a
failed or lost check, a missing field, an explicit "cannot determine", a
dependent or unestablished checking source, an absent source — keeps it
provisional with its reason. Confirming an entity says nothing about a content.

Reuse: a status the court recorded for the same hypothesis (same claim, same
source version) serves a later session only while it is fresh; a changed
source or unknown freshness never inherits it — the claim is checked again or
the program abstains.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import records
from .configuration import MemoryConfiguration
from .learning.provenance import INDEPENDENT, relation
from .records import canonical, digest

ASPECTS = ("entity", "content", "version")
STATUSES = ("provisional", "confirmed", "refuted")
_MAX_FIELDS = 16


class HypothesisViolation(ValueError):
    """A hypothesis is outside its declared shape."""


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _call(value: Any, name: str, configuration: MemoryConfiguration) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"tool", "args"} or type(value["args"]) is not dict:
        raise HypothesisViolation(f"hypothesis {name} names an admitted tool and its arguments")
    contract = configuration.tools.tools.get(value["tool"]) if type(value["tool"]) is str else None
    if contract is None or contract.role != "action":
        raise HypothesisViolation(f"hypothesis {name} names an admitted action tool")
    return {"tool": value["tool"], "args": dict(value["args"])}


def declare(claim: Any, configuration: MemoryConfiguration, source_ref: str | None) -> dict[str, Any]:
    """The hypothesis record; ``source_ref`` is the recorded source answer, ``None`` when absent."""
    if type(claim) is not dict or set(claim) != {"aspect", "subject", "statement", "scope", "source", "check"}:
        raise HypothesisViolation("a hypothesis states aspect, subject, statement, scope, source and check")
    if claim["aspect"] not in ASPECTS:
        raise HypothesisViolation("a hypothesis is about an entity, a content or a version")
    statement = claim["statement"]
    if (type(statement) is not dict or not 1 <= len(statement) <= _MAX_FIELDS
            or any(type(key) is not str or not _scalar(value) for key, value in statement.items())):
        raise HypothesisViolation("a hypothesis statement is a small object of scalar fields")
    for name in ("subject", "scope"):
        if type(claim[name]) is not str or not claim[name].strip():
            raise HypothesisViolation(f"a hypothesis {name} is named")
    source = _call(claim["source"], "source", configuration)
    return records.make("hypothesis", aspect=claim["aspect"], subject=claim["subject"], statement=dict(statement),
                        scope=claim["scope"], source={**source, "ref": source_ref},
                        check=_call(claim["check"], "check", configuration))


def claim_key(record: Mapping[str, Any]) -> str:
    """The claim without its source version: what a changed source is compared against."""
    return digest({"aspect": record["aspect"], "subject": record["subject"], "statement": record["statement"],
                   "scope": record["scope"], "source": {"tool": record["source"]["tool"],
                                                        "args": record["source"]["args"]}})


def resolve(record: Mapping[str, Any], view: Mapping[str, Any] | None,
            configuration: MemoryConfiguration) -> dict[str, Any]:
    """The status one recorded check gives the hypothesis (deterministic)."""
    if record["source"]["ref"] is None:
        return {"status": "provisional", "reason": "source_absent"}
    if view is None:
        return {"status": "provisional", "reason": "not_checked"}
    source = configuration.tools.contract(record["source"]["tool"]).source
    checker = view.get("source")
    independence = relation(configuration.tools.provenance, source, checker) if checker else None
    if independence != INDEPENDENT:
        return {"status": "provisional", "reason": f"checking_source_{independence or 'unknown'}"}
    payload = view.get("payload")
    if not view.get("ok") or not isinstance(payload, dict):
        return {"status": "provisional", "reason": "check_not_answered"}
    if payload.get("determinable") is False:
        return {"status": "provisional", "reason": "check_cannot_determine"}
    missing = sorted(name for name in record["statement"] if name not in payload)
    if missing:
        return {"status": "provisional", "reason": "check_silent_on:" + ",".join(missing)}
    differing = sorted(name for name, value in record["statement"].items() if canonical(payload[name]) != canonical(value))
    if differing:
        return {"status": "refuted", "reason": "contradicted:" + ",".join(differing)}
    return {"status": "confirmed", "reason": "check_agrees"}


def reuse(record: Mapping[str, Any], known: Mapping[str, Any] | None, claims: Mapping[str, Any],
          window: int | None, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a status the court recorded serves this session, with the reason either way."""
    if record["source"]["ref"] is None:
        return {"status": None, "reason": "source_absent"}
    if known is None:
        other = claims.get(claim_key(record))
        return {"status": None, "reason": "source_changed" if other is not None else "unknown_to_memory"}
    if window is None or window - known["window"] > parameters["hypothesis_fresh_windows"]:
        return {"status": None, "reason": "freshness_unknown" if window is None else "stale"}
    if known["status"] == "provisional":
        return {"status": None, "reason": "still_provisional"}
    return {"status": known["status"], "reason": "court_record"}
