"""The court's declared decision table at its thresholds (refinement §5, §6).

Pure data through the court's decision entry (``court.decide``): each declared
threshold of trust and the effectiveness automaton is checked just below, at
and just above its value; equal trust and permuted records never produce an
arbitrary winner. The heavy scenarios in this directory show the same rules
through the canonical launch; this table pins the boundaries themselves.
"""
from __future__ import annotations

import copy

import pytest

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.births import make_birth
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.projection import empty_state

CONDITION = {"event_types": ["external_error"], "context": ["search"],
             "when": [{"field": "route_kind", "op": "==", "value": "intl"}], "not_when": []}
QUOTA = [{"step": "call", "tool": "quota_status", "result_class": "ok"},
         {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
CAPACITY = [{"step": "call", "tool": "capacity_status", "result_class": "ok"},
            {"step": "call", "tool": "reserve_slot", "result_class": "ok"},
            {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
ROUTE = {"route": {"failed_arg": "route"}}


def _configuration(parameters):
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": {},
              "event_fields": []} for name in ("flights", "quota_status", "capacity_status", "reserve_slot")]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v1",
                  "servers": [{"id": "ops", "argv": ["ops"]}], "tools": tools,
                  "provenance": {f"{item['name']}:ops": {"ancestors": []} for item in tools}},
        "court": {"decision_rule": "threshold", "parameters": parameters}, "advisor": None, "scorer": None,
        "element": "acceptance.table"})


def _world(parameters, *habits):
    """A state at window 10 holding the given learned habits ``(label, steps, binding, metadata overrides)``."""
    configuration = _configuration(parameters)
    state = empty_state()
    state["window"] = 10
    ids = {}
    for label, steps, binding, overrides in habits:
        birth = make_birth(configuration.parameters, f"con_{label}", 1, condition=CONDITION, steps=steps,
                           binding=binding, template="external_error: route_kind {route_kind}",
                           source_episodes=[{"qid": f"q_{label}", "steps": steps}], basis_qids=[f"q_{label}"],
                           energy=1.0, trust=0.5, state_name="born")
        habit_id = birth["habit"]["id"]
        state["frozen"][habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        metadata = {**birth["metadata"], **overrides}
        # Trust lives per trigger; the habit's trust is its own trigger's value.
        metadata["context_trust"] = {metadata["trigger_id"]: metadata["trust"]}
        state["habits"][habit_id] = metadata
        ids[label] = habit_id
    return configuration, state, ids


def _fire(habit_id, trigger_id, index, *, signal=1.0, task="task"):
    return {"event_id": f"ev-{index}", "run_id": f"run-{index}", "habit_id": habit_id, "trigger_id": trigger_id,
            "task_id": f"{task}-{index}", "outcome": "success" if signal >= 0.6 else "failure",
            "runtime_outcome": None, "segment_marker_id": "mk", "segment_verdict": "confirmed", "signal": signal,
            "conflict_warning": False, "runner_up_habit_id": None, "semantic_score_micros": None,
            "context": {"event_type": "external_error", "fields": {"route_kind": "intl"}, "labels": ["search"]},
            "steps": [], "status": "counted", "why": None}


def _draft(fires=(), advice=None):
    return {"consolidation_id": "con_table", "mode": "full", "fires": list(fires), "declared_fires": [],
            "reactions": [], "near_misses": [], "misses": [], "requests": [], "cases": [], "replay": {},
            "conflict_advice": advice or {}, "arbitration": {}}


def _decide(configuration, state, fires=(), advice=None):
    legitimacy = {habit_id: {"admitted": True} for habit_id in state["habits"]}
    return decide(state, _draft(fires, advice), configuration, legitimacy)


def _moves(decision):
    return {(item["habit_id"], item["rule"], item["to"]) for item in decision["sections"]["transitions"]}


def _fires(habit_id, state, count, *, signal=1.0):
    trigger_id = state["habits"][habit_id]["trigger_id"]
    return [_fire(habit_id, trigger_id, index, signal=signal) for index in range(count)]


# -- trust: minimum evidence ---------------------------------------------------------
@pytest.mark.parametrize("ready, updated", [(2, False), (3, True), (4, True)])
def test_trust_changes_only_at_the_declared_minimum_evidence(ready, updated):
    configuration, state, ids = _world({"min_evidence": 3}, ("q", QUOTA, [{"step": 0, "args": ROUTE},
                                                                          {"step": 1, "failed_action": True}], {}))
    decision = _decide(configuration, state, _fires(ids["q"], state, ready))
    decisions = decision["sections"]["trust_decisions"]
    assert bool(decisions) is updated
    metadata = decision["habits"][ids["q"]]
    if updated:
        assert decisions[0]["counted"] == ready and metadata["trust"] == 0.5 + 0.5 * (1.0 - 0.5)
        assert metadata["pending"] == []
    else:
        assert metadata["trust"] == 0.5 and len(metadata["pending"]) == ready


# -- T1: born -> active (trust, fires, tasks) ------------------------------------------
BINDING = [{"step": 0, "args": ROUTE}, {"step": 1, "failed_action": True}]
_NO_UPDATE = {"min_evidence": 100}


@pytest.mark.parametrize("trust, promoted", [(0.69, False), (0.70, True), (0.71, True)])
def test_t1_trust_threshold(trust, promoted):
    configuration, state, ids = _world({**_NO_UPDATE, "t1_trust": 0.70, "t1_fires": 1, "t1_tasks": 1},
                                       ("q", QUOTA, BINDING, {"trust": trust}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 1)))
    assert ((ids["q"], "T1", "active") in moves) is promoted


