import json
import pytest

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM, VMState, VMCodeMigrationRequiresMapError, VMSnapshotFormatError
from synapse.runtime.vm_bridge import VMBridge, build_vm_snapshot_dict, restore_vm_from_snapshot


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def make_vm(bytecode, gas=5000):
    vm = CognitiveVM(bytecode)
    vm.state.gas_remaining = gas
    return vm


class FakeHost:
    """Minimal host for unit testing VMBridge snapshot logic."""
    def __init__(self):
        self.execution_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.current_context = None
        self.intention_cascades = []
        self.affective_states = {}
        self.history_chain_seed = "test-seed"
        self._trace_id = "fake-trace-001"

    def current_trace_id(self):
        return self._trace_id


def make_bridge(host=None):
    host = host or FakeHost()
    return VMBridge(host_getter=lambda: host), host


def run_until(vm, predicate, limit=500):
    steps = 0
    while not predicate(vm) and not vm.halted and vm.state.ip < len(vm.program.instructions) and steps < limit:
        vm.step()
        steps += 1
    return steps


# ─────────────────────────────────────────────────────────────
# Pure helper tests
# ─────────────────────────────────────────────────────────────

def test_build_vm_snapshot_dict_includes_call_stack():
    """Pure helper includes call_stack in snapshot and remains JSON-safe."""
    src = """
fn factorial(n) {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
let res = factorial(3)
"""
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: len(v.state.call_stack) >= 2)
    assert len(vm.state.call_stack) >= 2, "Should be in recursive call"

    snapshot = build_vm_snapshot_dict(vm, label="mid_recursive")

    assert "vm_state" in snapshot
    assert "program_hash" in snapshot
    assert snapshot["program_hash"] == bytecode.program_hash
    assert len(snapshot["vm_state"]["call_stack"]) >= 2
    assert snapshot["vm_state"]["call_stack"][0]["fn_name"] == "factorial"
    assert "name_save_stack" in snapshot["vm_state"]

    restored = json.loads(json.dumps(snapshot))
    assert len(restored["vm_state"]["call_stack"]) >= 2


def test_build_vm_snapshot_dict_includes_name_save_stack():
    """Pure helper includes name_save_stack in snapshot."""
    src = """
let items = [1, 2, 3]
for item in items {
    let x = item
}
"""
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: bool(v.state.name_save_stack))
    assert vm.state.name_save_stack

    snapshot = build_vm_snapshot_dict(vm, label="mid_loop")
    assert "name_save_stack" in snapshot["vm_state"]
    assert len(snapshot["vm_state"]["name_save_stack"]) >= 1


# ─────────────────────────────────────────────────────────────
# Mid-call resume tests
# ─────────────────────────────────────────────────────────────

def test_mid_call_checkpoint_resume_recursive():
    """Resume from deep inside recursion produces correct result."""
    src = """
fn factorial(n) {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
let res = factorial(5)
"""
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: len(v.state.call_stack) >= 2)
    assert len(vm.state.call_stack) >= 2

    snapshot = build_vm_snapshot_dict(vm, label="deep_recursion")
    vm2 = restore_vm_from_snapshot(snapshot, bytecode)

    assert len(vm2.state.call_stack) >= 2
    vm2.run()
    assert vm2.state.locals.get("res") == 120


def test_mid_call_resume_preserves_closure():
    """Closure variables survive checkpoint/resume."""
    src = """
let base = 100
fn add_base(x) { return base + x }
let res = add_base(42)
"""
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: bool(v.state.call_stack))
    assert vm.state.call_stack

    snapshot = build_vm_snapshot_dict(vm, label="mid_call")
    vm2 = restore_vm_from_snapshot(snapshot, bytecode)

    vm2.run()
    assert vm2.state.locals.get("res") == 142


# ─────────────────────────────────────────────────────────────
# Mid-loop resume tests
# ─────────────────────────────────────────────────────────────

