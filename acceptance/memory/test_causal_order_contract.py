"""Memory decides in the gateway's order (review M1, M2, M4, M6), over plain data.

Pure functions of the court:

* ``fold`` applies checks in the order of their places on the gateway's
  sequence — never the order they are handed over in, nor the names of their
  runs: the latest check decides, an older one is superseded and reported, a
  check without a recorded answer never displaces one;
* ``decided`` revises what a check decided by every correction that reaches
  it: a correction returns to ``provisional`` what was decided before it from
  the corrected source, a check at or after it stands, and one that cannot be
  placed reaches every status;
* ``changed_since`` tells whether a status left ``confirmed`` after the
  decision an action relies on: by places when both are placed, by windows
  for a status read from the court's record, as a change when unplaced;
* ``decided_before``, ``forgotten_since`` and ``revoke_forgotten`` place a
  decision against a forget by the gateway's head the operator recorded, and
  by the windows only when either is unplaced;
* the report of the actions that relied on a hypothesis says whether its
  status was revoked before or after the action's admission.
"""
from __future__ import annotations

import pytest

from synapse.memory_consolidation import hypotheses
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.dependencies import decided_before, forgotten_since, revoke_forgotten
from synapse.memory_consolidation.court.hypotheses import changed_since, decided, fold, revocation


def _configuration():
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


RECORD = hypotheses.declare({"aspect": "content", "subject": "A", "statement": {"balance": 20}, "scope": "bank",
                             "source": {"tool": "source", "args": {"account": "A"}},
                             "check": {"tool": "check", "args": {"account": "A"}}}, _configuration(), "ev-source")
H = RECORD["id"]


def _check(run_id, at, status, position=0):
    return {"kind": "hypothesis_probed", "position": position, "run_id": run_id, "hypothesis": H, "record": RECORD,
            "status": status, "reason": "check_agrees" if status == "confirmed" else "contradicted:balance",
            "check_ref": None if at is None else {"gw_seq": at, "evidence": f"ev-{at}"}, "rule": None,
            "check_basis": None}


def _held(status, at, window=3):
    return {H: {"record": RECORD, "claim_key": hypotheses.claim_key(RECORD), "source_ref": "ev-source",
                "status": status, "reason": "check_agrees", "window": window, "run_id": "earlier",
                "check_ref": None if at is None else {"gw_seq": at, "evidence": f"ev-{at}"}, "basis": "q-earlier",
                "rule": None, "check_basis": None, "at": at,
                "checked": {"status": status, "reason": "check_agrees", "at": at, "window": window}}}


def _correction(at):
    corrected = {"record": {"id": "s-old", "source": {"tool": "source", "args": {"account": "A"}, "ref": "ev-source"}}}
    return {"old": corrected, "by": "s-new", "at": at, "window": 4, "revision": {"statement": "s-old", "hypotheses": []}}


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_checks_fold_in_the_gateways_order_whatever_their_order_or_run_names(order):
    checks = [_check("z-first", 5, "confirmed"), _check("a-second", 9, "refuted")]
    updates, section = fold({}, [checks[index] for index in order], {}, 4)
    assert (updates[H]["status"], updates[H]["run_id"], updates[H]["at"]) == ("refuted", "a-second", 9)
    assert [(item["run_id"], item["at"]) for item in section["probed"]] == [("z-first", 5), ("a-second", 9)]


def test_a_check_older_than_the_status_memory_holds_is_superseded_and_reported():
    updates, section = fold(_held("confirmed", 9), [_check("late-consolidated", 5, "refuted")], {}, 4)
    assert updates == {}
    assert section["superseded"] == [{"run_id": "late-consolidated", "hypothesis": H, "status": "refuted", "at": 5,
                                      "held_at": 9}]


def test_a_check_without_a_recorded_answer_never_displaces_one():
    updates, section = fold(_held("confirmed", 5), [_check("absent", None, "provisional")], {}, 4)
    assert updates == {} and section["superseded"][0]["at"] is None
    updates, _ = fold({}, [_check("absent", None, "provisional")], {}, 4)
    assert updates[H]["status"] == "provisional"  # Nothing was held: it is all memory knows.


def test_a_correction_revises_what_was_decided_before_it_and_a_check_at_or_after_it_stands():
    status, revised = decided(_held("confirmed", 5)[H], [_correction(7)], 4)
    assert (status["status"], status["reason"], status["at"]) == ("provisional", "source_corrected:s-old", 7)
    assert [item["at"] for item in revised] == [7]
    # A status checked at the very observation that corrected the source reflects the corrected world.
    status, revised = decided(_held("confirmed", 7)[H], [_correction(7)], 4)
    assert (status["status"], status["at"], revised) == ("confirmed", 7, [])
    for at in (7, 8):
        # The latest check decides; a correction at or before its place does not revise it.
        updates, _ = fold(_held("confirmed", 5), [_check("fresh", at, "confirmed")], {}, 4)
        status, revised = decided(updates[H], [_correction(7)], 4)
        assert (status["status"], status["at"], revised) == ("confirmed", at, [])


