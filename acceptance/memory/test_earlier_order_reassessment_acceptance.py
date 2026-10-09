"""Memory an earlier court folded in the order its sessions finished is reassessed into the world's order (recheck
of 556624d: memory decided under court policy v5).

The earlier court (v5) folded a window's statements in the order sessions finished and kept no order of
statements: a reading made before another, published after it, displaced it. That court ran here, in this
process, through the canonical entry, with that order in place of the current one: a session read the starter
plan's price, 20, and waited at a gate; the catalog then said 30, a second session read it, and a third read 30
again, stated a claim read from that answer and checked it — billing agreed; then the waiting session finished,
and its 20 displaced 30.

Under the current court, in its own processes:

* a session is refused before anything runs: the memory must be reassessed first;
* ``synapse memory reassess`` calls no tool and restores the world's order from the declarations every session
  consolidated: 30 holds the slot again from the reassessment's window and 20 is history, while every earlier
  window answers as it did; the report names the correction;
* the claim read from 30 and checked after it stays confirmed: the earlier court closed 30 by a reading the order
  places before it, which is no correction;
* reassessed again, memory does not change, and a session runs.
"""
from __future__ import annotations

import os
import signal
import time

from acceptance.memory import _catalog as catalog
from acceptance.memory._world import MemoryWorld, answer, tool
from synapse.memory_consolidation.knowledge.timeline import held_period

EARLIER_POLICY = "synapse.memory.court-policy/v5"
WAITING = '''memory palace "catalog" { rooms { semantic } consolidate during dream }
let reading = {"segment": "read", "intent": "read what the catalog states", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}}
let waiting = {"segment": "wait", "intent": "wait at the gate", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "gate", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "learn the price", "segments": [reading, waiting]})
context "read" {
  let entry = tool("catalog_entry", {"plan": "starter"})
  let stated = know({"subject": "starter", "property": entry.payload.property, "value": entry.payload.value, "polarity": entry.payload.polarity, "valid": {"from": entry.payload.start}, "text": entry.payload.text, "source": {"tool": "catalog_entry", "args": {"plan": "starter"}}})
}
context "wait" {
  let waited = tool("gate", {"run": task_name})
}
print("learned")
'''


def _world(root) -> MemoryWorld:
    gate = tool("gate", "clock:ops", [answer({"ok": True}, delay=60, until="continue.signal")], server="gate",
                contract={"observation": True, "idempotent": True})
    return MemoryWorld(root, catalog.tools() + [gate], provenance={**catalog.provenance(),
                                                                    "clock:ops": {"ancestors": []}},
                       knowledge={"embedder": {"tool": "embed", "version": "concepts-v1"},
                                  "properties": catalog.PROPERTIES, "budget": 3})


def _says(world, price):
    for room in ("catalog", "billing"):
        catalog.publish(world, room, "starter", "monthly_price", price, text=f"starter monthly price {price}")


def _learning(run_id, check=False):
    return {"task_name": run_id, "plan_id": "starter", "source_tool": "catalog_entry", "confirm_now": check,
            "subject": "starter"}


def _earlier(patch):
    """The earlier court in this process: its policy's version, statements folded in the order sessions finished,
    and no order of statements kept in its reports."""
    from synapse.memory_consolidation import policy
    from synapse.memory_consolidation.court import apply, knowledge

    patch.setattr(policy, "POLICY_V6", EARLIER_POLICY)
    patch.setattr(knowledge, "_last_stated", lambda group, record: None)
    patch.setattr(apply, "additions", lambda held, stated: {})


def _held(world, window=None):
    versions = world.owner().state()["knowledge"]["versions"].values()
    return sorted(version["record"]["value"] for version in versions if held_period(version, window) is not None)


def _version(world, price):
    found, = [version for version in world.owner().state()["knowledge"]["versions"].values()
              if version["record"]["value"] == price]
    return found


def test_memory_folded_in_the_order_sessions_finished_is_reassessed_into_the_worlds_order(tmp_path, monkeypatch):
    from synapse import cli

    world = _world(tmp_path)
    _says(world, 20)
    delayed = world.start(WAITING, "delayed", {"task_name": "delayed"})
    deadline = time.monotonic() + 180
    while {"run": "delayed"} not in world.calls("gate"):
        assert delayed.poll() is None, delayed.communicate()
        assert time.monotonic() < deadline, "the session did not reach the gate"
        time.sleep(0.2)
    os.killpg(delayed.pid, signal.SIGKILL)  # It read 20; nothing consolidated it yet.
    delayed.wait(60)
    _says(world, 30)
    with monkeypatch.context() as earlier:
        _earlier(earlier)
        for run_id, check in (("newer", False), ("checker", True)):
            assert cli.main([str(item) for item in world._run_arguments(catalog.LEARN, run_id,
                                                                        _learning(run_id, check))]) == 0
        (world.root / "continue.signal").touch()
        assert cli.main(["resume", "--state-file", str(world.runs / "delayed.json")]) == 0
    assert {report["policy"]["policy"] for report in world.reports()} == {EARLIER_POLICY}
    assert _held(world) == [20]  # The reading made first, published last, displaced 30.
    claim_id, = world.owner().state()["hypotheses"]
    assert world.owner().state()["hypotheses"][claim_id]["status"] == "confirmed"
    windows = len(world.reports())
    before = [_held(world, window) for window in range(1, windows + 1)]

    # The current court refuses to run on memory it has not reassessed.
    calls = len(world.world()["calls"])
    code, _, _ = world.attempt(catalog.LEARN, "early", _learning("early"), configuration=world.configuration_path)
    assert code != 0 and len(world.world()["calls"]) == calls

    code, result, stderr = world.memory("reassess")
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    assert len(world.world()["calls"]) == calls  # Decided from the record: no tool was called.
    late, thirty = _version(world, 20), _version(world, 30)
    assert _held(world) == [30] and late["corrected_by"] == thirty["record"]["id"]
    assert [_held(world, window) for window in range(1, windows + 1)] == before
    assert world.reports()[-1]["knowledge"]["corrections"] == [
        {"statement": late["record"]["id"], "corrected_by": thirty["record"]["id"], "slot": late["slot"]}]
    assert world.owner().state()["hypotheses"][claim_id]["status"] == "confirmed"

    state, reassessed = world.owner().state(), len(world.reports())
    code, result, stderr = world.memory("reassess")
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    again = world.owner().state()
    assert all(report["knowledge"]["corrections"] == [] for report in world.reports()[reassessed:])
    assert {key: again[key] for key in ("hypotheses",)} == {key: state[key] for key in ("hypotheses",)}
    assert {key: again["knowledge"][key] for key in ("versions", "stated")} == {
        key: state["knowledge"][key] for key in ("versions", "stated")}
    world.run(catalog.LEARN, "later", _learning("later"))
    assert _held(world) == [30]
