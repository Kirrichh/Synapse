# RFC-CONSENSUS-P3C1 — Durable Consensus Ticket Creation & Replay

**Status:** DRAFT  
**Stage:** P3c-1 RFC  
**Implementation status:** NOT AUTHORIZED UNTIL APPROVAL GATE  
**Repository mutation:** DOCUMENTATION DRAFT ONLY  
**Primary implementation slice:** P3c-1 — Durable Consensus Ticket Creation and Replay  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Mailbox-backed vote delivery in P3c-1:** NOT ALLOWED  
**DurablePromise-backed vote completion in P3c-1:** NOT ALLOWED  
**Signal-injected vote resolution in P3c-1:** NOT ALLOWED  
**Network-backed vote transport in P3c-1:** NOT ALLOWED  
**Daemon-backed vote transport in P3c-1:** NOT ALLOWED  
**Live large language model vote production in P3c-1:** NOT ALLOWED  
**Parser / abstract syntax tree / lexer expansion in P3c-1:** NOT ALLOWED  
**Public ticket inspection API in P3c-1:** NOT ALLOWED  
**Ticket finalization / cancellation / expiration in P3c-1:** NOT ALLOWED  
**Ticket lifecycle state vocabulary before P3c-2:** NOT ALLOWED  
**Legacy deferred ticket synthesis:** NOT ALLOWED  
**Capability target after successful P3c-1 evidence closure:** `Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed`  
**Capability target explicitly not claimed:** `Production`  
**Overall P3c status after this RFC draft:** OPEN

---

## 0. Purpose of this RFC

This RFC defines the P3c-1 contract for deterministic durable consensus ticket creation and replay.

P3c-0 closed canonical replay consumption for `distributed_consensus_decided` events. It made a recorded `consensus.event.v2` event replay-sufficient by storing a normalized vote map and by requiring replay to consume the recorded decision without recollecting votes, invoking live vote providers, appending duplicate events, or mutating side-effect state.

P3c-1 addresses the next structural gap:

```text id="zljcw9"
When distributed consensus produces outcome=deferred because votes are missing,
what durable first-class object records the pending consensus state, and how
must that object be replayed deterministically?
```

P3c-1 does not resolve the ticket. It only creates and replays the durable ticket creation boundary.

The ticket exists so that later approved stages can resolve missing votes through explicitly approved mechanisms. Those mechanisms are not part of P3c-1.

P3c-1 is not a production distributed consensus protocol. It is a runtime / verification primitive inside the project’s controlled execution model.

---

## 1. Product Statement

When a `DistributedConsensusStmt` evaluation produces:

```text id="ar5pr0"
outcome = deferred
reason  = pending_missing_votes
```

the runtime must durably journal a deterministic `distributed_consensus_ticket_created` event immediately after the synchronous `distributed_consensus_decided` event.

The created ticket is a stable, replay-safe anchor for future resolution stages. It allows the runtime to restore the fact that consensus is pending without recollecting votes, invoking actor vote methods, reading mailboxes, resolving promises, injecting signals, calling network or daemon transports, invoking live large language models, or using parser / abstract syntax tree / lexer extensions.

P3c-1 is a structural enabler for the broader Gold / Verification flow. It turns a deferred consensus result into a first-class durable object that can be replayed, projected, audited, and later resolved by a separately approved stage.

P3c-1 does not make distributed consensus a production distributed consensus protocol.

---

## 2. Canonical User / Runtime Path

P3c-1 remains inside the existing canonical runtime path:

```text id="vlyhy4"
python -m synapse run program.syn
→ parse .syn program
→ build abstract syntax tree
→ execute DistributedConsensusStmt in the interpreter
→ call ConsensusEngine.decide(...)
→ append durable history events
→ bind observable runtime result
→ support deterministic replay from execution_history
```

P3c-1 must not introduce a new public command, parser construct, abstract syntax tree node, lexer token, external daemon path, network path, or public ticket management API.

The public observable behavior is the normal consensus result plus durable execution history containing the new ticket creation event when the consensus result is deferred because of missing votes.

---

## 3. Relationship to Existing P3 Stages

### 3.1 Relationship to P3a

P3a established the deterministic semantic consensus core.

P3a owns:

```text id="jjx3kw"
proposal identity
participant normalization
vote state validation
quorum semantics
outcome semantics
reason semantics
votes_hash
result_hash
distributed_consensus_decided event shape
```

P3c-1 must not weaken or reinterpret P3a semantics.

P3c-1 extends the deferred case by adding a deterministic ticket creation payload when the engine result is:

```text id="3v1jk4"
outcome = deferred
reason  = pending_missing_votes
```

### 3.2 Relationship to P3b

P3b established explicit opt-in local actor-method vote collection behind the `VoteSource` seam.

P3c-1 must not broaden P3b actor-method voting.

P3c-1 must not add mailbox-backed voting, daemon voting, network voting, signal voting, promise completion, or live large language model voting.

P3c-1 only journals the durable ticket that records that consensus is pending.

### 3.3 Relationship to P3c-0

P3c-0 closed replay consumption for `distributed_consensus_decided`.

P3c-1 must preserve that contract.

For deferred consensus, P3c-1 does not replace `distributed_consensus_decided`. It appends a second event immediately after it:

```text id="nl78kv"
distributed_consensus_decided
distributed_consensus_ticket_created
```

The first event remains the semantic evaluation boundary.

The second event is the durable lifecycle anchor.

P3c-1 must not remove `distributed_consensus_decided` from the deferred path.

P3c-1 must not silently change P3c-0 replay semantics. It must explicitly extend P3c-0 replay semantics only for the deferred `pending_missing_votes` case by consuming one additional adjacent ticket event.

