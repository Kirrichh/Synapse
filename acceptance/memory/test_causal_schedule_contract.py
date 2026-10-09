"""One causal order for knowledge and hypotheses, whatever the schedule (recheck of 556624d), over plain data.

The same observations — one source's statements about one slot, a claim read from one of its answers and
checked by another tool, a claim checked against that source — are folded by the court's own stages
(``knowledge_stage``, then ``hypothesis_stage``) under schedules a seeded generator draws: in one window or in
several, the sessions finishing in any order, a session publishing statements and checks late. Whatever the
schedule, the court gives what an independent reading of the world's order gives:

* memory holds what the source stated last; a statement read before it is history — closed in the very window
  it became known and never current — and a source that really returned to an earlier answer holds it again;
  every earlier window answers as it did when it was consolidated, and a report names a status only when
  corrections decided it otherwise than the window's checks or memory did;
* a claim's status is its latest check, revised by every correction after it: a statement whose content
  differs from the one before it corrects every answer of the run of equal statements it ends, at its own
  place; a claim read from such an answer, or checked against the source, and decided before the correction
  is ``provisional`` at the place of the last correction that reaches it.

Targeted cases pin the parts: a correction moves a ``provisional`` status to its place; one and two corrections
before a late check, with the claim unknown, confirmed or provisional; a check after a correction stands; a
late statement between two others corrects the answer before it there, adds that statement alone to the order
its report carries, and is closed by the version of what the source stated last; a status a correction no
longer reaches is its check's again, and one a late statement moves is named by the correction that moved it;
a forget's revocation is not undone, and a forget withdraws what was read, never that the source said something
else later; the admission of an action reads the basis itself by the same rule — absent from memory or not —
and a decision after the correction stands; the reassessment restores the order from what was consolidated,
from records that verify, holds the timeline by what the source stated last while every earlier window answers
as it did, reports each correction with its revision, and changes nothing when decided again; a held version
no record places keeps its slot, and an unplaceable recorded correction still reaches every status it can.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from synapse.memory_consolidation import hypotheses
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.hypotheses import decided, fold, held_corrections, hypothesis_stage, reaches
from synapse.memory_consolidation.court.knowledge import (additions, corrections_of, knowledge_stage,
                                                         reassessed_knowledge, recorded_order, retimed,
                                                         unplaced_corrections)
from synapse.memory_consolidation.court.projection import apply_report, empty_state
from synapse.memory_consolidation.session import MemorySession
from synapse.memory_consolidation.knowledge.statements import declare, slot, source_identity
from synapse.memory_consolidation.knowledge.timeline import held_period

SOURCE = {"tool": "source", "args": {"account": "A"}}
CHECK = {"tool": "check", "args": {"account": "A"}}
NOTES = {"tool": "notes", "args": {"account": "A"}}


def _configuration():
    contract = {"verifies": {"subject": {"request": "account", "answer": "account"}, "scope": {"value": "bank"}}}
    tools = [{"name": name, "server": "bank", "descriptor_sha256": "0" * 64, "input_schema": {"type": "object"},
              "output_schema": {"type": "object"}, "source": source, "contract": contract}
             for name, source in (("source", "core:bank"), ("check", "ledger:bank"), ("notes", "notes:bank"))]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1", "advisor": None, "scorer": None, "element": "bank",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "bank", "argv": ["x"]}],
                  "tools": tools, "provenance": {"core:bank": {"ancestors": []}, "ledger:bank": {"ancestors": []},
                                                  "notes:bank": {"ancestors": []}}}})


CONFIGURATION = _configuration()


def _statement(value, ref):
    return declare({"subject": "A", "property": "balance", "value": value, "text": f"balance {value}",
                    "valid": {"from": "2026-01-01"}, "source": SOURCE}, SOURCE, ref)


def _claim(source, check, ref):
    return hypotheses.declare({"aspect": "content", "subject": "A", "statement": {"balance": 20}, "scope": "bank",
                               "source": source, "check": check}, CONFIGURATION, ref)


def _empty():
    return empty_state()


def _declared(record, place, run_id, position=0):
    return {"run_id": run_id, "position": position, "statement": record, "vector": None, "embedded_by": None,
            "observed": place}


def _check(record, place, status, run_id, position=0):
    return {"kind": "hypothesis_probed", "position": position, "run_id": run_id, "hypothesis": record["id"],
            "record": record, "status": status, "reason": "check_agrees" if status == "confirmed" else "contradicted",
            "check_ref": {"gw_seq": place, "evidence": f"ev-check-{place}"}, "rule": None, "check_basis": None}


def _window(state, declared=(), checks=()):
    """One consolidation of the court's knowledge and hypothesis stages, applied as its report is (the projection
    joins what the report adds to each order); the state after it and its report."""
    context = SimpleNamespace(state=state, window=state["window"] + 1, report={},
                              draft={"knowledge": {"declared": list(declared), "uses": []},
                                     "hypotheses": list(checks), "cases": [], "relied": []})
    knowledge = knowledge_stage(context, {}, {}, CONFIGURATION)
    decided = hypothesis_stage(context, knowledge.pop("corrections"), knowledge["stated"])
    held = state["knowledge"]
    applied = {"habits": {}, "frozen": {}, "declared": {}, "slow_only": [], "pool": {}, "quanta": {}, "parts": {},
               "cursors": {}, "digest": None, "retention": state["retention"], "hypotheses": decided["updates"],
               "knowledge": {"versions": knowledge["versions"], "uses": knowledge["uses"],
                             "stated": additions(held["stated"], knowledge["stated"])}}
    after = apply_report(state, {"consolidation_id": f"window-{context.window}", "apply": applied})
    # The report's additions, joined by the projection, are the orders the court decided.
    assert after["knowledge"]["stated"] == {**held["stated"], **knowledge["stated"]}
    return after, {"knowledge": context.report["knowledge"], "hypotheses": decided["section"],
                   "added": applied["knowledge"]["stated"]}


def _current(state):
    return sorted(version["record"]["value"] for version in state["knowledge"]["versions"].values()
                  if version["known_until"] is None and version.get("withdrawn") is None)


def _status(state, record):
    entry = state["hypotheses"].get(record["id"])
    return None if entry is None else (entry["status"], entry["at"])


# -- the property ---------------------------------------------------------------------------------------------
def _oracle(observations, read_ref, checks):
    """What the world's order says, read independently: the last statement holds; a claim is its latest check,
    provisional at the last change after it that reaches it (any change for a claim checked against the source,
    the end of a run holding its answer for a claim read from it)."""
    ordered = sorted(observations)
    changes, run = [], [ordered[0]]
    for place, value, ref in ordered[1:]:
        if value != run[-1][1]:
            changes.append((place, {item[2] for item in run}))
            run = []
        run.append((place, value, ref))
    statuses = {}
    for name, items in checks.items():
        if not items:
            continue
        place, status = max(items)
        reached = [at for at, refs in changes if at > place and (name == "checked" or read_ref in refs)]
        statuses[name] = ("provisional", max(reached)) if reached else (status, place)
    return [ordered[-1][1]], statuses


def _draw(rng):
    """Observations at distinct places (values return, answers repeat or not), the claims and their checks, and
    each event's session."""
    places = rng.sample(range(1, 60), rng.randint(5, 10))
    count = rng.randint(2, min(5, len(places) - 3))
    observed, checked = sorted(places[:count]), places[count:]
    observations = []
    for place in observed:
        value = rng.choice((10, 20, 30))
        observations.append((place, value, f"ev-{value}" if rng.random() < 0.5 else f"ev-{place}"))
    read_from = rng.choice(observations)
    read = _claim(SOURCE, CHECK, read_from[2])
    against = _claim(NOTES, SOURCE, "ev-notes")
    checks = {"read": [], "checked": []}
    for place in checked:
        name = rng.choice(("read", "checked"))
        if name == "read" and place < read_from[0]:
            name = "checked"  # A claim is checked after the answer it was read from.
        checks[name].append((place, rng.choice(("confirmed", "refuted"))))
    sessions = rng.randint(1, 4)
    events = [("declared", item) for item in observations] + [
        ("check", (name, item)) for name, items in checks.items() for item in items]
    owners = [rng.randrange(sessions) for _ in events]
    return observations, read, against, read_from[2], checks, events, owners, sessions


