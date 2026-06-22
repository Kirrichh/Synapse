# RFC-CONSENSUS-P3CN1 — Local Mailbox-backed Vote Response Collection for Existing Pending Consensus Tickets

**Status:** DRAFT  
**Stage:** P3c-N1 RFC  
**Repository mutation:** DOCUMENTATION RFC DRAFT ONLY  
**Implementation status:** NOT AUTHORIZED  
**Approval status:** NOT APPROVED FOR IMPLEMENTATION  
**Target capability:** Add local mailbox-backed vote response collection for already-created pending consensus tickets.  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Fresh durable `DistributedConsensusStmt` execution:** NOT IN SCOPE  
**Vote request delivery:** NOT IN SCOPE FOR P3c-N1 unless separately approved  
**Network / daemon transport:** NOT IN SCOPE  
**Persistent durable inbox / early delivery:** NOT IN SCOPE  
**Durable timer / wall-clock scheduler:** NOT IN SCOPE  
**Parser / AST / lexer expansion:** NOT IN SCOPE  
**Primary dependency:** P2 mailbox wait durable lifecycle closed for approved P2 receive-wait scope.  
**Primary design rule:** P3c-N1 adds a runtime-domain mailbox vote response collection layer for existing pending tickets. It does not replace `ConsensusEngine`.

---

## 0. Source Anchors

This RFC is grounded in committed project facts and external distributed-workflow/messaging design constraints.

### Repository anchors

- `[S1]` PR #51 merged post-code evidence for P2 mailbox wait and updated P2 / capability documentation.
- `[S2]` P2 mailbox wait now supports durable `awaiting_message` and `awaiting_message_or_timeout`.
- `[S3]` P2 mailbox wait explicitly does not implement P3c-N, consensus mailbox vote delivery, receive-based vote collection, consensus-specific mailbox schemas, network/daemon transport, durable timers, persistent durable inbox, multi-pattern receive, parser/AST/lexer expansion, or production distributed consensus protocol behavior.
- `[S4]` `_SUPPORTED_SUSPENSION_REASONS` includes mailbox wait reasons.
- `[S5]` The durable subset still forbids general loops, general member access, and durable `DistributedConsensusStmt` execution.
- `[S6]` The actual AST node is `DistributedConsensusStmt`, not `DistributedConsensusExpr`.
- `[S7]` `ConsensusEngine` already owns deterministic proposal preparation, vote collection through `VoteSource`, canonical hashing, result construction, and ticket creation.
- `[S8]` `ConsensusEngine` already supports `rejected` outcomes; P3c-N1 must not invent reject semantics from scratch.
- `[S9]` P3c-0, P3c-1 and P3c-2 are evidence-closed, but Distributed consensus remains Partial and overall P3c remains open.
- `[S10]` `ActorRuntime` has local mailbox mechanics and process mailbox identities for spawned actors.
- `[S11]` P2 mailbox wait helpers validate external `mailbox_message` / `mailbox_timeout`, normalize mailbox signal hashes, validate replayed receive events, and enforce receiver binding.
- `[S12]` `ActorMethodVoteSource` exists as a synchronous local actor-method vote source, but it is not mailbox-backed.
- `[S13]` P3c-2 ticket resolution already validates resolution votes and produces terminal deterministic resolution events.

### External anchors

- `[E1]` Durable workflow systems require deterministic replay: replayed workflow code must emit the same durable commands/API calls in the same sequence; nondeterministic interactions must be externalized into replay-safe history.
- `[E2]` Event-sourced orchestrators constrain direct time/random/external APIs because replay must reproduce the same state.
- `[E3]` Actor message delivery commonly provides at-most-once delivery and per sender-receiver ordering, but stronger delivery/duplicate semantics must be built above the transport layer.
- `[E4]` Request/reply messaging requires correlation identifiers so replies can be bound to the request they answer.
- `[E5]` Message receivers may see duplicates and must implement idempotent receiver semantics through de-duplication or idempotent message semantics.

---

## 1. Purpose

This RFC defines the first mailbox-backed consensus substage after P3c-2:

```text
P3c-N1 — Local Mailbox-backed Vote Response Collection for Existing Pending Consensus Tickets
```

P3c-N1 exists because the runtime now has durable mailbox wait mechanics, and P3c-2 already has deterministic pending-ticket resolution, but there is not yet a consensus-domain layer that can consume mailbox-delivered vote responses and convert them into validated resolution votes.

P3c-N1 does **not** introduce fresh mailbox-backed consensus from an initial `DistributedConsensusStmt`.

P3c-N1 does **not** authorize changing the parser, lexer, AST, or durable classification of `DistributedConsensusStmt`.

P3c-N1 targets only this product question:

```text
Given an existing pending consensus ticket produced by earlier P3c mechanics,
can local mailbox-delivered vote responses be validated, accumulated, deduplicated,
replayed, and converted into P3c-2-compatible resolution votes?
```

This RFC does not authorize implementation. It prepares the contract and approval surface for a later implementation PR.

---

## 2. Executive Summary

