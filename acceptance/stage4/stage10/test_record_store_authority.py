from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    store_transaction,
)
from synapse.experiments.gold.stage10.context_codec import decode_canonical, encode_canonical
from synapse.experiments.gold.stage10.record_store import (
    FileStage10RecordStore,
    RecordStoreFailureCode,
    STAGE10_STORE_SCHEMA_V1,
    Stage10RecordKind,
    Stage10RecordStoreViolation,
)
from tests.gold_store_fence import fence_for


def _ref_for_raw(raw: bytes, *, ref_id: str | None = None) -> HashBoundRef:
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest if ref_id is None else ref_id,
        schema_id=STAGE10_STORE_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def _stored_record(tmp_path: Path) -> tuple[FileStage10RecordStore, Path, HashBoundRef]:
    owner = tmp_path / "owner"
    owner.mkdir()
    fence = fence_for(owner)
    root = owner / "records"
    store = FileStage10RecordStore(root, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        ref = store.put(
            kind=Stage10RecordKind.INTENT,
            record_key="authority-record",
            canonical_payload=encode_canonical({"schema_version": "acceptance.record/v1"}),
            ticket=ticket,
        )
    return store, root, ref


def test_record_store_rejects_a_foreign_coordinator_before_staging(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    foreign = tmp_path / "foreign"
    owner.mkdir()
    foreign.mkdir()
    owner_fence = fence_for(owner)
    store = FileStage10RecordStore(owner / "records", mutation_fence=owner_fence)

    with store_transaction(fence_for(foreign)) as foreign_ticket:
        with pytest.raises(PersistenceViolation) as raised:
            store.put(
                kind=Stage10RecordKind.INTENT,
                record_key="foreign-write",
                canonical_payload=encode_canonical({"schema_version": "acceptance.record/v1"}),
                ticket=foreign_ticket,
            )

    assert raised.value.failure_code is PersistenceFailureCode.MUTATION_COORDINATOR_MISMATCH
    assert not any(
        path.is_file()
        for path in (owner / "records").rglob("*")
    )


def test_record_store_rejects_canonical_wrapper_with_false_payload_digest(tmp_path: Path) -> None:
    store, root, ref = _stored_record(tmp_path)
    original = root / Stage10RecordKind.INTENT.value / f"{ref.ref_id}.stage10"
    decoded = decode_canonical(original.read_bytes())
    assert isinstance(decoded, dict)
    decoded["payload_sha256"] = "0" * 64
    tampered_raw = encode_canonical(decoded)
    tampered_ref = _ref_for_raw(tampered_raw)
    tampered_path = root / Stage10RecordKind.INTENT.value / f"{tampered_ref.ref_id}.stage10"
    tampered_path.write_bytes(tampered_raw)

    with pytest.raises(Stage10RecordStoreViolation) as raised:
        store.get(kind=Stage10RecordKind.INTENT, ref=tampered_ref)

    assert raised.value.failure_code is RecordStoreFailureCode.RECORD_CORRUPT


def test_record_store_rejects_bytes_published_under_an_unrelated_cas_name(tmp_path: Path) -> None:
    store, root, ref = _stored_record(tmp_path)
    original = root / Stage10RecordKind.INTENT.value / f"{ref.ref_id}.stage10"
    wrong_id = "f" * 64 if ref.ref_id != "f" * 64 else "e" * 64
    wrong_path = root / Stage10RecordKind.INTENT.value / f"{wrong_id}.stage10"
    raw = original.read_bytes()
    wrong_path.write_bytes(raw)

    with pytest.raises(Stage10RecordStoreViolation) as raised:
        store.get(
            kind=Stage10RecordKind.INTENT,
            ref=_ref_for_raw(raw, ref_id=wrong_id),
        )

    assert raised.value.failure_code is RecordStoreFailureCode.RECORD_CORRUPT
