"""A separately schedulable owner-retention case with actual durable writes."""
import pytest

from synapse.agents.retention import InvocationStore
from synapse.agents.invocation_observation import InvocationObservation
from synapse.commands.binding import CommandRefused


def test_compacted_projection_requires_resnapshot_but_preserves_execution_records(tmp_path):
    owner = InvocationStore(tmp_path / "retained")
    for index in range(2050):
        owner.claim(f"invocation-{index}", b'{}')
    reader = InvocationStore.open_existing(owner.root)
    snapshot = InvocationObservation(reader).snapshot(("invocation-0", "invocation-2049"))
    assert snapshot["position"] == 2050
    assert all(item["state"] == "EXECUTION_STATE_UNKNOWN" for item in snapshot["items"])
    with pytest.raises(CommandRefused, match="CURSOR_UNAVAILABLE"):
        InvocationObservation(reader).after(snapshot["generation"], 1, ("invocation-0",))
    progress = InvocationObservation(reader).after(snapshot["generation"], 2, ("invocation-2049",), limit=32)
    assert (progress["from"], progress["to"], progress["changes"]) == (2, 34, [])
    with reader.connect() as db:
        # This is the public numeric retention bound on actual owner data, not
        # a source/file-count architecture check.
        assert db.execute("SELECT count(*) FROM observation_changes").fetchone()[0] == 2048
    assert InvocationObservation(reader).inspect("invocation-0")["state"] == "EXECUTION_STATE_UNKNOWN"
