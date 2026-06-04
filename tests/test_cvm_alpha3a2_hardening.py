"""Alpha.3-A2.1 hardening tests.

Covers P0 restore safety gaps found after Alpha.3-A2:
- stale FunctionObject values after program migration (foreign closures)
- history boundary continuity for cold/cross-process resume
"""
from __future__ import annotations

import copy

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import (
    CognitiveVM,
    CallFrame,
    FunctionObject,
    VMCodeMigrationRequiresMapError,
    VMResumeSyncError,
    VMTamperDetectedError,
    VMState,
)
from synapse.hardening import hash_event_chain
from synapse.runtime.migration import iter_function_objects, validate_vm_state_program_hashes
from synapse.runtime.vm_bridge import VMBridge, restore_vm_from_snapshot


class FakeHost:
    """Minimal host compatible with VMBridge snapshot/restore tests."""

    def __init__(self):
        self.execution_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.current_context = "test_ctx"
        self.intention_cascades = []
        self.affective_states = {}
        self.history_chain_seed = "test-seed"
        self._trace_id = "trace-123"

    def current_trace_id(self):
        return self._trace_id


def make_program(value: int) -> BytecodeProgram:
    return BytecodeProgram(
        instructions=[Instruction("LOAD_CONST", 0), Instruction("RETURN")],
        constants=[value],
    )


def make_vm(program: BytecodeProgram) -> CognitiveVM:
    vm = CognitiveVM(program)
    vm.state.gas_remaining = 5000
    return vm


class TestForeignClosureDetection:
    def test_stale_function_in_locals_fails(self):
        old_program = make_program(42)
        new_program = make_program(99)
        assert old_program.program_hash != new_program.program_hash

        old_fn = FunctionObject(
            name="old_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        state = VMState(locals={"f": old_fn})

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, new_program.program_hash)

        assert "Stale FunctionObject 'old_function'" in str(exc_info.value)
        assert "state.locals" in str(exc_info.value)

    def test_stale_function_in_stack_fails(self):
        old_program = make_program(42)
        new_program = make_program(99)
        old_fn = FunctionObject(
            name="stack_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        state = VMState(stack=[old_fn])

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, new_program.program_hash)

        assert "stack_function" in str(exc_info.value)
        assert "state.stack" in str(exc_info.value)

    def test_stale_function_in_name_save_stack_fails(self):
        old_program = make_program(42)
        new_program = make_program(99)
        old_fn = FunctionObject(
            name="shadowed_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        state = VMState(name_save_stack=[("item", True, old_fn)])

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, new_program.program_hash)

        assert "shadowed_function" in str(exc_info.value)
        assert "state.name_save_stack" in str(exc_info.value)

    def test_stale_function_in_call_frame_locals_snapshot_fails(self):
        old_program = make_program(42)
        new_program = make_program(99)
        old_fn = FunctionObject(
            name="frame_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        frame = CallFrame(
            return_ip=10,
            locals_snapshot={"fn": old_fn},
            fn_name="caller",
            program_hash=old_program.program_hash,
            body_ip=0,
            stack_base=0,
        )
        state = VMState(call_stack=[frame])

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, new_program.program_hash)

        assert "frame_function" in str(exc_info.value)
        assert "state.call_stack.locals_snapshot" in str(exc_info.value)

    def test_nested_closure_stale_function_fails(self):
        old_program = make_program(42)
        new_program = make_program(99)
        inner_fn = FunctionObject(
            name="inner",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        outer_fn = FunctionObject(
            name="outer",
            params=[],
            body_ip=0,
            closure={"inner_fn": inner_fn},
            program_hash=old_program.program_hash,
        )
        state = VMState(locals={"outer": outer_fn})

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, new_program.program_hash)

        # Outer is encountered first and is already stale; either stale outer or
        # nested stale inner is sufficient to fail closed.
        assert "Stale FunctionObject" in str(exc_info.value)
        assert "state.locals" in str(exc_info.value)

    def test_legacy_function_without_hash_fails_under_migration(self):
        legacy_fn = FunctionObject(
            name="legacy_function",
            params=[],
            body_ip=5,
            closure={},
            program_hash=None,
        )
        state = VMState(locals={"legacy": legacy_fn})

        with pytest.raises(VMCodeMigrationRequiresMapError) as exc_info:
            validate_vm_state_program_hashes(state, make_program(99).program_hash)

        assert "has no program_hash" in str(exc_info.value)

    def test_matching_hash_allowed(self):
        program = make_program(42)
        fn = FunctionObject(
            name="valid_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=program.program_hash,
        )
        state = VMState(locals={"f": fn})
        validate_vm_state_program_hashes(state, program.program_hash)

    def test_restore_snapshot_rejects_foreign_closure_with_empty_callstack(self):
        old_program = make_program(42)
        new_program = make_program(99)
        old_fn = FunctionObject(
            name="old_function",
            params=[],
            body_ip=0,
            closure={},
            program_hash=old_program.program_hash,
        )
        state = VMState(locals={"f": old_fn}, call_stack=[])
        snapshot = {
            "vm_state": state.to_dict(),
            "program_hash": old_program.program_hash,
        }

        with pytest.raises(VMCodeMigrationRequiresMapError):
            restore_vm_from_snapshot(snapshot, new_program)


