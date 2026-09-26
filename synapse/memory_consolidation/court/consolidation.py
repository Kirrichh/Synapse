"""One consolidation of a memory owner: modes, identity, idempotency and the commit order.

The court runs inside the canonical launch, under the owner session of the
project journal, so every task stream reaches one writer:

* ``full`` — at the end of a session whose palace consolidates during dream;
* ``summary`` — ``consolidate palace`` in a maintenance program, all sessions;
* ``emergency`` — before a crashed session continues, over its tail only:
  stages 1–2 and 7a, provisional, no trust, births or boundary (fail-closed).
  An integrity failure of an existing source also runs the court in emergency.

The consolidation identity is the digest of its inputs (owner, chain head,
mode, window cursors and heads, the exact-subject tail, the gateway head, the
configuration). A window with nothing new returns the head in force. Commit
order: births pass Gold's gates (refused births make the court decide again
without them), the report is written, then the one decision on Gold's chain —
the registry entry — and only then the snapshot boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from synapse.experiments.gold.project_court import append_decision, establish_tail
from synapse.hardening import hash_event_chain

from .. import records
from ..configuration import MemoryConfiguration
from ..owner import MemoryOwner
from ..tools.gateway import Gateway
from .apply import assemble
from .custody import take_custody
from .decide import decide
from .evaluate import DreamInputs, evaluate
from .retention import apply_acts, pending_acts, retention_pass
from .window import closed_prefix, preflight, significant

MODES = ("full", "summary", "emergency")


@dataclass
class CourtPorts:
    """What the court uses outside its own records."""

    gateway: Gateway
    executor: str
    legitimacy: Any
    read_session: Callable[[Mapping[str, Any]], dict[str, Any]]
    replay: Callable[[Mapping[str, Any]], dict[str, Any]]
    #: A session re-executed from its replay data in custody and its recorded results only.
    reproduce: Callable[[Mapping[str, Any]], dict[str, Any]]


def _pinned(history) -> str | None:
    for event in history:
        if isinstance(event, Mapping) and event.get("type") == "memory_session_opened":
            return event.get("boundary")
    return None


def _session(entry, recorded, state, ending: bool) -> dict[str, Any]:
    """A session's window since its cursor; a live session's window stops before a segment it is still in."""
    cursor = state["cursors"].get(entry["run_id"], {"to": 0})
    history = recorded["history"]
    end = len(history) if ending else max(cursor["to"], closed_prefix(history, len(history)))
    return {"run_id": entry["run_id"], "source_code": recorded["source_code"], "source_hash": entry["source_hash"],
            "initial_bindings": recorded["initial_bindings"], "history": history, "from": cursor["to"],
            "to": end, "pinned": _pinned(history), "integrity_error": recorded.get("integrity_error")}


def _window(owner, ports, state, mode, current, guard) -> list[dict[str, Any]]:
    """Sessions with something new since their cursor; emergency sees only the crashed one."""
    sessions = []
    for entry in owner.sessions(guard=guard):
        if mode == "emergency" and entry["run_id"] != current["run_id"]:
            continue
        own = current is not None and entry["run_id"] == current["run_id"]
        recorded = current if own else ports.read_session(entry)
        session = _session(entry, recorded, state, ending=own and mode == "full")
        if session["to"] > session["from"] and significant(session):
            sessions.append(session)
    return sessions


def _cursor(session) -> dict[str, Any]:
    chain = hash_event_chain(session["history"][:session["to"]])
    return {"run_id": session["run_id"], "from": session["from"], "to": session["to"],
            "head": chain[-1]["hash"] if chain else None}


def _identity(owner, tail, mode, cursors, configuration, gateway_head, retention_passes) -> str:
    inputs = {"owner": owner.identity, "predecessor": tail["predecessor"], "mode": mode, "sessions": cursors,
              "exact_tail": [item["input"] for item in tail["entries"]], "gateway_head": gateway_head,
              "configuration_sha256": configuration.configuration_sha256,
              "policy_ref": configuration.policy["parameters_ref"], "retention_passes": retention_passes}
    return records.make("consolidation_inputs", inputs=inputs)["id"]


def _legitimacy(ports, state) -> dict[str, dict[str, Any]]:
    """Gold's current verdict on every learned behavior the court knows."""
    return {habit_id: ports.legitimacy.status(habit_id, metadata)
            for habit_id, metadata in sorted(state["habits"].items()) if metadata.get("publication") is not None}


def _gated_decision(state, draft, configuration, legitimacy, ports) -> tuple[dict, dict]:
    """Decide; every birth goes through Gold's gates; refused births are decided away."""
    refused: dict[str, str] = {}
    gates: dict[str, dict[str, Any]] = {}
    while True:
        decision = decide(state, draft, configuration, legitimacy, refused)
        pending = [birth for birth in decision["births"] if birth["habit"]["id"] not in gates]
        for birth in pending:
            verdict = ports.legitimacy.publish(birth, configuration)
            gates[birth["habit"]["id"]] = verdict
            if not verdict["admitted"]:
                refused[birth["habit"]["id"]] = verdict["reason"]
        if all(gates[birth["habit"]["id"]]["admitted"] for birth in decision["births"]):
            return decision, gates


