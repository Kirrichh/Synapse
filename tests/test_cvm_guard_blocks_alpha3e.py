"""Alpha3e Track B: Guard Blocks in Bytecode tests.

Covers:
  - GUARD_ENTER pushes GuardFrame onto guard_stack
  - GUARD_CHECK_RESULT reads bool, pushes GUARD_PASS/GUARD_FAIL signal
  - GUARD_EXIT pops frame, sets verdict, writes GUARD_EXIT event
  - GUARD_EXIT(FAIL) sets guard_violation_active=True
  - GUARD_VIOLATION_ACK clears guard_violation_active, writes ACK event
  - Nested guards LIFO order: inner exits before outer
  - _forcibly_close_guard_frames cleans up guard_stack on VMHostError
  - GUARD_FORCIBLY_CLOSED events written on forced close
  - guard_violation_active remains True after GUARD_FORCIBLY_CLOSED
  - Side effects blocked after GUARD_EXIT(FAIL)
  - Side effects resume after GUARD_VIOLATION_ACK
  - Double Fault: violation inside catch block is still blocked
  - Snapshot/restore preserves guard_stack and guard_violation_active
  - guard_cleanup_table affects program_hash
  - Overlapping cleanup ranges for nested guards
  - fail-fast: all BRIDGE_DISPATCHED symbols are classified
"""
from __future__ import annotations

import dataclasses
import hashlib
from unittest.mock import MagicMock

import pytest

from synapse.bytecode import BytecodeProgram, GuardCleanupRange, Instruction, CognitiveCompiler
from synapse.cvm import CognitiveVM, GuardFrame, VMHostError, VMState
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.runtime.vm_bridge import (
    VMBridge,
    BRIDGE_DISPATCHED,
    SIDE_EFFECTING_HOST_SYMBOLS,
    PURE_HOST_SYMBOLS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prog(*instructions, cleanup_table=None):
    """Build a BytecodeProgram from Instruction objects."""
    prog = BytecodeProgram(
        instructions=list(instructions) + [Instruction(op="HALT")],
        constants=[],
        guard_cleanup_table=cleanup_table or [],
    )
    return prog


def _make_host(agent_id="default_agent"):
    h = MagicMock()
    h.current_agent_id = agent_id
    h.execution_history = []
    h.current_trace_id.return_value = "guard-test-trace"
    h._llm_response_cache = {}
    return h


def _make_bridge(host):
    return VMBridge(lambda: host)


def _compile(source: str) -> BytecodeProgram:
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    return CognitiveCompiler().compile(ast)


# ---------------------------------------------------------------------------
# 1. GUARD_ENTER
# ---------------------------------------------------------------------------

def test_guard_enter_pushes_guard_frame():
    prog = _make_prog(Instruction(op="GUARD_ENTER", a="g1", b="phash", c="ghash"))
    vm = CognitiveVM(prog)
    vm.run()
    assert len(vm.state.guard_stack) == 1
    frame = vm.state.guard_stack[0]
    assert isinstance(frame, GuardFrame)
    assert frame.guard_id == "g1"
    assert frame.policy_hash == "phash"
    assert frame.guard_hash == "ghash"
    assert frame.verdict == "PENDING"
    assert frame.entered_at_ip == 0
    assert frame.parent_guard_id is None


def test_guard_enter_sets_parent_guard_id_for_nested():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="outer", b="ph1", c="gh1"),
        Instruction(op="GUARD_ENTER", a="inner", b="ph2", c="gh2"),
    )
    vm = CognitiveVM(prog)
    vm.run()
    assert len(vm.state.guard_stack) == 2
    inner = vm.state.guard_stack[1]
    assert inner.guard_id == "inner"
    assert inner.parent_guard_id == "outer"


# ---------------------------------------------------------------------------
# 2. GUARD_CHECK_RESULT
# ---------------------------------------------------------------------------

def test_guard_check_result_true_pushes_pass():
    prog = _make_prog(
        Instruction(op="LOAD_CONST", a=0),   # True in constants[0]
        Instruction(op="GUARD_CHECK_RESULT"),
    )
    prog.constants = [True]
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.stack[-1] == "GUARD_PASS"


