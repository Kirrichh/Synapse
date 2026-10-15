"""Measure prepared searches over two retained storage incident boundaries."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sys
import time
import tracemalloc


def questions():
    """Read the same incident questions as the durable storage investigation."""
    source = Path(__file__).parent / "storage_audit" / "questions.py"
    spec = importlib.util.spec_from_file_location("_prepared_search_questions", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.questions()


def retained_boundaries(state_root):
    """Select actual committed cuts before and after the incident correction."""
    from synapse.memory_consolidation.owner import MemoryOwner

    owner = MemoryOwner(state_root, read_only=True)
    selected = {}
    for item in owner.applied():
        report = item["report"]
        if report is not None and report["window"]["index"] in (2, 4):
            selected[report["window"]["index"]] = report["snapshot_boundary_after"]
    if set(selected) != {2, 4} or None in selected.values():
        raise ValueError("the retained incident requires complete boundaries at windows 2 and 4")
    return [{"window": window, "snapshot": selected[window]} for window in (2, 4)]


def elapsed(started):
    return {"wall_s": time.perf_counter() - started[0],
            "cpu_s": time.process_time() - started[1]}


def clock():
    return time.perf_counter(), time.process_time()


def open_pinned(state_root, boundary):
    """Open through the factory's read-only exam path without registering a run."""
    from synapse.memory_consolidation.factory import MemoryFactory
    from synapse.memory_consolidation.owner import MemoryOwner

    started = clock()
    owner = MemoryOwner(state_root, read_only=True)
    configuration = owner.bound_configuration()
    if configuration is None:
        raise ValueError("the retained owner has no bound memory configuration")
    opened = elapsed(started)
    started = clock()
    factory = MemoryFactory(state_root, configuration, owner=owner,
                            exam={"mode": "B", "snapshot": boundary["snapshot"]})
    session = factory.open_session({"run_id": "prepared-search-measurement", "history": []})
    pinned = elapsed(started)
    if session.pinned()["boundary"] != boundary["snapshot"]:
        raise AssertionError("the measurement opened a different snapshot")
    return session, {"owner_open": opened, "session_open": pinned}


def search(session, question):
    return session.search_knowledge(question["query"], **question["options"], embed=None)


def fingerprints(outputs):
    """Hash complete query outputs outside timing and allocation intervals."""
    from synapse.memory_consolidation.records import canonical

    return [{"sha256": hashlib.sha256(canonical(output)).hexdigest(),
             "candidates": len(output["candidates"]), "fused": len(output["fused"])}
            for output in outputs]


def timed_queries(session, asked):
    """Time the first build separately from subsequent distinct requests."""
    started = clock()
    outputs = [search(session, asked[0])]
    first = elapsed(started)
    rows = []
    started = clock()
    for question in asked[1:]:
        query_start = clock()
        output = search(session, question)
        rows.append(elapsed(query_start))
        outputs.append(output)
    subsequent = elapsed(started)
    return {"first_search": first, "subsequent_searches": subsequent,
            "subsequent_query_times": rows, "outputs": fingerprints(outputs)}


def allocated_queries(session, asked):
    """Measure retained search allocations after discarding every returned result."""
    gc.collect()
    tracemalloc.start()
    origin, _ = tracemalloc.get_traced_memory()
    result = search(session, asked[0])
    _, first_peak = tracemalloc.get_traced_memory()
    del result
    gc.collect()
    first_live, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    for question in asked[1:]:
        result = search(session, question)
        del result
    _, subsequent_peak = tracemalloc.get_traced_memory()
    gc.collect()
    subsequent_live, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    allocations = {"first_peak_bytes": first_peak - origin,
                   "first_retained_bytes": first_live - origin,
                   "subsequent_peak_bytes": subsequent_peak - origin,
                   "subsequent_retained_bytes": subsequent_live - origin,
                   "retained_growth_after_first_bytes": subsequent_live - first_live}
    # This pass is deliberately outside tracemalloc, so hashing cannot inflate its peaks.
    allocations["outputs"] = fingerprints([search(session, question) for question in asked])
    return allocations


def measure_boundary(state_root, boundary, asked, mode):
    gc.collect()
    session, opening = open_pinned(state_root, boundary)
    versions = session.boundary["boundary"]["knowledge"]
    result = {**boundary, "knowledge_versions": len(versions)}
    if mode == "timing":
        result.update(opening)
        result.update(timed_queries(session, asked))
    else:
        result.update(allocated_queries(session, asked))
    return result


def environment(product_root, label):
    sources, paths, harness = {}, {}, {}
    product_names = ["synapse.memory_consolidation.knowledge.search", "synapse.memory_consolidation.session",
                     "synapse.palace_admission", "synapse.interpreter"]
    if "synapse.memory_consolidation.knowledge.prepared" in sys.modules:
        product_names.append("synapse.memory_consolidation.knowledge.prepared")
    for name in product_names:
        source = Path(sys.modules[name].__file__).resolve()
        if not source.is_relative_to(product_root):
            raise AssertionError("measurement imported another product checkout")
        sources[str(source.relative_to(product_root))] = hashlib.sha256(source.read_bytes()).hexdigest()
        paths[name] = str(source)
    harness_root = Path(__file__).resolve().parents[2]
    for name in ("_prepared_search_questions", "__main__"):
        source = Path(sys.modules[name].__file__).resolve()
        if not source.is_relative_to(harness_root / "acceptance"):
            raise AssertionError("measurement imported another acceptance harness")
        harness[str(source.relative_to(harness_root))] = hashlib.sha256(source.read_bytes()).hexdigest()
        paths[name] = str(source)
    return {"label": label, "product_root": str(product_root), "python": sys.version,
            "platform": platform.platform(), "sources_sha256": sources,
            "harness_sha256": harness, "imported_module_paths": paths,
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=("timing", "allocations"), default="timing")
    options = parser.parse_args()
    product_root, state_root = options.product_root.resolve(), options.state_root.resolve()
    sys.path.insert(0, str(product_root))
    asked = questions()
    boundaries = retained_boundaries(state_root)
    result = {"mode": options.mode, "state_root": str(state_root), "queries": asked,
              "update_method": "fresh session over a later committed snapshot; full lazy rebuild",
              "boundaries": [measure_boundary(state_root, boundary, asked, options.mode)
                             for boundary in boundaries]}
    result["environment"] = environment(product_root, options.label)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(options.output), "mode": options.mode,
                      "windows": [item["window"] for item in boundaries]}), flush=True)


if __name__ == "__main__":
    main()