def _schedule(rng, sessions):
    """The order sessions finish in and how consecutive ones share a window."""
    order = rng.sample(range(sessions), sessions)
    windows, current = [], []
    for session in order:
        current.append(session)
        if rng.random() < 0.5:
            windows.append(current)
            current = []
    return windows + ([current] if current else [])


def _decision(entry):
    return entry["status"], entry["reason"], entry.get("at")


def _held_in(state, window):
    return sorted(version["record"]["value"] for version in state["knowledge"]["versions"].values()
                  if held_period(version, window) is not None)


def _fold_schedule(windows, events, owners, read, against):
    state = _empty()
    answered = []
    for window in windows:
        declared, checks = [], []
        for (kind, item), owner in zip(events, owners):
            if owner not in window:
                continue
            if kind == "declared":
                place, value, ref = item
                declared.append(_declared(_statement(value, ref), place, f"run-{owner}"))
            else:
                name, (place, status) = item
                checks.append(_check(read if name == "read" else against, place, status, f"run-{owner}"))
        held = {hypothesis: _decision(entry) for hypothesis, entry in state["hypotheses"].items()}
        state, report = _window(state, declared, checks)
        # The report names a status only when corrections decided it otherwise than the window's checks — or, for
        # a claim the window did not check, than memory held it.
        decided_by = {**held, **{item["hypothesis"]: _decision(item) for item in report["hypotheses"]["probed"]}}
        named = [item["hypothesis"] for item in report["hypotheses"]["restored"] + report["hypotheses"]["corrected"]]
        named += [hypothesis for item in report["knowledge"]["revisions"] for hypothesis in item["hypotheses"]]
        assert all(_decision(state["hypotheses"][hypothesis]) != decided_by.get(hypothesis) for hypothesis in named)
        # Every earlier window answers as it did when it was consolidated; this one holds what memory holds now.
        answered.append(_held_in(state, state["window"]))
        assert [_held_in(state, index) for index in range(1, state["window"] + 1)] == answered
        assert answered[-1] == _current(state)
    return state


