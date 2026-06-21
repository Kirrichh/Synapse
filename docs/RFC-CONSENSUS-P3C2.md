# RFC-CONSENSUS-P3C2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary

**Status:** DRAFT  
**Stage:** P3c-2 RFC  
**Implementation status:** NOT AUTHORIZED UNTIL APPROVAL GATE  
**Repository mutation:** DOCUMENTATION DRAFT ONLY  
**Primary implementation stage:** P3c-2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Mailbox-backed vote delivery in P3c-2:** NOT ALLOWED  
**Network-backed vote transport in P3c-2:** NOT ALLOWED  
**Daemon-backed vote transport in P3c-2:** NOT ALLOWED  
**Live LLM vote production in P3c-2:** NOT ALLOWED  
**Parser / AST / lexer expansion in P3c-2:** NOT ALLOWED  
**Public ticket inspection API in P3c-2:** NOT ALLOWED  
**Ticket cancellation / expiration in P3c-2:** NOT ALLOWED  
**Ticket lifecycle status field in durable event:** NOT ALLOWED  
**Automatic re-binding of program variables post-resolution:** NOT ALLOWED  
**P2 contract expansion in P3c-2:** NOT ALLOWED  
**New P2 suspension reason in P3c-2:** NOT ALLOWED  
**Change to `SuspendExpr` payload shape in P3c-2:** NOT ALLOWED  
**Capability target after successful P3c-2 evidence closure:** Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution closed via existing P2 resume boundary  
**Capability target explicitly not claimed:** Production  
**Overall P3c status after this RFC draft:** OPEN

---

## 0. Purpose

P3c-1 closed durable ticket creation and replay for deferred consensus.

P3c-2 defines how an already-created pending consensus ticket is resolved using the existing P2 durable resume boundary.

P3c-2 answers the following question:

```text id="rfc-p3c2-purpose-001"
Given a durable consensus ticket created for a deferred consensus result,
how does the runtime later accept missing votes through an existing durable boundary,
validate them, compute the final consensus outcome deterministically,
record the resolution as durable history,
update projection state,
and replay the same resolution without live side effects?
```

P3c-2 does not implement mailbox-backed vote collection, network transport, daemon transport, LLM voting, public ticket inspection API, parser expansion, AST expansion, lexer expansion, new P2 suspension reasons, or production distributed consensus protocol behavior.

---

## 1. Product Statement

When a `DistributedConsensusStmt` produces:

```text id="rfc-p3c2-product-001"
outcome = deferred
reason = pending_missing_votes
```

P3c-1 creates a durable consensus ticket.

P3c-2 allows that ticket to be resolved later through an existing P2 resume path.

The resolution process must:

```text id="rfc-p3c2-product-002"
1. carry a ticket-resolution request through the existing SuspendExpr request channel;
2. accept ticket-resolution data through the existing P2 resume signal channel;
3. validate that the signal targets the pending ticket selected by the trusted request_value;
4. validate that all previously missing participants have supplied final votes;
5. compute the final consensus result through ConsensusEngine-owned logic;
6. append a single distributed_consensus_ticket_resolved event;
7. update consensus_tickets projection;
8. replay the same resolution deterministically from recorded history.
```

The original deferred result binding is not automatically rewritten.

Resolution is visible through durable history and projection, not through hidden mutation of earlier program variables.

---

## 2. Canonical User / Runtime Path

P3c-2 uses the existing P2 durable resume model.

The public durable resume path remains:

```text id="rfc-p3c2-runtime-001"
python -m synapse resume
  --state-file <artifact.json>
  --suspension-id <id>
  --signal-file <json-file|->
```

P3c-2 must not add a new command.

P3c-2 must not add a new P2 suspension reason.

P3c-2 must not change the P2 artifact schema.

P3c-2 must not change P2 exit code mapping.

P3c-2 must not modify `_SUPPORTED_SUSPENSION_REASONS`.

The P3c-2 runtime path is:

```text id="rfc-p3c2-runtime-002"
1. Program evaluates DistributedConsensusStmt.
2. Consensus is deferred because votes are missing.
3. P3c-1 appends distributed_consensus_decided.
4. P3c-1 appends adjacent distributed_consensus_ticket_created.
5. Program later uses existing SuspendExpr to request external resolution.
6. SuspendExpr emits awaiting_external_signal through existing P2 mechanics.
7. P2 writes durable artifact and exits PENDING.
8. External resolver provides a strict JSON resolution signal through the existing resume path.
9. P2 resumes the program.
10. Interpreter detects that this suspend request/result pair is a consensus-ticket-resolution convention.
11. Interpreter extracts the trusted ticket_id from request_value, not from the injected signal.
12. Interpreter delegates validation to synapse.runtime.consensus_ticket_resolution.
13. Interpreter delegates final consensus computation to ConsensusEngine.
14. Interpreter appends distributed_consensus_ticket_resolved.
15. Interpreter updates consensus_tickets projection.
16. Program continues.
```

P3c-2 does not introduce ticket-specific syntax.

P3c-2 does not introduce a public ticket API.

P3c-2 does not introduce an `await ticket(...)` construct.

P3c-2 does not change parser, AST, or lexer.

---

## 3. Existing P2 Boundary: How P3c-2 Carries Resolution Without Expanding Interpreter Semantics

P3c-2 must use the existing `SuspendExpr` request/result convention.

Current `SuspendExpr` semantics are:

```text id="rfc-p3c2-boundary-001"
request_value = describe_request(node.request, env)
promise = create_durable_promise("suspend", request_value)
yield Suspension(
    reason = "awaiting_external_signal",
    payload = {
        "promise_id": promise.promise_id,
        "request": request_value
    }
)
injected = external signal supplied by P2 resume
resolve_promise(promise.promise_id, injected)
return injected
```

P3c-2 must not change this `Suspension.payload` shape.

Instead, P3c-2 defines a strict JSON convention inside the existing `request_value` and injected signal.

### 3.1 Consensus Ticket Resolution Request Convention

A P3c-2 resolution request is a strict JSON object passed through the existing suspend request channel.

Required request shape:

