"""Access-owner ports: protected registry transactions and resource custody.

Adapters establish OS protection; only the authority decides delegated rights.
The registry lock spans owner acceptance or one bounded protected delivery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ContextManager, Protocol
from uuid import uuid4

from .records import Application, Grant, ResourceIdentity


@dataclass
class Registry:
    applications: dict[str, Application] = field(default_factory=dict)
    grants: dict[str, Grant] = field(default_factory=dict)
    installation_id: str = field(default_factory=lambda: str(uuid4()))


class RegistryTransaction(Protocol):
    registry: Registry
    administrator: str | None

    def commit(self) -> None: ...


class AccessRegistry(Protocol):
    def transaction(self, *, administrative: bool) -> ContextManager[RegistryTransaction]:
        """Recheck OS protection; require admin for writes; lock through commit.

        Read transactions never initialize missing state. An administrative
        transaction exposes the OS-verified administrator, not a caller label.
        """
        ...


class ResourceCustody(Protocol):
    def hold(self, resource: ResourceIdentity) -> ContextManager[None]:
        """Open and verify the actual resource; prevent replacement until exit."""
        ...
