"""The paired A/B/C exam of the travel search and its separate measures (review §9.3).

One learned search recovery is born; one fixed snapshot is then examined in
three arms on the same tasks — A (accumulated experience off), B (admitted
habits on the fast path), C (the same habits, every learned trigger
slow-only). Before each arm the environment is restored to the state it had
when the snapshot was taken.

The unit of the experiment is one task in one arm: each task searches its own
route, so no task of an arm changes what another meets, and the arms of one
task start from the same restored state. Repetitions of a task across arms
are paired, never independent samples.

Every measure is taken separately, from the environment's own record and the
gateway's journal — never from the engine's account: the goal reached (the
independent booking system found the flights), erroneous actions (actions the
environment refused that the task did not need: every failed action but the
one refusal each task meets by design), abstention (the goal not reached
without an erroneous action), lost useful knowledge (a task the snapshot holds
an admitted habit for that the arm did not apply), the habits' actual fires,
tool and model calls, the latency the gateway measured and the size of the
memory the arm read. Cost stays an observation.
"""
from __future__ import annotations

import json

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld
from synapse.memory_consolidation import records

TASKS = ("TBS", "EVN", "LED")
#: Each task meets the busy refusal of its first search by design: one failed action that is not an error.
DESIGNED_REFUSALS = 1


def learn(world: MemoryWorld) -> tuple[dict, str]:
    """The birth of the recovery and the snapshot boundary the arms read."""
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]
    return birth, world.reports()[-1]["snapshot_boundary_after"]


def _journal(world: MemoryWorld) -> list[dict]:
    path = world.owner().gateway_root / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _latency_ms(world: MemoryWorld, results: set[int]) -> float:
    path = world.owner().gateway_root / "side-time.jsonl"
    side = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return round(sum(item["duration_ms"] for item in side
                     if item.get("measured_by") == "wall_clock" and item["gw_seq"] in results), 3)


def measures(world: MemoryWorld, run_id: str, route: str) -> dict:
    """The separate measures of one task in one arm."""
    journal = [record for record in _journal(world) if record["body"].get("run_id") == run_id]
    started = {record["seq"]: record["body"] for record in journal if record["kind"] == "STARTED"}
    results = [record for record in journal if record["kind"] == "RESULT"]
    failed = sum(1 for record in results if record["body"]["op_result"] != "ok"
                 and started[record["body"]["started_seq"]]["role"] == "action")
    goal = {"route": route} in world.calls("booking_state") and any(
        item == {"route": route} for item in world.calls("flights"))
    erroneous = max(0, failed - DESIGNED_REFUSALS) + sum(1 for record in journal if record["kind"] == "REJECTED")
    opening = world.opening(run_id)
    boundary = None if opening["boundary"] is None else next(
        item for item in world.owner().boundaries().values() if item["id"] == opening["boundary"])
    return {"goal": goal, "erroneous_actions": erroneous, "abstained": not goal and erroneous == 0,
            "habit_fires": len(world.events(run_id, "habit_activated")),
            "slow_path": len(world.events(run_id, "slow_path_used")),
            "tool_calls": sum(1 for body in started.values() if body["role"] == "action"),
            "model_calls": sum(1 for body in started.values() if body["role"] == "reason"),
            "latency_ms": _latency_ms(world, {record["seq"] for record in results}),
            "memory_bytes": 0 if boundary is None else len(records.canonical(boundary)),
            "loaded": [item["habit_id"] for item in opening["learned"]]}


def arm(world: MemoryWorld, mode: str, snapshot: str, initial: dict) -> tuple[dict, list]:
    """One arm on every task from the restored initial state: per-task measures and the effects it left."""
    world.restore(initial)
    found = {}
    for route in TASKS:
        run_id = f"{mode}-{route}"
        world.run(travel.PROGRAM, run_id, travel.inputs(run_id, route), exam=(mode, snapshot))
        found[route] = {**measures(world, run_id, route),
                        "flights": [call for call in world.calls("flights") if call == {"route": route}],
                        "suppressed": [item["reason"] for item in world.events(run_id, "habit_suppressed")]}
    return found, world.world()["effects"]


def summary(arms: dict) -> dict:
    """Per arm, every measure summed over the tasks; lost useful knowledge against the tasks B applied a habit to."""
    applicable = {route for route, item in arms["B"][0].items() if item["habit_fires"]}
    totals = {}
    for mode, (tasks, _) in sorted(arms.items()):
        keys = ("goal", "erroneous_actions", "abstained", "habit_fires", "slow_path", "tool_calls", "model_calls",
                "latency_ms", "memory_bytes")
        totals[mode] = {key: round(sum(float(item[key]) for item in tasks.values()), 3) for key in keys}
        totals[mode]["lost_useful_knowledge"] = sum(1 for route in applicable if not tasks[route]["habit_fires"])
        totals[mode]["tasks"] = len(tasks)
    return totals
