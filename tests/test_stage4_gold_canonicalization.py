from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.behavior import BehaviorCore, create_behavior_unit


_CANON_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "canonicalization_vectors_v1.json"
_BEHAVIOR_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _fixture() -> dict[str, object]:
    return json.loads(_CANON_FIXTURE.read_text(encoding="utf-8"))


def _valid_unit():
    source = json.loads(_BEHAVIOR_FIXTURE.read_text(encoding="utf-8"))["vectors"][0]["core"]
    core = BehaviorCore.from_dict(copy.deepcopy(source))
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


def _canonical(value: object) -> bytes:
    return canon.canonicalize_stage4_payload(
        value,
        profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=canon.STABLE_CANONICAL_CODEC_ID,
    )


def test_s4_p2_acc_canon_01_insertion_order_unicode_and_numeric_semantics_match_literals() -> None:
    vector = _fixture()["canonical_equivalence"]
    left = _canonical(vector["left"])
    right = _canonical(vector["right_insertion_order"])
    assert left == right
    assert left.hex() == vector["expected_hex"]

    numeric = _fixture()["numeric"]
    assert _canonical(numeric["safe_min"])
    assert _canonical(numeric["safe_max"])
    assert _canonical(1).hex() == numeric["int_one_hex"]
    assert _canonical(1.0).hex() == numeric["float_one_hex"]
    assert _canonical(1) != _canonical(1.0)
    for unsafe in (numeric["unsafe_low"], numeric["unsafe_high"]):
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            _canonical(unsafe)
        assert exc.value.failure_code is canon.CanonicalizationFailureCode.INVALID_CANONICAL_VALUE


def test_s4_p2_acc_canon_02_strict_utf8_json_round_trip_and_base64url() -> None:
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        _canonical("\ud800")
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.INVALID_UTF8
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        canon.decode_stage4_canonical_bytes(
            b"\xff",
            profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
            codec_id=canon.STABLE_CANONICAL_CODEC_ID,
        )
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.INVALID_UTF8

    for raw in (b'{"a":1,"a":2}', b'{ "a":1}', b'{"a":NaN}', b'{"a":1} trailing'):
        with pytest.raises(canon.CanonicalizationViolation):
            canon.decode_stage4_canonical_bytes(
                raw,
                profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
                codec_id=canon.STABLE_CANONICAL_CODEC_ID,
            )

    vector = _fixture()["base64url"]
    payload = bytes.fromhex(vector["payload_hex"])
    assert canon.canonical_base64url(payload) == vector["canonical_text"]
    assert canon.decode_canonical_base64url(vector["canonical_text"]) == payload
    for alias in vector["invalid"]:
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            canon.decode_canonical_base64url(alias)
        assert exc.value.failure_code is canon.CanonicalizationFailureCode.INVALID_BASE64URL


def test_s4_p2_acc_canon_03_unknown_profile_does_not_fallback() -> None:
    for kwargs, failure in (
        ({"profile_id": "synapse.stage4.gold.canonical-profile/v99", "codec_id": canon.STABLE_CANONICAL_CODEC_ID}, canon.CanonicalizationFailureCode.UNKNOWN_PROFILE),
        ({"profile_id": canon.STAGE4_CANONICAL_PROFILE_V1, "codec_id": "alpha3g.local-json.v1"}, canon.CanonicalizationFailureCode.UNKNOWN_CODEC),
    ):
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            canon.canonicalize_stage4_payload({}, **kwargs)
        assert exc.value.failure_code is failure

    unit = _valid_unit()
    base = dict(
        canonical_behavior_core_bytes=unit.canonical_core.canonical_bytes,
        profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=canon.STABLE_CANONICAL_CODEC_ID,
        core_schema_id=canon.BEHAVIOR_CORE_SCHEMA_V1,
        ir_schema_id=canon.CANONICAL_PROGRAM_IR_V1,
        language_version=unit.canonical_core.language_version,
        compiler_adapter_profile=canon.COMPILER_ADAPTER_PROFILE_V1,
    )
    for field, invalid, failure in (
        ("core_schema_id", "synapse.stage4.gold.behavior-core/v99", canon.CanonicalizationFailureCode.UNKNOWN_SCHEMA),
        ("ir_schema_id", "synapse.stage4.gold.canonical-program-ir/v99", canon.CanonicalizationFailureCode.UNKNOWN_SCHEMA),
        ("language_version", "2.2.0-unknown", canon.CanonicalizationFailureCode.UNKNOWN_LANGUAGE),
        ("compiler_adapter_profile", "synapse.stage4.gold.cognitive-compiler-adapter/v99", canon.CanonicalizationFailureCode.UNKNOWN_COMPILER),
    ):
        changed = {**base, field: invalid}
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            canon.compute_content_key(**changed)
        assert exc.value.failure_code is failure


