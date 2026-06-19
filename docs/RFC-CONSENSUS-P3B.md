# RFC-CONSENSUS-P3B — Runtime / Actor Vote Integration Contract

**Status:** APPROVED  
**Stage:** P3b RFC  
**Implementation status:** AUTHORIZED FOR P3B-0 IMPLEMENTATION  
**Repository mutation:** DOCUMENTATION APPROVAL ONLY  
**Primary target:** Define the first runtime-backed VoteSource contract after P3a  
**Primary implementation stage:** P3b-0 — Explicitly Enabled Synchronous Local Actor Method VoteSource  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Network / daemon vote integration in P3b-0:** NOT ALLOWED  
**Mailbox-backed vote collection in P3b-0:** NOT ALLOWED  
**Durable / replay closure in P3b-0:** NOT ALLOWED  
**Live LLM vote production in P3b-0:** NOT ALLOWED  
**Parser / AST / lexer expansion in P3b-0:** NOT ALLOWED  
**Durable allowlist expansion in P3b-0:** NOT ALLOWED  
**Default source policy:** Preserve P3a `NullVoteSource` unless actor-method voting is explicitly enabled before evaluation  
**Capability target after successful evidence closure:** `Partial — P3b local runtime vote source verified`  
**Capability target explicitly not claimed:** `Production`

---

## Approval Record

**Approval status:** APPROVED  
**Approval type:** Product / architecture approval gate  
**Approved implementation stage:** P3b-0 only  
**Approved implementation status:** AUTHORIZED FOR P3B-0 IMPLEMENTATION  
**Approved document:** `docs/RFC-CONSENSUS-P3B.md`  
**Approved branch for this gate:** `Approval-Gate`  
**Prior stage dependency:** P3a Semantic Consensus Core implemented and evidence-closed on `main`  
**Capability before implementation:** `Partial — P3a semantic core verified`  
**Capability after implementation:** must remain non-Production unless a later evidence closure justifies a narrower `Partial` update  

### Approved P3b-0 scope

The following implementation scope is approved:

```text
explicitly enabled synchronous local ActorMethodVoteSource
read-only VoteSource registry
deep-frozen JSON-compatible proposal view
vote-query mode
side-effect guard
participant fault isolation
contract violation fail-closed
preservation of P3a semantic rules
preservation of P3a result/event hash contracts
preservation of P3a validation behavior
```

P3b-0 may connect eligible local actor or agent methods to the existing P3a `VoteSource` seam.

The approved local method name is:

```text
consensus_vote(proposal)
```

Actor-method voting is explicit opt-in only. P3b-0 must preserve the P3a default behavior unless actor-method voting is explicitly enabled before evaluation.

### Deferred and not authorized in P3b-0

The following remain deferred and are not authorized by this approval:

```text
mailbox-backed voting
daemon-backed voting
network voting
DurablePromise voting
await/suspend vote collection
live LLM voting
durable replay closure
stateful consensus ticket lifecycle
parser/AST/lexer expansion
new .syn vote syntax
strategy grammar cleanup
production distributed consensus protocol claims
Raft/Paxos/Tendermint/PBFT implementation claims
```

### Approval invariant

The implementation must preserve the central P3b-0 invariant:

```text
ordinary participant failure becomes missing;
deterministic contract violation fails the whole evaluation.
```

### Approval consequence

After this approval gate is merged, a separate implementation PR may implement P3b-0 within the authorized scope.

This approval does not authorize implementation of P3c, P3d, mailbox voting, daemon voting, network voting, durable replay, or production distributed consensus protocol behavior.

---

## 0. Purpose of this RFC

This RFC defines the P3b-0 contract for connecting local runtime actors and agents to the existing P3a deterministic semantic consensus core.

P3a already replaced the prior distributed consensus facade with deterministic content-sensitive consensus semantics. P3a introduced the semantic reducer, strict validation, explicit vote states, deterministic strategy semantics, canonical result hashes, and the canonical `distributed_consensus_decided` event.

P3b must not reopen, dilute, or bypass P3a semantics. P3b may only define how runtime-owned vote providers supply explicit deterministic votes into the already-approved P3a engine.

This RFC deliberately defines only the first P3b substage:

```text
P3b-0 — explicitly enabled synchronous local actor/agent method VoteSource.
```

This means:

```text
local participant object -> deterministic consensus_vote(proposal) method -> VoteRecord -> P3a ConsensusEngine
```

This RFC does not authorize mailbox vote collection, daemon vote packets, network transport, durable promise voting, replay consumption, signal injection, live LLM vote generation, or new grammar.

---

## 1. Executive Summary

P3b-0 exists because P3a currently has a deterministic `VoteSource` seam, but the only safe way to connect runtime/actor behavior without crossing into durable/replay territory is to use a synchronous local method query.

The approved P3b-0 direction is:

```text
Explicitly enabled ActorMethodVoteSource
+ read-only VoteSource registry
+ deep-frozen proposal view
+ vote-query mode
+ side-effect guard
+ participant fault isolation
+ contract violation fail-closed
+ preservation of P3a semantics
```

P3b-0 answers this question:

```text
Given a valid P3a consensus request and a set of local runtime participants, how may the interpreter ask eligible local participants for deterministic votes without introducing side effects, replay obligations, network behavior, mailbox waiting, durable suspension, or hidden default voting?
```

P3b-0 does not answer:

```text
How are votes collected over time from actor mailboxes, remote nodes, daemon packets, durable promises, replay logs, external schedulers, user-facing vote syntax, or live LLM providers?
```

Those are deferred to future approved stages:

```text
P3b-1 / P3b-N — possible later actor/mailbox/daemon integration details
P3c — durable / replay closure
P3d — LLM-assisted voting
future RFC — parser / grammar cleanup, if ever approved
```

The core architectural decision is:

