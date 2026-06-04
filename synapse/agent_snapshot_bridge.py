"""AS2 Host Pre-Stage bridge skeleton (Alpha3g P0.6.12).

This module implements the approved bridge boundary between host-prepared
legacy-edge data and the standalone AS2 validation layer. It intentionally does
not import AgentRuntime, Environment, interpreter, actor runtime, storage, or
projection/runtime integration paths.

Allowed in P0.6.12:
- accept host pre-stage mappings;
- parse them into frozen DTOs;
- pass canonical ``model_selection_source`` into AS2 validation;
- validate prepared AS2 inputs through ``validate_as2_inputs(...)``;
- raise bridge-specific typed errors for host-stage failures.

Forbidden in P0.6.12:
- AgentRuntime / Environment imports;
- runtime wiring or feature-flag system integration;
- ``project_validated_as2_inputs(...)`` calls;
- AgentSnapshot construction;
- real I/O, caching, provider registries, FunctionDescriptor runtime registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from synapse.agent_snapshot_adapter import AS2AdapterError, validate_as2_inputs

AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False
AS2_BRIDGE_FIXTURE_SCHEMA = "alpha3g.as2_bridge_fixture.v1"

_ALLOWED_HOST_PRESTAGE_KEYS = frozenset(
    {
        "adapter_identity_context",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
        "model_selection_source",
        "host_stage_failure",
        "forbidden_runtime_reads",
        "inline_memory_payload",
        "notes",
    }
)


def _deep_freeze(value: Any) -> Any:
    """Return an immutable JSON-like snapshot using only stdlib types."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    """Return a mutable plain dict/list copy for standalone AS2 validation APIs."""

    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    if isinstance(value, list):
        return [_deep_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class AS2BridgeViolationContext:
    """Forensic context for Host Pre-Stage bridge failures."""

    rfc_reference: str
    violated_field: str | None = None
    fixture_case_id: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None


class AS2BridgeError(Exception):
    """Base class for all AS2 Host Pre-Stage bridge errors."""

    def __init__(self, message: str, context: AS2BridgeViolationContext | None = None) -> None:
        super().__init__(message)
        self.context = context


class AS2HostPreStageBridgeDisabledError(AS2BridgeError):
    """Bridge entrypoint was called while its local flag is disabled."""


class AS2BridgeInputError(AS2BridgeError):
    """Host Pre-Stage payload is missing, malformed, or AS2-incompatible."""


class HostPreStageUnexpectedFieldError(AS2BridgeInputError):
    """Host Pre-Stage payload contains fields outside the approved contract."""


class HostPreStageMissingIdentitySourceError(AS2BridgeInputError):
    """Host Pre-Stage payload is missing AdapterIdentityContext."""


class HostPreStageMissingDefinitionSourceError(AS2BridgeInputError):
    """Host Pre-Stage payload is missing AdapterDefinitionSource."""


class HostPreStageMissingModelRegistryError(AS2BridgeInputError):
    """Host Pre-Stage payload is missing StaticModelRegistry."""


class HostPreStageMissingCapabilityGrantsError(AS2BridgeInputError):
    """Host Pre-Stage payload is missing CapabilityGrantSource."""


class HostPreStageMissingMemoryRefSourceError(AS2BridgeInputError):
    """Host Pre-Stage payload is missing MemoryRefSource."""


class HostPreStageInvalidAS2InputsError(AS2BridgeInputError):
    """Host Pre-Stage output failed standalone AS2 validation."""


class HostPreStageIOError(AS2BridgeError):
    """Host failed during I/O-owned pre-stage work before AS2 inputs existed."""


class HostForbiddenRuntimeReadError(AS2BridgeError):
    """Host/bridge payload attempted to use a forbidden legacy runtime read."""


class HostInlineMemoryNotExternalizedError(AS2BridgeError):
    """Host supplied inline legacy memory instead of externalized memory refs."""


class HostEnvironmentEnvelopeForbiddenError(AS2BridgeError):
    """Host attempted to use Environment._json_safe() or a legacy envelope."""


class HostRuntimeEnvelopeSourceForbiddenError(AS2BridgeError):
    """Host attempted to use runtime-envelope sources as snapshot inputs."""


BRIDGE_ERROR_NAME_TO_CLASS: Mapping[str, type[AS2BridgeError]] = {
    cls.__name__: cls
    for cls in (
        AS2HostPreStageBridgeDisabledError,
        AS2BridgeInputError,
        HostPreStageUnexpectedFieldError,
        HostPreStageMissingIdentitySourceError,
        HostPreStageMissingDefinitionSourceError,
        HostPreStageMissingModelRegistryError,
        HostPreStageMissingCapabilityGrantsError,
        HostPreStageMissingMemoryRefSourceError,
        HostPreStageInvalidAS2InputsError,
        HostPreStageIOError,
        HostForbiddenRuntimeReadError,
        HostInlineMemoryNotExternalizedError,
        HostEnvironmentEnvelopeForbiddenError,
        HostRuntimeEnvelopeSourceForbiddenError,
    )
}


@dataclass(frozen=True)
class ModelSelectionSource:
    """Bridge-level canonical model selector supplied by Host policy."""

    model: str


@dataclass(frozen=True)
class HostPreStagePayload:
    """Frozen host-prestage payload parsed from a raw mapping."""

    adapter_identity_context: Mapping[str, Any]
    adapter_definition_source: Mapping[str, Any]
    static_model_registry: Mapping[str, Any]
    memory_ref_source: Mapping[str, Any]
    capability_grant_source: Mapping[str, Any]
    model_selection_source: ModelSelectionSource

    def __post_init__(self) -> None:
        for field_name in (
            "adapter_identity_context",
            "adapter_definition_source",
            "static_model_registry",
            "memory_ref_source",
            "capability_grant_source",
        ):
            object.__setattr__(self, field_name, _deep_freeze(getattr(self, field_name)))


@dataclass(frozen=True)
class PreparedAS2Inputs:
    """Bridge output: explicit AS2 inputs ready for standalone validation."""

    adapter_identity_context: Mapping[str, Any]
    adapter_definition_source: Mapping[str, Any]
    static_model_registry: Mapping[str, Any]
    memory_ref_source: Mapping[str, Any]
    capability_grant_source: Mapping[str, Any]
    model_selection_source: ModelSelectionSource

    def __post_init__(self) -> None:
        for field_name in (
            "adapter_identity_context",
            "adapter_definition_source",
            "static_model_registry",
            "memory_ref_source",
            "capability_grant_source",
        ):
            object.__setattr__(self, field_name, _deep_freeze(getattr(self, field_name)))

    def to_validate_kwargs(self) -> dict[str, Any]:
        """Return canonical kwargs for the standalone ``validate_as2_inputs`` API.

        P0.6.20 Contract completes AS2 model-selection naming. The bridge DTO
        emits only ``model_selection_source`` for the adapter validation boundary.
        """

        return {
            "adapter_identity_context": _deep_thaw(self.adapter_identity_context),
            "adapter_definition_source": _deep_thaw(self.adapter_definition_source),
            "static_model_registry": _deep_thaw(self.static_model_registry),
            "memory_ref_source": _deep_thaw(self.memory_ref_source),
            "capability_grant_source": _deep_thaw(self.capability_grant_source),
            "model_selection_source": {
                "model": self.model_selection_source.model,
            },
        }


def _bridge_context(
    rfc_reference: str,
    violated_field: str | None = None,
    *,
    expected_value: Any | None = None,
    actual_value: Any | None = None,
    fixture_case_id: str | None = None,
) -> AS2BridgeViolationContext:
    return AS2BridgeViolationContext(
        rfc_reference=rfc_reference,
        violated_field=violated_field,
        fixture_case_id=fixture_case_id,
        expected_value=None if expected_value is None else str(expected_value),
        actual_value=None if actual_value is None else str(actual_value),
    )


def _require_bridge_enabled() -> None:
    if not AS2_HOST_PRESTAGE_BRIDGE_ENABLED:
        raise AS2HostPreStageBridgeDisabledError(
            "AS2 Host Pre-Stage bridge is disabled by default",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §9", "AS2_HOST_PRESTAGE_BRIDGE_ENABLED"),
        )


def _require_mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AS2BridgeInputError(
            f"{name} must be a mapping",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5", name),
        )
    return value


def _require_field(
    payload: Mapping[str, Any],
    field_name: str,
    error_type: type[AS2BridgeError],
    rfc_reference: str,
) -> Mapping[str, Any]:
    if field_name not in payload or payload.get(field_name) is None:
        raise error_type(
            f"host_prestage_outputs.{field_name} is required",
            _bridge_context(rfc_reference, f"host_prestage_outputs.{field_name}"),
        )
    value = payload[field_name]
    if not isinstance(value, Mapping):
        raise HostPreStageInvalidAS2InputsError(
            f"host_prestage_outputs.{field_name} must be a mapping",
            _bridge_context(
                rfc_reference,
                f"host_prestage_outputs.{field_name}",
                expected_value="mapping",
                actual_value=type(value).__name__,
            ),
        )
    return value


def _parse_model_selection_source(payload: Mapping[str, Any]) -> ModelSelectionSource:
    if "model_selection_source" not in payload or payload.get("model_selection_source") is None:
        raise HostPreStageMissingModelRegistryError(
            "host_prestage_outputs.model_selection_source is required",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.3", "host_prestage_outputs.model_selection_source"),
        )
    selector = payload["model_selection_source"]
    if not isinstance(selector, Mapping):
        raise HostPreStageInvalidAS2InputsError(
            "model_selection_source must be a mapping",
            _bridge_context(
                "AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
                "host_prestage_outputs.model_selection_source",
                expected_value="mapping",
                actual_value=type(selector).__name__,
            ),
        )
    model = selector.get("model")
    if not isinstance(model, str) or model == "":
        raise HostPreStageMissingModelRegistryError(
            "model_selection_source.model must be a non-empty string",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.3", "model_selection_source.model"),
        )
    return ModelSelectionSource(model=model)


def _raise_for_host_stage_failure(failure: Any) -> None:
    if failure is None:
        return
    failure_map = _require_mapping("host_stage_failure", failure)
    failure_type = failure_map.get("failure_type")
    failure_phase = failure_map.get("failure_phase")
    if failure_type == "HostPreStageIOError":
        raise HostPreStageIOError(
            "host failed during memory externalization before AS2 inputs were available",
            _bridge_context(
                "AS2-LEGACY-BRIDGE-DESIGN.md §5.4",
                "host_prestage_outputs.memory_ref_source",
                actual_value=failure_phase,
            ),
        )
    raise AS2BridgeInputError(
        f"unsupported host_stage_failure: {failure_type!r}",
        _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5", "host_stage_failure.failure_type", actual_value=failure_type),
    )


def _raise_for_forbidden_reads(forbidden_reads: Any) -> None:
    if forbidden_reads is None:
        return
    if isinstance(forbidden_reads, (str, bytes)) or not isinstance(forbidden_reads, list):
        raise AS2BridgeInputError(
            "forbidden_runtime_reads must be a list of strings",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §7", "forbidden_runtime_reads"),
        )
    reads = [item for item in forbidden_reads if isinstance(item, str)]
    if not reads:
        return
    read_set = set(reads)
    if "AgentRuntime.memory" in read_set:
        raise HostInlineMemoryNotExternalizedError(
            "inline AgentRuntime.memory must be externalized by Host before bridge invocation",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.4", "AgentRuntime.memory"),
        )
    if "Environment._json_safe()" in read_set:
        raise HostEnvironmentEnvelopeForbiddenError(
            "Environment._json_safe() legacy envelope is forbidden as AS2 bridge input",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §7", "Environment._json_safe()"),
        )
    runtime_envelope_reads = {
        "AgentRuntime.llm",
        "AgentRuntime.env",
        "interpreter hidden state",
        "actor runtime mailbox",
        "scheduler timers",
        "sockets / process handles",
    }
    if read_set & runtime_envelope_reads:
        raise HostRuntimeEnvelopeSourceForbiddenError(
            "runtime-envelope sources are forbidden as AS2 bridge inputs",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §7", "runtime envelope sources"),
        )
    if "AgentRuntime.to_dict()" in read_set:
        raise HostForbiddenRuntimeReadError(
            "AgentRuntime.to_dict() is forbidden as canonical AS2 bridge input",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.8", "AgentRuntime.to_dict()"),
        )
    if "AgentRuntime.name" in read_set:
        raise HostForbiddenRuntimeReadError(
            "AgentRuntime.name is forbidden as identity source",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.1", "AgentRuntime.name"),
        )
    if "AgentRuntime.model" in read_set:
        raise HostForbiddenRuntimeReadError(
            "AgentRuntime.model is forbidden as model_ref source",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.3", "AgentRuntime.model"),
        )
    if "AgentRuntime.tools" in read_set or "AgentRuntime.tools.keys()" in read_set:
        raise HostForbiddenRuntimeReadError(
            "live AgentRuntime.tools introspection is forbidden",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.6", "AgentRuntime.tools"),
        )
    raise HostForbiddenRuntimeReadError(
        f"forbidden runtime read attempted: {reads[0]!r}",
        _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §7", reads[0]),
    )


def _raise_for_inline_memory(payload: Mapping[str, Any]) -> None:
    if payload.get("inline_memory_payload") is not None:
        raise HostInlineMemoryNotExternalizedError(
            "inline memory payload is forbidden in Host Pre-Stage bridge input",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.4", "inline_memory_payload"),
        )


def _reject_unknown_payload_fields(payload: Mapping[str, Any]) -> None:
    unknown = sorted(set(payload.keys()) - _ALLOWED_HOST_PRESTAGE_KEYS)
    if unknown:
        raise HostPreStageUnexpectedFieldError(
            f"unknown Host Pre-Stage payload fields: {unknown}",
            _bridge_context(
                "AS2-LEGACY-BRIDGE-DESIGN.md §5",
                "host_prestage_outputs",
                expected_value="approved Host Pre-Stage payload keys",
                actual_value=", ".join(unknown),
            ),
        )


def _reject_unknown_mapping_fields(
    value: Any,
    *,
    path: str,
    allowed: set[str],
    rfc_reference: str = "AS2-LEGACY-BRIDGE-DESIGN.md §5",
) -> None:
    if not isinstance(value, Mapping):
        return
    unknown = sorted(set(value.keys()) - allowed)
    if unknown:
        raise HostPreStageUnexpectedFieldError(
            f"unknown fields at {path}: {unknown}",
            _bridge_context(
                rfc_reference,
                path,
                expected_value="approved fields",
                actual_value=", ".join(unknown),
            ),
        )


def _reject_unknown_nested_fields(payload: Mapping[str, Any]) -> None:
    _reject_unknown_mapping_fields(
        payload.get("adapter_identity_context"),
        path="adapter_identity_context",
        allowed={"schema_version", "profile", "identity_seed", "audit_context"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.1",
    )
    identity_seed = payload.get("adapter_identity_context", {}).get("identity_seed") if isinstance(payload.get("adapter_identity_context"), Mapping) else None
    _reject_unknown_mapping_fields(
        identity_seed,
        path="adapter_identity_context.identity_seed",
        allowed={"parent_anchor", "definition_hash", "spawn_nonce", "namespace", "alias"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.1",
    )
    audit_context = payload.get("adapter_identity_context", {}).get("audit_context") if isinstance(payload.get("adapter_identity_context"), Mapping) else None
    _reject_unknown_mapping_fields(
        audit_context,
        path="adapter_identity_context.audit_context",
        allowed={"soulprint", "identity_version"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.1",
    )
    _reject_unknown_mapping_fields(
        payload.get("adapter_definition_source"),
        path="adapter_definition_source",
        allowed={"schema_version", "profile", "type", "definition_ref", "config", "canonical_fields"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.2",
    )
    definition_ref = payload.get("adapter_definition_source", {}).get("definition_ref") if isinstance(payload.get("adapter_definition_source"), Mapping) else None
    _reject_unknown_mapping_fields(
        definition_ref,
        path="adapter_definition_source.definition_ref",
        allowed={
            "schema_version",
            "profile",
            "type",
            "namespace",
            "class_name",
            "declared_version",
            "manifest_hash",
            "interface_schema_hash",
            "config_schema_hash",
            "capability_schema_hash",
        },
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.2",
    )
    _reject_unknown_mapping_fields(
        payload.get("static_model_registry"),
        path="static_model_registry",
        allowed={"schema_version", "profile", "type", "registry_snapshot_hash", "entries"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
    )
    entries = payload.get("static_model_registry", {}).get("entries") if isinstance(payload.get("static_model_registry"), Mapping) else None
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            _reject_unknown_mapping_fields(
                entry,
                path=f"static_model_registry.entries[{index}]",
                allowed={"legacy_model", "model_ref"},
                rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
            )
            model_ref = entry.get("model_ref") if isinstance(entry, Mapping) else None
            _reject_unknown_mapping_fields(
                model_ref,
                path=f"static_model_registry.entries[{index}].model_ref",
                allowed={
                    "schema_version",
                    "profile",
                    "type",
                    "provider_namespace",
                    "model_id",
                    "model_version",
                    "capability_profile_hash",
                },
                rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
            )
    _reject_unknown_mapping_fields(
        payload.get("memory_ref_source"),
        path="memory_ref_source",
        allowed={
            "schema_version",
            "profile",
            "type",
            "expected_memory_space_id",
            "memory_space_policy_version",
            "refs",
        },
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.5",
    )
    refs = payload.get("memory_ref_source", {}).get("refs") if isinstance(payload.get("memory_ref_source"), Mapping) else None
    if isinstance(refs, list):
        for index, ref in enumerate(refs):
            _reject_unknown_mapping_fields(
                ref,
                path=f"memory_ref_source.refs[{index}]",
                allowed={"schema_version", "profile", "type", "memory_space_id", "memory_key", "access_mode"},
                rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.5",
            )
    _reject_unknown_mapping_fields(
        payload.get("capability_grant_source"),
        path="capability_grant_source",
        allowed={"schema_version", "profile", "type", "grants"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.6",
    )
    grants = payload.get("capability_grant_source", {}).get("grants") if isinstance(payload.get("capability_grant_source"), Mapping) else None
    if isinstance(grants, list):
        for index, grant in enumerate(grants):
            _reject_unknown_mapping_fields(
                grant,
                path=f"capability_grant_source.grants[{index}]",
                allowed={
                    "schema_version",
                    "profile",
                    "type",
                    "tool_namespace",
                    "function_descriptor_ref",
                    "effect_policy_hash",
                    "policy_ref",
                },
                rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.6",
            )
            fd_ref = grant.get("function_descriptor_ref") if isinstance(grant, Mapping) else None
            _reject_unknown_mapping_fields(
                fd_ref,
                path=f"capability_grant_source.grants[{index}].function_descriptor_ref",
                allowed={"namespace", "symbol", "input_schema_hash", "output_schema_hash"},
                rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.6",
            )
    selector = payload.get("model_selection_source")
    _reject_unknown_mapping_fields(
        selector,
        path="model_selection_source",
        allowed={"model"},
        rfc_reference="AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
    )


def _parse_host_prestage_payload(payload: Mapping[str, Any]) -> HostPreStagePayload:
    _reject_unknown_payload_fields(payload)
    _reject_unknown_nested_fields(payload)
    _raise_for_host_stage_failure(payload.get("host_stage_failure"))
    _raise_for_forbidden_reads(payload.get("forbidden_runtime_reads"))
    _raise_for_inline_memory(payload)
    return HostPreStagePayload(
        adapter_identity_context=_require_field(
            payload,
            "adapter_identity_context",
            HostPreStageMissingIdentitySourceError,
            "AS2-LEGACY-BRIDGE-DESIGN.md §5.1",
        ),
        adapter_definition_source=_require_field(
            payload,
            "adapter_definition_source",
            HostPreStageMissingDefinitionSourceError,
            "AS2-LEGACY-BRIDGE-DESIGN.md §5.2",
        ),
        static_model_registry=_require_field(
            payload,
            "static_model_registry",
            HostPreStageMissingModelRegistryError,
            "AS2-LEGACY-BRIDGE-DESIGN.md §5.3",
        ),
        memory_ref_source=_require_field(
            payload,
            "memory_ref_source",
            HostPreStageMissingMemoryRefSourceError,
            "AS2-LEGACY-BRIDGE-DESIGN.md §5.5",
        ),
        capability_grant_source=_require_field(
            payload,
            "capability_grant_source",
            HostPreStageMissingCapabilityGrantsError,
            "AS2-LEGACY-BRIDGE-DESIGN.md §5.6",
        ),
        model_selection_source=_parse_model_selection_source(payload),
    )


def _prepared_from_payload(payload: HostPreStagePayload) -> PreparedAS2Inputs:
    return PreparedAS2Inputs(
        adapter_identity_context=payload.adapter_identity_context,
        adapter_definition_source=payload.adapter_definition_source,
        static_model_registry=payload.static_model_registry,
        memory_ref_source=payload.memory_ref_source,
        capability_grant_source=payload.capability_grant_source,
        model_selection_source=payload.model_selection_source,
    )


def prepare_as2_inputs_from_host_prestage(host_prestage_payload: Mapping[str, Any]) -> PreparedAS2Inputs:
    """Prepare explicit AS2 inputs from a Host Pre-Stage payload.

    The function is disabled by default through a local module flag, accepts no
    live runtime objects, performs no projection, performs no I/O, and validates
    the prepared inputs against the standalone AS2 validation boundary before
    returning a frozen DTO.
    """

    _require_bridge_enabled()
    payload = _require_mapping("host_prestage_payload", host_prestage_payload)
    parsed = _parse_host_prestage_payload(payload)
    prepared = _prepared_from_payload(parsed)
    try:
        validate_as2_inputs(**prepared.to_validate_kwargs())
    except AS2AdapterError as exc:
        raise HostPreStageInvalidAS2InputsError(
            "Host Pre-Stage produced AS2 inputs rejected by standalone validation",
            _bridge_context("AS2-LEGACY-BRIDGE-DESIGN.md §5.9", "prepared_as2_inputs"),
        ) from exc
    return prepared
