"""A state check on the fast path credits only the operation it is bound to (review R1).

The answer to a booking is lost after the airline booked the seat. A declared
habit reacts on the fast path by reading a state, through the same gateway and
the same contracts as the slow path:

* the booking system's record of this very flight attests this operation: the
  lost booking is settled as applied and the segment is confirmed;
* the record of another flight (a foreign object) says nothing about this
  operation: the effect stays unknown, the segment is not confirmed and the
  check is reported as bound to another resource;
* a seat map shows only that the seat is taken, by anyone: the state, not this
  operation — the effect stays unknown and nothing is credited.

The airline's own record shows the one booking each session really made.
"""
from __future__ import annotations

import json

from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
habit "resolve_lost_booking" from pattern {
  activate when { event "external_error" context "booking" tool == "book" transport == "lost" }
  priority high
  body {
    if remedy == "observe" {
      let seen = tool("booking_state", {"flight": flight})
    }
    if remedy == "observe_other" {
      let seen = tool("booking_state", {"flight": "F9"})
    }
    if remedy == "observe_state" {
      let seen = tool("seat_state", {"flight": flight})
    }
  }
}
let req = {"kind": "execute", "tool": "book", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "book", "fields": {"ok": true}}
let seg = {"segment": "booking", "intent": "book the requested flight", "element_part": "booking", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "book the requested flight", "segments": [seg]})
context "booking" {
  try {
    let booked = tool("book", {"flight": flight})
  } catch (ACTION_FAILED as lost) {
    print("answer lost")
  }
}
print("done")
'''


def _tools():
    book = tool("book", "airline:acct1", [{"when": {"flight": name}, "sequence": [],
                                           "then": {"lost": True, "effect": "booked"}}
                                          for name in ("F3", "F4", "F6")],
                contract={"effect_on_err": {"SOLD_OUT": "none"}})
    state = tool("booking_state", "gds:ops", [
        answer({"ok": True, "flight": name, "booked": True}, when={"flight": name}) for name in ("F3", "F9")],
        server="gds", contract={"state_check_for": "book", "resolve_state": {"booked": "applied"},
                                "binds": {"request": {"flight": "flight"}, "answer": {"flight": "flight"}},
                                "attests": "operation"})
    seats = tool("seat_state", "seats:ops", [answer({"ok": True, "taken": True})], server="gds",
                 contract={"state_check_for": "book", "resolve_state": {"taken": "applied"},
                           "binds": {"request": {"flight": "flight"}}})
    return [book, state, seats]


def _verdict(world, run_id):
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == run_id]
    return verdict


def _session(world, run_id, flight, remedy):
    world.run(PROGRAM, run_id, {"task_name": run_id, "flight": flight, "remedy": remedy})
    fired, = world.events(run_id, "habit_activated")
    assert fired["layer"] == 1
    # The check was made by the habit's body, on the fast path.
    journal = world.owner().gateway_root / "journal.jsonl"
    checks = [record["body"] for record in map(json.loads, journal.read_text().splitlines())
              if record["kind"] == "STARTED" and record["body"].get("run_id") == run_id
              and record["body"]["tool"] != "book"]
    assert [item["path"] for item in checks] == ["habit"]
    failure, = world.failures(run_id)
    return failure, _verdict(world, run_id)


def test_a_fast_path_check_credits_only_its_own_operation(tmp_path):
    world = MemoryWorld(tmp_path, _tools(), provenance={
        "airline:acct1": {"ancestors": []}, "gds:ops": {"ancestors": []}, "seats:ops": {"ancestors": []}})

    failure, verdict = _session(world, "own", "F3", "observe")
    assert failure["resolution"]["attests"] == "operation" and failure["settled"] is True
    assert failure["effect"] == "applied" and [item["by"] for item in failure["attestations"]] == ["state_check"]
    assert verdict["verdict"] == "confirmed"

    failure, verdict = _session(world, "foreign", "F4", "observe_other")
    assert failure["settled"] is False and failure["effect"] == "unknown" and failure["attestations"] == []
    assert [item["reason"] for item in failure["unbound_checks"]] == ["check_about_another_resource"]
    assert verdict["verdict"] != "confirmed"

    failure, verdict = _session(world, "state", "F6", "observe_state")
    assert failure["resolution"]["attests"] == "state" and failure["settled"] is False
    assert failure["effect"] == "unknown" and failure["attestations"] == []
    assert verdict["verdict"] != "confirmed"

    # The airline booked each requested flight exactly once; the checks booked nothing.
    booked = [(item["args"]["flight"], item["effect"]) for item in world.world()["effects"]]
    assert booked == [("F3", "booked"), ("F4", "booked"), ("F6", "booked")]
