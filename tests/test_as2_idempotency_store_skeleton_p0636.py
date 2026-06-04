"""P0.6.36 tests for the AS2 idempotency store skeleton."""
from __future__ import annotations

import threading

import pytest

from synapse.agent_snapshot_bridge import ModelSelectionSource, PreparedAS2Inputs
from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_audit_outbox import InMemoryOutboxAuditSink, OutboxCapacityExceeded
from synapse.runtime.as2_idempotency_store import (
    IdempotencyFailureReason,
    IdempotencyKey,
    IdempotencyRecordState,
    IdempotencyResultReason,
    IdempotencyStoreUnavailable,
    InMemoryIdempotencyStore,
)


def _key(correlation_id: str = "corr-p0636", prepared_inputs_hash: str = "hash-a") -> IdempotencyKey:
    return IdempotencyKey(correlation_id=correlation_id, prepared_inputs_hash=prepared_inputs_hash)


def test_reserve_if_absent_success() -> None:
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: 10.0)
    key = _key()

    result = store.reserve_if_absent(key, "hash-a")

    assert result.accepted is True
    assert result.reason_code is IdempotencyResultReason.RESERVED
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.IN_PROGRESS
    assert result.record.created_at == 10.0
    assert len(outbox.envelopes) == 1
    assert outbox.envelopes[0].payload.event_type == "idempotency_reserved"


def test_reserve_if_absent_duplicate_same_hash() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    key = _key()
    first = store.reserve_if_absent(key, "hash-a")

    duplicate = store.reserve_if_absent(key, "hash-a")

    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.reason_code is IdempotencyResultReason.DUPLICATE
    assert duplicate.record is first.record


def test_reserve_if_absent_poison_pill_same_correlation_different_hash() -> None:
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: 10.0)
    first_key = _key(correlation_id="corr-poison", prepared_inputs_hash="hash-a")
    poison_key = _key(correlation_id="corr-poison", prepared_inputs_hash="hash-b")
    store.reserve_if_absent(first_key, "hash-a")

    result = store.reserve_if_absent(poison_key, "hash-b")

    assert result.accepted is False
    assert result.reason_code is IdempotencyResultReason.POISON_PILL
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.FAILED
    assert result.record.failure_reason is IdempotencyFailureReason.POISON_PILL
    assert store.inspect(poison_key) == result.record
    assert outbox.envelopes[-1].payload.event_type == "idempotency_poison_pill"


def test_poison_pill_is_terminal_blocks_all_operations_except_inspect() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    first_key = _key(correlation_id="corr-terminal", prepared_inputs_hash="hash-a")
    poison_key = _key(correlation_id="corr-terminal", prepared_inputs_hash="hash-b")
    store.reserve_if_absent(first_key, "hash-a")
    poison = store.reserve_if_absent(poison_key, "hash-b")
    assert poison.record is not None

    repeat = store.reserve_if_absent(first_key, "hash-a")
    complete = store.complete_if_state(
        poison_key,
        snapshot_hash="snapshot-hash",
        derivation_record_hash="derivation-hash",
    )
    cancel = store.cancel_if_state(poison_key)

    assert repeat.reason_code is IdempotencyResultReason.TERMINAL_POISON_PILL
    assert complete.reason_code is IdempotencyResultReason.TERMINAL_POISON_PILL
    assert cancel.reason_code is IdempotencyResultReason.TERMINAL_POISON_PILL
    assert store.inspect(poison_key) == poison.record


def test_complete_if_state_success() -> None:
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")

    result = store.complete_if_state(
        key,
        snapshot_hash="snapshot-hash",
        derivation_record_hash="derivation-hash",
    )

    assert result.accepted is True
    assert result.reason_code is IdempotencyResultReason.COMPLETED
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.COMPLETED
    assert result.record.snapshot_hash == "snapshot-hash"
    assert result.record.derivation_record_hash == "derivation-hash"
    assert outbox.envelopes[-1].payload.event_type == "idempotency_completed"


def test_complete_if_state_wrong_state_rejected() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    store.cancel_if_state(key)

    result = store.complete_if_state(
        key,
        snapshot_hash="snapshot-hash",
        derivation_record_hash="derivation-hash",
    )

    assert result.accepted is False
    assert result.reason_code is IdempotencyResultReason.WRONG_STATE
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.CANCELLED


def test_fail_if_state_poison_pill() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")

    result = store.fail_if_state(key, reason=IdempotencyFailureReason.POISON_PILL)

    assert result.accepted is True
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.FAILED
    assert result.record.failure_reason is IdempotencyFailureReason.POISON_PILL


def test_cancel_if_state_success() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")

    result = store.cancel_if_state(key)

    assert result.accepted is True
    assert result.reason_code is IdempotencyResultReason.CANCELLED
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.CANCELLED


def test_mark_stale_if_expired() -> None:
    current = {"value": 10.0}
    store = InMemoryIdempotencyStore(clock=lambda: current["value"])
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    current["value"] = 25.0

    result = store.mark_stale_if_expired(key, ttl_seconds=10.0)

    assert result.accepted is True
    assert result.reason_code is IdempotencyResultReason.MARKED_STALE
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.STALE_IN_PROGRESS


