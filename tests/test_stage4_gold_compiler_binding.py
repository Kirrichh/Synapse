from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.behavior import (
    BehaviorCore,
    BehaviorViolation,
    compile_behavior_unit,
    compiler_binding_from_dict_for_unit,
    compiler_binding_to_dict_for_unit,
    create_behavior_unit,
    validate_behavior_unit,
    validate_compiler_binding_for_unit,
)


_CANON_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "canonicalization_vectors_v1.json"
_BEHAVIOR_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _core_payload() -> dict[str, object]:
    return copy.deepcopy(json.loads(_BEHAVIOR_FIXTURE.read_text(encoding="utf-8"))["vectors"][0]["core"])


def _unit(payload: dict[str, object] | None = None):
    core = BehaviorCore.from_dict(_core_payload() if payload is None else payload)
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


def test_s4_p2_acc_compiler_01_semantic_equivalence_and_difference_bind_key_hash_and_compiler() -> None:
    first = _unit()
    equivalent_payload = _core_payload()
    equivalent_payload["input_contract"]["fields"] = list(reversed(equivalent_payload["input_contract"]["fields"]))
    equivalent_payload["source_evidence_refs"] = list(reversed(equivalent_payload["source_evidence_refs"]))
    equivalent = _unit(equivalent_payload)
    first_binding = compile_behavior_unit(first)
    equivalent_binding = compile_behavior_unit(equivalent)
    assert first.canonical_core.canonical_bytes == equivalent.canonical_core.canonical_bytes
    assert first.content_key.value == equivalent.content_key.value
    assert first_binding.actual_program_hash == equivalent_binding.actual_program_hash
    assert first_binding.binding_id.value == equivalent_binding.binding_id.value

    changed_payload = _core_payload()
    changed_payload["canonical_program"]["ir"]["program"]["statements"][1]["value"]["right"]["value"] = 3
    changed = _unit(changed_payload)
    changed_binding = compile_behavior_unit(changed)
    assert changed.content_key.value != first.content_key.value
    assert changed_binding.actual_program_hash != first_binding.actual_program_hash
    assert changed_binding.binding_id.value != first_binding.binding_id.value


def test_s4_p2_acc_compiler_02_binding_rejects_program_substitution() -> None:
    unit = _unit()
    binding = compile_behavior_unit(unit)
    transport = compiler_binding_to_dict_for_unit(unit, binding)

    changed_payload = _core_payload()
    changed_payload["canonical_program"]["ir"]["program"]["statements"][1]["value"]["right"]["value"] = 9
    changed = _unit(changed_payload)
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        validate_compiler_binding_for_unit(changed, binding)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH

    object.__setattr__(binding.program.instructions[0], "op", "LOAD_TRUE")
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        validate_compiler_binding_for_unit(unit, binding)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH

    clean_binding = compile_behavior_unit(unit)
    forged = compiler_binding_to_dict_for_unit(unit, clean_binding)
    forged["program"]["program_hash"] = "f" * 64
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        compiler_binding_from_dict_for_unit(unit, forged)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH

    forged = copy.deepcopy(transport)
    forged["actual_program_hash"] = "e" * 64
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        compiler_binding_from_dict_for_unit(unit, forged)
    assert exc.value.failure_code is canon.CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH


def test_s4_p2_acc_compiler_03_raw_python_and_unsupported_ir_fail_before_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    class CompilerMustNotRun:
        def __init__(self) -> None:
            nonlocal called
            called = True
            raise AssertionError("compiler must not run for invalid IR")

    monkeypatch.setattr(canon, "CognitiveCompiler", CompilerMustNotRun)
    for invalid_program in (
        "return __import__('os')",
        {"form": "INLINE_IR_V1", "ir": {"schema_version": canon.CANONICAL_PROGRAM_IR_V1, "program": {"node": "program", "statements": [{"node": "call", "callee": "host"}]}}},
        {"form": "INLINE_IR_V1", "ir": {"schema_version": canon.CANONICAL_PROGRAM_IR_V1, "program": {"node": "program", "statements": [{"node": "return", "value": {"node": "literal", "value_kind": "STRING", "value": "raw"}}]}}},
    ):
        payload = _core_payload()
        payload["canonical_program"] = invalid_program
        with pytest.raises((BehaviorViolation, canon.CanonicalizationViolation)):
            _unit(payload)
    assert not called


