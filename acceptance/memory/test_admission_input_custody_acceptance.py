"""Admission history retains the claim evaluated before its caller changes input data."""
from __future__ import annotations

import json

import pytest

from acceptance.memory import _catalog as catalog


ENTITY = {"namespace": "storage", "type": "replica-pool", "id": "evacuation-pool", "uid": "generation-7"}
KEYS = ["evacuation-pool", "balance"]
AT = "2026-03-01T00:00:00Z"
PROGRAM = '''
memory palace "admission-input-custody" {
  rooms { semantic }
  consolidate during dream
}
fn extend_key(change) { change("subsequent-request") }
let req = {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}
let segment = {"segment": "verify", "intent": "Verify the pool before reusing request containers", "element_part": "catalog", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "Preserve the exact claim decided before input changes", "segments": [segment]})
context "verify" {
  let found = search_knowledge("evacuation-pool balance", {"valid_at": at, "channels": ["lexical"]})
  let observed = tool("catalog_entry", {"plan": "evacuation-pool"})
  let origin = {"tool": "catalog_entry", "args": {"plan": "evacuation-pool"}}
  let basis = hypothesis({"aspect": "content", "subject": subject, "statement": observed.payload.fact, "scope": "catalog", "source": origin, "check": {"tool": "billing_quote", "args": {"plan": "evacuation-pool"}}})
  let checked = probe(basis)
  let requested_keys = ["evacuation-pool", "balance"]
  let requested_conditions = INITIAL_CONDITIONS
  let wanted = {"entity": subject, "attribute": "balance", "keys": requested_keys, "conditions": requested_conditions, "verified_by": basis.id, "at": at}
  let decided = admit(found.candidates, wanted)
  MUTATE_INPUT
  print("original input was changed after admission")
}
print("admission input custody checked")
'''


def _learn_pool(world):
    catalog.publish(world, "catalog", "evacuation-pool", "balance", 96, entity=ENTITY,
                    text="evacuation-pool balance reserved replicas", start="2026-01-01")
    catalog.publish(world, "billing", "evacuation-pool", "balance", 96, entity=ENTITY)
    return catalog.learn(world, "learn-pool", "evacuation-pool", subject=ENTITY)


@pytest.mark.parametrize("conditions, mutation, decision, reasons", [
    ({}, 'extend_key(requested_keys.append)\n  assert len(wanted.keys) == 3', "admitted", []),
    ({"maintenance": {"generation": 7, "mode": "scheduled"}},
     'requested_conditions.maintenance.generation = 8\n  assert wanted.conditions.maintenance.generation == 8',
     "abstained", ["other_conditions"]),
], ids=["admitted-keys-list", "refused-nested-conditions"])
def test_mutating_the_input_claim_preserves_admission_history_then_court_replay(
        tmp_path, conditions, mutation, decision, reasons):
    world = catalog.world(tmp_path)
    _learn_pool(world)
    source = PROGRAM.replace("INITIAL_CONDITIONS", json.dumps(conditions)).replace("MUTATE_INPUT", mutation)
    result = world.run(source, "claim-custody", {"task_name": "claim-custody", "subject": ENTITY, "at": AT})
    assert "original input was changed after admission" in result["output_delta"]
    event, = world.events("claim-custody", "memory_admission")
    check, = event["checked"]
    assert event["decision"] == decision and check["reasons"] == reasons
    assert event["claim"] == {
        "entity": ENTITY, "attribute": "balance", "kind": "fact", "scope": None, "at": AT,
        "keys": KEYS, "threshold": 0.5, "polarity": True, "conditions": conditions,
        "verified_by": check["basis"], "identified_by": None,
    }
    report = world.reports()[-1]
    assert report["replay"]["claim-custody"]["status"] == "replay_verified"
    assert report["integrity"]["ok"] is True
