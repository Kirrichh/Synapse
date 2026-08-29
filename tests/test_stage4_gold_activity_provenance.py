"""Acceptance of subject-bound and durable Stage 4 activity provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import activity_policy as AP
from synapse.experiments.gold import activity_provenance as APR
from synapse.experiments.gold.activity_policy_store import (
    ActivityPolicyStoreFailureCode,
    ActivityPolicyStoreViolation,
    FileActivityPolicyStore,
)
from synapse.experiments.gold.activity_store import activity_result_ref
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.persistence import store_transaction
from tests import test_stage4_gold_activities as CASES


def governed_occurrence():
    activity = CASES.recorded()
    evaluator = CASES.evaluator()
    production = CASES.production_for(activity)
    consumption = CASES.consumption_provenance(evaluator)
    context = CASES.execution_context(
        activity,
        production=production,
        consumption=consumption,
    )
    decision = AP.evaluate_activity_policy(
        evaluator, activity=activity, **context
    )
    return activity, evaluator, production, consumption, context, decision


@pytest.mark.parametrize(
    "moved",
    (
        "kind",
        "inputs",
        "position",
        "result",
        "run_id",
        "attempt_id",
        "repository_revision",
        "environment_profile_id",
        "producer_component",
    ),
)
def test_one_entitlement_cannot_record_another_occurrence(moved: str) -> None:
    configured = CASES.evaluator()
    inputs = ACT.activity_inputs(prompt=b"original")
    production = CASES.production_provenance(
        configured,
        inputs=inputs,
        result=b"answer",
    )
    entitlement = AP.issue_activity_recorder_entitlement(
        configured, production=production
    )

    call = {
        "kind": ACT.ActivityKind.LLM_CALL,
        "inputs": inputs,
        "position": CASES.POSITION,
        "result": b"answer",
        "result_ref": activity_result_ref(b"answer"),
        "context": CASES.RECORD_CONTEXT,
        "entitlement": entitlement,
    }
    if moved == "kind":
        call["kind"] = ACT.ActivityKind.GIT_READ
    elif moved == "inputs":
        call["inputs"] = ACT.activity_inputs(prompt=b"substituted")
    elif moved == "position":
        call["position"] = CASES.OTHER_POSITION
    elif moved == "result":
        call["result"] = b"substituted"
        call["result_ref"] = activity_result_ref(b"substituted")
    else:
        context = {
            "run_id": CASES.RECORD_CONTEXT.run_id,
            "attempt_id": CASES.RECORD_CONTEXT.attempt_id,
            "repository_revision": CASES.RECORD_CONTEXT.repository_revision,
            "environment_profile_id": CASES.RECORD_CONTEXT.environment_profile_id,
            "producer_component": CASES.RECORD_CONTEXT.producer_component,
        }
        context[moved] = {
            "run_id": RunId("another-run"),
            "attempt_id": AttemptId("another-attempt"),
            "repository_revision": RepositoryRevision.git_commit("b" * 40),
            "environment_profile_id": "another-environment",
            "producer_component": "another-activity-recorder",
        }[moved]
        call["context"] = ACT.ActivityRecordContext(**context)

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.record_activity(**call)

    assert (
        excinfo.value.failure_code
        is ACT.ActivityFailureCode.RECORDER_ENTITLEMENT_SUBJECT_MISMATCH
    )
    assert isinstance(excinfo.value.__cause__, APR.ActivityProvenanceViolation)
    assert (
        excinfo.value.__cause__.failure_code
        is APR.ActivityProvenanceFailureCode.SUBJECT_MISMATCH
    )


def test_production_provenance_transport_refuses_subject_substitution() -> None:
    production = CASES.production_provenance()
    rewritten = production.to_dict()
    rewritten["kind"] = ACT.ActivityKind.GIT_READ.value

    with pytest.raises(APR.ActivityProvenanceViolation) as excinfo:
        APR.activity_production_provenance_from_dict(rewritten)

    assert (
        excinfo.value.failure_code
        is APR.ActivityProvenanceFailureCode.IDENTITY_MISMATCH
    )


def test_policy_store_restores_both_provenance_phases_after_restart(
    tmp_path: Path,
) -> None:
    activity, evaluator, production, consumption, _context, decision = (
        governed_occurrence()
    )
    fence = CASES.WORLD.coordinator_fence()
    store = FileActivityPolicyStore(tmp_path, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        production_ref = store.append_production_provenance(
            production, evaluator=evaluator, ticket=ticket
        )
        decision_ref = store.append_decision(
            decision,
            evaluator=evaluator,
            consumption=consumption,
            ticket=ticket,
        )

    restarted = FileActivityPolicyStore(tmp_path, mutation_fence=fence)
    restored_production = restarted.require_production_provenance_for_activity(
        production_ref,
        evaluator=evaluator,
        activity=activity,
    )
    restored_decision = restarted.require_decision(
        decision_ref, evaluator=evaluator
    )
    restored_consumption = restarted.require_consumption_provenance(
        decision_ref, evaluator=evaluator
    )

    assert restored_production.to_dict() == production.to_dict()
    assert restored_decision.to_dict() == decision.to_dict()
    assert restored_consumption.to_dict() == consumption.to_dict()


def test_policy_store_refuses_consumption_provenance_substitution(
    tmp_path: Path,
) -> None:
    _activity, evaluator, _production, consumption, _context, decision = (
        governed_occurrence()
    )
    substituted = APR.record_activity_consumption_provenance(
        evaluator.provenance_authority,
        machine_adapter_id="synapse.stage4.gold.another-machine-adapter/v1",
    )
    assert APR.activity_provenance_ref(substituted) != APR.activity_provenance_ref(
        consumption
    )
    fence = CASES.WORLD.coordinator_fence()
    store = FileActivityPolicyStore(tmp_path, mutation_fence=fence)

    with store_transaction(fence) as ticket:
        with pytest.raises(ActivityPolicyStoreViolation) as excinfo:
            store.append_decision(
                decision,
                evaluator=evaluator,
                consumption=substituted,
                ticket=ticket,
            )

    assert (
        excinfo.value.failure_code
        is ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH
    )
