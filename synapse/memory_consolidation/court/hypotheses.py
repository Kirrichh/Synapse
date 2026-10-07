"""The court's record of hypotheses (refinement §10): the one writer of their statuses.

A session decides a hypothesis only for itself: ``probe`` records the status
its check gave. The court folds every recorded check of a window into memory
state — the latest check of a hypothesis decides it — with the case that
holds the check as its basis. Reuses are reported, never folded: a reuse
decides nothing, it saves a check. The snapshot boundary carries the
statuses with the check rule each was decided under and, per claim, the source
version last checked, so a later session reuses a status only for the very same
source version under the current rule and tells a changed source apart from an
unknown claim. An emergency window changes nothing.

A reassessment decides again, from the recorded check alone, every status the
court decided: a configuration it adopts may change the check's contract, the
provenance graph or the identity rules, and the check rule's version says
nothing about those (review N2). The gateway journal and the evidence give
back the answer the check recorded, and the current rule and configuration
decide it — no tool is called, and an unchanged configuration decides it
alike. A check whose recorded answer no
longer resolves (forgotten, never recorded) decides nothing: the status becomes
provisional, never confirmed on the record's word.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from ..configuration import MemoryConfiguration
from ..hypotheses import CHECK_RULE, check_basis, claim_key, resolve
from ..tools.journal import GatewayIntegrityError

EVENTS = ("hypothesis_declared", "hypothesis_probed", "hypothesis_reused")


def hypothesis_events(found: Mapping[str, list], run_id: str) -> list[dict[str, Any]]:
    """A session's hypothesis events of one window, in history order."""
    declared = {event["hypothesis"]["id"]: event["hypothesis"]
                for _, event in found.get("hypothesis_declared", [])}
    items = []
    for kind in ("hypothesis_probed", "hypothesis_reused"):
        for position, event in found.get(kind, []):
            items.append({"kind": kind, "position": position, "run_id": run_id, "hypothesis": event["hypothesis"],
                          "record": declared.get(event["hypothesis"]), "status": event["status"],
                          "reason": event["reason"], "check_ref": event.get("check_ref"),
                          "rule": event.get("rule"), "check_basis": event.get("check_basis")})
    return sorted(items, key=lambda item: item["position"])


def hypothesis_stage(state: Mapping[str, Any], draft: Mapping[str, Any], cases, window: int) -> tuple[dict, dict]:
    """Updated hypothesis entries and the report section of one window (pure)."""
    section: dict[str, list] = {"probed": [], "reused": [], "unknown_record": []}
    updates: dict[str, dict[str, Any]] = {}
    if draft["mode"] == "emergency":
        return updates, section
    holding = {(case["run_id"], ref): case["quantum"]["id"] for case in cases for ref in case["recorded_results"]}
    for item in draft["hypotheses"]:
        if item["kind"] == "hypothesis_reused":
            section["reused"].append({key: item[key] for key in ("run_id", "hypothesis", "status", "reason")})
            continue
        if item["record"] is None:
            section["unknown_record"].append({"run_id": item["run_id"], "hypothesis": item["hypothesis"]})
            continue
        # The case whose recorded results hold the check's answer is the status's basis.
        check = item["check_ref"]
        basis = None if check is None else holding.get((item["run_id"], check["evidence"]))
        entry = {"record": copy.deepcopy(item["record"]), "claim_key": claim_key(item["record"]),
                 "source_ref": item["record"]["source"]["ref"], "status": item["status"], "reason": item["reason"],
                 "window": window, "run_id": item["run_id"], "check_ref": item["check_ref"], "basis": basis,
                 # The rule and basis the status was decided under: another rule or basis is never reused.
                 "rule": item["rule"], "check_basis": item["check_basis"]}
        updates[item["hypothesis"]] = entry
        section["probed"].append({key: entry[key] for key in ("run_id", "status", "reason", "basis")}
                                 | {"hypothesis": item["hypothesis"]})
    return updates, section


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


def reassess_hypotheses(state: Mapping[str, Any], configuration: MemoryConfiguration, gateway,
                        records) -> list[dict[str, Any]]:
    """Every decided status decided again from its recorded check under the configuration adopted now."""
    section = []
    for hypothesis_id, entry in sorted(state["hypotheses"].items()):
        if entry["status"] == "provisional":
            continue
        check = entry.get("check_ref") or {}
        unavailable = {"status": "provisional", "reason": "check_record_unavailable", "rule": CHECK_RULE,
                       "check_basis": check_basis(entry["record"], configuration)}
        try:
            # No recorded check, an answer that no longer resolves, or a tool no longer admitted: nothing decided.
            decided = unavailable if check.get("gw_seq") is None else resolve(
                entry["record"], gateway.recorded_outcome(check["gw_seq"], records)["view"], configuration)
        except (GatewayIntegrityError, PermissionError):
            decided = unavailable
        section.append({"hypothesis": hypothesis_id,
                        "from": {key: entry.get(key) for key in ("status", "reason", "rule", "check_basis")},
                        "to": {key: decided[key] for key in ("status", "reason", "rule", "check_basis")}})
    return section


def reassessed(state: Mapping[str, Any], section) -> dict[str, dict[str, Any]]:
    """The court's entries of the statuses a reassessment decided again: the check and its window stay."""
    return {item["hypothesis"]: {**copy.deepcopy(state["hypotheses"][item["hypothesis"]]), **item["to"]}
            for item in section}