def test_guard_check_result_false_pushes_fail():
    prog = _make_prog(
        Instruction(op="LOAD_CONST", a=0),
        Instruction(op="GUARD_CHECK_RESULT"),
    )
    prog.constants = [False]
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.stack[-1] == "GUARD_FAIL"


# ---------------------------------------------------------------------------
# 3. GUARD_EXIT
# ---------------------------------------------------------------------------

def test_guard_exit_pass_pops_frame_verdict_pass():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g1", b="", c=""),
        Instruction(op="LOAD_CONST", a=0),   # True
        Instruction(op="GUARD_CHECK_RESULT"),
        Instruction(op="GUARD_EXIT"),
    )
    prog.constants = [True]
    vm = CognitiveVM(prog)
    vm.run()
    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is False


def test_guard_exit_fail_sets_violation_active():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g1", b="", c=""),
        Instruction(op="LOAD_CONST", a=0),   # False
        Instruction(op="GUARD_CHECK_RESULT"),
        Instruction(op="GUARD_EXIT"),
    )
    prog.constants = [False]
    vm = CognitiveVM(prog)
    vm.run()
    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is True


def test_guard_exit_writes_guard_verdict_event():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="audit-g1", b="ph", c="gh"),
        Instruction(op="LOAD_CONST", a=0),
        Instruction(op="GUARD_CHECK_RESULT"),
        Instruction(op="GUARD_EXIT"),
    )
    prog.constants = [True]
    vm = CognitiveVM(prog)

    host = _make_host()
    # Inject host reference so GUARD_EXIT can write events
    vm._host = host

    vm.run()
    verdict_events = [e for e in host.execution_history if e.get("type") == "GUARD_EXIT"]
    assert len(verdict_events) == 1
    assert verdict_events[0]["guard_id"] == "audit-g1"
    assert verdict_events[0]["verdict"] == "PASS"
    assert "history_hash" in verdict_events[0]


def test_guard_exit_explicit_fail_verdict():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g2", b="", c=""),
        Instruction(op="GUARD_EXIT", a="FAIL"),   # explicit
    )
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.guard_violation_active is True


# ---------------------------------------------------------------------------
# 4. GUARD_VIOLATION_ACK
# ---------------------------------------------------------------------------

def test_guard_violation_ack_clears_flag():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g1", b="", c=""),
        Instruction(op="GUARD_EXIT", a="FAIL"),
        Instruction(op="GUARD_VIOLATION_ACK"),
    )
    vm = CognitiveVM(prog)
    host = _make_host()
    vm._host = host
    vm.run()
    assert vm.state.guard_violation_active is False

    ack_events = [e for e in host.execution_history
                  if e.get("type") == "GUARD_VIOLATION_ACKNOWLEDGED"]
    assert len(ack_events) == 1


def test_guard_violation_ack_noop_when_no_violation():
    """ACK is idempotent — safe to call when guard_violation_active is False."""
    prog = _make_prog(Instruction(op="GUARD_VIOLATION_ACK"))
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.guard_violation_active is False


# ---------------------------------------------------------------------------
# 5. Nested guards LIFO
# ---------------------------------------------------------------------------

def test_nested_guards_lifo_close_order():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="outer", b="", c=""),
        Instruction(op="GUARD_ENTER", a="inner", b="", c=""),
        Instruction(op="LOAD_CONST", a=0),   # True
        Instruction(op="GUARD_CHECK_RESULT"),
        Instruction(op="GUARD_EXIT"),         # close inner
        Instruction(op="LOAD_CONST", a=1),   # True
        Instruction(op="GUARD_CHECK_RESULT"),
        Instruction(op="GUARD_EXIT"),         # close outer
    )
    prog.constants = [True, True]
    vm = CognitiveVM(prog)
    host = _make_host()
    vm._host = host
    vm.run()

    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is False

    verdict_events = [e for e in host.execution_history if e.get("type") == "GUARD_EXIT"]
    assert len(verdict_events) == 2
    # Inner closes first (LIFO)
    assert verdict_events[0]["guard_id"] == "inner"
    assert verdict_events[1]["guard_id"] == "outer"


