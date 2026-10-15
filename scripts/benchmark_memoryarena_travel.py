#!/usr/bin/env python3
"""MemoryArena Group Travel Planner through Synapse's candidate channel: the parts measurable without a model.

MemoryArena (He et al., arXiv 2602.16313) plans trips for a group one traveler at a time; later travelers'
constraints name earlier travelers' choices ("rated within 0.3 of Kelly's second-day accommodation"), so the
plan an agent made for Kelly must come back from memory. The benchmark scores plans against ground truth by
string similarity (PS, SPS, SR); no model judges them. A model plans, though, and none is available here, so
this script publishes what does not need one:

* ``battery`` — memory holds, before each traveler, the base person's chunk and one entry per earlier traveler
  in the run's own JSON shape with the ground-truth plan as ``final_plan`` and an empty scratchpad; the query
  is the traveler's question. The relevant entries are the travelers the question's constraint sentences name
  (the companion line names everyone and stays in the query, uncounted). Systems: the lexical channel
  (``palace-lexical/v2`` through ``PreparedKnowledgeIndex.rank``, the ranking ``knowledge.search`` uses) at
  the benchmark's top_k=3 and at 5, MemoryArena's own ``RAGMemorySystem`` (BM25) at 3 and 5 when
  ``--memoryarena`` names its checkout, and long context.
* ``--dry-run DIR`` — folds in the logs of ``scripts/memoryarena_oracle_endpoint.py`` from a run of
  MemoryArena's own pipeline: there memory entries carry the scratchpad of real tool calls, and the measure is
  whether the named travelers' plans reached the agent's prompt.

What it does not measure, disclosed in the output: plan quality (PS, SPS, SR of a planning model), the
semantic channel, and the benchmark's other three domains. Retrieval proposes context; it admits nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

DATASET = {"name": "ZexueHe/memoryarena", "file": "group_travel_planner/data.jsonl",
           "revision": "da1a37c8b19280e18627ca01cf368195a5e1d92e",
           "sha256": "2f955d444f6f3ad3c5da2064359ab19f8fc1f90621ff9d00723a450a009c3732"}
TOKENIZER = "gpt-4o-mini"


def format_person_plan(name, daily_plans):
    """MemoryArena's ``run_travel.format_person_plan``."""
    lines = [f"=== {name}'s Plan ==="]
    for day in daily_plans:
        lines.append(f"Day {day.get('days') or day.get('day')}:")
        for label, key in (("Current City", "current_city"), ("Transportation", "transportation"),
                           ("Breakfast", "breakfast"), ("Attraction", "attraction"), ("Lunch", "lunch"),
                           ("Dinner", "dinner"), ("Accommodation", "accommodation")):
            lines.append(f"{label}: {day.get(key, '-')}")
        lines.append("")
    return "\n".join(lines)


def named_earlier(query: str, earlier) -> list[str]:
    """The earlier travelers a question's constraint sentences name; line two is the companion line."""
    body = "\n".join(query.split("\n")[2:])
    return sorted(name for name in earlier if re.search(r"\b%s\b" % re.escape(name), body))


def cases(rows):
    for row in rows:
        base = row["base_person"]
        chunks = [(base["name"], json.dumps({"name": base["name"], "query": base["query"], "is_base_person": True,
                                              "final_plan": format_person_plan(base["name"], base["daily_plans"])},
                                             ensure_ascii=False))]
        for round_idx, (query, plans) in enumerate(zip(row["questions"], row["answers"]), 1):
            name = re.match(r"I am (\w+)\.", query).group(1)
            yield round_idx, list(chunks), query, set(named_earlier(query, [owner for owner, _ in chunks]))
            chunks.append((name, json.dumps({"name": name, "query": query, "scratchpad": [],
                                             "final_plan": format_person_plan(name, plans)}, ensure_ascii=False)))


def lexical(k):
    from synapse.memory_consolidation.knowledge.prepared import PreparedKnowledgeIndex

    def retrieve(chunks, query):
        versions = {f"m{index:03d}": {"record": {"id": f"m{index:03d}", "subject": "", "property": "", "value": "",
                                                 "conditions": {}, "text": text}}
                    for index, (_, text) in enumerate(chunks)}
        ranked = PreparedKnowledgeIndex(versions).rank(query, list(versions.values()), k)
        return [chunks[int(identity[1:])] for identity in ranked]
    return retrieve


