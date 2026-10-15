"""Production wiring for the durable Stage 10 worker-context path."""

from __future__ import annotations

from pathlib import Path

from synapse.agents.execution import AgentExecutionPort
from synapse.agents.outputs import PATCH_CANDIDATE_OUTPUT_V1
from synapse.agents.registry import AgentAdapter, AgentRegistry
from synapse.agents.worker_bridge import AgentBackedWorkerTransport

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


def _require_stage10_agent_registry(registry: AgentRegistry) -> AgentAdapter:
    if type(registry) is not AgentRegistry or len(registry.adapters) != 1:
        raise TypeError("Stage 10 requires exactly one frozen admitted coding-agent profile")
    adapter = registry.adapters[0]
    profile = adapter.profile
    if "repository.edit" not in profile.capabilities:
        raise ValueError("Stage 10 coding agent lacks repository.edit capability")
    if PATCH_CANDIDATE_OUTPUT_V1 not in profile.output_profiles:
        raise ValueError("Stage 10 coding agent lacks the patch-candidate output profile")
    return adapter


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
    def agent_registry(self) -> AgentRegistry:
        return self._agent_registry

    @property
    def worker_transport(self) -> AgentBackedWorkerTransport:
        return self._worker_transport

    @property
    def worker_identity(self) -> tuple[str, str | None]:
        """The frozen admitted coding-agent identity exposed to the run boundary."""
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
    agent_registry: AgentRegistry,
    local_edit_profile: str | None = None,
    memory_profile: str | None = None,
) -> Stage10ProductionComposition:
    """Construct the one store and universal-agent execution graph.

    The caller provides one pre-admitted coding-agent registry; its adapter owns
    its own model-accounting boundary. There is no other agent path.
    """

    if type(record_root) is not type(Path()):
        raise TypeError("record_root must be an exact platform Path")
    fence = require_store_mutation_fence(mutation_fence)
    registry = agent_registry
    agent_adapter = _require_stage10_agent_registry(registry)
    coordinator_id = fence.coordinator_id()
    if type(coordinator_id) is not str or not coordinator_id:
        raise TypeError("mutation_fence must expose an exact coordinator identity")

    record_store = FileStage10RecordStore(record_root, mutation_fence=fence)
    agent_execution_port = AgentExecutionPort(registry, evidence_root=record_root.parent / "agent-executions")
    worker_transport = AgentBackedWorkerTransport(agent_execution_port)
    worker_adapter = Stage10WorkerContextAdapter(worker_transport, local_edit_profile=local_edit_profile,
                                                 memory_profile=memory_profile)

    result = object.__new__(Stage10ProductionComposition)
    object.__setattr__(result, "_record_store", record_store)
    object.__setattr__(result, "_agent_adapter", agent_adapter)
    object.__setattr__(result, "_agent_registry", registry)
    object.__setattr__(result, "_agent_execution_port", agent_execution_port)
    object.__setattr__(result, "_worker_transport", worker_transport)
    object.__setattr__(result, "_worker_adapter", worker_adapter)
    object.__setattr__(
        result,
        "_identity_snapshot",
        (
            record_store,
            agent_adapter,
            registry,
            agent_execution_port,
            worker_transport,
            worker_adapter,
            record_root,
            fence,
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
    agent_adapter = value._agent_adapter
    registry = value.agent_registry
    execution_port = value.agent_execution_port
    transport = value.worker_transport
    adapter = value.worker_adapter
    snapshot = getattr(value, "_identity_snapshot", None)
    if (
        type(store) is not FileStage10RecordStore
        or type(registry) is not AgentRegistry
        or type(execution_port) is not AgentExecutionPort
        or type(transport) is not AgentBackedWorkerTransport
        or type(adapter) is not Stage10WorkerContextAdapter
        or type(snapshot) is not tuple
        or len(snapshot) != 9
        or snapshot[0] is not store
        or snapshot[1] is not agent_adapter
        or snapshot[2] is not registry
        or snapshot[3] is not execution_port
        or snapshot[4] is not transport
        or snapshot[5] is not adapter
    ):
        raise TypeError("Stage 10 production component identity changed")

    record_root, fence, coordinator_id = snapshot[6:]
    require_store_mutation_fence(fence)
    if (
        type(record_root) is not type(Path())
        or type(coordinator_id) is not str
        or not coordinator_id
        or store.record_root is not record_root
        or store.mutation_fence is not fence
        or store.coordinator_id != coordinator_id
        or fence.coordinator_id() != coordinator_id
        or registry.adapters != (agent_adapter,)
        or _require_stage10_agent_registry(registry) is not agent_adapter
        or execution_port.registry is not registry
        or transport.execution_port is not execution_port
        or set(vars(transport)) != {"_execution_port"}
        or execution_port.evidence_root != record_root.parent / "agent-executions"
        or adapter.transport_binding is not transport
    ):
        raise TypeError("Stage 10 production configuration binding changed")
    return value


__all__ = [
    "Stage10ProductionComposition",
    "create_stage10_production_composition",
    "require_stage10_production_composition",
]
