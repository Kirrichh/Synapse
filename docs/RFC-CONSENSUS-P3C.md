# RFC-CONSENSUS-P3C — Durable Replay Closure Contract for Distributed Consensus

**Status:** DRAFT  
**Stage:** P3c RFC  
**Implementation status:** NOT AUTHORIZED UNTIL APPROVAL GATE  
**Repository mutation:** DOCUMENTATION DRAFT ONLY  
**Primary target:** Define the durable/replay closure contract for `distributed_consensus_decided`  
**Primary implementation slice:** P3c-0 — Canonical Replay Consumption for `distributed_consensus_decided`  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Mailbox-backed vote collection in P3c-0:** NOT ALLOWED  
**Daemon-backed vote collection in P3c-0:** NOT ALLOWED  
**Network-backed vote collection in P3c-0:** NOT ALLOWED  
**DurablePromise-backed vote completion in P3c-0:** NOT ALLOWED  
**Signal-injected vote resolution in P3c-0:** NOT ALLOWED  
**Stateful consensus ticket lifecycle in P3c-0:** NOT ALLOWED  
**Live LLM vote production in P3c-0:** NOT ALLOWED  
**Parser / AST / lexer expansion in P3c-0:** NOT ALLOWED  
**Durable allowlist expansion in P3c-0:** NOT ALLOWED  
**Capability target after successful P3c-0 evidence closure:** `Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed`  
**Capability target explicitly not claimed:** `Production`

---

## 0. Purpose of this RFC

This RFC defines the P3c durable/replay closure contract for the distributed consensus runtime path.

P3a established the deterministic semantic consensus core. P3b connected explicit opt-in synchronous local actor-method voting to the P3a `VoteSource` seam. P3b intentionally did not claim durable/replay closure, mailbox-backed voting, daemon voting, network voting, promise-backed vote completion, stateful ticket lifecycle, or production distributed consensus protocol behavior.

P3c exists because a consensus result that can be produced in LIVE execution must also have a well-defined REPLAY contract. Without a canonical REPLAY path, a snapshot or persisted runtime history containing `distributed_consensus_decided` cannot safely restore consensus-bound user-visible state.

The first P3c implementation slice is:

```text
P3c-0 — Canonical Replay Consumption for distributed_consensus_decided
```

P3c-0 answers this question:

```text
Given a previously recorded distributed_consensus_decided event in execution_history,
how must the interpreter replay the corresponding DistributedConsensusStmt without
recollecting votes, without invoking actor vote methods, without appending duplicate
events, and without mutating side-effect state?
```

P3c-0 does not answer:

```text
How are future votes collected over actor mailboxes, network transports, daemon packets,
DurablePromise completions, signal injection, live LLM providers, or stateful consensus
tickets?
```

Those are explicitly deferred to later approved stages.

---

## 1. Executive Summary

P3c-0 introduces a replay-sufficient event contract for distributed consensus.

The core decision is:

```text
Journal the nondeterministic result of vote collection.
Replay deterministic consensus reduction from the recorded votes.
Verify replay integrity through canonical hashes.
Bind the engine-produced result.
Advance replay_cursor according to the runtime replay primitive exactly once.
Do not append a new event during REPLAY.
```

In P3a/P3b, the LIVE consensus result contains a public `votes` map, but `distributed_consensus_decided` event payload currently contains only `vote_counts`, `votes_hash`, and `result_hash`. That event is not sufficient to restore the full public result shape during REPLAY without recollecting votes.

P3c-0 therefore introduces:

```text
schema_version: consensus.event.v2
```

The `consensus.event.v2` payload adds:

```text
votes: { participant: vote_state }
```

where `vote_state` is one of:

```text
yes
no
abstain
missing
```

The event does not need to duplicate an arbitrary serialized full result object. Instead, it records the nondeterministic vote-collection output. The deterministic consensus reducer remains the owner of outcome, reason, `vote_counts`, `votes_hash`, `result_hash`, and public result construction.

This mirrors durable execution practice: external or nondeterministic step results are journaled, while deterministic orchestration logic is replayed and checked against recorded history. Recorded votes in `consensus.event.v2` are analogous to a durable side-effect or activity result: they are recorded once and reused on replay instead of re-executing actor vote methods. See [TEMPORAL-WF], [TEMPORAL-EVENT], [TEMPORAL-ACTIVITY], [AZURE-DURABLE], [RESTATE-DURABLE], [DBOS-DETERMINISM].

---

## 2. Relationship to Existing Consensus Stages

### 2.1 P3a status before P3c

P3a delivered the deterministic semantic consensus core:

```text
ConsensusEngine
ConsensusRequest
VoteSource seam
VoteRecord
yes/no/abstain/missing vote states
MajorityVote / UnanimousVote / NoVetoVote
proposal_id
votes_hash
result_hash
distributed_consensus_decided event
strict validation
fail-closed malformed request behavior
```

P3a established the principle that the consensus engine owns consensus mathematics and canonical hash semantics.

P3c must preserve that ownership.

### 2.2 P3b status before P3c

P3b actor-method vote integration is implemented, merged, and evidence-closed.

P3b closed the following local runtime integration scope:

```text
explicit opt-in actor-method vote source
synchronous local runtime only
ActorMethodVoteSource behind VoteSource
deep-frozen proposal view
vote-query mode
side-effect guard
participant fault isolation
contract violation fail-closed
P3a hash/result/event contract preservation
```

P3b explicitly did not close:

```text
durable replay closure
mailbox-backed vote collection
daemon-backed vote collection
network-backed vote collection
DurablePromise vote completion
signal-injected vote resolution
stateful consensus ticket lifecycle
live LLM vote production
production distributed consensus protocol behavior
```

### 2.3 P3c relationship to P3b

P3c-0 does not add a new live vote source.

P3c-0 does not broaden P3b actor-method voting.

P3c-0 defines what happens after a P3a/P3b consensus event is already recorded in durable execution history and the program later replays that history.

### 2.4 P3c relationship to P3b-1 / P3b-N

Future mailbox, daemon, and network vote collection stages may still be called P3b-N or may be placed under later P3c sub-stages depending on team approval.

However, P3c-0 must happen before any of them because mailbox, daemon, network, promise, and signal delivery all require durable/replay semantics.

P3b-1 or mailbox stages require a separate RFC or separate approved substage even if they are later placed under the broader P3c umbrella.

### 2.5 P3c relationship to P3d

P3d owns LLM-assisted voting.

P3c-0 must not call live LLM providers.

P3c-0 must not define LLM vote prompts, model output schemas, refusal handling, provider timeout handling, cost controls, or replay rules for LLM votes.

If a vote path attempts to invoke a live LLM during replay, the evaluation must fail closed.

### 2.6 Dependency diagram

```text
P3a semantic consensus core
    ↓
P3b explicit local actor-method VoteSource
    ↓
P3c-0 canonical replay consumption
    ↓
P3c-1 durable consensus ticket lifecycle
    ↓
P3c-2 mailbox / promise / signal vote completion
    ↓
P3d LLM-assisted voting
```

Only P3c-0 is in scope for this RFC draft.

---

## 3. Current Code Facts Motivating P3c-0

### 3.1 Consensus adapter currently appends during every evaluation

The current interpreter adapter evaluates participants, topic, quorum, timeout, policy, builds a `ConsensusRequest`, calls `ConsensusEngine.decide(request)`, then appends `decision.event_payload` to `execution_history` and binds `decision.result`.

There is no current REPLAY branch that consumes a recorded `distributed_consensus_decided` event.

Consequences:

```text
REPLAY currently risks recollecting votes.
REPLAY currently risks invoking actor-method vote providers.
REPLAY currently risks appending duplicate consensus events.
REPLAY currently does not advance replay_cursor for consensus events.
REPLAY currently does not perform canonical consensus event matching.
```

P3c-0 must correct this.

### 3.2 ConsensusEngine currently has the right deterministic seam

`ConsensusEngine.decide(request)` already owns:

```text
proposal_preimage
proposal_id
vote collection through request.vote_source
vote_counts
outcome
reason
votes_hash
result_hash
public result shape
event payload shape
```

`ExplicitVoteSource` already accepts a mapping of participant to vote state and returns deterministic `VoteRecord` values.

This makes the following replay strategy possible:

```text
recorded event.v2 votes map
→ ExplicitVoteSource(recorded_votes)
→ ConsensusEngine.decide(replay_request)
→ recomputed votes_hash/result_hash/outcome/reason
→ compare with recorded event
→ bind engine-produced result
```

`ExplicitVoteSource(recorded_votes)` preserves `votes_hash` equivalence because source labels are intentionally excluded from the P3a/P3b vote hash contract.

### 3.3 Vote source labels are advisory and excluded from votes_hash

The P3a/P3b `votes_hash` preimage includes only participant/vote pairs, not source labels.

Therefore, a LIVE vote collected through `actor_method` and a REPLAY vote supplied through `explicit_map` can be hash-equivalent when the participant/vote pairs match.

This is not incidental. It is the reason P3c-0 can use `ExplicitVoteSource(recorded_votes)` during replay while preserving the same `votes_hash`.

P3c-0 must preserve this invariant.

### 3.4 Event v1 is not replay-sufficient

The existing `distributed_consensus_decided` event payload contains:

```text
type
schema_version
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
vote_counts
votes_hash
result_hash
```

It does not contain:

```text
votes
```

The public LIVE result does contain:

```text
votes
```

Therefore, `consensus.event.v1` cannot reconstruct the full public result shape without recollecting votes.

P3c-0 must not recollect votes.

Therefore, P3c-0 must introduce a replay-sufficient event schema.

### 3.5 Runtime replay infrastructure already exists

The runtime already has:

```text
execution_history
runtime_mode
replay_cursor
load_snapshot
restore_snapshot
history_hash
history_chain
mailboxes
actor_log
spawned_actors
promises
promise_routes
promise_tombstones
consensus_tickets
```

P3c-0 should integrate with the existing replay model rather than creating a parallel replay mechanism.

### 3.6 Mailbox, receive, promise, and signal paths are stateful

The existing mailbox and promise paths are not pure reads.

LIVE send/receive/promise behavior may mutate:

```text
mailboxes
actor_log
execution_history
outbound_packets
promises
runtime events
```

Receive also has explicit replay behavior around `message_received` and `receive_timeout`, and async receive may suspend and later continue with injected data.

These mechanisms require separate durable lifecycle design and must remain out of P3c-0.

### 3.7 Existing replay primitives have frontier and skip-list semantics

The existing replay engine has replay primitives that can:

```text
peek the next replay-significant event
consume an expected event
skip approved replay-skippable event types
advance replay_cursor
transition runtime_mode from REPLAY to LIVE at replay frontier
raise generic runtime mismatch errors
```

P3c-0 must preserve the existing frontier-to-LIVE behavior.

P3c-0 must not naively treat history exhaustion as replay corruption.

P3c-0 must not directly expose generic replay primitive mismatch errors as the consensus-specific replay error contract.

Instead, P3c-0 must use or introduce a consensus-specific replay consumption wrapper that preserves runtime replay semantics while mapping consensus mismatches into `ConsensusReplayIntegrityError`.

---

## 4. Open-Source Alignment and Rationale

This section is non-normative. It explains why P3c-0 follows a durable execution pattern rather than inventing a consensus-specific replay model.

### 4.1 Temporal-style deterministic workflow replay

Temporal requires workflow code to be deterministic. Workflow code must make the same Workflow API calls in the same sequence given the same input. Commands emitted during replay are compared against existing Event History, and a mismatch causes nondeterminism failure. External operations such as API calls, LLM/AI invocations, database queries, and other external interactions should be placed in Activities outside the replay path. See [TEMPORAL-WF].

P3c-0 applies the same split:

```text
actor-method vote collection = nondeterministic step
recorded votes map = journaled step result
consensus reduction from recorded votes = deterministic replayed logic
```

### 4.2 Temporal Event History and side effects

Temporal Event History is an append-only log that is durably persisted and used to recover workflow state after failures. Temporal Side Effects execute the nondeterministic function once and record its result in Event History; upon replay the Side Effect does not re-execute and returns the recorded result. See [TEMPORAL-EVENT].

P3c-0 maps this pattern to consensus:

```text
consensus_vote(proposal) calls are not replayed
their normalized vote result is recorded as votes map
REPLAY returns recorded vote data into deterministic consensus reduction
```

### 4.3 Temporal Activity completion and return values

Temporal Activities are used for business logic prone to failure and retries. Completed Activities do not re-execute as part of workflow replay. Activity parameters and return values are captured in Event History for the calling Workflow Execution. See [TEMPORAL-ACTIVITY].

P3c-0 follows this principle:

```text
ActorMethodVoteSource vote collection behaves like a nondeterministic activity boundary.
Recorded votes are the return value of that boundary.
Replay does not call the actor method again.
```

### 4.4 Azure Durable Functions event-sourced orchestrators

Azure Durable Functions orchestrators are event-sourced, reliable, and replayed. The replay behavior creates constraints: orchestrator code must be deterministic and must produce the same result each time. See [AZURE-DURABLE].

P3c-0 therefore keeps the replay path deterministic:

```text
re-evaluate proposal inputs
consume recorded event
use recorded votes
run deterministic reducer
fail closed on mismatch before replay frontier
transition to LIVE at replay frontier
```

### 4.5 Restate durable execution journal

Restate records every side-effecting operation and its result in a journal. On failure, Restate replays the journal, skips completed steps, and resumes from exactly where it left off. See [RESTATE-DURABLE].

P3c-0 follows this pattern:

```text
vote collection result is already completed after LIVE event write
replay must skip live vote collection
replay resumes by consuming recorded event data
```

### 4.6 DBOS deterministic workflows and completed steps

DBOS workflows are normal Python functions but must be deterministic: given the same inputs and step return values, they should invoke the same steps with the same inputs in the same order. DBOS also guarantees that completed steps are never re-executed after they complete. See [DBOS-DETERMINISM].

P3c-0 treats actor-method vote collection as a completed step:

```text
completed vote collection is not re-executed
recorded votes are replayed
deterministic consensus reduction is checked
```

### 4.7 Raft-style integrity note

P3c-0 does not implement Raft, Paxos, Tendermint, PBFT, leader election, log replication, membership, quorum replication, view changes, or Byzantine behavior.

The only relevant alignment is conceptual:

```text
durable logs must not silently diverge from replayed commands
```

P3c-0 uses this principle only for local runtime replay integrity.

---

## 5. Normative Language

The following terms are normative in this RFC:

- **MUST** means required.
- **MUST NOT** means forbidden.
- **SHOULD** means strongly expected unless an approved documented reason exists.
- **MAY** means permitted only inside the stated boundary.
- **P3a** means the approved deterministic semantic consensus core.
- **P3b** means the approved local actor-method vote source integration stage.
- **P3c** means durable / replay closure for consensus.
- **P3c-0** means the first P3c implementation slice: canonical replay consumption for `distributed_consensus_decided`.
- **Recorded votes** means the normalized participant-to-vote map written into `consensus.event.v2`.
- **Vote recollection** means calling any live `VoteSource`, `ActorMethodVoteSource`, actor method, mailbox, daemon, network, LLM, promise, signal, or other runtime source to obtain votes during REPLAY.
- **Replay-safe vote source** means a deterministic vote source constructed only from recorded history data, such as `ExplicitVoteSource(recorded_votes)`, that does not call live actor methods, live VoteSource selection, mailbox, daemon, network, promise, signal, or LLM paths.
- **Replay consumption** means reading an already recorded event from `execution_history` through approved replay semantics and using it to drive deterministic replay.
- **Replay frontier** means the point where persisted `execution_history` is exhausted and the runtime may transition from REPLAY to LIVE.
- **Replay integrity mismatch** means replayed deterministic inputs or recomputed integrity anchors do not match the recorded event before the replay frontier.
- **Replay-sufficient event** means an event with enough data to restore the public result shape without recollecting nondeterministic vote data.
- **Primary integrity anchors** means `proposal_id`, `votes_hash`, and `result_hash`.
- **Diagnostic fields** means event fields that are covered by primary integrity anchors but may be checked separately to produce clearer error messages.
- **Source mutation nondeterminism** means source-code or runtime input drift that changes deterministic proposal inputs or statement identity during replay.

---

## 6. Scope

### 6.1 In Scope for P3c-0

P3c-0 includes:

1. Defining `consensus.event.v2`.
2. Adding normalized `votes` map to `distributed_consensus_decided` event payload.
3. Preserving `distributed_consensus_decided` as canonical consensus event type.
4. Defining replay consumption for `distributed_consensus_decided`.
5. Defining replay cursor advancement for consensus events.
6. Preserving replay frontier behavior.
7. Defining consensus-specific replay error mapping.
8. Defining replay integrity checks for consensus events.
9. Defining default legacy v1 behavior.
10. Defining fail-closed errors for malformed, unsupported, or mismatched consensus events before replay frontier.
11. Defining how recorded votes are passed into deterministic consensus reduction.
12. Reusing `ExplicitVoteSource(recorded_votes)` or an equivalent engine-owned replay-safe path.
13. Preserving `ConsensusEngine` as owner of result/hash semantics.
14. Preserving source-label exclusion from `votes_hash`.
15. Preventing actor-method vote recollection during REPLAY.
16. Preventing live VoteSource use during REPLAY.
17. Preventing event append during REPLAY consensus consumption before frontier.
18. Preventing runtime state mutation during REPLAY consensus consumption before frontier.
19. Adding P3c replay tests.
20. Adding PR-body evidence for the implementation PR.
21. Adding post-merge evidence after implementation merge.

### 6.2 Out of Scope for P3c-0

P3c-0 excludes:

1. Parser extension.
2. AST extension.
3. Lexer extension.
4. New `.syn` vote syntax.
5. New consensus syntax.
6. Strategy grammar cleanup.
7. Mailbox-backed vote collection.
8. `send_message`-based vote request delivery.
9. `receive`-based vote response collection.
10. Actor mailbox waiting.
11. Async suspension for votes.
12. `DurablePromise` vote collection.
13. Signal-injected vote resolution.
14. Durable vote persistence beyond event-level recorded votes.
15. Durable allowlist expansion.
16. Daemon packet voting.
17. Network delivery.
18. Runtime transport protocol.
19. Live LLM vote production.
20. LLM prompt template versioning.
21. LLM model output schema.
22. Stateful consensus ticket lifecycle.
23. Production distributed consensus protocol claims.
24. Raft/Paxos/Tendermint/PBFT implementation.
25. Byzantine behavior modeling.
26. Signature verification.
27. Leader election.
28. View-change protocol.
29. Dynamic VoteSource registration during replay.
30. Using `process_id` as semantic participant identity.
31. Inferring executable actor environment from metadata alone.
32. Changing existing P3a/P3b tests to weaken hash equivalence.
33. Changing existing P3a/P3b tests to hide source-label behavior.
34. Changing parser/AST/lexer to support new replay syntax.
35. Adding degraded v1 replay projection as default behavior.

---

## 7. Core Design

### 7.1 P3c-0 replay model

P3c-0 uses the following durable split:

```text
Re-evaluated deterministic inputs:
- topic
- participants
- quorum
- timeout
- policy_ref / resolved policy
- strategy
- statement_identity
- coordinator advisory value

Journaled nondeterministic vote-collection result:
- votes map
```

P3c-0 does not journal arbitrary interpreter state as consensus state.

P3c-0 does not journal a Python frame.

P3c-0 does not journal actor method internals.

P3c-0 records the durable result of vote collection and replays deterministic consensus semantics over that recorded data.

### 7.2 Why event v2 records votes

The LIVE public result includes:

```text
votes: { participant: vote_state }
```

A replay-sufficient event must allow the runtime to bind a public result with the same observable consensus shape.

`votes_hash` is sufficient to prove integrity of a vote map, but it cannot reconstruct that map.

Therefore, P3c-0 introduces `consensus.event.v2` with:

```text
votes: { participant: vote_state }
```

### 7.3 Why event v2 does not need a full serialized result blob

The full public result is deterministic once these inputs are known:

```text
proposal_id
topic
participants
strategy
policy
quorum
timeout
votes
vote_counts
votes_hash
outcome
reason
result_hash
```

The engine already owns the deterministic construction of this result shape.

P3c-0 should not introduce a parallel adapter-owned result reconstruction path.

### 7.4 Replay through recorded votes

The intended replay flow before replay frontier is:

```text
1. Adapter re-evaluates deterministic proposal inputs from AST and runtime state.
2. Adapter constructs the same ConsensusRequest shape as LIVE, except it must not select a live VoteSource.
3. Adapter detects whether persisted replay history still has a replay-significant event for this statement.
4. If replay history is exhausted, runtime transitions to LIVE and executes the LIVE consensus path.
5. If a replay-significant event exists, adapter consumes exactly one distributed_consensus_decided event.
6. Adapter validates event type, schema, statement_identity, and recorded votes.
7. Adapter creates a replay-safe recorded-vote source, such as ExplicitVoteSource(recorded_votes).
8. Adapter invokes the engine-owned deterministic reducer path.
9. Adapter verifies proposal_id, votes_hash, result_hash, and diagnostic fields.
10. Adapter binds the engine-produced result.
11. Adapter appends no new event during the REPLAY consumption path.
```

### 7.5 Hash equivalence with label-exclusion

The live vote source may have source labels such as:

```text
actor_method
actor_method_missing
actor_method_exception
actor_method_invalid
actor_not_local
```

The replay source may use:

```text
explicit_map
recorded_replay
```

P3a/P3b vote hash semantics intentionally exclude source labels from `votes_hash`.

P3c-0 must preserve that design so replay via recorded votes remains hash-equivalent to live actor-method vote collection.

### 7.6 What gets matched

P3c-0 primary integrity anchors:

```text
proposal_id
votes_hash
result_hash
```

P3c-0 identity anchors:

```text
event.type
event.schema_version
statement_identity
```

P3c-0 diagnostic fields:

```text
outcome
reason
participants
strategy
policy
quorum
timeout
vote_counts
```

`outcome`, `reason`, `participants`, `strategy`, `policy`, `quorum`, `timeout`, and `vote_counts` are already covered by `result_hash` when the result preimage is unchanged. They may still be checked explicitly to provide stable, human-readable replay mismatch errors.

