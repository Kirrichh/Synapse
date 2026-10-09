"""A dependent step never runs as if its value had arrived (refinement §13, D2).

The learned drain procedure meets producers that do not give it the job it
needs. Each is a different, recorded reason, and in every case the dependent
status and cancel calls are never made:

* the job service refuses the creation (a quota): the body stops at the
  refusal, which the basis never saw;
* the creation answers without an identifier: the field is missing;
* the identifier is a number, where the basis always received a string;
* the identifier is empty.

The runtime's reason and the court's re-derivation from the journal agree:
the fire is a failure, never the success of the last answer it received.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs

CASES = (("svc-quota", {"reason": "diverged_from_basis", "step": 0, "observed": "QUOTA", "expected": "ok",
                        "cause": "producer_failed", "dependents": [1, 2]}),
         ("svc-missing", {"reason": "dependency_unavailable", "step": 1, "cause": "field_missing"}),
         ("svc-typed", {"reason": "dependency_unavailable", "step": 1, "cause": "type_mismatch"}),
         ("svc-empty", {"reason": "dependency_unavailable", "step": 1, "cause": "field_empty"}))


def test_an_unavailable_result_stops_the_body_before_its_consumer(tmp_path):
    world = jobs.world(tmp_path)
    birth = jobs.learn(world)
    for service, expected in CASES:
        run_id = f"refused-{service}"
        statuses, cancels = len(world.calls("jobs_status")), len(world.calls("jobs_cancel"))
        world.run(jobs.program(), run_id, jobs.inputs(run_id, service, careful=True))
        activated, = world.events(run_id, "habit_activated")
        assert activated["habit_id"] == birth["habit_id"] and activated["outcome"] == "failure", service
        detail = {key: activated["detail"][key] for key in expected}
        assert detail == expected, service
        # The consumers were never called; the deployment was never repeated.
        assert len(world.calls("jobs_status")) == statuses and len(world.calls("jobs_cancel")) == cancels
        assert world.calls("jobs_create")[-1] == {"service": service, "kind": "drain"}
        assert not [item for item in world.world()["effects"] if item["args"] == {"service": service}]
        # The court judged the fire from the journal by the same executor.
        recent, = [item for item in world.owner().state()["habits"][birth["habit_id"]]["recent"]
                   if item["run_id"] == run_id]
        assert recent["outcome"] == "failure"

