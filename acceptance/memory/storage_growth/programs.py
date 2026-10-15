"""Durable routing-table investigation programs for storage-growth acceptance."""

PALACE = '''memory palace "RoutingHistory" {
  rooms { semantic episodic procedural }
  consolidate during dream
}
'''

SURVEY = PALACE + '''
let received = tool("routing_snapshot", {})
for node in received.payload.nodes {
  let config = know({"subject": node.id, "property": "configuration", "value": node.configuration, "valid": {"from": "2026-10-01"}, "text": node.text, "source": {"tool": "routing_snapshot", "args": {}}})
}
let observed = tool("control_epoch", {"node": "router-000"})
let epoch = know({"subject": "router-000", "property": "epoch", "value": observed.payload.epoch, "valid": {"from": "2026-10-01"}, "text": "routerkey000 routing table epoch", "source": {"tool": "control_epoch", "args": {"node": "router-000"}}})
print("survey-complete")
'''

CORRECT = PALACE + '''
let observed = tool("control_epoch", {"node": "router-000"})
let changed = know({"subject": "router-000", "property": "epoch", "value": observed.payload.epoch, "valid": {"from": "2026-10-01"}, "text": "routerkey000 routing table epoch corrected", "source": {"tool": "control_epoch", "args": {"node": "router-000"}}})
print("correction-recorded")
'''

ACTION = PALACE + '''
let requirement = {"kind": "execute", "tool": "activate_routes", "admissible_err": [], "allowed_alternatives": []}
let segment = {"segment": "activate", "intent": "activate verified routing epoch", "element_part": "routing", "requirement": requirement}
let plan = task_plan({"task_id": task_name, "goal": "activate routing only at verified epoch", "segments": [segment]})
context "activate" {
  let observed = tool("control_epoch", {"node": "router-000"})
  let h = hypothesis({"aspect": "content", "subject": "router-000", "scope": "network", "statement": {"epoch": wanted_epoch}, "source": {"tool": "control_epoch", "args": {"node": "router-000"}}, "check": {"tool": "independent_epoch", "args": {"node": "router-000"}}})
  let checked = probe(h)
  try {
    let done = tool("activate_routes", {"node": "router-000", "expected_epoch": wanted_epoch}, {"requires": [h]})
  } catch (ACTION_FAILED as refused) { print("refused-before-effect") }
}
print("activation-finished")
'''

RECALL = PALACE + '''
let found = search_knowledge("routerkey000", options)
let observed = tool("control_epoch", {"node": "router-000"})
print("recalled")
'''

CHECKPOINT = PALACE + '''
let observed = tool("control_epoch", {"node": "router-000"})
print("memory-continued")
'''
