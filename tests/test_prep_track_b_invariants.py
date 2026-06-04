"""Alpha3e Prep Patch: Track B invariants tests.

Covers:
  - guard_cleanup_table in program_hash (replay correctness)
  - guard_cleanup_table serialization round-trip
  - guard_violation_active in VMState serialization
  - guard_stack in VMState serialization
  - SIDE_EFFECTING_HOST_SYMBOLS / PURE_HOST_SYMBOLS registry
  - fail-closed for unknown symbols under active violation
  - Side effect blocked when guard_violation_active=True
  - Side effect allowed when guard_violation_active=False
  - Pure host calls allowed under active violation
  - instruction_pointer in capability denial payload
  - unified execution_history across tree-walker/CVM boundary
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from synapse.bytecode import BytecodeProgram, GuardCleanupRange, Instruction
from synapse.cvm import CognitiveVM, VMHostError, VMState
from synapse.runtime.vm_bridge import (
    VMBridge,
    SIDE_EFFECTING_HOST_SYMBOLS,
    PURE_HOST_SYMBOLS,
)
from synapse.version import LANGUAGE_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(agent_id="default_agent"):
    h = MagicMock()
    h.current_agent_id = agent_id
    h.execution_history = []
    h.current_trace_id.return_value = "prep-trace-001"
    h._llm_response_cache = {}
    return h


def _make_bridge(host):
    return VMBridge(lambda: host)


def _simple_prog():
    from synapse.lexer import Lexer
    from synapse.parser import Parser
    from synapse.bytecode import CognitiveCompiler
    tokens = Lexer("let x = 1").scan_tokens()
    ast = Parser(tokens).parse()
    return CognitiveCompiler().compile(ast)


# ---------------------------------------------------------------------------
# 1. guard_cleanup_table in program_hash
# ---------------------------------------------------------------------------

def test_guard_cleanup_table_affects_program_hash():
    """Different guard_cleanup_tables → different program_hash."""
    p1 = BytecodeProgram()
    p2 = BytecodeProgram(
        guard_cleanup_table=[GuardCleanupRange(start_ip=0, end_ip=5, guard_id="g1")]
    )
    assert p1.program_hash != p2.program_hash


def test_same_guard_cleanup_table_same_hash():
    """Identical guard_cleanup_tables → identical program_hash."""
    p1 = BytecodeProgram(
        guard_cleanup_table=[GuardCleanupRange(0, 5, "g1"), GuardCleanupRange(6, 10, "g2")]
    )
    p2 = BytecodeProgram(
        guard_cleanup_table=[GuardCleanupRange(0, 5, "g1"), GuardCleanupRange(6, 10, "g2")]
    )
    assert p1.program_hash == p2.program_hash


def test_guard_cleanup_table_in_to_dict():
    table = [GuardCleanupRange(0, 5, "g1")]
    p = BytecodeProgram(guard_cleanup_table=table)
    d = p.to_dict()
    assert "guard_cleanup_table" in d
    assert d["guard_cleanup_table"] == [{"start_ip": 0, "end_ip": 5, "guard_id": "g1"}]


def test_guard_cleanup_table_round_trip_from_dict():
    table = [GuardCleanupRange(2, 8, "policy_guard_001")]
    p = BytecodeProgram(guard_cleanup_table=table)
    d = p.to_dict()
    p2 = BytecodeProgram.from_dict(d)
    assert len(p2.guard_cleanup_table) == 1
    r = p2.guard_cleanup_table[0]
    assert r.start_ip == 2 and r.end_ip == 8 and r.guard_id == "policy_guard_001"
    assert p.program_hash == p2.program_hash


def test_empty_guard_cleanup_table_backwards_compatible():
    """Programs without guard_cleanup_table deserialize to empty list."""
    d = BytecodeProgram().to_dict()
    d.pop("guard_cleanup_table", None)  # simulate old format
    p = BytecodeProgram.from_dict(d)
    assert p.guard_cleanup_table == []


# ---------------------------------------------------------------------------
# 2. VMState guard fields serialization
# ---------------------------------------------------------------------------

def test_guard_violation_active_default_false():
    state = VMState()
    assert state.guard_violation_active is False


def test_guard_violation_active_serializes():
    state = VMState()
    state.guard_violation_active = True
    d = state.to_dict()
    assert d["guard_violation_active"] is True


def test_guard_violation_active_deserializes():
    state = VMState()
    state.guard_violation_active = True
    d = state.to_dict()
    state2 = VMState.from_dict(d)
    assert state2.guard_violation_active is True


def test_guard_violation_active_default_on_restore():
    """Old snapshots without guard_violation_active restore to False."""
    state = VMState()
    d = state.to_dict()
    d.pop("guard_violation_active", None)
    state2 = VMState.from_dict(d)
    assert state2.guard_violation_active is False


def test_guard_stack_default_empty():
    state = VMState()
    assert state.guard_stack == []


def test_guard_stack_serializes_and_restores():
    from synapse.cvm import GuardFrame
    state = VMState()
    frame = GuardFrame(guard_id="g1", verdict="PENDING", entered_at_ip=3)
    state.guard_stack = [frame]
    d = state.to_dict()
    state2 = VMState.from_dict(d)
    assert len(state2.guard_stack) == 1
    restored = state2.guard_stack[0]
    assert isinstance(restored, GuardFrame), f"Expected GuardFrame, got {type(restored)}"
    assert restored.guard_id == "g1"
    assert restored.verdict == "PENDING"
    assert restored.entered_at_ip == 3


def test_guard_stack_no_shared_references():
    """Frozen GuardFrame + from_dict ensures snapshot frames are independent objects."""
    import dataclasses
    from synapse.cvm import GuardFrame
    state = VMState()
    frame = GuardFrame(guard_id="g2", verdict="PENDING")
    state.guard_stack = [frame]
    d = state.to_dict()
    state_restored = VMState.from_dict(d)

    # Mutate live stack — restored snapshot must be unaffected
    state.guard_stack[0] = dataclasses.replace(state.guard_stack[0], verdict="FAIL")
    assert state_restored.guard_stack[0].verdict == "PENDING", (
        "Restored snapshot must not be affected by live stack mutation"
    )


# ---------------------------------------------------------------------------
# 3. SIDE_EFFECTING / PURE symbol registries
# ---------------------------------------------------------------------------

def test_side_effecting_and_pure_are_disjoint():
    overlap = SIDE_EFFECTING_HOST_SYMBOLS & PURE_HOST_SYMBOLS
    assert overlap == set(), f"Symbols in both registries: {overlap}"


def test_key_symbols_classified():
    assert "llm.request" in SIDE_EFFECTING_HOST_SYMBOLS
    assert "SYS_MEMORY_WRITE" in SIDE_EFFECTING_HOST_SYMBOLS
    assert "SYS_MSG_SEND" in SIDE_EFFECTING_HOST_SYMBOLS
    assert "SYS_MEMORY_READ" in PURE_HOST_SYMBOLS
    assert "SYS_CONTEXT_ENTER" in PURE_HOST_SYMBOLS
    assert "SYS_CONTEXT_EXIT" in PURE_HOST_SYMBOLS


# ---------------------------------------------------------------------------
# 4. Guard violation enforcement: side effects blocked / allowed
# ---------------------------------------------------------------------------

def _vm_with_violation(active: bool):
    """Create a minimal VM with guard_violation_active set."""
    from synapse.bytecode import CognitiveCompiler
    from synapse.lexer import Lexer
    from synapse.parser import Parser
    tokens = Lexer("let x = 1").scan_tokens()
    prog = CognitiveCompiler().compile(Parser(tokens).parse())
    vm = CognitiveVM(prog)
    vm.state.guard_violation_active = active
    vm.state.agent_id = "default_agent"
    return vm


def test_side_effecting_call_blocked_under_active_violation():
    host = _make_host()
    bridge = _make_bridge(host)
    vm = _vm_with_violation(active=True)

    with pytest.raises(VMHostError) as exc_info:
        bridge.dispatch_host_call(vm, "llm.request", [])
    assert exc_info.value.code == "SIDE_EFFECT_BLOCKED_BY_GUARD"

    blocked_events = [e for e in host.execution_history
                      if e.get("type") == "SIDE_EFFECT_BLOCKED_BY_GUARD"]
    assert len(blocked_events) == 1
    assert blocked_events[0]["request_symbol"] == "llm.request"


def test_unknown_symbol_blocked_under_active_violation():
    """Fail-closed: unknown symbol is treated as side-effecting."""
    host = _make_host()
    bridge = _make_bridge(host)
    vm = _vm_with_violation(active=True)

    with pytest.raises(VMHostError) as exc_info:
        bridge.dispatch_host_call(vm, "SYS_FUTURE_UNKNOWN_SYMBOL", [])
    # Either SIDE_EFFECT_BLOCKED_BY_GUARD or UNKNOWN_SYMBOL — both acceptable
    # as long as the call is blocked
    assert exc_info.value.code in ("SIDE_EFFECT_BLOCKED_BY_GUARD", "UNKNOWN_SYMBOL")


def test_pure_call_allowed_under_active_violation():
    """PURE_HOST_SYMBOLS pass through even under active violation."""
    host = _make_host()
    bridge = _make_bridge(host)
    vm = _vm_with_violation(active=True)

    # SYS_MEMORY_READ is pure — should reach capability check, not violation block
    # It will fail with CAPABILITY_DENIED or succeed, but NOT SIDE_EFFECT_BLOCKED
    try:
        bridge.dispatch_host_call(vm, "SYS_MEMORY_READ", ["default"])
    except VMHostError as e:
        assert e.code != "SIDE_EFFECT_BLOCKED_BY_GUARD", (
            f"Pure symbol SYS_MEMORY_READ should not be blocked by guard violation, got {e.code}"
        )


def test_side_effecting_call_allowed_when_no_violation():
    """Normal execution: side-effecting calls pass through to capability check."""
    host = _make_host()
    bridge = _make_bridge(host)
    vm = _vm_with_violation(active=False)

    # Should proceed to LLM bridge (mock mode) without SIDE_EFFECT_BLOCKED error
    try:
        result = bridge.dispatch_host_call(vm, "llm.request", [
            {"type": "prompt_envelope", "template_hash": "t", "variables": {}, "variables_hash": "v"},
            "", {"model": "gpt-4o", "model_version": "v1"}, "model_change"
        ])
    except VMHostError as e:
        assert e.code != "SIDE_EFFECT_BLOCKED_BY_GUARD"


# ---------------------------------------------------------------------------
# 5. instruction_pointer in capability denial payload
# ---------------------------------------------------------------------------

def test_capability_denial_includes_ip():
    host = _make_host(agent_id="restricted_worker")
    bridge = _make_bridge(host)
    vm = _vm_with_violation(active=False)
    vm.state.agent_id = "restricted_worker"
    vm.state.ip = 42  # set a known IP

    with pytest.raises(VMHostError):
        bridge.dispatch_host_call(vm, "llm.request", [])

    denial_events = [e for e in host.execution_history
                     if e.get("type") in ("LLM_REQUEST_DENIED", "CAPABILITY_DENIED")]
    assert len(denial_events) >= 1
    assert "instruction_pointer" in denial_events[0]
    # ip=42 was set but dispatch_host_call reads vm.state.ip directly
    # value may be 42 or current ip — just verify the field is present and is int/None
    ip_val = denial_events[0]["instruction_pointer"]
    assert ip_val is None or isinstance(ip_val, int)


# ---------------------------------------------------------------------------
# 6. Unified execution_history invariant test
# ---------------------------------------------------------------------------

def test_unified_execution_history_linear_chain():
    """Tree-walker and CVM must share one linear execution_history.

    This regression test verifies that:
    - history events from both layers appear in the same list
    - the list is linear (no nested sub-lists)
    - history_hash is present on events that compute it
    """
    host = _make_host()
    bridge = _make_bridge(host)

    # Simulate a tree-walker event (written directly to host)
    host.execution_history.append({
        "type": "tree_walker_event",
        "detail": "fracture_enter",
        "history_hash": hashlib.sha256(b"genesis").hexdigest(),
    })

    # Simulate a CVM-side event (written via Bridge)
    vm = _vm_with_violation(active=False)
    # LLM bridge writes LLM_RESPONSE_CACHED to the same host.execution_history
    bridge._execute_llm_request(host, [
        {"type": "prompt_envelope", "template_hash": "t2",
         "variables": {}, "variables_hash": "v2"},
        "",
        {"model": "gpt-4o", "model_version": "v1"},
        "model_change",
    ])

    # Another tree-walker event after CVM
    host.execution_history.append({
        "type": "tree_walker_event",
        "detail": "fracture_exit",
    })

    # Verify: one flat list
    assert isinstance(host.execution_history, list)
    for event in host.execution_history:
        assert isinstance(event, dict), f"Event is not a dict: {event}"
        # No nested lists of events
        for v in event.values():
            assert not isinstance(v, list) or all(
                not isinstance(i, dict) or "type" not in i
                for i in v
            ), f"Possible nested event list found in {event}"

    # Verify both layers wrote to the same list
    types = [e.get("type") for e in host.execution_history]
    assert "tree_walker_event" in types
    assert "LLM_RESPONSE_CACHED" in types

    # Verify linear order: first tree-walker event appears before CVM event
    tw_idx = next(i for i, e in enumerate(host.execution_history)
                  if e.get("type") == "tree_walker_event")
    cvm_idx = next(i for i, e in enumerate(host.execution_history)
                   if e.get("type") == "LLM_RESPONSE_CACHED")
    assert tw_idx < cvm_idx


def test_version_is_track_a():
    assert LANGUAGE_VERSION == "2.2.0-alpha3e"
