# RFC-CONSENSUS-P3C-TICKET-LIFECYCLE — Terminal Ticket Lifecycle for Cancelled and Expired Consensus Tickets

**Status:** APPROVED  
**Stage:** P3c Ticket Lifecycle RFC  
**Repository mutation:** DOCUMENTATION RFC ONLY  
**Implementation status:** AUTHORIZED ONLY AFTER THE COMPANION APPROVAL PATCH MERGES  
**Approval status:** APPROVED BY COMPANION APPROVAL RECORD  
**Target capability:** Add deterministic terminal lifecycle states for existing consensus tickets: `cancelled` and `expired`.  
**Product Owner sign-off:** Кирилл Раков  
**Production distributed consensus protocol status:** NOT CLAIMED  
**P3c-N1 status:** CLOSED / POST_MERGE_ACCEPTED / EVIDENCE CLOSED  
**P3c-N2 status:** NOT IN SCOPE  
**Fresh durable `DistributedConsensusStmt` execution:** NOT IN SCOPE  
**Vote request delivery:** NOT IN SCOPE  
**Network / daemon transport:** NOT IN SCOPE  
**Persistent durable inbox / early delivery:** NOT IN SCOPE  
**Durable timer / wall-clock scheduler:** NOT IN SCOPE  
**Parser / AST / lexer expansion:** NOT IN SCOPE  
**Primary design rule:** Ticket lifecycle changes ticket projection state only. It must not alter consensus mathematics, vote request delivery, actor runtime delivery, durable execution classification, or parser surface.

---

## 0. Purpose

This RFC defines the ticket lifecycle closure contract for consensus tickets that already exist in runtime projection state.

The new lifecycle states are:

```text
cancelled
expired
```

The approved transition target is:

```text
pending -> cancelled
pending -> expired
```

This RFC does not reopen P3c-N1. P3c-N1 is already closed. P3c-N1 closed pending-ticket import and local mailbox-backed vote response collection.

This RFC also does not implement P3c-N2. P3c-N2 remains future work for fresh `DistributedConsensusStmt` mailbox-backed vote request delivery and initial collection.

---

## 1. Current Repository Facts

### 1.1 Closed prerequisite: P3c-N1

P3c-N1 is closed and provides the current local mailbox-backed response collection layer for existing pending tickets.

Closed P3c-N1 capabilities include:

```text
consensus_ticket_import validation
pending-ticket projection import
vote_counts / votes_hash recomputation
distributed_consensus_ticket_imported event
consensus_vote_response validation
distributed_consensus_vote_received event
local mailbox-backed response collection
full-coverage-only resolution through ConsensusEngine.resolve_pending_ticket(...)
```

P3c-N1 does not need to be reimplemented or re-approved by this RFC.

### 1.2 Current ticket projection states

Current ticket projections support:

```text
pending
resolved
```

Current resolution flow transitions:

```text
pending -> resolved
```

The current transition pattern is:

```text
validate pending ticket projection
validate terminal event schema
check identity fields
build terminal projection
project into consensus_tickets[ticket_id]
```

This RFC extends that pattern to `cancelled` and `expired`.

### 1.3 Current collection projection states

P3c-N1 collection projection has a separate state model:

```text
collecting
coverage_complete
```

These are collection states, not ticket states.

This RFC must not mix collection projection state with ticket projection state.

Ticket lifecycle states are:

```text
pending
resolved
cancelled
expired
```

Collection states remain:

```text
collecting
coverage_complete
```

### 1.4 Existing vote-response pending guard

The current vote-response path accepts responses only for a runtime ticket whose `projection_state` is `pending`.

Therefore, once a ticket is projected as `cancelled` or `expired`, subsequent `consensus_vote_response` messages must fail closed through the existing pending-ticket guard.

This RFC requires regression tests to prove that expanding accepted ticket states does not make pending-only gates accept terminal tickets.

### 1.5 Durable `DistributedConsensusStmt` remains unsupported

This RFC does not change durable classification for `DistributedConsensusStmt`.

Fresh durable `DistributedConsensusStmt` execution remains out of scope.

---

## 2. Scope

This RFC authorizes later implementation of externally injected lifecycle commands that add:

```text
pending -> cancelled
pending -> expired
```

