"""Deterministic discovery and selection of admitted external-agent adapters."""

from __future__ import annotations

from importlib import metadata
from typing import Mapping, Protocol, runtime_checkable

from .contracts import AgentExecutionRequest, AgentExecutionResult, AgentProfile, AgentRuntimeContext, LocalInformationPolicy


ENTRY_POINT_GROUP = "synapse.agent_adapters"


class AgentSelectionError(ValueError):
    """No admitted adapter can satisfy the exact frozen request."""


@runtime_checkable
class AgentAdapter(Protocol):
    @property
    def profile(self) -> AgentProfile: ...

    def execute(self, request: AgentExecutionRequest, runtime: AgentRuntimeContext) -> AgentExecutionResult: ...


@runtime_checkable
class AgentAdapterFactory(Protocol):
    def create(self, configuration: Mapping[str, object]) -> AgentAdapter: ...


def _validate_adapter(adapter: object) -> AgentAdapter:
    if not isinstance(adapter, AgentAdapter):
        raise TypeError("agent adapter must implement the exact AgentAdapter port")
    profile = adapter.profile
    if type(profile) is not AgentProfile:
        raise TypeError("agent adapter must expose an exact AgentProfile")
    profile.__post_init__()
    return adapter


class AgentRegistry:
    """Immutable-by-construction registry of operator-admitted adapter instances.

    Discovery and admission remain separate: entry points merely expose adapter
    factories; only adapters explicitly supplied to this registry are selectable.
    """

    def __init__(self, adapters: tuple[AgentAdapter, ...]) -> None:
        if type(adapters) is not tuple or not adapters:
            raise TypeError("agent registry requires a non-empty exact tuple")
        checked = tuple(_validate_adapter(item) for item in adapters)
        profile_ids = tuple(item.profile.profile_id for item in checked)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("agent registry profile identities must be unique")
        self._adapters = tuple(sorted(checked, key=lambda item: item.profile.profile_id))

    @property
    def adapters(self) -> tuple[AgentAdapter, ...]:
        return self._adapters

    def select(self, request: AgentExecutionRequest) -> AgentAdapter:
        if type(request) is not AgentExecutionRequest:
            raise TypeError("agent selection requires an exact AgentExecutionRequest")
        request.__post_init__()
        required = set(request.required_capabilities)
        eligible: list[AgentAdapter] = []
        for adapter in self._adapters:
            profile = adapter.profile
            if not required.issubset(profile.capabilities):
                continue
            if request.required_output_profile not in profile.output_profiles:
                continue
            if request.information_text is not None and profile.local_information_policy is LocalInformationPolicy.NOT_SUPPORTED:
                continue
            media = {item.media_type for item in request.artifacts}
            if not media.issubset(profile.accepted_media_types):
                continue
            eligible.append(adapter)
        if not eligible:
            raise AgentSelectionError("no admitted agent profile satisfies the frozen request")
        # Stable profile ordering is the deterministic tie-break. Policy owners
        # may narrow the admitted set before constructing this registry.
        return eligible[0]


def discover_adapter_factories() -> tuple[metadata.EntryPoint, ...]:
    """Return installed adapter entry points without admitting or loading them."""

    points = metadata.entry_points()
    selected = points.select(group=ENTRY_POINT_GROUP) if hasattr(points, "select") else points.get(ENTRY_POINT_GROUP, ())
    return tuple(sorted(selected, key=lambda item: (item.name, item.value)))


def load_admitted_adapter(
    *,
    entry_point_name: str,
    configuration: Mapping[str, object],
) -> AgentAdapter:
    """Load exactly one operator-selected plugin factory and validate its adapter.

    Merely being installed never grants execution authority. The caller must name
    the entry point explicitly from frozen/operator-approved configuration.
    """

    if type(entry_point_name) is not str or not entry_point_name:
        raise TypeError("entry point name must be an exact non-empty string")
    if not isinstance(configuration, Mapping):
        raise TypeError("adapter configuration must be a mapping")
    matches = tuple(item for item in discover_adapter_factories() if item.name == entry_point_name)
    if len(matches) != 1:
        raise ValueError("exactly one installed adapter entry point must match the admitted name")
    factory = matches[0].load()
    if isinstance(factory, type):
        factory = factory()
    if not isinstance(factory, AgentAdapterFactory):
        raise TypeError("agent adapter entry point must expose an AgentAdapterFactory")
    return _validate_adapter(factory.create(dict(configuration)))


__all__ = [
    "AgentAdapter",
    "AgentAdapterFactory",
    "AgentRegistry",
    "AgentSelectionError",
    "ENTRY_POINT_GROUP",
    "discover_adapter_factories",
    "load_admitted_adapter",
]
