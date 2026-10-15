"""Fast contract checks, separate from governed replay acceptance."""

from dataclasses import replace
import pytest

from synapse.experiments.gold import behavior as B
from synapse.experiments.gold import replay as R
from synapse.experiments.gold.stage10.context_codec import encode_canonical, decode_canonical
from tests.gold_typed_replay_support import field, numeric_procedure


@pytest.mark.parametrize("value_type,value", [(B.ValueType.INTEGER, 0), (B.ValueType.BOOLEAN, False),
    (B.ValueType.STRING, ""), (B.ValueType.LIST, []), (B.ValueType.RECORD, {})])
def test_empty_values_remain_present_and_distinct(value_type, value):
    fields = (field("argument", value_type),)
    actual = B.bind_contract_values(fields, {"argument": value})
    assert type(actual["argument"]) is type(value)
    assert actual["argument"] == value
    for invalid in ({}, {"argument": None}):
        with pytest.raises(B.BehaviorViolation):
            B.bind_contract_values(fields, invalid)


def test_bool_is_not_an_integer_and_unknown_fields_are_rejected():
    fields = (field("argument"),)
    for values in ({"argument": False}, {"argument": 1, "extra": 3}, {"argument": 2**53}):
        with pytest.raises(B.BehaviorViolation):
            B.bind_contract_values(fields, values)


def test_optional_absence_null_and_default_are_not_substituted():
    absent = replace(field("absent"), absence_policy=B.AbsencePolicy.OPTIONAL_ABSENT_ALLOWED)
    nullable = replace(field("nullable"), absence_policy=B.AbsencePolicy.OPTIONAL_NULL_ALLOWED)
    default = field("default", B.ValueType.LIST, default=[1, 2])
    actual = B.bind_contract_values((absent, nullable, default), {"nullable": None})
    assert actual == {"nullable": None, "default": [1, 2]}
    actual["default"].append(9)
    assert default.default.value == [1, 2]
    with pytest.raises(B.BehaviorViolation):
        B.bind_contract_values((nullable,), {})
    with pytest.raises(B.BehaviorViolation):
        B.bind_contract_values((absent,), {"absent": None})


def test_unknown_is_absent_and_cannot_be_silently_supplied_as_false():
    unknown = replace(field("unknown", B.ValueType.BOOLEAN), absence_policy=B.AbsencePolicy.UNKNOWN,
        absence_detail=B.AbsenceDetail(B.AbsenceDetailKind.UNKNOWN_REASON,
            reason_code=B.AbsenceReasonCode.UNKNOWN_AT_CAPTURE))
    assert B.bind_contract_values((unknown,), {}) == {}
    for substitute in (False, None, 0, "UNKNOWN"):
        with pytest.raises(B.BehaviorViolation):
            B.bind_contract_values((unknown,), {"unknown": substitute})


def test_invocation_copies_inputs_and_recomputes_bound_contract():
    unit = numeric_procedure()
    supplied = {"left": 9, "right": 4}
    raw = B.create_behavior_invocation(unit, supplied)
    supplied["left"] = 200
    assert B.inspect_behavior_invocation(raw, unit=unit)["bound_values"] == {"left": 9, "right": 4}
    payload = decode_canonical(raw)
    payload["bound_values"]["left"] = 200
    with pytest.raises(B.BehaviorViolation):
        B.inspect_behavior_invocation(encode_canonical(payload), unit=unit)
    with pytest.raises(B.BehaviorViolation):
        B.inspect_behavior_invocation(raw, unit=numeric_procedure(right=5))


def test_required_procedure_inputs_are_checked_before_admission():
    unit = numeric_procedure(required=True)
    with pytest.raises(B.BehaviorViolation) as error:
        B.create_behavior_invocation(unit, {})
    assert error.value.failure_code is B.BehaviorFailureCode.MISSING_REQUIRED_FIELD


def test_historical_profile_cannot_acquire_invocation_semantics():
    from tests.stage4_gold_replay_support import pure_behavior, admitted_subject
    unit, _ = pure_behavior()
    with pytest.raises(R.ReplayViolation):
        R.replay_subject(subject_ref=admitted_subject(unit), unit=unit, inputs={})


@pytest.mark.parametrize("changed", [
    {"expected_transition_ids": ("fixed-transition",)},
    {"expected_observation_ids": ("claimed-observation",)},
    {"expected_activity_ids": ("external-activity",)},
    {"allowed_result_classes": (B.ReplayResultClass.REJECTED,)},
])
def test_input_dependent_profile_cannot_inherit_a_fixed_path_or_external_history(changed):
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2)
    with pytest.raises(B.BehaviorViolation):
        replace(unit.core.replay_contract, **changed)


def test_input_dependent_invocations_share_code_but_never_input_identity():
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2)
    first = B.create_behavior_invocation(unit, {"left": 7, "right": 1})
    second = B.create_behavior_invocation(unit, {"left": -3, "right": 11})
    assert first != second
    assert (decode_canonical(first)["behavior_content_key"]
            == decode_canonical(second)["behavior_content_key"] == unit.content_key.value)
