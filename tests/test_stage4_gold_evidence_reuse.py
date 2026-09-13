"""Pure proof-read regression: reuse computation, never mutable state or authority."""
from dataclasses import replace
import json

import pytest

from synapse.experiments.gold import behavior
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID, STAGE4_CANONICAL_PROFILE_V1, canonicalize_stage4_payload,
)
from synapse.experiments.gold.source_verification import source_ref
from tests.test_stage4_gold_compiler_binding import _core_payload, _unit


def canonical(value):
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1,
                                      codec_id=STABLE_CANONICAL_CODEC_ID)


def evidence(value=2):
    core = _core_payload()
    core["source_evidence_refs"] = [source_ref(b'{"value":2}', "acceptance.proof-input/v1").to_dict()]
    core["canonical_program"]["ir"]["program"]["statements"][1]["value"]["right"]["value"] = value
    unit = _unit(core)
    blob = behavior.create_behavior_blob(unit)
    manifest = behavior.create_behavior_manifest(unit, blob,
        compiler_binding=behavior.compile_behavior_unit(unit))
    return canonical({"unit": unit.to_dict(), "manifest": manifest.to_dict(unit=unit, blob=blob)}), unit.core.source_evidence_refs[0]


@pytest.fixture(autouse=True)
def isolated_proof_reuse():
    behavior._reused_behavior_evidence_subject.cache_clear()
    yield
    behavior._reused_behavior_evidence_subject.cache_clear()


def count_compilations(monkeypatch):
    calls = []
    original = behavior.compile_behavior_unit
    def compile_actual(unit):
        calls.append(unit.content_key.value)
        return original(unit)
    monkeypatch.setattr(behavior, "compile_behavior_unit", compile_actual)
    return calls


def test_identical_retained_bytes_do_not_repeat_compilation(monkeypatch):
    proof, content = evidence()
    calls = count_compilations(monkeypatch)
    first = behavior.behavior_evidence_subject(proof, content)
    for _ in range(6):
        repeated = behavior.behavior_evidence_subject(bytes(bytearray(proof)), replace(content))
        assert repeated == first and repeated is not first
    assert len(calls) == 1


def test_a_different_valid_program_is_checked_and_has_its_own_subject(monkeypatch):
    first_proof, content = evidence(2)
    second_proof, second_content = evidence(3)
    calls = count_compilations(monkeypatch)
    first = behavior.behavior_evidence_subject(first_proof, content)
    second = behavior.behavior_evidence_subject(second_proof, second_content)
    assert first != second and len(calls) == 2


def test_a_previous_proof_does_not_establish_a_different_content_binding():
    proof, content = evidence()
    behavior.behavior_evidence_subject(proof, content)
    with pytest.raises(ValueError, match="content is not bound"):
        behavior.behavior_evidence_subject(proof, replace(content, sha256="f" * 64))


def test_a_previous_valid_proof_does_not_validate_inconsistent_manifest_bytes():
    proof, content = evidence()
    expected = behavior.behavior_evidence_subject(proof, content)
    changed = json.loads(proof)
    changed["manifest"]["unexpected_field"] = "different"
    for _ in range(2):
        with pytest.raises(ValueError, match="manifest"):
            behavior.behavior_evidence_subject(canonical(changed), content)
    assert behavior.behavior_evidence_subject(proof, content) == expected


def test_proofs_over_the_reuse_budget_use_the_same_complete_verification(monkeypatch):
    proof, content = evidence()
    monkeypatch.setattr(behavior, "_EVIDENCE_REUSE_MAX_BYTES", len(proof) - 1)
    calls = count_compilations(monkeypatch)
    first = behavior.behavior_evidence_subject(proof, content)
    assert behavior.behavior_evidence_subject(proof, content) == first
    assert len(calls) == 2 and behavior._reused_behavior_evidence_subject.cache_info().currsize == 0
