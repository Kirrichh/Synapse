"""Joint source publications deliver only the task's selected source paths."""

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from acceptance.stage4.stage16._source_delivery_case import SOURCES, capture, publish_case, source_records
from acceptance.stage4.stage16._source_inputs import prepare, learn
from synapse.experiments.gold.source_ingestion import SOURCE_INGESTION_V2
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.source_snapshot import read_source_snapshot
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V2, canonical
from synapse.experiments.gold.stage10.context_codec import decode_base64url


@pytest.fixture(scope="module")
def published_sources(tmp_path_factory):
    return publish_case(tmp_path_factory.mktemp("joint-source-scope"))


def test_joint_publication_excludes_unselected_bodies_and_symbols(published_sources):
    snapshot, held = capture(published_sources, "routing-only", ("routing/ring.py",))
    before = canonical(snapshot)
    record, = source_records(snapshot)
    assert record["matched_paths"] == ["routing/ring.py"]
    assert [item["path"] for item in record["sources"]] == ["routing/ring.py"]
    assert {item["path"] for item in record["symbols"]} == {"routing/ring.py"}
    assert decode_base64url(record["sources"][0]["content_base64url"]) == SOURCES["routing/ring.py"].encode()
    assert {item["path"] for item in held["active_memory_frame"]["elements"]} == {"routing/ring.py"}
    # Physical ownership retains the complete indivisible verification proof.
    experience, = [item["experience"] for item in snapshot["recall"]["selected"]]
    assert {item["path"] for item in experience["sources"]} == set(SOURCES)
    evidence_refs = {item["ref"]["sha256"] for item in experience["sources"]}
    for element in held["active_memory_frame"]["elements"]:
        assert {item["sha256"] for item in element["memory"]["Evidence"]["source_refs"]} == evidence_refs
    assert canonical(snapshot) == before
    assert read_source_snapshot(snapshot) == snapshot["recall"]


def test_task_declared_dependency_survives_source_projection(published_sources):
    paths = ("consensus/quorum.py", "routing/ring.py")
    snapshot, held = capture(published_sources, "routing-with-quorum", paths)
    record, = source_records(snapshot)
    assert record["matched_paths"] == list(paths)
    delivered = {item["path"]: decode_base64url(item["content_base64url"])
                 for item in record["sources"]}
    assert delivered == {path: SOURCES[path].encode() for path in paths}
    assert {item["path"] for item in record["symbols"]} == set(paths)
    # Dependency knowledge does not create a new effect or element owner.
    assert {item["path"] for item in held["active_memory_frame"]["elements"]} == {"routing/ring.py"}
    assert record["verification"] == "VERIFIED" and record["applicability"] == "UNASSESSED"
    isolated = published_sources[0] / "delivered-dependency"
    for path, raw in delivered.items():
        destination = isolated / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    run = subprocess.run([sys.executable, "-B", "-c",
        "from routing.ring import route_owner; "
        "assert all(route_owner(p, e, range(13)) == (p * 17 + e) % 24 "
        "for p in range(1536) for e in range(8)); print('DELIVERED_DEPENDENCY_OK')"],
        cwd=isolated, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0 and run.stdout.strip() == "DELIVERED_DEPENDENCY_OK", run.stderr


def test_projection_does_not_relax_physical_source_proof(published_sources):
    snapshot, _ = capture(published_sources, "proof-integrity", ("routing/ring.py",))
    changed = deepcopy(snapshot)
    experience = changed["recall"]["selected"][0]["experience"]
    experience["sources"] = [item for item in experience["sources"] if item["path"] != "billing/ledger.py"]
    with pytest.raises(ValueError, match="physical history"):
        read_source_snapshot(changed)
    assert read_source_snapshot(snapshot) == snapshot["recall"]
    # Omission at the worker port never permits a corrupted neighboring proof.
    held = snapshot["recall"]["selected"][0]["experience"]
    omitted = next(item for item in held["sources"] if item["path"] == "billing/ledger.py")
    stored = (published_sources[2] / "publications" / "prepared"
              / held["publication"]["transaction_id"] / omitted["ref"]["sha256"])
    raw = stored.read_bytes()
    stored.write_bytes(b"!" + raw[1:])
    try:
        with pytest.raises((ValueError, PersistenceViolation)):
            read_source_snapshot(snapshot)
    finally:
        stored.write_bytes(raw)
    assert read_source_snapshot(snapshot) == snapshot["recall"]


def test_uninterpreted_material_keeps_only_selected_path_metadata(tmp_path):
    notes = {"routing/notes.md": "Epoch migration notes: quorum remains mandatory.\n",
             "billing/notes.md": "Unrelated settlement currency hypothesis.\n"}
    repo, state, path = prepare(tmp_path, extra_sources=SOURCES | notes)
    value = json.loads(path.read_text())
    value["schema_version"] = SOURCE_INGESTION_V2
    value["claim"].update(schema_version=SOURCE_CLAIM_V2, kind="SOURCE_STUDY", symbols=[],
                          sources=sorted(SOURCES | notes))
    path.write_text(json.dumps(value))
    code, result = learn(state, path)
    assert code == 0 and result["status"] == "RETAINED", result
    case = tmp_path, repo, state, value["claim"]["revision"]
    snapshot, _ = capture(case, "notes", ("routing/notes.md", "routing/ring.py"))
    record, = source_records(snapshot)
    assert {item["path"] for item in record["uninterpreted"]} == {"routing/notes.md"}
    assert {item["path"] for item in record["sources"]} == {"routing/notes.md", "routing/ring.py"}
    assert {item["path"] for item in record["symbols"]} == {"routing/ring.py"}
    held = snapshot["recall"]["selected"][0]["experience"]
    assert {item["path"] for item in held["uninterpreted"]} == set(notes)
