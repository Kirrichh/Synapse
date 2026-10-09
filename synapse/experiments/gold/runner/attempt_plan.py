"""The declared plan a run's attempts are executed under (§26; plan Этап 12 §10).

One responsibility: turn one run's declared planning configuration into an
accepted operation plan and expose the stable semantic identity of what that
plan will do. Attempt-local proposal, snapshot and authority identities remain
provenance; they must not make the same operation look like a new hypothesis.
"""

from __future__ import annotations

from synapse.resource_usage import observed_operation

from dataclasses import dataclass, replace
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
from synapse.experiments.gold.admission import AdmittedKnowledgeHandle, validate_admitted_handle
from synapse.experiments.gold.gate_findings import validate_consumption_evidence_binding
from synapse.experiments.gold.run_compatibility import MintedCompatibilityEvidence
from synapse.experiments.gold.stage10.intent import (
    INTENT_SCHEMA_V3,
    AcceptanceKind,
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
    OPERATION_PLAN_SCHEMA_V2,
    validate_operation_plan_against_intent,
    topological_operation_order,
    FailureAction,
    OperationKind,
    OperationRecord,
    VerificationKind,
    VerificationObligation,
    propose_operation_plan,
    plan_verification_obligations,
    plan_semantic_sha256,
)
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V1, TASK_CONTRACT_SCHEMA_V3
from synapse.experiments.gold.task_targets import read_task_targets
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


def _plan_semantic_sha256(*, profile: "GoldAttemptPlanProfile", capability: str, plan=None, intent=None) -> str:
    """Identity of the operation/constraints, excluding attempt-local provenance."""

    if profile.task_contract.schema_version == TASK_CONTRACT_SCHEMA_V3:
        return plan_semantic_sha256(plan, intent=intent, policy_version=profile.policy_version)
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
    target_resolution: bytes | None = None
    replayed_feedback_required: bool = False
    full_positive_feedback_required: bool = False
    procedural_planning_required: bool = False

    def __post_init__(self) -> None:
        if type(self.procedural_planning_required) is not bool:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "procedural planning must be explicit")
        if self.procedural_planning_required and self.task_contract.schema_version != TASK_CONTRACT_SCHEMA_V3:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical tasks cannot acquire procedural planning")
        if type(self.task_contract) is not GoverningTaskContract:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan requires a governing task contract")
        if type(self.replayed_feedback_required) is not bool:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "replayed feedback profile must be explicit")
        if self.replayed_feedback_required and self.task_contract.schema_version != TASK_CONTRACT_SCHEMA_V3:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical tasks cannot acquire replay feedback")
        if type(self.full_positive_feedback_required) is not bool:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "positive feedback policy must be explicit")
        if type(self.repository_root) is not type(Path()) or not self.repository_root.is_absolute():
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan repository must be absolute")
        expected_targets = self.task_contract.target_bindings
        if self.task_contract.schema_version == TASK_CONTRACT_SCHEMA_V3:
            expected_targets = tuple(binding_to_ref(item) for item in read_task_targets(
                self.target_resolution, task=self.task_contract, repository_root=self.repository_root))
        elif self.target_resolution is not None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical task cannot acquire automatic targets")
        if type(self.target_records) is not tuple or tuple(binding_to_ref(item) for item in self.target_records) != expected_targets:
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