### 3.4 Relationship to P3c-2 and Later P3c Stages

P3c-2 and later stages may define how a ticket is resolved, cancelled, expired, resumed, completed by a promise, supplied by a mailbox message, supplied by a signal, or connected to a daemon or network transport.

P3c-1 does not define those transitions.

P3c-1 does not define future lifecycle status vocabulary.

P3c-1 must not contain a durable event field such as:

```text id="5lbv8u"
status: pending
```

The event type `distributed_consensus_ticket_created` itself denotes the creation of a pending ticket.

### 3.5 Relationship to P3d

P3d owns large language model assisted voting.

P3c-1 must not call live large language model providers, define prompt formats, model output schemas, refusal handling, provider timeout handling, cost controls, or replay rules for large language model votes.

If an implementation requires live large language model voting to complete P3c-1, it is outside this RFC and must stop.

---

## 4. Current State and Gap

### 4.1 Current Result Shape

The current consensus result already includes:

```text id="2txo3x"
deferred
ticket_id
```

However, `ticket_id` is currently always `None`.

This means the runtime has a public result slot for a future ticket, but no deterministic ticket identity, no ticket event, no ticket payload, and no replay projection.

### 4.2 Current Event Shape

The current `distributed_consensus_decided` event uses:

```text id="dkb0ih"
schema_version: consensus.event.v2
```

and records:

```text id="kthwf1"
proposal_id
statement_identity
outcome
reason
participants
coordinator
strategy
policy
quorum
timeout
votes
vote_counts
votes_hash
result_hash
```

This is sufficient for P3c-0 replay of the semantic decision.

It is not sufficient as a first-class durable ticket object for later resolution stages.

### 4.3 Current Registry Shape

The interpreter already contains:

```text id="vuiaxv"
consensus_tickets: Dict[str, Dict[str, Any]]
```

This field is present in runtime state projection and snapshot-related state. It is not currently populated by a deterministic P3c-1 ticket creation path.

P3c-1 defines the only authorized creation/replay path for this registry.

### 4.4 Why This Gap Matters

Without P3c-1, a deferred consensus result can be observed but cannot be represented as a durable first-class object.

That means the runtime can say:

```text id="8ckl3z"
consensus is deferred
```

but it cannot durably answer:

```text id="luxph9"
which exact pending consensus object exists?
which participants are still missing?
which vote snapshot was captured?
which deterministic ticket should future resolution target?
how must replay reconstruct that pending state?
```

P3c-1 closes this structural gap.

---

## 5. Core Design Decision

For a `DistributedConsensusStmt` whose semantic consensus result is:

```text id="l6atty"
outcome = deferred
reason  = pending_missing_votes
```

the runtime must write two adjacent history events in LIVE mode:

```text id="vrkpy4"
1. distributed_consensus_decided
2. distributed_consensus_ticket_created
```

The first event is the P3a/P3b/P3c-0 semantic decision boundary.

The second event is the P3c-1 durable ticket creation boundary.

These events have different responsibilities and must not be collapsed.

`distributed_consensus_decided` says:

```text id="iku9e4"
The consensus evaluation reached a semantic deferred outcome.
```

`distributed_consensus_ticket_created` says:

```text id="fbp82y"
A durable replay-safe ticket object now exists for future resolution stages.
```

The two events must be adjacent.

No unrelated history event may be appended between them in LIVE mode.

During REPLAY, if any unrelated event appears between the deferred decision event and the ticket creation event, the runtime must fail closed.

---

## 6. ConsensusTicketObject

### 6.1 Architectural Name

The architectural object is:

```text id="hz3440"
ConsensusTicketObject
```

This name is used in RFC text and Gold / Verification architecture discussion.

### 6.2 Runtime Type Name

The implementation may use the Python name:

```text id="osqyu7"
ConsensusTicket
```

This is consistent with existing runtime names such as:

```text id="rdm3kf"
ConsensusRequest
ConsensusDecision
VoteRecord
```

### 6.3 Object Role

A `ConsensusTicketObject` is not a distributed consensus protocol object.

A `ConsensusTicketObject` is a durable runtime object representing:

```text id="7p29hj"
A specific deferred consensus evaluation at a specific statement identity,
with a specific proposal, a specific vote map, and a specific missing
participant set.
```

It is created only for:

```text id="7rbmod"
outcome = deferred
reason  = pending_missing_votes
```

It is not created for:

```text id="l4o6r5"
committed
rejected
insufficient_quorum
explicit_no_vote
unanimity_broken_by_no
unanimity_broken_by_abstain
unknown deferred reason
```

### 6.4 Pending by Construction

The created ticket is pending by construction because the only authorized P3c-1 creation path is:

```text id="7zmvhu"
outcome = deferred
reason  = pending_missing_votes
```

No durable `status` field is required to express that fact.

No lifecycle transition vocabulary is authorized by P3c-1.

---

## 7. Ticket Identity Contract

### 7.1 Deterministic Identity

The ticket identity must be deterministic.

The identity is:

```text id="29j482"
ticket_id = sha256(canonical_json(ticket_preimage))
```

The hash prefix should follow existing consensus hash style if the implementation already prefixes consensus hashes with `sha256:`.

### 7.2 Ticket Preimage

The P3c-1 ticket preimage is:

```text id="6diuid"
schema_version: consensus.ticket.v1
proposal_id
statement_identity
missing_participants
votes_hash
```

### 7.3 Field Requirements

`schema_version` must be exactly:

```text id="4y1g12"
consensus.ticket.v1
```

`proposal_id` must be the engine-produced proposal identity for the same `DistributedConsensusStmt`.

`statement_identity` must be the interpreter-produced stable identity for the statement.

