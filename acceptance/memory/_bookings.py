"""The flight desk of the effect-uncertainty acceptance files (review R2).

Booking is a consequential, non-idempotent action. The booking service
documents one refusal (``SOLD_OUT``: nothing booked); anything else it may
answer is undocumented. Two bookings services are served: ``book`` deduplicates
requests by an idempotency key the gateway issues (its contract names the
argument and the provider's retention), ``book_plain`` does not.

Per flight the service answers:

* ``F-UNCLASSIFIED`` — books the seat, then refuses with an undocumented code;
* ``F-SOLD-OUT`` — first refuses as sold out (nothing booked), then books;
* ``F-OK`` — books;
* ``F-SLOW`` — books, then answers only after a long delay (a window to stop
  the session after the effect and before its answer is recorded);
* ``F-PARTIAL`` — first holds the outbound leg only and refuses with the
  documented ``PARTIAL`` (a partial effect), then completes the booking.
  ``book_resumable`` documents that a partial booking may be repeated to
  completion; the others do not.

A session that books once first reads the seat map, an observation whose
recorded answer persists the run before the booking starts.

The tool server's world counts the bookings that actually happened; the
checker never reads the engine's statuses for that.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, stateful, tool

KEY = "request_key"

BOOK = '''
memory palace "desk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": book_tool, "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "booking", "intent": "book one seat", "element_part": "desk", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "book a seat", "segments": [seg]})
context "booking" {
  if mode == "twice" {
    let first = tool(book_tool, {"flight": flight})
    let second = tool(book_tool, {"flight": flight})
  }
  if mode == "own_key" {
    try {
      let mine = tool(book_tool, {"flight": flight, "request_key": "chosen-by-the-agent"})
    } catch (ACTION_FAILED as refused) {
      print("own key refused")
    }
  }
  if mode == "retry" {
    try {
      let first = tool(book_tool, {"flight": flight})
    } catch (ACTION_FAILED as failed) {
      try {
        let again = tool(book_tool, {"flight": flight}, {"retry_of": failed.op})
      } catch (ACTION_FAILED as refused) {
        print("retry refused")
      }
    }
  }
  if mode == "changed" {
    try {
      let first = tool(book_tool, {"flight": flight})
    } catch (ACTION_FAILED as failed) {
      try {
        let again = tool(book_tool, {"flight": other}, {"retry_of": failed.op})
      } catch (ACTION_FAILED as refused) {
        print("changed retry refused")
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
  if mode == "once" {
    let seats = tool("seat_map", {"flight": flight})
    try {
      let only = tool(book_tool, {"flight": flight})
    } catch (ACTION_FAILED as unknown) {
      print("outcome unknown")
    }
  }
}
print("done")
'''

BOOKED = {"ok": True, "booked": True}


def _answers():
    return [
        answer({"ok": False, "err": "NEW_UNCLASSIFIED_ERROR"}, when={"flight": "F-UNCLASSIFIED"}, effect="booked"),
        answer(BOOKED, when={"flight": "F-SOLD-OUT"}, effect="booked",
               sequence=[{"payload": {"ok": False, "err": "SOLD_OUT"}}]),
        answer(BOOKED, when={"flight": "F-SLOW"}, effect="booked", delay=30),
        answer(BOOKED, when={"flight": "F-PARTIAL"}, effect="completed",
               sequence=[{"payload": {"ok": False, "err": "PARTIAL"}, "effect": "outbound_held"}]),
        answer(BOOKED, effect="booked"),
    ]


def tools(*, retention_s: int = 600) -> list:
    contract = {"effect_on_err": {"SOLD_OUT": "none", "PARTIAL": "partial"}}
    keyed = tool("book", "airline:desk", _answers(), server="airline",
                 contract={**contract, "idempotency_key": {"field": KEY, "retention_s": retention_s}},
                 dedupe={"field": KEY, "retention_s": retention_s})
    plain = tool("book_plain", "airline:plain", _answers(), server="airline", contract=contract)
    resumable = tool("book_resumable", "airline:resumable", _answers(), server="airline",
                     contract={**contract, "repeatable_on_partial": True})
    seats = tool("seat_map", "airline:seats", [answer({"ok": True, "free": 3})], server="airline",
                 contract={"observation": True, "idempotent": True})
    # Reserving records the request's key with the reservation, then answers with an undocumented error;
    # the reservation record echoes whose request made it.
    timeout = {"ok": False, "err": "GATEWAY_TIMEOUT"}
    reserve = tool("reserve", "airline:desk", [
        answer(timeout, when={"flight": "F-THEIRS"}),
        stateful(timeout, act={"put": "reservations", "key": "flight", "state": "booked", "keep": [KEY]},
                 effect="reserved")], server="airline",
        contract={"idempotency_key": {"field": KEY, "retention_s": 600}}, dedupe={"field": KEY, "retention_s": 600})
    reservation = tool("reservation_state", "airline:records", [stateful(
        {"ok": True}, act={"read": "reservations", "key": "flight", "flags": {"booked": "booked"}},
        otherwise={"ok": True, "booked": False})], server="records",
        contract={"state_check_for": "reserve", "resolve_state": {"booked": "applied"},
                  "binds": {"request": {"flight": "flight"}, "answer": {"flight": "flight"}},
                  "attests": "operation", "operation_field": KEY})
    return [keyed, plain, resumable, seats, reserve, reservation]


def world(root, **options) -> MemoryWorld:
    return MemoryWorld(root, tools(**options), provenance={
        "airline:desk": {"ancestors": []}, "airline:plain": {"ancestors": []}, "airline:seats": {"ancestors": []},
        "airline:resumable": {"ancestors": []},
        "airline:records": {"ancestors": []}})


def reserved_by_someone_else(world: MemoryWorld, flight: str) -> None:
    """A reservation another desk's request made before this session (the environment's own record)."""
    import fcntl
    import json

    path = world.world_path
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"calls": [], "effects": []}
        state.setdefault("objects", {}).setdefault("reservations", {})[flight] = {
            "flight": flight, "state": "booked", KEY: "another-desk-request"}
        path.write_text(json.dumps(state, sort_keys=True))


def inputs(task: str, book_tool: str, mode: str, flight: str, other: str = "F-OK") -> dict:
    return {"task_name": task, "book_tool": book_tool, "mode": mode, "flight": flight, "other": other}


def bookings(world: MemoryWorld, flight: str | None = None) -> list:
    """The bookings that actually happened (the environment's own record)."""
    return [item for item in world.world()["effects"] if item["effect"] in {"booked", "outbound_held", "completed"}
            and (flight is None or item["args"]["flight"] == flight)]


def keys(world: MemoryWorld, tool_name: str = "book") -> list:
    """The idempotency keys the service received, in call order."""
    return [item["args"].get(KEY) for item in world.world()["calls"] if item["tool"] == tool_name]


def actions(world: MemoryWorld, run_id: str) -> list:
    return [(event["request"]["tool"], event["request"]["args"], event["outcome"]["view"]["effect"],
             event["outcome"]["view"].get("reason")) for event in world.events(run_id, "external_action")]
