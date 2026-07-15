from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.behavior import (
    AbsenceDetail,
    AbsenceDetailKind,
    AbsencePolicy,
    BehaviorCore,
    BehaviorFailureCode,
    BehaviorKind,
    BehaviorViolation,
    ContractField,
    DefaultKind,
    DefaultValue,
    InputContract,
    SynapseBehaviorUnit,
    ValueType,
    behavior_blob_from_dict,
    behavior_manifest_from_dict,
    behavior_unit_from_dict,
    compile_behavior_unit,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
    validate_behavior_blob,
    validate_behavior_unit,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _vectors() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _unit_from_core(core_payload: dict[str, object]) -> SynapseBehaviorUnit:
    core = BehaviorCore.from_dict(copy.deepcopy(core_payload))
    return create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )


def make_valid_unit() -> SynapseBehaviorUnit:
    vector = _vectors()["vectors"][0]
    return _unit_from_core(vector["core"])


def _failure(exc: pytest.ExceptionInfo[BaseException]) -> str:
    return exc.value.failure_code.value


def test_s4_p2_acc_core_01_unit_core_blob_manifest_round_trip_and_literal_vector() -> None:
    vector = _vectors()["vectors"][0]
    unit = _unit_from_core(vector["core"])
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=None)

    assert BehaviorCore.from_dict(unit.core.to_dict()).to_dict() == unit.core.to_dict()
    assert behavior_unit_from_dict(unit.to_dict()).to_dict() == unit.to_dict()
    assert behavior_blob_from_dict(blob.to_dict(unit=unit), unit=unit).to_dict(unit=unit) == blob.to_dict(unit=unit)
    assert behavior_manifest_from_dict(
        manifest.to_dict(unit=unit, blob=blob),
        unit=unit,
        blob=blob,
        compiler_binding=None,
    ).to_dict(unit=unit, blob=blob) == manifest.to_dict(unit=unit, blob=blob)

    assert unit.canonical_core.canonical_bytes.hex() == vector["canonical_core_hex"]
    assert canon.canonical_base64url(unit.canonical_core.canonical_bytes) == vector["canonical_core_base64url"]
    assert unit.canonical_core.payload_sha256 == vector["payload_sha256"]
    assert unit.content_key.value == vector["content_key"]
    assert unit.to_dict() == vector["unit"]
    assert blob.to_dict(unit=unit) == vector["blob"]
    assert manifest.to_dict(unit=unit, blob=blob) == vector["manifest_without_binding"]


def test_s4_p2_acc_contract_01_strict_fields_conditions_and_absence_variants() -> None:
    unit = make_valid_unit()
    input_dict = unit.core.input_contract.to_dict()
    assert [field["name"] for field in input_dict["fields"]] == ["repository"]
    assert [ref["condition_id"] for ref in input_dict["preconditions"]] == ["repository-present"]
    variants = [ContractField.from_dict(item).to_dict() for item in _vectors()["absence_variants"]]
    policies = {field["name"]: (field["absence_policy"], field["default"]["kind"], field["absence_detail"]["kind"]) for field in variants}
    assert policies == {
        "defaulted_null": ("DEFAULTED", "NULL", "NONE"),
        "defaulted_value": ("DEFAULTED", "VALUE", "NONE"),
        "not_applicable": ("NOT_APPLICABLE", "ABSENT", "NOT_APPLICABLE_DETAIL"),
        "optional_absent": ("OPTIONAL_ABSENT_ALLOWED", "ABSENT", "NONE"),
        "optional_null": ("OPTIONAL_NULL_ALLOWED", "ABSENT", "NONE"),
        "redacted": ("REDACTED", "ABSENT", "REDACTION_DETAIL"),
        "required": ("REQUIRED", "ABSENT", "NONE"),
        "unavailable": ("UNAVAILABLE", "ABSENT", "UNAVAILABLE_DETAIL"),
        "unknown": ("UNKNOWN", "ABSENT", "UNKNOWN_REASON"),
    }

    with pytest.raises(BehaviorViolation) as exc:
        ContractField(
            "bad",
            ValueType.STRING,
            AbsencePolicy.UNAVAILABLE,
            DefaultValue(DefaultKind.ABSENT),
            AbsenceDetail(AbsenceDetailKind.NONE),
        )
    assert _failure(exc) == "INVALID_ABSENCE_DETAIL"

    duplicate = unit.core.input_contract.fields[0]
    with pytest.raises(BehaviorViolation) as exc:
        InputContract((duplicate, duplicate), ())
    assert _failure(exc) == "DUPLICATE_FIELD"

    raw = unit.core.to_dict()
    del raw["input_contract"]["preconditions"]
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "MISSING_REQUIRED_FIELD"


def test_s4_p2_acc_authority_01_verification_contract_is_mandatory_and_not_authority() -> None:
    raw = make_valid_unit().core.to_dict()
    raw["verification_contract"] = None
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "MISSING_VERIFICATION_CONTRACT"

    verification = make_valid_unit().core.verification_contract.to_dict()
    assert set(verification) == {
        "profile_id",
        "expected_result_class",
        "expected_claims",
        "evidence_requirements",
        "oracle_requirements",
    }
    assert not ({"verified", "trusted", "admitted", "approver"} & set(verification))


