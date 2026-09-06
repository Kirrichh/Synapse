"""Crash between graph and result repairs only the immutable local suffix."""
import pytest

from acceptance.stage4.stage13._case import negative_attempt
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind


@pytest.mark.parametrize("terminal_kind,graph_kind,key", [
    (RecordKind.ATTEMPT_RESULT, RecordKind.ATTEMPT_LINEAGE, "1"),
    (RecordKind.RUN_RESULT, RecordKind.RUN_LINEAGE, "final"),
])
def test_graph_first_suffix_is_recovered_without_external_work(tmp_path, monkeypatch, terminal_kind, graph_kind, key):
    attempt = negative_attempt(tmp_path)
    world = attempt.world
    original = RunRecordStore.put

    def interrupted(self, **kwargs):
        if kwargs["kind"] == terminal_kind:
            assert self.get(kind=graph_kind, key=key) is not None
            raise SystemExit("acceptance terminal suffix interruption")
        return original(self, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(RunRecordStore, "put", interrupted)
        with pytest.raises(SystemExit):
            world.composition.controller.execute()
    graph = world.composition.record_store.get(kind=graph_kind, key=key).payload
    final = world.composition.controller.execute()
    assert world.composition.controller.load_result() == final
    assert world.composition.record_store.get(kind=graph_kind, key=key).payload == graph
    assert world.worker_process.calls == 1
