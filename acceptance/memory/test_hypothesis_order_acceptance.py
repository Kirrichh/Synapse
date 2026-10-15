"""A hypothesis status follows the order of its checks on the gateway, whatever the windows or names (review M1, M2).

Real sessions check one claim — the restart command a knowledge-base card
gives — against the runbook, which first answers one way and then the other:

* one session checks, its palace is consolidated, and it checks again: the
  check made after the consolidation decides the status memory keeps, exactly
  as when both checks fall in one window (M1);
* two sessions check, then one consolidation folds both: the later check on
  the gateway decides, whether the sessions' names sort with that order or
  against it (M2).

A later session that acts on the claim acts on the status memory holds; the
environment's own record counts the restarts.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _runbook as runbook
from acceptance.memory._world import MemoryWorld, answer

KILL = "pg_ctl kill"
OBSERVE = '''
let reading = {"segment": "observe", "intent": "read the card", "element_part": "ops", "requirement": {"kind": "execute", "tool": "kb_card", "admissible_err": [], "allowed_alternatives": []}}
let checking = {"segment": "check", "intent": "check the command", "element_part": "ops", "requirement": {"kind": "execute", "tool": "runbook", "admissible_err": [], "allowed_alternatives": []}}
let plan = task_plan({"task_id": task_name, "goal": "check the restart command", "segments": [reading, checking]})
let h = {}
context "observe" {
  let card = tool("kb_card", {"id": "pg-steady"})
  h = hypothesis({"aspect": "content", "subject": "postgresql", "statement": {"restart_command": card.payload.command}, "scope": "host-a", "source": {"tool": "kb_card", "args": {"id": "pg-steady"}}, "check": {"tool": "runbook", "args": {"service": "postgresql"}}})
}
'''
CONSOLIDATING = 'memory palace "clerk" {\n  rooms { episodic procedural }\n  consolidate during dream\n}\n'
WAITING = 'memory palace "clerk" {\n  rooms { episodic procedural }\n}\n'
MAINTENANCE = 'memory palace "clerk" {\n  rooms { episodic semantic }\n  consolidate during dream\n}\nconsolidate palace\n'


def _world(root, first_confirms: bool) -> MemoryWorld:
    """The runbook's first answer confirms the card's command (or contradicts it); every later answer the other."""
    tools = runbook.tools()
    check = next(item for item in tools if item["name"] == "runbook")
    first, later = (runbook.TRUE, KILL) if first_confirms else (KILL, runbook.TRUE)
    check["answers"] = [answer({"ok": True, "restart_command": later},
                               sequence=[{"payload": {"ok": True, "restart_command": first}}])]
    return MemoryWorld(root, tools, provenance=runbook.provenance())


def _held(world):
    status, = world.owner().state()["hypotheses"].values()
    return status


def _checks(world, run_id):
    return [event["check_ref"]["gw_seq"] for event in world.events(run_id, "hypothesis_probed")]


@pytest.mark.parametrize("first_confirms", [True, False], ids=["confirmed-then-refuted", "refuted-then-confirmed"])
@pytest.mark.parametrize("between", ["consolidate palace\n", ""], ids=["across-a-consolidation", "within-one-window"])
def test_the_last_check_of_a_session_decides_what_memory_keeps(tmp_path, first_confirms, between):
    world = _world(tmp_path, first_confirms)
    source = (CONSOLIDATING + OBSERVE + 'context "check" {\n  let first = probe(h)\n}\n' + between
              + 'context "check" {\n  let second = probe(h)\n}\nprint("done")\n')
    world.run(source, "checker", {"task_name": "checker"})
    held = _held(world)
    first, second = _checks(world, "checker")
    assert first < second
    assert (held["status"], held["at"]) == ("refuted" if first_confirms else "confirmed", second)
    # A later session acts on the status memory holds: a refuted command is never run.
    world.run(runbook.PROGRAM, "later", runbook.inputs("later", "pg-steady"))
    assert runbook.restarts(world) == ([] if first_confirms else [runbook.TRUE])


@pytest.mark.parametrize("first_confirms", [True, False], ids=["confirmed-then-refuted", "refuted-then-confirmed"])
@pytest.mark.parametrize("names", [("z-first", "a-second"), ("a-first", "z-second")],
                         ids=["names-against-the-order", "names-in-the-order"])
def test_the_later_check_decides_whatever_the_sessions_are_named(tmp_path, first_confirms, names):
    world = _world(tmp_path, first_confirms)
    source = WAITING + OBSERVE + 'context "check" {\n  let checked = probe(h)\n}\n'
    for run_id in names:
        world.run(source, run_id, {"task_name": run_id})
    assert world.reports() == []  # Both sessions wait for one consolidation.
    world.run(MAINTENANCE, "maintenance", {})
    (earlier,), (later,) = (_checks(world, run_id) for run_id in names)
    held = _held(world)
    assert earlier < later
    assert (held["status"], held["run_id"], held["at"]) == ("refuted" if first_confirms else "confirmed", names[1], later)
    # The window applied both checks in the gateway's order.
    assert [(item["run_id"], item["at"]) for item in world.reports()[-1]["hypotheses"]["probed"]] == \
        [(names[0], earlier), (names[1], later)]
