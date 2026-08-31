"""Assemble Stage 11 acceptance runs through the production composition roots.

The only stand-ins here are external actors: a deterministic oracle and a real
subprocess used as the coding worker.  Retrieval, replay, point-of-use
admission, plan authorization, Stage 10 persistence/dispatch, C1 delegation,
and Stage 11 recovery are production objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path
import subprocess

from synapse.experiments.gold.contracts import ActorIdentity, AttemptId, AuthorityIdentity, RunId
from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    RefKind,
    content_key_digest,
)
from synapse.experiments.gold.runner.attempt_environment import (
    create_gold_attempt_environment,
)
from synapse.experiments.gold.runner.attempt_input_source import GoldAttemptInputSource
from synapse.experiments.gold.runner.attempt_inputs import (
    KnowledgeDependencyUnavailable,
    NoNewKnowledge,
    PreparedAttemptInputs,
)
from synapse.experiments.gold.runner.attempt_plan import GoldAttemptPlanProfile
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
from acceptance.stage4.stage11._retrieval_inputs import durable_retrieval_factory
from acceptance.stage4.stage11._worker_process import (
    WorkerProcessControl,
    create_worker_process,
)
from tests.stage4_gold_replay_support import pure_prepared
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
            replay_gas_budget=1_000,
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


@dataclass
class _FixtureReplay:
    """Hand the production source the replay this fixture already ran."""

    replay_result: object

    def replay_for_attempt(self, *, manifest, attempt_index):
        return self.replay_result


@dataclass
class _FixtureRetrieval:
    """Hand over the durable retrieval the point-of-use world produced."""

    case: object

    def retrieve_for_attempt(self, *, manifest, attempt_index, evaluator, compatibility_context):
        return _RetrievedForAttempt(
            gate_decision=self.case.chain.retrieval,
            result=self.case.durable_retrieval_result.result,
        )


@dataclass(frozen=True)
class _RetrievedForAttempt:
    gate_decision: object
    result: object


@dataclass
class _FixtureWorktrees:
    """Clone the isolated worktree each attempt's worker process will edit."""

    source: "ProductionAttemptInputs"

    def worktree_for_attempt(self, *, manifest, attempt_index):
        return self.source._worker_worktree(
            attempt_index, revision=manifest.config.base_revision
        )


def _attempt_environment(case):
    """Seal the fixture's point-of-use world as the production environment."""

    ambient = case.world
    binding = case.binding
    return create_gold_attempt_environment(
        authority_handle=ambient.handle,
        admitted_handle=case.handle,
        declaration=ambient.declaration,
        library=ambient.library,
        repo_root=ambient.root,
        lifecycle_store=binding.lifecycle_store,
        attestation_store=binding.attestation_store,
        taint_store=binding.taint_store,
        admission_journal=binding.admission_journal,
        admission_causal_history=binding.admission_causal_history,
        compatibility_history=binding.compatibility_history,
        knowledge_store=binding.knowledge_store,
        controller=binding.controller,
        chain=case.chain,
        chain_evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
        knowledge_snapshot_ref=case.boundary.manifest_ref,
        consumer_context_ref=case.context_ref,
        subjects=case.subjects,
        supported=case.supported,
        snapshot_attempt_id=binding.snapshot_attempt_id,
        snapshot_evaluator_declaration=binding.snapshot_evaluator_declaration,
        snapshot_actor_set=binding.snapshot_actor_set,
        snapshot_independence_proof=binding.snapshot_independence_proof,
        observation=ambient.observation,
        observation_provider=ambient.observation_provider,
        evidence_resolver=lambda descriptor: ambient.catalog[descriptor.descriptor_id.value],
        conflict_assessor=ambient.evaluator._conflict_assessor,
        retriever_actor=ambient.evaluator.retriever_actor,
        consumer_actor=ambient.evaluator.consumer_actor,
        score_provider_actor=ambient.evaluator.score_provider_actor,
        trusted_clock=lambda: case.now[0],
    )


