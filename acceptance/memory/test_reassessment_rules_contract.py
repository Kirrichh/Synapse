"""A reassessment decides again what earlier rules recorded, from the record alone (second review, F1–F4).

Pure data through the court's reassessment entries and decision. Memory decided
by a court of an earlier policy keeps statuses and states the corrected rules
would not grant. A reassessment, which calls no tool, corrects them:

* every decided hypothesis status is decided again from the check the gateway
  recorded, under the configuration adopted now — the check rule's version
  says nothing about the contract, provenance or identity rules a check rests
  on: a check that answered about another object, or outside the checker's
  scope now, or from a checker the graph now shows dependent on the source, no
  longer confirms; a check about the claim confirms (or refutes) alike under
  an unchanged configuration; a check whose record no longer resolves decides
  nothing; a provisional status is left as it is;
* a promotion an earlier policy decided is judged on the confirmed experience
  of the habit's recorded fires — reached with undecided fires, or at a fire
  no longer recorded, it returns the habit to probation (TU), where it keeps
  acting; a promotion with enough confirmed fires in enough tasks, or one the
  current policy decided, stands; the confirmed experience since birth is
  counted from the recorded fires — a lower bound when some were not kept;
* every resolution the record still holds is given the basis it was found
  on, read from the last decision of the ladder for the pair: a resolution
  found in stand trials stays only while the trials decide it under the current
  rules — on a stand that did not cover the trigger the slow-only ban is
  restored; on a covering stand, or for a resolution found in compared
  outcomes, it stays lifted.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from acceptance.memory import _court_data as data
from synapse.memory_consolidation import hypotheses
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.advice import conflict_advice
from synapse.memory_consolidation.court.automaton import reverify_promotions
from synapse.memory_consolidation.court.comparison import COMPARISON_BASIS, TRIAL_BASIS
from synapse.memory_consolidation.court.conflicts import resolution_bases
from synapse.memory_consolidation.court.decide import decide
from synapse.memory_consolidation.court.evaluate import empty_draft
from synapse.memory_consolidation.court.hypotheses import reassess_hypotheses, reassessed
from synapse.memory_consolidation.court.reassessment import reassess_bases
from synapse.memory_consolidation.court.hypotheses import declarations
from synapse.memory_consolidation.court.window import index_events
from synapse.memory_consolidation.policy import POLICY_V5
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


class _Read:
    """The sessions a reassessment reads: each run's whole recorded history, indexed as the court reads it."""

    def __init__(self, runs):
        self.runs = runs

    def facts(self, run_id):
        history = self.runs.get(run_id)
        if history is None:
            return None  # A run whose record is no longer readable.
        return SimpleNamespace(found=index_events(history, 0, len(history)), observed={}, problems=[],
                               declared=declarations(history, len(history)))


def _probed(record, seq, status="confirmed", rule=None):
    return {"type": "hypothesis_probed", "hypothesis": record["id"], "status": status, "reason": "check_agrees",
            "rule": rule, "check_basis": None, "check_ref": {"gw_seq": seq, "evidence": f"ev-{seq}"}}


def _memory(entry, histories):
    """Memory an earlier court decided: its entry, and every session consolidated to its end."""
    return {"window": 4, "hypotheses": {entry["record"]["id"]: entry}, "quanta": {},
            "knowledge": {"versions": {}, "uses": {}},
            "cursors": {run_id: {"to": len(history)} for run_id, history in histories.items()}}


