"""Shared construction for the Stage 11 acceptance layer. Contains no tests.

Everything here is a production object assembled in the order a caller would
assemble it. Two things are deliberately *not* production and are named as what
they are: a scripted oracle, whose verdicts the acceptance layer chooses so that
each C1 status can be reached, and a scripted worker transport, which stands in
for the external process. Neither decides anything the run decides — the oracle
verdict still travels through the real C1 boundary, and the transport's evidence
is still verified by the Stage 10 owner before a delivery is accepted.

The §22 admission is real and cannot be otherwise: ``admit_for_use_now`` refuses
anything but a genuine production binding, so every attempt in these acceptances
crosses the barrier for real. That costs about six seconds per attempt over a
shared world, which is why the heavy suites are separate files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import subprocess

from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import RunId
from synapse.experiments.gold.runner import (
    AttemptPhaseRefs,
    C1AttemptBoundary,
    FallbackPolicy,
    GoldRunConfig,
    GoldRunManifest,
)
from synapse.experiments.gold.runner.delivery import AttemptDeliveryPlan
from synapse.experiments.gold.stage10.context import (
    ExcludedKnowledgeRef,
    ExclusionReason,
    build_worker_context,
)
from synapse.experiments.gold.stage10.retrieval_adapter import context_knowledge_selection
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerDeliveryEvidence,
    WorkerDeliveryStatus,
    WorkerInvocation,
)
from synapse.experiments.swebench.contract import BaselineTask, OracleResult

import tests.gold_point_of_use_world as pou
from acceptance.stage4.stage10._builders import plan_world
from tests.test_swebench_gold_runner import (
    NEW_SOURCE,
    build_candidate_repo,
    make_writer,
    policy,
    worker_result,
)

ACCEPTANCE_SCHEMA = "acceptance.stage4.runner/v1"
TRANSPORT_NAME = "stage11-acceptance-transport"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout


def hash_ref(kind: RefKind, label: str) -> HashBoundRef:
    raw = label.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=kind,
        ref_id=digest,
        schema_id=ACCEPTANCE_SCHEMA,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def phase_refs(index: int) -> AttemptPhaseRefs:
    """Upstream phase identities, distinct for every attempt index."""

    label = f"attempt-{index}"
    return AttemptPhaseRefs(
        knowledge_snapshot_ref=hash_ref(RefKind.KNOWLEDGE_SNAPSHOT, f"snapshot-{label}"),
        retrieval_ref=hash_ref(RefKind.ARTIFACT, f"retrieval-{label}"),
        replay_ref=hash_ref(RefKind.ARTIFACT, f"replay-{label}"),
        intent_ref=hash_ref(RefKind.ARTIFACT, f"intent-{label}"),
        plan_ref=hash_ref(RefKind.ARTIFACT, f"plan-{label}"),
        worker_context_id="ctx_" + hashlib.sha256(f"ctx-{label}".encode("utf-8")).hexdigest(),
        worker_context_audit_sha256=hashlib.sha256(
            f"audit-{label}".encode("utf-8")
        ).hexdigest(),
    )


@dataclass
class ScriptedOracle:
    """External oracle stand-in whose verdicts are scripted in attempt order."""

    outcomes: list[tuple[bool, bool]]
    calls: int = 0

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        del worktree_path
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
    run_id: str = "acceptance-run",
) -> GoldRunManifest:
    config = GoldRunConfig(
        task_id="calc-fix",
        instance_id="calc-1",
        base_revision=git(repo, "rev-parse", "HEAD").strip(),
        provider="acceptance-provider",
        model="acceptance-model",
        oracle_name="scripted-oracle",
        environment_kind="TEST",
        max_attempts=max_attempts,
        fallback_policy=fallback_policy,
    )
    manifest = GoldRunManifest.create(
        run_id=RunId(run_id), gold_run_id=run_id, config=config
    )
    manifest.validate_identity()
    return manifest


def c1_boundary(repo: Path, run_root: Path, oracle: ScriptedOracle) -> C1AttemptBoundary:
    return C1AttemptBoundary(
        repo_root=repo,
        command_policy=policy(),
        oracle=oracle,
        writer=make_writer(run_root, repo),
        environment_kind="TEST",
    )


def scripted_transport(invocation: WorkerInvocation) -> WorkerDeliveryEvidence:
    """Stand in for the worker process, reporting exactly what it received."""

    return WorkerDeliveryEvidence(
        invocation_id=invocation.invocation_id,
        context_id=invocation.context_id,
        payload_sha256=invocation.payload_sha256,
        payload_byte_length=invocation.payload_byte_length,
        envelope_sha256=invocation.envelope_sha256,
        status=WorkerDeliveryStatus.PROCESS_STARTED,
        transport_name=TRANSPORT_NAME,
    )


def invocation_for(context) -> WorkerInvocation:
    """Render the invocation for an exact context envelope."""

    envelope = context.delivery_envelope
    return WorkerInvocation(
        invocation_id="inv_" + hashlib.sha256(context.context_id.encode("utf-8")).hexdigest(),
        attempt_id=context.attempt_id.value,
        context_id=context.context_id,
        payload_text=envelope.prompt_text,
        payload_sha256=envelope.prompt_sha256,
        payload_byte_length=envelope.prompt_byte_length,
        envelope_sha256=envelope.envelope_sha256,
        allowed_scope=("synapse/experiments/gold/stage10",),
        capabilities=("edit_controlled_change",),
    )


_PLAN_PAIR: list = []


def _plan_pair():
    """One accepted plan for the whole module; building it is deterministic."""

    if not _PLAN_PAIR:
        intent, _plan, _policy, _authority, _decision, accepted = plan_world()
        _PLAN_PAIR.append((intent, accepted))
    return _PLAN_PAIR[0]


def worker_context_source(admitted):
    """Build the Stage 10 worker context *from* the admission just taken."""

    intent, accepted = _plan_pair()
    subject = pou.subject_ref()
    subject_bytes = canonicalize_stage4_payload(
        {"subject": subject.ref_id},
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    item_ref = HashBoundRef(
        kind=subject.kind,
        ref_id=subject.ref_id,
        schema_id=subject.schema_id,
        sha256=hashlib.sha256(subject_bytes).hexdigest(),
        byte_length=len(subject_bytes),
        media_type=subject.media_type,
    )
    selection = context_knowledge_selection(
        retrieval_decision=pou.world().chain.retrieval,
        admitted_knowledge=admitted,
    )
    # Stage 11 acceptance is about the run lifecycle and the §22 crossing, not
    # about what a prompt carries: every candidate is withheld explicitly so the
    # context is complete without restating Stage 10's own payload acceptance.
    return build_worker_context(
        intent=intent,
        accepted_plan=accepted,
        attempt_id=admitted.envelope.attempt_id,
        admitted_knowledge=admitted,
        knowledge_selection=selection,
        knowledge_items=(),
        excluded_refs=tuple(
            ExcludedKnowledgeRef(ref=ref, reason=ExclusionReason.NOT_SELECTED_FOR_TASK)
            for ref in selection.candidate_refs
        ),
    )


def delivery_plan_source(_context) -> AttemptDeliveryPlan:
    """One attempt's delivery inputs, with a fresh real §22 admission."""

    return AttemptDeliveryPlan(
        admission_request=pou.admission_request(),
        context_source=worker_context_source,
        invocation_source=invocation_for,
        transport=scripted_transport,
    )


