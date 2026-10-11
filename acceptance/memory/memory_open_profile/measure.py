"""Profile canonical memory opening on authentic incident corpus copies."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import sys
from types import ModuleType


def load(product_root):
    sys.path.insert(0, str(product_root))
    directory = Path(__file__).resolve().parent
    for name, path in (("acceptance", directory.parents[1]), ("acceptance.memory", directory.parent)):
        package = ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    name = "_memory_open_profile_acceptance"
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(name + ".scopes").ScopedProfiles


def product_hashes(product_root):
    return {str(path.relative_to(product_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((product_root / "synapse").rglob("*.py"))}


def corpus_hashes(corpus):
    return {str(path.relative_to(corpus)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(corpus.rglob("*")) if path.is_file()}


def recorded_run(dataset, name):
    from synapse.durable_cognitive import read_cognitive_session

    path = dataset / "runs" / (name + ".json")
    saved = read_cognitive_session(path)
    assert saved["integrity_error"] is None
    return {key: saved[key] for key in ("run_id", "source_hash", "source_code", "initial_bindings", "history")} | {
        "artifact_path": str(path)}


def profile_exam_and_reopen(capture, label, state_root, configuration, dataset, run_name):
    from synapse.memory_consolidation.factory import MemoryFactory
    from synapse.memory_consolidation.owner import MemoryOwner

    inspector = MemoryOwner(state_root, read_only=True)
    boundary = max(inspector.boundaries().values(), key=lambda item: item["boundary"]["window"])
    reader = MemoryOwner(state_root, read_only=True)
    factory = MemoryFactory(state_root, configuration, owner=reader,
                            exam={"mode": "B", "snapshot": boundary["id"]})
    exam_run = {"run_id": "memory-opening-exam", "history": []}
    capture.call(label + "-exam-fresh-reader", factory.open_session, exam_run)
    capture.call(label + "-exam-reused-reader", factory.open_session, exam_run)
    factory = MemoryFactory(state_root, configuration)
    run = recorded_run(dataset, run_name)
    expected = next(event["boundary"] for event in run["history"] if event["type"] == "memory_session_opened")
    first = capture.call(label + "-recorded-pin-fresh-reader", factory.open_session, run)
    second = capture.call(label + "-recorded-pin-reused-reader", factory.open_session, run)
    assert first.pinned()["boundary"] == second.pinned()["boundary"] == expected


def profile_reads(capture, label, dataset, root, run_name, *, full):
    from synapse.memory_consolidation.owner import MemoryOwner

    destination = root / label
    destination.mkdir()
    state_root = destination / "memory"
    shutil.copytree(dataset / "memory", state_root)
    owner = capture.call(label + "-owner-create", MemoryOwner, state_root, read_only=True)
    configuration = capture.call(label + "-configuration-fresh-reader", owner.bound_configuration)
    reused = capture.call(label + "-configuration-reused-reader", owner.bound_configuration)
    assert configuration.configuration_sha256 == reused.configuration_sha256
    if full:
        owner = MemoryOwner(state_root, read_only=True)
        first = capture.call(label + "-boundaries-fresh-reader", owner.boundaries)
        second = capture.call(label + "-boundaries-reused-reader", owner.boundaries)
        assert first == second
    owner = MemoryOwner(state_root, read_only=True)
    state = capture.call(label + "-state-fresh-reader", owner.state)
    assert state == capture.call(label + "-state-reused-reader", owner.state)
    owner = MemoryOwner(state_root)
    context = owner.store.session()
    guard = capture.call(label + "-owner-guard-entry", context.__enter__)
    try:
        if full:
            capture.call(label + "-boundaries-held-guard", owner.boundaries, guard=guard)
        assert state == capture.call(label + "-state-held-guard", owner.state, guard=guard)
    finally:
        capture.call(label + "-owner-guard-exit", context.__exit__, None, None, None)
    if full:
        profile_exam_and_reopen(capture, label, state_root, configuration, dataset, run_name)
    return {"window": state["window"], "knowledge_versions": len(state["knowledge"]["versions"])}


def brief_investigation(run_id, query):
    return '''
memory palace "SnapshotInvestigation" { rooms { semantic } consolidate during dream }
let req = {"kind": "attempt_report", "tool": null, "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "inspect", "intent": "Inspect retained storage incident knowledge", "element_part": "storage-engineering", "requirement": req}
let plan = task_plan({"task_id": RUN_ID, "goal": "Inspect one incident aspect without changing the cluster", "segments": [seg]})
context "inspect" {
 let found = search_knowledge(QUERY, {"valid_at": "2026-10-09T09:30:00Z", "known_as_of": 4, "channels": ["lexical"]})
 assert len(found.candidates) > 0, "Retained incident knowledge is missing"
 print({"aspect": QUERY, "candidates": len(found.candidates)})
}
'''.replace("RUN_ID", json.dumps(run_id)).replace("QUERY", json.dumps(query))


def snapshot_corpus(dataset, destination):
    from synapse.application import DurableRunRequest, execute_durable_run
    from synapse.memory_consolidation.configuration import parse_memory_configuration
    from synapse.memory_consolidation.factory import MemoryFactory
    from acceptance.memory.storage_evacuation.cluster import Cluster

    destination.mkdir()
    shutil.copytree(dataset / "memory", destination / "memory")
    shutil.copyfile(dataset / "configuration.json", destination / "configuration.json")
    (destination / "runs").mkdir()
    configuration = parse_memory_configuration(json.loads((destination / "configuration.json").read_text()))
    transport = Cluster()
    factory = MemoryFactory(destination / "memory", configuration, transport=transport)
    for window, query in enumerate(("node alive availability", "node free capacity", "partition placement"), 6):
        name = f"snapshot-investigation-{window}"
        path = destination / (name + ".syn")
        path.write_text(brief_investigation(name, query))
        result = execute_durable_run(DurableRunRequest(path, destination / "runs", run_id=name, memory=factory))
        assert result.exit_code == 0 and result.status == "COMPLETED", result
    assert transport.calls == 0
    state = factory.owner.state()
    snapshots = [event for event, _ in factory.owner.store.inventory() if event["kind"] == "MEMORY_STATE"]
    assert state["window"] == 8 and len(snapshots) == 1
    assert len(state["knowledge"]["versions"]) == 3168
    return {"window": 8, "materialized_memory_states": len(snapshots), "transport_calls": transport.calls}


def capture_all(output, corpus, ScopedProfiles):
    from synapse.memory_consolidation.factory import MemoryFactory
    from acceptance.memory.storage_audit.scenario import execute

    control = execute(output / "uninstrumented-audit", corpus)
    print(json.dumps({"uninstrumented_durable_wall_s": control["execution_with_consolidation_wall_s"]}), flush=True)
    capture = ScopedProfiles(output / "profiles")
    with capture.observe({"ordinary-open": MemoryFactory.open_session, "ordinary-court": MemoryFactory.court,
                          "audit-replay-pin": MemoryFactory.replay_session}):
        observed = execute(output / "profiled-audit", corpus)
    assert control["full_search_events_sha256"] == observed["full_search_events_sha256"]
    datasets = {"window4": profile_reads(capture, "window4", corpus, output, "evacuate-replanned", full=True)}
    audit = output / "profiled-audit"
    datasets["window5"] = profile_reads(capture, "window5", audit, output, "storage-incident-audit", full=False)
    snapshot = output / "snapshot-corpus"
    prepared = snapshot_corpus(audit, snapshot)
    datasets["window8"] = profile_reads(capture, "window8", snapshot, output, "snapshot-investigation-8", full=False)
    return {"uninstrumented_control": control, "profiled_audit": observed, "profiles": capture.rows,
            "datasets": datasets, "snapshot_corpus_preparation": prepared}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    product_root, corpus, output = options.product_root.resolve(), options.corpus.resolve(), options.output.resolve()
    ScopedProfiles = load(product_root)
    output.mkdir(parents=True, exist_ok=False)
    before = product_hashes(product_root)
    corpus_before = corpus_hashes(corpus)
    (output / "corpus-manifest.json").write_text(json.dumps(corpus_before, indent=2))
    (output / "source-manifest.json").write_text(json.dumps(before, indent=2))
    result = capture_all(output, corpus, ScopedProfiles)
    assert before == product_hashes(product_root), "Product changed during observation"
    assert corpus_before == corpus_hashes(corpus), "Original corpus changed during observation"
    result.update(product_root=str(product_root), original_corpus=str(corpus), python=sys.version,
                  platform=platform.platform(), source_manifest="source-manifest.json",
                  interpretation="Profiles diagnose costs; their elapsed times are instrumented. Fresh/reused "
                  "names describe reader instances, not OS cache. Recorded-pin opening is a canonical API "
                  "reopen of a saved run descriptor, not a complete CLI resume. All writer paths use copies.")
    (output / "capture.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"capture": str(output / "capture.json"), "intervals": len(result["profiles"])}), flush=True)


if __name__ == "__main__":
    main()
