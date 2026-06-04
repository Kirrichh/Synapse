"""Alpha.3-D4: PolicyDef/PolicyRule structural wrapper implementation."""
from __future__ import annotations

from synapse.ast import ContextBlock, FnDef, Literal, PolicyDef, PolicyRule, ReturnStmt
from synapse.bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from synapse.cvm import CallFrame, CognitiveVM, FunctionObject, VMState
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.runtime.actor_runtime import ActorRuntime
from synapse.runtime.vm_bridge import VMBridge
from synapse.runtime.vm_routing import classify_ast_node_v22


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


class FakeContextTracker:
    def __init__(self):
        self.stack = []

    @property
    def current(self):
        return self.stack[-1] if self.stack else None

    def enter_event(self, label):
        self.stack.append(label)
        return {"type": "context_entered", "label": label, "depth": len(self.stack)}

    def exit_event(self, label):
        if self.stack and self.stack[-1] == label:
            self.stack.pop()
        elif label in self.stack:
            self.stack.remove(label)
        return {"type": "context_exited", "label": label, "depth": len(self.stack)}


class FakeHost:
    def __init__(self):
        self.execution_history = []
        self.side_effect_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.intention_cascades = []
        self.current_context = None
        self.current_actor = None
        self.current_policy = None
        self.current_policy_rule = None
        self.current_env = None
        self.current_agent_id = "default_agent"
        self.telemetry_events = []
        self.actor_stack = []
        self.policy_stack = []
        self.context_tracker = FakeContextTracker()
        self.actor_runtime = ActorRuntime(lambda: self, live_mode=None, replay_mode=None)

    def next_event_id(self):
        return f"evt-{len(self.execution_history):08d}"

    def current_trace_id(self):
        return "trace-d4"

    def emit_runtime_event(self, event, env=None):
        return None

    def metrics_snapshot(self):
        return {}


def make_vm(instructions, constants=None):
    program = BytecodeProgram(instructions=instructions, constants=constants or [])
    host = FakeHost()
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(program)
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm, host, bridge


def test_policy_def_compiler_emits_policy_enter_exit_and_rule_wrappers():
    policy = PolicyDef(
        name="TargetIsolation",
        rules=[PolicyRule(kind="require", value=Literal(value=True))],
    )
    bytecode = CognitiveCompiler().compile(policy)
    ops = [ins.op for ins in bytecode.instructions]
    assert "POLICY_ENTER" in ops
    assert "POLICY_RULE_ENTER" in ops
    assert "POLICY_RULE_EXIT" in ops
    assert "POLICY_EXIT" in ops
    enter = next(ins for ins in bytecode.instructions if ins.op == "POLICY_ENTER")
    assert enter.a == "TargetIsolation"
    assert enter.b["kind"] == "policy"
    rule_enter = next(ins for ins in bytecode.instructions if ins.op == "POLICY_RULE_ENTER")
    assert rule_enter.a.startswith("TargetIsolation:rule:0:require")
    assert rule_enter.b["rule_kind"] == "require"


def test_policy_structural_runtime_parity_events():
    vm, host, _bridge = make_vm([
        Instruction("POLICY_ENTER", "TargetIsolation", {"kind": "policy"}),
        Instruction("POLICY_RULE_ENTER", "TargetIsolation:rule:0:require", {"kind": "policy_rule"}),
        Instruction("POLICY_RULE_EXIT", "TargetIsolation:rule:0:require"),
        Instruction("POLICY_EXIT", "TargetIsolation"),
        Instruction("HALT"),
    ])
    vm.run()
    events = [e for e in host.execution_history if e.get("type", "").startswith("policy")]
    assert [e["type"] for e in events] == [
        "policy_entered",
        "policy_rule_entered",
        "policy_rule_exited",
        "policy_exited",
    ]
    assert [e["event_id"] for e in events] == ["evt-00000000", "evt-00000001", "evt-00000002", "evt-00000003"]
    assert host.current_policy is None
    assert host.current_policy_rule is None
    assert host.policy_stack == []
    assert vm.state.policy_stack == []


def test_nested_policy_rule_lifo_order():
    vm, host, _bridge = make_vm([
        Instruction("POLICY_ENTER", "P", {}),
        Instruction("POLICY_RULE_ENTER", "P:rule:0:require", {}),
        Instruction("POLICY_RULE_EXIT", "P:rule:0:require"),
        Instruction("POLICY_EXIT", "P"),
        Instruction("HALT"),
    ])
    vm.run()
    policy_events = [e for e in host.execution_history if e.get("type", "").startswith("policy")]
    assert [(e["type"], e.get("policy") or e.get("rule")) for e in policy_events] == [
        ("policy_entered", "P"),
        ("policy_rule_entered", "P:rule:0:require"),
        ("policy_rule_exited", "P:rule:0:require"),
        ("policy_exited", "P"),
    ]


