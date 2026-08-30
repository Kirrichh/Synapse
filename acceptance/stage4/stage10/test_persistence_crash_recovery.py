from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.admission_journal import (
    JournalAdapterFailureCode,
    JournalAdapterViolation,
)
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.stage10 import record_store as record_store_module
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage10.record_store import (
    FileStage10RecordStore,
    RecordStoreFailureCode,
    Stage10RecordKind,
    Stage10RecordStoreViolation,
)
from tests.gold_store_fence import fence_for


class _InterruptedPublication(RuntimeError):
    pass


def test_interrupted_publication_is_invisible_and_exact_retry_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = encode_canonical(
        {"schema_version": "acceptance.interrupted-publication/v1", "value": 11}
    )
    oracle_authority = tmp_path / "oracle-authority"
    oracle_authority.mkdir()
    oracle_fence = fence_for(oracle_authority)
    oracle_store = FileStage10RecordStore(
        oracle_authority / "records",
        mutation_fence=oracle_fence,
    )
    with store_transaction(oracle_fence) as ticket:
        expected_ref = oracle_store.put(
            kind=Stage10RecordKind.INTENT,
            record_key="interrupted-record",
            canonical_payload=payload,
            ticket=ticket,
        )

    interrupted_authority = tmp_path / "interrupted-authority"
    interrupted_authority.mkdir()
    interrupted_fence = fence_for(interrupted_authority)
    interrupted_root = interrupted_authority / "records"
    interrupted_store = FileStage10RecordStore(
        interrupted_root,
        mutation_fence=interrupted_fence,
    )

    def interrupt_after_staging(staged, destination, *, ticket) -> None:
        del destination, ticket
        assert staged.path.is_file()
        raise _InterruptedPublication("publication interrupted before immutable publish")

    with monkeypatch.context() as fault:
        fault.setattr(record_store_module, "publish_immutable", interrupt_after_staging)
        with pytest.raises(JournalAdapterViolation) as interrupted:
            with store_transaction(interrupted_fence) as ticket:
                interrupted_store.put(
                    kind=Stage10RecordKind.INTENT,
                    record_key="interrupted-record",
                    canonical_payload=payload,
                    ticket=ticket,
                )
    assert interrupted.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    assert isinstance(interrupted.value.__cause__, _InterruptedPublication)

    reopened = FileStage10RecordStore(
        interrupted_root,
        mutation_fence=interrupted_fence,
    )
    with pytest.raises(Stage10RecordStoreViolation) as absent:
        reopened.get(kind=Stage10RecordKind.INTENT, ref=expected_ref)
    assert absent.value.failure_code is RecordStoreFailureCode.RECORD_UNKNOWN

    interrupted_fence.recover_abandoned_interval()
    with store_transaction(interrupted_fence) as ticket:
        retry_ref = reopened.put(
            kind=Stage10RecordKind.INTENT,
            record_key="interrupted-record",
            canonical_payload=payload,
            ticket=ticket,
        )

    assert retry_ref == expected_ref
    durable = FileStage10RecordStore(
        interrupted_root,
        mutation_fence=interrupted_fence,
    )
    assert durable.get(kind=Stage10RecordKind.INTENT, ref=expected_ref).payload == payload


def test_committed_record_recovers_and_corrupt_bytes_fail_closed(tmp_path: Path) -> None:
    authority_root = tmp_path / "stage10-authority"
    authority_root.mkdir()
    fence = fence_for(authority_root)
    store_root = authority_root / "records"
    store = FileStage10RecordStore(store_root, mutation_fence=fence)
    payload = encode_canonical({"schema_version": "acceptance.persisted/v1", "value": 7})

    with store_transaction(fence) as ticket:
        ref = store.put(
            kind=Stage10RecordKind.INTENT,
            record_key="accepted-record",
            canonical_payload=payload,
            ticket=ticket,
        )

    reopened = FileStage10RecordStore(store_root, mutation_fence=fence)
    assert reopened.get(kind=Stage10RecordKind.INTENT, ref=ref).payload == payload
    stored_path = store_root / Stage10RecordKind.INTENT.value / f"{ref.ref_id}.stage10"
    stored_path.write_bytes(stored_path.read_bytes() + b"torn-tail")

    with pytest.raises(ValueError):
        reopened.get(kind=Stage10RecordKind.INTENT, ref=ref)