def test_s4_p2_acc_authority_02_rejected_hypothesis_never_becomes_fact() -> None:
    raw = make_valid_unit().core.to_dict()
    raw["behavior_kind"] = _vectors()["rejected_hypothesis"]["behavior_kind"]
    unit = _unit_from_core(raw)
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=None)
    assert unit.core.behavior_kind is BehaviorKind.REJECTED_HYPOTHESIS_GUARD
    assert unit.core.to_dict()["behavior_kind"] == "rejected_hypothesis_guard"
    assert manifest.to_dict(unit=unit, blob=blob)["behavior_kind"] == "rejected_hypothesis_guard"
    assert unit.content_key.value != make_valid_unit().content_key.value


def test_s4_p2_acc_granularity_01_capabilities_composition_raw_payload_and_program_union() -> None:
    raw = make_valid_unit().core.to_dict()
    raw["capability_requirements"] = ["*"]
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "CAPABILITY_WILDCARD"

    raw = make_valid_unit().core.to_dict()
    raw["capability_requirements"] = ["network.read", "network.read"]
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "DUPLICATE_CAPABILITY"

    unit = make_valid_unit()
    statements = unit.core.canonical_program.to_dict()["ir"]["program"]["statements"]
    assert [statement["node"] for statement in statements] == _vectors()["granularity"]["explicit_ordered_statement_nodes"]
    raw = unit.core.to_dict()
    raw["canonical_program"] = _vectors()["granularity"]["invalid_implicit_merge"]
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "INVALID_GRANULARITY"

    raw = unit.core.to_dict()
    raw["transcript"] = "raw dialogue"
    with pytest.raises(BehaviorViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert _failure(exc) == "UNKNOWN_FIELD"

    raw = unit.core.to_dict()
    raw["canonical_program"] = {**raw["canonical_program"], "artifact_ref": _vectors()["program_artifact"]["canonical_program"]["artifact_ref"]}
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        BehaviorCore.from_dict(raw)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.UNKNOWN_FIELD


def test_s4_p2_acc_core_02_program_artifact_is_hash_bound_and_compile_fails_without_io(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = make_valid_unit().core.to_dict()
    raw["canonical_program"] = _vectors()["program_artifact"]["canonical_program"]
    unit = _unit_from_core(raw)
    assert unit.canonical_core.program_form is canon.ProgramForm.ARTIFACT_REF_V1
    assert unit.canonical_core.program_artifact_ref.sha256 == "a" * 64

    def forbidden_io(*args: object, **kwargs: object) -> None:
        raise AssertionError("resolver/I/O must not be called")

    monkeypatch.setattr("builtins.open", forbidden_io)
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        compile_behavior_unit(unit)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.PROGRAM_ARTIFACT_UNAVAILABLE


def test_s4_p2_acc_content_03_blob_substitution_and_collision_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    unit = make_valid_unit()
    blob = create_behavior_blob(unit)
    transport = blob.to_dict(unit=unit)
    transport["canonical_core_base64url"] = canon.canonical_base64url(b"{}")
    with pytest.raises(BehaviorViolation) as exc:
        behavior_blob_from_dict(transport, unit=unit)
    assert _failure(exc) == "BLOB_MISMATCH"

    monkeypatch.setattr(canon, "_content_digest", lambda preimage: "0" * 64)
    first = make_valid_unit()
    changed = first.core.to_dict()
    changed["output_contract"]["fields"][0]["name"] = "different_result"
    second = _unit_from_core(changed)
    collision = canon.compare_canonical_content(first.canonical_core, second.canonical_core)
    assert collision.status is canon.ContentValidationStatus.QUARANTINED
    assert collision.reason is canon.ContentValidationReason.CONTENT_COLLISION_OR_CORRUPTION
    assert not collision.consumable
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        collision.require_consumable()
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.DEGRADED_CONTENT


def test_s4_p2_acc_content_04_incompatible_and_forged_records_cannot_be_consumed() -> None:
    unit = make_valid_unit()
    incompatible = make_valid_unit().canonical_core
    object.__setattr__(incompatible, "profile_id", "synapse.stage4.gold.canonical-profile/v99")
    result = canon.compare_canonical_content(unit.canonical_core, incompatible)
    assert result.status is canon.ContentValidationStatus.INCOMPATIBLE
    assert not result.consumable

    with pytest.raises(TypeError):
        SynapseBehaviorUnit()
    with pytest.raises(TypeError):
        replace(unit, content_key=unit.content_key)
    forged = object.__new__(SynapseBehaviorUnit)
    with pytest.raises(BehaviorViolation) as exc:
        validate_behavior_unit(forged)
    assert _failure(exc) == "TRUSTED_OBJECT_FORGED"

    nested_payload = make_valid_unit().core.to_dict()
    nested_payload["input_contract"]["fields"].append(copy.deepcopy(_vectors()["absence_variants"][-1]))
    nested = _unit_from_core(nested_payload)
    redacted = next(field for field in nested.core.input_contract.fields if field.name == "redacted")
    object.__setattr__(redacted.absence_detail.redaction_authority, "value", " bad actor ")
    with pytest.raises(ValueError):
        validate_behavior_unit(nested)


def test_s4_p2_acc_core_03_manifest_has_no_lifecycle_or_authority_state() -> None:
    unit = make_valid_unit()
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=None).to_dict(unit=unit, blob=blob)
    forbidden = {"admitted", "verified", "trusted", "lifecycle", "task_success", "FULL", "cost", "authority"}
    assert not forbidden.intersection(manifest)
    assert manifest["compiler_binding"] is None
    validate_behavior_blob(blob, unit=unit)
