"""The court's record of hypotheses (refinement §10): the one writer of their statuses.

A session decides a hypothesis only for itself: ``probe`` records the status
its check gave. The court folds every recorded check of a window into memory
state, with the case that holds the check as its basis. Reuses are reported,
never folded: a reuse decides nothing, it saves a check.

Statuses follow the causal order of what was observed, never the names of the
runs or the windows they were consolidated in (review M1, M2, M4). Every event
that sets a status has its place on the gateway's sequence, and the status
records it (``at``): a check is its recorded answer, a correction the
observation of the corrected source, a forget the gateway's head when the
operator recorded it (``dependencies.py``). The latest check decides; a check
older than the status memory holds is superseded and reported, never applied;
a check without a recorded answer (an absent source) never displaces one with
a recorded answer. A correction returns to ``provisional`` the statuses read
from the corrected answer or checked against the corrected source that were
decided before it — a check made after it stands. A check after a
consolidation names a hypothesis an earlier window of the same session
declared: the declaration is resolved from the session's verified history, and
only the window's own checks are applied, so a consolidation repeated decides
nothing twice. The order a window's events are applied in is the gateway's;
ties are broken by the session, then by the history position, so one record
always folds alike.

The snapshot boundary carries the statuses with the check rule each was
decided under and, per claim, the source version last checked, so a later
session reuses a status only for the very same source version under the
current rule and tells a changed source apart from an unknown claim. An
emergency window changes nothing.

A reassessment (court policy v5) decides every status again from the record:
the consolidated checks of every session, each decided under the configuration
adopted now from the answer the gateway recorded — a configuration may change
the check's contract, the provenance graph or the identity rules, which the
check rule's version says nothing about (review N2) — and the corrections the
knowledge timeline holds, folded in the same causal order. No tool is called.
A status whose check no readable session shows any more is decided again from
the answer the gateway recorded for that check, in its place. A check whose
recorded answer no longer resolves decides nothing: the status is
``provisional``, never confirmed on the record's word, and a correction whose
observation can no longer be placed revises every status it reaches.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping

from ..configuration import MemoryConfiguration
from ..hypotheses import CHECK_RULE, check_basis, claim_key, resolve
from ..knowledge.statements import source_identity
from ..records import digest
from ..tools.journal import GatewayIntegrityError

EVENTS = ("hypothesis_declared", "hypothesis_probed", "hypothesis_reused")
SECTION = ("probed", "superseded", "reused", "unknown_record", "relied")


def empty_section() -> dict[str, list]:
    return {name: [] for name in SECTION}


def declarations(history: list[Mapping[str, Any]], end: int) -> dict[str, dict[str, Any]]:
    """The hypotheses a session declared in its history before ``end``, by identity (content-addressed)."""
    return {event["hypothesis"]["id"]: event["hypothesis"] for event in history[:end]
            if isinstance(event, Mapping) and event.get("type") == "hypothesis_declared"}


def hypothesis_events(found: Mapping[str, list], run_id: str,
                      declared: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A session's checks and reuses of one window, in history order, each with the record its session declared
    (``declared``: the session's history up to the window's end, so a check after a consolidation still names
    its hypothesis — review M1)."""
    items = []
    for kind in ("hypothesis_probed", "hypothesis_reused"):
        for position, event in found.get(kind, []):
            items.append({"kind": kind, "position": position, "run_id": run_id, "hypothesis": event["hypothesis"],
                          "record": declared.get(event["hypothesis"]), "status": event["status"],
                          "reason": event["reason"], "check_ref": event.get("check_ref"),
                          "rule": event.get("rule"), "check_basis": event.get("check_basis")})
    return sorted(items, key=lambda item: item["position"])


def relied_actions(facts, gateway_records) -> list[dict[str, Any]]:
    """The window's actions that named the hypotheses they rely on, each with its admission point on the
    gateway's sequence: where it started, or where it was refused before any effect."""
    started = {record["seq"]: record["body"]["started_seq"] for record in gateway_records if record["kind"] == "RESULT"}
    items = []
    for position, event in facts.found.get("external_action", []):
        requires = event["request"].get("requires")
        if requires:
            seq = event["outcome"]["ref"]["gw_seq"]
            items.append({"run_id": facts.run, "position": position, "tool": event["request"]["tool"],
                          "performed": event["outcome"]["view"]["transport"] != "rejected",
                          "admitted_at": started.get(seq, seq), "hypotheses": [item["hypothesis"] for item in requires]})
    return items


