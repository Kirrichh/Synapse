"""A check made before another session corrected what it rests on never establishes the claim (recheck of 556624d).

A session reads the starter plan from the catalog, states a content hypothesis that the independent billing
service checks, checks it — billing agrees, 20 — and waits at a gate before booking the plan, an action that
requires the hypothesis. While it waits, another session reads billing anew and memory consolidates what
billing says now:

* billing changed, once or twice: the booking is refused before any effect, whether memory held no status for
  the hypothesis, a confirmation or a provisional status; consolidated afterwards, the check made before the
  change never establishes the claim — it stays provisional at the place of the last correction;
* billing did not change (control): the booking runs once;
* the session checks again after billing returned to 20, and billing agrees: a check made after the corrections
  stands and the booking runs once;
* the session is killed while it waits and recovered after the change: the booking is refused all the same;
* billing changes after the booking was sent (control: a session's history is durable once an action is
  recorded, so a check made before the booking is never published after such a change), while the session
  waits or after it was killed: the booking is never undone, the recovered session does not send it again — its
  replay consumes the recorded booking and admits nothing anew — and the report says the status was revoked
  after the action's admission;
* reassessing the memory afterwards leaves the status and the bookings as they are, and again changes nothing.

The environment's own record counts the bookings.
"""
from __future__ import annotations

import os
import signal
import time

import pytest