```text
ordinary participant fault -> participant vote becomes missing
deterministic contract violation -> whole consensus evaluation fails closed
```

This distinction is mandatory.

It allows fault isolation for a participant whose vote method is absent, malformed, or throws an ordinary runtime exception, while still protecting the deterministic consensus boundary from side effects such as `send`, `receive`, `await`, `suspend`, `llm_call`, mailbox mutation, registry mutation, proposal mutation, promise creation, outbound packet emission, or history mutation.

---

## 2. Relationship to Existing P3 RFC and P3a Evidence

### 2.1 P3a status before P3b

P3a is already closed for the approved local semantic consensus core scope.

P3a delivered:

```text
deterministic ConsensusEngine
interpreter adapter
explicit VoteSource seam
yes/no/abstain/missing vote states
MajorityVote / UnanimousVote / NoVetoVote
strict participant/vote/quorum/timeout/strategy validation
proposal_id
votes_hash
result_hash
distributed_consensus_decided event
no implicit current actor voting
no facade auto-yes behavior
no stateful ticket lifecycle
no actor_log mutation
no consensus_tickets mutation
no live LLM
no daemon/network voting
no durable replay closure
```

Capability status after P3a S1/S2 evidence closure:

```text
Partial — P3a semantic core verified
```

P3a must remain the semantic foundation. P3b-0 is not allowed to redefine consensus mathematics.

### 2.2 P3b relationship to P3a

P3b-0 is a source-integration layer.

It may provide vote records to the existing P3a engine.

It must not alter these P3a-owned contracts:

```text
participant identity validation
duplicate participant rejection
vote state validation
unknown participant vote rejection
quorum validation
timeout validation
strategy selection
strategy semantics
outcome semantics
reason semantics
proposal_id semantics
votes_hash semantics
result_hash semantics
event schema
binding result schema
fail-closed validation behavior
```

If implementation requires weakening P3a rules, P3b-0 is blocked.

### 2.3 P3b relationship to P3c

P3c owns durable/replay closure.

The following belong to P3c or a later explicitly approved durable stage, not to P3b-0:

```text
durable vote persistence
canonical event consumption during replay
replay cursor behavior for consensus votes
fail-closed replay matching
legacy event handling
durable ticket lifecycle
stateful pending vote lifecycle
artifact evidence
signal-injected vote resolution
promise-backed vote completion
mailbox wait / timeout lifecycle
```

P3b-0 must not infer P3c behavior from P3a or from this RFC.

### 2.4 P3b relationship to P3d

P3d owns LLM-assisted voting.

P3b-0 must not call live LLM providers.

P3b-0 must not define LLM vote prompts, model output schemas, refusal handling, provider timeout handling, cost controls, or replay rules for LLM votes.

If actor vote methods attempt to invoke an LLM, this is a deterministic contract violation and must fail closed.

---

## 3. Code Audit Findings

This section records the current runtime facts that motivate the P3b-0 boundary.

### 3.1 Agent method binding exists

The interpreter creates `AgentRuntime` instances from agent definitions.

For each agent method, the interpreter registers the method in the agent environment using function binding. This gives the runtime a local synchronous method execution path for agent methods.

This is the only P3b-0-approved runtime vote path.

Consequences:

```text
A local agent may expose consensus_vote(proposal).
The interpreter can call the method in-process.
The method can return a vote value during the same evaluation.
No mailbox is required.
No suspension is required.
No daemon is required.
No network is required.
No durable promise is required.
```

### 3.2 Mailbox send is not pure read

The current actor `send_message` path is stateful.

In LIVE mode it may:

```text
append to mailboxes
append to actor_log
append to execution_history
emit runtime events
append outbound_packets for nonlocal actors
```

In REPLAY mode, delivery is replay-gated and short-circuits without mutating mailbox or re-emitting network packets.

Consequences:

```text
Mailbox-backed voting is not pure.
Mailbox-backed voting requires event/replay semantics.
Mailbox-backed voting may require durable evidence.
Mailbox-backed voting is not P3b-0.
```

### 3.3 Receive is history/replay-bound

The current receive path consumes `message_received` and `receive_timeout` history entries when replaying.

When not replaying, it reads and mutates mailbox state and may emit timeout events.

The async receive path can suspend when no message is available.

Consequences:

```text
receive-based vote collection implies replay and/or suspension semantics.
receive-based vote collection is P3c territory.
P3b-0 must not use receive for votes.
```

### 3.4 DurablePromise belongs to durable completion

`DurablePromise` is a serializable placeholder for external durable completion.

The synchronous interpreter rejects `await` and `suspend` as durable suspension points and directs execution to `interpret_async()`.

Consequences:

```text
promise-backed vote completion is not P3b-0.
await/suspend during vote collection is forbidden.
signal-injected vote completion belongs to P3c.
```

### 3.5 DurableActorRef is identity metadata, not proof of executable method environment

`DurableActorRef` contains:

```text
actor_name
process_id
node
```

The semantic participant identity used by P3a is `actor_name`, not `process_id`.

The spawned actor registry may contain process metadata such as process ID, actor name, node, model, and status. That metadata does not by itself prove that an executable local `AgentRuntime` method environment exists.

Consequences:

```text
P3b-0 must not call vote methods by process_id.
P3b-0 must not assume spawned_actors metadata is an executable actor object.
P3b-0 may only call actor methods through an explicit deterministic local mapping to AgentRuntime method environment.
If such mapping is absent, vote is missing.
```

### 3.6 No approved daemon vote path exists

There is no approved runtime path where daemon vote packets are consumed by interpreter consensus evaluation.

Consequences:

```text
P3b-0 must not introduce daemon voting.
Daemon voting requires its own approved scope.
```

---

## 4. Open-Source Alignment Notes

