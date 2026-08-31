"""The declared plan a run's attempts are executed under (§26; plan Этап 12 §10).

One responsibility: turn one run's *declared* planning configuration into an
accepted operation plan, by taking it through the Stage 10 owners in their
required order — intent, plan proposal, authority decision, acceptance.

This is a separate module from ``attempt_input_source`` because it changes for
a different reason. What an operator declares about a run — the task, the paths
it may touch, who proposes and who reviews, which capability the operation
needs — changes when the governance of runs changes. How an attempt's Stage 3
evidence is minted changes when the compatibility or admission owners change.
Holding both in one module would make either change look like a change to the
other.

No authority is decided here: ``decide_operation_plan`` and
``accept_operation_plan`` remain the deciders, and a profile that its own
authority refuses produces a refusal rather than a plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse.experiments.gold.canonicalization import HashBoundRef
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

from .vocabulary import GoldRunFailureCode, GoldRunViolation


#: The single operation a Gold attempt plans. §26 runs one controlled change
#: per attempt; a multi-operation plan is a different run shape and would need
#: its own declared verification and acceptance mapping.
_OPERATION_ID = "operation-main"
_EFFECT_ID = "effect-main"
_ACCEPTANCE_ID = "acceptance-main"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class GoldAttemptPlanProfile:
    """The declared planning configuration one run's attempts are planned under.

    These are the values a fixture used to hard-code and an operator has to
    declare. Declaring them is what turns a fixture's plan world into a run's.
    """

    task_statement: str
    subject_path: str
    allowed_scope: tuple[str, ...]
    intent_proposer: ActorIdentity
    intent_source_actor: ActorIdentity
    plan_proposer: ActorIdentity
    plan_source_actor: ActorIdentity
    executor: ActorIdentity
    reviewer_authority: AuthorityIdentity
    governing_human_authority: AuthorityIdentity
    policy_version: str
    condition_ref: HashBoundRef
    compatibility_evidence_ref: HashBoundRef
    human_approval_ref: HashBoundRef | None = None
    operation_kind: OperationKind = OperationKind.EDIT_CONTROLLED_CHANGE
    effect_kind: EffectKind = EffectKind.PATH_MODIFIED

    def __post_init__(self) -> None:
        for name in ("task_statement", "subject_path", "policy_version"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")
        if type(self.allowed_scope) is not tuple or not self.allowed_scope or any(
            type(item) is not str or not item for item in self.allowed_scope
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "allowed_scope must be a non-empty tuple of paths",
            )
        for name in (
            "intent_proposer",
            "intent_source_actor",
            "plan_proposer",
            "plan_source_actor",
            "executor",
        ):
            if type(getattr(self, name)) is not ActorIdentity:
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be an exact actor identity",
                )
        for name in ("reviewer_authority", "governing_human_authority"):
            if type(getattr(self, name)) is not AuthorityIdentity:
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be an exact authority identity",
                )
        for name in ("condition_ref", "compatibility_evidence_ref"):
            if type(getattr(self, name)) is not HashBoundRef:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact ref")
        if self.human_approval_ref is not None and type(self.human_approval_ref) is not HashBoundRef:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "human_approval_ref must be an exact ref or None",
            )


@dataclass(frozen=True)
class AcceptedAttemptPlan:
    """The accepted plan together with the intent and authority that produced it."""

    accepted: object
    intent: object
    authority: object


def accept_attempt_plan(
    *,
    profile: GoldAttemptPlanProfile,
    repository_revision_sha256: str,
    knowledge_snapshot_ref: HashBoundRef,
) -> AcceptedAttemptPlan:
    """Take one declared profile through intent, plan, decision and acceptance."""

    if type(profile) is not GoldAttemptPlanProfile:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan profile must be exact")
    if type(knowledge_snapshot_ref) is not HashBoundRef:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge snapshot ref must be exact")
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    scope = create_repository_scope(profile.allowed_scope)
    intent = propose_intent(
        proposer=profile.intent_proposer,
        source_actors=(profile.intent_source_actor,),
        task_statement=profile.task_statement,
        repository_revision_sha256=repository_revision_sha256,
        knowledge_snapshot_ref=knowledge_snapshot_ref,
        allowed_scope=scope,
        required_capabilities=(capability,),
        effects=(
            EffectConstraint(
                constraint_id=_EFFECT_ID,
                disposition=EffectDisposition.EXPECTED,
                kind=profile.effect_kind,
                subject_path=profile.subject_path,
                verification_ref=profile.condition_ref,
            ),
        ),
        acceptance=(
            AcceptanceCriterion(
                criterion_id=_ACCEPTANCE_ID,
                kind=AcceptanceKind.CONTRACT_CONDITION,
                condition_ref=profile.condition_ref,
            ),
        ),
        uncertainties=(),
    )
    plan = propose_operation_plan(
        intent=intent,
        proposer=profile.plan_proposer,
        source_actors=(profile.plan_source_actor,),
        allowed_scope=scope,
        capability_profile=(capability,),
        operations=(
            OperationRecord(
                operation_id=_OPERATION_ID,
                kind=profile.operation_kind,
                subject_paths=(profile.subject_path,),
                input_refs=(),
                argv=(),
                depends_on=(),
                capability=capability,
                verification=VerificationObligation(
                    kind=VerificationKind.CONTRACT_CONDITION,
                    condition_ref=profile.condition_ref,
                    failure_action=FailureAction.ABORT_PLAN,
                ),
                effect_constraint_ids=(_EFFECT_ID,),
                acceptance_criterion_ids=(_ACCEPTANCE_ID,),
            ),
        ),
    )
    authority = configure_plan_authority(
        policy=PlanAuthorityPolicy(
            schema_version=PLAN_POLICY_SCHEMA_V1,
            policy_version=profile.policy_version,
            allowed_operation_kinds=(profile.operation_kind,),
            allowed_capabilities=(capability,),
            human_review_capabilities=(),
        ),
        reviewer_authority=profile.reviewer_authority,
        governing_human_authority=profile.governing_human_authority,
        compatibility_validator=_compatibility_validator(profile.compatibility_evidence_ref),
    )
    decision = decide_operation_plan(
        plan=plan,
        intent=intent,
        authority=authority,
        executor=profile.executor,
        requested_decision=PlanDecisionKind.ACCEPT,
        human_approval_ref=profile.human_approval_ref,
        compatibility_evidence_refs=(profile.compatibility_evidence_ref,),
    )
    accepted = accept_operation_plan(
        plan=plan, intent=intent, decision=decision, authority=authority
    )
    return AcceptedAttemptPlan(accepted=accepted, intent=intent, authority=authority)


def _compatibility_validator(expected_ref: HashBoundRef):
    """The plan authority's compatibility check, bound to one declared ref."""

    def validate(plan: object, intent: object, evidence_refs: tuple[HashBoundRef, ...]):
        if plan.knowledge_snapshot_ref != intent.knowledge_snapshot_ref:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the plan and its intent name different knowledge snapshots",
            )
        if evidence_refs != (expected_ref,):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "plan compatibility evidence differs from the declared evidence",
            )
        return evidence_refs

    return validate


__all__ = [
    "AcceptedAttemptPlan",
    "GoldAttemptPlanProfile",
    "accept_attempt_plan",
]
