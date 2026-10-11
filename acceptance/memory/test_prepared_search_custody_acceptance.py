"""Admission results cannot rewrite the pinned knowledge used by prepared search."""
from __future__ import annotations

import pytest

from acceptance.memory import _catalog as catalog


ENTITY = {"namespace": "storage", "type": "replica-pool", "id": "evacuation-pool", "uid": "pool-generation-7"}
AT = "2026-03-01"
PROGRAM = '''
memory palace "custody" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "catalog_entry", "admissible_err": [], "allowed_alternatives": []}
let segment = {"segment": "verify", "intent": "verify the replica pool balance", "element_part": "catalog", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "verify a pinned pool statement", "segments": [segment]})
context "verify" {
  let options = {"valid_at": at, "channels": ["lexical"]}
  let first_search = search_knowledge("evacuation-pool balance", options)
  let observed = tool("catalog_entry", {"plan": "evacuation-pool"})
  let origin = {"tool": "catalog_entry", "args": {"plan": "evacuation-pool"}}
  let basis = hypothesis({"aspect": "content", "subject": subject, "statement": observed.payload.fact, "scope": "catalog", "source": origin, "check": {"tool": "billing_quote", "args": {"plan": "evacuation-pool"}}})
  let checked = probe(basis)
  let wanted = {"entity": subject, "attribute": "balance", "keys": ["evacuation-pool", "balance"], "verified_by": basis.id, "at": at}
  let first = admit(first_search.candidates, wanted)
  for candidate_check in first.checked {
    let returned_entity = $ENTITY_FIELD
    returned_entity.id = "tampered-replica-pool"
    print(returned_entity.id)
  }
  let second_search = search_knowledge("evacuation-pool balance", options)
  let second = admit(second_search.candidates, wanted)
}
print("custody checked")
'''


def _learn_pool(world, checked_value):
    catalog.publish(world, "catalog", "evacuation-pool", "balance", 96,
                    text="evacuation-pool balance reserved replicas", start="2026-01-01", entity=ENTITY)
    catalog.publish(world, "billing", "evacuation-pool", "balance", checked_value, entity=ENTITY)
    report = catalog.learn(world, "learn-pool", "evacuation-pool", subject=ENTITY)
    return report["knowledge"]["declared"][0]["statement"]


@pytest.mark.parametrize("entity_field, checked_value, decision", [
    ("first.fact.entity", 96, "admitted"),
    ("candidate_check.attestation.statement.entity", 48, "abstained"),
])
def test_mutating_an_admission_result_preserves_pinned_search_and_recorded_history(
        tmp_path, entity_field, checked_value, decision):
    world = catalog.world(tmp_path)
    identity = _learn_pool(world, checked_value)
    result = world.run(PROGRAM.replace("$ENTITY_FIELD", entity_field), "check-custody",
                       {"task_name": "check-custody", "subject": ENTITY, "at": AT})
    assert "tampered-replica-pool" in result["output_delta"]
    searches = world.events("check-custody", "knowledge_searched")
    assert len(searches) == 2
    for search in searches:
        candidate, = search["candidates"]
        assert candidate["id"] == identity and candidate["entity"] == ENTITY and candidate["value"] == 96
    decisions = world.events("check-custody", "memory_admission")
    assert [item["decision"] for item in decisions] == [decision, decision]
    for item in decisions:
        checked, = item["checked"]
        assert checked["attestation"]["statement"]["entity"] == ENTITY
    assert world.owner().state()["knowledge"]["versions"][identity]["record"]["subject"] == ENTITY
    report = world.reports()[-1]
    assert report["replay"]["check-custody"]["status"] == "replay_verified"
    assert report["integrity"]["ok"] is True
