"""Alpha.3-C2 Commit 2: ContextBlock CVM compilation and runtime parity."""
import hashlib
import json
from pathlib import Path

import pytest

from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM, VMAssertionFailed, VMState
from synapse.habit import ContextTracker
from synapse.interpreter import Interpreter
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.runtime.vm_bridge import VMBridge, build_vm_snapshot_dict, restore_vm_from_snapshot


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


class FakeHost:
    def __init__(self):
        self.context_tracker = ContextTracker()
        self.execution_history = []
        self.telemetry_events = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.intention_cascades = []
        self.affective_states = {}
        self.current_context = None
        self.current_env = None
        self.output_buffer = []
        self.history_chain_seed = "test-seed"

    def next_event_id(self):
        return f"evt-{len(self.execution_history):08d}"

    def current_trace_id(self):
        return "trace-context-test"

    def emit_runtime_event(self, event, env=None):
        pass

    def current_mood_snapshot(self):
        return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}


def compile_vm(source: str):
    return CognitiveCompiler().compile(parse_source(source))


def make_vm(source: str, host: FakeHost | None = None):
    host = host or FakeHost()
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(compile_vm(source), state=VMState(gas_remaining=5000))
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm, bridge, host


def context_events(history):
    return [e for e in history if e.get("type") in {"context_entered", "context_exited"}]


def test_compiler_emits_context_opcodes():
    bytecode = compile_vm('context "task" { print("inside") }')
    ops = [ins.op for ins in bytecode.instructions]
    assert ops[:1] == ["CONTEXT_ENTER"]
    assert "CONTEXT_EXIT" in ops
    assert ops[-1] == "HALT"


def test_context_block_parity_with_tree_walker_events():
    source = 'context "task" { print("inside") }'

    tw = Interpreter()
    tw.interpret(parse_source(source))
    tw_events = context_events(tw.execution_history)

    vm, _bridge, host = make_vm(source)
    vm.run()
    cvm_events = context_events(host.execution_history)

    assert cvm_events == tw_events
    assert vm.state.context_stack == []
    assert host.context_tracker.stack == []
    assert host.current_context is None
    assert vm._output == ["inside"]


def test_nested_contexts_lifo_order_in_cvm():
    vm, _bridge, host = make_vm('context "outer" { context "inner" { print("nested") } }')
    vm.run()
    events = context_events(host.execution_history)
    assert [(e["type"], e["label"]) for e in events] == [
        ("context_entered", "outer"),
        ("context_entered", "inner"),
        ("context_exited", "inner"),
        ("context_exited", "outer"),
    ]
    assert vm.state.context_stack == []
    assert host.context_tracker.stack == []


def test_checkpoint_inside_nested_context_restores_context_tracker_stack():
    source = 'context "outer" { context "inner" { print("inside") } }'
    vm, bridge, host = make_vm(source)

    # Execute CONTEXT_ENTER outer and CONTEXT_ENTER inner.
    while vm.state.context_stack != ["outer", "inner"]:
        vm.step()

    snapshot = bridge.make_vm_snapshot(vm, label="mid_context", embed_history=True)
    restored = bridge.restore_vm_from_checkpoint("mid_context")

    assert restored.state.context_stack == ["outer", "inner"]
    assert host.context_tracker.stack == ["outer", "inner"]
    assert host.current_context == "inner"

    restored.run()
    assert restored.state.context_stack == []
    assert host.context_tracker.stack == []
    assert host.current_context is None


def test_return_from_context_uses_callframe_raii_unwind_event():
    source = 'fn foo() { context "inner" { return 42 } }\nlet result = foo()'
    vm, _bridge, host = make_vm(source)
    result = vm.run()

    events = context_events(host.execution_history)
    assert events == [
        {"type": "context_entered", "label": "inner", "event_id": "evt-00000000"},
        {"type": "context_exited", "label": "inner", "event_id": "evt-00000001", "unwind_reason": "function_return"},
    ]
    assert result["locals"]["result"] == 42
    assert vm.state.context_stack == []
    assert host.context_tracker.stack == []


def test_exception_unwind_closes_contexts_with_reason():
    source = 'context "outer" { context "inner" { assert 1 == 2 } }'
    vm, bridge, host = make_vm(source)

    with pytest.raises(VMAssertionFailed):
        bridge.run_cvm_with_context_safety(vm)

    events = context_events(host.execution_history)
    assert [(e["type"], e["label"]) for e in events] == [
        ("context_entered", "outer"),
        ("context_entered", "inner"),
        ("context_exited", "inner"),
        ("context_exited", "outer"),
    ]
    assert events[-1]["unwind_reason"] == "exception:VMAssertionFailed"
    assert events[-2]["unwind_reason"] == "exception:VMAssertionFailed"
    assert vm.state.context_stack == []
    assert host.context_tracker.stack == []
    assert host.current_context is None


def test_living_habits_alpha3c2_baseline_metrics():
    program_path = Path("examples/living_habits_phase_c.syn")
    source = program_path.read_text()
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    baseline = json.loads(Path("tests/baselines/living_habits_phase_c_alpha3c2.json").read_text())
    assert baseline["source_sha256"] == source_sha256

    interp = Interpreter()
    interp.source_code = source
    interp.interpret(parse_source(source))
    metrics = interp.metrics_snapshot()
    expected = baseline["expected"]

    assert metrics["vm_fallbacks_total"] == expected["vm_fallbacks_total"]
    assert metrics["vm_fallback_by_node_type"] == expected["vm_fallback_by_node_type"]
    assert metrics["vm_fallback_by_reason"] == expected["vm_fallback_by_reason"]
    assert metrics["vm_coverage_ratio"] == expected["vm_coverage_ratio"]
    assert metrics["context_entries_total"] == expected["context_entries_total"]
    assert metrics["context_exits_total"] == expected["context_exits_total"]
    assert "ContextBlock" not in metrics["vm_fallback_by_node_type"]
