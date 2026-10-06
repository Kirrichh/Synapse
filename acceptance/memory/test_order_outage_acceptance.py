"""Training order and an environment outage, varied separately (plan §17).

The same four verified recoveries of a refused search teach one procedure, in
two orders, each with and without an outage — a 2 × 2 design, so the effect of
the outage is never attributed to the order or the other way round. During the
outage the quota service documents its own unavailability (its contract
declares ``UNAVAILABLE`` environmental), so that recovery fails through no
fault of the procedure.

Measured per cell, from the court's reports: the session at which the
procedure is born and the procedure itself (its action pattern, binding and
learned applicability). Then:

* the order changes nothing: in each outage condition both orders give the
  same procedure born at the same session;
* the outage episode is set aside as environmental, never counted against the
  procedure: in both orders it delays the birth by exactly one session and
  changes nothing else.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = travel.PROGRAM.replace('''  } catch (ACTION_FAILED as refusal) {
    let quota = tool("quota_status", {"route": route})
    let repeated = tool("flights", {"route": route}, {"retry_of": refusal.op})
    if verify_state == true {
      let observed = tool("booking_state", {"route": route})
    }
  }''', '''  } catch (ACTION_FAILED as refusal) {
    try {
      let quota = tool("quota_status", {"route": route})
      let repeated = tool("flights", {"route": route}, {"retry_of": refusal.op})
      if verify_state == true {
        let observed = tool("booking_state", {"route": route})
      }
    } catch (ACTION_FAILED as down) {
      print("the environment is down")
    }
  }''')
ROUTES = ("BUS", "YVR", "MSQ", "TBS")
OUTAGE = "YVR"


def _world(root, outage: bool) -> MemoryWorld:
    down = [answer({"ok": False, "err": "UNAVAILABLE"}, when={"route": OUTAGE})] if outage else []
    quota = tool("quota_status", "quota:ops", [*down, answer({"ok": True, "quota": "fine"})], server="quota",
                 contract={"idempotent": True, "environmental_errors": ["UNAVAILABLE"]})
    return MemoryWorld(root, [travel.flights(), quota, travel.booking_state()],
                       provenance=travel.independent_provenance(), parameters={"environmental_run": 1})


def _cell(root, order, outage: bool) -> dict:
    world = _world(root, outage)
    born_at, birth = None, None
    for index, route in enumerate(order, 1):
        world.run(PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
        births = world.reports()[-1]["births"]
        if births and birth is None:
            born_at, (birth,) = index, births
    assert birth is not None, (order, outage)
    frozen = next(report["apply"]["frozen"][birth["habit_id"]] for report in world.reports()
                  if birth["habit_id"] in report["apply"]["frozen"])
    procedure = {"steps": frozen["habit"]["action_pattern"], "binding": birth["binding"],
                 "condition": birth["condition"],
        "applicability": {key: birth["applicability"][key] for key in ("essential", "irrelevant", "required")}}
    environmental = [verdict for report in world.reports() for verdict in report["marker_verdicts"]
                     if "environmental_failure" in verdict["flags"]]
    return {"born_at": born_at, "procedure": procedure, "environmental": len(environmental)}


def test_order_and_outage_are_varied_separately(tmp_path):
    orders = {"forward": ROUTES, "reverse": tuple(reversed(ROUTES))}
    cells = {(name, outage): _cell(tmp_path / f"{name}-{outage}", order, outage)
             for name, order in orders.items() for outage in (False, True)}

    for outage in (False, True):  # The order alone changes nothing.
        assert cells[("forward", outage)]["born_at"] == cells[("reverse", outage)]["born_at"]
        assert cells[("forward", outage)]["procedure"] == cells[("reverse", outage)]["procedure"]
    for name in orders:  # The outage is set aside: one session later, the same procedure.
        calm, outage = cells[(name, False)], cells[(name, True)]
        assert (calm["born_at"], outage["born_at"]) == (3, 4)
        assert calm["procedure"] == outage["procedure"]
        assert (calm["environmental"], outage["environmental"]) == (0, 1)