@dataclass
class ScriptedWorker:
    """The candidates a run's attempts receive, consumed in attempt order."""

    outcomes: list[object]
    calls: int = 0

    def __call__(self, delivery) -> object:
        del delivery
        behaviour = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour


@dataclass
class RunWorld:
    """One assembled run: its repository, its records and its composition."""

    repo: Path
    run_root: Path
    manifest: GoldRunManifest
    composition: object
    oracle: ScriptedOracle
    worker: ScriptedWorker
    patch_text: str

    @property
    def controller(self):
        return self.composition.controller


def candidate_result(patch_text: str):
    """The worker candidate the C1 boundary materializes for an attempt."""

    return worker_result(patch_text)


def no_candidate_result():
    """A worker that produced no patch; C1 classifies this as NO_CANDIDATE."""

    return worker_result(None)


def run_world(
    tmp_path: Path,
    *,
    max_attempts: int,
    fallback_policy: FallbackPolicy,
    oracle_outcomes: list[tuple[bool, bool]],
    worker_outcomes: list[object] | None = None,
    new_knowledge: dict[int, bool] | None = None,
    run_id: str = "acceptance-run",
) -> RunWorld:
    """Assemble one run exactly as the production composition root does."""

    from synapse.experiments.gold.runner_composition import create_gold_run_composition
    from tests.gold_store_fence import fence_for

    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    oracle = ScriptedOracle(list(oracle_outcomes))
    manifest = manifest_for(
        repo, max_attempts=max_attempts, fallback_policy=fallback_policy, run_id=run_id
    )
    outcomes = list(worker_outcomes) if worker_outcomes is not None else [candidate_result(patch_text)]
    worker = ScriptedWorker(outcomes)

    def knowledge_available(index: int) -> bool:
        return bool(new_knowledge and new_knowledge.get(index, False))

    composition = create_gold_run_composition(
        run_root=run_root,
        manifest=manifest,
        c1_boundary=c1_boundary(repo, run_root, oracle),
        mutation_fence=fence_for(run_root),
        phase_refs_source=phase_refs,
        delivery_plan_source=delivery_plan_source,
        worker_result_source=worker,
        new_knowledge_available=knowledge_available,
    )
    return RunWorld(
        repo=repo,
        run_root=run_root,
        manifest=manifest,
        composition=composition,
        oracle=oracle,
        worker=worker,
        patch_text=patch_text,
    )


def record_paths(run_root: Path, kind: str) -> list[Path]:
    """Every durable record of one kind, in stable order."""

    return sorted((run_root / "run-records" / kind).glob("*.json"))
