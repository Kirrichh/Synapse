"""A learned procedure's boundary comes from contrasting episodes (refinement §12, D1).

Three verified recoveries of a refused migration, in three tasks, and two
verified failures of the same procedure — with active readers, and with a
replication lag beyond what the checkpoint absorbs — teach the court where the
procedure works. The birth names readers and lag as essential (each with its
contrast), tenant as irrelevant (it varied), and requires the journal mode the
body reads to be present as a string. Then, through the canonical launch:

1. a new admissible situation — new database, new tenant, a field no basis had
   — runs on the fast path and the environment ends intact;
2. the near inadmissible situation — one active reader — is a near miss that
   names its condition, and nothing reaches the environment;
3. unknown information — readers absent, readers of another kind, the read
   journal mode absent — never grants automation;
4. a lag between the positives and the contrast was never verified: blocked;
5. a changed tool contract is another configuration, refused before any effect.
"""
from __future__ import annotations

import json

import pytest

from acceptance.memory import _checkpoint as checkpoint
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.owner import MemoryOwner, MemoryOwnerViolation


def _near_miss(world, run_id):
    event, = world.events(run_id, "habit_near_miss")
    return event["failed_condition"]


def test_contrasts_draw_the_boundary_and_unknown_information_never_grants_automation(tmp_path):
    world = checkpoint.world(tmp_path)
    birth = checkpoint.learn(world)

    # The birth: its scope and why, from the positives and the two contrasts.
    assert birth["criteria"]["contrasts"] == 2 and birth["criteria"]["all_success"] is True
    when = {(item["field"], item["op"]): item["value"] for item in birth["condition"]["when"]}
    assert when[("readers", "==")] == 0 and when[("lag_ms", "<=")] == 120
    assert when[("journal_mode", "is")] == "string" and "tenant" not in {field for field, _ in when}
    explanation = birth["applicability"]
    essential = {entry["field"]: [ref["value"] for ref in entry["contrasts"]] for entry in explanation["essential"]}
    assert essential == {"lag_ms": [900], "readers": [2]}
    assert {"field": "tenant", "values": 3} in explanation["irrelevant"]
    assert explanation["required"] == [{"field": "journal_mode", "kind": "string"}]
    assert birth["criteria"]["contradictions"] == 0 and birth["criteria"]["episodes"] == 3

    # 1. A new admissible situation: the fast path, and the environment is intact.
    world.run(checkpoint.program(), "new-catalog", checkpoint.inputs("new-catalog", "catalog"))
    activated, = world.events("new-catalog", "habit_activated")
    assert activated["habit_id"] == birth["habit_id"] and activated["outcome"] == "success"
    assert world.events("new-catalog", "slow_path_used") == []
    matched = {(item["field"], item["op"]): item["actual"] for item in activated["matched"]["when"]}
    assert matched[("readers", "==")] == 0 and matched[("lag_ms", "<=")] == 60
    assert checkpoint.effects(world, "catalog") == ["checkpointed"]
    assert world.calls("db_integrity")[-1] == {"db": "catalog"}
    assert world.calls("wal_checkpoint")[-1] == {"db": "catalog", "mode": "wal2"}

    # 2. The near inadmissible situation: one reader. A near miss names it; nothing is checkpointed.
    world.run(checkpoint.program(), "near-reports", checkpoint.inputs("near-reports", "reports", careful=True))
    assert world.events("near-reports", "habit_activated") == []
    assert _near_miss(world, "near-reports") == {"field": "readers", "op": "==", "value": 0, "actual": 1}
    assert checkpoint.effects(world, "reports") == []

    # 3. Unknown information: absent readers, readers of another kind, the read journal mode absent.
    for run_id, database, condition in (("unknown-metrics", "metrics", {"field": "readers", "op": "==", "value": 0,
                                                                         "actual": None}),
                                        ("unknown-audit", "audit", {"field": "readers", "op": "==", "value": 0,
                                                                     "actual": "0"}),
                                        ("unknown-events", "events", {"field": "journal_mode", "op": "is",
                                                                      "value": "string", "actual": None})):
        world.run(checkpoint.program(), run_id, checkpoint.inputs(run_id, database, careful=True))
        assert world.events(run_id, "habit_activated") == [], run_id
        assert _near_miss(world, run_id) == condition
        assert checkpoint.effects(world, database) == []

    # 4. Between the positives (at most 120) and the contrast (900) nothing was verified.
    world.run(checkpoint.program(), "untested-search", checkpoint.inputs("untested-search", "search", careful=True))
    assert _near_miss(world, "untested-search") == {"field": "lag_ms", "op": "<=", "value": 120, "actual": 500}
    assert checkpoint.effects(world, "search") == []

    # 5. A changed contract of the checkpoint is another configuration: refused before any effect.
    changed = json.loads(world.configuration_path.read_text())
    for item in changed["tools"]["tools"]:
        if item["name"] == "wal_checkpoint":
            item["contract"] = {"idempotent": True}
    path = tmp_path / "changed-memory.json"
    path.write_text(json.dumps(changed, sort_keys=True))
    environment, journal = world.world(), world.journal()
    code, payload, _ = world.attempt(checkpoint.program(), "changed-contract",
                                     checkpoint.inputs("changed-contract", "catalog"), configuration=path)
    assert code == 1 and payload["status"] == "ERROR"  # The public error names no internals.
    assert world.world() == environment and world.journal() == journal
    assert not (world.runs / "changed-contract.json").exists()
    # The refusal is the owner's: bound to one configuration, it refuses any other.
    owner = MemoryOwner(world.state)
    with owner.store.session() as guard, pytest.raises(MemoryOwnerViolation, match="another memory configuration"):
        owner.bind(guard, parse_memory_configuration(changed))
    assert world.journal() == journal
