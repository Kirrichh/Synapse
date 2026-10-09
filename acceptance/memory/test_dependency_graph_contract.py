"""What depended on a forgotten basis, over plain data (review §8.2).

Pure data through the court's dependency projection and decision entry. A case
is forgotten; its recorded results are named by its tombstone:

* the projection answers which authorities depended on them — the hypotheses
  read from or checked against them, the version of knowledge read from them,
  the learned habit derived from the case and the habit whose basis admitted
  that version — and lists what it cannot name (a confirmation without a
  recorded check) as unknown, never as absent;
* a correction is history, not support: forgetting the corrected version's
  source does not reach the correction;
* the decision keeps the three acts apart: the version leaves the index
  (withdrawn, still in the timeline), the hypotheses return to provisional (a
  revoked confirmation is no refutation), the habit whose retained basis no
  longer suffices is archived (TR), the habit that admitted the version goes
  to probation (TC); what does not depend on the case is untouched;
* the decision reads state only: the next window changes nothing more, and
  what was observed or checked again after the forget is new and stands.
"""
from __future__ import annotations

import copy

from acceptance.memory import _court_data as data
from synapse.memory_consolidation.court.dependencies import dependents, graph


def _hypothesis(status, source_ref, check=None, basis=None):
    return {"record": {"id": "h"}, "status": status, "reason": "check_agrees", "window": 3, "run_id": "run-x",
            "source_ref": source_ref, "check_ref": None if check is None else {"gw_seq": 1, "evidence": check},
            "basis": basis, "claim_key": "k"}


def _version(ref, **extra):
    return {"record": {"id": "s", "source": {"tool": "flights", "ref": ref}}, "known_from": 2, "known_until": None,
            "corrected_by": None, **extra}


def _world():
    # The two recoveries apply to different routes: no conflict stands between them.
    domestic = {**data.CONDITION, "when": [{"field": "route_kind", "op": "==", "value": "dom"}]}
    config, state, ids = data.world(labels=("q", "c"), state_name="active", conditions={"c": domestic})
    state["quanta"] = {
        "q_q": {"retention_state": "forgotten", "evidence_refs": [], "replay_ref": None, "tombstone": "tmb_1"},
        "q_c": {"retention_state": "full", "evidence_refs": ["ev-c"], "replay_ref": {"run_id": "run-c"}}}
    state["retention"]["tombstones"] = {"q_q": {"tombstone": "tmb_1", "reason": "data subject request",
                                                "authority": "GOVERNING_HUMAN", "window": state["window"],
                                                "refs": ["ev-q", "raw-q"]}}
    state["hypotheses"] = {"h_read": _hypothesis("confirmed", "ev-q", "ev-check"),
                           "h_checked": _hypothesis("refuted", "ev-other", "ev-q"),
                           "h_elsewhere": _hypothesis("confirmed", "ev-c", "ev-c2"),
                           "h_unchecked": _hypothesis("confirmed", "ev-c")}
    state["knowledge"] = {"versions": {"s_forgotten": _version("ev-q", corrected_by="s_correction"),
                                       "s_correction": _version("ev-new"), "s_kept": _version("ev-c")},
                          "uses": {"s_forgotten": ["run-c"]}}
    return config, state, ids


def test_the_projection_names_every_authority_that_depended_on_the_forgotten_results():
    _, state, ids = _world()
    projection = graph(state)
    found = dependents(projection, ["case:q_q", "observation:ev-q", "observation:raw-q"])
    assert found == {"habit": sorted([ids["q"], ids["c"]]), "hypothesis": ["h_checked", "h_read"],
                     "statement": ["s_forgotten"]}
    assert ["hypothesis:h_unchecked", "check_observation"] in [list(item) for item in projection["unknown"]]
    # A correction is a revision of the forgotten version, never supported by it.
    assert "s_correction" not in found["statement"]
    assert ("statement:s_correction", "wasRevisionOf", "statement:s_forgotten") in projection["edges"]


def test_the_decision_withdraws_revokes_and_archives_separately():
    config, state, ids = _world()
    after, decision = data.window(config, state)
    entry, = decision["sections"]["dependencies"]
    assert entry["index_withdrawn"] == ["s_forgotten"]
    assert entry["authority_revoked"] == {"hypotheses": ["h_checked", "h_read"], "habits_archived": [ids["q"]],
                                          "habits_on_probation": [ids["c"]]}
    knowledge = decision["knowledge"]
    assert knowledge["versions"]["s_forgotten"]["withdrawn"]["tombstone"] == "tmb_1"
    assert knowledge["versions"]["s_forgotten"]["record"] == state["knowledge"]["versions"]["s_forgotten"]["record"]
    revoked = decision["hypotheses"]["updates"]
    assert {hypothesis_id: (item["status"], item["reason"]) for hypothesis_id, item in
            revoked.items()} == {"h_read": ("provisional", "basis_forgotten:tmb_1"),
                                 "h_checked": ("provisional", "basis_forgotten:tmb_1")}
    moves = {(item["habit_id"], item["rule"], item["to"]) for item in decision["sections"]["transitions"]}
    assert moves == {(ids["q"], "TR", "dormant"), (ids["c"], "TC", "probation")}

    # The next window reads the applied state and changes nothing more.
    folded = copy.deepcopy(after)
    folded["hypotheses"] = {**state["hypotheses"], **revoked}
    folded["knowledge"] = {"versions": {**state["knowledge"]["versions"], **knowledge["versions"]},
                           "uses": state["knowledge"]["uses"]}
    _, again = data.window(config, folded)
    assert again["sections"]["dependencies"] == [] and again["hypotheses"]["updates"] == {}
    assert [item for item in again["sections"]["transitions"] if item["rule"] in {"TR", "TC"}] == []


def test_a_habit_whose_retained_basis_still_suffices_keeps_its_authority():
    config, state, ids = _world()
    # Four basis cases, one forgotten: three retained still meet the birth rule (three episodes).
    for qid in ("q_r1", "q_r2", "q_r3"):
        state["quanta"][qid] = {"retention_state": "full", "evidence_refs": [], "replay_ref": {"run_id": qid}}
    state["frozen"][ids["q"]]["habit"]["born_from"]["episodes"] = ["q_q", "q_r1", "q_r2", "q_r3"]
    _, decision = data.window(config, {**state, "knowledge": {"versions": {}, "uses": {}}})
    entry, = decision["sections"]["dependencies"]
    assert entry["authority_revoked"]["habits_archived"] == [] and entry["basis_still_holds"] == [ids["q"]]


def test_what_was_observed_or_checked_after_the_forget_stands():
    config, state, ids = _world()
    after = state["window"] + 1
    # The same answer read again and the claim checked again after the tombstone: new observations.
    state["knowledge"]["versions"]["s_forgotten"]["known_from"] = after
    state["hypotheses"]["h_read"]["window"] = after
    _, decision = data.window(config, state)
    entry, = decision["sections"]["dependencies"]
    assert entry["index_withdrawn"] == [] and entry["authority_revoked"]["hypotheses"] == ["h_checked"]