```json id="rfc-p3c2-boundary-002"
{
  "kind": "consensus_ticket_resolution",
  "ticket_id": "sha256:...",
  "missing_participants": ["participant_a", "participant_b"],
  "votes_hash": "sha256:..."
}
```

Rules:

```text id="rfc-p3c2-boundary-003"
kind must be exactly consensus_ticket_resolution
ticket_id must be a string
missing_participants must be a list of strings
votes_hash must be the original ticket votes_hash
the request must be strict JSON compatible
```

### 3.2 Consensus Ticket Resolution Signal Convention

The injected signal must be a strict JSON object.

Required signal shape:

```json id="rfc-p3c2-boundary-004"
{
  "kind": "consensus_ticket_resolution",
  "ticket_id": "sha256:...",
  "votes": {
    "participant_a": "yes",
    "participant_b": "abstain"
  }
}
```

Rules:

```text id="rfc-p3c2-boundary-005"
kind must be exactly consensus_ticket_resolution
ticket_id must match the trusted request_value ticket_id
votes must be an object
vote map keys must be strings
vote map values must be yes, no, or abstain
missing is not allowed in resolution votes
vote keys must exactly match missing_participants
extra vote keys are forbidden
missing vote keys are forbidden
```

### 3.3 Non-Consensus Suspend Values

If the existing suspend request/value pair does not match the consensus-ticket-resolution convention, P3c-2 must not alter existing `SuspendExpr` behavior.

The injected signal is returned normally.

This preserves P2 behavior for all non-consensus suspend use cases.

---

## 4. Relationship to Existing P3 Stages

### 4.1 P3a

P3a owns deterministic consensus semantics:

```text id="rfc-p3c2-relation-001"
participant normalization
strategy semantics
vote state vocabulary
quorum semantics
outcome semantics
reason semantics
proposal_id semantics
votes_hash semantics
result_hash semantics
strict validation
```

P3c-2 must not redefine P3a semantics.

### 4.2 P3b

P3b owns explicit local actor-method vote source.

P3c-2 must not call actor vote methods during replay.

P3c-2 must not broaden actor-method voting.

P3c-2 must not use actor method voting to resolve tickets.

### 4.3 P3c-0

P3c-0 owns replay consumption for `distributed_consensus_decided`.

P3c-2 must not weaken P3c-0 fail-closed replay behavior.

### 4.4 P3c-1

P3c-1 owns durable ticket creation and replay.

P3c-2 extends P3c-1 by adding durable ticket resolution after ticket creation.

The full P3c-2 lifecycle event sequence is:

```text id="rfc-p3c2-relation-002"
distributed_consensus_decided
distributed_consensus_ticket_created
... later durable execution ...
distributed_consensus_ticket_resolved
```

### 4.5 P3c-N

P3c-N is deferred.

P3c-N owns future mailbox-backed vote delivery and receive-based vote collection.

P3c-2 must not implement any P3c-N behavior.

### 4.6 P3d

P3d owns LLM-assisted voting.

P3c-2 must not call live LLM providers.

P3c-2 must not define LLM prompt formats, model-output schemas, provider timeout rules, cost controls, refusal handling, or LLM vote replay rules.

---

## 5. Relationship to P2 Durable Execution

P3c-2 is allowed only because it can reuse existing P2 mechanics.

P3c-2 may use:

```text id="rfc-p3c2-p2-001"
awaiting_external_signal
awaiting_promise
```

only because both are already P2-supported.

P3c-2 must not use:

```text id="rfc-p3c2-p2-002"
awaiting_message
awaiting_message_or_timeout
```

because both are unsupported by P2.

P3c-2 must reuse P2 idempotency:

```text id="rfc-p3c2-p2-003"
suspension_id + signal_value_hash
```

P3c-2 must not introduce a parallel primary duplicate-resolution surface such as:

```text id="rfc-p3c2-p2-004"
ticket_id + vote_value_hash
```

If implementation needs a new P2 suspension reason, artifact schema change, exit code change, signal inbox, mailbox wait, scheduler timeout, or durable receive expansion, it must stop.

---

## 6. Why P3c-N Is Deferred

P3c-N would require mailbox-backed vote delivery.

Mailbox-backed vote delivery requires at least one of:

```text id="rfc-p3c2-p3cn-001"
awaiting_message
awaiting_message_or_timeout
mailbox wait durable boundary
mailbox timeout durable boundary
signal inbox
scheduler timeout semantics
possibly daemon/network transport
```

The current P2 durable execution contract does not support `awaiting_message` or `awaiting_message_or_timeout`.

Therefore P3c-N is not just “another way to resolve votes.”

P3c-N is a later stage that first requires a P2 durable lifecycle expansion.

P3c-2 must not include mailbox-backed behavior.

---

## 7. Current State and Gap

### 7.1 Current State

At audit base:

```text id="rfc-p3c2-gap-001"
ConsensusEngine creates ticket_id and ticket_payload for deferred pending_missing_votes.
Interpreter appends distributed_consensus_decided.
Interpreter appends adjacent distributed_consensus_ticket_created.
Interpreter projects pending ticket into consensus_tickets.
Replay consumes the deferred decision and adjacent ticket.
Replay restores pending ticket projection.
P2 supports external-signal resume.
P2 supports promise resume.
```

### 7.2 Gap

The runtime does not yet define:

```text id="rfc-p3c2-gap-002"
resolution signal convention
ticket resolution validation
distributed_consensus_ticket_resolved event
final consensus recomputation from resolution votes
projection transition from pending to resolved
replay consumption of resolution event
fail-closed resolution replay
multiple concurrent ticket resolution rules
duplicate resolution behavior
projection/history consistency invariant
```

P3c-2 closes this gap.

---

## 8. Core Design Decision

P3c-2 introduces one new durable event:

```text id="rfc-p3c2-core-001"
distributed_consensus_ticket_resolved
```

This event records a later durable act: a previously pending consensus ticket has been resolved.

P3c-2 uses event sequence as lifecycle truth:

```text id="rfc-p3c2-core-002"
ticket_created only = pending by construction
ticket_created + ticket_resolved = resolved by construction
```

