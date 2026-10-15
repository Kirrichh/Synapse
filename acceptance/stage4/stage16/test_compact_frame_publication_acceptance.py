"""Heavy shard: bound frame publication preserves historical retry and worker scope."""
from copy import deepcopy
import json

import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold import project_agents
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_frame_storage import COMPACT_FRAME_V1, MAX_FRAME_PUBLICATION_BYTES, compact_frame, restore_frame
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore, memory_job_identity
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.experiments.gold.source_snapshot import SOURCE_SNAPSHOT_V3, capture_project_source_snapshot, memory_source_basis
from synapse.experiments.gold.source_delivery import _memory_information, source_experience_delivery
from synapse.experiments.gold.source_verification import canonical
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
from synapse.experiments.gold.task_targets import resolve_task_targets
from synapse.memory_consolidation.project_port import ProjectMemoryCourt


def _world(root):
    case, _ = consumer_case(root, automatic_targets=True)
    project = open_gold_project(root / "state")
    declaration = json.loads(case.input_path.read_text())
    task = GoverningTaskContract.from_dict(declaration["task_contract"])
    source, _, _ = capture_project_source_snapshot(project=project, task=task, limit=64)
    request = {"project_identity": source["project_record_sha256"], "run_root": str(case.run_root),
        "run_id": declaration["run_id"], "repository_root": str(case.repo), "source_snapshot": source,
        "target_resolution": json.loads(resolve_task_targets(task=task, repository_root=case.repo)),
        "run_memory_selection": "ALL"}
    return case, project, source, request


def _publish(store, request, *, profile, compact=False, alter=None):
    request = deepcopy(request)
    request["profile"] = profile
    if profile in {project_agents.OWNER_LIFECYCLE_V3, project_agents.OWNER_LIFECYCLE_V4}:
        request["court"] = None
    else:
        request.update(prior_outcomes=[], prior_learning=[])
    if compact:
        request["frame_storage"] = COMPACT_FRAME_V1
    job = memory_job_identity(request["project_identity"], request["run_root"], request["run_id"])
    with store.session() as guard:
        requested = store.put(kind="REQUESTED", job_key=job, payload=request, guard=guard)
        started = store.put(kind="STARTED", job_key=job, payload={"request_ref": requested}, guard=guard)
        payload = project_agents._frame_payload(request, requested, started, store)
        if alter is not None:
            frame = restore_frame(payload["active_memory_frame"], max_bytes=MAX_FRAME_PUBLICATION_BYTES)
            alter(frame)
            payload["active_memory_frame"] = compact_frame(frame, max_bytes=MAX_FRAME_PUBLICATION_BYTES)
        receipt = store.put(kind="FRAME_COMPLETED", job_key=job, payload=payload, guard=guard)
    return {"profile": profile, "job_key": job, "frame_event": receipt}, payload


def test_new_capture_uses_exact_compaction_with_stable_freeze_retry(tmp_path):
    case, project, _, _ = _world(tmp_path)
    frozen = freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root,
                               court=ProjectMemoryCourt())
    snapshot = frozen.data["source_snapshot"]
    binding = snapshot["project_memory"]
    store = ProjectMemoryStore(tmp_path / "state", read_only=True)
    held = store.read(binding["frame_event"])["payload"]
    assert held["active_memory_frame"]["schema_version"] == COMPACT_FRAME_V1
    request = store.read(held["request_ref"])["payload"]
    assert request["frame_storage"] == COMPACT_FRAME_V1
    expanded = project_agents.read_active_memory(binding, source_snapshot=memory_source_basis(snapshot), run_memory_selection="ALL")
    legacy_request = {key: value for key, value in request.items() if key != "frame_storage"}
    expected = project_agents._frame_payload(legacy_request, held["request_ref"], held["started_ref"], store)
    assert canonical(expanded) == canonical(expected)
    before = source_experience_delivery(snapshot)
    assert canonical(_memory_information(expanded["active_memory_frame"])) == canonical(_memory_information(expected["active_memory_frame"]))
    inventory = store.inventory()
    repeated = freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root,
                                 court=ProjectMemoryCourt())
    assert canonical(repeated.data["source_snapshot"]) == canonical(snapshot)
    assert store.inventory() == inventory
    assert source_experience_delivery(repeated.data["source_snapshot"]) == before
    assert not case.run_root.exists()


