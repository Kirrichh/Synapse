"""A reassessment decides again what earlier rules recorded, from the record alone (second review, F1–F4).

Pure data through the court's reassessment entries and decision. Memory decided
by a court of an earlier policy keeps statuses and states the corrected rules
would not grant. A reassessment, which calls no tool, corrects them:

* a hypothesis status of an earlier check rule is decided again from the check
  the gateway recorded: a check that answered about another object no longer
  confirms; a check about the claim confirms (or refutes) under the current
  rule; a check whose record no longer resolves decides nothing; a status of
  the current rule and a provisional one are left as they are;
* a promotion an earlier policy decided is judged on the confirmed experience
  of the habit's recorded fires — reached with undecided fires, or at a fire
  no longer recorded, it returns the habit to probation (TU), where it keeps
  acting; a promotion with enough confirmed fires in enough tasks, or one the
  current policy decided, stands; the confirmed experience since birth is
  counted from the recorded fires — a lower bound when some were not kept;
* a resolution an earlier rule found in stand trials is decided again by the
  trials under the current rules: on a stand that did not cover the trigger
  the slow-only ban is restored; on a covering stand, or for a resolution
  found in compared outcomes, it stays lifted.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _court_data as data
from synapse.memory_consolidation import hypotheses
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.advice import conflict_advice
from synapse.memory_consolidation.court.automaton import reverify_promotions
from synapse.memory_consolidation.court.conflicts import trial_resolutions
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.evaluate import empty_draft
from synapse.memory_consolidation.court.hypotheses import reassess_hypotheses, reassessed
from synapse.memory_consolidation.court.reassessment import reassess_bases
from synapse.memory_consolidation.policy import POLICY_V4
from synapse.memory_consolidation.tools.journal import GatewayIntegrityError

EARLIER = "synapse.memory.court-policy/v3"
V1 = "synapse.memory.hypothesis-check/v1"


# -- hypothesis statuses ------------------------------------------------------------------------------------
def _bank():
    tools = [{"name": name, "server": "bank", "descriptor_sha256": "0" * 64, "input_schema": {"type": "object"},
              "output_schema": {"type": "object"}, "source": source, "contract": contract}
             for name, source, contract in (
                 ("source", "core:bank", {}),
                 ("check", "ledger:bank", {"verifies": {"subject": {"request": "account", "answer": "account"},
                                                        "scope": {"value": "bank"}}}))]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1", "advisor": None, "scorer": None, "element": "bank",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "bank", "argv": ["x"]}],
                  "tools": tools, "provenance": {"core:bank": {"ancestors": []}, "ledger:bank": {"ancestors": []}}}})


class _Recorded:
    """The gateway's record of each check answer, by journal sequence; a missing one no longer resolves."""

    def __init__(self, answers):
        self.answers = answers

    def recorded_outcome(self, seq, records):
        if not 0 <= seq:  # As the journal: a sequence is a number.
            raise GatewayIntegrityError("a recorded action names no final gateway record")
        if seq not in self.answers:
            raise GatewayIntegrityError("a recorded result no longer resolves to its evidence")
        return {"view": {"ok": True, "source": "ledger:bank", "payload": {"ok": True, **self.answers[seq]}}}


def _entry(configuration, asked, *, status="confirmed", rule=None, seq=1):
    record = hypotheses.declare({"aspect": "content", "subject": "A", "statement": {"balance": 20}, "scope": "bank",
                                 "source": {"tool": "source", "args": {"account": "A"}},
                                 "check": {"tool": "check", "args": {"account": asked}}}, configuration, "ev-src")
    entry = {"record": record, "claim_key": hypotheses.claim_key(record), "source_ref": "ev-src", "status": status,
             "reason": "check_agrees", "window": 4, "run_id": "run-1",
             "check_ref": None if seq is None else {"gw_seq": seq, "evidence": "ev"},
             "basis": None, **({"rule": rule} if rule is not None else {})}
    return record["id"], entry


