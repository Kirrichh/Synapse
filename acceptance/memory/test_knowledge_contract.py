"""Contract of semantic knowledge over plain data (refinement §15).

The rules the heavy scenarios rely on, on the subsystem's pure functions: the
timeline's resolution at a valid time as known at a window (inertia, a late
report, a declared end, bounded and event currency, an undeclared property,
disagreeing versions), reciprocal rank fusion, the exactness of the semantic
ranking against one cosine per pair (equal scores included), the court's fold of
declarations (copy and repetition, which add nothing but their place in the
world's order of statements, correction with the revision of dependent hypotheses
and habits at the place of the correcting answer, conflict, a correction back to
an answer memory held before, which is held again while every earlier window
keeps its answer) and
admission's structured checks (an event is admitted at its moment only, however
the candidate was found — the timeline places events by the same rule).
"""
from __future__ import annotations

import math
import random
from types import SimpleNamespace

import pytest

from synapse.memory_consolidation.court.dependencies import graph
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.court.hypotheses import hypothesis_stage
from synapse.memory_consolidation.court.knowledge import corrections_of, knowledge_stage
from synapse.memory_consolidation.court.window import read_session
from synapse.memory_consolidation.knowledge.search import recorded as recorded_statement
from synapse.memory_consolidation.session import MemorySession
from synapse.memory_consolidation.tools.gateway import Gateway
from synapse.memory_consolidation.knowledge.search import fuse, semantic_ranking
from synapse.memory_consolidation.knowledge.statements import declare, slot, source_identity
from synapse.memory_consolidation.knowledge.timeline import held_period, resolve
from synapse.palace_admission import admit

SOURCE = {"tool": "catalog_entry", "args": {"plan": "basic"}}
STATE = {"freshness": "state"}


def _statement(value, start=None, *, until=None, ref="ev-1", polarity=True, prop="monthly_price", source=SOURCE,
               conditions=None):
    return declare({"subject": "basic", "property": prop, "value": value, "polarity": polarity,
                    "conditions": conditions or {}, "valid": {"from": start, "until": until},
                    "text": f"basic {prop} {value}", "source": source}, source, ref)


def _entry(record, known_from=1, known_until=None, vector=None):
    return {"record": record, "vector": vector, "slot": slot(record), "source_identity": source_identity(record),
            "source": "catalog:ops", "run_id": "run", "known_from": known_from, "known_until": known_until,
            "corrected_by": None}


def _values(found):
    return [(item["entry"]["record"]["value"], item["valid_until"], item["freshness"]) for item in found]


def test_a_state_holds_until_the_next_start_and_a_late_report_takes_its_place():
    january, june = _entry(_statement(20, "2026-01-01")), _entry(_statement(25, "2026-06-01"), known_from=2)
    october = _entry(_statement(18, "2025-10-01", ref="ev-3"), known_from=3)
    versions = [january, june, october]
    assert _values(resolve(versions, STATE, valid_at="2026-07-01T00:00:00Z", known_as_of=None)) == [
        (25, None, "current")]
    assert _values(resolve(versions, STATE, valid_at="2026-03-01T00:00:00Z", known_as_of=None)) == [
        (20, "2026-06-01T00:00:00Z", "current")]
    assert _values(resolve(versions, STATE, valid_at="2025-12-01T00:00:00Z", known_as_of=None)) == [
        (18, "2026-01-01T00:00:00Z", "current")]
    assert resolve(versions, STATE, valid_at="2025-01-01T00:00:00Z", known_as_of=None) == []
    # As known at window 1, June's price did not exist yet.
    assert _values(resolve(versions, STATE, valid_at="2026-07-01T00:00:00Z", known_as_of=1)) == [(20, None, "current")]
    # A declared end terminates the state; a corrected version is out of the present, not out of history.
    ended = _entry(_statement(30, "2026-01-01", until="2026-02-01", ref="ev-4"))
    assert resolve([ended], STATE, valid_at="2026-03-01T00:00:00Z", known_as_of=None) == []
    corrected = {**january, "known_until": 4}
    assert resolve([corrected], STATE, valid_at="2026-03-01T00:00:00Z", known_as_of=None) == []
    assert _values(resolve([corrected], STATE, valid_at="2026-03-01T00:00:00Z", known_as_of=3))[0][0] == 20


