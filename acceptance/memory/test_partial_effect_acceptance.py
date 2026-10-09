"""A documented partial effect is repeated only where the operation's contract allows it (review R2).

The airline holds only the outbound leg and says so (``PARTIAL``). The
environment's own record of bookings is the checker's truth:

* under the plain booking contract a partial effect is not repeatable: the
  declared retry is refused before any further effect, and only the held
  outbound leg exists;
* under a contract that declares a partial booking resumable, the same retry
  is admitted and completes the booking once.
"""
from __future__ import annotations

from acceptance.memory import _bookings as desk


def test_a_partial_effect_is_repeated_only_where_the_contract_allows_it(tmp_path):
    world = desk.world(tmp_path)
    world.run(desk.BOOK, "partial", desk.inputs("partial", "book_plain", "retry", "F-PARTIAL"))
    first, again = desk.actions(world, "partial")
    assert first[2] == "partial" and again[2] == "none"
    assert again[3] == "a partial effect is not repeatable under the operation's contract"
    assert [item["effect"] for item in desk.bookings(world, "F-PARTIAL")] == ["outbound_held"]

    world.run(desk.BOOK, "resumed", desk.inputs("resumed", "book_resumable", "retry", "F-PARTIAL"))
    first, again = desk.actions(world, "resumed")
    assert (first[2], again[2]) == ("partial", "applied")
    assert [item["effect"] for item in desk.bookings(world, "F-PARTIAL")] == [
        "outbound_held", "outbound_held", "completed"]