def test_a_correction_that_cannot_be_placed_reaches_every_status():
    status, _ = decided(_held("confirmed", 9)[H], [_correction(None)], 4)
    assert status["status"] == "provisional" and status["at"] is None
    # Placed after everything: it reaches a status checked later too.
    updates, _ = fold(_held("confirmed", 9), [_check("fresh", 12, "confirmed")], {}, 4)
    assert decided(updates[H], [_correction(None)], 4)[0]["status"] == "provisional"


@pytest.mark.parametrize("entry, read, changed", [
    ({"status": "confirmed", "at": 12, "window": 9}, {"observations": {"check": {"gw_seq": 5}}}, False),
    ({"status": "refuted", "at": 12, "window": 2}, {"observations": {"check": {"gw_seq": 5}}}, True),
    ({"status": "refuted", "at": 5, "window": 9}, {"observations": {"check": {"gw_seq": 5}}}, True),
    ({"status": "refuted", "at": 4, "window": 9}, {"observations": {"check": {"gw_seq": 5}}}, False),
    ({"status": "provisional", "window": 6}, {"method": "court_record", "window": 5, "observations": {}}, True),
    ({"status": "provisional", "window": 5}, {"method": "court_record", "window": 5, "observations": {}}, False),
    ({"status": "provisional", "window": 1}, {"method": "probe", "observations": {}}, True),
])
def test_a_status_changed_since_a_decision_by_places_then_windows(entry, read, changed):
    assert changed_since(entry, read) is changed


@pytest.mark.parametrize("at, window, tombstone, before", [
    (5, 9, {"after": 7, "window": 3}, True),
    (7, 9, {"after": 7, "window": 3}, True),
    (8, 1, {"after": 7, "window": 3}, False),
    (None, 3, {"after": 7, "window": 3}, True),
    (None, 4, {"after": 7, "window": 3}, False),
    (5, 4, {"after": None, "window": 3}, False),
    (None, None, {"after": 7, "window": 3}, True),
])
def test_a_decision_is_placed_against_a_forget_by_the_gateway_then_by_windows(at, window, tombstone, before):
    assert decided_before(at, window, tombstone) is before


def _forgetting(*tombstones):
    return {"hypotheses": {}, "quanta": {}, "frozen": {}, "knowledge": {"versions": {}, "uses": {}},
            "retention": {"tombstones": {f"q-{index}": tombstone for index, tombstone in enumerate(tombstones)}}}


def test_an_action_rests_on_forgotten_results_only_when_they_were_forgotten_after_its_decision():
    state = _forgetting({"tombstone": "tmb-early", "refs": ["ev-9"], "after": 4, "window": 2},
                        {"tombstone": "tmb-late", "refs": ["ev-9", "ev-x"], "after": 12, "window": 5},
                        {"tombstone": "tmb-other", "refs": ["ev-other"], "after": 12, "window": 5})
    assert forgotten_since(state, ("ev-source", "ev-9"), 9, 4) == ["tmb-late"]
    assert forgotten_since(state, ("ev-source", "ev-9"), 13, 6) == []


def test_a_forget_revokes_only_the_statuses_decided_before_it():
    state = _forgetting({"tombstone": "tmb", "refs": ["ev-5"], "after": 7, "window": 3})
    state["hypotheses"] = _held("confirmed", 5)
    revoked = {}
    (_, _, depending, ids), = revoke_forgotten(state, revoked, 4)
    assert ids == [H] and depending["hypothesis"] == [H]
    assert (revoked[H]["status"], revoked[H]["reason"], revoked[H]["at"]) == ("provisional", "basis_forgotten:tmb", 7)
    later = {H: {**_held("confirmed", 9)[H], "check_ref": {"gw_seq": 9, "evidence": "ev-5"}}}
    state["hypotheses"] = later
    revoked = {}
    (_, _, _, ids), = revoke_forgotten(state, revoked, 4)
    assert ids == [] and revoked == {}  # Checked again after the forget: the new check stands.


@pytest.mark.parametrize("entry, revoked", [
    ({"status": "confirmed", "at": 20}, None),
    ({"status": "refuted", "at": 6}, "before_admission"),
    ({"status": "provisional", "at": 12}, "after_admission"),
    ({"status": "refuted", "at": None}, None),
])
def test_the_report_says_whether_a_status_was_revoked_before_or_after_the_action(entry, revoked):
    assert revocation(entry, 9) == revoked
