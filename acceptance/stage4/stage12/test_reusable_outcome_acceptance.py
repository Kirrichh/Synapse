"""One real negative C1 result can earn narrowly admitted reusable value."""

import pytest
from dataclasses import replace

from acceptance.stage4.stage12._reusable_case import reusable_case
from synapse.experiments.gold.stage12.reusable import register_reusable_candidate
from synapse.experiments.gold.stage12.outcome import inspect_outcome
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import TerminalDecisionKind
from synapse.experiments.gold.runner_composition import create_gold_run_composition
from synapse.experiments.gold.knowledge_environment import open_gold_project


def test_independent_negative_proof_and_admission_survive_recovery(tmp_path):
    case = reusable_case(tmp_path)
    world = case.world
    with world.composition.record_recovery.session() as session:
        register_reusable_candidate(session=session, authority=case.authority, manifest=world.manifest,
            context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1,
            unit=case.unit, behavior_manifest=case.behavior_manifest,
            attestation=case.attestation, write_evidence=case.write)
    result = world.execute()
    attempt = load_run_state(world.composition.record_store).attempts[0].result
    assert inspect_outcome(attempt.structured_outcome)["status"] == "VERIFIED_REUSABLE_PARTIAL"
    assert inspect_outcome(result.structured_outcome)["status"] == "VERIFIED_REUSABLE_PARTIAL"
    assert result.terminal_decision is not TerminalDecisionKind.STOP_SUCCESS
    assert attempt.oracle_resolved is False
    assert result.structured_outcome["payload"]["publication_result"] == "ADMISSION_CONFIRMED"
    assert len(result.structured_outcome["payload"]["created_behaviors"]) == 1
    assert world.controller.load_result().stored_dict() == result.stored_dict()
    reopened = open_gold_project(case.project.declaration.state_root)
    fresh = create_gold_run_composition(
        run_root=world.run_root, manifest=world.manifest, c1_boundary=world.boundary,
        run_record_fence=world.run_record_fence, attempt_inputs=world.attempt_inputs,
        stage10_composition=world.stage10_composition, verification_profile=world.attempt_inputs.plan_profile,
        reusable_authority=replace(case.authority, authority_handle=reopened.authority_handle,
            library=reopened.library, attestation_store=reopened.attestation_store,
            lifecycle_store=reopened.lifecycle_store, admission_journal=reopened.admission_journal,
            fence=reopened.fence),
    )
    assert fresh.controller.load_result().stored_dict() == result.stored_dict()
    assert world.worker_process.calls == world.oracle.calls == 1
    with world.composition.record_recovery.session() as session:
        with pytest.raises(ValueError, match="retrospective"):
            register_reusable_candidate(session=session, authority=case.authority, manifest=world.manifest,
                context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1,
                unit=case.unit, behavior_manifest=case.behavior_manifest,
                attestation=case.attestation, write_evidence=case.write)