@pytest.mark.parametrize("earlier, promoted", [(3, False), (4, True), (5, True)])
def test_t1_fires_threshold(earlier, promoted):
    # One more fire this window: the counts are 4, 5 and 6 against t1_fires = 5.
    configuration, state, ids = _world({**_NO_UPDATE, "t1_trust": 0.5, "t1_fires": 5, "t1_tasks": 1},
                                       ("q", QUOTA, BINDING, {"fires_since_birth": earlier}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 1)))
    assert ((ids["q"], "T1", "active") in moves) is promoted


@pytest.mark.parametrize("earlier_tasks, promoted", [(0, False), (1, True), (2, True)])
def test_t1_tasks_threshold(earlier_tasks, promoted):
    # One more task this window: 1, 2 and 3 distinct tasks against t1_tasks = 2.
    configuration, state, ids = _world(
        {**_NO_UPDATE, "t1_trust": 0.5, "t1_fires": 1, "t1_tasks": 2},
        ("q", QUOTA, BINDING, {"tasks_since_birth": [f"earlier-{index}" for index in range(earlier_tasks)]}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 1)))
    assert ((ids["q"], "T1", "active") in moves) is promoted


# -- T3: active -> probation (window mean below t3_signal over at least t3_fires) --------
@pytest.mark.parametrize("fires, demoted", [(2, False), (3, True), (4, True)])
def test_t3_fire_count_threshold(fires, demoted):
    configuration, state, ids = _world({**_NO_UPDATE, "t3_fires": 3, "t3_signal": 0.65},
                                       ("q", QUOTA, BINDING, {"state": "active"}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, fires, signal=0.4)))
    assert ((ids["q"], "T3", "probation") in moves) is demoted


@pytest.mark.parametrize("signal, demoted", [(0.64, True), (0.65, False), (0.66, False)])
def test_t3_signal_threshold(signal, demoted):
    configuration, state, ids = _world({**_NO_UPDATE, "t3_fires": 3, "t3_signal": 0.65},
                                       ("q", QUOTA, BINDING, {"state": "active"}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 3, signal=signal)))
    assert ((ids["q"], "T3", "probation") in moves) is demoted


# -- T4: probation -> active (fires and mean in probation) --------------------------------
@pytest.mark.parametrize("earlier, promoted", [(3, False), (4, True), (5, True)])
def test_t4_fire_count_threshold(earlier, promoted):
    configuration, state, ids = _world(
        {**_NO_UPDATE, "t4_fires": 5, "t4_signal": 0.7},
        ("q", QUOTA, BINDING, {"state": "probation", "fires_in_state": earlier, "signals_in_state": earlier,
                               "signal_sum_in_state": float(earlier)}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 1)))
    assert ((ids["q"], "T4", "active") in moves) is promoted


@pytest.mark.parametrize("mean, promoted", [(0.69, False), (0.70, True), (0.71, True)])
def test_t4_signal_threshold(mean, promoted):
    configuration, state, ids = _world(
        {**_NO_UPDATE, "t4_fires": 1, "t4_signal": 0.7},
        ("q", QUOTA, BINDING, {"state": "probation"}))
    moves = _moves(_decide(configuration, state, _fires(ids["q"], state, 1, signal=mean)))
    assert ((ids["q"], "T4", "active") in moves) is promoted


