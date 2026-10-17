"""Tool configuration v3: two outside sources of one service are one operator, never two witnesses.

The search and the booking check are both served by one GDS: two accounts, ``gds:search`` and
``gds:bookings``, both declared outside. v3 derives the service's operator node as their common origin, so
in the travel scenario (``_witnesses``) their answers are dependent and the procedure is not born. Under v2
the same two accounts, with no declared common ancestor, counted as independent.
"""
from __future__ import annotations

from acceptance.memory import _witnesses as witnesses
from acceptance.memory._witnesses import INSIDE, OUTSIDE
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V2, TOOL_CONFIGURATION_V3


def test_two_outside_sources_of_one_service_are_one_operator(tmp_path):
    provenance = {"gds:search": OUTSIDE, "quota:ops": INSIDE, "gds:bookings": OUTSIDE}

    report = witnesses.learn(witnesses.world(tmp_path / "v3", provenance, schema=TOOL_CONFIGURATION_V3,
                                             flights="gds:search", booking="gds:bookings"))

    assert report["births"] == []
    candidate, = report["pool_updates"]
    assert candidate["independence"]["verdict"] == "dependent"
    assert candidate["reasons"] == ["sources_dependent"]
    v2 = witnesses.learn(witnesses.world(tmp_path / "v2", {name: {"ancestors": []} for name in provenance},
                                         schema=TOOL_CONFIGURATION_V2, flights="gds:search", booking="gds:bookings"))
    assert v2["births"][0]["criteria"]["independence"] == "independent"
