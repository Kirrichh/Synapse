#!/usr/bin/env python3
"""Prepare and run MemoryArena's Group Travel Planner with a memory system, then summarize it.

``prepare`` fetches what the benchmark needs and pins it: the MemoryArena
checkout at a fixed commit, the flights table its tools read (from the
benchmark's Google Drive folder, verified by SHA-256) and the dataset
revision MemoryArena's loader reads from Hugging Face, refused if it moved.

``run`` starts the benchmark's environment server and
``scripts/memoryarena_memory_server.py``, writes the travel config for one
memory system and one model, runs ``scripts/memoryarena_run_travel.py`` on the
chosen groups (a runner that stops is started again; finished groups are
kept) and writes ``summary.json``: the benchmark's PS, SPS and SR, the
provider's token counts, the agent's steps, and whether the plans a question's
constraint sentences name reached the agent through memory.

A per-day quota refusal ends the run as ``quota_exhausted``: the groups it
finished are kept and summarized, and the job is not marked as failed, since
the next day's run continues with the remaining groups.

``report`` joins summaries into one Markdown table and JSON file. ``merge``
joins runs of the same memory system made on different days and scores their
union with MemoryArena's own combination and evaluation.

The provider key is read from the environment by the transport; nothing here
prints or stores it. Plans are scored by the benchmark's string comparison; no
model judges them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))

from benchmark_memoryarena_travel import DATASET  # noqa: E402

MEMORYARENA = {"url": "https://github.com/ZexueHe/MemoryArena.git",
               "commit": "6cd9de14b71915e39ac742a20dc33785e14b6aab"}
FLIGHTS = {"drive_id": "1dNtxHFv7k0PeMHI0smZk8t-dBlWLA0Gz", "file": "clean_Flights_2022.csv",
           "sha256": "8dafdb0e3f8b79ce599a1e612a772865295bc226b46e5fb278368f7255b11cee"}
FLIGHTS_TARGET = Path("env/env_systems/travel_planner_env/database/flights")
ENV_PORT, MEMORY_PORT = 8001, 8010


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _stream(response, target: Path) -> None:
    with target.open("wb") as output:
        shutil.copyfileobj(response, output, 1 << 20)


def _download_drive(file_id: str, target: Path) -> None:
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download"
    with urllib.request.urlopen(url, timeout=120) as response:
        if response.headers.get_content_type() != "text/html":
            _stream(response, target)
            return
        fields = dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', response.read().decode("utf-8", "replace")))
    confirmed = "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
        {"id": file_id, "export": "download", "confirm": fields.get("confirm", "t"), "uuid": fields.get("uuid", "")})
    with urllib.request.urlopen(confirmed, timeout=900) as response:
        if response.headers.get_content_type() == "text/html":
            raise SystemExit("Google Drive answered with a page instead of the flights table (quota or refusal)")
        _stream(response, target)


def prepare(root: Path, cache: Path) -> None:
    checkout = root / "MemoryArena"
    if not checkout.exists():
        subprocess.run(["git", "clone", "--quiet", MEMORYARENA["url"], str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "advice.detachedHead=false", "checkout", "--quiet",
                        MEMORYARENA["commit"]], check=True)
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()
    if head != MEMORYARENA["commit"]:
        raise SystemExit(f"MemoryArena checkout is at {head}, not the pinned {MEMORYARENA['commit']}")
    cache.mkdir(parents=True, exist_ok=True)
    flights = cache / FLIGHTS["file"]
    if not flights.exists() or _sha256(flights) != FLIGHTS["sha256"]:
        partial = flights.with_suffix(".part")
        _download_drive(FLIGHTS["drive_id"], partial)
        found = _sha256(partial)
        if found != FLIGHTS["sha256"]:
            raise SystemExit(f"the flights table is not the pinned file ({found})")
        partial.replace(flights)
    target = checkout / FLIGHTS_TARGET / FLIGHTS["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(flights.resolve())
    with urllib.request.urlopen(f"https://huggingface.co/api/datasets/{DATASET['name']}", timeout=60) as response:
        revision = json.loads(response.read().decode("utf-8")).get("sha")
    if revision != DATASET["revision"]:
        raise SystemExit(f"{DATASET['name']} moved to {revision}; MemoryArena's loader would not read the pinned "
                         f"revision {DATASET['revision']}")
    print(f"MemoryArena {head}, flights {FLIGHTS['sha256'][:12]}, dataset {revision}")
    if os.environ.get("GEMINI_API_KEY"):
        request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                models = [model["name"] for model in json.loads(response.read().decode("utf-8")).get("models", [])]
            print("Gemini flash models:", ", ".join(name for name in models if "flash" in name))
        except OSError as error:
            print(f"Gemini model listing unavailable: {error}")


def _wait_for_port(port: int, process: subprocess.Popen, log: Path, seconds: float = 180) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server for port {port} exited:\n{log.read_text(errors='replace')[-4000:]}")
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise SystemExit(f"nothing listened on port {port} within {seconds} s")


def _config(out: Path, memory: str, model: str, base_url: str) -> Path:
    config = {"task_name": "travel_planner",
              "agent": {"model_name": model, "backend": "gemini" if "gemini" in model.lower() else "openai",
                        "api_key": "", "base_url": base_url},
              "memory": {"use_step_memory": False, "memory_system_name": memory,
                         "server_url": f"http://127.0.0.1:{MEMORY_PORT}"},
              "env": {"env_name": "travel_planner", "env_server_url": f"http://127.0.0.1:{ENV_PORT}",
                      "env_config": {"judgement_mode": "none"}},
              "task_specific": {"max_steps": 30},
              "output": {"output_dir": str(out / "run"), "log_dir": str(out / "logs"),
                         "global_csv": str(out / "results.csv")}}
    path = out / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def _reach(log: Path) -> dict:
    """Whether the plans a question's constraint sentences name came back from memory."""
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if log.exists() else []
    found = []
    for row in rows:
        lines = row["question"].split("\n")
        if len(lines) < 3:
            continue
        companions = set(re.findall(r"\b[A-Z][a-z]+\b", lines[1]))
        body = "\n".join(lines[2:])
        relevant = {name for name in companions if re.search(r"\b%s\b" % re.escape(name), body)}
        if relevant:
            reached = relevant & set(row["plans"])
            found.append((float(relevant <= reached), len(reached) / len(relevant), row["context_chars"]))
    if not found:
        return {"cases": 0}
    return {"cases": len(found), "recall_all": round(statistics.fmean(item[0] for item in found), 4),
            "dependency_recall": round(statistics.fmean(item[1] for item in found), 4),
            "context_chars_mean": round(statistics.fmean(item[2] for item in found), 1)}