@pytest.mark.parametrize("seed", range(40))
def test_any_schedule_of_the_same_observations_gives_the_world_order(seed):
    rng = random.Random(seed)
    for _ in range(10):
        observations, read, against, read_ref, checks, events, owners, sessions = _draw(rng)
        current, statuses = _oracle(observations, read_ref, checks)
        batch = _fold_schedule([list(range(sessions))], events, owners, read, against)
        for windows in [[list(range(sessions))]] + [_schedule(rng, sessions) for _ in range(4)]:
            state = _fold_schedule(windows, events, owners, read, against)
            assert _current(state) == current, (seed, windows, observations)
            found = {name: _status(state, record) for name, record in (("read", read), ("checked", against))
                     if checks[name]}
            assert found == statuses, (seed, windows, observations, checks)
            assert found == {name: _status(batch, record) for name, record in (("read", read), ("checked", against))
                             if checks[name]}
            for version in state["knowledge"]["versions"].values():
                # A version is current only while it is what the source stated last.
                assert version["known_until"] is not None or version["record"]["value"] == current[0]


# -- targeted cases -------------------------------------------------------------------------------------------
READ = _claim(SOURCE, CHECK, "ev-20")
AGAINST = _claim(NOTES, SOURCE, "ev-notes")


def _seeded(*observations):
    """Memory that folded these statements of the source, each in its own window."""
    state = _empty()
    for place, value in observations:
        state, _ = _window(state, [_declared(_statement(value, f"ev-{value}"), place, f"seed-{place}")])
    return state


