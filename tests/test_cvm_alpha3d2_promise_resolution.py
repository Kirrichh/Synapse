"""Alpha.3-D2: bridge-side promise resolution and history-bound replay."""
from types import SimpleNamespace

import pytest

from synapse.bytecode import BytecodeProgram
from synapse.cvm import CognitiveVM, VMState, VMResumeSyncError
from synapse.runtime.actor_runtime import ActorRuntime
from synapse.runtime.host_abi import HOST_ABI_VERSION
from synapse.runtime.replay_engine import ReplayEngine
from synapse.runtime.vm_bridge import VMBridge, HOST_CAPABILITIES
from synapse.hardening import hash_event_chain, verify_event_chain


class FakeHost:
    def __init__(self, agent_id="default_agent"):
        self.current_agent_id = agent_id
        self.trust_snapshot_agent_id = False
        self.execution_history = []
        self.side_effect_history = []
        self.promises = {}
        self.spawned_actors = {}
        self.mailboxes = {}
        self.actor_log = []
        self.node_id = "local"
        self.history_chain_seed = "test-seed"
        self.runtime_mode = "live"
        self.replay_cursor = 0
        self.runtime = SimpleNamespace(actor=ActorRuntime(lambda: self, "live", "replay"))

    def current_trace_id(self):
        return "trace-test"


def make_paused_vm(call_id="hc-test", *, agent_id="default_agent", required=None):
    program = BytecodeProgram(instructions=[], constants=[])
    required = list(required if required is not None else HOST_CAPABILITIES[agent_id])
    state = VMState(
        ip=1,
        gas_remaining=100,
        agent_id=agent_id,
        pending_host_call={
            "pending_schema_version": "1",
            "status": "STATUS_PAUSED_HOST_CALL",
            "call_id": call_id,
            "symbol": "SYS_LLM_EVAL",
            "args": ["prompt"],
            "argc": 1,
            "ip_after_call": 1,
            "program_hash": program.program_hash,
            "transition_hash_at_call": "sha256:call",
            "frame_depth_at_call": 0,
            "agent_id": agent_id,
            "required_capabilities": required,
            "host_abi_version": HOST_ABI_VERSION,
            "created_at_event_id": "evt--0000001",
            "determinism_class": "nondeterministic",
        },
    )
    vm = CognitiveVM(program, state=state)
    vm.halted = True
    return vm


def make_bridge(host=None):
    host = host or FakeHost()
    return VMBridge(host_getter=lambda: host), host


def test_promise_resolve_pushes_value_and_wakes_actor():
    bridge, host = make_bridge()
    vm = make_paused_vm("hc-resolve")

    promise = bridge.create_promise("hc-resolve", vm=vm, actor_id="actor-1")
    assert promise.record.status == "PENDING"
    assert host.spawned_actors["actor-1"]["status"] == "suspended"

    record = promise.resolve({"ok": True})

    assert record.status == "RESOLVED"
    assert vm.state.pending_host_call is None
    assert vm.state.stack[-1] == {"ok": True}
    assert host.spawned_actors["actor-1"]["status"] == "running"
    assert host.mailboxes["actor-1"][-1]["type"] == "promise_resolved"
    assert any(e["type"] == "promise_created" for e in host.execution_history)
    assert any(e["type"] == "promise_resolved" and e["call_id"] == "hc-resolve" for e in host.execution_history)
    assert any(e["type"] == "host_call_resolved" and e["call_id"] == "hc-resolve" for e in host.execution_history)


def test_promise_reject_pushes_json_safe_error_and_wakes_actor():
    bridge, host = make_bridge()
    vm = make_paused_vm("hc-reject")
    promise = bridge.create_promise("hc-reject", vm=vm, actor_id="actor-2")

    record = promise.reject({"code": "LLM_TIMEOUT", "message": "timeout"})

    assert record.status == "REJECTED"
    top = vm.state.stack[-1]
    assert top == {
        "type": "PromiseRejection",
        "code": "LLM_TIMEOUT",
        "message": "timeout",
        "call_id": "hc-reject",
    }
    assert host.mailboxes["actor-2"][-1]["type"] == "promise_rejected"


def test_double_resolve_fails_closed():
    bridge, _ = make_bridge()
    vm = make_paused_vm("hc-double")
    promise = bridge.create_promise("hc-double", vm=vm)
    promise.resolve("first")

    with pytest.raises(VMResumeSyncError) as exc:
        promise.resolve("second")
    assert exc.value.code == "PROMISE_ALREADY_RESOLVED"