This section is non-normative. It explains why the P3b-0 boundary matches well-known deterministic execution patterns.

### 4.1 Temporal-style workflow determinism

Temporal workflow definitions require deterministic workflow code because replay re-executes workflow logic and compares emitted commands with recorded history.

External interactions such as API calls, database queries, LLM/AI invocations, and other side-effecting operations are placed outside workflow replay paths in Activities.

P3b-0 follows the same split:

```text
consensus reducer / vote query mode = deterministic core
mailbox / network / LLM / durable promise = external or durable layer
```

### 4.2 Azure Durable Functions-style orchestrator constraints

Durable orchestrators use event sourcing and replay. Because orchestrator code may replay multiple times, it must use deterministic APIs and avoid direct nondeterministic calls.

P3b-0 follows the same rule:

```text
no direct mailbox wait
no direct network
no ordinary async await
no random/wall-clock/LLM in vote method
```

### 4.3 DBOS-style deterministic workflow / explicit steps

DBOS workflow code is expected to call durable steps in a deterministic order. External effects are modeled as steps rather than implicit side effects inside the deterministic workflow body.

P3b-0 applies the equivalent discipline:

```text
actor_method vote = synchronous deterministic local query
mailbox / durable promise / network = future explicit durable step, not P3b-0
```

### 4.4 Restate-style durable execution journal

Restate records side-effecting operations in a journal and resumes execution from journaled state.

P3b-0 does not yet define such a journaled vote path. Therefore P3b-0 must avoid side-effecting vote collection.

### 4.5 Raft-style decomposition discipline

Raft is not the protocol implemented here. P3b-0 must not claim Raft/Paxos/Tendermint/PBFT semantics.

However, Raft's decomposition discipline is relevant: leader election, log replication, and safety are separated to reduce state-space complexity.

P3 follows a similar engineering discipline:

```text
P3a = deterministic semantic reducer
P3b-0 = local runtime vote source
P3c = durable/replay closure
P3d = LLM-assisted voting
future stages = network/daemon/protocol behavior, if approved
```

---

## 5. Normative Language

The following terms are normative in this RFC.

- **MUST** means required.
- **MUST NOT** means forbidden.
- **SHOULD** means strongly expected unless an approved documented reason exists.
- **MAY** means permitted only inside the stated boundary.
- **P3a** means the approved deterministic semantic consensus core.
- **P3b** means runtime / actor vote integration.
- **P3b-0** means the first P3b substage: explicitly enabled synchronous local actor/agent method VoteSource.
- **P3c** means durable / replay closure.
- **P3d** means LLM-assisted voting.
- **VoteSource** means a deterministic source that supplies participant votes to the P3a consensus engine.
- **ActorMethodVoteSource** means the P3b-0 source that queries eligible local actor/agent methods synchronously.
- **Vote Query Mode** means an internal interpreter mode active while actor vote methods are being called; it rejects forbidden side-effecting operations.
- **Participant Fault** means a participant-local failure that does not violate the deterministic contract and can be represented as a `missing` vote.
- **Contract Violation** means an operation or mutation that threatens deterministic consensus evaluation and therefore fails the entire consensus evaluation.
- **Proposal View** means a read-only JSON-compatible object passed to `consensus_vote(proposal)`.
- **Deep-frozen** means recursively immutable, not merely shallow read-only.
- **Executable local method environment** means a local `AgentRuntime` method environment that can safely execute `consensus_vote(proposal)` synchronously without mailbox/network/durable resolution.
- **Semantic participant identity** means the identity used by P3a in participant normalization and hashing.
- **Advisory source label** means metadata describing where a vote came from; it does not change semantic vote value unless explicitly promoted by a future RFC.
- **Side-effect guard** means before/after runtime state observation used to detect forbidden mutations during vote collection.
- **Registry version** means a monotonic advisory marker used to detect VoteSource registry mutation during evaluation.

---

## 6. Detailed Terms

### 6.1 ConsensusEngine

`ConsensusEngine` is the deterministic semantic component introduced by P3a.

It owns:

```text
participant validation
vote validation
quorum validation
timeout validation
strategy validation
strategy evaluation
outcome selection
reason selection
proposal_id generation
votes_hash generation
result_hash generation
canonical result construction
canonical event payload construction
```

P3b-0 must not move these semantics out of the engine.

### 6.2 Interpreter Adapter

The interpreter adapter is the runtime bridge between AST evaluation and the consensus engine.

In P3b-0, the adapter may additionally:

```text
evaluate participants while preserving raw runtime values
build a proposal view before vote collection
select an approved VoteSource
activate vote-query mode
call ActorMethodVoteSource
pass collected VoteRecords into ConsensusEngine
bind result
append distributed_consensus_decided for valid operational outcomes
```

The adapter must not become the owner of consensus mathematics.

### 6.3 VoteSource Registry

The VoteSource registry maps source names or runtime configuration entries to approved source providers.

P3b-0 registry entries may include:

```text
null
explicit
actor_method
```

The registry is configuration-only.

The registry must be read-only during evaluation.

Dynamic registry mutation during `evaluate_distributed_consensus()` is forbidden.

### 6.4 ActorMethodVoteSource

`ActorMethodVoteSource` is the P3b-0 runtime source that queries local actor/agent methods.

It calls:

```text
consensus_vote(proposal)
```

on eligible local participant runtime objects.

It returns VoteRecord-compatible values.

It does not make the consensus decision.

It does not mutate runtime state.

It does not emit events.

It does not allocate tickets.

It does not touch durable artifacts.

It does not use mailbox/daemon/network/LLM/promise paths.

### 6.5 Participant Fault

A participant fault is local to one participant and can be safely represented as `missing`.

Participant faults include:

```text
no consensus_vote method
participant actor not local
no executable local method environment
wrong method signature
ordinary runtime exception before side effects
malformed vote return
unsupported vote return
non-JSON-compatible advisory return
```

