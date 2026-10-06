"""The decision of a reassessment (review, package 5), as pure data through the court's decision entry.

* Metadata recorded before a policy read a field is completed with the value
  that claims nothing — no verified resolution, no compared outcome, no error
  evidence — and the completion is reported per habit.
* A habit whose basis the reassessment no longer verifies is archived (TR, a
  changed basis); a habit whose basis holds keeps its state.
* Competitors are judged again: a pair an earlier policy resolved on a model's
  advice, with no verified winner recorded, goes back to the slow path; a pair
  with a recorded verified resolution keeps it.
* A reassessment observes no window: trust, pending evidence and the
  automaton's counters are untouched.
* A kept habit is verified under the current tool contracts; a session loads a
  learned habit only under the contracts it was verified under — one whose
  body's tool contract changed, or that names none, is listed as unverified
  and not loaded until a reassessment verifies it again.
"""
from __future__ import annotations

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.births import make_birth
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.evaluate import empty_draft
from synapse.memory_consolidation.court.projection import empty_state
from types import SimpleNamespace

from synapse.memory_consolidation.court.boundary import habit_entry
from synapse.memory_consolidation.learning.applicability import explanation_of
from synapse.memory_consolidation.learning.behavior import contracts_of
from synapse.memory_consolidation.session import MemorySession

CONDITION = {"event_types": ["external_error"], "context": ["search"],
             "when": [{"field": "route_kind", "op": "==", "value": "intl"}], "not_when": []}
QUOTA = [{"step": "call", "tool": "quota_status", "result_class": "ok"},
         {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
CAPACITY = [{"step": "call", "tool": "capacity_status", "result_class": "ok"},
            {"step": "call", "tool": "reserve_slot", "result_class": "ok"},
            {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
ROUTE = {"route": {"failed_arg": "route"}}
BINDINGS = {"q": [{"step": 0, "args": ROUTE}, {"step": 1, "failed_action": True}],
            "c": [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE}, {"step": 2, "failed_action": True}]}
LEGACY = ("yields_to", "compared", "tail")


def _configuration():
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": {},
              "event_fields": []} for name in ("flights", "quota_status", "capacity_status", "reserve_slot")]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2",
                  "servers": [{"id": "ops", "argv": ["ops"]}], "tools": tools,
                  "provenance": {f"{item['name']}:ops": {"ancestors": []} for item in tools}},
        "court": {"decision_rule": "threshold", "parameters": {}}, "advisor": None, "scorer": None,
        "element": "acceptance.reassessment"})


