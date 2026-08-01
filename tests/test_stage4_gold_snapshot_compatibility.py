from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from synapse.experiments.gold.canonicalization import (
    STAGE4_CANONICAL_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.compatibility import (
    CompatibilityDimension,
    CompatibilityFailureCode,
    CompatibilityViolation,
    DimensionResult,
    RevalidationStage,
    REQUIRED_COMPATIBILITY_DIMENSIONS,
)
from synapse.experiments.gold.compatibility_store import (
    CompatibilityStoredRecordKind,
    create_compatibility_durability_binding,
    create_compatibility_stored_artifact,
    open_compatibility_evidence_store,
    validate_compatibility_durability_binding,
)
from synapse.experiments.gold.contracts import (
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    SchemaVersion,
    create_common_envelope,
)
from synapse.experiments.gold.patch6_adapters import (
    configure_patch6_compatibility_adapter,
)
from synapse.experiments.gold.snapshot_compatibility import (
    COMPATIBILITY_CONTEXT_V2,
    COMPATIBILITY_DECISION_V2,
    COMPATIBILITY_EVIDENCE_V2,
    COMPATIBILITY_REVALIDATION_V2,
    CommittedCompatibilityRevalidation,
    CommittedSnapshotBoundCompatibility,
    SnapshotBoundCompatibilityContext,
    SnapshotBoundCompatibilityDecision,
    SnapshotBoundCompatibilityEvidence,
    SnapshotBoundCompatibilityRevalidation,
    configure_snapshot_compatibility,
    create_snapshot_bound_compatibility_context,
    evaluate_snapshot_bound_compatibility,
    evaluate_snapshot_bound_compatibility_revalidation,
    historical_compatibility_context_ref,
    require_snapshot_bound_compatibility_passed,
    snapshot_bound_compatibility_context_payload,
    snapshot_bound_compatibility_artifact_bytes,
    snapshot_bound_compatibility_artifact_ref,
    snapshot_bound_compatibility_record_identity,
)

from tests.test_stage4_gold_authority_overlay import build_authority_bundle
from tests.test_stage4_gold_compatibility import NOW as PATCH6_NOW, _make_harness
from tests.test_stage4_gold_knowledge_snapshot import build_snapshot_harness


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


class TickingClock:
    def __init__(self, current) -> None:
        self.current = current

    def __call__(self):
        observed = self.current
        self.current += timedelta(microseconds=1)
        return observed


def _stored_artifact(value: object):
    kind_by_type = {
        SnapshotBoundCompatibilityContext: CompatibilityStoredRecordKind.CONTEXT_V2,
        SnapshotBoundCompatibilityEvidence: CompatibilityStoredRecordKind.EVIDENCE_V2,
        SnapshotBoundCompatibilityDecision: CompatibilityStoredRecordKind.DECISION_V2,
        SnapshotBoundCompatibilityRevalidation: CompatibilityStoredRecordKind.REVALIDATION_V2,
    }
    kind = kind_by_type[type(value)]
    return create_compatibility_stored_artifact(
        record_kind=kind,
        record_identity=snapshot_bound_compatibility_record_identity(value),
        artifact_bytes=snapshot_bound_compatibility_artifact_bytes(value),
        artifact_ref=snapshot_bound_compatibility_artifact_ref(value),
    )


def build_snapshot_compatibility_harness(tmp_path: Path):
    patch6 = _make_harness(tmp_path / "patch6")
    authority = build_authority_bundle(
        base_handle=patch6.handle,
        compatibility_declaration_id=patch6.declaration.declaration_id,
        compatibility_evaluator_identity=patch6.declaration.evaluator_identity,
    )
    snapshot = build_snapshot_harness(
        tmp_path / "snapshot",
        authority=authority,
        repository_revision=patch6.context.repository_revision,
        policy_version=patch6.context.compatibility_policy,
    )
    adapter = configure_patch6_compatibility_adapter(patch6.evaluator)
    observed_at = PATCH6_NOW + timedelta(days=1)
    trusted_clock = TickingClock(observed_at)
    compatibility = configure_snapshot_compatibility(
        authority_binding=authority.binding,
        patch6_adapter=adapter,
        trusted_clock=trusted_clock,
    )
    store = open_compatibility_evidence_store(
        (tmp_path / "compatibility-store").resolve(),
        authority_binding=authority.binding,
        coordination_context=snapshot.store.coordination_context,
    )
    durability = create_compatibility_durability_binding(
        authority_binding=authority.binding,
        evidence_store=store,
        coordination_context=snapshot.store.coordination_context,
    )
    historical_ref = historical_compatibility_context_ref(
        patch6.context,
        compatibility=compatibility,
    )
    base = authority.base_handle.configuration
    overlay = authority.binding.knowledge_admission_authority_handle.configuration
    context_payload = snapshot_bound_compatibility_context_payload(
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        historical_context_ref=historical_ref,
        declared_input_refs=(historical_ref,),
        base_configuration_id=base.configuration_id,
        knowledge_admission_configuration_id=overlay.configuration_id,
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMPATIBILITY_CONTEXT_V2,
        canonical_payload_bytes=_canonical(context_payload),
        run_id=snapshot.core.envelope.run_id,
        attempt_id=snapshot.core.envelope.attempt_id,
        created_at_utc=observed_at,
        producer_component=patch6.declaration.evaluator_component_id,
        repository_revision=snapshot.core.envelope.repository_revision,
        policy_version=snapshot.core.envelope.policy_version,
        environment_profile_id=snapshot.core.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                snapshot.snapshot.snapshot_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                snapshot.boundary.atomic_boundary_id,
                LineageEdgeKind.REFERENCES,
            ),
        ),
    )
    context = create_snapshot_bound_compatibility_context(
        envelope=envelope,
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        historical_context_ref=historical_ref,
        declared_input_refs=(historical_ref,),
        compatibility=compatibility,
    )
    evaluation = evaluate_snapshot_bound_compatibility(
        compatibility=compatibility,
        context=context,
        historical_context=patch6.context,
        descriptor=patch6.descriptor,
        index_entry=patch6.entry,
        subject_ref=patch6.context.verification_refs[0],
        source_evidence_refs=patch6.context.verification_refs,
        valid_until_utc=observed_at + timedelta(hours=1),
    )
    context_commit = store.append(_stored_artifact(evaluation.context))
    evidence_commit = store.append(_stored_artifact(evaluation.evidence))
    decision_commit = store.append(_stored_artifact(evaluation.decision))
    committed = CommittedSnapshotBoundCompatibility(
        compatibility,
        patch6.context,
        evaluation.historical_decision,
        evaluation.context,
        context_commit,
        evaluation.evidence,
        evidence_commit,
        evaluation.decision,
        decision_commit,
    )
    return patch6, authority, snapshot, compatibility, store, durability, committed


