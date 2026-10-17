"""Tool configuration v3: a source not declared outside never witnesses, and the report says why.

An own store under an innocent name (``kv:bookings``) would have counted under v2's name rule. Under v3 it is
declared ``"outside": false``, so in the travel scenario (``_witnesses``) its confirmations never witness:
the candidate waits for independence and the report's ``witness_exclusions`` names every such answer with
``not_declared_outside``.
"""
from __future__ import annotations

from acceptance.memory import _witnesses as witnesses
from acceptance.memory._witnesses import INSIDE, OUTSIDE
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V3


def test_a_source_not_declared_outside_never_witnesses_and_the_report_says_why(tmp_path):
    world = witnesses.world(tmp_path, {"flights:acct1": OUTSIDE, "quota:ops": INSIDE, "kv:bookings": INSIDE},
                            schema=TOOL_CONFIGURATION_V3, booking="kv:bookings")

    report = witnesses.learn(world)

    assert report["births"] == []
    candidate, = report["pool_updates"]
    assert candidate["reasons"] == ["independence_not_established"]
    explained, = report["witness_exclusions"]
    assert (explained["counted"], explained["required"], explained["reasons"]) == (1, 2, {"not_declared_outside": 1})
    assert explained["excluded"][0]["source"] == "kv:bookings"
