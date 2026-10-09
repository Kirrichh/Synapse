"""One heavy shard: v2 approval binds each attempt's admitted selection."""

from dataclasses import replace
import json

import pytest

from synapse.cli import main
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus
from synapse.experiments.gold.stage10.approval import ApprovalRequired, RunApprovalPolicy
from synapse.experiments.gold.stage10.intent import propose_intent
from synapse.experiments.gold.stage10.plan_authority import (
    AuthorityFailureCode, AuthorityViolation, PlanDecisionKind, decide_operation_plan,
)
from synapse.experiments.gold.stage10.planning import propose_operation_plan
from synapse.experiments.gold.stage10.task_contract import TASK_CONTRACT_SCHEMA_V2
from acceptance.stage4.stage10._builders import hash_ref
from acceptance.stage4.stage11._builders import _plan_profile, create_composition, run_world


def test_one_approval_binds_admitted_selection_and_rejects_substitution(tmp_path, capsys):
    world = run_world(
        tmp_path, max_attempts=2, fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        worker_outcomes=("PATCH", "PATCH"), run_id="operator-approved-run",
    )
    profile = _plan_profile(world.repo, world.manifest)
    profile = replace(profile, task_contract=replace(
        profile.task_contract, schema_version=TASK_CONTRACT_SCHEMA_V2, behavior_refs=(),
    ))
    approvals = RunApprovalPolicy(
        tmp_path / "operator-approvals", world.manifest.manifest_sha256,
        profile.governing_human_authority,
    )
    world.attempt_inputs.plan_profile = replace(profile, approval_policy=approvals)
    world.composition = create_composition(world)
    with pytest.raises(ApprovalRequired) as pending:
        world.execute()
    assert world.composition.record_store.get(kind=RecordKind.PREPARATION_STARTED, key="1") is None
    assert world.attempt_inputs._cached_source is None
    assert world.worker_process.calls == world.oracle.calls == 0

    assert main(["approve", str(pending.value.request_path), "--store", str(approvals.store_root)]) == 0
    grant = json.loads(capsys.readouterr().out)["grant_ref"]
    result = world.execute()

    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert result.structured_outcome["payload"]["status"] == "FULL"
    assert world.worker_process.calls == world.oracle.calls == 2
    for prepared in world.attempt_inputs.prepared.values():
        receipt = prepared.accepted_plan.decision.human_approval_ref
        stored = json.loads((approvals.store_root / "receipts" / (receipt.sha256 + ".json")).read_bytes())
        assert stored["grant_ref"] == grant
        assert prepared.intent.behavior_refs == prepared.admission_request.handle.subject_refs
    assert world.execute() == result
    assert world.worker_process.calls == world.oracle.calls == 2

    prepared = world.attempt_inputs.prepared[1]
    original = prepared.intent
    injected = propose_intent(
        **profile.task_contract.intent_fields(), task_contract_ref=original.task_contract_ref,
        behavior_refs=(hash_ref(RefKind.ARTIFACT, "never-admitted"),),
        proposer=original.proposer, source_actors=original.source_actors,
        knowledge_snapshot_ref=original.knowledge_snapshot_ref,
    )
    candidate = prepared.accepted_plan.candidate
    substituted_inputs = tuple(sorted(
        profile.task_contract.target_bindings + injected.behavior_refs,
        key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256),
    ))
    proposal = propose_operation_plan(
        intent=injected, proposer=candidate.proposer, source_actors=candidate.source_actors,
        allowed_scope=candidate.allowed_scope, capability_profile=candidate.capability_profile,
        operations=(replace(candidate.operations[0], input_refs=substituted_inputs),),
    )
    with pytest.raises(AuthorityViolation) as rejected:
        decide_operation_plan(
            plan=proposal, intent=injected, authority=prepared.plan_authority, executor=profile.executor,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=prepared.accepted_plan.decision.compatibility_evidence_refs,
        )
    assert rejected.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID
    assert world.worker_process.calls == world.oracle.calls == 2
