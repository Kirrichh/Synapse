# RFC-CONSENSUS-P3CN1 Pending Ticket Import Approval Record

**Status:** APPROVED  
**Stage:** P3c-N1 pending-ticket import approval gate  
**Repository mutation:** DOCUMENTATION APPROVAL ONLY  
**Runtime implementation status:** AUTHORIZED AFTER THIS APPROVAL PATCH MERGES  
**Implementation PR allowed:** YES, after this approval patch merges  
**Product Owner sign-off:** Кирилл Раков  
**Target amendment:** `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_AMENDMENT.md`  
**Approval draft:** `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_APPROVAL_DRAFT.md`  
**Amendment merge PR:** `#55`  
**Amendment merge commit:** `d3070be9754d0a5694870610b3caef8574b87715`  
**Approval patch base SHA:** `d3070be9754d0a5694870610b3caef8574b87715`  
**Implementation base SHA:** the merge commit of this approval patch after it merges into `main`  
**Runtime code changes in this approval patch:** NONE

---

## 1. Approval Summary

This document approves the P3c-N1 pending ticket import amendment for later runtime implementation.

Approved state after this approval patch merges:

```text
Approval status: APPROVED
Runtime implementation status: AUTHORIZED AFTER THIS APPROVAL PATCH MERGES
Approved pending-ticket source: distributed_consensus_ticket_imported
Approved import delivery method: consensus_ticket_import mailbox message
Implementation PR allowed: YES, from the merge commit of this approval patch
```

This approval does not change runtime code. It only authorizes a later implementation PR constrained by this approval record and the referenced amendment.

---

## 2. Approved Source Mechanism

The approved P3c-N1 durable pending-ticket source is:

```text
distributed_consensus_ticket_imported
```

The approved delivery method for importing the ticket projection is:

```text
consensus_ticket_import
```

The import boundary is separate from vote response delivery.

The approved vote response method remains:

```text
consensus_vote_response
```

Vote responses must not carry full ticket projection fields.

---

## 3. Approved Import Contract

The later runtime implementation may add a pending ticket import boundary with this sequence:

```text
P2 mailbox_message envelope
  -> consensus_ticket_import inner method
  -> strict import payload validation
  -> full pending ticket projection validation
  -> vote_counts recompute-and-verify
  -> votes_hash recompute-and-verify
  -> ticket_import_hash computation
  -> idempotency/conflict check
  -> distributed_consensus_ticket_imported event
  -> replay reconstruction of consensus_tickets[ticket_id]
```

The import boundary may project the validated pending ticket into in-memory runtime state only after the event is recorded or replayed.

---

## 4. Required Import Validation

Runtime implementation must not trust imported integrity fields without recomputation.

Required validation pipeline:

```text
1. Validate the P2 mailbox envelope through the existing mailbox wait path.
2. Validate the consensus_ticket_import payload as strict closed JSON.
3. Validate coordinator == global.
4. Validate bootstrap_id is a string.
5. Validate ticket with validate_ticket_projection(ticket, allow_resolved=False).
6. Recompute vote_counts from ticket["votes"] and compare with ticket["vote_counts"].
7. Recompute votes_hash from ticket["votes"] using ticket["participants"] order and compare with ticket["votes_hash"].
8. Compute ticket_import_hash from the full normalized pending ticket projection.
9. Enforce duplicate/conflict policy.
10. Append distributed_consensus_ticket_imported only after all checks pass.
11. Reconstruct consensus_tickets[ticket_id] from the event during replay.
```

### 4.1 votes_hash recomputation

The imported `votes_hash` must be recomputed with this preimage:

```json
{
  "schema_version": "consensus.votes.v1",
  "votes": [["<participant>", "<vote>"]]
}
```

The `votes` list must be ordered by `ticket["participants"]`:

```python
[[participant, ticket["votes"][participant]] for participant in ticket["participants"]]
```

Hash algorithm:

```text
"sha256:" + sha256(canonical_json(votes_preimage)).hexdigest()
```

Mismatch must fail closed.

### 4.2 vote_counts recomputation

The imported `vote_counts` must be recomputed from `ticket["votes"]` across:

```text
yes
no
abstain
missing
```

Mismatch must fail closed.

### 4.3 Pending-only import

Imported tickets must satisfy:

```text
projection_state == pending
```

Resolved tickets must not be imported.

---

## 5. Approved Import Event

The later implementation may add this domain event:

```text
distributed_consensus_ticket_imported
```

Schema version:

```text
consensus.ticket.imported.event.v1
```

The event must contain enough canonical ticket data to reconstruct the pending ticket projection during replay without relying on live `consensus_tickets` or a new replay-state key.

The event must be closed-schema. Extra fields, missing fields, non-string mapping keys, and non-strict-JSON values must fail closed.

---

## 6. Approved Replay Semantics

Replay must reconstruct the pending ticket projection from recorded history:

```text
distributed_consensus_ticket_imported
```

Replay must not use live `consensus_tickets` as source of truth.

Replay must not require `consensus_tickets` in `_REPLAY_STATE_KEYS`.

Replay must validate the import event's closed schema, recompute `vote_counts`, recompute `votes_hash`, recompute `ticket_import_hash`, and reconstruct `consensus_tickets[ticket_id]` from the event's embedded ticket.

A `distributed_consensus_vote_received` event for a ticket must not be processed before the matching `distributed_consensus_ticket_imported` event.

Invalid ordering must fail closed.

---

## 7. Approved Idempotency / Conflict Policy

Ticket import must enforce:

```text
same ticket_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same ticket_id + different ticket_import_hash => conflict, fail closed
same bootstrap_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same bootstrap_id + different ticket_import_hash => conflict, fail closed
```

`ticket_id` is the primary consensus-domain idempotency and correlation key.

`bootstrap_id` is an additional delivery-level de-duplication key.

---

## 8. Approved Implementation Allowlist

The later runtime implementation may touch only the already-approved P3c-N1 implementation files unless a separate approval is merged:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_mailbox_collection_p3cn.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

`synapse/runtime/consensus_engine.py` remains excluded.

If recomputation cannot be implemented through existing canonical JSON / SHA-256 conventions without changing `ConsensusEngine`, implementation must stop and report:

```text
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
```

---

## 9. Stop Gate Status

This approval resolves:

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

## 10. Explicit Non-authorization

This approval does not authorize:

```text
fresh durable DistributedConsensusStmt execution
vote request delivery
network transport
daemon transport
persistent durable inbox
early delivery before active receive boundary
durable wall-clock timer service
scheduler timeout
public ticket API
parser expansion
lexer expansion
AST expansion
multi-pattern ReceiveBlock
general durable loops
general durable member access
live LLM vote production
production distributed consensus protocol behavior
overall P3 closure
adding consensus_tickets to _REPLAY_STATE_KEYS
changing artifact_schema_version
embedding ticket projection inside consensus_vote_response
changing ConsensusEngine
```

---

## 11. Required Runtime Tests

The later runtime implementation must cover at least:

```text
valid ticket import payload
invalid ticket import kind/schema/missing field/extra field
resolved ticket import rejected
non-pending ticket import rejected
votes_hash recompute mismatch rejected
vote_counts recompute mismatch rejected
same ticket_id same ticket_import_hash idempotent
same ticket_id different ticket_import_hash conflict
same bootstrap_id same ticket_import_hash idempotent
same bootstrap_id different ticket_import_hash conflict
import event replay reconstructs consensus_tickets[ticket_id]
vote response before ticket import rejected
valid vote response after ticket import accepted
partial collection remains non-terminal
full collection calls resolve_pending_ticket with Mapping[str, str]
message_received remains transport evidence
distributed_consensus_ticket_imported emitted only after validation
distributed_consensus_vote_received emitted only after validation
replay ordering import before vote received enforced
P2 mailbox wait regressions
P3c-2 ticket resolution regressions
```

---

## 12. Non-claims

This approval does not claim production distributed consensus protocol behavior, persistent durable inbox, wall-clock scheduling, parser/AST/lexer expansion, or full P3 closure.

Distributed consensus remains `Partial`.

Overall P3 remains open.

---

## 13. Current Decision

```text
Decision: APPROVED
Runtime implementation: AUTHORIZED AFTER THIS APPROVAL PATCH MERGES
Runtime code changes in this approval patch: NONE
Selected pending-ticket source mechanism: distributed_consensus_ticket_imported
Implementation base SHA: merge commit of this approval patch after it merges into main
```