P3c-N1 is now technically possible because P2 mailbox wait durable lifecycle is closed for the approved P2 receive-wait scope. Durable execution can suspend on `awaiting_message` and `awaiting_message_or_timeout`, validate mailbox resume envelopes, normalize mailbox signal hashes before idempotency lookup, and replay `message_received` / `receive_timeout` events.

However, P3c-N1 must not be implemented as a user-authored `.syn` loop such as:

```text
while not all_votes_received:
    receive timeout X {
        voter => msg {
            collect(msg.vote)
        }
    }
```

That shape is incompatible with the current durable subset:

- general `ForStmt` is unsupported;
- general `WhileStmt` is unsupported;
- general `MemberAccess` is unsupported;
- `DistributedConsensusStmt` itself is currently unsupported by the durable validator.

Therefore P3c-N1 is not a DSL-level vote collection loop.

The correct architecture is:

```text
existing pending consensus ticket
  -> P3c-N1 runtime-domain mailbox collection module
  -> P2 mailbox wait boundary
  -> strict consensus vote response validation
  -> domain duplicate policy
  -> collection projection
  -> complete coverage check
  -> ConsensusEngine.resolve_pending_ticket(...)
  -> existing distributed_consensus_ticket_resolved event
```

The core rule is:

```text
P3c-N1 adds mailbox-backed vote response collection for existing pending tickets.
P3c-N1 does not replace ConsensusEngine.
P3c-N1 does not implement fresh durable DistributedConsensusStmt execution.
```

---

## 3. Relationship to Prior Stages

### 3.1 P3a

P3a delivered the deterministic semantic consensus core:

- `ConsensusEngine`;
- `ConsensusRequest`;
- `VoteSource`;
- `VoteRecord`;
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

That path is synchronous and local. It can call a local actor method such as `consensus_vote(proposal)` and return a `VoteRecord`, but it is not mailbox-backed vote delivery or durable mailbox response collection.

P3c-N1 does not delete P3b. It adds a different vote ingestion path for existing pending tickets.

### 3.3 P3c-0

P3c-0 closed replay consumption for recorded `distributed_consensus_decided`.

P3c-N1 must not weaken replay integrity for already-recorded consensus events.

### 3.4 P3c-1

P3c-1 closed durable consensus ticket creation and replay for deferred consensus.

P3c-N1 starts from an existing pending ticket produced by this already-approved lifecycle.

### 3.5 P3c-2

P3c-2 closed durable ticket resolution through an existing P2 external-signal boundary.

P3c-N1 reuses the terminal resolution semantics from P3c-2 once mailbox-collected vote responses cover the pending ticket's full `missing_participants` set.

P3c-N1 must not duplicate final consensus mathematics already owned by `ConsensusEngine.resolve_pending_ticket()`.

### 3.6 P2 mailbox wait

P2 mailbox wait closed the generic durable receive-wait lifecycle.

P3c-N1 depends on this, but P2 mailbox wait does not itself define consensus-specific vote response schemas, participant validation, duplicate vote policy, or pending-ticket collection lifecycle.

---

## 4. Current Code Facts

### 4.1 The AST node is `DistributedConsensusStmt`

The current consensus AST surface is statement-based:

```python
@dataclass
class DistributedConsensusStmt(Node):
    participants: List[Node] = field(default_factory=list)
    topic: Optional[Node] = None
    quorum: Optional[Node] = None
    timeout: Optional[Node] = None
    policy_ref: Optional[str] = None
    binding: str = "vote"
```

There is no current `DistributedConsensusExpr`.

### 4.2 `DistributedConsensusStmt` remains unsupported in durable subset

The durable validator currently classifies `DistributedConsensusStmt` as `UNSUPPORTED_EXECUTION_ENGINE`.

Therefore P3c-N1 must not require fresh durable execution of `DistributedConsensusStmt`.

If a later stage wants constrained durable `DistributedConsensusStmt` support, that must be a separate approval scope.

### 4.3 General durable DSL collection is not available

The durable subset still rejects:

```text
ForStmt
WhileStmt
MemberAccess
```

Therefore P3c-N1 cannot depend on durable `.syn` code that loops over vote responses or extracts fields via `msg.vote`.

Consensus-domain vote extraction must happen in runtime-domain validation code, not in user-authored durable pattern bodies.

### 4.4 `ConsensusEngine` is a deterministic core, not a mailbox collector

`ConsensusEngine.decide()` prepares a proposal, collects votes through the `VoteSource` seam, derives counts, computes outcome/reason, builds hashes, and creates a deterministic result/event payload.

`ConsensusEngine.resolve_pending_ticket()` deterministically merges final votes into a pending ticket and produces the terminal resolution event.

P3c-N1 must preserve the engine's ownership of:

- participant normalization;
- strategy selection;
- quorum derivation;
- timeout normalization;
- proposal identity;
- vote counting;
- outcome/reason derivation;
- `votes_hash`;
- `result_hash`;
- terminal ticket resolution.

### 4.5 Vote sources are not hardcoded yes

The default source is `NullVoteSource`, which returns `missing` for each participant.

`ExplicitVoteSource` accepts deterministic supplied vote records.

