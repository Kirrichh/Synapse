"""The fast path obeys the same outcome rules as the slow path (refinement §8).

A declared habit reacts to a refused checkpoint on the fast path. Its body goes
through the same gateway, so the same rules decide the segment: a declared
retry of the same operation settles it; a successful note in a log is a foreign
success that settles nothing; a retry that changes the essential arguments is
refused before any effect. The tool server's world is the checker's truth.
"""
from __future__ import annotations

import json

from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
habit "recover_checkpoint" from pattern {
  activate when { event "external_error" context "maintenance" tool == "checkpoint" op_err == "BUSY" }
  priority high
  body {
    if remedy == "retry" {
      let again = tool("checkpoint", {"db": db}, {"retry_of": 1})
    }
    if remedy == "log" {
      let noted = tool("ops_log", {"note": db})
    }
    if remedy == "changed" {
      let again = tool("checkpoint", {"db": db, "quick": true}, {"retry_of": 1})
    }
  }
}
let req = {"kind": "execute", "tool": "checkpoint", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "maintenance", "intent": "apply the checkpoint", "element_part": "checkpoints", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "apply the checkpoint", "segments": [seg]})
context "maintenance" {
  try {
    let done = tool("checkpoint", {"db": db})
  } catch (ACTION_FAILED as busy) {
    print("not recovered")
  }
}
print("done")
'''

BUSY = {"payload": {"ok": False, "err": "BUSY"}, "effect": "partial"}


def _tools():
    checkpoint = tool("checkpoint", "db:primary", [
        answer({"ok": True, "applied": True}, when={"db": db}, sequence=[BUSY], effect="applied")
        for db in ("retry", "log", "changed")], server="db", contract={"effect_on_err": {"BUSY": "partial"}, "repeatable_on_partial": True})
    log = tool("ops_log", "log:ops", [answer({"ok": True, "noted": True})], server="log")
    return [checkpoint, log]


def _verdict(world, run_id):
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == run_id]
    return verdict


def _gateway(world, run_id):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record for record in map(json.loads, journal.read_text().splitlines())
            if record["body"].get("run_id") == run_id]


def _effects(world, db):
    return [item["effect"] for item in world.world()["effects"] if item["args"].get("db") == db]


def test_a_declared_habit_body_is_judged_by_the_slow_path_rules(tmp_path):
    world = MemoryWorld(tmp_path, _tools(), provenance={"db:primary": {"ancestors": []}, "log:ops": {"ancestors": []}})

    # A declared retry of the same operation, on the fast path: one operation, settled.
    world.run(PROGRAM, "retry", {"task_name": "retry", "remedy": "retry", "db": "retry"})
    fired, = world.events("retry", "habit_activated")
    assert fired["layer"] == 1 and world.events("retry", "slow_path_used") == []
    assert _verdict(world, "retry")["verdict"] != "failed"
    started = [record["body"] for record in _gateway(world, "retry") if record["kind"] == "STARTED"]
    assert [(item["path"], item["op_seq"], item["retry_of"], item["admitted"]) for item in started] == [
        ("slow", 1, None, None), ("habit", 1, 1, True)]
    assert _effects(world, "retry") == ["partial", "applied"]

    # A successful note in a log is a foreign success: the checkpoint stays refused.
    world.run(PROGRAM, "log", {"task_name": "log", "remedy": "log", "db": "log"})
    assert len(world.events("log", "habit_activated")) == 1 and len(world.events("log", "slow_path_used")) == 1
    verdict = _verdict(world, "log")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert _effects(world, "log") == ["partial"] and world.calls("ops_log") == [{"note": "log"}]

    # A retry that changes the essential arguments is refused before any effect, on this path too.
    world.run(PROGRAM, "changed", {"task_name": "changed", "remedy": "changed", "db": "changed"})
    verdict = _verdict(world, "changed")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    rejected, = [record["body"] for record in _gateway(world, "changed") if record["kind"] == "REJECTED"]
    assert rejected["path"] == "habit" and rejected["reason"] == \
        "a declared retry changes the tool or its essential arguments"
    assert world.calls("checkpoint").count({"db": "changed"}) == 1 and _effects(world, "changed") == ["partial"]