def test_s4_p2_acc_canon_04_data_only_purity_and_no_custom_serialization_hook() -> None:
    class MappingSubclass(dict):
        def items(self):
            raise AssertionError("custom mapping hook must not execute")

    class Custom:
        def to_dict(self):
            raise AssertionError("custom serialization hook must not execute")

    original = {"b": [2, 1], "a": {"x": True}}
    snapshot = copy.deepcopy(original)
    first = _canonical(original)
    second = _canonical(original)
    assert first == second
    assert original == snapshot
    for value in (MappingSubclass(a=1), Custom(), lambda: None, bytearray(b"x"), memoryview(b"x"), {"x"}):
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            _canonical(value)
        assert exc.value.failure_code is canon.CanonicalizationFailureCode.INVALID_CANONICAL_VALUE


def test_s4_p2_acc_canon_05_closed_ir_round_trip_size_and_unsupported_nodes() -> None:
    vector = _fixture()["program_ir"]
    encoded = canon.canonical_program_ir_bytes(vector["value"])
    assert encoded.hex() == vector["canonical_hex"]
    assert canon.decode_canonical_program_ir(encoded) == vector["value"]

    noncanonical = b'{ "schema_version":"synapse.stage4.gold.canonical-program-ir/v1","program":{"node":"program","statements":[]}}'
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        canon.decode_canonical_program_ir(noncanonical)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.NON_CANONICAL_BYTES

    for invalid in (
        {"schema_version": canon.CANONICAL_PROGRAM_IR_V1, "program": {"node": "program", "statements": [{"node": "call", "callee": "host"}]}},
        {"schema_version": canon.CANONICAL_PROGRAM_IR_V1, "program": {"node": "program", "statements": [{"node": "return", "value": {"node": "literal", "value_kind": "STRING", "value": "python"}}]}},
        "return 1",
    ):
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            canon.canonical_program_ir_bytes(invalid)
        assert exc.value.failure_code in {canon.CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, canon.CanonicalizationFailureCode.TYPE_MISMATCH}

    huge = copy.deepcopy(vector["value"])
    huge["program"]["statements"] = [
        {"node": "expr_stmt", "expression": {"node": "literal", "value_kind": "INT", "value": index}}
        for index in range(20_000)
    ]
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        canon.canonical_program_ir_bytes(huge)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.IR_TOO_LARGE


def test_s4_p2_acc_content_01_content_key_preimage_and_consumer_recomputation_match_literals() -> None:
    unit = _valid_unit()
    vector = _fixture()["content_identity"]
    preimage = canon.content_key_preimage(
        canonical_behavior_core_bytes=unit.canonical_core.canonical_bytes,
        profile_id=unit.canonical_core.profile_id,
        codec_id=unit.canonical_core.codec_id,
        core_schema_id=unit.canonical_core.core_schema_id,
        ir_schema_id=unit.canonical_core.ir_schema_id,
        language_version=unit.canonical_core.language_version,
        compiler_adapter_profile=unit.canonical_core.compiler_adapter_profile,
    )
    assert preimage.hex() == vector["preimage_hex"]
    assert unit.canonical_core.payload_sha256 == vector["payload_sha256"]
    assert unit.content_key.value == vector["content_key"]
    assert canon.content_key_from_dict(
        unit.content_key.to_dict(),
        canonical_behavior_core_bytes=unit.canonical_core.canonical_bytes,
        profile_id=unit.canonical_core.profile_id,
        codec_id=unit.canonical_core.codec_id,
        core_schema_id=unit.canonical_core.core_schema_id,
        ir_schema_id=unit.canonical_core.ir_schema_id,
        language_version=unit.canonical_core.language_version,
        compiler_adapter_profile=unit.canonical_core.compiler_adapter_profile,
    ).value == unit.content_key.value

    claimed = unit.content_key.to_dict()
    claimed["value"] = canon.CONTENT_KEY_TEXT_PREFIX + "f" * 64
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        canon.content_key_from_dict(
            claimed,
            canonical_behavior_core_bytes=unit.canonical_core.canonical_bytes,
            profile_id=unit.canonical_core.profile_id,
            codec_id=unit.canonical_core.codec_id,
            core_schema_id=unit.canonical_core.core_schema_id,
            ir_schema_id=unit.canonical_core.ir_schema_id,
            language_version=unit.canonical_core.language_version,
            compiler_adapter_profile=unit.canonical_core.compiler_adapter_profile,
        )
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.CONTENT_KEY_MISMATCH