`ActorMethodVoteSource` can synchronously call local `consensus_vote(proposal)` for local `AgentRuntime` participants.

P3c-N1 adds a mailbox-backed response ingestion path. It does not claim the existing vote sources are invalid.

### 4.6 Reject semantics already exist

`ConsensusEngine._evaluate_outcome()` already produces `rejected` for cases such as:

- insufficient quorum;
- unanimity broken by no;
- unanimity broken by abstain;
- explicit no vote under `NoVetoVote`.

P3c-N1 must not claim reject semantics are absent from the engine.

### 4.7 Actor mailbox identity is process-keyed

Spawned actors receive durable process IDs such as:

```text
Inbox#<suffix>
```

Local `send_message()` writes to `mailboxes[receiver]`.

Top-level durable receive may wait on `global`.

This means P3c-N1 must explicitly distinguish:

```text
participant_id
actor_name
process_id
mailbox receiver key
vote author identity
coordinator identity
```

### 4.8 Spawned process IDs are replayed, not recomputed

Spawned process IDs use nondeterministic UUID generation in live execution, but replay restores the process ID from the recorded `actor_spawned` event.

Therefore any participant-to-mailbox binding that depends on spawned process IDs must be replay-derived from recorded runtime history or existing ticket/projection state. It must not recompute mailbox IDs during replay.

---

## 5. Problem Statement

Current P3 consensus can:

- create deterministic consensus decisions;
- represent missing votes as `missing`;
- create durable pending consensus tickets;
- replay consensus decisions;
- replay ticket creation;
- resolve a pending ticket through a P2 external signal.

Current P3 consensus cannot yet:

- consume mailbox-delivered vote responses for a pending ticket;
- bind vote responses to pending ticket `missing_participants`;
- validate participant-to-mailbox identity for vote responses;
- enforce one vote per participant at domain level;
- accumulate collected votes across one or more durable mailbox resume cycles;
- replay mailbox-backed vote collection as a consensus-domain sequence;
- convert mailbox vote responses into P3c-2-compatible `resolution_votes`;
- resolve a pending ticket from mailbox-collected votes.

This RFC defines the missing P3c-N1 contract.

---

## 6. Non-goals

P3c-N1 must not implement or claim:

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

P3c-N1 also must not weaken existing P3a/P3b/P3c-0/P3c-1/P3c-2 evidence.

---

## 7. Architectural Principles

### 7.1 Pending-ticket-only first slice

P3c-N1 works only with already-created pending consensus tickets.

The ticket already contains:

- `ticket_id`;
- `proposal_id`;
- `statement_identity`;
- `participants`;
- `missing_participants`;
- partial vote map;
- vote counts;
- strategy;
- policy;
- quorum;
- timeout;
- `votes_hash`.

P3c-N1 does not create fresh consensus tickets.

P3c-N1 does not authorize durable `DistributedConsensusStmt`.

### 7.2 Runtime-domain collection, not DSL collection

P3c-N1 must not require `.syn` code to parse `msg.vote`, loop over participants, mutate a collection, or determine completion.

The vote response is delivered through P2 mailbox wait, but consensus-domain extraction and validation happen in runtime-domain code.

### 7.3 Engine-owned final reduction

P3c-N1 converts collected mailbox vote responses into canonical `resolution_votes`.

When and only when the collected valid votes cover the full pending ticket `missing_participants` set, the runtime calls:

```text
ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes)
```

The engine remains responsible for final vote counts, outcome, reason, hashes and public result shape.

### 7.4 Closed-schema events

Every new P3c-N1 durable event must use a closed schema.

Extra fields must fail closed during replay.

Missing fields must fail closed during replay.

Non-string mapping keys must fail closed.

Non-strict-JSON values must fail closed.

### 7.5 Transport evidence vs domain evidence

Generic P2/actor events such as `message_received` prove that a mailbox message was consumed.

They do not, by themselves, prove that the message was a valid consensus vote response for a pending ticket.

P3c-N1 must decide whether to introduce a domain event such as `distributed_consensus_vote_received`.

Recommended first-scope decision:

```text
Use existing message_received as transport evidence.
Add distributed_consensus_vote_received as domain evidence only after successful consensus-domain validation.
Do not add distributed_consensus_vote_requested in P3c-N1, because vote request delivery is not in scope.
```

### 7.6 No silent production claim

P3c-N1 may extend local mailbox-backed pending-ticket resolution semantics, but it does not claim production distributed consensus protocol behavior.

---

## 8. Proposed P3c-N1 Runtime Module

Introduce a new module, subject to approval:

```text
synapse/runtime/consensus_mailbox_collection.py
```

The module should own:

```text
schema constants
strict JSON validation
vote response envelope validation
participant identity validation
participant-to-mailbox binding validation
pending ticket coverage validation
domain duplicate vote policy
collection projection validation
domain event validation
response hash preimage construction
conversion to resolution_votes mapping
replay validation helpers
```

The interpreter must not own these schema constants or domain validation rules.

`ConsensusEngine` must not become a mailbox collector.

---

## 9. Canonical P3c-N1 User Path

