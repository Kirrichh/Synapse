"""Alpha.3-C1 structured fallback audit metric tests."""
from synapse.metrics import SynapseMetrics
from synapse.runtime.vm_bridge import VMBridge
from synapse.runtime.vm_routing import fallback_reason_for, fallback_audit_from_events


class FakeNode:
    line = 14
    col = 4


class AffectiveEventStmt(FakeNode):
    pass


class DreamStmt(FakeNode):
    pass


class FakeHost:
    def __init__(self):
        self.execution_history = []
        self.current_program_hash = "sha256:test-program"

    def current_trace_id(self):
        return "trace-fallback"


def test_fallback_reason_for_is_structured():
    reason = fallback_reason_for("AffectiveEventStmt")
    assert reason["code"] == "COMPILER_UNSUPPORTED_AFFECTIVE"
    assert "detail" in reason

    fallback = fallback_reason_for("UnknownNode")
    assert fallback["code"] == "COMPILER_NO_HANDLER"


def test_vmbridge_log_fallback_emits_structured_event():
    host = FakeHost()
    bridge = VMBridge(host_getter=lambda: host)

    bridge.log_fallback(AffectiveEventStmt(), compiler_phase="compile_stmt")

    event = host.execution_history[-1]
    assert event["type"] == "vm_fallback"
    assert event["ast_node_type"] == "AffectiveEventStmt"
    assert event["node_location"] == {"line": 14, "col": 4}
    assert event["compiler_phase"] == "compile_stmt"
    assert event["fallback_reason"]["code"] == "COMPILER_UNSUPPORTED_AFFECTIVE"
    assert event["program_hash"] == "sha256:test-program"


def test_fallback_audit_aggregates_by_node_and_reason():
    events = [
        {"type": "vm_fallback", "ast_node_type": "AffectiveEventStmt", "fallback_reason": {"code": "COMPILER_UNSUPPORTED_AFFECTIVE"}},
        {"type": "vm_fallback", "ast_node_type": "AffectiveEventStmt", "fallback_reason": {"code": "COMPILER_UNSUPPORTED_AFFECTIVE"}},
        {"type": "vm_fallback", "ast_node_type": "DreamStmt", "fallback_reason": {"code": "COMPILER_UNSUPPORTED_DREAM"}},
    ]
    audit = fallback_audit_from_events(events)
    assert audit["vm_fallbacks_total"] == 3
    assert audit["vm_fallback_by_node_type"] == {"AffectiveEventStmt": 2, "DreamStmt": 1}
    assert audit["vm_fallback_by_reason"] == {"COMPILER_UNSUPPORTED_AFFECTIVE": 2, "COMPILER_UNSUPPORTED_DREAM": 1}


def test_metrics_snapshot_exposes_fallback_audit():
    host = FakeHost()
    host.execution_history = [
        {"type": "vm_fallback", "ast_node_type": "AffectiveEventStmt", "fallback_reason": {"code": "COMPILER_UNSUPPORTED_AFFECTIVE"}},
        {"type": "vm_fallback", "ast_node_type": "DreamStmt", "fallback_reason": {"code": "COMPILER_UNSUPPORTED_DREAM"}},
    ]
    metrics = SynapseMetrics(host).snapshot()
    assert metrics["vm_fallbacks_total"] == 2
    assert metrics["vm_fallback_by_node_type"]["AffectiveEventStmt"] == 1
    assert metrics["vm_fallback_by_reason"]["COMPILER_UNSUPPORTED_DREAM"] == 1