def test_disagreeing_versions_all_hold_and_currency_follows_the_declared_kind():
    one, other = _entry(_statement(20, "2026-01-01")), _entry(_statement(99, "2026-01-01", ref="ev-9"))
    assert sorted(value for value, _, _ in _values(resolve([one, other], STATE, valid_at=None, known_as_of=None))) == [
        20, 99]
    promo = [_entry(_statement(15, "2026-03-01", prop="promo_price"))]
    bounded = {"freshness": "bounded", "ttl_days": 30}
    assert _values(resolve(promo, bounded, valid_at="2026-03-15T00:00:00Z", known_as_of=None)) == [
        (15, "2026-03-31T00:00:00Z", "current")]
    assert _values(resolve(promo, bounded, valid_at="2026-05-01T00:00:00Z", known_as_of=None))[0][2] == "unknown"
    assert _values(resolve(promo, bounded, valid_at=None, known_as_of=None))[0][2] == "unknown"
    outage = [_entry(_statement("eu", "2026-02-10", prop="outage"))]
    assert _values(resolve(outage, {"freshness": "event"}, valid_at="2026-02-10T00:00:00Z", known_as_of=None)) == [
        ("eu", None, "event")]
    assert resolve(outage, {"freshness": "event"}, valid_at="2026-02-11T00:00:00Z", known_as_of=None) == []
    assert _values(resolve(outage, None, valid_at=None, known_as_of=None))[0][2] == "undeclared"