def test_a_correction_moves_a_provisional_status_to_its_place():
    state, _ = _window(_seeded((1, 20)), checks=[_check(AGAINST, 2, "confirmed", "checker")])
    state, _ = _window(state, [_declared(_statement(25, "ev-25"), 5, "first")])
    assert _status(state, AGAINST) == ("provisional", 5)
    state, report = _window(state, [_declared(_statement(30, "ev-30"), 9, "second")])
    assert _status(state, AGAINST) == ("provisional", 9)  # Already provisional: its place moves.
    revision, = report["knowledge"]["revisions"]
    assert revision["hypotheses"] == [AGAINST["id"]]
    # A check made between the two corrections can no longer establish it.
    state, report = _window(state, checks=[_check(AGAINST, 7, "confirmed", "late")])
    assert _status(state, AGAINST) == ("provisional", 9)
    assert report["hypotheses"]["corrected"] == [{"hypothesis": AGAINST["id"], "statement": revision["statement"],
                                                  "corrected_by": _statement(30, "ev-30")["id"], "at": 9}]


@pytest.mark.parametrize("prior", ["absent", "confirmed", "provisional"])
@pytest.mark.parametrize("corrections", [1, 2])
def test_a_late_check_meets_the_corrections_memory_already_holds(prior, corrections):
    state = _seeded((1, 20))
    if prior != "absent":
        state, _ = _window(state, checks=[_check(AGAINST, 2, "confirmed", "seed-check")])
    if prior == "provisional":
        state, _ = _window(state, [_declared(_statement(25, "ev-25"), 3, "first")])
        state, _ = _window(state, [_declared(_statement(20, "ev-20b"), 4, "back")])
    last = 10
    for index in range(corrections):
        state, _ = _window(state, [_declared(_statement(30 + index, f"ev-{30 + index}"), 8 + 2 * index, "change")])
        last = 8 + 2 * index
    # Checked at 6, published after every correction: the corrections memory already holds reach it.
    state, _ = _window(state, checks=[_check(AGAINST, 6, "confirmed", "delayed")])
    assert _status(state, AGAINST) == ("provisional", last)
    # A check after the last correction stands.
    state, _ = _window(state, checks=[_check(AGAINST, 12, "confirmed", "fresh")])
    assert _status(state, AGAINST) == ("confirmed", 12)


def test_a_late_statement_holds_nothing_and_corrects_the_answer_before_it_there():
    state = _seeded((1, 20))
    state, _ = _window(state, checks=[_check(READ, 2, "confirmed", "reader")])
    state, _ = _window(state, [_declared(_statement(20, "ev-20"), 9, "restated")])  # The same answer, later.
    assert _status(state, READ) == ("confirmed", 2)
    # Read at 5 by a session that finished last: memory keeps holding 20 (stated again at 9).
    state, report = _window(state, [_declared(_statement(25, "ev-25"), 5, "delayed")])
    assert _current(state) == [20]
    late = state["knowledge"]["versions"][_statement(25, "ev-25")["id"]]
    assert (late["known_from"], late["known_until"], late["corrected_by"]) == (state["window"], state["window"],
                                                                                _statement(20, "ev-20")["id"])
    assert held_period(late, state["window"]) is None and held_period(late, None) is None
    assert report["knowledge"]["declared"][0]["late"] and report["knowledge"]["corrections"][0]["late"]
    # Its report adds the one statement to the order memory keeps, never the order again.
    assert [statement[:2] for group in report["added"].values() for statement in group["statements"]] == [
        [5, _statement(25, "ev-25")["id"]]]
    # The answer the claim was read from was superseded at 5: the check at 2 no longer establishes it.
    assert _status(state, READ) == ("provisional", 5)


def test_a_late_statement_is_closed_by_what_the_source_stated_last():
    state, _ = _window(_seeded((1, 10), (5, 30), (9, 40)), [_declared(_statement(20, "ev-20"), 3, "delayed")])
    late = state["knowledge"]["versions"][_statement(20, "ev-20")["id"]]
    assert late["corrected_by"] == _statement(40, "ev-40")["id"] and _current(state) == [40]


def test_a_source_returning_to_an_earlier_answer_holds_it_again():
    state = _seeded((1, 20), (5, 30))
    state, report = _window(state, [_declared(_statement(20, "ev-20"), 9, "back")])
    assert _current(state) == [20] and report["knowledge"]["declared"][0].get("held_again") is True


