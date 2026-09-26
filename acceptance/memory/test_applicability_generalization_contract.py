"""Contract of applicability generalization over plain data (refinement §12, D1).

The rules the heavy scenarios rely on, checked directly on the court's pure
functions: order independence and canonical equivalence, boundaries drawn
from contrasts, near and far contrasts, the typed presence a body needs,
sound coverage for value sets, ranges and kinds, and widening that extends
but never removes a condition nor crosses a recorded contrast.
"""
from __future__ import annotations

import itertools

import pytest

from synapse.habit_triggers import TypedTrigger, typed_condition_holds
from synapse.memory_consolidation.learning.applicability import BoundaryUnavailable, generalize, widen
from synapse.memory_consolidation.learning.triggers import covers, make_trigger
from synapse.memory_points import TypedCondition

BASE = {"tool": "migrate", "op_err": "LOCKED"}


def _episode(name, **fields):
    return {"qid": f"q-{name}", "steps": [0, 3], "run_id": f"run-{name}", "event_id": f"ev-{name}",
            "context": {"event_type": "external_error", "fields": {**BASE, **fields}, "labels": ["migrate"]}}


POSITIVES = [_episode("orders", readers=0, lag=5, tenant="acme"),
             _episode("billing", readers=0, lag=40, tenant="globex"),
             _episode("ledger", readers=0, lag=120, tenant="initech")]
CONTRASTS = [_episode("sessions", readers=2, lag=40, tenant="globex"),
             _episode("archive", readers=0, lag=900, tenant="acme")]


def _trigger(condition, explanation):
    return make_trigger(condition, template="t", born_from="con_x", source_episodes=[], applicability=explanation)


def _when(condition):
    return {(item["field"], item["op"]): item["value"] for item in condition["when"]}


def test_every_order_of_basis_and_contrasts_gives_one_trigger():
    expected = None
    for positives in itertools.permutations(POSITIVES):
        for contrasts in itertools.permutations(CONTRASTS):
            condition, explanation, unexplained = generalize(list(positives), list(contrasts), ["tenant"])
            trigger = _trigger(condition, explanation)
            assert unexplained == []
            expected = expected or trigger["id"]
            assert trigger["id"] == expected


def test_equivalent_canonical_forms_are_one_record_and_a_significant_value_is_another():
    condition, explanation, _ = generalize(POSITIVES, CONTRASTS)
    shuffled = {**condition, "when": list(reversed(condition["when"]))}
    assert _trigger(shuffled, explanation)["id"] == _trigger(condition, explanation)["id"]
    in_set = {**condition, "when": [{"field": "tenant", "op": "in", "value": ["b", "a"]}]}
    same_set = {**condition, "when": [{"field": "tenant", "op": "in", "value": ["a", "b", "a"]}]}
    assert _trigger(in_set, explanation)["id"] == _trigger(same_set, explanation)["id"]
    other = {**condition, "when": [dict(item, value=1) if item["field"] == "readers" else item
                                   for item in condition["when"]]}
    assert _trigger(other, explanation)["id"] != _trigger(condition, explanation)["id"]


def test_near_contrasts_make_fields_essential_and_variation_makes_them_irrelevant():
    condition, explanation, unexplained = generalize(POSITIVES, CONTRASTS)
    assert unexplained == []
    assert _when(condition)[("readers", "==")] == 0 and _when(condition)[("lag", "<=")] == 120
    assert ("tenant", "in") not in _when(condition) and ("tenant", "==") not in _when(condition)
    assert [entry["field"] for entry in explanation["essential"]] == ["lag", "readers"]
    assert explanation["irrelevant"] == [{"field": "tenant", "values": 3}]
    assert explanation["unproven"] == ["op_err", "tool"] and explanation["bounded"] == []


@pytest.mark.parametrize("value, expected", [(900, ("lag", "<=", 120)), (1, ("lag", ">=", 5)),
                                             (60, ("lag", "in", [5, 40, 120]))])
def test_a_contrast_inside_the_base_draws_the_boundary_from_the_positives(value, expected):
    condition, explanation, _ = generalize(POSITIVES, [_episode("c", readers=0, lag=value, tenant="acme")])
    field, op, bound = expected
    assert _when(condition)[(field, op)] == bound
    assert explanation["essential"][0]["contrasts"][0]["value"] == value