### In scope

```text
consensus_ticket_cancel command validation
consensus_ticket_expire command validation
distributed_consensus_ticket_cancelled durable event
distributed_consensus_ticket_expired durable event
cancelled ticket projection
expired ticket projection
terminal transition validation
idempotent duplicate handling
conflicting duplicate rejection
replay validation and projection reconstruction
vote-response rejection after non-pending terminal state
evidence and matrix updates after implementation merge
```

### Out of scope

```text
P3c-N1 reimplementation
P3c-N2
fresh durable DistributedConsensusStmt execution
vote request delivery
distributed_consensus_vote_requested
network transport
daemon transport
persistent durable inbox
early delivery before active receive boundary
automatic timeout
durable wall-clock timer
scheduler
background expiration scan
daemon-driven clock
public ticket API
parser expansion
lexer expansion
AST expansion
actor_runtime.py delivery changes
ConsensusEngine mathematics changes
production distributed consensus protocol behavior
overall P3 production claim
```

---

## 3. State Model

### 3.1 Ticket states

The ticket projection state set becomes:

```text
pending
resolved
cancelled
expired
```

### 3.2 Terminal states

Terminal ticket states are:

```text
resolved
cancelled
expired
```

A terminal ticket cannot be reactivated.

### 3.3 Allowed transitions

```text
pending -> resolved
pending -> cancelled
pending -> expired
```

`pending -> resolved` is already implemented through P3c-2 / P3c-N1 resolution paths.

This RFC adds only:

```text
pending -> cancelled
pending -> expired
```

### 3.4 Forbidden transitions

The following transitions are forbidden and must fail closed:

```text
resolved -> cancelled
resolved -> expired
cancelled -> resolved
cancelled -> expired
expired -> resolved
expired -> cancelled
resolved -> resolved with different terminal payload
cancelled -> cancelled with different terminal payload
expired -> expired with different terminal payload
```

The only accepted repeat terminal operation is an idempotent duplicate with the same terminal action hash.

---

## 4. Validation Model

### 4.1 Required validator change

Current ticket projection validation uses a binary `allow_resolved` model.

The implementation must avoid broadening pending-only contexts accidentally.

The implementation must either:

```text
replace allow_resolved with explicit allowed_states
```

or add internal helpers equivalent to:

```text
validate_ticket_projection(ticket, allowed_states={"pending"})
validate_ticket_projection(ticket, allowed_states={"pending", "resolved", "cancelled", "expired"})
```

The naming is implementation-defined, but the semantic requirement is strict:

```text
pending-only callers must remain pending-only.
terminal-aware callers may accept resolved/cancelled/expired.
```

The implementation must preserve both current pending-only mechanisms:

```text
1. Validator-level pending restriction:
   callers that currently require allow_resolved=False or equivalent must continue
   to accept only pending.

2. Runtime pending guard:
   callers that validate a broader ticket shape and then explicitly check
   projection_state != "pending" must keep that explicit guard.
```

Adding `cancelled` and `expired` to terminal-aware validation must not make import, vote response, collection, or resolution paths accept terminal tickets.

### 4.2 Pending-only contexts

The following contexts must remain pending-only:

```text
pending ticket import
vote response validation
collection projection validation
resolution vote collection
new collection projection creation
```

These paths must reject `resolved`, `cancelled`, and `expired`.

### 4.3 Terminal-aware contexts

The following contexts may accept terminal ticket projections:

```text
diagnostic ticket validation
lifecycle idempotency validation
lifecycle terminal projection validation
replay reconstruction validation
evidence/debug projection reads
```

---

## 5. Command Model

Lifecycle commands are externally injected mailbox/domain messages.

They are not timers.

They are not scheduler tasks.

They are not daemon events.

### 5.1 Cancel command

```json
{
  "kind": "consensus_ticket_cancel",
  "schema_version": "consensus.ticket.cancel.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "statement_identity": "<non-empty string>",
  "coordinator": "global",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "action_id": "<non-empty string>"
}
```

### 5.2 Expire command

```json
{
  "kind": "consensus_ticket_expire",
  "schema_version": "consensus.ticket.expire.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "statement_identity": "<non-empty string>",
  "coordinator": "global",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "action_id": "<non-empty string>"
}
```