class TestCycleSafeTraversal:
    def test_cyclic_closure_handled_once(self):
        program = make_program(42)
        fn_a = FunctionObject("fn_a", [], 0, {}, program.program_hash)
        fn_b = FunctionObject("fn_b", [], 0, {}, program.program_hash)
        fn_a.closure["b"] = fn_b
        fn_b.closure["a"] = fn_a

        functions = list(iter_function_objects({"a": fn_a}))
        assert {fn.name for fn in functions} == {"fn_a", "fn_b"}
        assert len(functions) == 2


class TestHistoryBoundary:
    def test_embedded_history_hydrates_empty_host(self):
        program = make_program(42)
        vm = make_vm(program)

        old_host = FakeHost()
        old_host.execution_history = [
            {"type": "host_call", "id": "evt-a"},
            {"type": "host_call", "id": "evt-b"},
        ]
        bridge = VMBridge(host_getter=lambda: old_host)
        snapshot = bridge.make_vm_snapshot(vm, label="cold", embed_history=True)

        new_host = FakeHost()
        new_host.vm_checkpoints["cold"] = copy.deepcopy(snapshot)
        new_bridge = VMBridge(host_getter=lambda: new_host)

        restored = new_bridge.restore_vm_from_checkpoint("cold")
        assert isinstance(restored, CognitiveVM)
        assert len(new_host.execution_history) >= 2
        assert new_host.execution_history[0]["id"] == "evt-a"
        assert new_host.execution_history[1]["id"] == "evt-b"

    def test_missing_history_fails_closed(self):
        program = make_program(42)
        vm = make_vm(program)

        old_host = FakeHost()
        old_host.execution_history = [{"type": "host_call", "id": "evt-a"}]
        bridge = VMBridge(host_getter=lambda: old_host)
        snapshot = bridge.make_vm_snapshot(vm, label="no_embed", embed_history=False)

        new_host = FakeHost()
        new_host.vm_checkpoints["no_embed"] = copy.deepcopy(snapshot)
        new_bridge = VMBridge(host_getter=lambda: new_host)

        with pytest.raises(VMResumeSyncError) as exc_info:
            new_bridge.restore_vm_from_checkpoint("no_embed")

        assert "execution history is truncated" in str(exc_info.value)

    def test_history_mismatch_fails_closed(self):
        program = make_program(42)
        vm = make_vm(program)

        old_host = FakeHost()
        old_host.execution_history = [{"type": "host_call", "id": "evt-a"}]
        bridge = VMBridge(host_getter=lambda: old_host)
        snapshot = bridge.make_vm_snapshot(vm, label="boundary", embed_history=False)

        new_host = FakeHost()
        new_host.execution_history = [{"type": "host_call", "id": "evt-x"}]
        new_host.vm_checkpoints["boundary"] = copy.deepcopy(snapshot)
        new_bridge = VMBridge(host_getter=lambda: new_host)

        with pytest.raises(VMTamperDetectedError) as exc_info:
            new_bridge.restore_vm_from_checkpoint("boundary")

        # Same length but different event payload means canonical tamper detection.
        assert "History hash mismatch" in str(exc_info.value)

    def test_make_snapshot_adds_history_boundary_without_polluting_pure_helper(self):
        program = make_program(42)
        vm = make_vm(program)
        host = FakeHost()
        bridge = VMBridge(host_getter=lambda: host)

        snapshot = bridge.make_vm_snapshot(vm, label="boundary", embed_history=False)
        assert "history_boundary" in snapshot
        assert snapshot["history_boundary"]["history_embedded"] is False
        assert "embedded_execution_history" not in snapshot