P3c-N1's canonical path is pending-ticket-only.

```text
1. Earlier P3c mechanics create a pending consensus ticket.
2. A durable run is suspended at a P2 mailbox wait boundary for the coordinator.
3. The first P3c-N1 scope uses coordinator mailbox identity "global" unless a later approved scope authorizes actor-local coordinator identity.
4. External mailbox_message resume delivers a consensus_vote_response message to actor="global".
5. P2 validates the generic mailbox envelope and injects the internal message through the existing receive path.
6. P3c-N1 runtime-domain module validates that the internal message carries a consensus_vote_response for an existing pending ticket.
7. P3c-N1 validates participant, ticket_id, proposal_id, vote state and duplicate policy.
8. P3c-N1 updates collection state only if validation succeeds.
9. If all missing_participants are covered by valid yes/no/abstain votes, P3c-N1 calls ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes).
10. If coverage is incomplete, the ticket remains pending and no distributed_consensus_ticket_resolved event is emitted.
```

P3c-N1 does not require a coordinator to send vote requests.

P3c-N1 does not require participant actors to send internal replies to `global`.

P3c-N1 treats vote response arrival as externally resumed mailbox delivery into the coordinator mailbox boundary.

---

## 10. Coordinator Identity

### 10.1 First scope

The first P3c-N1 scope uses:

```text
coordinator = "global"
```

for top-level pending-ticket mailbox collection.

The P2 external resume envelope must target:

```json
{
  "kind": "mailbox_message",
  "actor": "global",
  "message": {
    "receiver": "global"
  }
}
```

### 10.2 Future scope

A later scope may authorize actor-local coordinator identity.

That later scope must define:

- how coordinator actor identity is created;
- how coordinator mailbox identity is persisted;
- how participant responses bind to coordinator process ID;
- how replay reconstructs that coordinator identity.

### 10.3 Stop rule

If implementation requires internal actor send to `global`, or a special coordinator send path, implementation must stop until that path is explicitly approved.

---

## 11. Participant Identity and Mailbox Binding

P3c-N1 must define a binding table:

```text
participant_id -> participant_mailbox_id
```

### 11.1 Participant ID

`participant_id` is the identity used by `ConsensusEngine` for vote maps and ticket fields.

For P3c-N1, participant IDs must match the existing pending ticket's `missing_participants` entries.

### 11.2 Participant mailbox ID

`participant_mailbox_id` is the mailbox receiver key associated with the participant.

For spawned actors, this may be a process ID such as:

```text
Inbox#<suffix>
```

### 11.3 Binding source

For P3c-N1, the binding map must be derived from approved runtime state:

```text
pending ticket participants
spawned actor registry
recorded actor_spawned history
existing replay_state mailboxes/spawned_actors
```

Binding must be replay-stable.

The implementation must not recompute process IDs during replay.

### 11.4 Binding failure

The RFC requires fail-closed behavior for:

```text
unknown participant
participant not present in ticket missing_participants
participant with no mailbox binding when binding is required
ambiguous participant mailbox
duplicate participant identity
participant mailbox mismatch
response from unknown sender
response where sender/mailbox does not bind to participant
```

### 11.5 P3c-N1 simplification

Because P3c-N1 starts from externally delivered vote responses, it may not require request delivery to participant mailboxes.

However, if `participant_mailbox_id` is present in the vote response, it must match the approved binding map or fail closed.

---

## 12. Consensus Vote Response Message

### 12.1 Purpose

A vote response message carries one participant's vote for one existing pending consensus ticket.

### 12.2 P2 external envelope

P2 mailbox resume owns the outer envelope.

P3c-N1 must not modify this P2 envelope.

Expected outer shape:

```json
{
  "kind": "mailbox_message",
  "message_id": "<string>",
  "actor": "global",
  "message": {
    "sender": "<sender>",
    "receiver": "global",
    "method": "consensus_vote_response",
    "args": [
      {
        "kind": "consensus_vote_response"
      }
    ]
  }
}
```

P3c-N1 interprets the first strict-JSON argument inside `message.args` as the consensus vote response payload.

### 12.3 Inner vote response schema

P3c-N1 vote response payload:

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

### 12.4 Required fields

For P3c-N1:

```text
ticket_id is required and non-null
proposal_id is required
participant is required
coordinator is required
vote is required
response_id is required
```

### 12.5 Vote states

P3c-N1 vote responses may contain only:

```text
yes
no
abstain
```

`missing` is not allowed as a mailbox-collected terminal response for `resolve_pending_ticket`.

Missing votes remain missing by absence, not by a submitted `"missing"` vote response.

### 12.6 Invariants

```text
kind == consensus_vote_response
schema_version == consensus.vote.response.v1
ticket_id must target an existing pending consensus ticket
proposal_id must match the pending ticket proposal_id
participant must be one of ticket.missing_participants
coordinator must match the active coordinator mailbox identity
vote must be yes/no/abstain
reason must be null or string
response_id must be string
participant_mailbox must be null or match the approved participant binding map
```

---

## 13. Response Hash

P3c-N1 must define response hash without self-reference.

