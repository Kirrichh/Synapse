#!/usr/bin/env python3
"""Compare two checkouts through the canonical CLI and public in-process API.

This is a manual benchmark, not a timing gate. Every observation must match
an independently specified answer. Neither checkout is modified by the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


CASES = {
    "for_arithmetic_100k": (
        "let total = 0 for i in range(100000) { total = total + i * 2 } print(total)",
        "9999900000",
    ),
    "while_arithmetic_100k": (
        "let i = 0 let total = 0 while i < 100000 { total = total + i i = i + 1 } print(total)",
        "4999950000",
    ),
    "recursive_fibonacci_20": (
        "fn fib(n) { if n < 2 { return n } return fib(n - 1) + fib(n - 2) } print(fib(20))",
        "6765",
    ),
    "empty_for_100k": ("for i in range(100000) {} print(100000)", "100000"),
    "list_reads_30k": (
        "let items = [2, 3, 5] let total = 0 for i in range(30000) { total = total + items[i % 3] } print(total)",
        "100000",
    ),
    "function_calls_60k": (
        "fn next(n) { return n + 1 } let total = 0 for i in range(60000) { total = total + next(i) } print(total)",
        "1800030000",
    ),
    "string_concat_20k": (
        'let value = "" for i in range(20000) { value = value + "x" } print(len(value))',
        "20000",
    ),
    "print_20k": ("for i in range(20000) { print(i) }", "\n".join(map(str, range(20000)))),
    "mailbox_500": (
        'agent Worker { model "mock" } for i in range(500) { send Worker.process(i) } '
        'let self = Worker for i in range(500) { receive { sender => msg { print(msg.payload) } } }',
        "\n".join(map(str, range(500))),
    ),
}

IN_PROCESS_WORKER = '''
import json, sys, time
from synapse import run
source = sys.stdin.read()
started = time.perf_counter()
output = run(source)
elapsed = time.perf_counter() - started
print(json.dumps({"seconds": elapsed, "output": output}))
'''


def environment(root):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SYNAPSE_LLM_") and key not in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}}
    env.update(PYTHONPATH=str(root), PYTHONHASHSEED="0", SYNAPSE_LLM_MODE="mock",
               SYNAPSE_LLM_PROVIDER="mock", SYNAPSE_FUEL_LIMIT="20000000")
    return env


def revision(root):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def identity(root):
    result = subprocess.run([sys.executable, "-B", "-c", "import synapse; print(synapse.__file__)"],
                            cwd=root, env=environment(root), text=True, capture_output=True, check=True)
    loaded = Path(result.stdout.strip()).resolve()
    if not loaded.is_relative_to(root):
        raise RuntimeError(f"Wrong checkout imported: {loaded}, expected {root}")
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((root / "synapse").rglob("*.py"))}
    return {"head": revision(root), "product_source_digest": hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def observation(root, mode, source, expected, timeout):
    command = [sys.executable, "-B"]
    command += ["-m", "synapse", "run", "-c", source] if mode == "cli" else ["-c", IN_PROCESS_WORKER]
    started = time.perf_counter()
    result = subprocess.run(command, cwd=root, env=environment(root), input=source if mode == "in_process" else None,
                            text=True, capture_output=True, timeout=timeout)
    seconds = time.perf_counter() - started
    if result.returncode or result.stderr:
        raise RuntimeError(f"{mode} failed in {root}: exit={result.returncode}, stderr={result.stderr[:2000]}")
    output = result.stdout
    if mode == "in_process":
        measured = json.loads(output)
        output, seconds = measured["output"], measured["seconds"]
    else:
        expected += "\n"
    if output != expected:
        raise AssertionError(f"Wrong answer in {root} ({mode}): {output[:200]!r}, expected {expected[:200]!r}")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error("at least two observations are required")
    roots = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    report = {"python": sys.version, "repeats": args.repeats,
              "method": "fresh processes; alternating version order; CLI includes startup; in_process measures run(source)",
              "identity": {name: identity(root) for name, root in roots.items()}, "cases": [], "aggregate": {}}
    for mode in ("cli", "in_process"):
        for name, (source, expected) in CASES.items():
            values = {label: [] for label in roots}
            for repetition in range(args.repeats):
                labels = tuple(roots) if repetition % 2 == 0 else tuple(reversed(roots))
                for label in labels:
                    values[label].append(observation(roots[label], mode, source, expected, args.timeout))
            medians = {label: statistics.median(times) for label, times in values.items()}
            row = {"mode": mode, "name": name, "source": source,
                   "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                   "expected_output_sha256": hashlib.sha256(expected.encode()).hexdigest(),
                   "seconds": values, "median_seconds": medians,
                   "min_seconds": {label: min(times) for label, times in values.items()},
                   "median_speedup": medians["baseline"] / medians["candidate"],
                   "answers_match": True}
            report["cases"].append(row)
            print(f"{mode} {name}: {row['median_speedup']:.2f}x", flush=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        rows = [row for row in report["cases"] if row["mode"] == mode]
        report["aggregate"][mode] = {
            "ratio_of_summed_medians": sum(row["median_seconds"]["baseline"] for row in rows)
                / sum(row["median_seconds"]["candidate"] for row in rows),
            "geometric_mean_speedup": math.exp(statistics.mean(math.log(row["median_speedup"]) for row in rows)),
        }
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
