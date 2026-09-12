"""Method observations affect operation inputs, with an explicit approval rule."""
from dataclasses import replace
from pathlib import Path

from acceptance.stage4.stage10._builders import hash_ref
from acceptance.stage4.stage10.test_task_target_resolution import project
from acceptance.stage4.stage10.test_verification_plan_contract import command_plan
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id
from synapse.experiments.gold.runner.attempt_plan import _proposal_inputs
from synapse.experiments.gold.stage10.approval import RunApprovalPolicy, APPROVAL_REQUEST_SCHEMA_V4
from synapse.experiments.gold.stage10.intent import propose_intent
from synapse.experiments.gold.stage10.planning import propose_operation_plan, plan_semantic_sha256
from synapse.experiments.gold.stage10.planning_basis import create_planning_basis, read_planning_basis, method_groups
from synapse.experiments.gold.stage10.plan_transport import encode_operation_plan, decode_operation_plan


def row(subject, paths):
    return {"subject_ref": subject.to_dict(),
        "observation_id": compute_record_id(domain=IdentityDomain.REPLAY_OBSERVATION,
                                             canonical_bytes=b'{"acceptance":"ordinary-data"}').to_dict(),
        "terminal_snapshot_ref": hash_ref(RefKind.ARTIFACT, "terminal").to_dict(),
        "coverage": [int(bool(paths)), len(paths), 0], "covered_paths": sorted(paths)}


def test_method_search_partitions_actual_coverage_and_keeps_all_alternatives():
    one, two = hash_ref(RefKind.ARTIFACT, "one"), hash_ref(RefKind.ARTIFACT, "two")
    combined = create_planning_basis(target_paths=("a.py", "b.py"),
        alternatives=[row(one, ["a.py"]), row(two, ["a.py", "b.py"])])
    separate = create_planning_basis(target_paths=("a.py", "b.py"),
        alternatives=[row(one, ["a.py"]), row(two, ["b.py"])])
    assert len(method_groups(combined)) == 1
    assert len(method_groups(separate)) == 2
    assert method_groups(combined)[0] == (("a.py", "b.py"), two)
    assert len(read_planning_basis(combined)["alternatives"]) == 2
    assert read_planning_basis(separate)["uncovered_paths"] == []


def test_replayed_method_changes_plan_semantics_without_changing_task_permission(command_plan, tmp_path):
    profile, original, _, _, policy = command_plan
    profile = replace(profile, procedural_planning_required=True)
    subject = original.behavior_refs[0]
    candidates = []
    for alternatives in ([], [row(subject, ["calc.py"])]):
        basis = create_planning_basis(target_paths=("calc.py",), alternatives=alternatives)
        fields, plan_fields, _ = _proposal_inputs(profile, original.repository_revision_sha256,
            selected_behavior_refs=original.behavior_refs, planning_basis=basis)
        intent = propose_intent(**fields, knowledge_snapshot_ref=original.knowledge_snapshot_ref)
        plan = propose_operation_plan(intent=intent, **plan_fields)
        assert decode_operation_plan(encode_operation_plan(plan), intent=intent) == plan
        candidates.append((intent, plan))
    assert candidates[0][1].operations[0].input_refs != candidates[1][1].operations[0].input_refs
    assert len({plan_semantic_sha256(plan, intent=intent, policy_version=profile.policy_version)
                for intent, plan in candidates}) == 2
    approval = RunApprovalPolicy(tmp_path / "approvals", "a" * 64, profile.governing_human_authority)
    requests = [approval.request_for(plan=plan, intent=intent, policy_sha256=policy.sha256,
                                     executor=profile.executor) for intent, plan in candidates]
    assert requests[0] == requests[1]
    assert requests[0]["schema_version"] == APPROVAL_REQUEST_SCHEMA_V4
    assert requests[0]["plan_contract"]["planning_bounds"]["maximum_methods"] == 128
