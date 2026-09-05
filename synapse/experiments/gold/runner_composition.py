"""Single production composition for one multi-attempt Gold run.

This root binds one run-record coordinator, one sealed Stage 10 composition,
one exact C1 boundary, and one coherent attempt-input port to the controller.
It does not accept phase callbacks, worker-result sources, transports, or a
second knowledge predicate.
"""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.stage10_composition import (
    Stage10ProductionComposition,
    require_stage10_production_composition,
)

from .runner.attempt_inputs import AttemptInputsPort, require_attempt_inputs_port
from .runner.attempt_plan import GoldAttemptPlanProfile
from .runner.c1_boundary import C1AttemptBoundary
from .runner.controller import GoldRunController
from .runner.controller_recovery import (
    AttemptPhaseMaterializer,
    require_attempt_phase_materializer,
)
from .runner.models import GoldRunConfig, GoldRunManifest
from .runner.records import RunRecordStore
from .runner.run_recovery import RunRecordRecovery
from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


_RUN_COMPOSITION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldRunProductionComposition:
    """Immutable identity snapshot for the only controller construction path."""

    __slots__ = (
        "_manifest",
        "_controller",
        "_run_root",
        "_record_store",
        "_record_recovery",
        "_run_record_fence",
        "_stage10_composition",
        "_c1_boundary",
        "_attempt_inputs",
        "_attempt_materializer",
        "_identity_snapshot",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> GoldRunProductionComposition:
        raise TypeError("GoldRunProductionComposition is factory-created")

    @property
    def manifest(self) -> GoldRunManifest:
        return self._manifest

    @property
    def controller(self) -> GoldRunController:
        return self._controller

    @property
    def run_root(self) -> Path:
        return self._run_root

    @property
    def record_store(self) -> RunRecordStore:
        return self._record_store

    @property
    def record_recovery(self) -> RunRecordRecovery:
        return self._record_recovery

    @property
    def stage10_composition(self) -> Stage10ProductionComposition:
        return self._stage10_composition

    def execute(self):
        """Drive or resume the bound run through its controller."""

        return require_gold_run_composition(self).controller.execute()

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")


def _validate_cross_owner_bindings(
    *,
    run_root: Path,
    manifest: GoldRunManifest,
    c1_boundary: C1AttemptBoundary,
    run_record_fence: FileSnapshotFence,
    stage10_composition: Stage10ProductionComposition,
) -> None:
    config = manifest.config
    if (
        config.provider != stage10_composition.worker_identity[0]
        or c1_boundary.environment_kind != config.environment_kind
        or c1_boundary.command_policy.task_id != config.task_id
        or c1_boundary.command_policy.instance_id != config.instance_id
        or c1_boundary.oracle_identity != config.oracle_name
        or c1_boundary.writer.run_root != run_root
        or c1_boundary.writer.repo_root != c1_boundary.repo_root
    ):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 boundary or closed worker provider differs from frozen config",
        )
    worker_model = stage10_composition.worker_identity[1]
    if worker_model is None or worker_model != config.model:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "Stage 10 worker model differs from the frozen run configuration",
        )
    stage10_fence = stage10_composition.record_store.mutation_fence
    if stage10_fence.coordinator_id() == run_record_fence.coordinator_id():
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run records and Stage 10 records require independent coordinators",
        )