`missing_participants` must be a sorted list of normalized participant identities whose vote state is `missing`.

`missing_participants` must use the same normalized participant identity space and the same canonical ordering discipline as the participants used to construct `proposal_id`.

`votes_hash` must be the P3a/P3c-0 consensus votes hash for the current recorded vote map.

### 7.4 Votes Are Not in the Ticket Preimage

The full vote map is not included directly in the ticket preimage.

The full vote map is represented in the ticket preimage by `votes_hash`.

This keeps `ticket_id` compact and deterministic while still cryptographically binding the ticket to the vote snapshot.

### 7.5 Excluded from Ticket Preimage

The following must not enter the ticket preimage:

```text id="bp7qgj"
votes
vote_counts
source_label
coordinator
runtime UUID
random number
wall-clock time
process id
mailbox id
promise id
network route
daemon id
large language model provider id
actor process id
execution_history index
replay_cursor
```

### 7.6 Rationale

`votes` is intentionally excluded from the ticket preimage because `votes_hash` already commits to the full vote map using the consensus engine’s canonical hash rules.

`votes` is still recorded in the ticket event for auditability and future resolution stages.

`source_label` is intentionally excluded because P3c-0 established that source labels are provenance-only and do not enter `votes_hash`.

Runtime identifiers such as UUIDs, wall-clock time, random values, actor process ids, mailbox ids, promise ids, and network routes are excluded because ticket identity must replay to the same value from the same semantic inputs.

---

## 8. Ticket Event Contract

### 8.1 Event Type

P3c-1 introduces:

```text id="w6g385"
distributed_consensus_ticket_created
```

### 8.2 Event Schema

The event payload must use:

```text id="kep99f"
schema_version: consensus.ticket.event.v1
```

### 8.3 Required Event Fields

The event must contain:

```text id="h75eg5"
type
schema_version
ticket_id
proposal_id
statement_identity
participants
missing_participants
votes
vote_counts
votes_hash
strategy
policy
quorum
timeout
```

### 8.4 Field Definitions

`type` must be:

```text id="sb2wjs"
distributed_consensus_ticket_created
```

`schema_version` must be:

```text id="b5t0yr"
consensus.ticket.event.v1
```

`ticket_id` must match the deterministic hash of the `consensus.ticket.v1` preimage.

`proposal_id` must match the preceding `distributed_consensus_decided` event.

`statement_identity` must match the preceding `distributed_consensus_decided` event.

`participants` must match the normalized participant order used by `ConsensusEngine`.

`missing_participants` must be exactly the sorted list of participants whose vote state is `missing`.

`votes` must be the full normalized vote map at ticket creation time.

`votes` must include all vote states in the map, including:

```text id="uzp8nj"
yes
no
abstain
missing
```

The `votes` field in `distributed_consensus_ticket_created` must exactly match the `votes` field in the immediately preceding `distributed_consensus_decided` event.

The `votes` field must not filter out participants whose state is `missing`.

`vote_counts` must match the vote counts produced by `ConsensusEngine`.

`votes_hash` must match the preceding `distributed_consensus_decided` event.

`strategy`, `policy`, `quorum`, and `timeout` must match the preceding `distributed_consensus_decided` event.

### 8.5 Result Hash Is Not Repeated

`distributed_consensus_ticket_created` does not repeat `result_hash`.

The immediately preceding `distributed_consensus_decided` event remains the owner of `result_hash`.

The ticket event anchors to the decision through:

```text id="irxib8"
adjacency in execution_history
proposal_id
statement_identity
votes
votes_hash
missing_participants
hash_event_chain continuity
```

Repeating `result_hash` in the ticket event is not required for P3c-1.

### 8.6 Deliberately Omitted Field: status

The event must not contain:

```text id="zotcda"
status
```

The durable event type itself denotes that a ticket was created and is pending by construction.

P3c-1 does not define ticket lifecycle state vocabulary.

The following vocabulary is not authorized in P3c-1 durable event schema:

```text id="kca5n6"
pending
resolved
finalized
cancelled
expired
replayed
failed
closed
```

P3c-2 or a later approved RFC may define lifecycle transition vocabulary.

### 8.7 Internal Projection State

The interpreter may store an internal projection record in:

```text id="05zk49"
interpreter.consensus_tickets[ticket_id]
```

That record may contain a local projection marker, for example:

```text id="1oxcdb"
projection_state: pending
```

This projection marker is not durable event truth.

The durable truth is the `distributed_consensus_ticket_created` event.

Projection state names used internally in P3c-1 must not be treated as a durable lifecycle vocabulary.

---

## 9. ConsensusEngine Ownership

`ConsensusEngine` remains the single owner of semantic consensus mathematics.

In P3c-1, `ConsensusEngine` must own:

```text id="muye45"
ticket_id
ticket_preimage
ticket_payload
missing_participants derivation
votes projection into ticket payload
deferred-ticket invariant checks
```

The interpreter adapter must not independently derive `ticket_id`.

The interpreter adapter must not construct ticket preimage semantics in parallel.

The interpreter adapter must not change the `ticket_payload` shape after receiving it from `ConsensusEngine`, except for ordinary history append mechanics if the runtime has a standard append mechanism that injects history metadata.

### 9.1 ConsensusDecision Extension

`ConsensusDecision` may be extended as follows:

```python id="9g7bmk"
@dataclass(frozen=True)
class ConsensusDecision:
    result: Dict[str, Any]
    event_payload: Dict[str, Any]
    proposal_preimage: Dict[str, Any]
    votes_preimage: Dict[str, Any]
    result_preimage: Dict[str, Any]
    ticket_id: Optional[str] = None
    ticket_payload: Optional[Dict[str, Any]] = None
```

