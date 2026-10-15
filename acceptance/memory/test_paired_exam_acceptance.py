"""A paired A/B/C exam: the same tasks, each arm from the same initial state (review §9.3).

After a learned search recovery is born and admitted, one fixed snapshot is
examined in three arms on the very same tasks (``_paired.py``):

* A — accumulated experience switched off: nothing is loaded;
* B — the learned habits the snapshot admits act on the fast path;
* C — the same habits are loaded but every learned trigger is slow-only.

Before each arm the environment is restored to the state it had when the
snapshot was taken, so every arm meets exactly the same refusals and answers
and no arm's effects reach another. The tasks include one outside the habit's
scope (a domestic route), where all three arms must behave alike. No arm
teaches the memory.

Each measure is taken separately: the goal is reached in every task of every
arm, with no erroneous action and no abstention; only B applies the habit, so
A and C each lose the useful knowledge of the two tasks it applies to; the
habit's actual fires, the tool calls, model calls, the latency the gateway
measured and the size of the memory each arm read are published apart —
cost is an observation, never a verdict.
"""
from __future__ import annotations

from acceptance.memory import _paired as paired
from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def test_three_arms_on_the_same_tasks_from_the_same_state(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    birth, snapshot = paired.learn(world)
    initial, journal = world.world(), world.journal()

    arms = {mode: paired.arm(world, mode, snapshot, initial) for mode in "ABC"}

    for route in paired.TASKS:
        a, b, c = (arms[mode][0][route] for mode in "ABC")
        # The same environment met every arm: the refused search and its repeat, then the independent record.
        assert a["flights"] == b["flights"] == c["flights"] == [{"route": route}] * 2
        assert a["goal"] and b["goal"] and c["goal"]
        assert a["loaded"] == [] and b["loaded"] == c["loaded"] == [birth["habit_id"]]
    # Inside the habit's scope only B takes the fast path; outside it every arm takes the slow path.
    assert [arms["B"][0][route]["habit_fires"] for route in paired.TASKS] == [1, 1, 0]
    assert [arms["A"][0][route]["slow_path"] for route in paired.TASKS] == [1, 1, 1]
    assert [arms["C"][0][route]["slow_path"] for route in paired.TASKS] == [1, 1, 1]
    assert [arms["C"][0][route]["suppressed"] for route in paired.TASKS] == [["slow_only"], ["slow_only"], []]
    # Each arm started from the same state and left the same effects: none leaked into another.
    assert arms["A"][1] == arms["B"][1] == arms["C"][1]
    # No arm taught the memory.
    assert world.journal() == journal

    totals = paired.summary(arms)
    for mode in "ABC":
        assert (totals[mode]["goal"], totals[mode]["erroneous_actions"], totals[mode]["abstained"]) == (3, 0, 0)
        assert totals[mode]["model_calls"] == 0 and totals[mode]["latency_ms"] > 0
    assert [totals[mode]["lost_useful_knowledge"] for mode in "ABC"] == [2, 0, 2]
    assert [totals[mode]["habit_fires"] for mode in "ABC"] == [0, 2, 0]
    # The recovery calls the same tools on either path: the fast path saves the slow path, not the work.
    assert totals["A"]["tool_calls"] == totals["B"]["tool_calls"] == totals["C"]["tool_calls"]
    assert totals["A"]["memory_bytes"] == 0 < totals["B"]["memory_bytes"] == totals["C"]["memory_bytes"]
