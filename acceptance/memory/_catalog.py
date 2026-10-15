"""The plan catalog of the semantic knowledge acceptance files (refinement §15).

A catalog service describes the plans of a hosting provider; a billing
service quotes the same plans independently; a web mirror republishes the
catalog (its provenance descends from it). What each service answers is kept
in the tool server's own world, so a test changes the catalog between sessions
(a correction, a new price) and the checker reads the truth from that world.

The embedding model is scripted by concepts: synonyms and translations share a
concept, while a negation or an entity's name changes nothing — how dense
retrievers are known to fail (NevIR; EntityQuestions). Admission, not the
embedding, must tell those apart.

A learning session reads one plan from a source and states what it read with
``know``; the court folds the statement into memory at the end of the session.
An asking session searches memory, re-reads the catalog as the source of a
content hypothesis, checks it against billing and asks admission. A statement
is about a subject — the plan's name, or an entity reference with a namespace
and an incarnation; an alias is named by a directory and confirmed or refuted
by an independent registry.
"""
from __future__ import annotations

import fcntl
import json

from acceptance.memory._world import MemoryWorld, embedder, stateful, tool

CONCEPTS = (
    ("price", {"price", "prices", "cost", "costs", "стоимость", "стоит", "цена"}),
    ("plan", {"plan", "plans", "tariff", "тариф", "тарифа", "subscription", "подписка"}),
    ("starter", {"basic", "starter", "начальный", "начального", "базовый"}),
    ("month", {"month", "monthly", "месяц", "ежемесячно"}),
    ("backup", {"backup", "backups", "резервные", "копии"}),
    ("include", {"include", "includes", "включает", "входят"}),
    ("outage", {"outage", "downtime", "сбой"}),
    ("promo", {"promo", "discount", "скидка", "акция"}),
)

LEARN = '''
memory palace "catalog" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": source_tool, "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "read", "intent": "read what a source states about a plan", "element_part": "catalog", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "learn about a plan", "segments": [seg]})
context "read" {
  let entry = tool(source_tool, {"plan": plan_id})
  let stated = know({"subject": subject, "property": entry.payload.property, "value": entry.payload.value, "polarity": entry.payload.polarity, "valid": {"from": entry.payload.start}, "text": entry.payload.text, "source": {"tool": source_tool, "args": {"plan": plan_id}}})
  if confirm_now == true {
    let origin = {"tool": source_tool, "args": {"plan": plan_id}}
    let h = hypothesis({"aspect": "content", "subject": subject, "statement": entry.payload.fact, "scope": "catalog", "source": origin, "check": {"tool": "billing_quote", "args": {"plan": plan_id}}})
    let checked = probe(h)
  }
}
print("learned")
'''

ASK = '''
memory palace "catalog" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "ask", "intent": "answer a question about a plan", "element_part": "catalog", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "answer about a plan", "segments": [seg]})
context "ask" {
  let found = search_knowledge(query, options)
  let entry = tool("catalog_entry", {"plan": plan_id})
  let origin = {"tool": "catalog_entry", "args": {"plan": plan_id}}
  let h = hypothesis({"aspect": "content", "subject": subject, "statement": entry.payload.fact, "scope": "catalog", "source": origin, "check": {"tool": "billing_quote", "args": {"plan": plan_id}}})
  let checked = probe(h)
  let wanted = {"entity": subject, "attribute": prop, "keys": [plan_id, prop], "polarity": polarity, "verified_by": h.id}
  if at != "" {
    wanted = {"entity": subject, "attribute": prop, "keys": [plan_id, prop], "polarity": polarity, "verified_by": h.id, "at": at}
  }
  let decided = admit(found.candidates, wanted)
}
print("answered")
'''


IDENTIFY = '''
memory palace "catalog" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "ask", "intent": "answer about an entity known under another name", "element_part": "catalog", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "answer about an aliased entity", "segments": [seg]})
context "ask" {
  let found = search_knowledge(query, options)
  let entry = tool("catalog_entry", {"plan": plan_id})
  let origin = {"tool": "catalog_entry", "args": {"plan": plan_id}}
  let h = hypothesis({"aspect": "content", "subject": plan_id, "statement": entry.payload.fact, "scope": "catalog", "source": origin, "check": {"tool": "billing_quote", "args": {"plan": plan_id}}})
  let checked = probe(h)
  let named = tool("directory", {"entity": alias})
  let link = hypothesis({"aspect": "entity", "subject": alias, "statement": named.payload.link, "scope": "catalog", "source": {"tool": "directory", "args": {"entity": alias}}, "check": {"tool": "registry", "args": {"entity": alias}}})
  let linked = probe(link)
  let decided = admit(found.candidates, {"entity": alias, "attribute": prop, "keys": [plan_id, prop], "verified_by": h.id, "identified_by": link.id, "at": at})
}
print("answered")
'''