P3c-2 does not add a durable `status` field.

Projection may derive local state:

```text id="rfc-p3c2-core-003"
projection_state = pending
projection_state = resolved
```

Projection state is runtime-derived. It is not durable event truth.

### 8.1 Resolution Is Terminal

A successful resolution is terminal.

P3c-2 permits only:

```text id="rfc-p3c2-core-004"
pending -> resolved
```

P3c-2 does not support:

```text id="rfc-p3c2-core-005"
resolved -> pending
resolved -> cancelled
resolved -> expired
resolved -> failed
resolved -> finalized
re-resolution with different votes
```

A duplicate resolution signal for an already resolved ticket is allowed only if it is semantically identical to the existing resolved projection.

If duplicate content matches, the operation is an idempotent no-op and must not append a duplicate event.

If duplicate content conflicts, it fails closed.

---

## 9. Consensus Ticket Resolution Request Contract

### 9.1 Request Carrier

P3c-2 uses the existing `SuspendExpr` request channel.

The request must be a strict JSON object.

### 9.2 Required Request Fields

```text id="rfc-p3c2-request-001"
kind
ticket_id
missing_participants
votes_hash
```

### 9.3 Required Request Values

```text id="rfc-p3c2-request-002"
kind: consensus_ticket_resolution
ticket_id: deterministic ticket_id from P3c-1
missing_participants: exact list from ticket projection
votes_hash: original ticket votes_hash
```

### 9.4 Request Validation

If a suspend request claims:

```text id="rfc-p3c2-request-003"
kind = consensus_ticket_resolution
```

the runtime must validate:

```text id="rfc-p3c2-request-004"
ticket_id exists
ticket is pending or already resolved with matching final content
missing_participants matches ticket projection for pending ticket
votes_hash matches ticket projection
request is strict JSON-compatible
```

Failure must stop before history append.

---

## 10. Consensus Ticket Resolution Signal Contract

### 10.1 Signal Carrier

P3c-2 uses the existing P2 resume signal value.

The signal must be strict JSON.

### 10.2 Required Signal Fields

```text id="rfc-p3c2-signal-001"
kind
ticket_id
votes
```

### 10.3 Trusted Ticket Identity

The trusted `ticket_id` for lookup is extracted from `request_value`, not from the injected signal.

The signal’s `ticket_id` is used only for consistency checking.

Required rule:

```text id="rfc-p3c2-signal-002"
trusted_ticket_id = request_value["ticket_id"]
signal_ticket_id must equal trusted_ticket_id
lookup consensus_tickets using trusted_ticket_id only
```

### 10.4 Required Signal Values

```text id="rfc-p3c2-signal-003"
kind: consensus_ticket_resolution
ticket_id: deterministic ticket_id from P3c-1
votes: object mapping missing participant identity to final vote state
```

### 10.5 Allowed Resolution Vote States

Resolution votes may be:

```text id="rfc-p3c2-signal-004"
yes
no
abstain
```

Resolution votes must not be:

```text id="rfc-p3c2-signal-005"
missing
```

### 10.6 Participant Coverage

The `votes` object must satisfy:

```text id="rfc-p3c2-signal-006"
all keys are strings
keys as a set exactly match missing_participants
no extra keys
no omitted missing participants
all values are yes, no, or abstain
missing is forbidden as a resolution vote value
```

After JSON parsing, duplicate object keys may no longer be observable if the parser collapses them. If the P2 signal loader supports duplicate-key detection, duplicate textual keys must fail closed at load time. If duplicate-key detection is not available in the current loader, P3c-2 validates the parsed object and must still require exact participant set equality.

---

## 11. Ticket Payload Contract

The `ticket_payload` passed to resolution logic is derived from `distributed_consensus_ticket_created` and the `consensus_tickets` projection.

It must contain the P3c-1 event shape:

```json id="rfc-p3c2-ticket-001"
{
  "type": "distributed_consensus_ticket_created",
  "schema_version": "consensus.ticket.event.v1",
  "ticket_id": "sha256:...",
  "proposal_id": "sha256:...",
  "statement_identity": "source:line:column",
  "participants": ["A", "B", "C"],
  "missing_participants": ["B", "C"],
  "votes": {
    "A": "yes",
    "B": "missing",
    "C": "missing"
  },
  "vote_counts": {
    "yes": 1,
    "no": 0,
    "abstain": 0,
    "missing": 2
  },
  "votes_hash": "sha256:...",
  "strategy": "MajorityVote",
  "policy": null,
  "quorum": 2,
  "timeout": 30
}
```

P3c-2 must use the existing `votes` field.

P3c-2 must not introduce or require a `known_votes` field.

The `votes` map contains the original vote state at ticket creation time, including `missing` markers for participants that must be resolved later.

The `missing_participants` list is the authoritative participant set for resolution coverage.

Stop gate:

```text id="rfc-p3c2-ticket-002"
BLOCKED — TICKET_PAYLOAD_STRUCTURE_UNDEFINED
```

---

## 12. Resolution Event Contract

### 12.1 Event Type

```text id="rfc-p3c2-event-001"
distributed_consensus_ticket_resolved
```

### 12.2 Schema Version

```text id="rfc-p3c2-event-002"
consensus.ticket.resolution.event.v1
```

### 12.3 Required Fields

```text id="rfc-p3c2-event-003"
type
schema_version
ticket_id
proposal_id
statement_identity
resolution_votes
votes_final
vote_counts_final
outcome
reason
votes_hash_final
result_hash_final
```

### 12.4 Deliberately Omitted Fields

The resolution event must not contain:

```text id="rfc-p3c2-event-004"
status
previous_hash
projection_state
source_label
promise_id
suspension_id
mailbox_id
network_route
daemon_id
runtime_uuid
wall_clock_time
random_value
process_id
```

### 12.5 Closed Schema

Replay must reject:

```text id="rfc-p3c2-event-005"
missing fields
extra fields
non-string keys
wrong schema_version
wrong type
malformed resolution_votes
malformed votes_final
invalid vote states
mismatched ticket anchors
```