@pytest.mark.parametrize("asked, answered, status, rule, seq, decided", [
    ("B", {"account": "B", "balance": 20}, "confirmed", V1, 1, ("provisional", "check_about_another:subject")),
    ("A", {"account": "A", "balance": 20}, "confirmed", None, 1, ("confirmed", "check_agrees")),
    ("A", {"account": "A", "balance": 99}, "refuted", V1, 1, ("refuted", "contradicted:balance")),
    ("A", {"account": "A", "balance": 20}, "confirmed", V1, 2, ("provisional", "check_record_unavailable")),
    ("A", {"account": "A", "balance": 20}, "confirmed", V1, None, ("provisional", "check_record_unavailable")),
])
def test_an_earlier_status_is_decided_again_from_its_recorded_check(asked, answered, status, rule, seq, decided):
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, asked, status=status, rule=rule, seq=seq)
    state = {"hypotheses": {hypothesis_id: entry}}
    section = reassess_hypotheses(state, configuration, _Recorded({1: answered}), [])
    assert section == [{"hypothesis": hypothesis_id, "from": {"status": status, "reason": "check_agrees", "rule": rule},
                        "to": {"status": decided[0], "reason": decided[1], "rule": hypotheses.CHECK_RULE}}]
    found = reassessed(state, section)[hypothesis_id]
    assert (found["status"], found["reason"], found["rule"]) == (*decided, hypotheses.CHECK_RULE)
    assert {key: found[key] for key in ("window", "check_ref", "run_id", "record")} == {
        key: entry[key] for key in ("window", "check_ref", "run_id", "record")}
    # The corrected status is what a later session may reuse.
    reused = hypotheses.reuse(entry["record"], found, {}, 5, configuration.parameters)
    assert reused["status"] == (decided[0] if decided[0] != "provisional" else None)


@pytest.mark.parametrize("status, rule", [("confirmed", hypotheses.CHECK_RULE), ("provisional", V1)])
def test_a_current_or_undecided_status_is_left_as_recorded(status, rule):
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "B", status=status, rule=rule)
    assert reassess_hypotheses({"hypotheses": {hypothesis_id: entry}}, configuration, _Recorded({}), []) == []


# -- promotions -----------------------------------------------------------------------------------------------
def _fire(index, confirmed, task=None, *, failed=False):
    """A recorded fire: a confirmed success, a confirmed failure, or one the environment left undecided."""
    outcome, verdict = ("failure", "failed") if failed else ("success", "confirmed") if confirmed else (
        "uncertain", "uncertain")
    return {"outcome": outcome, "task_id": task or f"task-{index}", "run_id": f"run-{index:04d}",
            "event_id": f"ev-{index:04d}", "segment_verdict": verdict, "fields": {"route_kind": "intl"}}


def _at(index):
    return {"run_id": f"run-{index:04d}", "event_id": f"ev-{index:04d}"}


def _report(index, transitions, *, policy=EARLIER, runs=()):
    return {"window": {"index": index, "sessions": [{"run_id": run} for run in runs]}, "policy": {"policy": policy},
            "transitions": transitions}


def _promoted(recent, reports, *, total=None, state_name="active"):
    config, state, ids = data.world(state_name=state_name, trust=0.9)
    habit_id = ids["q"]
    state["habits"][habit_id].update(recent=recent, exec_summary={
        "fires_total": len(recent) if total is None else total, "successes": 0, "failures": 0, "uncertain": 0})
    for report in reports:
        for move in report["transitions"]:
            move.setdefault("habit_id", habit_id)
    return config, state, habit_id


def _t1(at, policy=EARLIER):
    return _report(3, [{"from": "born", "to": "active", "rule": "T1", "basis": "earlier", "cause": "x", "at": at}],
                   policy=policy)