@pytest.mark.parametrize("asked, answered, status, rule, readable, decided", [
    ("B", {"account": "B", "balance": 20}, "confirmed", V1, True, ("provisional", "check_about_another:subject")),
    ("A", {"account": "A", "balance": 20}, "confirmed", None, True, ("confirmed", "check_agrees")),
    ("A", {"account": "A", "balance": 99}, "refuted", V1, True, ("refuted", "contradicted:balance")),
    ("A", None, "confirmed", V1, True, ("provisional", "check_record_unavailable")),  # The answer no longer resolves.
    # A session no longer readable: the status is decided again from the answer the gateway recorded for its check.
    ("A", {"account": "A", "balance": 20}, "confirmed", V1, False, ("confirmed", "check_agrees")),
    ("A", None, "confirmed", V1, False, ("provisional", "check_record_unavailable")),
    # A status of the current rule under an unchanged configuration is decided again — and alike.
    ("A", {"account": "A", "balance": 20}, "confirmed", hypotheses.CHECK_RULE, True, ("confirmed", "check_agrees")),
])
def test_a_recorded_status_is_decided_again_from_its_recorded_check(asked, answered, status, rule, readable, decided):
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, asked, status=status, rule=rule)
    history = [{"type": "hypothesis_declared", "hypothesis": entry["record"]}, _probed(entry["record"], 1, status, rule)]
    state = _memory(entry, {"run-1": history})
    section = reassess_hypotheses(state, configuration, _Recorded({} if answered is None else {1: answered}), [],
                                  _Read({"run-1": history} if readable else {}), [])
    basis = hypotheses.check_basis(entry["record"], configuration)
    item, = section
    assert (item["hypothesis"], item["from"]["status"], item["from"]["rule"]) == (hypothesis_id, status, rule)
    assert item["to"] == {"status": decided[0], "reason": decided[1], "rule": hypotheses.CHECK_RULE,
                          "check_basis": basis, "at": 1}
    found = reassessed(section)[hypothesis_id]
    assert (found["status"], found["reason"], found["rule"], found["check_basis"]) == (
        *decided, hypotheses.CHECK_RULE, basis)
    assert {key: found[key] for key in ("run_id", "record")} == {key: entry[key] for key in ("run_id", "record")}
    # The status decided again is what a later session may reuse.
    reused = hypotheses.reuse(entry["record"], found, {}, 5, configuration.parameters, basis)
    assert reused["status"] == (decided[0] if decided[0] != "provisional" else None)


def test_an_undecided_status_is_left_as_recorded():
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "B", status="provisional", rule=V1)
    assert reassess_hypotheses(_memory(entry, {}), configuration, _Recorded({}), [], _Read({}), []) == []


@pytest.mark.parametrize("names", [("z-first", "a-second"), ("a-first", "z-second")])
def test_the_latest_recorded_check_decides_whatever_its_session_is_named(names):
    # Two sessions checked the claim: the ledger agreed at sequence 3 and contradicted it at sequence 7.
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "A", rule=hypotheses.CHECK_RULE, seq=3)
    first, second = ([{"type": "hypothesis_declared", "hypothesis": entry["record"]}, _probed(entry["record"], seq)]
                     for seq in (3, 7))
    state = _memory(entry, {names[0]: first, names[1]: second})
    section = reassess_hypotheses(state, configuration, _Recorded({3: {"account": "A", "balance": 20},
                                                                  7: {"account": "A", "balance": 99}}),
                                  [], _Read({names[0]: first, names[1]: second}), [])
    found = reassessed(section)[hypothesis_id]
    assert (found["status"], found["at"], found["run_id"]) == ("refuted", 7, names[1])


def test_a_check_after_a_consolidation_names_the_hypothesis_its_session_declared_before():
    # One session: declared and checked (agreed at 3), consolidated, then checked again (contradicted at 9).
    configuration = _bank()
    hypothesis_id, entry = _entry(configuration, "A", rule=hypotheses.CHECK_RULE, seq=3)
    history = [{"type": "hypothesis_declared", "hypothesis": entry["record"]}, _probed(entry["record"], 3),
               {"type": "memory_consolidated"}, _probed(entry["record"], 9)]
    section = reassess_hypotheses(_memory(entry, {"run-1": history}), configuration,
                                  _Recorded({3: {"account": "A", "balance": 20}, 9: {"account": "A", "balance": 99}}),
                                  [], _Read({"run-1": history}), [])
    assert reassessed(section)[hypothesis_id]["status"] == "refuted"


@pytest.mark.parametrize("change, reason", [("scope", "check_about_another:scope"),
                                            ("provenance", "checking_source_dependent")])
