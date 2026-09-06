"""Historical fact compatibility recognizes only the exact inert program."""

from dataclasses import replace
import inspect
from copy import deepcopy

import pytest

from synapse.experiments.gold.behavior import BehaviorViolation, compile_behavior_unit, create_behavior_unit
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.replay import ReplayViolation, replay_machine_execution_context
from synapse.experiments.gold.replay_vm_adapter import certify_literal_return_transitions
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage13.publication import reference
from synapse.experiments.gold.stage13.rejected_patch_profile import (
    REJECTED_PATCH_DOMAIN_V2, build_rejected_patch_guard, fingerprint_words, is_inert_rejected_patch_guard,
)


@pytest.mark.parametrize("change", ["program", "capability", "condition"])
def test_profile_label_does_not_grant_historical_fact_compatibility(change):
    domain = replace(reference({"example": "domain"}, REJECTED_PATCH_DOMAIN_V2), kind=RefKind.CONTRACT_CONDITION)
    unit = build_rejected_patch_guard(domain_ref=domain,
        report=replace(reference({"example": "report"}), kind=RefKind.SOURCE_EVIDENCE),
        oracle=reference({"example": "oracle"}))
    assert is_inert_rejected_patch_guard(unit)
    fields = {name: getattr(unit.core, name) for name in inspect.signature(create_behavior_unit).parameters}
    if change == "program":
        program = unit.core.canonical_program.to_dict()
        program["ir"]["program"]["statements"][0]["value"]["elements"][0]["value"] ^= 1
        fields["canonical_program"] = program
    elif change == "capability":
        fields["capability_requirements"] = ("read",)
        with pytest.raises(BehaviorViolation, match="CAPABILITY_MISMATCH"):
            create_behavior_unit(**fields)
        return
    else:
        condition = unit.core.input_contract.preconditions[0]
        fields["input_contract"] = replace(unit.core.input_contract,
            preconditions=(replace(condition, condition_schema_id="synapse.stage4.other-condition/v1"),))
    changed = create_behavior_unit(**fields)
    assert not is_inert_rejected_patch_guard(changed)


def test_literal_certificate_binds_the_frozen_gas_and_closed_program():
    domain = replace(reference({"example": "domain"}, REJECTED_PATCH_DOMAIN_V2), kind=RefKind.CONTRACT_CONDITION)
    unit = build_rejected_patch_guard(domain_ref=domain,
        report=replace(reference({"example": "report"}), kind=RefKind.SOURCE_EVIDENCE),
        oracle=reference({"example": "oracle"}))
    program = compile_behavior_unit(unit).program
    context = replay_machine_execution_context(run_id=RunId("certificate-acceptance"), attempt_id=AttemptId("1"),
        repository_revision=RepositoryRevision.git_commit("a" * 40), environment_profile_id="pure", policy_version="policy/v1")
    trace = certify_literal_return_transitions(program, gas_budget=10000, execution_context=context)
    assert trace and len(set(trace)) == len(trace)
    assert certify_literal_return_transitions(program, gas_budget=10000, execution_context=context) == trace
    assert certify_literal_return_transitions(program, gas_budget=9999, execution_context=context) != trace
    changed = deepcopy(program)
    changed.instructions.pop()
    with pytest.raises(ReplayViolation):
        certify_literal_return_transitions(changed, gas_budget=10000, execution_context=context)


@pytest.mark.parametrize(("digest", "expected"), [
    ("0" * 64, [0, 0, 0, 0, 0]),
    ("f" * 64, [2**52 - 1, 2**52 - 1, 2**52 - 1, 2**52 - 1, 2**48 - 1]),
    ("8" + "0" * 63, [2**51, 0, 0, 0, 0]),
])
def test_fingerprint_preserves_all_bits_with_exact_json_integers(digest, expected):
    assert fingerprint_words(digest) == expected
