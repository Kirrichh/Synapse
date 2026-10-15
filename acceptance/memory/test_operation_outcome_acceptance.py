"""Operation results and unknown effects: what closes a task's goal (refinement §8, §18).

One booking segment requires the requested flight to be booked. Every session
completes as a program; only the recorded answers, read under the operators'
tool contracts, decide the segment. The tool server's own world shows what
really happened to the bookings.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": goal_kind, "tool": "book", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "book", "fields": {"ok": true}}
let seg = {"segment": "booking", "intent": "book the requested flight", "element_part": "booking", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "book the requested flight", "segments": [seg]})
context "booking" {
  if precheck == true {
    let before = tool("booking_state", {"flight": flight})
  }
  try {
    let booked = tool("book", {"flight": flight})
  } catch (ACTION_FAILED as refusal) {
    if remedy == "cancel" {
      let cancelled = tool("cancel", {"flight": flight})
    }
    if remedy == "other" {
      let other = tool("book", {"flight": "F9"})
    }
    if remedy == "repeat" {
      try {
        let again = tool("book", {"flight": flight}, {"retry_of": refusal.op})
      } catch (ACTION_FAILED as refused) {
        print("repeat refused")
      }
    }
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
print("done")
'''


def _tools():
    book = tool("book", "airline:acct1", [
        answer({"ok": False, "err": "SOLD_OUT"}, when={"flight": "F1"}),
        answer({"ok": False, "err": "PAYMENT_PENDING"}, when={"flight": "F2"}, effect="held"),
        answer({"ok": False, "err": "SOLD_OUT"}, when={"flight": "F5"}),
        answer({"ok": True, "flight": "F9"}, when={"flight": "F9"}, effect="booked"),
        {"when": {"flight": "F3"}, "sequence": [], "then": {"lost": True, "effect": "booked"}},
        *[{"when": {"flight": name}, "sequence": [], "then": {"lost": True, "effect": "booked"}}
          for name in ("F4", "F6", "F7", "F8")]],
        contract={"effect_on_err": {"SOLD_OUT": "none", "PAYMENT_PENDING": "partial"}})
    cancel = tool("cancel", "airline:acct1", [answer({"ok": True, "cancelled": True}, effect="cancelled")],
                  contract={"compensates": "book", "compensation_signs": {"cancelled": True}})
    # The operator's contract: the booking system's record of a flight is this desk's own booking of it.
    state = tool("booking_state", "gds:ops", [answer({"ok": True, "booked": True})], server="gds",
                 contract={"state_check_for": "book", "resolve_state": {"booked": "applied"},
                           "binds": {"request": {"flight": "flight"}}, "attests": "operation"})
    # A seat map shows that the seat is taken, by anyone: the state, not this desk's operation.
    seats = tool("seat_state", "seats:ops", [answer({"ok": True, "taken": True})], server="gds",
                 contract={"state_check_for": "book", "resolve_state": {"taken": "applied"},
                           "binds": {"request": {"flight": "flight"}}})
    return [book, cancel, state, seats]


def _verdict(world, run_id):
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == run_id]
    return verdict


def _effects(world, flight):
    return [item["effect"] for item in world.world()["effects"] if item["args"].get("flight") == flight]


def test_only_the_requested_effect_closes_the_goal(tmp_path):
    provenance = {"airline:acct1": {"ancestors": []}, "gds:ops": {"ancestors": []}, "seats:ops": {"ancestors": []}}
    world = MemoryWorld(tmp_path, _tools(), provenance=provenance)

    def session(run_id, flight, remedy, *, goal="execute", precheck=False):
        world.run(PROGRAM, run_id, {"task_name": run_id, "flight": flight, "remedy": remedy, "goal_kind": goal,
                                    "precheck": precheck})
        return _verdict(world, run_id)

    # Transport success with an operation error: the answer arrived, the goal did not.
    verdict = session("refused", "F1", "none")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert _effects(world, "F1") == []

    # A confirmed compensation undoes a partial effect; it does not book the flight.
    verdict = session("compensated", "F2", "cancel")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert _effects(world, "F2") == ["held", "cancelled"]

    # Another resource succeeded: a booking of another flight is not the requested one.
    verdict = session("foreign", "F5", "other")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert _effects(world, "F5") == [] and _effects(world, "F9") == ["booked"]

    # A lost answer is no success, and the non-idempotent booking is never repeated blindly.
    verdict = session("lost", "F3", "repeat")
    assert verdict["verdict"] == "uncertain" and verdict["criterion"] == "uncertainty_retained"
    assert world.calls("book").count({"flight": "F3"}) == 1 and _effects(world, "F3") == ["booked"]
    refused, = [event for event in world.events("lost", "external_action")
                if event["request"]["retry_of"] is not None]
    assert refused["outcome"]["view"]["ok"] is False and world.events("lost", "external_error")

    # The same lost answer resolved by an independent state check: the effect is established.
    verdict = session("observed", "F4", "observe")
    assert verdict["verdict"] == "confirmed" and verdict["stage"] == "1" and verdict["evidence"]
    assert world.calls("book").count({"flight": "F4"}) == 1 and _effects(world, "F4") == ["booked"]

    # A check of another flight says nothing about this booking: the lost answer stays uncertain (review R1).
    verdict = session("observed-other", "F6", "observe_other")
    assert verdict["verdict"] == "uncertain" and verdict["criterion"] == "uncertainty_retained"
    failure, = world.failures("observed-other")
    assert failure["settled"] is False and failure["attestations"] == []
    assert [item["reason"] for item in failure["unbound_checks"]] == ["check_about_another_resource"]

    # The same record was already there before the booking: the state existed, so it is no proof of this
    # operation's effect.
    verdict = session("observed-stale", "F7", "observe", precheck=True)
    assert verdict["verdict"] == "uncertain"
    failure, = world.failures("observed-stale")
    assert [item["reason"] for item in failure["unbound_checks"]] == ["state_observed_before_the_operation"]
    assert failure["settled"] is False and failure["attestations"] == []

    # A seat map shows the seat taken — the state, by anyone: it closes a state goal and never this operation.
    verdict = session("seat-taken", "F8", "observe_state")
    assert verdict["verdict"] == "uncertain"
    failure, = world.failures("seat-taken")
    assert failure["settled"] is False and failure["attestations"] == [] and failure["effect"] == "unknown"
    assert failure["resolution"]["attests"] == "state"
    verdict = session("seat-goal", "F8", "observe_state", goal="reach_state")
    assert verdict["verdict"] == "confirmed"
    failure, = world.failures("seat-goal")
    assert failure["settled"] is False and failure["attestations"] == []  # No credit for the operation.

