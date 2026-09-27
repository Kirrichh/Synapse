"""A new configuration is adopted only by a reassessment of the recorded memory (review, package 5).

A learned search recovery stands on three recorded recoveries whose repeat of
the refused search was admitted because the flight contract documented
``BUSY`` as a refusal without effect. The operator corrects the contract: the
service never documented that. A session under the corrected configuration is
refused before anything runs — the owner is bound to the old one. The
operator reassesses the memory under the corrected configuration: the court
re-reads the recorded answers under the new contract, calls no tool and no
model, finds that none of the three repeats would be admitted now, archives the
habit (TR, a changed basis) and publishes why, episode by episode; only then
does the owner adopt the new configuration, and the next session does not load
the habit. Reassessing again changes nothing.

A correction that does not touch what a habit stands on keeps it: the same
reassessment finds every basis episode still anchored, Gold admits the habit
again under the new tool binding and it acts as before under the adopted
configuration.

A court of a later policy version does not read or extend memory decided under
an earlier one until it has reassessed it (simulated here by the next policy
version reading this memory in process).
"""
from __future__ import annotations

import json
from dataclasses import fields

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def _learned(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]
    return world, birth


def _sha(path):
    from synapse.memory_consolidation.configuration import read_memory_configuration

    return read_memory_configuration(path).configuration_sha256


def test_a_corrected_contract_archives_a_habit_its_basis_no_longer_supports(tmp_path):
    world, birth = _learned(tmp_path)
    before = _sha(world.configuration_path)
    corrected = world.reconfigured("corrected", {"flights": {}})  # BUSY is no longer documented as effect-free.

    # Not adopted yet: a session under the corrected configuration is refused before anything runs.
    calls = len(world.world()["calls"])
    code, payload, stderr = world.attempt(travel.PROGRAM, "early", travel.inputs("early", "TBS"),
                                          configuration=corrected)
    assert code != 0 and len(world.world()["calls"]) == calls, (code, payload, stderr)

    reports = len(world.reports())
    code, result, stderr = world.memory("reassess", configuration=corrected)
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    reassessment = result["consolidation"]["reassessment"]
    assert reassessment["configuration"] == {"from": before, "to": _sha(corrected)}
    assert reassessment["policy"]["to"] == "synapse.memory.court-policy/v3"
    habit, = reassessment["habits"]
    assert habit["habit_id"] == birth["habit_id"] and habit["verdict"] == "basis_no_longer_verified"
    assert (habit["verified"], habit["required"]) == (0, 3)
    assert [item["now"] for item in habit["episodes"]] == ["not_verified"] * 3
    assert all(item["reason"].startswith("repeat_no_longer_admissible") and item["recorded"]["verdict"] == "confirmed"
               for item in habit["episodes"])
    # Nothing outside was called; the decision is recorded as one reassessment report.
    assert len(world.world()["calls"]) == calls and len(world.reports()) == reports + 1
    report = world.reports()[-1]
    assert report["mode"] == "reassess" and report["window"]["sessions"] == []
    assert [(item["habit_id"], item["rule"], item["to"], item["cause"]) for item in report["transitions"]] == [
        (birth["habit_id"], "TR", "dormant", "changed_basis")]

    # Adopted: a session under the corrected configuration runs and does not load the archived habit.
    world.attempt(travel.PROGRAM, "after", travel.inputs("after", "TBS"), configuration=corrected)
    assert world.opening("after")["learned"] == [] and world.events("after", "habit_activated") == []
    # The old configuration is no longer the owner's.
    code, _, _ = world.attempt(travel.PROGRAM, "old", travel.inputs("old", "RIX"),
                               configuration=world.configuration_path)
    assert code != 0 and world.runs.joinpath("old.json").exists() is False

    # Reassessing again under the adopted configuration changes nothing.
    code, again, _ = world.memory("reassess", configuration=corrected)
    assert code == 0 and again["consolidation"]["consolidation_id"] == result["consolidation"]["consolidation_id"]
    assert len(world.reports()) == reports + 2  # The session "after" added its own consolidation.

    # The operator may return to the first configuration, again only through a reassessment.
    code, back, stderr = world.memory("reassess", configuration=world.configuration_path)
    assert code == 0 and back["consolidation"]["reassessment"]["configuration"] == {
        "from": _sha(corrected), "to": before}, (code, back, stderr)
    code, payload, stderr = world.attempt(travel.PROGRAM, "back", travel.inputs("back", "EVN"),
                                          configuration=world.configuration_path)
    assert code == 0, (code, payload, stderr)
    assert world.opening("back")["learned"] == []  # Archived by the first reassessment, not revived by a return.


def test_a_correction_that_leaves_the_basis_intact_keeps_the_habit(tmp_path):
    world, birth = _learned(tmp_path)
    configuration = json.loads(world.configuration_path.read_text())
    quota = next(item for item in configuration["tools"]["tools"] if item["name"] == "quota_status")
    corrected = world.reconfigured("documented", {"quota_status": {**quota["contract"], "doc": "a read of the quota"}})

    code, result, stderr = world.memory("reassess", configuration=corrected)
    assert code == 0, (code, result, stderr)
    habit, = result["consolidation"]["reassessment"]["habits"]
    assert habit["verdict"] == "basis_holds" and habit["verified"] == 3
    assert {item["reason"] for item in habit["episodes"]} == {"anchored"}
    assert world.reports()[-1]["transitions"] == []

    code, payload, stderr = world.attempt(travel.PROGRAM, "after", travel.inputs("after", "TBS"),
                                          configuration=corrected)
    assert code == 0, (code, payload, stderr)
    fired, = world.events("after", "habit_activated")
    assert fired["habit_id"] == birth["habit_id"] and fired["outcome"] == "success"


def _next_policy(configuration):
    """The same configuration as read by the court of the next policy version."""
    from synapse.memory_consolidation.configuration import MemoryConfiguration

    class NextPolicy(MemoryConfiguration):
        @property
        def policy(self):
            return {**super().policy, "policy": "synapse.memory.court-policy/next"}

    return NextPolicy(**{item.name: getattr(configuration, item.name) for item in fields(configuration)})


def test_memory_decided_under_another_court_policy_is_reassessed_before_use(tmp_path):
    from synapse.memory_consolidation.configuration import read_memory_configuration
    from synapse.memory_consolidation.factory import MemoryFactory
    from synapse.memory_consolidation.owner import MemoryOwnerViolation

    world, birth = _learned(tmp_path)
    current = read_memory_configuration(world.configuration_path)
    later = _next_policy(current)
    factory = MemoryFactory(world.state, later)
    reports = len(world.reports())
    with pytest.raises(MemoryOwnerViolation, match="reassess"):
        factory.court("summary")
    assert len(world.reports()) == reports

    result = factory.reassess()
    assert result["reassessment"]["policy"] == {"from": current.policy["policy"],
                                                "to": "synapse.memory.court-policy/next"}
    habit, = result["reassessment"]["habits"]
    assert habit["habit_id"] == birth["habit_id"] and habit["verdict"] == "basis_holds"
    factory.owner.require_policy(later)  # Reassessed: the later court may now read and extend it.
