"""An external routing-control world for large durable-memory acceptance."""
from __future__ import annotations

import hashlib

from acceptance.memory._world import MemoryWorld, answer, stateful, tool

NODES = 128
CONFIGURATION_BYTES = 160 * 1024


def configuration(node, size=CONFIGURATION_BYTES):
    """Distinct deterministic route tables, not a repeated padding string."""
    routes = []
    for index in range(size // 80 + 1):
        key = hashlib.sha256(f"node={node};prefix={index};epoch=7".encode()).hexdigest()
        routes.append(f"10.{node}.{index // 256}.{index % 256}/32 via zone-{index % 3} sha256={key}\n")
    return "".join(routes)[:size]


def snapshot(nodes=NODES, size=CONFIGURATION_BYTES):
    return [{"id": f"router-{index:03d}", "epoch": 7, "configuration": configuration(index, size),
             "text": f"routerkey{index:03d} signed routing table across three failure zones"}
            for index in range(nodes)]


def world(root, *, nodes=NODES, size=CONFIGURATION_BYTES):
    read = {"idempotent": True, "observation": True}
    items = [tool("routing_snapshot", "control:network", [answer({"ok": True, "nodes": snapshot(nodes, size)})],
                  server="control", contract=read)]
    for name, source in (("control_epoch", "control"), ("independent_epoch", "witness")):
        contract = dict(read)
        if source == "witness":
            contract["verifies"] = {"subject": {"request": "node", "answer": "node"},
                                    "scope": {"value": "network"}}
        items.append(tool(name, f"{source}:network", [stateful({"ok": True}, act={"read": "epochs", "key": "node"},
                          otherwise={"ok": False, "err": "UNKNOWN_NODE"})], server=source, contract=contract))
    items.append(tool("activate_routes", "control:network", [answer({"ok": True}, effect="routes-activated")],
                      server="control", contract={"requires_established": True}))
    instance = MemoryWorld(root, items, provenance={"control:network": {"ancestors": []},
                           "witness:network": {"ancestors": []}}, knowledge={"embedder": None, "budget": 3,
                           "properties": {key: {"freshness": "state"} for key in ("configuration", "epoch")}})
    publish_epoch(instance, 7)
    return instance


def publish_epoch(instance, epoch):
    state = instance.world()
    state["objects"] = {"epochs": {"router-000": {"node": "router-000", "epoch": epoch}}}
    instance.restore(state)
