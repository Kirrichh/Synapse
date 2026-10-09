"""What a source stated last holds memory, whatever session finished last (recheck of 556624d).

Learning sessions read the starter plan's price from the catalog and state what they read; a session may wait at
a gate after its reading, so it consolidates after sessions that read later:

* a reading made before the one memory holds — the session finished last — is kept as history and never held:
  closed in the very window it became known, while every earlier window answers as it did, and its report adds
  that one statement to the order memory keeps; finishing in the world's order gives the same memory;
* the catalog stated 20 again after a delayed reading of 25: 20 stays held (a restatement adds no confidence,
  but it keeps what memory holds current);
* the catalog really returned to 20 after 30 (control): 20 is held again;
* a claim read from the answer the delayed reading superseded, and checked before it, is provisional at the
  delayed reading's place — as if the readings had been consolidated together;
* a session killed after its reading and recovered after a later one holds nothing either;
* reassessing the memory keeps the order, and again changes nothing.
"""
from __future__ import annotations

import os
import signal
import time

import pytest

from acceptance.memory import _catalog as catalog
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''memory palace "catalog" { rooms { semantic } consolidate during dream }
let reading = {"segment": "read", "intent": "read what the catalog states", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}}
let waiting = {"segment": "wait", "intent": "wait at the gate", "element_part": "catalog", "requirement": {"kind": "execute", "tool": "gate", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "learn the price", "segments": [reading, waiting]})
context "read" {
  let entry = tool("catalog_entry", {"plan": "starter"})
  let stated = know({"subject": "starter", "property": entry.payload.property, "value": entry.payload.value, "polarity": entry.payload.polarity, "valid": {"from": entry.payload.start}, "text": entry.payload.text, "source": {"tool": "catalog_entry", "args": {"plan": "starter"}}})
}
context "wait" {
  if pause == true {
    let waited = tool("gate", {"run": task_name})
  }
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


def _catalog_says(world, price):
    catalog.publish(world, "catalog", "starter", "monthly_price", price, text=f"starter monthly price {price}")
    catalog.publish(world, "billing", "starter", "monthly_price", price, text=f"starter monthly price {price}")


def _read(world, run_id, price):
    _catalog_says(world, price)
    world.run(PROGRAM, run_id, {"task_name": run_id, "pause": False})


def _delayed(world, run_id, price):
    """A session that read ``price`` and waits at the gate before it consolidates."""
    _catalog_says(world, price)
    process = world.start(PROGRAM, run_id, {"task_name": run_id, "pause": True})
    deadline = time.monotonic() + 180
    while {"run": run_id} not in world.calls("gate"):
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the session did not reach the gate"
        time.sleep(0.2)
    return process


def _finished(world, process):
    (world.root / "continue.signal").touch()
    stdout, stderr = process.communicate(timeout=600)
    assert process.returncode == 0, (stdout, stderr)


def _held(world, window=None):
    """The prices memory holds now, or held as it knew them in ``window``."""
    from synapse.memory_consolidation.knowledge.timeline import held_period

    versions = world.owner().state()["knowledge"]["versions"].values()
    return sorted(version["record"]["value"] for version in versions if held_period(version, window) is not None)


def _version(world, price):
    found, = [version for version in world.owner().state()["knowledge"]["versions"].values()
              if version["record"]["value"] == price]
    return found


@pytest.mark.parametrize("finishing", ["in the world's order", "the earlier reading last"])
def test_a_reading_made_before_the_one_memory_holds_is_history(tmp_path, finishing):
    world = _world(tmp_path)
    if finishing == "in the world's order":
        _read(world, "earlier", 20)
        _read(world, "later", 30)
        assert _held(world) == [30]
        return
    delayed = _delayed(world, "earlier", 20)
    _read(world, "later", 30)
    before = [_held(world, window) for window in range(1, len(world.reports()) + 1)]
    _finished(world, delayed)
    assert _held(world) == [30]
    late = _version(world, 20)
    window = len(world.reports())
    assert (late["known_from"], late["known_until"]) == (window, window)
    assert late["corrected_by"] == _version(world, 30)["record"]["id"]
    # Every earlier window answers as it did; the window that learned the earlier reading holds 30.
    assert [_held(world, index) for index in range(1, window)] == before and _held(world, window) == [30]
    report = world.reports()[-1]
    assert report["knowledge"]["declared"][0]["late"] and report["knowledge"]["corrections"][0]["late"]
    # The report adds the earlier reading to the order memory keeps, never the order again.
    added, = report["apply"]["knowledge"]["stated"].values()
    assert [statement[1] for statement in added["statements"]] == [late["record"]["id"]]


def test_a_restatement_keeps_what_memory_holds_against_a_delayed_reading(tmp_path):
    world = _world(tmp_path)
    _read(world, "seed", 20)
    delayed = _delayed(world, "delayed", 25)
    _read(world, "restated", 20)  # The catalog states 20 again, after the delayed reading.
    _finished(world, delayed)
    assert _held(world) == [20]
    assert _version(world, 25)["known_until"] == _version(world, 25)["known_from"]


def test_control_a_source_that_returned_to_an_earlier_answer_holds_it_again(tmp_path):
    world = _world(tmp_path)
    _read(world, "first", 20)
    _read(world, "second", 30)
    _read(world, "back", 20)
    assert _held(world) == [20]


def test_a_claim_read_from_the_answer_a_delayed_reading_superseded_is_checked_again(tmp_path):
    world = _world(tmp_path)
    _catalog_says(world, 20)
    catalog.learn(world, "reader", "starter", check=True)  # Reads 20, states the claim, billing confirms it.
    probed, = world.events("reader", "hypothesis_probed")
    assert probed["status"] == "confirmed"
    delayed = _delayed(world, "delayed", 25)
    _read(world, "restated", 20)
    _finished(world, delayed)
    reading, = [event for event in world.events("delayed", "external_action")
                if event["request"]["tool"] == "catalog_entry"]
    entry = world.owner().state()["hypotheses"][probed["hypothesis"]]
    assert (entry["status"], entry["at"]) == ("provisional", reading["outcome"]["ref"]["gw_seq"])
    assert entry["reason"].startswith("source_corrected:")
    assert _held(world) == [20]  # What the catalog stated last: the delayed reading only revised the claim.


def test_a_session_recovered_after_a_later_reading_holds_nothing_either(tmp_path):
    world = _world(tmp_path)
    delayed = _delayed(world, "earlier", 20)
    os.killpg(delayed.pid, signal.SIGKILL)  # After its reading, before it consolidated.
    delayed.wait(60)
    _read(world, "later", 30)
    (world.root / "continue.signal").touch()
    code, payload, stderr = world.resume("earlier")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert _held(world) == [30]


def test_reassessing_keeps_the_order_and_again_changes_nothing(tmp_path):
    world = _world(tmp_path)
    delayed = _delayed(world, "earlier", 20)
    _read(world, "later", 30)
    _finished(world, delayed)
    stated = world.owner().state()["knowledge"]["stated"]
    for _ in range(2):
        code, result, stderr = world.memory("reassess")
        assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
        assert _held(world) == [30] and world.owner().state()["knowledge"]["stated"] == stated
        assert world.reports()[-1]["knowledge"]["corrections"] == []