from acceptance.memory import _catalog as catalog
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''memory palace "catalog" { rooms { semantic } consolidate during dream }
let reading = {"segment": "read", "intent": "verify the price", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}}
let waiting = {"segment": "wait", "intent": "wait at the gate", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "gate", "admissible_err": [], "allowed_alternatives": []}}
let booking = {"segment": "book", "intent": "book the plan", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "book_plan", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "verify the price, then book", "segments": [reading, waiting, booking]})
let h = {}
context "read" {
  let entry = tool("catalog_entry", {"plan": "starter"})
  h = hypothesis({"aspect": "content", "subject": "starter", "statement": entry.payload.fact, "scope": "catalog", "source": {"tool": "catalog_entry", "args": {"plan": "starter"}}, "check": {"tool": "billing_quote", "args": {"plan": "starter"}}})
  let checked = probe(h)
}
context "wait" {
  if pause == true {
    let waited = tool("gate", {"run": task_name})
  }
  if recheck == true {
    let again = probe(h)
  }
}
context "book" {
  try {
    let booked = tool("book_plan", {"price": 20}, {"requires": [h]})
  } catch (ACTION_FAILED as refused) {
    print("refused")
  }
  if hold == true {
    let held = tool("gate", {"run": task_name, "after": "book"})
  }
}
print("done")
'''


def _world(root, prior) -> MemoryWorld:
    gate = tool("gate", "clock:ops", [answer({"ok": True}, delay=60, until="continue.signal")], server="gate",
                contract={"observation": True, "idempotent": True})
    book = tool("book_plan", "booking:ops", [answer({"ok": True}, effect="booked")], server="booking",
                contract={"requires_established": True})
    world = MemoryWorld(root, catalog.tools() + [gate, book],
                        provenance={**catalog.provenance(), "clock:ops": {"ancestors": []},
                                    "booking:ops": {"ancestors": []}},
                        knowledge={"embedder": {"tool": "embed", "version": "concepts-v1"},
                                   "properties": catalog.PROPERTIES, "budget": 3})
    for room in ("catalog", "billing"):
        catalog.publish(world, room, "starter", "monthly_price", 20, text="starter monthly price 20")
    catalog.learn(world, "seed-billing", "starter", source="billing_quote")
    if prior != "absent":
        catalog.learn(world, "seed-hypothesis", "starter", check=True)  # The same hypothesis, confirmed.
    if prior == "provisional":
        _billing(world, "first-correction", 25)  # Corrected: memory holds it provisional.
        catalog.publish(world, "billing", "starter", "monthly_price", 20, text="starter monthly price 20")
    return world


def _billing(world, run_id, price) -> int:
    """Billing quotes ``price`` from now on, and a session reads it and consolidates; the place of that reading."""
    catalog.publish(world, "billing", "starter", "monthly_price", price, text=f"starter monthly price {price}")
    catalog.learn(world, run_id, "starter", source="billing_quote")
    read, = [event for event in world.events(run_id, "external_action") if event["request"]["tool"] == "billing_quote"]
    return read["outcome"]["ref"]["gw_seq"]


def _waiting(world, run_id, after=None, **options):
    """A session at the gate before its booking, or at the one after it (``after``)."""
    process = world.start(PROGRAM, run_id, {"task_name": run_id, "recheck": False, "pause": after is None,
                                            "hold": after is not None, **options})
    wanted = {"run": run_id} if after is None else {"run": run_id, "after": after}
    deadline = time.monotonic() + 180
    while wanted not in world.calls("gate"):
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the session did not reach the gate"
        time.sleep(0.2)
    return process


def _released(world, process):
    (world.root / "continue.signal").touch()
    stdout, stderr = process.communicate(timeout=600)
    assert process.returncode == 0, (stdout, stderr)
    return stdout


def _bookings(world):
    return [item for item in world.world()["effects"] if item["tool"] == "book_plan"]


def _status(world):
    """Memory's status of the session's hypothesis and the place of the check the session made."""
    probed = world.events("waiting", "hypothesis_probed")
    hypothesis = probed[0]["hypothesis"]
    entry = world.owner().state()["hypotheses"][hypothesis]
    return entry, [event["check_ref"]["gw_seq"] for event in probed]


@pytest.mark.parametrize("prior", ["absent", "confirmed", "provisional"])
@pytest.mark.parametrize("changes", [1, 2])
def test_a_check_made_before_a_correction_never_admits_the_action_nor_establishes_the_claim(tmp_path, prior,
                                                                                            changes):
    world = _world(tmp_path, prior)
    waiting = _waiting(world, "waiting")
    places = [_billing(world, f"later-{index}", 30 + index) for index in range(changes)]
    assert "refused" in _released(world, waiting)
    assert _bookings(world) == []
    refused, = [event for event in world.events("waiting", "external_action")
                if event["request"]["tool"] == "book_plan"]
    assert "a required basis changed since it was read" in refused["outcome"]["view"]["reason"]
    entry, (checked,) = _status(world)
    assert (entry["status"], entry["at"]) == ("provisional", places[-1]) and checked < places[0]


def test_control_an_unchanged_basis_admits_the_action_once(tmp_path):
    world = _world(tmp_path, "absent")
    waiting = _waiting(world, "waiting")
    _released(world, waiting)
    assert len(_bookings(world)) == 1
    entry, (checked,) = _status(world)
    assert (entry["status"], entry["at"]) == ("confirmed", checked)


def test_a_check_made_after_the_corrections_admits_the_action(tmp_path):
    world = _world(tmp_path, "absent")
    waiting = _waiting(world, "waiting", recheck=True)
    places = [_billing(world, "later-0", 25), _billing(world, "back", 20)]  # Billing returned to 20.
    assert "refused" not in _released(world, waiting)
    assert len(_bookings(world)) == 1
    entry, (first, again) = _status(world)
    assert first < places[0] < places[1] < again and (entry["status"], entry["at"]) == ("confirmed", again)


def test_a_session_recovered_after_the_correction_is_refused_all_the_same(tmp_path):
    world = _world(tmp_path, "absent")
    waiting = _waiting(world, "waiting")
    os.killpg(waiting.pid, signal.SIGKILL)  # After its check, before the booking.
    waiting.wait(60)
    place = _billing(world, "later-0", 30)
    (world.root / "continue.signal").touch()
    code, payload, stderr = world.resume("waiting")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert "refused" in payload["output_delta"] and _bookings(world) == []
    entry, _ = _status(world)
    assert (entry["status"], entry["at"]) == ("provisional", place)


@pytest.mark.parametrize("crash", [False, True], ids=["waiting", "recovered"])
def test_a_booking_already_sent_stays_sent_when_billing_changes_after_it(tmp_path, crash):
    world = _world(tmp_path, "absent")
    waiting = _waiting(world, "waiting", after="book")
    if crash:
        os.killpg(waiting.pid, signal.SIGKILL)  # After the booking and the gate that followed it.
        waiting.wait(60)
    place = _billing(world, "later-0", 30)
    if crash:
        (world.root / "continue.signal").touch()
        code, payload, stderr = world.resume("waiting")
        assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    else:
        _released(world, waiting)
    assert len(_bookings(world)) == 1  # Never undone, never sent again.
    relied = [item for report in world.reports() for item in report["hypotheses"]["relied"]
              if item["run_id"] == "waiting"]
    assert [entry["revoked"] for item in relied for entry in item["hypotheses"]] == ["after_admission"]
    entry, _ = _status(world)
    assert (entry["status"], entry["at"]) == ("provisional", place)


def test_reassessing_leaves_the_status_and_the_bookings_and_again_changes_nothing(tmp_path):
    world = _world(tmp_path, "absent")
    waiting = _waiting(world, "waiting")
    place = _billing(world, "later-0", 30)
    _released(world, waiting)
    for _ in range(2):
        code, result, stderr = world.memory("reassess")
        assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
        entry, _ = _status(world)
        assert (entry["status"], entry["at"]) == ("provisional", place)
    assert _bookings(world) == []
