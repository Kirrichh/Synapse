# RFC-CONSENSUS-P3C-TICKET-LIFECYCLE Approval Record

**Status:** APPROVED  
**Stage:** P3c Ticket Lifecycle approval gate  
**Repository mutation:** DOCUMENTATION APPROVAL ONLY  
**Implementation status:** AUTHORIZED FOR P3c TICKET LIFECYCLE IMPLEMENTATION AFTER THIS APPROVAL PATCH MERGES  
**Implementation PR allowed:** YES, after this approval patch merges  
**Product Owner sign-off:** Кирилл Раков  
**Target RFC:** `docs/RFC-CONSENSUS-P3C-TICKET-LIFECYCLE.md`  
**Approved RFC content SHA:** `e9c35004cb68b7ad58e6ea363553b8a16b3a4dd9`  
**Implementation base SHA:** the merge commit of this approval patch after it merges into `main`  
**Target implementation slice:** Externally injected terminal ticket lifecycle transitions for existing consensus tickets: `pending -> cancelled` and `pending -> expired`.

---

## 1. Approval Summary

This document approves `RFC-CONSENSUS-P3C-TICKET-LIFECYCLE` for implementation under the constrained scope below.

Approved state after this approval patch merges:

```text
Approval status: APPROVED
Implementation status: AUTHORIZED FOR P3c TICKET LIFECYCLE IMPLEMENTATION
Approved implementation scope: TERMINAL TICKET LIFECYCLE FOR EXISTING CONSENSUS TICKETS
Implementation PR allowed: YES, from the merge commit of this approval patch
```

Implementation work must branch from the `main` commit produced by merging this approval patch. Implementation must not branch from the pre-approval documentation branch commit.

This approval does not change runtime code. It only authorizes a later implementation PR constrained by this approval record.

P3c-N1 is closed and is a prerequisite, not future work.

P3c-N2 remains future work and is not approved by this record.

---

## 2. Approved Scope

The approved implementation scope is:

```text
pending -> cancelled
pending -> expired
```

The approved lifecycle commands are externally injected mailbox/domain messages:

```text
consensus_ticket_cancel
consensus_ticket_expire
```

The approved durable events are:

```text
distributed_consensus_ticket_cancelled
distributed_consensus_ticket_expired
```

The implementation must preserve the existing `pending -> resolved` resolution path and must not change consensus reduction mathematics.

---

## 3. Explicit Non-authorization

This approval does not authorize:

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

Any implementation that requires one of these behaviors must stop and return to RFC/approval review.

---

## 4. Approved Implementation Allowlist

A P3c Ticket Lifecycle implementation PR may touch only the files listed below:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_ticket_lifecycle_*.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

### 4.1 Explicitly excluded files

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

If any excluded file becomes necessary, implementation must stop and a separate amendment must be opened before code changes proceed.

---

## 5. Approved Stop Gates

Implementation must stop if any of the following becomes necessary:

```text
P3C_TICKET_LIFECYCLE_SCOPE_NOT_ACCEPTED
PENDING_ONLY_GATE_BROADENING_REQUIRED
TERMINAL_TICKET_IMPORT_REQUIRED
FRESH_DISTRIBUTED_CONSENSUS_DURABLE_EXECUTION_REQUIRED
VOTE_REQUEST_DELIVERY_REQUIRED
NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
DURABLE_TIMER_OR_SCHEDULER_REQUIRED
AUTOMATIC_TIMEOUT_REQUIRED
BACKGROUND_EXPIRATION_SCAN_REQUIRED
PERSISTENT_DURABLE_INBOX_REQUIRED
PARSER_AST_LEXER_CHANGE_REQUIRED
CONSENSUS_ENGINE_CHANGE_REQUIRED
ACTOR_RUNTIME_DELIVERY_CHANGE_REQUIRED
APPLICATION_DURABLE_CLASSIFICATION_CHANGE_REQUIRED
DURABLE_ARTIFACT_SCHEMA_CHANGE_REQUIRED
PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

These stop gates are implementation-blocking. Passing tests does not override them.

---

## 6. Approved Required Contract

The later implementation PR must implement only the approved contract below.

### 6.1 Ticket state model

Approved ticket projection states:

```text
pending
resolved
cancelled
expired
```

Approved terminal states:

```text
resolved
cancelled
expired
```

Approved new transitions:

```text
pending -> cancelled
pending -> expired
```

Forbidden transitions must fail closed:

```text
resolved -> cancelled
resolved -> expired
cancelled -> resolved
cancelled -> expired
expired -> resolved
expired -> cancelled
```

### 6.2 Pending-only gate preservation

The implementation must preserve both current pending-only mechanisms:

```text
1. Validator-level pending restriction:
   callers that currently require allow_resolved=False or equivalent must continue
   to accept only pending.