# ---------------------------------------------------------------------------
# 6. _forcibly_close_guard_frames
# ---------------------------------------------------------------------------

def test_forcibly_close_guard_frames_via_cleanup_table():
    """GUARD_FORCIBLY_CLOSED emitted for guards in cleanup table range."""
    vm_state = VMState()
    frame = GuardFrame(guard_id="g-fc", entered_at_ip=0)
    vm_state.guard_stack = [frame]
    vm_state.guard_violation_active = False

    prog = BytecodeProgram(
        instructions=[Instruction(op="HALT")],
        guard_cleanup_table=[GuardCleanupRange(start_ip=0, end_ip=10, guard_id="g-fc")],
    )
    vm = CognitiveVM(prog)
    vm.state = vm_state

    host = _make_host()
    vm._host = host

    vm._forcibly_close_guard_frames(at_ip=5)

    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is True

    fc_events = [e for e in host.execution_history
                 if e.get("type") == "GUARD_FORCIBLY_CLOSED"]
    assert len(fc_events) == 1
    assert fc_events[0]["guard_id"] == "g-fc"
    assert fc_events[0]["guard_violation_active_after"] is True


def test_forcibly_close_runtime_fallback_when_no_cleanup_table():
    """Runtime safety fallback: closes all active frames if cleanup table is empty."""
    vm_state = VMState()
    vm_state.guard_stack = [
        GuardFrame(guard_id="g-fb1"),
        GuardFrame(guard_id="g-fb2"),
    ]
    prog = BytecodeProgram(instructions=[Instruction(op="HALT")])
    vm = CognitiveVM(prog)
    vm.state = vm_state
    host = _make_host()
    vm._host = host

    vm._forcibly_close_guard_frames(at_ip=0)

    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is True

    fc_events = [e for e in host.execution_history
                 if e.get("type") == "GUARD_FORCIBLY_CLOSED"]
    assert len(fc_events) == 2


def test_guard_violation_active_persists_after_forcibly_closed():
    """guard_violation_active remains True after GUARD_FORCIBLY_CLOSED.
    Only GUARD_VIOLATION_ACK clears it."""
    vm_state = VMState()
    vm_state.guard_stack = [GuardFrame(guard_id="g-persist")]
    prog = BytecodeProgram(
        instructions=[Instruction(op="HALT")],
        guard_cleanup_table=[GuardCleanupRange(0, 5, "g-persist")],
    )
    vm = CognitiveVM(prog)
    vm.state = vm_state
    vm._host = _make_host()

    vm._forcibly_close_guard_frames(at_ip=2)
    assert vm.state.guard_violation_active is True

    # ACK clears it
    prog2 = BytecodeProgram(instructions=[Instruction(op="GUARD_VIOLATION_ACK"), Instruction(op="HALT")])
    vm2 = CognitiveVM(prog2)
    vm2.state.guard_violation_active = True
    vm2.run()
    assert vm2.state.guard_violation_active is False


# ---------------------------------------------------------------------------
# 7. Side effects blocked / resumed
# ---------------------------------------------------------------------------

def test_side_effects_blocked_after_guard_fail():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g-block", b="", c=""),
        Instruction(op="GUARD_EXIT", a="FAIL"),
    )
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.guard_violation_active is True

    host = _make_host()
    bridge = _make_bridge(host)
    with pytest.raises(VMHostError) as exc_info:
        bridge.dispatch_host_call(vm, "llm.request", [])
    assert exc_info.value.code == "SIDE_EFFECT_BLOCKED_BY_GUARD"


def test_side_effects_resume_after_ack():
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g-resume", b="", c=""),
        Instruction(op="GUARD_EXIT", a="FAIL"),
        Instruction(op="GUARD_VIOLATION_ACK"),
    )
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.guard_violation_active is False

    host = _make_host()
    bridge = _make_bridge(host)
    # LLM request should now proceed (mock mode, no SIDE_EFFECT_BLOCKED)
    try:
        result = bridge.dispatch_host_call(vm, "llm.request", [
            {"type": "prompt_envelope", "template_hash": "t",
             "variables": {}, "variables_hash": "v"},
            "", {"model": "gpt-4o", "model_version": "v1"}, "model_change"
        ])
        assert result is not None
    except VMHostError as e:
        assert e.code != "SIDE_EFFECT_BLOCKED_BY_GUARD", (
            f"Side effects should be allowed after ACK, got {e.code}"
        )