def test_mark_stale_only_affects_in_progress_state() -> None:
    current = {"value": 10.0}
    store = InMemoryIdempotencyStore(clock=lambda: current["value"])
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    store.cancel_if_state(key)
    current["value"] = 25.0

    result = store.mark_stale_if_expired(key, ttl_seconds=1.0)

    assert result.accepted is False
    assert result.reason_code is IdempotencyResultReason.WRONG_STATE
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.CANCELLED


def test_clock_injection_makes_ttl_deterministic() -> None:
    current = {"value": 100.0}
    store = InMemoryIdempotencyStore(clock=lambda: current["value"])
    key = _key()
    store.reserve_if_absent(key, "hash-a")

    current["value"] = 104.0
    not_expired = store.mark_stale_if_expired(key, ttl_seconds=10.0)
    current["value"] = 111.0
    expired = store.mark_stale_if_expired(key, ttl_seconds=10.0)

    assert not_expired.reason_code is IdempotencyResultReason.NOT_EXPIRED
    assert expired.accepted is True
    assert expired.record is not None
    assert expired.record.state is IdempotencyRecordState.STALE_IN_PROGRESS


def test_completed_after_stale_is_rejected() -> None:
    current = {"value": 10.0}
    store = InMemoryIdempotencyStore(clock=lambda: current["value"])
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    current["value"] = 25.0
    store.mark_stale_if_expired(key, ttl_seconds=10.0)

    result = store.complete_if_state(
        key,
        snapshot_hash="snapshot-hash",
        derivation_record_hash="derivation-hash",
    )

    assert result.accepted is False
    assert result.reason_code is IdempotencyResultReason.WRONG_STATE
    assert result.record is not None
    assert result.record.state is IdempotencyRecordState.STALE_IN_PROGRESS


def test_stale_in_progress_emits_operator_review_audit_event() -> None:
    current = {"value": 10.0}
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: current["value"])
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    current["value"] = 25.0

    store.mark_stale_if_expired(key, ttl_seconds=10.0)

    event = outbox.envelopes[-1].payload
    assert event.event_type == "idempotency_stale_in_progress"
    assert event.detail["operator_review_required"] is True


def test_store_unavailable_fail_closed() -> None:
    store = InMemoryIdempotencyStore(available=False)

    with pytest.raises(IdempotencyStoreUnavailable):
        store.reserve_if_absent(_key(), "hash-a")

    assert len(store) == 0


def test_prepared_inputs_hash_is_deterministic_across_dict_ordering() -> None:
    prepared_a = PreparedAS2Inputs(
        adapter_identity_context={"agent_id": "agent-1", "tenant": "t"},
        adapter_definition_source={"z": 2, "a": 1},
        static_model_registry={"models": {"mock": {"version": "1"}}},
        memory_ref_source={"refs": [{"b": 2, "a": 1}]},
        capability_grant_source={"grants": [{"scope": "read", "name": "memory"}]},
        model_selection_source=ModelSelectionSource(model="mock-agent-model"),
    )
    prepared_b = PreparedAS2Inputs(
        adapter_identity_context={"tenant": "t", "agent_id": "agent-1"},
        adapter_definition_source={"a": 1, "z": 2},
        static_model_registry={"models": {"mock": {"version": "1"}}},
        memory_ref_source={"refs": [{"a": 1, "b": 2}]},
        capability_grant_source={"grants": [{"name": "memory", "scope": "read"}]},
        model_selection_source=ModelSelectionSource(model="mock-agent-model"),
    )

    assert stable_canonical_hash(prepared_a.to_validate_kwargs()) == stable_canonical_hash(
        prepared_b.to_validate_kwargs()
    )


def test_atomic_linkage_state_update_and_audit_event() -> None:
    outbox = InMemoryOutboxAuditSink()
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")

    result = store.complete_if_state(
        key,
        snapshot_hash="snapshot-hash",
        derivation_record_hash="derivation-hash",
    )

    assert result.accepted is True
    assert store.inspect(key) == result.record
    assert outbox.envelopes[-1].payload.event_type == "idempotency_completed"
    assert outbox.envelopes[-1].payload.detail["snapshot_hash"] == "snapshot-hash"


def test_transition_rolls_back_when_outbox_append_fails() -> None:
    outbox = InMemoryOutboxAuditSink(max_entries=1)
    store = InMemoryIdempotencyStore(audit_sink=outbox, clock=lambda: 10.0)
    key = _key()
    store.reserve_if_absent(key, "hash-a")
    before = store.inspect(key)

    with pytest.raises(OutboxCapacityExceeded):
        store.complete_if_state(
            key,
            snapshot_hash="snapshot-hash",
            derivation_record_hash="derivation-hash",
        )

    assert store.inspect(key) == before
    assert store.inspect(key) is not None
    assert store.inspect(key).state is IdempotencyRecordState.IN_PROGRESS  # type: ignore[union-attr]


def test_concurrent_reservation_only_one_succeeds() -> None:
    store = InMemoryIdempotencyStore(clock=lambda: 10.0)
    key = _key(correlation_id="corr-concurrent")
    results = []
    result_lock = threading.Lock()

    def reserve() -> None:
        result = store.reserve_if_absent(key, "hash-a")
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [result for result in results if result.accepted]
    duplicates = [result for result in results if result.reason_code is IdempotencyResultReason.DUPLICATE]
    assert len(accepted) == 1
    assert len(duplicates) == 7
    assert len(store) == 1