Participant faults do not abort the whole consensus.

### 6.6 Contract Violation

A contract violation means the vote method or source attempted to cross the deterministic boundary.

Contract violations include:

```text
send
receive
spawn
await
suspend
migrate
llm_call
network call
daemon packet use
mailbox mutation
actor_log mutation
execution_history mutation
outbound_packets mutation
consensus_tickets mutation
promise creation
promise resolution
VoteSource registry mutation during evaluation
proposal_view mutation
runtime event emission
durable artifact mutation
```

Contract violations fail the entire consensus evaluation.

### 6.7 Missing Vote

A missing vote is a first-class P3a vote state.

It means:

```text
the participant did not provide a valid yes/no/abstain vote through the current approved source
```

It does not mean:

```text
the participant voted no
the participant abstained
the participant failed validation structurally
the participant was silently ignored
```

### 6.8 Deep-Frozen Proposal View

A deep-frozen proposal view is a recursively immutable JSON-compatible object.

It must not expose:

```text
interpreter instance
Environment
AgentRuntime object
DurableActorRef object
mailbox object
actor_log object
execution_history object
callables
host object references
mutable nested dict/list
```

A shallow read-only wrapper is insufficient if nested values remain mutable.

---

## 7. Scope

### 7.1 In Scope for P3b-0

P3b-0 includes:

1. Defining `ActorMethodVoteSource`.
2. Defining the `consensus_vote(proposal)` method contract.
3. Defining the deep-frozen proposal view shape.
4. Defining participant fault handling.
5. Defining contract violation handling.
6. Defining vote-query mode.
7. Defining side-effect guard.
8. Defining VoteSource registry immutability.
9. Defining explicit opt-in default policy.
10. Defining local actor/agent method resolution.
11. Defining missing behavior for nonlocal/unresolved participants.
12. Defining source labels for actor-method voting.
13. Preserving P3a engine semantics.
14. Preserving P3a result/event schema.
15. Adding tests for actor-method votes.
16. Adding tests for side-effect rejection.
17. Adding tests for proposal view immutability.
18. Adding tests for registry immutability.
19. Adding tests proving P3a regression safety.
20. Adding PR-body evidence.

### 7.2 Out of Scope for P3b-0

P3b-0 excludes:

1. Parser extension.
2. AST extension.
3. Lexer extension.
4. New `.syn` vote syntax.
5. New `strategy <name>` syntax.
6. Grammar migration from `policy` to `strategy`.
7. Mailbox-backed vote collection.
8. `send_message`-based vote request delivery.
9. `receive`-based vote response collection.
10. Actor mailbox waiting.
11. Async suspension.
12. `DurablePromise` vote collection.
13. Signal-injected vote resolution.
14. Durable vote persistence.
15. Durable allowlist expansion.
16. Replay consumption of vote events.
17. Daemon packet voting.
18. Network delivery.
19. Runtime transport protocol.
20. Live LLM vote production.
21. LLM prompt template versioning.
22. LLM model output schema.
23. Stateful consensus ticket lifecycle.
24. Production distributed consensus protocol claims.
25. Raft/Paxos/Tendermint/PBFT implementation.
26. Byzantine behavior modeling.
27. Signature verification.
28. Leader election.
29. View-change protocol.
30. Dynamic VoteSource registration during evaluation.
31. Using `process_id` as semantic identity.
32. Inferring executable actor environment from metadata alone.

---

## 8. Normative Decisions

### P3B-D1 — P3a engine remains the semantic authority

P3b-0 MUST preserve the P3a consensus engine as the semantic authority.

P3b-0 MUST NOT reimplement consensus strategy semantics in the interpreter adapter or in `ActorMethodVoteSource`.

P3b-0 MUST NOT bypass engine validation.

P3b-0 MAY refactor internal engine APIs only if externally observable P3a semantics remain unchanged.

### P3B-D2 — Vote collection is a VoteSource concern

Runtime vote collection belongs to a VoteSource implementation.

The VoteSource may inspect local runtime participant values.

The VoteSource may call eligible local actor/agent methods.

The VoteSource must return VoteRecord-compatible data.

The VoteSource must not decide the final outcome.

### P3B-D3 — P3b-0 is synchronous only

P3b-0 vote collection MUST complete during the current interpreter evaluation.

P3b-0 MUST NOT:

```text
wait
suspend
poll
send actor messages
receive actor messages
create DurablePromise
await DurablePromise
emit network packets
call daemon transport
call live LLM providers
allocate consensus tickets
mutate consensus_tickets
mutate actor_log
append per-vote runtime events
```

### P3B-D4 — Approved vote method name

The default P3b-0 local actor vote method is:

```text
consensus_vote
```

A participant may vote by exposing this method.

The method signature is:

```text
consensus_vote(proposal)
```

The method receives exactly one proposal argument.

P3b-0 MUST NOT introduce new `.syn` syntax to configure this method name.

A future amendment may introduce explicit source/method configuration.

### P3B-D5 — Proposal view shape

The actor vote method receives a JSON-compatible proposal view.

The proposal view contains:

```text
schema_version
proposal_id
topic
participants
strategy
policy
quorum
timeout
statement_identity
```

The proposal view MUST NOT include:

```text
votes
vote_counts
votes_hash
result_hash
outcome
committed
deferred
ticket_id
runtime object references
callables
interpreter instance
environment object
actor mailbox
execution_history
actor_log
consensus_tickets
outbound_packets
promises
```

### P3B-D5.1 — Deep-frozen proposal view

The proposal view passed to an actor vote method MUST be a deep-frozen JSON-compatible structure.

A shallow read-only wrapper is insufficient if nested dictionaries or arrays remain mutable.

The vote method MUST NOT be able to mutate:

```text
schema_version
proposal_id
topic
participants
strategy
policy
quorum
timeout
statement_identity
```

If implementation detects proposal view mutation, or cannot guarantee immutability, P3b-0 MUST fail closed with:

```text
invalid_request: proposal_view_mutated
```

### P3B-D5.2 — Proposal view construction pattern

The implementation SHOULD construct the proposal view from already validated semantic/advisory data.

The implementation SHOULD ensure host-object exclusion before freezing.

An acceptable pattern is:

```text
validate JSON-compatible proposal view
round-trip through JSON-compatible representation if needed
recursively convert dict to read-only mapping
recursively convert list to tuple
retain only scalar JSON-compatible leaf values
```

The implementation MUST NOT pass mutable interpreter-owned objects to actor vote methods.

### P3B-D6 — Proposal identity must be available before vote collection

P3b-0 may require proposal identity before vote collection, because actor methods receive `proposal_id`.

The consensus engine or adapter may be refactored into a proposal-first flow:

```text
validate request
build proposal preimage
build proposal_id
construct proposal view
collect votes
evaluate outcome
construct result/event
```

This refactor MUST NOT change the P3a proposal identity contract.

The proposal identity MUST NOT include:

```text
votes
vote counts
coordinator runtime counters
wall-clock time
randomness
process_id
mailbox state
history cursor
registry version
actor object identity
```

### P3B-D7 — Supported participant runtime values

P3b-0 supports actor-method voting for local `AgentRuntime` participants.

A participant represented by a string may vote only if the adapter has an explicit deterministic local mapping from that string identity to an executable local `AgentRuntime` method environment.

A participant represented by `DurableActorRef` may vote only if the adapter has an explicit deterministic local mapping from `actor_name` to an executable local `AgentRuntime` method environment.

If no approved executable local method environment exists, the participant vote is `missing`.

### P3B-D7.1 — DurableActorRef local resolution

P3b-0 MUST NOT use `DurableActorRef.process_id` as semantic participant identity.

For P3b-0, a `DurableActorRef` participant may produce an actor-method vote only when the adapter has an explicit deterministic local mapping from `actor_name` to an executable local `AgentRuntime` method environment.

The spawned actor registry alone MUST NOT be treated as proof that an executable local method environment exists.

If no approved local method environment exists, the participant vote MUST be:

```text
missing
```

with source label:

```text
actor_not_local
```

P3b-0 MUST NOT send mailbox messages, network packets, daemon packets, or promise requests to resolve the vote.

### P3B-D8 — Participant identity remains P3a-compatible

Participant identity MUST continue to use the P3a identity contract:

```text
AgentRuntime.name
DurableActorRef.actor_name
string participant identity
```

`DurableActorRef.process_id` MUST NOT become a semantic participant identity in P3b-0.

If an implementation requires `process_id` as semantic identity, it MUST stop.

### P3B-D9 — Vote method return forms

A vote method may return one of the approved vote states:

```text
yes
no
abstain
missing
```

A vote method may also return an object:

```text
{
  "vote": "yes" | "no" | "abstain" | "missing",
  "reason": "<optional string>"
}
```

The canonical vote map stores only the vote state.

`reason` is advisory.

`reason` MUST NOT enter `votes_hash` or `result_hash` unless a later RFC explicitly promotes it.

### P3B-D10 — Invalid vote method result is participant fault

If a vote method returns unsupported data, malformed object, unsupported vote state, or non-JSON-compatible advisory data, this is a participant fault.

The participant vote becomes:

```text
missing
```

with source label:

```text
actor_method_invalid
```

This does not abort the whole consensus evaluation, unless invalid return handling itself caused or attempted a deterministic contract violation.

### P3B-D11 — Missing vote method means missing vote

If a local participant has no approved `consensus_vote` method, the participant vote becomes:

```text
missing
```

with source label:

```text
actor_method_missing
```

Missing vote method is not a structural validation error.

Strategy semantics decide the final outcome.

### P3B-D12 — Participant vote failures

P3b-0 distinguishes participant-level vote failure from deterministic contract violation.

If an actor vote method is absent, not locally resolvable, has an incompatible signature, raises an ordinary runtime exception before any forbidden side effect, or returns a malformed vote value, the VoteSource MUST treat that participant's vote as:

```text
missing
```

Allowed source labels:

```text
actor_method_missing
actor_not_local
actor_method_exception
actor_method_invalid
```

The diagnostic channel may record:

```text
participant identity
source label
exception type
stable error category
```

The diagnostic channel MUST NOT leak raw secrets, prompts, private payloads, or mutable host object internals.

This participant-level failure MUST NOT abort the whole consensus evaluation.

Strategy semantics determine the final result based on the resulting vote map.

### P3B-D12.1 — Contract violations fail the whole evaluation

The following are not participant-level vote failures:

```text
send
receive
spawn
await
suspend
migrate
live LLM call
network call
daemon packet use
mailbox mutation
actor_log mutation
execution_history mutation
consensus_tickets mutation
outbound_packets mutation
promise creation or resolution
dynamic VoteSource registry mutation
proposal view mutation
runtime event emission
durable artifact mutation
```

If any such operation is attempted or detected during vote collection, P3b-0 MUST fail closed with a stable validation error.

Approved reasons:

```text
invalid_request: vote_collection_side_effect
invalid_request: dynamic_votesource_registration
invalid_request: proposal_view_mutated
```

The adapter MUST NOT bind a consensus result.

The adapter MUST NOT append `distributed_consensus_decided`.

The adapter MUST NOT append legacy facade events.

### P3B-D13 — Side-effect guard

Before vote collection, the adapter or VoteSource MUST capture:

```text
execution_history length
actor_log length
consensus_tickets size
outbound_packets length
mailbox keys
per-mailbox lengths
promises size
runtime event buffer length, if present
VoteSource registry version
```

