"""Alpha3g P0.6.19 AS2 runtime wiring hardening tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.agent_snapshot_adapter import AdapterIdentityContextIncompleteError
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    AS2WiringOutcomeKind,
    AS2WiringReasonCode,
    WiringAgentQuarantineRequest,
    WiringBridgeDisabled,
    WiringGateClosed,
    WiringSuccess,
    WiringSystemicDisableRequest,
    process_host_prestage,
)

AS2_BRIDGE_FIXTURE = Path(__file__).parent / "fixtures" / "as2_bridge" / "positive_bridge_minimal_host_prestage.json"


def _payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


@pytest.mark.parametrize(
    ("gate_state", "expected_reason_code"),
    [
        (AS2WiringGateState.DISABLED_BY_DEFAULT, AS2WiringReasonCode.GATE_DISABLED_BY_DEFAULT),
        (AS2WiringGateState.DISABLED_AGENT_QUARANTINE, AS2WiringReasonCode.GATE_DISABLED_QUARANTINE),
        (AS2WiringGateState.DISABLED_SYSTEMIC, AS2WiringReasonCode.GATE_DISABLED_SYSTEMIC),
        (AS2WiringGateState.DISABLED_OPERATOR_OVERRIDE, AS2WiringReasonCode.GATE_DISABLED_OPERATOR),
    ],
)
def test_p0619_gate_closed_matrix_does_not_call_bridge(
    monkeypatch: pytest.MonkeyPatch,
    gate_state: AS2WiringGateState,
    expected_reason_code: AS2WiringReasonCode,
) -> None:
    def _prepare_must_not_be_called(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise AssertionError("gate-closed path must not call bridge preparation")

    monkeypatch.setattr(
        "synapse.runtime.as2_runtime_wiring.prepare_as2_inputs_from_host_prestage",
        _prepare_must_not_be_called,
    )

    outcome = process_host_prestage(
        _payload(),
        gate_state=gate_state,
        correlation_id=f"corr.{gate_state.value}",
    )

    assert isinstance(outcome, WiringGateClosed)
    assert outcome.kind is AS2WiringOutcomeKind.GATE_CLOSED
    assert outcome.current_state is gate_state
    assert outcome.reason_code is expected_reason_code
    assert outcome.correlation_id == f"corr.{gate_state.value}"


def test_p0619_enabled_for_test_with_bridge_disabled_returns_bridge_disabled() -> None:
    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.bridge.disabled",
    )

    assert isinstance(outcome, WiringBridgeDisabled)
    assert outcome.kind is AS2WiringOutcomeKind.BRIDGE_DISABLED
    assert outcome.correlation_id == "corr.bridge.disabled"
    assert outcome.reason_code is AS2WiringReasonCode.BRIDGE_SAFETY_DISABLED


def test_p0619_enabled_for_test_with_valid_payload_preserves_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.success.propagated",
    )

    assert isinstance(outcome, WiringSuccess)
    assert outcome.correlation_id == "corr.success.propagated"
    assert outcome.kind is AS2WiringOutcomeKind.SUCCESS


def test_p0619_missing_correlation_id_generates_root_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
    )

    assert isinstance(outcome, WiringSuccess)
    assert outcome.correlation_id
    assert isinstance(outcome.correlation_id, str)


def test_p0619_agent_scoped_validation_failure_preserves_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload = _payload()
    payload["adapter_identity_context"] = {}

    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="test-corr-001",
    )

    assert isinstance(outcome, WiringAgentQuarantineRequest)
    assert outcome.correlation_id == "test-corr-001"
    assert outcome.agent_id is None
    assert outcome.reason_code is AS2WiringReasonCode.MALFORMED_PAYLOAD_AGENT


def test_p0619_adapter_identity_error_maps_to_missing_identity_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)

    def _raise_identity(**_kwargs: Any) -> None:
        raise AdapterIdentityContextIncompleteError("identity context incomplete")

    monkeypatch.setattr("synapse.runtime.as2_runtime_wiring.validate_as2_inputs", _raise_identity)

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.identity",
    )

    assert isinstance(outcome, WiringAgentQuarantineRequest)
    assert outcome.correlation_id == "corr.identity"
    assert outcome.reason_code is AS2WiringReasonCode.MISSING_IDENTITY_CONTEXT


def test_p0619_missing_capability_grant_maps_to_capability_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload = _payload()
    payload.pop("capability_grant_source")

    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.capability",
    )

    assert isinstance(outcome, WiringAgentQuarantineRequest)
    assert outcome.reason_code is AS2WiringReasonCode.INVALID_CAPABILITY_GRANT


def test_p0619_unexpected_payload_key_maps_to_payload_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload = _payload()
    payload["unexpected_runtime_leak"] = True

    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.classification",
    )

    assert isinstance(outcome, WiringAgentQuarantineRequest)
    assert outcome.reason_code is AS2WiringReasonCode.PAYLOAD_CLASSIFICATION_FAILED


def test_p0619_provider_unavailable_maps_to_systemic_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_provider_unavailable(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise bridge.HostPreStageIOError("provider unavailable")

    monkeypatch.setattr(
        "synapse.runtime.as2_runtime_wiring.prepare_as2_inputs_from_host_prestage",
        _raise_provider_unavailable,
    )

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.provider",
    )

    assert isinstance(outcome, WiringSystemicDisableRequest)
    assert outcome.reason_code is AS2WiringReasonCode.PROVIDER_UNAVAILABLE
    assert outcome.failure_context == {"error_type": "HostPreStageIOError"}


def test_p0619_unexpected_preparation_failure_maps_to_systemic_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_unexpected(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise RuntimeError("unexpected preparation fault")

    monkeypatch.setattr(
        "synapse.runtime.as2_runtime_wiring.prepare_as2_inputs_from_host_prestage",
        _raise_unexpected,
    )

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.unexpected",
    )

    assert isinstance(outcome, WiringSystemicDisableRequest)
    assert outcome.reason_code is AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE
    assert outcome.failure_context == {"error_type": "RuntimeError"}


def test_p0619_reason_code_taxonomy_is_exercised() -> None:
    exercised = {
        AS2WiringReasonCode.GATE_DISABLED_BY_DEFAULT,
        AS2WiringReasonCode.GATE_DISABLED_SYSTEMIC,
        AS2WiringReasonCode.GATE_DISABLED_OPERATOR,
        AS2WiringReasonCode.GATE_DISABLED_QUARANTINE,
        AS2WiringReasonCode.BRIDGE_SAFETY_DISABLED,
        AS2WiringReasonCode.MISSING_IDENTITY_CONTEXT,
        AS2WiringReasonCode.INVALID_CAPABILITY_GRANT,
        AS2WiringReasonCode.PAYLOAD_CLASSIFICATION_FAILED,
        AS2WiringReasonCode.PROVIDER_UNAVAILABLE,
        AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE,
        AS2WiringReasonCode.PROJECTION_COMPLETED,
        AS2WiringReasonCode.PROJECTION_DENIED,
        AS2WiringReasonCode.PROJECTION_DUPLICATE,
        AS2WiringReasonCode.PROJECTION_HANDOFF_NOT_CONFIGURED,
    }
    reserved_for_future_fixtures = {
        AS2WiringReasonCode.VALIDATION_FAILED_AGENT_SCOPE,
        AS2WiringReasonCode.MALFORMED_PAYLOAD_AGENT,
        AS2WiringReasonCode.VALIDATION_FAILED_SYSTEMIC,
    }

    assert exercised | reserved_for_future_fixtures == set(AS2WiringReasonCode)