Invalid pattern:

```text
response_hash = hash(vote_response_message including response_hash)
```

Approved preimage draft:

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

The hash preimage must be strict JSON.

Non-finite float, non-string mapping key, host object, or unsupported JSON value must fail closed.

---

## 14. Conversion to Resolution Votes

P3c-N1 must convert validated mailbox vote responses into P3c-2-compatible `resolution_votes`.

### 14.1 Input

Validated vote response payload:

```json
{
  "participant": "Analyst",
  "vote": "yes"
}
```

### 14.2 Output

Resolution votes mapping:

```json
{
  "Analyst": "yes"
}
```

### 14.3 Conversion rule

For each accepted response:

```text
resolution_votes[participant] = vote
```

Conversion must occur in the P3c-N1 runtime-domain module, not in `ConsensusEngine`.

### 14.4 Coverage rule

Before calling:

```text
ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes)
```

P3c-N1 must verify:

```text
set(resolution_votes.keys()) == set(ticket_payload["missing_participants"])
```

If coverage is incomplete, P3c-N1 must not call `resolve_pending_ticket`.

### 14.5 Vote-state rule

P3c-N1 must verify all resolution vote values are:

```text
yes
no
abstain
```

No `"missing"` vote is allowed in `resolution_votes`.

### 14.6 Ownership

```text
P3c-N1 owns mailbox response validation and conversion.
ConsensusEngine owns final reduction.
consensus_ticket_resolution.py / P3c-2 path owns terminal resolution event validation.
```

---

## 15. Vote Collection Lifecycle

### 15.1 Core lifecycle

P3c-N1 collection lifecycle:

```text
1. Load existing pending ticket projection.
2. Derive expected missing_participants.
3. Derive or validate participant mailbox binding map.
4. Enter or resume a P2 mailbox wait boundary for coordinator "global".
5. Receive one consensus_vote_response through P2 mailbox_message.
6. Validate P2 mailbox envelope through existing P2 mechanics.
7. Validate P3c-N1 inner vote response schema.
8. Validate ticket_id/proposal_id/coordinator/participant.
9. Validate duplicate policy.
10. Compute response_hash.
11. Record accepted domain vote event if domain event policy is approved.
12. Update collection projection if projection persistence is approved.
13. If full coverage exists, call ConsensusEngine.resolve_pending_ticket.
14. If full coverage does not exist, remain non-terminal.
```

### 15.2 First scope restriction

P3c-N1 must not define vote request delivery.

P3c-N1 must not require internal actor sends to `global`.

P3c-N1 must not require user-authored durable loops.

P3c-N1 must not require `MemberAccess`.

### 15.3 Collection completion modes

Allowed completion modes:

```text
vote_accepted_non_terminal
vote_accepted_terminal_coverage_complete
duplicate_idempotent_noop
duplicate_conflict
invalid_response_rejected
timeout_non_terminal
```

No terminal resolution event may be emitted unless full missing-participant coverage exists.

---

## 16. Timeout Policy

P2 mailbox wait supports externally resolved `mailbox_timeout`.

P3c-N1 does not implement automatic wall-clock timeout.

### 16.1 Timeout injection

Timeout can only occur through externally supplied P2 mailbox timeout resume payload.

Example:

```json
{
  "kind": "mailbox_timeout",
  "actor": "global",
  "timeout": true
}
```

### 16.2 P3c-N1 timeout semantics

P3c-N1 timeout before full coverage is non-terminal.

On timeout:

```text
if full missing_participant coverage exists:
    terminal resolution may proceed
else:
    collection remains non-terminal
    no distributed_consensus_ticket_resolved event is emitted
    pending ticket remains pending
```

### 16.3 No partial terminal resolution

P3c-N1 must not reduce partial collected votes into terminal ticket resolution.

Reason:

```text
P3c-2 pending ticket resolution requires complete coverage of missing_participants.
P3c-2 resolution votes do not allow "missing".
resolve_pending_ticket fails if the result is still deferred or missing votes remain.
```

### 16.4 Future scope

A later stage may define automatic timeout, scheduler delivery, or durable timer semantics.

That is not P3c-N1.

---

## 17. Duplicate Vote Policy

P3c-N1 has two idempotency layers.

### 17.1 P2 transport layer

P2 mailbox wait has message-level normalized signal hashing and durable resume idempotency.

This protects the same resume signal from being applied twice.

### 17.2 P3 domain layer

P3c-N1 must enforce one accepted vote per `participant_id` per ticket.

### 17.3 Required behavior

| Case | Required behavior |
|---|---|
| Same P2 signal replay | Existing P2 idempotency applies. |
| Same participant, same response_hash | Idempotent no-op or replay-equivalent no mutation. |
| Same participant, same vote but different response_hash | Stop until approved policy defines semantics. |
| Same participant, different vote | Fail closed as conflicting duplicate. |
| Unknown participant | Fail closed. |
| Participant not in `missing_participants` | Fail closed. |
| Wrong `proposal_id` | Fail closed. |
| Wrong `ticket_id` | Fail closed. |
| Malformed vote | Fail closed. |