After vote collection, those values MUST be unchanged.

Any change MUST fail closed with:

```text
invalid_request: vote_collection_side_effect
```

The side-effect guard is required even if dispatch-level vote-query mode exists.

The side-effect guard must detect side effects that pass through indirect paths.

### P3B-D13.1 — Forbidden operation trace

The implementation SHOULD record an internal forbidden-operation trace during vote-query mode.

The trace may include stable operation names such as:

```text
send
receive
spawn
await
suspend
migrate
llm_call
memory.write
memory.clear
memory.forget
network_emit
daemon_emit
promise_create
promise_resolve
registry_mutation
proposal_mutation
```

This trace is diagnostic.

It MUST NOT enter semantic hashes.

It MUST NOT expose private payloads.

### P3B-D14 — Vote Query Mode

P3b-0 MUST execute actor vote methods under internal vote-query mode.

While vote-query mode is active, interpreter dispatch MUST reject:

```text
send
receive
spawn
await
suspend
migrate
llm_call
memory.write
memory.clear
memory.forget
network emit
daemon packet emit
promise create
promise resolve
consensus ticket mutation
VoteSource registry mutation
```

Vote-query mode violations MUST be treated as deterministic contract violations, not as ordinary participant exceptions.

Vote-query mode MUST be entered before invoking the first actor vote method.

Vote-query mode MUST be exited in a `finally`-equivalent cleanup path.

Vote-query mode MUST NOT remain enabled after consensus evaluation fails or succeeds.

### P3B-D15 — Source labels

P3b-0 may add these source labels:

```text
actor_method
actor_method_missing
actor_method_exception
actor_method_invalid
actor_not_local
```

These labels are advisory.

They MUST NOT imply daemon, network, durable, replay, mailbox, or LLM voting.

They MUST NOT alter vote semantics.

### P3B-D16 — VoteSource registry

P3b-0 may introduce a runtime VoteSource registry.

Approved registry entries:

```text
null
explicit
actor_method
```

Forbidden registry entries in P3b-0:

```text
daemon
network
mailbox
durable
promise
llm
```

Registry selection MUST NOT use new user-facing grammar in P3b-0.

### P3B-D16.1 — Registry immutability

The P3b-0 VoteSource registry is configuration-only.

Registry mutation is permitted only before consensus evaluation begins, through an approved interpreter setter or runtime configuration path.

Dynamic registration, deregistration, replacement, or precedence mutation during `evaluate_distributed_consensus()` is forbidden.

If registry mutation is attempted or detected during consensus evaluation, P3b-0 MUST fail closed with:

```text
invalid_request: dynamic_votesource_registration
```

### P3B-D17 — Default source policy

P3b-0 MUST preserve the P3a default unless actor-method voting is explicitly enabled.

Default policy:

```text
If an explicit VoteSource is configured, use it.
Else if ActorMethodVoteSource is explicitly enabled by runtime configuration before evaluation, use ActorMethodVoteSource.
Else use NullVoteSource.
```

P3b-0 MUST NOT silently switch the default from `NullVoteSource` to actor-method voting.

This prevents hidden behavior changes in programs that happen to define a method named `consensus_vote`.

### P3B-D18 — P3a test suite remains authoritative

All P3a tests MUST continue to pass unchanged unless this RFC explicitly identifies a test whose expected behavior must change.

P3b-0 MUST add tests.

P3b-0 MUST NOT weaken P3a assertions.

---

## 9. Proposed Implementation Shape

### 9.1 Suggested source module

Suggested file:

```text
synapse/runtime/consensus_vote_sources.py
```

Suggested class:

```text
ActorMethodVoteSource
```

This RFC does not require this exact file name, but implementation must preserve the separation:

```text
VoteSource implementation != ConsensusEngine
VoteSource implementation != parser
VoteSource implementation != AST
VoteSource implementation != durable replay engine
```

### 9.2 Suggested VoteSource interface

A source may expose:

```text
collect_votes(request, context) -> list[VoteRecord]
```

Where `context` contains advisory runtime lookup data.

The `context` must not enter semantic hashes.

### 9.3 Suggested vote collection context

Suggested context fields:

```text
participant_runtime_values
participant_identity_map
proposal_view
method_name
vote_query_mode_hooks
side_effect_guard
registry_version
```

Forbidden context fields:

```text
mailbox object
actor_log object
execution_history object
interpreter mutable internals exposed to actor
durable artifact object
network transport object
daemon object
LLM provider object
```

### 9.4 Suggested adapter flow

The interpreter adapter may follow this flow:

```text
1. Evaluate participants while preserving raw runtime values.
2. Normalize participants using existing P3a rules.
3. Evaluate topic/quorum/timeout/policy.
4. Build ConsensusRequest without votes.
5. Ask engine to validate request and build proposal_id/proposal_view preimage.
6. Deep-freeze proposal_view.
7. Select VoteSource:
   - explicit source if configured;
   - actor_method only if explicitly enabled;
   - otherwise null source.
8. Capture side-effect guard snapshot.
9. Enter vote-query mode.
10. Collect votes.
11. Exit vote-query mode.
12. Verify side-effect guard snapshot.
13. Pass votes into existing engine decision path.
14. Bind result if operational result is valid.
15. Append distributed_consensus_decided if operational result is valid.
```

### 9.5 Suggested engine refactor

P3b-0 may require the engine to expose proposal-first functionality:

```text
validate_request_without_votes(...)
build_proposal_view(...)
evaluate_with_votes(...)
```

This refactor is allowed only if P3a visible semantics remain stable.

If this refactor changes `proposal_id`, `votes_hash`, `result_hash`, event shape, reason values, or outcome semantics without explicit approval, implementation is blocked.

---

## 10. Validation and Error Semantics

