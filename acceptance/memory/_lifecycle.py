"""The memory lifecycle scenario of the sequence acceptance file (review §9.2).

One program, several kinds of session, all through the canonical launch:

* ``book`` — book a seat with the keyed booking service; when the answer is
  lost or refused, declare the retry (the gateway decides whether it may be
  made). Per flight the service books (``OK-…``), books and loses the answer
  (``LOST-…``), loses the request before booking (``GONE-…``), refuses as sold
  out first (``SOLD-…``, nothing booked) or books and answers only late
  (``SLOW-…``, a window to stop the session after the effect). The session
  first passes a gate, an observation whose recorded answer persists the run,
  and may wait there (a window to stop it before anything is sent). Within one
  process a tool server that died loses every later call, so a lost answer's
  repeat is made by the resumed session;
* ``reserve`` — reserve a seat (the service records the reservation with the
  request's key and answers with an undocumented error), then check the
  reservation record, which echoes the key of the request that made it;
* ``restart`` — read the knowledge-base card (its revision changes when the
  source is updated), state its restart command as a content hypothesis,
  establish it — reused from memory or checked by the runbook, or by a mirror of
  the card that can never confirm it — and restart the service, which requires
  the hypothesis established;
* ``idle`` — a session that does nothing memory needs: an empty window.

The tool server's world is the checker's truth: bookings, reservations,
restarts, and the runbook's checks.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from acceptance.memory._world import MemoryWorld, REPOSITORY, answer, stateful, tool

KEY = "request_key"
COMMAND = "pg_ctl restart -m fast"

PROGRAM = '''
memory palace "desk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "book", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "work", "intent": "do the session's work", "element_part": "desk", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "do the work", "segments": [seg]})
context "work" {
  if mode == "book" {
    let opened = tool("gate", {"run": task_name, "step": "opened"})
    if pause == true {
      let waited = tool("gate", {"run": task_name, "step": "wait"})
    }
    try {
      let first = tool("book", {"flight": flight})
    } catch (ACTION_FAILED as failed) {
      try {
        let again = tool("book", {"flight": flight}, {"retry_of": failed.op})
      } catch (ACTION_FAILED as refused) {
        print("retry refused")
      }
    }
  }
  if mode == "reserve" {
    try {
      let held = tool("reserve", {"flight": flight})
    } catch (ACTION_FAILED as unknown) {
      let checked = tool("reservation_state", {"flight": flight})
    }
  }
  if mode == "restart" {
    let card = tool("kb_card", {"id": "pg"})
    let origin = {"tool": "kb_card", "args": {"id": "pg"}}
    let content = hypothesis({"aspect": "content", "subject": "postgresql", "statement": {"restart_command": card.payload.command}, "scope": "host-a", "source": origin, "check": {"tool": checker, "args": {"service": "postgresql"}}})
    if established(content) == false {
      let c = probe(content)
    }
    try {
      let done = tool("restart_service", {"command": card.payload.command}, {"requires": [content]})
    } catch (ACTION_FAILED as refused) {
      print("abstained")
    }
  }
}
print("done")
'''

#: Flights per kind of answer; a session books a fresh one.
KINDS = ("OK", "LOST", "GONE", "SOLD", "SLOW")


def _booking_rules(count: int) -> list:
    booked = {"ok": True, "booked": True}
    rules = []
    for index in range(count):
        # The provider keeps the booking it made for a key even when its answer was lost in transit: the
        # repeat with that key is answered from it (the checker separately sees the repeat carry the key).
        rules += [answer(booked, when={"flight": f"LOST-{index}"}, sequence=[{"lost": True, "effect": "booked"}]),
                  {"when": {"flight": f"GONE-{index}"}, "sequence": [{"lost": True, "effect": None}],
                   "then": {"payload": booked, "effect": "booked"}},
                  answer(booked, when={"flight": f"SOLD-{index}"}, effect="booked",
                         sequence=[{"payload": {"ok": False, "err": "SOLD_OUT"}}]),
                  answer(booked, when={"flight": f"SLOW-{index}"}, effect="booked", delay=25)]
    return [*rules, answer(booked, effect="booked")]


def tools(count: int = 40) -> list:
    keyed = {"field": KEY, "retention_s": 3600}
    timeout = {"ok": False, "err": "GATEWAY_TIMEOUT"}
    return [
        tool("book", "airline:desk", _booking_rules(count), server="airline",
             contract={"effect_on_err": {"SOLD_OUT": "none"}, "idempotency_key": keyed}, dedupe=keyed),
        tool("gate", "clock:ops", [answer({"ok": True}, when={"step": "wait"}, delay=25, until="gate.signal"),
                                   answer({"ok": True})], server="clock",
             contract={"observation": True, "idempotent": True}),
        tool("reserve", "airline:desk", [stateful(timeout, act={"put": "reservations", "key": "flight",
                                                                "state": "booked", "keep": [KEY]}, effect="reserved")],
             server="airline", contract={"idempotency_key": keyed}, dedupe=keyed),
        tool("reservation_state", "airline:records", [stateful(
            {"ok": True}, act={"read": "reservations", "key": "flight", "flags": {"booked": "booked"}},
            otherwise={"ok": True, "booked": False})], server="records",
            contract={"state_check_for": "reserve", "resolve_state": {"booked": "applied"},
                      "binds": {"request": {"flight": "flight"}, "answer": {"flight": "flight"}},
                      "attests": "operation", "operation_field": KEY}),
        tool("kb_card", "kb:wiki", [stateful({"ok": True}, act={"read": "cards", "key": "id"},
                                             otherwise={"ok": False, "err": "NO_CARD"})], server="kb",
             contract={"effect_on_err": {"NO_CARD": "none"}}),
        tool("runbook", "runbook:ops", [answer({"ok": True, "restart_command": COMMAND})], server="runbook",
             contract={"verifies": {"subject": {"request": "service"}, "scope": {"value": "host-a"}}}),
        tool("kb_mirror", "mirror:kb", [answer({"ok": True, "restart_command": COMMAND})], server="mirror"),
        tool("restart_service", "ops:host-a", [answer({"ok": True, "restarted": True}, effect="restarted")],
             server="ops", contract={"requires_established": True})]


def provenance() -> dict:
    return {"airline:desk": {"ancestors": []}, "airline:records": {"ancestors": []}, "clock:ops": {"ancestors": []},
            "kb:wiki": {"ancestors": []}, "runbook:ops": {"ancestors": []}, "ops:host-a": {"ancestors": []},
            # The mirror republishes the card: it can never confirm it.
            "mirror:kb": {"ancestors": ["kb:wiki"]}}


def world(root: Path) -> MemoryWorld:
    # A status stays fresh across any number of windows: empty windows must not degrade the stream.
    created = MemoryWorld(root, tools(), provenance=provenance(), parameters={"hypothesis_fresh_windows": 10000})
    publish(created, 1)
    return created


def publish(world: MemoryWorld, revision: int) -> None:
    """The card as the knowledge base serves it from now on (a new revision is a new source version)."""
    path = world.world_path
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"calls": [], "effects": []}
        state.setdefault("objects", {}).setdefault("cards", {})["pg"] = {
            "id": "pg", "command": COMMAND, "rev": revision}
        path.write_text(json.dumps(state, sort_keys=True))


def inputs(task: str, mode: str, *, flight: str = "", pause: bool = False, checker: str = "runbook") -> dict:
    return {"task_name": task, "mode": mode, "flight": flight, "pause": pause, "checker": checker}


def crash(world: MemoryWorld, run_id: str, bindings: dict, point: str) -> int:
    """The session through the crash driver, dying at ``point``; its exit code."""
    program = world.root / f"{run_id}.syn"
    program.write_text(PROGRAM)
    data = world.root / f"{run_id}.input.json"
    data.write_text(json.dumps(bindings, sort_keys=True))
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "acceptance.memory._crash_driver", str(world.state),
         str(world.configuration_path), str(program), str(world.runs), run_id, str(data), point],
        cwd=REPOSITORY, env={**os.environ, "PYTHONPATH": str(REPOSITORY)}, capture_output=True, text=True,
        timeout=900)
    return completed.returncode


def effects(world: MemoryWorld, name: str) -> list:
    return [item["args"] for item in world.world()["effects"] if item["effect"] == name]