### 5.3 Required keys and nullable values

The command schema is closed.

The following keys are required:

```text
kind
schema_version
ticket_id
proposal_id
statement_identity
coordinator
reason
request_id
action_id
```

`reason` and `request_id` are required keys in the closed command schema.

Their values may be `null`.

A missing `reason` key or missing `request_id` key is a command schema error, not an implicit `null`.

This keeps action-hash preimages stable and avoids implicit null normalization.

`action_id` is required and must be a non-empty string.

`coordinator` must be exactly:

```text
global
```

### 5.4 Rejected timer/scheduler fields

The command schema is closed.

The following fields are not accepted:

```text
expires_at
deadline
timeout_at
duration
timer_id
scheduler_id
wall_clock
daemon_clock
background_scan
```

Any such field must fail command schema validation.

---

## 6. Expire Semantics

`expired` means:

```text
externally injected expiration evidence has been accepted and durably recorded.
```

`expired` does not mean:

```text
the runtime checked wall-clock time
the runtime scheduled timeout
a background daemon expired the ticket
a durable timer fired
```

This RFC explicitly does not authorize timers, schedulers, background scans, or daemon-driven clocks.

If a future stage wants automatic timeout behavior, it must be approved through a separate RFC/amendment that introduces the required deterministic durable timer or scheduler primitive.

---

## 7. Durable Event Model

This RFC uses two explicit event types.

A generic lifecycle transition event is rejected for this scope.

### 7.1 Cancelled event

```json
{
  "type": "distributed_consensus_ticket_cancelled",
  "schema_version": "consensus.ticket.cancelled.event.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "statement_identity": "<non-empty string>",
  "coordinator": "global",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "action_id": "<non-empty string>",
  "action_hash": "sha256:<64-hex>"
}
```

### 7.2 Expired event

```json
{
  "type": "distributed_consensus_ticket_expired",
  "schema_version": "consensus.ticket.expired.event.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "statement_identity": "<non-empty string>",
  "coordinator": "global",
  "reason": "<string-or-null>",
  "request_id": "<string-or-null>",
  "action_id": "<non-empty string>",
  "action_hash": "sha256:<64-hex>"
}
```

### 7.3 Event hash

This RFC does not require a separate `terminal_event_hash`.

The required integrity anchor is:

```text
action_hash
```

Replay must still compare expected and recorded event payloads exactly.

A later amendment may add an event-level hash if the project needs a separate event integrity anchor.

---

## 8. Action Hash

### 8.1 Action hash preimage

The canonical action hash preimage is:

```json
{
  "schema_version": "consensus.ticket.lifecycle.action.hash.v1",
  "kind": "consensus_ticket_cancel",
  "ticket_id": "<ticket_id>",
  "proposal_id": "<proposal_id>",
  "statement_identity": "<statement_identity>",
  "coordinator": "global",
  "reason": "<reason-or-null>",
  "request_id": "<request_id-or-null>",
  "action_id": "<action_id>"
}
```

For expiration, `kind` is:

```text
consensus_ticket_expire
```

### 8.2 Hash algorithm

```text
action_hash = "sha256:" + sha256(canonical_json(preimage)).hexdigest()
```

The implementation must use the repository canonical JSON encoder already used for consensus hashes.

---

## 9. Idempotency and Conflict Policy

### 9.1 Same ticket, same terminal kind, same hash

```text
same ticket_id + same terminal kind + same action_hash
=> idempotent no-op
```

### 9.2 Same ticket, same terminal kind, different hash

```text
same ticket_id + same terminal kind + different action_hash
=> conflict fail-closed
```

### 9.3 Same ticket, different terminal kind

```text
same ticket_id + different terminal kind after terminal state
=> conflict fail-closed
```

### 9.4 Same action_id reuse

`action_id` is scoped to lifecycle action identity.

Policy:

```text
same action_id + same ticket_id + same action_hash
=> idempotent no-op

same action_id + same ticket_id + different action_hash
=> conflict fail-closed

same action_id + different ticket_id
=> conflict fail-closed
```

The implementation must not treat action ID reuse across different tickets as idempotent.

---

## 10. Projection Shape

### 10.1 Cancelled projection

A cancelled ticket projection keeps the existing ticket fields and adds:

