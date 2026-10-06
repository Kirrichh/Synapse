"""A basis read from the court is read again before the effect (review §8.2, after Kubernetes resourceVersion).

A first session confirms the card's restart command by the runbook; the court
records the confirmation and the restart runs. Later sessions reuse it:

* a session whose basis nothing changed meanwhile restarts on the reused
  confirmation (the positive control);
* a session pauses between reading its basis and acting; meanwhile another
  session checks the same claim again, the runbook can no longer determine it,
  and the court records the claim as provisional — the latest check decides.
  When the paused session resumes, its restart is refused before any effect:
  the basis it read changed since it was read. The revising session itself
  never acts on a status it saw go provisional.

The environment's own record shows exactly the restarts that were allowed.
"""
from __future__ import annotations

import json
import time

from acceptance.memory._world import MemoryWorld, answer, tool

COMMAND = "pg_ctl restart -m fast"
SIGNAL = "revised.signal"  # The paused session's gate opens once the revision is applied.
PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
context "restart" {
  let card = tool("kb_card", {"id": "pg"})
  let origin = {"tool": "kb_card", "args": {"id": "pg"}}
  let content = hypothesis({"aspect": "content", "subject": "postgresql", "statement": {"restart_command": card.payload.command}, "scope": "host-a", "source": origin, "check": {"tool": "runbook", "args": {"service": "postgresql"}}})
  if established(content) == false {
    let c = probe(content)
  }
  if recheck == true {
    let again = probe(content)
  }
  if pause == true {
    let waited = tool("gate", {"run": task_name})
  }
  try {
    let done = tool("restart_service", {"command": card.payload.command}, {"requires": [content]})
  } catch (ACTION_FAILED as refused) {
    print("abstained")
  }
}
print("done")
'''


def _world(root):
    tools = [
        tool("kb_card", "kb:wiki", [answer({"ok": True, "command": COMMAND})], server="kb"),
        # The runbook answers once, then can no longer determine the command.
        tool("runbook", "runbook:ops", [answer({"ok": True, "determinable": False},
                                               sequence=[{"payload": {"ok": True, "restart_command": COMMAND}}])],
             server="runbook",
             contract={"verifies": {"subject": {"request": "service"}, "scope": {"value": "host-a"}}}),
        tool("gate", "clock:ops", [answer({"ok": True}, when={"run": "paused"}, delay=25, until=SIGNAL), answer({"ok": True})],
             server="clock"),
        tool("restart_service", "ops:host-a", [answer({"ok": True, "restarted": True}, effect="restarted")],
             server="ops", contract={"requires_established": True})]
    return MemoryWorld(root, tools, provenance={"kb:wiki": {"ancestors": []}, "runbook:ops": {"ancestors": []},
                                                "clock:ops": {"ancestors": []}, "ops:host-a": {"ancestors": []}})


def _inputs(task, *, pause=False, recheck=False):
    return {"task_name": task, "pause": pause, "recheck": recheck}


def _restarts(world):
    return [item["args"]["command"] for item in world.world()["effects"] if item["tool"] == "restart_service"]


def _refusals(world, run_id):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record["body"]["reason"] for record in map(json.loads, journal.read_text().splitlines())
            if record["kind"] == "REJECTED" and record["body"]["run_id"] == run_id]


def test_a_basis_changed_since_it_was_read_refuses_the_effect(tmp_path):
    world = _world(tmp_path)
    world.run(PROGRAM, "first", _inputs("first"))
    assert _restarts(world) == [COMMAND]

    # Nothing changed the basis: the reused confirmation carries the restart.
    world.run(PROGRAM, "steady", _inputs("steady", pause=True))
    assert _restarts(world) == [COMMAND, COMMAND] and _refusals(world, "steady") == []

    paused = world.start(PROGRAM, "paused", _inputs("paused", pause=True))
    deadline = time.monotonic() + 300
    while {"run": "paused"} not in world.calls("gate"):
        assert paused.poll() is None and time.monotonic() < deadline, paused.communicate()
        time.sleep(0.2)
    # While it waits, the same claim is checked again and the court records it provisional.
    world.run(PROGRAM, "revise", _inputs("revise", recheck=True))
    hypothesis_id, = world.owner().state()["hypotheses"]
    entry = world.owner().state()["hypotheses"][hypothesis_id]
    assert (entry["status"], entry["reason"]) == ("provisional", "check_cannot_determine")
    assert _refusals(world, "revise") == [f"a required hypothesis is not established: {hypothesis_id}"]

    assert paused.poll() is None, "the paused session acted before the revision was applied"
    (world.world_path.parent / SIGNAL).touch()
    stdout, stderr = paused.communicate(timeout=900)
    assert paused.returncode == 0, (stdout, stderr)
    assert _refusals(world, "paused") == [
        f"a required basis changed since it was read: {hypothesis_id} is now provisional (check_cannot_determine)"]
    assert _restarts(world) == [COMMAND, COMMAND]
