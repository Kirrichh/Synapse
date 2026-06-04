"""Alpha.3-D1 durable single pending host-call lifecycle tests."""
import json
import threading

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import (
    CognitiveVM,
    FunctionObject,
    VMResumeSyncError,
    VMState,
    VMStatus,
    compute_call_id,
)
from synapse.runtime.host_abi import HOST_ABI_VERSION
from synapse.runtime.vm_bridge import VMBridge, HOST_CAPABILITIES


class FakeHost:
    def __init__(self, agent_id="default_agent"):
        self.current_agent_id = agent_id
        self.trust_snapshot_agent_id = False
        self.execution_history = []
        self.side_effect_history = []
        self.vm_checkpoints = {}
        self.vm_snapshots = []
        self.intention_cascades = []
        self.affective_states = {}
        self.current_context = None
        self.history_chain_seed = "seed"
        self._event_counter = 0

    def current_trace_id(self):
        return "trace-test"

    def next_event_id(self):
        event_id = f"evt-{self._event_counter:08d}"
        self._event_counter += 1
        return event_id


def make_vm(host=None):
    program = BytecodeProgram(instructions=[Instruction("HALT")], constants=[])
    return CognitiveVM(program, gas=5000) if False else CognitiveVM(program)


def make_bridge_vm(agent_id="default_agent"):
    host = FakeHost(agent_id=agent_id)
    bridge = VMBridge(host_getter=lambda: host)
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("HALT")], constants=[]))
    vm.host = bridge.get_cvm_callback_adapter(vm)
    vm.state.agent_id = agent_id
    return host, bridge, vm


def pause_vm(vm, symbol="SYS_LLM_EVAL", args=None, required_capabilities=None, event_id="evt-00000000"):
    args = list(args or [])
    vm.program.instructions = [Instruction("CALL_HOST", symbol, len(args), None)]
    vm.state.ip = 0
    for arg in args:
        vm._push(arg)

    def host_callback(opcode, a, b):
        assert opcode == "CALL_HOST"
        return {
            "status": VMStatus.PAUSED_HOST_CALL,
            "created_at_event_id": event_id,
            "required_capabilities": sorted(required_capabilities or []),
            "determinism_class": "nondeterministic",
        }

    vm.host = host_callback
    vm.step()
    return vm.state.pending_host_call


def test_compute_call_id_is_deterministic_and_frame_depth_sensitive():
    seed = ("ph", 7, "sha256:abc", "evt-1", 2)
    assert compute_call_id(*seed) == compute_call_id(*seed)
    assert compute_call_id("ph", 7, "sha256:abc", "evt-1", 2) != compute_call_id(
        "ph", 7, "sha256:abc", "evt-1", 3
    )


def test_pending_host_call_envelope_v1_json_roundtrip():
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 1, None)], constants=[]))
    pending = pause_vm(vm, args=[{"prompt": "hello"}], event_id="evt-42")

    assert pending["pending_schema_version"] == "1"
    assert pending["status"] == VMStatus.PAUSED_HOST_CALL
    assert pending["host_abi_version"] == HOST_ABI_VERSION
    assert pending["ip_after_call"] == 1
    assert pending["frame_depth_at_call"] == 0
    assert pending["determinism_class"] == "nondeterministic"

    raw = json.loads(json.dumps(vm.state.to_dict()))
    restored = VMState.from_dict(raw)
    assert restored.pending_host_call["call_id"] == pending["call_id"]
    assert restored.pending_host_call["args"] == [{"prompt": "hello"}]


def test_resume_correct_call_id_pushes_once_and_preserves_ip():
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 0, None)], constants=[]))
    pending = pause_vm(vm)
    ip_after_pause = vm.state.ip

    vm.resume_host_call(pending["call_id"], "ok")

    assert vm.status() == VMStatus.RUNNING
    assert vm.state.pending_host_call is None
    assert vm.state.stack == ["ok"]
    assert vm.state.ip == ip_after_pause


def test_resume_wrong_call_id_fails_and_preserves_error():
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 0, None)], constants=[]))
    pause_vm(vm)

    with pytest.raises(VMResumeSyncError) as exc:
        vm.resume_host_call("hc-wrong", "ok")

    assert exc.value.code == "CALL_ID_MISMATCH"
    assert vm.status() == VMStatus.PAUSED_HOST_CALL
    assert vm.halted is True
    assert vm.state.error["code"] == "CALL_ID_MISMATCH"


def test_double_resume_forbidden_code():
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 0, None)], constants=[]))
    pending = pause_vm(vm)
    vm.resume_host_call(pending["call_id"], "first")

    with pytest.raises(VMResumeSyncError) as exc:
        vm.resume_host_call(pending["call_id"], "second")

    assert exc.value.code == "DOUBLE_RESUME_FORBIDDEN"


