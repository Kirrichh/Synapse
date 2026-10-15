"""The stock-count scenario of the retention acceptance files.

Every session counts one item inside a planned segment without an anchor —
with no advisor its verdict stays uncertain, so its case is of medium
significance — and first notes the task in an operations log off the plan, a
case of low significance. A declared retention policy with windows of one lets
retention act on each case one consolidation after it was made.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "inventory", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "count", "intent": "count the stock of one item", "element_part": "stock", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "count the stock", "segments": [seg]})
let note = tool("ops_log", {"note": task_name})
context "count" {
  let counted = tool("inventory", {"item": item})
}
print("counted")
'''

POLICY = {"n_medium_windows": 1, "n_low_windows": 1, "k_rollup": 1}


def tools():
    inventory = tool("inventory", "stock:main", [answer({"ok": True, "count": 7})], server="stock")
    log = tool("ops_log", "log:ops", [answer({"ok": True, "noted": True})], server="log")
    return [inventory, log]


def world(root, **options) -> MemoryWorld:
    parameters = {**POLICY, **options.pop("parameters", {})}
    return MemoryWorld(root, tools(), provenance={"stock:main": {"ancestors": []}, "log:ops": {"ancestors": []}},
                       parameters=parameters, **options)


def count(world: MemoryWorld, run_id: str) -> None:
    world.run(PROGRAM, run_id, {"task_name": run_id, "item": run_id})


def cases(world: MemoryWorld, run_id: str) -> dict[str, dict]:
    """The quanta of one session's cases, by their tier at birth."""
    report = next(report for report in world.reports() if any(
        entry["run_id"] == run_id for entry in report["window"]["sessions"]))
    return {entry["tier"]: entry for entry in report["apply"]["quanta"].values()
            if entry["replay_ref"] and entry["replay_ref"]["run_id"] == run_id}
