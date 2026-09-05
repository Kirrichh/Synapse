"""§26 acceptance for immutable, fenced, exact-key run records."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.admission_journal import (
    JournalAdapterFailureCode,
    JournalAdapterViolation,
)
from synapse.experiments.gold.persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    store_transaction,
)
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore
from synapse.experiments.gold.runner.vocabulary import GoldRunViolation
from tests.gold_store_fence import fence_for


PAYLOAD = {"record_sha256": "a" * 64, "payload": {"schema_version": "acceptance/v1"}}


def configured_store(root: Path):
    fence = fence_for(root / "coordinator")
    return RunRecordStore(root, mutation_fence=fence), fence


def put(store: RunRecordStore, fence, *, key: str, payload: dict) -> str:
    with store_transaction(fence) as ticket:
        return store.put(
            kind=RecordKind.ATTEMPT_RESULT,
            key=key,
            canonical_payload=payload,
            ticket=ticket,
        )


def test_a_stored_record_reads_back_as_the_exact_bytes_written(tmp_path: Path) -> None:
    store, fence = configured_store(tmp_path)
    put(store, fence, key="1", payload=PAYLOAD)
    stored = store.get(kind=RecordKind.ATTEMPT_RESULT, key="1")
    assert stored is not None
    assert stored.payload == PAYLOAD


def test_dotted_keys_are_resolved_exactly_and_never_as_prefix_aliases(tmp_path: Path) -> None:
    store, fence = configured_store(tmp_path)
    put(store, fence, key="attempt.1", payload=PAYLOAD)
    assert store.get(kind=RecordKind.ATTEMPT_RESULT, key="attempt") is None
    assert store.get(kind=RecordKind.ATTEMPT_RESULT, key="attempt.1") is not None


def test_the_same_bytes_under_the_same_key_are_idempotent(tmp_path: Path) -> None:
    store, fence = configured_store(tmp_path)
    first_digest = put(store, fence, key="1", payload=PAYLOAD)
    second_digest = put(store, fence, key="1", payload=PAYLOAD)
    assert second_digest == first_digest
    assert store.iter_keys(kind=RecordKind.ATTEMPT_RESULT) == ("1",)


def test_different_content_under_a_recorded_key_is_a_conflict(tmp_path: Path) -> None:
    store, fence = configured_store(tmp_path)
    put(store, fence, key="1", payload=PAYLOAD)
    other = {"record_sha256": "b" * 64, "payload": {"schema_version": "acceptance/v1"}}
    with pytest.raises(JournalAdapterViolation) as caught:
        put(store, fence, key="1", payload=other)
    assert caught.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    assert isinstance(caught.value.__cause__, GoldRunViolation)
    assert caught.value.__cause__.failure_code.value == "RECORD_CONFLICT"


def test_a_ticket_from_another_coordinator_cannot_publish(tmp_path: Path) -> None:
    store, _fence = configured_store(tmp_path / "store")
    foreign_fence = fence_for(tmp_path / "foreign")
    with store_transaction(foreign_fence) as ticket:
        with pytest.raises(PersistenceViolation) as caught:
            store.put(
                kind=RecordKind.ATTEMPT_RESULT,
                key="1",
                canonical_payload=PAYLOAD,
                ticket=ticket,
            )
    assert caught.value.failure_code is PersistenceFailureCode.MUTATION_COORDINATOR_MISMATCH


def test_unknown_visible_record_entries_block_recovery_audit(tmp_path: Path) -> None:
    store, _fence = configured_store(tmp_path)
    unknown = tmp_path / "run-records" / RecordKind.ATTEMPT_RESULT / "not-a-record"
    unknown.write_bytes(b"visible but outside the closed format")
    with pytest.raises(GoldRunViolation) as caught:
        store.audit_recoverable_state()
    assert caught.value.failure_code.value == "RECORD_CONFLICT"


def test_an_edited_record_is_refused_on_read(tmp_path: Path) -> None:
    store, fence = configured_store(tmp_path)
    put(store, fence, key="1", payload=PAYLOAD)
    stored_file = next((tmp_path / "run-records" / RecordKind.ATTEMPT_RESULT).glob("*.json"))
    stored_file.write_text('{"record_sha256":"c","payload":{}}', encoding="utf-8")
    with pytest.raises(GoldRunViolation):
        store.get(kind=RecordKind.ATTEMPT_RESULT, key="1")


def test_a_missing_record_is_authoritative_absence(tmp_path: Path) -> None:
    store, _fence = configured_store(tmp_path)
    assert store.get(kind=RecordKind.ATTEMPT_RESULT, key="7") is None