# -- T2/T5: giving up after idle windows ----------------------------------------------------
@pytest.mark.parametrize("state_name, rule, target", [("born", "T2", "probation"), ("probation", "T5", "dormant")])
@pytest.mark.parametrize("idle, gave_up", [(3, False), (4, True), (5, True)])
def test_idle_windows_threshold(state_name, rule, target, idle, gave_up):
    # No fire this window: the idle count becomes idle + 1 against m_idle = 5.
    configuration, state, ids = _world({**_NO_UPDATE, "m_idle": 5},
                                       ("q", QUOTA, BINDING, {"state": state_name, "idle_windows": idle}))
    moves = _moves(_decide(configuration, state))
    assert ((ids["q"], rule, target) in moves) is gave_up


# -- T9: active and idle -> dormant (only when another habit covers it) -------------------
@pytest.mark.parametrize("idle, archived", [(8, False), (9, True), (10, True)])
def test_t9_idle_threshold(idle, archived):
    # Two habits with one applicability and outcome: the first covers the second, so neither is key.
    configuration, state, ids = _world(
        {**_NO_UPDATE, "n_t9": 10, "conflict_gap": 0.0},
        ("q", QUOTA, BINDING, {"state": "active", "idle_windows": idle}),
        ("c", CAPACITY, [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE}, {"step": 2, "failed_action": True}],
         {"state": "active", "idle_windows": idle}))
    moves = _moves(_decide(configuration, state))
    assert ((ids["q"], "T9", "dormant") in moves) is archived


# -- T7: dormant -> extinct ---------------------------------------------------------------
@pytest.mark.parametrize("cold, extinct", [(1, False), (2, True), (3, True)])
def test_t7_cold_windows_threshold(cold, extinct):
    configuration, state, ids = _world({**_NO_UPDATE, "k_extinct": 3},
                                       ("q", QUOTA, BINDING, {"state": "dormant", "cold_windows": cold}))
    moves = _moves(_decide(configuration, state))
    assert ((ids["q"], "T7", "extinct") in moves) is extinct


# -- pending evidence expires after its declared number of windows -------------------------
@pytest.mark.parametrize("age, expired", [(5, False), (6, True), (7, True)])
def test_pending_evidence_horizon(age, expired):
    configuration, state, ids = _world({"min_evidence": 3, "pending_max_windows": 6}, ("q", QUOTA, BINDING, {}))
    metadata = state["habits"][ids["q"]]
    metadata["pending"] = [{"event_id": "old", "run_id": "run-old", "trigger_id": metadata["trigger_id"],
                            "signal": 1.0, "window": state["window"] + 1 - age, "why": None, "provisional": False}]
    report = _decide(configuration, state)["sections"]
    assert bool(report["expired_pending"]) is expired


# -- conflicts: the trust gap of step 1 -------------------------------------------------------
@pytest.mark.parametrize("gap, step", [(0.29, 3), (0.30, 1), (0.31, 1)])
def test_conflict_gap_threshold(gap, step):
    configuration, state, ids = _world(
        {**_NO_UPDATE, "conflict_gap": 0.30},
        ("q", QUOTA, BINDING, {"trust": 0.5 + gap}),
        ("c", CAPACITY, [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE}, {"step": 2, "failed_action": True}],
         {"trust": 0.5}))
    conflict, = _decide(configuration, state)["sections"]["conflicts"]
    assert conflict["step"] == step
    if step == 1:
        assert conflict["habits"]["A"] == ids["q"] and conflict["resolution"] == "A_selected_by_trust_gap"
    else:
        assert conflict["resolution"] == "both_probation_trigger_slow_only"


def test_equal_trust_and_permuted_records_give_one_answer():
    habits = (("q", QUOTA, BINDING, {"trust": 0.6}),
              ("c", CAPACITY, [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE},
                               {"step": 2, "failed_action": True}], {"trust": 0.6}))
    configuration, state, ids = _world({**_NO_UPDATE, "conflict_gap": 0.30}, *habits)
    fires = _fires(ids["q"], state, 2) + _fires(ids["c"], state, 2)
    first = _decide(configuration, state, fires)
    permuted = copy.deepcopy(state)
    for key in ("habits", "frozen"):
        permuted[key] = dict(reversed(list(permuted[key].items())))
    second = _decide(configuration, permuted, list(reversed(fires)))
    assert first["sections"] == second["sections"] and first["slow_only"] == second["slow_only"]
    conflict, = first["sections"]["conflicts"]
    # Equal trust: identity order names the senior, and no step selects a winner by it.
    assert conflict["habits"]["A"] == min(ids.values()) and conflict["step"] == 3