def _proposal_inputs(profile: GoldAttemptPlanProfile, repository_revision_sha256: str,
                     *, selected_behavior_refs: tuple[HashBoundRef, ...] = (), planning_basis=None):
    """One declaration feeds both operator preview and the actual proposals."""
    task = profile.task_contract
    if profile.procedural_planning_required != (planning_basis is not None):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan method basis differs from frozen profile")
    if repository_revision_sha256 != task.repository_revision_sha256:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan differs from the governing task revision")
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    expected = tuple(item for item in task.effects if item.disposition is EffectDisposition.EXPECTED)
    conditions = {item.verification_ref for item in expected}
    if not expected or len(conditions) != 1:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "controlled change needs one exact verification contract")
    condition = next(iter(conditions))
    historical = task.schema_version == TASK_CONTRACT_SCHEMA_V1
    behaviors = task.behavior_refs if historical else selected_behavior_refs
    if selected_behavior_refs and historical and not set(behaviors) <= set(selected_behavior_refs):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "required historical knowledge was not selected")
    task_fields = task.intent_fields()
    targets = tuple(binding_to_ref(item) for item in profile.target_records)
    task_fields["target_bindings"] = targets
    task_fields["behavior_refs"] = behaviors
    intent_fields = dict(
        **task_fields, task_contract_ref=task.reference,
        proposer=profile.intent_proposer,
        source_actors=(profile.intent_source_actor,), uncertainties=(),
    )
    command_criteria = tuple(item for item in task.acceptance
                             if item.kind is AcceptanceKind.VERIFICATION_COMMAND)
    if command_criteria and (
        task.schema_version != TASK_CONTRACT_SCHEMA_V3
        or profile.operation_kind is not OperationKind.EDIT_CONTROLLED_CHANGE
        or "verification.run" not in task.required_capabilities
        or any(item.condition_ref != condition for item in command_criteria)
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "verification steps require an explicit task v3 command contract")
    operations = [OperationRecord(
            operation_id=_OPERATION_ID, kind=profile.operation_kind,
            subject_paths=tuple(sorted({item.subject_path for item in expected if item.subject_path is not None})),
            input_refs=tuple(sorted(targets + behaviors,
                                    key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256))),
            argv=(), depends_on=(),
            capability=capability,
            verification=VerificationObligation(
                kind=VerificationKind.CONTRACT_CONDITION, condition_ref=condition,
                failure_action=FailureAction.ABORT_PLAN,
            ),
            effect_constraint_ids=tuple(sorted(item.constraint_id for item in expected)),
            acceptance_criterion_ids=tuple(sorted(item.criterion_id for item in task.acceptance
                if item.kind is not AcceptanceKind.VERIFICATION_COMMAND)),
        )]
    if planning_basis is not None:
        from ..stage10.planning_basis import method_groups, read_planning_basis
        basis = read_planning_basis(planning_basis)
        if basis["target_paths"] != list(operations[0].subject_paths):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "method search targets differ from task effects")
        main, operations = operations[0], []
        targets_by_path = {item.path: [] for item in profile.target_records}
        for item in profile.target_records:
            targets_by_path[item.path].append(binding_to_ref(item))
        for index, (paths, method) in enumerate(method_groups(planning_basis), 1):
            if method is not None and method not in behaviors:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "chosen method is not admitted")
            inputs = tuple(ref for path in paths for ref in targets_by_path[path])
            if method is not None:
                inputs += (method,)
            operations.append(replace(main,
                operation_id=f"operation-edit-{index}", subject_paths=paths,
                input_refs=tuple(sorted(inputs, key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256))),
                depends_on=() if index == 1 else (operations[-1].operation_id,),
                effect_constraint_ids=tuple(sorted(item.constraint_id for item in expected if item.subject_path in paths)),
                acceptance_criterion_ids=main.acceptance_criterion_ids if index == 1 else ()))
    # C1 remains the sole effect executor. These are its task-declared checks,
    # in the same order, and each receives its own retained-report obligation.
    # The composition owner binds the entire command list to the frozen C1
    # policy before this proposal or the operator preview can be accepted.
    for index, criterion in enumerate(command_criteria, 1):
        operations.append(OperationRecord(
            operation_id=f"operation-check-{index}", kind=OperationKind.RUN_VERIFICATION_COMMAND,
            subject_paths=(), input_refs=(condition,), argv=criterion.argv,
            depends_on=(operations[-1].operation_id,), capability="verification.run",
            verification=VerificationObligation(VerificationKind.COMMAND_RESULT,
                condition, FailureAction.ABORT_PLAN),
            acceptance_criterion_ids=(criterion.criterion_id,),
        ))
    capabilities = tuple(sorted({item.capability for item in operations}))
    operation_kinds = tuple(sorted({item.kind for item in operations}, key=lambda item: item.value))
    plan_fields = dict(
        proposer=profile.plan_proposer,
        source_actors=(profile.plan_source_actor,),
        allowed_scope=task.allowed_scope,
        capability_profile=capabilities,
        operations=tuple(operations),
        **({"planning_basis": planning_basis} if planning_basis is not None else {}),
    )
    policy = PlanAuthorityPolicy(
        schema_version=PLAN_POLICY_SCHEMA_V1,
        policy_version=profile.policy_version,
        allowed_operation_kinds=operation_kinds,
        allowed_capabilities=capabilities,
        human_review_capabilities=capabilities if profile.approval_policy is not None else (),
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
    planning_basis = None
    if profile.procedural_planning_required:
        from ..stage10.planning_basis import create_planning_basis
        planning_basis = create_planning_basis(
            target_paths=tuple(sorted({item.path for item in profile.target_records})), alternatives=[])
    intent_fields, plan_fields, policy = _proposal_inputs(profile, manifest.config.base_revision,
                                                         planning_basis=planning_basis)
    if planning_basis is not None:
        from ..stage10.planning_basis import read_planning_basis
        plan_fields["planning_basis"] = read_planning_basis(planning_basis)
    request = approval.request_for_contract(
        intent_contract={"schema_version": INTENT_SCHEMA_V3,
                         **{key: _approval_field(value) for key, value in intent_fields.items()}},
        plan_contract={"schema_version": OPERATION_PLAN_SCHEMA_V2 if planning_basis is not None else OPERATION_PLAN_SCHEMA_V1,
                       "repository_revision_sha256": manifest.config.base_revision,
                       "execution_order": list(topological_operation_order(plan_fields["operations"])),
                       **{key: _approval_field(value) for key, value in plan_fields.items()}},
        policy_sha256=policy.sha256, executor=profile.executor,
    )
    approval.review_request(request)


def previous_execution_feedback(previous_result, *, full_positive_required=False):
    """Project the exact independently verified predecessor into neutral data."""
    if type(full_positive_required) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "positive feedback policy must be explicit")
    if previous_result is None:
        return ()
    previous_result.validate_identity()
    if previous_result.verified_patch_sha256 is None:
        return ()
    if (full_positive_required and previous_result.oracle_resolved is True
            and previous_result.structured_outcome["payload"]["status"] != "FULL"):
        # The complete result remains in the attempt archive. This neutral
        # selection port cannot rank an incomplete task as a verified solution
        # just because one oracle returned true. Negative observations retain
        # their value independently of positive-method preference.
        return ()
    return (ExecutionFeedback(
        source_result_ref=HashBoundRef(
            kind=RefKind.ARTIFACT, ref_id=previous_result.result_sha256,
            schema_id=GOLD_ATTEMPT_RESULT_SCHEMA_V4, sha256=previous_result.result_sha256,
            byte_length=len(previous_result.canonical_bytes()), media_type="application/json"),
        evaluated_patch_sha256=previous_result.verified_patch_sha256,
        oracle_resolved=previous_result.oracle_resolved),)