def revocation(entry: Mapping[str, Any], admitted_at: int) -> str | None:
    """Whether memory's status of a hypothesis an action relied on was revoked before or after the action's
    admission: an action already sent is never undone by a later status, and the report says which it was."""
    if entry["status"] == "confirmed" or entry.get("at") is None:
        return None
    return "after_admission" if entry["at"] > admitted_at else "before_admission"


def _at(check_ref: Mapping[str, Any] | None) -> int | None:
    return None if check_ref is None else check_ref["gw_seq"]


def depends_on(entry: Mapping[str, Any], corrected: Mapping[str, Any]) -> str | None:
    """Why a correction of the version ``corrected`` reaches a status: it was read from the corrected answer, or
    checked against the corrected source (``None``: neither)."""
    source, record = source_identity(corrected["record"]), entry["record"]
    if (digest({"tool": record["source"]["tool"], "args": record["source"]["args"]}) == source
            and entry["source_ref"] == corrected["record"]["source"]["ref"]):
        return "source_corrected"
    if digest({"tool": record["check"]["tool"], "args": record["check"]["args"]}) == source:
        return "check_source_corrected"
    return None


def _apply_check(item, held, updates, section, holding, window) -> None:
    hypothesis, at = item["hypothesis"], _at(item["check_ref"])
    current = updates.get(hypothesis, held.get(hypothesis))
    if current is not None and (at is None or (current.get("at") is not None and at < current["at"])):
        # Older than the status memory holds, or no recorded answer at all: it decides nothing (review M2).
        section["superseded"].append({"run_id": item["run_id"], "hypothesis": hypothesis, "status": item["status"],
                                      "at": at, "held_at": current.get("at")})
        return
    check = item["check_ref"]
    entry = {"record": copy.deepcopy(item["record"]), "claim_key": claim_key(item["record"]),
             "source_ref": item["record"]["source"]["ref"], "status": item["status"], "reason": item["reason"],
             "window": item.get("window", window), "run_id": item["run_id"], "check_ref": check,
             "basis": None if check is None else holding.get((item["run_id"], check["evidence"])),
             # The rule and basis the status was decided under: another rule or basis is never reused.
             "rule": item["rule"], "check_basis": item["check_basis"], "at": at}
    updates[hypothesis] = entry
    section["probed"].append({key: entry[key] for key in ("run_id", "status", "reason", "basis", "at")}
                             | {"hypothesis": hypothesis})


def _apply_correction(correction, held, updates, window) -> None:
    at = correction["at"]
    for hypothesis, entry in sorted({**held, **updates}.items()):
        why = None if entry["status"] == "provisional" else depends_on(entry, correction["old"])
        if why is None or (at is not None and entry.get("at") is not None and entry["at"] >= at):
            continue  # Not reached, or checked again after the correction: the later check stands.
        updates[hypothesis] = {**copy.deepcopy(entry), "status": "provisional",
                               "reason": f"{why}:{correction['old']['record']['id']}",
                               "window": correction.get("window", window), "at": at}
        correction["revision"]["hypotheses"].append(hypothesis)


def fold(held: Mapping[str, Mapping[str, Any]], checks, corrections, holding, window: int) -> tuple[dict, dict]:
    """The statuses ``checks`` and ``corrections`` give over ``held``, in the gateway's order (pure).

    A check's place is its recorded answer (an absent source: before everything); a correction's, the
    observation of the corrected source (unknown: after everything). At one place a correction comes first —
    a check that read the very observation that corrected a source reflects the corrected world."""
    updates: dict[str, dict[str, Any]] = {}
    section = empty_section()
    events = [((-1 if _at(item["check_ref"]) is None else _at(item["check_ref"])), 1, item["run_id"],
               item["position"], index, "check") for index, item in enumerate(checks)]
    events += [((math.inf if item["at"] is None else item["at"]), 0, "", index, index, "correction")
               for index, item in enumerate(corrections)]
    for *_, index, kind in sorted(events):
        if kind == "check":
            _apply_check(checks[index], held, updates, section, holding, window)
        else:
            _apply_correction(corrections[index], held, updates, window)
    return updates, section