def create_gold_run_composition(
    *,
    run_root: Path,
    verification_profile: GoldAttemptPlanProfile,
    manifest: GoldRunManifest,
    c1_boundary: C1AttemptBoundary,
    run_record_fence: FileSnapshotFence,
    attempt_inputs: AttemptInputsPort,
    stage10_composition: Stage10ProductionComposition,
    reusable_authority=None,
) -> GoldRunProductionComposition:
    """Construct the sole exact production controller graph.

    Validation and construction remain one linear factory so no partially
    assembled graph can escape between owner bindings or bypass the final
    sealed identity check.
    """

    if type(run_root) is not type(Path()):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be exact")
    if type(manifest) is not GoldRunManifest:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
    manifest.validate_identity()
    if type(manifest.config) is not GoldRunConfig:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest config must be exact")
    if type(c1_boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 boundary must be exact")
    if type(run_record_fence) is not FileSnapshotFence:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "run record fence must be the exact file coordinator",
        )
    inputs = require_attempt_inputs_port(attempt_inputs)
    stage10 = require_stage10_production_composition(stage10_composition)
    _validate_cross_owner_bindings(
        run_root=run_root,
        manifest=manifest,
        c1_boundary=c1_boundary,
        run_record_fence=run_record_fence,
        stage10_composition=stage10,
    )

    record_store = RunRecordStore(
        run_root,
        mutation_fence=run_record_fence,
    )
    record_recovery = RunRecordRecovery(
        store=record_store,
        fence=run_record_fence,
    )
    attempt_materializer = AttemptPhaseMaterializer(
        verification_profile=verification_profile,
        reusable_authority=reusable_authority,
        manifest=manifest,
        boundary=c1_boundary,
        stage10_record_store=stage10.record_store,
        worker_adapter=stage10.worker_adapter,
        run_root=run_root,
    )
    controller = GoldRunController(
        manifest=manifest,
        record_recovery=record_recovery,
        attempt_inputs=inputs,
        attempt_materializer=attempt_materializer,
        run_root=run_root,
    )
    result = object.__new__(GoldRunProductionComposition)
    fields = {
        "_manifest": manifest,
        "_controller": controller,
        "_run_root": run_root,
        "_record_store": record_store,
        "_record_recovery": record_recovery,
        "_run_record_fence": run_record_fence,
        "_stage10_composition": stage10,
        "_c1_boundary": c1_boundary,
        "_attempt_inputs": inputs,
        "_attempt_materializer": attempt_materializer,
        "_identity_snapshot": (
            manifest,
            controller,
            run_root,
            record_store,
            record_recovery,
            run_record_fence,
            stage10,
            c1_boundary,
            inputs,
            attempt_materializer,
        ),
        "_trusted_seal": _RUN_COMPOSITION_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_gold_run_composition(result)


def require_gold_run_composition(value: object) -> GoldRunProductionComposition:
    """Refuse a forged composition or any changed concrete binding."""

    if (
        type(value) is not GoldRunProductionComposition
        or getattr(value, "_trusted_seal", None) is not _RUN_COMPOSITION_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "an exact sealed run composition is required",
        )
    snapshot = getattr(value, "_identity_snapshot", None)
    current = (
        value._manifest,
        value._controller,
        value._run_root,
        value._record_store,
        value._record_recovery,
        value._run_record_fence,
        value._stage10_composition,
        value._c1_boundary,
        value._attempt_inputs,
        value._attempt_materializer,
    )
    if type(snapshot) is not tuple or len(snapshot) != len(current) or any(
        original is not bound for original, bound in zip(snapshot, current)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run composition identity changed",
        )
    value.manifest.validate_identity()
    require_stage10_production_composition(value.stage10_composition)
    require_attempt_phase_materializer(
        value._attempt_materializer,
        manifest=value.manifest,
        run_root=value.run_root,
    )
    if (
        value.record_recovery.store is not value.record_store
        or value.record_recovery.fence is not value._run_record_fence
        or value.record_store.record_root != value.run_root / "run-records"
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run store, recovery, fence, and root bindings differ",
        )
    _validate_cross_owner_bindings(
        run_root=value.run_root,
        manifest=value.manifest,
        c1_boundary=value._c1_boundary,
        run_record_fence=value._run_record_fence,
        stage10_composition=value.stage10_composition,
    )
    return value


def compose_frozen_gold_run(inputs) -> GoldRunProductionComposition:
    """Bind the existing controller graph from the manifest's frozen inputs."""
    from .bindings import binding_from_dict, binding_to_ref
    from .contracts import ActorIdentity, AuthorityIdentity, RepositoryRevision
    from .knowledge_environment import read_gold_project_declaration, open_gold_project
    from .run_attempt_world import ProjectAttemptWorlds
    from .run_inputs import FrozenGoldInputs
    from .runner.attempt_input_source import GoldAttemptInputSource
    from .runner.attempt_plan import GoldAttemptPlanProfile
    from .runner.attempt_worktree import GitAttemptWorktrees
    from .runner.c1_boundary import compose_c1_boundary, command_policy_from_payload, command_policy_reference
    from .stage10.approval import RunApprovalPolicy
    from .stage10.task_contract import GoverningTaskContract
    from .stage10.intent import AcceptanceKind, EffectDisposition, EffectKind
    from .stage10_composition import create_stage10_production_composition, decode_worker_configuration
    import re

    if type(inputs) is not FrozenGoldInputs:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run inputs must be exact frozen data")
    data = inputs.data
    declaration = data["declaration"]
    manifest = inputs.manifest
    root, repo = Path(data["run_root"]), Path(data["repo_root"])
    inputs.verify_runtime(root)
    if root.resolve().is_relative_to(repo.resolve()):
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "run state and operator approvals must be outside the worker repository")
    project = read_gold_project_declaration(Path(data["project_state_root"]))
    task = GoverningTaskContract.from_dict(declaration["task_contract"])
    policy = command_policy_from_payload(declaration["command_policy"])
    if (
        task.task_id != manifest.config.task_id or policy.task_id != task.task_id
        or task.task_statement != policy.statement
        or task.repository_revision_sha256 != manifest.config.base_revision
        or tuple(policy.allowed_scope) != task.allowed_scope.entries
        or project.repo_root != repo
    ):
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "governing task, project and C1 boundaries differ")
    verification_ref = command_policy_reference(policy)
    if any(item.condition_ref != verification_ref for item in task.acceptance) or any(item.verification_ref != verification_ref for item in task.effects):
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "task verification is not bound to the exact C1 policy")
    expected = tuple(item for item in task.effects if item.disposition is EffectDisposition.EXPECTED)
    # This frozen experiment executes one controlled file edit. Exact C1 scope
    # makes the existing materializer enforce the planned target physically.
    # Broader task kinds need their own executable verification contract.
    if (len(expected) != 1 or expected[0].kind is not EffectKind.PATH_MODIFIED
        or task.allowed_scope.entries != (expected[0].subject_path,)
        or any(item.kind is not AcceptanceKind.CONTRACT_CONDITION for item in task.acceptance)
        or any(item.disposition is EffectDisposition.FORBIDDEN and (
            item.kind not in (EffectKind.PATH_CREATED, EffectKind.PATH_DELETED)
            or item.subject_path != expected[0].subject_path
        ) for item in task.effects)):
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "controlled-file-edit profile requires one exact modification target and C1 verification")
    targets = tuple(binding_from_dict(
        item, repo_root=repo, consumer_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
    ) for item in declaration["target_records"])
    if tuple(binding_to_ref(item) for item in targets) != task.target_bindings:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "governing target refs differ from resolved records")
    target_paths = {item.path for item in targets}
    if any(item.subject_path is not None and item.subject_path not in target_paths for item in task.effects):
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "task effect names an unresolved target")
    namespace = declaration["actor_namespace"]
    if type(namespace) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}", namespace) is None:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "actor namespace must be explicit and bounded")
    if declaration["replay_profile"] != "pure-cvm/v1":
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "unknown frozen replay profile")
    human = project.identities.governing_human_authority
    if human is None:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "connected project has no governing operator")
    worker_config = decode_worker_configuration(declaration["worker"])
    if declaration["worker"]["provider"] != manifest.config.provider or worker_config.model != manifest.config.model:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "worker identity differs from frozen configuration")
    if worker_config.timeout_seconds > manifest.config.budgets.maximum_wall_clock_seconds:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, "worker timeout exceeds the frozen run budget")
    profile = GoldAttemptPlanProfile(
        task_contract=task, target_records=targets, repository_root=repo,
        intent_proposer=ActorIdentity(f"{namespace}.intent-proposer"), intent_source_actor=ActorIdentity(f"{namespace}.task-source"),
        plan_proposer=ActorIdentity(f"{namespace}.plan-proposer"), plan_source_actor=ActorIdentity(f"{namespace}.plan-source"),
        executor=ActorIdentity(f"{namespace}.executor"), reviewer_authority=AuthorityIdentity(f"{namespace}.plan-reviewer"),
        governing_human_authority=human, policy_version=manifest.versions.policy_version,
        approval_policy=RunApprovalPolicy(run_manifest_sha256=manifest.manifest_sha256,
                                          governing_human_authority=human, store_root=root / "approvals"),
    )
    stage10_root = root / "stage10"
    stage10_root.mkdir(exist_ok=True)
    stage10 = create_stage10_production_composition(
        record_root=stage10_root / "records", mutation_fence=FileSnapshotFence(stage10_root / "coordinator"),
        mini_config=worker_config,
    )
    from .stage12.reusable import ReusableVerificationAuthority
    reusable_project = open_gold_project(Path(data["project_state_root"]), trusted_heads=data["trusted_heads"])
    reusable_authority = ReusableVerificationAuthority(
        repository_root=repo, environment_profile_id=project.environment_profile_id,
        authority_handle=reusable_project.authority_handle, library=reusable_project.library,
        attestation_store=reusable_project.attestation_store, lifecycle_store=reusable_project.lifecycle_store,
        admission_journal=reusable_project.admission_journal, fence=reusable_project.fence,
    )
    return create_gold_run_composition(
        verification_profile=profile, reusable_authority=reusable_authority,
        run_root=root, manifest=manifest,
        c1_boundary=compose_c1_boundary(repo_root=repo, run_root=root, command_policy=policy,
                                        oracle_config=declaration["oracle"], environment_kind=manifest.config.environment_kind),
        run_record_fence=FileSnapshotFence(root / "run-coordinator"), stage10_composition=stage10,
        attempt_inputs=GoldAttemptInputSource(
            worlds=ProjectAttemptWorlds(inputs=inputs, task_contract=task), plan_profile=profile,
            worktrees=GitAttemptWorktrees(source_repo=repo, worktree_root=root / "worker-worktrees"),
        ),
    )