def _plan_profile() -> GoldAttemptPlanProfile:
    """The declaration this acceptance run plans its attempts under."""

    return GoldAttemptPlanProfile(
        task_statement="Fix add(a, b).",
        subject_path="src/calc.py",
        allowed_scope=("src",),
        intent_proposer=ActorIdentity("acceptance-intent-producer"),
        intent_source_actor=ActorIdentity("acceptance-requirement-source"),
        plan_proposer=ActorIdentity("acceptance-plan-producer"),
        plan_source_actor=ActorIdentity("acceptance-plan-source"),
        executor=ActorIdentity("acceptance-executor"),
        reviewer_authority=AuthorityIdentity("acceptance-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("acceptance-governing-human"),
        policy_version="acceptance-plan-policy-v1",
        condition_ref=hash_ref(RefKind.CONTRACT_CONDITION, "condition"),
        compatibility_evidence_ref=hash_ref(RefKind.SOURCE_EVIDENCE, "plan-compatibility"),
    )


@dataclass
class ProductionAttemptInputs:
    """Test-side supplier of coherent production records for each attempt."""

    run_root: Path
    source_repo: Path
    knowledge_available: dict[int, bool]
    refusal_attempts: set[int] = field(default_factory=set)
    unavailable_attempts: set[int] = field(default_factory=set)
    delivery_unavailable_attempts: set[int] = field(default_factory=set)
    reused_inputs: dict[int, int] = field(default_factory=dict)
    environment_suffix: str = "primary"
    calls: list[int] = field(default_factory=list)
    prepared: dict[int, PreparedAttemptInputs] = field(default_factory=dict)
    cases: dict[int, object] = field(default_factory=dict)

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
        if attempt_index > 1 and not self.knowledge_available.get(attempt_index, False):
            if previous_context is None:
                raise RuntimeError("later attempt has no previous durable context")
            return NoNewKnowledge(
                attempt_index=attempt_index,
                previous_retrieval_ref=previous_context.phase_refs.retrieval_ref,
            )
        cached = self.prepared.get(attempt_index)
        if cached is not None:
            return cached
        created = self._prepare(manifest=manifest, attempt_index=attempt_index)
        self.prepared[attempt_index] = created
        return created

    def _prepare(self, *, manifest: GoldRunManifest, attempt_index: int) -> PreparedAttemptInputs:
        """Mint this attempt's world, then let production assemble the inputs.

        The fixture supplies actors this repository does not have — a published
        behavior world and an isolated worktree. The ordering that turns them
        into one coherent attempt is production's: this method calls
        ``GoldAttemptInputSource`` and asserts nothing about how it works.
        """

        environment = self._environment_profile(attempt_index)
        retrieval_root = self.run_root / "attempt-authority" / str(attempt_index)
        with pou.authority_identity_scope(
            run_id=manifest.run_id,
            attempt_id=AttemptId(str(attempt_index)),
            repository_revision=manifest.config.base_revision,
            policy_version="policy-v1",
            environment_profile_id=environment,
            retrieval_result_factory=durable_retrieval_factory(retrieval_root),
        ):
            replay_preparation = pure_prepared()
            replay_result = replay_preparation.run()
            case = pou.world(replay_preparation.core, replay_preparation.extra)
            if case.durable_retrieval_result.result.causal_record is None:
                raise RuntimeError("durable retrieval produced no causal record")
            source = GoldAttemptInputSource(
                environment=_attempt_environment(case),
                plan_profile=_plan_profile(),
                replay=_FixtureReplay(replay_result),
                retrieval=_FixtureRetrieval(case),
                worktrees=_FixtureWorktrees(self),
                context_budget=ContextSizeBudget(),
            )
            inputs = source.prepare(
                manifest=manifest, attempt_index=attempt_index, previous_context=None
            )
            self.cases[attempt_index] = case
            if attempt_index in self.refusal_attempts:
                _revoke_point_of_use_subject(case)
            if attempt_index in self.delivery_unavailable_attempts:
                case.grant_dependency.available = False
            return inputs

    def _environment_profile(self, attempt_index: int) -> str:
        root_digest = hashlib.sha256(str(self.run_root).encode("utf-8")).hexdigest()[:16]
        return f"stage11-{self.environment_suffix}-{root_digest}-{attempt_index}"

    def _worker_worktree(self, attempt_index: int, *, revision: str) -> Path:
        target = (
            self.run_root
            / "worker-worktrees"
            / self.environment_suffix
            / str(attempt_index)
        )
        subprocess.run(
            ["git", "clone", "--quiet", "--no-local", str(self.source_repo), str(target)],
            text=True,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "--quiet", revision],
            cwd=target,
            text=True,
            capture_output=True,
            check=True,
        )
        return target


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
    """Rebuild the sealed composition over the same exact durable owners."""

    from synapse.experiments.gold.runner_composition import create_gold_run_composition

    return create_gold_run_composition(
        run_root=world.run_root,
        manifest=world.manifest,
        c1_boundary=world.boundary,
        run_record_fence=world.run_record_fence,
        attempt_inputs=attempt_inputs or world.attempt_inputs,
        stage10_composition=world.stage10_composition,
    )


def run_world(
    tmp_path: Path,
    *,
    max_attempts: int,
    fallback_policy: FallbackPolicy,
    oracle_outcomes: list[tuple[bool, bool]],
    worker_outcomes: tuple[str, ...] = ("PATCH",),
    new_knowledge: dict[int, bool] | None = None,
    refusal_attempts: set[int] | None = None,
    unavailable_attempts: set[int] | None = None,
    delivery_unavailable_attempts: set[int] | None = None,
    run_id: str = "acceptance-run",
) -> RunWorld:
    """Assemble one run through the sole Stage 10 and Stage 11 roots."""

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
        knowledge_available=dict(new_knowledge or {}),
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
    """Describe a caller attempting to pair A's evidence with B's authority."""

    return replace(original, admission_request=substitute.admission_request)


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
    subject_ref = behavior_attestation_to_ref(harness.attestation)
    proposal = create_lifecycle_authority_proposal(
        action=LifecycleAuthorityAction.REVOKE,
        subject_ref=subject_ref,
        replacement_ref=None,
        context=harness.lifecycle_context,
        proposer_identity=ActorIdentity("stage11-revocation-proposer"),
        producer_actor_ids=(harness.handle.configuration.lifecycle_writer_actor,),
        source_actor_ids=(ActorIdentity("stage11-revocation-source"),),
        evidence_refs=(_evidence_ref(RefKind.SOURCE_EVIDENCE, "revocation-evidence"),),
        compatibility_refs=(),
        policy_refs=(_evidence_ref(RefKind.CONTRACT_CONDITION, "revocation-policy"),),
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
    case.lifecycle_store.persist_authority_decision(
        authority_handle=harness.handle,
        decision=decision,
    )
    head = harness.lifecycle_record
    case.lifecycle_store.append(
        authority_handle=harness.handle,
        subject_ref=subject_ref,
        context=harness.lifecycle_context,
        to_state=LifecycleState.REVOKED,
        reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
        evidence_refs=(_evidence_ref(RefKind.SOURCE_EVIDENCE, "revocation-transition"),),
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