### 7.7 Proposal preimage recomputation

P3c-0 proposal identity must be recomputed through the same engine-owned canonical preimage path as LIVE.

The proposal preimage includes:

```text
topic
participants
quorum
timeout
policy
strategy
statement_identity
```

P3c-0 must not add a duplicate `proposal_recomputed_hash` field to the event.

The existing `proposal_id` remains the proposal input integrity anchor.

### 7.8 Source code mutation nondeterminism

If `.syn` source code or deterministic runtime inputs change between LIVE execution and REPLAY such that topic, participants, quorum, timeout, strategy, policy, or `statement_identity` changes, the recomputed `proposal_id` or `statement_identity` will mismatch the recorded event.

P3c-0 must fail closed in this case before replay frontier.

This is an intentional durable execution invariant, not a bug.

Future RFCs may define source layout-stable consensus block identity, AST path identity, structural fingerprint identity, or runtime versioning. P3c-0 does not change statement identity.

### 7.9 Statement identity note

P3c-0 preserves the current P3a/P3b statement identity shape:

```text
source:{line}:{column}
```

This identity is source-layout-sensitive.

A future RFC may replace it with:

```text
AST path
structural fingerprint
stable block id
explicit statement id
```

P3c-0 must not change parser, AST, lexer, or statement identity syntax.

---

## 8. Event Schema

### 8.1 Event type

P3c-0 preserves the canonical event type:

```text
distributed_consensus_decided
```

P3c-0 changes schema version for replay-sufficient payloads:

```text
consensus.event.v2
```

### 8.2 Required consensus.event.v2 fields

A `consensus.event.v2` payload must contain:

```text
type
schema_version
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

### 8.3 Field definitions

#### type

Required exact value:

```text
distributed_consensus_decided
```

#### schema_version

Required exact value:

```text
consensus.event.v2
```

#### proposal_id

Canonical hash of proposal preimage.

#### statement_identity

Stable adapter identity for the source statement.

Current identity form:

```text
source:{line}:{column}
```

P3c-0 does not change the statement identity contract.

#### votes

Normalized participant-to-vote map:

```text
{
  "<participant>": "yes" | "no" | "abstain" | "missing"
}
```

Rules:

```text
Every normalized participant key must be present in the votes map.
A participant key may map to the legal vote state "missing".
No extra participant keys are allowed.
All values must be strictly one of: "yes", "no", "abstain", "missing".
The mapping must be JSON-compatible.
The mapping must be canonicalizable through the project's canonical JSON path.
History hashing must not depend on incidental Python dict iteration order.
Source labels are not included.
```

#### vote_counts

Counts for approved vote states.

Required keys:

```text
yes
no
abstain
missing
```

#### votes_hash

Canonical hash over participant/vote pairs.

The source label must not enter `votes_hash`.

#### result_hash

Canonical hash over result preimage.

#### coordinator

Advisory runtime identity. It must not enter proposal identity. It may be present in event/result for diagnostics.

### 8.4 Example v2 event

```json
{
  "type": "distributed_consensus_decided",
  "schema_version": "consensus.event.v2",
  "proposal_id": "sha256:...",
  "statement_identity": "source:42:5",
  "outcome": "committed",
  "reason": "quorum_reached",
  "participants": ["Alice", "Bob", "Carol"],
  "coordinator": "global",
  "strategy": "MajorityVote",
  "policy": null,
  "quorum": 2,
  "timeout": 0,
  "votes": {
    "Alice": "yes",
    "Bob": "yes",
    "Carol": "missing"
  },
  "vote_counts": {
    "yes": 2,
    "no": 0,
    "abstain": 0,
    "missing": 1
  },
  "votes_hash": "sha256:...",
  "result_hash": "sha256:..."
}
```

### 8.5 Event v1 handling

`consensus.event.v1` is not replay-sufficient because it lacks the normalized `votes` map.

Pre-P3c consensus replay consumption was not an approved working contract. The adapter did not consume consensus events from history as a durable replay boundary; it re-evaluated and appended a new event.

Therefore, P3c-0 default behavior for `consensus.event.v1` is fail-closed before replay frontier.

Required behavior:

```text
If REPLAY encounters distributed_consensus_decided with schema_version consensus.event.v1
before replay frontier:
    raise ConsensusReplayIntegrityError / ReplayIntegrityError
```

The error must indicate that the event is not replay-sufficient.

A future explicit compatibility mode may define degraded projection for v1, but that mode:

```text
must be explicitly enabled
must not silently upgrade v1 to v2
must not claim full result parity
must not count as P3c-0 replay closure evidence
```

### 8.6 Migration note

Existing histories that contain `consensus.event.v1` are not P3c-0 replay-closed histories.

P3c-0 does not promise automatic migration for v1 consensus events.

A later RFC may define:

```text
explicit v1 projection mode
offline history migration
schema upgrade tooling
historical reducer compatibility
```

None of those are authorized in P3c-0.

---

## 9. LIVE Execution Algorithm

### 9.1 Current LIVE behavior to preserve

In LIVE mode, consensus evaluation must continue to:

```text
evaluate participants
evaluate topic
evaluate quorum
evaluate timeout
resolve policy_ref
build ConsensusRequest
select allowed VoteSource
collect votes through allowed source
evaluate outcome through ConsensusEngine
bind result
append distributed_consensus_decided event
```

### 9.2 P3c-0 LIVE changes

P3c-0 changes the event schema written in LIVE mode.

After P3c-0, LIVE mode must write:

```text
schema_version: consensus.event.v2
votes: normalized vote map
```

LIVE mode must still preserve:

```text
proposal_id semantics
votes_hash semantics
result_hash semantics
outcome semantics
reason semantics
vote_counts semantics
public result shape
P3a strategy behavior
P3b actor-method opt-in behavior
```

LIVE mode must not change `votes_hash` semantics.

Source labels remain excluded from `votes_hash`.

### 9.3 LIVE event construction ownership

The consensus engine should remain owner of event payload construction.

The adapter must not manually add semantically meaningful fields to the event after engine decision except for already approved advisory runtime metadata.

The preferred implementation is to update `ConsensusEngine.decide()` so its `event_payload` is v2 and includes `votes`.

### 9.4 LIVE state mutation

LIVE mode may append exactly one `distributed_consensus_decided` event for the consensus statement.

P3c-0 does not add any extra LIVE consensus events.

P3c-0 does not add per-participant vote events.

P3c-0 does not add ticket lifecycle events.

---

## 10. REPLAY Execution Algorithm

### 10.1 Replay branch entry

The adapter must branch before live vote collection.

Pseudocode:

```python
def evaluate_distributed_consensus(self, node, env):
    request = self._build_consensus_request_from_node(node, env)

    if self.runtime_mode == RuntimeMode.REPLAY:
        replay_result = self._replay_distributed_consensus(node, env, request)
        if replay_result is not None or self.runtime_mode == RuntimeMode.REPLAY:
            return replay_result
        # replay frontier reached; continue with LIVE path

    return self._live_distributed_consensus(node, env, request)
