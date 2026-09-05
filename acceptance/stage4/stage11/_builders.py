"""Assemble Stage 11 acceptance runs through the production composition roots.

The only stand-ins here are external actors: a deterministic oracle and a real
subprocess used as the coding worker. Retrieval, replay, point-of-use
admission, plan authorization, Stage 10 persistence/dispatch, C1 delegation,
and Stage 11 recovery are production objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

from synapse.experiments.gold.contracts import ActorIdentity, AttemptId, AuthorityIdentity, RunId
from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    RefKind,
    content_key_digest,
)
from synapse.experiments.gold.runner.attempt_input_source import GoldAttemptInputSource
from synapse.experiments.gold.runner.attempt_inputs import (
    KnowledgeDependencyUnavailable,
    PreparedAttemptInputs,
)
from synapse.experiments.gold.runner.attempt_plan import GoldAttemptPlanProfile
from synapse.experiments.gold.runner.attempt_worktree import GitAttemptWorktrees
from synapse.experiments.gold.runner.c1_boundary import C1AttemptBoundary
from synapse.experiments.gold.runner.models import (
    GoldRunBudgets,
    GoldRunConfig,
    GoldRunManifest,
    GoldRunVersions,
    GoldReplicatePolicy,
)
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage10.context import (
    ContextSizeBudget,
    ExcludedKnowledgeRef,
    ExclusionReason,
)
from synapse.experiments.gold.stage10.plan_revalidation import CurrentPlanState
from synapse.experiments.gold.stage10_composition import (
    Stage10ProductionComposition,
    create_stage10_production_composition,
)
from synapse.experiments.swebench.contract import BaselineTask, OracleResult

import tests.gold_point_of_use_world as pou
from acceptance.stage4.stage10._builders import hash_ref
from acceptance.stage4.stage11._retrieval_inputs import acceptance_retrieval_bindings
from acceptance.stage4.stage11._worker_process import (
    WorkerProcessControl,
    create_worker_process,
)
from synapse.experiments.gold import replay_composition as RC
from tests.stage4_gold_replay_support import GAS
from tests.test_swebench_gold_runner import (
    NEW_SOURCE,
    build_candidate_repo,
    make_writer,
    policy,
)


SPECIFICATION_DIGEST = hashlib.sha256(b"Stage 4 Gold Specification v2.2").hexdigest()
POLICY_DIGEST = hashlib.sha256(b"stage11-acceptance-policy-v1").hexdigest()
ORACLE_IDENTITY = "acceptance.stage4.stage11._builders.ScriptedOracle"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@dataclass
class ScriptedOracle:
    """External oracle whose independent verdicts are selected by a scenario."""

    outcomes: list[tuple[bool, bool]]
    calls: int = 0

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        if not worktree_path.is_dir():
            raise RuntimeError("oracle received no C1 worktree")
        resolved, infra_error = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return OracleResult(
            resolved=resolved,
            returncode=0 if resolved and not infra_error else 1,
            stdout="oracle stdout",
            stderr="oracle stderr",
            duration_seconds=0.01,
            diagnostics={"infra_error": infra_error, "task_id": task.task_id},
        )


def manifest_for(
    repo: Path,
    *,
    max_attempts: int,
    fallback_policy: FallbackPolicy,
    run_id: str,
) -> GoldRunManifest:
    base_revision = git(repo, "rev-parse", "HEAD")
    config = GoldRunConfig(
        task_id="calc-fix",
        instance_id="calc-1",
        base_revision=base_revision,
        provider="mini",
        model="acceptance-model",
        oracle_name=ORACLE_IDENTITY,
        environment_kind="TEST",
        budgets=GoldRunBudgets(
            maximum_wall_clock_seconds=3_600,
            maximum_worker_tokens=100_000,
            replay_gas_budget=GAS,
            replay_cognitive_budget=8,
        ),
        max_attempts=max_attempts,
        replicate_policy=GoldReplicatePolicy(
            group_id=f"{run_id}-replicates",
            replicate_count=1,
            replicate_index=1,
        ),
        fallback_policy=fallback_policy,
    )
    versions = GoldRunVersions(
        specification_version="2.2",
        specification_sha256=SPECIFICATION_DIGEST,
        implementation_revision=base_revision,
        policy_version="stage11-acceptance-policy-v1",
        policy_sha256=POLICY_DIGEST,
    )
    return GoldRunManifest.create(
        run_id=RunId(run_id),
        gold_run_id=run_id,
        config=config,
        versions=versions,
        inputs_sha256=hashlib.sha256(b"stage11-acceptance-inputs-v1").hexdigest(),
    )


def c1_boundary(
    repo: Path,
    run_root: Path,
    oracle: ScriptedOracle,
) -> C1AttemptBoundary:
    return C1AttemptBoundary(
        repo_root=repo,
        command_policy=policy(),
        oracle=oracle,
        writer=make_writer(run_root, repo),
        environment_kind="TEST",
    )


def _replayed_core() -> dict:
    """The published behavior core this suite's attempts retrieve and replay."""

    from tests.stage4_gold_replay_support import published_core, pure_behavior

    unit, _binding = pure_behavior()
    return published_core(unit)


