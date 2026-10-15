#!/usr/bin/env python3
"""Run MemoryArena's own ``run_travel.main`` on the first N travel groups.

A pilot or a dry run needs a few groups, not all 270; MemoryArena's runner has
no such option. This loads its checkout, narrows the data its runner reads and
calls the runner unchanged with the given config. The environment server
still serves every group; the evaluation scores the groups that ran.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path, help="a MemoryArena checkout")
    parser.add_argument("config", type=Path, help="a travel planner config (configs/travel_planner_configs)")
    parser.add_argument("groups", type=int, help="how many groups to run, from the first")
    options = parser.parse_args()
    sys.path.insert(0, str(options.checkout.resolve()))
    import run_travel

    load = run_travel.load_travel_data
    run_travel.load_travel_data = lambda: load()[:options.groups]
    sys.argv = ["run_travel.py", "--config", str(options.config)]
    run_travel.main()


if __name__ == "__main__":
    main()
