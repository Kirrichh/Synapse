# RFC-CONSENSUS-P3CN1 Approval Record — Pending Ticket Import Amendment Draft

**Status:** DRAFT AMENDMENT — NOT APPROVED FOR IMPLEMENTATION  
**Stage:** P3c-N1 approval amendment gate  
**Repository mutation:** DOCUMENTATION APPROVAL AMENDMENT ONLY  
**Implementation status:** NOT AUTHORIZED BY THIS PATCH  
**Product Owner sign-off:** PENDING  
**Target RFC:** `docs/RFC-CONSENSUS-P3CN1.md`  
**Original approved RFC content SHA:** `ff95d7daac3fcffad461356e7b3ad9a7b446377c`  
**Amendment target:** Define durable pending-ticket source for P3c-N1.  
**Runtime code changes in this patch:** NONE

---

## 1. Amendment Summary

The original P3c-N1 approval authorized mailbox-backed vote response collection for existing pending consensus tickets, but it did not define how a pending ticket projection becomes available inside a durable P2 mailbox wait run.

This amendment records the required design correction:

```text
P3c-N1 requires an explicit durable pending-ticket import boundary before vote response collection can run.
```

The selected mechanism is:

```text
consensus_ticket_import mailbox message
  -> strict ticket projection validation
  -> distributed_consensus_ticket_imported event
  -> replay reconstruction of consensus_tickets[ticket_id]
```

This amendment does not implement runtime code.

This amendment does not authorize runtime implementation until it is explicitly approved and merged.

---

## 2. Current Blocker

Implementation must not proceed while this condition is unresolved:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED
```

The blocker exists because:

```text
DistributedConsensusStmt is durable-unsupported.
consensus_tickets is in-memory interpreter state.
consensus_tickets is not persisted through _REPLAY_STATE_KEYS.
P3c-2 resolution requires an already-existing ticket projection.
```

---

## 3. Amendment Decision

If this amendment is approved, the approved P3c-N1 pending-ticket source is:

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

## 4. Scope Added by This Amendment

If approved, this amendment adds only the following design scope to P3c-N1:

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

## 5. Import Event Schema

If approved, P3c-N1 may add this domain event:

```json
{
  "type": "distributed_consensus_ticket_imported",
  "schema_version": "consensus.ticket.imported.event.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "bootstrap_id": "<string>",
  "coordinator": "global",
  "votes_hash": "sha256:<64-hex>",
  "ticket_import_hash": "sha256:<64-hex>",
  "ticket": {
    "ticket_id": "sha256:<64-hex>",
    "proposal_id": "sha256:<64-hex>",
    "statement_identity": "<string>",
    "participants": ["<participant>"],
    "missing_participants": ["<participant>"],
    "votes": {"<participant>": "yes | no | abstain | missing"},
    "vote_counts": {"yes": 0, "no": 0, "abstain": 0, "missing": 0},
    "votes_hash": "sha256:<64-hex>",
    "strategy": "MajorityVote | UnanimousVote | NoVetoVote",
    "policy": {},
    "quorum": 1,
    "timeout": null,
    "projection_state": "pending"
  }
}
```

The embedded `ticket` object must match the existing required pending-ticket projection fields.

The event must be closed-schema. Extra fields, missing fields, non-string mapping keys, and non-strict-JSON values must fail closed.

---

## 6. Required Import Validation

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

### 6.1 votes_hash recomputation

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

### 6.2 vote_counts recomputation

The imported `vote_counts` must be recomputed from `ticket["votes"]` across:

```text
yes
no
abstain
missing
```

Mismatch must fail closed.

### 6.3 Pending-only import

Imported ticket must satisfy:

```text
projection_state == pending
```

Resolved tickets must not be imported.

---

## 7. Import Idempotency / Conflict Policy

If approved, the import boundary must enforce:

```text
same ticket_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same ticket_id + different ticket_import_hash => conflict, fail closed
same bootstrap_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same bootstrap_id + different ticket_import_hash => conflict, fail closed
```

`ticket_id` is the primary consensus-domain idempotency and correlation key.

`bootstrap_id` is an additional delivery-level de-duplication key.

---

## 8. Replay Semantics

If approved, replay must reconstruct the pending ticket projection from recorded history:

```text
distributed_consensus_ticket_imported
```

Replay must not use live `consensus_tickets` as source of truth.

Replay must not require `consensus_tickets` in `_REPLAY_STATE_KEYS`.

Replay must validate the import event's closed schema, recompute `vote_counts`, recompute `votes_hash`, recompute `ticket_import_hash`, and reconstruct `consensus_tickets[ticket_id]` from the event's embedded `ticket`.

A `distributed_consensus_vote_received` event for a ticket must not be processed before the matching `distributed_consensus_ticket_imported` event.

Invalid ordering must fail closed.

---

## 9. Implementation Allowlist Amendment

If approved, the later runtime implementation remains constrained to the P3c-N1 allowlist:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_mailbox_collection_p3cn.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

`synapse/runtime/consensus_engine.py` remains excluded.

If recomputation needs engine-compatible hashing, implementation must reuse the existing canonical JSON / SHA-256 convention without changing `ConsensusEngine`.

If implementation cannot recompute `votes_hash` without touching `ConsensusEngine`, it must stop and report:

```text
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
```

---

## 10. Stop Gate Status

If this amendment is approved, this stop gate is resolved by the selected import-boundary mechanism:

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

## 11. Non-claims

This amendment does not claim production distributed consensus protocol behavior, persistent durable inbox, wall-clock scheduling, parser/AST/lexer expansion, or full P3 closure.

Distributed consensus remains `Partial`.

Overall P3 remains open.

---

## 12. Current Decision

```text
Decision: DRAFT AMENDMENT
Runtime implementation: NOT AUTHORIZED BY THIS PATCH
Approval required before runtime implementation: YES
Runtime code changes in this patch: NONE
Selected pending-ticket source mechanism: distributed_consensus_ticket_imported, pending approval
```

This amendment becomes implementation-authorizing only after explicit approval and merge under project review policy.