@observed_operation("plan.accept")
def accept_attempt_plan(
    *,
    profile: GoldAttemptPlanProfile,
    repository_revision_sha256: str,
    knowledge_snapshot_ref: HashBoundRef,
    compatibility: MintedCompatibilityEvidence,
    compatibility_history: FileCompatibilityStore,
    admitted_knowledge: AdmittedKnowledgeHandle,
    previous_result: GoldAttemptResult | None = None,
    replayed_feedback: tuple[ExecutionFeedback, ...] = (),
    lineage_sources=None,
) -> AcceptedAttemptPlan:
    """Take one declared profile through intent, plan, decision and acceptance."""

    if type(profile) is not GoldAttemptPlanProfile:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan profile must be exact")
    if type(knowledge_snapshot_ref) is not HashBoundRef:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge snapshot ref must be exact")
    validate_admitted_handle(admitted_knowledge)
    selected = admitted_knowledge.subject_refs
    capability = CAPABILITY_BY_OPERATION[profile.operation_kind]
    if (type(replayed_feedback) is not tuple
            or any(type(item) is not ExecutionFeedback for item in replayed_feedback)
            or replayed_feedback and not profile.replayed_feedback_required):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "replay feedback differs from the frozen profile")
    feedback = previous_execution_feedback(previous_result,
        full_positive_required=profile.full_positive_feedback_required) + replayed_feedback
    planning_basis = None
    if profile.procedural_planning_required:
        from .procedural_observations import read_procedural_observations
        planning_basis = read_procedural_observations(catalog=lineage_sources,
            target_records=profile.target_records, selected_subject_refs=selected)
    intent_fields, plan_fields, policy = _proposal_inputs(
        profile, repository_revision_sha256, selected_behavior_refs=selected, planning_basis=planning_basis,
    )
    intent = propose_intent(**intent_fields, knowledge_snapshot_ref=knowledge_snapshot_ref, execution_feedback=feedback)
    plan = propose_operation_plan(intent=intent, **plan_fields)
    authority = configure_plan_authority(
        policy=policy,
        task_contract=profile.task_contract,
        target_resolution=profile.target_resolution,
        repository_root=profile.repository_root if profile.target_resolution is not None else None,
        approval_policy=profile.approval_policy,
        reviewer_authority=profile.reviewer_authority,
        governing_human_authority=profile.governing_human_authority,
        compatibility_validator=_compatibility_validator(
            compatibility, compatibility_history, knowledge_snapshot_ref, profile,
            admitted_knowledge, planning_basis=planning_basis,
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
        semantic_sha256=_plan_semantic_sha256(profile=profile, capability=capability, plan=plan, intent=intent),
    )


def validate_recorded_attempt_plan(*, profile: GoldAttemptPlanProfile, intent, accepted,
                                   selected_behavior_refs: tuple[HashBoundRef, ...],
                                   expected_semantic_sha256: str, lineage_sources=None) -> None:
    """Bind already-dispatched history to this run's frozen plan declaration.

    This grants no new admission and does not rerun historical Stage 3 probes.
    The caller must first resolve the exact persisted dispatch bundle.
    """
    profile.task_contract.validate_intent(intent,
        resolved_target_bindings=tuple(binding_to_ref(item) for item in profile.target_records))
    if (type(selected_behavior_refs) is not tuple or not selected_behavior_refs
            or any(type(ref) is not HashBoundRef or ref.kind is not RefKind.ARTIFACT for ref in selected_behavior_refs)
            or len(set(selected_behavior_refs)) != len(selected_behavior_refs)):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical plan has no exact selected knowledge basis")
    planning_basis = accepted.candidate.planning_basis
    if profile.procedural_planning_required:
        from .procedural_observations import read_procedural_observations
        observed_basis = read_procedural_observations(catalog=lineage_sources,
            target_records=profile.target_records, selected_subject_refs=selected_behavior_refs)
        if observed_basis != planning_basis:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "saved methods differ from physical replay observations")
    intent_fields, plan_fields, policy = _proposal_inputs(
        profile, intent.repository_revision_sha256, selected_behavior_refs=selected_behavior_refs,
        planning_basis=planning_basis,
    )
    for name, expected in intent_fields.items():
        if getattr(intent, name) != expected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "historical intent differs from frozen profile")
    expected_plan = accepted.candidate
    validate_operation_plan_against_intent(expected_plan, intent=intent)
    for name, expected in plan_fields.items():
        if getattr(expected_plan, name) != expected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "saved operations differ from their verified method decision")
    actual_semantics = _plan_semantic_sha256(profile=profile,
        capability=CAPABILITY_BY_OPERATION[profile.operation_kind], plan=accepted.candidate, intent=intent)
    if expected_semantic_sha256 != actual_semantics:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "recorded plan semantics differ from its actual operations")
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


