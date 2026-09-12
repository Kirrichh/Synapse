"""Deterministic discovery and selection of admitted external-agent adapters."""

from __future__ import annotations

from importlib import metadata
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from .contracts import AgentExecutionRequest, AgentExecutionResult, AgentProfile, AgentRuntimeContext
from .codec import digest
from .policy import IsolationKind, ResourceBudget
from .contracts import LocalInformationPolicy


ENTRY_POINT_GROUP = "synapse.agent_adapters"


class AgentSelectionError(ValueError):
    """No admitted adapter can satisfy the exact frozen request."""

    def __init__(self, detail):
        self.code = "NO_ELIGIBLE_AGENT"
        super().__init__(self.code + ": " + detail)


@dataclass(frozen=True)
class CapabilityAdmission:
    profile_sha256: str
    capabilities: tuple[str, ...]
    evidence_sha256: str

    def __post_init__(self):
        import re
        if any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
               for v in (self.profile_sha256, self.evidence_sha256)):
            raise ValueError("capability admission requires exact profile and evidence digests")
        if (type(self.capabilities) is not tuple or not self.capabilities
                or any(type(c) is not str or not c for c in self.capabilities)
                or self.capabilities != tuple(sorted(set(self.capabilities)))):
            raise ValueError("admitted capabilities must be sorted, unique and nonempty")


@dataclass(frozen=True)
class AgentRequirements:
    required_capabilities: tuple[str, ...]
    required_output_profile: str
    required_effect_classes: tuple[str, ...]
    allowed_effects: tuple[str, ...]
    media_types: tuple[str, ...]
    information_policy: LocalInformationPolicy
    allowed_networks: tuple[str, ...]
    resource_budget: ResourceBudget | None = None
    selected_profile_id: str | None = None

    def __post_init__(self):
        from .contracts import _identifier, _sorted_strings
        _sorted_strings(self.required_capabilities, "required capabilities", nonempty=True)
        _identifier(self.required_output_profile, "required output profile")
        for name in ("required_effect_classes", "allowed_effects", "media_types", "allowed_networks"):
            _sorted_strings(getattr(self, name), name)
        if not set(self.required_effect_classes).issubset(self.allowed_effects):
            raise ValueError("required effects exceed permitted effects")
        if not self.allowed_networks or not set(self.allowed_networks).issubset({"NONE", "LOCAL_BROKER", "REMOTE_ENDPOINT"}):
            raise ValueError("agent requirements need an exact egress policy")
        if type(self.information_policy) is not LocalInformationPolicy:
            raise TypeError("agent requirements need an exact information policy")
        if self.resource_budget is not None:
            if type(self.resource_budget) is not ResourceBudget:
                raise TypeError("agent requirements need an exact resource budget")
            self.resource_budget.__post_init__()
        if self.selected_profile_id is not None:
            _identifier(self.selected_profile_id, "selected profile")


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

    def __init__(self, adapters: tuple[AgentAdapter, ...], *,
                 admissions: tuple[CapabilityAdmission, ...], preferred_profiles: tuple[str, ...] = (),
                 retained_evidence: tuple[bytes, ...] = ()) -> None:
        if type(adapters) is not tuple or not adapters:
            raise TypeError("agent registry requires a non-empty exact tuple")
        checked = tuple(_validate_adapter(item) for item in adapters)
        profile_ids = tuple(item.profile.profile_id for item in checked)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("agent registry profile identities must be unique")
        self._adapters = tuple(sorted(checked, key=lambda item: item.profile.profile_id))
        if type(admissions) is not tuple or len(admissions) != len(checked):
            raise ValueError("every selectable profile requires independent capability admission")
        self._admissions = {}
        for admission in admissions:
            if type(admission) is not CapabilityAdmission:
                raise TypeError("agent capability admission must be exact")
            admission.__post_init__()
            if admission.profile_sha256 in self._admissions:
                raise ValueError("duplicate capability admission")
            self._admissions[admission.profile_sha256] = admission
        for adapter in checked:
            admission = self._admissions.get(digest(adapter.profile))
            if admission is None or not set(admission.capabilities).issubset(adapter.profile.capabilities):
                raise ValueError("admitted capabilities differ from the exact agent profile")
        if type(preferred_profiles) is not tuple or len(set(preferred_profiles)) != len(preferred_profiles):
            raise ValueError("agent preferences must be an exact ordered unique tuple")
        if not set(preferred_profiles).issubset(profile_ids):
            raise ValueError("preferred profile is not admitted")
        self._preferred_profiles = preferred_profiles
        if type(retained_evidence) is not tuple or any(type(raw) is not bytes for raw in retained_evidence):
            raise TypeError("admission evidence must be retained exact bytes")
        self._retained_evidence = retained_evidence

    @property
    def adapters(self) -> tuple[AgentAdapter, ...]:
        return self._adapters

    @property
    def retained_evidence(self):
        return self._retained_evidence

    def admission(self, adapter: AgentAdapter) -> CapabilityAdmission:
        admission = self._admissions.get(digest(adapter.profile))
        if admission is None:
            raise ValueError("agent profile changed after admission")
        return admission

    def select(self, request: AgentExecutionRequest) -> AgentAdapter:
        if type(request) is not AgentExecutionRequest:
            raise TypeError("agent selection requires an exact AgentExecutionRequest")
        request.__post_init__()
        return self.resolve(AgentRequirements(request.required_capabilities, request.required_output_profile,
            request.required_effect_classes, request.allowed_effects or request.required_effect_classes,
            tuple(sorted({a.media_type for a in request.artifacts})), request.information_policy,
            request.allowed_networks, request.resource_budget, request.selected_profile_id))

    def resolve(self, requirements: AgentRequirements) -> AgentAdapter:
        if type(requirements) is not AgentRequirements:
            raise TypeError("agent resolution requires exact task requirements")
        requirements.__post_init__()
        request = requirements
        required = set(request.required_capabilities)
        required_effects = set(request.required_effect_classes)
        media = set(request.media_types)
        eligible: list[AgentAdapter] = []
        for adapter in self._adapters:
            profile = adapter.profile
            admission = self.admission(adapter)
            if profile.runtime_policy.network not in request.allowed_networks:
                continue
            if request.selected_profile_id is not None and profile.profile_id != request.selected_profile_id:
                continue
            if not required.issubset(admission.capabilities):
                continue
            if not required_effects.issubset(profile.effect_classes):
                continue
            if request.required_output_profile not in profile.output_profiles:
                continue
            if request.information_policy is not LocalInformationPolicy.NOT_SUPPORTED and profile.local_information_policy is not request.information_policy:
                continue
            if not media.issubset(profile.accepted_media_types):
                continue
            if request.allowed_effects is not None and not set(profile.effect_classes).issubset(request.allowed_effects):
                continue
            if request.resource_budget is not None and not request.resource_budget.fits(profile.resource_limits):
                continue
            if request.information_policy is not LocalInformationPolicy.NOT_SUPPORTED and profile.runtime_policy.isolation is IsolationKind.REMOTE:
                continue
            eligible.append(adapter)
        if not eligible:
            raise AgentSelectionError("no admitted agent profile satisfies the frozen request")
        # Stable profile ordering is the deterministic tie-break. Policy owners
        # may narrow the admitted set before constructing this registry.
        preferences = {name: index for index, name in enumerate(self._preferred_profiles)}
        return min(eligible, key=lambda item: (preferences.get(item.profile.profile_id, len(preferences)),
                                             item.profile.profile_id))


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
    "CapabilityAdmission",
    "ENTRY_POINT_GROUP",
    "discover_adapter_factories",
    "load_admitted_adapter",
]
