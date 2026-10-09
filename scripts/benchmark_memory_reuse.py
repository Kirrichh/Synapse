#!/usr/bin/env python3
"""Paired measurement of verified memory reuse through the canonical launch (review §8.3).

Each checkout builds the same memory — ``--windows`` stock-count sessions, each
one durable run through ``python -m synapse run`` with its consolidation — and
then measures, in the order the review gives:

1. restoring the same state from the journal again (``MemoryOwner.state``);
2. checking the unchanged journal prefix again (``MemoryOwner.applied`` and the
   newest snapshot boundary a session pins), with the journal events read;
3. ranking by semantic similarity over ``--vectors`` recorded vectors, whose
   output must be identical in both checkouts;
4. one more session end to end.

Re-checks of confirmed, unchanged bases are saved by hypothesis reuse; the
acceptance files count them (exam A checks, exam B reuses) and nothing here
times them. Timing is a manual measurement, never a pytest acceptance
threshold. ``--baseline`` names a second checkout; the same measurement runs in
both and the JSON records both and their ratio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time

REPOSITORY = Path(__file__).resolve().parents[1]


def _median(samples):
    return {"median_s": round(statistics.median(samples), 6), "samples": len(samples)}


def _timed(action, repeat):
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        action()
        samples.append(time.perf_counter() - started)
    return _median(samples)


def measure(windows: int, vectors: int, repeat: int, seed: int) -> dict:
    """The measurement inside one checkout (this process imports that checkout)."""
    from acceptance.memory import _stock as stock
    from synapse.experiments.gold import project_memory_store
    from synapse.memory_consolidation.configuration import read_memory_configuration
    from synapse.memory_consolidation.court.projection import fold
    from synapse.memory_consolidation.factory import MemoryFactory
    from synapse.memory_consolidation.knowledge.search import semantic_ranking
    from synapse.memory_consolidation.owner import MemoryOwner

    reads = {"count": 0}
    original = project_memory_store.ProjectMemoryStore._read

    def counted(self, transaction):
        reads["count"] += 1
        return original(self, transaction)

    project_memory_store.ProjectMemoryStore._read = counted
    result = {}
    with tempfile.TemporaryDirectory(prefix="memory-reuse-") as root:
        world = stock.world(Path(root), parameters={"n_medium_windows": 10 * windows, "n_low_windows": 10 * windows})
        sessions = []
        for index in range(windows):
            started = time.perf_counter()
            stock.count(world, f"build-{index:04d}")
            sessions.append(time.perf_counter() - started)
        result["build"] = {"windows": windows, "first_session_s": round(sessions[0], 3),
                           "last_session_s": round(sessions[-1], 3), "total_s": round(sum(sessions), 3)}
        owner = MemoryOwner(world.state, read_only=True)
        factory = MemoryFactory(world.state, read_memory_configuration(world.configuration_path),
                                owner=MemoryOwner(world.state))
        state = owner.state()
        result["state_sha256"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        # The state read from a snapshot and its tail is the fold of every report, or the measurement is void.
        result["state_equals_full_fold"] = state == fold(item["report"] for item in owner.applied()
                                                         if item["report"] is not None)
        for name, action in (("state", owner.state), ("applied", owner.applied),
                             ("latest_boundary", lambda: factory.latest_boundary(None))):
            reads["count"] = 0
            action()
            result[name] = {**_timed(action, repeat), "journal_reads": reads["count"]}
        started = time.perf_counter()
        stock.count(world, "probe")
        result["next_session_s"] = round(time.perf_counter() - started, 3)
    project_memory_store.ProjectMemoryStore._read = original

    rng = random.Random(seed)
    entries = [{"record": {"id": f"stm_{index:06d}"}, "vector": [rng.uniform(-1, 1) for _ in range(384)]}
               for index in range(vectors)]
    query = [rng.uniform(-1, 1) for _ in range(384)]
    ranking = semantic_ranking(query, entries, 25)
    result["semantic_ranking"] = {**_timed(lambda: semantic_ranking(query, entries, 25), repeat),
                                  "vectors": vectors, "dimensions": 384, "budget": 25,
                                  "output_sha256": hashlib.sha256(json.dumps(ranking).encode()).hexdigest()}
    return result


def _run(checkout: Path, arguments) -> dict:
    environment = {**os.environ, "PYTHONPATH": str(checkout)}
    completed = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--inside", *arguments],
                               cwd=checkout, env=environment, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout.splitlines()[-1])


def _git(checkout: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True,
                          check=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=int, default=40)
    parser.add_argument("--vectors", type=int, default=5000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument("--baseline", type=Path, help="a second checkout measured the same way")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    arguments = ["--windows", str(options.windows), "--vectors", str(options.vectors), "--repeat",
                 str(options.repeat), "--seed", str(options.seed)]
    if options.inside:
        print(json.dumps(measure(options.windows, options.vectors, options.repeat, options.seed), sort_keys=True))
        return
    report = {"schema_version": "synapse.memory.reuse-measurement/v1", "python": platform.python_version(),
              "platform": platform.platform(), "parameters": {"windows": options.windows, "vectors": options.vectors,
                                                               "repeat": options.repeat, "seed": options.seed},
              "current": {"commit": _git(REPOSITORY), "dirty": bool(subprocess.run(
                  ["git", "status", "--porcelain"], cwd=REPOSITORY, capture_output=True, text=True).stdout.strip()),
                          "result": _run(REPOSITORY, arguments)}}
    if options.baseline is not None:
        baseline = _run(options.baseline.resolve(), arguments)
        report["baseline"] = {"commit": _git(options.baseline), "result": baseline}
        current = report["current"]["result"]
        report["comparison"] = {
            "state_equals_full_fold": [baseline["state_equals_full_fold"], current["state_equals_full_fold"]],
            "same_ranking": current["semantic_ranking"]["output_sha256"] == baseline["semantic_ranking"]["output_sha256"],
            "speedup": {name: round(baseline[name]["median_s"] / current[name]["median_s"], 2)
                        for name in ("state", "applied", "latest_boundary", "semantic_ranking")},
            "journal_reads": {name: [baseline[name]["journal_reads"], current[name]["journal_reads"]]
                              for name in ("state", "applied", "latest_boundary")},
            "next_session_s": [baseline["next_session_s"], current["next_session_s"]]}
    text = json.dumps(report, indent=2, sort_keys=True)
    if options.output is not None:
        options.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