def test_s4_p2_acc_compiler_04_each_compile_uses_fresh_compiler_and_actual_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    original = canon.CognitiveCompiler
    instances: list[object] = []

    class CountingCompiler(original):
        def __init__(self) -> None:
            super().__init__()
            instances.append(self)

    monkeypatch.setattr(canon, "CognitiveCompiler", CountingCompiler)
    unit = _unit()
    first = compile_behavior_unit(unit)
    second = compile_behavior_unit(unit)
    assert len(instances) == 2
    assert instances[0] is not instances[1]
    assert first.program is not second.program
    assert first.actual_program_hash == first.program.program_hash
    assert second.actual_program_hash == second.program.program_hash


def test_s4_p2_acc_compiler_05_full_unit_revalidation_precedes_compiler_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    unit = _unit()
    object.__setattr__(unit.core.verification_contract, "expected_claims", ())
    called = False

    class CompilerMustNotRun:
        def __init__(self) -> None:
            nonlocal called
            called = True

        def compile(self, value: object) -> object:
            called = True
            raise AssertionError("compiler ran before Unit revalidation")

    monkeypatch.setattr(canon, "CognitiveCompiler", CompilerMustNotRun)
    with pytest.raises(BehaviorViolation):
        compile_behavior_unit(unit)
    assert not called

    with pytest.raises(BehaviorViolation) as exc:
        compile_behavior_unit(_unit().canonical_core)  # type: ignore[arg-type]
    assert exc.value.failure_code.value == "TYPE_MISMATCH"

    forged = object.__new__(type(_unit()))
    with pytest.raises(BehaviorViolation):
        validate_behavior_unit(forged)


def test_s4_p2_acc_compiler_06_transport_versions_and_binding_fixture_are_exact() -> None:
    unit = _unit()
    binding = compile_behavior_unit(unit)
    transport = compiler_binding_to_dict_for_unit(unit, binding)
    restored = compiler_binding_from_dict_for_unit(unit, transport)
    validate_compiler_binding_for_unit(unit, restored)
    vector = json.loads(_CANON_FIXTURE.read_text(encoding="utf-8"))["compiler_binding"]
    assert binding.compiler_identity == vector["compiler_identity"]
    assert binding.compiler_adapter_profile == vector["compiler_adapter_profile"]
    assert binding.language_version == vector["language_version"]
    assert binding.bytecode_version == vector["bytecode_version"]
    assert binding.host_abi_version == vector["host_abi_version"]
    assert binding.compiler_target == vector["compiler_target"]
    assert binding.actual_program_hash == vector["program_hash"]
    assert binding.binding_id.value == vector["binding_id"]

    for field, wrong in (
        ("language_version", "2.2.0-unknown"),
        ("bytecode_version", "9.9"),
        ("host_abi_version", "9.9"),
        ("compiler_identity", "unknown-compiler"),
        ("compiler_target", "unknown-target"),
    ):
        forged = copy.deepcopy(transport)
        forged[field] = wrong
        with pytest.raises(canon.CanonicalizationViolation):
            compiler_binding_from_dict_for_unit(unit, forged)


def test_s4_p2_acc_compiler_07_adapter_compiles_but_never_executes_cvm() -> None:
    binding = compile_behavior_unit(_unit())
    assert binding.program.instructions[-1].op == "HALT"
    assert all(instruction.op not in {"HOST_EVAL", "CALL_HOST", "LLM_REQUEST"} for instruction in binding.program.instructions)
    assert not hasattr(binding, "execution_result")
    assert not hasattr(binding, "replay_result")
