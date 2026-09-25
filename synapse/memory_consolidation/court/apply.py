"""The court's application (``integrate``, stage 7): the one legal act of memory change.

Assembles the consolidation report of a decision whose every birth Gold has
admitted. Its decision sections explain each change down to events, cases and
recorded results (И4); its ``apply`` section carries the exact values the
projection folds. Order follows spec part 2 §4.7: verdicts, trust and pending
evidence, births with their gate decisions, supersessions, transitions, quanta
with element roots and the retention plan, the digest. The registry entry —
the court's chain decision — is written after this report by the court.
Legitimacy is never argued with: a behavior Gold no longer admits leaves the
next boundary whatever its effectiveness.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import records
from .boundary import boundary_record
from .digest import digest_record
from .quantization import quantize

REPORT_V1 = "synapse.memory.consolidation-report/v1"


def _admitted(decision, gates) -> list[dict[str, Any]]:
    admitted = []
    for birth in decision["births"]:
        verdict = gates.get(birth["habit"]["id"])
        if verdict is None or not verdict["admitted"]:
            raise ValueError("a birth reached the report without its admitting Gold gates")
        admitted.append({**birth, "gates": dict(verdict)})
    return admitted


def _births(decision, admitted, legitimacy) -> tuple[dict, dict, dict, list]:
    """Metadata, frozen parts and legitimacy after the admitted births, and their report view."""
    habits = copy.deepcopy(decision["habits"])
    frozen, legitimacy_after, view = {}, copy.deepcopy(dict(legitimacy)), []
    for birth in admitted:
        habit_id = birth["habit"]["id"]
        frozen[habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        habits[habit_id] = {**copy.deepcopy(birth["metadata"]), "publication": birth["gates"]["publication"]}
        legitimacy_after[habit_id] = dict(birth["gates"]["status"])
        predecessor = (birth.get("boundary") or {}).get("predecessor")
        if predecessor is not None and predecessor in habits:
            habits[predecessor]["superseded_by"] = habit_id
            legitimacy_after[predecessor] = {**legitimacy_after.get(predecessor, {}), "admitted": False,
                                             "superseded_by": habit_id}
        view.append({"habit_id": habit_id, "trigger_id": birth["trigger"]["id"], "criteria": birth.get("criteria"),
                     "typed_check": birth.get("typed_check", "successor"), "arbitration": birth.get("arbitration"),
                     "independence": birth.get("independence"), "boundary": birth.get("boundary"),
                     "episodes": birth["basis"], "gates": birth["gates"], "state": habits[habit_id]["state"],
                     "trust": habits[habit_id]["trust"]})
    return habits, frozen, legitimacy_after, view


def _window_stats(draft, admitted) -> dict[str, Any]:
    verdicts = draft["verdicts"]
    anchored = sum(1 for item in verdicts if item["evidence"] and item["stage"] in {"1", "1b"})
    return {**draft["stats"], "anchored_segment_share": anchored / len(verdicts) if verdicts else None,
            "birth_episodes_verified_share": 1.0 if admitted else None,
            "advice_questions": draft["counsel"]["questions"], "advice_agreed": draft["counsel"]["agreed"]}


def assemble(*, state, draft, decision, configuration, inputs_hash, window_sessions, legitimacy,
             gates: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The report, snapshot boundary and digest records of one consolidation."""
    admitted = _admitted(decision, gates)
    habits, frozen, legitimacy_after, births_view = _births(decision, admitted, legitimacy)
    window = state["window"] + 1
    quantized = quantize(state, draft, decision, configuration, admitted, window)
    boundary = digest = None
    if draft["mode"] != "emergency":
        after = {**state, "habits": {**state["habits"], **habits}, "frozen": {**state["frozen"], **frozen},
                 "declared": decision["declared"], "slow_only": decision["slow_only"]}
        boundary = boundary_record(after, legitimacy_after, draft["consolidation_id"])
        digest = digest_record(draft, boundary["id"], decision["slow_only"], draft["consolidation_id"])
    report = {
        "schema_version": REPORT_V1, "consolidation_id": draft["consolidation_id"], "mode": draft["mode"],
        "window": {"index": window, "sessions": window_sessions}, "inputs_hash": inputs_hash,
        "policy": configuration.policy, "components": configuration.components,
        "configuration_sha256": configuration.configuration_sha256, "integrity": draft["integrity"],
        "evidence_problems": draft["evidence_problems"], "replay": draft["replay"],
        "window_stats": _window_stats(draft, admitted), "marker_verdicts": draft["verdicts"],
        **copy.deepcopy(decision["sections"]), "births": births_view, "slow_only_triggers": decision["slow_only"],
        "quantization": quantized["section"], "side_time_accounting": quantized["side_time"],
        "legitimacy": {habit_id: dict(verdict) for habit_id, verdict in sorted(legitimacy_after.items())},
        "digest_id": None if digest is None else digest["id"],
        "snapshot_boundary_after": None if boundary is None else boundary["id"],
        "apply": {"habits": habits, "frozen": frozen, "declared": decision["declared"],
                  "slow_only": decision["slow_only"], "pool": copy.deepcopy(decision["pool"]),
                  "quanta": quantized["quanta"], "parts": quantized["parts"],
                  "cursors": {item["run_id"]: {"to": item["to"], "head": item["head"]} for item in window_sessions},
                  "digest": digest}}
    return {"report": records.make("consolidation_report", report=report), "boundary": boundary, "digest": digest}
