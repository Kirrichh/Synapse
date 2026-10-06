#!/usr/bin/env python3
"""The separate measures of the paired A/B/C exam, published (review §9.3).

Runs the paired exam of ``acceptance/memory/_paired.py`` through the canonical
launch ``--repeat`` times, each on a fresh memory, and writes every task's
measures and each arm's totals: goal reached, erroneous actions, abstention,
lost useful knowledge, habit fires, tool and model calls, gateway latency and
the size of the memory read. The unit is one task in one arm; repetitions of
the whole exam are listed apart and never pooled into one sample. Timing is a
manual measurement, never a pytest acceptance threshold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))


def main() -> None:
    from acceptance.memory import _paired as paired
    from acceptance.memory import _travel as travel
    from acceptance.memory._world import MemoryWorld

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    exams = []
    for index in range(options.repeat):
        with tempfile.TemporaryDirectory(prefix="memory-modes-") as root:
            world = MemoryWorld(Path(root), travel.tools(), provenance=travel.independent_provenance())
            _, snapshot = paired.learn(world)
            initial = world.world()
            arms = {mode: paired.arm(world, mode, snapshot, initial) for mode in "ABC"}
            exams.append({"exam": index, "tasks": {mode: tasks for mode, (tasks, _) in arms.items()},
                          "totals": paired.summary(arms)})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPOSITORY, capture_output=True,
                                text=True).stdout.strip())
    report = {"schema_version": "synapse.memory.paired-exam-measures/v1", "commit": commit, "dirty": dirty,
              "python": platform.python_version(), "platform": platform.platform(), "tasks": list(paired.TASKS),
              "unit": "one task in one arm, from the restored initial state", "exams": exams}
    text = json.dumps(report, indent=2, sort_keys=True)
    if options.output is not None:
        options.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