```text
terminal_kind = "cancelled"
terminal_reason
terminal_action_id
terminal_action_hash
```

`projection_state` becomes:

```text
cancelled
```

### 10.2 Expired projection

An expired ticket projection keeps the existing ticket fields and adds:

```text
terminal_kind = "expired"
terminal_reason
terminal_action_id
terminal_action_hash
```

`projection_state` becomes:

```text
expired
```

### 10.3 Fields not approved in this scope

This RFC does not approve:

```text
terminal_logical_index
```

This RFC also does not require:

```text
terminal_event_hash
```

### 10.4 Compatibility with pending import

Pending ticket import remains closed-schema and pending-only.

The import path must continue to validate imported ticket payloads against the exact pending ticket field set.

Terminal metadata fields such as:

```text
terminal_kind
terminal_reason
terminal_action_id
terminal_action_hash
```

must be rejected by import validation because they are not part of the pending import schema.

Terminal projections may exist in runtime `consensus_tickets[ticket_id]`, where terminal-aware diagnostic/lifecycle validation may accept extra terminal fields.

Terminal projections must not be importable through `consensus_ticket_import`.

Import must reject terminal tickets through both applicable mechanisms:

```text
closed-schema rejection of terminal_* fields
projection_state rejection when terminal metadata is absent but projection_state is cancelled/expired
```

---

## 11. Replay Model

### 11.1 LIVE

```text
receive mailbox message
recognize lifecycle method
validate command closed schema
validate coordinator == global
validate ticket exists
validate ticket projection is pending
validate ticket_id / proposal_id / statement_identity identity
compute action_hash
check idempotency/conflict policy
append terminal durable event
project ticket as cancelled or expired
return handled
```

### 11.2 REPLAY

```text
receive recorded mailbox message
recognize lifecycle method
validate command closed schema
validate ticket exists or replay-reconstructable prior projection exists
validate expected action_hash
read next event at replay_cursor
validate terminal event schema
compare recorded event == expected event
advance replay_cursor
project ticket as cancelled or expired
return handled
```

### 11.3 Replay failures

Replay must fail closed on:

```text
missing terminal event
malformed terminal event
wrong event type
schema mismatch
action_hash mismatch
ticket_id mismatch
proposal_id mismatch
statement_identity mismatch
out-of-order terminal event
conflicting terminal state
```

Replay mismatch must raise the existing replay integrity error family, not silently repair history.

---

## 12. Interaction with Vote Collection

After a ticket becomes `cancelled` or `expired`, `consensus_vote_response` must be rejected because the ticket is no longer pending.

The collection projection is not tombstoned by this RFC.

Collection projection remains diagnostic runtime state.

Further collection mutation is prevented by the ticket pending guard.

This RFC must not add collection lifecycle states.

---

## 13. Race and Ordering Semantics

Durable history order is authoritative.

The first terminal ticket event wins.

Examples:

```text
distributed_consensus_vote_received
distributed_consensus_ticket_cancelled
distributed_consensus_vote_received
```

The final vote response must fail closed after cancellation.

```text
distributed_consensus_ticket_cancelled
distributed_consensus_ticket_expired
```

The expiration must fail closed as a terminal conflict.

```text
distributed_consensus_ticket_cancelled(action_id=X, action_hash=H)
distributed_consensus_ticket_cancelled(action_id=X, action_hash=H)
```

The second event is an idempotent duplicate.

```text
distributed_consensus_ticket_cancelled(action_id=X, action_hash=H1)
distributed_consensus_ticket_cancelled(action_id=X, action_hash=H2)
```

The second event is a conflict.

---

## 14. Error Taxonomy

This RFC authorizes a new domain error class:

```python
class ConsensusTicketLifecycleError(Exception):
    """Stable validation boundary for ticket lifecycle commands and events."""
```

Replay mismatches should continue to use the existing replay integrity error family at the interpreter boundary.

Stable lifecycle reasons should include:

```text
consensus_ticket_lifecycle_ticket_not_found
consensus_ticket_lifecycle_not_pending
consensus_ticket_lifecycle_invalid_transition
consensus_ticket_lifecycle_terminal_conflict
consensus_ticket_lifecycle_action_id_conflict
consensus_ticket_lifecycle_action_hash_mismatch
consensus_ticket_lifecycle_command_schema
consensus_ticket_lifecycle_event_schema
consensus_ticket_lifecycle_identity_mismatch
```