Existing fields must remain compatible.

The new fields are optional at the dataclass level because terminal consensus outcomes do not create tickets.

The new fields are populated only for the approved deferred case.

### 9.2 Dependent Invariant

`ticket_id` and `ticket_payload` are optional at the dataclass level because terminal consensus results do not produce tickets.

However, the following invariant is mandatory:

```text id="ya8041"
If result["outcome"] == "deferred" and result["reason"] == "pending_missing_votes":

    ticket_id MUST be non-null.
    ticket_payload MUST be non-null.
    result["ticket_id"] MUST equal ticket_id.
    ticket_payload["ticket_id"] MUST equal ticket_id.

If result["outcome"] != "deferred":

    ticket_id MUST be None.
    ticket_payload MUST be None.
    result["ticket_id"] MUST be None.
```

If the engine creates a deferred `pending_missing_votes` result without a ticket, the implementation is invalid.

If the engine creates a ticket for a committed or rejected result, the implementation is invalid.

### 9.3 Deferred Reason Validation

P3c-1 only authorizes tickets for:

```text id="z7ny99"
reason = pending_missing_votes
```

If the engine returns:

```text id="ncs8nd"
outcome = deferred
```

with any reason other than:

```text id="a3t4v0"
pending_missing_votes
```

the implementation must fail closed or raise a stable invalid request error before any ticket event is appended.

No ticket may be created for an unknown deferred reason in P3c-1.

If a future engine state introduces a new deferred reason, P3c-1 must fail closed unless a later RFC authorizes it.

---

## 10. Interpreter Adapter Ownership

The interpreter adapter owns the runtime interaction with history and environment.

In P3c-1, the interpreter owns:

```text id="ut8m44"
LIVE append order
REPLAY consumption order
replay_cursor advancement
environment binding
consensus_tickets projection mutation
failure translation to ConsensusReplayIntegrityError
```

The adapter must not own ticket identity mathematics.

The adapter must not use mailbox, promise, signal, daemon, network, large language model, parser, abstract syntax tree, or lexer features to create or resolve tickets.

The adapter must reuse the `ConsensusReplayIntegrityError` error family introduced for P3c-0 replay integrity failures.

P3c-1 must not define a duplicate replay integrity exception class.

---

## 11. LIVE Semantics

### 11.1 Terminal Result

If the consensus outcome is terminal:

```text id="nhfx8x"
committed
rejected
```

the interpreter must:

```text id="16m4u5"
append distributed_consensus_decided
bind decision.result
not append distributed_consensus_ticket_created
not mutate consensus_tickets
```

### 11.2 Deferred Result With Approved Reason

If the consensus outcome is:

```text id="9752in"
outcome = deferred
reason  = pending_missing_votes
```

the interpreter must:

```text id="0bcvpf"
append distributed_consensus_decided
append distributed_consensus_ticket_created immediately after it
populate consensus_tickets[ticket_id] from ticket_payload
bind decision.result
```

The append order is mandatory.

The `distributed_consensus_ticket_created` event must be adjacent to the `distributed_consensus_decided` event for the same statement.

No unrelated history event may be inserted between them.

### 11.3 Deferred Result With Unknown Reason

If the consensus outcome is:

```text id="22eur4"
outcome = deferred
```

and the reason is not:

```text id="zh8y3e"
pending_missing_votes
```

then P3c-1 must not append `distributed_consensus_ticket_created`.

The runtime must fail closed or raise a stable invalid request error before any ticket event is appended.

P3c-1 does not authorize inferred ticket semantics for unknown deferred reasons.

### 11.4 Environment Binding

The runtime result bound into the program environment must be the `ConsensusEngine` result.

The result must include:

```text id="hmcpw0"
ticket_id
```

with a deterministic non-null value for the deferred `pending_missing_votes` case.

The result must not include delivery, resolution, mailbox, promise, signal, daemon, network, large language model, or public ticket API fields.

---

## 12. REPLAY Semantics

### 12.1 Replay Extends P3c-0

P3c-1 replay must extend the P3c-0 replay branch.

Replay must not bypass P3c-0.

Replay must first consume the `distributed_consensus_decided` event using the existing P3c-0 logic.

For committed or rejected outcomes, replay behavior remains the P3c-0 behavior.

For the deferred `pending_missing_votes` outcome, P3c-1 adds validation and consumption of the immediately following ticket creation event.

### 12.2 Sequential Replay Cursor Advancement

For deferred consensus, replay consumes two adjacent events:

```text id="td9zka"
1. distributed_consensus_decided
2. distributed_consensus_ticket_created
```

The replay cursor advances exactly once per consumed event.

Therefore, one deferred `DistributedConsensusStmt` advances the replay cursor by exactly two events.

The required sequence is:

```text id="9991oq"
1. Consume distributed_consensus_decided using P3c-0 logic.
2. Inspect engine-produced result.
3. If result.outcome == "deferred" and result.reason == "pending_missing_votes":
   a. Peek the very next history event.
   b. Validate it is distributed_consensus_ticket_created.
   c. Validate its schema_version.
   d. Validate ticket_id.
   e. Validate proposal_id.
   f. Validate statement_identity.
   g. Validate missing_participants.
   h. Validate votes.
   i. Validate votes_hash.
   j. Consume the ticket event exactly once.
   k. Populate consensus_tickets projection.
4. Append nothing.
5. Bind the engine-produced result.
```

### 12.3 Peek Versus Consume

`peek_next_history_event()` is classification-only.

It must not advance `replay_cursor`.

`next_history_event(...)` is the consumption primitive.

It advances `replay_cursor` exactly once when it consumes the expected event.