@pytest.mark.parametrize("recent, reports, total, verdict", [
    ([_fire(i, True, f"task-{i % 2}") for i in range(5)], lambda: [_t1(_at(4))], None, "promotion_verified"),
    ([_fire(0, True)] + [_fire(i, False) for i in range(1, 5)], lambda: [_t1(_at(4))], None,
     "promotion_not_verified"),
    ([_fire(i, True) for i in range(5)], lambda: [_t1(_at(9))], 70, "promotion_not_verified"),  # Not recorded.
    ([_fire(i, True) for i in range(5)], lambda: [_t1(_at(4), POLICY_V4)], None, None),  # Decided by this policy.
    ([_fire(i, True, "one") for i in range(5)], lambda: [_t1(_at(4))], None, "promotion_not_verified"),  # One task.
    # Confirmed fires after the promotion were no part of it.
    ([_fire(0, True)] + [_fire(i, False) for i in range(1, 5)] + [_fire(i, True) for i in range(5, 10)],
     lambda: [_t1(_at(4))], None, "promotion_not_verified"),
])
def test_an_earlier_promotion_stands_only_on_confirmed_experience(recent, reports, total, verdict):
    reports = reports()
    config, state, habit_id = _promoted(recent, reports, total=total)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    assert item["verdict"] == verdict


def test_a_probation_promotion_counts_from_the_entry_into_probation():
    # Confirmed fires 0–4 in active, a confirmed error demotes at fire 5, then one confirmed and four undecided.
    recent = [_fire(i, True) for i in range(5)] + [_fire(5, False, failed=True)] + [_fire(6, True)] + [
        _fire(i, False) for i in range(7, 11)]
    reports = [_report(2, [{"from": "active", "to": "probation", "rule": "T3", "basis": "x", "cause": "x",
                            "at": _at(5)}]),
               _report(3, [{"from": "probation", "to": "active", "rule": "T4", "basis": "x", "cause": "x",
                            "at": _at(10)}])]
    config, state, habit_id = _promoted(recent, reports)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    assert item["verdict"] == "promotion_not_verified" and item["experience"]["counted"] == 1
    later = recent + [_fire(i, True) for i in range(11, 15)]
    reports[-1]["transitions"][0]["at"] = _at(14)
    config, state, habit_id = _promoted(later, reports)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    assert item["verdict"] == "promotion_verified" and item["experience"]["counted"] == 5


def test_a_probation_promotion_needs_its_mean_and_counts_after_a_window_entry():
    # Entered probation at window 2 (a conflict, not at a fire); fires 0–4 ran by then, fires 5–9 after.
    recent = [_fire(i, True) for i in range(5)] + [_fire(i, True, failed=failed) for i, failed in
                                                    zip(range(5, 10), (True, True, True, False, False))]
    reports = [_report(2, [{"from": "active", "to": "probation", "rule": "TC", "basis": "x", "cause": "conflict"}],
                       runs=[f"run-{i:04d}" for i in range(5)]),
               _report(3, [{"from": "probation", "to": "active", "rule": "T4", "basis": "x", "cause": "x",
                            "at": _at(9)}], runs=[f"run-{i:04d}" for i in range(5, 10)])]
    config, state, habit_id = _promoted(recent, reports)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    # Five confirmed fires in probation, but three were failures: the mean is below what a promotion needs.
    assert item["verdict"] == "promotion_not_verified" and item["experience"]["counted"] == 5
    assert item["experience"]["mean"] < config.parameters["t4_signal"]


def test_a_window_promotion_counts_runs_ended_by_its_window():
    recent = [_fire(i, True, f"task-{i % 2}") for i in range(5)]
    reports = [_report(1, [], runs=[f"run-{i:04d}" for i in range(3)]),
               _report(2, [{"from": "born", "to": "active", "rule": "T1", "basis": "SPRT accepted H0", "cause": "x"}],
                       runs=[f"run-{i:04d}" for i in range(3, 5)]),
               _report(3, [], runs=["run-0004"])]  # The last run went on after the promotion's window.
    config, state, habit_id = _promoted(recent, reports)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    assert item["verdict"] == "promotion_not_verified" and item["experience"]["counted"] == 4


