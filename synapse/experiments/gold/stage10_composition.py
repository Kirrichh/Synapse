"""Production wiring for the durable Stage 10 worker-context path."""

from __future__ import annotations

from pathlib import Path

from synapse.worker.mini_adapter import MiniAdapterConfig, MiniWorkerTransport

from .persistence import StoreMutationFencePort, require_store_mutation_fence
from .stage10.record_store import FileStage10RecordStore
from .stage10.worker_context_adapter import Stage10WorkerContextAdapter
from .stage10.approval import grant_approval, revoke_approval


def execute_approval_action(*, store_root: Path, request_path: Path | None = None,
                            grant_sha256: str | None = None,
                            duration_seconds: int = 3600) -> dict[str, object]:
    """Canonical CLI boundary for operator grants; workers never invoke it."""
    root = store_root.expanduser().absolute()
    if (request_path is None) == (grant_sha256 is None):
        raise ValueError("select exactly one approval request or revocation")
    if grant_sha256 is not None:
        revoke_approval(store_root=root, grant_sha256=grant_sha256)
        return {"status": "REVOKED", "grant_sha256": grant_sha256}
    request = request_path.expanduser().absolute()
    if request.parent != root / "requests":
        raise ValueError("approve a pending request from the configured operator store")
    grant = grant_approval(request_path=request, store_root=root, duration_seconds=duration_seconds)
    return {"status": "APPROVED", "grant_ref": grant.to_dict(), "duration_seconds": duration_seconds}


_STAGE10_COMPOSITION_SEAL = object()


class Stage10ProductionComposition:
    """Immutable identity binding for the exact Stage 10 production adapters."""

    __slots__ = (
        "_record_store",
        "_worker_transport",
        "_worker_adapter",
        "_identity_snapshot",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> Stage10ProductionComposition:
        raise TypeError("Stage10ProductionComposition is factory-created")

    @property
    def record_store(self) -> FileStage10RecordStore:
        return self._record_store

    @property
    def worker_transport(self) -> MiniWorkerTransport:
        return self._worker_transport

    @property
    def worker_adapter(self) -> Stage10WorkerContextAdapter:
        return self._worker_adapter

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("Stage10ProductionComposition is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("Stage10ProductionComposition is immutable")


def create_stage10_production_composition(
    *,
    record_root: Path,
    mutation_fence: StoreMutationFencePort,
    mini_config: MiniAdapterConfig,
) -> Stage10ProductionComposition:
    """Construct the one concrete store, transport, and translation adapter."""

    if type(record_root) is not type(Path()):
        raise TypeError("record_root must be an exact platform Path")
    fence = require_store_mutation_fence(mutation_fence)
    if type(mini_config) is not MiniAdapterConfig:
        raise TypeError("mini_config must be an exact MiniAdapterConfig")
    coordinator_id = fence.coordinator_id()
    if type(coordinator_id) is not str or not coordinator_id:
        raise TypeError("mutation_fence must expose an exact coordinator identity")

    record_store = FileStage10RecordStore(
        record_root,
        mutation_fence=fence,
    )
    worker_transport = MiniWorkerTransport(config=mini_config)
    worker_adapter = Stage10WorkerContextAdapter(worker_transport)

    result = object.__new__(Stage10ProductionComposition)
    object.__setattr__(result, "_record_store", record_store)
    object.__setattr__(result, "_worker_transport", worker_transport)
    object.__setattr__(result, "_worker_adapter", worker_adapter)
    object.__setattr__(
        result,
        "_identity_snapshot",
        (
            record_store,
            worker_transport,
            worker_adapter,
            record_root,
            fence,
            mini_config,
            coordinator_id,
        ),
    )
    object.__setattr__(result, "_trusted_seal", _STAGE10_COMPOSITION_SEAL)
    return require_stage10_production_composition(result)


def require_stage10_production_composition(
    value: object,
) -> Stage10ProductionComposition:
    """Refuse a forged composition or any changed concrete binding."""

    if (
        type(value) is not Stage10ProductionComposition
        or getattr(value, "_trusted_seal", None) is not _STAGE10_COMPOSITION_SEAL
    ):
        raise TypeError("Stage 10 production composition is not factory sealed")

    store = value.record_store
    transport = value.worker_transport
    adapter = value.worker_adapter
    snapshot = getattr(value, "_identity_snapshot", None)
    if (
        type(store) is not FileStage10RecordStore
        or type(transport) is not MiniWorkerTransport
        or type(adapter) is not Stage10WorkerContextAdapter
        or type(snapshot) is not tuple
        or len(snapshot) != 7
        or snapshot[0] is not store
        or snapshot[1] is not transport
        or snapshot[2] is not adapter
    ):
        raise TypeError("Stage 10 production component identity changed")

    record_root, fence, mini_config, coordinator_id = snapshot[3:]
    require_store_mutation_fence(fence)
    if (
        type(record_root) is not type(Path())
        or type(mini_config) is not MiniAdapterConfig
        or type(coordinator_id) is not str
        or not coordinator_id
        or store.record_root is not record_root
        or store.mutation_fence is not fence
        or store.coordinator_id != coordinator_id
        or fence.coordinator_id() != coordinator_id
        or transport.config is not mini_config
        or adapter.transport_binding is not transport
    ):
        raise TypeError("Stage 10 production configuration binding changed")
    return value


__all__ = [
    "Stage10ProductionComposition",
    "create_stage10_production_composition",
    "require_stage10_production_composition",
]
