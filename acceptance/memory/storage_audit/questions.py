"""Specify the historical views a storage incident investigation must distinguish."""
from __future__ import annotations

import json
from pathlib import Path

QUERIES = (
    "storage engineering node capacity availability",
    "storage node free",
    "node alive availability",
    "engineering capacity free availability",
    "STORAGE node alive",
    "node free capacity",
    "availability storage engineering",
    "free alive",
    "replicastore partition placement",
    "replica layout segment ownership",
    "partition placement replicas",
    "segment ownership 14",
    "replica layout 17 23",
    "partition layout 10 18",
    "placement ownership 12 19",
    "unobserved firmware temperature checksum",
)
VIEWS = (
    ("before-observation", "2026-10-09T07:59:59Z", 4),
    ("original-history", "2026-10-09T08:30:00Z", 4),
    ("correction-not-known", "2026-10-09T09:30:00Z", 2),
    ("corrected-history", "2026-10-09T09:30:00Z", 4),
)


def questions():
    return [{"view": view, "topic": number, "query": query,
             "options": {"valid_at": valid_at, "known_as_of": known, "channels": ["lexical"]}}
            for view, valid_at, known in VIEWS for number, query in enumerate(QUERIES)]


def source():
    template = (Path(__file__).parent / "investigate.syn").read_text()
    return template.replace("INVESTIGATION_QUESTIONS", json.dumps(questions()))