def _supersede(ports, state, decision, gates) -> None:
    for birth in decision["births"]:
        predecessor = (birth.get("boundary") or {}).get("predecessor")
        if predecessor is not None:
            ports.legitimacy.supersede(state["habits"][predecessor].get("publication"),
                                       gates[birth["habit"]["id"]]["publication"])


def _head(owner, guard, run_id) -> dict[str, Any]:
    """The decision in force with nothing new: the chain head, or the last report that covered a session."""
    applied = owner.applied(guard=guard)
    for item in reversed(applied):
        report = item["report"]
        if run_id is None or (report is not None
                              and any(entry["run_id"] == run_id for entry in report["window"]["sessions"])):
            consolidation = item["decision"].get("consolidation") or {"consolidation_id": None, "mode": None}
            return _summary(consolidation, item["receipt"], report)
    return {"consolidation_id": None, "mode": None, "decision": applied[-1]["receipt"] if applied else None,
            "report": None, "births": [], "transitions": [], "boundary": None}


def _summary(consolidation, receipt, report) -> dict[str, Any]:
    return {"consolidation_id": consolidation["consolidation_id"], "mode": consolidation["mode"],
            "decision": receipt, "report": report["consolidation_id"] if report else None,
            "births": [item["habit_id"] for item in (report or {}).get("births", [])],
            "transitions": [{"habit_id": item["habit_id"], "rule": item["rule"]}
                            for item in (report or {}).get("transitions", [])],
            "boundary": None if report is None else report["snapshot_boundary_after"]}


def consolidate(owner: MemoryOwner, configuration: MemoryConfiguration, ports: CourtPorts, guard, *,
                mode: str, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run the court once under the owner session ``guard``; returns the summary in force."""
    if mode not in MODES:
        raise ValueError("consolidation mode is full, summary or emergency")
    state = owner.state(guard=guard)
    sessions = _window(owner, ports, state, mode, current, guard)
    tail = establish_tail(owner.store, guard, project_identity=owner.identity)
    if not sessions and tail["unchanged"]:
        return _head(owner, guard, None if current is None else current["run_id"])
    # Retention facts recorded since the last report enter this one; nothing edits the state in place.
    passes = owner.retention_passes(guard=guard)
    acts = pending_acts(passes, state)
    retained, retention_section = apply_acts(state, acts, len(passes))
    state = {**state, "quanta": {**state["quanta"], **retained}, "retention": retention_section}
    gateway_records = ports.gateway.records()
    integrity = preflight(sessions, state, configuration, ports.gateway, gateway_records, ports.executor)
    effective_mode = mode if integrity["ok"] else "emergency"
    cursors = [_cursor(session) for session in sessions]
    head = gateway_records[-1]["hash"] if gateway_records else None
    consolidation_id = _identity(owner, tail, effective_mode, cursors, configuration, head, len(passes))
    draft = evaluate(DreamInputs(consolidation_id, effective_mode, sessions, state, configuration, ports.gateway,
                                 ports.executor, ports.replay,
                                 frozenset({current["run_id"]} if current is not None and mode == "full" else ())))
    legitimacy = _legitimacy(ports, state)
    decision, gates = _gated_decision(state, draft, configuration, legitimacy, ports)
    _supersede(ports, state, decision, gates)
    custody = take_custody(ports.gateway.evidence, sessions, draft["cases"], ports.executor)
    result = assemble(state=state, draft=draft, decision=decision, configuration=configuration,
                      inputs_hash={"sessions": cursors, "exact_tail": len(tail["entries"]), "gateway_head": head,
                                   "retention_passes": len(passes)},
                      window_sessions=cursors, legitimacy=legitimacy, gates=gates, custody=custody,
                      retention={"acts": acts, "quanta": retained})
    receipt = owner.put_report(guard, result["report"])
    summary = append_decision(owner.store, guard, project_identity=owner.identity, tail=tail,
                              consolidation={"consolidation_id": consolidation_id, "mode": effective_mode,
                                             "report": receipt})
    if result["boundary"] is not None:
        owner.put_boundary(guard, result["boundary"])
        # Retention runs after the decision is applied, under the same owner session (spec part 3 §8.4).
        retention_pass(owner, configuration, ports, guard)
    return _summary({"consolidation_id": consolidation_id, "mode": effective_mode}, summary["decision"],
                    result["report"]["report"])


def consolidate_exact(owner: MemoryOwner, guard) -> dict[str, Any]:
    """A decision of an owner without memory configuration: only the exact-subject section."""
    tail = establish_tail(owner.store, guard, project_identity=owner.identity)
    if tail["unchanged"]:
        return _head(owner, guard, None)
    consolidation_id = records.make("consolidation_inputs", inputs={
        "owner": owner.identity, "predecessor": tail["predecessor"], "mode": "full", "sessions": [],
        "exact_tail": [item["input"] for item in tail["entries"]]})["id"]
    summary = append_decision(owner.store, guard, project_identity=owner.identity, tail=tail,
                              consolidation={"consolidation_id": consolidation_id, "mode": "full", "report": None})
    return _summary({"consolidation_id": consolidation_id, "mode": "full"}, summary["decision"], None)