### 10.1 Existing P3a validation reasons

Existing P3a validation reasons remain valid, including:

```text
invalid_request: empty_participants
invalid_request: duplicate_participant
invalid_request: unresolved_participant
invalid_request: unsupported_participant_identity
invalid_request: unknown_vote_state
invalid_request: vote_for_unknown_participant
invalid_request: conflicting_vote
invalid_request: duplicate_vote
invalid_request: non_integer_quorum
invalid_request: quorum_out_of_bounds
invalid_request: non_integer_timeout
invalid_request: negative_timeout
invalid_request: unknown_strategy
invalid_request: unsupported_canonical_value
```

### 10.2 New P3b-0 participant source labels

P3b-0 may produce missing votes with source labels:

```text
actor_method_missing
actor_not_local
actor_method_exception
actor_method_invalid
```

These are not structural validation failures.

### 10.3 New P3b-0 validation reasons

P3b-0 may add these structural validation reasons:

```text
invalid_request: vote_collection_side_effect
invalid_request: dynamic_votesource_registration
invalid_request: proposal_view_mutated
```

### 10.4 Result binding rule

If the failure is participant-level and becomes `missing`, consensus evaluation continues.

If the failure is a contract violation, the adapter must not bind the consensus result.

### 10.5 Event append rule

If the failure is participant-level and the engine returns an operational result, the adapter may append `distributed_consensus_decided`.

If the failure is a contract violation, the adapter must not append `distributed_consensus_decided`.

Legacy facade events remain forbidden.

---

## 11. Stop Gates

Implementation MUST stop if any of the following are required or observed:

```text
BLOCKED — PARSER_AST_EXPANSION_REQUIRED
BLOCKED — USER_FACING_VOTE_SYNTAX_REQUIRED
BLOCKED — MAILBOX_VOTE_COLLECTION_REQUIRED
BLOCKED — DAEMON_VOTE_PATH_REQUIRED
BLOCKED — NETWORK_VOTE_PATH_REQUIRED
BLOCKED — DURABLE_REPLAY_SCOPE_REQUIRED
BLOCKED — DURABLE_ALLOWLIST_EXPANSION_REQUIRED
BLOCKED — LIVE_LLM_VOTE_PATH_REQUIRED
BLOCKED — ACTOR_MAILBOX_WAIT_REQUIRED
BLOCKED — PROMISE_SUSPENSION_REQUIRED
BLOCKED — CONSENSUS_TICKET_LIFECYCLE_REQUIRED
BLOCKED — P3A_SEMANTIC_RULE_REGRESSION
BLOCKED — VOTE_COLLECTION_SIDE_EFFECT
BLOCKED — PROCESS_ID_SEMANTIC_IDENTITY_REQUIRED
BLOCKED — DYNAMIC_VOTESOURCE_REGISTRATION_ATTEMPTED
BLOCKED — DURABLE_PROMISE_VOTE_COLLECTION_ATTEMPTED
BLOCKED — VOTE_METHOD_MUTATES_PROPOSAL_VIEW
BLOCKED — PROPOSAL_VIEW_MUTATED
BLOCKED — ACTOR_METHOD_DEFAULT_ENABLEMENT_UNAPPROVED
BLOCKED — EXECUTABLE_ACTOR_ENV_MAPPING_UNRESOLVED
```

---

## 12. Required Tests

### 12.1 Actor method happy path

A local agent defines:

```text
consensus_vote(proposal) -> "yes"
```

ActorMethodVoteSource is explicitly enabled before evaluation.

Consensus commits under `MajorityVote` quorum 1.

### 12.2 Explicit enablement path

If ActorMethodVoteSource is not explicitly enabled, the interpreter uses `NullVoteSource`.

A local agent with `consensus_vote` must not be queried silently.

### 12.3 Missing method path

A local agent without `consensus_vote` produces:

```text
vote = missing
source_label = actor_method_missing
```

### 12.4 Nonlocal actor path

A `DurableActorRef` without executable local method environment produces:

```text
vote = missing
source_label = actor_not_local
```

No mailbox/network/daemon/promise action is attempted.

### 12.5 Ordinary exception path

A `consensus_vote` method that raises an ordinary exception before side effects produces:

```text
vote = missing
source_label = actor_method_exception
```

Consensus evaluation continues.

### 12.6 Wrong signature path

A method with the wrong signature produces:

```text
vote = missing
source_label = actor_method_exception
```

Consensus evaluation continues.

### 12.7 Invalid return path

A vote method returning any of the following produces:

```text
vote = missing
source_label = actor_method_invalid
```

Cases:

```text
"maybe"
42
{"vote": "maybe"}
{"unexpected": "yes"}
non-JSON-compatible advisory object
```

### 12.8 Contract violation: send

A vote method attempting `send` fails the whole evaluation with:

```text
invalid_request: vote_collection_side_effect
```

No binding.

No `distributed_consensus_decided`.

### 12.9 Contract violation: receive

A vote method attempting `receive` fails the whole evaluation with:

```text
invalid_request: vote_collection_side_effect
```

### 12.10 Contract violation: await/suspend

A vote method attempting `await` or `suspend` fails the whole evaluation with:

```text
invalid_request: vote_collection_side_effect
```

### 12.11 Contract violation: LLM

A vote method attempting a live LLM call fails the whole evaluation with:

```text
invalid_request: vote_collection_side_effect
```

### 12.12 Contract violation: memory mutation

A vote method attempting:

```text
memory.write
memory.clear
memory.forget
```

fails the whole evaluation with:

```text
invalid_request: vote_collection_side_effect
```

### 12.13 Proposal view immutability

A vote method attempting to mutate:

```text
proposal["topic"]
proposal["participants"]
proposal["quorum"]
nested proposal fields
```

must either be physically blocked by the deep-frozen object or detected after the call.