def replay_preparation_for(context) -> SimpleNamespace:
    """The Stage 9 sides a governed replay needs, for one admitted attempt.

    Assembled from the attempt's own context rather than from a world cache.
    ``prepare_for`` cannot be used here: it takes a point-of-use admission in its
    constructor, and taking one before the attempt exists is the circularity the
    two-phase factory removes. Everything below is a *reading* of this run's
    world -- its stores, its published behavior, its reader -- and the subject
    ref comes from the gates that admitted this attempt.
    """

    from synapse.experiments.gold import replay as R
    from tests.gold_point_of_use_world import artifact_reader
    from tests.stage4_gold_replay_support import (
        compile_behavior_unit,
        policy_bundle,
        published_core,
        pure_behavior,
    )

    unit, _binding = pure_behavior()
    core = published_core(unit)
    digest = unit.content_key.digest_sha256
    admitted = next(
        (item for item in context.environment.subjects if item.ref_id == digest), None
    )
    if admitted is None:
        raise RuntimeError("this attempt admitted no subject naming the replayed behavior")
    return SimpleNamespace(
        bundle=policy_bundle(core, ()),
        artifact_reader=artifact_reader(core, ()),
        subjects=(R.replay_subject(subject_ref=admitted, unit=unit),),
        compiler=compile_behavior_unit,
    )


@dataclass
class _FixtureReplayBinding:
    """This suite's ``AttemptReplayBindingPort`` bound to one admitted attempt."""

    scope: dict

    def bind(self, context):
        with pou.authority_identity_scope(**self.scope):
            preparation = replay_preparation_for(context)
            bundle = preparation.bundle
        return RC.GoldAttemptReplay(
            bindings=RC.AttemptReplayBindings(
                replay_store=bundle.replay_store,
                activity_store=bundle.activity_store,
                activity_policy_store=bundle.activity_policy_store,
                activity_policy_evaluator=bundle.evaluator,
                artifact_reader=preparation.artifact_reader,
            ),
            subjects=preparation.subjects,
            compiler=preparation.compiler,
            admission_source=context.mint_admission,
            budgets=RC.ReplayBudgets(
                gas_budget=GAS,
                cognitive_budget=8,
                step_limit=1_000,
            ),
        )


