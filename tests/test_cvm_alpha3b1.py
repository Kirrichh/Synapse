"""Alpha.3-B1: CALL_HOST hardening tests.

This suite verifies JSON-safe host errors and async host-call pause/resume
without introducing capability enforcement.  Capability checks are Alpha.3-B2.
"""
import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMHostError, VMResumeSyncError, VMState


def _make_vm(host=None):
    program = BytecodeProgram(instructions=[], constants=[])
    return CognitiveVM(program, state=VMState(gas_remaining=5000), host=host)


def test_vmhosterror_to_dict():
    payload = VMHostError("TEST", "msg", "sym").to_dict()
    assert payload == {
        "type": "VMHostError",
        "code": "TEST",
        "message": "msg",
        "symbol": "sym",
    }


def test_call_host_pushes_dict_on_error():
    def fail_host(opcode, a, b):
        raise VMHostError("ERR", "fail", "test")

    vm = _make_vm(fail_host)
    vm.program.instructions = [Instruction("CALL_HOST", "test", 0, None)]
    vm.state.ip = 0

    vm.step()

    assert vm.state.stack == [
        {"type": "VMHostError", "code": "ERR", "message": "fail", "symbol": "test"}
    ]


def test_call_host_normalizes_python_host_errors():
    def bad_host(opcode, a, b):
        raise TypeError("bad host type")

    vm = _make_vm(bad_host)
    vm.program.instructions = [Instruction("CALL_HOST", "SYS_BAD", 0, None)]
    vm.state.ip = 0

    vm.step()

    assert len(vm.state.stack) == 1
    payload = vm.state.stack[0]
    assert payload["type"] == "VMHostError"
    assert payload["code"] == "HOST_ERROR"
    assert payload["symbol"] == "SYS_BAD"
    assert "bad host type" in payload["message"]


def test_async_pause_stack_integrity():
    def pause_host(opcode, a, b):
        assert opcode == "CALL_HOST"
        assert a == {"symbol": "SYS_TEST", "args": ["arg1"]}
        return {"status": "STATUS_PAUSED_HOST_CALL", "call_id": "hc-1"}

    vm = _make_vm(pause_host)
    vm._push("arg1")
    vm.program.instructions = [Instruction("CALL_HOST", "SYS_TEST", 1, None)]
    vm.state.ip = 0

    vm.step()

    assert vm.halted is True
    assert vm.state.pending_host_call == {
        "symbol": "SYS_TEST",
        "args": ["arg1"],
        "argc": 1,
        "call_id": "hc-1",
    }
    assert vm.state.ip == 1  # pre-incremented by step()
    assert vm.state.stack == []

    vm.resume("resp")

    assert vm.halted is False
    assert vm.state.pending_host_call is None
    assert vm.state.stack == ["resp"]
    assert vm.state.ip == 1  # resume does not mutate IP


def test_async_pause_generates_call_id_when_missing():
    def pause_host(opcode, a, b):
        return {"status": "STATUS_PAUSED_HOST_CALL"}

    vm = _make_vm(pause_host)
    vm.program.instructions = [Instruction("CALL_HOST", "SYS_TEST", 0, None)]
    vm.state.ip = 0

    vm.step()

    call_id = vm.state.pending_host_call["call_id"]
    assert isinstance(call_id, str)
    assert call_id.startswith("hc-")


def test_resume_without_pending_raises():
    with pytest.raises(VMResumeSyncError):
        _make_vm().resume("x")


def test_pending_host_call_serialization():
    state = VMState(pending_host_call={"symbol": "S", "args": [1], "argc": 1, "call_id": "hc-123"})

    restored = VMState.from_dict(state.to_dict())

    assert restored.pending_host_call == {
        "symbol": "S",
        "args": [1],
        "argc": 1,
        "call_id": "hc-123",
    }


def test_builtin_len_execution():
    vm = _make_vm()
    assert vm._call_host_builtin("len", [[1, 2, 3]]) == 3


def test_builtin_range_execution():
    vm = _make_vm()
    assert vm._call_host_builtin("range", [0, 5]) == [0, 1, 2, 3, 4]


def test_type_error_in_builtin_converted_to_host_error():
    vm = _make_vm()

    with pytest.raises(VMHostError) as exc_info:
        vm._call_host_builtin("int", ["not_an_int"])

    assert exc_info.value.code == "HOST_ERROR"
    assert exc_info.value.symbol == "int"
    assert "int()" in str(exc_info.value)


def test_call_host_builtin_error_is_json_safe_on_stack():
    vm = _make_vm()
    vm._push("not_an_int")
    vm.program.instructions = [Instruction("CALL_HOST", "int", 1, None)]
    vm.state.ip = 0

    vm.step()

    assert len(vm.state.stack) == 1
    payload = vm.state.stack[0]
    assert isinstance(payload, dict)
    assert payload["type"] == "VMHostError"
    assert payload["code"] == "HOST_ERROR"
    assert payload["symbol"] == "int"
