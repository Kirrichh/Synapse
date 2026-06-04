"""Executable evidence for P0.6.37 ProductionProviderAggregator skeleton."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from synapse.runtime.as2_provider_aggregator import (
    AS2ProviderAggregatorSkeleton,
    AggregatorFailure,
    AggregatorSuccess,
    BRIDGE_PAYLOAD_KEYS,
    classify_failure_scope,
    select_representative_failure,
)
from synapse.runtime.as2_provider_ports import (
    AS2ProviderName,
    HostProviderRequestContext,
    ProviderFailure,
    ProviderReasonCode,
    ProviderSuccess,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ProviderPort:
    def __init__(
        self,
        *,
        data: Mapping[str, Any] | None = None,
        failure: ProviderFailure | None = None,
    ) -> None:
        self.data = dict(data if data is not None else {})
        self.failure = failure
        self.calls = 0

    def _outcome(self, context: HostProviderRequestContext):
        self.calls += 1
        if self.failure is not None:
            return self.failure
        return ProviderSuccess(data=dict(self.data), correlation_id=context.correlation_id, latency_ms=1)

    def get_adapter_identity_context(self, context: HostProviderRequestContext, *, agent_id: str):
        del agent_id
        return self._outcome(context)

    def get_model_selection_source(self, context: HostProviderRequestContext, *, agent_id: str):
        del agent_id
        return self._outcome(context)

    def get_adapter_definition_source(self, context: HostProviderRequestContext, *, agent_id: str):
        del agent_id
        return self._outcome(context)

    def get_static_model_registry(self, context: HostProviderRequestContext):
        return self._outcome(context)

    def get_memory_ref_source(self, context: HostProviderRequestContext, *, agent_id: str):
        del agent_id
        return self._outcome(context)

    def get_capability_grant_source(self, context: HostProviderRequestContext, *, agent_id: str):
        del agent_id
        return self._outcome(context)


def _context() -> HostProviderRequestContext:
    return HostProviderRequestContext(correlation_id="corr-p0637", request_id="req-p0637")


def _success_providers() -> dict[AS2ProviderName, _ProviderPort]:
    return {
        AS2ProviderName.IDENTITY: _ProviderPort(
            data={
                "schema_version": "alpha3g.adapter_identity_context.v1",
                "profile": "stable-canonical.v1",
                "identity_seed": {"alias": "agent-1"},
                "debug_identity_provider": "must-strip",
            }
        ),
        AS2ProviderName.MODEL_SELECTION: _ProviderPort(
            data={"model": "mock-agent-model", "selection_source": "p0628_fake"}
        ),
        AS2ProviderName.DEFINITION: _ProviderPort(
            data={
                "type": "adapter_definition_source",
                "schema_version": "alpha3g.adapter_definition_source.v1",
                "profile": "stable-canonical.v1",
                "definition_ref": {"namespace": "alpha3g.fixture", "class_name": "FixtureAgent"},
                "config": {"max_steps": 1},
                "canonical_fields": {"legacy_name_alias": "agent-1"},
                "debug_definition_provider": "must-strip",
            }
        ),
        AS2ProviderName.STATIC_MODEL_REGISTRY: _ProviderPort(
            data={
                "type": "static_model_registry",
                "schema_version": "alpha3g.static_model_registry.v1",
                "profile": "stable-canonical.v1",
                "registry_snapshot_hash": "sha256:" + "7" * 64,
                "entries": [],
                "debug_registry_provider": "must-strip",
            }
        ),
        AS2ProviderName.MEMORY_REFERENCE: _ProviderPort(
            data={
                "type": "memory_ref_source",
                "schema_version": "alpha3g.memory_ref_source.v1",
                "profile": "stable-canonical.v1",
                "expected_memory_space_id": "sha256:" + "3" * 64,
                "memory_space_policy_version": "alpha3g.memory_space_policy.v1",
                "refs": [],
                "debug_memory_provider": "must-strip",
            }
        ),
        AS2ProviderName.CAPABILITY_GRANT: _ProviderPort(
            data={
                "type": "capability_grant_source",
                "schema_version": "alpha3g.capability_grant_source.v1",
                "profile": "stable-canonical.v1",
                "grants": [],
                "debug_capability_provider": "must-strip",
            }
        ),
    }


def _failure(reason_code: ProviderReasonCode, provider_name: AS2ProviderName) -> ProviderFailure:
    return ProviderFailure(
        reason_code=reason_code,
        detail=f"{provider_name.value} failed",
        correlation_id="corr-p0637",
        latency_ms=1,
        provider_name=provider_name,
    )


def test_success_all_6_providers_returns_bridge_valid_payload() -> None:
    result = AS2ProviderAggregatorSkeleton(_success_providers()).aggregate(_context(), agent_id="agent-1")

    assert isinstance(result, AggregatorSuccess)
    assert tuple(result.payload.keys()) == BRIDGE_PAYLOAD_KEYS
    assert result.payload["model_selection_source"] == {"model": "mock-agent-model"}


def test_model_selection_strips_selection_source_regression() -> None:
    result = AS2ProviderAggregatorSkeleton(_success_providers()).aggregate(_context(), agent_id="agent-1")

    assert isinstance(result, AggregatorSuccess)
    model_selection = result.payload["model_selection_source"]
    assert model_selection == {"model": "mock-agent-model"}
    assert "selection_source" not in model_selection


def test_non_bridge_fields_are_stripped_for_all_fragments() -> None:
    result = AS2ProviderAggregatorSkeleton(_success_providers()).aggregate(_context(), agent_id="agent-1")

    assert isinstance(result, AggregatorSuccess)
    for fragment in result.payload.values():
        assert not any(str(key).startswith("debug_") for key in fragment)


def test_missing_required_field_returns_schema_failure() -> None:
    providers = _success_providers()
    providers[AS2ProviderName.MODEL_SELECTION] = _ProviderPort(data={"selection_source": "p0628_fake"})

    result = AS2ProviderAggregatorSkeleton(providers).aggregate(_context(), agent_id="agent-1")

    assert isinstance(result, AggregatorFailure)
    assert result.failure.reason_code == ProviderReasonCode.SCHEMA_MISMATCH
    assert result.failure.provider_name == AS2ProviderName.MODEL_SELECTION
    assert result.scope == "systemic"


def test_fail_fast_stops_after_first_failure() -> None:
    providers = _success_providers()
    providers[AS2ProviderName.MODEL_SELECTION] = _ProviderPort(
        failure=_failure(ProviderReasonCode.UNAUTHORIZED, AS2ProviderName.MODEL_SELECTION)
    )

    result = AS2ProviderAggregatorSkeleton(providers).aggregate(_context(), agent_id="agent-1")

    assert isinstance(result, AggregatorFailure)
    assert result.failure.reason_code == ProviderReasonCode.UNAUTHORIZED
    assert providers[AS2ProviderName.IDENTITY].calls == 1
    assert providers[AS2ProviderName.MODEL_SELECTION].calls == 1
    assert providers[AS2ProviderName.DEFINITION].calls == 0
    assert providers[AS2ProviderName.STATIC_MODEL_REGISTRY].calls == 0
    assert providers[AS2ProviderName.MEMORY_REFERENCE].calls == 0
    assert providers[AS2ProviderName.CAPABILITY_GRANT].calls == 0


def test_select_representative_failure_priority_matrix() -> None:
    timeout = _failure(ProviderReasonCode.TIMEOUT, AS2ProviderName.CAPABILITY_GRANT)
    missing_context = _failure(
        ProviderReasonCode.MISSING_REQUEST_CONTEXT,
        AS2ProviderName.MEMORY_REFERENCE,
    )

    assert select_representative_failure([timeout, missing_context]) is missing_context


def test_select_representative_failure_tie_breaker_by_provider_enum_value() -> None:
    memory = _failure(ProviderReasonCode.INVALID_INPUT, AS2ProviderName.MEMORY_REFERENCE)
    definition = _failure(ProviderReasonCode.SCHEMA_MISMATCH, AS2ProviderName.DEFINITION)

    assert select_representative_failure([memory, definition]) is definition


def test_select_representative_failure_is_input_order_independent() -> None:
    first = _failure(ProviderReasonCode.UNAVAILABLE, AS2ProviderName.MEMORY_REFERENCE)
    second = _failure(ProviderReasonCode.BACKPRESSURE_REJECTED, AS2ProviderName.CAPABILITY_GRANT)

    assert select_representative_failure([first, second]) is second
    assert select_representative_failure([second, first]) is second


def test_invalid_input_with_agent_id_and_agent_scoped_true_returns_agent_scope() -> None:
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.INVALID_INPUT,
        detail="agent-local invalid provider data",
        correlation_id="corr-p0637",
        latency_ms=1,
        provider_name=AS2ProviderName.MEMORY_REFERENCE,
        context={"agent_id": "agent-1", "agent_scoped": True},
    )

    assert classify_failure_scope(failure) == "agent"


def test_invalid_input_without_agent_scoped_true_returns_systemic_scope() -> None:
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.INVALID_INPUT,
        detail="invalid provider data without explicit scope",
        correlation_id="corr-p0637",
        latency_ms=1,
        provider_name=AS2ProviderName.MEMORY_REFERENCE,
        context={"agent_id": "agent-1"},
    )

    assert classify_failure_scope(failure) == "systemic"


def test_as2_provider_name_is_string_compatible_for_existing_provider_failures() -> None:
    assert AS2ProviderName.MODEL_SELECTION == "model_selection"


def test_aggregator_uses_explicit_none_check_for_dependency_injection() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_provider_aggregator.py").read_text(encoding="utf-8")

    assert "if providers is not None else {}" in source
    assert "providers or" not in source


def test_aggregator_source_has_no_direct_io_or_idempotency_coupling_terms() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_provider_aggregator.py").read_text(encoding="utf-8")

    forbidden_terms = {
        "as2_idempotency_store",
        "InMemoryIdempotencyStore",
        "project_validated_as2_inputs",
        "prepare_as2_inputs_from_host_prestage",
        "AgentSnapshot",
        "requests.",
        "httpx.",
        "socket.",
        "sqlite3.",
        "redis.",
        "boto3.",
        "open(",
        "time.time(",
    }
    assert not {term for term in forbidden_terms if term in source}