def _quota(log: Path) -> dict | None:
    """The per-day quota a provider refusal named in the runner's output, if one did."""
    tail = log.read_text(errors="replace")[-20000:] if log.exists() else ""
    if "PerDay" not in tail:
        return None
    limit = re.findall(r'"quotaValue":\s*"?(\d+)', tail)
    return {"exhausted": "per-day", "limit": int(limit[-1]) if limit else None}


def _plans(directory: Path) -> list[Path]:
    return sorted(directory.glob("generated_plan_*.json"), key=lambda path: int(path.stem.rsplit("_", 1)[1]))


def summarize(out: Path, *, memory: str, client: str, model: str, selection: str, attempts: int,
              exit_code: int, seconds: float) -> dict:
    scores = {}
    if (out / "results.csv").exists():
        with (out / "results.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if rows:
            scores = {key: rows[-1][key] for key in ("total_groups", "PS", "SPS", "SR")}
    steps, failures, persons = [], 0, 0
    for plan in _plans(out / "run"):
        for pad in json.loads(plan.read_text(encoding="utf-8")).get("scratchpads", []):
            persons += 1
            steps.append(pad.get("total_steps") or 0)
            failures += not pad.get("success", True)
    usage = {"calls": 0, "input": 0, "output": 0, "thoughts": 0, "cached": 0, "retries": 0}
    usage_log = out / "usage.jsonl"
    if usage_log.exists():
        for line in usage_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                call = json.loads(line)
                usage["calls"] += 1
                for key in ("input", "output", "thoughts", "cached", "retries"):
                    usage[key] += call.get(key, 0)
    else:
        stats = out / "run" / "stats_results" / "usage_stats.json"
        if stats.exists():
            recorded = json.loads(stats.read_text())
            usage.update(input=recorded.get("total_input_tokens", 0), output=recorded.get("total_output_tokens", 0))
    quota = _quota(out / "run_travel.log") if exit_code else None
    summary = {"memory": memory, "client": client, "model": model, "groups": selection, "attempts": attempts,
               "status": "complete" if exit_code == 0 else "quota_exhausted" if quota else "failed",
               "groups_done": [int(path.stem.rsplit("_", 1)[1]) for path in _plans(out / "run")], "quota": quota,
               "exit_code": exit_code, "seconds": round(seconds, 1), "scores": scores, "persons": persons,
               "steps_mean": round(statistics.fmean(steps), 2) if steps else None,
               "persons_without_final_plan": failures, "usage": usage, "memory_reach": _reach(out / "retrieval.jsonl")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run(root: Path, out: Path, *, memory: str, client: str, model: str, first: int | None, ids: str | None,
        endpoint: str | None, attempts: int) -> int:
    checkout = root / "MemoryArena"
    out.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, MEMORYARENA_USAGE_LOG=str(out / "usage.jsonl"),
                       HF_HUB_DISABLE_TELEMETRY="1", PYTHONUNBUFFERED="1")
    base_url = ""
    if endpoint:
        base_url = f"{endpoint.rstrip('/')}/v1"
        environment["MEMORYARENA_GEMINI_ENDPOINT"] = f"{endpoint.rstrip('/')}/v1beta/models/{{model}}:generateContent"
        environment.setdefault("OPENAI_API_KEY", "dry-run")
        environment.setdefault("GEMINI_API_KEY", "dry-run")
    selection = ["--first", str(first)] if first is not None else ["--ids", ids]
    servers = []
    started = time.monotonic()
    code, attempt = 1, 0
    try:
        for command, port, log in (
                ([sys.executable, "env/env_server.py"], ENV_PORT, out / "env_server.log"),
                ([sys.executable, str(SCRIPTS / "memoryarena_memory_server.py"), "--port", str(MEMORY_PORT),
                  "--memoryarena", str(checkout), "--log", str(out / "retrieval.jsonl")], MEMORY_PORT,
                 out / "memory_server.log")):
            stream = log.open("w")
            servers.append((subprocess.Popen(command, cwd=checkout, stdout=stream, stderr=subprocess.STDOUT,
                                             env=environment), stream))
            _wait_for_port(port, servers[-1][0], log)
        config = _config(out, memory, model, base_url)
        with (out / "run_travel.log").open("a") as log:
            for attempt in range(1, attempts + 1):
                log.write(f"\n===== attempt {attempt} =====\n")
                log.flush()
                code = subprocess.call([sys.executable, str(SCRIPTS / "memoryarena_run_travel.py"), str(checkout),
                                        str(config), *selection, "--client", client],
                                       cwd=out, stdout=log, stderr=subprocess.STDOUT, env=environment)
                if code == 0 or _quota(out / "run_travel.log"):
                    break
    finally:
        for process, stream in servers:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            stream.close()
    summary = summarize(out, memory=memory, client=client, model=model, selection=" ".join(selection),
                        attempts=attempt, exit_code=code, seconds=time.monotonic() - started)
    print(json.dumps(summary, indent=2))
    if summary["status"] == "quota_exhausted":
        print(f"::warning title=MemoryArena::{memory}: the provider's per-day quota ended the run after groups "
              f"{summary['groups_done']}; the next run continues with the rest")
        return 0
    if code != 0:
        tail = (out / "run_travel.log").read_text(errors="replace")[-6000:]
        print(f"the runner failed with exit code {code}; last output:\n{tail}", file=sys.stderr)
    return code


def report(inputs: list[Path], output: Path) -> None:
    summaries = sorted((json.loads(path.read_text()) for directory in inputs
                        for path in directory.rglob("summary.json")),
                       key=lambda item: (item["client"], item["memory"]))
    header = ("| memory | client | model | groups | done | persons | PS | SPS | SR | reach recall_all | calls | "
              "input tokens | output tokens | thought tokens | steps | no final plan | status |")
    lines = [header, "|" + "---|" * (header.count("|") - 1)]
    for item in summaries:
        scores, usage, reach = item["scores"], item["usage"], item["memory_reach"]
        lines.append(f"| {item['memory']} | {item['client']} | {item['model']} | {item['groups']} | "
                     f"{len(item.get('groups_done', []))} | {item['persons']} | {scores.get('PS', '-')} | "
                     f"{scores.get('SPS', '-')} | {scores.get('SR', '-')} | {reach.get('recall_all', '-')} | "
                     f"{usage['calls']} | {usage['input']} | {usage['output']} | {usage['thoughts']} | "
                     f"{item['steps_mean']} | {item['persons_without_final_plan']} | "
                     f"{item.get('status', item['exit_code'])} |")
    text = "## MemoryArena Group Travel Planner\n\n" + "\n".join(lines) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(summaries, indent=2) + "\n")
    output.write_text(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(text)
    print(text)


def merge(checkout: Path, inputs: list[Path], output: Path) -> None:
    """Score the union of several runs per memory system with MemoryArena's own combination and evaluation."""
    sys.path.insert(0, str(checkout.resolve()))
    from env.env_systems.travel_planner_env.combination import combine
    from env.env_systems.travel_planner_env.eval import evaluate

    runs: dict[tuple, list[Path]] = {}
    for directory in inputs:
        for path in sorted(directory.rglob("summary.json")):
            item = json.loads(path.read_text())
            runs.setdefault((item["memory"], item["client"], item["model"]), []).append(path.parent)
    for (memory, client, model), directories in sorted(runs.items()):
        work = output.parent / f"merged-{memory}-{client}"
        shutil.rmtree(work, ignore_errors=True)
        (work / "run").mkdir(parents=True)
        for directory in directories:
            for plan in _plans(directory / "run"):
                if (work / "run" / plan.name).exists():
                    raise SystemExit(f"group {plan.stem} of {memory} ran twice; keep one run of each group")
                shutil.copy2(plan, work / "run" / plan.name)
        with (work / "usage.jsonl").open("w") as usage, (work / "retrieval.jsonl").open("w") as retrieval:
            for directory in directories:
                for name, stream in (("usage.jsonl", usage), ("retrieval.jsonl", retrieval)):
                    if (directory / name).exists():
                        stream.write((directory / name).read_text())
        submission = combine(model_name=model, output_dir=str(work / "run"),
                             submission_file_dir=str(work / "submission"), mode="sole_planning")
        scores = evaluate(submission_path=submission, model_name=model, memory_system=memory, global_csv=None)
        with (work / "results.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["total_groups", "PS", "SPS", "SR"])
            writer.writeheader()
            writer.writerow({"total_groups": len(_plans(work / "run")), "PS": f"{scores['ps']:.2f}",
                             "SPS": f"{scores['sps']:.2f}", "SR": f"{scores['sr']:.2f}"})
        summarize(work, memory=memory, client=client, model=model, selection=f"{len(directories)} runs",
                  attempts=0, exit_code=0, seconds=0.0)
    report([output.parent / f"merged-{memory}-{client}" for memory, client, _ in sorted(runs)], output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prepared = commands.add_parser("prepare")
    prepared.add_argument("--root", type=Path, required=True)
    prepared.add_argument("--cache", type=Path, required=True, help="where the flights table is kept between runs")
    running = commands.add_parser("run")
    running.add_argument("--root", type=Path, required=True)
    running.add_argument("--out", type=Path, required=True)
    running.add_argument("--memory", required=True, help="synapse-lexical, synapse-lexical-5, bm25 or long_context")
    running.add_argument("--client", choices=("benchmark", "gemini-rest"), default="gemini-rest")
    running.add_argument("--model", required=True)
    chosen = running.add_mutually_exclusive_group(required=True)
    chosen.add_argument("--first", type=int)
    chosen.add_argument("--ids")
    running.add_argument("--endpoint", help="a dry-run model endpoint (scripts/memoryarena_oracle_endpoint.py)")
    running.add_argument("--attempts", type=int, default=3)
    reporting = commands.add_parser("report")
    reporting.add_argument("inputs", type=Path, nargs="+")
    reporting.add_argument("--output", type=Path, required=True)
    merging = commands.add_parser("merge")
    merging.add_argument("inputs", type=Path, nargs="+", help="downloaded run artifacts, any number of days")
    merging.add_argument("--checkout", type=Path, required=True, help="the pinned MemoryArena checkout")
    merging.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.command == "prepare":
        prepare(options.root, options.cache)
    elif options.command == "run":
        raise SystemExit(run(options.root, options.out, memory=options.memory, client=options.client,
                             model=options.model, first=options.first, ids=options.ids, endpoint=options.endpoint,
                             attempts=options.attempts))
    elif options.command == "merge":
        merge(options.checkout, options.inputs, options.output)
    else:
        report(options.inputs, options.output)


if __name__ == "__main__":
    main()