def _plan_profile(repo: Path, manifest: GoldRunManifest) -> GoldAttemptPlanProfile:
    from synapse.experiments.gold.bindings import (
        BINDING_CONTRACT_VERSION_V1, PYTHON_BINDING_RESOLVER_V1,
        PythonSymbolKind, resolve_python_binding, binding_to_ref,
    )
    from synapse.experiments.gold.contracts import RepositoryRevision
    from synapse.experiments.gold.canonicalization import library_subject_ref
    from synapse.experiments.gold.runner.c1_boundary import command_policy_reference
    from synapse.experiments.gold.stage10.intent import (
        AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind,
    )
    from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
    from synapse.experiments.gold.stage10.planning import CAPABILITY_BY_OPERATION, OperationKind
    from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
    from tests.test_stage4_gold_compatibility import _behavior

    target = resolve_python_binding(
        repo, repository_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
        path="src/calc.py", module="src.calc", qualname="add", symbol_kind=PythonSymbolKind.FUNCTION,
        contract_version=BINDING_CONTRACT_VERSION_V1, resolver_version=PYTHON_BINDING_RESOLVER_V1,
    )
    unit, blob, behavior_manifest = _behavior(core_payload=_replayed_core())
    subject = library_subject_ref(
        content_key=unit.content_key.value, manifest_id=behavior_manifest.manifest_id.value,
        blob_digest_sha256=unit.content_key.digest_sha256,
        manifest_digest_sha256=behavior_manifest.manifest_id.digest_sha256,
    )
    condition = command_policy_reference(policy())
    task = GoverningTaskContract(
        task_id="calc-fix", task_statement="Fix add(a, b).",
        repository_revision_sha256=manifest.config.base_revision,
        allowed_scope=create_repository_scope(("src",)),
        required_capabilities=(CAPABILITY_BY_OPERATION[OperationKind.EDIT_CONTROLLED_CHANGE],),
        target_bindings=(binding_to_ref(target),), behavior_refs=(subject,),
        effects=(EffectConstraint("effect-main", EffectDisposition.EXPECTED,
                                  EffectKind.PATH_MODIFIED, "src/calc.py", condition),),
        acceptance=(AcceptanceCriterion("acceptance-main", AcceptanceKind.CONTRACT_CONDITION, condition),),
    )
    return GoldAttemptPlanProfile(
        task_contract=task, target_records=(target,), repository_root=repo,
        intent_proposer=ActorIdentity("acceptance-intent-producer"),
        intent_source_actor=ActorIdentity("acceptance-requirement-source"),
        plan_proposer=ActorIdentity("acceptance-plan-producer"),
        plan_source_actor=ActorIdentity("acceptance-plan-source"),
        executor=ActorIdentity("acceptance-executor"),
        reviewer_authority=AuthorityIdentity("acceptance-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("acceptance-governing-human"),
        policy_version="acceptance-plan-policy-v1",
    )


@dataclass
class ProductionAttemptInputs:
    """Test-side supplier of coherent production records for each attempt."""

    run_root: Path
    source_repo: Path
    plan_profile: GoldAttemptPlanProfile | None = None
    refusal_attempts: set[int] = field(default_factory=set)
    unavailable_attempts: set[int] = field(default_factory=set)
    delivery_unavailable_attempts: set[int] = field(default_factory=set)
    reused_inputs: dict[int, int] = field(default_factory=dict)
    environment_suffix: str = "primary"
    calls: list[int] = field(default_factory=list)
    prepared: dict[int, PreparedAttemptInputs] = field(default_factory=dict)
    cases: dict[int, object] = field(default_factory=dict)
    case: object | None = None
    _cached_source: object | None = None

    def check_approval(self, *, manifest):
        from synapse.experiments.gold.runner.attempt_plan import check_attempt_plan_approval
        check_attempt_plan_approval(profile=self.plan_profile or _plan_profile(self.source_repo, manifest), manifest=manifest)

    def prepare(self, *, manifest, attempt_index: int, previous_context):
        self.calls.append(attempt_index)
        if attempt_index in self.unavailable_attempts:
            return KnowledgeDependencyUnavailable(
                attempt_index=attempt_index,
                detail_code="stage11_authority_unavailable",
            )
        reused = self.reused_inputs.get(attempt_index)
        if reused is not None:
            return self.prepared[reused]
        cached = self.prepared.get(attempt_index)
        if cached is not None:
            return cached
        created = self._prepare(
            manifest=manifest,
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        self.prepared[attempt_index] = created
        return created

    def _prepare(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        previous_context: object | None,
    ) -> PreparedAttemptInputs:
        inputs = self._source(manifest).prepare(
            manifest=manifest,
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        self.cases[attempt_index] = self.case
        if attempt_index in self.refusal_attempts:
            _revoke_point_of_use_subject(self.case)
        if attempt_index in self.delivery_unavailable_attempts:
            self.case.grant_dependency.available = False
        return inputs

    def _source(self, manifest: GoldRunManifest) -> GoldAttemptInputSource:
        if self._cached_source is not None:
            return self._cached_source
        scope = dict(
            run_id=manifest.run_id,
            attempt_id=AttemptId("1"),
            repository_revision=manifest.config.base_revision,
            policy_version="policy-v1",
            environment_profile_id=self._environment_profile(),
            retrieval_bindings=acceptance_retrieval_bindings(),
            retrieval_root=self.run_root / "attempt-authority",
            attempt_world_factory=True,
        )
        scope["replay_binding"] = _FixtureReplayBinding(scope=dict(scope))
        with pou.authority_identity_scope(**scope):
            case = pou.world(_replayed_core(), ())
            self.case = case
            source = GoldAttemptInputSource(
                worlds=case.factory,
                plan_profile=self.plan_profile or _plan_profile(self.source_repo, manifest),
                worktrees=GitAttemptWorktrees(
                    source_repo=self.source_repo,
                    worktree_root=(
                        self.run_root
                        / "worker-worktrees"
                        / self.environment_suffix
                    ),
                ),
                context_budget=ContextSizeBudget(),
            )
        self._cached_source = source
        return source

    def _environment_profile(self) -> str:
        root_digest = hashlib.sha256(
            str(self.run_root).encode("utf-8")
        ).hexdigest()[:16]
        return f"stage11-{self.environment_suffix}-{root_digest}"


@dataclass
class RunWorld:
    repo: Path
    run_root: Path
    manifest: GoldRunManifest
    composition: object
    boundary: C1AttemptBoundary
    oracle: ScriptedOracle
    worker_process: WorkerProcessControl
    attempt_inputs: ProductionAttemptInputs
    stage10_composition: Stage10ProductionComposition
    run_record_fence: object
    patch_text: str

    @property
    def controller(self):
        return self.composition.controller

    def execute(self):
        return self.composition.execute()


def create_composition(world: RunWorld, *, attempt_inputs=None):
    from synapse.experiments.gold.runner_composition import create_gold_run_composition

    return create_gold_run_composition(
        run_root=world.run_root,
        manifest=world.manifest,
        c1_boundary=world.boundary,
        run_record_fence=world.run_record_fence,
        attempt_inputs=attempt_inputs or world.attempt_inputs,
        stage10_composition=world.stage10_composition,
        verification_profile=_plan_profile(world.repo, world.manifest),
    )


def run_world(
    tmp_path: Path,
    *,
    max_attempts: int,
    fallback_policy: FallbackPolicy,
    oracle_outcomes: list[tuple[bool, bool]],
    worker_outcomes: tuple[str, ...] = ("PATCH",),
    refusal_attempts: set[int] | None = None,
    unavailable_attempts: set[int] | None = None,
    delivery_unavailable_attempts: set[int] | None = None,
    run_id: str = "acceptance-run",
) -> RunWorld:
    from synapse.experiments.gold.runner_composition import create_gold_run_composition
    from tests.gold_store_fence import fence_for

    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    manifest = manifest_for(
        repo,
        max_attempts=max_attempts,
        fallback_policy=fallback_policy,
        run_id=run_id,
    )
    oracle = ScriptedOracle(list(oracle_outcomes))
    boundary = c1_boundary(repo, run_root, oracle)
    worker_process = create_worker_process(
        tmp_path / "external-worker",
        outcomes=worker_outcomes,
        patch_source=NEW_SOURCE,
    )
    stage10_root = run_root / "stage10-owner"
    stage10_fence = fence_for(stage10_root)
    stage10 = create_stage10_production_composition(
        record_root=stage10_root / "records",
        mutation_fence=stage10_fence,
        mini_config=worker_process.config(model=manifest.config.model),
    )
    inputs = ProductionAttemptInputs(
        run_root=run_root,
        source_repo=repo,
        refusal_attempts=set(refusal_attempts or ()),
        unavailable_attempts=set(unavailable_attempts or ()),
        delivery_unavailable_attempts=set(delivery_unavailable_attempts or ()),
    )
    run_fence = fence_for(run_root / "run-record-owner")
    composition = create_gold_run_composition(
        run_root=run_root,
        manifest=manifest,
        c1_boundary=boundary,
        run_record_fence=run_fence,
        attempt_inputs=inputs,
        stage10_composition=stage10,
        verification_profile=_plan_profile(repo, manifest),
    )
    return RunWorld(
        repo=repo,
        run_root=run_root,
        manifest=manifest,
        composition=composition,
        boundary=boundary,
        oracle=oracle,
        worker_process=worker_process,
        attempt_inputs=inputs,
        stage10_composition=stage10,
        run_record_fence=run_fence,
        patch_text=patch_text,
    )


def with_admission_from(
    original: PreparedAttemptInputs,
    substitute: PreparedAttemptInputs,
) -> PreparedAttemptInputs:
    return replace(
        original,
        admission_request=substitute.admission_request,
    )


def record_paths(run_root: Path, kind: str) -> list[Path]:
    return sorted((run_root / "run-records" / kind).glob("*.json"))


def _evidence_ref(kind: RefKind, label: str) -> HashBoundRef:
    raw = label.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=kind,
        ref_id=digest,
        schema_id="acceptance.stage11.lifecycle-evidence/v1",
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def _revoke_point_of_use_subject(case) -> None:
    """Make fresh Stage 3 revalidation fail through a real lifecycle transition."""

    from synapse.experiments.gold.contracts import ActorIdentity
    from synapse.experiments.gold.lifecycle import (
        LifecycleAuthorityAction,
        LifecycleReasonCode,
        LifecycleState,
        configure_lifecycle_authority_evaluator,
        create_lifecycle_authority_proposal,
        create_revocation_decision,
    )
    from synapse.experiments.gold.provenance import behavior_attestation_to_ref

    harness = case.world
    lifecycle_store = case.binding.lifecycle_store
    subject_ref = behavior_attestation_to_ref(harness.attestation)
    proposal = create_lifecycle_authority_proposal(
        action=LifecycleAuthorityAction.REVOKE,
        subject_ref=subject_ref,
        replacement_ref=None,
        context=harness.lifecycle_context,
        proposer_identity=ActorIdentity("stage11-revocation-proposer"),
        producer_actor_ids=(harness.handle.configuration.lifecycle_writer_actor,),
        source_actor_ids=(ActorIdentity("stage11-revocation-source"),),
        evidence_refs=(
            _evidence_ref(RefKind.SOURCE_EVIDENCE, "revocation-evidence"),
        ),
        compatibility_refs=(),
        policy_refs=(
            _evidence_ref(RefKind.CONTRACT_CONDITION, "revocation-policy"),
        ),
        reason_codes=("REVOCATION_REQUIRED",),
        predecessor_decision_id=None,
        decision_sequence=1,
    )
    evaluator = configure_lifecycle_authority_evaluator(
        authority_handle=harness.handle,
        policy_version="synapse.stage11.acceptance.lifecycle-policy/v1",
        trusted_clock=lambda: case.now[0],
    )
    decision = create_revocation_decision(
        authority_handle=harness.handle,
        evaluator=evaluator,
        proposal=proposal,
        executor_identity=ActorIdentity("stage11-lifecycle-executor"),
    )
    lifecycle_store.persist_authority_decision(
        authority_handle=harness.handle,
        decision=decision,
    )
    head = harness.lifecycle_record
    lifecycle_store.append(
        authority_handle=harness.handle,
        subject_ref=subject_ref,
        context=harness.lifecycle_context,
        to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
        evidence_refs=(
            _evidence_ref(RefKind.SOURCE_EVIDENCE, "revocation-transition"),
        ),
        expected_predecessor_record_id=head.record_id.value,
        expected_subject_sequence=head.subject_sequence + 1,
        revocation_decision=decision,
    )


__all__ = [
    "ProductionAttemptInputs",
    "RunWorld",
    "ScriptedOracle",
    "c1_boundary",
    "create_composition",
    "manifest_for",
    "record_paths",
    "run_world",
    "with_admission_from",
]
