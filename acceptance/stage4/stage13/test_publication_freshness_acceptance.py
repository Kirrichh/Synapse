"""Independent publication reads observe physical deletion and substitution."""

from contextlib import nullcontext
import hashlib
import json

import pytest

from acceptance.stage4.stage16._source_inputs import learn, prepare
from synapse.experiments.gold import source_experience
from synapse.experiments.gold.persistence import PersistenceViolation, read_committed_snapshot_transaction
from synapse.experiments.gold.stage10.context_codec import decode_canonical, encode_canonical
from synapse.experiments.gold.stage13.publication import PublicationViolation
from synapse.experiments.gold.stage13 import publication_store
from synapse.experiments.gold.stage13.publication_store import (
    PublicationResult, read_prepared_publication_request,
)
from synapse.experiments.gold.stage14.read_traversal import publication_read_scope


@pytest.fixture
def published(tmp_path):
    _, state, input_path = prepare(tmp_path)
    code, result = learn(state, input_path)
    assert code == 0, result
    payload = result["publication"]
    record = PublicationResult(state / "publications", payload["transaction_id"])
    assert record.retained_payload() == payload
    return state, record, payload


def replace_prepared_request(record):
    """Keep the storage transaction valid while changing its authorized request."""
    directory = record.root / "prepared" / record.transaction_id
    _, members = read_committed_snapshot_transaction(
        record.root / "prepared", transaction_id=record.transaction_id)
    request = decode_canonical(members["request.json"])
    request["domain"]["operation_id"] = "substituted-source-request"
    raw = encode_canonical(request)
    (directory / "request.json").write_bytes(raw)
    marker_path = directory / "commit-marker.json"
    marker = json.loads(marker_path.read_bytes())
    for member in marker["members"]:
        if member["member_name"] == "request.json":
            member.update(sha256=hashlib.sha256(raw).hexdigest(), byte_length=len(raw))
    marker_path.write_bytes(encode_canonical(marker))
    _, physical = read_committed_snapshot_transaction(
        record.root / "prepared", transaction_id=record.transaction_id)
    assert physical["request.json"] == raw


@pytest.mark.parametrize("ambient_scope", [False, True])
@pytest.mark.parametrize("damage", ["delete_request", "corrupt_lineage"])
def test_suspended_source_generator_cannot_hide_physical_damage(published, ambient_scope, damage):
    state, record, expected = published
    with publication_read_scope() if ambient_scope else nullcontext():
        iterator = source_experience.source_publications(state)
        try:
            result, _, _ = next(iterator)
            assert result == expected
            if damage == "delete_request":
                (record.root / "prepared" / record.transaction_id / "request.json").unlink()
                with pytest.raises(PersistenceViolation):
                    read_prepared_publication_request(record.root, record.transaction_id)
            else:
                (record.root / "committed" / record.transaction_id / "lineage.json").write_bytes(b"damaged")
            with pytest.raises(PersistenceViolation):
                record.retained_payload()
            with pytest.raises(PersistenceViolation):
                next(source_experience.source_publications(state))
        finally:
            iterator.close()


@pytest.mark.parametrize("warm_result", [False, True])
def test_prepared_substitution_is_not_combined_with_a_previous_request(published, warm_result):
    _, record, expected = published
    with publication_read_scope():
        original = read_prepared_publication_request(record.root, record.transaction_id)
        if warm_result:
            assert record.payload() == expected
        replace_prepared_request(record)
        current = read_prepared_publication_request(record.root, record.transaction_id)
        assert current != original
        with pytest.raises(PublicationViolation, match="lost its verified request"):
            record.payload()


def test_source_generator_rejects_request_replaced_after_result_read(published, monkeypatch):
    state, record, _ = published
    original = source_experience.read_prepared_publication_request

    def replace_before_request(root, transaction_id):
        replace_prepared_request(record)
        return original(root, transaction_id)

    monkeypatch.setattr(source_experience, "read_prepared_publication_request", replace_before_request)
    with pytest.raises(PublicationViolation, match="request changed after its verified result"):
        next(source_experience.source_publications(state))


def test_publication_uses_request_from_its_read_member_set(published, monkeypatch):
    """A later replacement cannot change the request of already read members."""
    _, record, expected = published
    original = publication_store.read_committed_snapshot_transaction
    replaced = False

    def replace_after_prepared_read(root, *, transaction_id):
        nonlocal replaced
        captured = original(root, transaction_id=transaction_id)
        if root == record.root / "prepared" and not replaced:
            replaced = True
            replace_prepared_request(record)
        return captured

    monkeypatch.setattr(publication_store, "read_committed_snapshot_transaction", replace_after_prepared_read)
    assert record.payload() == expected
    assert replaced
    with pytest.raises(PublicationViolation, match="lost its verified request"):
        record.payload()