### 17.4 Recommended first policy

```text
same participant + same ticket + same proposal + same response_hash => idempotent no-op
same participant + same ticket + same proposal + different response_hash => conflict
```

This is stricter than vote-value-only de-duplication and avoids accepting contradictory reasons or replay metadata.

---

## 18. Domain Event Policy

### 18.1 Existing transport events

Existing `message_received` remains transport evidence.

It proves that the mailbox message was consumed.

It does not prove that the consumed message was a valid consensus vote response.

### 18.2 New domain event

P3c-N1 should introduce:

```text
distributed_consensus_vote_received
```

This event is emitted only after the P3c-N1 runtime-domain module validates the inner vote response and accepts it under duplicate policy.

### 18.3 No request event in P3c-N1

P3c-N1 should not introduce:

```text
distributed_consensus_vote_requested
```

because vote request delivery is not in first scope.

That event belongs to P3c-N2 or another future vote-delivery stage.

### 18.4 Vote received event schema

Draft event:

```json
{
  "type": "distributed_consensus_vote_received",
  "schema_version": "consensus.vote.received.event.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "participant": "<participant-id>",
  "participant_mailbox": "<mailbox-id-or-null>",
  "coordinator": "global",
  "vote": "yes | no | abstain",
  "reason": "<string-or-null>",
  "response_id": "<string>",
  "response_hash": "sha256:<64-hex>"
}
```

### 18.5 Event redundancy decision

This RFC intentionally chooses domain event Option B:

```text
message_received is transport evidence.
distributed_consensus_vote_received is domain evidence.
```

Rationale:

- transport replay and domain replay have different validation obligations;
- domain event carries normalized participant/vote/hash information;
- collection projection can be reconstructed without reparsing untrusted transport payloads during later stages.

---

## 19. Collection Projection

P3c-N1 should introduce an internal collection projection only if storage compatibility is approved.

Draft projection:

```json
{
  "schema_version": "consensus.vote.collection.projection.v1",
  "ticket_id": "sha256:<64-hex>",
  "proposal_id": "sha256:<64-hex>",
  "coordinator": "global",
  "missing_participants": ["<participant-id>", "..."],
  "participant_mailboxes": {
    "<participant-id>": "<mailbox-id-or-null>"
  },
  "votes_collected": {
    "<participant-id>": "yes | no | abstain"
  },
  "responses": {
    "<participant-id>": {
      "response_id": "<string>",
      "response_hash": "sha256:<64-hex>"
    }
  },
  "projection_state": "collecting | coverage_complete | conflict"
}
```

### 19.1 Replay-state gate

The current durable replay state key list is explicit.

Adding a new top-level replay_state key such as:

```text
consensus_vote_collections
```

may require approval.

Therefore implementation must stop if collection projection persistence requires artifact/replay_state schema expansion not explicitly approved.

### 19.2 First-scope storage options

P3c-N1 approval must choose one:

| Option | Description |
|---|---|
| A | Store collection projection inside existing `consensus_tickets[ticket_id]` under approved closed fields. |
| B | Store collection projection under new `consensus_vote_collections`, requiring replay_state approval. |
| C | Do not persist intermediate projection; only terminal full-coverage resolution is committed. |

Recommended for first implementation:

```text
Option A if closed-schema extension to existing consensus_tickets projection is approved;
otherwise Option C.
```

Option B requires explicit replay_state schema approval.

---

## 20. Interaction with P3c-2 Ticket Resolution

P3c-N1 targets existing pending tickets.

### 20.1 Non-terminal collection

If one valid vote response is accepted but not all `missing_participants` are covered:

```text
no distributed_consensus_ticket_resolved event
pending ticket remains pending
collection state is updated only if projection persistence is approved
```

### 20.2 Terminal full coverage

If valid responses cover all `missing_participants`:

```text
resolution_votes = {participant: vote for accepted responses}
ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes)
emit existing distributed_consensus_ticket_resolved event
mark ticket projection resolved through existing P3c-2 rules
```

### 20.3 Conflict

If a conflicting duplicate response appears:

```text
fail closed before projection mutation
do not emit terminal resolution event
do not mutate ticket projection
```

---

## 21. Durable Surface Decision

P3c-N1 chooses Path A:

```text
No durable DistributedConsensusStmt expansion in P3c-N1.
```

P3c-N1 operates only on existing pending tickets.

This avoids changing durable classification for `DistributedConsensusStmt`.

A later P3c-N2 or P3d-adjacent stage may propose constrained durable `DistributedConsensusStmt` support.

That future stage must separately define:

```text
fresh proposal document construction
vote request delivery
coordinator send path
participant mailbox binding at creation time
initial mailbox-backed collection
parser/AST impact if any
durable validator impact
```

---

## 22. Replay Semantics

Replay must be deterministic and side-effect-free.

During replay P3c-N1 must:

```text
not send vote request messages
not poll live mailboxes
not call actor vote methods
not call LLM providers
not mutate mailboxes from replayed vote delivery
consume recorded message_received transport events only through existing replay path
consume recorded distributed_consensus_vote_received domain events in order
validate event schema and response_hash
reconstruct collection projection only from recorded canonical domain data
call ConsensusEngine.resolve_pending_ticket only when recorded canonical data proves full coverage
fail closed on mismatch before projection mutation
```

