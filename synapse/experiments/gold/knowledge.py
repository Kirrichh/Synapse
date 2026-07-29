"""Repository knowledge snapshot coordination."""

from __future__ import annotations

from pathlib import Path

from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    validate_knowledge_admission_authority_binding,
)
from .coordination import (
    SnapshotCoordinationContext,
    _create_snapshot_coordination_context,
    validate_snapshot_coordination_context,
)
from .knowledge_contracts import (
    KnowledgeSnapshotFailureCode,
    _fail,
    _safe_text,
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
