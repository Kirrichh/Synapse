"""Production wiring for the durable Stage 10 worker-context path."""

from __future__ import annotations

from pathlib import Path
import math

from synapse.agents.execution import AgentExecutionPort
from synapse.agents.mini_adapter import MiniAgentAdapter
from synapse.agents.registry import AgentRegistry
from synapse.agents.worker_bridge import AgentBackedWorkerTransport
from synapse.worker.mini_adapter import MiniAdapterConfig
from synapse.worker.provider_transport import WorkerAccountingPort

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


def decode_worker_configuration(value: object) -> MiniAdapterConfig:
    """Translate the historical Mini worker declaration at its adapter boundary.

    The frozen Stage 10 declaration remains historical input. Runtime execution
    now goes through AgentExecutionPort; this decoder does not select an agent.
    """
    if type(value) is not dict or set(value) - {"accounting", "input_profile"} != {"provider", "command", "model", "timeout_seconds", "max_steps", "cost_limit"}:
        raise ValueError("worker configuration must be explicit and complete")
    if value["provider"] != "mini":
        raise ValueError("the historical Stage 10 worker declaration is not Mini")
    command = value["command"]
    if type(command) is not list or not command or any(type(item) is not str or not item or "\x00" in item for item in command):
        raise ValueError("worker command must be argv tokens")
    for name in ("timeout_seconds", "max_steps"):
        if type(value[name]) is not int or value[name] <= 0:
            raise ValueError(f"worker {name} must be a positive integer")
    if type(value["model"]) is not str or not value["model"]:
        raise ValueError("worker model must be frozen")
    if type(value["cost_limit"]) is not str:
        raise ValueError("worker cost limit must be an explicit decimal string")
    cost = float(value["cost_limit"])
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("worker cost limit must be finite and non-negative")
    return MiniAdapterConfig(command=tuple(command), timeout_seconds=value["timeout_seconds"],
                             max_steps=value["max_steps"], cost_limit=cost, model=value["model"],
                             **({"input_profile": value["input_profile"]} if "input_profile" in value else {}))


class Stage10ProductionComposition:
    """Immutable identity binding for the exact Stage 10 production adapters."""

    __slots__ = (
        "_record_store",
        "_agent_adapter",
        "_agent_registry",
        "_agent_execution_port",
        "_worker_transport",
        "_worker_adapter",
        "_identity_snapshot",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "Stage10ProductionComposition":
        raise TypeError("Stage10ProductionComposition is factory-created")

    @property
    def record_store(self) -> FileStage10RecordStore:
        return self._record_store

    @property
    def agent_execution_port(self) -> AgentExecutionPort:
        return self._agent_execution_port

    @property
    def worker_transport(self) -> AgentBackedWorkerTransport:
        return self._worker_transport

    @property
    def worker_identity(self) -> tuple[str, str | None]:
        """The selected admitted agent identity exposed to the run boundary."""
        profile = self._agent_adapter.profile
        return profile.provider_name, profile.model_name

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
    accounting: WorkerAccountingPort | None = None,
) -> Stage10ProductionComposition:
    """Construct the one store and universal-agent execution graph.

    Historical Stage 10 callers still provide MiniAdapterConfig. Mini is then
    admitted as an ordinary agent profile and invoked only through the neutral
    AgentExecutionPort. No direct Gold -> Mini transport remains in composition.
    """

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
    agent_adapter = MiniAgentAdapter(config=mini_config, accounting=accounting)
    agent_registry = AgentRegistry((agent_adapter,))
    agent_execution_port = AgentExecutionPort(agent_registry)
    worker_transport = AgentBackedWorkerTransport(agent_execution_port)
    worker_adapter = Stage10WorkerContextAdapter(worker_transport)

    result = object.__new__(Stage10ProductionComposition)
    object.__setattr__(result, "_record_store", record_store)
    object.__setattr__(result, "_agent_adapter", agent_adapter)
    object.__setattr__(result, "_agent_registry", agent_registry)
    object.__setattr__(result, "_agent_execution_port", agent_execution_port)
    object.__setattr__(result, "_worker_transport", worker_transport)
    object.__setattr__(result, "_worker_adapter", worker_adapter)
    object.__setattr__(
        result,
        "_identity_snapshot",
        (
            record_store,
            agent_adapter,
            agent_registry,
            agent_execution_port,
            worker_transport,
            worker_adapter,
            record_root,
            fence,
            mini_config,
            coordinator_id,
            accounting,
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
    agent_adapter = value._agent_adapter
    registry = value._agent_registry
    execution_port = value.agent_execution_port
    transport = value.worker_transport
    adapter = value.worker_adapter
    snapshot = getattr(value, "_identity_snapshot", None)
    if (
        type(store) is not FileStage10RecordStore
        or type(agent_adapter) is not MiniAgentAdapter
        or type(registry) is not AgentRegistry
        or type(execution_port) is not AgentExecutionPort
        or type(transport) is not AgentBackedWorkerTransport
        or type(adapter) is not Stage10WorkerContextAdapter
        or type(snapshot) is not tuple
        or len(snapshot) != 11
        or snapshot[0] is not store
        or snapshot[1] is not agent_adapter
        or snapshot[2] is not registry
        or snapshot[3] is not execution_port
        or snapshot[4] is not transport
        or snapshot[5] is not adapter
    ):
        raise TypeError("Stage 10 production component identity changed")

    record_root, fence, mini_config, coordinator_id, accounting = snapshot[6:]
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
        or registry.adapters != (agent_adapter,)
        or execution_port.registry is not registry
        or transport.execution_port is not execution_port
        or adapter.transport_binding is not transport
        or agent_adapter.mini_transport.config is not mini_config
        or agent_adapter.mini_transport.accounting is not accounting
    ):
        raise TypeError("Stage 10 production configuration binding changed")
    return value


__all__ = [
    "Stage10ProductionComposition",
    "create_stage10_production_composition",
    "require_stage10_production_composition",
]
