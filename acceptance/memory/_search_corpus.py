"""Construct versioned engineering knowledge for search acceptance."""
from __future__ import annotations

from synapse.memory_consolidation.configuration import Component, KnowledgePolicy
from synapse.memory_consolidation.knowledge.statements import declare

MODEL = "ops-embedding/v1"
JANUARY = "2026-01-01T00:00:00Z"
FEBRUARY = "2026-02-01T00:00:00Z"
MARCH = "2026-03-01T00:00:00Z"
NODE = {"namespace": "cluster-a", "type": "node", "id": "node-7", "uid": "incarnation-a"}


def policy(budget=20):
    return KnowledgePolicy(Component("embed", "v1"), {
        "capacity": {"freshness": "state"},
        "placement": {"freshness": "state"},
        "certificate": {"freshness": "state"},
        "load": {"freshness": "bounded", "ttl_days": 2},
        "failure": {"freshness": "event"},
    }, budget)


def entry(label, *, entity=NODE, prop="capacity", value=96, text="rebalance evacuation capacity",
          start=FEBRUARY, end=None, known_from=1, known_until=None, vector=(1.0, 0.0, 0.0),
          model=MODEL, source="inventory:ops", conditions=None, polarity=True):
    origin = {"tool": "inventory", "args": {"entity": entity, "property": prop}}
    record = declare({"subject": entity, "property": prop, "value": value, "text": text,
                      "source": origin, "valid": {"from": start, "until": end},
                      "conditions": {} if conditions is None else conditions, "polarity": polarity},
                     origin, f"evidence:{label}")
    return {"record": record, "source": source, "run_id": f"observe-{label}",
            "known_from": known_from, "known_until": known_until,
            "vector": None if vector is None else list(vector), "embedded_by": model}


def versions(entries):
    return {item["record"]["id"]: item for item in entries}


def engineering_history():
    history = {
        "initial": entry("initial", value=128, start=JANUARY, known_until=3),
        "corrected": entry("corrected", value=112, start=JANUARY, known_from=3),
        "latest": entry("latest", known_from=4),
        "conflict": entry("conflict", value=72, text="independent observation", vector=None,
                          known_from=5, source="rack-controller:independent"),
        "recreated": entry("recreated", entity={**NODE, "uid": "incarnation-b"}, value=256),
        "follower": entry("follower", value=80, conditions={"role": "follower"}),
        "denial": entry("denial", prop="certificate", value="sha256:a", polarity=False),
        "load": entry("load", prop="load", value=0.71),
        "failure": entry("failure", prop="failure", value="rack-2", start=FEBRUARY),
        "unknown": entry("unknown", prop="firmware", value="v4"),
        "other-model": entry("other-model", entity="node-8", model="ops-embedding/v2"),
        "unnamed-model": entry("unnamed-model", entity="node-9", model=None),
        "short-vector": entry("short-vector", entity="node-10", vector=(1.0,)),
        "zero-vector": entry("zero-vector", entity="node-11", vector=(0.0, 0.0, 0.0)),
        "withdrawn": entry("withdrawn", entity="forgotten-node", known_until=6),
        "returned": entry("returned", entity="node-12", known_from=7),
    }
    history["withdrawn"]["withdrawn"] = 6
    history["returned"]["earlier_known"] = [{"from": 1, "until": 2}, {"from": 4, "until": 5}]
    return history


def fleet_history(nodes=128, epochs=3):
    """Retain capacity corrections plus contradictory rack attestations across a fleet."""
    items = []
    for node in range(nodes):
        subject = {"namespace": f"rack-{node % 6}", "type": "node", "id": node, "uid": f"boot-{node}"}
        for epoch in range(epochs):
            items.append(entry(f"node-{node}-epoch-{epoch}", entity=subject, value=96 - epoch * 8,
                               known_from=epoch + 1, known_until=epoch + 2 if epoch + 1 < epochs else None,
                               vector=(1.0, float(node % 4) / 4, float(epoch) / 8)))
        items.append(entry(f"node-{node}-conflict", entity=subject, value=56, known_from=epochs,
                           text="independent rack attestation", vector=None, source="rack:independent"))
    return versions(reversed(items))