def test_bridge_resume_validates_host_abi_and_capability_exact_match():
    host, bridge, vm = make_bridge_vm("default_agent")
    pending = pause_vm(
        vm,
        required_capabilities=HOST_CAPABILITIES["default_agent"],
        event_id="evt-00000001",
    )
    vm.host = bridge.get_cvm_callback_adapter(vm)

    bridge.resume_host_call(vm, pending["call_id"], "ok")
    assert vm.state.stack[-1] == "ok"

    pending = pause_vm(
        vm,
        required_capabilities={"memory.write"},
        event_id="evt-00000002",
    )
    vm.host = bridge.get_cvm_callback_adapter(vm)
    with pytest.raises(VMResumeSyncError) as exc:
        bridge.resume_host_call(vm, pending["call_id"], "denied")
    assert exc.value.code == "CAPABILITY_EXACT_MATCH_FAILED"


def test_bridge_resume_rejects_host_abi_mismatch():
    host, bridge, vm = make_bridge_vm("default_agent")
    pending = pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"])
    pending["host_abi_version"] = "old-abi"
    vm.host = bridge.get_cvm_callback_adapter(vm)

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.resume_host_call(vm, pending["call_id"], "x")
    assert exc.value.code == "HOST_ABI_MISMATCH"


def test_pending_args_function_object_program_hash_mismatch_fails_closed():
    host, bridge, vm = make_bridge_vm("default_agent")
    fn = FunctionObject(name="old", params=[], body_ip=0, closure={}, program_hash="old-hash")
    pending = pause_vm(vm, args=[fn], required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm.host = bridge.get_cvm_callback_adapter(vm)

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.resume_host_call(vm, pending["call_id"], "x")
    assert exc.value.code == "PENDING_ARG_PROGRAM_HASH_MISMATCH"


def test_replay_lookup_by_call_id_missing_duplicate_and_success():
    host, bridge, vm = make_bridge_vm("default_agent")
    pending = pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm.host = bridge.get_cvm_callback_adapter(vm)

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.resume_host_call(vm, pending["call_id"], replay=True)
    assert exc.value.code == "HOST_CALL_REPLAY_MISSING"

    host.execution_history.append({"type": "host_call_resolved", "call_id": pending["call_id"], "result": "from-history"})
    bridge.resume_host_call(vm, pending["call_id"], replay=True)
    assert vm.state.stack[-1] == "from-history"

    pending = pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"], event_id="evt-dup")
    vm.host = bridge.get_cvm_callback_adapter(vm)
    host.execution_history.extend([
        {"type": "host_call_resolved", "call_id": pending["call_id"], "result": 1},
        {"type": "host_call_resolved", "call_id": pending["call_id"], "result": 2},
    ])
    with pytest.raises(VMResumeSyncError) as exc2:
        bridge.resume_host_call(vm, pending["call_id"], replay=True)
    assert exc2.value.code == "HOST_CALL_REPLAY_DUPLICATE"


def test_deterministic_side_effect_print_uses_side_effect_history_not_execution_lookup():
    host, bridge, vm = make_bridge_vm("restricted_worker")
    result = bridge.dispatch_host_call(vm, "print", ["hello"])
    assert result is None
    assert host.side_effect_history[-1]["symbol"] == "print"
    assert not [e for e in host.execution_history if e.get("type") == "host_call_resolved"]


def test_snapshot_during_pause_restore_and_resume():
    host, bridge, vm = make_bridge_vm("default_agent")
    pending = pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm.host = bridge.get_cvm_callback_adapter(vm)
    snapshot = bridge.make_vm_snapshot(vm, "paused", embed_history=True)
    assert snapshot["vm_state"]["pending_host_call"]["call_id"] == pending["call_id"]

    vm2 = bridge.restore_vm_from_checkpoint("paused")
    assert vm2.status() == VMStatus.PAUSED_HOST_CALL
    bridge.resume_host_call(vm2, pending["call_id"], "resumed")
    assert vm2.state.stack[-1] == "resumed"


def test_vm_level_reentrancy_guard_blocks_step_while_paused():
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("HALT")], constants=[]))
    vm.halted = False
    vm.state.pending_host_call = {"call_id": "hc-x"}
    with pytest.raises(Exception, match="pending host call"):
        vm.step()


def test_two_phase_snapshot_lock_does_not_deadlock():
    host, bridge, vm = make_bridge_vm("default_agent")
    pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm.host = bridge.get_cvm_callback_adapter(vm)

    result = []

    def worker():
        result.append(bridge.make_vm_snapshot(vm, "threaded"))

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)
    assert not t.is_alive()
    assert result and result[0]["vm_state"]["pending_host_call"]

# ---------------------------------------------------------------------------
# Alpha.3-D1 hardening tests added after architectural review
# ---------------------------------------------------------------------------

from synapse.cvm import OutOfEnergy


