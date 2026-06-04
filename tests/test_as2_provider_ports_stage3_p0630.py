"""P0.6.30 Stage 3 Provider Fakes + Routing Tests."""
from __future__ import annotations

from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecisionKind,
    AS2ProviderFailureReasonCode,
)
from synapse.runtime.as2_provider_ports import HostProviderRequestContext, ProviderFailure, ProviderReasonCode, ProviderSuccess
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState
from tests.support.as2_provider_fakes import FakeCapabilityGrantProvider, FakeMemoryReferenceProvider
from tests.support.as2_provider_routing import route_provider_failure_to_controller, safe_map_provider_reason


def _context() -> HostProviderRequestContext:
    return HostProviderRequestContext(
        correlation_id="corr.p0630.provider",
        request_id="req.p0630.provider",
        timestamp="2026-06-02T00:00:00Z",
    )


def test_p0630_memory_reference_provider_success() -> None:
    provider = FakeMemoryReferenceProvider(
        memory_by_agent={
            "agent-42": {
                "type": "memory_ref_source",
                "schema_version": "alpha3g.memory_ref_source.v1",
                "profile": "stable-canonical.v1",
                "expected_memory_space_id": "sha256:" + "3" * 64,
                "memory_space_policy_version": "alpha3g.memory_space_policy.v1",
                "refs": [
                    {
                        "type": "memory_ref",
                        "schema_version": "alpha3g.memory_ref.v1",
                        "profile": "stable-canonical.v1",
                        "memory_space_id": "sha256:" + "3" * 64,
                        "memory_key": "episodic/1",
                        "access_mode": "read",
                    }
                ],
            }
        }
    )

    outcome = provider.get_memory_ref_source(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["refs"][0]["memory_key"] == "episodic/1"


def test_p0630_memory_reference_provider_empty_refs_are_success_not_not_found() -> None:
    outcome = FakeMemoryReferenceProvider().get_memory_ref_source(_context(), agent_id="agent-empty")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["refs"] == []


def test_p0630_memory_reference_provider_missing_context() -> None:
    outcome = FakeMemoryReferenceProvider().get_memory_ref_source(
        HostProviderRequestContext(correlation_id="", request_id=""), agent_id="agent-42"
    )

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.MISSING_REQUEST_CONTEXT


def test_p0630_memory_reference_provider_invalid_input_not_found_and_cancelled() -> None:
    provider = FakeMemoryReferenceProvider(
        invalid_input_agents=frozenset({"agent-invalid"}),
        not_found_agents=frozenset({"agent-missing"}),
        cancelled_agents=frozenset({"agent-cancelled"}),
    )

    invalid = provider.get_memory_ref_source(_context(), agent_id="agent-invalid")
    missing = provider.get_memory_ref_source(_context(), agent_id="agent-missing")
    cancelled = provider.get_memory_ref_source(_context(), agent_id="agent-cancelled")

    assert isinstance(invalid, ProviderFailure)
    assert invalid.reason_code is ProviderReasonCode.INVALID_INPUT
    assert isinstance(missing, ProviderFailure)
    assert missing.reason_code is ProviderReasonCode.NOT_FOUND
    assert isinstance(cancelled, ProviderFailure)
    assert cancelled.reason_code is ProviderReasonCode.CANCELLED


def test_p0630_capability_grant_provider_success() -> None:
    provider = FakeCapabilityGrantProvider(
        grants_by_agent={
            "agent-42": {
                "type": "capability_grant_source",
                "schema_version": "alpha3g.capability_grant_source.v1",
                "profile": "stable-canonical.v1",
                "grants": [
                    {
                        "type": "capability_grant",
                        "schema_version": "alpha3g.capability_grant.v1",
                        "profile": "stable-canonical.v1",
                        "tool_namespace": "fixture.echo",
                        "function_descriptor_ref": {
                            "namespace": "fixture.tools",
                            "symbol": "echo",
                            "input_schema_hash": "sha256:" + "e" * 64,
                            "output_schema_hash": "sha256:" + "e" * 64,
                        },
                        "effect_policy_hash": "sha256:" + "9" * 64,
                        "policy_ref": "policy:fixture.echo.v1",
                    }
                ],
            }
        }
    )

    outcome = provider.get_capability_grant_source(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["grants"][0]["tool_namespace"] == "fixture.echo"


def test_p0630_capability_grant_provider_empty_grants_are_success_not_not_found() -> None:
    outcome = FakeCapabilityGrantProvider().get_capability_grant_source(_context(), agent_id="agent-empty")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["grants"] == []


def test_p0630_capability_grant_provider_missing_context() -> None:
    outcome = FakeCapabilityGrantProvider().get_capability_grant_source(
        HostProviderRequestContext(correlation_id="", request_id=""), agent_id="agent-42"
    )

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.MISSING_REQUEST_CONTEXT


def test_p0630_capability_grant_provider_invalid_input_not_found_and_cancelled() -> None:
    provider = FakeCapabilityGrantProvider(
        invalid_input_agents=frozenset({"agent-invalid"}),
        not_found_agents=frozenset({"agent-missing"}),
        cancelled_agents=frozenset({"agent-cancelled"}),
    )

    invalid = provider.get_capability_grant_source(_context(), agent_id="agent-invalid")
    missing = provider.get_capability_grant_source(_context(), agent_id="agent-missing")
    cancelled = provider.get_capability_grant_source(_context(), agent_id="agent-cancelled")

    assert isinstance(invalid, ProviderFailure)
    assert invalid.reason_code is ProviderReasonCode.INVALID_INPUT
    assert isinstance(missing, ProviderFailure)
    assert missing.reason_code is ProviderReasonCode.NOT_FOUND
    assert isinstance(cancelled, ProviderFailure)
    assert cancelled.reason_code is ProviderReasonCode.CANCELLED


def test_p0630_safe_provider_reason_mapping_covers_stage3_codes() -> None:
    assert safe_map_provider_reason(ProviderReasonCode.INVALID_INPUT) is AS2ProviderFailureReasonCode.INVALID_INPUT
    assert safe_map_provider_reason(ProviderReasonCode.CANCELLED) is AS2ProviderFailureReasonCode.CANCELLED


def test_p0630_invalid_input_routes_to_systemic_disable() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.INVALID_INPUT,
        detail="malformed capability grant",
        correlation_id="corr.p0630.invalid",
        latency_ms=1,
        provider_name="capability_grant",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0630_cancelled_routes_to_observe_without_gate_transition() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.CANCELLED,
        detail="caller cancelled request",
        correlation_id="corr.p0630.cancelled",
        latency_ms=1,
        provider_name="memory_reference",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.OBSERVE
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST
