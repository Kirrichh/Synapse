"""A palace admission is part of the record its session is replayed and recovered from (review M5).

The admission's decision is a recorded event: the court's replay check of the
session re-executes the program and verifies it, byte for byte, like every
other event, and a session recovered after a crash consumes the recorded
decision instead of deciding again:

* a session that admits the card's command, and one whose admission abstains
  (a poisoned command its check refutes), are verified by the court's replay;
  the same program without an admission is the control;
* a session stopped after its admission and recovered keeps exactly the
  admission it recorded, and acts once.
"""
from __future__ import annotations

import os
import signal
import time

import pytest

from acceptance.memory import _runbook as runbook
from acceptance.memory._world import MemoryWorld, answer, tool

ADMIT_THEN_WAIT = '''memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let deciding = {"segment": "decide", "intent": "admit the restart command", "element_part": "ops", "requirement": {"kind": "execute", "tool": "runbook", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "admit then wait", "segments": [deciding]})
context "decide" {
  let card = tool("kb_card", {"id": "pg-steady"})
  let content = hypothesis({"aspect": "content", "subject": "postgresql", "statement": {"restart_command": card.payload.command}, "scope": "host-a", "source": {"tool": "kb_card", "args": {"id": "pg-steady"}}, "check": {"tool": "runbook", "args": {"service": "postgresql"}}})
  let checked = probe(content)
  let fact = {"id": "pg-steady", "entity": "postgresql", "attribute": "restart_command", "value": card.payload.command, "source": "kb:wiki", "hypothesis": content.id, "content": "postgresql restart command"}
  let admitted = admit([fact], {"entity": "postgresql", "attribute": "restart_command", "keys": ["postgresql", "restart", "command"], "scope": "host-a"})
  let seen = tool("clock", {"step": "after_admission"})
  let waited = tool("gate", {"run": task_name})
}
print("done")
'''


def _replayed(world, run_id):
    report = world.reports()[-1]
    return report["replay"][run_id]["status"], report["integrity"]["ok"]


@pytest.mark.parametrize("card, decision, restarted", [
    ("pg-steady", "admitted", [runbook.TRUE]),
    ("pg-bad", "abstained", []),
])
def test_a_session_with_an_admission_is_verified_by_the_courts_replay(tmp_path, card, decision, restarted):
    world = runbook.world(tmp_path)
    world.run(runbook.PROGRAM, "restart", runbook.inputs("restart", card))
    recorded, = world.events("restart", "memory_admission")
    assert recorded["decision"] == decision
    assert _replayed(world, "restart") == ("replay_verified", True)
    assert runbook.restarts(world) == restarted


def test_control_the_same_program_without_an_admission_is_verified_alike(tmp_path):
    world = runbook.world(tmp_path)
    source = "\n".join(line for line in runbook.PROGRAM.splitlines() if "admit(" not in line) + "\n"
    world.run(source, "restart", runbook.inputs("restart", "pg-steady"))
    assert world.events("restart", "memory_admission") == []
    assert _replayed(world, "restart") == ("replay_verified", True)
    assert runbook.restarts(world) == [runbook.TRUE]


def _waiting_world(root) -> MemoryWorld:
    tools = runbook.tools() + [
        tool("clock", "clock:ops", [answer({"ok": True})], server="clock", contract={"observation": True, "idempotent": True}),
        tool("gate", "clock:ops", [answer({"ok": True}, delay=60, until="continue.signal")], server="gate",
             contract={"observation": True, "idempotent": True})]
    return MemoryWorld(root, tools, provenance={**runbook.provenance(), "clock:ops": {"ancestors": []}})


def test_a_recovered_session_keeps_the_admission_it_recorded(tmp_path):
    world = _waiting_world(tmp_path)
    process = world.start(ADMIT_THEN_WAIT, "waiting", {"task_name": "waiting"})
    deadline = time.monotonic() + 180
    while not [call for call in world.world()["calls"] if call["tool"] == "gate"]:
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the session did not reach the gate"
        time.sleep(0.2)
    os.killpg(process.pid, signal.SIGKILL)  # After the admission and the observation that followed it.
    process.wait(60)
    (tmp_path / "continue.signal").touch()
    code, payload, stderr = world.resume("waiting")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    admission, = world.events("waiting", "memory_admission")
    assert admission["decision"] == "admitted"
    assert [call["args"] for call in world.world()["calls"] if call["tool"] == "clock"] == [{"step": "after_admission"}]
    assert _replayed(world, "waiting") == ("replay_verified", True)
