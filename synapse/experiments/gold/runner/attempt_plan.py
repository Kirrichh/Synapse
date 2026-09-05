"""The declared plan a run's attempts are executed under (§26; plan Этап 12 §10).

One responsibility: turn one run's declared planning configuration into an
accepted operation plan and expose the stable semantic identity of what that
plan will do. Attempt-local proposal, snapshot and authority identities remain
provenance; they must not make the same operation look like a new hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
    INTENT_SCHEMA_V3,
    EffectDisposition,
    ExecutionFeedback,
    intent_payload_sha256,
    propose_intent,
)
from synapse.experiments.gold.stage10.plan_authority import (
    PLAN_POLICY_SCHEMA_V1,
    PlanAuthorityPolicy,
    PlanDecisionKind,
    PlanDecisionReason,
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
    plan_verification_obligations,
)
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
from synapse.experiments.gold.bindings import binding_from_dict, binding_to_ref
from synapse.experiments.gold.contracts import RepositoryRevision
from synapse.experiments.gold.stage10.approval import RunApprovalPolicy

from .vocabulary import GoldRunFailureCode, GoldRunViolation
from .models import GOLD_ATTEMPT_RESULT_SCHEMA_V4, GoldAttemptResult


_OPERATION_ID = "operation-main"


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
        "task_contract_ref": profile.task_contract.reference.to_dict(),
        "operation_kind": profile.operation_kind.value,
        "capability": capability,
        "policy_version": profile.policy_version,
    }
    return hashlib.sha256(_semantic_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class GoldAttemptPlanProfile:
    """The declared planning configuration one run's attempts are planned under."""

    task_contract: GoverningTaskContract
    target_records: tuple[object, ...]
    repository_root: Path
    intent_proposer: ActorIdentity
    intent_source_actor: ActorIdentity
    plan_proposer: ActorIdentity
    plan_source_actor: ActorIdentity
    executor: ActorIdentity
    reviewer_authority: AuthorityIdentity
    governing_human_authority: AuthorityIdentity
    policy_version: str
    approval_policy: RunApprovalPolicy | None = None
    operation_kind: OperationKind = OperationKind.EDIT_CONTROLLED_CHANGE

    def __post_init__(self) -> None:
        if type(self.task_contract) is not GoverningTaskContract:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan requires a governing task contract")
        if type(self.repository_root) is not type(Path()) or not self.repository_root.is_absolute():
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan repository must be absolute")
        if type(self.target_records) is not tuple or tuple(binding_to_ref(item) for item in self.target_records) != self.task_contract.target_bindings:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan target records differ from the governing task")
        if type(self.policy_version) is not str or not self.policy_version:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan policy must be explicit")
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
    task = profile.task_contract
    if repository_revision_sha256 != task.repository_revision_sha256:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan differs from the governing task revision")
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    expected = tuple(item for item in task.effects if item.disposition is EffectDisposition.EXPECTED)
    conditions = {item.verification_ref for item in expected}
    if not expected or len(conditions) != 1:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "controlled change needs one exact verification contract")
    condition = next(iter(conditions))
    intent_fields = dict(
        **task.intent_fields(), task_contract_ref=task.reference,
        proposer=profile.intent_proposer,
        source_actors=(profile.intent_source_actor,), uncertainties=(),
    )
    plan_fields = dict(
        proposer=profile.plan_proposer,
        source_actors=(profile.plan_source_actor,),
        allowed_scope=task.allowed_scope,
        capability_profile=(capability,),
        operations=(OperationRecord(
            operation_id=_OPERATION_ID, kind=profile.operation_kind,
            subject_paths=tuple(sorted({item.subject_path for item in expected if item.subject_path is not None})),
            input_refs=tuple(sorted(task.target_bindings + task.behavior_refs,
                                    key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256))),
            argv=(), depends_on=(),
            capability=capability,
            verification=VerificationObligation(
                kind=VerificationKind.CONTRACT_CONDITION, condition_ref=condition,
                failure_action=FailureAction.ABORT_PLAN,
            ),
            effect_constraint_ids=tuple(item.constraint_id for item in expected),
            acceptance_criterion_ids=tuple(item.criterion_id for item in task.acceptance),
        ),),
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
        intent_contract={"schema_version": INTENT_SCHEMA_V3,
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
                    schema_id=GOLD_ATTEMPT_RESULT_SCHEMA_V4,
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
        task_contract=profile.task_contract,
        approval_policy=profile.approval_policy,
        reviewer_authority=profile.reviewer_authority,
        governing_human_authority=profile.governing_human_authority,
        compatibility_validator=_compatibility_validator(
            compatibility, compatibility_history, knowledge_snapshot_ref, profile
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


def validate_recorded_attempt_plan(*, profile: GoldAttemptPlanProfile, intent, accepted) -> None:
    """Bind already-dispatched history to this run's frozen plan declaration.

    This grants no new admission and does not rerun historical Stage 3 probes.
    The caller must first resolve the exact persisted dispatch bundle.
    """
    profile.task_contract.validate_intent(intent)
    intent_fields, plan_fields, policy = _proposal_inputs(profile, intent.repository_revision_sha256)
    for name, expected in intent_fields.items():
        if getattr(intent, name) != expected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical intent differs from frozen profile")
    expected_plan = propose_operation_plan(intent=intent, **plan_fields)
    if accepted.candidate.to_dict() != expected_plan.to_dict():
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical plan differs from frozen profile")
    decision = accepted.decision
    human = profile.approval_policy is not None
    expected_reason = PlanDecisionReason.GOVERNING_HUMAN_ACCEPTED if human else PlanDecisionReason.POLICY_ACCEPTED
    proof = decision.independence_proof
    if (
        decision.policy_sha256 != policy.sha256
        or decision.policy_version != policy.policy_version
        or decision.reason is not expected_reason
        or decision.intent_proposal_id != intent.proposal_id
        or decision.intent_sha256 != intent_payload_sha256(intent)
        or decision.validated_scope != expected_plan.allowed_scope
        or decision.capability_profile != expected_plan.capability_profile
        or decision.knowledge_snapshot_ref != intent.knowledge_snapshot_ref
        or decision.oracle_ref != expected_plan.operations[0].verification.condition_ref
        or decision.verification_obligations != plan_verification_obligations(expected_plan)
        or proof.authority_identity != (profile.governing_human_authority if human else profile.reviewer_authority)
        or proof.executor_identity != profile.executor
        or proof.proposer_identity != profile.plan_proposer
        or {item.value for item in proof.producer_actor_ids} != {profile.intent_proposer.value, profile.plan_proposer.value}
        or {item.value for item in proof.source_actor_ids} != {profile.intent_source_actor.value, profile.plan_source_actor.value}
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical plan authority differs from frozen profile")
    if human:
        profile.approval_policy.validate(
            decision.human_approval_ref, current=False, plan=accepted.candidate,
            intent=intent, policy_sha256=policy.sha256, executor=profile.executor,
        )


def _compatibility_validator(compatibility, history, snapshot_ref, profile):
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
        for binding in profile.target_records:
            resolved = binding_from_dict(
                binding.to_dict(), repo_root=profile.repository_root,
                consumer_revision=RepositoryRevision.git_commit(profile.task_contract.repository_revision_sha256),
            )
            if not profile.task_contract.allowed_scope.covers(resolved.path):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "target binding exceeds the task scope")
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
