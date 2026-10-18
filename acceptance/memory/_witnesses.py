"""Witness scenarios over the travel world (``_travel``).

The flight search refuses the first search of every route; the slow path repeats it (a witness from the
flight service) and confirms it through a booking system's state check (a second witness when the booking
system is an independent outside source). A birth needs three such recoveries in different tasks and two
independent outside witnesses of the refused search.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

#: Provenance nodes of tool configuration v3: every node states whether its source is outside.
OUTSIDE = {"ancestors": [], "outside": True}
INSIDE = {"ancestors": [], "outside": False}


def world(root, provenance, *, schema, **sources) -> MemoryWorld:
    """The travel world under a declared provenance and tool configuration version."""
    return MemoryWorld(root, travel.tools(**sources), provenance=provenance, tool_schema=schema)


def learn(world: MemoryWorld, routes=("BUS", "YVR", "MSQ"), first=0) -> dict:
    """One recovery per route, each in its own task; the report of the last consolidation."""
    for index, route in enumerate(routes, first):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    return world.reports()[-1]