def test_a_status_a_correction_no_longer_reaches_is_its_checks_again():
    state = _seeded((1, 10))
    read = _claim(SOURCE, CHECK, "ev-10")
    state, _ = _window(state, checks=[_check(read, 5, "confirmed", "reader")])
    state, _ = _window(state, [_declared(_statement(30, "ev-30"), 7, "later")])
    assert _status(state, read) == ("provisional", 7)
    # Read at 3 and published last: the answer the claim rests on was superseded at 3, before its check at 5.
    state, report = _window(state, [_declared(_statement(20, "ev-20"), 3, "delayed")])
    assert _status(state, read) == ("confirmed", 5)
    assert report["hypotheses"]["restored"] == [{"hypothesis": read["id"], "status": "confirmed", "at": 5}]


def test_a_status_a_late_statement_moves_is_named_by_the_correction_that_moved_it():
    state = _seeded((1, 10))
    read = _claim(SOURCE, CHECK, "ev-10")
    state, _ = _window(state, checks=[_check(read, 2, "confirmed", "reader")])
    state, _ = _window(state, [_declared(_statement(30, "ev-30"), 7, "later")])
    assert _status(state, read) == ("provisional", 7)
    # Read at 3 and published last: the answer the claim rests on was superseded at 3, after its check at 2.
    state, report = _window(state, [_declared(_statement(20, "ev-20"), 3, "delayed")])
    assert _status(state, read) == ("provisional", 3)
    ten = _statement(10, "ev-10")["id"]
    assert [item["hypotheses"] for item in report["knowledge"]["revisions"] if item["statement"] == ten] == [
        [read["id"]]]
    assert report["hypotheses"]["corrected"] == []  # Named by the correction this window adds, not as a held one.


def test_every_correction_a_window_adds_names_what_it_revised():
    state, _ = _window(_seeded((1, 20)), checks=[_check(AGAINST, 2, "confirmed", "checker")])
    state, report = _window(state, [_declared(_statement(25, "ev-25"), 5, "first"),
                                    _declared(_statement(30, "ev-30"), 9, "second")])
    assert _status(state, AGAINST) == ("provisional", 9)  # Revised at 5, then moved to 9.
    assert [item["hypotheses"] for item in report["knowledge"]["revisions"]] == [[AGAINST["id"]], [AGAINST["id"]]]


def test_a_forget_revocation_is_never_undone_by_the_order():
    state = _seeded((1, 10))
    read = _claim(SOURCE, CHECK, "ev-10")
    state, _ = _window(state, checks=[_check(read, 5, "confirmed", "reader")])
    state, _ = _window(state, [_declared(_statement(30, "ev-30"), 7, "later")])
    entry = state["hypotheses"][read["id"]]
    state["hypotheses"][read["id"]] = {**entry, "reason": "basis_forgotten:tmb_1"}
    state, _ = _window(state, [_declared(_statement(20, "ev-20"), 3, "delayed")])
    assert state["hypotheses"][read["id"]]["reason"] == "basis_forgotten:tmb_1"


def test_admission_reads_the_basis_itself_by_the_courts_rule():
    state = _seeded((1, 20), (8, 30))
    for at, reached in ((6, True), (8, False), (9, False)):
        decision = {"record": AGAINST, "source_ref": AGAINST["source"]["ref"], "at": at}
        found = [correction for correction in held_corrections(state["knowledge"], [AGAINST])
                 if reaches(decision, correction)]
        assert bool(found) is reached, at  # Absent from memory's statuses: the order alone decides.
    # A claim about another source rests on nothing this order corrects.
    other = _claim(NOTES, CHECK, "ev-notes")
    assert held_corrections(state["knowledge"], [other]) == []
    # The session reads it so before an effect, and an action may rest only on a claim this run declared.
    gateway = SimpleNamespace(refuse=lambda request, reason: {"refused": reason}, invoke=lambda request: {"sent": True})
    session = MemorySession(SimpleNamespace(configuration=CONFIGURATION, memory_now=lambda: state, gateway=gateway),
                            {"run_id": "acting"}, None, None)
    assert session.declare_hypothesis({"aspect": "content", "subject": "A", "statement": {"balance": 20},
                                       "scope": "bank", "source": NOTES, "check": SOURCE}, "ev-notes") == AGAINST

    def act(at, hypothesis=AGAINST["id"]):
        read = {"method": "probe", "window": None,
                "observations": {"source": "ev-notes", "check": {"gw_seq": at, "evidence": f"ev-check-{at}"}}}
        return session.invoke_action({"tool": "notes", "args": {}, "path": "slow",
                                      "requires": [{"hypothesis": hypothesis, "status": "confirmed", "read": read}]})

    assert "corrected by" in act(6)["refused"] and act(9) == {"sent": True}
    assert "was not declared by this run" in act(9, "hyp_elsewhere")["refused"]


