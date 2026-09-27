"""Contract of semantic knowledge over plain data (refinement §15).

The rules the heavy scenarios rely on, on the subsystem's pure functions: the
timeline's resolution at a valid time as known at a window (inertia, a late
report, a declared end, bounded and event currency, an undeclared property,
disagreeing versions), reciprocal rank fusion, the court's fold of
declarations (copy, repetition, correction with the revision of dependent
hypotheses and habits, conflict) and admission's structured checks.
"""
from __future__ import annotations

from types import SimpleNamespace

from synapse.memory_consolidation.court.knowledge import knowledge_stage
from synapse.memory_consolidation.knowledge.search import fuse, semantic_ranking
from synapse.memory_consolidation.knowledge.statements import declare, slot, source_identity
from synapse.memory_consolidation.knowledge.timeline import resolve
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


def _court(state, declared, uses=(), habits=None):
    context = SimpleNamespace(state=state, window=state["window"] + 1, report={},
                              draft={"knowledge": {"declared": declared, "uses": list(uses)}})
    configuration = SimpleNamespace(tools=SimpleNamespace(tools={"catalog_entry": SimpleNamespace(
        source="catalog:ops")}))
    forced = {}
    result = knowledge_stage(context, habits or {}, forced, configuration)
    return result, context.report["knowledge"], forced


def _declared(record, position=1, run_id="run-b"):
    return {"run_id": run_id, "position": position, "statement": record, "vector": None}


def test_the_court_folds_copies_corrections_and_conflicts_and_revises_what_depended_on_a_correction():
    old = _statement(20, "2026-01-01", ref="ev-1")
    hypothesis = {"record": {"source": SOURCE, "check": {"tool": "billing_quote", "args": {"plan": "basic"}}},
                  "source_ref": "ev-1", "status": "confirmed", "reason": "check_agrees", "window": 1}
    checked = {"record": {"source": {"tool": "notes", "args": {}}, "check": SOURCE}, "source_ref": "ev-0",
               "status": "confirmed", "reason": "check_agrees", "window": 1}
    state = {"window": 1, "knowledge": {"versions": {old["id"]: _entry(old)}, "uses": {old["id"]: ["run-a"]}},
             "hypotheses": {"hyp_read": hypothesis, "hyp_checked": checked},
             "frozen": {"hab_a": {"habit": {"born_from": {"episodes": ["q1"]}}},
                        "hab_b": {"habit": {"born_from": {"episodes": ["q2"]}}}},
             "quanta": {"q1": {"replay_ref": {"run_id": "run-a"}}, "q2": {"replay_ref": {"run_id": "run-z"}}}}
    habits = {"hab_a": {"state": "active", "superseded_by": None}, "hab_b": {"state": "active", "superseded_by": None}}

    # The very same statement, and the same source read again saying the same: copies.
    same = _statement(20, "2026-01-01", ref="ev-5")
    result, report, forced = _court(state, [_declared(old), _declared(same, 2)], habits=habits)
    assert result["versions"] == {} and [item["of"] for item in report["copies"]] == [old["id"], old["id"]]

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
                       "source_ref": "ev-1", **(hypothesis or {})}}
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