@pytest.mark.parametrize("total, counted", [(None, 3), (40, 3)])
def test_confirmed_experience_since_birth_is_counted_from_the_recorded_fires(total, counted):
    recent = [_fire(0, True, "a"), _fire(1, False), _fire(2, True, "b"), _fire(3, True, "b")]
    config, state, habit_id = _promoted(recent, [], total=total, state_name="born")
    item, = reverify_promotions(state, [], config.parameters, POLICY_V4)
    assert (item["counted_since_birth"], item["counted_tasks_since_birth"], item["complete"]) == (
        counted, ["a", "b"], total is None)


def _reassess(config, state, draft_extra, habits=()):
    draft = {**empty_draft("con_reassess", "reassess", {"ok": True, "problems": []}), "conflict_advice": {},
             "arbitration": {}, **draft_extra}
    draft.setdefault("reassessment", {"schema_version": "synapse.memory.reassessment/v2", "habits": [
        {"habit_id": habit_id, "state": state["habits"][habit_id]["state"], "verified": 3, "required": 3,
         "episodes": [], "contracts": {}, "contracts_changed": [], "verdict": "basis_holds"} for habit_id in habits]})
    return decide(state, draft, config, {habit_id: {"admitted": True} for habit_id in state["habits"]})


def test_the_decision_returns_an_unverified_promotion_to_probation_and_keeps_a_verified_one():
    recent = [_fire(0, True)] + [_fire(i, False) for i in range(1, 5)]
    reports = [_t1(_at(4))]
    config, state, habit_id = _promoted(recent, reports)
    promotions = reverify_promotions(state, reports, config.parameters, POLICY_V4)
    decision = _reassess(config, state, {}, [habit_id])
    assert decision["habits"][habit_id]["state"] == "active"  # Without the promotion's judgment nothing moves.
    draft = {"reassessment": {"schema_version": "synapse.memory.reassessment/v2", "promotions": promotions,
                              "habits": [{"habit_id": habit_id, "state": "active", "verified": 3, "required": 3,
                                          "episodes": [], "contracts": {}, "contracts_changed": [],
                                          "verdict": "basis_holds"}]}}
    decision = _reassess(config, state, draft)
    habit = decision["habits"][habit_id]
    assert [(item["rule"], item["from"], item["to"], item["cause"]) for item in decision["sections"]["transitions"]] \
        == [("TU", "active", "probation", "promotion_not_verified")]
    assert (habit["state"], habit["counted_since_birth"], habit["counted_tasks_since_birth"]) == (
        "probation", 1, ["task-0"])


def test_the_decision_records_the_statuses_decided_again():
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "B", rule=V1)
    config, state, _ = data.world()
    state["hypotheses"] = {hypothesis_id: entry}
    section = reassess_hypotheses(state, configuration, _Recorded({1: {"account": "B", "balance": 20}}), [])
    decision = _reassess(config, state, {"reassessment": {"schema_version": "synapse.memory.reassessment/v2",
                                                          "habits": [], "hypotheses": section}})
    assert decision["knowledge"]["hypotheses"] == reassessed(state, section)
    assert decision["knowledge"]["hypotheses"][hypothesis_id]["status"] == "provisional"


