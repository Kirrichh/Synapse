"""Measure the storage acceptance scenario against a selected product checkout."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sys


def load_scenario(product_root):
    sys.path.insert(0, str(product_root))
    name = "_storage_evacuation_acceptance"
    directory = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(name + ".scenario")


def metadata(product_root, label):
    import synapse.memory_consolidation.knowledge.search as search

    source = Path(search.__file__).resolve()
    assert source.is_relative_to(product_root)
    return {"label": label, "product_root": str(product_root),
            "search_module": str(source),
            "search_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "python": sys.version, "platform": platform.platform(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def replay_existing(scenario, recorded, output):
    from synapse.memory_consolidation.factory import MemoryFactory

    output.mkdir(parents=True, exist_ok=False)
    cluster = scenario.Cluster()
    factory = MemoryFactory(recorded / "memory", scenario.configuration(), transport=cluster)
    rows = scenario.replay_phases(recorded, factory, cluster)
    assert cluster.calls == 0
    return {"recorded_root": str(recorded), "replay": rows,
            "replay_transport_calls": cluster.calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--replay-existing", type=Path)
    options = parser.parse_args()
    product_root = options.product_root.resolve()
    scenario = load_scenario(product_root)
    if options.replay_existing is None:
        result = scenario.execute(options.output)
    else:
        result = replay_existing(scenario, options.replay_existing, options.output)
    result["environment"] = metadata(product_root, options.label)
    (options.output / "measurement.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
