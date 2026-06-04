"""P0.6.29 Stage 2 Provider Fakes + Integration Tests."""
from __future__ import annotations

from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecisionKind,
    AS2ProviderFailureReasonCode,
)
from synapse.runtime.as2_provider_ports import (
    HostProviderRequestContext,
    ProviderFailure,
    ProviderReasonCode,
    ProviderSuccess,
    is_provider_failure,
    is_provider_success,
)
from synapse.runtime.as2_projection_handoff import AS2ProjectionHandoffResultKind
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState
from tests.support.as2_prestage_provider_harness import HostPreStageProviderHarness
from tests.support.as2_provider_fakes import (
    FakeCapabilityGrantProvider,
    FakeHostDefinitionProvider,
    FakeHostIdentityProvider,
    FakeMemoryReferenceProvider,
    FakeModelSelectionProvider,
    FakeStaticModelRegistryProvider,
)
from tests.support.as2_provider_routing import (
    route_provider_failure_to_controller,
    safe_map_provider_reason,
)


def _context() -> HostProviderRequestContext:
    return HostProviderRequestContext(
        correlation_id="corr.p0629.provider",
        request_id="req.p0629.provider",
        timestamp="2026-06-02T00:00:00Z",
    )


def test_p0629_typeguards_narrow_provider_outcomes() -> None:
    success = ProviderSuccess(data={"ok": True}, correlation_id="corr", latency_ms=1)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.NOT_FOUND,
        detail="missing",
        correlation_id="corr",
        latency_ms=1,
    )

    assert is_provider_success(success)
    assert not is_provider_failure(success)
    assert is_provider_failure(failure)
    assert not is_provider_success(failure)


