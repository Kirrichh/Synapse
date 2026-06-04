"""AS2 adapter skeleton/projection/audit boundary (Alpha3g P0.6.8).

This module materializes the approved AS2 adapter boundary as an isolated
validation and fixture-driven standalone projection module. It intentionally
does **not** bridge legacy ``AgentRuntime`` or runtime serialization paths.

Allowed through P0.6.8:
- local AS2 error taxonomy;
- explicit input value containers;
- validation-only boundary functions;
- ``project_validated_as2_inputs(...)`` for synthetic fixture-driven projection
  from explicit validated AS2 inputs into the standalone AgentSnapshot core.

Forbidden in P0.6.7:
- ``to_agent_snapshot()``;
- imports from legacy runtime, interpreter, actor runtime, Integrate, Dream,
  CVM, provider registries, or storage backends;
- I/O, storage writes, wall-clock, UUIDs, network, global/runtime lookup;
- legacy runtime wiring or profile selector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from synapse.agent_snapshot import (
    AgentDefinitionRef,
    AgentIdSeed,
    AgentSnapshot,
    CapabilityGrant,
    MemoryRef,
)
from synapse.canonical_service import stable_canonical_hash

AS2_FIXTURE_PROFILE = "stable-canonical.v1"
ADAPTER_IDENTITY_CONTEXT_SCHEMA = "alpha3g.adapter_identity_context.v1"
STATIC_MODEL_REGISTRY_SCHEMA = "alpha3g.static_model_registry.v1"
MODEL_REF_SCHEMA = "alpha3g.model_ref.v1"
MEMORY_REF_SOURCE_SCHEMA = "alpha3g.memory_ref_source.v1"
CAPABILITY_GRANT_SOURCE_SCHEMA = "alpha3g.capability_grant_source.v1"
CAPABILITY_GRANT_SCHEMA = "alpha3g.capability_grant.v1"
ADAPTER_DEFINITION_SOURCE_SCHEMA = "alpha3g.adapter_definition_source.v1"
MEMORY_SPACE_POLICY_V1 = "alpha3g.memory_space_policy.v1"
AS2_CAPABILITY_PROJECTION_VERSION = "alpha3g.as2_capability_projection.v1"

# Declarative quarantine marker. P0.6.8 keeps the module importable for tests and standalone projection,
# but does not wire it into runtime or expose legacy bridge behavior.
AS2_ADAPTER_SKELETON_ENABLED = False

_ALLOWED_PROVIDER_NAMESPACES = frozenset({"mock"})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RFC_REFERENCE_RE = re.compile(r"^RFC-[A-Z0-9-]+\.md §[0-9]+(\.[0-9]+)*$")


@dataclass(frozen=True)
class AS2ViolationContext:
    """Immutable forensic context for AS2 fail-closed errors.

    The context annotates failure paths only. It is intentionally separate from
    AdapterDerivationRecord, which describes successful projection paths.
    """

    rfc_reference: str
    violated_field: str | None = None
    fixture_case_id: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rfc_reference, str) or _RFC_REFERENCE_RE.match(self.rfc_reference) is None:
            raise ValueError(
                "rfc_reference must match 'RFC-<NAME>.md §<section>[.<subsection>...]'"
            )


def _violation(
    rfc_reference: str,
    violated_field: str | None = None,
    *,
    expected_value: Any | None = None,
    actual_value: Any | None = None,
    fixture_case_id: str | None = None,
) -> AS2ViolationContext:
    """Build a canonical AS2 violation context with stringified optional values."""

    return AS2ViolationContext(
        rfc_reference=rfc_reference,
        violated_field=violated_field,
        fixture_case_id=fixture_case_id,
        expected_value=None if expected_value is None else str(expected_value),
        actual_value=None if actual_value is None else str(actual_value),
    )


class AS2AdapterError(Exception):
    """Base class for all AS2 adapter errors. Never raised directly."""

    def __init__(self, message: str, context: AS2ViolationContext | None = None) -> None:
        super().__init__(message)
        self.context = context


class AS2AdapterInputError(AS2AdapterError):
    """Caller-provided AS2 input is missing or incomplete."""


class AS2AdapterMappingError(AS2AdapterError):
    """Static registry or declarative mapping failed."""


class AS2AdapterIntegrityError(AS2AdapterError):
    """Host/adapter boundary or integrity invariant was violated."""


class AdapterIdentityContextMissingError(AS2AdapterInputError):
    """AdapterIdentityContext was not provided."""


class AdapterIdentityContextIncompleteError(AS2AdapterInputError):
    """AdapterIdentityContext is present but missing required seed fields."""


class MemoryRefSourceMissingError(AS2AdapterInputError):
    """MemoryRefSource was not provided for the canonical AS2 path."""


class CapabilityGrantSourceMissingError(AS2AdapterInputError):
    """CapabilityGrantSource was not provided for declared/live capabilities."""


class ModelRefUnknownError(AS2AdapterMappingError):
    """Selected model string is absent from the static model registry."""


class CapabilityGrantInvalidRefError(AS2AdapterMappingError):
    """Capability grant source contains an invalid FunctionDescriptor ref."""


class AdapterMemorySpaceMismatchError(AS2AdapterIntegrityError):
    """MemoryRefSource contains missing, mixed, or foreign memory spaces."""


class AdapterEnvelopeConflictError(AS2AdapterIntegrityError):
    """Candidate AS2 output attempted to reuse a legacy envelope marker."""


class AdapterAmbientAuthorityError(AS2AdapterIntegrityError):
    """Input attempts to authorize ambient runtime/global authority."""


class AdapterInlineMemoryRejectedError(AS2AdapterIntegrityError):
    """Inline legacy memory content was supplied to the canonical AS2 path."""


class AdapterSubagentOutOfScopeError(AS2AdapterIntegrityError):
    """Subagent/fracture runtime graph data is out of AS2 v1 scope."""


ERROR_NAME_TO_CLASS: Mapping[str, type[AS2AdapterError]] = {
    cls.__name__: cls
    for cls in (
        AdapterIdentityContextMissingError,
        AdapterIdentityContextIncompleteError,
        MemoryRefSourceMissingError,
        CapabilityGrantSourceMissingError,
        ModelRefUnknownError,
        CapabilityGrantInvalidRefError,
        AdapterMemorySpaceMismatchError,
        AdapterEnvelopeConflictError,
        AdapterAmbientAuthorityError,
        AdapterInlineMemoryRejectedError,
        AdapterSubagentOutOfScopeError,
    )
}


@dataclass(frozen=True)
class AdapterIdentitySeed:
    """Causal identity seed for AS2 validation.

    This is a value skeleton; P0.6.8 derives ``agent_id`` only inside
    fixture-driven standalone projection.
    """

    parent_anchor: str
    definition_hash: str
    spawn_nonce: str
    namespace: str
    alias: str | None = None


@dataclass(frozen=True)
class AdapterAuditContext:
    """Provenance metadata excluded from canonical AgentSnapshot state hash."""

    soulprint: str | None = None
    identity_version: int | None = None


@dataclass(frozen=True)
class AdapterIdentityContext:
    """Explicit complete-or-absent AS2 identity context."""

    identity_seed: AdapterIdentitySeed
    audit_context: AdapterAuditContext | None = None
    schema_version: str = ADAPTER_IDENTITY_CONTEXT_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE


@dataclass(frozen=True)
class ModelRef:
    """Minimal AS2 model_ref.v1 value skeleton."""

    provider_namespace: str
    model_id: str
    model_version: str
    capability_profile_hash: str
    schema_version: str = MODEL_REF_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE
    type: str = "model_ref"


@dataclass(frozen=True)
class StaticModelRegistry:
    """Immutable local model-registry snapshot skeleton."""

    registry_snapshot_hash: str
    entries: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    schema_version: str = STATIC_MODEL_REGISTRY_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE
    type: str = "static_model_registry"


@dataclass(frozen=True)
class MemoryRefSource:
    """Read-only memory reference source skeleton for AS2 validation."""

    expected_memory_space_id: str
    memory_space_policy_version: str
    refs: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    schema_version: str = MEMORY_REF_SOURCE_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE
    type: str = "memory_ref_source"


@dataclass(frozen=True)
class CapabilityGrantSource:
    """Declarative capability-grant source skeleton."""

    grants: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    schema_version: str = CAPABILITY_GRANT_SOURCE_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE
    type: str = "capability_grant_source"


@dataclass(frozen=True)
class AdapterDerivationRecordSkeleton:
    """AdapterDerivationRecord shape placeholder for fixture compatibility."""

    input_hashes: Mapping[str, str]
    memory_space_policy: Mapping[str, str]
    schema_version: str = "alpha3g.adapter_derivation.v1"
    profile: str = AS2_FIXTURE_PROFILE


@dataclass(frozen=True)
class AdapterDefinitionSource:
    """Explicit definition/config source required for standalone projection.

    R9 closure: AdapterIdentityContext is identity-only and does not carry
    AgentDefinitionRef, config, or canonical_fields. Projection therefore
    consumes this separate explicit source instead of inferring those values
    from legacy runtime state.
    """

    definition_ref: Mapping[str, Any]
    config: Mapping[str, Any] = field(default_factory=dict)
    canonical_fields: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ADAPTER_DEFINITION_SOURCE_SCHEMA
    profile: str = AS2_FIXTURE_PROFILE
    type: str = "adapter_definition_source"


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.match(value) is not None


def _require_mapping(name: str, value: Any, error_type: type[AS2AdapterError] = AS2AdapterInputError) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_type(f"{name} must be a mapping")
    return value


def _require_nonempty_string(name: str, value: Any, error_type: type[AS2AdapterError] = AS2AdapterInputError) -> str:
    if not isinstance(value, str) or not value:
        raise error_type(f"{name} must be a non-empty string")
    return value


def _require_sha256(name: str, value: Any, error_type: type[AS2AdapterError] = AS2AdapterInputError) -> str:
    _require_nonempty_string(name, value, error_type)
    if not _is_sha256(value):
        raise error_type(f"{name} must be a sha256:<64 lowercase hex> digest")
    return value


def validate_identity_context(adapter_identity_context: Mapping[str, Any] | None) -> None:
    """Validate explicit complete-or-absent AS2 identity input.

    Raises ``AdapterIdentityContextMissingError`` when the context is absent and
    ``AdapterIdentityContextIncompleteError`` when required seed fields are
    missing or malformed. The function performs no agent-id derivation.
    """

    if adapter_identity_context is None:
        raise AdapterIdentityContextMissingError(
            "AdapterIdentityContext is required for AS2 canonical projection",
            _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1", "adapter_identity_context"),
        )
    ctx = _require_mapping("adapter_identity_context", adapter_identity_context, AdapterIdentityContextIncompleteError)
    if ctx.get("schema_version") != ADAPTER_IDENTITY_CONTEXT_SCHEMA:
        raise AdapterIdentityContextIncompleteError("unsupported AdapterIdentityContext schema_version")
    if ctx.get("profile") != AS2_FIXTURE_PROFILE:
        raise AdapterIdentityContextIncompleteError("unsupported AdapterIdentityContext profile")
    seed = ctx.get("identity_seed")
    if not isinstance(seed, Mapping):
        raise AdapterIdentityContextIncompleteError("identity_seed is required")
    for field_name in ("parent_anchor", "definition_hash", "spawn_nonce", "namespace"):
        if field_name not in seed:
            raise AdapterIdentityContextIncompleteError(
                f"identity_seed.{field_name} is required",
                _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1", f"identity_seed.{field_name}"),
            )
        _require_nonempty_string(f"identity_seed.{field_name}", seed[field_name], AdapterIdentityContextIncompleteError)
    _require_sha256("identity_seed.definition_hash", seed["definition_hash"], AdapterIdentityContextIncompleteError)
    if "alias" in seed and seed["alias"] is not None:
        _require_nonempty_string("identity_seed.alias", seed["alias"], AdapterIdentityContextIncompleteError)
        # P0.6.6 hardening: whitespace-only alias is an identity drift surface.
        # P0.5.9 AgentIdSeed collapses it to None at the standalone-core layer;
        # the AS2 boundary must not accept ambiguous alias shapes at all because
        # the adapter has no authority to silently rewrite caller-supplied identity.
        if isinstance(seed["alias"], str) and seed["alias"].strip() == "":
            raise AdapterIdentityContextIncompleteError(
                "identity_seed.alias must not be whitespace-only; use null for 'no alias'"
            )
    audit = ctx.get("audit_context")
    if audit is not None:
        audit_map = _require_mapping("audit_context", audit, AdapterIdentityContextIncompleteError)
        if "soulprint" in audit_map:
            _require_sha256("audit_context.soulprint", audit_map["soulprint"], AdapterIdentityContextIncompleteError)
        if "identity_version" in audit_map:
            version = audit_map["identity_version"]
            # P0.6.6 hardening: identity_version must be a non-negative integer.
            # bool is a subclass of int in Python, but True/False as a version is
            # nonsensical at the audit boundary; reject explicitly.
            if isinstance(version, bool) or not isinstance(version, int):
                raise AdapterIdentityContextIncompleteError("audit_context.identity_version must be an integer")
            if version < 0:
                raise AdapterIdentityContextIncompleteError("audit_context.identity_version must be non-negative")


def validate_model_registry(legacy_model: str, static_model_registry: Mapping[str, Any] | None) -> None:
    """Validate legacy model mapping through a static mock-only registry."""

    if static_model_registry is None:
        raise ModelRefUnknownError("StaticModelRegistry is required")
    registry = _require_mapping("static_model_registry", static_model_registry, ModelRefUnknownError)
    if registry.get("schema_version") != STATIC_MODEL_REGISTRY_SCHEMA:
        raise ModelRefUnknownError("unsupported StaticModelRegistry schema_version")
    if registry.get("profile") != AS2_FIXTURE_PROFILE:
        raise ModelRefUnknownError("unsupported StaticModelRegistry profile")
    _require_sha256("static_model_registry.registry_snapshot_hash", registry.get("registry_snapshot_hash"), ModelRefUnknownError)
    entries = registry.get("entries")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise ModelRefUnknownError("StaticModelRegistry.entries must be a sequence")
    # P0.6.6 hardening: detect duplicate legacy_model keys across the registry
    # snapshot. RFC §5.2 mandates append-only history, but inside a single
    # registry snapshot a duplicate legacy_model creates lookup ambiguity
    # (first match wins is an implementation detail, not a contract). Fail
    # closed so registry construction errors surface at the boundary, not as
    # silent provider drift downstream.
    seen_legacy: set[str] = set()
    match: Mapping[str, Any] | None = None
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_legacy = entry.get("legacy_model")
        if not isinstance(entry_legacy, str):
            continue
        if entry_legacy in seen_legacy:
            raise ModelRefUnknownError(
                f"duplicate legacy_model in StaticModelRegistry: {entry_legacy!r}"
            )
        seen_legacy.add(entry_legacy)
        if entry_legacy == legacy_model and match is None:
            match = entry
    if match is None:
        raise ModelRefUnknownError(
        f"unknown model selection mapping: {legacy_model!r}",
        _violation(
            "RFC-AGENT-SNAPSHOT-ADAPTER.md §5.2",
            "model_selection_source.model",
            actual_value=legacy_model,
        ),
    )
    model_ref = match.get("model_ref")
    if not isinstance(model_ref, Mapping):
        raise ModelRefUnknownError("matching registry entry lacks model_ref")
    if model_ref.get("provider_namespace") not in _ALLOWED_PROVIDER_NAMESPACES:
        raise ModelRefUnknownError("provider_namespace is not allowed in P0.6.5 skeleton")
    for field_name in ("model_id", "model_version"):
        _require_nonempty_string(f"model_ref.{field_name}", model_ref.get(field_name), ModelRefUnknownError)
    _require_sha256("model_ref.capability_profile_hash", model_ref.get("capability_profile_hash"), ModelRefUnknownError)


def validate_memory_ref_source(memory_ref_source: Mapping[str, Any] | None) -> None:
    """Validate MemoryRefSource shape and per-ref memory-space boundary."""

    if memory_ref_source is None:
        raise MemoryRefSourceMissingError(
            "MemoryRefSource is required; empty memory must be refs: []",
            _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §5.3", "memory_ref_source"),
        )
    source = _require_mapping("memory_ref_source", memory_ref_source, MemoryRefSourceMissingError)
    if source.get("schema_version") != MEMORY_REF_SOURCE_SCHEMA:
        raise MemoryRefSourceMissingError("unsupported MemoryRefSource schema_version")
    if source.get("profile") != AS2_FIXTURE_PROFILE:
        raise MemoryRefSourceMissingError("unsupported MemoryRefSource profile")
    expected = source.get("expected_memory_space_id")
    _require_sha256("memory_ref_source.expected_memory_space_id", expected, AdapterMemorySpaceMismatchError)
    if source.get("memory_space_policy_version") != MEMORY_SPACE_POLICY_V1:
        raise AdapterMemorySpaceMismatchError("unsupported memory_space_policy_version")
    refs = source.get("refs")
    if not isinstance(refs, list):
        raise MemoryRefSourceMissingError("MemoryRefSource.refs must be an explicit list, not null or omitted")
    # P0.6.6 hardening: align with P0.5.9 standalone-core invariants. Duplicate
    # or conflicting memory refs are construction errors that must surface at
    # the AS2 boundary so they never reach projection or AgentSnapshot
    # construction (which would also reject them, but later and with worse
    # diagnostics).
    seen_exact: set[tuple[str, str, str]] = set()
    seen_address_mode: dict[tuple[str, str], str] = {}
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise AdapterMemorySpaceMismatchError("memory_ref entries must be mappings")
        if ref.get("memory_space_id") != expected:
            raise AdapterMemorySpaceMismatchError(
                "memory_ref.memory_space_id does not match expected_memory_space_id",
                _violation(
                    "RFC-AGENT-SNAPSHOT-ADAPTER.md §7.2",
                    "memory_ref_source.refs[].memory_space_id",
                    expected_value=expected,
                    actual_value=ref.get("memory_space_id"),
                ),
            )
        memory_key = ref.get("memory_key")
        access_mode = ref.get("access_mode")
        if not isinstance(memory_key, str) or not isinstance(access_mode, str):
            # leave deeper memory_ref shape validation to core; AS2 only enforces
            # space-boundary and duplicate invariants
            continue
        exact = (expected, memory_key, access_mode)
        if exact in seen_exact:
            raise AdapterMemorySpaceMismatchError(
                f"duplicate memory_ref entry: memory_key={memory_key!r} access_mode={access_mode!r}"
            )
        seen_exact.add(exact)
        address = (expected, memory_key)
        prior = seen_address_mode.get(address)
        if prior is not None and prior != access_mode:
            raise AdapterMemorySpaceMismatchError(
                f"conflicting access_mode for memory_key={memory_key!r}: "
                f"{prior!r} and {access_mode!r}"
            )
        seen_address_mode[address] = access_mode


def validate_capability_grant_source(capability_grant_source: Mapping[str, Any] | None) -> None:
    """Validate declarative capability grants without inspecting live tools."""

    if capability_grant_source is None:
        raise CapabilityGrantSourceMissingError(
            "CapabilityGrantSource is required for AS2 canonical projection",
            _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §5.4", "capability_grant_source"),
        )
    source = _require_mapping("capability_grant_source", capability_grant_source, CapabilityGrantSourceMissingError)
    if source.get("schema_version") != CAPABILITY_GRANT_SOURCE_SCHEMA:
        raise CapabilityGrantSourceMissingError("unsupported CapabilityGrantSource schema_version")
    if source.get("profile") != AS2_FIXTURE_PROFILE:
        raise CapabilityGrantSourceMissingError("unsupported CapabilityGrantSource profile")
    grants = source.get("grants")
    if not isinstance(grants, list):
        raise CapabilityGrantSourceMissingError("CapabilityGrantSource.grants must be an explicit list")
    # P0.6.6 hardening: align with P0.5.9 standalone-core invariants. Multiple
    # grants per tool_namespace is ambiguous (which scope/policy wins?) and
    # must be a CapabilityGrantSource-construction error, not silent first-wins
    # behavior downstream.
    seen_namespaces: set[str] = set()
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise CapabilityGrantInvalidRefError("capability grant entries must be mappings")
        if grant.get("schema_version") != CAPABILITY_GRANT_SCHEMA:
            raise CapabilityGrantInvalidRefError("unsupported capability grant schema_version")
        tool_namespace = grant.get("tool_namespace")
        _require_nonempty_string("capability_grant.tool_namespace", tool_namespace, CapabilityGrantInvalidRefError)
        if tool_namespace in seen_namespaces:
            raise CapabilityGrantInvalidRefError(
                f"duplicate capability_grant for tool_namespace={tool_namespace!r}"
            )
        seen_namespaces.add(tool_namespace)
        fd_ref = grant.get("function_descriptor_ref")
        if not isinstance(fd_ref, Mapping):
            raise CapabilityGrantInvalidRefError("capability grant requires function_descriptor_ref")
        for field_name in ("namespace", "symbol"):
            _require_nonempty_string(f"function_descriptor_ref.{field_name}", fd_ref.get(field_name), CapabilityGrantInvalidRefError)
        for hash_name in ("input_schema_hash", "output_schema_hash"):
            _require_sha256(f"function_descriptor_ref.{hash_name}", fd_ref.get(hash_name), CapabilityGrantInvalidRefError)
        _require_sha256("capability_grant.effect_policy_hash", grant.get("effect_policy_hash"), CapabilityGrantInvalidRefError)


def validate_adapter_definition_source(adapter_definition_source: Mapping[str, Any] | None) -> None:
    """Validate explicit R9 definition/config source for projection."""

    if adapter_definition_source is None:
        raise AdapterIdentityContextIncompleteError("AdapterDefinitionSource is required for AS2 projection")
    source = _require_mapping(
        "adapter_definition_source",
        adapter_definition_source,
        AdapterIdentityContextIncompleteError,
    )
    if source.get("schema_version") != ADAPTER_DEFINITION_SOURCE_SCHEMA:
        raise AdapterIdentityContextIncompleteError("unsupported AdapterDefinitionSource schema_version")
    if source.get("profile") != AS2_FIXTURE_PROFILE:
        raise AdapterIdentityContextIncompleteError("unsupported AdapterDefinitionSource profile")
    definition_ref = source.get("definition_ref")
    if not isinstance(definition_ref, Mapping):
        raise AdapterIdentityContextIncompleteError("AdapterDefinitionSource.definition_ref is required")
    # Delegate exact core shape checks to the hardened AgentDefinitionRef value object.
    try:
        AgentDefinitionRef.from_dict(definition_ref)
    except Exception as exc:  # core errors are intentionally collapsed at the AS2 input boundary
        raise AdapterIdentityContextIncompleteError(f"invalid AdapterDefinitionSource.definition_ref: {exc}") from exc
    for mapping_name in ("config", "canonical_fields"):
        if mapping_name not in source:
            raise AdapterIdentityContextIncompleteError(f"AdapterDefinitionSource.{mapping_name} is required")
        if not isinstance(source.get(mapping_name), Mapping):
            raise AdapterIdentityContextIncompleteError(f"AdapterDefinitionSource.{mapping_name} must be a mapping")


def _derive_agent_id_from_identity_context(adapter_identity_context: Mapping[str, Any]) -> str:
    seed = adapter_identity_context["identity_seed"]
    return AgentIdSeed(
        parent_anchor=seed["parent_anchor"],
        definition_hash=seed["definition_hash"],
        spawn_nonce=seed["spawn_nonce"],
        alias=seed.get("alias"),
        namespace=seed["namespace"],
    ).derive_agent_id()


def _select_model_ref(legacy_model: str, static_model_registry: Mapping[str, Any]) -> Mapping[str, Any]:
    for entry in static_model_registry.get("entries", []):
        if isinstance(entry, Mapping) and entry.get("legacy_model") == legacy_model:
            model_ref = entry.get("model_ref")
            if isinstance(model_ref, Mapping):
                return dict(model_ref)
    raise ModelRefUnknownError(
        f"unknown model selection mapping: {legacy_model!r}",
        _violation(
            "RFC-AGENT-SNAPSHOT-ADAPTER.md §5.2",
            "model_selection_source.model",
            actual_value=legacy_model,
        ),
    )


def _project_memory_refs(memory_ref_source: Mapping[str, Any]) -> tuple[MemoryRef, ...]:
    return tuple(MemoryRef.from_dict(ref) for ref in memory_ref_source.get("refs", []))


def _project_capability_grant(grant: Mapping[str, Any]) -> CapabilityGrant:
    """R8-A canonical projection from AS2-rich grant to core v1 grant.

    Core CapabilityGrant v1 carries ``tool_namespace``, ``scope_hash``, and
    ``policy_ref``. AS2 carries richer FunctionDescriptor/effect-policy shape.
    P0.6.8 preserves ``tool_namespace`` directly and folds the richer AS2 fields
    into a deterministic ``scope_hash`` payload. This avoids core schema drift
    while keeping the reduction reproducible and auditable.
    """

    tool_namespace = grant["tool_namespace"]
    policy_ref = grant.get("policy_ref")
    _require_nonempty_string("capability_grant.policy_ref", policy_ref, CapabilityGrantInvalidRefError)
    scope_payload = {
        "type": "as2_capability_projection_scope",
        "projection_version": AS2_CAPABILITY_PROJECTION_VERSION,
        "tool_namespace": tool_namespace,
        "function_descriptor_ref": grant["function_descriptor_ref"],
        "effect_policy_hash": grant["effect_policy_hash"],
        "policy_ref": policy_ref,
        "profile": AS2_FIXTURE_PROFILE,
    }
    return CapabilityGrant(
        tool_namespace=tool_namespace,
        scope_hash=stable_canonical_hash(scope_payload),
        policy_ref=policy_ref,
    )


def _project_capability_grants(capability_grant_source: Mapping[str, Any]) -> tuple[CapabilityGrant, ...]:
    return tuple(_project_capability_grant(grant) for grant in capability_grant_source.get("grants", []))


def _compute_derivation_input_hashes(
    *,
    adapter_identity_context: Mapping[str, Any],
    static_model_registry: Mapping[str, Any],
    adapter_definition_source: Mapping[str, Any],
    memory_ref_source: Mapping[str, Any],
    capability_grant_source: Mapping[str, Any],
) -> Mapping[str, str]:
    """Compute P0.6.8 Merkle-transparent hashes for all explicit AS2 inputs."""

    return {
        "identity_context_hash": stable_canonical_hash(adapter_identity_context),
        "model_registry_snapshot_hash": stable_canonical_hash(static_model_registry),
        "adapter_definition_source_hash": stable_canonical_hash(adapter_definition_source),
        "memory_ref_source_hash": stable_canonical_hash(memory_ref_source),
        "capability_grant_source_hash": stable_canonical_hash(capability_grant_source),
    }


def _build_derivation_record(
    *,
    adapter_identity_context: Mapping[str, Any],
    static_model_registry: Mapping[str, Any],
    adapter_definition_source: Mapping[str, Any],
    memory_ref_source: Mapping[str, Any],
    capability_grant_source: Mapping[str, Any],
) -> AdapterDerivationRecordSkeleton:
    """Return P0.6.8 derivation record with real stable-canonical input hashes."""

    return AdapterDerivationRecordSkeleton(
        input_hashes=_compute_derivation_input_hashes(
            adapter_identity_context=adapter_identity_context,
            static_model_registry=static_model_registry,
            adapter_definition_source=adapter_definition_source,
            memory_ref_source=memory_ref_source,
            capability_grant_source=capability_grant_source,
        ),
        memory_space_policy={
            "policy_version": memory_ref_source.get("memory_space_policy_version", ""),
            "expected_memory_space_id": memory_ref_source.get("expected_memory_space_id", ""),
        },
    )


def _resolve_model_selection(
    *,
    model_selection_source: Mapping[str, Any] | None = None,
) -> str:
    """Resolve the canonical AS2 model selector after P0.6.20 Contract.

    P0.6.20 Contract completes AS2 model-selection naming.
    The validation and projection boundary now accepts only
    ``model_selection_source``.
    """

    if isinstance(model_selection_source, Mapping):
        model = model_selection_source.get("model")
        return _require_nonempty_string("model_selection_source.model", model, ModelRefUnknownError)

    if model_selection_source is not None:
        _require_mapping("model_selection_source", model_selection_source, ModelRefUnknownError)

    raise ModelRefUnknownError(
        "model_selection_source is required",
        _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §5.2", "model_selection_source.model"),
    )


def validate_as2_inputs(
    *,
    adapter_identity_context: Mapping[str, Any] | None = None,
    static_model_registry: Mapping[str, Any] | None = None,
    memory_ref_source: Mapping[str, Any] | None = None,
    capability_grant_source: Mapping[str, Any] | None = None,
    adapter_definition_source: Mapping[str, Any] | None = None,
    model_selection_source: Mapping[str, Any] | None = None,
    candidate_output_envelope: Mapping[str, Any] | None = None,
    inline_memory_payload: Any | None = None,
    subagent_runtime_graph: Any | None = None,
    harness_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Validate AS2 explicit inputs without building an ``AgentSnapshot``.

    This is the validation boundary used by P0.6.7 projection. It may reject inputs with the
    typed AS2 error taxonomy, but it must not perform projection or legacy
    integration.
    """

    # P0.6.6 hardening: RFC §6.3 forbids reuse of the legacy envelope marker in
    # canonical AS2 payloads. The legacy marker is the *key* `__type__`, not a
    # specific value: Environment._json_safe emits "__type__": "agent" /
    # "durable_actor_ref" / "durable_promise" / "opaque". Any presence of
    # `__type__` on the candidate canonical envelope is therefore a conflict.
    if candidate_output_envelope is not None and isinstance(candidate_output_envelope, Mapping):
        if "__type__" in candidate_output_envelope:
            raise AdapterEnvelopeConflictError(
                "AS2 canonical output must not carry legacy '__type__' envelope marker",
                _violation(
                    "RFC-AGENT-SNAPSHOT-ADAPTER.md §6.3",
                    "candidate_output_envelope.__type__",
                    actual_value=candidate_output_envelope.get("__type__"),
                ),
            )
    if subagent_runtime_graph is not None:
        raise AdapterSubagentOutOfScopeError(
            "subagent/fracture runtime graph is out of AS2 v1 scope",
            _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §11", "subagent_runtime_graph"),
        )
    if harness_metadata is not None and harness_metadata.get("requires_sandbox_mock") is True:
        forbidden_calls = harness_metadata.get("forbidden_calls", [])
        if forbidden_calls:
            raise AdapterAmbientAuthorityError(
                "ambient authority markers are forbidden in AS2 adapter inputs",
                _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §3", "harness_metadata.forbidden_calls"),
            )
    if inline_memory_payload is not None:
        raise AdapterInlineMemoryRejectedError(
            "inline memory payload is forbidden in AS2 canonical path",
            _violation("RFC-AGENT-SNAPSHOT-ADAPTER.md §7.1", "inline_memory_payload"),
        )

    validate_identity_context(adapter_identity_context)

    selected_model = _resolve_model_selection(
        model_selection_source=model_selection_source,
    )
    validate_model_registry(selected_model, static_model_registry)
    validate_memory_ref_source(memory_ref_source)
    validate_capability_grant_source(capability_grant_source)
    if adapter_definition_source is not None:
        validate_adapter_definition_source(adapter_definition_source)