def test_the_reassessment_judges_every_earlier_decision_from_the_record():
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "B", rule=V1)
    recent = [_fire(0, True)] + [_fire(i, False) for i in range(1, 5)]
    reports = [_t1(_at(4))]
    _, state, habit_id = _promoted(recent, reports)
    loser = "hab_loser"
    reports.append(_resolved(habit_id, loser, by_trial=True))
    state["hypotheses"] = {hypothesis_id: entry}
    found = reassess_bases(state, configuration, _Recorded({1: {"account": "B", "balance": 20}}), [], [],
                           lambda entry: None, reports)
    assert found["schema_version"] == "synapse.memory.reassessment/v2"
    assert [item["to"]["status"] for item in found["hypotheses"]] == ["provisional"]
    assert [(item["habit_id"], item["verdict"]) for item in found["promotions"]] == [
        (habit_id, "promotion_not_verified")]
    assert found["trial_resolutions"] == ["|".join(sorted((habit_id, loser)))]


# -- conflicts ------------------------------------------------------------------------------------------------
def _resolved(winner, loser, *, by_trial):
    """An earlier report's step-2 resolution of the pair, found in trials or in compared outcomes."""
    advice = {"comparison": {"winner": None if by_trial else winner}, "trial": {"winner": winner if by_trial else None}}
    return {**_report(4, []), "conflicts": [{"habits": {"A": winner, "B": loser}, "step": 2, "advice": advice,
                                             "resolution": f"{winner}_stays_{loser}_probation"}]}


@pytest.mark.parametrize("ladder, retried", [
    ([("trial", 2)], True),
    ([("trial", 2), ("standing", 2)], True),  # A standing resolution keeps the basis it was found on.
    ([("trial", 2), ("comparison", 2)], False),
    ([("trial", 2), ("unresolved", 3)], False),  # Unresolved since: nothing stands to be decided again.
])
def test_a_resolution_found_in_trials_is_named_for_the_trials_to_decide_again(ladder, retried):
    reports = []
    for basis, step in ladder:
        advice = {"comparison": {"winner": "hab_a" if basis == "comparison" else None},
                  "trial": {"winner": "hab_a" if basis == "trial" else None}}
        reports.append({"conflicts": [{"habits": {"A": "hab_a", "B": "hab_b"}, "step": step, "advice": advice,
                                       **({"standing": True} if basis == "standing" else {})}]})
    assert trial_resolutions(reports) == (["hab_a|hab_b"] if retried else [])


@pytest.mark.parametrize("fields, by_trial, restored", [
    ({"route_kind": ["intl"], "weather": ["clear"]}, True, True),  # Found in trials that do not cover the trigger.
    ({"route_kind": ["intl"]}, True, False),  # Found in trials that cover it: decided again, it stands.
    ({"route_kind": ["intl"], "weather": ["clear"]}, False, False),  # Found in compared outcomes: it stands.
])
def test_a_ban_lifted_on_trials_that_did_not_cover_the_trigger_is_restored(fields, by_trial, restored):
    config, state, ids = data.world(labels=("q", "c"), trust=0.7, state_name="active")
    winner, loser = ids["q"], ids["c"]
    state["slow_only"] = []  # An earlier rule lifted the ban and the loser yields.
    state["habits"][loser]["yields_to"] = [winner]
    trials = [{"id": f"trl-{name}", "stand": "stand-1", "scope": {"fields": fields}, "situation": name,
               "decided": True, "arms": {winner: {"outcome": "success"}, loser: {"outcome": "failure"}}}
              for name in "123"]
    advice = conflict_advice(None, state, config.parameters, [], [], [], trials)
    reassessment = {"schema_version": "synapse.memory.reassessment/v2",
                    "trial_resolutions": trial_resolutions([_resolved(winner, loser, by_trial=by_trial)]),
                    "habits": [{"habit_id": habit_id, "state": "active", "verified": 3, "required": 3, "episodes": [],
                                "contracts": {}, "contracts_changed": [], "verdict": "basis_holds"}
                               for habit_id in (winner, loser)]}
    decision = _reassess(config, state, {"conflict_advice": advice, "reassessment": reassessment})
    assert (decision["slow_only"] == [data.CONDITION]) is restored
    assert (decision["reassessment"]["slow_only_added"] == [data.CONDITION]) is restored
