"""Closed literal certification and lossless negative-fact identity."""

from dataclasses import replace
from copy import deepcopy

import pytest

from synapse.experiments.gold.behavior import compile_behavior_unit
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.replay import ReplayViolation, replay_machine_execution_context
from synapse.experiments.gold.replay_vm_adapter import certify_literal_return_transitions
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage13.publication import reference
from synapse.experiments.gold.stage13.rejected_patch_profile import (
    REJECTED_PATCH_DOMAIN_V2, build_rejected_patch_guard, fingerprint_words,
)


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
