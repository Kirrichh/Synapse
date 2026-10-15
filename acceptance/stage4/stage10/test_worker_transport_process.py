from __future__ import annotations

from pathlib import Path

import pytest

from acceptance.agents.coding_agents import create_process_agent
from synapse.agents.execution import AgentExecutionPort
from synapse.agents.worker_bridge import AgentBackedWorkerTransport
from synapse.experiments.gold.stage10_composition import (
    create_stage10_production_composition,
    require_stage10_production_composition,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateStatus,
    WorkerDeliveryStatus,
)
from tests.gold_store_fence import fence_for


def test_typed_context_crosses_adapter_transport_and_real_subprocess(
    stage10_delivery_world,
) -> None:
    world = stage10_delivery_world
    invocation = world.dispatch.invocation
    result = world.dispatch.worker_result

    assert (world.worker_worktree / ".git").is_dir()
    assert invocation.payload_text == world.context.delivery_envelope.prompt_text
    assert result.status is WorkerCandidateStatus.NO_PATCH
    assert result.delivery_evidence.status is WorkerDeliveryStatus.PROCESS_STARTED
    assert result.delivery_evidence.transport_name == "synapse-agent-stdio/v1"
    assert result.delivery_evidence.payload_sha256 == invocation.payload_sha256
    assert result.touched_files == ()
    assert invocation.payload_text not in repr(result.diagnostics)


def test_canonical_composition_refuses_rewired_component_bindings(tmp_path: Path) -> None:
    agent = create_process_agent(tmp_path / "agent", outcomes=("NO_PATCH",), patch_source="")

    def composition_for(case: int):
        authority_root = tmp_path / f"authority-{case}"
        authority_root.mkdir()
        return create_stage10_production_composition(
            record_root=authority_root / "records",
            mutation_fence=fence_for(authority_root),
            agent_registry=agent.registry(model="acceptance-model"),
        )

    rewired = composition_for(0)
    object.__setattr__(rewired.record_store, "_root", tmp_path / "other-records")
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(1)
    object.__setattr__(rewired.record_store, "_coordinator_id", "other-coordinator")
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(2)
    other_port = AgentExecutionPort(agent.registry(model="acceptance-model"), evidence_root=tmp_path / "other")
    object.__setattr__(rewired.worker_transport, "_execution_port", other_port)
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(3)
    object.__setattr__(rewired.worker_adapter, "_transport", AgentBackedWorkerTransport(other_port))
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)
