"""Execute a durable investigation over one authentic storage history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time

from synapse.application import DurableRunRequest, execute_durable_run
from synapse.durable_cognitive import read_cognitive_session
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.factory import MemoryFactory
from synapse.memory_consolidation.owner import MemoryOwner

from acceptance.memory.storage_evacuation.cluster import Cluster
from acceptance.memory.storage_evacuation.scenario import execute as evacuate

from .oracle import verify_temporal_facts
from .questions import questions, source
from .reference import search as reference_search

RUN_ID = "storage-incident-audit"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare(root, corpus):
    if corpus is None:
        corpus = root.parent / (root.name + "-corpus")
        evacuate(corpus)
    corpus = Path(corpus).resolve()
    initial = MemoryOwner(corpus / "memory", read_only=True).state()
    assert len(initial["knowledge"]["versions"]) == 3168 and initial["window"] == 4
    root.mkdir(parents=True, exist_ok=False)
    # Authoritative records retain their original project identity and source paths.
    # The original corpus remains present for their immutable artifact references.
    shutil.copytree(corpus / "memory", root / "memory")
    (root / "runs").mkdir()
    (root / "investigate.syn").write_text(source())
    configuration = parse_memory_configuration(json.loads((corpus / "configuration.json").read_text()))
    (root / "configuration.json").write_text(json.dumps(configuration.raw, indent=2))
    return corpus, configuration, digest(initial["knowledge"])


def run_investigation(root, factory):
    started, cpu = time.perf_counter(), time.process_time()
    result = execute_durable_run(DurableRunRequest(
        root / "investigate.syn", root / "runs", run_id=RUN_ID, memory=factory,
    ))
    measured = {"execution_with_consolidation_wall_s": time.perf_counter() - started,
                "execution_with_consolidation_cpu_s": time.process_time() - cpu,
                "status": result.status, "exit_code": result.exit_code}
    assert result.exit_code == 0 and result.status == "COMPLETED", result
    (root / "public-result.json").write_text(json.dumps(result.public_payload, indent=2))
    return measured


def verify_reference(factory, session):
    history = session["history"]
    opening, = [event for event in history if event["type"] == "memory_session_opened"]
    boundary, = [item for item in factory.owner.boundaries().values() if item["id"] == opening["boundary"]]
    assert boundary["boundary"]["window"] == 4
    versions = boundary["boundary"]["knowledge"]
    assert len(versions) == 3168
    events = [event for event in history if event["type"] == "knowledge_searched"]
    requests = questions()
    assert len(events) == len(requests) == 64
    for question, event in zip(requests, events):
        expected = reference_search(question["query"], versions, factory.configuration.knowledge,
                                    **question["options"], embed=None)
        assert event["query"] == question["query"] and event["options"] == question["options"]
        assert {key: event[key] for key in expected} == expected
        assert event["candidate_ids"] == [item["id"] for item in expected["candidates"]]
    return events


def replay(root, factory, transport):
    session = read_cognitive_session(root / "runs" / (RUN_ID + ".json"))
    assert session["integrity_error"] is None
    cuts = [index for index, event in enumerate(session["history"])
            if event["type"] == "session_consolidated"]
    assert len(cuts) == 1
    session["to"] = cuts[0]
    before = digest(factory.owner.state())
    started, cpu = time.perf_counter(), time.process_time()
    result = factory.ports().replay(session)
    measured = {"wall_s": time.perf_counter() - started, "cpu_s": time.process_time() - cpu,
                "result": result}
    assert result["status"] == "replay_verified", result
    assert transport.calls == 0 and digest(factory.owner.state()) == before
    return measured


def execute(root, corpus=None):
    root = Path(root).resolve()
    corpus, configuration, initial_knowledge = prepare(root, corpus)
    transport = Cluster()
    factory = MemoryFactory(root / "memory", configuration, transport=transport)
    measured = run_investigation(root, factory)
    assert transport.calls == 0
    session = read_cognitive_session(root / "runs" / (RUN_ID + ".json"))
    assert session["integrity_error"] is None
    events = verify_reference(factory, session)
    findings = verify_temporal_facts(corpus, questions(), events)
    state = factory.owner.state()
    assert state["window"] == 5 and digest(state["knowledge"]) == initial_knowledge
    replayed = replay(root, factory, transport)
    (root / "knowledge-searched.json").write_text(json.dumps(events, indent=2))
    result = {**measured, **findings, "queries": len(events),
              "distinct_query_texts": len({event["query"] for event in events}),
              "initial_window": 4, "final_window": state["window"], "knowledge_versions": 3168,
              "knowledge_unchanged_sha256": initial_knowledge, "frozen_reference_equal": True,
              "full_search_events_sha256": digest(events), "transport_calls": transport.calls,
              "replay_transport_calls": 0, "replay": replayed, "corpus_root": str(corpus),
              "original_source_paths_preserved": True,
              "scope": "Timed durable execution includes consolidation; authentic corpus creation, "
                       "cloning, reference comparisons, state inspection and replay are separate."}
    (root / "summary.json").write_text(json.dumps(result, indent=2))
    return result