### 12.6 Required Field Set

P3c-2 defines the closed schema field set:

```python id="rfc-p3c2-event-006"
_ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS = {
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "resolution_votes",
    "votes_final",
    "vote_counts_final",
    "outcome",
    "reason",
    "votes_hash_final",
    "result_hash_final",
}
```

### 12.7 Hash Computation

P3c-2 must reuse the same canonical JSON hashing serialization profile established by P3a/P3c.

#### 12.7.1 Final Votes Hash

`votes_hash_final` is computed from an ordered participant list.

Required preimage shape:

```json id="rfc-p3c2-event-007"
{
  "schema_version": "consensus.votes.v1",
  "votes": [
    ["A", "yes"],
    ["B", "no"],
    ["C", "abstain"]
  ]
}
```

Ordering must follow canonical participant order from the consensus proposal.

The engine owns this computation.

The interpreter must not compute this hash.

#### 12.7.2 Final Result Hash

`result_hash_final` must mirror the P3a result preimage profile.

Required preimage shape:

```json id="rfc-p3c2-event-008"
{
  "schema_version": "consensus.result.v1",
  "proposal_id": "sha256:...",
  "outcome": "committed",
  "reason": "quorum_reached",
  "participants": ["A", "B", "C"],
  "strategy": "MajorityVote",
  "policy": null,
  "quorum": 2,
  "timeout": 30,
  "vote_counts": {
    "yes": 2,
    "no": 0,
    "abstain": 1,
    "missing": 0
  },
  "votes_hash": "sha256:..."
}
```

The final result hash must not introduce a new hash profile unless a future RFC explicitly approves it.

The engine owns this computation.

The interpreter must not compute this hash.

---

## 13. Engine Ownership

P3c-2 adds an engine-owned resolution method equivalent to:

```text id="rfc-p3c2-engine-001"
resolve_pending_ticket(ticket_payload, resolution_votes) -> ConsensusDecision
```

The engine must:

```text id="rfc-p3c2-engine-002"
validate ticket_payload
validate resolution_votes
verify resolution_votes covers exactly missing_participants
reject missing state in resolution_votes
merge original ticket votes and resolution_votes
derive votes_final
derive vote_counts_final
derive final outcome
derive final reason
derive votes_hash_final
derive result_hash_final
construct or delegate construction of distributed_consensus_ticket_resolved payload
return a structured decision object
```

The engine must not:

```text id="rfc-p3c2-engine-003"
mutate consensus_tickets
append history
read mailbox
write mailbox
resolve promises
inspect P2 artifacts
call actors
call LLM providers
read wall-clock time
use random values
generate UUIDs
```

The engine owns final consensus mathematics.

---

## 14. Resolution Module Ownership

P3c-2 implementation must introduce:

```text id="rfc-p3c2-module-001"
synapse/runtime/consensus_ticket_resolution.py
```

This module owns resolution-domain constants and validation helpers.

It owns:

```text id="rfc-p3c2-module-002"
RESOLUTION_EVENT_TYPE
RESOLUTION_SCHEMA_VERSION
_ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS
validate_resolution_request_payload(...)
validate_resolution_signal_payload(...)
validate_resolution_event_schema(...)
validate_projection_transition(...)
validate_idempotent_duplicate_resolution(...)
build_resolved_projection(...)
stable error message fragments
```

The module may expose helpers used by `ConsensusEngine` and `Interpreter`.

The module does not own consensus mathematics.

The module does not append history.

The module does not mutate interpreter state directly.

---

## 15. Interpreter Adapter Ownership

Interpreter owns runtime interaction only.

Interpreter may:

```text id="rfc-p3c2-adapter-001"
detect consensus_ticket_resolution convention inside an existing suspend request/result pair
extract trusted_ticket_id from request_value
look up trusted_ticket_id in consensus_tickets
call resolution module request/signal validators
call ConsensusEngine.resolve_pending_ticket
preflight returned event shape
append distributed_consensus_ticket_resolved
update projection using module helper
consume replayed distributed_consensus_ticket_resolved
rollback cursor and projection on replay failure
```

Interpreter must not:

```text id="rfc-p3c2-adapter-002"
own resolution event field constants
own resolution hash construction
own final outcome logic
own final reason logic
own lifecycle vocabulary
own resolution vote coverage rules
trust ticket_id from injected signal for lookup
change SuspendExpr payload shape
add new AST execution path
add new parser syntax
add new P2 suspension reason
```

If implementation requires non-adapter resolution domain logic inside `Interpreter`, it must stop.

---

## 16. LIVE Semantics

### 16.1 Non-Consensus Suspend Path

If `SuspendExpr` request does not carry:

```text id="rfc-p3c2-live-001"
kind = consensus_ticket_resolution
```

existing behavior is unchanged.

P3c-2 must not affect generic suspend behavior.

### 16.2 Consensus Ticket Resolution Request

If `SuspendExpr` request carries:

```text id="rfc-p3c2-live-002"
kind = consensus_ticket_resolution
```

the interpreter must follow this validation order:

```text id="rfc-p3c2-live-003"
1. Validate request_value is a strict JSON-compatible mapping.
2. Validate kind == consensus_ticket_resolution.
3. Extract trusted_ticket_id from request_value.
4. Validate trusted_ticket_id is a valid ticket identifier string.
5. Look up trusted_ticket_id in consensus_tickets.
6. Validate ticket projection exists.
7. Validate ticket projection is pending, or is resolved with identical final content.
8. Validate request missing_participants matches pending ticket projection.
9. Validate request votes_hash matches pending ticket projection.
10. Continue with existing P2 awaiting_external_signal mechanics.
```

All failures before suspension must fail closed without appending resolution history.

### 16.3 Resume Signal

After P2 resume supplies injected signal, interpreter must follow this validation order:

```text id="rfc-p3c2-live-004"
1. Validate injected signal is a strict JSON-compatible mapping.
2. Validate signal kind == consensus_ticket_resolution.
3. Validate signal ticket_id equals trusted_ticket_id from request_value.
4. Validate votes object exists.
5. Validate vote keys are strings.
6. Validate vote keys exactly match missing_participants from ticket projection.
7. Validate vote values are yes, no, or abstain.
8. Reject missing as a resolution vote value.
9. If ticket is already resolved, compare incoming votes with resolved projection.
10. If duplicate content matches, return injected signal unchanged without appending.
11. If duplicate content conflicts, fail closed.
12. If ticket is pending, call ConsensusEngine.resolve_pending_ticket.
```

The interpreter must never use `injected["ticket_id"]` to select the ticket projection.

The injected signal’s `ticket_id` is only a consistency check.

### 16.4 Preflight Before Append

Before appending any event:

```text id="rfc-p3c2-live-005"
ticket must still be pending
engine must return final non-deferred outcome
resolution event type must be distributed_consensus_ticket_resolved
schema_version must be consensus.ticket.resolution.event.v1
payload must be strict JSON-compatible
event schema must be closed
```

Failure leaves:

```text id="rfc-p3c2-live-006"
execution_history unchanged
consensus_tickets unchanged
```

### 16.5 Append and Projection Order

Required LIVE order:

```text id="rfc-p3c2-live-007"
1. Validate request and signal.
2. Call engine.
3. Validate returned resolution event.
4. Append distributed_consensus_ticket_resolved.
5. Update consensus_tickets projection to resolved.
6. Continue execution.
```

### 16.6 Return Value of the Suspend Expression

The suspend expression must return the injected signal value unchanged.

This is mandatory.

P3c-2 must not return a normalized consensus result from `SuspendExpr`.

Rationale:

```text id="rfc-p3c2-live-008"
SuspendExpr is a generic P2 external signal construct.
P3c-2 must not overload its return semantics.
Programs that use generic suspend must remain compatible.
Consensus ticket resolution is a history/projection side effect.
The original deferred consensus binding is not automatically re-bound.
```

Return value:

```text id="rfc-p3c2-live-009"
return injected_signal
```

Resolution effects:

```text id="rfc-p3c2-live-010"
append distributed_consensus_ticket_resolved
update consensus_tickets projection
```

---

## 17. REPLAY Semantics

### 17.1 Replay Entry

When replay reaches the same `SuspendExpr` boundary with:

```text id="rfc-p3c2-replay-001"
kind = consensus_ticket_resolution
```

P2 replay/resume reconstructs the committed boundary.

P3c-2 then consumes the recorded consensus ticket resolution event.

### 17.2 Resolution Event Is Not Adjacent to Ticket Creation

Unlike P3c-1, the resolution event is not adjacent to the ticket creation event.

Expected lifecycle:

```text id="rfc-p3c2-replay-002"
distributed_consensus_decided
distributed_consensus_ticket_created
... arbitrary later history ...
distributed_consensus_ticket_resolved
```

The replay path must consume the appropriate resolution event at the resolution boundary.

### 17.3 Strict Resolution Event Matching at Replay Boundary

At the resolution boundary:

```text id="rfc-p3c2-replay-003"
1. Extract trusted_ticket_id from request_value.
2. Inspect the current replay frontier.
3. Skip only events that the existing replay policy already classifies as replay-skippable metadata.
4. The next replay-significant event consumed for this boundary must be distributed_consensus_ticket_resolved.
5. The event ticket_id must equal trusted_ticket_id.
6. If another semantic event appears first, fail closed.
7. If no resolution event is available where expected, fail closed.
```

Replay must not perform arbitrary unbounded search through future semantic history to find a matching `ticket_id`.

This prevents replay from hiding nondeterministic control flow.

### 17.4 Closed Schema Validation

Replay must validate:

```text id="rfc-p3c2-replay-004"
event is mapping
keys are strings
field set exactly matches _ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS
type is distributed_consensus_ticket_resolved
schema_version is consensus.ticket.resolution.event.v1
ticket_id matches trusted_ticket_id
proposal_id matches ticket
statement_identity matches ticket
resolution_votes are valid
votes_final are valid
```

### 17.5 Engine Recompute

Replay must call engine-owned resolution math.

Replay must verify:

```text id="rfc-p3c2-replay-005"
recomputed votes_final == recorded votes_final
recomputed vote_counts_final == recorded vote_counts_final
recomputed outcome == recorded outcome
recomputed reason == recorded reason
recomputed votes_hash_final == recorded votes_hash_final
recomputed result_hash_final == recorded result_hash_final
```

Mismatch raises `ConsensusReplayIntegrityError`.

### 17.6 Rollback

If replay validation fails after cursor advancement:

```text id="rfc-p3c2-replay-006"
restore replay_cursor
restore prior consensus_tickets projection
raise ConsensusReplayIntegrityError
```

Projection rollback is mandatory.

---

## 18. Hash Chain Continuity

`distributed_consensus_ticket_resolved` is a normal `execution_history` event.

It participates in the existing history hash chain.

The event payload must not contain `previous_hash`.

Hash-chain linkage remains owned by the existing history mechanism.

P3c-2 must not bypass canonical history append mechanics.

---

## 19. Projection Contract

### 19.1 Pending Projection

After P3c-1 ticket creation:

```text id="rfc-p3c2-projection-001"
consensus_tickets[ticket_id]["projection_state"] == "pending"
```

### 19.2 Resolved Projection

After P3c-2 resolution:

```text id="rfc-p3c2-projection-002"
consensus_tickets[ticket_id]["projection_state"] == "resolved"
```

Resolved projection may include:

```text id="rfc-p3c2-projection-003"
resolution_votes
votes_final
vote_counts_final
outcome
reason
votes_hash_final
result_hash_final
```

### 19.3 Projection Is Not Durable Truth

Projection state is derived from event sequence.

Durable truth remains:

```text id="rfc-p3c2-projection-004"
distributed_consensus_ticket_created
distributed_consensus_ticket_resolved
```

Manual mutation of projection outside event replay/application is forbidden.

Projection must be reconstructible from durable history.

### 19.4 Projection Consistency Invariant

For every `ticket_id` in `consensus_tickets`:

```text id="rfc-p3c2-projection-005"
If projection_state == "pending":
  distributed_consensus_ticket_created exists in history.
  distributed_consensus_ticket_resolved does not exist for the same ticket_id.

If projection_state == "resolved":
  distributed_consensus_ticket_created exists in history.
  distributed_consensus_ticket_resolved exists in history.
  both events have matching ticket_id.
  both events have matching proposal_id.
  both events have matching statement_identity.
```

If this invariant is violated during replay reconstruction, replay must fail closed.

### 19.5 Allowed Projection States

P3c-2 allows only:

```text id="rfc-p3c2-projection-006"
pending
resolved
```

If implementation requires:

```text id="rfc-p3c2-projection-007"
cancelled
expired
failed
finalized
```

it must stop.

---

## 20. Compatibility Rules

P3c-2 must preserve:

```text id="rfc-p3c2-compat-001"
P3a participant normalization
P3a vote states
P3a strategy semantics
P3a quorum semantics
P3a outcome/reason semantics
P3a hash semantics
P3b actor-method vote source contract
P3c-0 distributed_consensus_decided replay consumption
P3c-1 distributed_consensus_ticket_created replay consumption
```

P3c-2 must not migrate old ticket events.

A ticket without a later resolution event is a valid pending ticket.

Existing histories that contain `distributed_consensus_ticket_created` but do not contain `distributed_consensus_ticket_resolved` must remain replayable as pending-ticket histories.

Replay must not synthesize a resolution.

Replay must not infer final result from missing data.

---

## 21. Failure Semantics

### 21.1 LIVE Failures

LIVE failures must occur before any history append.

LIVE invalid resolution cases include:

```text id="rfc-p3c2-failure-001"
ticket not found
ticket_id mismatch
ticket already resolved with conflicting final content
malformed request payload
malformed signal payload
missing votes object
non-string vote keys
invalid vote state
missing vote state in resolution
participant set mismatch
engine validation failure
projection transition failure before append
```

### 21.2 Duplicate Resolution Behavior

If a ticket is already resolved:

```text id="rfc-p3c2-failure-002"
If incoming resolution_votes are semantically identical to the existing resolved projection,
the operation is idempotent and must not append a duplicate event.

If incoming resolution_votes conflict with the existing resolved projection,
the operation fails closed.
```

The idempotent duplicate path returns the injected signal unchanged.

The conflicting duplicate path must not append history and must not mutate projection.

### 21.3 REPLAY Failures

REPLAY failures raise `ConsensusReplayIntegrityError`.

Replay invalid cases include:

```text id="rfc-p3c2-failure-003"
missing resolution event when expected
wrong event type
wrong schema version
extra field
missing field
non-string key
ticket_id mismatch
proposal_id mismatch
statement_identity mismatch
votes_final mismatch
vote_counts_final mismatch
outcome mismatch
reason mismatch
votes_hash_final mismatch
result_hash_final mismatch
projection update failure
projection/history invariant mismatch
```

Cursor rollback and projection rollback are mandatory.

---

## 22. Non-Claims

P3c-2 does not claim or implement:

```text id="rfc-p3c2-nonclaims-001"
mailbox-backed vote delivery
receive-based vote collection
network-backed vote transport
daemon-backed vote transport
live LLM vote production
ticket cancellation
ticket expiration
ticket finalization
public ticket inspection API
automatic variable re-binding
new parser syntax
new AST node
new lexer token
new P2 suspension reason
P2 artifact schema expansion
P2 exit code expansion
production distributed consensus protocol behavior
Raft semantics
Paxos semantics
Tendermint semantics
PBFT semantics
Byzantine fault tolerance
leader election
view-change protocol
network replication
overall P3c closure
```

---

## 23. Stop Gates

Implementation must stop and report the corresponding blocked status if any of the following occurs:

```text id="rfc-p3c2-stop-001"
BLOCKED — P3C2_APPROVAL_GATE_MISSING
BLOCKED — RFC_CHANGE_REQUIRED
BLOCKED — P2_CONTRACT_EXPANSION_REQUIRED
BLOCKED — NEW_SUSPENSION_REASON_REQUIRED
BLOCKED — MAILBOX_VOTE_DELIVERY_REQUIRED
BLOCKED — DURABLE_LIFECYCLE_EXPANSION_FOR_MAILBOX_REQUIRED
BLOCKED — NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
BLOCKED — PUBLIC_TICKET_API_REQUIRED
BLOCKED — PARSER_AST_LEXER_CHANGE_REQUIRED
BLOCKED — TICKET_CANCELLATION_REQUIRED
BLOCKED — TICKET_EXPIRATION_REQUIRED
BLOCKED — TIMEOUT_AUTO_RESOLUTION_REQUIRED
BLOCKED — TICKET_LIFECYCLE_STATUS_FIELD_IN_EVENT_REQUIRED
BLOCKED — AUTOMATIC_VARIABLE_REBINDING_REQUIRED
BLOCKED — RESOLUTION_WAITPOINT_NOT_REACHABLE_WITH_EXISTING_SYNTAX
BLOCKED — INTERPRETER_MONOLITH_EXPANSION_FOR_TICKET_RESOLUTION
BLOCKED — RESOLUTION_RECOMPUTATION_BYPASSES_ENGINE
BLOCKED — DUPLICATE_IDEMPOTENCY_SURFACE
BLOCKED — NEW_RESOLUTION_HASH_SCHEME_REQUIRED
BLOCKED — ADDITIONAL_PROJECTION_STATE_REQUIRED
BLOCKED — SUSPEND_EXPR_PAYLOAD_SHAPE_CHANGE_REQUIRED
BLOCKED — APPLICATION_P2_SURFACE_CHANGE_REQUIRED
BLOCKED — TICKET_PAYLOAD_STRUCTURE_UNDEFINED
BLOCKED — RESOLUTION_EVENT_MATCHING_AMBIGUOUS
BLOCKED — PROJECTION_STATE_HISTORY_MISMATCH
BLOCKED — DUPLICATE_RESOLUTION_APPENDS_SECOND_EVENT
BLOCKED — CONFLICTING_DUPLICATE_RESOLUTION
BLOCKED — HASH_PROFILE_MISMATCH
BLOCKED — PRODUCTION_PROTOCOL_CLAIM
BLOCKED — CAPABILITY_OVERCLAIM
BLOCKED — MATRIX_UPDATE_ATTEMPTED_BEFORE_EVIDENCE
BLOCKED — EVIDENCE_CREATED_BEFORE_IMPLEMENTATION_MERGE
```