# ---------------------------------------------------------------------------
# 8. Double Fault scenario
# ---------------------------------------------------------------------------

def test_double_fault_violation_still_blocked_without_ack():
    """Catching a GUARD_VIOLATION without ACK keeps side effects blocked."""
    # Guard FAILS → violation active
    # Agent "catches" exception but does NOT call ACK
    # Side-effecting call in catch body → still blocked
    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g-df", b="", c=""),
        Instruction(op="GUARD_EXIT", a="FAIL"),
        # No GUARD_VIOLATION_ACK here — double fault scenario
    )
    vm = CognitiveVM(prog)
    vm.run()
    assert vm.state.guard_violation_active is True

    host = _make_host()
    bridge = _make_bridge(host)
    # Try to call side-effecting host — must fail
    with pytest.raises(VMHostError) as exc_info:
        bridge.dispatch_host_call(vm, "SYS_MEMORY_WRITE", ["room", "data"])
    assert exc_info.value.code == "SIDE_EFFECT_BLOCKED_BY_GUARD"

    # BLOCKED_BY_GUARD event written
    blocked = [e for e in host.execution_history
               if e.get("type") == "SIDE_EFFECT_BLOCKED_BY_GUARD"]
    assert len(blocked) >= 1


# ---------------------------------------------------------------------------
# 9. Snapshot / restore preserves guard state
# ---------------------------------------------------------------------------

def test_snapshot_restore_preserves_guard_stack():
    from synapse.cvm import VMSnapshot
    vm_state = VMState()
    vm_state.guard_stack = [
        GuardFrame(guard_id="g-snap", verdict="PENDING", entered_at_ip=7)
    ]
    vm_state.guard_violation_active = True

    d = vm_state.to_dict()
    vm_state2 = VMState.from_dict(d)

    assert len(vm_state2.guard_stack) == 1
    assert isinstance(vm_state2.guard_stack[0], GuardFrame)
    assert vm_state2.guard_stack[0].guard_id == "g-snap"
    assert vm_state2.guard_stack[0].verdict == "PENDING"
    assert vm_state2.guard_stack[0].entered_at_ip == 7
    assert vm_state2.guard_violation_active is True


def test_snapshot_restore_no_shared_references():
    """Restored snapshot guard frames must be independent objects."""
    vm_state = VMState()
    frame = GuardFrame(guard_id="g-iso", verdict="PENDING")
    vm_state.guard_stack = [frame]

    d = vm_state.to_dict()
    vm_state_restored = VMState.from_dict(d)

    # Mutate the live stack
    vm_state.guard_stack[0] = dataclasses.replace(vm_state.guard_stack[0], verdict="FAIL")
    # Restored snapshot must be unaffected
    assert vm_state_restored.guard_stack[0].verdict == "PENDING"


# ---------------------------------------------------------------------------
# 10. guard_cleanup_table in program_hash
# ---------------------------------------------------------------------------

def test_overlapping_cleanup_ranges_both_in_hash():
    """Nested guards produce overlapping ranges — both enter program_hash."""
    p = BytecodeProgram(
        guard_cleanup_table=[
            GuardCleanupRange(0, 10, "outer"),
            GuardCleanupRange(3, 7, "inner"),
        ]
    )
    p_same = BytecodeProgram(
        guard_cleanup_table=[
            GuardCleanupRange(0, 10, "outer"),
            GuardCleanupRange(3, 7, "inner"),
        ]
    )
    p_diff = BytecodeProgram(
        guard_cleanup_table=[
            GuardCleanupRange(0, 10, "outer"),
            GuardCleanupRange(3, 8, "inner"),   # end_ip differs
        ]
    )
    assert p.program_hash == p_same.program_hash
    assert p.program_hash != p_diff.program_hash


