"""Knowledge enters memory whichever window states it, and a correction reaches the statuses decided before it
(review M3, M4).

Real sessions learn a plan's price from the catalog and state it with ``know``:

* a statement made after the session's palace was consolidated is kept by the
  consolidation that covers it, once, as one made inside the window (M3);
* a correction of the catalog (the price changes, the second session states
  the new one) returns to ``provisional`` the status checked against the old
  answer — when both sessions are folded in one window as when the correction
  comes a window later — while a check made of the corrected value after it
  stands (M4).
"""
from __future__ import annotations

import pytest

from acceptance.memory import _catalog as catalog

MAINTENANCE = 'memory palace "catalog" {\n  rooms { semantic }\n  consolidate during dream\n}\nconsolidate palace\n'
LEARN_LATE = '''memory palace "catalog" {
  rooms { semantic }
  consolidate during dream
}
let reading = {"segment": "read", "intent": "read a source", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": "learn", "goal": "learn a price", "segments": [reading]})
let entry = {}
context "read" {
  entry = tool("catalog_entry", {"plan": "basic"})
}
BETWEEN
let stated = know({"subject": "basic", "property": entry.payload.property, "value": entry.payload.value, "polarity": true, "valid": {"from": entry.payload.start}, "text": entry.payload.text, "source": {"tool": "catalog_entry", "args": {"plan": "basic"}}})
print("learned")
'''


def _versions(world):
    return sorted(world.owner().state()["knowledge"]["versions"])


@pytest.mark.parametrize("between", ["consolidate palace", ""], ids=["after-the-consolidation", "inside-the-window"])
def test_a_statement_is_kept_once_whichever_window_states_it(tmp_path, between):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "monthly_price", 20, text="basic plan monthly price")
    world.run(LEARN_LATE.replace("BETWEEN", between), "learn", {})
    assert len(world.events("learn", "knowledge_declared")) == 1
    kept = _versions(world)
    assert len(kept) == 1
    world.run(MAINTENANCE, "maintenance", {})
    assert _versions(world) == kept


def _corrected(world, *, deferred, check_new):
    source = catalog.LEARN.replace("  consolidate during dream\n", "") if deferred else catalog.LEARN
    for run_id, value, check in (("first", 20, True), ("second", 25, check_new)):
        catalog.publish(world, "catalog", "basic", "monthly_price", value, text=f"basic monthly price {value}",
                        start="2026-01-01")
        catalog.publish(world, "billing", "basic", "monthly_price", value)
        world.run(source, run_id, {"task_name": run_id, "plan_id": "basic", "source_tool": "catalog_entry",
                                   "confirm_now": check, "subject": "basic"})
    world.run(MAINTENANCE, "maintenance", {})
    return {entry["record"]["statement"]["monthly_price"]: entry
            for entry in world.owner().state()["hypotheses"].values()}


@pytest.mark.parametrize("deferred", [True, False], ids=["one-window", "a-later-window"])
def test_a_correction_returns_the_status_checked_before_it_to_provisional(tmp_path, deferred):
    world = catalog.world(tmp_path)
    held = _corrected(world, deferred=deferred, check_new=False)
    old, = held.values()
    assert old["status"] == "provisional" and old["reason"].startswith("source_corrected:")
    revised = [revision for report in world.reports() for revision in report["knowledge"]["revisions"]
               if revision["hypotheses"]]
    assert [revision["hypotheses"] for revision in revised] == [[old["record"]["id"]]]


def test_control_a_check_of_the_corrected_value_made_after_it_stands(tmp_path):
    world = catalog.world(tmp_path)
    held = _corrected(world, deferred=True, check_new=True)
    assert held[25]["status"] == "confirmed"
    assert held[20]["status"] == "provisional" and held[20]["reason"].startswith("source_corrected:")
