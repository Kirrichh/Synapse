"""The service-restart scenario of the hypothesis acceptance files (refinement §10).

A knowledge-base card says which service it is about and which command
restarts it. The program never acts on the card itself: it states two
hypotheses read from the recorded card — the entity (the service) and the
content (the command) — and checks them with independent sources: the
inventory for the entity, the runbook for the command. The restart is a
consequential action by its operator contract: it runs only when every
hypothesis it names is established, and is refused before any effect
otherwise. A mirror of the knowledge base answers like the card, so it can
never confirm it.

Cards: ``pg-good`` carries the true command (its third and later reads carry a
new revision); ``pg-bad`` names the right service with a poisoned command;
``pg-mute`` asks the runbook about a topic it cannot determine.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, tool

TRUE = "pg_ctl restart -m fast"

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "restart_service", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "restart", "intent": "restart the service by the command its card gives", "element_part": "ops", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "restart the service", "segments": [seg]})
context "restart" {
  let command = fallback_command
  if read_card == true {
    let card = tool("kb_card", {"id": card_id})
    command = card.payload.command
  }
  let origin = {"tool": "kb_card", "args": {"id": card_id}}
  let entity = hypothesis({"aspect": "entity", "subject": card_id, "statement": {"service": "postgresql"}, "scope": "host-a", "source": origin, "check": {"tool": "inventory", "args": {"host": "host-a"}}})
  let content = hypothesis({"aspect": "content", "subject": card_id, "statement": {"command": command}, "scope": "host-a", "source": origin, "check": {"tool": checker, "args": check_args}})
  if established(entity) == false {
    let e = probe(entity)
  }
  if established(content) == false {
    let c = probe(content)
  }
  let fact = {"id": card_id, "entity": "postgresql", "attribute": "restart_command", "value": command, "source": "kb:wiki", "hypothesis": content.id, "content": "postgresql restart command"}
  let admitted = admit([fact], {"entity": "postgresql", "attribute": "restart_command", "keys": ["postgresql", "restart", "command"]})
  try {
    let done = tool("restart_service", {"command": command}, {"requires": [entity, content]})
  } catch (ACTION_FAILED as refused) {
    print("abstained")
  }
}
print("done")
'''


def tools():
    card = {"ok": True, "service": "postgresql", "command": TRUE, "rev": 1}
    changed = {**card, "rev": 2}
    kb = tool("kb_card", "kb:wiki", [
        answer(changed, when={"id": "pg-good"}, sequence=[{"payload": card}, {"payload": card}]),
        answer({"ok": True, "service": "postgresql", "command": "pg_ctl kill", "rev": 1}, when={"id": "pg-bad"}),
        answer({"ok": True, "service": "postgresql", "command": "pg_ctl reload", "rev": 1}, when={"id": "pg-mute"})],
        server="kb")
    mirror = tool("kb_mirror", "mirror:kb", [answer({"ok": True, "command": "pg_ctl kill"})], server="mirror")
    inventory = tool("inventory", "cmdb:ops", [answer({"ok": True, "service": "postgresql", "version": "14"})],
                     server="cmdb")
    runbook = tool("runbook", "runbook:ops", [
        answer({"ok": True, "determinable": False}, when={"topic": "reload"}),
        answer({"ok": True, "command": TRUE})], server="runbook")
    restart = tool("restart_service", "ops:host-a", [answer({"ok": True, "restarted": True}, effect="restarted")],
                   server="ops", contract={"requires_established": True})
    return [kb, mirror, inventory, runbook, restart]


def provenance():
    return {"kb:wiki": {"ancestors": []}, "mirror:kb": {"ancestors": ["kb:wiki"]}, "cmdb:ops": {"ancestors": []},
            "runbook:ops": {"ancestors": []}, "ops:host-a": {"ancestors": []}}


def world(root) -> MemoryWorld:
    return MemoryWorld(root, tools(), provenance=provenance())


def inputs(task, card, *, checker="runbook", check_args=None, read_card=True, fallback=TRUE):
    return {"task_name": task, "card_id": card, "checker": checker, "read_card": read_card,
            "check_args": check_args or {"service": "postgresql"}, "fallback_command": fallback}


def statuses(world: MemoryWorld, run_id: str) -> dict[str, dict]:
    """The status each hypothesis of a run ended with, by aspect."""
    declared = {event["hypothesis"]["id"]: event["hypothesis"]["aspect"]
                for event in world.events(run_id, "hypothesis_declared")}
    found = {}
    for event in world.history(run_id):
        if event.get("type") in {"hypothesis_probed", "hypothesis_reused"} and event["status"] is not None:
            found[declared[event["hypothesis"]]] = {"status": event["status"], "reason": event["reason"],
                                                    "by": event["type"]}
    return found


def admission(world: MemoryWorld, run_id: str) -> dict:
    """The run's palace admission of the card's command as an established fact."""
    event, = world.events(run_id, "memory_admission")
    return event


def restarts(world: MemoryWorld) -> list[str]:
    return [item["args"]["command"] for item in world.world()["effects"] if item["tool"] == "restart_service"]