P3c-1 replay must use `peek_next_history_event()` to validate the next event before consuming it.

P3c-1 replay must use `next_history_event("distributed_consensus_ticket_created")` or the approved equivalent to consume the ticket event.

The implementation must not call `next_history_event(...)` twice for the same ticket event.

The implementation must not validate with a consuming call and then consume again.

### 12.4 Missing Ticket Event

If replay consumes a deferred `distributed_consensus_decided` event and the next event is missing before the replay frontier, replay must fail closed.

Expected error family:

```text id="cyrfde"
ConsensusReplayIntegrityError
```

Suggested stable message:

```text id="3wytdd"
consensus ticket replay integrity mismatch: missing distributed_consensus_ticket_created after deferred decision
```

### 12.5 Wrong Next Event

If replay consumes a deferred `distributed_consensus_decided` event and the next event is not:

```text id="xmaxdc"
distributed_consensus_ticket_created
```

replay must fail closed.

Suggested stable message:

```text id="tr3hto"
consensus ticket replay integrity mismatch: expected distributed_consensus_ticket_created
```

### 12.6 Ticket Mismatch

Replay must fail closed for any mismatch in:

```text id="a7m4l8"
ticket_id
proposal_id
statement_identity
votes_hash
missing_participants
votes
schema_version
type
```

Suggested stable messages:

```text id="nmoxdo"
consensus ticket_id mismatch / non-determinism
consensus ticket proposal_id mismatch / non-determinism
consensus ticket statement_identity mismatch / non-determinism
consensus ticket votes_hash mismatch / non-determinism
consensus ticket missing_participants mismatch / non-determinism
consensus ticket votes mismatch / non-determinism
unsupported consensus ticket event schema
```

### 12.7 Duplicate Ticket Event

A duplicate `distributed_consensus_ticket_created` event before the replay frontier must fail closed if it would be consumed as the next event for a statement that does not expect a ticket.

The runtime must not silently skip duplicate ticket events.

The runtime must not use duplicate ticket events to overwrite projection state.

### 12.8 No Side Effects During Replay

Replay must not:

```text id="h00vvu"
append a new event
call live VoteSource
call ActorMethodVoteSource
call actor consensus_vote
read mailbox
write mailbox
create DurablePromise
resolve DurablePromise
inject signal
call network
call daemon
call live large language model provider
mutate consensus_tickets except by projecting the consumed ticket event
```

---

## 13. History Hash Chain Continuity

`distributed_consensus_ticket_created` is a standard `execution_history` event.

It must participate in the same tamper-evident history guarantees as other runtime events.

The required chain order for a deferred consensus statement is:

```text id="dfiun7"
previous event
→ distributed_consensus_decided
→ distributed_consensus_ticket_created
→ next event
```

The ticket event must be appended through the standard `execution_history.append(...)` path or the canonical runtime history append helper if such helper is introduced by the implementation.

The ticket event must participate in the existing `hash_event_chain` / `verify_history_chain` mechanism.

If the runtime stores a field such as `previous_hash`, or an equivalent chain-link field, the ticket event’s `previous_hash` must be exactly the hash of the immediately preceding `distributed_consensus_decided` event.

The interpreter’s standard history append mechanism must handle this linkage automatically.

Any manual bypass of the canonical history chain integration is a stop-gate violation.

The ticket event must not be written only to `consensus_tickets`.

The ticket event must not be written only to `actor_log`.

The ticket event must not bypass the canonical history integrity path.

Replay verification must treat the ticket event as part of the same ordered durable history.

---

## 14. Registry Projection Contract

### 14.1 Source of Truth

`execution_history` is the durable source of truth.

`consensus_tickets` is an internal projection.

A projection is a derived runtime view built from durable events. It may accelerate lookup or expose current in-memory state to future runtime stages, but it is not itself the durable source of truth.

The registry may be used for runtime lookup by later stages, but it does not independently define durable truth.

### 14.2 Allowed Registry Mutation

P3c-1 authorizes exactly two registry mutation points:

```text id="29aosy"
LIVE: immediately after appending distributed_consensus_ticket_created
REPLAY: immediately after consuming distributed_consensus_ticket_created
```

### 14.3 Forbidden Registry Mutation

P3c-1 forbids mutating `consensus_tickets` from:

```text id="nlm6cu"
mailbox handler
promise resolver
signal handler
network receiver
daemon receiver
large language model vote path
public API
test-only helper
parser side effect
abstract syntax tree transformation
lexer transformation
```

### 14.4 Projection Record

A projection record may include:

```text id="it72sj"
ticket_id
proposal_id
statement_identity
participants
missing_participants
votes
vote_counts
votes_hash
strategy
policy
quorum
timeout
projection_state
```

If `projection_state` exists, it is derived from the event type and is not durable lifecycle vocabulary.

P3c-1 must not define lifecycle transition states.

---

## 15. Compatibility Rules

### 15.1 P3a Compatibility

P3a semantic outcomes must remain unchanged.

P3c-1 may add a non-null `ticket_id` for deferred `pending_missing_votes` results.

P3c-1 must not change:

```text id="7gwk42"
participant normalization
strategy semantics
quorum semantics
vote state vocabulary
outcome vocabulary
reason vocabulary
votes_hash preimage
result_hash preimage
proposal_id preimage
```

### 15.2 P3b Compatibility

P3b actor-method vote source behavior must remain unchanged.

P3c-1 must not call actor-method vote providers during replay.

P3c-1 must not broaden actor-method voting.

### 15.3 P3c-0 Compatibility

P3c-0 replay consumption for `distributed_consensus_decided` must remain intact.

P3c-1 must add ticket consumption after P3c-0 consumption for the deferred case.

