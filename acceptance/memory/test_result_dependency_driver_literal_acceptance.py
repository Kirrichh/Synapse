"""A literal the driver supplies in advance is not a learned dependency (refinement §13, D2).

A job service with predictable identifiers lets the driver know each job's
identifier before the job exists. The recovery reads the identifier from its
input instead of from the creation's answer. Every session drains correctly —
the literal happens to be right — and every argument equals the identifier
the creation answered. The court still does not credit a dependency: the
value was known before its producer answered, so it is an echo, and no rule
explains an argument that changes with every task. No habit is born, and the
report names why.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs


def test_a_value_known_before_its_producer_answered_earns_no_dependency(tmp_path):
    world = jobs.world(tmp_path, ids="sequential")
    for index, (run_id, service) in enumerate(jobs.LEARNING, 1):
        world.run(jobs.program(literal=True), run_id, jobs.inputs(run_id, service, planned_job=f"job-{index}"))

    # The environment: each session cancelled the job it created, as the literal predicted.
    assert jobs.jobs(world) == {
        f"job-{index}": {"job_id": f"job-{index}", "service": service, "kind": "drain", "state": "revoked",
                         "consumed": True} for index, (_, service) in enumerate(jobs.LEARNING, 1)}
    assert [item["job_id"] for item in world.calls("jobs_cancel")] == ["job-1", "job-2", "job-3"]

    # The court: three verified, independent recoveries in three tasks — and no birth.
    reports = world.reports()
    assert [len(report["births"]) for report in reports] == [0, 0, 0]
    update, = reports[-1]["pool_updates"]
    assert update["criteria"]["episodes"] == 3 and update["criteria"]["tasks"] == 3
    assert update["criteria"]["independence"] == "independent" and update["criteria"]["dependencies"] == 0
    assert update["reasons"] == ["binding_not_derivable"]
    assert "known before its producer answered" in update["unavailable"]["binding"]
