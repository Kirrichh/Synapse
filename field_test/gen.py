"""Incident generator and independent oracle for Operation Blackout."""
from __future__ import annotations

import json
import random

SERVICE_NAMES = [
    "gateway", "auth", "orders", "payments", "billing", "catalog", "search",
    "inventory", "cart", "ledger", "notify", "profile", "db", "cache", "queue",
]


def make_incident(rng: random.Random, idx: int, minutes: int = 30) -> dict:
    n = rng.randint(7, 12)
    names = rng.sample(SERVICE_NAMES, n)
    # DAG: a service may depend only on services later in the order.
    graph = {s: [] for s in names}
    for i, s in enumerate(names):
        later = names[i + 1:]
        if later:
            graph[s] = sorted(rng.sample(later, rng.randint(0, min(3, len(later)))))
    root = rng.choice(names[len(names) // 2:])  # deep enough to have dependents
    # Dependents of root fail after it, with a delay per hop.
    dependents = {root: 0}
    frontier = [root]
    while frontier:
        nxt = []
        for cur in frontier:
            for s in names:
                if cur in graph[s] and s not in dependents:
                    dependents[s] = dependents[cur] + rng.randint(1, 3)
                    nxt.append(s)
        frontier = nxt
    threshold = 50
    start = rng.randint(5, 12)
    telemetry = {}
    for s in names:
        series = [rng.randint(0, 20) for _ in range(minutes)]  # background noise
        if s in dependents:
            for m in range(start + dependents[s], minutes):
                series[m] = rng.randint(60, 400)
        elif rng.random() < 0.3:
            # a noisy but healthy service: one spike exactly at threshold
            series[rng.randrange(minutes)] = threshold
        telemetry[s] = series
    deploys = []
    others = [s for s in names if s != root]
    for k in range(rng.randint(4, 8)):
        deploys.append({"id": f"d{idx}-{k}", "service": rng.choice(others), "minute": rng.randint(0, minutes - 1)})
    # The culprit: last deploy to root before its first failure (minute `start`).
    culprit_minute = rng.randint(1, start)
    deploys.append({"id": f"d{idx}-c", "service": root, "minute": culprit_minute})
    deploys.append({"id": f"d{idx}-early", "service": root, "minute": rng.randint(0, culprit_minute - 1)})
    deploys.append({"id": f"d{idx}-late", "service": root, "minute": rng.randint(start + 1, minutes - 1)})
    rng.shuffle(deploys)
    alerts = []
    for s in sorted(dependents, key=lambda x: dependents[x]):
        for _ in range(rng.randint(1, 3)):  # duplicates
            alerts.append({"service": s, "level": rng.randint(2, 4)})
    blast = sorted(dependents)
    return {
        "id": f"INC-{idx}",
        "graph": graph,
        "telemetry": telemetry,
        "threshold": threshold,
        "deploys": deploys,
        "alerts": alerts,
        "approved_radius": len(blast) + rng.randint(0, 3),
    }


def oracle(inc: dict) -> dict:
    """Expected answers, computed independently of the Synapse program."""
    thr = inc["threshold"]
    first = {}
    for s, series in inc["telemetry"].items():
        for m, v in enumerate(series):
            if v > thr:
                first[s] = m
                break
    roots = sorted(s for s in first if not any(d in first for d in inc["graph"][s]))
    root = roots[0]
    cands = [d for d in inc["deploys"] if d["service"] == root and d["minute"] <= first[root]]
    culprit = max(cands, key=lambda d: d["minute"])["id"]
    levels = max(a["level"] for a in inc["alerts"])
    sev = "SEV1" if levels >= 4 else "SEV2" if levels >= 3 else "SEV3"
    blast = sorted(first)
    budget = sum(sum(inc["telemetry"][s]) for s in blast)
    return {
        "alerts_received": str(len(inc["alerts"])),
        "alerting_services": ",".join(sorted({a["service"] for a in inc["alerts"]})),
        "severity": sev,
        "root_candidates": ",".join(roots),
        "root": root,
        "blast_radius": ",".join(blast),
        "blast_consistent": "True",
        "culprit": culprit,
        "debate_has_verdict": "True",
        "fracture_agreed": root,
        "plan_status": None,  # recorded, not judged
        "ops_executed": f"rollback {culprit};restart {root}",
        "postmortem_after_bad_draft": "draft",
        "postmortem": f"{inc['id']}: root {root}, culprit {culprit}",
        "modulation_suppresses_risky": None,
        "budget": str(budget),
        "vm_budget": str(budget),
        "vm_resumed_budget": str(budget),
    }


def to_synapse(value) -> str:
    """JSON is a subset of Synapse literals except for true/false/null spelling (same)."""
    return json.dumps(value)
