#!/usr/bin/env python3
"""What the world's order of statements costs as it grows (recheck of 556624d), published.

One source states the same balance again and again. Memory folds the readings through the court's own
knowledge and hypothesis stages, a hundred readings a window, and applies each window as its report is: the
projection joins what the report adds to the order. For orders of each ``--readings`` length, with no
hypothesis or ``--claims`` hypotheses resting on the source, one more window reading the source is measured
``--repeat`` times: its median time, the bytes of the order in the state and the bytes its report adds.
Timing is a manual measurement, never a pytest acceptance threshold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

SOURCE = {"tool": "ledger", "args": {"account": "A"}}
CHECK = {"tool": "audit", "args": {"account": "A"}}


def _configuration():
    from synapse.memory_consolidation.configuration import parse_memory_configuration

    contract = {"verifies": {"subject": {"request": "account", "answer": "account"}, "scope": {"value": "bank"}}}
    tools = [{"name": name, "server": "bank", "descriptor_sha256": "0" * 64, "input_schema": {"type": "object"},
              "output_schema": {"type": "object"}, "source": source, "contract": contract}
             for name, source in (("ledger", "core:bank"), ("audit", "audit:bank"))]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1", "advisor": None, "scorer": None, "element": "bank",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "bank", "argv": ["x"]}],
                  "tools": tools, "provenance": {"core:bank": {"ancestors": []}, "audit:bank": {"ancestors": []}}}})


class Memory:
    """Memory folded window by window through the court's stages and the projection of their reports."""

    def __init__(self, configuration) -> None:
        from synapse.memory_consolidation.court.projection import empty_state

        self.configuration, self.state = configuration, empty_state()

    def window(self, declared=(), checks=()) -> dict:
        from synapse.memory_consolidation.court.hypotheses import hypothesis_stage
        from synapse.memory_consolidation.court.knowledge import additions, knowledge_stage
        from synapse.memory_consolidation.court.projection import apply_report

        context = SimpleNamespace(state=self.state, window=self.state["window"] + 1, report={},
                                  draft={"knowledge": {"declared": list(declared), "uses": []},
                                         "hypotheses": list(checks), "cases": [], "relied": []})
        knowledge = knowledge_stage(context, {}, {}, self.configuration)
        decided = hypothesis_stage(context, knowledge.pop("corrections"), knowledge["stated"])
        added = additions(self.state["knowledge"]["stated"], knowledge["stated"])
        applied = {"habits": {}, "frozen": {}, "declared": {}, "slow_only": [], "pool": {}, "quanta": {},
                   "parts": {}, "cursors": {}, "digest": None, "retention": self.state["retention"],
                   "hypotheses": decided["updates"],
                   "knowledge": {"versions": knowledge["versions"], "uses": knowledge["uses"], "stated": added}}
        self.state = apply_report(self.state, {"consolidation_id": f"window-{context.window}", "apply": applied})
        return added


def _reading(place: int) -> dict:
    from synapse.memory_consolidation.knowledge.statements import declare

    statement = declare({"subject": "A", "property": "balance", "value": 20, "text": "balance 20",
                         "valid": {"from": "2026-01-01"}, "source": SOURCE}, SOURCE, "ev-20")
    return {"run_id": f"run-{place}", "position": 0, "statement": statement, "vector": None, "embedded_by": None,
            "observed": place}


def _memory(configuration, readings: int, claims: int) -> Memory:
    from synapse.memory_consolidation import hypotheses

    memory = Memory(configuration)
    for start in range(0, readings, 100):
        memory.window([_reading(place + 1) for place in range(start, min(readings, start + 100))])
    checks = []
    for index in range(claims):
        record = hypotheses.declare({"aspect": "content", "subject": f"A{index}", "statement": {"balance": 20},
                                     "scope": "bank", "source": SOURCE, "check": CHECK}, configuration, "ev-20")
        place = readings + 1 + index
        checks.append({"kind": "hypothesis_probed", "position": index, "run_id": "checker", "hypothesis": record["id"],
                       "record": record, "status": "confirmed", "reason": "check_agrees",
                       "check_ref": {"gw_seq": place, "evidence": f"ev-check-{place}"}, "rule": None,
                       "check_basis": None})
    if checks:
        memory.window(checks=checks)
    return memory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readings", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--claims", type=int, default=100)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    configuration = _configuration()
    rows = []
    for readings in options.readings:
        for claims in (0, options.claims):
            memory = _memory(configuration, readings, claims)
            order = len(json.dumps(memory.state["knowledge"]["stated"], sort_keys=True))
            times, added = [], None
            for trial in range(options.repeat):
                measured = Memory(configuration)
                measured.state = memory.state
                began = time.perf_counter()
                added = measured.window([_reading(10_000_000 + trial)])
                times.append(time.perf_counter() - began)
            rows.append({"readings": readings, "claims": claims, "order_bytes": order,
                         "order_bytes_per_reading": round(order / readings, 1),
                         "window_seconds_median": round(statistics.median(times), 4),
                         "report_added_bytes": len(json.dumps(added, sort_keys=True))})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPOSITORY, capture_output=True,
                                text=True).stdout.strip())
    report = {"schema_version": "synapse.memory.knowledge-order-cost/v1", "commit": commit, "dirty": dirty,
              "python": platform.python_version(), "platform": platform.platform(), "repeat": options.repeat,
              "unit": "one more window reading a source whose order holds the given readings", "rows": rows}
    text = json.dumps(report, indent=2, sort_keys=True)
    if options.output is not None:
        options.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
