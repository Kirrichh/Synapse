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
older than the check memory holds (``checked``; for a status recorded without
it, older than the status) is superseded and reported, never applied; a check
without a recorded answer (an absent source) never displaces one with a
recorded answer. A correction returns to ``provisional`` the statuses read
from the corrected answer or checked against the corrected source that were
decided before it — a check made after it stands — and moves a status already
``provisional`` to its place too, so a check made before it can never establish
the claim later (recheck of 556624d). The corrections memory's order of
statements already makes reach a window's checks as well: a check published late
— made before a correction another session consolidated first — is decided with
them in the same order, as in one window. A status keeps what its latest check
decided (``checked``): when a window's statements change a source's order — a
late statement moves a correction earlier, or leaves a later one a restatement —
every status resting on that source is decided again from its check against
the corrections the order now makes, and one no correction reaches any more is
its check's again (``restored``). One rule (``reaches``, applied by
``decided``) decides whether a correction reaches a decision, for a window's
statuses, the admission of an action and the reassessment alike. A check after a
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

A reassessment (court policy v6) decides every status again from the record:
the consolidated checks of every session, each decided under the configuration
adopted now from the answer the gateway recorded — a configuration may change
the check's contract, the provenance graph or the identity rules, which the
check rule's version says nothing about (review N2) — and the corrections the
world's order of statements makes, in the order the record restores
(``knowledge.py``), folded in the same causal order. No tool is called.
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
from .knowledge import corrections_of, known_of, unplaced_corrections

EVENTS = ("hypothesis_declared", "hypothesis_probed", "hypothesis_reused")
SECTION = ("probed", "superseded", "reused", "unknown_record", "relied", "corrected", "restored")


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


def held_corrections(knowledge: Mapping[str, Any], records) -> list[dict[str, Any]]:
    """The corrections the world's order memory keeps makes of what these claims rest on — the sources they were
    read from or checked against — each at its place."""
    return corrections_of(knowledge, {digest({"tool": record[part]["tool"], "args": record[part]["args"]})
                                      for record in records for part in ("source", "check")})


def _apply_check(item, held, updates, section, holding, window) -> None:
    hypothesis, at = item["hypothesis"], _at(item["check_ref"])
    current = updates.get(hypothesis, held.get(hypothesis))
    # The latest check decides: one older than the check memory holds — or than its status, where the check it
    # was decided by is not kept — or without a recorded answer at all decides nothing (review M2).
    latest = None if current is None else (current["checked"] if "checked" in current else current).get("at")
    if current is not None and (at is None or (latest is not None and at < latest)):
        section["superseded"].append({"run_id": item["run_id"], "hypothesis": hypothesis, "status": item["status"],
                                      "at": at, "held_at": current.get("at")})
        return
    check = item["check_ref"]
    entry = {"record": copy.deepcopy(item["record"]), "claim_key": claim_key(item["record"]),
             "source_ref": item["record"]["source"]["ref"], "status": item["status"], "reason": item["reason"],
             "window": item.get("window", window), "run_id": item["run_id"], "check_ref": check,
             "basis": None if check is None else holding.get((item["run_id"], check["evidence"])),
             # The rule and basis the status was decided under: another rule or basis is never reused.
             "rule": item["rule"], "check_basis": item["check_basis"], "at": at,
             # What the check itself decided: a correction revises the status, never this.
             "checked": {"status": item["status"], "reason": item["reason"], "at": at,
                         "window": item.get("window", window)}}
    updates[hypothesis] = entry
    section["probed"].append({key: entry[key] for key in ("run_id", "status", "reason", "basis", "at")}
                             | {"hypothesis": hypothesis})


def reaches(entry: Mapping[str, Any], correction: Mapping[str, Any]) -> str | None:
    """Why ``correction`` reaches a decision (``entry``: the claim's record, the answer it was read from and the
    decision's place ``at``), or ``None``: the decision rests on the corrected answer or was checked against the
    corrected source, and came before the correction — one at or after it reflects the corrected world; an
    unplaced correction or decision cannot be shown to come later. A window's statuses (``decided``), the
    admission of an action and the reassessment decide by this one rule."""
    why = depends_on(entry, correction["old"])
    if why is None or (correction["at"] is not None and entry.get("at") is not None
                       and entry["at"] >= correction["at"]):
        return None
    return why


def _corrected(entry, correction, why, window) -> dict[str, Any]:
    return {**copy.deepcopy(entry), "status": "provisional", "reason": f"{why}:{correction['old']['record']['id']}",
            "window": correction.get("window", window), "at": correction["at"]}


def fold(held: Mapping[str, Mapping[str, Any]], checks, holding, window: int) -> tuple[dict, dict]:
    """The checks over ``held``, in the gateway's order (pure): the latest check of each claim decides it — a
    check's place is its recorded answer, an absent source's before everything. What corrections make of a
    check's verdict is ``decided``."""
    updates: dict[str, dict[str, Any]] = {}
    section = empty_section()
    for index in sorted(range(len(checks)), key=lambda index: (
            -1 if _at(checks[index]["check_ref"]) is None else _at(checks[index]["check_ref"]),
            checks[index]["run_id"], checks[index]["position"], index)):
        _apply_check(checks[index], held, updates, section, holding, window)
    return updates, section


def _in_order(corrections) -> list:
    return sorted(corrections, key=lambda item: (math.inf if item["at"] is None else item["at"],
                                                 item["old"]["record"]["source"]["ref"]))


