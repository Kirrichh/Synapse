"""A boundary moves only by verified evidence and never over a recorded contrast (refinement §12, D1).

After the migration procedure is born with ``lag_ms <= 120`` (its lag contrast
failed at 900), one session migrates three databases at lag 300: each is a
near miss of that one condition, and the slow path recovers each with the
same procedure. The court widens the boundary to 300 — the condition is
extended, never removed, and the readers condition stays — in a successor on
probation that supersedes the habit in Gold. A new database at lag 250 then
runs on the successor's fast path.

Three recoveries at lag 1000 are as verified, but admitting 1000 would admit
the recorded failure at 900: the court refuses that widening and says why,
and lag 1000 stays on the slow path.
"""
from __future__ import annotations

from acceptance.memory import _checkpoint as checkpoint


def _conditions(item):
    return {(entry["field"], entry["op"]): entry["value"] for entry in item["when"]}


def test_recovered_near_misses_extend_a_boundary_that_never_crosses_a_contrast(tmp_path):
    world = checkpoint.world(tmp_path)
    birth = checkpoint.learn(world)
    assert _conditions(birth["condition"])[("lag_ms", "<=")] == 120

    # Three near misses of lag_ms <= 120 in one window, each recovered by the same procedure.
    world.run(checkpoint.program(3), "caches", checkpoint.inputs("caches", "cache-1", "cache-2", "cache-3"))
    misses = world.events("caches", "habit_near_miss")
    assert [event["failed_condition"]["actual"] for event in misses] == [300, 300, 300]
    assert len(world.events("caches", "slow_path_used")) == 3
    report = world.reports()[-1]
    supersession, = report["supersessions"]
    assert supersession["predecessor"] == birth["habit_id"] and supersession["kind"] == "widen"
    successor, = report["births"]
    widened = _conditions(successor["condition"])
    assert widened[("lag_ms", "<=")] == 300 and widened[("readers", "==")] == 0
    assert widened == {**_conditions(birth["condition"]), ("lag_ms", "<="): 300}  # Extended, nothing removed.
    assert successor["state"] == "probation" and successor["boundary"]["predecessor"] == birth["habit_id"]
    assert report["legitimacy"][birth["habit_id"]]["admitted"] is False
    assert successor["applicability"]["essential"] == birth["applicability"]["essential"]

    # The successor acts inside its widened scope.
    world.run(checkpoint.program(), "cache-4", checkpoint.inputs("cache-4", "cache-4"))
    activated, = world.events("cache-4", "habit_activated")
    assert activated["habit_id"] == successor["habit_id"] and activated["outcome"] == "success"
    assert checkpoint.effects(world, "cache-4") == ["checkpointed"]

    # Verified recoveries at 1000 would admit the recorded failure at 900: the widening is refused.
    world.run(checkpoint.program(3), "blobs", checkpoint.inputs("blobs", "blob-1", "blob-2", "blob-3"))
    report = world.reports()[-1]
    assert report["supersessions"] == [] and report["births"] == []
    refusal, = report["boundary_refusals"]
    assert refusal["habit_id"] == successor["habit_id"] and refusal["near_misses"] == 3
    assert refusal["failed_condition"]["field"] == "lag_ms" and "contrast" in refusal["reason"]
    world.run(checkpoint.program(), "blob-4", checkpoint.inputs("blob-4", "blob-4", careful=True))
    assert world.events("blob-4", "habit_activated") == []
    near, = world.events("blob-4", "habit_near_miss")
    assert near["failed_condition"] == {"field": "lag_ms", "op": "<=", "value": 300, "actual": 1000}
    assert checkpoint.effects(world, "blob-4") == []
