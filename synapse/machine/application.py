"""Compose the machine's access owner with platform and invocation adapters."""
from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.windows_invocations import WindowsInvocationResources
from synapse.access.windows_registry import WindowsAccessRegistry

from .domain_session import DomainSession


def domain_session() -> DomainSession:
    registry = WindowsAccessRegistry()
    resources = WindowsInvocationResources()
    return DomainSession(WorkspaceAccessAuthority(registry, resources))