```

The exact implementation may differ, but it must preserve:

```text
replay consumption before live vote source selection
frontier-to-LIVE behavior
no actor method call before replay event classification
```

### 10.2 Request construction during REPLAY

During REPLAY, the adapter re-evaluates deterministic proposal inputs:

```text
participants
topic
quorum
timeout
policy_ref
statement_identity
coordinator advisory identity
```

This mirrors ordinary deterministic replay of workflow/orchestrator code.

P3c-0 does not journal these proposal inputs separately as a replacement for re-evaluation.

If proposal input evaluation itself has nondeterministic or side-effecting behavior, that is outside P3c-0 and must be governed by existing or future replay rules for those constructs.

### 10.3 Replay event classification

The adapter must not naively call a generic replay primitive and expose its generic mismatch error as the public consensus replay contract.

The adapter must use or introduce a consensus-specific replay consumption wrapper.

The wrapper must:

```text
preserve runtime replay frontier behavior
respect existing replay skip-list semantics
classify the next replay-significant event
consume exactly one matching distributed_consensus_decided event
avoid replay_cursor double-advance
map wrong-type, malformed, unsupported-schema, and hash mismatches to ConsensusReplayIntegrityError
```

### 10.4 Replay frontier behavior

P3c-0 must distinguish:

```text
A. Replay-significant event exists before frontier but is wrong or malformed.
B. Persisted execution_history is exhausted and replay frontier is reached.
```

Case A:

```text
fail closed
```

Case B:

```text
transition runtime to LIVE and execute the LIVE consensus path
```

P3c-0 must not treat replay frontier as corruption.

### 10.5 Event consumption and cursor advancement

The adapter consumes exactly one consensus event when replay history has a matching consensus event.

The adapter may use existing replay primitives if wrapped safely.

If a replay primitive advances `replay_cursor`, the consensus replay wrapper must not advance it again.

If the wrapper performs manual cursor advancement, it must do so exactly once after successful event classification.

The wrapper must not perform arbitrary forward scans beyond approved replay skip behavior.

### 10.6 Recorded vote extraction

The adapter reads:

```text
event["votes"]
```

It validates at the structural boundary only enough to classify the event as having a votes mapping. Full semantic vote validation must remain engine-owned.

Required structural checks:

```text
votes exists
votes is mapping-like / JSON object-like
votes is JSON-compatible enough to be passed into ExplicitVoteSource(recorded_votes)
```

Semantic checks are owned by the engine.

Malformed recorded votes fail closed as replay integrity error.

### 10.7 Replay-safe vote source

The adapter must not call live vote source selection during REPLAY.

The adapter must not call:

```text
_select_consensus_vote_source()
ActorMethodVoteSource
consensus_vote(proposal)
mailbox vote source
daemon vote source
network vote source
LLM vote source
DurablePromise vote source
NullVoteSource as fallback for missing replay votes
```

The adapter must use recorded votes through a replay-safe deterministic source.

Acceptable approach:

```python
replay_vote_source = ExplicitVoteSource(recorded_votes)
replay_request = replace(request, vote_source=replay_vote_source)
decision = self._consensus_engine.decide(replay_request)
```

Alternative implementation is allowed only if it remains engine-owned and replay-safe:

```text
ConsensusEngine.replay_decide(request, event_payload)
ConsensusEngine.decide_from_recorded_votes(request, recorded_votes, expected_event_payload)
```

The adapter must not manually rebuild the public result shape.

### 10.8 Replay integrity verification

After deterministic reduction from recorded votes, P3c-0 verifies primary integrity anchors:

```text
decision.result["proposal_id"] == event["proposal_id"]
decision.result["votes_hash"] == event["votes_hash"]
decision.result["result_hash"] == event["result_hash"]
```

P3c-0 also verifies identity anchors:

```text
event["type"] == "distributed_consensus_decided"
event["schema_version"] == "consensus.event.v2"
decision.event_payload["statement_identity"] == event["statement_identity"]
```

The implementation may also compare diagnostic fields for clearer error messages:

```text
participants
strategy
policy
quorum
timeout
vote_counts
outcome
reason
```

A mismatch in diagnostic fields may be reported separately, but primary integrity remains anchored by `proposal_id`, `votes_hash`, and `result_hash`.

### 10.9 Recorded votes validation error mapping

The consensus engine remains the owner of vote validation.

When the adapter constructs `ExplicitVoteSource(recorded_votes)` and passes it to the engine, the engine's existing validation logic must reject malformed records, unknown participants, invalid participant coverage, or invalid vote states.

The adapter must not implement a parallel vote-validation path.

If `ConsensusValidationError` is raised during REPLAY while reducing recorded votes, the adapter must translate it into:

```text
ConsensusReplayIntegrityError:
REPLAY_INTEGRITY_ERROR: consensus recorded votes malformed
```

Recorded vote corruption is replay history corruption, not an ordinary participant-local fault.

### 10.10 Binding during REPLAY

The bound result must be the engine-produced result based on recorded votes.

Required result properties:

```text
schema_version == consensus.result.v1
proposal_id matches recorded event
votes map comes from recorded event through engine-owned path
vote_counts recomputed from recorded votes
outcome and reason recomputed deterministically
votes_hash matches recorded event
result_hash matches recorded event
```

The adapter must bind the result to `node.binding` exactly as LIVE mode does.

### 10.11 No event append during REPLAY consumption

During REPLAY consensus consumption before frontier, the adapter must not call:

```text
self.execution_history.append
self.emit_runtime_event
self.actor_log.append
self.mailboxes mutation
self.consensus_tickets mutation
self.promises mutation
self.outbound_packets mutation
```

If replay frontier is reached and runtime transitions to LIVE, subsequent LIVE execution follows LIVE rules and may append a new event.

### 10.12 Source code mutation / nondeterminism

If `.syn` source code is modified between LIVE execution and REPLAY such that deterministic proposal inputs or `statement_identity` change, recomputed `proposal_id` or `statement_identity` will mismatch the recorded event.

P3c-0 must fail closed before replay frontier in this scenario.

Stable diagnostic text should indicate:

```text
REPLAY_INTEGRITY_ERROR: consensus proposal_id mismatch
```

or:

```text
REPLAY_INTEGRITY_ERROR: consensus statement_identity mismatch
```

This is intentional durable replay behavior.

### 10.13 Replay purity snapshot

Implementation may use a replay-side state snapshot check similar to P3b vote-query side-effect guard.

If such a snapshot is used, it should cover:

```text
execution_history length
actor_log length
mailboxes shape
promises
consensus_tickets
outbound_packets
routing_table
promise_routes
promise_tombstones
telemetry_events
output_buffer
```

However, the primary P3c-0 contract is structural:

```text
do not call side-effect sources
do not append before frontier
do not mutate side-effect stores before frontier
```

---

## 11. Error Contract

### 11.1 Error class

P3c-0 should introduce:

```python
class ConsensusReplayIntegrityError(ReplayIntegrityError):
    pass
