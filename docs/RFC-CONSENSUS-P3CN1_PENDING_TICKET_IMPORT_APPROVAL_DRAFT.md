# RFC-CONSENSUS-P3CN1 Pending Ticket Import Approval Draft

**Status:** DRAFT APPROVAL AMENDMENT — NOT APPROVED FOR IMPLEMENTATION  
**Repository mutation:** DOCUMENTATION APPROVAL DRAFT ONLY  
**Runtime implementation status:** NOT AUTHORIZED BY THIS DOCUMENT  
**Parent approval record:** `docs/RFC-CONSENSUS-P3CN1_APPROVAL.md`  
**Target amendment:** `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_AMENDMENT.md`  
**Product Owner sign-off:** PENDING

---

## 1. Approval Question

Should P3c-N1 define and approve the following durable pending-ticket source?

```text
consensus_ticket_import mailbox message
  -> strict ticket projection validation
  -> distributed_consensus_ticket_imported event
  -> replay reconstruction of consensus_tickets[ticket_id]
```

This document is a draft approval surface only. It does not authorize runtime implementation until explicitly approved and merged according to project review policy.

---

## 2. Problem Being Resolved

The original P3c-N1 approval authorized mailbox-backed vote response collection for existing pending consensus tickets, but it did not define how a pending ticket projection becomes available inside a durable P2 mailbox wait run.

The unresolved condition is:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED
```

Current relevant facts:

```text
DistributedConsensusStmt is durable-unsupported.
consensus_tickets is in-memory interpreter state.
consensus_tickets is not persisted through _REPLAY_STATE_KEYS.
P3c-2 resolution requires an already-existing ticket projection.
```

---

## 3. Proposed Approval Decision

If approved, the approved P3c-N1 pending-ticket source is:

```text
distributed_consensus_ticket_imported
```

The import is delivered through a separate mailbox message method:

```text
consensus_ticket_import
```

The import boundary is separate from vote response delivery.

Vote responses must not carry full ticket projections.

---

## 4. Scope Added If Approved

If approved, this adds only the following design scope to P3c-N1:

```text
A durable pending-ticket import boundary that supplies the pending ticket projection through a validated distributed_consensus_ticket_imported event.
```

The import boundary may:

```text
consume a consensus_ticket_import mailbox message for coordinator global
validate a full pending ticket projection
recompute and verify vote_counts
recompute and verify votes_hash
compute ticket_import_hash
record distributed_consensus_ticket_imported in execution_history
reconstruct consensus_tickets[ticket_id] from the import event during replay
apply import idempotency by ticket_id and bootstrap_id
```

The import boundary may not:

```text
execute DistributedConsensusStmt in durable mode
send vote requests
add network or daemon transport
add persistent durable inbox
add wall-clock timers or scheduler behavior
add parser/lexer/AST syntax
change ConsensusEngine
add consensus_tickets to _REPLAY_STATE_KEYS
change artifact_schema_version
embed the full ticket projection inside consensus_vote_response
claim production distributed consensus protocol behavior
```

---

## 5. Required Import Validation If Approved

Runtime implementation must not trust imported integrity fields without recomputation.

If approved, import validation must perform this pipeline:

```text
1. Validate the P2 mailbox envelope through the existing mailbox wait path.
2. Validate the consensus_ticket_import payload as strict closed JSON.
3. Validate coordinator == global.
4. Validate bootstrap_id is a string.
5. Validate ticket with validate_ticket_projection(ticket, allow_resolved=False).
6. Recompute vote_counts from ticket["votes"] and compare to ticket["vote_counts"].
7. Recompute votes_hash from ticket["votes"] using ticket["participants"] order and compare to ticket["votes_hash"].
8. Compute ticket_import_hash from the full normalized pending ticket projection.
9. Enforce duplicate/conflict policy.
10. Append distributed_consensus_ticket_imported only after all checks pass.
```

### 5.1 votes_hash recomputation

The imported `votes_hash` must be recomputed using the same canonical vote-hash profile as the consensus engine.

Preimage:

```json
{
  "schema_version": "consensus.votes.v1",
  "votes": [["<participant>", "<vote>"]]
}
```

The `votes` list is ordered by `ticket["participants"]`:

```python
[[participant, ticket["votes"][participant]] for participant in ticket["participants"]]
```

Hash algorithm:

```text
"sha256:" + sha256(canonical_json(votes_preimage)).hexdigest()
```

Mismatch must fail closed.

### 5.2 vote_counts recomputation

The imported `vote_counts` must be recomputed from `ticket["votes"]` across:

```text
yes
no
abstain
missing
```

Mismatch must fail closed.

### 5.3 Pending-only import

Imported ticket must satisfy:

```text
projection_state == pending
```

Resolved tickets must not be imported.

---

## 6. Replay Semantics If Approved

Replay must reconstruct the pending ticket projection from recorded history:

```text
distributed_consensus_ticket_imported
```

Replay must not use live `consensus_tickets` as source of truth.

Replay must not require `consensus_tickets` in `_REPLAY_STATE_KEYS`.

Replay must validate the import event's closed schema, recompute `vote_counts`, recompute `votes_hash`, recompute `ticket_import_hash`, and reconstruct `consensus_tickets[ticket_id]` from the event's embedded `ticket`.

A `distributed_consensus_vote_received` event for a ticket must not be processed before the matching `distributed_consensus_ticket_imported` event.

Invalid ordering must fail closed.

---

## 7. Stop Gate Status If Approved

If this draft is approved, this stop gate is resolved by the selected import-boundary mechanism:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED
```

All other P3c-N1 stop gates remain active, including:

```text
FRESH_DISTRIBUTED_CONSENSUS_DURABLE_EXECUTION_REQUIRED
VOTE_REQUEST_DELIVERY_REQUIRED
REPLAY_STATE_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
P2_ARTIFACT_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
PARSER_AST_LEXER_CHANGE_REQUIRED
NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
DURABLE_TIMER_OR_SCHEDULER_REQUIRED
PERSISTENT_DURABLE_INBOX_REQUIRED
PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

---

## 8. Non-claims

This draft does not claim:

```text
production distributed consensus protocol behavior
network consensus
leader election
view-change protocol
Byzantine fault tolerance
persistent durable inbox
wall-clock scheduling
parser/AST/lexer expansion
full P3 closure
```

Distributed consensus remains `Partial`.

Overall P3 remains open.

---

## 9. Current Decision

```text
Decision: DRAFT APPROVAL AMENDMENT
Runtime implementation: NOT AUTHORIZED BY THIS DOCUMENT
Approval required before runtime implementation: YES
Runtime code changes in this draft: NONE
Selected pending-ticket source mechanism: distributed_consensus_ticket_imported, pending approval
```