---

## 24. Allowed Files for Implementation PR

The implementation PR may modify:

```text id="rfc-p3c2-files-001"
synapse/runtime/consensus_engine.py
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
tests/test_consensus_resolution_p3c2.py
```

The implementation PR may include compatibility-preserving updates to:

```text id="rfc-p3c2-files-002"
tests/test_consensus_engine_p3a.py
tests/test_consensus_adapter_p3a.py
tests/test_consensus_actor_method_p3b.py
tests/test_consensus_replay_p3c.py
tests/test_durable_execution.py
```

Only if required by the approved P3c-2 contract.

The implementation PR must not modify:

```text id="rfc-p3c2-files-003"
docs/RFC-*
docs/evidence/*
docs/CAPABILITY_MATURITY_MATRIX.md
parser / AST / lexer
workflows
examples
synapse/application.py
P2 artifact schema
public API documentation
```

The implementation PR must not add to `_SUPPORTED_SUSPENSION_REASONS`.

The implementation PR must not create new evidence documents.

---

## 25. Required Tests

### 25.1 LIVE Tests

Required LIVE tests:

```text id="rfc-p3c2-tests-live-001"
valid consensus_ticket_resolution suspend request is detected
valid resolution signal produces distributed_consensus_ticket_resolved
resolution event has closed schema
resolution event has correct ticket_id
resolution event has correct proposal_id
resolution event has correct statement_identity
resolution event has votes_final
resolution event has vote_counts_final
resolution event has votes_hash_final
resolution event has result_hash_final
projection changes from pending to resolved
original deferred consensus binding is not automatically re-bound
SuspendExpr returns injected signal unchanged
generic non-consensus suspend behavior remains unchanged
```

### 25.2 LIVE Fail-Closed Tests

Required LIVE fail-closed tests:

```text id="rfc-p3c2-tests-live-002"
ticket_id mismatch fails before append
ticket not found fails before append
resolution votes contain missing state fails before append
resolution votes omit missing participant fails before append
resolution votes include extra participant fails before append
resolution votes contain invalid vote state fails before append
malformed signal object fails before append
malformed request object fails before append
conflicting duplicate resolution fails before append
```

### 25.3 Duplicate Resolution Tests

Required duplicate-resolution tests:

```text id="rfc-p3c2-tests-dup-001"
already resolved ticket with identical resolution_votes is idempotent no-op
idempotent duplicate does not append duplicate distributed_consensus_ticket_resolved
idempotent duplicate does not mutate projection
already resolved ticket with conflicting resolution_votes fails closed
conflicting duplicate does not append history
conflicting duplicate does not mutate projection
```

### 25.4 Multiple Concurrent Ticket Tests

Required multiple-ticket tests:

```text id="rfc-p3c2-tests-multi-001"
multiple deferred tickets are independently created
each ticket has unique ticket_id
multiple pending tickets coexist in consensus_tickets
resolving ticket A does not mutate ticket B
resolving ticket B after ticket A works
interleaved resolution events preserve correct ticket_id
projection reconstructs independent resolved/pending states
```

### 25.5 REPLAY Tests

Required REPLAY tests:

```text id="rfc-p3c2-tests-replay-001"
replay consumes distributed_consensus_ticket_resolved
replay recomputes final outcome through engine
replay verifies votes_hash_final
replay verifies result_hash_final
replay updates projection to resolved
replay does not call live vote source
replay does not call actor vote methods
replay does not read mailbox
replay does not resolve promises physically
replay correctly handles arbitrary intermediate history before resolution boundary
replay correctly handles interleaved multiple tickets
```

### 25.6 REPLAY Fail-Closed Tests

Required REPLAY fail-closed tests:

```text id="rfc-p3c2-tests-replay-002"
missing resolution event when expected fails closed
wrong resolution event type fails closed
wrong schema_version fails closed
extra field fails closed
missing field fails closed
non-string key fails closed
votes_final mismatch fails closed
vote_counts_final mismatch fails closed
outcome mismatch fails closed
reason mismatch fails closed
votes_hash_final mismatch fails closed
result_hash_final mismatch fails closed
projection/history invariant mismatch fails closed
cursor rollback works
projection rollback works
```

### 25.7 Regression Tests

Required regression tests:

```text id="rfc-p3c2-tests-regression-001"
P3a tests remain green
P3b tests remain green
P3c-0 replay tests remain green
P3c-1 ticket creation/replay tests remain green
P2 durable execution tests remain green
generic suspend behavior remains green
```

---

## 26. Evidence Plan

After implementation merge, a separate evidence PR may update only:

```text id="rfc-p3c2-evidence-001"
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

P3c evidence must remain accumulated in `docs/evidence/P3C_EVIDENCE.md`.

No separate per-substage P3c evidence document is authorized by this RFC.

Expected capability wording after successful evidence closure:

```text id="rfc-p3c2-evidence-002"
Partial — P3b local actor-method vote source verified;
P3c-0 replay consumption closed;
P3c-1 durable ticket creation/replay closed;
P3c-2 durable ticket resolution closed via existing P2 resume boundary
```

The evidence must not claim:

```text id="rfc-p3c2-evidence-003"
P3c closed
distributed consensus Production
ticket lifecycle closed
mailbox-backed voting closed
network-backed consensus
daemon-backed consensus
LLM voting closed
production distributed consensus protocol behavior
```

---

## 27. Non-Normative External References

This RFC aligns with durable execution patterns in event-sourced workflow systems.

The following references are non-normative and are included only to document architecture alignment:

```text id="rfc-p3c2-external-001"
Temporal Workflow Execution:
  durable workflow execution, replay against event history, commands mapped to history.

Temporal Workflow Message Passing:
  external signals received by workflow code and buffered/handled by the workflow runtime.