Expected failure when mutation is attempted:

```text
invalid_request: proposal_view_mutated
```

### 12.14 Registry immutability

A vote method or nested call attempting to register/replace/remove a VoteSource during consensus evaluation fails with:

```text
invalid_request: dynamic_votesource_registration
```

### 12.15 Side-effect guard

A vote method that indirectly mutates any of the following must fail:

```text
execution_history
actor_log
mailboxes
outbound_packets
promises
consensus_tickets
runtime event buffer
VoteSource registry version
```

Expected failure:

```text
invalid_request: vote_collection_side_effect
```

### 12.16 Hash compatibility

For equivalent explicit votes and actor-method votes:

```text
participants equal
vote states equal
strategy equal
topic equal
quorum equal
timeout equal
```

The following must match:

```text
votes_hash
result_hash
outcome
committed
reason
```

### 12.17 P3a regression

Existing P3a tests must pass unchanged.

At least these suites must be covered:

```text
tests/test_consensus_engine_p3a.py
tests/test_consensus_adapter_p3a.py
tests/test_collective_intelligence.py
```

### 12.18 Scope guard tests

Implementation PR must prove no changes are required to:

```text
synapse/parser.py
synapse/ast.py
synapse/lexer.py
synapse/application.py durable allowlist
docs/RFC-CONSENSUS-P3.md approved contract
```

---

## 13. Evidence Requirements for Implementation PR

The implementation PR must include:

```text
base SHA
final head SHA
changed files
test commands
test counts
known failures
diff check result
py_compile result
P3a regression result
P3b targeted result
scope statement
review status
```

The PR body must explicitly state:

```text
No parser/AST/lexer expansion.
No durable allowlist expansion.
No mailbox-backed voting.
No daemon-backed voting.
No network voting.
No DurablePromise vote path.
No live LLM voting.
No durable replay closure.
No stateful consensus ticket lifecycle.
No production distributed consensus protocol claim.
```

The implementation PR must not update capability status to `Production`.

The implementation PR may leave capability status unchanged until evidence closure.

A separate evidence closure PR may update status to:

```text
Partial — P3b local runtime vote source verified
```

if evidence supports it.

---

## 14. Documentation Requirements

P3b-0 implementation must update or add documentation only if authorized by the implementation scope.

Recommended documents for implementation/evidence closure:

```text
docs/evidence/P3B_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

The original approved `docs/RFC-CONSENSUS-P3.md` should not be rewritten by implementation PRs.

This RFC document is published as:

```text
docs/RFC-CONSENSUS-P3B.md
```

Approval must be explicit before runtime implementation.

---

## 15. Approval Gate

This RFC is approved by the Approval Record at the top of this document.

The approval authorizes only:

```text
explicitly enabled synchronous local ActorMethodVoteSource
read-only VoteSource registry
deep-frozen proposal view
vote-query mode
side-effect guard
participant fault isolation
contract violation fail-closed
preservation of P3a semantics
```

The approval does not authorize:

```text
mailbox-backed voting
daemon-backed voting
network voting
DurablePromise vote path
await/suspend vote collection
live LLM voting
durable replay closure
stateful consensus ticket lifecycle
parser/AST/lexer expansion
production distributed consensus protocol claims
```

---

## 16. Future Work

### 16.1 P3b-1 — Spawned actor executable environment contract

May define whether and how spawned actor process instances map to executable method environments.

Must resolve:

```text
actor_name vs process_id
local instance identity
migration state
method environment availability
multi-instance ambiguity
```

### 16.2 P3b-2 — Mailbox-backed VoteSource

May define vote request/response messages.

Must resolve:

```text
message schemas
history events
replay matching
timeouts
pending lifecycle
duplicate responses
stale responses
side-effect recording
```

This likely overlaps P3c and must be scoped carefully.

### 16.3 P3b-3 — Daemon packet VoteSource

May define daemon vote packets.

Must resolve:

```text
packet schema
transport semantics
node identity
participant identity
signature or trust model
packet replay
packet duplication
packet ordering
network partitions
```

This must not be claimed in P3b-0.

### 16.4 P3c — Durable / Replay Closure

Must define:

```text
durable-supported classification
canonical event consumption
durable vote persistence
replay cursor behavior
fail-closed replay matching
legacy event handling
durable ticket lifecycle
hash coverage
artifact evidence
```

### 16.5 P3d — LLM-Assisted Voting

Must define:

```text
LLM VoteSource contract
prompt template versioning
model output schema
refusal handling
invalid output handling
provider timeout handling
cost controls
latency controls
replay rule
recorded vote consumption
no live model call during replay
```

### 16.6 Future grammar cleanup

A later RFC may introduce:

```text
strategy <strategy_name>
vote source configuration syntax
explicit vote syntax
```

P3b-0 does not.

---

## 17. Non-Goals

P3b-0 is not:

```text
a distributed protocol
a consensus network layer
a mailbox protocol
a durable vote workflow
a replay-verified vote journal
an LLM voting system
a grammar migration
a production distributed consensus implementation
a Raft/Paxos/Tendermint/PBFT implementation
```

P3b-0 is:

```text
a deterministic runtime bridge from explicitly enabled local actor methods to the existing P3a semantic engine.
```

---

## 18. Final Approved Position

The approved direction is:

```text
P3b-0 must connect local actor/agent methods to P3a through an explicitly enabled ActorMethodVoteSource.
```

The mandatory invariant is:

```text
ordinary participant failure becomes missing;
deterministic contract violation fails the whole evaluation.
```

This preserves:

```text
P3a deterministic reducer
honest capability signaling
scope separation
future P3c replay closure
future P3d LLM voting
future network/daemon protocol work
```

This RFC authorizes P3b-0 implementation only within the approved scope.

It does not authorize production distributed consensus protocol behavior.