def execute_gold_project_run(*, run_root: Path, state_root: Path | None = None,
                             declaration_path: Path | None = None) -> tuple[int, dict[str, object]]:
    """Canonical application action: start or resume the same durable Gold run."""
    from .knowledge_environment import open_gold_project
    from .admission import GateDependencyUnavailable
    from .run_inputs import freeze_gold_inputs, persist_frozen_inputs, reopen_frozen_inputs
    from .stage10.approval import ApprovalRequired
    import shlex

    root = run_root.expanduser().resolve()
    try:
        if (state_root is None) != (declaration_path is None):
            raise ValueError("a new run requires both the connected project and input declaration")
        if state_root is not None:
            project = open_gold_project(state_root.expanduser().resolve())
            if root.is_relative_to(project.declaration.repo_root.resolve()) or root == project.declaration.state_root:
                raise ValueError("run state must be separate from the project and worker repository")
            inputs = freeze_gold_inputs(
                declaration_path=declaration_path.expanduser().resolve(), project=project, run_root=root,
            )
            persist_frozen_inputs(inputs, root)
        else:
            inputs = reopen_frozen_inputs(root)
        composition = compose_frozen_gold_run(inputs)
        result = composition.execute()
        return 0, {"status": result.final_status.value,
                   "outcome_status": result.structured_outcome["payload"]["status"],
                   "outcome_ref": result.structured_outcome["outcome_ref"],
                   "result": result.payload(), "run_root": str(root),
                   "worker_records": str(root / "gold_attempts.jsonl")}
    except ApprovalRequired as exc:
        command = ["python", "-m", "synapse", "approve", str(exc.request_path),
                   "--store", str(root / "approvals"), "--resume-run", str(root)]
        return 3, {"status": "APPROVAL_REQUIRED", "request_path": str(exc.request_path),
                   "run_root": str(root), "approve_command": shlex.join(command)}
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, GateDependencyUnavailable) as exc:
        return 1, {"status": "GOLD_UNAVAILABLE", "detail": str(exc)[:512], "run_root": str(root)}


__all__ = [
    "GoldRunProductionComposition",
    "create_gold_run_composition",
    "require_gold_run_composition",
]