def test_fusion_uses_ranks_never_scores():
    assert fuse([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]
    assert fuse([["x"], ["y"]]) == ["x", "y"]  # Equal fused ranks are ordered by identity.
    entries = [_entry(_statement(20, "2026-01-01"), vector=[1.0, 0.0]),
               _entry(_statement(21, "2026-01-01", ref="ev-2"), vector=[0.0, 1.0])]
    assert semantic_ranking([1.0, 0.0], entries, 5) == [entries[0]["record"]["id"]]  # No similarity, no candidate.


def _pairwise(query, entries, budget):
    """The reference ranking: one cosine per pair, sorted in full, ties by identity."""
    def cosine(left, right):
        dot = sum(a * b for a, b in zip(left, right))
        norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return 0.0 if norm == 0 else dot / norm
    scored = [(-round(cosine(query, entry["vector"]), 12), entry["record"]["id"]) for entry in entries
              if len(entry["vector"]) == len(query) and cosine(query, entry["vector"]) > 0]
    return [identity for _, identity in sorted(scored)[:budget]]


def test_the_semantic_ranking_is_exact():
    rng = random.Random(7)
    for trial in range(30):
        entries = [{"record": {"id": f"stm_{index:04d}"}, "vector": [rng.choice((-1.0, 0.0, 0.5, 1.0))
                                                                    for _ in range(6)]}
                   for index in range(rng.randrange(1, 120))]  # Few component values: many equal scores.
        entries += [{"record": {"id": "stm_short"}, "vector": [1.0]}, {"record": {"id": "stm_zero"},
                                                                      "vector": [0.0] * 6}]
        rng.shuffle(entries)  # Recorded order is never identity order: equal scores are ordered by identity.
        query = [rng.uniform(-1, 1) for _ in range(6)]
        budget = rng.randrange(1, 30)
        assert semantic_ranking(query, entries, budget) == _pairwise(query, entries, budget), trial


def _court(state, declared, uses=(), habits=None):
    context = SimpleNamespace(state=state, window=state["window"] + 1, report={},
                              draft={"knowledge": {"declared": declared, "uses": list(uses)}, "hypotheses": [],
                                     "cases": [], "relied": []})
    configuration = SimpleNamespace(tools=SimpleNamespace(tools={"catalog_entry": SimpleNamespace(
        source="catalog:ops")}))
    forced = {}
    result = knowledge_stage(context, habits or {}, forced, configuration)
    # The decision's next stage revises what the window's corrections reached, in the gateway's order.
    result["hypotheses"] = hypothesis_stage(context, result.pop("corrections"), result["stated"])["updates"]
    return result, context.report["knowledge"], forced


def _declared(record, position=1, run_id="run-b", observed=None):
    """A declaration whose source was observed at gateway sequence ``observed`` (its position by default)."""
    return {"run_id": run_id, "position": position, "statement": record, "vector": None,
            "observed": position if observed is None else observed}


def test_the_court_folds_copies_corrections_and_conflicts_and_revises_what_depended_on_a_correction():
    old = _statement(20, "2026-01-01", ref="ev-1")
    decision = {"status": "confirmed", "reason": "check_agrees", "window": 1, "at": None}
    hypothesis = {"record": {"source": SOURCE, "check": {"tool": "billing_quote", "args": {"plan": "basic"}}},
                  "source_ref": "ev-1", **decision, "checked": dict(decision)}
    checked = {"record": {"source": {"tool": "notes", "args": {}}, "check": SOURCE}, "source_ref": "ev-0",
               **decision, "checked": dict(decision)}
    state = {"window": 1, "knowledge": {"versions": {old["id"]: _entry(old)}, "uses": {old["id"]: ["run-a"]}},
             "hypotheses": {"hyp_read": hypothesis, "hyp_checked": checked},
             "frozen": {"hab_a": {"habit": {"born_from": {"episodes": ["q1"]}}},
                        "hab_b": {"habit": {"born_from": {"episodes": ["q2"]}}}},
             "quanta": {"q1": {"replay_ref": {"run_id": "run-a"}}, "q2": {"replay_ref": {"run_id": "run-z"}}}}
    habits = {"hab_a": {"state": "active", "superseded_by": None}, "hab_b": {"state": "active", "superseded_by": None}}

    # The very same statement, and the same source read again saying the same: copies.
    same = _statement(20, "2026-01-01", ref="ev-5")
    result, report, forced = _court(state, [_declared(old), _declared(same, 2), _declared(old, 3, observed=1)],
                                    habits=habits)
    assert result["versions"] == {} and [item["of"] for item in report["copies"]] == [old["id"]] * 3
    # No confidence is added, but the order keeps where the source stated it: the held version takes its place;
    # declared again from the same answer, it is the same statement at the same place.
    order, = result["stated"].values()
    assert [statement[:3] for statement in order["statements"]] == [[1, old["id"], "ev-1"], [2, same["id"], "ev-5"]]

    # The same source saying something else for the same start: a correction.
    new = _statement(22, "2026-01-01", ref="ev-6")
    result, report, forced = _court(state, [_declared(new)], uses=[{"run_id": "run-c", "statement": new["id"]}],
                                    habits=habits)
    assert result["versions"][old["id"]]["known_until"] == 2 and result["versions"][old["id"]]["corrected_by"] == new["id"]
    assert result["versions"][new["id"]]["known_from"] == 2
    assert report["revisions"] == [{"statement": old["id"], "hypotheses": ["hyp_checked", "hyp_read"],
                                    "habits": ["hab_a"]}]
    assert result["hypotheses"]["hyp_read"]["status"] == "provisional"
    assert result["hypotheses"]["hyp_read"]["reason"] == f"source_corrected:{old['id']}"
    assert result["hypotheses"]["hyp_checked"]["reason"] == f"check_source_corrected:{old['id']}"
    assert forced == {"hab_a": ("TC", f"a fact its basis admitted was corrected ({old['id']})")}
    assert result["uses"] == {new["id"]: ["run-c"]}

    # Another source saying something else: a conflict, both current.
    mirror = {"tool": "mirror_entry", "args": {"plan": "basic"}}
    poisoned = _statement(99, "2026-01-01", ref="ev-7", source=mirror)
    result, report, _ = _court(state, [_declared(poisoned)], habits=habits)
    assert report["conflicts"] == [{"statement": poisoned["id"], "slot": slot(old), "with": [old["id"]]}]
    assert result["versions"][poisoned["id"]]["known_until"] is None and old["id"] not in result["versions"]


def _candidate(**changes):
    base = {"id": "stm_1", "kind": "fact", "entity": "basic", "attribute": "monthly_price", "value": 20,
            "polarity": True, "conditions": {}, "valid_from": "2026-01-01T00:00:00Z", "valid_until": None,
            "freshness": "current", "source": {"tool": "catalog_entry", "ref": "ev-1"}}
    return {**base, **changes}


CLAIM = {"entity": "basic", "attribute": "monthly_price", "keys": ["basic", "monthly_price"], "at": "2026-03-01",
         "verified_by": "hyp_1"}


def _reasons(candidate, hypothesis=None, **claim):
    known = {"hyp_1": {"status": "confirmed", "aspect": "content", "subject": "basic",
                       "statement": {"monthly_price": 20}, "scope": "catalog", "conditions": {},
                       "source_ref": "ev-1", "source": {"tool": "catalog_entry", "name": "shop:catalog", "ref": "ev-1"},
                       **(hypothesis or {})}}
    decision = admit([candidate], {**CLAIM, **claim}, hypothesis_of=known.get)
    return decision["checked"][0]["reasons"]


def test_admission_reads_the_statements_structure():
    assert _reasons(_candidate()) == []
    assert _reasons(_candidate(polarity=False)) == ["another_polarity", "basis_states_another_value"]
    assert _reasons(_candidate(conditions={"region": "eu"})) == ["other_conditions", "basis_under_other_conditions"]
    assert _reasons(_candidate(conditions={"region": "eu"}), {"conditions": {"region": "eu"}},
                    conditions={"region": "eu"}) == []
    assert _reasons(_candidate(freshness="unknown")) == ["freshness_unknown"]
    assert _reasons(_candidate(valid_until="2026-03-01T00:00:00Z")) == ["outside_validity"]  # The end is exclusive.
    # A confirmation of another value, subject or source version confirms nothing here.
    assert _reasons(_candidate(), {"statement": {"monthly_price": 99}}) == ["basis_states_another_value"]
    assert _reasons(_candidate(), {"subject": "basic-plus"}) == ["basis_about_another_entity"]
    assert _reasons(_candidate(), {"source_ref": "ev-0"}) == ["basis_for_another_version"]
    assert _reasons(_candidate(), {"status": "provisional"}) == ["not_confirmed:provisional"]


def test_a_correction_can_return_to_an_answer_memory_held_before():
    twenty, twenty_five = _statement(20, "2026-01-01", ref="ev-20"), _statement(25, "2026-01-01", ref="ev-25")
    state = {"window": 0, "knowledge": {"versions": {}, "uses": {}}, "hypotheses": {}, "frozen": {}, "quanta": {}}
    history = []
    for observed, record in enumerate((twenty, twenty_five, twenty, twenty_five, twenty, twenty), start=1):
        result, report, _ = _court(state, [_declared(record, observed=observed)])
        state = {**state, "window": state["window"] + 1,
                 "knowledge": {"versions": {**state["knowledge"]["versions"], **result["versions"]}, "uses": {},
                               "stated": {**state["knowledge"].get("stated", {}), **result["stated"]}}}
        history.append(report)
    assert [len(item["corrections"]) for item in history] == [0, 1, 1, 1, 1, 0]
    assert history[2]["corrections"] == [{"statement": twenty_five["id"], "corrected_by": twenty["id"],
                                          "slot": slot(twenty)}]
    assert history[2]["declared"][0].get("held_again") is True and len(history[5]["copies"]) == 1
    versions = state["knowledge"]["versions"]
    assert versions[twenty["id"]]["earlier_known"] == [
        {"from": 1, "until": 2, "corrected_by": twenty_five["id"], "withdrawn": None},
        {"from": 3, "until": 4, "corrected_by": twenty_five["id"], "withdrawn": None}]
    assert versions[twenty_five["id"]]["earlier_known"] == [
        {"from": 2, "until": 3, "corrected_by": twenty["id"], "withdrawn": None}]
    # The order keeps every statement at its place; each change corrects the answer before it, there.
    order, = state["knowledge"]["stated"].values()
    assert [statement[1] for statement in order["statements"]] == [
        twenty["id"], twenty_five["id"], twenty["id"], twenty_five["id"], twenty["id"], twenty["id"]]
    assert [(item["at"], item["old"]["record"]["id"]) for item in corrections_of(state["knowledge"])] == [
        (2, twenty["id"]), (3, twenty_five["id"]), (4, twenty["id"]), (5, twenty_five["id"])]
    # Every window answers as memory knew it then; now, the returned answer holds.
    for window, value in ((1, 20), (2, 25), (3, 20), (4, 25), (5, 20), (6, 20), (None, 20)):
        held = [entry for entry in versions.values() if held_period(entry, window) is not None]
        assert _values(resolve(held, STATE, valid_at="2026-03-01T00:00:00Z", known_as_of=window)) == [
            (value, None, "current")], window
    # The dependency projection names both revisions.
    edges = graph({**state, "retention": {"tombstones": {}}})["edges"]
    assert {(f"statement:{twenty_five['id']}", "wasRevisionOf", f"statement:{twenty['id']}"),
            (f"statement:{twenty['id']}", "wasRevisionOf", f"statement:{twenty_five['id']}")} <= set(edges)


def test_a_version_withdrawn_while_held_keeps_that_period_when_held_again():
    twenty = _statement(20, "2026-01-01", ref="ev-20")
    mark = {"tombstone": "tmb_1", "window": 3}
    state = {"window": 4, "knowledge": {"versions": {twenty["id"]: {**_entry(twenty), "withdrawn": mark}},
                                        "uses": {}}, "hypotheses": {}, "frozen": {}, "quanta": {}}
    result, report, _ = _court(state, [_declared(twenty)])
    held = result["versions"][twenty["id"]]
    assert (held["known_from"], held["known_until"], held.get("withdrawn")) == (5, None, None)
    assert held["earlier_known"] == [{"from": 1, "until": 5, "corrected_by": None, "withdrawn": mark}]


def test_an_event_is_admitted_at_its_moment_only():
    event = _candidate(attribute="outage", value="eu", freshness="event", valid_from="2026-10-01T10:00:00Z")
    basis = {"statement": {"outage": "eu"}}
    assert _reasons(event, basis, attribute="outage", keys=["basic", "outage"], at="2026-10-01T10:00:00Z") == []
    assert _reasons(event, basis, attribute="outage", keys=["basic", "outage"], at="2026-10-01T11:00:00Z") == ["outside_validity"]
    assert _reasons(event, basis, attribute="outage", keys=["basic", "outage"], at="2026-10-01T09:00:00Z") == ["outside_validity"]
    assert _reasons(event, basis, attribute="outage", keys=["basic", "outage"], at=None) == ["freshness_unknown"]
    # An event placed nowhere holds at no moment; the timeline places it by the same rule.
    assert _reasons({**event, "valid_from": None}, basis, attribute="outage", keys=["basic", "outage"], at="2026-10-01T10:00:00Z") == [
        "outside_validity"]
    outage = [_entry(_statement("eu", "2026-10-01T10:00:00Z", prop="outage"))]
    for at, holds in (("2026-10-01T10:00:00Z", True), ("2026-10-01T11:00:00Z", False)):
        assert bool(resolve(outage, {"freshness": "event"}, valid_at=at, known_as_of=None)) is holds


@pytest.mark.parametrize("first, second", [("run-a", "run-z"), ("run-z", "run-a")])
def test_one_consolidation_folds_readings_in_the_order_the_world_was_read(first, second):
    twenty, twenty_five = _statement(20, "2026-01-01", ref="ev-20"), _statement(25, "2026-01-01", ref="ev-25")
    state = {"window": 0, "knowledge": {"versions": {}, "uses": {}}, "hypotheses": {}, "frozen": {}, "quanta": {}}
    result, report, _ = _court(state, [_declared(twenty_five, 1, second, observed=5),
                                       _declared(twenty, 1, first, observed=1)])
    current = [entry["record"]["value"] for entry in result["versions"].values() if entry["known_until"] is None]
    assert current == [25] and report["corrections"][0]["statement"] == twenty["id"]


def _recorded(view, reason=None):
    """A memory session's resolution of its statements: ``view`` for ``stm_1``, nothing for a program's card."""
    return lambda identity, at, known_as_of: None if identity != "stm_1" else (view, reason)


def test_a_statement_of_memory_is_admitted_as_memory_recorded_it():
    recorded = _candidate(text="basic monthly_price 20")
    decide = lambda candidate, view, reason=None: admit(
        [candidate], {**CLAIM}, hypothesis_of={"hyp_1": {
            "status": "confirmed", "aspect": "content", "subject": "basic", "statement": {"monthly_price": 20},
            "scope": "catalog", "conditions": {}, "source_ref": "ev-1",
            "source": {"tool": "catalog_entry", "name": "shop:catalog", "ref": "ev-1"}}}.get,
        recorded=_recorded(view, reason))["checked"][0]["reasons"]
    assert decide(recorded, recorded) == []
    # A copy's own validity or currency decides nothing; what memory recorded does.
    assert decide({**recorded, "valid_until": None, "freshness": "current"}, None, "outside_validity") == [
        "outside_validity"]
    assert decide({**recorded, "freshness": "unknown"}, recorded) == []
    # A copy that changes what the statement states is not the statement.
    assert "candidate_not_as_recorded" in decide({**recorded, "conditions": {"region": "eu"}}, recorded)
    assert decide(recorded, None, "statement_not_recorded") == ["statement_not_recorded"]
    # A program's own card is no statement of memory: it is judged as given.
    assert decide({**recorded, "id": "card-1"}, None, "statement_not_recorded") == []


class _Catalog:
    """The catalog's answers, in turn."""

    def __init__(self, prices):
        self.prices = list(prices)

    def call(self, contract, args):
        return "ok", {"ok": True, "price": self.prices.pop(0)}


def _gateway(root, prices):
    configuration = parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1", "advisor": None, "scorer": None, "element": "shop",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "shop", "argv": ["x"]}],
                  "tools": [{"name": "catalog_entry", "server": "shop", "descriptor_sha256": "0" * 64,
                             "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                             "source": "catalog:ops", "contract": {}}],
                  "provenance": {"catalog:ops": {"ancestors": []}}}})
    return configuration, Gateway(root, configuration.tools, executor="contract", transport=_Catalog(prices))