### 22.1 Ordering rule

Collection domain events are replayed in recorded FIFO order.

Final `votes_hash` remains engine-owned and participant-order canonical.

This means:

```text
history order is replay-sensitive
final vote hash is participant-order canonical
```

These two facts must not be conflated.

---

## 23. Idempotency

P3c-N1 idempotency combines:

```text
P2 resume idempotency
P3 participant-level duplicate policy
collection projection integrity
P3c-2 terminal resolution conflict handling
```

### 23.1 Same mailbox resume

If the same P2 mailbox resume signal is replayed against the same active suspension, P2 should return stored semantics through existing idempotency.

### 23.2 Same participant duplicate

Recommended first policy:

```text
same participant + same ticket_id + same proposal_id + same response_hash => idempotent no-op
same participant + same ticket_id + same proposal_id + different response_hash => conflict
```

### 23.3 Same participant same vote but different reason

This is a conflict unless separately approved.

Reason: same vote value with different reason may still represent a different response preimage.

---

## 24. Security and Integrity

P3c-N1 must fail closed on:

```text
non-object vote response
unknown kind
wrong schema_version
non-string mapping key
non-strict-JSON value
wrong ticket_id
wrong proposal_id
wrong coordinator
unknown participant
participant not in missing_participants
participant mailbox mismatch
vote outside yes/no/abstain
duplicate conflicting vote
response for terminal collection
response for resolved ticket
extra event fields
missing event fields
response_hash mismatch
replay order mismatch
partial timeout terminal resolution attempt
```

No consensus mailbox message may be trusted solely because it arrived through P2 mailbox wait.

P2 validates the generic mailbox envelope. P3c-N1 validates the consensus-domain content.

---

## 25. Implementation Allowlist Draft

Implementation is not authorized by this RFC draft.

If approved later, P3c-N1 implementation PR may touch only an explicit allowlist.

Recommended P3c-N1 allowlist:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_mailbox_collection_p3cn.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Conditional only with explicit approval:

```text
synapse/application.py
```

Allowed reasons for `application.py` would be limited to:

```text
approved replay_state key handling
approved durable error/status mapping
approved artifact validation for new projection
```

Not in first P3c-N1 allowlist unless separately justified:

```text
synapse/runtime/consensus_engine.py
```

Reason: P3c-N1 pending-ticket mode should be able to use existing `ConsensusEngine.resolve_pending_ticket()`.

Not allowed without separate approval:

```text
synapse/ast.py
synapse/parser.py
synapse/lexer.py
network code
daemon code
timer/scheduler code
persistent inbox code
workflow files
examples
```

---

## 26. Stop Gates

Implementation must stop if any of the following becomes necessary:

```text
P3CN1_RFC_NOT_APPROVED
P3CN1_APPROVAL_RECORD_MISSING
P3CN1_PENDING_TICKET_ONLY_SCOPE_NOT_ACCEPTED
CANONICAL_USER_PATH_UNDEFINED
FRESH_DISTRIBUTED_CONSENSUS_DURABLE_EXECUTION_REQUIRED
PROPOSAL_DOCUMENT_REQUIRED_FOR_P3CN1
VOTE_REQUEST_DELIVERY_REQUIRED
COORDINATOR_SEND_PATH_UNDEFINED
PARTICIPANT_TO_MAILBOX_BINDING_UNDEFINED
COORDINATOR_IDENTITY_UNDEFINED
VOTE_RESPONSE_MESSAGE_SCHEMA_UNDEFINED
RESPONSE_HASH_PREIMAGE_UNDEFINED
MAILBOX_VOTES_TO_RESOLUTION_VOTES_CONVERSION_UNDEFINED
DUPLICATE_VOTE_POLICY_UNDEFINED
TIMEOUT_INJECTION_MECHANISM_UNDEFINED
PARTIAL_TIMEOUT_TERMINAL_RESOLUTION_REQUESTED
DOMAIN_EVENT_POLICY_UNDEFINED
VOTE_EVENTS_REDUNDANCY_UNRESOLVED
VOTE_COLLECTION_PROJECTION_UNDEFINED
REPLAY_STATE_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
PARTICIPANT_MAILBOX_REPLAY_BINDING_UNDEFINED
DURABLE_DSL_LOOP_REQUIRED
GENERAL_MEMBER_ACCESS_REQUIRED
NETWORK_OR_DAEMON_TRANSPORT_REQUIRED
DURABLE_TIMER_OR_SCHEDULER_REQUIRED
PERSISTENT_DURABLE_INBOX_REQUIRED
PARSER_AST_LEXER_CHANGE_REQUIRED
PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
CONSENSUS_ENGINE_REPLACEMENT_REQUIRED
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
P2_ARTIFACT_SCHEMA_BUMP_REQUIRED_WITHOUT_APPROVAL
```

---

## 27. Test Plan

A later implementation PR must include tests for the approved P3c-N1 scope.

