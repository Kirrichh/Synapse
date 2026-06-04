"""Alpha.3-D5 Commit 2: actor messaging opcodes + compiler surface.

The tests keep D5 guardrails explicit: no parser grammar changes, no payload
unpacking opcodes, no LLM/prompt changes, and messaging pause is isolated from
pending_host_call.
"""

from synapse.ast import Program, SendStmt, ReceiveBlock, ReceivePattern, Literal, LetStmt, Variable
from synapse.bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMState, VMStatus
from synapse.runtime.vm_bridge import VMBridge


class FakeHost:
    def __init__(self):
        self.mailboxes = {}
        self.actor_log = []
        self.execution_history = []
        self.current_env = None
        self.current_actor = "actor-a"
        self.current_agent_id = "default_agent"
        self._event_id = 0

    def next_event_id(self):
        self._event_id += 1
        return f"evt-{self._event_id:04d}"

    def emit_runtime_event(self, event, env=None):
        self.actor_log.append(event)


def _vm_with_bridge(program, state=None):
    host = FakeHost()
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(program, state=state or VMState(actor_stack=["actor-a"]))
    vm.host = bridge.get_cvm_callback_adapter(vm)
    return vm, host, bridge


def test_msg_send_opcode_dispatches_via_bridge_and_records_outbound_snapshot():
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("LOAD_CONST", 1),
            Instruction("MSG_SEND", "ping"),
            Instruction("HALT"),
        ],
        constants=["actor-b", {"text": "hello"}],
    )
    vm, host, _ = _vm_with_bridge(program)

    vm.step()
    vm.step()
    vm.step()

    assert vm.state.mailbox_outbound[0]["target_id"] == "actor-b"
    assert vm.state.mailbox_outbound[0]["msg_type"] == "ping"
    assert host.mailboxes["actor-b"][0]["payload"] == {"text": "hello"}
    assert any(e.get("type") == "message_sent" for e in host.execution_history)


def test_msg_receive_binds_sender_and_message_when_inbox_has_message():
    message = {
        "msg_type": "ping",
        "sender_id": "actor-b",
        "target_id": "actor-a",
        "payload": {"n": 1},
    }
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "payload"), Instruction("HALT")],
        constants=[],
    )
    vm, host, _ = _vm_with_bridge(program, VMState(actor_stack=["actor-a"], mailbox_inbound=[message]))

    vm.step()

    assert vm.status() == VMStatus.RUNNING
    assert vm.state.locals["sender"] == "actor-b"
    assert vm.state.locals["payload"]["payload"] == {"n": 1}
    assert vm.state.mailbox_inbound == []
    assert any(e.get("type") == "message_consumed" for e in host.execution_history)


def test_msg_receive_empty_inbox_enters_paused_messaging_without_host_call_pause():
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "payload"), Instruction("HALT")],
        constants=[],
    )
    vm, _, _ = _vm_with_bridge(program, VMState(actor_stack=["actor-a"]))

    result = vm.step()

    assert result is True
    assert vm.status() == VMStatus.PAUSED_MESSAGING
    assert vm.state.pending_host_call is None
    assert vm.state.pending_message_receive["status"] == VMStatus.PAUSED_MESSAGING
    assert vm.state.pending_message_receive["sender_var"] == "sender"
    assert vm.state.pending_message_receive["target_var"] == "payload"


def test_resume_message_receive_clears_pending_and_binds_message():
    vm = CognitiveVM(
        BytecodeProgram(instructions=[Instruction("HALT")], constants=[]),
        state=VMState(
            actor_stack=["actor-a"],
            pending_message_receive={
                "status": VMStatus.PAUSED_MESSAGING,
                "message_receive_id": "mr-1",
                "sender_var": "sender",
                "target_var": "payload",
            },
        ),
    )
    vm.halted = True

    vm.resume_message_receive({"sender_id": "actor-b", "msg_type": "pong", "payload": 42})

    assert vm.status() == VMStatus.RUNNING
    assert vm.state.pending_message_receive is None
    assert vm.state.locals["sender"] == "actor-b"
    assert vm.state.locals["payload"]["payload"] == 42


def test_compiler_emits_msg_send_without_payload_unpacking_opcodes():
    program = CognitiveCompiler().compile(Program(statements=[
        SendStmt(receiver=Literal("actor-b"), method="ping", args=[Literal("hello")])
    ]))
    ops = [ins.op for ins in program.instructions]

    assert "MSG_SEND" in ops
    assert "MSG_UNPACK" not in ops
    assert "MSG_MATCH_PAYLOAD" not in ops


def test_compiler_emits_receive_enter_msg_receive_and_receive_exit_for_current_syntax():
    source = Program(statements=[
        ReceiveBlock(patterns=[
            ReceivePattern(
                sender_var="sender",
                target_var="payload",
                body=[LetStmt(name="seen", value=Variable("payload"))],
            )
        ])
    ])
    program = CognitiveCompiler().compile(source)
    ops = [ins.op for ins in program.instructions]

    assert "RECEIVE_ENTER" in ops
    assert "MSG_RECEIVE" in ops
    assert "RECEIVE_EXIT" in ops
    assert "MSG_UNPACK" not in ops