2. Runtime pending guard:
   callers that validate a broader ticket shape and then explicitly check
   projection_state != "pending" must keep that explicit guard.
```

Adding `cancelled` and `expired` must not cause import, vote response, collection, or resolution paths to accept terminal tickets.

### 6.3 Pending import compatibility

Pending ticket import must remain exact-field closed-schema and pending-only.

Terminal metadata fields must be rejected by import validation:

```text
terminal_kind
terminal_reason
terminal_action_id
terminal_action_hash
```

Terminal projections may exist in runtime `consensus_tickets[ticket_id]`, but they must not be importable through `consensus_ticket_import`.

### 6.4 Expire semantics

`expired` is approved only as externally injected expiration evidence.

The implementation must not introduce:

```text
wall-clock timeout
durable timer
scheduler
automatic timeout
background expiration scan
daemon-driven clock
```

### 6.5 Command schema

The approved command kinds are:

```text
consensus_ticket_cancel
consensus_ticket_expire
```

The approved command keys are closed and required:

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

`reason` and `request_id` may be `null`, but their keys must be present.

`action_id` must be present and must be a non-empty string.

Timer/scheduler fields such as `expires_at`, `deadline`, and `timeout_at` must be rejected by closed-schema validation.

### 6.6 Event schema

The approved event types are:

```text
distributed_consensus_ticket_cancelled
distributed_consensus_ticket_expired
```

The approved schema versions are:

```text
consensus.ticket.cancelled.event.v1
consensus.ticket.expired.event.v1
```

A generic lifecycle transition event is not approved for this implementation slice.

### 6.7 Idempotency and conflict policy

Approved policy:

```text
same ticket_id + same terminal kind + same action_hash
=> idempotent no-op

same ticket_id + same terminal kind + different action_hash
=> conflict fail-closed

same ticket_id + different terminal kind after terminal state
=> conflict fail-closed

same action_id + same ticket_id + same action_hash
=> idempotent no-op

same action_id + same ticket_id + different action_hash
=> conflict fail-closed

same action_id + different ticket_id
=> conflict fail-closed
```

The implementation must use repository canonical JSON and SHA-256 with `sha256:` prefix for the approved action hash.

---

## 7. Required Tests

A later implementation PR must include tests for:

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

## 8. Required Post-merge Evidence

After implementation merge, a separate evidence patch must update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

The evidence must record:

```text
P3c Ticket Lifecycle POST_MERGE_ACCEPTED / EVIDENCE CLOSED
implementation PR number
implementation head SHA
implementation merge SHA
changed files
test counts
non-claims
P3 remains Partial
production distributed consensus not claimed
P3c-N2 remains future work
P3d remains future work
```

---

## 9. Approval Decision

This approval authorizes a later implementation PR only after this approval patch is merged into `main`.

The implementation PR must branch from the merge commit of this approval patch.

The implementation PR must stay within the allowlist, obey every stop gate, preserve pending-only gates, reject terminal ticket import, and maintain the non-claims in this record.
