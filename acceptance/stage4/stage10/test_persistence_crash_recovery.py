from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage10.record_store import (
    FileStage10RecordStore,
    Stage10RecordKind,
)
from tests.gold_store_fence import fence_for


def test_committed_record_recovers_and_corrupt_bytes_fail_closed(tmp_path: Path) -> None:
    authority_root = tmp_path / "stage10-authority"
    authority_root.mkdir()
    fence = fence_for(authority_root)
    store_root = authority_root / "records"
    store = FileStage10RecordStore(store_root)
    payload = encode_canonical({"schema_version": "acceptance.persisted/v1", "value": 7})

    with store_transaction(fence) as ticket:
        ref = store.put(
            kind=Stage10RecordKind.INTENT,
            record_key="accepted-record",
            canonical_payload=payload,
            ticket=ticket,
        )

    reopened = FileStage10RecordStore(store_root)
    assert reopened.get(kind=Stage10RecordKind.INTENT, ref=ref).payload == payload
    stored_path = store_root / Stage10RecordKind.INTENT.value / f"{ref.ref_id}.stage10"
    stored_path.write_bytes(stored_path.read_bytes() + b"torn-tail")

    with pytest.raises(ValueError):
        reopened.get(kind=Stage10RecordKind.INTENT, ref=ref)