Timer and scheduler attempts should fail through closed-schema validation unless the implementation needs a more specific error for an explicit rejected field.

---

## 15. Implementation Surface

### 15.1 Approved implementation files

A future implementation PR may modify only:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_ticket_lifecycle_*.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

### 15.2 Forbidden files

The implementation PR must not modify:

```text
synapse/runtime/consensus_engine.py
synapse/application.py
synapse/runtime/actor_runtime.py
synapse/ast.py
synapse/parser.py
synapse/lexer.py
network implementation files
daemon implementation files
scheduler implementation files
timer implementation files
durable artifact schema files
```

If any forbidden file becomes necessary, the team must stop and open a separate amendment before implementation.

---

## 16. Required Tests

A future implementation PR must include tests for:

```text
cancel pending ticket -> cancelled projection
expire pending ticket -> expired projection
cancel non-existing ticket fails
expire non-existing ticket fails
cancel resolved ticket rejected
expire resolved ticket rejected
cancel cancelled duplicate same hash no-op
cancel cancelled different hash conflict
expire expired duplicate same hash no-op
expire expired different hash conflict
cancel then expire conflict
expire then cancel conflict
vote response after cancelled rejected
vote response after expired rejected
pending import rejects cancelled ticket
pending import rejects expired ticket
import rejects ticket carrying terminal_* fields via closed-schema
collection projection not tombstoned after cancelled
collection projection not tombstoned after expired
collection projection cannot mutate after cancelled
collection projection cannot mutate after expired
replay cancelled event reconstructs projection
replay expired event reconstructs projection
replay missing terminal event fails closed
replay malformed terminal event fails closed
replay mismatched terminal event fails closed
replay out-of-order terminal event fails closed
malformed cancel command rejected before mutation
malformed expire command rejected before mutation
extra timer/scheduler field rejected by closed schema
action_id_empty_string_rejected
action_id reuse against another ticket conflicts
no consensus_engine.py changes
no application.py changes
no actor_runtime.py changes
no parser/AST/lexer changes
```

Regression tests must prove that expanding ticket projection states does not weaken pending-only gates.

---

## 17. Evidence Requirements

After implementation merge, a separate post-merge evidence patch must update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Required evidence fields:

```text
P3c Ticket Lifecycle POST_MERGE_ACCEPTED / EVIDENCE CLOSED
implementation PR number
implementation head SHA
implementation merge SHA
changed files
test counts
new failures
non-claims
P3 remains Partial
production distributed consensus not claimed
P3c-N2 remains future work
P3d remains future work
```

The evidence patch must not claim production distributed consensus.

---

## 18. Non-Claims

This RFC does not claim:

```text
production distributed consensus protocol behavior
fresh durable DistributedConsensusStmt execution
vote request delivery
distributed_consensus_vote_requested
N2 closure
P3d closure
network transport
daemon transport
automatic timeout
durable wall-clock scheduler
background expiration scan
persistent durable inbox
public ticket API
parser/AST/lexer expansion
ConsensusEngine semantic change
overall P3 production status
```

---

## 19. Approval Requirements

Before implementation, the approval document must explicitly approve:

```text
RFC name
approved content SHA
implementation base SHA
allowed file list
forbidden file list
terminal ticket states
allowed transitions
event schemas
command schemas
idempotency/conflict policy
replay requirements
test requirements
non-claims
```

Implementation must not begin until approval is merged.

---

## 20. Final RFC Decision

This RFC is approved for implementation after the companion approval record merges into `main`.

Approved lifecycle transitions:

```text
pending -> cancelled
pending -> expired
```

The approved design uses externally injected lifecycle commands, strict durable events, idempotent duplicate handling, fail-closed conflicts, and replay reconstruction from execution history.

The primary implementation risk is the shared ticket projection validator. Approval requires pending-only gates to remain pending-only after `cancelled` and `expired` are added.

The second implementation risk is import compatibility. Approval requires pending import to remain exact-field closed-schema and pending-only, rejecting terminal metadata fields and terminal projection states.
