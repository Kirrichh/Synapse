"""Alpha.3-B2: Capability Enforcement & Adapter Integration."""
import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMHostError, VMState
from synapse.runtime.vm_bridge import (
    BRIDGE_DISPATCHED,
    HOST_CAPABILITIES,
    SYMBOL_TO_CAPABILITY,
    VMBridge,
    VM_LOCAL_DETERMINISTIC,
    VM_LOCAL_SIDE_EFFECT,
)


class FakeMemoryPalace:
    def __init__(self):
        self.writes = []

    def recall(self, room, query=None):
        return [f"recall:{room}:{query}"]

    def imprint(self, room, content, metadata=None):
        self.writes.append((room, content, metadata or {}))
        return "ok"


class FakeHost:
    def __init__(self, agent_id="default_agent"):
        self.current_agent_id = agent_id
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
        return "trace-test"

    def metrics_snapshot(self):
        return {"ok": True}


def _make_vm_with_adapter(agent_id="default_agent"):
    host = FakeHost(agent_id)
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(BytecodeProgram(instructions=[]), state=VMState(gas_remaining=5000))
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm, bridge, host


def _step_call_host(vm, symbol, args):
    for arg in args:
        vm._push(arg)
    vm.program.instructions = [Instruction("CALL_HOST", symbol, len(args), None)]
    vm.state.ip = 0
    return vm.step()


# ─── Classification / registry smoke tests ───
def test_capability_tables_are_wired():
    assert "default_agent" in HOST_CAPABILITIES
    assert SYMBOL_TO_CAPABILITY["SYS_MEMORY_WRITE"] == "memory.write"
    assert "SYS_MEMORY_WRITE" in BRIDGE_DISPATCHED
    assert "len" in VM_LOCAL_DETERMINISTIC
    assert "print" in VM_LOCAL_SIDE_EFFECT


# ─── Capability Tests ───
def test_capability_success_direct_check():
    vm, bridge, _ = _make_vm_with_adapter("default_agent")
    ctx = bridge._get_host_call_context(vm)
    assert "memory.write" in ctx.capabilities
    bridge._check_capability("SYS_MEMORY_WRITE", ctx)  # no raise


def test_capability_denied_direct_check():
    vm, bridge, _ = _make_vm_with_adapter("restricted_worker")
    ctx = bridge._get_host_call_context(vm)
    assert "memory.write" not in ctx.capabilities
    with pytest.raises(VMHostError) as exc_info:
        bridge._check_capability("SYS_MEMORY_WRITE", ctx)
    assert exc_info.value.code == "CAPABILITY_DENIED"


def test_capability_denied_via_step_pushes_json_error():
    vm, _, _ = _make_vm_with_adapter("restricted_worker")
    _step_call_host(vm, "SYS_MEMORY_WRITE", ["room", "content"])
    top = vm.state.stack[-1]
    assert isinstance(top, dict)
    assert top["type"] == "VMHostError"
    assert top["code"] == "CAPABILITY_DENIED"
    assert top["symbol"] == "SYS_MEMORY_WRITE"


# ─── Routing & Safety Tests ───
def test_print_always_allowed_for_restricted_worker():
    vm, _, _ = _make_vm_with_adapter("restricted_worker")
    _step_call_host(vm, "print", ["ok", "now"])
    assert vm.state.stack[-1] is None
    assert hasattr(vm, "_output")
    assert vm._output[-1] == "ok now"


def test_unknown_symbol_via_step_pushes_json_error():
    vm, _, _ = _make_vm_with_adapter("default_agent")
    _step_call_host(vm, "SYS_NONEXISTENT", [])
    top = vm.state.stack[-1]
    assert isinstance(top, dict)
    assert top["code"] == "UNKNOWN_SYMBOL"


def test_nested_call_guard_direct_dispatch():
    vm, bridge, _ = _make_vm_with_adapter("default_agent")
    bridge._nested_host_call_depth = 1
    with pytest.raises(VMHostError) as exc_info:
        bridge.dispatch_host_call(vm, "len", [[1, 2]])
    assert exc_info.value.code == "NESTED_HOST_CALL"
    assert bridge._nested_host_call_depth == 1


def test_builtin_dispatch_via_adapter():
    vm, _, _ = _make_vm_with_adapter("default_agent")
    _step_call_host(vm, "len", [[1, 2, 3]])
    assert vm.state.stack[-1] == 3


def test_bridge_dispatched_memory_write_success():
    vm, _, host = _make_vm_with_adapter("default_agent")
    _step_call_host(vm, "SYS_MEMORY_WRITE", ["episodic", "content"])
    assert vm.state.stack[-1] == "ok"
    assert host.memory_palace.writes == [("episodic", "content", {})]


def test_bridge_dispatched_memory_read_success():
    vm, _, _ = _make_vm_with_adapter("default_agent")
    _step_call_host(vm, "SYS_MEMORY_READ", ["episodic"])
    assert vm.state.stack[-1] == ["recall:episodic:None"]


def test_agent_id_mismatch_fails_closed_by_default():
    vm, bridge, _ = _make_vm_with_adapter("restricted_worker")
    vm.state.agent_id = "default_agent"
    from synapse.cvm import VMResumeSyncError
    with pytest.raises(VMResumeSyncError, match="Trust Gate"):
        bridge._get_host_call_context(vm)


def test_agent_id_prefers_vm_state_when_trusted():
    vm, bridge, host = _make_vm_with_adapter("restricted_worker")
    vm.state.agent_id = "default_agent"
    host.trust_snapshot_agent_id = True
    ctx = bridge._get_host_call_context(vm)
    assert ctx.agent_id == "default_agent"
    bridge._check_capability("SYS_MEMORY_WRITE", ctx)  # no raise


def test_legacy_host_abi_passthrough_is_not_capability_gated():
    vm, _, _ = _make_vm_with_adapter("restricted_worker")
    result = vm.host("METRICS", None, None)
    assert result["opcode"] == "METRICS"
    assert result["status"] == "ok"
