# RFC-CONSENSUS-P3CN1 Pending Ticket Import Amendment

**Status:** DRAFT AMENDMENT  
**Repository mutation:** DOCUMENTATION RFC AMENDMENT ONLY  
**Runtime implementation status:** NOT AUTHORIZED BY THIS DOCUMENT  
**Parent RFC:** `docs/RFC-CONSENSUS-P3CN1.md`  
**Amendment target:** Define the durable pending-ticket source required by P3c-N1.  
**Resolved design stop gate if approved:** `PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED`

---

## 1. Purpose

This amendment adds the missing source-of-truth boundary for the phrase used by P3c-N1:

```text
existing pending consensus ticket
```

The parent RFC assumes that a pending ticket is available before mailbox vote collection begins. Current code makes that assumption unsafe for a durable P2 mailbox wait run because:

```text
consensus_tickets is in-memory interpreter state
consensus_tickets is not persisted through _REPLAY_STATE_KEYS
DistributedConsensusStmt is durable-unsupported
P3c-2 resolution requires an already-existing ticket projection
```

Therefore P3c-N1 needs an explicit durable pending-ticket source before it can validate and collect mailbox vote responses.

---

## 2. Selected Mechanism

This amendment selects a separate ticket import boundary:

```text
consensus_ticket_import mailbox message
  -> strict ticket projection validation
  -> distributed_consensus_ticket_imported event
  -> replay reconstruction of consensus_tickets[ticket_id]
```

This is aligned with durable workflow practice where start/import state is recorded separately from later external events. In this design:

```text
consensus_ticket_import = state import / bootstrap message
consensus_vote_response = later external vote response event
```

The import boundary is distinct from vote response delivery.

Vote responses must not carry full ticket projection fields.

---

## 3. Non-authorization

This amendment does not authorize:

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

## 4. Import Message Schema

The import message is delivered through the existing P2 mailbox wait path. P2 validates the mailbox envelope. P3c-N1 validates the consensus-domain payload.

The consensus-domain import payload is strict JSON and closed-schema:

```json
{
  "kind": "consensus_ticket_import",
  "schema_version": "consensus.ticket.import.v1",
  "bootstrap_id": "<string>",
  "coordinator": "global",
  "ticket": {
    "ticket_id": "sha256:<64-hex>",
    "proposal_id": "sha256:<64-hex>",
    "statement_identity": "<string>",
    "participants": ["<participant>"],
    "missing_participants": ["<participant>"],
    "votes": {
      "<participant>": "yes | no | abstain | missing"
    },
    "vote_counts": {
      "yes": 0,
      "no": 0,
      "abstain": 0,
      "missing": 0
    },
    "votes_hash": "sha256:<64-hex>",
    "strategy": "MajorityVote | UnanimousVote | NoVetoVote",
    "policy": {},
    "quorum": 1,
    "timeout": null,
    "projection_state": "pending"
  }
}
```

The `ticket` object intentionally matches the required pending-ticket projection fields used by the existing ticket projection validator.

---

## 5. Import Event Schema

After successful validation, runtime records this domain event:

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
    "votes": {
      "<participant>": "yes | no | abstain | missing"
    },
    "vote_counts": {
      "yes": 0,
      "no": 0,
      "abstain": 0,
      "missing": 0
    },
    "votes_hash": "sha256:<64-hex>",
    "strategy": "MajorityVote | UnanimousVote | NoVetoVote",
    "policy": {},
    "quorum": 1,
    "timeout": null,
    "projection_state": "pending"
  }
}
```

The event must be self-contained enough to reconstruct the pending ticket projection during replay without relying on live `consensus_tickets` or a new replay-state key.

The event must be closed-schema. Extra fields, missing fields, non-string mapping keys, and non-strict-JSON values must fail closed.

---

## 6. Import Validation Pipeline

Runtime implementation must not trust imported integrity fields without recomputation.

The import validation pipeline is:

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
11. Project in-memory consensus_tickets[ticket_id] from the validated event.
```

### 6.1 votes_hash recomputation

`validate_ticket_projection(...)` validates projection structure, but it does not by itself prove that `votes_hash` was derived from the submitted `votes` map.

Therefore the import boundary must recompute `votes_hash`.

Preimage:

```json
{
  "schema_version": "consensus.votes.v1",
  "votes": [["<participant>", "<vote>"]]
}
```

The ordered `votes` list is:

```python
[[participant, ticket["votes"][participant]] for participant in ticket["participants"]]
```

Hash algorithm:

```text
"sha256:" + sha256(canonical_json(votes_preimage)).hexdigest()
```

The canonical JSON helper must follow the existing repository convention used by consensus hashing.

Mismatch between recomputed hash and `ticket["votes_hash"]` must fail closed.

### 6.2 vote_counts recomputation

The import boundary must recompute `vote_counts` from `ticket["votes"]` across:

```text
yes
no
abstain
missing
```

Mismatch between recomputed counts and `ticket["vote_counts"]` must fail closed.

### 6.3 Pending-only import

Imported tickets must satisfy:

```text
projection_state == pending
```

Resolved tickets must not be imported through this boundary.

---

## 7. Import Idempotency and Conflict Policy

For ticket import:

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

During live execution:

```text
P2 validates mailbox envelope
runtime validates consensus_ticket_import payload
runtime appends distributed_consensus_ticket_imported
runtime projects consensus_tickets[ticket_id] in memory
```

During replay:

```text
runtime consumes recorded message_received transport event through existing receive replay semantics
runtime consumes recorded distributed_consensus_ticket_imported in order
runtime validates closed schema
runtime recomputes vote_counts
runtime recomputes votes_hash
runtime recomputes ticket_import_hash
runtime reconstructs consensus_tickets[ticket_id] from event.ticket
```

Replay must not poll live mailboxes, call actor vote methods, execute fresh `DistributedConsensusStmt`, or use a new replay-state key.

A `distributed_consensus_vote_received` event for a ticket must not be consumed before the matching `distributed_consensus_ticket_imported` event.

Invalid event ordering must fail closed.

---

## 9. Boundary with Vote Response

The existing P3c-N1 vote response schema remains narrow.

`consensus_vote_response` must reference:

```text
ticket_id
proposal_id
participant
vote
response_id
```

It must not carry full ticket projection fields.

A vote response for a ticket without a preceding valid `distributed_consensus_ticket_imported` event must fail closed.

---

## 10. Stop Gate Resolution

If this amendment is approved, the following design stop gate is resolved by the selected import-boundary mechanism:

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

## 11. Required Runtime Tests After Approval

A later runtime implementation must test at least:

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

## 12. Current Amendment Decision

```text
Decision: DRAFT AMENDMENT
Runtime implementation: NOT AUTHORIZED BY THIS DOCUMENT
Approval required before runtime implementation: YES
Runtime code changes in this amendment: NONE
Selected pending-ticket source mechanism: distributed_consensus_ticket_imported, pending approval
```
