"""Measure temporal storage investigation against a selected product checkout."""
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
from types import ModuleType


def load_scenario(product_root):
    sys.path.insert(0, str(product_root))
    directory = Path(__file__).resolve().parent
    for name, path in (("acceptance", directory.parents[1]), ("acceptance.memory", directory.parent)):
        package = ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    name = "_storage_audit_acceptance"
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(name + ".scenario")


def metadata(product_root, label):
    sources, paths, harness = {}, {}, {}
    product_names = ["synapse.memory_consolidation.session", "synapse.memory_consolidation.knowledge.search",
                     "synapse.palace_admission", "synapse.interpreter"]
    if "synapse.memory_consolidation.knowledge.prepared" in sys.modules:
        product_names.append("synapse.memory_consolidation.knowledge.prepared")
    for name in product_names:
        module = sys.modules[name]
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(product_root)
        sources[str(path.relative_to(product_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        paths[name] = str(path)
    harness_root = Path(__file__).resolve().parents[3]
    harness_names = ["_storage_audit_acceptance." + name
                     for name in ("scenario", "reference", "oracle", "questions")]
    harness_names += ["acceptance.memory._lexical_reference",
                     "acceptance.memory.storage_evacuation.cluster",
                     "acceptance.memory.storage_evacuation.scenario"]
    for name in harness_names:
        path = Path(sys.modules[name].__file__).resolve()
        assert path.is_relative_to(harness_root / "acceptance")
        harness[str(path.relative_to(harness_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        paths[name] = str(path)
    return {"label": label, "product_root": str(product_root), "source_sha256": sources,
            "harness_sha256": harness, "imported_module_paths": paths,
            "python": sys.version, "platform": platform.platform(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def replay_existing(scenario, recorded):
    from synapse.memory_consolidation.configuration import parse_memory_configuration
    from synapse.memory_consolidation.factory import MemoryFactory

    configuration = parse_memory_configuration(json.loads((recorded / "configuration.json").read_text()))
    transport = scenario.Cluster()
    factory = MemoryFactory(recorded / "memory", configuration, transport=transport)
    return {"recorded_root": str(recorded), "replay": scenario.replay(recorded, factory, transport),
            "replay_transport_calls": transport.calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--replay-existing", type=Path)
    options = parser.parse_args()
    product_root = options.product_root.resolve()
    scenario = load_scenario(product_root)
    if options.replay_existing is None:
        result = scenario.execute(options.output, options.corpus)
    else:
        options.output.mkdir(parents=True, exist_ok=False)
        result = replay_existing(scenario, options.replay_existing)
    result["environment"] = metadata(product_root, options.label)
    (options.output / "measurement.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
