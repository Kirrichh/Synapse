"""Lightweight operator-grant contracts; no repository/replay fixtures."""

from dataclasses import replace
import json

import pytest

from synapse.cli import main
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.stage10.approval import ApprovalRequired, RunApprovalPolicy, grant_approval, revoke_approval
from synapse.experiments.gold.stage10.plan_authority import (
    AuthorityFailureCode, AuthorityViolation, PlanDecisionKind,
    configure_plan_authority, decide_operation_plan, require_human_approval,
)
from synapse.experiments.gold.stage10.plan_transport import decode_plan_decision, encode_plan_decision
from acceptance.stage4.stage10._builders import hash_ref, plan_world, validate_plan_compatibility


@pytest.fixture
def approval_world(tmp_path):
    intent, plan, policy, authority, decision, _ = plan_world()
    clock = [1000000]
    approval = RunApprovalPolicy(tmp_path / "operator", "1" * 64, authority.governing_human_authority, lambda: clock[0])
    policy = replace(policy, human_review_capabilities=policy.allowed_capabilities)
    authority = configure_plan_authority(
        task_contract=authority.task_contract,
        policy=policy, reviewer_authority=authority.reviewer_authority,
        governing_human_authority=authority.governing_human_authority,
        compatibility_validator=validate_plan_compatibility, approval_policy=approval,
    )
    inputs = dict(plan=plan, intent=intent, authority=authority,
                  executor=ActorIdentity("acceptance-executor"),
                  requested_decision=PlanDecisionKind.ACCEPT,
                  compatibility_evidence_refs=decision.compatibility_evidence_refs)
    with pytest.raises(ApprovalRequired) as pending:
        decide_operation_plan(**inputs)
    return inputs, approval, pending.value.request_path, clock


def test_pending_request_never_grants_authority(approval_world):
    inputs, approval, request, _ = approval_world
    assert request.exists()
    assert not (approval.store_root / "grants").exists()
    with pytest.raises(AuthorityViolation) as rejected:
        decide_operation_plan(**inputs, human_approval_ref=hash_ref(RefKind.CONTRACT_CONDITION, "claimed-human"))
    assert rejected.value.failure_code is AuthorityFailureCode.HUMAN_APPROVAL_INVALID


def test_one_cli_command_grants_matching_plans_and_revoke_stops_them(approval_world, capsys):
    inputs, approval, request, clock = approval_world
    # CLI uses the real operator clock; runtime is configured to that same clock.
    import time
    clock[0] = time.time_ns() // 1000000
    assert main(["approve", str(request), "--store", str(approval.store_root)]) == 0
    grant = json.loads(capsys.readouterr().out)["grant_ref"]
    clock[0] = time.time_ns() // 1000000
    first = decide_operation_plan(**inputs)
    second = decide_operation_plan(**inputs)
    assert first.human_approval_ref is not None
    assert second.human_approval_ref is not None
    assert main(["revoke-approval", grant["sha256"], "--store", str(approval.store_root)]) == 0
    capsys.readouterr()
    clock[0] = time.time_ns() // 1000000
    with pytest.raises(ApprovalRequired):
        decide_operation_plan(**inputs)


@pytest.mark.parametrize("change", ["task", "scope", "revision", "policy", "run", "executor"])
def test_approval_does_not_cover_changed_conditions(approval_world, change):
    inputs, approval, request, clock = approval_world
    grant_approval(request_path=request, store_root=approval.store_root, duration_seconds=60, observed_at_unix_ms=clock[0])
    decide_operation_plan(**inputs)
    altered = dict(inputs)
    if change in {"task", "scope", "revision"}:
        options = {
            "task": {"task_statement": "A different task"},
            "scope": {"allowed_scope": ("synapse/experiments/gold",)},
            "revision": {"repository_revision_sha256": "b" * 40},
        }
        altered["intent"], altered["plan"], _, new_authority, _, _ = plan_world(**options[change])
        authority = inputs["authority"]
        altered["authority"] = configure_plan_authority(
            task_contract=new_authority.task_contract,
            policy=authority.policy,
            reviewer_authority=authority.reviewer_authority,
            governing_human_authority=authority.governing_human_authority,
            compatibility_validator=validate_plan_compatibility,
            approval_policy=approval,
        )
    elif change == "executor":
        altered["executor"] = ActorIdentity("different-executor")
    else:
        authority = inputs["authority"]
        altered["authority"] = configure_plan_authority(
            task_contract=authority.task_contract,
            policy=replace(authority.policy, policy_version="new-policy") if change == "policy" else authority.policy,
            reviewer_authority=authority.reviewer_authority,
            governing_human_authority=authority.governing_human_authority,
            compatibility_validator=validate_plan_compatibility,
            approval_policy=replace(approval, run_manifest_sha256="2" * 64) if change == "run" else approval,
        )
    with pytest.raises(ApprovalRequired):
        decide_operation_plan(**altered)


@pytest.mark.parametrize("invalidated", ["expired", "revoked", "clock_regressed"])
def test_archived_decision_readable_but_new_effect_requires_current_grant(approval_world, invalidated):
    inputs, approval, request, clock = approval_world
    grant = grant_approval(request_path=request, store_root=approval.store_root, duration_seconds=60, observed_at_unix_ms=clock[0])
    decision = decide_operation_plan(**inputs)
    encoded = encode_plan_decision(decision)
    clock[0] += 1000
    if invalidated == "revoked":
        revoke_approval(store_root=approval.store_root, grant_sha256=grant.sha256, observed_at_unix_ms=clock[0])
    elif invalidated == "expired":
        clock[0] += 60000
    else:
        clock[0] -= 2000
    assert decode_plan_decision(encoded, plan=inputs["plan"], intent=inputs["intent"], authority=inputs["authority"]) == decision
    with pytest.raises(AuthorityViolation):
        require_human_approval(
            authority=inputs["authority"], plan=inputs["plan"], intent=inputs["intent"],
            executor=inputs["executor"], approval_ref=decision.human_approval_ref, current=True,
        )


def test_automatic_approval_does_not_replace_compatibility(approval_world):
    inputs, approval, request, clock = approval_world
    grant_approval(request_path=request, store_root=approval.store_root, duration_seconds=60, observed_at_unix_ms=clock[0])
    inputs["compatibility_evidence_refs"] = (hash_ref(RefKind.SOURCE_EVIDENCE, "wrong-compatibility"),)
    with pytest.raises(AuthorityViolation) as denied:
        decide_operation_plan(**inputs)
    assert denied.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID


def test_altered_grant_bytes_cannot_be_used(approval_world):
    inputs, approval, request, clock = approval_world
    grant = grant_approval(request_path=request, store_root=approval.store_root, duration_seconds=60, observed_at_unix_ms=clock[0])
    path = next((approval.store_root / "grants").glob(f"*/{grant.sha256}.json"))
    path.write_bytes(path.read_bytes().replace(b'1060000', b'9999999'))
    with pytest.raises(ValueError):
        decide_operation_plan(**inputs)


def test_revocation_does_not_report_success_for_an_unknown_grant(tmp_path, capsys):
    assert main(["revoke-approval", "f" * 64, "--store", str(tmp_path / "operator")]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "not found" in output.err
