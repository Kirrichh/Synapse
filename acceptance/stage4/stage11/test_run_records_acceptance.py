"""§26 acceptance: run records are immutable, content-addressed and fail closed.

The controller keeps no truth in memory between processes, so these records are
the run. What is checked here is that they behave like it: bytes are bound to
the key that names them, an existing key cannot be rewritten with different
content, and a record edited on disk is refused rather than read.

Light by construction: the store and a mutation fence, no run and no C1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.runner import GoldRunViolation
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore
from tests.gold_store_fence import fence_for

PAYLOAD = {"record_sha256": "a" * 64, "payload": {"schema_version": "acceptance/v1"}}


def put(store: RunRecordStore, fence, *, key: str, payload: dict) -> None:
    with store_transaction(fence) as ticket:
        store.put(kind=RecordKind.ATTEMPT_RESULT, key=key, canonical_payload=payload, ticket=ticket)


def test_a_stored_record_reads_back_as_the_exact_bytes_written(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path)
    put(store, fence_for(tmp_path), key="1", payload=PAYLOAD)
    stored = store.get(kind=RecordKind.ATTEMPT_RESULT, key="1")
    assert stored is not None
    assert stored.payload == PAYLOAD


def test_the_same_bytes_under_the_same_key_are_idempotent(tmp_path: Path) -> None:
    """A repeated write after a crash must not become a second record."""

    store = RunRecordStore(tmp_path)
    fence = fence_for(tmp_path)
    put(store, fence, key="1", payload=PAYLOAD)
    put(store, fence, key="1", payload=PAYLOAD)
    assert len(list((tmp_path / "run-records" / RecordKind.ATTEMPT_RESULT).glob("*.json"))) == 1


def test_different_content_under_a_recorded_key_is_a_conflict(tmp_path: Path) -> None:
    """NR-13: a record is never overwritten, and a divergence is refused at the write.

    The refusal reaches the caller as an aborted mutation interval, because that
    is what a store under a fence reports when a write does not complete. What
    matters for §26 is underneath it: the typed conflict is the cause, and the
    run root still holds exactly one record for the key.
    """

    store = RunRecordStore(tmp_path)
    fence = fence_for(tmp_path)
    put(store, fence, key="1", payload=PAYLOAD)
    other = {"record_sha256": "b" * 64, "payload": {"schema_version": "acceptance/v1"}}

    with pytest.raises(Exception) as caught:
        put(store, fence, key="1", payload=other)

    cause = caught.value.__cause__
    assert isinstance(cause, GoldRunViolation)
    assert cause.failure_code.value == "RECORD_CONFLICT"
    assert len(list((tmp_path / "run-records" / RecordKind.ATTEMPT_RESULT).glob("*.json"))) == 1


def test_a_record_edited_on_disk_is_refused(tmp_path: Path) -> None:
    """Content addressing is checked on read, not assumed from the file name."""

    store = RunRecordStore(tmp_path)
    put(store, fence_for(tmp_path), key="1", payload=PAYLOAD)
    stored_file = next((tmp_path / "run-records" / RecordKind.ATTEMPT_RESULT).glob("*.json"))
    stored_file.write_text('{"record_sha256": "c", "payload": {}}', encoding="utf-8")
    with pytest.raises(GoldRunViolation):
        store.get(kind=RecordKind.ATTEMPT_RESULT, key="1")


def test_a_missing_record_is_absence_and_not_an_error(tmp_path: Path) -> None:
    """Absence is a state the recovery path reads, so it is not an exception."""

    assert RunRecordStore(tmp_path).get(kind=RecordKind.ATTEMPT_RESULT, key="7") is None