Azure Durable Functions / Durable Task:
  deterministic orchestrator constraints, event sourcing, external event wait/raise pattern,
  and duplicate external event considerations.

Restate / journaled durable execution:
  durable step results recorded in a journal and reused during replay.
```

These external references do not override repository RFCs, P2 contracts, P3 contracts, or current code.

---

## 28. Implementation Sketch

This sketch is illustrative and non-normative. The normative contract is the text above. Actual implementation must follow ownership rules and may differ in function names or module structure.

### 28.1 Resolution Module Sketch

```python id="rfc-p3c2-sketch-001"
# synapse/runtime/consensus_ticket_resolution.py

RESOLUTION_EVENT_TYPE = "distributed_consensus_ticket_resolved"
RESOLUTION_SCHEMA_VERSION = "consensus.ticket.resolution.event.v1"

ALLOWED_RESOLUTION_EVENT_FIELDS = {
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "resolution_votes",
    "votes_final",
    "vote_counts_final",
    "outcome",
    "reason",
    "votes_hash_final",
    "result_hash_final",
}

def is_consensus_ticket_resolution_request(value):
    return (
        isinstance(value, dict)
        and value.get("kind") == "consensus_ticket_resolution"
    )

def validate_resolution_request_payload(value):
    # strict JSON-compatible request validation
    # ticket_id, missing_participants, votes_hash
    ...

def validate_resolution_signal_payload(value, *, trusted_ticket_id):
    # strict JSON-compatible signal validation
    # signal ticket_id must match trusted_ticket_id
    # votes object must match missing_participants
    ...

def validate_resolution_event_schema(event):
    # closed-schema validation
    ...

def validate_idempotent_duplicate_resolution(existing_projection, incoming_votes):
    # identical duplicate -> idempotent no-op
    # conflicting duplicate -> fail closed
    ...

def build_resolved_projection(ticket_projection, resolution_event):
    # returns a new projection dict
    ...
```

### 28.2 Engine Sketch

```python id="rfc-p3c2-sketch-002"
# synapse/runtime/consensus_engine.py

def resolve_pending_ticket(self, ticket_payload, resolution_votes):
    # Validate ticket payload shape enough to recompute.
    # Validate coverage against missing_participants.
    # Reject missing state in resolution_votes.
    # Merge original ticket votes and resolution_votes.
    # Recompute vote_counts_final.
    # Recompute final outcome and reason using same strategy/quorum logic.
    # Recompute votes_hash_final using consensus.votes.v1 list preimage.
    # Recompute result_hash_final using consensus.result.v1 preimage.
    # Build distributed_consensus_ticket_resolved event, using constants from
    # consensus_ticket_resolution module.
    # Return structured decision.
    ...
```

### 28.3 Interpreter Adapter Sketch

```python id="rfc-p3c2-sketch-003"
# synapse/interpreter.py

def suspend_expression(self, node, env):
    request_value = self.describe_request(node.request, env)
    promise = self.create_durable_promise("suspend", request_value)

    event = self.next_history_event("promise_resolved")
    if event is not None and event.get("promise_id") == promise.promise_id:
        injected = event.get("result")
        return self._maybe_apply_consensus_ticket_resolution(
            request_value=request_value,
            injected=injected,
        )

    injected = yield Suspension(
        node,
        env,
        reason="awaiting_external_signal",
        payload={"promise_id": promise.promise_id, "request": request_value},
    )
    self.resolve_promise(promise.promise_id, injected)

    return self._maybe_apply_consensus_ticket_resolution(
        request_value=request_value,
        injected=injected,
    )

def _maybe_apply_consensus_ticket_resolution(self, request_value, injected):
    if not consensus_ticket_resolution.is_consensus_ticket_resolution_request(request_value):
        return injected

    trusted_ticket_id = request_value["ticket_id"]

    # Validate request and signal.
    # Lookup pending/resolved ticket using trusted_ticket_id only.
    # For already resolved ticket:
    #   identical duplicate -> return injected unchanged, no append
    #   conflicting duplicate -> fail closed
    # For pending ticket:
    #   call engine.resolve_pending_ticket
    #   append returned event
    #   update projection
    # Return injected unchanged.
    ...
```

The sketch must not be treated as the final implementation plan. The ownership constraints, replay rules, validation rules, event contract, and stop gates above are normative.

---

## 29. Review Checklist

Reviewer must verify:

```text id="rfc-p3c2-review-001"
Status remains DRAFT before approval.
No implementation starts before approval gate.
No P2 contract change.
No application.py change.
No new supported suspension reason.
No parser / AST / lexer changes.
No mailbox-backed path.
No public ticket API.
No automatic variable re-binding.
SuspendExpr payload shape remains unchanged.
SuspendExpr returns injected signal unchanged.
Resolution convention is carried through request_value and injected signal.
trusted_ticket_id is taken from request_value, not injected signal.
New consensus_ticket_resolution.py module exists in implementation.
Engine owns final consensus math.
Interpreter remains thin adapter.
Resolution event schema is closed.
Replay matching is strict at resolution boundary.
Replay recomputes and compares final anchors.
Cursor rollback exists.
Projection rollback exists.
Duplicate identical resolution does not append duplicate event.
Conflicting duplicate resolution fails closed.
Multiple pending tickets resolve independently.
Evidence plan updates docs/evidence/P3C_EVIDENCE.md only.
Matrix update is deferred until evidence closure.
No separate per-substage P3c evidence document is created.
```

---

## 30. Final Recommendation

Approve drafting direction:

```text id="rfc-p3c2-final-001"
P3c-2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
```

Approve deferred direction:

```text id="rfc-p3c2-final-002"
P3c-N — Mailbox-backed Vote Delivery and Receive-based Vote Collection
```

Do not approve implementation until this RFC has passed an explicit approval gate.

Do not approve any mailbox vote delivery, network transport, daemon transport, LLM voting, public API, parser/AST/lexer change, new P2 suspension reason, P2 artifact schema change, separate per-substage P3c evidence document, or production protocol behavior in P3c-2.
