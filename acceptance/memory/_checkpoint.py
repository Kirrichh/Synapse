"""The migration scenario of the applicability acceptance files (refinement §12, D1).

A schema migration is refused with ``LOCKED`` while the database journal holds
pages; the refusal reports the database's state: active readers, replication
lag, tenant and journal mode (and, on newer databases, a region). The recovery
the agent writes on the slow path checkpoints the journal in its mode, repeats
the migration, reads the migration state from an independent registry and
checks integrity — the segment's anchor.

The environment decides what the procedure does: with active readers the
checkpoint disrupts them, and with a lag beyond what the checkpoint can absorb
it loses pages; in both cases every call still answers ``ok`` and only the
integrity check reveals the damage. These are the contrasts the court learns
the procedure's boundary from. A careful session abstains instead of acting.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, tool

FIELDS = ("readers", "lag_ms", "tenant", "journal_mode", "region")

# name -> the refusal's facts, and whether the recovery leaves the database intact.
DATABASES = {
    # Learning: three positives in three tasks, a readers contrast and a lag contrast.
    "orders": ({"readers": 0, "lag_ms": 5, "tenant": "acme", "journal_mode": "wal"}, True),
    "billing": ({"readers": 0, "lag_ms": 40, "tenant": "globex", "journal_mode": "wal2"}, True),
    "sessions": ({"readers": 2, "lag_ms": 40, "tenant": "globex", "journal_mode": "wal2"}, False),
    "archive": ({"readers": 0, "lag_ms": 900, "tenant": "acme", "journal_mode": "wal"}, False),
    "ledger": ({"readers": 0, "lag_ms": 120, "tenant": "initech", "journal_mode": "wal"}, True),
    # A new admissible situation: new tenant, new database, a field no basis had.
    "catalog": ({"readers": 0, "lag_ms": 60, "tenant": "umbrella", "journal_mode": "wal2", "region": "eu"}, True),
    # Near and unknown situations.
    "reports": ({"readers": 1, "lag_ms": 40, "tenant": "acme", "journal_mode": "wal"}, False),
    "metrics": ({"lag_ms": 40, "tenant": "acme", "journal_mode": "wal"}, True),
    "audit": ({"readers": "0", "lag_ms": 40, "tenant": "acme", "journal_mode": "wal"}, True),
    "events": ({"readers": 0, "lag_ms": 40, "tenant": "acme"}, True),
    "search": ({"readers": 0, "lag_ms": 500, "tenant": "acme", "journal_mode": "wal"}, True),
    # Widening: lag 300 is recovered, lag 1000 is recovered too but lies beyond a recorded contrast.
    **{f"cache-{index}": ({"readers": 0, "lag_ms": 300, "tenant": "acme", "journal_mode": "wal"}, True)
       for index in range(1, 4)},
    "cache-4": ({"readers": 0, "lag_ms": 250, "tenant": "globex", "journal_mode": "wal2"}, True),
    **{f"blob-{index}": ({"readers": 0, "lag_ms": 1000, "tenant": "acme", "journal_mode": "wal"}, True)
       for index in range(1, 4)},
    "blob-4": ({"readers": 0, "lag_ms": 1000, "tenant": "initech", "journal_mode": "wal"}, True),
}

_BLOCK = '''
context "{segment}" {{
  try {{
    let done_{index} = tool("migrate", {{"db": {db}}})
  }} catch (ACTION_FAILED as refusal) {{
    if careful == true {{
      print("abstained")
    }} else {{
      let facts = refusal.action.payload
      let flushed = tool("wal_checkpoint", {{"db": {db}, "mode": facts.journal_mode}})
      let repeated = tool("migrate", {{"db": {db}}}, {{"retry_of": refusal.op}})
      let recorded = tool("migration_state", {{"db": {db}}})
      let verified = tool("db_integrity", {{"db": {db}}})
    }}
  }}
}}
'''


def program(count: int = 1) -> str:
    """A task that migrates ``count`` databases, one segment each (``db``, or ``db_1``, ``db_2``, …).

    Each segment is its own context inside the ``migrate`` context, so every
    reactive event carries the same context labels as a single migration.
    """
    names = ["db"] if count == 1 else [f"db_{index}" for index in range(1, count + 1)]
    segments = [f"migrate_{index}" if count > 1 else "migrate" for index in range(1, count + 1)]
    plan = ", ".join(f'{{"segment": "{segment}", "intent": "migrate the database schema", '
                     f'"element_part": "db_migration", "requirement": req, "anchor": anchor}}' for segment in segments)
    blocks = "".join(_BLOCK.format(segment=segment, index=index, db=name)
                     for index, (segment, name) in enumerate(zip(segments, names)))
    if count > 1:
        # Every segment runs inside the same "migrate" context the learned trigger requires.
        blocks = 'context "migrate" {' + blocks + '}\n'

    return '''
memory palace "dba" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "migrate", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "db_integrity", "fields": {"intact": true}}
let plan = task_plan({"task_id": task_name, "goal": "migrate the databases", "segments": [''' + plan + ''']})
''' + blocks + '''
print("migrated")
'''


def _checkpoint_effect(facts, intact) -> str:
    if facts.get("readers") not in (0, "0", None):
        return "readers_disrupted"
    return "checkpointed" if intact else "pages_lost"


def tools(*, checkpoint_contract=None):
    migrate = tool("migrate", "orm:acct", [
        answer({"ok": True, "db": name, "version": 2}, when={"db": name},
               sequence=[{"payload": {"ok": False, "err": "LOCKED", **facts}}])
        for name, (facts, _) in DATABASES.items()], contract={"effect_on_err": {"LOCKED": "none"}},
        event_fields=list(FIELDS))
    checkpoint = tool("wal_checkpoint", "sqlite:ops", [
        answer({"ok": True, "db": name, "pages": 12}, when={"db": name}, effect=_checkpoint_effect(facts, intact))
        for name, (facts, intact) in DATABASES.items()], server="sqlite", contract=checkpoint_contract or {})
    state = tool("migration_state", "cmdb:ops", [answer({"ok": True, "applied": True})], server="cmdb",
                 contract={"state_check_for": "migrate", "resolve_state": {"applied": "applied"}})
    integrity = tool("db_integrity", "fsck:ops", [
        answer({"ok": True, "db": name, "intact": intact}, when={"db": name})
        for name, (_, intact) in DATABASES.items()], server="fsck")
    return [migrate, checkpoint, state, integrity]


def provenance():
    return {source: {"ancestors": []} for source in ("orm:acct", "sqlite:ops", "cmdb:ops", "fsck:ops")}


def world(root) -> MemoryWorld:
    return MemoryWorld(root, tools(), provenance=provenance())


def inputs(task: str, *databases: str, careful: bool = False) -> dict:
    names = {"db": databases[0]} if len(databases) == 1 else {
        f"db_{index}": name for index, name in enumerate(databases, 1)}
    return {"task_name": task, "careful": careful, **names}


LEARNING = (("learn-orders", "orders"), ("learn-billing", "billing"), ("contrast-sessions", "sessions"),
            ("contrast-archive", "archive"), ("learn-ledger", "ledger"))


def learn(world: MemoryWorld) -> dict:
    """Three verified recoveries in three tasks and two verified contrasts; the last session births."""
    for run_id, database in LEARNING:
        world.run(program(), run_id, inputs(run_id, database))
    birth, = world.reports()[-1]["births"]
    return birth


def effects(world: MemoryWorld, database: str) -> list[str]:
    """What the environment applied to one database."""
    return [item["effect"] for item in world.world()["effects"] if item["args"].get("db") == database]
