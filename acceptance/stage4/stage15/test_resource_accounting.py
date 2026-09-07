"""Actual persistence/VM work, exclusive counters and recording failure."""

from synapse.resource_usage import recording_resources, measure_operation
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.persistence import initialize_journal, store_transaction, append_journal_payload, read_regular_bytes
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture
from synapse.experiments.gold.stage15.resource_accounting import ResourceRecorder, ResourceEvidence
from synapse.experiments.gold.stage15.telemetry import reference


def test_nested_file_work_is_measured_once_and_unavailable_inventory_is_not_zero(tmp_path):
    outcome = reference({"completed": True}, "acceptance.result/v1")
    store = CaptureStore(tmp_path / "capture", run_id="resource-run", manifest_ref=outcome)
    recorder = ResourceRecorder(store)
    fence = FileSnapshotFence(tmp_path / "data-coordinator")
    path = tmp_path / "data.v1"
    initialize_journal(path)
    with recording_resources(recorder):
        with measure_operation("runtime.execution") as root:
            with measure_operation("knowledge.snapshot"):
                with fence.exclusive() as guard, store_transaction(fence, guard=guard) as ticket:
                    append_journal_payload(path, b"retained source payload", ticket=ticket)
                with measure_operation("knowledge.read") as read:
                    raw = read_regular_bytes(path, maximum_bytes=4096)
            root.bind_result(outcome.to_dict())
    recorder.seal(outcome)
    assert recorder.failures == []
    evidence = ResourceEvidence(cut=store.cut(), run_id=store.run_id, outcome_ref=outcome)
    assert evidence.report().status == "COMPLETE"
    records = evidence.infrastructure_records()
    assert len(records) == 3
    assert read.finished["io_read_bytes"] is not None
    assert int(read.finished["io_read_bytes"]) >= len(raw) > 0
    assert all(int(record["cpu_ns"]) > 0 for record in records)
    assert all(int(record["io_write_bytes"]) > 0 for record in records)
    assert sum(int(record["cpu_ns"]) for record in records) == int(root.finished["ended_cpu_ns"]) - int(root.started["started_cpu_ns"])
    assert sum(int(record["wall_ns"]) for record in records) == int(root.finished["ended_monotonic_ns"]) - int(root.started["started_monotonic_ns"])
    assert sum(int(bucket["wall_ns"]) for bucket in evidence.report().to_dict()["source_totals"].values()) == sum(int(r["wall_ns"]) for r in records)
    missing = ResourceEvidence(cut=None, run_id=store.run_id, outcome_ref=outcome).report().to_dict()
    assert missing["status"] == "INCOMPLETE"
    assert all(bucket["cpu_ns"] is None and bucket["io_write_bytes"] is None for bucket in missing["source_totals"].values())


def test_failed_completion_receipt_preserves_effect_and_exposes_unknown_cost(tmp_path, monkeypatch):
    outcome = reference({"completed": True}, "acceptance.result/v1")
    store = CaptureStore(tmp_path / "capture", run_id="receipt-run", manifest_ref=outcome)
    recorder = ResourceRecorder(store)
    original = store.record_resource

    def fail_finish(kind, payload):
        if kind == "RESOURCE_FINISHED":
            raise OSError("receipt device unavailable")
        return original(kind, payload)

    monkeypatch.setattr(store, "record_resource", fail_finish)
    effect = tmp_path / "completed-effect"
    with recording_resources(recorder), measure_operation("runtime.execution"):
        effect.write_bytes(b"already completed")
    recorder.seal(outcome)
    assert effect.read_bytes() == b"already completed"
    evidence = ResourceEvidence(cut=store.cut(), run_id=store.run_id, outcome_ref=outcome)
    assert {gap["code"] for gap in evidence.gaps} >= {"resource_operation_unfinished", "resource_recording_failed"}
    record, = evidence.infrastructure_records()
    assert record["cpu_ns"] is None and record["wall_ns"] is None
    assert record["result_class"] == "UNKNOWN"
    before = (store.root / "calls.v1").read_bytes()
    inspect_capture(store.resource_execution_cut())
    assert (store.root / "calls.v1").read_bytes() == before


def test_actual_recorded_activity_enters_vm_host_callback_once(tmp_path):
    from tests.stage4_gold_replay_support import effect_fixture, rebuild_recorded_activity, channel_for, vm_adapter

    outcome = reference({"completed": True}, "acceptance.result/v1")
    store = CaptureStore(tmp_path / "capture", run_id="vm-run", manifest_ref=outcome)
    recorder = ResourceRecorder(store)
    expected, program, records = effect_fixture()
    activity = rebuild_recorded_activity(records[0]["payload"])
    channel = channel_for(activity, budget=8)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)
    with recording_resources(recorder), measure_operation("runtime.execution"):
        with measure_operation("replay.execute") as replay:
            while not adapter.is_halted() and adapter.next_opcode() is not None:
                adapter.step()
    assert adapter.snapshot_digest() == expected["expected_terminal_snapshot_digest"]
    assert channel.consumed_identities() == (expected["activity_identity"],)
    assert replay.finished["host_calls"] == 1
    assert int(replay.finished["ended_monotonic_ns"]) > int(replay.started["started_monotonic_ns"])
    assert recorder.failures == []


def test_false_nested_duration_is_refused_at_the_durable_capture_boundary(tmp_path, monkeypatch):
    outcome = reference({"completed": True}, "acceptance.result/v1")
    store = CaptureStore(tmp_path / "capture", run_id="clock-run", manifest_ref=outcome)
    recorder = ResourceRecorder(store)
    original = store.record_resource

    def corrupt_child_total(kind, payload):
        if kind == "RESOURCE_FINISHED" and payload["children"]:
            payload = {**payload, "children_cpu_ns": "0", "children_wall_ns": "0"}
        return original(kind, payload)

    monkeypatch.setattr(store, "record_resource", corrupt_child_total)
    with recording_resources(recorder), measure_operation("runtime.execution"):
        with measure_operation("knowledge.read"):
            read_regular_bytes(store.root / "calls.v1", maximum_bytes=100000)
    recorder.seal(outcome)
    evidence = ResourceEvidence(cut=store.cut(), run_id=store.run_id, outcome_ref=outcome)
    assert recorder.failures and evidence.report().status == "INCOMPLETE"
    assert any(gap["code"] == "resource_operation_unfinished" for gap in evidence.gaps)
