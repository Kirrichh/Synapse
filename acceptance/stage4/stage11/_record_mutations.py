"""Republish genuine run records with one deliberate semantic mutation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Callable

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.runner.models import canonical_run_bytes
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore
from synapse.experiments.gold.stage10.context_codec import encode_base64url
from tests.gold_store_fence import fence_for


RecordMutation = Callable[[str, str, dict[str, object]], dict[str, object]]


def clone_run_records(
    source: RunRecordStore,
    destination: Path,
    *,
    mutate: RecordMutation,
    omit_kinds: frozenset[str] = frozenset(),
) -> RunRecordStore:
    """Copy public store records through the production adapter after mutation."""

    fence = fence_for(destination / "coordinator")
    result = RunRecordStore(destination, mutation_fence=fence)
    for kind in RecordKind.ALL:
        if kind in omit_kinds:
            continue
        for key in source.iter_keys(kind=kind):
            stored = source.get(kind=kind, key=key)
            if stored is None:
                raise RuntimeError("source record disappeared during mutation copy")
            payload = mutate(kind, key, deepcopy(stored.payload))
            with store_transaction(fence) as ticket:
                result.put(
                    kind=kind,
                    key=key,
                    canonical_payload=payload,
                    ticket=ticket,
                )
    return result


def rehash_record(stored: dict[str, object]) -> dict[str, object]:
    """Keep the outer record identity valid so semantic validation is exercised."""

    payload = stored["payload"]
    if type(payload) is not dict:
        raise TypeError("run record payload must be exact")
    stored["record_sha256"] = hashlib.sha256(canonical_run_bytes(payload)).hexdigest()
    return stored


def replace_progress_payload(
    stored: dict[str, object],
    *,
    raw: bytes,
) -> dict[str, object]:
    payload = stored["payload"]
    if type(payload) is not dict or type(payload.get("payload_ref")) is not dict:
        raise TypeError("progress record has no exact payload ref")
    previous = HashBoundRef.from_dict(payload["payload_ref"])
    digest = hashlib.sha256(raw).hexdigest()
    payload["payload_ref"] = HashBoundRef(
        kind=previous.kind,
        ref_id=digest,
        schema_id=previous.schema_id,
        sha256=digest,
        byte_length=len(raw),
        media_type=previous.media_type,
    ).to_dict()
    payload["payload_base64url"] = encode_base64url(raw)
    return rehash_record(stored)


def replace_progress_ref_digest(
    stored: dict[str, object],
    *,
    digest: str,
) -> dict[str, object]:
    payload = stored["payload"]
    if type(payload) is not dict or type(payload.get("payload_ref")) is not dict:
        raise TypeError("progress record has no exact payload ref")
    claimed = dict(payload["payload_ref"])
    claimed["ref_id"] = digest
    claimed["sha256"] = digest
    payload["payload_ref"] = claimed
    return rehash_record(stored)
