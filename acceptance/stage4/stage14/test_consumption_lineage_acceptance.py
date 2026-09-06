"""Publication must retain the actual consumption proof used by its execution."""
from pathlib import Path

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold import persistence
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage14.graph import LineageViolation, LineageFailureCode
from synapse.experiments.gold.stage14.sources import read_input_graph


def test_publication_reopens_replay_and_worker_consumption_history(tmp_path, monkeypatch):
    attempt = negative_attempt(tmp_path / "attempt")
    case = publication_case(tmp_path / "project", attempt)
    result = case.publisher.publish(case.request)
    expected = result.payload()
    catalog = attempt.world.composition.record_store.get(kind=RecordKind.LINEAGE_SOURCES, key="1").payload
    path = Path(catalog["admission"]["path"])
    original_bytes = path.read_bytes()
    frames = persistence.scan_journal(path).frames
    consumption = [frame for frame in frames
                   if decode_canonical(frame.payload)["payload"]["gate_kind"] == "CONSUMPTION"]
    open_original = persistence._open_no_follow_read

    # Only the physical source read changes. The catalogue, publication commit,
    # verification and real readers stay intact; no graph validator is patched.
    missing = tmp_path / "admission-without-consumption.journal"
    missing.write_bytes(original_bytes[:consumption[0].start_offset])
    with monkeypatch.context() as patch:
        patch.setattr(persistence, "_open_no_follow_read",
            lambda candidate: open_original(missing if candidate == path else candidate))
        with pytest.raises(LineageViolation) as failure:
            read_input_graph(catalog)
        assert failure.value.failure_code is LineageFailureCode.MISSING_RECORD
        with pytest.raises(LineageViolation):
            result.payload()

    # Keep the replay's decision but lose a later committed occurrence. A
    # repeated identical decision ID must not stand in for the worker's prefix.
    rolled_back = tmp_path / "admission-before-worker.journal"
    rolled_back.write_bytes(original_bytes[:consumption[0].end_offset])
    with monkeypatch.context() as patch:
        patch.setattr(persistence, "_open_no_follow_read",
            lambda candidate: open_original(rolled_back if candidate == path else candidate))
        read_input_graph(catalog)
        with pytest.raises(LineageViolation):
            result.payload()
    assert result.payload() == expected
    assert path.read_bytes() == original_bytes
