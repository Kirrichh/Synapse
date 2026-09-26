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
let req = {"kind": "execute", "tool": "book", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "book", "fields": {"ok": true}}
let seg = {"segment": "booking", "intent": "book the requested flight", "element_part": "booking", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "book the requested flight", "segments": [seg]})
context "booking" {
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
        {"when": {"flight": "F4"}, "sequence": [], "then": {"lost": True, "effect": "booked"}}],
        contract={"effect_on_err": {"SOLD_OUT": "none", "PAYMENT_PENDING": "partial"}})
    cancel = tool("cancel", "airline:acct1", [answer({"ok": True, "cancelled": True}, effect="cancelled")],
                  contract={"compensates": "book", "compensation_signs": {"cancelled": True}})
    state = tool("booking_state", "gds:ops", [answer({"ok": True, "booked": True})], server="gds",
                 contract={"state_check_for": "book", "resolve_state": {"booked": "applied"}})
    return [book, cancel, state]


def _verdict(world, run_id):
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == run_id]
    return verdict


def _effects(world, flight):
    return [item["effect"] for item in world.world()["effects"] if item["args"].get("flight") == flight]


def test_only_the_requested_effect_closes_the_goal(tmp_path):
    provenance = {"airline:acct1": {"ancestors": []}, "gds:ops": {"ancestors": []}}
    world = MemoryWorld(tmp_path, _tools(), provenance=provenance)

    def session(run_id, flight, remedy):
        world.run(PROGRAM, run_id, {"task_name": run_id, "flight": flight, "remedy": remedy})
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
