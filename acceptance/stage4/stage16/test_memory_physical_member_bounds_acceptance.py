"""A memory reader bounds physical publication descriptors before loading bytes."""
import json

import pytest

from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold import persistence as P
from synapse.experiments.gold.memory_event_storage import MemoryEventStorage


def _commit(tmp_path, bodies):
    root = tmp_path / "events"
    P.ensure_directory(root)
    transaction = P.new_operation_id()
    fence = FileSnapshotFence(tmp_path / "coordinator")
    with P.store_transaction(fence) as ticket:
        members = P.stage_snapshot_transaction(root, transaction_id=transaction, members=bodies, ticket=ticket)
        P.commit_snapshot_transaction(root, transaction_id=transaction, members=members,
                                      boundary_id="memory-root", marker_sha256="a" * 64, ticket=ticket)
    return root, transaction


def test_oversized_declared_member_is_refused_before_its_file_is_opened(tmp_path):
    root, transaction = _commit(tmp_path, {"event.json": b"retained"})
    path = root / transaction / "event.json"
    path.unlink()
    with pytest.raises(P.PersistenceViolation) as caught:
        P.read_committed_snapshot_transaction(root, transaction_id=transaction, maximum_member_bytes=7)
    assert caught.value.failure_code is P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED
    # Without the explicit memory bound, the same marker proceeds to the missing file.
    with pytest.raises(P.PersistenceViolation) as caught:
        P.read_committed_snapshot_transaction(root, transaction_id=transaction)
    assert caught.value.failure_code is not P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_extra_members_are_refused_before_any_of_their_files_are_opened(tmp_path):
    bodies = {"first.json": b"first", "second.json": b"second"}
    root, transaction = _commit(tmp_path, bodies)
    assert P.read_committed_snapshot_transaction(root, transaction_id=transaction)[1] == bodies
    for name in bodies:
        (root / transaction / name).unlink()
    with pytest.raises(P.PersistenceViolation) as caught:
        P.read_committed_snapshot_transaction(root, transaction_id=transaction,
                                              maximum_member_bytes=16 * 1024 * 1024, maximum_members=1)
    assert caught.value.failure_code is P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED
    with pytest.raises(P.PersistenceViolation) as caught:
        MemoryEventStorage(tmp_path).read(transaction)
    assert caught.value.failure_code is P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_a_member_at_the_explicit_bound_keeps_its_exact_bytes(tmp_path):
    bodies = {"event.json": b"retained"}
    root, transaction = _commit(tmp_path, bodies)
    assert P.read_committed_snapshot_transaction(root, transaction_id=transaction,
        maximum_member_bytes=8, maximum_members=1)[1] == bodies


def test_memory_reader_refuses_an_oversized_member_before_loading_it(tmp_path):
    root, transaction = _commit(tmp_path, {"event.json": b"retained"})
    path = root / transaction / "commit-marker.json"
    marker = json.loads(path.read_text())
    marker["members"][0]["byte_length"] = 16 * 1024 * 1024 + 1
    path.write_text(json.dumps(marker))
    (root / transaction / "event.json").unlink()
    with pytest.raises(P.PersistenceViolation) as caught:
        MemoryEventStorage(tmp_path).read(transaction)
    assert caught.value.failure_code is P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED
