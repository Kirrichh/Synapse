"""The declared plan a run's attempts are executed under (§26; plan Этап 12 §10).

One responsibility: turn one run's declared planning configuration into an
accepted operation plan and expose the stable semantic identity of what that
plan will do. Attempt-local proposal, snapshot and authority identities remain
provenance; they must not make the same operation look like a new hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.compatibility import CompatibilityDecisionKind, validate_compatibility_decision
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore, compatibility_record_ref
from synapse.experiments.gold.run_compatibility import MintedCompatibilityEvidence
from synapse.experiments.gold.stage10.intent import (
    INTENT_SCHEMA_V2,
    AcceptanceCriterion,
    AcceptanceKind,
    EffectConstraint,
    EffectDisposition,
    EffectKind,
    ExecutionFeedback,
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
    OPERATION_PLAN_SCHEMA_V1,
    topological_operation_order,
    FailureAction,
    OperationKind,
    OperationRecord,
    VerificationKind,
    VerificationObligation,
    propose_operation_plan,
)
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.stage10.approval import RunApprovalPolicy

from .vocabulary import GoldRunFailureCode, GoldRunViolation
from .models import GOLD_ATTEMPT_RESULT_SCHEMA_V3, GoldAttemptResult


_OPERATION_ID = "operation-main"
_EFFECT_ID = "effect-main"
_ACCEPTANCE_ID = "acceptance-main"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _semantic_bytes(payload: dict[str, object]) -> bytes:
    return canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _plan_semantic_sha256(*, profile: "GoldAttemptPlanProfile", capability: str) -> str:
    """Identity of the operation/constraints, excluding attempt-local provenance."""

    payload = {
        "task_statement": profile.task_statement,
        "subject_path": profile.subject_path,
        "allowed_scope": list(profile.allowed_scope),
        "operation_kind": profile.operation_kind.value,
        "effect_kind": profile.effect_kind.value,
        "capability": capability,
        "condition_ref": profile.condition_ref.to_dict(),
        "policy_version": profile.policy_version,
    }
    return hashlib.sha256(_semantic_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class GoldAttemptPlanProfile:
    """The declared planning configuration one run's attempts are planned under."""

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
    approval_policy: RunApprovalPolicy | None = None
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
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
        for name in ("reviewer_authority", "governing_human_authority"):
            if type(getattr(self, name)) is not AuthorityIdentity:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact authority identity")
        for name in ("condition_ref",):
            if type(getattr(self, name)) is not HashBoundRef:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact ref")
        if self.approval_policy is not None and (
            type(self.approval_policy) is not RunApprovalPolicy
            or self.approval_policy.governing_human_authority != self.governing_human_authority
        ):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "approval policy must name the governing human")


@dataclass(frozen=True)
class AcceptedAttemptPlan:
    """Accepted authority objects plus stable operation semantics."""

    accepted: object
    intent: object
    authority: object
    semantic_sha256: str


