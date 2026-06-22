# RFC-CONSENSUS-P3CN1 — Local Mailbox-backed Vote Response Collection for Existing Pending Consensus Tickets

**Status:** DRAFT AMENDMENT  
**Stage:** P3c-N1 RFC amendment for durable pending-ticket source  
**Repository mutation:** DOCUMENTATION RFC AMENDMENT ONLY  
**Implementation status:** NOT AUTHORIZED BY THIS PATCH  
**Approval status:** NOT APPROVED FOR IMPLEMENTATION UNTIL APPROVAL RECORD IS UPDATED AND MERGED  
**Target capability:** Add local mailbox-backed vote response collection for already-created pending consensus tickets.  
**Amendment target:** Resolve `PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED`.  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Fresh durable `DistributedConsensusStmt` execution:** NOT IN SCOPE  
**Vote request delivery:** NOT IN SCOPE FOR P3c-N1 unless separately approved  
**Network / daemon transport:** NOT IN SCOPE  
**Persistent durable inbox / early delivery:** NOT IN SCOPE  
**Durable timer / wall-clock scheduler:** NOT IN SCOPE  
**Parser / AST / lexer expansion:** NOT IN SCOPE  
**Replay-state schema bump:** NOT IN SCOPE  
**Primary dependency:** P2 mailbox wait durable lifecycle closed for approved P2 receive-wait scope.  
**Primary design rule:** P3c-N1 adds a runtime-domain mailbox vote response collection layer for existing pending tickets. It does not replace `ConsensusEngine`.

---

## 0. Source Anchors

This RFC is grounded in committed project facts and durable workflow / messaging design constraints.

### Repository anchors

- `[S1]` P2 mailbox wait supports durable `awaiting_message` and `awaiting_message_or_timeout`.
- `[S2]` P2 mailbox wait validates external `mailbox_message` / `mailbox_timeout`, normalizes mailbox signal hashes, validates replayed receive events, and enforces receiver binding.
- `[S3]` The durable subset forbids general loops, general member access, and durable `DistributedConsensusStmt` execution.
- `[S4]` The actual consensus AST node is `DistributedConsensusStmt`, not `DistributedConsensusExpr`.
- `[S5]` `ConsensusEngine` owns deterministic proposal preparation, vote collection through `VoteSource`, canonical hashing, result construction, and ticket creation.
- `[S6]` `ConsensusEngine.resolve_pending_ticket(...)` owns terminal pending-ticket reduction.
- `[S7]` P3c-2 ticket resolution validates resolution votes and produces terminal deterministic resolution events, but it expects an already-existing ticket projection.
- `[S8]` `consensus_tickets` is an in-memory interpreter projection and is not a persisted replay-state key.
- `[S9]` `validate_ticket_projection(...)` validates ticket projection structure, but import boundaries must additionally recompute and verify integrity fields derived from votes.
- `[S10]` P3c-0, P3c-1 and P3c-2 are evidence-closed, but Distributed consensus remains Partial and overall P3c remains open.

### External durable-pattern anchors

- `[E1]` Temporal Signal-With-Start separates Workflow arguments from Signal arguments and starts/signals a Workflow by Workflow Id if it is not already running.
- `[E2]` Temporal Workflow Event History is the replay source of truth for accepted signals / updates and deterministic workflow reconstruction.
- `[E3]` Azure Durable Functions external events are asynchronous signals delivered to an existing orchestration instance and require orchestration-side de-duplication when duplicate delivery is possible.
- `[E4]` AWS Step Functions callback-token integration illustrates correlation-token based resumption of a waiting workflow state, but does not replace the need for an authoritative workflow state source.
- `[E5]` Messaging systems require correlation identifiers and idempotent receivers above the transport layer.

---

## 1. Purpose

This RFC defines the first mailbox-backed consensus substage after P3c-2:

```text
P3c-N1 — Local Mailbox-backed Vote Response Collection for Existing Pending Consensus Tickets
```

P3c-N1 exists because the runtime now has durable mailbox wait mechanics, and P3c-2 already has deterministic pending-ticket resolution, but there is not yet a consensus-domain layer that can consume mailbox-delivered vote responses and convert them into validated resolution votes.

This amendment adds the missing durable source-of-truth boundary for the phrase:

