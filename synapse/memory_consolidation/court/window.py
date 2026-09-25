"""Reading one consolidation window: sessions, plans and operation scopes.

The window is the history of each session after its consolidated cursor. Its
facts are read only from what was recorded: the durable history (whose chain
must still extend the consolidated prefix), the plans it fixed (re-derived from
their contracts) and the gateway journal (whose outcome for every recorded
action must equal the outcome the history recorded). A failure of an existing
source is reported as a problem that moves the court to emergency mode; an
episode from another executor or a broken side-time chain refuses the court.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from synapse.hardening import hash_event_chain

from ..configuration import MemoryConfiguration
from ..formation import TaskContractViolation, verify_plan
from ..records import canonical
from ..tools.episodes import EvidenceUnavailable, derive_scope, group_scopes
from ..tools.gateway import Gateway
from ..tools.journal import GatewayIntegrityError

BOUND_KINDS = ("external_error", "habit_activated", "habit_near_miss", "habit_miss", "slow_path_used",
               "habit_suppressed")


class CourtRefusal(RuntimeError):
    """The court cannot run on these inputs at all (spec part 2 §3.2)."""


@dataclass
class SessionFacts:
    """Everything the evaluation derives from one session's window."""

    session: Mapping[str, Any]
    found: dict[str, list]
    plans: dict[str, dict[str, Any]]
    markers: dict[str, tuple[dict, dict]]
    scopes: dict[str, dict[str, Any]]
    problems: list[str] = field(default_factory=list)
    evidence_problems: list[str] = field(default_factory=list)

    @property
    def run(self) -> str:
        return self.session["run_id"]


def index_events(history: list[Mapping[str, Any]], start: int, end: int) -> dict[str, list]:
    """Window events by type, with their history positions."""
    found: dict[str, list] = {}
    for position in range(start, end):
        event = history[position]
        if isinstance(event, Mapping):
            found.setdefault(event.get("type"), []).append((position, event))
    return found


def check_integrity(sessions, state, gateway: Gateway, gateway_records, executor: str) -> dict[str, Any]:
    """Chains of every source; the court refuses on another executor or a broken side log."""
    problems: list[str] = []
    for session in sessions:
        chain = hash_event_chain(session["history"][:session["to"]])
        cursor = state["cursors"].get(session["run_id"])
        if cursor is not None and cursor["to"] > 0 and (
                cursor["to"] > session["to"] or chain[cursor["to"] - 1]["hash"] != cursor["head"]):
            problems.append(f"history of {session['run_id']} differs from its consolidated prefix")
        if session.get("integrity_error"):
            problems.append(f"history of {session['run_id']}: {session['integrity_error']}")
    if any(record["kind"] == "STARTED" and record["body"]["executor"] != executor for record in gateway_records):
        raise CourtRefusal("an episode was executed by another executor than this configuration")
    try:
        gateway.side_records()
    except GatewayIntegrityError as exc:
        raise CourtRefusal("the side time log lost its chain") from exc
    return {"ok": not problems, "problems": problems}


def durations(gateway: Gateway) -> dict[int, float]:
    """Measured seconds of each gateway result (side log only)."""
    return {item["gw_seq"]: item["duration_ms"] / 1000.0 for item in gateway.side_records()}


def _plans(session: Mapping[str, Any], configuration: MemoryConfiguration) -> dict[str, dict[str, Any]]:
    plans: dict[str, dict[str, Any]] = {}
    for event in session["history"][:session["to"]]:
        if isinstance(event, Mapping) and event.get("type") == "task_plan_declared":
            plan = verify_plan(event["plan"], event["contract"], configuration)
            plans[plan["task_id"]] = plan
    return plans


def _referenced(found, run, gateway: Gateway, gateway_records, problems) -> list[int]:
    """Gateway records of window actions whose recorded outcome equals the journal's."""
    referenced = []
    for position, event in found.get("external_action", []):
        seq = event["outcome"]["ref"]["gw_seq"]
        try:
            recorded = gateway.recorded_outcome(seq, gateway_records)
        except (GatewayIntegrityError, EvidenceUnavailable, PermissionError) as exc:
            problems.append(f"{run}@{position}: {exc}")
            continue
        if canonical(recorded) != canonical(event["outcome"]):
            problems.append(f"{run}@{position}: history outcome differs from the gateway")
            continue
        referenced.append(seq)
    return referenced


def _scopes(run, referenced, gateway: Gateway, gateway_records, configuration, evidence_problems) -> dict:
    limit = max(referenced, default=-1)
    grouped = group_scopes(item for item in gateway_records
                           if item["body"].get("run_id") == run and item["seq"] <= limit)
    scopes: dict[str, dict[str, Any]] = {}
    for (_, name), items in grouped.items():
        if not any(item["seq"] in referenced for item in items):
            continue
        try:
            scopes[name] = derive_scope(items, configuration.tools, gateway.evidence)
        except EvidenceUnavailable as exc:
            evidence_problems.append(f"{run}/{name}: {exc}")
    return scopes


def read_session(session, configuration: MemoryConfiguration, gateway: Gateway, gateway_records) -> SessionFacts:
    """The recorded facts of one session's window."""
    run = session["run_id"]
    problems: list[str] = []
    try:
        plans = _plans(session, configuration)
    except TaskContractViolation as exc:
        problems.append(f"{run}: {exc}")
        plans = {}
    found = index_events(session["history"], session["from"], session["to"])
    referenced = _referenced(found, run, gateway, gateway_records, problems)
    evidence_problems: list[str] = []
    scopes = _scopes(run, referenced, gateway, gateway_records, configuration, evidence_problems)
    markers = {marker["id"]: (marker, plan) for plan in plans.values() for marker in plan["markers"]}
    return SessionFacts(session, found, plans, markers, scopes, problems, evidence_problems)


def learning_requests(facts: SessionFacts) -> list[dict[str, Any]]:
    """Declared habits without a body: the area and thresholds the court learns in."""
    return [{"run_id": facts.run, "habit_id": event["habit_id"], "area": event["area"],
             "frequency": event["frequency"], "stability": event["stability"]}
            for _, event in facts.found.get("habit_learning_requested", [])]
