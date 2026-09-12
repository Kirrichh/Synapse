"""Pure contract checks; independent C1 publication has its own heavy shard."""
from dataclasses import replace
import pytest

from synapse.experiments.gold.behavior import BehaviorViolation, compile_behavior_unit
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage13.publication import reference
from synapse.experiments.gold.stage13.rejected_patch_profile import (
    REJECTED_PATCH_DOMAIN_V2, REJECTED_PATCH_GUARD_V4, build_conditional_rejected_patch_guard,
    rejected_guard_inputs, fingerprint_words)
from synapse.experiments.gold.replay_vm_adapter import observe_typed_pure_invocation
from tests.stage4_gold_replay_support import MACHINE_CONTEXT, GAS


def test_conditional_guard_computes_task_and_revision_applicability():
    domain = {"base_revision": "a" * 40, "task_contract_ref": {"sha256": "b" * 64}}
    domain_ref = replace(reference(domain, REJECTED_PATCH_DOMAIN_V2), kind=RefKind.CONTRACT_CONDITION)
    unit = build_conditional_rejected_patch_guard(domain=domain, domain_ref=domain_ref,
        report=replace(reference({"evidence": "negative-c1"}), kind=RefKind.SOURCE_EVIDENCE),
        oracle=reference({"evidence": "oracle"}))
    assert unit.core.verification_contract.profile_id == REJECTED_PATCH_GUARD_V4
    opcodes = [item.op for item in compile_behavior_unit(unit).program.instructions]
    assert "EQ" in opcodes and "JUMP_IF_FALSE" in opcodes
    arguments = {"gas_budget": GAS, "step_limit": 1000, "execution_context": MACHINE_CONTEXT}
    trace, result = observe_typed_pure_invocation(unit, inputs={}, **arguments)
    assert result == fingerprint_words(domain_ref.sha256)
    for revision, task in (("c" * 40, "b" * 64), ("a" * 40, "d" * 64)):
        inputs = rejected_guard_inputs(repository_revision=revision, task_contract_sha256=task)
        changed_trace, result = observe_typed_pure_invocation(unit, inputs=inputs, **arguments)
        assert result == [] and changed_trace != trace
    inputs = rejected_guard_inputs(repository_revision="a" * 40, task_contract_sha256="b" * 64)
    inputs["revision_word_0"] = False
    with pytest.raises(BehaviorViolation):
        observe_typed_pure_invocation(unit, inputs=inputs, **arguments)
