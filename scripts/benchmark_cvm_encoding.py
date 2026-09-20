#!/usr/bin/env python3
"""Paired CVM measurement through the existing public run-vm application path.

Freeze bytecode with the baseline compiler, then give both checkouts identical
programs. Every sample must complete and match full result, snapshot and history.
Timing is a manual measurement, never a pytest acceptance threshold.
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
import time


CASES = {
    "for_200": ("let acc = 12345 for i in range(200) { acc = (acc * 48271) % 2147483647 }",
                12345 * pow(48271, 200, 2147483647) % 2147483647),
    "for_400": ("let acc = 12345 for i in range(400) { acc = (acc * 48271) % 2147483647 }",
                12345 * pow(48271, 400, 2147483647) % 2147483647),
    "while_400": ("let acc = 12345 let i = 0 while i < 400 { acc = (acc * 48271) % 2147483647 i = i + 1 }",
                  12345 * pow(48271, 400, 2147483647) % 2147483647),
    "calls_150": ("fn advance(acc, i) { return acc + i } let acc = 0 for i in range(150) { acc = advance(acc, i) }",
                  149 * 150 // 2),
    "array_200": ("let items = [2, 3, 5] let acc = 0 for i in range(200) { acc = acc + items[i % 3] }",
                  sum((2, 3, 5)[i % 3] for i in range(200))),
    "strings_150": ('let acc = "" for i in range(150) { acc = acc + "я" }', "я" * 150),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    paths = ("synapse/cvm.py", "synapse/bytecode.py", "synapse/runtime/vm_bridge.py")
    return {"head": git("rev-parse", "HEAD"), "status": git("status", "--porcelain"),
            "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}}


def worker(root):
    sys.path.insert(0, str(root))
    import synapse
    from synapse import Interpreter, compile_to_ast, run
    from synapse.bytecode import CognitiveCompiler
    from synapse.cvm import encode_vm_value

    assert Path(synapse.__file__).resolve().is_relative_to(root), synapse.__file__
    for line in sys.stdin:
        request = json.loads(line)
        if request["action"] == "freeze":
            response = {name: {"source": source, "expected": expected,
                               "program": CognitiveCompiler().compile(compile_to_ast(source)).to_dict()}
                        for name, (source, expected) in CASES.items()}
        else:
            # Parsing the benchmark envelope is outside timing. Product parsing,
            # VM construction, execution, bridge snapshots and events are inside.
            started = time.perf_counter_ns()
            interp = Interpreter()
            # Both executions represent the same run input. Otherwise fresh
            # application UUIDs legitimately produce different history trace IDs.
            interp.attach_storage(None, run_id=request["run_id"])
            interp.global_env.define("code", request["program"])
            run("run vm { source code gas 1000000 bind result }", interp)
            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
            result = interp.global_env.get("result")
            assert result.get("halted") and not result.get("error"), result
            assert result["locals"]["acc"] == request["expected"], result
            response = {"milliseconds": elapsed_ms, "steps": result["steps"],
                        "gas_remaining": result["gas_remaining"],
                        "transition_hash": result["transition_hash"],
                        "semantic_digest": digest(encode_vm_value({
                            "result": result, "snapshots": interp.vm_snapshots,
                            "history": interp.execution_history,
                        }))}
        print(json.dumps(response), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--corpus", type=Path, help="Reuse the frozen_programs from a prior report")
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--processes", type=int, default=3)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve())
        return
    if not (args.baseline and args.candidate and args.out):
        parser.error("--baseline, --candidate and --out are required")
    if args.rounds < 3 or args.processes < 1:
        parser.error("at least 3 rounds and 1 process are required")
    cpu = None
    if hasattr(os, "sched_getaffinity"):
        cpu = min(os.sched_getaffinity(0))
        os.sched_setaffinity(0, {cpu})
    roots = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    manifest = {name: identity(root) for name, root in roots.items()}
    if any(item["status"] for item in manifest.values()):
        parser.error("benchmark requires clean checkouts; commit local work first")
    observations = []
    corpus = json.loads(args.corpus.read_text())["frozen_programs"] if args.corpus else None
    rng = random.Random(20260920)
    for process_index in range(args.processes):
        workers = {}
        try:
            for name, root in roots.items():
                env = {**os.environ, "PYTHONPATH": str(root), "PYTHONHASHSEED": "0",
                       "SYNAPSE_LLM_MODE": "mock", "SYNAPSE_LLM_PROVIDER": "mock"}
                workers[name] = subprocess.Popen(
                    [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(root)],
                    cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

            def ask(name, packet):
                process = workers[name]
                process.stdin.write(json.dumps(packet) + "\n")
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"{name} worker ended: {process.poll()}")
                return json.loads(line)

            if corpus is None:
                corpus = ask("baseline", {"action": "freeze"})
            for round_index in range(-2, args.rounds):
                names = list(corpus)
                rng.shuffle(names)
                for case in names:
                    order = list(roots)
                    rng.shuffle(order)
                    packet = {"action": "run", **corpus[case],
                              "run_id": f"cvm-encoding-{process_index}-{round_index}-{case}"}
                    pair = {name: ask(name, packet) for name in order}
                    for key in ("semantic_digest", "steps", "gas_remaining", "transition_hash"):
                        assert pair["baseline"][key] == pair["candidate"][key], (case, key, pair)
                    observations.append({"process": process_index, "round": round_index,
                                         "warmup": round_index < 0, "case": case, **pair})
        finally:
            for process in workers.values():
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
    summary = {}
    for case in corpus:
        rows = [x for x in observations if x["case"] == case and not x["warmup"]]
        medians = {name: statistics.median(x[name]["milliseconds"] for x in rows) for name in roots}
        summary[case] = {"median_ms": medians, "speedup": medians["baseline"] / medians["candidate"],
                         "sample_pairs": len(rows), "semantic_match": True}
    after = {name: identity(root) for name, root in roots.items()}
    assert manifest == after, "checkouts changed during measurement"
    report = {"method": "same frozen bytecode and explicit run identity; public run-vm; warm processes; fresh Interpreter each run; paired randomized order",
              "includes": "Interpreter creation, wrapper parse, VM execution, snapshots and application events",
              "excludes": "process startup/imports, source-to-bytecode compilation, benchmark transport and digesting",
              "python": sys.version, "platform": platform.platform(), "cpu_affinity": cpu,
              "processes": args.processes, "rounds": args.rounds, "warmups_per_process": 2,
              "checkouts": manifest, "corpus_sha256": digest(corpus), "frozen_programs": corpus,
              "summary": summary, "observations": observations}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
