"""Repository knowledge snapshot coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .admission_contracts import (
    AdmissionFailureCode,
    AdmissionViolation,
    KnowledgeBoundaryResolution,
    SealedKnowledgeBoundaryResolver,
    seal_knowledge_boundary_resolver,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    validate_knowledge_admission_authority_binding,
)
from .coordination import (
    SnapshotCoordinationContext,
    _create_snapshot_coordination_context,
    validate_snapshot_coordination_context,
)
from .contracts import IdentityDomain, RecordId
from .knowledge_contracts import (
    AtomicSnapshotBoundary,
    ConfiguredSnapshotCompletenessEvaluator,
    KnowledgeSnapshotFailureCode,
    RepositoryKnowledgeSnapshot,
    SnapshotCompletenessDecision,
    SnapshotManifestCore,
    _fail,
    _safe_text,
    require_configured_snapshot_completeness_evaluator,
    validate_atomic_snapshot_boundary,
    validate_repository_knowledge_snapshot,
    validate_snapshot_completeness_decision,
    validate_snapshot_manifest_core,
)
from .knowledge_store import (
    CommittedAtomicSnapshotBoundary,
    KnowledgeSnapshotStore,
    require_committed_atomic_snapshot_boundary,
)
from .snapshot_adapters import SnapshotSourceHeadReader


@dataclass(frozen=True)
class _CommittedKnowledgeBoundaryResolver:
    store: KnowledgeSnapshotStore

    def resolve_boundary(
        self,
        *,
        repository_knowledge_snapshot_id: RecordId,
        atomic_boundary_id: RecordId,
        commit_sequence: int,
    ) -> KnowledgeBoundaryResolution:
        if (
            type(repository_knowledge_snapshot_id) is not RecordId
            or repository_knowledge_snapshot_id.domain
            is not IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT
            or type(atomic_boundary_id) is not RecordId
            or atomic_boundary_id.domain
            is not IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY
            or type(commit_sequence) is not int
            or commit_sequence < 1
        ):
            raise AdmissionViolation(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary lookup identity is invalid",
            )
        repository_knowledge_snapshot_id.to_dict()
        atomic_boundary_id.to_dict()
        recovery = self.store.recover()
        if recovery.diagnostic is not None or recovery.trusted_head is None:
            raise AdmissionViolation(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary history has no exact trusted head",
            )
        head = recovery.trusted_head
        if (
            head.snapshot_id != repository_knowledge_snapshot_id
            or head.boundary_id != atomic_boundary_id
            or head.commit_sequence != commit_sequence
        ):
            raise AdmissionViolation(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary is not the current committed head",
            )
        return KnowledgeBoundaryResolution(
            schema_version=(
                "synapse.stage4.gold.knowledge-boundary-resolution/v1"
            ),
            repository_knowledge_snapshot_id=head.snapshot_id,
            atomic_boundary_id=head.boundary_id,
            commit_sequence=head.commit_sequence,
            context_fingerprint_sha256=head.context_fingerprint_sha256,
            admission_anchor=head.admission_anchor,
            compatibility_anchor=head.compatibility_anchor,
            valid_until_utc=head.valid_until_utc,
        )


def create_knowledge_boundary_resolver(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    store: KnowledgeSnapshotStore,
) -> SealedKnowledgeBoundaryResolver:
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if type(store) is not KnowledgeSnapshotStore:
        raise AdmissionViolation(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge boundary resolver requires the exact snapshot store",
        )
    store.require_authority_binding(authority_binding)
    implementation = _CommittedKnowledgeBoundaryResolver(store)
    return seal_knowledge_boundary_resolver(
        implementation=implementation,
        profile_id=overlay.knowledge_boundary_resolver_profile_id,
        component_identity=(
            overlay.knowledge_boundary_resolver_component_identity
        ),
    )


def create_snapshot_coordination_context(
    *,
    coordination_root: Path,
    fence_identity: str,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> SnapshotCoordinationContext:
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if not isinstance(coordination_root, Path) or not coordination_root.is_absolute():
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            "coordination_root must be an absolute Path",
        )
    normalized = Path(str(coordination_root))
    context = _create_snapshot_coordination_context(
        coordination_root=normalized,
        fence_identity=_safe_text(fence_identity, "fence_identity"),
        base_configuration_id_text=base.configuration_id.value,
        knowledge_admission_configuration_id_text=overlay.configuration_id.value,
        lock_path=normalized / "snapshot-coordinator.lock",
        journal_path=normalized / "snapshot-coordinator.journal",
    )
    validate_knowledge_coordination_context(
        context=context,
        authority_binding=authority_binding,
    )
    return context


def validate_knowledge_coordination_context(
    *,
    context: SnapshotCoordinationContext,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> None:
    validate_snapshot_coordination_context(context)
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if (
        context.base_configuration_id_text != base.configuration_id.value
        or context.knowledge_admission_configuration_id_text
        != overlay.configuration_id.value
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "coordination context authority identities differ",
        )


def commit_repository_knowledge_snapshot(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    coordination_context: SnapshotCoordinationContext,
    store: KnowledgeSnapshotStore,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    core: SnapshotManifestCore,
    decision: SnapshotCompletenessDecision,
    snapshot: RepositoryKnowledgeSnapshot,
    boundary: AtomicSnapshotBoundary,
    source_head_reader: SnapshotSourceHeadReader,
    trusted_clock: Callable[[], datetime],
) -> CommittedAtomicSnapshotBoundary:
    validate_knowledge_coordination_context(
        context=coordination_context,
        authority_binding=authority_binding,
    )
    require_configured_snapshot_completeness_evaluator(evaluator)
    validate_snapshot_manifest_core(core)
    validate_snapshot_completeness_decision(
        decision,
        evaluator=evaluator,
        core=core,
    )
    validate_repository_knowledge_snapshot(snapshot, evaluator=evaluator)
    validate_atomic_snapshot_boundary(boundary, evaluator=evaluator)
    if (
        type(store) is not KnowledgeSnapshotStore
        or store.coordination_context is not coordination_context
        or not callable(trusted_clock)
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "snapshot facade dependencies use another coordination boundary",
        )
    committed = store.commit(
        evaluator=evaluator,
        core=core,
        decision=decision,
        snapshot=snapshot,
        boundary=boundary,
        source_head_reader=source_head_reader,
    )
    require_committed_atomic_snapshot_boundary(
        committed,
        evaluator=evaluator,
        expected_store=store,
        trusted_clock=trusted_clock,
    )
    return committed
