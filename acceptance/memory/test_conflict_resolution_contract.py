"""How learned competitors are resolved, and when they may act (review R5).

Pure data through the court's comparison, its decision entry and the
runtime's rivalry rule:

* the verdict of step 2 is the comparison of decided outcomes in one
  situation; uncertain episodes, copies, a repeated episode, results of other
  situations and a situation where one procedure both succeeded and failed
  are no pairs, and equal results name no winner;
* a model's agreed answer is a proposal: it never lifts a slow-only ban;
  a verified winner does, and the loser yields to it until recorded outcomes
  contradict the result;
* rivals whose triggers overlap only partly become competitors once the
  runtime found both applicable to one event; they keep their own triggers;
* at run time a rival that yields gives way only to an applicable winner, and
  an unresolved pair is held back whole before any effect; declared habits are
  never held back by learned rivalry.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.births import make_birth
from synapse.memory_consolidation.court.comparison import compare
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.projection import empty_state
from synapse.memory_consolidation.learning.applicability import explanation_of
from synapse.memory_consolidation.session import rivalry
from synapse.memory_points import LearnedHabitEntry
from synapse.runtime.habit_engine import HabitEngine

INTL = {"route_kind": "intl"}
DOM = {"route_kind": "dom"}


def _outcome(result, fields=INTL, *, ref, verdict=None, copy=False):
    verdict = verdict or {"success": "confirmed", "failure": "failed"}.get(result)
    return {"outcome": result, "segment_verdict": verdict, "fields": dict(fields), "ref": ref, "copy": copy}


def _situations(left, right, *situations):
    """Histories of ``left`` and ``right`` with one pair of results per situation."""
    histories = {left: [], right: []}
    for index, (a, b) in enumerate(situations):
        fields = {"route_kind": "intl", "slot": index}
        histories[left].append(_outcome(a, fields, ref=f"a{index}"))
        histories[right].append(_outcome(b, fields, ref=f"b{index}"))
    return histories


# -- the comparison ------------------------------------------------------------------------
def test_a_procedure_better_in_enough_comparable_situations_wins():
    result = compare("q", "c", _situations("q", "c", ("failure", "success"), ("failure", "success")), 2)
    assert (result["winner"], result["reason"], result["pairs"]) == ("c", "better_in_comparable_situations", 2)


@pytest.mark.parametrize("pairs, winner", [(1, None), (2, "c"), (3, "c")])
def test_the_declared_minimum_of_comparable_situations(pairs, winner):
    histories = _situations("q", "c", *[("failure", "success")] * pairs)
    assert compare("q", "c", histories, 2)["winner"] == winner


def test_undecided_episodes_copies_and_repeats_are_no_pairs():
    histories = {"q": [_outcome("failure", ref="q1", verdict="unclear"), _outcome("uncertain", ref="q2"),
                       _outcome("failure", ref="q3", copy=True)],
                 "c": [_outcome("success", ref="c1"), _outcome("success", ref="c1"), _outcome("success", ref="c1")]}
    result = compare("q", "c", histories, 1)
    assert result["winner"] is None and result["pairs"] == 0
    assert result["excluded"] == {"q": {"undecided": 3, "repeated": 0}, "c": {"undecided": 0, "repeated": 2}}


def test_results_of_other_situations_never_pair():
    histories = {"q": [_outcome("failure", DOM, ref="q1")], "c": [_outcome("success", INTL, ref="c1")]}
    result = compare("q", "c", histories, 1)
    assert (result["winner"], result["reason"], result["pairs"]) == (None, "too_few_comparable_situations", 0)


def test_a_situation_where_a_procedure_both_succeeded_and_failed_is_set_aside():
    histories = {"q": [_outcome("failure", ref="q1"), _outcome("success", ref="q2")],
                 "c": [_outcome("success", ref="c1")]}
    result = compare("q", "c", histories, 1)
    assert result["winner"] is None and result["mixed_situations"] == 1 and result["pairs"] == 0


@pytest.mark.parametrize("situations, reason", [
    ((("success", "success"), ("failure", "failure")), "no_difference_established"),
    ((("success", "failure"), ("failure", "success")), "contradicting_situations")])
def test_equal_or_contradicting_results_name_no_winner(situations, reason):
    result = compare("q", "c", _situations("q", "c", *situations), 1)
    assert (result["winner"], result["reason"]) == (None, reason)


# -- the ladder through the court's decision entry ------------------------------------------
QUOTA = [{"step": "call", "tool": "quota_status", "result_class": "ok"},
         {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
CAPACITY = [{"step": "call", "tool": "capacity_status", "result_class": "ok"},
            {"step": "call", "tool": "reserve_slot", "result_class": "ok"},
            {"step": "call", "tool": "$same", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
ROUTE = {"route": {"failed_arg": "route"}}
BINDINGS = {"q": [{"step": 0, "args": ROUTE}, {"step": 1, "failed_action": True}],
            "c": [{"step": 0, "args": ROUTE}, {"step": 1, "args": ROUTE}, {"step": 2, "failed_action": True}]}


def _condition(*when):
    return {"event_types": ["external_error"], "context": ["search"], "when": list(when), "not_when": []}


SHARED = _condition({"field": "route_kind", "op": "==", "value": "intl"})


def _configuration():
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": {},
              "event_fields": []} for name in ("flights", "quota_status", "capacity_status", "reserve_slot")]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2",
                  "servers": [{"id": "ops", "argv": ["ops"]}], "tools": tools,
                  "provenance": {f"{item['name']}:ops": {"ancestors": []} for item in tools}},
        "court": {"decision_rule": "threshold",
                  "parameters": {"min_evidence": 100, "conflict_gap": 0.30, "counterfactual_min_pairs": 1}},
        "advisor": None, "scorer": None, "element": "acceptance.conflicts"})


def _world(conditions=None, **overrides):
    """A state at window 10 with the quota (``q``) and capacity (``c``) recoveries, equal trust."""
    configuration = _configuration()
    state = empty_state()
    state["window"] = 10
    ids = {}
    for label, steps in (("q", QUOTA), ("c", CAPACITY)):
        condition = (conditions or {}).get(label, SHARED)
        birth = make_birth(configuration.parameters, f"con_{label}", 1, condition=condition,
                           applicability=explanation_of({}), steps=steps, binding=BINDINGS[label],
                           template="external_error: route_kind {route_kind}",
                           source_episodes=[{"qid": f"q_{label}", "steps": steps}], basis_qids=[f"q_{label}"],
                           energy=1.0, trust=0.5, state_name="active")
        habit_id = birth["habit"]["id"]
        state["frozen"][habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        state["habits"][habit_id] = {**birth["metadata"], **overrides.get(label, {})}
        ids[label] = habit_id
    return configuration, state, ids


def _decide(configuration, state, advice=None, suppressed=()):
    draft = {"consolidation_id": "con_conflicts", "mode": "full", "fires": [], "declared_fires": [], "reactions": [],
             "near_misses": [], "misses": [], "requests": [], "cases": [], "replay": {},
             "conflict_advice": advice or {}, "arbitration": {}, "suppressed": list(suppressed)}
    return decide(state, draft, configuration, {habit_id: {"admitted": True} for habit_id in state["habits"]})


def _advice(ids, *, winner, answer="yes", attributed=None):
    left, right = sorted(ids.values())
    comparison = {"basis": "recorded_outcomes_same_situation", "winner": winner,
                  "reason": "better_in_comparable_situations" if winner else "no_difference_established",
                  "pairs": 1, "better": {left: 0, right: 0}, "mixed_situations": 0, "excluded": {}}
    return {f"{left}|{right}": {"basis": "proposal", "asked": True, "calls": [answer, answer], "answer": answer,
                                "agreed": True, "comparison": comparison, "attributed": attributed or {}}}


def test_an_agreeing_model_without_a_verified_winner_keeps_the_trigger_slow_only():
    configuration, state, ids = _world()
    state["slow_only"] = [SHARED]
    decision = _decide(configuration, state, _advice(ids, winner=None))
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 3 and conflict["advice"]["agreed"] is True
    assert decision["slow_only"] == [SHARED]
    assert all(decision["habits"][habit_id]["yields_to"] == [] for habit_id in ids.values())


def test_a_verified_winner_lifts_the_ban_and_the_loser_yields_to_it():
    configuration, state, ids = _world()
    state["slow_only"] = [SHARED]
    # The model proposes the opposite: the verdict is the comparison's.
    decision = _decide(configuration, state, _advice(ids, winner=ids["c"], answer="no"))
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 2 and conflict["slow_only_lifted"] is True
    assert conflict["resolution"] == f"{ids['c']}_stays_{ids['q']}_probation"
    assert decision["slow_only"] == []
    assert decision["habits"][ids["q"]]["yields_to"] == [ids["c"]] and decision["habits"][ids["c"]]["yields_to"] == []
    assert (ids["q"], "TC", "probation") in {(item["habit_id"], item["rule"], item["to"])
                                             for item in decision["sections"]["transitions"]}


def test_a_verified_result_stands_until_recorded_outcomes_contradict_it():
    configuration, state, ids = _world(q={"yields_to": []}, c={"yields_to": []})
    state["habits"][ids["q"]]["yields_to"] = [ids["c"]]
    standing = _decide(configuration, state, _advice(ids, winner=None))
    conflict, = standing["sections"]["conflicts"]
    assert conflict["step"] == 2 and conflict["standing"] is True and standing["slow_only"] == []

    advice = _advice(ids, winner=None)
    next(iter(advice.values()))["comparison"]["reason"] = "contradicting_situations"
    contradicted = _decide(configuration, state, advice)
    conflict, = contradicted["sections"]["conflicts"]
    assert conflict["step"] == 3 and contradicted["habits"][ids["q"]]["yields_to"] == []
    assert contradicted["slow_only"] == [SHARED]


def test_attributed_slow_path_outcomes_accumulate_once_per_episode():
    configuration, state, ids = _world()
    state["habits"][ids["c"]]["compared"] = [_outcome("success", ref="run-1|ev-1")]
    attributed = {ids["c"]: [_outcome("success", ref="run-1|ev-1"), _outcome("success", ref="run-2|ev-1")],
                  ids["q"]: [_outcome("failure", ref="run-3|ev-1")]}
    decision = _decide(configuration, state, _advice(ids, winner=None, attributed=attributed))
    assert [item["ref"] for item in decision["habits"][ids["c"]]["compared"]] == ["run-1|ev-1", "run-2|ev-1"]
    assert [item["ref"] for item in decision["habits"][ids["q"]]["compared"]] == ["run-3|ev-1"]


def test_partly_overlapping_rivals_compete_once_the_runtime_met_them_on_one_event():
    wide = _condition({"field": "route_kind", "op": "in", "value": ["intl", "dom"]})
    configuration, state, ids = _world({"c": wide})
    assert _decide(configuration, state)["sections"]["conflicts"] == []  # Other triggers: not yet met.

    met = [{"run_id": "run-1", "habit_id": ids["q"], "reason": "unresolved_conflict", "competitor": ids["c"],
            "trigger_event_id": "ev-1"}]
    decision = _decide(configuration, state, suppressed=met)
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 3 and conflict["resolution"] == "held_back_on_the_overlap_until_compared"
    assert conflict["trigger"] is None and set(conflict["overlap"]) == {"A", "B"}
    # Each keeps its own trigger outside the overlap: nothing becomes slow-only, nobody goes on probation.
    assert decision["slow_only"] == [] and decision["sections"]["transitions"] == []

    resolved = _decide(configuration, state, _advice(ids, winner=ids["c"]), suppressed=met)
    conflict, = resolved["sections"]["conflicts"]
    assert conflict["resolution"] == f"{ids['q']}_yields_to_{ids['c']}"
    assert resolved["habits"][ids["q"]]["yields_to"] == [ids["c"]] and resolved["sections"]["transitions"] == []


# -- the runtime's rivalry rule ---------------------------------------------------------------
PARAMETERS = {"action_same": 0.8, "conflict_gap": 0.30}


def _entry(habit_id, trust):
    return LearnedHabitEntry(habit_id=habit_id, trigger_id=f"trg_{habit_id}", event_types=("external_error",),
                             context=(), when=(), not_when=(), priority="medium", context_trust=trust, energy_cost=1.0)


def _boundary(outcomes, yields=None):
    patterns = {"q": QUOTA, "c": CAPACITY, "k": CAPACITY}
    return {habit_id: {"habit": {"expected_outcome": outcome, "action_pattern": patterns[habit_id]},
                       "yields_to": (yields or {}).get(habit_id, [])} for habit_id, outcome in outcomes.items()}


def _rivalry(trust, outcomes, yields=None):
    entries = tuple(_entry(habit_id, value) for habit_id, value in trust.items())
    return {entry.habit_id: (entry.yields_to, entry.unresolved_with)
            for entry in rivalry(entries, _boundary(outcomes, yields), PARAMETERS)}


def test_rivals_without_a_verified_resolution_are_unresolved_both_ways():
    found = _rivalry({"q": 0.5, "c": 0.7}, {"q": "found", "c": "found"})
    assert found == {"q": ((), ("c",)), "c": ((), ("q",))}


def test_a_verified_loser_and_a_junior_by_an_established_gap_yield():
    assert _rivalry({"q": 0.9, "c": 0.5}, {"q": "found", "c": "found"}, {"q": ["c"]}) == {
        "q": (("c",), ()), "c": ((), ())}
    assert _rivalry({"q": 0.8, "c": 0.5}, {"q": "found", "c": "found"}) == {"q": ((), ()), "c": (("q",), ())}


def test_same_actions_or_other_expected_outcomes_are_no_rivals():
    assert _rivalry({"c": 0.5, "k": 0.5}, {"c": "found", "k": "found"}) == {"c": ((), ()), "k": ((), ())}
    assert _rivalry({"q": 0.5, "c": 0.5}, {"q": "found", "c": "booked"}) == {"q": ((), ()), "c": ((), ())}


def _candidate(habit_id, *, layer=2, yields_to=(), unresolved_with=()):
    return {"habit": SimpleNamespace(habit_id=habit_id, layer=layer, yields_to=tuple(yields_to),
                                     unresolved_with=tuple(unresolved_with), slow_only=False)}


def test_an_unresolved_pair_applicable_to_one_event_is_held_back_whole():
    held = HabitEngine.held_rivals([_candidate("q", unresolved_with=["c"]), _candidate("c", unresolved_with=["q"])])
    assert held == {"q": ("unresolved_conflict", "c"), "c": ("unresolved_conflict", "q")}


def test_a_rival_acts_alone_where_the_other_does_not_apply():
    assert HabitEngine.held_rivals([_candidate("q", unresolved_with=["c"])]) == {}
    assert HabitEngine.held_rivals([_candidate("q", yields_to=["c"])]) == {}


def test_a_loser_gives_way_to_an_applicable_winner_and_declared_habits_are_untouched():
    held = HabitEngine.held_rivals([_candidate("d", layer=1), _candidate("q", yields_to=["c"]), _candidate("c")])
    assert held == {"q": ("yields_to_rival", "c")}