```

This class preserves compatibility with the existing replay integrity error family.

Tests may assert either:

```text
ReplayIntegrityError
```

or:

```text
ConsensusReplayIntegrityError
```

depending on implementation shape.

### 11.2 Stable error messages

Required stable messages include:

```text
REPLAY_INTEGRITY_ERROR: consensus event type mismatch
REPLAY_INTEGRITY_ERROR: expected consensus event, got {actual_type}
REPLAY_INTEGRITY_ERROR: consensus event schema unsupported
REPLAY_INTEGRITY_ERROR: consensus event malformed
REPLAY_INTEGRITY_ERROR: consensus recorded votes malformed
REPLAY_INTEGRITY_ERROR: consensus proposal_id mismatch
REPLAY_INTEGRITY_ERROR: consensus statement_identity mismatch
REPLAY_INTEGRITY_ERROR: consensus votes_hash mismatch
REPLAY_INTEGRITY_ERROR: consensus result_hash mismatch
REPLAY_INTEGRITY_ERROR: consensus outcome mismatch
REPLAY_INTEGRITY_ERROR: consensus reason mismatch
REPLAY_INTEGRITY_ERROR: consensus replay state mutation
REPLAY_INTEGRITY_ERROR: consensus replay cursor double advance
```

The RFC no longer requires history exhaustion itself to throw `consensus event missing`.

History exhaustion means replay frontier.

Wrong event before frontier is a replay integrity error.

### 11.3 Fail-closed rule before frontier

All replay integrity mismatches before replay frontier fail closed.

P3c-0 must not silently recompute live votes to recover.

P3c-0 must not ignore malformed consensus events.

P3c-0 must not fall back to `NullVoteSource` during REPLAY.

P3c-0 must not silently degrade v1 events into replay-successful results.

### 11.4 Replay frontier rule

If persisted history is exhausted, runtime may transition to LIVE and execute the consensus statement through LIVE behavior.

This is not an integrity error.

---

## 12. Normative Decisions

### P3C-D1 — P3c-0 is replay consumption only

P3c-0 must define only canonical replay consumption for `distributed_consensus_decided`.

P3c-0 must not add mailbox, promise, signal, network, daemon, ticket, LLM, parser, AST, lexer, or durable allowlist behavior.

### P3C-D2 — Proposal inputs are re-evaluated

During REPLAY, the adapter must re-evaluate deterministic proposal inputs from the program and runtime state.

These include:

```text
participants
topic
quorum
timeout
policy_ref
statement_identity
coordinator advisory identity
```

P3c-0 must not treat recorded event payload as a substitute for replaying deterministic proposal input evaluation.

### P3C-D3 — Only recorded votes are journaled as nondeterministic vote-collection result

P3c-0 must journal the normalized `votes` map in `consensus.event.v2`.

P3c-0 must treat actor-method vote collection as nondeterministic for replay purposes.

P3c-0 must not recollect votes during REPLAY.

### P3C-D4 — distributed_consensus_decided remains canonical event type

P3c-0 must preserve:

```text
distributed_consensus_decided
```

as the canonical consensus event type.

### P3C-D5 — consensus.event.v2 adds votes map

P3c-0 must introduce:

```text
schema_version: consensus.event.v2
```

and must include:

```text
votes
```

as normalized participant-to-vote map.

### P3C-D6 — Source labels remain advisory

P3c-0 must preserve the P3a/P3b rule that source labels do not enter `votes_hash`.

This is necessary for replay via recorded votes to remain hash-equivalent when live source labels differ from replay source labels.

### P3C-D7 — REPLAY uses recorded votes

During REPLAY before frontier, P3c-0 must use recorded event votes.

P3c-0 must not use live `VoteSource` selection.

P3c-0 must not call actor vote methods.

### P3C-D8 — Engine owns replay result/hash semantics

The consensus engine must remain the owner of result shape and hash semantics.

The adapter must not manually rebuild public consensus result shape.

Acceptable implementations include:

```text
ConsensusEngine.decide(request with ExplicitVoteSource(recorded_votes))
```

or an equivalent engine-owned replay helper.

### P3C-D9 — REPLAY verifies primary integrity anchors

P3c-0 must verify:

```text
proposal_id
votes_hash
result_hash
```

A mismatch in any primary integrity anchor must fail closed before replay frontier.

### P3C-D10 — Diagnostic field mismatches may be reported explicitly

P3c-0 should report clearer mismatch errors for:

```text
outcome
reason
participants
strategy
policy
quorum
timeout
vote_counts
statement_identity
```

Some of these checks are diagnostic because many are already covered by `proposal_id` or `result_hash`.

### P3C-D11 — REPLAY consumes exactly one consensus event before frontier

P3c-0 must consume exactly one `distributed_consensus_decided` event for the replayed consensus statement when such an event exists before replay frontier.

### P3C-D12 — REPLAY advances replay_cursor through approved primitive semantics

P3c-0 must avoid double-advancing `replay_cursor`.

If existing replay primitives advance cursor, the consensus replay wrapper must not advance it again.

### P3C-D13 — REPLAY appends no event before frontier

P3c-0 must not append `distributed_consensus_decided` or any substitute event during REPLAY consensus consumption before frontier.

### P3C-D14 — REPLAY mutates no side-effect state before frontier

P3c-0 must not mutate:

```text
execution_history
actor_log
mailboxes
consensus_tickets
promises
outbound_packets
routing_table
promise_routes
promise_tombstones
telemetry_events
```

during consensus replay consumption before frontier, except for approved replay cursor advancement.

### P3C-D15 — Event v1 is not replay-sufficient

`consensus.event.v1` lacks `votes`.

P3c-0 must fail closed by default when encountering `consensus.event.v1` before replay frontier.

A future explicit compatibility mode may define degraded projection, but that mode is outside P3c-0.

### P3C-D16 — Replay frontier transitions to LIVE

When persisted `execution_history` is exhausted, P3c-0 must treat this as replay frontier and allow transition to LIVE behavior.

History exhaustion is not a consensus replay integrity error.

### P3C-D17 — Deferred outcomes remain synchronous outcomes

`pending_missing_votes` remains a synchronous consensus outcome.

P3c-0 must keep:

```text
ticket_id = None
```

P3c-0 must not create durable consensus tickets.

### P3C-D18 — Mailbox, promise, signal, daemon, network, LLM are deferred

P3c-0 must defer:

```text
mailbox vote collection
receive-based vote collection
DurablePromise vote completion
signal-injected vote resolution
daemon vote packets
network vote transport
live LLM vote generation
```

### P3C-D19 — Recorded vote validation remains engine-owned

The consensus engine must remain the owner of recorded vote validation.

The adapter must map recorded vote validation failures during REPLAY to `ConsensusReplayIntegrityError`.

---

## 13. Implementation Plan for P3c-0

This section describes the future code changes once this RFC is approved.

### 13.1 Expected files

Expected implementation files:

```text
synapse/runtime/consensus_engine.py
synapse/interpreter.py
tests/test_consensus_replay_p3c.py
```

Potentially unchanged files:

```text
parser
AST
lexer
application durable allowlist
examples
workflows
docs/CAPABILITY_MATURITY_MATRIX.md
```

The implementation PR must not modify parser, AST, lexer, durable allowlist, examples, or workflows.

### 13.2 consensus_engine.py changes

#### 13.2.1 Event v2

Update `ConsensusEngine.decide()` to produce:

```text
schema_version: consensus.event.v2
```

for `distributed_consensus_decided` event payloads.

Add:

```text
votes
```

to the event payload.

The `votes` map must be:

```text
normalized participant identity -> vote state
```

It must match the public result votes map.

#### 13.2.2 Hash semantics unchanged

Do not change:

```text
proposal_id preimage
votes_hash preimage
result_hash preimage
strategy semantics
outcome semantics
reason semantics
quorum behavior
timeout behavior
participant identity normalization
```

In particular, do not add source labels to `votes_hash`.

#### 13.2.3 Replay-safe engine-owned reduction

The implementation may reuse existing `ConsensusEngine.decide()` by supplying `ExplicitVoteSource(recorded_votes)`.

If helper extraction is clearer, the engine may expose a pure helper, but it must not introduce separate result shape semantics.

Allowed helper names:

```text
replay_decide
decide_from_recorded_votes
verify_recorded_decision
```

No helper is required if the adapter can safely use existing `decide()`.

#### 13.2.4 Validation of recorded votes

The consensus engine remains the owner of vote validation.

When the adapter constructs `ExplicitVoteSource(recorded_votes)` and passes it to the engine, the engine's existing validation logic must reject malformed records, unknown participants, missing participant keys, extra participant keys, or invalid vote states.

The adapter must not implement a parallel vote-validation path.

If the engine raises a validation error during REPLAY from recorded votes, the adapter must translate it into:

```text
ConsensusReplayIntegrityError:
REPLAY_INTEGRITY_ERROR: consensus recorded votes malformed
```

### 13.3 interpreter.py changes

#### 13.3.1 Add consensus replay error

Add:

```python
class ConsensusReplayIntegrityError(ReplayIntegrityError):
    """Strict replay detected a missing, malformed, or mismatched consensus event before replay frontier."""
