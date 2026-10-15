"""Alpha.3-D5 Commit 2: actor messaging opcodes + compiler surface.

The tests keep D5 guardrails explicit: no parser grammar changes, no payload
unpacking opcodes, no LLM/prompt changes, and messaging pause is isolated from
pending_host_call.
"""

import pytest

from synapse import Interpreter, compile_to_ast
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

    def record_history_event(self, event):
        self.execution_history.append(event)
        return event

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
    host = Interpreter()
    host.interpret(compile_to_ast('policy Gov { target "actor-b.ping" forbid "blocked" }'))
    vm = CognitiveVM(program, VMState(actor_stack=["actor-a"]))
    vm.host = host.runtime.vm.get_cvm_callback_adapter(vm)

    vm.step()
    vm.step()
    vm.step()

    assert vm.state.mailbox_outbound[0]["target_id"] == "actor-b"
    assert vm.state.mailbox_outbound[0]["msg_type"] == "ping"
    assert vm.state.mailbox_outbound[0]["sender"] == "actor-a"
    assert host.mailboxes["actor-b"][0]["payload"] == {"text": "hello"}
    assert host.mailboxes["actor-b"][0]["args"] == [{"text": "hello"}]
    assert [event["type"] for event in host.execution_history] == ["policy_evaluated", "message_sent"]
    assert host.execution_history[0]["args"] == [{"text": "hello"}]


def test_msg_send_does_not_commit_outbound_when_host_refuses():
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1),
            Instruction("MSG_SEND", "ping"), Instruction("HALT"),
        ],
        constants=["actor-b", {"text": "not-delivered"}],
    )
    vm = CognitiveVM(program, state=VMState(actor_stack=["actor-a"]))

    def refuse(*_args):
        raise RuntimeError("host refused the send")

    vm.host = refuse
    vm.step(); vm.step()
    with pytest.raises(RuntimeError, match="host refused"):
        vm.step()

    assert vm.state.mailbox_outbound == []


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


def test_msg_receive_does_not_consume_or_bind_when_host_refuses():
    message = {
        "msg_type": "ping", "sender_id": "actor-b", "target_id": "actor-a",
        "payload": {"n": 1},
    }
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "payload"), Instruction("HALT")]
    )
    vm = CognitiveVM(
        program, state=VMState(actor_stack=["actor-a"], mailbox_inbound=[message])
    )

    def refuse(*_args):
        raise RuntimeError("host refused the consume")

    vm.host = refuse
    with pytest.raises(RuntimeError, match="host refused"):
        vm.step()

    assert vm.state.mailbox_inbound == [message]
    assert "sender" not in vm.state.locals and "payload" not in vm.state.locals
    assert vm.state.stack == []


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


@pytest.mark.parametrize("arguments", [[], [None], ["hello", 7], [["hello", 7]]],
                         ids=["zero", "one-none", "two", "one-list"])
def test_compiled_send_preserves_arguments_for_policy_and_delivery(arguments):
    program = CognitiveCompiler().compile(Program(statements=[
        SendStmt(receiver=Literal(value="actor-b"), method="ping", args=[Literal(value=arg) for arg in arguments])
    ]))
    host = Interpreter()
    host.interpret(compile_to_ast('policy Gov { target "actor-b.ping" forbid "blocked" }'))
    vm = CognitiveVM(program)
    vm.host = host.runtime.vm.get_cvm_callback_adapter(vm)
    result = host.runtime.vm.run_cvm_with_context_safety(vm)
    assert result["halted"] is True
    assert vm.state.mailbox_outbound[0]["argc"] == len(arguments)
    assert "args" not in vm.state.mailbox_outbound[0]
    assert host.mailboxes["actor-b"][0]["args"] == arguments
    expected_payload = arguments[0] if len(arguments) == 1 else arguments
    assert vm.state.mailbox_outbound[0]["payload"] == expected_payload
    assert host.mailboxes["actor-b"][0]["payload"] == expected_payload
    assert [event["type"] for event in host.execution_history] == ["policy_evaluated", "message_sent"]
    assert host.execution_history[0]["args"] == arguments
    assert host.execution_history[0]["sender"] == "global"


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
