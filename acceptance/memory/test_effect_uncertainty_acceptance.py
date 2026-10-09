"""An undocumented refusal leaves the effect unknown; a key makes a repeat safe (review R2).

The environment's own record of bookings is the checker's truth:

* The service books the seat and then refuses with a code its contract does
  not describe. The effect is unknown, never "none": the declared retry of the
  non-idempotent booking is refused before any effect, and exactly one seat
  is booked. With the provider's idempotency key, the retry carries the same
  key and the provider answers it again without a second booking.
* A documented refusal (sold out, nothing booked) admits the declared retry,
  which books once.
* Two independent identical bookings are two operations with two keys: two
  seats, as asked.
* A declared retry that changes the flight is refused before any effect, and
  the agent can never choose the key itself.
* A reservation record that echoes this operation's key attests the operation;
  another desk's reservation of the same flight shows only the state (review
  R1).
"""
from __future__ import annotations

from acceptance.memory import _bookings as desk


def test_an_undocumented_refusal_never_licenses_a_second_booking(tmp_path):
    world = desk.world(tmp_path)
    world.run(desk.BOOK, "plain", desk.inputs("plain", "book_plain", "retry", "F-UNCLASSIFIED"))
    first, again = desk.actions(world, "plain")
    assert first[2] == "unknown"  # Not "none": the service may have acted before refusing.
    assert again[2] == "none" and again[3].endswith("resolve it by a state check")
    assert len(desk.bookings(world, "F-UNCLASSIFIED")) == 1

    world.run(desk.BOOK, "keyed", desk.inputs("keyed", "book", "retry", "F-UNCLASSIFIED"))
    first, again = desk.actions(world, "keyed")
    assert first[2] == again[2] == "unknown"  # The provider answered the kept refusal again.
    sent = [key for key in desk.keys(world) if key is not None]
    assert len(sent) == 2 and sent[0] == sent[1]  # The repeat carried the operation's key.
    assert len(desk.bookings(world, "F-UNCLASSIFIED")) == 2  # One per session, never a second in one.


def test_a_documented_refusal_admits_the_retry_and_it_books_once(tmp_path):
    world = desk.world(tmp_path)
    world.run(desk.BOOK, "sold-out", desk.inputs("sold-out", "book_plain", "retry", "F-SOLD-OUT"))
    first, again = desk.actions(world, "sold-out")
    assert (first[2], again[2]) == ("none", "applied")
    assert len(desk.bookings(world, "F-SOLD-OUT")) == 1


def test_independent_identical_bookings_are_two_operations_with_two_keys(tmp_path):
    world = desk.world(tmp_path)
    world.run(desk.BOOK, "twice", desk.inputs("twice", "book", "twice", "F-OK"))
    assert len(desk.bookings(world, "F-OK")) == 2
    sent = desk.keys(world)
    assert len(sent) == 2 and len(set(sent)) == 2 and None not in sent


def test_a_changed_retry_and_an_agent_chosen_key_are_refused_before_any_effect(tmp_path):
    world = desk.world(tmp_path)
    world.run(desk.BOOK, "changed", desk.inputs("changed", "book", "changed", "F-SOLD-OUT", other="F-OK"))
    first, again = desk.actions(world, "changed")
    assert again[3] == "a declared retry changes the tool or its essential arguments"
    assert desk.bookings(world) == []
    calls = len(world.world()["calls"])
    world.run(desk.BOOK, "own-key", desk.inputs("own-key", "book", "own_key", "F-OK"))
    only, = desk.actions(world, "own-key")
    assert only[3] == "the idempotency key is the gateway's to issue, never an argument"
    assert desk.bookings(world) == [] and len(world.world()["calls"]) == calls  # It never reached the service.


def test_a_state_check_credits_the_operation_only_when_it_names_the_operations_key(tmp_path):
    world = desk.world(tmp_path)
    # This desk's reservation, answered with an undocumented error: the record echoes this operation's key.
    world.run(desk.BOOK, "mine", desk.inputs("mine", "reserve", "reserve", "F-MINE"))
    failure, = world.failures("mine")
    assert failure["resolution"]["attests"] == "operation" and failure["settled"] is True
    assert [item["by"] for item in failure["attestations"]] == ["state_check"]
    # Another desk's reservation of the flight: the state exists, this operation is not shown to have made it.
    desk.reserved_by_someone_else(world, "F-THEIRS")
    world.run(desk.BOOK, "theirs", desk.inputs("theirs", "reserve", "reserve", "F-THEIRS"))
    failure, = world.failures("theirs")
    assert failure["resolution"]["attests"] == "state" and failure["settled"] is False
    assert failure["attestations"] == [] and failure["effect"] == "unknown"