def _folded_late(readable=True):
    """Memory decided under an earlier policy: a statement read at 3 folded after one read at 7, as a correction;
    and the record of both sessions — the delayed one declared its statement twice from one answer (its record
    gone when not ``readable``), the newer one declared another after its last consolidation, and a third
    session's record no longer verifies."""
    old, new = _statement(30, "ev-30"), _statement(20, "ev-20")
    versions = {old["id"]: {"record": old, "slot": slot(old), "source_identity": source_identity(old),
                            "source": "core:bank", "run_id": "newer", "vector": None, "embedded_by": None,
                            "known_from": 1, "known_until": 2, "corrected_by": new["id"]},
                new["id"]: {"record": new, "slot": slot(new), "source_identity": source_identity(new),
                            "source": "core:bank", "run_id": "delayed", "vector": None, "embedded_by": None,
                            "known_from": 2, "known_until": None, "corrected_by": None}}
    facts = {"newer": ({0: 7, 1: 9}, [old, _statement(40, "ev-40")], []),
             "delayed": ({0: 3, 1: 3}, [new, new], []),
             "broken": ({0: 8}, [_statement(50, "ev-50")], ["a history that does not verify"])}
    sessions = SimpleNamespace(facts=lambda run_id: None if run_id == "delayed" and not readable else SimpleNamespace(
        problems=facts[run_id][2], observed=facts[run_id][0],
        found={"knowledge_declared": [(position, {"statement": record})
                                      for position, record in enumerate(facts[run_id][1])]}))
    recorded = recorded_order({"cursors": {"newer": {"to": 1}, "delayed": {"to": 2}, "broken": {"to": 1}}},
                              sessions)
    return old, new, versions, recorded


def test_the_reassessment_holds_what_the_source_stated_last_and_keeps_every_earlier_window():
    old, new, versions, recorded = _folded_late()
    knowledge = {"versions": versions, "uses": {}, "stated": {}}
    # Only what was consolidated, from records that verify, once per answer.
    order, = recorded.values()
    assert [statement[:3] for statement in order["statements"]] == [[3, new["id"], "ev-20"], [7, old["id"], "ev-30"]]
    stated, updated, rows = retimed(knowledge, recorded, 3)
    assert stated == recorded
    assert rows == [{"statement": new["id"], "corrected_by": old["id"], "slot": slot(new)}]
    assert (updated[new["id"]]["known_until"], updated[old["id"]]["known_from"]) == (3, 3)
    timeline = {**versions, **updated}
    for window, value in ((1, 30), (2, 20), (3, 30)):
        assert [entry["record"]["value"] for entry in timeline.values() if held_period(entry, window)] == [value]
    # Reassessed again, it changes nothing.
    assert retimed({"versions": timeline, "uses": {}, "stated": stated}, recorded, 4)[1:] == ({}, [])
    # The earlier policy closed 30 by the reading of 20, which the order places before it: no correction at all,
    # so nothing unplaceable revises a claim read from 30.
    assert unplaced_corrections({"versions": timeline, "uses": {}, "stated": stated}) == []
    # A version memory kept ordered by arrival is placed by the record, then ordered by it.
    key, = recorded
    kept = {key: {**order, "statements": [[None, *order["statements"][0][1:]], order["statements"][1]]}}
    assert retimed({**knowledge, "stated": kept}, recorded, 3) == (recorded, updated, rows)
    # The decision reports each correction with its revision: a live habit whose basis admitted the corrected
    # version goes to probation.
    context = SimpleNamespace(
        state={"window": 2, "knowledge": {**knowledge, "uses": {new["id"]: ["admitting"]}},
               "frozen": {"hab_a": {"habit": {"born_from": {"episodes": ["q1"]}}}},
               "quanta": {"q1": {"replay_ref": {"run_id": "admitting"}}}},
        window=3, report={"knowledge": {"corrections": [], "revisions": []}})
    forced = {}
    decided = reassessed_knowledge(context, {"hab_a": {"state": "active", "superseded_by": None}}, forced, recorded)
    assert decided == {"versions": updated, "stated": stated}
    assert context.report["knowledge"] == {"corrections": rows, "revisions": [
        {"statement": new["id"], "hypotheses": [], "habits": ["hab_a"]}]}
    assert forced == {"hab_a": ("TC", f"a fact its basis admitted was corrected ({new['id']})")}
    # What memory holds the record cannot place: nothing to order it against.
    assert retimed(knowledge, {}, 3)[1:] == ({}, [])
    assert retimed(knowledge, _folded_late(readable=False)[3], 3)[1:] == ({}, [])


