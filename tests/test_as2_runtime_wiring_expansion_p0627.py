"""P0.6.27 Runtime Wiring Expansion under gate tests."""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.agent_snapshot_adapter import project_validated_as2_inputs
from synapse.runtime.as2_gate_controller import AS2GateControllerSkeleton
from synapse.runtime.as2_projection_handoff import (
    AS2ProjectionDedupPolicy,
    AS2ProjectionHandoffResult,
    AS2ProjectionHandoffResultKind,
    AS2ProjectionHandoffSkeleton,
)
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    WiringProjectionCompleted,
    WiringProjectionSkipped,
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


def _prepared(monkeypatch: pytest.MonkeyPatch, *, correlation_id: str = "corr.p0627.prepared"):
    _enable_bridge(monkeypatch)
    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id=correlation_id,
    )
    assert isinstance(outcome, WiringSuccess)
    return outcome.prepared_inputs


class _FakeHandoff:
    def __init__(self, result: AS2ProjectionHandoffResult) -> None:
        self.result = result
        self.called = False
        self.seen_correlation_id: str | None = None

    def execute_projection(self, prepared_inputs: Any, *, correlation_id: str) -> AS2ProjectionHandoffResult:
        self.called = True
        self.seen_correlation_id = correlation_id
        return self.result


def test_p0627_wiring_expansion_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    fake = _FakeHandoff(
        AS2ProjectionHandoffResult(
            kind=AS2ProjectionHandoffResultKind.COMPLETED,
            correlation_id="corr.p0627.disabled",
            snapshot_hash="snapshot-hash",
        )
    )

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0627.disabled",
        projection_handoff=fake,
    )

    assert isinstance(outcome, WiringSuccess)
    assert fake.called is False


def test_p0627_wiring_expansion_delegates_to_handoff_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    fake = _FakeHandoff(
        AS2ProjectionHandoffResult(
            kind=AS2ProjectionHandoffResultKind.COMPLETED,
            correlation_id="corr.p0627.completed",
            snapshot_hash="snapshot-hash-p0627",
            derivation_record_hash="derivation-hash-p0627",
        )
    )

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0627.completed",
        projection_handoff_enabled=True,
        projection_handoff=fake,
    )

    assert fake.called is True
    assert fake.seen_correlation_id == "corr.p0627.completed"
    assert isinstance(outcome, WiringProjectionCompleted)
    assert outcome.snapshot_hash == "snapshot-hash-p0627"
    assert outcome.derivation_record_hash == "derivation-hash-p0627"


def test_p0627_wiring_expansion_missing_handoff_skips_without_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)

    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0627.no-handoff",
        projection_handoff_enabled=True,
    )

    assert isinstance(outcome, WiringProjectionSkipped)
    assert outcome.reason_code.value == "projection_handoff_not_configured"


def test_p0627_handoff_denied_and_duplicate_map_to_projection_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_bridge(monkeypatch)
    denied = _FakeHandoff(
        AS2ProjectionHandoffResult(
            kind=AS2ProjectionHandoffResultKind.DENIED,
            correlation_id="corr.p0627.denied",
        )
    )
    denied_outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0627.denied",
        projection_handoff_enabled=True,
        projection_handoff=denied,
    )
    assert isinstance(denied_outcome, WiringProjectionSkipped)
    assert denied_outcome.reason_code.value == "projection_denied"

    duplicate = _FakeHandoff(
        AS2ProjectionHandoffResult(
            kind=AS2ProjectionHandoffResultKind.DUPLICATE,
            correlation_id="corr.p0627.duplicate",
            snapshot_hash="snapshot-hash-duplicate",
            derivation_record_hash="derivation-hash-duplicate",
        )
    )
    duplicate_outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0627.duplicate",
        projection_handoff_enabled=True,
        projection_handoff=duplicate,
    )
    assert isinstance(duplicate_outcome, WiringProjectionSkipped)
    assert duplicate_outcome.reason_code.value == "projection_duplicate"
    assert duplicate_outcome.snapshot_hash == "snapshot-hash-duplicate"


def test_p0627_dedup_index_write_is_after_successful_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(monkeypatch, correlation_id="corr.p0627.write-after-success")
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    calls = {"count": 0}

    def flaky_projection(**kwargs: Any):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("first projection fails before dedup write")
        return project_validated_as2_inputs(**kwargs)

    handoff = AS2ProjectionHandoffSkeleton(controller=controller, projection_func=flaky_projection)

    first = handoff.execute_projection(prepared, correlation_id="corr.p0627.write-after-success")
    second = handoff.execute_projection(prepared, correlation_id="corr.p0627.write-after-success")

    assert isinstance(first.outcome, WiringSystemicDisableRequest)
    assert first.kind is AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE
    assert second.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert second.projection_call_count == 2


def test_p0627_duplicate_result_is_hash_only(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(monkeypatch, correlation_id="corr.p0627.hash-only")
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    handoff = AS2ProjectionHandoffSkeleton(controller=controller)

    first = handoff.execute_projection(prepared, correlation_id="corr.p0627.hash-only")
    second = handoff.execute_projection(prepared, correlation_id="corr.p0627.hash-only")

    assert first.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert second.kind is AS2ProjectionHandoffResultKind.DUPLICATE
    assert second.snapshot is None
    assert second.derivation_record is None
    assert second.snapshot_hash == first.snapshot_hash
    assert second.derivation_record_hash == first.derivation_record_hash


def test_p0627_dedup_concurrent_calls_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared(monkeypatch, correlation_id="corr.p0627.concurrent")
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    projection_started = threading.Event()
    release_projection = threading.Event()

    def blocking_projection(**kwargs: Any):
        projection_started.set()
        release_projection.wait(timeout=3)
        return project_validated_as2_inputs(**kwargs)

    handoff = AS2ProjectionHandoffSkeleton(controller=controller, projection_func=blocking_projection)
    results: list[AS2ProjectionHandoffResult] = []

    def call_handoff() -> None:
        results.append(handoff.execute_projection(prepared, correlation_id="corr.p0627.concurrent"))

    first_thread = threading.Thread(target=call_handoff, name="p0627-first")
    second_thread = threading.Thread(target=call_handoff, name="p0627-second")
    first_thread.start()
    assert projection_started.wait(timeout=3)
    second_thread.start()
    time.sleep(0.05)
    release_projection.set()
    first_thread.join(timeout=3)
    second_thread.join(timeout=3)

    kinds = [result.kind for result in results]
    assert kinds.count(AS2ProjectionHandoffResultKind.COMPLETED) == 1
    assert kinds.count(AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE) == 1
    assert handoff.projection_call_count == 1