def _read(gateway, ordinal):
    request = {"tool": "catalog_entry", "args": SOURCE["args"], "run_id": "run-1", "ordinal": ordinal,
               "episode": "read", "op_scope": "read", "path": "slow"}
    return {"type": "external_action", "request": request, "outcome": gateway.invoke(request)}


def test_a_statement_is_ordered_by_the_observation_it_was_read_from(tmp_path):
    configuration, gateway = _gateway(tmp_path, [20, 25, 20, 20])
    reads = [_read(gateway, ordinal) for ordinal in range(1, 5)]  # 20, 25, 20, 20: sequences 1..4.
    twenty = reads[2]["outcome"]["ref"]
    declared = {"type": "knowledge_declared", "statement": _statement(20, "2026-01-01", ref=twenty["evidence"]),
                "vector": None}
    unread = {"type": "knowledge_declared", "statement": _statement(99, "2026-01-01", ref="ev-none"), "vector": None}
    # The last reading of 20 before the declaration orders it; the same answer read after it does not.
    history = [reads[0], reads[1], reads[2], declared, reads[3], unread]
    facts = read_session({"run_id": "run-1", "history": history, "from": 0, "to": len(history)}, configuration,
                         gateway, gateway.records())
    assert facts.observed == {3: twenty["gw_seq"]} and twenty["gw_seq"] != reads[0]["outcome"]["ref"]["gw_seq"]
    assert facts.problems == ["run-1@5: a statement read from no recorded observation"]


