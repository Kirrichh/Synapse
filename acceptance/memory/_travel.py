"""The travel-search scenario: a search that is refused while busy and recovered on the slow path.

The flight service refuses the first search of every route with ``BUSY`` (no
effect by its contract) and answers the repeat. The recovery the agent writes
on the slow path checks the quota, repeats the refused search and — when the
scenario asks for it — confirms the search through an independent booking
system that observes the same flights. Routes carry their kind as an event
field, so a learned trigger has a typed scope to stay inside.
"""
from __future__ import annotations

from acceptance.memory._world import answer, tool

ROUTES = {"BUS": "intl", "YVR": "intl", "MSQ": "intl", "TBS": "intl", "EVN": "intl", "RIX": "intl",
          "VNO": "intl", "TLL": "intl", "HEL": "intl", "WAW": "intl", "LED": "dom", "KZN": "dom"}

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "flights", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "flights", "fields": {"ok": true}}
let seg = {"segment": "search", "intent": "find flights", "element_part": "flights_search", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "find flights", "segments": [seg]})
context "search" {
  try {
    let found = tool("flights", {"route": route})
  } catch (ACTION_FAILED as refusal) {
    let quota = tool("quota_status", {"route": route})
    let repeated = tool("flights", {"route": route}, {"retry_of": refusal.op})
    if verify_state == true {
      let observed = tool("booking_state", {"route": route})
    }
  }
}
print("searched")
'''


def flights(source="flights:acct1", *, busy_every_time=()):
    """Refuses the first search of a route (or every other search of the routes in ``busy_every_time``)."""
    rules = []
    for route, kind in ROUTES.items():
        busy = {"payload": {"ok": False, "err": "BUSY", "route_kind": kind}}
        found = {"payload": {"ok": True, "route": route, "offers": 3}}
        sequence = [busy, found] * 8 if route in busy_every_time else [busy]
        rules.append(answer(found["payload"], when={"route": route}, sequence=sequence))
    return tool("flights", source, rules, contract={"effect_on_err": {"BUSY": "none"}}, event_fields=["route_kind"])


def quota(source="quota:ops", *, slow_routes=()):
    """A read of the quota: idempotent by contract; its first answer for ``slow_routes`` is slow."""
    slow = {"payload": {"ok": True, "quota": "fine"}, "delay": 120}
    rules = [answer({"ok": True, "quota": "fine"}, when={"route": route}, sequence=[slow]) for route in slow_routes]
    return tool("quota_status", source, [*rules, answer({"ok": True, "quota": "fine"})], server="quota",
                contract={"idempotent": True})


def booking_state(source="gds:ops"):
    """An independent observation of the searched flights: it attests the same claim."""
    rules = [answer({"ok": True, "route": route, "found": True}, when={"route": route}) for route in ROUTES]
    return tool("booking_state", source, rules, server="gds",
                contract={"state_check_for": "flights", "resolve_state": {"found": "applied"}})


def tools(*, busy_every_time=(), slow_quota=(), **sources):
    return [flights(sources.get("flights", "flights:acct1"), busy_every_time=busy_every_time),
            quota(sources.get("quota", "quota:ops"), slow_routes=slow_quota),
            booking_state(sources.get("booking", "gds:ops"))]


def independent_provenance():
    return {"flights:acct1": {"ancestors": []}, "quota:ops": {"ancestors": []}, "gds:ops": {"ancestors": []}}


def inputs(task, route, *, verify_state=True):
    return {"task_name": task, "route": route, "verify_state": verify_state}
