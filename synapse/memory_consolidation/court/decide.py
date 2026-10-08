"""The court's decision (``integrate``, stages 3–6): deterministic and cheap.

No model is asked here (И6): advice and similarity were recorded by the
evaluation. Given the previous state, the draft, the declared parameters and
the legitimacy Gold reports for existing behaviors, the result is determined;
ties are broken by identity. An emergency consolidation stops after stage 3:
verdicts and signals are kept as pending evidence and nothing else changes
(fail-closed). A reassessment observes no window: it completes recorded
metadata with neutral values, archives habits whose basis the reassessment no
longer verifies (TR), returns to probation habits whose recorded promotion it
does not find resting on confirmed experience (TU), records the hypothesis
statuses it decided again from the record (with the forgets of what they rest
on) and judges every competitor pair again.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from ..configuration import MemoryConfiguration
from .automaton import automaton, forced_transitions
from .births import pool_stage
from .boundaries import boundary_stage
from .cold import cold_stage
from .dependencies import forgotten_stage, revoke_forgotten
from .compositions import composition_stage
from .conflicts import birth_conflicts, conflict_stage, recorded_bases
from .habit_state import complete_metadata
from .hypotheses import empty_section, hypothesis_stage, reassessed
from .knowledge import knowledge_stage
from .reassessment import lost_bases
from .trust import trust_stage

REPORT_SECTIONS = ("trust_decisions", "pending_evidence", "excluded_signals", "expired_pending",
                   "declared_observations", "recommendations", "conflicts", "votes", "pool_updates",
                   "supersessions", "boundary_refusals", "transitions", "refused_births", "compositions",
                   "composition_reviews", "contract_violations", "dependencies")


@dataclass
class DecisionContext:
    """What every decision stage of one consolidation reads, and the report it writes."""

    state: dict[str, Any]
    draft: dict[str, Any]
    configuration: MemoryConfiguration
    window: int
    report: dict[str, Any]

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self.configuration.parameters


def _empty_report() -> dict[str, Any]:
    report: dict[str, Any] = {name: [] for name in REPORT_SECTIONS}
    report["cold_checks"] = {"dormant_matches": [], "extinct_matches": []}
    report["knowledge"] = {"declared": [], "copies": [], "corrections": [], "conflicts": [], "revisions": [],
                           "reindexed": []}
    return report


def _distinct_fires(draft, report) -> dict[str, Any]:
    """The draft with every fire once: an event delivered again is excluded, never counted twice."""
    seen: set[tuple[str, str, str]] = set()
    fires = []
    for fire in draft["fires"]:
        key = (fire["habit_id"], fire["run_id"], fire["event_id"])
        if key in seen:
            report["excluded_signals"].append({"habit_id": fire["habit_id"], "run_id": fire["run_id"],
                                               "event_id": fire["event_id"], "why": "repeated_event"})
            continue
        seen.add(key)
        fires.append(fire)
    return {**draft, "fires": fires}


def _contract_violations(context, habits, forced) -> None:
    """A body that attempted what its contracts forbid loses its fast path at once (learned) or is reported to
    its author (declared); nothing waits for statistics (review R6)."""
    for layer, fires in ((2, context.draft["fires"]), (1, context.draft["declared_fires"])):
        for fire in sorted(fires, key=lambda item: (item["run_id"], item["event_id"])):
            if not fire.get("refusals"):
                continue
            context.report["contract_violations"].append({
                "habit_id": fire["habit_id"], "layer": layer, "run_id": fire["run_id"], "event_id": fire["event_id"],
                "refusals": list(fire["refusals"])})
            basis = f"the body attempted what its contracts forbid: {fire['refusals'][0]}"
            if layer == 2 and fire["habit_id"] in habits and forced.get(fire["habit_id"], ("",))[0] != "TS":
                # A supersession archives it already, naming its successor; the violation stays reported.
                forced[fire["habit_id"]] = ("TV", basis)
            elif layer == 1:
                context.report["recommendations"].append({
                    "habit_id": fire["habit_id"], "layer": 1, "recommendation": "review_declared_habit",
                    "basis": basis, "authority": "GOVERNING_HUMAN"})


def _reassess(context, state, draft) -> dict[str, Any]:
    """A reassessment observes no window: it completes recorded metadata neutrally, archives habits whose basis
    is no longer verified and judges every competitor pair again under the policy in force."""
    habits = copy.deepcopy(state["habits"])
    completed = complete_metadata(habits)
    unverified = {}
    for item in draft["reassessment"].get("promotions", []):
        # Confirmed experience since birth, from the recorded fires (a lower bound when some were not kept).
        habits[item["habit_id"]].update(counted_since_birth=item["counted_since_birth"],
                                        counted_tasks_since_birth=list(item["counted_tasks_since_birth"]))
        if item["verdict"] == "promotion_not_verified":
            unverified[item["habit_id"]] = ("TU", f"{item['promotion']['rule']} of window "
                                                  f"{item['promotion']['window']}: {item['reason']}")
    for item in draft["reassessment"]["habits"]:
        if (item.get("republication") or {}).get("admitted"):
            habits[item["habit_id"]]["publication"] = item["republication"]["publication"]
        if item["verdict"] == "basis_holds":
            # Its bases were judged again under these contracts: its applicability is verified under them.
            habits[item["habit_id"]]["verified_under"] = {"contracts": dict(item["contracts"])}
    recorded_bases(habits, draft["reassessment"].get("resolutions", []))
    forced = {**unverified, **lost_bases(draft["reassessment"])}  # A lost basis archives over a probation.
    slow_only = conflict_stage(context.parameters, state, habits, draft, context.report, forced)
    forced_transitions(context, habits, forced)
    # Every status decided again from the record, then the forgets of what it rests on applied to it.
    hypotheses = reassessed(draft["reassessment"].get("hypotheses", []))
    revoke_forgotten({**state, "hypotheses": {}}, hypotheses, context.window)
    return {"sections": context.report, "habits": habits, "declared": copy.deepcopy(state["declared"]), "births": [],
            "pool": {}, "slow_only": slow_only, "knowledge": {"versions": {}, "uses": {}},
            "hypotheses": {"updates": hypotheses, "section": empty_section()},
            "reassessment": {**draft["reassessment"], "fields_completed": completed,
                             "slow_only_added": [item for item in slow_only if item not in state["slow_only"]]}}


def decide(state, draft, configuration, legitimacy, refused: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Stages 3–6. Returns the decision sections and the metadata they produce.

    ``refused`` names births whose behavior Gold did not admit, with the
    reason; the court decides again without them, so no later stage acts on
    a birth that never happened.
    """
    refused = dict(refused or {})
    report = _empty_report()
    draft = _distinct_fires(draft, report)
    context = DecisionContext(state, draft, configuration, state["window"] + 1, report)
    if draft["mode"] == "reassess":
        return _reassess(context, state, draft)
    habits, declared = trust_stage(configuration.parameters, state, draft, draft["mode"], context.report)
    if draft["mode"] == "emergency":
        return {"sections": context.report, "habits": habits, "declared": declared, "births": [], "pool": {},
                "slow_only": copy.deepcopy(state["slow_only"]), "knowledge": {"versions": {}, "uses": {}},
                "hypotheses": {"updates": {}, "section": empty_section()}}
    forced: dict[str, tuple[str, str]] = {}
    slow_only = conflict_stage(configuration.parameters, state, habits, draft, context.report, forced)
    knowledge = knowledge_stage(context, habits, forced, configuration)
    # The window's checks and corrections in the gateway's order, then the forgets of what they rest on.
    hypotheses = hypothesis_stage(context, knowledge.pop("corrections"))
    forgotten_stage(context, habits, forced, knowledge, hypotheses["updates"])
    wakes, consumed = cold_stage(context, legitimacy)
    births: list[dict[str, Any]] = []
    boundary_stage(context, habits, births, forced, refused)
    composed = composition_stage(context, habits, births, forced, refused, legitimacy)
    pool = {**pool_stage(context, births, consumed, refused), **composed}
    slow_only = birth_conflicts(configuration.parameters, state, habits, births, slow_only, context.report, forced)
    for vote in context.report["votes"]:
        if vote["habit_id"] in habits:
            habits[vote["habit_id"]]["votes"] += 1
    _contract_violations(context, habits, forced)
    automaton(context, habits, forced, wakes, legitimacy)
    return {"sections": context.report, "habits": habits, "declared": declared, "births": births, "pool": pool,
            "slow_only": slow_only, "knowledge": knowledge, "hypotheses": hypotheses}