def test_overlapping_ranges_each_guard_forcibly_closed():
    """With overlapping ranges, both outer and inner get GUARD_FORCIBLY_CLOSED."""
    vm_state = VMState()
    vm_state.guard_stack = [
        GuardFrame(guard_id="outer", entered_at_ip=0),
        GuardFrame(guard_id="inner", entered_at_ip=3),
    ]
    prog = BytecodeProgram(
        instructions=[Instruction(op="HALT")],
        guard_cleanup_table=[
            GuardCleanupRange(0, 10, "outer"),
            GuardCleanupRange(3, 7, "inner"),
        ],
    )
    vm = CognitiveVM(prog)
    vm.state = vm_state
    host = _make_host()
    vm._host = host

    # Unwind at ip=5 — both ranges cover it
    vm._forcibly_close_guard_frames(at_ip=5)

    assert len(vm.state.guard_stack) == 0
    assert vm.state.guard_violation_active is True

    fc_events = [e for e in host.execution_history
                 if e.get("type") == "GUARD_FORCIBLY_CLOSED"]
    closed_ids = {e["guard_id"] for e in fc_events}
    assert "outer" in closed_ids
    assert "inner" in closed_ids


# ---------------------------------------------------------------------------
# 11. Fail-fast: all BRIDGE_DISPATCHED symbols classified
# ---------------------------------------------------------------------------

def test_all_bridge_dispatched_symbols_classified():
    """Import-time safety: every registered BRIDGE_DISPATCHED symbol must be
    in SIDE_EFFECTING_HOST_SYMBOLS or PURE_HOST_SYMBOLS."""
    unclassified = []
    for sym in BRIDGE_DISPATCHED:
        normalized = sym.split("@")[0].split("::")[0].strip()
        if normalized not in SIDE_EFFECTING_HOST_SYMBOLS and normalized not in PURE_HOST_SYMBOLS:
            unclassified.append(sym)
    assert unclassified == [], (
        f"Unclassified BRIDGE_DISPATCHED symbols: {unclassified}. "
        "Add to SIDE_EFFECTING_HOST_SYMBOLS or PURE_HOST_SYMBOLS."
    )

# ---------------------------------------------------------------------------
# 16. Track B final cleanup regressions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", ["false", 1, {}, None, [], {"error": False}])
def test_guard_check_result_rejects_non_bool_values(bad_value):
    """GUARD_CHECK_RESULT must be strict bool-only; no Python truthiness."""
    prog = _make_prog(
        Instruction(op="LOAD_CONST", a=0),
        Instruction(op="GUARD_CHECK_RESULT"),
    )
    prog.constants = [bad_value]
    vm = CognitiveVM(prog)
    with pytest.raises(VMHostError) as exc_info:
        vm.run()
    assert exc_info.value.code == "GUARD_RESULT_TYPE_ERROR"
    assert exc_info.value.symbol == "GUARD_CHECK_RESULT"


def test_real_vmhosterror_propagation_forcibly_closes_guard_frames():
    """VMHostError during real VM execution triggers guard cleanup automatically."""
    def failing_host(opcode, a, b):
        raise VMHostError("HOST_FAIL", "forced host failure", "HOST_EVAL")

    prog = _make_prog(
        Instruction(op="GUARD_ENTER", a="g-prop", b="ph", c="gh"),
        Instruction(op="HOST_EVAL", a="unsafe", b=None),
        cleanup_table=[GuardCleanupRange(start_ip=0, end_ip=2, guard_id="g-prop")],
    )
    vm = CognitiveVM(prog, host=failing_host)
    host = _make_host()
    vm._host = host

    with pytest.raises(VMHostError) as exc_info:
        vm.run()

    assert exc_info.value.code == "HOST_FAIL"
    assert vm.state.guard_stack == []
    assert vm.state.guard_violation_active is True
    fc_events = [e for e in host.execution_history if e.get("type") == "GUARD_FORCIBLY_CLOSED"]
    assert len(fc_events) == 1
    assert fc_events[0]["guard_id"] == "g-prop"
    assert fc_events[0]["guard_violation_active_after"] is True


def test_acknowledge_violation_source_call_is_compile_error():
    """GUARD_VIOLATION_ACK is internal-only and cannot be emitted by .syn syntax."""
    from synapse.bytecode import CompileError

    source = "fn main() { acknowledge_violation() }"
    with pytest.raises(CompileError):
        _compile(source)