def test_s4_acc_snapshot_compatibility_is_v2_durable_and_all_dimensions_pass(
    tmp_path: Path,
) -> None:
    _, _, _, compatibility, store, durability, committed = (
        build_snapshot_compatibility_harness(tmp_path)
    )

    validate_compatibility_durability_binding(
        durability,
        authority_binding=committed.compatibility.authority_binding,
    )
    require_snapshot_bound_compatibility_passed(
        committed,
        at_utc=PATCH6_NOW + timedelta(days=1, minutes=1),
    )
    assert tuple(item.dimension for item in committed.evidence.dimensions) == (
        REQUIRED_COMPATIBILITY_DIMENSIONS
    )
    assert all(
        item.result is DimensionResult.PASS
        for item in committed.evidence.dimensions
    )
    assert len(
        tuple(
            item
            for item in REQUIRED_COMPATIBILITY_DIMENSIONS
            if item is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN
        )
    ) == 1
    assert store.recover().diagnostic is None
    assert (
        store.require_inclusion(_stored_artifact(committed.decision))
        == committed.decision_commit
    )


def test_s4_acc_consumption_compatibility_requires_loading_then_fresh_revalidation(
    tmp_path: Path,
) -> None:
    patch6, _, _, compatibility, store, _, committed = (
        build_snapshot_compatibility_harness(tmp_path)
    )
    observed_heads = (patch6.context.verification_refs[0],)

    with pytest.raises(CompatibilityViolation) as missing_loading:
        evaluate_snapshot_bound_compatibility_revalidation(
            compatibility=compatibility,
            committed=committed,
            descriptor=patch6.descriptor,
            index_entry=patch6.entry,
            subject_ref=patch6.context.verification_refs[0],
            source_evidence_refs=patch6.context.verification_refs,
            stage=RevalidationStage.BEFORE_CONSUMPTION,
            observed_head_refs=observed_heads,
            prior=None,
        )
    assert (
        missing_loading.value.failure_code
        is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    )

    loading = evaluate_snapshot_bound_compatibility_revalidation(
        compatibility=compatibility,
        committed=committed,
        descriptor=patch6.descriptor,
        index_entry=patch6.entry,
        subject_ref=patch6.context.verification_refs[0],
        source_evidence_refs=patch6.context.verification_refs,
        stage=RevalidationStage.BEFORE_LOADING,
        observed_head_refs=observed_heads,
        prior=None,
    )
    loading_evidence_commit = store.append(_stored_artifact(loading.evidence))
    loading_commit = store.append(_stored_artifact(loading.record))
    committed_loading = CommittedCompatibilityRevalidation(
        compatibility,
        loading.evidence,
        loading_evidence_commit,
        loading.record,
        loading_commit,
    )
    consumption = evaluate_snapshot_bound_compatibility_revalidation(
        compatibility=compatibility,
        committed=committed,
        descriptor=patch6.descriptor,
        index_entry=patch6.entry,
        subject_ref=patch6.context.verification_refs[0],
        source_evidence_refs=patch6.context.verification_refs,
        stage=RevalidationStage.BEFORE_CONSUMPTION,
        observed_head_refs=observed_heads,
        prior=committed_loading,
    )

    assert consumption.record.stage is RevalidationStage.BEFORE_CONSUMPTION
    assert consumption.record.predecessor_revalidation_id == loading.record.revalidation_id


