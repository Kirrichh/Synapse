"""A graph interrupted by a crash resumes without a second effect or a second account (refinement §16).

The session is killed while the quote and the audit are still in flight and
the stock count has already been answered and recorded. Resumed, the run
follows its record up to the crash — the stock is not read again — and goes on
live: the two calls whose answers were lost are observations, so they are read
again under the same identities; the graph commits once and the order is
placed once. Every computation has one step in the history and every call one
final answer in the gateway's journal. Re-entering the completed run changes
nothing.
"""
from __future__ import annotations

import json
import os
import signal
import time

from acceptance.memory import _offers as offers


def _await(condition, seconds=180):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the scenario did not reach its crash point"
        time.sleep(0.2)


def _recorded(world, run_id: str, tool: str) -> bool:
    try:
        history = world.history(run_id)
    except (OSError, ValueError, KeyError):
        return False  # Not written yet.
    return any(event.get("type") == "external_action" and event["request"]["tool"] == tool for event in history)


def _finals(world, run_id: str) -> dict:
    counts: dict = {}
    for line in (world.owner().gateway_root / "journal.jsonl").read_text().splitlines():
        body = json.loads(line)["body"]
        if body.get("run_id") == run_id and body.get("final"):
            counts[body["ordinal"]] = counts.get(body["ordinal"], 0) + 1
    return counts


def test_a_graph_killed_mid_flight_resumes_with_one_commit_one_order_and_one_account(tmp_path):
    world = offers.world(tmp_path, {("billing_quote", "a"): 4.0, ("audit_trail", "a"): 9.0})
    process = world.start(offers.OFFER, "crashed", {"task_name": "crashed", "plan_id": "a"})
    _await(lambda: {"plan": "a"} in world.calls("billing_quote") and {"plan": "a"} in world.calls("audit_trail")
           and _recorded(world, "crashed", "inventory"))
    os.killpg(process.pid, signal.SIGKILL)  # The session and its tool servers die with two calls in flight.
    process.wait(60)
    assert offers.orders(world) == []

    code, payload, stderr = world.resume("crashed")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    # Answered before the crash: never asked again. Lost in flight: observations, read again.
    assert world.calls("inventory") == [{"plan": "a"}]
    assert world.calls("billing_quote") == [{"plan": "a"}, {"plan": "a"}]
    assert world.calls("audit_trail") == [{"plan": "a"}, {"plan": "a"}]
    assert offers.orders(world) == [{"tool": "place_order", "effect": "order_placed",
                                     "args": {"plan": "a", "price": 20, "count": 5}}]

    found = offers.result(world, "crashed")
    assert found["commit"]["value"] == {"price": 20, "count": 5}
    assert sorted((step["node"], step["attempt"], step["status"]) for step in found["steps"]) == [
        ("audit", 1, "cancelled"), ("quote", 1, "integrated"), ("stock", 1, "integrated"),
        ("terms", 1, "integrated")]
    finals = _finals(world, "crashed")
    assert {ordinal: count for ordinal, count in finals.items() if str(ordinal).startswith("df:")} == {
        "df:offer-1:quote:1": 1, "df:offer-1:stock:1": 1, "df:offer-1:audit:1": 1}
    assert all(count == 1 for count in finals.values())
    assert world.reports()[-1]["replay"]["crashed"]["status"] == "replay_verified"

    # Re-entering the completed run applies nothing again.
    journal = world.journal()
    world.resume("crashed")
    assert world.journal() == journal and len(offers.orders(world)) == 1