def hypothesis_stage(context, corrections) -> dict[str, Any]:
    """The window's statuses: its checks and its knowledge corrections in causal order; reuses reported."""
    draft = context.draft
    holding = {(case["run_id"], ref): case["quantum"]["id"] for case in draft["cases"]
               for ref in case["recorded_results"]}
    probed = [item for item in draft["hypotheses"] if item["kind"] == "hypothesis_probed"]
    updates, section = fold(context.state["hypotheses"], [item for item in probed if item["record"] is not None],
                            corrections, holding, context.window)
    section["reused"] = [{key: item[key] for key in ("run_id", "hypothesis", "status", "reason")}
                         for item in draft["hypotheses"] if item["kind"] == "hypothesis_reused"]
    section["unknown_record"] = [{"run_id": item["run_id"], "hypothesis": item["hypothesis"]}
                                 for item in probed if item["record"] is None]
    held = {**context.state["hypotheses"], **updates}
    section["relied"] = [{**{key: item[key] for key in ("run_id", "position", "tool", "performed", "admitted_at")},
                          "hypotheses": [{"hypothesis": hypothesis_id, "status": entry["status"], "at": entry.get("at"),
                                          "revoked": revocation(entry, item["admitted_at"])}
                                         for hypothesis_id in item["hypotheses"]
                                         for entry in [held.get(hypothesis_id)] if entry is not None]}
                         for item in draft["relied"]]
    return {"updates": updates, "section": section}


def changed_since(entry: Mapping[str, Any], read: Mapping[str, Any]) -> bool:
    """Whether memory's status of a hypothesis left ``confirmed`` after the decision an action relies on (review
    M6). ``read`` is how that decision was read — the session's own check or the court's record, with the
    observations it rests on. Both placed: a status set at or after the decision's check changed since; else a
    status read from the court's record compares by windows, and an unplaced change counts as later."""
    if entry["status"] == "confirmed":
        return False
    at = ((read.get("observations") or {}).get("check") or {}).get("gw_seq")
    if entry.get("at") is not None and at is not None:
        return entry["at"] >= at
    if read.get("method") == "court_record" and read.get("window") is not None:
        return entry["window"] > read["window"]
    return True


def boundary_view(hypotheses: Mapping[str, Mapping[str, Any]]) -> tuple[dict, dict]:
    """What a pinned snapshot offers for reuse: statuses by hypothesis, the last checked source per claim."""
    statuses, claims = {}, {}
    for hypothesis_id, entry in sorted(hypotheses.items(), key=lambda item: (item[1]["window"], item[0])):
        statuses[hypothesis_id] = {"status": entry["status"], "window": entry["window"],
                                   "claim_key": entry["claim_key"], "source_ref": entry["source_ref"],
                                   "run_id": entry.get("run_id"), "check_ref": entry.get("check_ref"),
                                   "basis": entry.get("basis"), "rule": entry.get("rule"),
                                   "check_basis": entry.get("check_basis")}
        claims[entry["claim_key"]] = hypothesis_id
    return statuses, claims


def _consolidated_in(reports, run_id: str, position: int) -> int | None:
    """The window whose consolidation covered a session's history position."""
    for report in reports:
        for session in report["window"]["sessions"]:
            if session["run_id"] == run_id and session["from"] <= position < session["to"]:
                return report["window"]["index"]
    return None


def _decided_again(item, configuration: MemoryConfiguration, gateway, records) -> dict[str, Any]:
    check = item["check_ref"]
    try:
        view = None if check is None else gateway.recorded_outcome(check["gw_seq"], records)["view"]
        return resolve(item["record"], view, configuration)
    except (GatewayIntegrityError, PermissionError):
        # An answer that no longer resolves, or a tool no longer admitted: the check decides nothing.
        return {"status": "provisional", "reason": "check_record_unavailable", "rule": CHECK_RULE,
                "check_basis": check_basis(item["record"], configuration)}


