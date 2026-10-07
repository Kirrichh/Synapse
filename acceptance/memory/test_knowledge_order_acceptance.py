"""One consolidation of several sessions folds readings in the order the world was read (review DEEP-3).

Two learning sessions read the basic plan's price from the catalog — 20, then,
after the catalog changed, 25 — and neither consolidates: the court folds both
at one explicit consolidation. Whatever the runs are named, memory holds 25,
the later reading: the order is the gateway's, never the order of run names.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _catalog as catalog

DEFERRED = catalog.LEARN.replace("  consolidate during dream\n", "")
MAINTENANCE = '''memory palace "catalog" { rooms { semantic } consolidate during dream }
consolidate palace
print("maintained")'''


def _read(world, run_id, value):
    catalog.publish(world, "catalog", "basic", "monthly_price", value, text=f"Basic monthly_price {value}",
                    start="2026-01-01")
    world.run(DEFERRED, run_id, {"task_name": run_id, "plan_id": "basic", "source_tool": "catalog_entry",
                                 "confirm_now": False, "subject": "basic"})


@pytest.mark.parametrize("first, second", [("a-first", "z-second"), ("z-first", "a-second")])
def test_one_consolidation_holds_the_later_reading_whatever_the_runs_are_named(tmp_path, first, second):
    world = catalog.world(tmp_path)
    _read(world, first, 20)
    _read(world, second, 25)
    assert world.reports() == []  # Nothing consolidated yet.
    world.run(MAINTENANCE, "maintenance", {})
    report, = world.reports()
    assert len(report["knowledge"]["corrections"]) == 1
    current = [entry["record"]["value"] for entry in world.owner().state()["knowledge"]["versions"].values()
               if entry["known_until"] is None]
    assert current == [25]