def test_p0629_host_definition_provider_success() -> None:
    provider = FakeHostDefinitionProvider(
        definitions_by_agent={
            "agent-42": {
                "type": "adapter_definition_source",
                "schema_version": "alpha3g.adapter_definition_source.v1",
                "profile": "stable-canonical.v1",
                "definition_ref": {"class_name": "Stage2Agent"},
                "config": {"max_steps": 2},
            }
        }
    )

    outcome = provider.get_adapter_definition_source(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["definition_ref"]["class_name"] == "Stage2Agent"
    assert outcome.correlation_id == "corr.p0629.provider"


def test_p0629_host_definition_provider_missing_context() -> None:
    provider = FakeHostDefinitionProvider()
    missing = HostProviderRequestContext(correlation_id="", request_id="")

    outcome = provider.get_adapter_definition_source(missing, agent_id="agent-42")

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.MISSING_REQUEST_CONTEXT
    assert outcome.provider_name == "host_definition"


def test_p0629_host_definition_provider_schema_mismatch() -> None:
    provider = FakeHostDefinitionProvider(schema_mismatch_agents=frozenset({"agent-42"}))

    outcome = provider.get_adapter_definition_source(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.SCHEMA_MISMATCH
    assert outcome.context == {"agent_id": "agent-42"}


def test_p0629_host_definition_provider_not_found() -> None:
    provider = FakeHostDefinitionProvider(not_found_agents=frozenset({"agent-404"}))

    outcome = provider.get_adapter_definition_source(_context(), agent_id="agent-404")

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.NOT_FOUND
    assert outcome.provider_name == "host_definition"


def test_p0629_static_model_registry_provider_success() -> None:
    provider = FakeStaticModelRegistryProvider(
        registry={"model-stage2": {"provider_namespace": "stage2", "model_version": "v2"}}
    )

    outcome = provider.get_static_model_registry(_context())

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.data["entries"][0]["legacy_model"] == "model-stage2"
    assert outcome.data["entries"][0]["model_ref"]["provider_namespace"] == "stage2"


def test_p0629_static_model_registry_provider_missing_context() -> None:
    provider = FakeStaticModelRegistryProvider()
    missing = HostProviderRequestContext(correlation_id="", request_id="")

    outcome = provider.get_static_model_registry(missing)

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.MISSING_REQUEST_CONTEXT
    assert outcome.provider_name == "static_model_registry"


def test_p0629_static_model_registry_provider_schema_mismatch() -> None:
    provider = FakeStaticModelRegistryProvider(schema_mismatch=True)

    outcome = provider.get_static_model_registry(_context())

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.SCHEMA_MISMATCH


def test_p0629_static_model_registry_provider_not_found_unknown_model() -> None:
    provider = FakeStaticModelRegistryProvider(
        registry={"unknown-model": {"provider_namespace": "stage2"}},
        not_found_models=frozenset({"unknown-model"}),
    )

    outcome = provider.get_static_model_registry(_context())

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.NOT_FOUND
    assert outcome.context == {"model": "unknown-model"}


def test_p0629_safe_provider_reason_mapping_covers_stage2_codes() -> None:
    assert safe_map_provider_reason(ProviderReasonCode.UNAVAILABLE) is AS2ProviderFailureReasonCode.UNAVAILABLE
    assert (
        safe_map_provider_reason(ProviderReasonCode.BACKPRESSURE_REJECTED)
        is AS2ProviderFailureReasonCode.BACKPRESSURE_REJECTED
    )
    assert (
        safe_map_provider_reason(ProviderReasonCode.SCHEMA_MISMATCH)
        is AS2ProviderFailureReasonCode.SCHEMA_MISMATCH
    )
    assert safe_map_provider_reason(ProviderReasonCode.NOT_FOUND) is AS2ProviderFailureReasonCode.NOT_FOUND


def test_p0629_safe_provider_reason_mapping_never_raises_for_unmapped_codes() -> None:
    assert safe_map_provider_reason(ProviderReasonCode.INVALID_INPUT) is AS2ProviderFailureReasonCode.INVALID_INPUT
    assert safe_map_provider_reason(ProviderReasonCode.CANCELLED) is AS2ProviderFailureReasonCode.CANCELLED


def test_p0629_stage2_schema_mismatch_routes_to_systemic_disable() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.SCHEMA_MISMATCH,
        detail="definition schema mismatch",
        correlation_id="corr.p0629.schema",
        latency_ms=1,
        provider_name="host_definition",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0629_stage2_not_found_routes_to_systemic_disable() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.NOT_FOUND,
        detail="model registry missing requested model",
        correlation_id="corr.p0629.not-found",
        latency_ms=1,
        provider_name="static_model_registry",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0629_backpressure_rejected_routes_to_observe_policy() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.BACKPRESSURE_REJECTED,
        detail="provider asked caller to slow down",
        correlation_id="corr.p0629.backpressure",
        latency_ms=1,
        provider_name="static_model_registry",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.OBSERVE
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST


def test_p0629_prestage_provider_harness_assembles_stage1_and_stage2_payload() -> None:
    harness = HostPreStageProviderHarness(
        identity_provider=FakeHostIdentityProvider(),
        model_selection_provider=FakeModelSelectionProvider(model_by_agent={"agent-42": "model-stage2"}),
        definition_provider=FakeHostDefinitionProvider(),
        registry_provider=FakeStaticModelRegistryProvider(
            registry={"model-stage2": {"provider_namespace": "stage2"}}
        ),
        memory_reference_provider=FakeMemoryReferenceProvider(),
        capability_grant_provider=FakeCapabilityGrantProvider(),
    )

    payload = harness.build_prestage_payload(_context(), agent_id="agent-42")

    assert not isinstance(payload, ProviderFailure)
    assert set(payload) == {
        "adapter_identity_context",
        "model_selection_source",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
    }
    assert payload["model_selection_source"]["model"] == "model-stage2"
    assert payload["static_model_registry"]["entries"][0]["legacy_model"] == "model-stage2"


def test_p0629_prestage_provider_harness_stops_on_first_provider_failure() -> None:
    harness = HostPreStageProviderHarness(
        identity_provider=FakeHostIdentityProvider(),
        model_selection_provider=FakeModelSelectionProvider(),
        definition_provider=FakeHostDefinitionProvider(not_found_agents=frozenset({"agent-404"})),
        registry_provider=FakeStaticModelRegistryProvider(),
        memory_reference_provider=FakeMemoryReferenceProvider(),
        capability_grant_provider=FakeCapabilityGrantProvider(),
    )

    result = harness.build_prestage_payload(_context(), agent_id="agent-404")

    assert isinstance(result, ProviderFailure)
    assert result.reason_code is ProviderReasonCode.NOT_FOUND
    assert result.provider_name == "host_definition"


def test_p0629_provider_failure_path_remains_distinct_from_projection_failure_path() -> None:
    provider_failure = ProviderFailure(
        reason_code=ProviderReasonCode.NOT_FOUND,
        detail="definition missing",
        correlation_id="corr.p0629.path",
        latency_ms=1,
        provider_name="host_definition",
    )

    gate_reason = safe_map_provider_reason(provider_failure.reason_code)

    assert gate_reason is AS2ProviderFailureReasonCode.NOT_FOUND
    assert AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE.value == "projection_systemic_failure"
    assert provider_failure.reason_code is ProviderReasonCode.NOT_FOUND
