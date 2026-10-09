"""A learned procedure explains its generalization and the contracts it was verified under (review §8.1).

Three verified drains in three tasks teach the court the recovery of a refused
deployment (the drain scenario of the result-dependency files). The birth
explains, from the basis episodes alone, what it generalizes:

* why every argument is what it is — the job identifier is a variable because
  it first appeared in the creation's answer in every episode and its values
  differ; the service comes from the failed deployment; the job kind never
  changed, so it stays a constant, never shown to vary;
* which checks verified each basis episode (their recorded evidence);
* the tool contracts all of it was verified under, which the procedure keeps.

When the operator changes the contract of a tool the body calls, the memory is
not trusted on its word: a session under the new configuration is refused until
the reassessment, which names the changed contract as the reason the habit was
judged again, keeps the habit on its re-verified bases and records the new
contract version — and the next session loads it under that version.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs
from synapse.memory_consolidation.configuration import read_memory_configuration


def _contracts(path, tools):
    configuration = read_memory_configuration(path)
    return {name: configuration.tools.tools[name].contract_ref for name in tools}


def test_the_birth_explains_its_generalization_and_keeps_its_contract_versions(tmp_path):
    world = jobs.world(tmp_path)
    birth = jobs.learn(world)
    generalization = birth["generalization"]
    why = {(item["step"], item["argument"]): item for item in generalization["arguments"]}
    assert why[(1, "job_id")]["rule"] == why[(2, "job_id")]["rule"] == "result"
    assert why[(1, "job_id")]["distinct_values"] == why[(1, "job_id")]["episodes"] == 3
    assert why[(0, "service")]["rule"] == "failed_arg" and why[(0, "service")]["distinct_values"] == 3
    assert why[(0, "kind")]["rule"] == "const" and why[(0, "kind")]["distinct_values"] == 1
    assert {item["run_id"] for item in generalization["checks"]} == {run for run, _ in jobs.LEARNING}
    assert all(item["evidence"] for item in generalization["checks"])
    body = sorted(generalization["contracts"])
    assert body == ["jobs_cancel", "jobs_create", "jobs_status", "release_state"]  # The body confirms the release.
    assert generalization["contracts"] == _contracts(world.configuration_path, body)
    assert birth["verified_under"] == {"contracts": generalization["contracts"]}
    world.run(jobs.program(), "fast", jobs.inputs("fast", "svc-new"))
    assert world.opening("fast")["unverified"] == []
    assert [item["habit_id"] for item in world.opening("fast")["learned"]] == [birth["habit_id"]]

    # The operator documents the cancel more precisely: another contract version of a tool the body calls.
    cancel = next(item for item in jobs.tools() if item["name"] == "jobs_cancel")["contract"]
    changed = world.reconfigured("cancel-doc", {"jobs_cancel": {**cancel, "doc": "revokes exactly the named job"}})
    code, _, _ = world.attempt(jobs.program(), "early", jobs.inputs("early", "svc-early"), configuration=changed)
    assert code != 0  # Not before the memory was judged again under it.

    code, result, stderr = world.memory("reassess", configuration=changed)
    assert code == 0, (result, stderr)
    judged, = result["consolidation"]["reassessment"]["habits"]
    assert judged["habit_id"] == birth["habit_id"] and judged["contracts_changed"] == ["jobs_cancel"]
    assert judged["verdict"] == "basis_holds"
    now = _contracts(changed, body)
    assert world.owner().state()["habits"][birth["habit_id"]]["verified_under"] == {"contracts": now}
    assert now["jobs_cancel"] != generalization["contracts"]["jobs_cancel"]

    code, payload, stderr = world.attempt(jobs.program(), "after", jobs.inputs("after", "svc-after"),
                                          configuration=changed)
    assert code == 0, (payload, stderr)
    assert world.opening("after")["unverified"] == []
    assert [item["habit_id"] for item in world.opening("after")["learned"]] == [birth["habit_id"]]