def _run_pause_loop_until_ooe(*, replay: bool) -> int:
    """Run a small infinite CALL_HOST loop and count completed host calls."""
    host, bridge, vm = make_bridge_vm("default_agent")
    vm.program = BytecodeProgram(instructions=[
        Instruction("CALL_HOST", "SYS_LLM_EVAL", 0, None),
        Instruction("POP", None, None, None),
        Instruction("JUMP", 0, None, None),
    ], constants=[])
    vm.state.gas_remaining = 40
    vm.host = bridge.get_cvm_callback_adapter(vm)

    # Override adapter target so the nondeterministic call always pauses.
    def pause_callback(opcode, a, b):
        if opcode == "CALL_HOST":
            return {
                "status": VMStatus.PAUSED_HOST_CALL,
                "created_at_event_id": f"evt-live-{len(host.execution_history)}",
                "required_capabilities": sorted(HOST_CAPABILITIES["default_agent"]),
                "determinism_class": "nondeterministic",
            }
        return bridge.vm_host_call(opcode, a, b)

    vm.host = pause_callback
    completed = 0
    try:
        while True:
            vm.step()
            if vm.status() == VMStatus.PAUSED_HOST_CALL:
                pending = vm.state.pending_host_call
                vm.host = bridge.get_cvm_callback_adapter(vm)
                if replay:
                    host.execution_history.append({
                        "type": "host_call_resolved",
                        "call_id": pending["call_id"],
                        "result": f"history-{completed}",
                    })
                    bridge.resume_host_call(vm, pending["call_id"], replay=True)
                else:
                    bridge.resume_host_call(vm, pending["call_id"], f"live-{completed}")
                completed += 1
                vm.host = pause_callback
    except OutOfEnergy:
        return completed


def test_replay_gas_accounting_nondeterministic():
    """Replay from execution_history must still consume the same instruction gas."""
    assert _run_pause_loop_until_ooe(replay=False) == _run_pause_loop_until_ooe(replay=True)


def test_deterministic_pure_gas_at_replay():
    """Deterministic pure builtins consume fixed CALL_HOST gas during replay/recompute."""
    def run_len_loop() -> int:
        vm = CognitiveVM(BytecodeProgram(instructions=[
            Instruction("LOAD_CONST", 0, None, None),
            Instruction("CALL_HOST", "len", 1, None),
            Instruction("POP", None, None, None),
            Instruction("JUMP", 0, None, None),
        ], constants=[[1, 2, 3]]))
        vm.state.gas_remaining = 40
        completed = 0
        try:
            while True:
                before_ip = vm.state.ip
                vm.step()
                if before_ip == 1:  # CALL_HOST len completed
                    completed += 1
        except OutOfEnergy:
            return completed

    assert run_len_loop() == run_len_loop()


def test_deterministic_pure_allowed_during_pause():
    """Bridge-level deterministic pure calls remain allowed while VM has pending state."""
    host, bridge, vm = make_bridge_vm("default_agent")
    pause_vm(vm, required_capabilities=HOST_CAPABILITIES["default_agent"])
    assert vm.status() == VMStatus.PAUSED_HOST_CALL
    assert bridge.dispatch_host_call(vm, "len", [[1, 2, 3]]) == 3
    assert vm.status() == VMStatus.PAUSED_HOST_CALL
    assert vm.state.pending_host_call is not None


def test_transition_hash_updated_after_resume():
    """transition_hash_at_call is identity metadata; resume advances state hash."""
    vm = CognitiveVM(BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 0, None)], constants=[]))
    pending = pause_vm(vm)
    at_call = pending["transition_hash_at_call"]
    vm.resume_host_call(pending["call_id"], "resume-value")
    assert vm.state.transition_hash != at_call
    assert vm.snapshot()["state"]["transition_hash"] == vm.state.transition_hash


def test_function_object_in_args_recursive_validation():
    """FunctionObject args, including nested closures, are recursively validated."""
    host, bridge, vm = make_bridge_vm("default_agent")
    ph = BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 1, None)], constants=[]).program_hash
    inner = FunctionObject(name="inner", params=["y"], body_ip=0, closure={}, program_hash=ph)
    outer = FunctionObject(name="outer", params=["x"], body_ip=0, closure={"inner": inner}, program_hash=ph)
    pending = pause_vm(vm, args=[outer], required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm.host = bridge.get_cvm_callback_adapter(vm)
    bridge.resume_host_call(vm, pending["call_id"], "ok")
    assert vm.state.stack[-1] == "ok"

    host2, bridge2, vm2 = make_bridge_vm("default_agent")
    stale_inner = FunctionObject(name="stale_inner", params=[], body_ip=0, closure={}, program_hash="old")
    current_ph = BytecodeProgram(instructions=[Instruction("CALL_HOST", "SYS_LLM_EVAL", 1, None)], constants=[]).program_hash
    stale_outer = FunctionObject(name="stale_outer", params=[], body_ip=0, closure={"inner": stale_inner}, program_hash=current_ph)
    pending2 = pause_vm(vm2, args=[stale_outer], required_capabilities=HOST_CAPABILITIES["default_agent"])
    vm2.host = bridge2.get_cvm_callback_adapter(vm2)
    with pytest.raises(VMResumeSyncError) as exc:
        bridge2.resume_host_call(vm2, pending2["call_id"], "bad")
    assert exc.value.code == "PENDING_ARG_PROGRAM_HASH_MISMATCH"


def test_golden_replay_backward_compat():
    """Alpha.3-C1 capability golden replay scenarios remain green after dual history split."""
    import tests.test_cvm_capability_golden_replay as golden

    golden.test_capability_denied_continues_golden()
    golden.test_agent_id_override_replay_golden()
    golden.test_legacy_abi_bypass_stable_golden()