P3c-1 must not weaken any P3c-0 fail-closed behavior.

### 15.4 Legacy Events

P3c-1 does not silently upgrade old history.

P3c-1 does not mutate old events.

P3c-1 does not write a ticket event into legacy history during replay.

P3c-1 only claims replay support for histories containing the approved ticket event after a deferred decision.

### 15.5 Legacy Deferred History Rule

If REPLAY encounters a legacy `distributed_consensus_decided` event with:

```text id="ap45z0"
outcome = deferred
reason  = pending_missing_votes
```

and that event is not immediately followed by:

```text id="g3g6vd"
distributed_consensus_ticket_created
```

then runtime must fail closed with:

```text id="5p1a6l"
ConsensusReplayIntegrityError
```

The runtime must not auto-generate a ticket.

The runtime must not synthesize a ticket.

The runtime must not project a ticket from old history.

The runtime must not silently upgrade old deferred histories.

Old deferred histories without a matching ticket event are treated as pre-P3c-1 dead ends for this replay path.

---

## 16. Failure Semantics

P3c-1 follows fail-closed semantics.

Unknown, damaged, incompatible, missing, out-of-order, duplicated, or nondeterministic ticket data must not be replaced by silent fallback values.

P3c-1 must fail closed for:

```text id="3xfesr"
missing ticket_created after deferred decision
ticket_created after terminal decision
ticket_id mismatch
proposal_id mismatch
statement_identity mismatch
votes_hash mismatch
missing_participants mismatch
votes mismatch
malformed votes
unsupported schema_version
duplicate ticket_created before replay frontier
ticket registry state without history support
nondeterministic ticket_id
legacy deferred decision without adjacent ticket_created
```

P3c-1 must not continue replay with invented ticket data.

P3c-1 must not use `legacy_unknown` style projection for tickets.

---

## 17. Non-Claims

P3c-1 does not claim or implement:

```text id="pekfk7"
ticket resolution
ticket finalization
ticket cancellation
ticket expiration
ticket lifecycle state machine
ticket lifecycle state vocabulary
public ticket inspection API
mailbox-backed vote delivery
DurablePromise-backed vote completion
signal-injected vote resolution
await/suspend vote collection
network-backed vote transport
daemon-backed vote transport
live large language model vote production
parser expansion
abstract syntax tree expansion
lexer expansion
event v1 migration
silent event upgrade
durable allowlist expansion
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

## 18. Stop Gates

Implementation must stop and report the corresponding blocked status if any of the following occurs:

```text id="jv1drs"
BLOCKED — P3C1_APPROVAL_GATE_MISSING
BLOCKED — CONTRACT_NOT_DEFINED
BLOCKED — CANONICAL_OWNER_NOT_DEFINED
BLOCKED — NONDETERMINISTIC_TICKET_ID
BLOCKED — HASH_ALGORITHM_CHANGE_REQUIRED
BLOCKED — CANONICAL_SERIALIZATION_CHANGE_REQUIRED
BLOCKED — P3C0_REPLAY_CONTRACT_REGRESSION
BLOCKED — DISTRIBUTED_CONSENSUS_DECIDED_REMOVED_FOR_DEFERRED
BLOCKED — TICKET_CREATED_EVENT_SCHEMA_UNDEFINED
BLOCKED — TICKET_REPLAY_FAILURE_SEMANTICS_UNDEFINED
BLOCKED — TICKET_REGISTRY_SOURCE_OF_TRUTH_AMBIGUOUS
BLOCKED — DURABLEPROMISE_COMPLETION_REQUIRED
BLOCKED — MAILBOX_DELIVERY_REQUIRED
BLOCKED — SIGNAL_RESOLUTION_REQUIRED
BLOCKED — NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
BLOCKED — LLM_VOTE_PRODUCTION_REQUIRED
BLOCKED — PARSER_AST_LEXER_CHANGE_REQUIRED
BLOCKED — PUBLIC_TICKET_API_REQUIRED
BLOCKED — TICKET_FINALIZATION_REQUIRED
BLOCKED — TICKET_CANCELLATION_REQUIRED
BLOCKED — TICKET_EXPIRATION_REQUIRED
BLOCKED — TICKET_LIFECYCLE_STATE_VOCABULARY_DEFINED_BEFORE_P3C2
BLOCKED — PRODUCTION_PROTOCOL_CLAIM
BLOCKED — CAPABILITY_OVERCLAIM
BLOCKED — MATRIX_UPDATE_ATTEMPTED_BEFORE_EVIDENCE
BLOCKED — EVIDENCE_CREATED_BEFORE_IMPLEMENTATION_MERGE
BLOCKED — LEGACY_DEFERRED_TICKET_SYNTHESIS_ATTEMPTED
BLOCKED — TICKET_EVENT_HASH_CHAIN_BYPASS
BLOCKED — TICKET_REPLAY_CURSOR_ADVANCEMENT_AMBIGUOUS
```

---

## 19. Allowed Files for Implementation PR

The implementation PR may modify:

```text id="inw2ye"
synapse/runtime/consensus_engine.py
synapse/interpreter.py
tests/test_consensus_replay_p3c.py
```

The implementation PR may include compatibility-preserving updates to:

```text id="vklzhh"
tests/test_consensus_engine_p3a.py
tests/test_consensus_adapter_p3a.py
tests/test_consensus_actor_method_p3b.py
```

Allowed reason:

```text id="dk02re"
Deferred consensus now carries a deterministic ticket_id and appends a second
history event for ticket creation. Existing P3a/P3b/P3c-0 tests may need to
expect this approved durable behavior.
```

These updates must not weaken P3a, P3b, or P3c-0 assertions.

The implementation PR must not modify:

```text id="04t0ti"
parser
abstract syntax tree definitions
lexer
examples
documentation matrix
evidence files
RFC text
workflows
large language model providers
network or daemon transport
public API documentation
```

unless a later approved RFC explicitly authorizes it.

---

## 20. Required Tests

The implementation PR must include tests covering:

```text id="9xkg83"
LIVE deferred appends distributed_consensus_decided then distributed_consensus_ticket_created
LIVE deferred result contains deterministic ticket_id
ticket_id is stable across equivalent LIVE and REPLAY
ticket_id mismatch fails closed
proposal_id mismatch fails closed
statement_identity mismatch fails closed
votes_hash mismatch fails closed
missing_participants mismatch fails closed
votes mismatch fails closed
missing ticket_created after deferred decided fails closed
wrong next event after deferred decided fails closed
duplicate ticket_created before frontier fails closed
committed outcome does not create ticket
rejected outcome does not create ticket
insufficient_quorum does not create ticket
unknown deferred reason is rejected or fails closed
REPLAY consumes both events and appends nothing
REPLAY advances replay_cursor exactly twice for one deferred DistributedConsensusStmt
REPLAY does not leave ticket_created to be consumed by the next statement
REPLAY populates consensus_tickets only from ticket_created
consensus_tickets is not treated as source of truth without history
ticket event participates in canonical history chain
legacy deferred decided event without adjacent ticket_created fails closed
ticket preimage excludes uuid, random, time, process_id, promise_id, mailbox_id, network route, daemon id, source_label, replay_cursor, and execution_history index
missing_participants is explicitly sorted and deterministic
P3a regression behavior remains intact
P3b regression behavior remains intact
P3c-0 replay behavior remains intact
```

The test suite must not create test-only production paths.

Tests must confirm the approved RFC semantics. Tests must not define semantics not present in this RFC.

---

## 21. Evidence Plan

After implementation merge, a separate evidence PR may create:

```text id="7q6hxu"
docs/evidence/P3C1_EVIDENCE.md
```

or another evidence file name explicitly approved by the team.

The evidence PR may update:

```text id="msy4hy"
docs/CAPABILITY_MATURITY_MATRIX.md
```

only if evidence supports the capability wording.

The evidence PR must be documentation-only.

The evidence PR must not modify runtime code, tests, parser, abstract syntax tree, lexer, examples, workflows, RFC text, or previous P3 evidence files except by explicit approved evidence plan.

The expected capability wording after successful evidence closure is:

```text id="gh8pmv"
Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed
```

The evidence PR must not write:

```text id="buvwtk"
P3c closed
ticket lifecycle closed
distributed consensus complete
Production distributed consensus
```

---

## 22. Open Design Decisions Closed by This RFC Draft

This draft records the following accepted decisions:

```text id="o9tf9v"
P3c-1 is ticket creation/replay only.
P3c-1 preserves distributed_consensus_decided for deferred outcomes.
P3c-1 adds distributed_consensus_ticket_created as a second adjacent event.
P3c-1 omits status from durable ticket event.
P3c-1 does not define lifecycle state vocabulary.
P3c-1 uses deterministic ticket_id.
P3c-1 excludes votes from ticket_id preimage.
P3c-1 stores the full normalized votes map in ticket payload.
P3c-1 uses the event field name votes, not known_votes.
P3c-1 treats consensus_tickets as projection, not durable truth.
P3c-1 authorizes compatibility-preserving updates to selected P3 tests.
P3c-1 does not update capability matrix until evidence closure.
P3c-1 fails closed for legacy deferred histories without ticket_created.
P3c-1 requires ticket_created to participate in hash_event_chain.
```

---

## 23. Remaining Questions for Approval Gate

The draft intentionally leaves only implementation-level choices for the approval gate.

These are not architecture-open questions.

They are finalization details:

```text id="pngp5c"
Exact stable error message strings for each ConsensusReplayIntegrityError case.
Final helper names for ticket preimage construction.
Final helper names for ticket event validation.
Final evidence file name after implementation merge.
Whether the ticket projection record uses projection_state internally or stores only event fields.
Whether hash_event_chain linkage is provided by direct execution_history.append or by an existing canonical helper.
```

The approval gate must not reopen the following decisions unless explicitly instructed by the product owner:

```text id="epzdbs"
event model
status omission
ticket preimage fields
ConsensusEngine ownership
Interpreter adapter ownership
registry projection contract
non-scope restrictions
capability wording
```

---

## 24. Implementation Sketch

This sketch is non-normative.

The normative contract is the text above.

The sketch is included only to reduce ambiguity for implementers.

If the sketch conflicts with the normative text, the normative text wins.

### 24.1 Engine-Side Shape

```python id="qoel3w"
ticket_payload = None
ticket_id = None

if outcome == "deferred" and reason == "pending_missing_votes":
    missing_participants = sorted(
        participant
        for participant in participants
        if votes[participant] == "missing"
    )

    ticket_preimage = {
        "schema_version": "consensus.ticket.v1",
        "proposal_id": proposal_id,
        "statement_identity": statement_identity,
        "missing_participants": missing_participants,
        "votes_hash": votes_hash,
    }

    ticket_id = self._hash_payload(ticket_preimage)

    ticket_payload = {
        "type": "distributed_consensus_ticket_created",
        "schema_version": "consensus.ticket.event.v1",
        "ticket_id": ticket_id,
        "proposal_id": proposal_id,
        "statement_identity": statement_identity,
        "participants": participants,
        "missing_participants": missing_participants,
        "votes": {participant: votes[participant] for participant in participants},
        "vote_counts": vote_counts,
        "votes_hash": votes_hash,
        "strategy": strategy,
        "policy": policy,
        "quorum": quorum,
        "timeout": timeout,
    }

    result["ticket_id"] = ticket_id
