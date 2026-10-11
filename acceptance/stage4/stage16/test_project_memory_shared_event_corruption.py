"""Physical sharing cannot conceal invalid bytes from any trusted journal read."""
from copy import deepcopy

import pytest

from acceptance.stage4.stage16 import _shared_memory_events as E
from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.stage10.context_codec import ContextCodecViolation


def _damage(path, mode, outside):
    raw = path.read_bytes()
    if mode == "changed":
        path.write_bytes(b"!" + raw[1:])
    else:
        path.unlink()
        if mode == "symlink":
            outside.write_bytes(raw)
            path.symlink_to(outside)


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink"])
@pytest.mark.parametrize("entry", ["cached_receipt", "excluded_selection", "new_owner_session"])
def test_damaged_shared_block_is_rejected_even_when_its_event_was_cached_or_excluded(tmp_path, damage, entry):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    with store.session() as guard:
        receipt = E.put(store, guard, value)
    assert store.read(receipt) == value
    path = sorted(E.blocks(store, receipt))[0]
    _damage(path, damage, tmp_path / "external-copy.bin")
    with pytest.raises((PersistenceViolation, ValueError)):
        if entry == "cached_receipt":
            store.read(receipt)
        elif entry == "excluded_selection":
            M.ProjectMemoryStore(tmp_path, read_only=True).inventory(kind="STARTED")
        else:
            with store.session() as guard:
                store.inventory(kind="STARTED", guard=guard)


def _change_manifest(layout, violation):
    changed = deepcopy(layout)
    if violation == "logical_digest":
        changed["logical_sha256"] = "0" * 64
    elif violation == "logical_length":
        changed["logical_byte_length"] += 1
    elif violation == "chunk_length":
        changed["chunks"][0]["byte_length"] += 1
    elif violation == "chunk_address":
        changed["chunks"][0]["sha256"] = "../outside"
    elif violation == "reordered":
        changed["chunks"] = list(reversed(changed["chunks"]))
    elif violation == "unknown_schema":
        changed["schema_version"] = "synapse.stage4.gold.memory-event-blocks/v0"
    elif violation == "extra_field":
        changed["trusted"] = True
    return changed


@pytest.mark.parametrize("violation", ["logical_digest", "logical_length", "chunk_length", "chunk_address",
                                      "reordered", "unknown_schema", "extra_field"])
def test_a_valid_physical_commit_marker_does_not_make_an_invalid_manifest_trusted(tmp_path, violation):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    with store.session() as guard:
        receipt = E.put(store, guard, value)
        changed = _change_manifest(E.manifest(store, receipt), violation)
        forged = E.commit_members(store, guard, {"event-manifest.json": M.canonical(changed)},
                                  logical=M.canonical(value), job_key=value["job_key"])
    with pytest.raises((PersistenceViolation, ValueError)):
        M.ProjectMemoryStore(tmp_path, read_only=True).read(forged)


@pytest.mark.parametrize("violation", ["whitespace", "duplicate_key", "nonfinite", "unknown_field"])
def test_strict_decoder_still_rejects_old_event_bytes_with_an_honest_physical_commit(tmp_path, violation):
    store, value = M.ProjectMemoryStore(tmp_path), E.event(body={"value": 1})
    raw = M.canonical(value)
    if violation == "whitespace":
        raw += b"\n"
    elif violation == "duplicate_key":
        raw = raw.replace(b'"value":1', b'"value":1,"value":1')
    elif violation == "nonfinite":
        raw = raw.replace(b'"value":1', b'"value":NaN')
    else:
        raw = M.canonical({**value, "trusted": True})
    with store.session() as guard:
        receipt = E.commit_legacy(store, guard, value, raw=raw)
    with pytest.raises((ContextCodecViolation, ValueError)):
        M.ProjectMemoryStore(tmp_path, read_only=True).read(receipt)


def test_shared_block_membership_does_not_allow_a_different_logical_publication(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    original, replacement = E.event(), E.event(job="b", body=E.payload(source="other-source"))
    with store.session() as guard:
        receipt = E.put(store, guard, original)
        forged = E.commit_members(store, guard,
            {"event-manifest.json": M.canonical(E.manifest(store, receipt))},
            logical=M.canonical(replacement), job_key=replacement["job_key"])
    with pytest.raises((PersistenceViolation, ValueError)):
        M.ProjectMemoryStore(tmp_path, read_only=True).read(forged)
