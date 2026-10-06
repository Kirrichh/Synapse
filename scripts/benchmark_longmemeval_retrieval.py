#!/usr/bin/env python3
"""LongMemEval through Synapse's candidate channel: the retrieval part of the published protocol (review §9.4).

LongMemEval (Wu et al., ICLR 2025) measures five abilities of long-term
information memory; its retrieval evaluation asks whether the evidence
sessions of a question are among the top-k candidates of its haystack. This
adapter runs that part on the cleaned release (``longmemeval_s_cleaned.json``,
dataset ``xiaowu0162/longmemeval-cleaned``) with Synapse's lexical candidate
channel (``palace-lexical/v2``, the channel ``search_knowledge`` ranks with):

* every haystack session is one candidate, its text the session's turns;
* the query is the question; abstention questions (``_abs``) are excluded, as
  the published retrieval evaluation excludes them;
* the measures are the published ones: recall_any@k, recall_all@k and
  ndcg_any@k over the evidence sessions, for k = 5 and 10, overall and per
  question type.

What it does not measure, disclosed in the output: no reader model answers the
questions and no model judges answers (no model is available here), so QA
accuracy — and with it temporal reasoning, knowledge updates and abstention as
answers — is not measured; the semantic channel is not measured (no external
embedding model is available; the scripted concept embedder of the acceptance
files proves wiring, not quality). Admission is not involved: retrieval
proposes candidates, it never establishes a fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

DATASET = {"name": "xiaowu0162/longmemeval-cleaned", "file": "longmemeval_s_cleaned.json",
           "revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f",
           "sha256": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"}
KS = (5, 10)


def _ndcg(ranked, relevant, k) -> float:
    gains = sum(1.0 / math.log2(position + 2) for position, item in enumerate(ranked[:k]) if item in relevant)
    ideal = sum(1.0 / math.log2(position + 2) for position in range(min(k, len(relevant))))
    return gains / ideal if ideal else 0.0


def evaluate(questions) -> dict:
    from synapse.memory_consolidation.knowledge.search import lexical_ranking

    rows = []
    for question in questions:
        if question["question_id"].endswith("_abs"):
            continue
        entries = [{"record": {"id": session_id, "subject": "", "property": "", "value": "", "conditions": {},
                               "text": "\n".join(turn["content"] for turn in session)}}
                   for session_id, session in zip(question["haystack_session_ids"], question["haystack_sessions"])]
        ranked = lexical_ranking(question["question"], entries, max(KS))
        relevant = set(question["answer_session_ids"])
        row = {"question_id": question["question_id"], "type": question["question_type"]}
        for k in KS:
            top = set(ranked[:k])
            row[f"recall_any@{k}"] = float(bool(top & relevant))
            row[f"recall_all@{k}"] = float(relevant <= top)
            row[f"ndcg_any@{k}"] = _ndcg(ranked, relevant, k)
        rows.append(row)
    measures = [key for key in rows[0] if "@" in key]

    def mean(selected):
        return {key: round(sum(row[key] for row in selected) / len(selected), 4) for key in measures} | {
            "questions": len(selected)}

    types = sorted({row["type"] for row in rows})
    return {"overall": mean(rows), "by_type": {name: mean([row for row in rows if row["type"] == name])
                                               for name in types}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help=f"{DATASET['file']} downloaded from {DATASET['name']}")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    raw = options.dataset.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    if sha256 != DATASET["sha256"]:
        raise SystemExit(f"the dataset is not the pinned release ({sha256})")
    result = evaluate(json.loads(raw))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPOSITORY, capture_output=True,
                                text=True).stdout.strip())
    report = {"schema_version": "synapse.memory.longmemeval-retrieval/v1", "dataset": DATASET, "commit": commit,
              "dirty": dirty, "python": platform.python_version(), "channel": "lexical (palace-lexical/v2)",
              "granularity": "session", "query": "question", "excluded": "abstention questions (_abs)",
              "not_measured": ["QA accuracy (no reader or judge model available)",
                               "semantic channel (no external embedding model available)"],
              "result": result}
    text = json.dumps(report, indent=2, sort_keys=True)
    if options.output is not None:
        options.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