def test_vmstate_and_callframe_policy_stack_serialization():
    frame = CallFrame(return_ip=3, locals_snapshot={}, policy_stack_snapshot=["P"])
    state = VMState(policy_stack=["P", "P:rule:0:require"], call_stack=[frame])
    restored = VMState.from_dict(state.to_dict())
    assert restored.policy_stack == ["P", "P:rule:0:require"]
    assert restored.call_stack[0].policy_stack_snapshot == ["P"]


def test_callframe_policy_raii_cleanup_on_return():
    vm, host, _bridge = make_vm([Instruction("RETURN")])
    fn = FunctionObject(name="f", params=[], body_ip=0, program_hash=vm.program.program_hash)
    frame = CallFrame(
        return_ip=1,
        locals_snapshot={"f": fn},
        fn_name="f",
        program_hash=vm.program.program_hash,
        body_ip=0,
        stack_base=0,
        policy_stack_snapshot=["P"],
    )
    vm.state.call_stack.append(frame)
    vm.state.stack.append(42)
    vm.state.policy_stack = ["P", "P:rule:0:require"]
    host.policy_stack = ["P", "P:rule:0:require"]
    host.current_policy = "P"
    host.current_policy_rule = "P:rule:0:require"
    vm.step()
    assert vm.state.policy_stack == ["P"]
    assert host.policy_stack == ["P"]
    assert host.current_policy == "P"
    assert host.current_policy_rule is None
    exit_events = [e for e in host.execution_history if e.get("type") == "policy_rule_exited"]
    assert exit_events[-1]["rule"] == "P:rule:0:require"
    assert exit_events[-1]["unwind_reason"] == "function_return"


def test_nested_policy_context_actor_return_unwind_cross_domain():
    vm, host, _bridge = make_vm([Instruction("RETURN")])
    frame = CallFrame(
        return_ip=1,
        locals_snapshot={},
        stack_base=0,
        context_stack_snapshot=["ctx-root"],
        actor_stack_snapshot=["ActorRoot"],
        policy_stack_snapshot=["PolicyRoot"],
    )
    vm.state.call_stack.append(frame)
    vm.state.stack.append("ok")
    vm.state.context_stack = ["ctx-root", "ctx-inner"]
    vm.state.actor_stack = ["ActorRoot", "ActorInner"]
    vm.state.policy_stack = ["PolicyRoot", "PolicyRoot:rule:0:require"]
    host.context_tracker.stack = ["ctx-root", "ctx-inner"]
    host.actor_stack = ["ActorRoot", "ActorInner"]
    host.policy_stack = ["PolicyRoot", "PolicyRoot:rule:0:require"]
    vm.step()
    assert vm.state.context_stack == ["ctx-root"]
    assert vm.state.actor_stack == ["ActorRoot"]
    assert vm.state.policy_stack == ["PolicyRoot"]
    assert any(e.get("type") == "context_exited" and e.get("unwind_reason") == "function_return" for e in host.execution_history)
    assert any(e.get("type") == "actor_exited" and e.get("unwind_reason") == "function_return" for e in host.execution_history)
    assert any(e.get("type") == "policy_rule_exited" and e.get("unwind_reason") == "function_return" for e in host.execution_history)


def test_policy_stack_changes_transition_hash():
    ins = Instruction("HALT")
    vm_a, _host_a, _bridge_a = make_vm([ins])
    vm_b, _host_b, _bridge_b = make_vm([ins])
    vm_a.state.policy_stack = ["P1"]
    vm_b.state.policy_stack = ["P2"]
    vm_a.step()
    vm_b.step()
    assert vm_a.state.transition_hash != vm_b.state.transition_hash


def test_restore_syncs_policy_runtime_from_vm_state():
    vm, host, bridge = make_vm([Instruction("HALT")])
    vm.state.policy_stack = ["P", "P:rule:0:require"]
    bridge.sync_policy_runtime_from_vm_state(vm)
    assert host.policy_stack == ["P", "P:rule:0:require"]
    assert host.current_policy == "P"
    assert host.current_policy_rule == "P:rule:0:require"


def test_policy_routing_surface_enabled():
    assert classify_ast_node_v22(PolicyDef(name="P")).route == "CVM"
    assert classify_ast_node_v22(PolicyRule(kind="require", value=Literal(value=True))).route == "CVM"


def test_policy_metadata_is_compile_time_constant_descriptor():
    ast = parse_source('policy P { target: "service" require true }')
    bytecode = CognitiveCompiler().compile(ast)
    enter = next(ins for ins in bytecode.instructions if ins.op == "POLICY_ENTER")
    assert isinstance(enter.b, dict)
    assert enter.b["target"] == "service"