@pytest.mark.parametrize("profile", project_agents.OWNER_LIFECYCLES)
def test_expanded_historical_publication_keeps_its_receipt_on_retry(tmp_path, profile):
    case, project, source, request = _world(tmp_path)
    store = ProjectMemoryStore(tmp_path / "state")
    binding, payload = _publish(store, request, profile=profile)
    before = store.inventory()
    result = project_agents.capture_active_memory(project=project, run_root=case.run_root, run_id=request["run_id"],
        target_resolution=request["target_resolution"], source_snapshot=source, run_memory_selection="ALL", court=ProjectMemoryCourt())
    assert result == binding
    assert store.inventory() == before
    assert project_agents.read_active_memory(result, source_snapshot=source, run_memory_selection="ALL") == payload
    assert payload["active_memory_frame"]["schema_version"] != COMPACT_FRAME_V1


def test_interrupted_legacy_request_keeps_its_started_receipts_and_expanded_shape(tmp_path):
    case, project, source, request = _world(tmp_path)
    request.update(profile=project_agents.OWNER_LIFECYCLE_V3, court=None)
    store = ProjectMemoryStore(tmp_path / "state")
    job = memory_job_identity(request["project_identity"], request["run_root"], request["run_id"])
    with store.session() as guard:
        requested = store.put(kind="REQUESTED", job_key=job, payload=request, guard=guard)
        started = store.put(kind="STARTED", job_key=job, payload={"request_ref": requested}, guard=guard)
    before = store.inventory()
    assert {event["kind"] for event, _ in before} == {"REQUESTED", "STARTED"}
    binding = project_agents.capture_active_memory(project=project, run_root=case.run_root, run_id=request["run_id"],
        target_resolution=request["target_resolution"], source_snapshot=source, run_memory_selection="ALL", court=ProjectMemoryCourt())
    payload = store.read(binding["frame_event"])["payload"]
    assert payload["request_ref"] == requested and payload["started_ref"] == started
    assert payload["active_memory_frame"]["schema_version"] == "synapse.stage4.gold.active-memory-frame/v3"
    assert [(event, receipt) for event, receipt in store.inventory() if event["kind"] != "FRAME_COMPLETED"] == before
    assert project_agents.read_active_memory(binding, source_snapshot=source, run_memory_selection="ALL") == payload
    assert not case.run_root.exists()


def test_self_consistent_neighbor_substitution_is_refused_before_worker_delivery(tmp_path):
    _, _, source, request = _world(tmp_path)
    store = ProjectMemoryStore(tmp_path / "state")
    neighbor = {"private_neighbor": "UNSELECTED_NEIGHBOR_DO_NOT_DELIVER"}
    with store.session() as guard:
        neighbor_ref = store.put(kind="MEMORY_STATE", job_key="f" * 64, payload=neighbor, guard=guard)
    assert store.read(neighbor_ref)["payload"] == neighbor

    def substitute(frame):
        frame["elements"][0]["memory"]["Context"]["allowed_scope"]["entries"].append(neighbor["private_neighbor"])

    binding, payload = _publish(store, request, profile=project_agents.OWNER_LIFECYCLE_V3, compact=True, alter=substitute)
    forged = restore_frame(payload["active_memory_frame"], max_bytes=MAX_FRAME_PUBLICATION_BYTES)
    assert neighbor["private_neighbor"].encode() in canonical(_memory_information(forged))
    with pytest.raises(ValueError, match="retained project evidence"):
        project_agents.read_active_memory(binding, source_snapshot=source, run_memory_selection="ALL")
    snapshot = {**source, "schema_version": SOURCE_SNAPSHOT_V3,
        "source_schema_version": source["schema_version"], "project_memory": binding, "run_memory_selection": "ALL"}
    with pytest.raises(ValueError, match="retained project evidence"):
        source_experience_delivery(snapshot)