def decided(entry: Mapping[str, Any], corrections, window: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A status as its latest check decided it (``checked``), revised by every correction of ``corrections``
    that reaches it, in their order (a correction's place is the observation of the corrected source; unknown:
    after everything) — the last names the status's place, so a provisional status moves to it too and nothing
    checked before it establishes the claim. The window, the admission's reading and the reassessment decide by
    this one rule, whatever windows the check and the corrections were consolidated in. Returns the status and
    the corrections that revised it, in order (none: its check decides)."""
    status = {**copy.deepcopy(entry), **{key: entry["checked"][key] for key in ("status", "reason", "at", "window")}}
    revised = []
    for correction in _in_order(corrections):
        why = reaches(status, correction)
        if why is not None:
            status = _corrected(status, correction, why, window)
            revised.append(correction)
    return status, revised


def _depends(record, sources) -> bool:
    return any(digest({"tool": record[part]["tool"], "args": record[part]["args"]}) in sources
               for part in ("source", "check"))


def hypothesis_stage(context, corrections, stated) -> dict[str, Any]:
    """The window's statuses: its checks decide in the gateway's order (``fold``); reuses reported. Then every
    status a check of this window decided, or resting on a source whose order of statements this window changed
    (``stated``), is decided from its latest check against every correction the order now makes (``decided``):
    a check published late meets the corrections memory already holds, and a status a correction no longer
    reaches — a late statement showed the source changed before the check — is its check's again (recheck of
    556624d). Each correction this window adds names in its revision the statuses it revised. A status a forget
    revoked stays revoked; one kept from before checks were kept (``checked``, provisional) stays as it is."""
    draft = context.draft
    holding = {(case["run_id"], ref): case["quantum"]["id"] for case in draft["cases"]
               for ref in case["recorded_results"]}
    probed = [item for item in draft["hypotheses"] if item["kind"] == "hypothesis_probed"]
    checks = [item for item in probed if item["record"] is not None]
    held_before = context.state["hypotheses"]
    updates, section = fold(held_before, checks, holding, context.window)
    knowledge = known_of(context.state)
    after = {**knowledge, "stated": {**knowledge["stated"], **stated}}
    touched = {source_identity({"source": group["source"]}) for group in stated.values()}
    new = {(item["at"], item["old"]["record"]["id"]): item for item in corrections}
    affected = {item["hypothesis"] for item in checks}
    if touched:  # Statements folded in this window: what rests on their sources may be decided otherwise now.
        affected |= {hypothesis for hypothesis, entry in held_before.items() if _depends(entry["record"], touched)}
    for hypothesis in sorted(affected):
        entry = updates.get(hypothesis, held_before.get(hypothesis))
        if entry is None or "checked" not in entry or entry["reason"].startswith("basis_forgotten:"):
            continue
        again, revised = decided(entry, held_corrections(after, [entry["record"]]), context.window)
        if (again["status"], again["reason"], again["at"]) == (entry["status"], entry["reason"], entry.get("at")):
            continue
        updates[hypothesis] = again
        if not revised:
            section["restored"].append({"hypothesis": hypothesis, "status": again["status"], "at": again["at"]})
            continue
        for correction in revised:
            found = new.get((correction["at"], correction["old"]["record"]["id"]))
            if found is not None:
                found["revision"]["hypotheses"] = sorted({*found["revision"]["hypotheses"], hypothesis})
        last = revised[-1]
        if (last["at"], last["old"]["record"]["id"]) not in new:  # A correction memory held already.
            section["corrected"].append({"hypothesis": hypothesis, "statement": last["old"]["record"]["id"],
                                         "corrected_by": last["by"], "at": last["at"]})
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


def reassess_hypotheses(state: Mapping[str, Any], configuration: MemoryConfiguration, gateway, records,
                        sessions, reports, knowledge) -> list[dict[str, Any]]:
    """Every status decided again from the record under the configuration adopted now (``sessions`` reads each
    session's whole history once): the consolidated checks of every session and the corrections the world's
    order of statements ``knowledge`` makes (with the places the record restores; one recorded at no place
    reaches every status it can), in causal order; each changed or newly found status is listed with what it
    was."""
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
    corrections = corrections_of(knowledge) + unplaced_corrections(knowledge)
    holding = {(quantum["replay_ref"]["run_id"], ref): qid for qid, quantum in state["quanta"].items()
               if quantum.get("replay_ref") is not None for ref in quantum.get("evidence_refs") or []}
    found, _ = fold({}, checks, holding, state["window"])
    found = {hypothesis: decided(entry, corrections, state["window"])[0] for hypothesis, entry in found.items()}
    section = []
    for hypothesis_id in sorted(set(state["hypotheses"]) | set(found)):
        before = state["hypotheses"].get(hypothesis_id)
        after = found.get(hypothesis_id)
        if after is None:
            if before is None or before["status"] == "provisional":
                continue
            # Decided by no check that still resolves: nothing confirms it now.
            after = {**copy.deepcopy(before), "status": "provisional", "reason": "check_record_unavailable",
                     "rule": CHECK_RULE, "check_basis": check_basis(before["record"], configuration), "at": None,
                     "checked": {"status": "provisional", "reason": "check_record_unavailable", "at": None,
                                 "window": state["window"]}}
        section.append({"hypothesis": hypothesis_id,
                        "from": None if before is None else {key: before.get(key) for key in (
                            "status", "reason", "rule", "check_basis", "at")},
                        "to": {key: after.get(key) for key in ("status", "reason", "rule", "check_basis", "at")},
                        "entry": after})
    return section


def reassessed(section) -> dict[str, dict[str, Any]]:
    """The court's entries of the statuses a reassessment decided again."""
    return {item["hypothesis"]: copy.deepcopy(item["entry"]) for item in section}
