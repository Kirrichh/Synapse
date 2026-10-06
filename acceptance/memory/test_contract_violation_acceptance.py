"""A habit body that attempts what the tool contracts forbid is caught at once (review R6).

The answer to a booking is lost after the airline booked the seat. A declared
habit reacts on the fast path; the gateway decides every call of its body
before any effect, as it does for the slow path:

* repeating the lost booking without an idempotency key is a hidden repeat of
  an operation whose effect is unknown: refused, and reported as a contract
  violation of the habit with a recommendation to its governing author;
* holding the seat with an idempotency key the body made up is refused (the
  key is the gateway's to issue): a violation as well;
* acting on a hypothesis not yet established is a precondition the world has
  not met — refused, but no violation: nothing about the body's contract is
  wrong.

The airline booked each requested flight once; no refused call reached it.
Learned habits lose their fast path on such a fire (rule TV): the decision
contract states it in ``test_automaton_experience_contract.py``.
"""
from __future__ import annotations

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
    if remedy == "repeat" {
      let again = tool("book", {"flight": flight})
    }
    if remedy == "own_key" {
      let held = tool("hold", {"flight": flight, "request_key": "made-up"})
    }
    if remedy == "unestablished" {
      let sure = tool("confirm_seat", {"flight": flight})
    }
  }
}
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
    hold = tool("hold", "airline:acct1", [answer({"ok": True, "held": True}, effect="held")],
                contract={"idempotency_key": {"field": "request_key", "retention_s": 3600}})
    confirm = tool("confirm_seat", "airline:acct1", [answer({"ok": True}, effect="confirmed")],
                   contract={"requires_established": True})
    return [book, hold, confirm]


def _violations(world, run_id):
    return [item for report in world.reports() for item in report["contract_violations"]
            if item["run_id"] == run_id]


def test_a_body_that_breaks_its_contracts_is_reported_at_once(tmp_path):
    world = MemoryWorld(tmp_path, _tools(), provenance={"airline:acct1": {"ancestors": []}})

    world.run(PROGRAM, "repeat", {"flight": "F3", "remedy": "repeat"})
    violation, = _violations(world, "repeat")
    fired, = world.events("repeat", "habit_activated")
    habit_id = fired["habit_id"]
    assert violation["layer"] == 1 and violation["habit_id"] == habit_id
    assert violation["refusals"][0].startswith("a hidden repeat of an unresolved operation")

    world.run(PROGRAM, "own_key", {"flight": "F4", "remedy": "own_key"})
    violation, = _violations(world, "own_key")
    assert violation["refusals"] == ["the idempotency key is the gateway's to issue, never an argument"]

    world.run(PROGRAM, "unestablished", {"flight": "F6", "remedy": "unestablished"})
    assert _violations(world, "unestablished") == []

    # Each violation asks the habit's governing author to review it; the precondition asks nothing.
    reviews = [item for report in world.reports() for item in report["recommendations"]
               if item["recommendation"] == "review_declared_habit"]
    assert [(item["habit_id"], item["authority"]) for item in reviews] == [(habit_id, "GOVERNING_HUMAN")] * 2

    # No refused call reached the airline: each flight booked once, nothing held or confirmed.
    assert [(item["args"]["flight"], item["effect"]) for item in world.world()["effects"]] == [
        ("F3", "booked"), ("F4", "booked"), ("F6", "booked")]