```text
existing pending consensus ticket
```

The missing boundary is named:

```text
P3c-N1 Pending Ticket Import Boundary
```

This amendment does not authorize runtime implementation by itself. It defines the RFC-level mechanism that a later approval record must authorize before runtime implementation proceeds.

---

## 2. Executive Summary

P3c-N1 cannot safely assume that `consensus_tickets[ticket_id]` already exists inside a durable mailbox receive run.

The current runtime facts are:

```text
consensus_tickets is in-memory interpreter state
consensus_tickets is not persisted through _REPLAY_STATE_KEYS
DistributedConsensusStmt is unsupported by the durable validator
P3c-2 resolution expects an already-existing ticket projection
```

Therefore the first P3c-N1 runtime implementation needs an explicit durable pending-ticket source before it can collect mailbox vote responses.

This amendment selects one mechanism:

```text
A separate consensus_ticket_import mailbox message imports a full pending ticket projection.
The runtime validates it and records distributed_consensus_ticket_imported in execution_history.
Replay reconstructs the in-memory pending ticket projection from the recorded import event.
```

This is analogous to mature durable systems where start/import state and external signals are separate inputs to a durable execution. The ticket import is the start/import state; the later vote response is an external signal against that state.

P3c-N1 remains pending-ticket-only. It still does not implement fresh durable `DistributedConsensusStmt`, vote request delivery, parser/AST/lexer changes, network/daemon transport, persistent inboxes, timers, or production distributed consensus protocol behavior.

---

## 3. Relationship to Prior Stages

### 3.1 P3a

P3a delivered the deterministic semantic consensus core:

- `ConsensusEngine`;
- `ConsensusRequest`;
- `VoteSource`;
- approved vote states;
- approved strategies;
- proposal identity;
- vote counting;
- outcome/reason derivation;
- canonical `votes_hash`;
- canonical `result_hash`.

P3c-N1 must preserve this core.

### 3.2 P3b

P3b connected local actor-method voting through `ActorMethodVoteSource`.

That path is synchronous and local. It is not mailbox-backed vote delivery or durable mailbox response collection.

P3c-N1 does not delete P3b. It adds a different vote ingestion path for existing pending tickets.

### 3.3 P3c-0 / P3c-1 / P3c-2

P3c-0 closed replay consumption for recorded `distributed_consensus_decided`.

P3c-1 closed durable consensus ticket creation and replay for the already-approved consensus path.

P3c-2 closed durable ticket resolution through an existing P2 external-signal boundary.

P3c-N1 reuses the terminal resolution semantics from P3c-2 after mailbox-collected vote responses cover the pending ticket's full `missing_participants` set.

This amendment adds the missing bridge between a durable P2 mailbox wait run and the pending ticket projection that P3c-N1 must bind vote responses to.

### 3.4 P2 mailbox wait

P2 mailbox wait closed the generic durable receive-wait lifecycle.

P3c-N1 depends on this, but P2 mailbox wait does not itself define consensus-specific ticket import, vote response schemas, participant validation, duplicate vote policy, or pending-ticket collection lifecycle.

---

## 4. Current Code Facts

### 4.1 `DistributedConsensusStmt` remains unsupported in the durable subset

The durable validator currently classifies `DistributedConsensusStmt` as `UNSUPPORTED_EXECUTION_ENGINE`.

Therefore P3c-N1 must not create the pending ticket by executing `DistributedConsensusStmt` inside the durable run.

### 4.2 General durable DSL collection is not available

The durable subset still rejects general loop/member-access shapes needed by a user-authored vote-collection loop.

Therefore P3c-N1 cannot depend on `.syn` code that loops over vote responses or extracts fields through `msg.vote`.

Consensus-domain vote extraction must happen in runtime-domain validation code.

### 4.3 `consensus_tickets` is not durable artifact state

`consensus_tickets` exists as interpreter state, but it is not in `_REPLAY_STATE_KEYS`.

Therefore durable resume/replay must not assume that `consensus_tickets[ticket_id]` survives through artifact serialization.

P3c-N1 must reconstruct the pending ticket projection from recorded durable history.

### 4.4 P3c-2 resolution expects an existing ticket

P3c-2 validates a resolution request/signal against an existing `consensus_tickets[ticket_id]` projection. It does not create the pending ticket projection from the resolution signal.

