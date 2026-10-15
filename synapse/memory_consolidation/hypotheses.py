"""Testable hypotheses: from assumption to commitment (refinement §10).

A hypothesis states one claim — about an entity, a content or a version, never
two at once — about one entity (``synapse.entity_identity``), under the
conditions it holds in, with its scope, its source and the permitted check.
An entity hypothesis stating ``{"same_as": <other reference>}`` is the only way
two names become one entity (an alias). Its source is
a recorded observation of the same session (the tool, the arguments and the
recorded result's address), so the address is the version of the source the
claim was read from. The claim, source and check form a content-addressed
record: another source version is another hypothesis.

A hypothesis starts ``provisional``. Only its declared check decides it, through
the gateway, and only when the checking source is independent of the claim's
source in the declared provenance graph *and* the check answered about this
very claim (review F1): an independent source that answered about another
object proves nothing about this one. The operator's contract of the checking
tool (``verifies``) binds the claim to the check — which request argument and
which answer field name the subject, the scope (or the one scope the service
answers for) and each condition. The subject is compared by the exact identity
rules, the scope and conditions by value; a claim's condition the contract
cannot bind, or a condition the check answered under that the claim does not
state, leaves the answer about another context. Then every stated field
present and equal confirms it, a present and different field refutes it;
anything else — a failed or lost check, a check without a binding, an answer
about another subject, scope or context, a missing field, an explicit "cannot
determine", a dependent or unestablished checking source, an absent source —
keeps it provisional with its reason, neither confirmed nor refuted.
Confirming an entity says nothing about a content.

Reuse: a status the court recorded for the same hypothesis (same claim, same
source version) serves a later session only while it is fresh and only when it
was decided under the current check rule *and* on the current check basis —
the checking tool's contract, the provenance relation between the claim's
source and the checker, and the identity rules (review N2): a check decided
under another contract or another provenance graph is no check under these. A
changed source, unknown freshness, an earlier rule or a changed basis never
inherits it — the claim is checked again or the program abstains.
"""
from __future__ import annotations

from typing import Any, Mapping

from synapse import entity_identity

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
    required = {"aspect", "subject", "statement", "scope", "source", "check"}
    if type(claim) is not dict or not required <= set(claim) or set(claim) - required - {"conditions"}:
        raise HypothesisViolation("a hypothesis states aspect, subject, statement, scope, source and check "
                                  "(conditions optional)")
    if claim["aspect"] not in ASPECTS:
        raise HypothesisViolation("a hypothesis is about an entity, a content or a version")
    statement = claim["statement"]
    if (type(statement) is not dict or not 1 <= len(statement) <= _MAX_FIELDS
            or any(type(key) is not str or not _scalar(value) for key, value in statement.items())):
        raise HypothesisViolation("a hypothesis statement is a small object of scalar fields")
    try:
        subject = entity_identity.canonical(claim["subject"])
    except entity_identity.EntityViolation as exc:
        raise HypothesisViolation(f"a hypothesis subject is an entity: {exc}") from None
    if type(claim["scope"]) is not str or not claim["scope"].strip():
        raise HypothesisViolation("a hypothesis scope is named")
    conditions = claim.get("conditions") or {}
    if (type(conditions) is not dict or len(conditions) > _MAX_FIELDS
            or any(type(key) is not str or not _scalar(value) for key, value in conditions.items())):
        raise HypothesisViolation("a hypothesis's conditions are a small object of scalar fields")
    source = _call(claim["source"], "source", configuration)
    return records.make("hypothesis", aspect=claim["aspect"], subject=subject, statement=dict(statement),
                        scope=claim["scope"], conditions=dict(sorted(conditions.items())),
                        source={**source, "ref": source_ref}, check=_call(claim["check"], "check", configuration))


#: v2 (review F1): a check decides only a claim its contract binds it to. A status decided under v1 is never
#: reused; the claim is checked again.
CHECK_RULE = "synapse.memory.hypothesis-check/v2"


def verification(record: Mapping[str, Any], configuration: MemoryConfiguration, *, method: str | None,
                 observation: Any, checked_in: str | None, window: int | None, case: str | None,
                 reason: str | None) -> dict[str, Any]:
    """How a status of this hypothesis was decided: the rule, the check and the contract it ran under, and
    the recorded observations it read — the verifier half of an admission's attestation (review R3)."""
    contract = configuration.tools.tools.get(record["check"]["tool"])
    return {"method": method, "rule": CHECK_RULE, "check": {"tool": record["check"]["tool"],
                                                           "args": dict(record["check"]["args"])},
            "contract_ref": None if contract is None else contract.contract_ref,
            "checker_source": None if contract is None else contract.source,
            "observations": {"source": record["source"]["ref"], "check": observation},
            "checked_in": checked_in, "window": window, "case": case, "reason": reason}


def claim_key(record: Mapping[str, Any]) -> str:
    """The claim without its source version: what a changed source is compared against."""
    conditions = record.get("conditions") or {}
    # Conditions enter the key only when a claim has them, so claims recorded before conditions keep theirs.
    return digest({"aspect": record["aspect"], "subject": record["subject"], "statement": record["statement"],
                   "scope": record["scope"], **({"conditions": conditions} if conditions else {}),
                   "source": {"tool": record["source"]["tool"], "args": record["source"]["args"]}})