def _proposal_inputs(profile: GoldAttemptPlanProfile, repository_revision_sha256: str):
    """One declaration feeds both operator preview and the actual proposals."""
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    scope = create_repository_scope(profile.allowed_scope)
    intent_fields = dict(
        proposer=profile.intent_proposer,
        source_actors=(profile.intent_source_actor,),
        task_statement=profile.task_statement,
        repository_revision_sha256=repository_revision_sha256,
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
    plan_fields = dict(
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
    policy = PlanAuthorityPolicy(
        schema_version=PLAN_POLICY_SCHEMA_V1,
        policy_version=profile.policy_version,
        allowed_operation_kinds=(profile.operation_kind,),
        allowed_capabilities=(capability,),
        human_review_capabilities=(capability,) if profile.approval_policy is not None else (),
    )
    return intent_fields, plan_fields, policy


def _approval_field(value):
    if type(value) is tuple:
        return [_approval_field(item) for item in value]
    return value.to_dict() if hasattr(value, "to_dict") else value


def check_attempt_plan_approval(*, profile: GoldAttemptPlanProfile, manifest) -> None:
    """Pause before preparation effects; never approve from a worker response."""
    approval = profile.approval_policy
    if approval is None:
        return
    manifest.validate_identity()
    if approval.run_manifest_sha256 != manifest.manifest_sha256:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "approval policy belongs to another run")
    intent_fields, plan_fields, policy = _proposal_inputs(profile, manifest.config.base_revision)
    request = approval.request_for_contract(
        intent_contract={"schema_version": INTENT_SCHEMA_V2,
                         **{key: _approval_field(value) for key, value in intent_fields.items()}},
        plan_contract={"schema_version": OPERATION_PLAN_SCHEMA_V1,
                       "repository_revision_sha256": manifest.config.base_revision,
                       "execution_order": list(topological_operation_order(plan_fields["operations"])),
                       **{key: _approval_field(value) for key, value in plan_fields.items()}},
        policy_sha256=policy.sha256, executor=profile.executor,
    )
    approval.review_request(request)


def accept_attempt_plan(
    *,
    profile: GoldAttemptPlanProfile,
    repository_revision_sha256: str,
    knowledge_snapshot_ref: HashBoundRef,
    compatibility: MintedCompatibilityEvidence,
    compatibility_history: FileCompatibilityStore,
    previous_result: GoldAttemptResult | None = None,
) -> AcceptedAttemptPlan:
    """Take one declared profile through intent, plan, decision and acceptance."""

    if type(profile) is not GoldAttemptPlanProfile:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan profile must be exact")
    if type(knowledge_snapshot_ref) is not HashBoundRef:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge snapshot ref must be exact")
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    feedback = ()
    if previous_result is not None:
        previous_result.validate_identity()
        if previous_result.verified_patch_sha256 is not None:
            feedback = (ExecutionFeedback(
                source_result_ref=HashBoundRef(
                    kind=RefKind.ARTIFACT,
                    ref_id=previous_result.result_sha256,
                    schema_id=GOLD_ATTEMPT_RESULT_SCHEMA_V3,
                    sha256=previous_result.result_sha256,
                    byte_length=len(previous_result.canonical_bytes()),
                    media_type="application/json",
                ),
                evaluated_patch_sha256=previous_result.verified_patch_sha256,
                oracle_resolved=previous_result.oracle_resolved,
            ),)
    intent_fields, plan_fields, policy = _proposal_inputs(profile, repository_revision_sha256)
    intent = propose_intent(**intent_fields, knowledge_snapshot_ref=knowledge_snapshot_ref, execution_feedback=feedback)
    plan = propose_operation_plan(intent=intent, **plan_fields)
    authority = configure_plan_authority(
        policy=policy,
        approval_policy=profile.approval_policy,
        reviewer_authority=profile.reviewer_authority,
        governing_human_authority=profile.governing_human_authority,
        compatibility_validator=_compatibility_validator(
            compatibility, compatibility_history, knowledge_snapshot_ref
        ),
    )
    decision = decide_operation_plan(
        plan=plan,
        intent=intent,
        authority=authority,
        executor=profile.executor,
        requested_decision=PlanDecisionKind.ACCEPT,
        compatibility_evidence_refs=tuple(sorted(
            (compatibility_record_ref(item) for item in compatibility.decisions),
            key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256),
        )),
    )
    accepted = accept_operation_plan(plan=plan, intent=intent, decision=decision, authority=authority)
    return AcceptedAttemptPlan(
        accepted=accepted,
        intent=intent,
        authority=authority,
        semantic_sha256=_plan_semantic_sha256(profile=profile, capability=capability),
    )


def _compatibility_validator(compatibility, history, snapshot_ref):
    """Resolve the independently minted evidence; ref equality alone grants nothing."""

    if type(compatibility) is not MintedCompatibilityEvidence or type(history) is not FileCompatibilityStore:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan requires durable compatibility evidence")
    if not compatibility.decisions:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan has no compatibility decisions")
    expected_refs = tuple(sorted(
        (compatibility_record_ref(item) for item in compatibility.decisions),
        key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256),
    ))

    def validate(plan: object, intent: object, evidence_refs: tuple[HashBoundRef, ...]):
        if plan.knowledge_snapshot_ref != snapshot_ref or intent.knowledge_snapshot_ref != snapshot_ref:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the plan and its intent name different knowledge snapshots",
            )
        if evidence_refs != expected_refs:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "plan compatibility evidence differs from the declared evidence",
            )
        for decision in compatibility.decisions:
            ref = compatibility_record_ref(decision)
            validate_compatibility_decision(
                decision, evaluator=compatibility.evaluator, context=compatibility.context
            )
            if decision.decision_kind is not CompatibilityDecisionKind.COMPATIBLE:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan evidence is not compatible")
            raw = history.resolve_ref(ref)
            if hashlib.sha256(raw).hexdigest() != ref.sha256 or len(raw) != ref.byte_length:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan evidence bytes differ from durable ref")
        return evidence_refs

    return validate


__all__ = [
    "AcceptedAttemptPlan",
    "GoldAttemptPlanProfile",
    "accept_attempt_plan",
]