def test_a_forget_ends_every_period_before_it():
    twenty = _statement(20, "2026-01-01", ref="ev-20")
    mark = {"tombstone": "tmb_1", "window": 4}
    entry = {**_entry(twenty, known_from=6), "earlier_known": [
        {"from": 1, "until": 2, "corrected_by": "stm_25", "withdrawn": None},
        {"from": 3, "until": 6, "corrected_by": None, "withdrawn": mark}]}
    # Held, corrected away, held again, forgotten, read again: only the reading after the forget answers.
    assert [held_period(entry, window) for window in (1, 3, 5)] == [None, None, None]
    assert held_period(entry, 6) == held_period(entry, None) == {"from": 6, "until": None}


def test_admission_resolves_a_statement_in_the_window_memory_held_the_copy_in():
    asked = []

    def recorded(identity, at, known_as_of):
        asked.append(known_as_of)
        return None
    for held in ({"known_from": 1, "known_until": 2}, {"known_from": 4, "known_until": None}):
        admit([_candidate(**held)], {**CLAIM}, recorded=recorded)
    assert asked == [1, None]  # A historical copy is resolved in its period; a current one now.


def test_the_record_resolved_is_the_statement_named():
    mirror = {"tool": "mirror_entry", "args": {"plan": "basic"}}
    one, other = _statement(20, "2026-01-01", ref="ev-1"), _statement(99, "2026-01-01", ref="ev-7", source=mirror)
    versions = {record["id"]: _entry(record) for record in (one, other)}  # Both current in one slot: a conflict.
    policy = SimpleNamespace(properties={"monthly_price": STATE})
    for record in (one, other):
        view, reason = recorded_statement(record["id"], versions, policy, valid_at="2026-03-01T00:00:00Z",
                                          known_as_of=None)
        assert (view["id"], view["value"], reason) == (record["id"], record["value"], None)


