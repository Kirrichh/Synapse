"""Knowledge and admission authority overlay for Stage 4 Gold."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Callable

from .contracts import *
from .contracts import (
    _frame_identity_bytes,
    _frame_identity_text,
    _require_component,
    _require_identifier,
    _require_trusted_seal,
    _require_versioned,
    _validate_actor_identity,
    _validate_authority_identity,
    _validate_record_id_consistency,
    _violation,
)

_KNOWLEDGE_ADMISSION_EVALUATOR_SEAL = object()
_KNOWLEDGE_ADMISSION_CONFIGURATION_SEAL = object()
_KNOWLEDGE_ADMISSION_HANDLE_SEAL = object()
_KNOWLEDGE_ADMISSION_BINDING_SEAL = object()

_KNOWLEDGE_ADMISSION_EVALUATOR_REGISTRY = {
    AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR: (
        SNAPSHOT_COMPLETENESS_EVALUATOR_DECLARATION_SCHEMA_V1,
        IdentityDomain.SNAPSHOT_COMPLETENESS_EVALUATOR_DECLARATION,
    ),
    AuthorityRole.INGESTION_GATE_EVALUATOR: (
        INGESTION_GATE_EVALUATOR_DECLARATION_SCHEMA_V1,
        IdentityDomain.INGESTION_GATE_EVALUATOR_DECLARATION,
    ),
    AuthorityRole.PUBLICATION_GATE_EVALUATOR: (
        PUBLICATION_GATE_EVALUATOR_DECLARATION_SCHEMA_V1,
        IdentityDomain.PUBLICATION_GATE_EVALUATOR_DECLARATION,
    ),
    AuthorityRole.RETRIEVAL_GATE_EVALUATOR: (
        RETRIEVAL_GATE_EVALUATOR_DECLARATION_SCHEMA_V1,
        IdentityDomain.RETRIEVAL_GATE_EVALUATOR_DECLARATION,
    ),
    AuthorityRole.CONSUMPTION_GATE_EVALUATOR: (
        CONSUMPTION_GATE_EVALUATOR_DECLARATION_SCHEMA_V1,
        IdentityDomain.CONSUMPTION_GATE_EVALUATOR_DECLARATION,
    ),
}


def _validate_knowledge_admission_evaluator_registry() -> None:
    expected_roles = {
        AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
    }
    entries = tuple(_KNOWLEDGE_ADMISSION_EVALUATOR_REGISTRY.values())
    if (
        set(_KNOWLEDGE_ADMISSION_EVALUATOR_REGISTRY) != expected_roles
        or len({schema for schema, _ in entries}) != len(entries)
        or len({domain for _, domain in entries}) != len(entries)
    ):
        raise _violation(
            ContractFailureCode.ROLE_REASON_MATRIX_INCOMPLETE,
            "knowledge/admission evaluator registry is incomplete",
        )


@dataclass(frozen=True, init=False)
class KnowledgeAdmissionEvaluatorDeclaration:
    schema_version: str
    authority_role: AuthorityRole
    independence_reason: ReasonCode
    evaluator_identity: AuthorityIdentity
    evaluator_component_identity: ActorIdentity
    evaluator_component_version: str
    required_checked_dimensions: tuple[str, ...]
    resolver_profile_ids: tuple[str, ...]
    declaration_id: RecordId
    _base_authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> KnowledgeAdmissionEvaluatorDeclaration:
        raise TypeError(
            "KnowledgeAdmissionEvaluatorDeclaration is created only by its factory"
        )

    def to_dict(self) -> dict[str, object]:
        validate_knowledge_admission_evaluator_declaration(self)
        return {
            "schema_version": self.schema_version,
            "authority_role": self.authority_role.value,
            "independence_reason": self.independence_reason.value,
            "evaluator_identity": self.evaluator_identity.to_dict(),
            "evaluator_component_identity": (
                self.evaluator_component_identity.to_dict()
            ),
            "evaluator_component_version": self.evaluator_component_version,
            "required_checked_dimensions": list(
                self.required_checked_dimensions
            ),
            "resolver_profile_ids": list(self.resolver_profile_ids),
            "declaration_id": self.declaration_id.to_dict(),
        }


def _knowledge_admission_evaluator_preimage(
    *,
    schema_version: str,
    authority_role: AuthorityRole,
    independence_reason: ReasonCode,
    evaluator_identity: AuthorityIdentity,
    evaluator_component_identity: ActorIdentity,
    evaluator_component_version: str,
    required_checked_dimensions: tuple[str, ...],
    resolver_profile_ids: tuple[str, ...],
    base_configuration_id: RecordId,
) -> bytes:
    parts = [
        _frame_identity_text(schema_version),
        _frame_identity_text(authority_role.value),
        _frame_identity_text(independence_reason.value),
        _frame_identity_text(evaluator_identity.value),
        _frame_identity_text(evaluator_component_identity.value),
        _frame_identity_text(evaluator_component_version),
        _frame_identity_text(base_configuration_id.value),
        _frame_identity_count(len(required_checked_dimensions)),
    ]
    parts.extend(
        _frame_identity_text(dimension)
        for dimension in required_checked_dimensions
    )
    parts.append(_frame_identity_count(len(resolver_profile_ids)))
    parts.extend(_frame_identity_text(profile) for profile in resolver_profile_ids)
    return b"".join(parts)


def _closed_sorted_identifiers(
    value: object,
    field_name: str,
    *,
    non_empty: bool,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            f"{field_name} must be an exact tuple",
        )
    result = tuple(_require_identifier(item, field_name) for item in value)
    if (non_empty and not result) or len(set(result)) != len(result):
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            f"{field_name} is empty or contains duplicates",
        )
    if result != tuple(sorted(result)):
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            f"{field_name} must use canonical order",
        )
    return result


def _closed_sorted_versions(
    value: object,
    field_name: str,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            f"{field_name} must be an exact tuple",
        )
    result = tuple(_require_versioned(item, field_name) for item in value)
    if len(set(result)) != len(result) or result != tuple(sorted(result)):
        raise _violation(
            ContractFailureCode.MALFORMED_VERSION,
            f"{field_name} must be distinct and canonically ordered",
        )
    return result


def create_knowledge_admission_evaluator_declaration(
    *,
    base_authority_handle: Stage4AuthorityHandle,
    authority_role: AuthorityRole,
    evaluator_identity: AuthorityIdentity,
    evaluator_component_identity: ActorIdentity,
    evaluator_component_version: str,
    required_checked_dimensions: tuple[str, ...],
    resolver_profile_ids: tuple[str, ...],
) -> KnowledgeAdmissionEvaluatorDeclaration:
    base = require_stage4_authority_handle(base_authority_handle)
    _validate_knowledge_admission_evaluator_registry()
    if type(authority_role) is not AuthorityRole:
        raise _violation(
            ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
            "knowledge/admission evaluator role is invalid",
        )
    registry_entry = _KNOWLEDGE_ADMISSION_EVALUATOR_REGISTRY.get(authority_role)
    if registry_entry is None:
        raise _violation(
            ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
            "authority role is not a knowledge/admission evaluator role",
        )
    schema_version, identity_domain = registry_entry
    evaluator = _snapshot_authority(evaluator_identity, "evaluator_identity")
    component = _snapshot_actor(
        evaluator_component_identity,
        "evaluator_component_identity",
    )
    version = _require_versioned(
        evaluator_component_version,
        "evaluator_component_version",
    )
    dimensions = _closed_sorted_identifiers(
        required_checked_dimensions,
        "required_checked_dimensions",
        non_empty=True,
    )
    profiles = _closed_sorted_versions(
        resolver_profile_ids,
        "resolver_profile_ids",
    )
    reason = _reason_for_authority_role(authority_role)
    base_actors = {
        base.platform_attester_actor.value,
        base.builder_actor.value,
        base.taint_classifier_authority.value,
        base.taint_reviewer_authority.value,
        base.supersession_reviewer_authority.value,
        base.revocation_reviewer_authority.value,
        base.lifecycle_writer_actor.value,
    }
    if base.governing_human_authority is not None:
        base_actors.add(base.governing_human_authority.value)
    if evaluator.value in base_actors or evaluator.value == component.value:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission evaluator is not independent",
        )
    preimage = _knowledge_admission_evaluator_preimage(
        schema_version=schema_version,
        authority_role=authority_role,
        independence_reason=reason,
        evaluator_identity=evaluator,
        evaluator_component_identity=component,
        evaluator_component_version=version,
        required_checked_dimensions=dimensions,
        resolver_profile_ids=profiles,
        base_configuration_id=base.configuration_id,
    )
    result = object.__new__(KnowledgeAdmissionEvaluatorDeclaration)
    object.__setattr__(result, "schema_version", schema_version)
    object.__setattr__(result, "authority_role", authority_role)
    object.__setattr__(result, "independence_reason", reason)
    object.__setattr__(result, "evaluator_identity", evaluator)
    object.__setattr__(result, "evaluator_component_identity", component)
    object.__setattr__(result, "evaluator_component_version", version)
    object.__setattr__(result, "required_checked_dimensions", dimensions)
    object.__setattr__(result, "resolver_profile_ids", profiles)
    object.__setattr__(
        result,
        "declaration_id",
        compute_record_id(domain=identity_domain, canonical_bytes=preimage),
    )
    object.__setattr__(result, "_base_authority_handle", base_authority_handle)
    object.__setattr__(
        result,
        "_trusted_seal",
        _KNOWLEDGE_ADMISSION_EVALUATOR_SEAL,
    )
    validate_knowledge_admission_evaluator_declaration(result)
    return result


def validate_knowledge_admission_evaluator_declaration(
    value: KnowledgeAdmissionEvaluatorDeclaration,
    *,
    expected_base_authority_handle: Stage4AuthorityHandle | None = None,
    expected_role: AuthorityRole | None = None,
) -> None:
    if (
        type(value) is not KnowledgeAdmissionEvaluatorDeclaration
        or getattr(value, "_trusted_seal", None)
        is not _KNOWLEDGE_ADMISSION_EVALUATOR_SEAL
    ):
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            "knowledge/admission evaluator declaration is not factory sealed",
        )
    base = require_stage4_authority_handle(
        value._base_authority_handle,
        expected_handle=expected_base_authority_handle,
    )
    _validate_knowledge_admission_evaluator_registry()
    registry_entry = _KNOWLEDGE_ADMISSION_EVALUATOR_REGISTRY.get(
        value.authority_role
    )
    if registry_entry is None:
        raise _violation(
            ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
            "knowledge/admission evaluator role is unknown",
        )
    schema_version, identity_domain = registry_entry
    if (
        value.schema_version != schema_version
        or type(value.schema_version) is not str
    ):
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "knowledge/admission evaluator schema is unknown",
        )
    if expected_role is not None and value.authority_role is not expected_role:
        raise _violation(
            ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
            "knowledge/admission evaluator role differs from the required role",
        )
    if (
        type(value.independence_reason) is not ReasonCode
        or value.independence_reason
        is not _reason_for_authority_role(value.authority_role)
    ):
        raise _violation(
            ContractFailureCode.UNKNOWN_REASON_CODE,
            "knowledge/admission evaluator reason does not match its role",
        )
    _validate_authority_identity(value.evaluator_identity)
    _validate_actor_identity(
        value.evaluator_component_identity,
        "evaluator_component_identity",
    )
    _require_versioned(
        value.evaluator_component_version,
        "evaluator_component_version",
    )
    _closed_sorted_identifiers(
        value.required_checked_dimensions,
        "required_checked_dimensions",
        non_empty=True,
    )
    _closed_sorted_versions(
        value.resolver_profile_ids,
        "resolver_profile_ids",
    )
    preimage = _knowledge_admission_evaluator_preimage(
        schema_version=value.schema_version,
        authority_role=value.authority_role,
        independence_reason=value.independence_reason,
        evaluator_identity=value.evaluator_identity,
        evaluator_component_identity=value.evaluator_component_identity,
        evaluator_component_version=value.evaluator_component_version,
        required_checked_dimensions=value.required_checked_dimensions,
        resolver_profile_ids=value.resolver_profile_ids,
        base_configuration_id=base.configuration_id,
    )
    if (
        type(value.declaration_id) is not RecordId
        or value.declaration_id.domain is not identity_domain
    ):
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "knowledge/admission evaluator declaration domain is invalid",
        )
    validate_record_id(value.declaration_id, canonical_bytes=preimage)


def _create_role_declaration(
    role: AuthorityRole,
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return create_knowledge_admission_evaluator_declaration(
        authority_role=role,
        **kwargs,
    )


def create_snapshot_completeness_evaluator_declaration(
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return _create_role_declaration(
        AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        **kwargs,
    )


def create_ingestion_gate_evaluator_declaration(
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return _create_role_declaration(
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        **kwargs,
    )


def create_publication_gate_evaluator_declaration(
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return _create_role_declaration(
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        **kwargs,
    )


def create_retrieval_gate_evaluator_declaration(
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return _create_role_declaration(
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        **kwargs,
    )


def create_consumption_gate_evaluator_declaration(
    **kwargs: object,
) -> KnowledgeAdmissionEvaluatorDeclaration:
    return _create_role_declaration(
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        **kwargs,
    )


@dataclass(frozen=True, init=False)
class Stage4KnowledgeAdmissionAuthorityConfiguration:
    schema_version: SchemaVersion
    base_configuration_id: RecordId
    snapshot_completeness_evaluator: KnowledgeAdmissionEvaluatorDeclaration
    ingestion_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration
    publication_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration
    retrieval_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration
    consumption_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration
    compatibility_evaluator_declaration_id: RecordId
    compatibility_evaluator_identity: AuthorityIdentity
    knowledge_boundary_resolver_profile_id: str
    knowledge_boundary_resolver_component_identity: ActorIdentity
    retrieval_authority_resolver_profile_id: str
    retrieval_authority_resolver_component_identity: ActorIdentity
    compatibility_resolver_profile_id: str
    compatibility_resolver_component_identity: ActorIdentity
    snapshot_coordinator_actor_identity: ActorIdentity
    separation_matrix: tuple[tuple[str, str], ...]
    configuration_id: RecordId
    _base_authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> Stage4KnowledgeAdmissionAuthorityConfiguration:
        raise TypeError(
            "Stage4KnowledgeAdmissionAuthorityConfiguration is factory-created"
        )

    def to_dict(self) -> dict[str, object]:
        validate_stage4_knowledge_admission_authority_configuration(self)
        return {
            "schema_version": self.schema_version.value,
            "base_configuration_id": self.base_configuration_id.to_dict(),
            "snapshot_completeness_evaluator": (
                self.snapshot_completeness_evaluator.to_dict()
            ),
            "ingestion_gate_evaluator": self.ingestion_gate_evaluator.to_dict(),
            "publication_gate_evaluator": (
                self.publication_gate_evaluator.to_dict()
            ),
            "retrieval_gate_evaluator": self.retrieval_gate_evaluator.to_dict(),
            "consumption_gate_evaluator": (
                self.consumption_gate_evaluator.to_dict()
            ),
            "compatibility_evaluator_declaration_id": (
                self.compatibility_evaluator_declaration_id.to_dict()
            ),
            "compatibility_evaluator_identity": (
                self.compatibility_evaluator_identity.to_dict()
            ),
            "knowledge_boundary_resolver_profile_id": (
                self.knowledge_boundary_resolver_profile_id
            ),
            "knowledge_boundary_resolver_component_identity": (
                self.knowledge_boundary_resolver_component_identity.to_dict()
            ),
            "retrieval_authority_resolver_profile_id": (
                self.retrieval_authority_resolver_profile_id
            ),
            "retrieval_authority_resolver_component_identity": (
                self.retrieval_authority_resolver_component_identity.to_dict()
            ),
            "compatibility_resolver_profile_id": (
                self.compatibility_resolver_profile_id
            ),
            "compatibility_resolver_component_identity": (
                self.compatibility_resolver_component_identity.to_dict()
            ),
            "snapshot_coordinator_actor_identity": (
                self.snapshot_coordinator_actor_identity.to_dict()
            ),
            "separation_matrix": [
                [left, right] for left, right in self.separation_matrix
            ],
            "configuration_id": self.configuration_id.to_dict(),
        }


def _authority_configuration_participants(
    configuration: Stage4AuthorityConfiguration,
) -> set[str]:
    participants = {
        configuration.platform_attester_actor.value,
        configuration.builder_actor.value,
        configuration.taint_classifier_authority.value,
        configuration.taint_reviewer_authority.value,
        configuration.supersession_reviewer_authority.value,
        configuration.revocation_reviewer_authority.value,
        configuration.lifecycle_writer_actor.value,
    }
    if configuration.governing_human_authority is not None:
        participants.add(configuration.governing_human_authority.value)
    return participants


def _knowledge_admission_separation_matrix(
    *,
    declarations: tuple[KnowledgeAdmissionEvaluatorDeclaration, ...],
    compatibility_evaluator_identity: AuthorityIdentity,
    knowledge_boundary_resolver_component_identity: ActorIdentity,
    retrieval_authority_resolver_component_identity: ActorIdentity,
    compatibility_resolver_component_identity: ActorIdentity,
    snapshot_coordinator_actor_identity: ActorIdentity,
) -> tuple[tuple[str, str], ...]:
    participants = tuple(
        sorted(
            (
                *(item.evaluator_identity.value for item in declarations),
                compatibility_evaluator_identity.value,
                knowledge_boundary_resolver_component_identity.value,
                retrieval_authority_resolver_component_identity.value,
                compatibility_resolver_component_identity.value,
                snapshot_coordinator_actor_identity.value,
            )
        )
    )
    if len(set(participants)) != len(participants):
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission authorities and components must be distinct",
        )
    return tuple(
        (left, right)
        for index, left in enumerate(participants)
        for right in participants[index + 1 :]
    )


def _knowledge_admission_configuration_preimage(
    *,
    base_configuration_id: RecordId,
    declarations: tuple[KnowledgeAdmissionEvaluatorDeclaration, ...],
    compatibility_evaluator_declaration_id: RecordId,
    compatibility_evaluator_identity: AuthorityIdentity,
    knowledge_boundary_resolver_profile_id: str,
    knowledge_boundary_resolver_component_identity: ActorIdentity,
    retrieval_authority_resolver_profile_id: str,
    retrieval_authority_resolver_component_identity: ActorIdentity,
    compatibility_resolver_profile_id: str,
    compatibility_resolver_component_identity: ActorIdentity,
    snapshot_coordinator_actor_identity: ActorIdentity,
    separation_matrix: tuple[tuple[str, str], ...],
) -> bytes:
    parts = [
        _frame_identity_text(
            KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION_SCHEMA_V1
        ),
        _frame_identity_text(base_configuration_id.value),
        _frame_identity_count(len(declarations)),
    ]
    parts.extend(
        _frame_identity_text(declaration.declaration_id.value)
        for declaration in declarations
    )
    parts.extend(
        (
            _frame_identity_text(compatibility_evaluator_declaration_id.value),
            _frame_identity_text(compatibility_evaluator_identity.value),
            _frame_identity_text(knowledge_boundary_resolver_profile_id),
            _frame_identity_text(
                knowledge_boundary_resolver_component_identity.value
            ),
            _frame_identity_text(retrieval_authority_resolver_profile_id),
            _frame_identity_text(
                retrieval_authority_resolver_component_identity.value
            ),
            _frame_identity_text(compatibility_resolver_profile_id),
            _frame_identity_text(
                compatibility_resolver_component_identity.value
            ),
            _frame_identity_text(snapshot_coordinator_actor_identity.value),
            _frame_identity_count(len(separation_matrix)),
        )
    )
    for left, right in separation_matrix:
        parts.extend((_frame_identity_text(left), _frame_identity_text(right)))
    return b"".join(parts)


def _knowledge_admission_declarations(
    *,
    snapshot_completeness_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    ingestion_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    publication_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    retrieval_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    consumption_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    base_authority_handle: Stage4AuthorityHandle,
) -> tuple[KnowledgeAdmissionEvaluatorDeclaration, ...]:
    declarations = (
        snapshot_completeness_evaluator,
        ingestion_gate_evaluator,
        publication_gate_evaluator,
        retrieval_gate_evaluator,
        consumption_gate_evaluator,
    )
    roles = (
        AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
    )
    for declaration, role in zip(declarations, roles):
        validate_knowledge_admission_evaluator_declaration(
            declaration,
            expected_base_authority_handle=base_authority_handle,
            expected_role=role,
        )
    if len({item.evaluator_identity.value for item in declarations}) != len(
        declarations
    ):
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission evaluator authorities must be pairwise distinct",
        )
    return declarations


def create_stage4_knowledge_admission_authority_configuration(
    *,
    base_authority_handle: Stage4AuthorityHandle,
    snapshot_completeness_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    ingestion_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    publication_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    retrieval_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    consumption_gate_evaluator: KnowledgeAdmissionEvaluatorDeclaration,
    compatibility_evaluator_declaration_id: RecordId,
    compatibility_evaluator_identity: AuthorityIdentity,
    knowledge_boundary_resolver_profile_id: str,
    knowledge_boundary_resolver_component_identity: ActorIdentity,
    retrieval_authority_resolver_profile_id: str,
    retrieval_authority_resolver_component_identity: ActorIdentity,
    compatibility_resolver_profile_id: str,
    compatibility_resolver_component_identity: ActorIdentity,
    snapshot_coordinator_actor_identity: ActorIdentity,
) -> Stage4KnowledgeAdmissionAuthorityConfiguration:
    base = require_stage4_authority_handle(base_authority_handle)
    declarations = _knowledge_admission_declarations(
        snapshot_completeness_evaluator=snapshot_completeness_evaluator,
        ingestion_gate_evaluator=ingestion_gate_evaluator,
        publication_gate_evaluator=publication_gate_evaluator,
        retrieval_gate_evaluator=retrieval_gate_evaluator,
        consumption_gate_evaluator=consumption_gate_evaluator,
        base_authority_handle=base_authority_handle,
    )
    _validate_record_id_consistency(compatibility_evaluator_declaration_id)
    if (
        compatibility_evaluator_declaration_id.domain
        is not IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION
    ):
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "compatibility evaluator declaration domain is invalid",
        )
    compatibility_evaluator = _snapshot_authority(
        compatibility_evaluator_identity,
        "compatibility_evaluator_identity",
    )
    knowledge_profile = _require_versioned(
        knowledge_boundary_resolver_profile_id,
        "knowledge_boundary_resolver_profile_id",
    )
    knowledge_component = _snapshot_actor(
        knowledge_boundary_resolver_component_identity,
        "knowledge_boundary_resolver_component_identity",
    )
    retrieval_profile = _require_versioned(
        retrieval_authority_resolver_profile_id,
        "retrieval_authority_resolver_profile_id",
    )
    retrieval_component = _snapshot_actor(
        retrieval_authority_resolver_component_identity,
        "retrieval_authority_resolver_component_identity",
    )
    compatibility_profile = _require_versioned(
        compatibility_resolver_profile_id,
        "compatibility_resolver_profile_id",
    )
    compatibility_component = _snapshot_actor(
        compatibility_resolver_component_identity,
        "compatibility_resolver_component_identity",
    )
    coordinator = _snapshot_actor(
        snapshot_coordinator_actor_identity,
        "snapshot_coordinator_actor_identity",
    )
    separation = _knowledge_admission_separation_matrix(
        declarations=declarations,
        compatibility_evaluator_identity=compatibility_evaluator,
        knowledge_boundary_resolver_component_identity=knowledge_component,
        retrieval_authority_resolver_component_identity=retrieval_component,
        compatibility_resolver_component_identity=compatibility_component,
        snapshot_coordinator_actor_identity=coordinator,
    )
    new_participants = {item for pair in separation for item in pair}
    if new_participants & _authority_configuration_participants(base):
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission authority overlaps the base configuration",
        )
    preimage = _knowledge_admission_configuration_preimage(
        base_configuration_id=base.configuration_id,
        declarations=declarations,
        compatibility_evaluator_declaration_id=(
            compatibility_evaluator_declaration_id
        ),
        compatibility_evaluator_identity=compatibility_evaluator,
        knowledge_boundary_resolver_profile_id=knowledge_profile,
        knowledge_boundary_resolver_component_identity=knowledge_component,
        retrieval_authority_resolver_profile_id=retrieval_profile,
        retrieval_authority_resolver_component_identity=retrieval_component,
        compatibility_resolver_profile_id=compatibility_profile,
        compatibility_resolver_component_identity=compatibility_component,
        snapshot_coordinator_actor_identity=coordinator,
        separation_matrix=separation,
    )
    result = object.__new__(Stage4KnowledgeAdmissionAuthorityConfiguration)
    object.__setattr__(
        result,
        "schema_version",
        SchemaVersion.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION_V1,
    )
    object.__setattr__(result, "base_configuration_id", base.configuration_id)
    object.__setattr__(
        result,
        "snapshot_completeness_evaluator",
        snapshot_completeness_evaluator,
    )
    object.__setattr__(result, "ingestion_gate_evaluator", ingestion_gate_evaluator)
    object.__setattr__(
        result,
        "publication_gate_evaluator",
        publication_gate_evaluator,
    )
    object.__setattr__(result, "retrieval_gate_evaluator", retrieval_gate_evaluator)
    object.__setattr__(
        result,
        "consumption_gate_evaluator",
        consumption_gate_evaluator,
    )
    object.__setattr__(
        result,
        "compatibility_evaluator_declaration_id",
        compatibility_evaluator_declaration_id,
    )
    object.__setattr__(
        result,
        "compatibility_evaluator_identity",
        compatibility_evaluator,
    )
    object.__setattr__(
        result,
        "knowledge_boundary_resolver_profile_id",
        knowledge_profile,
    )
    object.__setattr__(
        result,
        "knowledge_boundary_resolver_component_identity",
        knowledge_component,
    )
    object.__setattr__(
        result,
        "retrieval_authority_resolver_profile_id",
        retrieval_profile,
    )
    object.__setattr__(
        result,
        "retrieval_authority_resolver_component_identity",
        retrieval_component,
    )
    object.__setattr__(
        result,
        "compatibility_resolver_profile_id",
        compatibility_profile,
    )
    object.__setattr__(
        result,
        "compatibility_resolver_component_identity",
        compatibility_component,
    )
    object.__setattr__(
        result,
        "snapshot_coordinator_actor_identity",
        coordinator,
    )
    object.__setattr__(result, "separation_matrix", separation)
    object.__setattr__(
        result,
        "configuration_id",
        compute_record_id(
            domain=IdentityDomain.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION,
            canonical_bytes=preimage,
        ),
    )
    object.__setattr__(result, "_base_authority_handle", base_authority_handle)
    object.__setattr__(
        result,
        "_trusted_seal",
        _KNOWLEDGE_ADMISSION_CONFIGURATION_SEAL,
    )
    validate_stage4_knowledge_admission_authority_configuration(result)
    return result


def validate_stage4_knowledge_admission_authority_configuration(
    value: Stage4KnowledgeAdmissionAuthorityConfiguration,
    *,
    expected_base_authority_handle: Stage4AuthorityHandle | None = None,
) -> None:
    if (
        type(value) is not Stage4KnowledgeAdmissionAuthorityConfiguration
        or getattr(value, "_trusted_seal", None)
        is not _KNOWLEDGE_ADMISSION_CONFIGURATION_SEAL
    ):
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            "knowledge/admission configuration is not factory sealed",
        )
    if (
        value.schema_version
        is not SchemaVersion.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION_V1
    ):
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "knowledge/admission configuration schema is unknown",
        )
    base = require_stage4_authority_handle(
        value._base_authority_handle,
        expected_handle=expected_base_authority_handle,
    )
    if value.base_configuration_id != base.configuration_id:
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "knowledge/admission configuration changed its base",
        )
    declarations = _knowledge_admission_declarations(
        snapshot_completeness_evaluator=value.snapshot_completeness_evaluator,
        ingestion_gate_evaluator=value.ingestion_gate_evaluator,
        publication_gate_evaluator=value.publication_gate_evaluator,
        retrieval_gate_evaluator=value.retrieval_gate_evaluator,
        consumption_gate_evaluator=value.consumption_gate_evaluator,
        base_authority_handle=value._base_authority_handle,
    )
    _validate_record_id_consistency(
        value.compatibility_evaluator_declaration_id
    )
    if (
        value.compatibility_evaluator_declaration_id.domain
        is not IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION
    ):
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "compatibility evaluator declaration domain changed",
        )
    _validate_authority_identity(value.compatibility_evaluator_identity)
    _require_versioned(
        value.knowledge_boundary_resolver_profile_id,
        "knowledge_boundary_resolver_profile_id",
    )
    _validate_actor_identity(
        value.knowledge_boundary_resolver_component_identity,
        "knowledge_boundary_resolver_component_identity",
    )
    _require_versioned(
        value.retrieval_authority_resolver_profile_id,
        "retrieval_authority_resolver_profile_id",
    )
    _validate_actor_identity(
        value.retrieval_authority_resolver_component_identity,
        "retrieval_authority_resolver_component_identity",
    )
    _require_versioned(
        value.compatibility_resolver_profile_id,
        "compatibility_resolver_profile_id",
    )
    _validate_actor_identity(
        value.compatibility_resolver_component_identity,
        "compatibility_resolver_component_identity",
    )
    _validate_actor_identity(
        value.snapshot_coordinator_actor_identity,
        "snapshot_coordinator_actor_identity",
    )
    expected_separation = _knowledge_admission_separation_matrix(
        declarations=declarations,
        compatibility_evaluator_identity=value.compatibility_evaluator_identity,
        knowledge_boundary_resolver_component_identity=(
            value.knowledge_boundary_resolver_component_identity
        ),
        retrieval_authority_resolver_component_identity=(
            value.retrieval_authority_resolver_component_identity
        ),
        compatibility_resolver_component_identity=(
            value.compatibility_resolver_component_identity
        ),
        snapshot_coordinator_actor_identity=(
            value.snapshot_coordinator_actor_identity
        ),
    )
    if (
        type(value.separation_matrix) is not tuple
        or value.separation_matrix != expected_separation
    ):
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission separation matrix changed",
        )
    if {
        item for pair in expected_separation for item in pair
    } & _authority_configuration_participants(base):
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "knowledge/admission participant overlaps the base configuration",
        )
    preimage = _knowledge_admission_configuration_preimage(
        base_configuration_id=value.base_configuration_id,
        declarations=declarations,
        compatibility_evaluator_declaration_id=(
            value.compatibility_evaluator_declaration_id
        ),
        compatibility_evaluator_identity=value.compatibility_evaluator_identity,
        knowledge_boundary_resolver_profile_id=(
            value.knowledge_boundary_resolver_profile_id
        ),
        knowledge_boundary_resolver_component_identity=(
            value.knowledge_boundary_resolver_component_identity
        ),
        retrieval_authority_resolver_profile_id=(
            value.retrieval_authority_resolver_profile_id
        ),
        retrieval_authority_resolver_component_identity=(
            value.retrieval_authority_resolver_component_identity
        ),
        compatibility_resolver_profile_id=(
            value.compatibility_resolver_profile_id
        ),
        compatibility_resolver_component_identity=(
            value.compatibility_resolver_component_identity
        ),
        snapshot_coordinator_actor_identity=(
            value.snapshot_coordinator_actor_identity
        ),
        separation_matrix=value.separation_matrix,
    )
    if (
        type(value.configuration_id) is not RecordId
        or value.configuration_id.domain
        is not IdentityDomain.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION
    ):
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "knowledge/admission configuration identity domain is invalid",
        )
    validate_record_id(value.configuration_id, canonical_bytes=preimage)


@dataclass(frozen=True, init=False)
class Stage4KnowledgeAdmissionAuthorityHandle:
    _configuration: Stage4KnowledgeAdmissionAuthorityConfiguration
    _base_authority_handle: Stage4AuthorityHandle
    _instance_token: object
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> Stage4KnowledgeAdmissionAuthorityHandle:
        raise TypeError(
            "Stage4KnowledgeAdmissionAuthorityHandle is created only by its factory"
        )

    @property
    def configuration(self) -> Stage4KnowledgeAdmissionAuthorityConfiguration:
        validate_stage4_knowledge_admission_authority_configuration(
            self._configuration,
            expected_base_authority_handle=self._base_authority_handle,
        )
        return self._configuration


def create_stage4_knowledge_admission_authority_handle(
    *,
    base_authority_handle: Stage4AuthorityHandle,
    configuration: Stage4KnowledgeAdmissionAuthorityConfiguration,
) -> Stage4KnowledgeAdmissionAuthorityHandle:
    require_stage4_authority_handle(base_authority_handle)
    validate_stage4_knowledge_admission_authority_configuration(
        configuration,
        expected_base_authority_handle=base_authority_handle,
    )
    result = object.__new__(Stage4KnowledgeAdmissionAuthorityHandle)
    object.__setattr__(result, "_configuration", configuration)
    object.__setattr__(result, "_base_authority_handle", base_authority_handle)
    object.__setattr__(result, "_instance_token", object())
    object.__setattr__(result, "_trusted_seal", _KNOWLEDGE_ADMISSION_HANDLE_SEAL)
    require_stage4_knowledge_admission_authority_handle(result)
    return result


def require_stage4_knowledge_admission_authority_handle(
    value: Stage4KnowledgeAdmissionAuthorityHandle,
    *,
    expected_handle: Stage4KnowledgeAdmissionAuthorityHandle | None = None,
    expected_base_authority_handle: Stage4AuthorityHandle | None = None,
) -> Stage4KnowledgeAdmissionAuthorityConfiguration:
    if (
        type(value) is not Stage4KnowledgeAdmissionAuthorityHandle
        or getattr(value, "_trusted_seal", None)
        is not _KNOWLEDGE_ADMISSION_HANDLE_SEAL
        or type(getattr(value, "_instance_token", None)) is not object
    ):
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "knowledge/admission handle is not a configured capability",
        )
    if expected_handle is not None and value is not expected_handle:
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "knowledge/admission boundary requires the exact opened handle",
        )
    if (
        expected_base_authority_handle is not None
        and value._base_authority_handle is not expected_base_authority_handle
    ):
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "knowledge/admission handle is bound to another base handle",
        )
    validate_stage4_knowledge_admission_authority_configuration(
        value._configuration,
        expected_base_authority_handle=value._base_authority_handle,
    )
    return value._configuration


@dataclass(frozen=True, init=False)
class KnowledgeAdmissionAuthorityBinding:
    base_authority_handle: Stage4AuthorityHandle
    knowledge_admission_authority_handle: Stage4KnowledgeAdmissionAuthorityHandle
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> KnowledgeAdmissionAuthorityBinding:
        raise TypeError(
            "KnowledgeAdmissionAuthorityBinding is created only by its factory"
        )


def create_knowledge_admission_authority_binding(
    *,
    base_authority_handle: Stage4AuthorityHandle,
    knowledge_admission_authority_handle: (
        Stage4KnowledgeAdmissionAuthorityHandle
    ),
) -> KnowledgeAdmissionAuthorityBinding:
    require_stage4_authority_handle(base_authority_handle)
    require_stage4_knowledge_admission_authority_handle(
        knowledge_admission_authority_handle,
        expected_base_authority_handle=base_authority_handle,
    )
    result = object.__new__(KnowledgeAdmissionAuthorityBinding)
    object.__setattr__(
        result,
        "base_authority_handle",
        base_authority_handle,
    )
    object.__setattr__(
        result,
        "knowledge_admission_authority_handle",
        knowledge_admission_authority_handle,
    )
    object.__setattr__(
        result,
        "_trusted_seal",
        _KNOWLEDGE_ADMISSION_BINDING_SEAL,
    )
    validate_knowledge_admission_authority_binding(result)
    return result


def validate_knowledge_admission_authority_binding(
    value: KnowledgeAdmissionAuthorityBinding,
    *,
    expected_binding: KnowledgeAdmissionAuthorityBinding | None = None,
) -> tuple[
    Stage4AuthorityConfiguration,
    Stage4KnowledgeAdmissionAuthorityConfiguration,
]:
    if (
        type(value) is not KnowledgeAdmissionAuthorityBinding
        or getattr(value, "_trusted_seal", None)
        is not _KNOWLEDGE_ADMISSION_BINDING_SEAL
    ):
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "knowledge/admission authority binding is not factory sealed",
        )
    if expected_binding is not None and value is not expected_binding:
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "knowledge/admission authority binding instance differs",
        )
    base = require_stage4_authority_handle(value.base_authority_handle)
    overlay = require_stage4_knowledge_admission_authority_handle(
        value.knowledge_admission_authority_handle,
        expected_base_authority_handle=value.base_authority_handle,
    )
    if overlay.base_configuration_id != base.configuration_id:
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "knowledge/admission authority binding base changed",
        )
    return base, overlay