### 27.1 Vote response schema tests

```text
valid vote response
invalid kind
invalid schema_version
missing ticket_id
null ticket_id rejected
wrong proposal_id rejected
wrong coordinator rejected
unknown participant rejected
participant not in missing_participants rejected
invalid vote state rejected
missing vote rejected
extra field rejected
non-string mapping key rejected
non-strict-JSON value rejected
response_hash mismatch rejected
```

### 27.2 Participant binding tests

```text
spawned actor participant binds to process mailbox from recorded state
binding uses replayed process_id, not recomputed uuid
participant mailbox mismatch rejected
ambiguous participant binding rejected
unknown participant binding rejected
response from unbound participant rejected
```

### 27.3 Conversion tests

```text
single valid response converts to resolution_votes mapping
multiple valid responses convert to complete resolution_votes mapping
conversion preserves participant IDs used by ticket missing_participants
conversion rejects missing vote state
conversion rejects duplicate conflicting vote
conversion rejects wrong ticket_id
conversion rejects wrong proposal_id
```

### 27.4 Collection lifecycle tests

```text
accepted vote without full coverage is non-terminal
accepted vote with full coverage calls resolve_pending_ticket
partial collection does not emit distributed_consensus_ticket_resolved
full collection emits existing distributed_consensus_ticket_resolved
conflicting duplicate fails before projection mutation
same response_hash duplicate is idempotent no-op
```

### 27.5 Timeout tests

```text
mailbox_timeout before full coverage is non-terminal
mailbox_timeout does not call resolve_pending_ticket with partial votes
mailbox_timeout after full coverage may allow terminal resolution if approved
automatic wall-clock timeout is not used
scheduler is not required
```

### 27.6 Domain event tests

```text
message_received transport event exists for consumed mailbox message
distributed_consensus_vote_received emitted only after domain validation
malformed mailbox message does not emit domain vote event
domain event closed schema rejects extra fields on replay
domain event response_hash mismatch fails closed
```

### 27.7 Replay tests

```text
replay does not send vote requests
replay does not poll live mailbox
replay does not call actor vote methods
replay consumes message_received through existing path
replay consumes distributed_consensus_vote_received in recorded order
replay reconstructs collection projection from domain events
replay mismatch fails closed before projection mutation
replay final result hash matches original after full coverage
```

### 27.8 Regression tests

```text
P2 mailbox wait regression suite
P2 durable run/resume regression selection
P3a regression suite
P3b actor-method vote regression suite
P3c-0 replay regression suite
P3c-1 ticket creation/replay regression suite
P3c-2 ticket resolution regression suite
```

---

## 28. Evidence Requirements

A later implementation PR must record:

```text
implementation base SHA
implementation final head SHA
changed file list
test command list
exact test counts
known failures
new failures
scope non-claims
review verdict
```

After merge, a separate docs/evidence patch must update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

The evidence update must keep Distributed consensus in Partial unless an explicitly approved production protocol stage has also been completed.

---

## 29. Capability Impact

If P3c-N1 is implemented and evidence-closed, capability may move from:

```text
Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P2 mailbox wait prerequisite closed
```

to:

```text
Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution closed; P3c-N1 local mailbox-backed pending-ticket vote response collection closed
```

The capability statement must include limitations:

```text
pending-ticket-only
no fresh durable DistributedConsensusStmt execution
no vote request delivery
no automatic timeout
no network/daemon
no persistent inbox
no durable scheduler
no production distributed consensus protocol claim
```

Production distributed consensus remains not claimed.

---

## 30. Approval Record

Approval status: `DRAFT / NOT APPROVED`

Product Owner sign-off: `PENDING`

Approved implementation scope: `NONE`

Implementation PR allowed: `NO`

This RFC becomes implementation-authorizing only after a separate approval patch sets:

```text
Approval status: APPROVED
Implementation status: AUTHORIZED FOR P3c-N1 IMPLEMENTATION
Approved RFC content SHA: <sha>
Implementation base SHA: <merge-sha-of-approval-pr>
Approved file allowlist: <explicit list>
Approved stop-gates: <explicit list>
```

Until then:

```text
P3c-N1 implementation remains blocked.
```

---

## 31. Final RFC Statement

P3c-N1 is not a replacement for `ConsensusEngine`.

P3c-N1 is the missing local mailbox-backed vote response collection layer for existing pending consensus tickets.

P3c-N1 accepts validated mailbox-delivered vote responses, binds them to an existing pending ticket, enforces participant-level duplicate policy, converts accepted responses into P3c-2-compatible `resolution_votes`, and calls existing engine-owned terminal resolution only when full missing-participant coverage exists.

P3c-N1 does not implement fresh `DistributedConsensusStmt` mailbox-backed execution, vote request delivery, automatic timeout, network delivery, daemon delivery, durable timers, persistent durable inbox, parser/AST/lexer changes, or production distributed consensus protocol behavior.

Recommended next implementation slice after approval:

```text
P3c-N1 — Local mailbox-backed vote response collection for existing pending consensus tickets
```

Deferred later slice:

```text
P3c-N2 — Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection
```
