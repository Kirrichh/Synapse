"""Two learned competitors of one refused search, for the conflict ladder scenarios.

Two recoveries of the same refused search — through the quota, or through the
capacity and a reserved slot — are verified in different tasks. The first is
born while a session opened on the older snapshot is still in its slow path;
that session's recovery completes the second candidate, which is born as a
competitor of the first: one applicability, one expected outcome, another
action. Each ladder scenario starts from this pair.
"""
from __future__ import annotations

import time

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = travel.PROGRAM.replace('''    let quota = tool("quota_status", {"route": route})
''', '''    if recovery == "quota" {
      let quota = tool("quota_status", {"route": route})
    } else {
      let capacity = tool("capacity_status", {"route": route})
      let slot = tool("reserve_slot", {"route": route})
    }
''')
RECOVERY = {"quota_status": "quota", "capacity_status": "capacity"}


def tools(*extra):
    slow = {"payload": {"ok": True, "capacity": "open"}, "delay": 20}
    capacity = tool("capacity_status", "capacity:ops", [
        answer({"ok": True, "capacity": "open"}, when={"route": "EVN"}, sequence=[slow]),
        answer({"ok": True, "capacity": "open"})], server="ops", contract={"idempotent": True})
    slot = tool("reserve_slot", "slots:ops", [answer({"ok": True, "slot": "held"})], server="ops")
    return [*travel.tools(), capacity, slot, *extra]


def provenance():
    return {**travel.independent_provenance(), "capacity:ops": {"ancestors": []}, "slots:ops": {"ancestors": []}}


def inputs(task, route, recovery):
    return {**travel.inputs(task, route), "recovery": recovery}


def world(root, *extra, **options) -> MemoryWorld:
    return MemoryWorld(root, tools(*extra), provenance=provenance(), **options)


def learn_competitors(world: MemoryWorld) -> tuple[dict, dict]:
    """Births of the quota recovery and of its competitor, the capacity recovery."""
    for run_id, route, recovery in (("quota-1", "BUS", "quota"), ("quota-2", "YVR", "quota"),
                                    ("capacity-1", "MSQ", "capacity"), ("capacity-2", "TBS", "capacity")):
        world.run(PROGRAM, run_id, inputs(run_id, route, recovery))
    # A session opened on the older snapshot waits in its slow path while the first recovery is born.
    waiting = world.start(PROGRAM, "capacity-3", inputs("capacity-3", "EVN", "capacity"))
    deadline = time.monotonic() + 120
    while {"route": "EVN"} not in world.calls("capacity_status"):
        assert time.monotonic() < deadline and waiting.poll() is None
        time.sleep(0.2)
    world.run(PROGRAM, "quota-3", inputs("quota-3", "RIX", "quota"))
    first, = world.reports()[-1]["births"]
    stdout, stderr = waiting.communicate(timeout=600)
    assert waiting.returncode == 0, (stdout, stderr)
    assert world.opening("capacity-3")["learned"] == []
    second, = world.reports()[-1]["births"]
    assert second["typed_check"] != "successor" and second["habit_id"] != first["habit_id"]
    return first, second


def recovery_of(world: MemoryWorld, habit_id: str) -> str:
    """Which recovery a learned habit executes, read from its frozen action pattern."""
    frozen = next(report["apply"]["frozen"][habit_id] for report in world.reports()
                  if habit_id in report["apply"]["frozen"])
    called = [step.get("tool") for step in frozen["habit"]["action_pattern"] if step["step"] == "call"]
    return next(RECOVERY[name] for name in called if name in RECOVERY)
