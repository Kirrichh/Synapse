"""An action is admitted only on a basis memory still holds at the moment it is sent (review M6).

A session reads a restart command from a card, decides the content hypothesis
— by its own check, or from the court's record of an earlier session — and
waits at a gate before the restart that requires the hypothesis. While it
waits, memory learns something about that basis:

* another session's later check refutes it, or the operator forgets the case
  holding the check it was decided on: the restart is refused before any
  effect, whichever way the session read the decision;
* nothing changes (control): the restart runs once;
* the session checks again after the refutation, or after the forget, and the
  runbook agrees: a decision made after the change stands and the restart runs;
* an exam relies on its fixed snapshot: nothing memory learns later changes
  what it relied on;
* a restart already sent is never undone by a later refutation: the report
  says the status was revoked after the action's admission, and a session
  recovered after a crash does not send it again.

The environment's own record counts the restarts.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from acceptance.memory._world import REPOSITORY, MemoryWorld, answer, stateful, tool

COMMAND = "pg_ctl restart -m fast"
PROGRAM = '''memory palace "ops" {
  rooms { episodic procedural }
  consolidate during dream
}
let checking = {"segment": "check", "intent": "check the command", "element_part": "ops", "requirement": {"kind": "execute", "tool": "runbook", "admissible_err": [], "allowed_alternatives": []}}
let waiting = {"segment": "wait", "intent": "wait at the gate", "element_part": "ops", "requirement": {"kind": "execute", "tool": "gate", "admissible_err": [], "allowed_alternatives": []}}
let acting = {"segment": "act", "intent": "restart the service", "element_part": "ops", "requirement": {"kind": "execute", "tool": "restart_service", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "check then act", "segments": [checking, waiting, acting]})
let h = {}
context "check" {
  let card = tool("kb_card", {"id": "pg"})
  h = hypothesis({"aspect": "content", "subject": "postgresql", "statement": {"restart_command": card.payload.command}, "scope": "host-a", "source": {"tool": "kb_card", "args": {"id": "pg"}}, "check": {"tool": "runbook", "args": {"service": "postgresql"}}})
  if fresh == true {
    let checked = probe(h)
  }
  if fresh == false {
    if established(h) == false {
      let checked = probe(h)
    }
  }
}
if consolidate_first == true {
  consolidate palace
}
context "wait" {
  if pause == true {
    let waited = tool("gate", {"run": task_name})
  }
  if recheck == true {
    let again = probe(h)
  }
}
context "act" {
  if act == true {
    try {
      let done = tool("restart_service", {"command": "pg_ctl restart -m fast"}, {"requires": [h]})
    } catch (ACTION_FAILED as refused) {
      print("refused")
    }
  }
  if hold == true {
    let held = tool("gate", {"run": task_name, "after": "act"})
  }
}
print("done")
'''


def _world(root) -> MemoryWorld:
    tools = [
        tool("kb_card", "kb:wiki", [answer({"ok": True, "command": COMMAND})], server="kb"),
        # The runbook answers what the environment holds now (``_runbook_says``).
        tool("runbook", "runbook:ops", [stateful({"ok": True}, act={"read": "runbook", "key": "service"})],
             server="checker", contract={"verifies": {"subject": {"request": "service"}, "scope": {"value": "host-a"}}}),
        tool("gate", "clock:ops", [answer({"ok": True}, delay=60, until="continue.signal")], server="gate",
             contract={"observation": True, "idempotent": True}),
        tool("restart_service", "ops:host-a", [answer({"ok": True, "restarted": True}, effect="restarted")],
             server="ops", contract={"requires_established": True}),
    ]
    world = MemoryWorld(root, tools, provenance={source: {"ancestors": []}
                                                 for source in ("kb:wiki", "runbook:ops", "clock:ops", "ops:host-a")})
    _runbook_says(world, COMMAND)
    return world


def _runbook_says(world, command):
    with open(world.world_path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(world.world_path.read_text()) if world.world_path.exists() else {"calls": [], "effects": []}
        state.setdefault("objects", {}).setdefault("runbook", {})["postgresql"] = {
            "service": "postgresql", "restart_command": command}
        world.world_path.write_text(json.dumps(state, sort_keys=True))


def _inputs(name, **options):
    flags = {"fresh": True, "pause": False, "act": False, "consolidate_first": False, "recheck": False, "hold": False}
    return {"task_name": name, **flags, **options}


def _restarts(world):
    return [item for item in world.world()["effects"] if item["tool"] == "restart_service"]


def _at_gate(world, process, run_id, after=None, seconds=180):
    wanted = {"run": run_id} if after is None else {"run": run_id, "after": after}
    deadline = time.monotonic() + seconds
    while wanted not in [call["args"] for call in world.world()["calls"] if call["tool"] == "gate"]:
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the session did not reach the gate"
        time.sleep(0.2)


def _released(root, process):
    (root / "continue.signal").touch()
    stdout, stderr = process.communicate(timeout=600)
    assert process.returncode == 0, (stdout, stderr)
    return stdout


def _refuted_by_another_session(world):
    _runbook_says(world, "pg_ctl kill")
    world.run(PROGRAM, "reviser", _inputs("reviser"))


def _forgotten(world):
    status, = world.owner().state()["hypotheses"].values()
    code, payload, stderr = world.memory("forget", "--quantum", status["basis"], "--reason", "operator removal",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)


@pytest.mark.parametrize("change", ["refuted", "forgotten"])
@pytest.mark.parametrize("fresh", [True, False], ids=["own-check", "court-record"])
def test_a_basis_changed_while_waiting_refuses_the_action_before_its_effect(tmp_path, change, fresh):
    world = _world(tmp_path)
    if not fresh:
        world.run(PROGRAM, "seed", _inputs("seed"))
    waiting = world.start(PROGRAM, "waiting", _inputs("waiting", fresh=fresh, pause=True, act=True,
                                                      consolidate_first=fresh and change == "forgotten"))
    _at_gate(world, waiting, "waiting")
    (_refuted_by_another_session if change == "refuted" else _forgotten)(world)
    assert "refused" in _released(tmp_path, waiting)
    assert _restarts(world) == []
    refused, = [event for event in world.events("waiting", "external_action")
                if event["request"]["tool"] == "restart_service"]
    assert "a required basis changed since it was read" in refused["outcome"]["view"]["reason"]


@pytest.mark.parametrize("fresh", [True, False], ids=["own-check", "court-record"])
def test_control_an_unchanged_basis_lets_the_action_run_once(tmp_path, fresh):
    world = _world(tmp_path)
    if not fresh:
        world.run(PROGRAM, "seed", _inputs("seed"))
    waiting = world.start(PROGRAM, "waiting", _inputs("waiting", fresh=fresh, pause=True, act=True))
    _at_gate(world, waiting, "waiting")
    _released(tmp_path, waiting)
    assert len(_restarts(world)) == 1


def test_a_check_made_after_the_refutation_decides_the_action(tmp_path):
    world = _world(tmp_path)
    waiting = world.start(PROGRAM, "waiting", _inputs("waiting", pause=True, act=True, recheck=True))
    _at_gate(world, waiting, "waiting")
    _refuted_by_another_session(world)
    _runbook_says(world, COMMAND)  # The runbook agrees with the card again before the session checks anew.
    _released(tmp_path, waiting)
    assert len(_restarts(world)) == 1


def test_a_check_made_after_the_forget_decides_the_action(tmp_path):
    world = _world(tmp_path)
    waiting = world.start(PROGRAM, "waiting", _inputs("waiting", pause=True, act=True, consolidate_first=True,
                                                      recheck=True))
    _at_gate(world, waiting, "waiting")
    _forgotten(world)
    _released(tmp_path, waiting)
    assert len(_restarts(world)) == 1


def test_an_exam_relies_on_its_fixed_snapshot_whatever_memory_learns_later(tmp_path):
    world = _world(tmp_path)
    world.run(PROGRAM, "seed", _inputs("seed"))
    snapshot = world.reports()[-1]["snapshot_boundary_after"]
    arguments = world._run_arguments(PROGRAM, "exam", _inputs("exam", fresh=False, pause=True, act=True),
                                     exam=("B", snapshot))
    exam = subprocess.Popen([sys.executable, "-B", "-m", "synapse", *map(str, arguments)], cwd=REPOSITORY,
                            env={**os.environ, "PYTHONPATH": str(REPOSITORY)}, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    _at_gate(world, exam, "exam")
    _refuted_by_another_session(world)
    _released(tmp_path, exam)
    assert len(_restarts(world)) == 1
    assert world.events("exam", "hypothesis_reused")[0]["reason"] == "court_record"


def test_an_action_already_sent_stays_sent_and_is_never_sent_again(tmp_path):
    world = _world(tmp_path)
    acted = world.start(PROGRAM, "acted", _inputs("acted", act=True, hold=True))
    _at_gate(world, acted, "acted", after="act")
    os.killpg(acted.pid, signal.SIGKILL)  # After the restart and the gate that followed it.
    acted.wait(60)
    _refuted_by_another_session(world)
    (tmp_path / "continue.signal").touch()
    code, payload, stderr = world.resume("acted")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert len(_restarts(world)) == 1
    relied = [item for report in world.reports() for item in report["hypotheses"]["relied"]
              if item["run_id"] == "acted"]
    assert [entry["revoked"] for item in relied for entry in item["hypotheses"]] == ["after_admission"]
    assert all(item["performed"] for item in relied)