def _compatibility_validator(compatibility, history, snapshot_ref, profile, admitted_knowledge, *, planning_basis=None):
    """Resolve the independently minted evidence; ref equality alone grants nothing."""

    if type(compatibility) is not MintedCompatibilityEvidence or type(history) is not FileCompatibilityStore:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan requires durable compatibility evidence")
    if not compatibility.decisions:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan has no compatibility decisions")
    expected_refs = tuple(sorted(
        (compatibility_record_ref(item) for item in compatibility.decisions),
        key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256),
    ))
    validate_admitted_handle(admitted_knowledge)
    selected = admitted_knowledge.subject_refs
    bindings = {
        validate_consumption_evidence_binding(item).subject_ref: item
        for item in compatibility.consumption_bindings
    }
    if not set(selected) <= set(bindings):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "selected knowledge lacks consumption evidence")
    if any(bindings[ref].original_decision not in compatibility.decisions for ref in selected):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "selected knowledge names another compatibility decision")

    def validate(plan: object, intent: object, evidence_refs: tuple[HashBoundRef, ...]):
        validate_admitted_handle(admitted_knowledge)
        if plan.planning_basis != planning_basis:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "plan changed its observed method basis")
        if planning_basis is not None:
            expected_intent, expected_plan, _ = _proposal_inputs(
                profile, profile.task_contract.repository_revision_sha256,
                selected_behavior_refs=selected, planning_basis=planning_basis)
            if (any(getattr(intent, name) != value for name, value in expected_intent.items())
                    or any(getattr(plan, name) != value for name, value in expected_plan.items())):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH,
                            "proposed operations differ from their physical method observations")
        if admitted_knowledge.subject_refs != selected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "selected knowledge handle changed")
        if profile.task_contract.schema_version == TASK_CONTRACT_SCHEMA_V1:
            if not set(intent.behavior_refs) <= set(selected):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "intent requires unselected knowledge")
        elif intent.behavior_refs != selected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "intent differs from this attempt's selected knowledge")
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
