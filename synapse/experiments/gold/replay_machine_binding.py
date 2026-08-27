"""Owner-internal seal around the one root-selected replay machine factory.

This is not a fourth replay adapter.  It contains no CognitiveVM integration
and creates no machine itself; it makes the composition root's exact adapter
choice immutable before the replay owner accepts a production binding.
"""

from __future__ import annotations


_PRODUCTION_MACHINE_FACTORY_SEAL = object()
_OPERATIONS = ("adapter_id", "build", "restore")


class ReplayMachineBindingViolation(ValueError):
    """The root-selected factory binding is absent, changed, or incomplete."""


def _validate_delegate(delegate: object, expected_adapter_id: str) -> None:
    if type(expected_adapter_id) is not str or not expected_adapter_id:
        raise ReplayMachineBindingViolation("expected adapter identity is invalid")
    missing = [name for name in _OPERATIONS if not callable(getattr(delegate, name, None))]
    if missing:
        raise ReplayMachineBindingViolation(
            f"machine factory is missing {', '.join(missing)}"
        )
    if delegate.adapter_id() != expected_adapter_id:
        raise ReplayMachineBindingViolation("machine factory identity differs")


class ProductionReplayMachineFactory:
    """Sealed forwarding port whose delegate identity cannot be replaced."""

    __slots__ = ("_delegate", "_configuration_snapshot", "_trusted_seal")

    def __init__(
        self,
        delegate: object,
        *,
        expected_adapter_id: str,
        _seal: object,
    ) -> None:
        if _seal is not _PRODUCTION_MACHINE_FACTORY_SEAL:
            raise TypeError("ProductionReplayMachineFactory is composition-created")
        _validate_delegate(delegate, expected_adapter_id)
        object.__setattr__(self, "_delegate", delegate)
        object.__setattr__(
            self, "_configuration_snapshot", (delegate, expected_adapter_id)
        )
        object.__setattr__(self, "_trusted_seal", _PRODUCTION_MACHINE_FACTORY_SEAL)

    @property
    def delegate(self) -> object:
        return self._delegate

    def __setattr__(self, name: str, value: object) -> None:
        raise ReplayMachineBindingViolation("production machine factory is immutable")

    def __delattr__(self, name: str) -> None:
        raise ReplayMachineBindingViolation("production machine factory is immutable")

    def adapter_id(self) -> str:
        return self._configuration_snapshot[1]

    def build(self, program: object, **kwargs: object) -> object:
        return self._delegate.build(program, **kwargs)

    def restore(self, snapshot_bytes: bytes, **kwargs: object) -> object:
        return self._delegate.restore(snapshot_bytes, **kwargs)


def require_production_replay_machine_factory(
    value: object,
    *,
    expected_adapter_id: str,
) -> ProductionReplayMachineFactory:
    if (
        type(value) is not ProductionReplayMachineFactory
        or getattr(value, "_trusted_seal", None) is not _PRODUCTION_MACHINE_FACTORY_SEAL
    ):
        raise ReplayMachineBindingViolation("production machine factory is not sealed")
    delegate = value.delegate
    snapshot = getattr(value, "_configuration_snapshot", None)
    if (
        type(snapshot) is not tuple
        or len(snapshot) != 2
        or snapshot[0] is not delegate
        or snapshot[1] != expected_adapter_id
    ):
        raise ReplayMachineBindingViolation("production machine factory binding changed")
    _validate_delegate(delegate, expected_adapter_id)
    return value


__all__ = [
    "ProductionReplayMachineFactory",
    "ReplayMachineBindingViolation",
    "require_production_replay_machine_factory",
]
