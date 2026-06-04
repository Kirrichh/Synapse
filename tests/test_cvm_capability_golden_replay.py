"""Alpha.3-C1 golden replay tests for capability determinism."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMResumeSyncError, VMState
from synapse.runtime.vm_bridge import HOST_ABI_VERSION, VMBridge

GOLDEN_DIR = Path(__file__).parent / "golden_replays_capability"


class FakeMemoryPalace:
    def __init__(self):
        self.writes = []

    def recall(self, room, query=None):
        return []

    def imprint(self, room, content, metadata=None):
        self.writes.append((room, content, metadata or {}))
        return "ok"


class FakeHost:
    def __init__(self, agent_id="default_agent"):
        self.current_agent_id = agent_id
        self.trust_snapshot_agent_id = False
        self.memory_palace = FakeMemoryPalace()
        self.output_buffer = []
        self.execution_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.current_context = "test"
        self.history_chain_seed = "seed"
        self.intention_cascades = []
        self.affective_states = {}

    def current_trace_id(self):
        return "trace-golden"

    def metrics_snapshot(self):
        return {"ok": True, "agent_id": self.current_agent_id}


def load_scenario(name: str) -> dict:
    with (GOLDEN_DIR / name).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("host_abi_version") != HOST_ABI_VERSION:
        raise VMResumeSyncError(
            f"Golden replay ABI mismatch: expected {data.get('host_abi_version')}, current {HOST_ABI_VERSION}"
        )
    return data


def make_vm(host: FakeHost, bridge: VMBridge, state: VMState | None = None) -> CognitiveVM:
    program = BytecodeProgram(instructions=[], constants=[], host_abi_version="2.2")
    vm = CognitiveVM(program, state=state or VMState(gas_remaining=5000))
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm


def step_call_host(vm: CognitiveVM, symbol: str, args: list):
    for arg in args:
        vm._push(arg)
    vm.program.instructions = [Instruction("CALL_HOST", symbol, len(args), None)]
    vm.state.ip = 0
    vm.step()
    return vm.state.stack[-1] if vm.state.stack else None


def test_capability_denied_continues_golden():
    scenario = load_scenario("capability_denied_continues.json")
    host = FakeHost(agent_id=scenario["agent_id"])
    bridge = VMBridge(host_getter=lambda: host)
    vm = make_vm(host, bridge)

    action = scenario["actions"][0]
    payload = step_call_host(vm, action["symbol"], action["args"])

    assert set(payload.keys()) == {"type", "code", "message", "symbol"}
    assert "traceback" not in json.dumps(payload).lower()
    assert payload["type"] == scenario["expected_error"]["type"]
    assert payload["code"] == scenario["expected_error"]["code"]
    assert payload["symbol"] == scenario["expected_error"]["symbol"]

    vm.state.locals["err"] = payload
    restored = VMState.from_dict(vm.state.to_dict())
    assert restored.locals["err"] == payload


def test_agent_id_override_replay_golden():
    scenario = load_scenario("agent_id_override_replay.json")
    host = FakeHost(agent_id=scenario["host_agent_id"])
    bridge = VMBridge(host_getter=lambda: host)
    vm = make_vm(host, bridge)
    vm.state.agent_id = scenario["snapshot_agent_id"]

    with pytest.raises(VMResumeSyncError, match="Trust Gate"):
        bridge._get_host_call_context(vm)

    host.trust_snapshot_agent_id = True
    result = step_call_host(vm, scenario["symbol"], scenario["args"])
    assert result == scenario["expected_result"]
    assert vm.state.agent_id == scenario["snapshot_agent_id"]

    restored_state = VMState.from_dict(vm.state.to_dict())
    assert restored_state.agent_id == scenario["snapshot_agent_id"]


def test_legacy_abi_bypass_stable_golden():
    scenario = load_scenario("legacy_abi_bypass_stable.json")
    host = FakeHost(agent_id=scenario["agent_id"])
    bridge = VMBridge(host_getter=lambda: host)
    vm = make_vm(host, bridge)

    result = vm.host(scenario["legacy_opcode"], None, None)
    assert result["opcode"] == scenario["legacy_opcode"]
    assert result["status"] == scenario["expected_status"]