def test_cancel_is_reserved_for_future_scope():
    bridge, _ = make_bridge()
    vm = make_paused_vm("hc-cancel")
    promise = bridge.create_promise("hc-cancel", vm=vm)

    with pytest.raises(VMResumeSyncError) as exc:
        promise.cancel()
    assert exc.value.code == "PROMISE_CANCEL_RESERVED"


def test_security_gate_rejects_wrong_call_id():
    bridge, _ = make_bridge()
    vm = make_paused_vm("hc-real")

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.create_promise("hc-other", vm=vm)
    assert exc.value.code == "PROMISE_CALL_ID_MISMATCH"


def test_security_gate_rejects_agent_mismatch_without_trust():
    bridge, host = make_bridge(FakeHost(agent_id="restricted_worker"))
    vm = make_paused_vm("hc-agent", agent_id="default_agent")
    promise = bridge.create_promise("hc-agent", vm=vm)

    with pytest.raises(VMResumeSyncError) as exc:
        promise.resolve("value")
    assert exc.value.code == "PROMISE_AGENT_ID_MISMATCH"

    # trust_snapshot_agent_id does not bypass exact capability matching; that
    # behavior is covered separately by the capability mismatch test.


def test_security_gate_rejects_capability_exact_mismatch():
    bridge, _ = make_bridge()
    vm = make_paused_vm("hc-cap", required={"memory.read"})
    promise = bridge.create_promise("hc-cap", vm=vm)

    with pytest.raises(VMResumeSyncError) as exc:
        promise.resolve("value")
    assert exc.value.code == "PROMISE_CAPABILITY_EXACT_MATCH_FAILED"


def test_history_bound_replay_lookup_by_call_id():
    bridge, host = make_bridge()
    vm = make_paused_vm("hc-replay")
    promise = bridge.create_promise("hc-replay", vm=vm)
    promise.resolve("answer")

    assert bridge.promise_result_from_history("hc-replay") == "answer"

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.promise_result_from_history("missing")
    assert exc.value.code == "PROMISE_REPLAY_MISSING"

    host.execution_history.append({"type": "promise_resolved", "call_id": "hc-replay", "result": "duplicate"})
    with pytest.raises(VMResumeSyncError) as exc:
        bridge.promise_result_from_history("hc-replay")
    assert exc.value.code == "PROMISE_REPLAY_DUPLICATE"


def test_replay_engine_promise_result_by_call_id_and_hash_chain():
    history = [
        {"type": "promise_created", "call_id": "hc-r", "promise_id": "hc-r"},
        {"type": "promise_resolved", "call_id": "hc-r", "promise_id": "hc-r", "result": {"ok": 1}},
    ]
    cursor = {"value": 0}
    mode = {"value": "live"}
    engine = ReplayEngine(
        history_getter=lambda: history,
        runtime_mode_getter=lambda: mode["value"],
        runtime_mode_setter=lambda value: mode.__setitem__("value", value),
        replay_cursor_getter=lambda: cursor["value"],
        replay_cursor_setter=lambda value: cursor.__setitem__("value", value),
        live_mode="live",
        replay_mode="replay",
        builtins_registry={},
        hash_event_chain_fn=hash_event_chain,
        verify_event_chain_fn=verify_event_chain,
        history_chain_seed_getter=lambda: "seed",
    )

    assert engine.promise_result_by_call_id("hc-r") == {"ok": 1}
    chain = engine.history_hash_chain()
    assert chain and chain[-1]["hash"]


def test_d2_preserves_single_pending_call_invariant():
    bridge, _ = make_bridge()
    vm = make_paused_vm("hc-single")
    p1 = bridge.create_promise("hc-single", vm=vm)
    p2 = bridge.create_promise("hc-single", vm=vm)

    assert p1.call_id == p2.call_id == "hc-single"
    assert len([k for k in bridge.get_host().promises if k == "hc-single"]) == 1


def test_promise_result_replay_missing_after_history_tamper():
    bridge, host = make_bridge()
    vm = make_paused_vm("hc-tamper")
    promise = bridge.create_promise("hc-tamper", vm=vm)
    promise.resolve("stable")
    host.execution_history = [e for e in host.execution_history if e.get("type") != "promise_resolved"]

    with pytest.raises(VMResumeSyncError) as exc:
        bridge.promise_result_from_history("hc-tamper")
    assert exc.value.code == "PROMISE_REPLAY_MISSING"
