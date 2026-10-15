"""Model storage migrations with exhaustive independent safety checks."""
from __future__ import annotations

import copy


class Cluster:
    def __init__(self, shard_count=1536):
        capacity = (520 * shard_count + 383) // 384
        self.nodes = [
            {"id": node, "rack": node % 6, "capacity": capacity, "used": 0,
             "alive": True, "drain": node < 4}
            for node in range(24)
        ]
        self.shards = [
            {"id": shard, "size": shard % 9 + 1,
             "replicas": [shard % 24, (shard + 7) % 24, (shard + 14) % 24]}
            for shard in range(shard_count)
        ]
        for shard in self.shards:
            for node in shard["replicas"]:
                self.nodes[node]["used"] += shard["size"]
        self.epoch = self.calls = 0
        self.applied, self.reviews, self.refusals = [], [], []
        self.disturbance = None
        self.validate(self.nodes, self.shards)

    @staticmethod
    def validate(nodes, shards):
        usage, minimum_live = [0] * len(nodes), 4
        for shard in shards:
            replicas = shard["replicas"]
            assert len(replicas) == 3 and len(set(replicas)) == 3
            assert len({nodes[node]["rack"] for node in replicas}) == 3
            living = sum(nodes[node]["alive"] for node in replicas)
            assert living >= 2, (shard["id"], replicas, living)
            minimum_live = min(minimum_live, living)
            for node in replicas:
                usage[node] += shard["size"]
        for node in nodes:
            assert node["used"] == usage[node["id"]]
            assert 0 <= node["used"] <= node["capacity"]
        return minimum_live

    @classmethod
    def move(cls, nodes, shards, move):
        shard = shards[move["shard"]]
        source, target = move["source"], move["target"]
        assert source in shard["replicas"] and target not in shard["replicas"]
        assert nodes[source]["drain"] or not nodes[source]["alive"]
        assert nodes[target]["alive"] and not nodes[target]["drain"]
        assert nodes[target]["used"] + shard["size"] <= nodes[target]["capacity"]
        staying = [node for node in shard["replicas"] if node != source]
        assert nodes[target]["rack"] not in {nodes[node]["rack"] for node in staying}
        live_before = sum(nodes[node]["alive"] for node in shard["replicas"])
        assert live_before >= 2
        # The copy exists before the source replica is removed.
        shard["replicas"] = staying + [target]
        nodes[source]["used"] -= shard["size"]
        nodes[target]["used"] += shard["size"]
        minimum = cls.validate(nodes, shards)
        return {"shard": shard["id"], "source": source, "target": target,
                "size": shard["size"], "live_before": live_before,
                "live_after_copy": live_before + 1,
                "live_after_remove": sum(nodes[node]["alive"] for node in shard["replicas"]),
                "cluster_minimum_live": minimum}

    def call(self, contract, args):
        self.calls += 1
        if contract.name == "topology_read":
            return "ok", {"ok": True, "epoch": self.epoch,
                          "nodes": [{**node, "free": node["capacity"] - node["used"]}
                                    for node in self.nodes],
                          "shards": copy.deepcopy(self.shards)}
        if contract.name == "plan_review":
            return self._review(args)
        if contract.name == "migrate_batch":
            return self._migrate(args)
        raise AssertionError(contract.name)

    def _review(self, args):
        assert args["epoch"] == self.epoch
        nodes, shards = copy.deepcopy(self.nodes), copy.deepcopy(self.shards)
        for move in args["moves"]:
            self.move(nodes, shards, move)
        assert all(not nodes[node]["drain"] and nodes[node]["alive"]
                   for shard in shards for node in shard["replicas"])
        value = {"epoch": self.epoch, "planned": len(args["moves"]),
                 "copy_units": sum(self.shards[move["shard"]]["size"] for move in args["moves"]),
                 "all_steps_independently_verified": True}
        self.reviews.append(value)
        return "ok", {"ok": True, **value}

    def _migrate(self, args):
        if args["epoch"] != self.epoch:
            self.refusals.append({"plan_epoch": args["epoch"], "live_epoch": self.epoch,
                                  "attempted_moves": len(args["moves"]), "applied": 0})
            return "ok", {"ok": False, "err": "STALE_TOPOLOGY", "epoch": self.epoch}
        assert 0 < len(args["moves"]) <= 24
        assert sum(self.shards[move["shard"]]["size"] for move in args["moves"]) <= 216
        nodes, shards = copy.deepcopy(self.nodes), copy.deepcopy(self.shards)
        for move in args["moves"]:
            self.move(nodes, shards, move)
        for move in args["moves"]:
            self.applied.append({"epoch": self.epoch,
                                 **self.move(self.nodes, self.shards, move)})
        answer = {"ok": True, "applied": len(args["moves"]), "epoch": self.epoch}
        if self.disturbance is None:
            self._disturb()
        return "ok", answer

    def _disturb(self):
        self.nodes[12]["alive"] = False
        previous_capacity = self.nodes[5]["capacity"]
        self.nodes[5]["capacity"] = self.nodes[5]["used"] + 8
        self.epoch += 1
        self.disturbance = {"after_moves": len(self.applied), "failed_node": 12,
                            "capacity_node": 5, "capacity_before": previous_capacity,
                            "capacity_after": self.nodes[5]["capacity"], "epoch": self.epoch}
        self.validate(self.nodes, self.shards)
