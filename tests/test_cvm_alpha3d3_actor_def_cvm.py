"""Alpha.3-D3: AgentDef/SubAgentDef structural wrapper implementation."""
from __future__ import annotations

from synapse.ast import AgentDef, FnDef, Literal, ReturnStmt, SubAgentDef
from synapse.bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from synapse.cvm import CallFrame, CognitiveVM, FunctionObject, VMState
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.runtime.actor_runtime import ActorRuntime
from synapse.runtime.vm_bridge import VMBridge
from synapse.runtime.vm_routing import classify_ast_node_v22


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


class FakeHost:
    def __init__(self):
        self.execution_history = []
        self.side_effect_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.intention_cascades = []
        self.current_context = None
        self.current_actor = None
        self.current_env = None
        self.current_agent_id = "default_agent"
        self.telemetry_events = []
        self.actor_stack = []
        self.actor_runtime = ActorRuntime(lambda: self, live_mode=None, replay_mode=None)

    def next_event_id(self):
        return f"evt-{len(self.execution_history):08d}"

    def current_trace_id(self):
        return "trace-d3"

    def emit_runtime_event(self, event, env=None):
        return None

    def metrics_snapshot(self):
        return {}


def make_vm(instructions):
    program = BytecodeProgram(instructions=instructions, constants=[])
    host = FakeHost()
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(program)
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm, host, bridge


def test_agent_def_compiler_emits_actor_enter_exit():
    program = AgentDef(
        name="Worker",
        model="mock",
        methods=[FnDef(name="act", params=[], body=[ReturnStmt(value=Literal(value=1))])],
    )
    bytecode = CognitiveCompiler().compile(program)
    ops = [ins.op for ins in bytecode.instructions]
    assert "ACTOR_ENTER" in ops
    assert "ACTOR_EXIT" in ops
    enter = next(ins for ins in bytecode.instructions if ins.op == "ACTOR_ENTER")
    assert enter.a == "Worker"
    assert enter.b["kind"] == "agent"


def test_subagent_def_compiler_emits_actor_enter_exit():
    sub = SubAgentDef(
        name="Critic",
        focus="review",
        body=[ReturnStmt(value=Literal(value=7))],
    )
    bytecode = CognitiveCompiler().compile(sub)
    assert [ins.op for ins in bytecode.instructions][:1] == ["ACTOR_ENTER"]
    assert any(ins.op == "ACTOR_EXIT" and ins.a == "Critic" for ins in bytecode.instructions)


def test_actor_structural_runtime_parity_events():
    vm, host, _bridge = make_vm([
        Instruction("ACTOR_ENTER", "Worker", {"kind": "agent"}),
        Instruction("ACTOR_EXIT", "Worker"),
        Instruction("HALT"),
    ])
    vm.run()
    events = [e for e in host.execution_history if e.get("type") in {"actor_entered", "actor_exited"}]
    assert [e["type"] for e in events] == ["actor_entered", "actor_exited"]
    assert [e["event_id"] for e in events] == ["evt-00000000", "evt-00000001"]
    assert host.current_actor is None
    assert host.actor_stack == []
    assert vm.state.actor_stack == []


def test_nested_actor_structural_runtime_lifo_order():
    vm, host, _bridge = make_vm([
        Instruction("ACTOR_ENTER", "Outer", {"kind": "agent"}),
        Instruction("ACTOR_ENTER", "Inner", {"kind": "subagent"}),
        Instruction("ACTOR_EXIT", "Inner"),
        Instruction("ACTOR_EXIT", "Outer"),
        Instruction("HALT"),
    ])
    vm.run()
    actor_events = [e for e in host.execution_history if e.get("type") in {"actor_entered", "actor_exited"}]
    assert [(e["type"], e["actor"]) for e in actor_events] == [
        ("actor_entered", "Outer"),
        ("actor_entered", "Inner"),
        ("actor_exited", "Inner"),
        ("actor_exited", "Outer"),
    ]
    assert host.actor_stack == []
    assert vm.state.actor_stack == []


def test_vmstate_and_callframe_actor_stack_serialization():
    frame = CallFrame(return_ip=3, locals_snapshot={}, actor_stack_snapshot=["Outer"])
    state = VMState(actor_stack=["Outer", "Inner"], call_stack=[frame])
    restored = VMState.from_dict(state.to_dict())
    assert restored.actor_stack == ["Outer", "Inner"]
    assert restored.call_stack[0].actor_stack_snapshot == ["Outer"]


def test_callframe_actor_raii_cleanup_on_return():
    vm, host, _bridge = make_vm([Instruction("RETURN")])
    fn = FunctionObject(name="f", params=[], body_ip=0, program_hash=vm.program.program_hash)
    frame = CallFrame(
        return_ip=1,
        locals_snapshot={"f": fn},
        fn_name="f",
        program_hash=vm.program.program_hash,
        body_ip=0,
        stack_base=0,
        actor_stack_snapshot=["Outer"],
    )
    vm.state.call_stack.append(frame)
    vm.state.stack.append(42)
    vm.state.actor_stack = ["Outer", "Inner"]
    host.actor_stack = ["Outer", "Inner"]
    host.current_actor = "Inner"
    vm.step()
    assert vm.state.actor_stack == ["Outer"]
    assert host.actor_stack == ["Outer"]
    assert host.current_actor == "Outer"
    exit_events = [e for e in host.execution_history if e.get("type") == "actor_exited"]
    assert exit_events[-1]["actor"] == "Inner"
    assert exit_events[-1]["unwind_reason"] == "function_return"


def test_actor_stack_changes_transition_hash():
    ins = Instruction("HALT")
    vm_a, _host_a, _bridge_a = make_vm([ins])
    vm_b, _host_b, _bridge_b = make_vm([ins])
    vm_a.state.actor_stack = ["A"]
    vm_b.state.actor_stack = ["B"]
    vm_a.step()
    vm_b.step()
    assert vm_a.state.transition_hash != vm_b.state.transition_hash


def test_restore_syncs_actor_runtime_from_vm_state():
    vm, host, bridge = make_vm([Instruction("HALT")])
    vm.state.actor_stack = ["Outer", "Inner"]
    bridge.sync_actor_runtime_from_vm_state(vm)
    assert host.actor_stack == ["Outer", "Inner"]
    assert host.current_actor == "Inner"


def test_agent_and_subagent_routing_surface_enabled():
    assert classify_ast_node_v22(AgentDef(name="A")).route == "CVM"
    assert classify_ast_node_v22(SubAgentDef(name="S")).route == "CVM"