```

The implementation must not derive `missing_participants` from a set.

The implementation must not derive `missing_participants` from an unordered dictionary traversal.

The implementation must not include UUID, random, time, mailbox, promise, network, daemon, actor process, replay cursor, or execution index data in `ticket_preimage`.

### 24.2 Adapter-Side LIVE Sketch

```python id="riejtl"
decision = self._consensus_engine.decide(request)

self.execution_history.append(dict(decision.event_payload))

if decision.ticket_payload is not None:
    self.execution_history.append(dict(decision.ticket_payload))
    self.consensus_tickets[decision.ticket_id] = dict(decision.ticket_payload)

env.define(node.binding, decision.result)
return decision.result
```

The implementation must ensure that `distributed_consensus_ticket_created` is appended immediately after `distributed_consensus_decided`.

The implementation must ensure that the ticket event participates in the same history chain as the decision event.

### 24.3 Adapter-Side REPLAY Sketch

```python id="lixqs7"
decision_result = self._consume_replayed_distributed_consensus(
    request,
    replay_event,
    node,
    env,
)

if (
    decision_result["outcome"] == "deferred"
    and decision_result["reason"] == "pending_missing_votes"
):
    ticket_event = self.peek_next_history_event()

    if not isinstance(ticket_event, Mapping):
        raise ConsensusReplayIntegrityError(
            "malformed consensus ticket replay event"
        )

    if ticket_event.get("type") != "distributed_consensus_ticket_created":
        raise ConsensusReplayIntegrityError(
            "consensus ticket replay integrity mismatch: expected distributed_consensus_ticket_created"
        )

    if ticket_event.get("schema_version") != "consensus.ticket.event.v1":
        raise ConsensusReplayIntegrityError(
            "unsupported consensus ticket event schema"
        )

    if decision_result["ticket_id"] != ticket_event.get("ticket_id"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket_id mismatch / non-determinism"
        )

    if decision_result["proposal_id"] != ticket_event.get("proposal_id"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket proposal_id mismatch / non-determinism"
        )

    expected_statement_identity = request.statement_identity
    if expected_statement_identity != ticket_event.get("statement_identity"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket statement_identity mismatch / non-determinism"
        )

    if decision_result["votes_hash"] != ticket_event.get("votes_hash"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket votes_hash mismatch / non-determinism"
        )

    expected_votes = decision_result["votes"]
    if expected_votes != ticket_event.get("votes"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket votes mismatch / non-determinism"
        )

    expected_missing = sorted(
        participant
        for participant, vote in expected_votes.items()
        if vote == "missing"
    )
    if expected_missing != ticket_event.get("missing_participants"):
        raise ConsensusReplayIntegrityError(
            "consensus ticket missing_participants mismatch / non-determinism"
        )

    consumed = self.next_history_event("distributed_consensus_ticket_created")

    if consumed is None:
        raise ConsensusReplayIntegrityError(
            "consensus ticket replay event disappeared before consumption"
        )

    self.consensus_tickets[decision_result["ticket_id"]] = dict(consumed)

env.define(node.binding, decision_result)
return decision_result
```

The actual implementation may use helper functions.

The actual implementation must preserve the normative ownership, order, validation, cursor advancement, and failure semantics.

---

## 25. Review Checklist

A reviewer must verify:

```text id="oimi0w"
RFC status is DRAFT before approval.
Implementation is not started before approval gate.
distributed_consensus_decided remains present for deferred outcomes.
distributed_consensus_ticket_created is separate and adjacent.
Replay cursor advances once per consumed event.
Deferred replay consumes exactly two adjacent events.
peek validates without advancing.
next consumes and advances.
Ticket event participates in standard history chain.
Ticket event previous_hash or equivalent chain link points to the immediately preceding distributed_consensus_decided event.
ticket_id is deterministic.
No uuid/random/time enters ticket preimage.
votes is not in ticket preimage.
votes is in ticket payload.
votes includes missing states.
missing_participants is explicitly sorted.
status field is absent from durable ticket event.
No lifecycle state vocabulary is introduced.
ConsensusEngine owns ticket math.
Interpreter owns append/consume/projection.
consensus_tickets is projection only.
Legacy deferred history without ticket_created fails closed.
No mailbox/promise/signal/network/daemon/LLM behavior is introduced.
No parser/AST/lexer changes are introduced.
Capability matrix is not updated before evidence.
```

---

## 26. Final Recommendation

Approve drafting direction:

```text id="uvox5i"
P3c-1 — Durable Consensus Ticket Creation & Replay
```

Approve event model:

```text id="p8vfeb"
distributed_consensus_decided
distributed_consensus_ticket_created
```

Approve ticket identity:

```text id="z3hkhl"
sha256(canonical_json(consensus.ticket.v1 preimage))
```

Approve durable event:

```text id="fk5rnu"
distributed_consensus_ticket_created
```

Approve omission of durable `status` field.

Approve the event field name:

```text id="p4o5rw"
votes
```

not:

```text id="5hs5ea"
known_votes
```

Approve explicit fail-closed behavior for legacy deferred histories without adjacent `distributed_consensus_ticket_created`.

Approve explicit participation of ticket events in canonical history chain.

Approve P3c-1 as the next RFC after P3c-0 evidence closure.

Do not approve implementation until this RFC has passed an explicit approval gate.

Do not approve any ticket resolution, delivery, mailbox, promise, signal, network, daemon, large language model, parser, abstract syntax tree, lexer, public ticket API, lifecycle transition, lifecycle state vocabulary, or production distributed consensus protocol behavior in P3c-1.
