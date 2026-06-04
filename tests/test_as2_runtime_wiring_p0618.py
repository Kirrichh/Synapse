"""Alpha3g P0.6.18 AS2 Runtime Wiring Skeleton tests."""
from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Mapping

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateEvaluator,
    AS2WiringGateState,
    WiringAgentQuarantineRequest,
    WiringGateClosed,
    WiringBridgeDisabled,
    WiringSuccess,
    WiringSystemicDisableRequest,
    process_host_prestage,
)

AS2_BRIDGE_FIXTURE = Path(__file__).parent / "fixtures" / "as2_bridge" / "positive_bridge_minimal_host_prestage.json"


def _bridge_fixture_payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def test_p0618_gate_evaluator_opens_only_for_enabled_for_test():
    assert AS2WiringGateEvaluator(AS2WiringGateState.ENABLED_FOR_TEST).is_open_for_test()

    closed_states = {
        AS2WiringGateState.DISABLED_BY_DEFAULT,
        AS2WiringGateState.DISABLED_AGENT_QUARANTINE,
        AS2WiringGateState.DISABLED_SYSTEMIC,
        AS2WiringGateState.DISABLED_OPERATOR_OVERRIDE,
    }
    for state in closed_states:
        assert not AS2WiringGateEvaluator(state).is_open_for_test()


def test_p0618_gate_state_has_no_production_enabled_state():
    assert "ENABLED" not in AS2WiringGateState.__members__


def test_p0618_gate_closed_returns_outcome_without_calling_bridge(monkeypatch):
    def _prepare_must_not_be_called(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise AssertionError("gate-closed skeleton must not prepare payload")

    monkeypatch.setattr(
        "synapse.runtime.as2_runtime_wiring.prepare_as2_inputs_from_host_prestage",
        _prepare_must_not_be_called,
    )

    outcome = process_host_prestage(
        _bridge_fixture_payload(),
        gate_state=AS2WiringGateState.DISABLED_BY_DEFAULT,
        correlation_id="corr.gate.closed",
    )

    assert isinstance(outcome, WiringGateClosed)
    assert outcome.correlation_id == "corr.gate.closed"
    assert outcome.current_state == AS2WiringGateState.DISABLED_BY_DEFAULT


def test_p0618_enabled_for_test_valid_payload_returns_success(monkeypatch):
    _enable_bridge(monkeypatch)

    outcome = process_host_prestage(
        _bridge_fixture_payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.success",
    )

    assert isinstance(outcome, WiringSuccess)
    assert outcome.correlation_id == "corr.success"
    assert isinstance(outcome.prepared_inputs, bridge.PreparedAS2Inputs)
    assert outcome.prepared_inputs.model_selection_source.model == "mock-agent-model"


def test_p0618_success_outcome_requires_prepared_inputs():
    with pytest.raises(ValueError, match="PreparedAS2Inputs"):
        WiringSuccess(correlation_id="corr.invalid")


def test_p0618_outcomes_are_immutable(monkeypatch):
    _enable_bridge(monkeypatch)
    outcome = process_host_prestage(
        _bridge_fixture_payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.immutable",
    )

    with pytest.raises(FrozenInstanceError):
        outcome.correlation_id = "changed"  # type: ignore[misc]


def test_p0618_single_agent_bad_payload_returns_quarantine_request(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _bridge_fixture_payload()
    payload["adapter_identity_context"] = {}

    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.quarantine",
    )

    assert isinstance(outcome, WiringAgentQuarantineRequest)
    assert outcome.correlation_id == "corr.quarantine"
    assert outcome.agent_id is None
    assert outcome.reason_code.value == "malformed_payload_agent"


def test_p0618_bridge_disabled_returns_bridge_disabled_outcome():
    outcome = process_host_prestage(
        _bridge_fixture_payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.bridge.disabled",
    )

    assert isinstance(outcome, WiringBridgeDisabled)
    assert outcome.correlation_id == "corr.bridge.disabled"
    assert outcome.reason_code.value == "bridge_safety_disabled"


def test_p0618_systemic_provider_failure_returns_disable_request(monkeypatch):
    def _raise_systemic(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise bridge.HostPreStageIOError("provider unavailable")

    monkeypatch.setattr(
        "synapse.runtime.as2_runtime_wiring.prepare_as2_inputs_from_host_prestage",
        _raise_systemic,
    )

    outcome = process_host_prestage(
        _bridge_fixture_payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.systemic",
    )

    assert isinstance(outcome, WiringSystemicDisableRequest)
    assert outcome.reason_code.value == "provider_unavailable"
    assert outcome.failure_context == {"error_type": "HostPreStageIOError"}
