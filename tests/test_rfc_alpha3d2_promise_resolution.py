"""RFC coverage tests for Alpha.3-D2 promise resolution amendment.

These tests intentionally validate the architecture contract only. They do not
exercise runtime implementation because Alpha.3-D2 code is gated on RFC review.
"""
from pathlib import Path

RFC_PATH = Path("docs/RFC-CVM-ASYNC-HOST-CALL-LIFECYCLE.md")


def _rfc_text() -> str:
    return RFC_PATH.read_text(encoding="utf-8")


def test_alpha3d2_promise_amendment_sections_present():
    text = _rfc_text()
    required_sections = [
        "# Amendment A: Alpha.3-D2 Bridge-Side Promise Resolution",
        "## A1. Goals & Non-Goals",
        "## A2. Promise Lifecycle States",
        "## A3. Promise Record Schema",
        "## A4. Bridge-Side Promise API",
        "## A5. Actor Runtime Integration",
        "## A6. History Chain Integrity",
        "## A7. Error Propagation",
        "## A8. Multiple Promises Scope Decision",
        "## A9. Security Gates at Resolve",
        "## A10. Replay Semantics",
        "## A11. Timeout and Cancellation Reserved States",
        "## A12. HOST_ABI_VERSION Policy for D2",
        "## A13. D2 Acceptance Checklist",
        "## A14. D2 Implementation Gate",
    ]
    for section in required_sections:
        assert section in text


def test_alpha3d2_scope_preserves_single_pending_vm_invariant():
    text = _rfc_text()
    assert "D2 keeps the D1 **single pending call per VM** invariant" in text
    assert "Multiple concurrent pending calls from the same CVM are reserved for D3" in text
    assert '"pending_host_calls"' in text  # reserved future shape, not D2 implementation


def test_alpha3d2_history_and_replay_are_call_id_bound():
    text = _rfc_text()
    assert "promise_resolved" in text
    assert "promise_rejected" in text
    assert "Replay resolves pending host calls by reading promise resolution events by `call_id`" in text
    assert "Replay must not call the external host provider again" in text


def test_alpha3d2_security_and_abi_policy_are_explicit():
    text = _rfc_text()
    assert "Resolver capabilities must exactly match the promise `required_capabilities`" in text
    assert "Any `FunctionObject` in the result must pass recursive program-hash validation" in text
    assert 'HOST_ABI_VERSION = "2.2.0-alpha3b2"' in text
    assert "does not require an ABI bump" in text