This is why P3c-N1 needs a ticket import boundary before vote response collection.

### 4.5 `ConsensusEngine` is a deterministic core, not a mailbox collector

`ConsensusEngine.decide()` prepares a proposal, collects votes through the `VoteSource` seam, derives counts, computes outcome/reason, builds hashes, and creates a deterministic result/event payload.

`ConsensusEngine.resolve_pending_ticket()` deterministically merges final votes into a pending ticket and produces the terminal resolution event.

P3c-N1 must preserve the engine's ownership of final reduction.

---

## 5. Problem Statement

Current P3 consensus can:

- create deterministic consensus decisions;
- represent missing votes as `missing`;
- create pending consensus tickets in already-approved consensus execution paths;
- replay consensus decisions;
- replay ticket creation in the approved consensus path;
- resolve a pending ticket through a P2 external signal when the ticket is already projected.

Current P3 consensus cannot yet:

- provide an approved pending-ticket source inside a durable P2 mailbox wait run;
- consume mailbox-delivered vote responses for that pending ticket;
- bind vote responses to pending ticket `missing_participants`;
- validate participant-to-mailbox identity for vote responses;
- enforce one vote per participant at domain level;
- accumulate collected votes across one or more durable mailbox resume cycles;
- replay mailbox-backed vote collection as a consensus-domain sequence;
- convert mailbox vote responses into P3c-2-compatible `resolution_votes`;
- resolve a pending ticket from mailbox-collected votes.

This amendment resolves the first missing item by defining a durable pending-ticket import boundary.

---

## 6. Non-goals

P3c-N1 and this amendment must not implement or claim:

```text
fresh durable DistributedConsensusStmt execution
initial mailbox-backed vote request delivery
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
Raft / Paxos / Tendermint / PBFT
Byzantine fault tolerance
leader election
view-change protocol
production distributed consensus protocol behavior
overall P3 closure
```

This amendment also does not authorize:

```text
adding consensus_tickets to _REPLAY_STATE_KEYS
changing artifact_schema_version
embedding a full ticket projection inside consensus_vote_response
changing ConsensusEngine
```

---

## 7. Architectural Principles

### 7.1 Pending-ticket-only first slice

P3c-N1 works only with an imported existing pending consensus ticket.

A pending ticket is not created by P3c-N1.

A pending ticket is made available to a durable mailbox wait run only by the import boundary defined in this amendment:

```text
consensus_ticket_import mailbox message
  -> strict ticket projection validation
  -> distributed_consensus_ticket_imported event
  -> in-memory consensus_tickets[ticket_id] reconstructed from history
```

### 7.2 Runtime-domain collection, not DSL collection

P3c-N1 must not require `.syn` code to parse `msg.vote`, loop over participants, mutate a collection, or determine completion.

The ticket import and vote response are delivered through P2 mailbox wait, but consensus-domain extraction and validation happen in runtime-domain code.

### 7.3 Engine-owned final reduction

P3c-N1 converts collected mailbox vote responses into canonical `resolution_votes`.

When and only when the collected valid votes cover the full pending ticket `missing_participants` set, the runtime calls:

```text
ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes)
```

The engine remains responsible for final vote counts, outcome, reason, hashes and public result shape.

### 7.4 Closed-schema events

Every new P3c-N1 durable event must use a closed schema.

Extra fields, missing fields, non-string mapping keys and non-strict-JSON values must fail closed during validation and replay.

### 7.5 Transport evidence vs domain evidence

Generic P2/actor events such as `message_received` prove that a mailbox message was consumed.

They do not prove that the message was a valid consensus ticket import or vote response.

P3c-N1 therefore uses domain events:

```text
distributed_consensus_ticket_imported
distributed_consensus_vote_received
```

P3c-N1 must not add `distributed_consensus_vote_requested`, because vote request delivery is not in scope.

---

## 8. Pending Ticket Import Boundary

### 8.1 Message method

The approved import boundary uses a separate mailbox message method:

```text
consensus_ticket_import
```

This method is distinct from:

```text
consensus_vote_response
```

A vote response must not carry the full ticket projection.

### 8.2 Import payload schema

The import payload must be strict JSON and closed-schema:

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

### 8.3 Import event schema