def memoryarena_bm25(checkout: Path, k):
    spec = importlib.util.spec_from_file_location("memoryarena_rag", checkout / "memory/memory_systems/rag.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def retrieve(chunks, query):
        system = module.RAGMemorySystem(retrieval_method="bm25", top_k=k)
        owner_of = {}
        for owner, text in chunks:
            for piece in system._split_chunk(text):
                owner_of.setdefault(piece, owner)
            system.add_chunk(text)
        return [(owner_of[piece], piece) for piece in system._bm25_retrieve(query)]
    return retrieve


def long_context(tokenizer, budget=120_000):
    def retrieve(chunks, query):
        kept, used = [], 0
        for owner, text in reversed(chunks):
            used += len(tokenizer.encode(text, disallowed_special=()))
            if used > budget:
                break
            kept.append((owner, text))
        return kept[::-1]
    return retrieve


def _summary(rows, context_key):
    return {"cases": len(rows),
            "recall_all": round(statistics.fmean(row["recall_all"] for row in rows), 4),
            "dependency_recall": round(statistics.fmean(row["dependency_recall"] for row in rows), 4),
            f"{context_key}_mean": round(statistics.fmean(row[context_key] for row in rows), 1)}


def battery(rows, systems, tokenizer) -> dict:
    measured = {name: [] for name in systems}
    for round_idx, chunks, query, relevant in cases(rows):
        if not relevant:
            continue
        for name, retrieve in systems.items():
            got = retrieve(chunks, query)
            owners = {owner for owner, _ in got}
            measured[name].append({"memory": len(chunks), "recall_all": float(relevant <= owners),
                                   "dependency_recall": len(relevant & owners) / len(relevant),
                                   "context_tokens": sum(len(tokenizer.encode(text, disallowed_special=()))
                                                         for _, text in got)})
    return {name: {"overall": _summary(found, "context_tokens"),
                   "by_memory_entries": {str(size): _summary([row for row in found if row["memory"] == size],
                                                             "context_tokens")
                                         for size in sorted({row["memory"] for row in found})}}
            for name, found in measured.items()}


def dry_run(directory: Path) -> dict:
    """The oracle endpoint's logs, one file per memory system."""
    report = {}
    for log in sorted(directory.glob("oracle_*.jsonl")):
        logged = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        found = [{"round": row["round"], "recall_all": float(set(row["relevant"]) <= set(row["reached"])),
                  "dependency_recall": len(row["reached"]) / len(row["relevant"]),
                  "memory_chars": row["memory_chars"]} for row in logged if row["relevant"]]
        report[log.stem.removeprefix("oracle_")] = {
            "travelers": len(logged), "overall": _summary(found, "memory_chars"),
            "by_round": {str(k): _summary([row for row in found if row["round"] == k], "memory_chars")
                         for k in sorted({row["round"] for row in found})}}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", type=Path, help=f"{DATASET['file']} downloaded from {DATASET['name']}")
    parser.add_argument("--memoryarena", type=Path, help="a MemoryArena checkout, for its BM25 baseline")
    parser.add_argument("--dry-run", type=Path, help="the oracle endpoint's log directory")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    import tiktoken

    tokenizer = tiktoken.encoding_for_model(TOKENIZER)
    raw = options.dataset.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    if sha256 != DATASET["sha256"]:
        raise SystemExit(f"the dataset is not the pinned release ({sha256})")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    systems = {"synapse-lexical@3": lexical(3), "synapse-lexical@5": lexical(5)}
    if options.memoryarena is not None:
        systems |= {"memoryarena-bm25@3": memoryarena_bm25(options.memoryarena, 3),
                    "memoryarena-bm25@5": memoryarena_bm25(options.memoryarena, 5)}
    systems["long_context"] = long_context(tokenizer)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPOSITORY, capture_output=True,
                                text=True).stdout.strip())
    report = {"schema_version": "synapse.memory.memoryarena-travel/v1",
              "dataset": DATASET | {"groups": len(rows)},
              "commit": commit, "dirty": dirty, "python": platform.python_version(), "tokenizer": TOKENIZER,
              "channel": "lexical (palace-lexical/v2)", "relevant": "earlier travelers named in constraint sentences",
              "not_measured": ["plan quality: PS, SPS and SR of a planning model (no model available)",
                               "semantic channel (no external embedding model available)",
                               "bundled shopping, progressive search and formal reasoning domains"],
              "battery": battery(rows, systems, tokenizer)}
    if options.dry_run is not None:
        report["dry_run"] = dry_run(options.dry_run)
    text = json.dumps(report, indent=2, sort_keys=True)
    if options.output is not None:
        options.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
