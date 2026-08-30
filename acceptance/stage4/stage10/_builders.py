"""Acceptance-only builders for Stage 10 public production contracts."""

from __future__ import annotations

import hashlib

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.stage10.intent import (
    AcceptanceCriterion,
    AcceptanceKind,
    EffectConstraint,
    EffectDisposition,
    EffectKind,
    propose_intent,
)
from synapse.experiments.gold.stage10.plan_authority import (
    PLAN_POLICY_SCHEMA_V1,
    PlanAuthorityPolicy,
    PlanDecisionKind,
    accept_operation_plan,
    configure_plan_authority,
    decide_operation_plan,
)
from synapse.experiments.gold.stage10.planning import (
    CAPABILITY_BY_OPERATION,
    FailureAction,
    OperationKind,
    OperationRecord,
    VerificationKind,
    VerificationObligation,
    propose_operation_plan,
)
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope


def hash_ref(kind: RefKind, label: str, *, schema: str = "acceptance.stage10/v1") -> HashBoundRef:
    raw = label.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=kind,
        ref_id=digest,
        schema_id=schema,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def plan_world(
    *,
    snapshot_ref: HashBoundRef | None = None,
    risky: bool = False,
    uncertainties: tuple[str, ...] = (),
):
    condition = hash_ref(RefKind.CONTRACT_CONDITION, "condition")
    approval = hash_ref(RefKind.CONTRACT_CONDITION, "human-approval")
    snapshot = snapshot_ref or hash_ref(
        RefKind.KNOWLEDGE_SNAPSHOT,
        "snapshot",
        schema="acceptance.knowledge-snapshot/v1",
    )
    kind = OperationKind.PUBLISH_CANDIDATE if risky else OperationKind.EDIT_CONTROLLED_CHANGE
    capability = CAPABILITY_BY_OPERATION[kind]
    scope = create_repository_scope(("synapse/experiments/gold/stage10",))
    intent = propose_intent(
        proposer=ActorIdentity("acceptance-intent-producer"),
        source_actors=(ActorIdentity("acceptance-requirement-source"),),
        task_statement="Implement the accepted Stage 10 change.",
        repository_revision_sha256="a" * 40,
        knowledge_snapshot_ref=snapshot,
        allowed_scope=scope,
        required_capabilities=(capability,),
        effects=(
            EffectConstraint(
                constraint_id="effect-main",
                disposition=EffectDisposition.EXPECTED,
                kind=EffectKind.PATH_MODIFIED,
                subject_path="synapse/experiments/gold/stage10/context.py",
                verification_ref=condition,
            ),
        ),
        acceptance=(
            AcceptanceCriterion(
                criterion_id="acceptance-main",
                kind=AcceptanceKind.CONTRACT_CONDITION,
                condition_ref=condition,
            ),
        ),
        uncertainties=uncertainties,
    )
    operation = OperationRecord(
        operation_id="operation-main",
        kind=kind,
        subject_paths=("synapse/experiments/gold/stage10/context.py",),
        input_refs=(),
        argv=(),
        depends_on=(),
        capability=capability,
        verification=VerificationObligation(
            kind=VerificationKind.CONTRACT_CONDITION,
            condition_ref=condition,
            failure_action=FailureAction.ABORT_PLAN,
        ),
    )
    plan = propose_operation_plan(
        intent=intent,
        proposer=ActorIdentity("acceptance-plan-producer"),
        source_actors=(ActorIdentity("acceptance-plan-source"),),
        allowed_scope=scope,
        capability_profile=(capability,),
        operations=(operation,),
    )
    policy = PlanAuthorityPolicy(
        schema_version=PLAN_POLICY_SCHEMA_V1,
        policy_version="acceptance-plan-policy-v1",
        allowed_operation_kinds=(kind,),
        allowed_capabilities=(capability,),
        human_review_capabilities=(),
    )
    authority = configure_plan_authority(
        policy=policy,
        reviewer_authority=AuthorityIdentity("acceptance-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("acceptance-governing-human"),
    )
    decision = decide_operation_plan(
        plan=plan,
        intent=intent,
        authority=authority,
        executor=ActorIdentity("acceptance-executor"),
        requested_decision=PlanDecisionKind.ACCEPT,
        human_approval_ref=approval if risky or uncertainties else None,
    )
    accepted = accept_operation_plan(
        plan=plan,
        intent=intent,
        decision=decision,
        authority=authority,
    )
    return intent, plan, policy, authority, decision, accepted
