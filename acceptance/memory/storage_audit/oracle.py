"""Check historical candidates against independent storage state reconstruction."""
from __future__ import annotations

import ast
import copy
import json

from acceptance.memory.storage_evacuation.cluster import Cluster


def observed_states(corpus):
    original = Cluster()
    corrected = copy.deepcopy(original)
    moves = json.loads((corpus / "move-ledger.json").read_text())
    first_batch = moves[:24]
    assert len(first_batch) == 24 and all(move["epoch"] == 0 for move in first_batch)
    for move in first_batch:
        Cluster.move(corrected.nodes, corrected.shards, move)
    corrected.nodes[12]["alive"] = False
    corrected.nodes[5]["capacity"] = corrected.nodes[5]["used"] + 8
    Cluster.validate(corrected.nodes, corrected.shards)
    return {"original-history": original, "correction-not-known": original,
            "corrected-history": corrected}


def check_candidate(candidate, cluster):
    identity, attribute = candidate["entity"], candidate["attribute"]
    assert candidate["polarity"] is True and candidate["conditions"] == {}
    assert candidate["freshness"] == "current"
    if identity["namespace"] == "storage":
        assert identity["type"] == "node" and attribute in {"free", "alive"}
        node = cluster.nodes[identity["id"]]
        expected = node["alive"] if attribute == "alive" else node["capacity"] - node["used"]
        assert candidate["value"] == expected, (candidate, expected)
    else:
        assert identity["namespace"] == "replicastore" and identity["type"] == "partition"
        assert attribute == "placement"
        expected = cluster.shards[identity["id"]]["replicas"]
        assert ast.literal_eval(candidate["value"]) == expected, (candidate, expected)


def verify_temporal_facts(corpus, questions, events):
    states, primary, checked = observed_states(corpus), {}, 0
    for question, event in zip(questions, events):
        candidates = event["candidates"]
        if question["view"] == "before-observation" or question["topic"] == 15:
            assert candidates == []
        else:
            for candidate in candidates:
                check_candidate(candidate, states[question["view"]])
                checked += 1
        if question["topic"] == 0 and question["view"] != "before-observation":
            assert len(candidates) == 48
            primary[question["view"]] = {
                (item["entity"]["id"], item["attribute"]): item["value"] for item in candidates}
    assert primary["original-history"][(12, "alive")] is True
    assert primary["correction-not-known"][(12, "alive")] is True
    assert primary["corrected-history"][(12, "alive")] is False
    assert primary["corrected-history"][(5, "free")] == 8
    assert primary["original-history"][(5, "free")] > 8
    return {"independently_checked_candidates": checked, "node12_original_alive": True,
            "node12_before_correction_known_alive": True, "node12_corrected_alive": False,
            "node5_original_free": primary["original-history"][(5, "free")],
            "node5_corrected_free": 8}
