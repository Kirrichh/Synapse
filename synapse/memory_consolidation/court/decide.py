"""The court's decision (``integrate``, stages 3–6): deterministic and cheap.

No model is asked here (И6): advice and similarity were recorded by the
evaluation. Given the previous state, the draft, the declared parameters and
the legitimacy Gold reports for existing behaviors, the result is determined;
ties are broken by identity. An emergency consolidation stops after stage 3:
verdicts and signals are kept as pending evidence and nothing else changes
(fail-closed).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from ..configuration import MemoryConfiguration
from .automaton import automaton
from .births import pool_stage
from .boundaries import boundary_stage
from .cold import cold_stage
from .conflicts import conflict_stage
from .trust import trust_stage

REPORT_SECTIONS = ("trust_decisions", "pending_evidence", "excluded_signals", "expired_pending",
                   "declared_observations", "recommendations", "conflicts", "votes", "pool_updates",
                   "supersessions", "transitions", "refused_births")


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
    return report


def decide(state, draft, configuration, legitimacy, refused: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Stages 3–6. Returns the decision sections and the metadata they produce.

    ``refused`` names births whose behavior Gold did not admit, with the
    reason; the court decides again without them, so no later stage acts on
    a birth that never happened.
    """
    refused = dict(refused or {})
    context = DecisionContext(state, draft, configuration, state["window"] + 1, _empty_report())
    habits, declared, signals = trust_stage(configuration.parameters, state, draft, draft["mode"], context.report)
    if draft["mode"] == "emergency":
        return {"sections": context.report, "habits": habits, "declared": declared, "births": [], "pool": {},
                "slow_only": copy.deepcopy(state["slow_only"])}
    forced: dict[str, tuple[str, str]] = {}
    slow_only = conflict_stage(configuration.parameters, state, habits, draft, context.report, forced)
    wakes, consumed = cold_stage(context, legitimacy)
    births: list[dict[str, Any]] = []
    boundary_stage(context, habits, births, forced, refused)
    pool = pool_stage(context, births, consumed, refused)
    for vote in context.report["votes"]:
        if vote["habit_id"] in habits:
            habits[vote["habit_id"]]["votes"] += 1
    automaton(context, habits, signals, forced, wakes, legitimacy)
    return {"sections": context.report, "habits": habits, "declared": declared, "births": births, "pool": pool,
            "slow_only": slow_only}