def test_a_reading_reindexes_only_a_vector_the_index_cannot_compare():
    record = _statement(20, "2026-01-01", ref="ev-20")
    for vector, by, reindexed in (([1.0], "emb-1", False), (None, None, True), ([1.0], "emb-0", True)):
        state = {"window": 1, "knowledge": {"versions": {record["id"]: {**_entry(record, vector=vector),
                                                                        "embedded_by": by}}, "uses": {}},
                 "hypotheses": {}, "frozen": {}, "quanta": {}}
        _, report, _ = _court(state, [{**_declared(record), "vector": [1.0], "embedded_by": "emb-1"}])
        assert len(report["copies"]) == 1 and bool(report["reindexed"]) is reindexed, (vector, by)


def _embedder_configuration(version="v1", descriptor="0" * 64):
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v2", "advisor": None, "scorer": None, "element": "shop",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "knowledge": {"embedder": {"tool": "embed", "version": version}, "properties": {}, "budget": 3},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "model", "argv": ["x"]}],
                  "tools": [{"name": "embed", "server": "model", "descriptor_sha256": descriptor, "role": "reason",
                             "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                             "source": "embed:model", "contract": {}}],
                  "provenance": {"embed:model": {"ancestors": []}}}})


def test_a_vector_names_the_embedder_tool_version_and_contract():
    def embedded_by(configuration):
        return MemorySession.embedded_by(SimpleNamespace(factory=SimpleNamespace(configuration=configuration)))
    base = embedded_by(_embedder_configuration())
    assert embedded_by(_embedder_configuration()) == base
    assert embedded_by(_embedder_configuration(version="v2")) != base
    assert embedded_by(_embedder_configuration(descriptor="1" * 64)) != base