def tools():
    read = {"read": "catalog", "key": "plan"}
    catalog = tool("catalog_entry", "catalog:ops", [stateful({"ok": True}, act=read,
                                                             otherwise={"ok": False, "err": "UNKNOWN_PLAN"})],
                   server="catalog", contract={"effect_on_err": {"UNKNOWN_PLAN": "none"}})
    mirror = tool("mirror_entry", "mirror:web", [stateful({"ok": True}, act={"read": "mirror", "key": "plan"},
                                                          otherwise={"ok": False, "err": "UNKNOWN_PLAN"})],
                  server="mirror", contract={"effect_on_err": {"UNKNOWN_PLAN": "none"}})
    # Billing names the entity each quote is about; it answers only for the catalog.
    billing = tool("billing_quote", "billing:ops", [stateful({"ok": True}, act={"read": "billing", "key": "plan"},
                                                             otherwise={"ok": False, "err": "UNKNOWN_PLAN"})],
                   server="billing", contract={"effect_on_err": {"UNKNOWN_PLAN": "none"}, "verifies": {
                       "subject": {"answer": "entity"}, "scope": {"value": "catalog"}}})
    embed = tool("embed", "embed:model", [embedder(CONCEPTS)], server="model", role="reason")
    # A directory names what an alias stands for; an independent registry confirms or refutes the link.
    directory = tool("directory", "directory:ops", [stateful({"ok": True}, act={"read": "directory", "key": "entity"},
                                                             otherwise={"ok": False, "err": "UNKNOWN_NAME"})],
                     server="names", contract={"effect_on_err": {"UNKNOWN_NAME": "none"}})
    registry = tool("registry", "registry:ops", [stateful({"ok": True}, act={"read": "registry", "key": "entity"},
                                                          otherwise={"ok": False, "err": "UNKNOWN_NAME"})],
                    server="names", contract={"effect_on_err": {"UNKNOWN_NAME": "none"}, "verifies": {
                        "subject": {"request": "entity", "answer": "entity"}, "scope": {"value": "catalog"}}})
    return [catalog, mirror, billing, embed, directory, registry]


def provenance():
    return {"catalog:ops": {"ancestors": []}, "billing:ops": {"ancestors": []}, "embed:model": {"ancestors": []},
            "directory:ops": {"ancestors": []}, "registry:ops": {"ancestors": []},
            # The mirror republishes the catalog.
            "mirror:web": {"ancestors": ["catalog:ops"]}}


PROPERTIES = {"monthly_price": {"freshness": "state"}, "backups_included": {"freshness": "state"},
              "balance": {"freshness": "state"},
              "promo_price": {"freshness": "bounded", "ttl_days": 30}, "outage": {"freshness": "event"}}


def world(root, *, budget: int = 3) -> MemoryWorld:
    knowledge = {"embedder": {"tool": "embed", "version": "concepts-v1"}, "properties": PROPERTIES,
                 "budget": budget}
    return MemoryWorld(root, tools(), provenance=provenance(), knowledge=knowledge)


def _put(world: MemoryWorld, room: str, key: str, content: dict) -> None:
    path = world.world_path
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"calls": [], "effects": []}
        state.setdefault("objects", {}).setdefault(room, {})[key] = content
        path.write_text(json.dumps(state, sort_keys=True))


def publish(world: MemoryWorld, room: str, plan: str, prop: str, value, *, text: str = "", start: str | None = None,
            polarity: bool = True, **extra) -> None:
    """What one service answers about one plan from now on (the checker's truth); the entity it names is the
    plan unless ``entity`` says which object (a namespace, a type, an incarnation)."""
    _put(world, room, plan, {"plan": plan, "entity": plan, "property": prop, "value": value, "polarity": polarity,
                             "start": start, "text": text, "fact": {prop: value}, prop: value, **extra})


def learn(world: MemoryWorld, run_id: str, plan: str, *, source: str = "catalog_entry", check: bool = False,
          subject=None) -> dict:
    """One learning session; ``subject`` is the entity the statement is about (the plan's name by default)."""
    world.run(LEARN, run_id, {"task_name": run_id, "plan_id": plan, "source_tool": source, "confirm_now": check,
                              "subject": plan if subject is None else subject})
    return world.reports()[-1]


def name(world: MemoryWorld, alias: str, *, directory: str | None, registry: str | None) -> None:
    """What the directory says ``alias`` stands for, and what the independent registry says (``None``: unknown)."""
    for room, target in (("directory", directory), ("registry", registry)):
        if target is not None:
            _put(world, room, alias, {"entity": alias, "link": {"same_as": target}, "same_as": target})


def identify(world: MemoryWorld, run_id: str, plan: str, alias: str, prop: str, query: str, *, at: str) -> dict:
    """One session asking about ``alias``: content checked on ``plan``, the alias checked by the registry."""
    world.run(IDENTIFY, run_id, {"task_name": run_id, "plan_id": plan, "alias": alias, "prop": prop,
                                 "query": query, "at": at, "options": {"valid_at": at}})
    searched, = world.events(run_id, "knowledge_searched")
    decided, = world.events(run_id, "memory_admission")
    return {"search": searched, "admission": decided}


def ask(world: MemoryWorld, run_id: str, plan: str, prop: str, query: str, *, at: str = "", polarity: bool = True,
        subject=None, **options) -> dict:
    """One asking session; returns its search and its admission decision."""
    search = {**options, **({"valid_at": at} if at else {})}
    world.run(ASK, run_id, {"task_name": run_id, "plan_id": plan, "prop": prop, "query": query, "at": at,
                            "polarity": polarity, "options": search, "subject": plan if subject is None else subject})
    searched, = world.events(run_id, "knowledge_searched")
    decided, = world.events(run_id, "memory_admission")
    return {"search": searched, "admission": decided}
