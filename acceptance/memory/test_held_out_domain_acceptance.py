"""A held-out domain the memory never learned (plan §17).

The memory learns one recovery in the flight-search domain: a busy search is
recovered by checking the quota and repeating it. One fixed snapshot is then
examined in arms A (no accumulated experience) and B (admitted habits on the
fast path), each from the restored initial state, on two families of tasks:

* in the learned domain, B recovers on the fast path and reaches the goal;
* in a held-out domain — a hotel search with the same kind of busy refusal,
  never seen in training — B claims no transfer: the learned habit does not
  apply, B takes the slow path exactly as A does, makes no erroneous action
  and reaches the goal the same way.

The environment's record is the checker's truth.
"""
from __future__ import annotations

from acceptance.memory import _paired as paired
from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer, tool

HOTELS = travel.PROGRAM.replace('"flights"', '"hotels"').replace('"booking_state"', '"hotel_state"').replace(
    "find flights", "find hotels")
CITIES = ("RIX", "VNO")


def _tools():
    busy = {"payload": {"ok": False, "err": "BUSY", "route_kind": "intl"}}
    hotels = tool("hotels", "hotels:acct1", [answer({"ok": True, "route": city, "rooms": 2}, when={"route": city},
                                                    sequence=[busy]) for city in CITIES],
                  server="hotels", contract={"effect_on_err": {"BUSY": "none"}}, event_fields=["route_kind"])
    state = tool("hotel_state", "hotel-gds:ops", [answer({"ok": True, "route": city, "found": True},
                                                         when={"route": city}) for city in CITIES], server="gds",
                 contract={"state_check_for": "hotels", "resolve_state": {"found": "applied"},
                           "binds": {"request": {"route": "route"}, "answer": {"route": "route"}},
                           "attests": "operation"})
    return [*travel.tools(), hotels, state]


def _task(world, mode, snapshot, program, run_id, route, check):
    world.run(program, run_id, travel.inputs(run_id, route), exam=(mode, snapshot))
    measured = paired.measures(world, run_id, route)
    measured["goal"] = {"route": route} in world.calls(check)
    return measured


def test_a_held_out_domain_gets_no_claimed_transfer(tmp_path):
    world = MemoryWorld(tmp_path, _tools(), provenance={**travel.independent_provenance(),
                                                        "hotels:acct1": {"ancestors": []},
                                                        "hotel-gds:ops": {"ancestors": []}})
    birth, snapshot = paired.learn(world)
    initial = world.world()
    found = {}
    for mode in "AB":
        world.restore(initial)
        found[mode] = {"learned": _task(world, mode, snapshot, travel.PROGRAM, f"{mode}-TBS", "TBS", "booking_state"),
                       **{city: _task(world, mode, snapshot, HOTELS, f"{mode}-{city}", city, "hotel_state")
                          for city in CITIES}}

    assert found["B"]["learned"]["habit_fires"] == 1 and found["A"]["learned"]["habit_fires"] == 0
    assert found["B"]["learned"]["goal"] and found["A"]["learned"]["goal"]
    for city in CITIES:
        a, b = found["A"][city], found["B"][city]
        assert b["loaded"] == [birth["habit_id"]] and b["habit_fires"] == 0  # Loaded, never applied.
        assert (a["slow_path"], b["slow_path"]) == (1, 1)
        assert a["goal"] and b["goal"] and a["erroneous_actions"] == b["erroneous_actions"] == 0
        assert a["tool_calls"] == b["tool_calls"]