def test_a_confirmation_decided_on_another_check_basis_does_not_survive_the_new_one(change, reason):
    # Confirmed under the configuration of the time; then the checker's scope or its provenance changes.
    before = _bank()
    hypothesis_id, entry = _entry(before, "A", rule=hypotheses.CHECK_RULE)
    entry["check_basis"] = hypotheses.check_basis(entry["record"], before)
    raw = copy.deepcopy(before.raw)
    if change == "scope":
        raw["tools"]["tools"][1]["contract"]["verifies"]["scope"] = {"value": "another-bank"}
    else:
        raw["tools"]["provenance"]["ledger:bank"]["ancestors"] = ["core:bank"]
    after = parse_memory_configuration(raw)
    basis = hypotheses.check_basis(entry["record"], after)
    # Not reused on the record's word before the reassessment decides it again ...
    assert hypotheses.reuse(entry["record"], entry, {}, 5, after.parameters, basis)["reason"] == "check_basis_changed"
    # ... and decided again from the recorded answer under the new contract and graph.
    history = [{"type": "hypothesis_declared", "hypothesis": entry["record"]}, _probed(entry["record"], 1)]
    section = reassess_hypotheses(_memory(entry, {"run-1": history}), after,
                                  _Recorded({1: {"account": "A", "balance": 20}}), [], _Read({"run-1": history}), [])
    found = reassessed(section)[hypothesis_id]
    assert (found["status"], found["reason"], found["check_basis"]) == ("provisional", reason, basis)
    assert hypotheses.reuse(entry["record"], found, {}, 5, after.parameters, basis)["status"] is None


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
    ([_fire(i, True) for i in range(5)], lambda: [_t1(_at(4), POLICY_V5)], None, None),  # Decided by this policy.
    ([_fire(i, True, "one") for i in range(5)], lambda: [_t1(_at(4))], None, "promotion_not_verified"),  # One task.
    # Confirmed fires after the promotion were no part of it.
    ([_fire(0, True)] + [_fire(i, False) for i in range(1, 5)] + [_fire(i, True) for i in range(5, 10)],
     lambda: [_t1(_at(4))], None, "promotion_not_verified"),
])
def test_an_earlier_promotion_stands_only_on_confirmed_experience(recent, reports, total, verdict):
    reports = reports()
    config, state, habit_id = _promoted(recent, reports, total=total)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V5)
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
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V5)
    assert item["verdict"] == "promotion_not_verified" and item["experience"]["counted"] == 1
    later = recent + [_fire(i, True) for i in range(11, 15)]
    reports[-1]["transitions"][0]["at"] = _at(14)
    config, state, habit_id = _promoted(later, reports)
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V5)
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
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V5)
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
    item, = reverify_promotions(state, reports, config.parameters, POLICY_V5)
    assert item["verdict"] == "promotion_not_verified" and item["experience"]["counted"] == 4


@pytest.mark.parametrize("total, counted", [(None, 3), (40, 3)])
def test_confirmed_experience_since_birth_is_counted_from_the_recorded_fires(total, counted):
    recent = [_fire(0, True, "a"), _fire(1, False), _fire(2, True, "b"), _fire(3, True, "b")]
    config, state, habit_id = _promoted(recent, [], total=total, state_name="born")
    item, = reverify_promotions(state, [], config.parameters, POLICY_V5)
    assert (item["counted_since_birth"], item["counted_tasks_since_birth"], item["complete"]) == (
        counted, ["a", "b"], total is None)


def _reassess(config, state, draft_extra, habits=()):
    draft = {**empty_draft("con_reassess", "reassess", {"ok": True, "problems": []}), "conflict_advice": {},
             "arbitration": {}, **draft_extra}
    draft.setdefault("reassessment", {"schema_version": "synapse.memory.reassessment/v3", "habits": [
        {"habit_id": habit_id, "state": state["habits"][habit_id]["state"], "verified": 3, "required": 3,
         "episodes": [], "contracts": {}, "contracts_changed": [], "verdict": "basis_holds"} for habit_id in habits]})
    return decide(state, draft, config, {habit_id: {"admitted": True} for habit_id in state["habits"]})


