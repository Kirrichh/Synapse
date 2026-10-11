"""Measure shared event storage through canonical engineering workloads."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import platform
import resource
import sys
import time
from types import ModuleType


def load(product_root):
    sys.path.insert(0, str(product_root))
    directory = Path(__file__).resolve().parent
    for name, path in (("acceptance", directory.parents[1]), ("acceptance.memory", directory.parent)):
        package = ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    return importlib.import_module("acceptance.memory.shared_storage_measure.retained_reads")


def hashes(root, paths):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def inspect(root, name, result):
    from acceptance.memory.shared_storage_measure.physical_bytes import physical_bytes
    from acceptance.memory.shared_storage_measure.retained_reads import inspect_dataset

    result[name] = {"reads": inspect_dataset(root), "physical": physical_bytes(root)}
    print(json.dumps({"dataset": name, "window": result[name]["reads"]["window"],
                      "physical_bytes": result[name]["physical"]["total_bytes"]}), flush=True)


def mixed_replay(corpus, audit):
    from acceptance.memory.storage_evacuation.scenario import configuration, replay_phases
    from acceptance.memory.storage_evacuation.cluster import Cluster
    from synapse.memory_consolidation.factory import MemoryFactory

    transport = Cluster()
    factory = MemoryFactory(audit / "memory", configuration(), transport=transport)
    result = replay_phases(corpus, factory, transport)
    assert transport.calls == 0
    return {"phases": result, "transport_calls": transport.calls}


def execute(options):
    from acceptance.memory.storage_audit.scenario import execute as audit
    from acceptance.memory.storage_evacuation.scenario import execute as evacuate
    from acceptance.memory.memory_open_profile.measure import snapshot_corpus

    result, corpus = {}, options.corpus
    if options.mode == "inspect":
        inspect(corpus, "retained", result)
        return result
    if options.mode == "fresh":
        corpus = options.output / "evacuation"
        result["evacuation"] = evacuate(corpus)
    inspect(corpus, "window4", result)
    audit_root = options.output / "audit"
    result["audit"] = audit(audit_root, corpus)
    inspect(audit_root, "window5", result)
    if options.replay_prior:
        result["earlier_phases_replayed_from_extended_journal"] = mixed_replay(corpus, audit_root)
    if options.snapshots:
        snapshot = options.output / "snapshot"
        started, cpu = time.perf_counter(), time.process_time()
        result["snapshot_preparation"] = snapshot_corpus(audit_root, snapshot)
        result["snapshot_preparation"].update(
            wall_s=time.perf_counter() - started, cpu_s=time.process_time() - cpu,
            timing_scope="Cloning, three durable investigations with consolidation, state verification")
        inspect(snapshot, "window8", result)
    return result


def metadata(options, sources, corpus_before):
    harness_root = Path(__file__).resolve().parents[3]
    imported = {name: Path(module.__file__).resolve() for name, module in sys.modules.items()
                if name.startswith("synapse.") and getattr(module, "__file__", None)}
    assert all(path.is_relative_to(options.product_root) for path in imported.values())
    baseline = options.product_root / "baseline-manifest.json"
    return {"label": options.label, "mode": options.mode, "product_root": str(options.product_root),
            "python": sys.version, "platform": platform.platform(), "product_sha256": sources,
            "baseline_manifest_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest()
                if baseline.is_file() else None,
            "harness_sha256": hashes(harness_root, (harness_root / "acceptance/memory").rglob("*.py")),
            "input_corpus_sha256": corpus_before, "product_unchanged": True,
            "input_corpus_unchanged": True, "imported_product_paths": {k: str(v) for k, v in imported.items()},
            "whole_process_peak_rss": {
                "self": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "children": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                "units": "KiB on Linux; bytes on macOS",
                "scope": "Whole measurement process, including workload, reads, comparisons and hashing. "
                         "Children are a separate OS-reported maximum, not a simultaneous sum."}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=("fresh", "retained", "inspect"), default="fresh")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--snapshots", action="store_true")
    parser.add_argument("--replay-prior", action="store_true")
    options = parser.parse_args()
    options.product_root, options.output = options.product_root.resolve(), options.output.resolve()
    options.corpus = options.corpus.resolve() if options.corpus else None
    if options.mode != "fresh" and options.corpus is None:
        parser.error("--corpus is required for retained and inspect modes")
    load(options.product_root)
    options.output.mkdir(parents=True, exist_ok=False)
    sources = hashes(options.product_root, (options.product_root / "synapse").rglob("*.py"))
    corpus_before = hashes(options.corpus, options.corpus.rglob("*")) if options.corpus else {}
    result = execute(options)
    assert sources == hashes(options.product_root, (options.product_root / "synapse").rglob("*.py"))
    if options.corpus:
        assert corpus_before == hashes(options.corpus, options.corpus.rglob("*"))
    result["environment"] = metadata(options, sources, corpus_before)
    (options.output / "measurement.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"measurement": str(options.output / "measurement.json")}), flush=True)


if __name__ == "__main__":
    main()