def project_validated_as2_inputs(
    *,
    adapter_identity_context: Mapping[str, Any],
    adapter_definition_source: Mapping[str, Any],
    static_model_registry: Mapping[str, Any],
    memory_ref_source: Mapping[str, Any],
    capability_grant_source: Mapping[str, Any],
    model_selection_source: Mapping[str, Any] | None = None,
    expected_derivation_record: Mapping[str, Any] | None = None,
) -> tuple[AgentSnapshot, AdapterDerivationRecordSkeleton]:
    """Project validated explicit AS2 inputs into a standalone AgentSnapshot.

    P0.6.8 projection is fixture-driven and standalone only. It does
    not accept AgentRuntime, does not call ``to_dict()``, does not perform I/O,
    computes AdapterDerivationRecord input hashes, and does not wire into legacy
    runtime paths.
    """

    validate_as2_inputs(
        adapter_identity_context=adapter_identity_context,
        adapter_definition_source=adapter_definition_source,
        static_model_registry=static_model_registry,
        memory_ref_source=memory_ref_source,
        capability_grant_source=capability_grant_source,
        model_selection_source=model_selection_source,
    )

    validate_adapter_definition_source(adapter_definition_source)
    selected_model = _resolve_model_selection(
        model_selection_source=model_selection_source,
    )
    snapshot = AgentSnapshot(
        agent_id=_derive_agent_id_from_identity_context(adapter_identity_context),
        definition_ref=AgentDefinitionRef.from_dict(adapter_definition_source["definition_ref"]),
        config=dict(adapter_definition_source["config"]),
        canonical_fields=dict(adapter_definition_source["canonical_fields"]),
        memory_refs=_project_memory_refs(memory_ref_source),
        model_ref=_select_model_ref(selected_model, static_model_registry),
        capability_grants=_project_capability_grants(capability_grant_source),
    )
    derivation = _build_derivation_record(
        adapter_identity_context=adapter_identity_context,
        static_model_registry=static_model_registry,
        adapter_definition_source=adapter_definition_source,
        memory_ref_source=memory_ref_source,
        capability_grant_source=capability_grant_source,
    )
    return snapshot, derivation