def test_the_decision_returns_an_unverified_promotion_to_probation_and_keeps_a_verified_one():
    recent = [_fire(0, True)] + [_fire(i, False) for i in range(1, 5)]
    reports = [_t1(_at(4))]
    config, state, habit_id = _promoted(recent, reports)
    promotions = reverify_promotions(state, reports, config.parameters, POLICY_V5)
    decision = _reassess(config, state, {}, [habit_id])
    assert decision["habits"][habit_id]["state"] == "active"  # Without the promotion's judgment nothing moves.
    draft = {"reassessment": {"schema_version": "synapse.memory.reassessment/v3", "promotions": promotions,
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
    history = [{"type": "hypothesis_declared", "hypothesis": entry["record"]}, _probed(entry["record"], 1)]
    section = reassess_hypotheses({**state, "cursors": {"run-1": {"to": 2}}}, configuration,
                                  _Recorded({1: {"account": "B", "balance": 20}}), [], _Read({"run-1": history}), [])
    decision = _reassess(config, state, {"reassessment": {"schema_version": "synapse.memory.reassessment/v3",
                                                          "habits": [], "hypotheses": section}})
    assert decision["hypotheses"]["updates"] == reassessed(section)
    assert decision["hypotheses"]["updates"][hypothesis_id]["status"] == "provisional"


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
    assert found["schema_version"] == "synapse.memory.reassessment/v3"
    # No session of the memory is readable here: the confirmation is decided again from the answer the gateway
    # recorded for its check, under the rule now in force — a check about another subject.
    assert [(item["to"]["status"], item["to"]["reason"]) for item in found["hypotheses"]] == [
        ("provisional", "check_about_another:subject")]
    assert [(item["habit_id"], item["verdict"]) for item in found["promotions"]] == [
        (habit_id, "promotion_not_verified")]
    assert found["resolutions"] == [{"winner": habit_id, "loser": loser, "basis": TRIAL_BASIS}]


# -- conflicts ------------------------------------------------------------------------------------------------
def _resolved(winner, loser, *, by_trial):
    """An earlier report's step-2 resolution of the pair, found in trials or in compared outcomes."""
    advice = {"comparison": {"winner": None if by_trial else winner}, "trial": {"winner": winner if by_trial else None}}
    return {**_report(4, []), "conflicts": [{"habits": {"A": winner, "B": loser}, "step": 2, "advice": advice,
                                             "resolution": f"{winner}_stays_{loser}_probation"}]}


@pytest.mark.parametrize("ladder, basis", [
    ([("trial", 2)], TRIAL_BASIS),
    ([("trial", 2), ("standing", 2)], TRIAL_BASIS),  # A standing resolution keeps the basis it was found on.
    ([("trial", 2), ("comparison", 2)], COMPARISON_BASIS),
    ([("comparison", 2), ("trial", 2)], TRIAL_BASIS),
    ([("both", 2)], COMPARISON_BASIS),  # The ladder took the compared outcomes first.
    ([("trial", 2), ("unresolved", 3)], None),  # Unresolved since: nothing stands.
])
def test_a_resolution_is_given_the_basis_its_last_decision_found_it_on(ladder, basis):
    reports = []
    for kind, step in ladder:
        advice = {"comparison": {"winner": "hab_a" if kind in ("comparison", "both") else None},
                  "trial": {"winner": "hab_a" if kind in ("trial", "both") else None}}
        reports.append({"conflicts": [{"habits": {"A": "hab_a", "B": "hab_b"}, "step": step, "advice": advice,
                                       **({"standing": True} if kind == "standing" else {})}]})
    assert resolution_bases(reports) == ([] if basis is None else [{"winner": "hab_a", "loser": "hab_b",
                                                                      "basis": basis}])


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
    reassessment = {"schema_version": "synapse.memory.reassessment/v3",
                    "resolutions": resolution_bases([_resolved(winner, loser, by_trial=by_trial)]),
                    "habits": [{"habit_id": habit_id, "state": "active", "verified": 3, "required": 3, "episodes": [],
                                "contracts": {}, "contracts_changed": [], "verdict": "basis_holds"}
                               for habit_id in (winner, loser)]}
    decision = _reassess(config, state, {"conflict_advice": advice, "reassessment": reassessment})
    assert (decision["slow_only"] == [data.CONDITION]) is restored
    assert (decision["reassessment"]["slow_only_added"] == [data.CONDITION]) is restored
    assert decision["habits"][loser]["resolved_by"] == ({} if restored else {
        winner: TRIAL_BASIS if by_trial else COMPARISON_BASIS})
