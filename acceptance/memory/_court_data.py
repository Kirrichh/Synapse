"""Plain data for the court's decision entry: a configuration, learned habits and verified fires.

The contract files of this directory drive ``court.decide`` window by window
without a launch. They share these builders so that every file states only its
own rules.
"""
from __future__ import annotations

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.births import make_birth
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.evaluate import empty_draft
from synapse.memory_consolidation.court.projection import empty_state
from synapse.memory_consolidation.learning.applicability import explanation_of

CONDITION = {"event_types": ["external_error"], "context": ["search"],
             "when": [{"field": "route_kind", "op": "==", "value": "intl"}], "not_when": []}
QUOTA = [{"step": "call", "tool": "quota_status", "result_class": "ok"},
         {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
CAPACITY = [{"step": "call", "tool": "capacity_status", "result_class": "ok"},
            {"step": "call", "tool": "reserve_slot", "result_class": "ok"},
            {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
ROUTE = {"route": {"failed_arg": "route"}}
STEPS = {"q": QUOTA, "c": CAPACITY}
BINDINGS = {"q": [{"step": 0, "args": ROUTE}, {"step": 1, "failed_action": True}],
            "c": [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE}, {"step": 2, "failed_action": True}]}


def configuration(parameters=None, element="acceptance.court", rule="threshold"):
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": {},
              "event_fields": []} for name in ("flights", "quota_status", "capacity_status", "reserve_slot")]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2",
                  "servers": [{"id": "ops", "argv": ["ops"]}], "tools": tools,
                  "provenance": {f"{item['name']}:ops": {"ancestors": []} for item in tools}},
        "court": {"decision_rule": rule, "parameters": parameters or {}}, "advisor": None, "scorer": None,
        "element": element})


def world(parameters=None, labels=("q",), *, conditions=None, trust=0.5, state_name="born", window=10,
          rule="threshold", **overrides):
    """A court state holding the quota (``q``) and capacity (``c``) recoveries as learned habits."""
    config = configuration(parameters, rule=rule)
    state = empty_state()
    state["window"] = window
    ids = {}
    for label in labels:
        steps = STEPS[label]
        birth = make_birth(config.parameters, f"con_{label}", 1, condition=(conditions or {}).get(label, CONDITION),
                           applicability=explanation_of({}), steps=steps, binding=BINDINGS[label],
                           template="external_error: route_kind {route_kind}",
                           source_episodes=[{"qid": f"q_{label}", "steps": steps}], basis_qids=[f"q_{label}"],
                           energy=1.0, trust=trust, state_name=state_name)
        habit_id = birth["habit"]["id"]
        metadata = {**birth["metadata"], **overrides.get(label, {})}
        metadata["context_trust"] = {metadata["trigger_id"]: metadata["trust"]}
        state["frozen"][habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        state["habits"][habit_id] = metadata
        ids[label] = habit_id
    return config, state, ids


def fire(habit_id, trigger_id, index, success, *, fields=None):
    """One verified fire of a learned habit, in its own run and task."""
    return {"event_id": f"ev-{index:04d}", "run_id": f"run-{index:04d}", "habit_id": habit_id,
            "trigger_id": trigger_id, "task_id": f"task-{index}", "outcome": "success" if success else "failure",
            "runtime_outcome": None, "segment_marker_id": "mk", "segment_verdict": "confirmed" if success else "failed",
            "signal": 1.0 if success else 0.0, "conflict_warning": False, "runner_up_habit_id": None,
            "semantic_score_micros": None,
            "context": {"event_type": "external_error", "fields": fields or {"route_kind": "intl"},
                        "labels": ["search"]},
            "steps": [], "status": "counted", "why": None}


def draft(fires=(), *, mode="full", advice=None, suppressed=(), **extra):
    return {**empty_draft("con_contract", mode, {"ok": True, "problems": []}), "fires": list(fires),
            "conflict_advice": advice or {}, "arbitration": {}, "suppressed": list(suppressed), **extra}


def window(config, state, fires=(), **options):
    """One consolidation; the state after it (the fold of its decision) and the decision."""
    decision = decide(state, draft(fires, **options), config,
                      {habit_id: {"admitted": True} for habit_id in state["habits"]})
    after = {**state, "habits": decision["habits"], "slow_only": decision["slow_only"], "window": state["window"] + 1}
    return after, decision
