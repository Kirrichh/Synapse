"""Measure owner selections over a retained, fully validated project journal."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def operations(owner, guard):
    return {
        "bound_digest": lambda: owner.bound_digest(guard=guard),
        "sessions": lambda: owner.sessions(guard=guard),
        "retention_passes": lambda: owner.retention_passes(guard=guard),
        "trials": lambda: owner.trials(guard=guard),
        "boundaries": lambda: owner.boundaries(guard=guard),
        "full_inventory": lambda: owner.store.inventory(guard=guard),
    }


def timed_samples(operation, repeats):
    samples, expected = [], None
    for _ in range(repeats):
        gc.collect()
        wall, cpu = time.perf_counter(), time.process_time()
        result = operation()
        sample = {"wall_s": time.perf_counter() - wall,
                  "cpu_s": time.process_time() - cpu}
        actual = digest(result)
        assert expected is None or actual == expected
        expected = actual
        samples.append(sample)
        del result
    return {"samples": samples, "result_sha256": expected,
            "median_wall_s": statistics.median(row["wall_s"] for row in samples)}


def allocation_samples(operation, repeats, expected):
    samples = []
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        try:
            result = operation()
            retained, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert digest(result) == expected
        samples.append({"live_traced_bytes_at_return": retained, "peak_bytes": peak,
                        "peak_minus_live_bytes": peak - retained})
        del result
    return {"samples": samples,
            "median_peak_bytes": statistics.median(row["peak_bytes"] for row in samples)}


def measure(owner, repeats):
    started = time.perf_counter()
    with owner.store.session() as guard:
        opening = time.perf_counter() - started
        calls = operations(owner, guard)
        timing = {name: timed_samples(operation, repeats) for name, operation in calls.items()}
        allocations = {name: allocation_samples(operation, repeats, timing[name]["result_sha256"])
                       for name, operation in calls.items()}
    return {"session_open_wall_s": opening, "timing": timing, "allocations": allocations}


def journal_evidence(state_root):
    entries = sorted((state_root / "project-memory" / "events").iterdir())
    return {"transaction_directories": len(entries),
            "event_body_bytes": sum((path / "event.json").stat().st_size for path in entries
                                    if (path / "event.json").exists())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    options = parser.parse_args()
    product_root, state_root = options.product_root.resolve(), options.state_root.resolve()
    sys.path.insert(0, str(product_root))
    owner_module = importlib.import_module("synapse.memory_consolidation.owner")
    store_module = importlib.import_module("synapse.experiments.gold.project_memory_store")
    sources = {}
    for module in (owner_module, store_module):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(product_root)
        sources[str(path.relative_to(product_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    owner = owner_module.MemoryOwner(state_root)
    result = {"label": options.label, "product_root": str(product_root),
              "state_root": str(state_root), "python": sys.version, "platform": platform.platform(),
              "product_source_sha256": sources, "journal": journal_evidence(state_root),
              "repeats": 3, "scope": {
                  "opening": "Owner session validates every committed event before any selection.",
                  "timing": "Three calls per operation under the same guard; allocation tracing disabled.",
                  "allocation": "Three separate calls per operation with tracemalloc after session opening; "
                                "measures allocations during the call, not persistent process RAM.",
                  "verification": "Complete returned values are hashed outside each measured interval.",
              }, **measure(owner, 3)}
    result["full_owner_state_sha256"] = digest(owner.state())
    options.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