def check_basis(record: Mapping[str, Any], configuration: MemoryConfiguration) -> str | None:
    """What a check's decision rests on besides its recorded answer: the checking tool's contract, the source the
    claim was read from, the provenance relation between that source and the checker, and the identity rules
    (``None`` when a tool is no longer admitted)."""
    tools = configuration.tools.tools
    check, source = tools.get(record["check"]["tool"]), tools.get(record["source"]["tool"])
    if check is None or source is None:
        return None
    return digest({"check": check.contract_ref, "source": source.source,
                   "relation": relation(configuration.tools.provenance, source.source, check.source),
                   "identity": dict(sorted(configuration.identity.items()))})


def _bound(binding: Mapping[str, str], args: Mapping[str, Any], payload: Mapping[str, Any]) -> list | str:
    """The values one binding reads from the check's request and answer, or why it cannot read them."""
    values = []
    if "request" in binding:
        if binding["request"] not in args:
            return "check_unbound"
        values.append(args[binding["request"]])
    if "answer" in binding:
        if binding["answer"] not in payload:
            return "check_silent_on"
        values.append(payload[binding["answer"]])
    return values


def _about_claim(record: Mapping[str, Any], payload: Mapping[str, Any],
                 configuration: MemoryConfiguration) -> str | None:
    """Why the check did not answer about this claim's subject, scope and conditions (``None``: it did)."""
    verifies = configuration.tools.contract(record["check"]["tool"]).verifies
    if verifies is None:
        return "check_unbound"
    args = record["check"]["args"]
    values = _bound(verifies["subject"], args, payload)
    if type(values) is str:
        return values + ":subject"
    if any(entity_identity.compare(record["subject"], value, configuration.identity) is not None for value in values):
        return "check_about_another:subject"
    scope = verifies["scope"]
    values = [scope["value"]] if "value" in scope else _bound(scope, args, payload)
    if type(values) is str:
        return values + ":scope"
    if any(value != record["scope"] for value in values):
        return "check_about_another:scope"
    conditions = record.get("conditions") or {}
    for name in sorted(set(conditions) | set(verifies["conditions"])):
        binding = verifies["conditions"].get(name)
        if binding is None:
            return f"check_unbound:condition:{name}"
        if name not in conditions:
            # The check answered under a condition the claim does not state: about a narrower context.
            if binding.get("request") in args or binding.get("answer") in payload:
                return f"check_under_condition:{name}"
            continue
        values = _bound(binding, args, payload)
        if type(values) is str:
            return f"{values}:condition:{name}"
        if any(canonical(value) != canonical(conditions[name]) for value in values):
            return f"check_under_other_condition:{name}"
    return None


def resolve(record: Mapping[str, Any], view: Mapping[str, Any] | None,
            configuration: MemoryConfiguration) -> dict[str, Any]:
    """The status one recorded check gives the hypothesis (deterministic), under ``CHECK_RULE``."""
    def decided(status: str, reason: str) -> dict[str, Any]:
        return {"status": status, "reason": reason, "rule": CHECK_RULE,
                "check_basis": check_basis(record, configuration)}

    if record["source"]["ref"] is None:
        return decided("provisional", "source_absent")
    if view is None:
        return decided("provisional", "not_checked")
    source = configuration.tools.contract(record["source"]["tool"]).source
    checker = view.get("source")
    independence = relation(configuration.tools.provenance, source, checker) if checker else None
    if independence != INDEPENDENT:
        return decided("provisional", f"checking_source_{independence or 'unknown'}")
    payload = view.get("payload")
    if not view.get("ok") or not isinstance(payload, dict):
        return decided("provisional", "check_not_answered")
    if payload.get("determinable") is False:
        return decided("provisional", "check_cannot_determine")
    elsewhere = _about_claim(record, payload, configuration)
    if elsewhere is not None:
        return decided("provisional", elsewhere)
    missing = sorted(name for name in record["statement"] if name not in payload)
    if missing:
        return decided("provisional", "check_silent_on:" + ",".join(missing))
    differing = sorted(name for name, value in record["statement"].items() if canonical(payload[name]) != canonical(value))
    if differing:
        return decided("refuted", "contradicted:" + ",".join(differing))
    return decided("confirmed", "check_agrees")


def reuse(record: Mapping[str, Any], known: Mapping[str, Any] | None, claims: Mapping[str, Any],
          window: int | None, parameters: Mapping[str, Any], basis: str | None) -> dict[str, Any]:
    """Whether a status the court recorded serves this session, with the reason either way; ``basis`` is the
    check basis under this session's configuration."""
    if record["source"]["ref"] is None:
        return {"status": None, "reason": "source_absent"}
    if known is None:
        other = claims.get(claim_key(record))
        return {"status": None, "reason": "source_changed" if other is not None else "unknown_to_memory"}
    if window is None or window - known["window"] > parameters["hypothesis_fresh_windows"]:
        return {"status": None, "reason": "freshness_unknown" if window is None else "stale"}
    if known["status"] == "provisional":
        return {"status": None, "reason": "still_provisional"}
    if known.get("rule") != CHECK_RULE:
        return {"status": None, "reason": "checked_under_another_rule"}
    if basis is None or known.get("check_basis") != basis:
        return {"status": None, "reason": "check_basis_changed"}
    # The court's record of the check that decided it: which session checked, what it read, which case holds it.
    return {"status": known["status"], "reason": "court_record",
            "record": {"window": known["window"], "run_id": known.get("run_id"),
                       "check_ref": known.get("check_ref"), "case": known.get("basis")}}
