"""The search index follows its embedder (review DEEP-5, DEEP-6).

* The embedding model is busy once, when a session learns the basic plan's
  price: the statement is recorded without a vector, and the semantic channel
  cannot find it. A later session reads the same price again — a copy, no new
  confidence — and the model answers: the copy restores the index, and the
  semantic channel finds the plan.
* Memory learned Alpha (basic) and Beta (plus) under embedder v1. The operator
  adopts embedder v2, whose space orders the same concepts the other way round
  (equal dimensions, another space), and reassesses. Asked about Alpha under v2,
  the semantic channel compares nothing of v1 — it never offers plus for Alpha;
  once Alpha is read again under v2, it finds basic.
"""
from __future__ import annotations

import json

from acceptance.memory import _catalog as catalog
from acceptance.memory._world import MemoryWorld, embedder, tool

BUSY = {"when": {"text": "Basic plan costs 20 euro per month"}, "sequence": [{"payload": {"ok": False, "err": "MODEL_BUSY"}}],
        "then": embedder(catalog.CONCEPTS)["then"]}
GREEK = (("alpha", {"alpha"}), ("beta", {"beta"}))


def _world(root, embedders, embedder_tool="embed") -> MemoryWorld:
    tools = [item for item in catalog.tools() if item["name"] != "embed"]
    tools += [tool(name, f"{name}:model", rules, server="model", role="reason") for name, rules in embedders.items()]
    provenance = {**catalog.provenance(), **{f"{name}:model": {"ancestors": []} for name in embedders}}
    return MemoryWorld(root, tools, provenance=provenance, knowledge={
        "embedder": {"tool": embedder_tool, "version": embedder_tool}, "properties": catalog.PROPERTIES, "budget": 1})


def _semantic(world, run_id, query):
    found = catalog.ask(world, run_id, "basic", "monthly_price", query, channels=["semantic"])
    return found["search"]


def _learn(world, run_id, plan, text, value=20):
    catalog.publish(world, "catalog", plan, "monthly_price", value, text=text, start="2026-01-01")
    return catalog.learn(world, run_id, plan)


def test_a_reading_after_a_busy_model_restores_the_index(tmp_path):
    world = _world(tmp_path, {"embed": [BUSY, embedder(catalog.CONCEPTS)]})
    first = _learn(world, "learn", "basic", "Basic plan costs 20 euro per month")
    statement = first["knowledge"]["declared"][0]["statement"]
    assert world.owner().state()["knowledge"]["versions"][statement]["vector"] is None
    assert _semantic(world, "before", "basic plan price")["candidates"] == []

    again = _learn(world, "learn-again", "basic", "Basic plan costs 20 euro per month")
    assert again["knowledge"]["declared"] == [] and len(again["knowledge"]["copies"]) == 1  # No new confidence.
    assert again["knowledge"]["reindexed"] == [{"run_id": "learn-again", "statement": statement}]
    entry = world.owner().state()["knowledge"]["versions"][statement]
    assert entry["vector"] is not None and (entry["known_from"], entry["known_until"]) == (first["window"]["index"],
                                                                                          None)
    assert [item["id"] for item in _semantic(world, "after", "basic plan price")["candidates"]] == [statement]


def test_vectors_of_another_embedder_are_never_compared(tmp_path):
    world = _world(tmp_path, {"v1": [embedder(GREEK)], "v2": [embedder(tuple(reversed(GREEK)))]}, "v1")
    alpha = _learn(world, "learn-alpha", "basic", "Alpha")["knowledge"]["declared"][0]["statement"]
    _learn(world, "learn-beta", "plus", "Beta", 30)
    assert [item["id"] for item in _semantic(world, "v1-alpha", "Alpha")["candidates"]] == [alpha]

    configuration = json.loads(world.configuration_path.read_text())
    configuration["knowledge"]["embedder"] = {"tool": "v2", "version": "v2"}
    adopted = world.root / "memory.v2.json"
    adopted.write_text(json.dumps(configuration, sort_keys=True))
    code, result, stderr = world.memory("reassess", configuration=adopted)
    assert code == 0 and result["status"] == "RECORDED", (result, stderr)
    world.configuration_path = adopted

    searched = _semantic(world, "v2-alpha", "Alpha")
    assert searched["candidates"] == [] and searched["cost"]["vectors"] == 0  # Nothing of v1 is compared.
    _learn(world, "relearn-alpha", "basic", "Alpha")
    assert [item["id"] for item in _semantic(world, "v2-alpha-again", "Alpha")["candidates"]] == [alpha]
