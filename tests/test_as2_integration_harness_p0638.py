"""Executable evidence for P0.6.38 AS2 Integration Harness."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.runtime.as2_audit_outbox import InMemoryOutboxAuditSink
from synapse.runtime.as2_audit_sink import AS2AuditEvent
from synapse.runtime.as2_gate_controller import AS2GateControllerSkeleton
from synapse.runtime.as2_idempotency_store import (
    IdempotencyKey,
    IdempotencyRecordState,
    InMemoryIdempotencyStore,
)
from synapse.runtime.as2_integration_harness import (
    AS2IntegrationHarness,
    IntegrationDuplicate,
    IntegrationFailure,
    IntegrationPoisonPill,
    IntegrationProviderFailure,
    IntegrationSuccess,
)
from synapse.runtime.as2_projection_handoff import (
    AS2ProjectionHandoffResultKind,
    AS2ProjectionHandoffSkeleton,
)
from synapse.runtime.as2_provider_aggregator import AS2ProviderAggregatorSkeleton
from synapse.runtime.as2_provider_ports import (
    AS2ProviderName,
    HostProviderRequestContext,
    ProviderFailure,
    ProviderReasonCode,
    ProviderSuccess,
)
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState
from synapse.canonical_service import stable_canonical_hash

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS2_BRIDGE_FIXTURE = PROJECT_ROOT / "tests/fixtures/as2_bridge/positive_bridge_minimal_host_prestage.json"


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class CaptureSink:
    def __init__(self) -> None:
        self.events: list[AS2AuditEvent] = []

    def record(self, event: AS2AuditEvent) -> None:
        self.events.append(event)


class FailOnEventSink:
    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        self.events: list[AS2AuditEvent] = []

    def record(self, event: AS2AuditEvent) -> None:
        if event.event_type == self.event_type:
            raise RuntimeError(f"forced failure for {self.event_type}")
        self.events.append(event)


class _ProviderPort:
    def __init__(self, *, data: Mapping[str, Any] | None = None, failure: ProviderFailure | None = None) -> None:
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


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def _payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _providers_from_payload(payload: Mapping[str, Any]) -> dict[AS2ProviderName, _ProviderPort]:
    return {
        AS2ProviderName.IDENTITY: _ProviderPort(data=payload["adapter_identity_context"]),
        AS2ProviderName.MODEL_SELECTION: _ProviderPort(data=payload["model_selection_source"]),
        AS2ProviderName.DEFINITION: _ProviderPort(data=payload["adapter_definition_source"]),
        AS2ProviderName.STATIC_MODEL_REGISTRY: _ProviderPort(data=payload["static_model_registry"]),
        AS2ProviderName.MEMORY_REFERENCE: _ProviderPort(data=payload["memory_ref_source"]),
        AS2ProviderName.CAPABILITY_GRANT: _ProviderPort(data=payload["capability_grant_source"]),
    }


def _context(correlation_id: str = "corr-p0638") -> HostProviderRequestContext:
    return HostProviderRequestContext(correlation_id=correlation_id, request_id=f"req-{correlation_id}")


def _handoff(*, audit_sink: CaptureSink | None = None, projection_func=None) -> AS2ProjectionHandoffSkeleton:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    kwargs: dict[str, Any] = {"controller": controller, "audit_sink": audit_sink if audit_sink is not None else CaptureSink()}
    if projection_func is not None:
        kwargs["projection_func"] = projection_func
    return AS2ProjectionHandoffSkeleton(**kwargs)


def _harness(
    *,
    providers: Mapping[AS2ProviderName, _ProviderPort] | None = None,
    idempotency_store: InMemoryIdempotencyStore | None = None,
    projection_handoff: AS2ProjectionHandoffSkeleton | None = None,
    enabled_for_test: bool = True,
    clock: FakeClock | None = None,
) -> AS2IntegrationHarness:
    clock = clock if clock is not None else FakeClock()
    store = idempotency_store if idempotency_store is not None else InMemoryIdempotencyStore(clock=clock)
    return AS2IntegrationHarness(
        aggregator=AS2ProviderAggregatorSkeleton(providers if providers is not None else _providers_from_payload(_payload())),
        idempotency_store=store,
        projection_handoff=projection_handoff if projection_handoff is not None else _handoff(),
        enabled_for_test=enabled_for_test,
        clock=clock,
    )


def _prepared_hash_for_payload(payload: Mapping[str, Any]) -> str:
    prepared = bridge.prepare_as2_inputs_from_host_prestage(payload)
    return stable_canonical_hash(prepared.to_validate_kwargs())


def test_happy_path_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    outbox = InMemoryOutboxAuditSink()
    clock = FakeClock()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=clock)
    projection_sink = CaptureSink()
    handoff = _handoff(audit_sink=projection_sink)

    result = _harness(idempotency_store=store, projection_handoff=handoff, clock=clock).execute(
        _context(),
        agent_id="agent-p0638",
        correlation_id="corr-p0638-success",
    )

    assert isinstance(result, IntegrationSuccess)
    assert result.idempotency_state is IdempotencyRecordState.COMPLETED
    assert result.result_ref["snapshot_hash"]
    assert result.result_ref["derivation_record_hash"]
    assert handoff.projection_call_count == 1
    assert [event.event_type for event in outbox.iter_payloads()] == [
        "idempotency_reserved",
        "idempotency_completed",
    ]
    assert projection_sink.events[-1].event_type == "projection_completed"


def test_provider_failure_no_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    providers = _providers_from_payload(_payload())
    providers[AS2ProviderName.IDENTITY] = _ProviderPort(
        failure=ProviderFailure(
            reason_code=ProviderReasonCode.UNAUTHORIZED,
            detail="identity denied",
            correlation_id="corr-provider-failure",
            latency_ms=1,
            provider_name=AS2ProviderName.IDENTITY,
        )
    )
    store = InMemoryIdempotencyStore()

    result = _harness(providers=providers, idempotency_store=store).execute(
        _context("corr-provider-failure"),
        agent_id="agent-p0638",
        correlation_id="corr-provider-failure",
    )

    assert isinstance(result, IntegrationProviderFailure)
    assert len(store) == 0


def test_normalization_failure_no_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    providers = _providers_from_payload(_payload())
    providers[AS2ProviderName.MODEL_SELECTION] = _ProviderPort(data={"selection_source": "must-not-pass"})
    store = InMemoryIdempotencyStore()

    result = _harness(providers=providers, idempotency_store=store).execute(
        _context("corr-normalization-failure"),
        agent_id="agent-p0638",
        correlation_id="corr-normalization-failure",
    )

    assert isinstance(result, IntegrationProviderFailure)
    assert result.failure.reason_code is ProviderReasonCode.SCHEMA_MISMATCH
    assert len(store) == 0


def test_duplicate_same_hash_no_second_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    handoff = _handoff()
    store = InMemoryIdempotencyStore()
    harness = _harness(idempotency_store=store, projection_handoff=handoff)

    first = harness.execute(_context("corr-duplicate"), agent_id="agent-p0638", correlation_id="corr-duplicate")
    second = harness.execute(_context("corr-duplicate"), agent_id="agent-p0638", correlation_id="corr-duplicate")

    assert isinstance(first, IntegrationSuccess)
    assert isinstance(second, IntegrationDuplicate)
    assert second.idempotency_state is IdempotencyRecordState.COMPLETED
    assert second.result_ref == first.result_ref
    assert handoff.projection_call_count == 1


def test_poison_pill_terminal_no_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload_a = _payload()
    payload_b = copy.deepcopy(payload_a)
    payload_b["adapter_definition_source"]["config"]["variant"] = "changed"
    handoff = _handoff()
    store = InMemoryIdempotencyStore()
    first_harness = _harness(providers=_providers_from_payload(payload_a), idempotency_store=store, projection_handoff=handoff)
    second_harness = _harness(providers=_providers_from_payload(payload_b), idempotency_store=store, projection_handoff=handoff)

    first = first_harness.execute(_context("corr-poison"), agent_id="agent-p0638", correlation_id="corr-poison")
    second = second_harness.execute(_context("corr-poison"), agent_id="agent-p0638", correlation_id="corr-poison")

    assert isinstance(first, IntegrationSuccess)
    assert isinstance(second, IntegrationPoisonPill)
    assert second.idempotency_state is IdempotencyRecordState.FAILED
    assert handoff.projection_call_count == 1


def test_reserve_audit_failure_creates_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload = _payload()
    prepared_hash = _prepared_hash_for_payload(payload)
    key = IdempotencyKey(correlation_id="corr-reserve-audit", prepared_inputs_hash=prepared_hash)
    store = InMemoryIdempotencyStore(audit_sink=InMemoryOutboxAuditSink(max_entries=0))

    result = _harness(providers=_providers_from_payload(payload), idempotency_store=store).execute(
        _context("corr-reserve-audit"),
        agent_id="agent-p0638",
        correlation_id="corr-reserve-audit",
    )

    assert isinstance(result, IntegrationFailure)
    assert result.reason.startswith("idempotency_reserve_failed:")
    assert store.inspect(key) is None


def test_complete_audit_failure_preserves_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    payload = _payload()
    prepared_hash = _prepared_hash_for_payload(payload)
    key = IdempotencyKey(correlation_id="corr-complete-audit", prepared_inputs_hash=prepared_hash)
    store = InMemoryIdempotencyStore(audit_sink=InMemoryOutboxAuditSink(max_entries=1))

    result = _harness(providers=_providers_from_payload(payload), idempotency_store=store).execute(
        _context("corr-complete-audit"),
        agent_id="agent-p0638",
        correlation_id="corr-complete-audit",
    )

    assert isinstance(result, IntegrationFailure)
    assert result.reason.startswith("idempotency_complete_failed:")
    record = store.inspect(key)
    assert record is not None
    assert record.state is IdempotencyRecordState.IN_PROGRESS


def test_projection_failure_marks_idempotency_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)

    def _raise_projection(**_kwargs: Any):
        raise RuntimeError("projection failed")

    store = InMemoryIdempotencyStore()
    result = _harness(idempotency_store=store, projection_handoff=_handoff(projection_func=_raise_projection)).execute(
        _context("corr-projection-failed"),
        agent_id="agent-p0638",
        correlation_id="corr-projection-failed",
    )

    assert isinstance(result, IntegrationFailure)
    assert result.idempotency_state is IdempotencyRecordState.FAILED
    assert result.scope == "systemic"


def test_stale_in_progress_blocks_completion_with_fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    clock = FakeClock()
    sink = FailOnEventSink("idempotency_completed")
    payload = _payload()
    prepared_hash = _prepared_hash_for_payload(payload)
    key = IdempotencyKey(correlation_id="corr-stale", prepared_inputs_hash=prepared_hash)
    store = InMemoryIdempotencyStore(audit_sink=sink, clock=clock)

    result = _harness(providers=_providers_from_payload(payload), idempotency_store=store, clock=clock).execute(
        _context("corr-stale"),
        agent_id="agent-p0638",
        correlation_id="corr-stale",
    )
    assert isinstance(result, IntegrationFailure)
    assert store.inspect(key).state is IdempotencyRecordState.IN_PROGRESS  # type: ignore[union-attr]

    clock.advance(61)
    stale = store.mark_stale_if_expired(key, ttl_seconds=60)
    assert stale.accepted
    assert stale.record is not None
    assert stale.record.state is IdempotencyRecordState.STALE_IN_PROGRESS

    rejected = store.complete_if_state(
        key,
        expected_state=IdempotencyRecordState.IN_PROGRESS,
        snapshot_hash="sha256:" + "1" * 64,
        derivation_record_hash="sha256:" + "2" * 64,
    )
    assert not rejected.accepted
    assert rejected.record is not None
    assert rejected.record.state is IdempotencyRecordState.STALE_IN_PROGRESS


def test_gate_not_enabled_for_test_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    store = InMemoryIdempotencyStore()
    result = _harness(idempotency_store=store, enabled_for_test=False).execute(
        _context("corr-gate-denied"),
        agent_id="agent-p0638",
        correlation_id="corr-gate-denied",
    )

    assert isinstance(result, IntegrationFailure)
    assert result.reason == "gate_not_enabled_for_test"
    assert len(store) == 0


def test_audit_chain_valid_for_idempotency_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox)

    result = _harness(idempotency_store=store).execute(
        _context("corr-audit-chain"),
        agent_id="agent-p0638",
        correlation_id="corr-audit-chain",
    )

    assert isinstance(result, IntegrationSuccess)
    envelopes = outbox.envelopes
    assert len(envelopes) == 2
    assert envelopes[0].payload.previous_state_hash == "CHAIN_START"
    assert envelopes[1].payload.previous_state_hash == envelopes[0].payload.record_hash()


def test_boundary_guard_source_has_no_real_io_runtime_wiring_or_direct_time() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_integration_harness.py").read_text(encoding="utf-8")
    forbidden_terms = {
        "as2_runtime_wiring",
        "AgentRuntime",
        "Environment",
        "interpreter",
        "actor_runtime",
        "sqlite3",
        "redis",
        "boto3",
        "requests",
        "httpx",
        "socket",
        "pathlib",
        "time.time",
        "project_validated_as2_inputs",
        "open(",
    }
    assert not {term for term in forbidden_terms if term in source}


def test_integration_harness_requires_explicit_clock_and_avoids_truthiness_defaults() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_integration_harness.py").read_text(encoding="utf-8")

    assert "clock: Callable[[], float]" in source
    assert "clock: Callable[[], float] =" not in source
    assert " or {}" not in source
    assert " or NoOp" not in source
    assert " or InMemory" not in source