def test_a_far_contrast_bounds_every_discriminator_and_proves_none():
    far = _episode("far", readers=0, lag=900, tenant="umbrella")
    condition, explanation, _ = generalize(POSITIVES, [far])
    assert _when(condition)[("lag", "<=")] == 120
    assert _when(condition)[("tenant", "in")] == ["acme", "globex", "initech"]
    assert explanation["essential"] == [] and explanation["bounded"] == ["lag", "tenant"]


def test_a_failure_inside_the_positives_is_an_unexplained_contradiction():
    inside = _episode("inside", readers=0, lag=40, tenant="globex")
    _, _, unexplained = generalize(POSITIVES, [inside])
    assert [ref["event_id"] for ref in unexplained] == ["ev-inside"]


def test_a_field_the_body_reads_is_required_with_its_one_kind():
    condition, explanation, _ = generalize(POSITIVES, [], ["tenant"])
    assert _when(condition)[("tenant", "is")] == "string"
    assert explanation["required"] == [{"field": "tenant", "kind": "string"}]
    mixed = [*POSITIVES, _episode("mixed", readers=0, lag=7, tenant=7)]
    with pytest.raises(BoundaryUnavailable):
        generalize(mixed, [], ["tenant"])


@pytest.mark.parametrize("fields, holds", [({"mode": "wal"}, True), ({"mode": 1}, False), ({}, None)])
def test_is_holds_only_for_a_present_value_of_its_kind(fields, holds):
    assert typed_condition_holds(TypedCondition("mode", "is", "string"), fields) is holds
    trigger = TypedTrigger("trg", ("external_error",), (), (TypedCondition("mode", "is", "string"),))
    status, failed = trigger.match({"type": "external_error", "fields": fields})
    assert status == ("applicable" if holds else "near_miss")
    if holds is not True:
        assert failed["field"] == "mode"


def test_coverage_is_sound_for_value_sets_ranges_and_kinds():
    def area(*when):
        return {"event_types": ["external_error"], "context": "any", "when": list(when), "not_when": []}
    assert covers(area({"field": "lag", "op": "<=", "value": 300}), area({"field": "lag", "op": "<=", "value": 120}))
    assert not covers(area({"field": "lag", "op": "<=", "value": 120}), area({"field": "lag", "op": "<=", "value": 300}))
    assert covers(area({"field": "t", "op": "in", "value": ["a", "b", "c"]}), area({"field": "t", "op": "in",
                                                                                    "value": ["a", "b"]}))
    assert not covers(area({"field": "t", "op": "in", "value": ["a"]}), area({"field": "t", "op": "in",
                                                                              "value": ["a", "b"]}))
    assert covers(area({"field": "t", "op": "is", "value": "string"}), area({"field": "t", "op": "==", "value": "a"}))
    assert not covers(area({"field": "t", "op": "is", "value": "number"}), area({"field": "t", "op": "==",
                                                                                "value": "a"}))


def test_widening_extends_a_condition_and_never_crosses_a_contrast():
    condition, explanation, _ = generalize(POSITIVES, CONTRASTS)
    trigger = _trigger(condition, explanation)
    failed = {"field": "lag", "op": "<=", "value": 120}
    widened, _ = widen(trigger, failed, [300, 300, 250])
    assert _when(widened)[("lag", "<=")] == 300 and _when(widened)[("readers", "==")] == 0
    with pytest.raises(BoundaryUnavailable):
        widen(trigger, failed, [1000, 1000, 1000])  # 900 failed: the extension would admit it.
    readers = {"field": "readers", "op": "==", "value": 0}
    with pytest.raises(BoundaryUnavailable):
        widen(trigger, readers, [2])
    tenant_bound = _trigger({**condition, "when": [*condition["when"],
                                                   {"field": "tenant", "op": "in", "value": ["acme"]}]}, explanation)
    extended, _ = widen(tenant_bound, {"field": "tenant", "op": "in", "value": ["acme"]}, ["globex"])
    assert _when(extended)[("tenant", "in")] == ["acme", "globex"]
