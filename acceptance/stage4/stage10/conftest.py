from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

import pytest

from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.persistence import StoreMutationFencePort, store_transaction
from synapse.experiments.gold.stage10_composition import (
    create_stage10_production_composition,
)
from synapse.experiments.gold.stage10.context import (
    AdmittedKnowledgeItem,
    ContextPersistenceEvidence,
    WorkerContextRecord,
    build_worker_context,
)
from synapse.experiments.gold.stage10.plan_authority import AcceptedOperationPlan
from synapse.experiments.gold.stage10.plan_revalidation import (
    CurrentPlanState,
    PlanPersistenceEvidence,
    SideEffectAuthorization,
    authorize_first_side_effect,
)
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage10.retrieval_adapter import (
    context_knowledge_selection,
)
from synapse.experiments.gold.stage10.worker_context_adapter import WorkerDispatchResult
from synapse.worker.mini_adapter import MiniAdapterConfig
from tests.gold_store_fence import fence_for
from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

from acceptance.stage4.stage10._builders import plan_world


@dataclass(frozen=True)
class DeliveredStage10World:
    case: object
    accepted_plan: AcceptedOperationPlan
    context: WorkerContextRecord
    context_persistence: ContextPersistenceEvidence
    plan_persistence: PlanPersistenceEvidence
    authorization: SideEffectAuthorization
    store_fence: StoreMutationFencePort
    store: FileStage10RecordStore
    dispatch: WorkerDispatchResult
    transport_proof_path: Path
    worker_worktree: Path


@pytest.fixture(scope="session")
def stage10_delivery_world(tmp_path_factory: pytest.TempPathFactory) -> DeliveredStage10World:
    root = tmp_path_factory.mktemp("stage10-delivery-world")
    worker_worktree = root / "worker-repository"
    worker_worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worker_worktree, check=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=worker_worktree,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Stage10 Acceptance"],
        cwd=worker_worktree,
        check=True,
    )
    seed = worker_worktree / "synapse" / "experiments" / "gold" / "stage10" / "seed.txt"
    seed.parent.mkdir(parents=True)
    seed.write_text("clean worker repository\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worker_worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=worker_worktree, check=True)

    case = production_point_of_use_case(root / "point-of-use")
    intent, _plan, _policy, authority, _decision, accepted = plan_world(
        snapshot_ref=case.boundary.manifest_ref
    )
    stage10_root = root / "stage10-store"
    stage10_root.mkdir()
    store_fence = fence_for(stage10_root)
    transport_proof_path = root / "transport-proof.txt"
    worker_probe = root / "worker-probe.py"
    worker_probe.write_text(
        "import hashlib, pathlib, sys\n"
        "task = sys.argv[sys.argv.index('-t') + 1]\n"
        "pathlib.Path(sys.argv[1]).write_text("
        "hashlib.sha256(task.encode('utf-8')).hexdigest(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    composition = create_stage10_production_composition(
        record_root=stage10_root / "records",
        mutation_fence=store_fence,
        mini_config=MiniAdapterConfig(
            command=(sys.executable, str(worker_probe), str(transport_proof_path)),
            timeout_seconds=30,
            max_steps=1,
            cost_limit=0,
        ),
    )
    store = composition.record_store
    with store_transaction(store_fence) as ticket:
        plan_persistence = store.persist_plan_bundle(
            intent=intent,
            accepted_plan=accepted,
            ticket=ticket,
        )

    admitted = P.admit_for_use_now(
        case.handle,
        binding=case.binding,
        chain=case.chain,
        evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
    )
    descriptor = case.supported[0][1]
    subject_bytes = canonicalize_stage4_payload(
        {
            "content_key": descriptor.content_key.value,
            "manifest_id": descriptor.manifest_id.value,
        },
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    context = build_worker_context(
        intent=intent,
        accepted_plan=accepted,
        attempt_id=admitted.envelope.attempt_id,
        admitted_knowledge=admitted,
        knowledge_selection=context_knowledge_selection(
            retrieval_decision=case.chain.retrieval,
            admitted_knowledge=admitted,
        ),
        knowledge_items=(
            AdmittedKnowledgeItem(
                item_id="admitted-subject",
                ref=case.subject,
                content=subject_bytes,
                taint_classes=(),
                failed_hypothesis=False,
            ),
        ),
    )
    with store_transaction(store_fence) as ticket:
        context_persistence = store.persist_worker_context(context, ticket=ticket)

    current = CurrentPlanState(
        repository_revision_sha256=intent.repository_revision_sha256,
        knowledge_snapshot_ref=intent.knowledge_snapshot_ref,
        policy_sha256=authority.policy.sha256,
        admitted_knowledge=admitted,
        compatibility_revalidation=case.compatibility_probe.records[-1],
    )
    authorization = authorize_first_side_effect(
        accepted_plan=accepted,
        intent=intent,
        authority=authority,
        attempt_id=context.attempt_id,
        current_state_reader=lambda: current,
        admission_freshness_validator=lambda value: P.require_current_point_of_use_evidence(
            value,
            binding=case.binding,
        ),
        context_id=context.context_id,
        context_audit_sha256=context.audit_sha256,
        delivery_envelope_sha256=context.delivery_envelope.envelope_sha256,
        plan_bundle_sha256=plan_persistence.bundle_sha256,
    )
    dispatch = composition.worker_adapter.dispatch(
        worktree_path=worker_worktree,
        context=context,
        persistence=context_persistence,
        plan_persistence=plan_persistence,
        authorization=authorization,
    )
    with store_transaction(store_fence) as ticket:
        store.persist_delivery_receipt(dispatch.delivery_receipt, ticket=ticket)
    return DeliveredStage10World(
        case=case,
        accepted_plan=accepted,
        context=context,
        context_persistence=context_persistence,
        plan_persistence=plan_persistence,
        authorization=authorization,
        store_fence=store_fence,
        store=store,
        dispatch=dispatch,
        transport_proof_path=transport_proof_path,
        worker_worktree=worker_worktree,
    )