def test_a_forget_withdraws_what_was_read_never_that_the_source_said_something_else_later():
    # Folded in the world's order (10, 20, 30, then 30 forgotten), memory holds nothing; so with the reading of 20
    # published after the forget.
    thirty, twenty = _statement(30, "ev-30")["id"], _statement(20, "ev-20")["id"]
    in_order, late = _seeded((1, 10), (5, 20), (9, 30)), _seeded((1, 10), (9, 30))
    for state in (in_order, late):
        state["knowledge"]["versions"][thirty]["withdrawn"] = {"tombstone": "tmb_1", "window": state["window"]}
    late, report = _window(late, [_declared(_statement(20, "ev-20"), 5, "delayed")])
    assert _current(in_order) == _current(late) == []
    version = late["knowledge"]["versions"][twenty]
    assert (version["known_until"], version["corrected_by"]) == (late["window"], thirty)
    assert report["knowledge"]["declared"][0]["late"]
    # A reading stating what the forgotten statement stated, made before it: nothing after it said otherwise.
    same = _seeded((1, 10), (9, 20))
    same["knowledge"]["versions"][twenty]["withdrawn"] = {"tombstone": "tmb_1", "window": same["window"]}
    same, _ = _window(same, [_declared(_statement(20, "ev-20b"), 5, "delayed")])
    assert _current(same) == [20]
    # A reassessment of memory that held the earlier reading — folded after 30 was forgotten while held — closes
    # it, and never holds the forgotten one again.
    old, new, versions, recorded = _folded_late()
    versions[old["id"]].update(known_until=None, corrected_by=None, withdrawn={"tombstone": "tmb_1", "window": 2})
    _, updated, rows = retimed({"versions": versions, "uses": {}, "stated": {}}, recorded, 3)
    assert rows == [{"statement": new["id"], "corrected_by": old["id"], "slot": slot(new)}]
    assert set(updated) == {new["id"]} and updated[new["id"]]["known_until"] == 3


def test_an_unplaceable_recorded_correction_still_reaches_every_status_it_can():
    old = _statement(20, "ev-20")
    knowledge = {"versions": {old["id"]: {"record": old, "slot": slot(old), "source_identity": source_identity(old),
                                          "known_from": 1, "known_until": 2, "corrected_by": "stm_newer"}},
                 "uses": {}, "stated": {}}
    unplaced, = unplaced_corrections(knowledge)
    assert (unplaced["at"], unplaced["old"]["record"]["id"]) == (None, old["id"])
    updates, _ = fold({}, [_check(READ, 12, "confirmed", "reader")], {}, 3)
    assert decided(updates[READ["id"]], [unplaced], 3)[0]["status"] == "provisional"  # Placed after everything.
    assert corrections_of(knowledge) == []
    # A closure of a period held before is one too; a closure the order places is never repeated after everything.
    held_again = {**knowledge["versions"][old["id"]], "known_until": None, "corrected_by": None,
                  "earlier_known": [{"from": 1, "until": 2, "corrected_by": "stm_newer", "withdrawn": None}]}
    assert [item["by"] for item in unplaced_corrections({**knowledge, "versions": {old["id"]: held_again}})] == [
        "stm_newer"]
    assert unplaced_corrections(_seeded((1, 20), (5, 30))["knowledge"]) == []