def test_s4_acc_historical_v1_decision_is_not_current_compatibility_authority(
    tmp_path: Path,
) -> None:
    patch6, _, _, _, _, _, _ = build_snapshot_compatibility_harness(tmp_path)

    with pytest.raises(CompatibilityViolation) as raised:
        require_snapshot_bound_compatibility_passed(
            patch6.decision,
            at_utc=PATCH6_NOW,
        )
    assert raised.value.failure_code is CompatibilityFailureCode.TRUSTED_OBJECT_FORGED


def test_s4_acc_snapshot_compatibility_rejects_envelope_context_substitution(
    tmp_path: Path,
) -> None:
    _, _, _, compatibility, _, _, committed = build_snapshot_compatibility_harness(
        tmp_path
    )
    object.__setattr__(
        committed.context.envelope,
        "policy_version",
        "synapse.stage4.gold.substituted-policy/v1",
    )

    with pytest.raises((CompatibilityViolation, ValueError)):
        require_snapshot_bound_compatibility_passed(
            committed,
            at_utc=PATCH6_NOW + timedelta(days=1),
        )


def test_snapshot_compatibility_schema_constants_are_exact() -> None:
    assert COMPATIBILITY_CONTEXT_V2 == "synapse.stage4.gold.compatibility-context/v2"
    assert COMPATIBILITY_EVIDENCE_V2 == "synapse.stage4.gold.compatibility-evidence/v2"
    assert COMPATIBILITY_DECISION_V2 == "synapse.stage4.gold.compatibility-decision/v2"
    assert COMPATIBILITY_REVALIDATION_V2 == "synapse.stage4.gold.compatibility-revalidation/v2"