def _correction_at(statement_id: str, versions, sessions) -> int | None:
    """Where on the gateway's sequence the version ``statement_id`` was observed (``None``: it cannot be placed)."""
    version = versions.get(statement_id)
    facts = None if version is None else sessions.facts(version["run_id"])
    if facts is None:
        return None
    for position, event in facts.found.get("knowledge_declared", []):
        if event["statement"]["id"] == statement_id and position in facts.observed:
            return facts.observed[position]
    return None


def reassess_hypotheses(state: Mapping[str, Any], configuration: MemoryConfiguration, gateway, records,
                        sessions, reports) -> list[dict[str, Any]]:
    """Every status decided again from the record under the configuration adopted now (``sessions`` reads each
    session's whole history once): the consolidated checks of every session and the corrections of the
    knowledge timeline, in causal order; each changed or newly found status is listed with what it was."""
    checks = []
    for run_id, cursor in sorted(state["cursors"].items()):
        facts = sessions.facts(run_id)
        if facts is None or facts.problems:
            continue  # A record that does not verify decides nothing.
        for item in hypothesis_events(facts.found, run_id, facts.declared):
            if item["kind"] != "hypothesis_probed" or item["record"] is None or item["position"] >= cursor["to"]:
                continue
            checks.append({**item, **_decided_again(item, configuration, gateway, records),
                           "window": _consolidated_in(reports, run_id, item["position"]) or state["window"]})
    for hypothesis_id, entry in sorted(state["hypotheses"].items()):
        check = entry.get("check_ref")
        if entry["status"] == "provisional" or check is None or any(
                item["hypothesis"] == hypothesis_id and _at(item["check_ref"]) == check["gw_seq"] for item in checks):
            continue
        # A check no readable session shows (its history is gone): the answer the gateway recorded still decides.
        item = {"kind": "hypothesis_probed", "position": 0, "run_id": entry.get("run_id") or "",
                "hypothesis": hypothesis_id, "record": entry["record"], "check_ref": check}
        checks.append({**item, **_decided_again(item, configuration, gateway, records), "window": entry["window"]})
    versions = (state.get("knowledge") or {"versions": {}})["versions"]
    corrections = []
    for statement_id, version in sorted(versions.items()):
        for period in [*version.get("earlier_known", []), version]:
            if period.get("corrected_by") is not None:
                corrections.append({"old": version, "by": period["corrected_by"],
                                    "at": _correction_at(period["corrected_by"], versions, sessions),
                                    "window": versions.get(period["corrected_by"], {}).get("known_from"),
                                    "revision": {"statement": statement_id, "hypotheses": []}})
    holding = {(quantum["replay_ref"]["run_id"], ref): qid for qid, quantum in state["quanta"].items()
               if quantum.get("replay_ref") is not None for ref in quantum.get("evidence_refs") or []}
    found, _ = fold({}, checks, corrections, holding, state["window"])
    section = []
    for hypothesis_id in sorted(set(state["hypotheses"]) | set(found)):
        before = state["hypotheses"].get(hypothesis_id)
        after = found.get(hypothesis_id)
        if after is None:
            if before is None or before["status"] == "provisional":
                continue
            # Decided by no check that still resolves: nothing confirms it now.
            after = {**copy.deepcopy(before), "status": "provisional", "reason": "check_record_unavailable",
                     "rule": CHECK_RULE, "check_basis": check_basis(before["record"], configuration), "at": None}
        section.append({"hypothesis": hypothesis_id,
                        "from": None if before is None else {key: before.get(key) for key in (
                            "status", "reason", "rule", "check_basis", "at")},
                        "to": {key: after.get(key) for key in ("status", "reason", "rule", "check_basis", "at")},
                        "entry": after})
    return section


def reassessed(section) -> dict[str, dict[str, Any]]:
    """The court's entries of the statuses a reassessment decided again."""
    return {item["hypothesis"]: copy.deepcopy(item["entry"]) for item in section}