After successful validation, runtime records a domain event:

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
    "...": "full validated pending ticket projection"
  }
}
```

The event must contain enough canonical ticket data to reconstruct the pending ticket projection during replay without relying on live `consensus_tickets` or a new replay-state key.

### 8.4 Import validation pipeline

Runtime implementation must validate ticket import in this order:

```text
1. Validate the P2 mailbox envelope through the existing mailbox wait path.
2. Validate the import payload as strict closed JSON.
3. Validate coordinator == "global".
4. Validate bootstrap_id is a string.
5. Validate ticket with validate_ticket_projection(ticket, allow_resolved=False).
6. Recompute vote_counts from ticket["votes"] and compare with ticket["vote_counts"].
7. Recompute votes_hash from ticket["votes"] using ticket["participants"] order and compare with ticket["votes_hash"].
8. Compute ticket_import_hash from the full normalized ticket projection.
9. Enforce idempotency/conflict policy.
10. Append distributed_consensus_ticket_imported.
11. Project in-memory consensus_tickets[ticket_id] from the validated event.
```

### 8.5 votes_hash recomputation

The import boundary must not trust the provided `votes_hash`.

Runtime implementation must recompute it using the same canonical profile as consensus engine vote hashing:

```json
{
  "schema_version": "consensus.votes.v1",
  "votes": [["<participant>", "<vote>"]]
}
```

The `votes` list must be ordered by the ticket's `participants` order:

```python
[[participant, ticket["votes"][participant]] for participant in ticket["participants"]]
```

Hash algorithm:

```text
"sha256:" + sha256(canonical_json(votes_preimage)).hexdigest()
```

Mismatch between recomputed hash and `ticket["votes_hash"]` must fail closed.

### 8.6 vote_counts recomputation

The import boundary must not trust the provided `vote_counts`.

Runtime implementation must recompute counts from `ticket["votes"]` across the approved vote states:

```text
yes
no
abstain
missing
```

Mismatch between recomputed counts and `ticket["vote_counts"]` must fail closed.

### 8.7 Pending-only import

Imported tickets must satisfy:

```text
projection_state == pending
```

Resolved tickets must not be imported through this boundary.

### 8.8 Idempotency and conflict policy

For ticket import:

```text
same ticket_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same ticket_id + different ticket_import_hash => conflict, fail closed
same bootstrap_id + same ticket_import_hash => idempotent no-op or replay-equivalent no mutation
same bootstrap_id + different ticket_import_hash => conflict, fail closed
```

`ticket_id` is the primary idempotency/correlation key.

`bootstrap_id` provides an additional delivery-level de-duplication key.

---

## 9. Vote Response Boundary

Vote response remains narrow and separate from ticket import.

P3c-N1 accepts only strict JSON inner payloads with this closed schema:

```json
{
  "kind": "consensus_vote_response",
  "schema_version": "consensus.vote.response.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "participant": "<participant-id>",
  "participant_mailbox": "<mailbox-delivery-id-or-null>",
  "coordinator": "global",
  "vote": "yes | no | abstain",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "response_id": "<string>"
}
```

Vote response must not include ticket projection fields.

Vote response must fail closed if no imported pending ticket exists for its `ticket_id`.

---

## 10. Response Hash

Mailbox vote response hashing remains defined by the response payload, not by the imported ticket.

Approved response hash preimage:

```json
{
  "schema_version": "consensus.vote.response.hash.v1",
  "ticket_id": "<ticket_id>",
  "proposal_id": "<proposal_id>",
  "participant": "<participant_id>",
  "participant_mailbox": "<participant_mailbox_or_null>",
  "coordinator": "global",
  "vote": "yes | no | abstain",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "response_id": "<string>"
}
```

Hash algorithm:

```text
response_hash = "sha256:" + sha256(canonical_json(response_hash_preimage)).hexdigest()
```

The implementation must not include `response_hash` inside its own preimage.

---

## 11. P2 Mailbox Envelope Handling

Do not modify the P2 `mailbox_message` envelope.

P3c-N1 interprets only the first strict JSON argument inside the internal mailbox message.

Approved consensus-domain methods are:

```text
consensus_ticket_import
consensus_vote_response
```

For any other mailbox method, P3c-N1 must not interfere.

---

## 12. Replay Semantics

### 12.1 Import replay

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

### 12.2 Vote response replay

A `distributed_consensus_vote_received` event for a ticket must not be consumed before the matching `distributed_consensus_ticket_imported` event.

If event ordering is invalid, replay must fail closed.

### 12.3 History as source of truth

For P3c-N1, pending ticket projection is replay-derived from `distributed_consensus_ticket_imported` event history.

It is not replay-derived from a live runtime dictionary and is not persisted through `_REPLAY_STATE_KEYS`.

---

## 13. Collection and Resolution Rules

After ticket import, vote collection follows the existing P3c-N1 rules:

```text
validate vote response
bind to imported pending ticket
validate participant is in missing_participants
apply participant-level duplicate policy
accumulate accepted yes/no/abstain votes
call ConsensusEngine.resolve_pending_ticket only after full missing_participants coverage
```

Before terminal resolution, implementation must prove:

```text
set(resolution_votes.keys()) == set(ticket_payload["missing_participants"])
```

Partial collection must remain non-terminal.

Timeout before full coverage must remain non-terminal.

---

## 14. Duplicate Vote Policy

Same participant, same ticket, same proposal, same response hash:

```text
idempotent no-op or replay-equivalent no mutation
```

Same participant, same ticket, same proposal, different response hash:

```text
conflict, fail closed before projection mutation
```

Same vote with different reason is not equivalent.

---

## 15. Interpreter Integration Contract

The intended integration remains inside the existing durable async `ReceiveBlock` path:

```text
after message = mailbox.pop(0)
after message_received transport event append
before apply_receive_patterns(...)
```

If method is `consensus_ticket_import`, delegate import validation/projection to the P3c-N1 runtime-domain module.

If method is `consensus_vote_response`, require an already imported pending ticket and delegate response validation/collection to the P3c-N1 runtime-domain module.

For non-consensus messages, preserve the existing `apply_receive_patterns(...)` behavior.

P3c-N1 collection must not depend on user-authored `ReceivePattern` body logic.

---

## 16. Stop Gates

Runtime implementation must stop if any of these becomes necessary:

```text
FRESH_DISTRIBUTED_CONSENSUS_DURABLE_EXECUTION_REQUIRED
VOTE_REQUEST_DELIVERY_REQUIRED
PARSER_AST_LEXER_CHANGE_REQUIRED
REPLAY_STATE_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
P2_ARTIFACT_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
DURABLE_TIMER_OR_SCHEDULER_REQUIRED
PERSISTENT_DURABLE_INBOX_REQUIRED
DURABLE_DSL_BODY_DEPENDENCY_REQUIRED
GENERAL_MEMBER_ACCESS_REQUIRED
TICKET_IMPORT_SCHEMA_UNDEFINED
TICKET_IMPORT_HASH_PREIMAGE_UNDEFINED
TICKET_IMPORT_VOTES_HASH_MISMATCH
TICKET_IMPORT_VOTE_COUNTS_MISMATCH
TICKET_IMPORT_DUPLICATE_POLICY_UNDEFINED
TICKET_IMPORT_REPLAY_ORDER_UNDEFINED
VOTE_COLLECTION_PROJECTION_UNDEFINED
MAILBOX_VOTES_TO_RESOLUTION_VOTES_CONVERSION_UNDEFINED
PARTIAL_TIMEOUT_TERMINAL_RESOLUTION_REQUESTED
PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

Passing tests does not override stop gates.

---

## 17. Required Tests for Later Runtime Implementation

The later runtime PR must test at least:

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

## 18. Documentation / Evidence Requirements

This amendment does not close P3c-N1 evidence.

After this amendment is merged and separately approved, a later runtime PR may implement the import boundary and vote response collection.

After runtime merge, a separate evidence patch must update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Distributed consensus must remain `Partial` unless a later approved production protocol stage is also completed.

---

## 19. Amendment Decision

This RFC amendment selects:

```text
Durable pending ticket source mechanism: distributed_consensus_ticket_imported event
Import delivery method: consensus_ticket_import mailbox message
Replay source: execution_history event reconstruction
Ticket projection in vote response: forbidden
Replay-state schema bump: not approved
Fresh durable DistributedConsensusStmt: not approved
```

This amendment resolves the design-level stop gate:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED
```

but does not authorize runtime implementation until the approval record is updated and merged.
