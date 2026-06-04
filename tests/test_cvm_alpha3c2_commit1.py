"""Alpha.3-C2 Commit 1: context_stack durable state + CallFrame RAII."""

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMState, CallFrame
from synapse.runtime.vm_bridge import VMBridge


def test_vmstate_context_stack_serialization_roundtrip():
    state = VMState(context_stack=["outer", "inner"])

    encoded = state.to_dict()
    restored = VMState.from_dict(encoded)

    assert encoded["context_stack"] == ["outer", "inner"]
    assert restored.context_stack == ["outer", "inner"]


def test_callframe_context_stack_snapshot_roundtrip():
    frame = CallFrame(
        return_ip=7,
        locals_snapshot={"x": 1},
        fn_name="f",
        program_hash="sha256:test",
        body_ip=3,
        stack_base=2,
        context_stack_snapshot=["caller_ctx"],
    )

    encoded = frame.to_dict()
    restored = CallFrame.from_dict(encoded)

    assert encoded["context_stack_snapshot"] == ["caller_ctx"]
    assert restored.context_stack_snapshot == ["caller_ctx"]
    assert restored.locals_snapshot == {"x": 1}


def test_transition_hash_includes_context_stack():
    program = BytecodeProgram(instructions=[Instruction("HALT")], constants=[])

    vm_a = CognitiveVM(program, state=VMState(context_stack=["ctx_a"], gas_remaining=1000))
    vm_b = CognitiveVM(program, state=VMState(context_stack=["ctx_b"], gas_remaining=1000))

    vm_a.step()
    vm_b.step()

    assert vm_a.state.transition_hash != vm_b.state.transition_hash


def test_return_restores_context_stack_to_callframe_snapshot_and_unwinds_dangling():
    program = BytecodeProgram(instructions=[Instruction("RETURN")], constants=[])
    host_calls = []

    def host(opcode, a, b):
        host_calls.append((opcode, a, b))
        return {"status": "ok", "from_cache": False}

    frame = CallFrame(
        return_ip=9,
        locals_snapshot={"caller": True},
        fn_name="opens_context",
        stack_base=0,
        context_stack_snapshot=["outer"],
    )
    state = VMState(
        ip=0,
        stack=[42],
        locals={"callee_tmp": "leak_candidate"},
        call_stack=[frame],
        context_stack=["outer", "inner"],
        gas_remaining=1000,
    )
    vm = CognitiveVM(program, state=state, host=host)

    vm.step()

    assert vm.state.context_stack == ["outer"]
    assert vm.state.locals == {"caller": True}
    assert vm.state.ip == 9
    assert vm.state.stack == [42]
    assert host_calls == [
        (
            "CALL_HOST",
            {"symbol": "SYS_CONTEXT_EXIT", "args": ["inner", "function_return"]},
            None,
        )
    ]


class _FakeTracker:
    def __init__(self):
        self.current = None
        self.closed = []

    def exit_event(self, label):
        self.closed.append(label)
        return {"type": "context_exited", "label": label}


class _FakeHost:
    def __init__(self):
        self.context_tracker = _FakeTracker()
        self.execution_history = []
        self.current_context = "ctx"
        self.current_env = None
        self._next = 0

    def next_event_id(self):
        self._next += 1
        return f"evt-{self._next}"

    def emit_runtime_event(self, event, env):
        event["emitted"] = True

    def current_trace_id(self):
        return "trace-test"


def test_vmbridge_unwind_context_is_idempotent_and_no_throw():
    host = _FakeHost()
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(BytecodeProgram(instructions=[]), state=VMState(context_stack=["outer", "inner"]))

    bridge.unwind_context(vm, "inner", reason="test_cleanup")
    bridge.unwind_context(vm, "missing", reason="test_cleanup")

    assert vm.state.context_stack == ["outer"]
    assert host.context_tracker.closed[0] == "inner"
    assert host.execution_history[0]["unwind_reason"] == "test_cleanup"
    assert host.execution_history[0]["emitted"] is True
