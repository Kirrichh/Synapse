"""Two learned competitors of one refused search, for the conflict ladder scenarios.

Two recoveries of the same refused search — through the quota, or through the
capacity and a reserved slot — are verified in different tasks. The first is
born while a session opened on the older snapshot is still in its slow path;
that session's recovery completes the second candidate, which is born as a
competitor of the first: one applicability, one expected outcome, another
action. Each ladder scenario starts from this pair.

The environment decides which recovery works. On an ordinary route the repeat
of a refused search is answered; on a full route (``FULL``) it is answered only
once a slot is held for the route (it refuses the repeat otherwise, saying so),
so only the capacity recovery succeeds there and the independent booking system
finds flights only then. Full routes
are international like every other route: the recorded situation is the same,
so their outcomes are comparable with the ordinary ones.
"""
from __future__ import annotations

import time

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer, stateful, tool

PROGRAM = travel.PROGRAM.replace('''    let quota = tool("quota_status", {"route": route})
''', '''    if recovery == "quota" {
      let quota = tool("quota_status", {"route": route})
    } else {
      let capacity = tool("capacity_status", {"route": route})
      let slot = tool("reserve_slot", {"route": route})
    }
''').replace('''    let repeated = tool("flights", {"route": route}, {"retry_of": refusal.op})
''', '''    try {
      let repeated = tool("flights", {"route": route}, {"retry_of": refusal.op})
    } catch (ACTION_FAILED as still) {
      print("still refused")
    }
''')
RECOVERY = {"quota_status": "quota", "capacity_status": "capacity"}
FULL = ("OSL", "ARN", "CPH")
BUSY = {"ok": False, "err": "BUSY", "route_kind": "intl"}
#: In a tiered world the service also reports a tier for these routes: the capacity recovery is learned on
#: them only, so its trigger requires the tier and overlaps the quota recovery's only partly.
GOLD = ("MSQ", "TBS", "EVN", "VNO")


def _flights(tiered):
    base = travel.flights()
    full = [{"when": {"route": route}, "sequence": [{"payload": BUSY}],
             "then": {"payload": {"ok": True, "route": route, "offers": 3}, "act": {"read": "slots", "key": "route"},
                      "otherwise": {"payload": {**BUSY, "reason": "no slot held"}}}} for route in FULL]
    if not tiered:
        return {**base, "answers": [*full, *base["answers"]]}
    gold = [answer({"ok": True, "route": route, "offers": 3}, when={"route": route},
                   sequence=[{"payload": {**BUSY, "tier": "gold"}}]) for route in GOLD]
    return {**base, "answers": [*gold, *full, *base["answers"]], "event_fields": ["route_kind", "tier"]}


def _booking_state():
    base = travel.booking_state()
    full = [stateful({"ok": True, "route": route}, act={"read": "slots", "key": "route", "flags": {"found": "held"}},
                     otherwise={"ok": True, "route": route, "found": False}, when={"route": route}) for route in FULL]
    return {**base, "answers": [*full, *base["answers"]]}


def _slot(route=None, delay=None):
    rule = stateful({"ok": True, "slot": "held"}, act={"put": "slots", "key": "route", "state": "held"},
                    effect="slot_held", when=None if route is None else {"route": route})
    if delay:
        rule["then"]["delay"] = delay
    return rule


def tools(*extra, delay=20, hold=0, tiered=False):
    """``delay`` and ``hold`` are the seconds the waiting session's capacity read and slot reservation take
    (each below the gateway's call timeout)."""
    slow = {"payload": {"ok": True, "capacity": "open"}, "delay": delay}
    capacity = tool("capacity_status", "capacity:ops", [
        answer({"ok": True, "capacity": "open"}, when={"route": "EVN"}, sequence=[slow]),
        answer({"ok": True, "capacity": "open"})], server="ops", contract={"idempotent": True})
    slot = tool("reserve_slot", "slots:ops", [_slot("EVN", hold), _slot()], server="ops")
    return [_flights(tiered), travel.quota(), _booking_state(), capacity, slot, *extra]


def provenance():
    return {**travel.independent_provenance(), "capacity:ops": {"ancestors": []}, "slots:ops": {"ancestors": []}}


def inputs(task, route, recovery):
    return {**travel.inputs(task, route), "recovery": recovery}


def world(root, *extra, delay=20, hold=0, tiered=False, **options) -> MemoryWorld:
    return MemoryWorld(root, tools(*extra, delay=delay, hold=hold, tiered=tiered), provenance=provenance(),
                       **options)


def learn_competitors(world: MemoryWorld, meanwhile=None) -> tuple[dict, dict]:
    """Births of the quota recovery and of its competitor, the capacity recovery.

    ``meanwhile`` runs after the first birth, while the session that completes the second candidate
    still waits in its slow path."""
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
    if meanwhile is not None:
        meanwhile()
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

