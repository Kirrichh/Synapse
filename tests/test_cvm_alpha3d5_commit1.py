"""Alpha.3-D5 Commit 1: messaging VM substrate only.

These tests validate durable mailbox state, STATUS_PAUSED_MESSAGING, and
transition-hash participation. They intentionally do not exercise messaging
opcodes, compiler routing, or bridge dispatch; those belong to later D5 commits.
"""

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMState, VMStatus


def _halt_vm(state: VMState) -> CognitiveVM:
    return CognitiveVM(
        BytecodeProgram(instructions=[Instruction("HALT")], constants=[]),
        state=state,
    )


def test_vmstate_mailbox_snapshot_serialization_roundtrip():
    state = VMState(
        mailbox_inbound=[
            {
                "message_id": "msg-1",
                "msg_type": "ping",
                "sender_id": "actor-a",
                "payload": {"text": "hello"},
            }
        ],
        mailbox_outbound=[
            {
                "message_id": "msg-2",
                "msg_type": "pong",
                "target_id": "actor-b",
                "payload": [1, 2, 3],
            }
        ],
    )

    encoded = state.to_dict()
    restored = VMState.from_dict(encoded)

    assert encoded["mailbox_inbound"] == state.mailbox_inbound
    assert encoded["mailbox_outbound"] == state.mailbox_outbound
    assert restored.mailbox_inbound == state.mailbox_inbound
    assert restored.mailbox_outbound == state.mailbox_outbound


def test_pending_message_receive_serialization_is_separate_from_host_call():
    state = VMState(
        pending_host_call=None,
        pending_message_receive={
            "pending_schema_version": "1",
            "status": VMStatus.PAUSED_MESSAGING,
            "receiver_id": "actor-b",
            "expected_msg_type": "ping",
            "sender_var": "sender",
            "target_var": "payload",
        },
    )

    encoded = state.to_dict()
    restored = VMState.from_dict(encoded)

    assert encoded["pending_host_call"] is None
    assert encoded["pending_message_receive"]["status"] == VMStatus.PAUSED_MESSAGING
    assert restored.pending_host_call is None
    assert restored.pending_message_receive == state.pending_message_receive


def test_vm_status_reports_paused_messaging_without_reusing_host_call_pause():
    vm = _halt_vm(
        VMState(
            pending_host_call=None,
            pending_message_receive={"status": VMStatus.PAUSED_MESSAGING},
        )
    )

    assert vm.status() == VMStatus.PAUSED_MESSAGING
    assert vm.status() != VMStatus.PAUSED_HOST_CALL


def test_vm_status_host_call_pause_still_has_priority_over_messaging_pause():
    vm = _halt_vm(
        VMState(
            pending_host_call={"status": VMStatus.PAUSED_HOST_CALL, "call_id": "hc-1"},
            pending_message_receive={"status": VMStatus.PAUSED_MESSAGING},
        )
    )

    assert vm.status() == VMStatus.PAUSED_HOST_CALL


def test_transition_hash_includes_mailbox_inbound_content_and_order():
    state_a = VMState(
        mailbox_inbound=[
            {"message_id": "m1", "msg_type": "ping"},
            {"message_id": "m2", "msg_type": "pong"},
        ],
        gas_remaining=1000,
    )
    state_b = VMState(
        mailbox_inbound=[
            {"message_id": "m2", "msg_type": "pong"},
            {"message_id": "m1", "msg_type": "ping"},
        ],
        gas_remaining=1000,
    )

    vm_a = _halt_vm(state_a)
    vm_b = _halt_vm(state_b)
    vm_a.step()
    vm_b.step()

    assert vm_a.state.transition_hash != vm_b.state.transition_hash


def test_transition_hash_includes_mailbox_outbound_content():
    vm_a = _halt_vm(
        VMState(mailbox_outbound=[{"target_id": "actor-a", "msg_type": "ping"}])
    )
    vm_b = _halt_vm(
        VMState(mailbox_outbound=[{"target_id": "actor-b", "msg_type": "ping"}])
    )

    vm_a.step()
    vm_b.step()

    assert vm_a.state.transition_hash != vm_b.state.transition_hash


def test_transition_hash_includes_pending_message_receive_envelope():
    vm_a = _halt_vm(
        VMState(pending_message_receive={"receiver_id": "actor-a", "expected_msg_type": "ping"})
    )
    vm_b = _halt_vm(
        VMState(pending_message_receive={"receiver_id": "actor-a", "expected_msg_type": "pong"})
    )

    vm_a.step()
    vm_b.step()

    assert vm_a.state.transition_hash != vm_b.state.transition_hash