def _state(*labels, legacy=True, **overrides):
    """Learned habits as an earlier policy recorded them: without the fields later policies read."""
    configuration = _configuration()
    state = empty_state()
    state["window"] = 12
    ids = {}
    for label in labels:
        steps = QUOTA if label == "q" else CAPACITY
        birth = make_birth(configuration.parameters, f"con_{label}", 1, condition=CONDITION,
                           applicability=explanation_of({}), steps=steps, binding=BINDINGS[label],
                           template="external_error: route_kind {route_kind}",
                           source_episodes=[{"qid": f"q_{label}", "steps": steps}], basis_qids=[f"q_{label}"],
                           energy=1.0, trust=0.6, state_name="active")
        habit_id = birth["habit"]["id"]
        metadata = {**birth["metadata"], "pending": [{"event_id": "e", "run_id": "r", "trigger_id": "t",
                                                      "signal": 1.0, "window": 11, "why": None,
                                                      "provisional": False}],
                    **overrides.get(label, {})}
        if legacy:
            for name in LEGACY:
                metadata.pop(name, None)
        state["frozen"][habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        state["habits"][habit_id] = metadata
        ids[label] = habit_id
    return configuration, state, ids


def _reassess(configuration, state, verdicts):
    draft = {**empty_draft("con_reassess", "reassess", {"ok": True, "problems": []}), "conflict_advice": {},
             "arbitration": {}, "reassessment": {"schema_version": "synapse.memory.reassessment/v1", "habits": [
                 {"habit_id": habit_id, "state": state["habits"][habit_id]["state"], "verified": verified,
                  "required": 3, "episodes": [], "contracts": {"quota_status": "c" * 64}, "contracts_changed": [],
                  "verdict": "basis_holds" if verified >= 3 else "basis_no_longer_verified"}
                 for habit_id, verified in verdicts.items()]}}
    return decide(state, draft, configuration, {habit_id: {"admitted": True} for habit_id in state["habits"]})


def test_recorded_metadata_is_completed_neutrally_and_the_completion_reported():
    configuration, state, ids = _state("q")
    decision = _reassess(configuration, state, {ids["q"]: 3})
    metadata = decision["habits"][ids["q"]]
    assert {name: metadata[name] for name in LEGACY} == {"yields_to": [], "compared": [], "tail": []}
    assert decision["reassessment"]["fields_completed"] == [{"habit_id": ids["q"], "fields": sorted(LEGACY)}]
    # Nothing observed: trust, pending evidence and counters are as recorded.
    for name in ("trust", "pending", "fires_in_state", "signals_in_state", "idle_windows", "state"):
        assert metadata[name] == state["habits"][ids["q"]][name]
    assert decision["sections"]["transitions"] == [] and decision["sections"]["trust_decisions"] == []


def test_current_metadata_needs_no_completion():
    configuration, state, ids = _state("q", legacy=False)
    assert _reassess(configuration, state, {ids["q"]: 3})["reassessment"]["fields_completed"] == []


def test_a_basis_no_longer_verified_archives_the_habit():
    configuration, state, ids = _state("q")
    decision = _reassess(configuration, state, {ids["q"]: 2})
    assert [(item["habit_id"], item["rule"], item["to"], item["cause"])
            for item in decision["sections"]["transitions"]] == [(ids["q"], "TR", "dormant", "changed_basis")]


def test_competitors_resolved_on_advice_alone_return_to_the_slow_path():
    # An earlier policy lifted the ban on agreed advice: both are live, the loser on probation, no winner recorded.
    configuration, state, ids = _state("q", "c", c={"state": "probation"})
    decision = _reassess(configuration, state, {ids["q"]: 3, ids["c"]: 3})
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 3 and conflict["advice"]["basis"] == "not_asked"
    assert decision["slow_only"] == [CONDITION] and decision["reassessment"]["slow_only_added"] == [CONDITION]


def test_a_recorded_verified_resolution_stands():
    configuration, state, ids = _state("q", "c", legacy=False, c={"state": "probation"})
    state["habits"][ids["c"]]["yields_to"] = [ids["q"]]
    decision = _reassess(configuration, state, {ids["q"]: 3, ids["c"]: 3})
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 2 and conflict["standing"] is True and decision["slow_only"] == []


def test_a_kept_habit_is_verified_under_the_current_contracts_and_a_lost_one_is_not():
    configuration, state, ids = _state("q", "c")
    decision = _reassess(configuration, state, {ids["q"]: 3, ids["c"]: 1})
    assert decision["habits"][ids["q"]]["verified_under"] == {"contracts": {"quota_status": "c" * 64}}
    assert decision["habits"][ids["c"]].get("verified_under") is None


def test_a_session_loads_a_habit_only_under_the_contracts_it_was_verified_under():
    configuration, state, ids = _state("q", "c")
    current = contracts_of(QUOTA, configuration)
    verified = {ids["q"]: {"contracts": current},
                ids["c"]: {"contracts": {**contracts_of(CAPACITY, configuration), "reserve_slot": "0" * 64}}}
    habits = []
    for label, habit_id in ids.items():
        metadata = {**state["habits"][habit_id], "verified_under": verified.get(habit_id), "priority": "medium",
                    "energy_cost": 1.0, "publication": None}
        habits.append(habit_entry(habit_id, metadata, state["frozen"][habit_id]))
    legacy = {**habits[0], "habit_id": "hab_legacy", "verified_under": None}
    factory = SimpleNamespace(configuration=configuration, admitted_now=lambda habit_id, item: True)
    session = MemorySession(factory, {"run_id": "load"}, {"id": "bnd_x", "boundary": {"habits": [*habits, legacy]}},
                            None)
    loaded = [entry.habit_id for entry in session.registry_entries()]
    assert loaded == [ids["q"]]
    assert session.unverified == sorted([{"habit_id": ids["c"], "contracts_changed": ["reserve_slot"]},
                                         {"habit_id": "hab_legacy", "contracts_changed": ["*"]}],
                                        key=lambda item: item["habit_id"])