def test_mid_loop_checkpoint_preserves_save_stack():
    """SAVE_NAME state survives checkpoint/resume inside for-loop."""
    src = """
let items = [1, 2, 3]
let total = 0
for item in items {
    total = total + item
}
"""
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: bool(v.state.name_save_stack))
    assert vm.state.name_save_stack

    snapshot = build_vm_snapshot_dict(vm, label="mid_loop")
    vm2 = restore_vm_from_snapshot(snapshot, bytecode)

    assert len(vm2.state.name_save_stack) >= 1
    assert vm2.state.name_save_stack[0][0] == "item"

    vm2.run()
    assert vm2.state.locals.get("total") == 6
    assert "item" not in vm2.state.locals


# ─────────────────────────────────────────────────────────────
# Conservative migration failure tests
# ─────────────────────────────────────────────────────────────

def test_program_hash_mismatch_with_callstack_fails_closed():
    """Non-empty call_stack + hash mismatch → VMCodeMigrationRequiresMapError."""
    src = "fn f() { return 1 }\nlet x = f()"
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    run_until(vm, lambda v: bool(v.state.call_stack))
    assert vm.state.call_stack

    snapshot = build_vm_snapshot_dict(vm)
    snapshot["program_hash"] = "tampered_hash_deadbeef" * 4

    with pytest.raises(VMCodeMigrationRequiresMapError):
        restore_vm_from_snapshot(snapshot, bytecode)


def test_program_hash_mismatch_with_empty_callstack_allowed():
    """Empty call_stack + hash mismatch → migration allowed (locals only)."""
    src = "let x = 42"
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)
    vm.run()

    snapshot = build_vm_snapshot_dict(vm)
    snapshot["program_hash"] = "different_hash"

    vm2 = restore_vm_from_snapshot(snapshot, bytecode)
    assert vm2.state.locals.get("x") == 42


# ─────────────────────────────────────────────────────────────
# VMBridge integration tests
# ─────────────────────────────────────────────────────────────

def test_vmbridge_with_fake_host():
    """VMBridge.make_vm_snapshot works with minimal fake host."""
    src = "let x = 42"
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)
    vm.run()

    bridge, fake_host = make_bridge()
    snapshot = bridge.make_vm_snapshot(vm, label="test")

    assert "vm_state" in snapshot
    assert "host_metadata" in snapshot
    assert snapshot["host_metadata"]["trace_id"] == "fake-trace-001"
    assert "test" in fake_host.vm_checkpoints
    assert fake_host.vm_checkpoints["test"]["vm_state"]["locals"]["x"] == 42


def test_vmbridge_restore_from_checkpoint():
    """VMBridge.restore_vm_from_checkpoint reconstructs VM correctly."""
    src = "fn f() { return 10 }\nlet x = f()"
    bytecode = CognitiveCompiler().compile(parse_source(src))
    vm = make_vm(bytecode, gas=5000)

    bridge, fake_host = make_bridge()
    run_until(vm, lambda v: bool(v.state.call_stack))
    assert vm.state.call_stack

    bridge.make_vm_snapshot(vm, label="mid_call")
    vm2 = bridge.restore_vm_from_checkpoint("mid_call")

    assert len(vm2.state.call_stack) >= 1
    vm2.run()
    assert vm2.state.locals.get("x") == 10


def test_vmbridge_restore_nonexistent_snapshot_raises():
    """Restoring nonexistent snapshot raises VMSnapshotFormatError."""
    bytecode = CognitiveCompiler().compile(parse_source("let x = 1"))
    bridge, _ = make_bridge()

    with pytest.raises(VMSnapshotFormatError, match="Unknown VM checkpoint"):
        bridge.restore_vm_from_checkpoint("nonexistent")


def test_v22_snapshot_backward_compatible():
    """Old-style state without call_stack/name_save_stack still restores."""
    old_snapshot = {
        "vm_state": {
            "ip": 0,
            "stack": [],
            "locals": {"x": 10},
            "gas_remaining": 1000,
        },
        "program_hash": None,
    }
    vm = restore_vm_from_snapshot(old_snapshot, None)
    assert vm.state.locals["x"] == 10
    assert vm.state.call_stack == []
    assert vm.state.name_save_stack == []