def test_s4_p2_acc_migration_01_non_migratable_is_only_supported_relation() -> None:
    unit = _valid_unit()
    old_key = unit.content_key.value
    relation = canon.create_non_migratable_relation(unit.canonical_core, reason_code=canon.MigrationReasonCode.NO_APPROVED_TARGET)
    assert relation.to_dict() == _fixture()["non_migratable"]["relation"]
    assert relation.old_endpoint.content_key.value == old_key
    assert relation.new_endpoint is None
    assert relation.migrator_id is None
    assert unit.content_key.value == old_key
    assert not ({"equivalent", "admitted", "consumable", "authoritative_key"} & set(relation.to_dict()))
    assert canon.migration_relation_from_dict(relation.to_dict(), old_core=unit.canonical_core).to_dict() == relation.to_dict()

    changed_payload = unit.core.to_dict()
    changed_payload["output_contract"]["fields"][0]["name"] = "new_result"
    core = BehaviorCore.from_dict(changed_payload)
    changed = create_behavior_unit(
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
    for strategy in (canon.MigrationStrategy.RECANONICALIZE_REVERIFY, canon.MigrationStrategy.DUAL_KEY_TRANSITION):
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            canon.create_positive_migration_relation(
                old_core=unit.canonical_core,
                new_core=changed.canonical_core,
                strategy=strategy,
                reason_code=canon.MigrationReasonCode.SEMANTIC_MIGRATION_UNPROVEN,
                migrator_id="synapse.stage4.test.migrator/v1",
                reverify_reattest_required=True,
            )
        assert exc.value.failure_code is canon.CanonicalizationFailureCode.MIGRATION_ENDPOINT_NOT_APPROVED

    with pytest.raises(canon.CanonicalizationViolation) as exc:
        canon.create_positive_migration_relation(
            old_core=unit.canonical_core,
            new_core=changed.canonical_core,
            strategy=canon.MigrationStrategy.RECANONICALIZE_REVERIFY,
            reason_code=canon.MigrationReasonCode.SEMANTIC_MIGRATION_UNPROVEN,
            migrator_id="synapse.stage4.test.migrator/v1",
            reverify_reattest_required=False,
        )
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.MIGRATION_INVARIANT


def test_s4_p2_followup_consumption_01_result_is_sealed_and_pair_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _valid_unit()
    matching = canon.compare_canonical_content(primary.canonical_core, primary.canonical_core)

    changed_payload = primary.core.to_dict()
    changed_payload["output_contract"]["fields"][0]["name"] = "different_result"
    changed_core = BehaviorCore.from_dict(changed_payload)
    changed = create_behavior_unit(
        behavior_kind=changed_core.behavior_kind,
        canonical_program=changed_core.canonical_program,
        input_contract=changed_core.input_contract,
        output_contract=changed_core.output_contract,
        capability_requirements=changed_core.capability_requirements,
        replay_contract=changed_core.replay_contract,
        verification_contract=changed_core.verification_contract,
        binding_refs=changed_core.binding_refs,
        source_evidence_refs=changed_core.source_evidence_refs,
        artifact_refs=changed_core.artifact_refs,
    )
    distinct = canon.compare_canonical_content(primary.canonical_core, changed.canonical_core)

    monkeypatch.setattr(canon, "_content_digest", lambda preimage: primary.content_key.digest_sha256)
    collision_core = BehaviorCore.from_dict(changed_payload)
    collision_unit = create_behavior_unit(
        behavior_kind=collision_core.behavior_kind,
        canonical_program=collision_core.canonical_program,
        input_contract=collision_core.input_contract,
        output_contract=collision_core.output_contract,
        capability_requirements=collision_core.capability_requirements,
        replay_contract=collision_core.replay_contract,
        verification_contract=collision_core.verification_contract,
        binding_refs=collision_core.binding_refs,
        source_evidence_refs=collision_core.source_evidence_refs,
        artifact_refs=collision_core.artifact_refs,
    )
    collision = canon.compare_canonical_content(primary.canonical_core, collision_unit.canonical_core)

    incompatible_core = _valid_unit().canonical_core
    object.__setattr__(incompatible_core, "profile_id", "synapse.stage4.gold.canonical-profile/v99")
    incompatible = canon.compare_canonical_content(primary.canonical_core, incompatible_core)

    allowed = (matching, distinct, collision, incompatible)
    assert [(item.status, item.reason) for item in allowed] == [
        (canon.ContentValidationStatus.USABLE, canon.ContentValidationReason.MATCHING_CONTENT),
        (canon.ContentValidationStatus.USABLE, canon.ContentValidationReason.DISTINCT_CONTENT),
        (
            canon.ContentValidationStatus.QUARANTINED,
            canon.ContentValidationReason.CONTENT_COLLISION_OR_CORRUPTION,
        ),
        (canon.ContentValidationStatus.INCOMPATIBLE, canon.ContentValidationReason.UNSUPPORTED_ENDPOINT),
    ]
    for usable in (matching, distinct):
        assert usable.consumable
        usable.require_consumable()
    for degraded in (collision, incompatible):
        assert not degraded.consumable
        with pytest.raises(canon.CanonicalizationViolation) as exc:
            degraded.require_consumable()
        assert exc.value.failure_code is canon.CanonicalizationFailureCode.DEGRADED_CONTENT

    with pytest.raises(TypeError):
        canon.ContentValidationResult(
            canon.ContentValidationStatus.USABLE,
            canon.ContentValidationReason.CONTENT_COLLISION_OR_CORRUPTION,
        )
    with pytest.raises(TypeError):
        replace(matching, reason=canon.ContentValidationReason.UNSUPPORTED_ENDPOINT)

    unsealed = object.__new__(canon.ContentValidationResult)
    object.__setattr__(unsealed, "status", canon.ContentValidationStatus.USABLE)
    object.__setattr__(unsealed, "reason", canon.ContentValidationReason.MATCHING_CONTENT)
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        unsealed.require_consumable()
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED

    inconsistent = object.__new__(canon.ContentValidationResult)
    object.__setattr__(inconsistent, "status", canon.ContentValidationStatus.USABLE)
    object.__setattr__(inconsistent, "reason", canon.ContentValidationReason.UNSUPPORTED_ENDPOINT)
    object.__setattr__(inconsistent, "_trusted_seal", matching._trusted_seal)
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        inconsistent.require_consumable()
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED
