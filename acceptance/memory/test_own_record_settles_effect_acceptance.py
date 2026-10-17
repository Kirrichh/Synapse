"""An own record still settles an unknown effect, and never witnesses it.

A reservation times out with its effect unknown; the slow path reads the reservation record, which settles
the effect as applied (``_bookings``). Kept under the agent-own name ``ledger:records`` the record still
settles the effect of every reservation, and the report says it could not witness the reservation.
"""
from __future__ import annotations

from acceptance.memory import _bookings as desk
from acceptance.memory._world import MemoryWorld


def test_an_own_record_still_settles_an_unknown_effect_and_never_witnesses_it(tmp_path):
    tools = [dict(item, source="ledger:records") if item["name"] == "reservation_state" else item
             for item in desk.tools()]
    world = MemoryWorld(tmp_path, tools, provenance={
        "airline:desk": {"ancestors": []}, "airline:plain": {"ancestors": []}, "airline:seats": {"ancestors": []},
        "airline:resumable": {"ancestors": []}, "ledger:records": {"ancestors": []}})
    for index, flight in enumerate(["F-A", "F-B", "F-C"]):
        world.run(desk.BOOK, f"reserve-{index}", desk.inputs(f"task-{index}", "book", "reserve", flight))

    assert [failure["settled"] for index in range(3) for failure in world.failures(f"reserve-{index}")] == [True] * 3
    explained, = world.reports()[-1]["witness_exclusions"]
    assert (explained["counted"], explained["required"], explained["reasons"]) == (0, 2, {"agent_own_service": 1})
    excluded, = explained["excluded"]
    assert {key: excluded[key] for key in ("tool", "source", "by", "reasons", "resolves_effect")} == {
        "tool": "reservation_state", "source": "ledger:records", "by": "state_check",
        "reasons": ["agent_own_service"], "resolves_effect": True}
