"""A session stopped after the booking and before its answer was recorded (review R2).

The service books the seat and answers only after a long delay; the session
is killed in between, so the gateway's journal holds the booking's start and
no result. On resume:

* with the provider's idempotency key still within its retention, recovery
  repeats the booking with the same key and the provider answers the kept
  result without booking again: one seat, the operation settled;
* without a key, the lost booking is never repeated: one seat, the effect
  stays unknown;
* with a key older than the provider's retention, the repeat is refused as
  well — the provider would have forgotten the key and booked twice.

The environment's own record counts the bookings.
"""
from __future__ import annotations

import os
import signal
import time

from acceptance.memory import _bookings as desk


def _await(condition, seconds=180):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the scenario did not reach its crash point"
        time.sleep(0.2)


def _killed_after_the_effect(world, run_id, book_tool):
    process = world.start(desk.BOOK, run_id, desk.inputs(run_id, book_tool, "once", "F-SLOW"))
    _await(lambda: len(desk.bookings(world, "F-SLOW")) == 1)
    os.killpg(process.pid, signal.SIGKILL)  # After the booking, before its answer.
    process.wait(60)


def _resumed(world, run_id):
    code, payload, stderr = world.resume(run_id)
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    return desk.actions(world, run_id)


def test_a_keyed_booking_is_recovered_with_its_key_and_books_once(tmp_path):
    world = desk.world(tmp_path)
    _killed_after_the_effect(world, "keyed", "book")
    _, only = _resumed(world, "keyed")
    assert only[2] == "applied"
    assert len(desk.bookings(world, "F-SLOW")) == 1
    sent = desk.keys(world)
    assert len(sent) == 2 and sent[0] == sent[1]  # The repeat carried the same key.


def test_a_lost_booking_without_a_key_is_never_repeated(tmp_path):
    world = desk.world(tmp_path)
    _killed_after_the_effect(world, "plain", "book_plain")
    _, only = _resumed(world, "plain")
    assert only[2] == "unknown"
    assert len(desk.bookings(world, "F-SLOW")) == 1 and len(desk.keys(world, "book_plain")) == 1


def test_an_expired_key_does_not_license_the_repeat(tmp_path):
    world = desk.world(tmp_path, retention_s=2)
    _killed_after_the_effect(world, "expired", "book")
    time.sleep(3)  # Past the provider's retention.
    _, only = _resumed(world, "expired")
    assert only[2] == "unknown"
    assert len(desk.bookings(world, "F-SLOW")) == 1 and len(desk.keys(world)) == 1