```

#### 13.3.2 Split live/replay consensus adapter path

Refactor `evaluate_distributed_consensus()` into shape similar to:

```python
def evaluate_distributed_consensus(self, node, env):
    request = self._build_consensus_request_from_node(node, env)

    if self.runtime_mode == RuntimeMode.REPLAY:
        replay_value = self._replay_distributed_consensus(node, env, request)
        if replay_value is not None or self.runtime_mode == RuntimeMode.REPLAY:
            return replay_value
        # replay frontier reached; continue with LIVE path

    return self._live_distributed_consensus(node, env, request)
```

The exact function names may differ, but the separation must be clear.

#### 13.3.3 Build request before branch

The adapter should build deterministic request inputs before selecting the replay/live vote source.

Required ordering:

```text
evaluate participants
evaluate topic
evaluate quorum
evaluate timeout
resolve policy_ref
construct statement_identity
construct ConsensusRequest without selecting live vote source in REPLAY
branch on runtime_mode
```

During REPLAY, the adapter must not call `_select_consensus_vote_source()`.

#### 13.3.4 LIVE branch

LIVE branch:

```text
select live vote source
call ConsensusEngine.decide()
append event v2
bind result
return result
```

#### 13.3.5 REPLAY branch before frontier

REPLAY branch before frontier:

```text
classify next replay-significant event
validate event type distributed_consensus_decided
validate schema_version consensus.event.v2
validate statement_identity
read recorded votes
construct ExplicitVoteSource(recorded_votes)
call engine-owned reducer path
compare proposal_id
compare votes_hash
compare result_hash
compare diagnostic fields as implemented
consume/advance cursor exactly once through approved primitive semantics
bind engine-produced result
return result
```

#### 13.3.6 REPLAY branch at frontier

If persisted history is exhausted:

```text
runtime transitions to LIVE
consensus executes through LIVE branch
new event v2 may be appended
```

#### 13.3.7 No append before frontier

The replay branch before frontier must not call:

```text
self.execution_history.append
self.emit_runtime_event
self.actor_log.append
self.mailboxes mutation
self.consensus_tickets mutation
self.promises mutation
self.outbound_packets mutation
```

#### 13.3.8 Actor-method skip proof

The replay branch must prove that actor vote methods are not invoked.

Implementation must not select `ActorMethodVoteSource` during REPLAY before frontier.

Tests must include a spy or counter that fails if `consensus_vote(proposal)` is called during REPLAY consumption.

### 13.4 tests/test_consensus_replay_p3c.py

Create a new test file:

```text
tests/test_consensus_replay_p3c.py
```

Required tests:

```text
test_live_consensus_writes_event_v2_with_votes_map
test_replay_consumes_matching_consensus_event_v2
test_replay_re_evaluates_proposal_inputs
test_replay_uses_recorded_votes_through_explicit_source
test_replay_does_not_call_actor_consensus_vote
test_replay_does_not_call_actor_method_vote_source
test_replay_advances_cursor_once_without_double_advance
test_replay_does_not_append_consensus_event_before_frontier
test_replay_type_mismatch_before_frontier_fails_closed
test_replay_frontier_executes_live_and_appends_event_v2
test_replay_malformed_event_fails_closed
test_replay_unsupported_v1_event_fails_closed
test_replay_proposal_id_mismatch_fails_closed
test_replay_statement_identity_mismatch_fails_closed
test_replay_votes_hash_mismatch_fails_closed
test_replay_result_hash_mismatch_fails_closed
test_replay_recorded_votes_malformed_fails_closed
test_replay_source_label_difference_does_not_change_votes_hash
test_replay_source_mutation_changes_proposal_id_and_fails_closed
```

Tests should exercise the real snapshot/replay mechanism where feasible.

Tests must not rely only on manually mocked `execution_history` when an end-to-end snapshot/replay path is practical.

### 13.5 Tests that must remain unchanged

Existing P3a/P3b tests must continue to pass.

Existing actor-method vote tests must still prove:

```text
default NullVoteSource preserved
actor-method voting explicit opt-in
explicit VoteSource override precedence
side-effect guard fail-closed
proposal view mutation fail-closed
source labels advisory only
P3a hash compatibility
```

Implementation must not weaken existing P3a/P3b hash-equivalence tests.

### 13.6 Full-suite gate

Implementation PR must report:

```text
targeted P3a/P3b/P3c tests
collective regression tests
full suite result
new_failures = []
```

If full suite has known baseline failures in an environment, the PR body must record:

```text
base failures
head failures
new_failures = []
```

---

## 14. Acceptance Criteria

P3c-0 implementation is accepted only when all criteria are met:

```text
LIVE writes distributed_consensus_decided with schema_version consensus.event.v2.
LIVE event v2 includes normalized votes map.
votes_hash still excludes source labels.
REPLAY consumes distributed_consensus_decided from execution_history before frontier.
REPLAY respects existing replay skip-list semantics.
REPLAY transitions to LIVE at replay frontier.
REPLAY does not double-advance replay_cursor.
REPLAY does not append distributed_consensus_decided before frontier.
REPLAY does not call ActorMethodVoteSource.
REPLAY does not call actor consensus_vote.
REPLAY does not call live VoteSource selection.
REPLAY does not fall back to NullVoteSource for missing replay votes.
REPLAY uses recorded votes.
REPLAY binds engine-produced result.
REPLAY verifies proposal_id.
REPLAY verifies statement_identity.
REPLAY verifies votes_hash.
REPLAY verifies result_hash.
REPLAY fails closed on wrong event type before frontier.
REPLAY fails closed on malformed event before frontier.
REPLAY fails closed on unsupported v1 event before frontier.
REPLAY fails closed on proposal_id mismatch.
REPLAY fails closed on statement_identity mismatch.
REPLAY fails closed on votes_hash mismatch.
REPLAY fails closed on result_hash mismatch.
REPLAY fails closed on malformed recorded votes.
REPLAY mutates no side-effect stores before frontier.
Mailbox voting remains unimplemented.
Promise vote completion remains unimplemented.
Signal-injected vote resolution remains unimplemented.
Ticket lifecycle remains unimplemented.
Network/daemon voting remains unimplemented.
Live LLM voting remains unimplemented.
Parser/AST/lexer remain unchanged.
Durable allowlist remains unchanged.
Existing P3a/P3b tests still pass.
```

---

## 15. Stop Gates

Implementation must stop if any of the following becomes necessary:

```text
BLOCKED — VOTE_RECOLLECTION_IN_REPLAY
BLOCKED — ACTOR_METHOD_CALL_IN_REPLAY
BLOCKED — LIVE_VOTESOURCE_IN_REPLAY
BLOCKED — NULL_VOTESOURCE_FALLBACK_IN_REPLAY
BLOCKED — MAILBOX_PROMISE_NETWORK_IN_P3C0
BLOCKED — LIVE_LLM_IN_REPLAY
BLOCKED — STATE_MUTATION_DURING_REPLAY
BLOCKED — HISTORY_APPEND_DURING_REPLAY
BLOCKED — REPLAY_CURSOR_DOUBLE_ADVANCE
BLOCKED — PARSER_AST_EXPANSION
BLOCKED — DURABLE_ALLOWLIST_EXPANSION
BLOCKED — TICKET_LIFECYCLE_IN_P3C0
BLOCKED — EVENT_V2_WITHOUT_VOTES_MAP
BLOCKED — RECORDED_VOTES_MALFORMED
BLOCKED — PROPOSAL_ID_RECOMPUTE_MISMATCH
BLOCKED — STATEMENT_IDENTITY_MISMATCH_ON_REPLAY
BLOCKED — RESULT_RECONSTRUCTION_OUTSIDE_ENGINE
BLOCKED — SOURCE_LABEL_ENTERED_VOTES_HASH
BLOCKED — PRODUCTION_CONSENSUS_CLAIM
```

Stop gates must not be bypassed.

---

## 16. Evidence Requirements

After implementation merge, a separate evidence PR must add:

```text
docs/evidence/P3C_EVIDENCE.md
```

The evidence document must record:

```text
implementation PR
implementation branch
base SHA
head SHA
merge SHA
changed files
diff size
targeted test counts
full-suite result
new_failures = []
post-merge replay artifact
review verdict
capability impact
explicit non-claims
```

The evidence must include a replay artifact demonstrating:

```text
LIVE execution wrote event v2 with votes map
snapshot/history was replayed
REPLAY consumed matching event before frontier
replay_cursor advanced without double-advance
actor vote method was not called
result_hash matched
votes_hash matched
result binding succeeded
no duplicate event was appended before frontier
frontier-to-LIVE behavior remained intact
```

The evidence must also prove:

```text
existing P3a tests still pass
existing P3b actor-method tests still pass
collective regression tests still pass
new_failures = []
```

Capability matrix update is not part of RFC draft.

Capability matrix may be updated only after implementation and evidence closure.

---

## 17. Capability Matrix Impact

After RFC draft:

```text
No matrix change.
```

After P3c-0 implementation and evidence closure, distributed consensus maturity may become:

```text
Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed
```

It must not become:

```text
Production
```

because P3c-0 still does not close:

```text
mailbox vote collection
promise vote completion
signal-injected vote resolution
stateful consensus ticket lifecycle
daemon/network vote transport
live LLM vote production
production distributed consensus protocol behavior
```

---

## 18. Explicit Non-Claims

P3c-0 does not claim:

```text
Production distributed consensus
Raft
Paxos
Tendermint
PBFT
Byzantine fault tolerance
network replication
leader election
view changes
node membership protocol
quorum replication
daemon transport
mailbox-backed consensus
promise-backed consensus
LLM-assisted consensus
stateful consensus ticket lifecycle
automatic v1 consensus event migration
degraded v1 replay compatibility
source-layout-stable consensus identity
```

P3c-0 is strictly a local durable/replay closure for already recorded consensus decisions and replay-frontier-safe continuation into LIVE execution.

---

## 19. Security and Integrity Notes

### 19.1 Tamper-evidence

P3c-0 uses:

```text
votes_hash
result_hash
history_hash
history_chain
```

as integrity anchors.

If a recorded votes map is altered, recomputed `votes_hash` must mismatch.

If outcome/reason/counts or result preimage semantics are altered, recomputed `result_hash` must mismatch.

### 19.2 No hidden live actor execution

P3c-0 must guarantee that replay before frontier does not call `consensus_vote(proposal)`.

A malicious or changed actor method must not be able to alter replayed consensus outcome.

### 19.3 No silent legacy upgrade

Event v1 must not be silently treated as v2.

There is no default degraded v1 projection in P3c-0.

There is no automatic v1-to-v2 migration in P3c-0.

### 19.4 No source-label semantic drift

Source labels remain provenance-only. They must not influence canonical vote hashes.

### 19.5 Source-layout-sensitive identity known limitation

`source:{line}:{column}` remains the current statement identity.

This is source-layout-sensitive.

P3c-0 does not solve that limitation.

A future RFC may define structural statement identity.

### 19.6 Proposal-input nondeterminism known limitation

P3c-0 assumes proposal inputs are deterministic under replay.

If proposal input expressions depend on nondeterministic runtime state outside existing replay contracts, P3c-0 may fail closed through `proposal_id` or `statement_identity` mismatch.

This is intentional.

---

## 20. Review Checklist

Reviewers must verify:

```text
RFC only changes docs/RFC-CONSENSUS-P3C.md during draft PR.
Approval PR is separate.
Implementation PR is separate.
No code change before approval.
Event v2 includes votes map.
Replay path uses recorded votes.
Replay path respects frontier-to-LIVE behavior.
Replay path respects existing skip-list semantics.
Replay path does not call actor vote methods.
Replay path is engine-owned for result/hash semantics.
Legacy v1 behavior is fail-closed by default before frontier.
No degraded v1 projection is claimed.
Stop gates are complete.
Open-source rationale is present.
Acceptance tests are explicit.
Matrix is not changed in RFC draft.
```

---

## 21. Future Work

Future explicitly approved stages may address:

```text
P3c-1 — durable consensus ticket lifecycle
P3c-2 — DurablePromise-backed vote completion
P3c-N — mailbox-backed vote delivery and receive-based vote collection
P3d — LLM-assisted voting
future RFC — network/daemon/protocol behavior
future RFC — parser / AST / lexer vote syntax
future RFC — source-layout-stable statement identity
future RFC — event v1 compatibility projection or migration tooling
future RFC — production distributed consensus protocol claims
```

None of these are authorized by P3c-0.

---

## 22. Proposed PR Plan

### RFC draft PR

Title:

```text
docs(rfc): draft RFC-CONSENSUS-P3C durable replay closure contract
```

Suggested branch:

```text
docs/rfc-consensus-p3c-durable-replay-closure
```

Allowed file:

```text
docs/RFC-CONSENSUS-P3C.md
```

Forbidden files:

```text
synapse/**
tests/**
docs/CAPABILITY_MATURITY_MATRIX.md
docs/evidence/**
examples/**
.github/workflows/**
AGENTS.md
docs/agent-guides/**
```

### Approval PR

After team review, a separate approval PR may update `docs/RFC-CONSENSUS-P3C.md` status to approved and authorize P3c-0 implementation.

### Implementation PR

Only after approval, implementation PR may modify:

```text
synapse/runtime/consensus_engine.py
synapse/interpreter.py
tests/test_consensus_replay_p3c.py
```

### Evidence PR

After implementation merge, a separate evidence PR may add:

```text
docs/evidence/P3C_EVIDENCE.md
```

and may update:

```text
docs/CAPABILITY_MATURITY_MATRIX.md
```

only if evidence supports the capability wording.

---

## 23. Final Recommendation

Approve the RFC draft direction:

```text
OPTION A — P3c-0 replay consumption only.
```

Approve the core event decision:

```text
consensus.event.v2 = consensus.event.v1 + normalized votes map
```

Approve the core replay decision:

```text
Replay deterministic proposal inputs from program.
Consume recorded consensus.event.v2 before replay frontier.
Use recorded votes through engine-owned deterministic reducer path.
Verify proposal_id, statement_identity, votes_hash, and result_hash.
Bind engine-produced result.
Advance replay_cursor exactly once through approved replay primitive semantics.
Append no new event before replay frontier.
Transition to LIVE at replay frontier.
```

Do not approve any mailbox, promise, signal, network, daemon, LLM, ticket, parser, AST, lexer, durable allowlist, v1 compatibility projection, or production distributed consensus work in P3c-0.
