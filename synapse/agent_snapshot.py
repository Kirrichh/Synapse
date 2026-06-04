"""Standalone AgentSnapshot schema/value core (Alpha3g P0.5.8 / SA1).

This module implements pure value objects, validation, and stable-canonical
serialization helpers for the approved Agent Canonicalization v1.0 boundary.
It is intentionally standalone: it does not import or modify AgentRuntime,
interpreter, actor runtime, memory backends, CVM/opcodes, golden fixtures, or
FunctionDescriptor runtime registries.

P0.5.8 uses a local fail-closed schema/profile allowlist as authorized by
``docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md``. The allowlist is sufficient only
for standalone schema/value work; deployment and integration still require the
future central schema/profile registry gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from synapse.canonical_service import (
    STABLE_CANONICAL_PROFILE,
    stable_canonical_hash,
)
from synapse.canonical_values import CanonicalSerializationError as StableCanonicalSerializationError
from synapse.canonical_values import is_canonical_serializable

AGENT_SNAPSHOT_SCHEMA = "alpha3g.agent_snapshot.v1"
AGENT_DEFINITION_REF_SCHEMA = "alpha3g.agent_definition_ref.v1"
AGENT_ID_SCHEMA = "alpha3g.agent_id.v1"
MEMORY_REF_SCHEMA = "alpha3g.memory_ref.v1"
MEMORY_SPACE_ID_SCHEMA = "alpha3g.memory_space_id.v1"
CAPABILITY_GRANT_SCHEMA = "alpha3g.capability_grant.v1"
FUNCTION_DESCRIPTOR_SCHEMA = "alpha3g.function_descriptor.v1"

LOCAL_SCHEMA_ALLOWLIST = frozenset(
    {
        AGENT_SNAPSHOT_SCHEMA,
        AGENT_DEFINITION_REF_SCHEMA,
        AGENT_ID_SCHEMA,
        MEMORY_REF_SCHEMA,
        MEMORY_SPACE_ID_SCHEMA,
        CAPABILITY_GRANT_SCHEMA,
        FUNCTION_DESCRIPTOR_SCHEMA,
        STABLE_CANONICAL_PROFILE,
    }
)

AGENT_SNAPSHOT_TYPE = "agent_snapshot"
AGENT_DEFINITION_REF_TYPE = "agent_definition_ref"
AGENT_ID_SEED_TYPE = "agent_id_seed"
MEMORY_REF_TYPE = "memory_ref"
CAPABILITY_GRANT_TYPE = "capability_grant"

ACCESS_MODES = frozenset({"read", "write", "read-write"})
AGENT_SNAPSHOT_FIELDS = frozenset(
    {
        "type",
        "schema_version",
        "profile",
        "agent_id",
        "definition_ref",
        "config",
        "canonical_fields",
        "memory_refs",
        "model_ref",
        "capability_grants",
    }
)

_RUNTIME_ENVELOPE_FIELDS = frozenset(
    {
        "tools",
        "llm",
        "env",
        "mailbox",
        "scheduler",
        "thread",
        "task",
        "socket",
        "file",
        "logger",
        "tracer",
        "metrics",
        "process_id",
        "thread_id",
        "memory",
    }
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AgentSnapshotError(RuntimeError):
    """Base error for standalone AgentSnapshot value-core failures."""


class AgentSnapshotValidationError(AgentSnapshotError, ValueError):
    """Raised when a standalone AgentSnapshot value object is invalid."""


class AgentSnapshotSerializationError(AgentSnapshotValidationError, TypeError):
    """Raised when an AgentSnapshot field is not stable-canonical serializable."""


class AgentSnapshotSchemaError(AgentSnapshotValidationError):
    """Raised when a snapshot schema/profile is missing or unsupported."""


class AgentRuntimeEnvelopeViolation(AgentSnapshotValidationError):
    """Raised when runtime-envelope fields try to enter canonical snapshot data."""


class AgentCapabilityGrantError(AgentSnapshotValidationError):
    """Raised when a capability grant is malformed or unsupported."""


class AgentMemoryRefError(AgentSnapshotValidationError):
    """Raised when a memory reference lacks stable canonical structure."""


class MemoryRefNotResolvedError(AgentMemoryRefError):
    """Reserved fail-closed error for future runtime/storage dereference failures."""


class AgentDefinitionRefError(AgentSnapshotValidationError):
    """Raised when an agent definition reference is unstable or malformed."""


class AgentIdCollisionError(AgentSnapshotValidationError):
    """Reserved fail-closed error for recorded agent-id collision checks."""


class UnknownSchemaVersionError(AgentSnapshotSchemaError):
    """Raised when a schema_version or profile is outside the local allowlist."""


def _require_known_schema(schema_version: str) -> None:
    if schema_version not in LOCAL_SCHEMA_ALLOWLIST:
        raise UnknownSchemaVersionError(f"unsupported AgentSnapshot schema/profile: {schema_version!r}")


def _require_profile(profile: str) -> None:
    _require_known_schema(profile)
    if profile != STABLE_CANONICAL_PROFILE:
        raise UnknownSchemaVersionError(f"unsupported AgentSnapshot profile: {profile!r}")


def _require_type(value: str, expected: str) -> None:
    if value != expected:
        raise AgentSnapshotSchemaError(f"invalid type {value!r}; expected {expected!r}")


def _require_nonempty_string(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise AgentSnapshotValidationError(f"{name} must be a non-empty string")


def _require_sha256(name: str, value: str) -> None:
    _require_nonempty_string(name, value)
    if not _SHA256_RE.match(value):
        raise AgentSnapshotValidationError(f"{name} must be a sha256:<64 lowercase hex> digest")


def _require_stable_value(name: str, value: Any) -> None:
    if not is_canonical_serializable(value):
        raise AgentSnapshotSerializationError(f"{name} is not stable-canonical.v1 serializable")


def _reject_runtime_fields(payload: Mapping[str, Any], *, context: str) -> None:
    leaked = sorted(_RUNTIME_ENVELOPE_FIELDS.intersection(payload))
    if leaked:
        raise AgentRuntimeEnvelopeViolation(f"runtime-envelope fields forbidden in {context}: {', '.join(leaked)}")


def _reject_extra_fields(payload: Mapping[str, Any], allowed: frozenset[str], *, context: str) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise AgentSnapshotSchemaError(f"unexpected fields in {context}: {', '.join(extra)}")


def _deep_freeze(value: Any) -> Any:
    """Recursively convert mapping/sequence containers into immutable views.

    Frozen dataclass attributes are unreassignable, but ``config = {...}``
    still leaves the underlying dict mutable through any retained external
    reference. SA1 was found vulnerable to ``cfg["k"] = ...`` after construction
    silently shifting ``snapshot_hash()``. P0.5.9 closes this by storing a deep
    read-only view as the canonical attribute value.

    Scalars and already-immutable values pass through unchanged. Tuples are
    recursed but kept as tuples. ``bytes``/``bytearray`` are treated as scalars
    (canonical layer rejects them anyway via ``is_canonical_serializable``).
    """

    if isinstance(value, Mapping):
        return MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _reject_duplicate_memory_refs(refs: Sequence["MemoryRef"]) -> None:
    """Reject identical or conflicting MemoryRef entries.

    Two distinct rules:
    - exact duplicate ``(memory_space_id, memory_key, access_mode)`` is invalid;
    - same logical address ``(memory_space_id, memory_key)`` with differing
      ``access_mode`` is invalid because a canonical snapshot must declare one
      coherent access posture per address (use ``read-write`` to express both).
    """

    seen_exact: set[tuple[str, str, str]] = set()
    seen_address: dict[tuple[str, str], str] = {}
    for ref in refs:
        exact = (ref.memory_space_id, ref.memory_key, ref.access_mode)
        if exact in seen_exact:
            raise AgentMemoryRefError(
                f"duplicate memory_ref entry: memory_key={ref.memory_key!r} access_mode={ref.access_mode!r}"
            )
        seen_exact.add(exact)
        address = (ref.memory_space_id, ref.memory_key)
        prior = seen_address.get(address)
        if prior is not None and prior != ref.access_mode:
            raise AgentMemoryRefError(
                f"conflicting access_mode for memory_key={ref.memory_key!r}: "
                f"{prior!r} and {ref.access_mode!r} (use 'read-write' for combined access)"
            )
        seen_address[address] = ref.access_mode


def _reject_duplicate_capability_grants(grants: Sequence["CapabilityGrant"]) -> None:
    """Reject duplicate capability grants for the same ``tool_namespace``.

    Multiple grants per tool_namespace is ambiguous: a canonical snapshot must
    express exactly one declarative grant per tool. Differing ``scope_hash``
    or ``policy_ref`` for the same tool is therefore a snapshot-construction
    error, not a runtime composition step.
    """

    seen: dict[str, "CapabilityGrant"] = {}
    for grant in grants:
        prior = seen.get(grant.tool_namespace)
        if prior is not None:
            raise AgentCapabilityGrantError(
                f"duplicate capability_grant for tool_namespace={grant.tool_namespace!r}"
            )
        seen[grant.tool_namespace] = grant


def _normalize_alias(alias: str | None) -> str | None:
    """Collapse empty / whitespace-only alias to ``None``.

    ``alias`` is an optional human-readable label; an empty or whitespace-only
    string is semantically identical to "no alias" but produces a different
    ``agent_id`` hash, which is an identity drift surface. P0.5.9 normalizes
    both shapes to ``None`` before hashing.
    """

    if alias is None:
        return None
    if not isinstance(alias, str):
        raise AgentSnapshotValidationError("alias must be a string or None")
    if alias.strip() == "":
        return None
    return alias


@dataclass(frozen=True)
class AgentDefinitionRef:
    """Canonical reference to an externally declared static agent definition."""

    namespace: str
    class_name: str
    declared_version: str
    interface_schema_hash: str
    config_schema_hash: str
    capability_schema_hash: str
    manifest_hash: str
    schema_version: str = AGENT_DEFINITION_REF_SCHEMA
    profile: str = STABLE_CANONICAL_PROFILE
    type: str = AGENT_DEFINITION_REF_TYPE

    def __post_init__(self) -> None:
        _require_known_schema(self.schema_version)
        _require_profile(self.profile)
        _require_type(self.type, AGENT_DEFINITION_REF_TYPE)
        _require_nonempty_string("namespace", self.namespace)
        _require_nonempty_string("class_name", self.class_name)
        _require_nonempty_string("declared_version", self.declared_version)
        _require_sha256("interface_schema_hash", self.interface_schema_hash)
        _require_sha256("config_schema_hash", self.config_schema_hash)
        _require_sha256("capability_schema_hash", self.capability_schema_hash)
        _require_sha256("manifest_hash", self.manifest_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "schema_version": self.schema_version,
            "namespace": self.namespace,
            "class_name": self.class_name,
            "declared_version": self.declared_version,
            "interface_schema_hash": self.interface_schema_hash,
            "config_schema_hash": self.config_schema_hash,
            "capability_schema_hash": self.capability_schema_hash,
            "manifest_hash": self.manifest_hash,
            "profile": self.profile,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentDefinitionRef":
        _reject_extra_fields(
            payload,
            frozenset(
                {
                    "type",
                    "schema_version",
                    "namespace",
                    "class_name",
                    "declared_version",
                    "interface_schema_hash",
                    "config_schema_hash",
                    "capability_schema_hash",
                    "manifest_hash",
                    "profile",
                }
            ),
            context="AgentDefinitionRef",
        )
        return cls(
            type=payload.get("type", ""),
            schema_version=payload.get("schema_version", ""),
            namespace=payload.get("namespace", ""),
            class_name=payload.get("class_name", ""),
            declared_version=payload.get("declared_version", ""),
            interface_schema_hash=payload.get("interface_schema_hash", ""),
            config_schema_hash=payload.get("config_schema_hash", ""),
            capability_schema_hash=payload.get("capability_schema_hash", ""),
            manifest_hash=payload.get("manifest_hash", ""),
            profile=payload.get("profile", ""),
        )


@dataclass(frozen=True)
class AgentIdSeed:
    """Deterministic seed for canonical agent_id derivation.

    ``causal_index`` is intentionally absent from this value object. It may live
    in recorded spawn event metadata for audit/debug output, but it must not
    participate in ``agent_id`` derivation.
    """

    parent_anchor: str
    definition_hash: str
    spawn_nonce: str
    alias: str | None
    namespace: str
    schema_version: str = AGENT_ID_SCHEMA
    type: str = AGENT_ID_SEED_TYPE

    def __post_init__(self) -> None:
        _require_known_schema(self.schema_version)
        _require_type(self.type, AGENT_ID_SEED_TYPE)
        _require_sha256("parent_anchor", self.parent_anchor)
        _require_sha256("definition_hash", self.definition_hash)
        _require_nonempty_string("spawn_nonce", self.spawn_nonce)
        object.__setattr__(self, "alias", _normalize_alias(self.alias))
        _require_nonempty_string("namespace", self.namespace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "schema_version": self.schema_version,
            "parent_anchor": self.parent_anchor,
            "definition_hash": self.definition_hash,
            "spawn_nonce": self.spawn_nonce,
            "alias": self.alias,
            "namespace": self.namespace,
        }

    def derive_agent_id(self) -> str:
        return stable_canonical_hash(self.to_dict())


@dataclass(frozen=True)
class MemoryRef:
    """Address-only canonical reference to a logical memory location."""

    memory_space_id: str
    memory_key: str
    access_mode: str
    schema_version: str = MEMORY_REF_SCHEMA
    profile: str = STABLE_CANONICAL_PROFILE
    type: str = MEMORY_REF_TYPE

    def __post_init__(self) -> None:
        _require_known_schema(self.schema_version)
        _require_profile(self.profile)
        _require_type(self.type, MEMORY_REF_TYPE)
        _require_sha256("memory_space_id", self.memory_space_id)
        _require_nonempty_string("memory_key", self.memory_key)
        if self.memory_key.strip() == "":
            raise AgentMemoryRefError("memory_key must not be whitespace-only")
        if self.access_mode not in ACCESS_MODES:
            raise AgentMemoryRefError(f"unsupported memory access_mode: {self.access_mode!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "schema_version": self.schema_version,
            "memory_space_id": self.memory_space_id,
            "memory_key": self.memory_key,
            "access_mode": self.access_mode,
            "profile": self.profile,
        }

    def memory_ref_id(self) -> str:
        return stable_canonical_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryRef":
        _reject_extra_fields(
            payload,
            frozenset({"type", "schema_version", "memory_space_id", "memory_key", "access_mode", "profile"}),
            context="MemoryRef",
        )
        return cls(
            type=payload.get("type", ""),
            schema_version=payload.get("schema_version", ""),
            memory_space_id=payload.get("memory_space_id", ""),
            memory_key=payload.get("memory_key", ""),
            access_mode=payload.get("access_mode", ""),
            profile=payload.get("profile", ""),
        )


@dataclass(frozen=True)
class CapabilityGrant:
    """Declarative capability grant; never a live tool object."""

    tool_namespace: str
    scope_hash: str
    policy_ref: str
    schema_version: str = CAPABILITY_GRANT_SCHEMA
    type: str = CAPABILITY_GRANT_TYPE

    def __post_init__(self) -> None:
        _require_known_schema(self.schema_version)
        _require_type(self.type, CAPABILITY_GRANT_TYPE)
        _require_nonempty_string("tool_namespace", self.tool_namespace)
        _require_sha256("scope_hash", self.scope_hash)
        _require_nonempty_string("policy_ref", self.policy_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "tool_namespace": self.tool_namespace,
            "scope_hash": self.scope_hash,
            "policy_ref": self.policy_ref,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapabilityGrant":
        _reject_extra_fields(
            payload,
            frozenset({"type", "tool_namespace", "scope_hash", "policy_ref", "schema_version"}),
            context="CapabilityGrant",
        )
        return cls(
            type=payload.get("type", ""),
            schema_version=payload.get("schema_version", ""),
            tool_namespace=payload.get("tool_namespace", ""),
            scope_hash=payload.get("scope_hash", ""),
            policy_ref=payload.get("policy_ref", ""),
        )


@dataclass(frozen=True)
class AgentSnapshot:
    """Standalone canonical AgentSnapshot v1 value object."""

    agent_id: str
    definition_ref: AgentDefinitionRef
    config: Mapping[str, Any] = field(default_factory=dict)
    canonical_fields: Mapping[str, Any] = field(default_factory=dict)
    memory_refs: tuple[MemoryRef, ...] = field(default_factory=tuple)
    model_ref: Mapping[str, Any] = field(default_factory=dict)
    capability_grants: tuple[CapabilityGrant, ...] = field(default_factory=tuple)
    schema_version: str = AGENT_SNAPSHOT_SCHEMA
    profile: str = STABLE_CANONICAL_PROFILE
    type: str = AGENT_SNAPSHOT_TYPE

    def __post_init__(self) -> None:
        _require_known_schema(self.schema_version)
        _require_profile(self.profile)
        _require_type(self.type, AGENT_SNAPSHOT_TYPE)
        _require_sha256("agent_id", self.agent_id)
        if not isinstance(self.definition_ref, AgentDefinitionRef):
            raise AgentDefinitionRefError("definition_ref must be an AgentDefinitionRef")
        object.__setattr__(self, "memory_refs", _coerce_tuple(self.memory_refs, MemoryRef, "memory_refs"))
        object.__setattr__(
            self, "capability_grants", _coerce_tuple(self.capability_grants, CapabilityGrant, "capability_grants")
        )
        _reject_duplicate_memory_refs(self.memory_refs)
        _reject_duplicate_capability_grants(self.capability_grants)
        for name in ("config", "canonical_fields", "model_ref"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise AgentSnapshotValidationError(f"{name} must be a mapping")
            _reject_runtime_fields(value, context=name)
            _require_stable_value(name, value)
            # Defensive deep-freeze: prevents external mutation of mapping
            # values from silently shifting snapshot_hash() after construction.
            object.__setattr__(self, name, _deep_freeze(value))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "type": self.type,
            "schema_version": self.schema_version,
            "profile": self.profile,
            "agent_id": self.agent_id,
            "definition_ref": self.definition_ref.to_dict(),
            "config": dict(self.config),
            "canonical_fields": dict(self.canonical_fields),
            "memory_refs": [ref.to_dict() for ref in self.memory_refs],
            "model_ref": dict(self.model_ref),
            "capability_grants": [grant.to_dict() for grant in self.capability_grants],
        }
        validate_agent_snapshot_payload(payload)
        return payload

    def snapshot_hash(self) -> str:
        return stable_canonical_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentSnapshot":
        validate_agent_snapshot_payload(payload)
        return cls(
            type=payload.get("type", ""),
            schema_version=payload.get("schema_version", ""),
            profile=payload.get("profile", ""),
            agent_id=payload.get("agent_id", ""),
            definition_ref=AgentDefinitionRef.from_dict(payload.get("definition_ref", {})),
            config=payload.get("config", {}),
            canonical_fields=payload.get("canonical_fields", {}),
            memory_refs=tuple(MemoryRef.from_dict(item) for item in payload.get("memory_refs", [])),
            model_ref=payload.get("model_ref", {}),
            capability_grants=tuple(CapabilityGrant.from_dict(item) for item in payload.get("capability_grants", [])),
        )


def _coerce_tuple(value: Sequence[Any], expected_type: type, name: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        value = tuple(value)
    for item in value:
        if not isinstance(item, expected_type):
            raise AgentSnapshotValidationError(f"{name} must contain {expected_type.__name__} values")
    return value


def validate_agent_snapshot_payload(payload: Mapping[str, Any]) -> None:
    """Validate raw AgentSnapshot v1 payload shape without integration side effects."""

    if not isinstance(payload, Mapping):
        raise AgentSnapshotSchemaError("AgentSnapshot payload must be a mapping")
    _reject_extra_fields(payload, AGENT_SNAPSHOT_FIELDS, context="AgentSnapshot")
    _reject_runtime_fields(payload, context="AgentSnapshot")
    required = AGENT_SNAPSHOT_FIELDS
    missing = sorted(required - set(payload))
    if missing:
        raise AgentSnapshotSchemaError(f"missing AgentSnapshot fields: {', '.join(missing)}")
    _require_type(payload["type"], AGENT_SNAPSHOT_TYPE)
    _require_known_schema(payload["schema_version"])
    _require_profile(payload["profile"])
    _require_sha256("agent_id", payload["agent_id"])
    if not isinstance(payload["definition_ref"], Mapping):
        raise AgentDefinitionRefError("definition_ref must be a mapping")
    AgentDefinitionRef.from_dict(payload["definition_ref"])
    if not isinstance(payload["memory_refs"], list):
        raise AgentMemoryRefError("memory_refs must be a list")
    memory_refs_parsed: list[MemoryRef] = []
    for item in payload["memory_refs"]:
        if not isinstance(item, Mapping):
            raise AgentMemoryRefError("memory_refs entries must be mappings")
        memory_refs_parsed.append(MemoryRef.from_dict(item))
    _reject_duplicate_memory_refs(memory_refs_parsed)
    if not isinstance(payload["capability_grants"], list):
        raise AgentCapabilityGrantError("capability_grants must be a list")
    capability_grants_parsed: list[CapabilityGrant] = []
    for item in payload["capability_grants"]:
        if not isinstance(item, Mapping):
            raise AgentCapabilityGrantError("capability_grants entries must be mappings")
        capability_grants_parsed.append(CapabilityGrant.from_dict(item))
    _reject_duplicate_capability_grants(capability_grants_parsed)
    for name in ("config", "canonical_fields", "model_ref"):
        value = payload[name]
        if not isinstance(value, Mapping):
            raise AgentSnapshotValidationError(f"{name} must be a mapping")
        _reject_runtime_fields(value, context=name)
        _require_stable_value(name, value)
    try:
        stable_canonical_hash(payload)
    except StableCanonicalSerializationError as exc:
        raise AgentSnapshotSerializationError(str(exc)) from exc


__all__ = [
    "ACCESS_MODES",
    "AGENT_DEFINITION_REF_SCHEMA",
    "AGENT_ID_SCHEMA",
    "AGENT_SNAPSHOT_FIELDS",
    "AGENT_SNAPSHOT_SCHEMA",
    "CAPABILITY_GRANT_SCHEMA",
    "FUNCTION_DESCRIPTOR_SCHEMA",
    "LOCAL_SCHEMA_ALLOWLIST",
    "MEMORY_REF_SCHEMA",
    "MEMORY_SPACE_ID_SCHEMA",
    "AgentCapabilityGrantError",
    "AgentDefinitionRef",
    "AgentDefinitionRefError",
    "AgentIdCollisionError",
    "AgentIdSeed",
    "AgentMemoryRefError",
    "AgentRuntimeEnvelopeViolation",
    "AgentSnapshot",
    "AgentSnapshotError",
    "AgentSnapshotSchemaError",
    "AgentSnapshotSerializationError",
    "AgentSnapshotValidationError",
    "CapabilityGrant",
    "MemoryRef",
    "MemoryRefNotResolvedError",
    "STABLE_CANONICAL_PROFILE",
    "UnknownSchemaVersionError",
    "validate_agent_snapshot_payload",
]
