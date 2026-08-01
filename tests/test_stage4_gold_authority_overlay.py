from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from synapse.experiments.gold.admission_contracts import (
    CONSUMPTION_REQUIRED_DIMENSIONS,
    INGESTION_REQUIRED_DIMENSIONS,
    PUBLICATION_REQUIRED_DIMENSIONS,
    RETRIEVAL_REQUIRED_DIMENSIONS,
)
from synapse.experiments.gold.authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    create_consumption_gate_evaluator_declaration,
    create_ingestion_gate_evaluator_declaration,
    create_knowledge_admission_authority_binding,
    create_publication_gate_evaluator_declaration,
    create_retrieval_gate_evaluator_declaration,
    create_snapshot_completeness_evaluator_declaration,
    create_stage4_knowledge_admission_authority_configuration,
    create_stage4_knowledge_admission_authority_handle,
    validate_knowledge_admission_authority_binding,
    validate_stage4_knowledge_admission_authority_configuration,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    ContractFailureCode,
    ContractViolation,
    IdentityDomain,
    RecordId,
    Stage4AuthorityHandle,
    compute_record_id,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)


KNOWLEDGE_RESOLVER_PROFILE = "synapse.stage4.gold.knowledge-boundary-resolver/v1"
RETRIEVAL_RESOLVER_PROFILE = "synapse.stage4.gold.retrieval-authority-resolver/v1"
COMPATIBILITY_RESOLVER_PROFILE = "synapse.stage4.gold.compatibility-resolver/v1"


@dataclass(frozen=True)
class AuthorityBundle:
    base_handle: Stage4AuthorityHandle
    compatibility_declaration_id: RecordId
    binding: KnowledgeAdmissionAuthorityBinding


def build_authority_bundle(
    *,
    base_handle: Stage4AuthorityHandle | None = None,
    compatibility_declaration_id: RecordId | None = None,
    compatibility_evaluator_identity: AuthorityIdentity | None = None,
) -> AuthorityBundle:
    if base_handle is None:
        base_configuration = create_stage4_authority_configuration(
            platform_attester_actor=ActorIdentity("p78-platform-attester"),
            builder_actor=ActorIdentity("p78-platform-builder"),
            taint_classifier_authority=AuthorityIdentity("p78-taint-classifier"),
            taint_reviewer_authority=AuthorityIdentity("p78-taint-reviewer"),
            supersession_reviewer_authority=AuthorityIdentity(
                "p78-supersession-reviewer"
            ),
            revocation_reviewer_authority=AuthorityIdentity(
                "p78-revocation-reviewer"
            ),
            lifecycle_writer_actor=ActorIdentity("p78-lifecycle-writer"),
            governing_human_authority=AuthorityIdentity("p78-governing-human"),
        )
        base_handle = create_stage4_authority_handle(base_configuration)
    compatibility_id = compatibility_declaration_id or compute_record_id(
        domain=IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION,
        canonical_bytes=b"p78-compatibility-evaluator-declaration",
    )
    compatibility_identity = (
        compatibility_evaluator_identity
        or AuthorityIdentity("p78-compatibility-evaluator")
    )

    def declaration(factory, name, dimensions):
        return factory(
            base_authority_handle=base_handle,
            evaluator_identity=AuthorityIdentity(f"p78-{name}-authority"),
            evaluator_component_identity=ActorIdentity(f"p78-{name}-component"),
            evaluator_component_version=f"synapse.stage4.gold.{name}-component/v1",
            required_checked_dimensions=dimensions,
            resolver_profile_ids=(f"synapse.stage4.gold.{name}-observation/v1",),
        )

    configuration = create_stage4_knowledge_admission_authority_configuration(
        base_authority_handle=base_handle,
        snapshot_completeness_evaluator=declaration(
            create_snapshot_completeness_evaluator_declaration,
            "snapshot-completeness",
            ("all-required-inputs",),
        ),
        ingestion_gate_evaluator=declaration(
            create_ingestion_gate_evaluator_declaration,
            "ingestion-gate",
            INGESTION_REQUIRED_DIMENSIONS,
        ),
        publication_gate_evaluator=declaration(
            create_publication_gate_evaluator_declaration,
            "publication-gate",
            PUBLICATION_REQUIRED_DIMENSIONS,
        ),
        retrieval_gate_evaluator=declaration(
            create_retrieval_gate_evaluator_declaration,
            "retrieval-gate",
            RETRIEVAL_REQUIRED_DIMENSIONS,
        ),
        consumption_gate_evaluator=declaration(
            create_consumption_gate_evaluator_declaration,
            "consumption-gate",
            CONSUMPTION_REQUIRED_DIMENSIONS,
        ),
        compatibility_evaluator_declaration_id=compatibility_id,
        compatibility_evaluator_identity=compatibility_identity,
        knowledge_boundary_resolver_profile_id=KNOWLEDGE_RESOLVER_PROFILE,
        knowledge_boundary_resolver_component_identity=ActorIdentity(
            "p78-knowledge-boundary-resolver"
        ),
        retrieval_authority_resolver_profile_id=RETRIEVAL_RESOLVER_PROFILE,
        retrieval_authority_resolver_component_identity=ActorIdentity(
            "p78-retrieval-authority-resolver"
        ),
        compatibility_resolver_profile_id=COMPATIBILITY_RESOLVER_PROFILE,
        compatibility_resolver_component_identity=ActorIdentity(
            "p78-compatibility-resolver"
        ),
        snapshot_coordinator_actor_identity=ActorIdentity(
            "p78-snapshot-coordinator"
        ),
    )
    overlay_handle = create_stage4_knowledge_admission_authority_handle(
        base_authority_handle=base_handle,
        configuration=configuration,
    )
    binding = create_knowledge_admission_authority_binding(
        base_authority_handle=base_handle,
        knowledge_admission_authority_handle=overlay_handle,
    )
    return AuthorityBundle(base_handle, compatibility_id, binding)


def test_s4_p78_authority_overlay_is_exactly_bound_to_base_handle() -> None:
    bundle = build_authority_bundle()
    base, overlay = validate_knowledge_admission_authority_binding(bundle.binding)

    assert base.configuration_id == bundle.base_handle.configuration.configuration_id
    assert overlay.base_configuration_id == base.configuration_id
    assert overlay.compatibility_evaluator_declaration_id == bundle.compatibility_declaration_id
    assert len(overlay.separation_matrix) == len(set(overlay.separation_matrix))
    assert all(left != right for left, right in overlay.separation_matrix)
    validate_stage4_knowledge_admission_authority_configuration(overlay)


def test_s4_p78_authority_overlay_rejects_base_actor_collision() -> None:
    with pytest.raises(ContractViolation) as raised:
        build_authority_bundle(
            compatibility_evaluator_identity=AuthorityIdentity(
                "p78-taint-classifier"
            )
        )

    assert raised.value.failure_code is ContractFailureCode.AUTHORITY_AMBIGUITY


def test_s4_p78_authority_binding_rejects_replace_bypass() -> None:
    bundle = build_authority_bundle()

    with pytest.raises((TypeError, ContractViolation)):
        replace(bundle.binding, base_authority_handle=bundle.base_handle)
