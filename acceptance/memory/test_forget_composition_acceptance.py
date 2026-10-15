"""A forgotten composed recovery leaves its composition (finding R6 on 556624d, for compositions).

Memory holds the drain of a refused deployment and the recovery of a refused cancel (``_jobs``); on a busy service
the slow planner composes them. Two composed recoveries make a composition candidate and the operator forgets the
first: its recorded results leave store D. A third composed recovery leaves two examples — nothing is born, and no
birth is attempted on the forgotten one, whose results Gold would find gone — and a fourth completes three: the
composition is born from the second, third and fourth, and the first case stays forgotten.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs


def _composed(world, index, service):
    run_id = f"composed-{index}"
    world.run(jobs.COMPOSE, run_id, jobs.compose_inputs(run_id, service))
    return world.reports()[-1]


def test_a_forgotten_composed_recovery_leaves_its_composition(tmp_path):
    world = jobs.world(tmp_path)
    jobs.learn(world)
    jobs.learn_cancel(world, "pause")
    for index, service in enumerate(("svc-busy-1", "svc-busy-2")):
        _composed(world, index, service)
    candidate, = [entry for entry in world.owner().state()["pool"].values() if entry.get("kind") == "composition"]
    first, = [item for item in candidate["episodes"] if item["run_id"] == "composed-0"]
    code, payload, stderr = world.memory("forget", "--quantum", first["qid"], "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    assert [act["qid"] for act in payload["acts"]] == [first["qid"]]
    assert all(world.evidence().get(ref) is None for ref in first["evidence"])

    report = _composed(world, 2, "svc-busy-3")
    assert report["births"] == [] and report["refused_births"] == []
    update, = [item for item in report["pool_updates"] if item["candidate_key"] == candidate["candidate_key"]]
    assert update["episodes_total"] == 2 and "repeatability_below_threshold" in update["reasons"]

    composite, = _composed(world, 3, "svc-busy-5")["births"]
    assert sorted(item["run_id"] for item in composite["episodes"]) == ["composed-1", "composed-2", "composed-3"]
    assert composite["criteria"]["episodes"] == 3
    assert world.owner().state()["quanta"][first["qid"]]["retention_state"] == "forgotten"
