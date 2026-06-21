# Read-only audit P3c-2 / P3c-N

**AUDIT_ID:** AUDIT-P3C2-P3CN-READONLY  
**TARGET_REPO:** Kirrichh/Synapse  
**AUDIT_BASE_SHA:** `0f0f6c9669715cdedec7e2a2efef5e6b3c6ac72e` — `main` after PR #41 P3c-1 evidence correction merged  
**PREVIOUS_IMPLEMENTATION_SHA:** `88210654223b19a52bfddf9f3715e1a95af90367` — PR #39 P3c-1 implementation merge  
**PREVIOUS_SCOPE_CLOSED:** P3c-1 durable consensus ticket creation and replay  
**SCOPE:** read-only audit result — documentation-only record, no runtime/code mutation  
**NEXT_CANDIDATE_ARTIFACTS:**

1. `docs/RFC-CONSENSUS-P3C2.md` — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
2. `docs/RFC-CONSENSUS-P3CN.md` — Mailbox-backed Vote Delivery and Receive-based Vote Collection, deferred until P2 durable lifecycle expansion

---

## 0. Audit Purpose

This audit determines the correct next architectural stage after P3c-1.

P3c-1 closed the creation and replay of durable consensus tickets. That closed scope answers the question:

```text id="aud-p3c2-purpose-001"
How does the runtime create a deterministic durable ticket when a consensus decision
is deferred because participant votes are missing, and how does replay consume that
ticket without inventing state or recollecting votes?
```

The next unresolved question is different:

```text id="aud-p3c2-purpose-002"
How does the runtime resolve a previously-created durable consensus ticket,
accept the missing votes through an already-supported durable boundary,
compute the final consensus outcome deterministically,
record the resolution in durable history,
update projection state,
and replay the same resolution without live side effects?
```

This audit compares two possible next directions:

```text id="aud-p3c2-purpose-003"
P3c-2 — durable consensus ticket resolution via existing P2 resume boundary
P3c-N — mailbox-backed vote delivery and receive-based vote collection
```

The audit determines that these two directions must remain separate because they depend on different durable lifecycle contracts.

---

## 1. Executive Summary

P3c-2 and P3c-N must not be combined.

P3c-2 is the correct next consensus stage because it can be defined on top of existing P2 durable execution boundaries without expanding P2.

P3c-N must be deferred because mailbox-backed vote delivery requires durable mailbox wait semantics, and the current P2 durable execution contract does not support the necessary suspension reasons.

The decisive technical fact is the supported P2 suspension reason set.

P2 supports:

```text id="aud-p3c2-summary-001"
awaiting_external_signal
awaiting_promise
awaiting_llm
```

P2 does not support:

```text id="aud-p3c2-summary-002"
awaiting_message
awaiting_message_or_timeout
```

The runtime enforces unsupported durable suspension reasons with exit code `25` and error code:

```text id="aud-p3c2-summary-003"
UNSUPPORTED_DURABLE_OPERATION_OR_REASON
```

Therefore:

```text id="aud-p3c2-summary-004"
P3c-2 can proceed after audit acceptance and RFC approval.
P3c-N is blocked until a separate P2 durable lifecycle expansion is approved.
```

The next stage should be:

```text id="aud-p3c2-summary-005"
P3c-2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
```

The deferred later stage should be:

```text id="aud-p3c2-summary-006"
P3c-N — Mailbox-backed Vote Delivery and Receive-based Vote Collection
```

---

## 2. Sources of Audit Truth

### 2.1 Normative Repository Documents

The following committed repository documents are normative for this audit:

```text id="aud-p3c2-sources-001"
docs/RFC-CONSENSUS-P3.md
docs/RFC-CONSENSUS-P3B.md
docs/RFC-CONSENSUS-P3C.md
docs/RFC-CONSENSUS-P3C1.md
docs/RFC-CONSENSUS-P3C1_APPROVAL.md
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
docs/RFC-ASYNC-EXECUTION.md
docs/ASYNC_DURABLE_EXECUTION_STATUS.md
```

`docs/evidence/P3C_EVIDENCE.md` is the accumulated P3c evidence document.

All future P3c evidence for P3c-2 must be recorded by updating the accumulated P3c evidence document.

No separate per-substage P3c evidence document is authorized by this audit.

### 2.2 External Architecture References

The following Gold / Verification documents are available to the team as architecture references. They may inform terminology, design intent, product framing, and long-range Verification-chain reasoning, but they are not treated as committed repository source-of-truth unless they are added to the repository by a separate approved documentation PR:

```text id="aud-p3c2-sources-002"
SYNAPSE_GOLD_EXECUTION_ARCHITECTURE_SPEC.md
SYNAPSE_GOLD_ARITHMETIC_MODEL_SPEC.md
SYNAPSE_GOLD_V0_1_ERRATA.md
```

For repository-scoped RFCs, normative implementation constraints must be grounded in committed repository files and current code.

If the RFC cites Gold / Verification documents, it must label them as external architecture references unless the documents have been committed into the repository by the time the RFC is drafted.

### 2.3 Non-Normative External References

This audit is aligned with durable execution and event-sourced replay patterns used in systems such as Temporal Workflow Execution, Azure Durable Functions external events and deterministic orchestrators, Restate-style journaled execution, and DBOS-style durable workflow execution.

These external references are non-normative. They support the general pattern:

```text id="aud-p3c2-sources-003"
external signal or awaited result is recorded durably;
replay consumes recorded history instead of repeating live side effects;
workflow/orchestrator code must be deterministic across replay;
duplicate external delivery is a real operational concern and requires idempotent handling.
```

They do not override repository RFCs or current code.

### 2.4 Code Anchors Verified

The audit reviewed the following code surfaces at `AUDIT_BASE_SHA`:

```text id="aud-p3c2-sources-004"
synapse/runtime/consensus_engine.py
synapse/interpreter.py
synapse/runtime/actor_runtime.py
synapse/application.py
synapse/builtins.py
tests/test_consensus_replay_p3c.py
tests/test_durable_execution.py
```

Verified facts:

```text id="aud-p3c2-sources-005"
application.py defines _SUPPORTED_SUSPENSION_REASONS as awaiting_external_signal,
awaiting_promise, awaiting_llm.

application.py maps unsupported durable suspension after resume to exit_code 25
and UNSUPPORTED_DURABLE_OPERATION_OR_REASON.

interpreter.py SuspendExpr uses awaiting_external_signal and keeps Suspension.payload
as {"promise_id": ..., "request": request_value}.

interpreter.py contains consensus_tickets projection storage.

consensus_engine.py creates P3c-1 ticket payload with votes, vote_counts,
missing_participants, votes_hash, strategy, policy, quorum, and timeout.
```

---

## 3. What P3c-1 Closed

P3c-1 closed durable consensus ticket creation and replay.

The closed scope includes:

```text id="aud-p3c2-p3c1-001"
deterministic ticket_id generation through consensus.ticket.v1 preimage
engine-owned ticket_payload construction
adjacent LIVE append of distributed_consensus_decided and distributed_consensus_ticket_created
deferred-ticket invariant preflight before any LIVE history append
raw-adjacent two-event replay consumption
closed-schema validation of distributed_consensus_ticket_created
replay cursor rollback on ticket validation or projection failure
consensus_tickets projection from durable history
fail-closed legacy deferred history without adjacent ticket_created
```

P3c-1 explicitly did not close:

```text id="aud-p3c2-p3c1-002"
ticket resolution
ticket finalization
ticket cancellation
ticket expiration
ticket lifecycle state machine
public ticket API
mailbox-backed vote delivery
DurablePromise-backed vote completion
signal-injected vote resolution
network-backed vote transport
daemon-backed vote transport
live LLM vote production
parser / AST / lexer expansion
production distributed consensus protocol behavior
overall P3c closure
```

The gap P3c-2 must close is durable resolution of pending consensus tickets.

A pending consensus ticket after P3c-1 is not an error. It is a valid durable projection of a deferred consensus result. P3c-2 must define how that pending ticket is later resolved through a supported durable boundary.

---

## 4. Code Fact: ConsensusEngine Owns Ticket Creation Today

At audit base, `ConsensusEngine.decide()` owns P3c-1 ticket construction.

For deferred consensus with:

```text id="aud-p3c2-engine-001"
outcome = deferred
reason = pending_missing_votes
```

the engine already:

```text id="aud-p3c2-engine-002"
derives missing_participants
builds consensus.ticket.v1 ticket preimage
derives deterministic ticket_id
builds distributed_consensus_ticket_created payload
sets result["ticket_id"]
returns ticket_id and ticket_payload through ConsensusDecision
```

This establishes the ownership rule for P3c-2:

```text id="aud-p3c2-engine-003"
ConsensusEngine must own final consensus resolution mathematics.
Interpreter must not own final consensus mathematics.
```

P3c-2 must not move outcome derivation, votes hashing, result hashing, final vote counting, final reason derivation, or final consensus reduction into the interpreter adapter.

The engine may call helpers from a resolution-specific runtime module for schema constants and validation helpers, but the engine remains the canonical owner of deterministic consensus mathematics.

---

## 5. Code Fact: Interpreter Owns Append / Replay / Projection Adapter Work Today

At audit base, `Interpreter` owns the runtime adapter around the engine:

```text id="aud-p3c2-interpreter-001"
construct ConsensusRequest
select VoteSource
call ConsensusEngine.decide
append distributed_consensus_decided
append distributed_consensus_ticket_created for deferred pending_missing_votes
project ticket into consensus_tickets
consume replayed distributed_consensus_decided
consume replayed adjacent distributed_consensus_ticket_created
rollback cursor and projection on ticket replay failure
bind initial result into program environment
```

This establishes the P3c-2 adapter rule:

```text id="aud-p3c2-interpreter-002"
Interpreter may remain the adapter for runtime interaction.
Interpreter must not become the owner of resolution domain logic.
```

For P3c-2, the interpreter may:

```text id="aud-p3c2-interpreter-003"
detect a consensus ticket resolution signal carried by the existing suspend request/result channel
look up the pending ticket projection
delegate validation and schema checks to a resolution module
delegate final outcome computation to ConsensusEngine
append the returned resolution event
update projection through a helper
perform replay cursor/projection rollback
```

The interpreter must not:

```text id="aud-p3c2-interpreter-004"
own resolution event schema
own resolution field constants
own final votes hash construction
own final result hash construction
own ticket lifecycle semantics
own resolution vote coverage rules
introduce new parser/AST execution semantics
introduce new P2 suspension reasons
modify P2 durable artifact schema
modify P2 exit code mapping
```

This audit therefore requires P3c-2 to introduce a dedicated resolution module if implementation proceeds.

---

## 6. Code Fact: Existing External-Signal Boundary Is `SuspendExpr`

At audit base, the existing P2 external-signal path is not a ticket-specific construct.

The existing `suspend_expression()` follows this lifecycle:

```text id="aud-p3c2-suspend-001"
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

This is the supported path P3c-2 must reuse if P3c-2 is not allowed to add syntax, parser nodes, AST nodes, lexer tokens, public APIs, or P2 suspension reasons.

Therefore, P3c-2 must not require a new ticket-specific suspension payload shape like:

```text id="aud-p3c2-suspend-002"
Suspension.payload = {
    "kind": "consensus_ticket_resolution",
    "ticket_id": "...",
    "missing_participants": [...],
    "votes_hash": "sha256:..."
}
```

The existing `Suspension.payload` shape for `SuspendExpr` must remain:

```text id="aud-p3c2-suspend-003"
{
  "promise_id": <promise_id>,
  "request": <request_value>
}
```

P3c-2 may define a strict JSON convention inside `request_value` and inside the injected signal result.

That means the correct P3c-2 transport shape is:

```text id="aud-p3c2-suspend-004"
SuspendExpr request_value carries consensus-ticket-resolution request metadata.
Injected signal carries consensus-ticket-resolution response data.
Interpreter detects the convention after resume and delegates resolution.
```

This preserves the current P2 boundary and avoids unauthorized interpreter semantic expansion.

---

## 7. Code Fact: P2 Supported Suspension Reasons Gate the Stage Split

At audit base, P2 durable execution supports exactly the following reasons:

```text id="aud-p3c2-p2-001"
awaiting_external_signal
awaiting_promise
awaiting_llm
```

It does not support:

```text id="aud-p3c2-p2-002"
awaiting_message
awaiting_message_or_timeout
```

If a resumed run produces an unsupported suspension reason, runtime writes an ERROR artifact with:

```text id="aud-p3c2-p2-003"
exit_code = 25
error_code = UNSUPPORTED_DURABLE_OPERATION_OR_REASON
```

This creates the strict split:

```text id="aud-p3c2-p2-004"
P3c-2 may use existing supported P2 reasons.
P3c-N requires unsupported mailbox wait reasons and is blocked.
```

---

## 8. Code Fact: Mailbox Mechanics Exist but Are Not P2-Durable for Waiting

`ActorRuntime` already has local mailbox mechanics:

```text id="aud-p3c2-mailbox-001"
send_message
message_sent
message_forwarded
message_received
receive_timeout
evaluate_receive
evaluate_async_receive
```

However, async receive emits:

```text id="aud-p3c2-mailbox-002"
awaiting_message
awaiting_message_or_timeout
```

when the mailbox is empty.

Those reasons are unsupported by P2 durable execution. Therefore, mailbox-backed vote delivery cannot be part of P3c-2.

Mailbox-backed vote delivery belongs to P3c-N and requires a prior P2 durable lifecycle expansion.

---

## 9. Code Fact: DurablePromise Primitives Exist but Do Not Authorize Broader Transport

The runtime already has:

```text id="aud-p3c2-promise-001"
DurablePromise
create_durable_promise
resolve_promise
resolve_promise_location
build_resolve_promise_packet
emit_or_apply_promise_resolution
```

These primitives are relevant to ticket resolution and future automation, but their existence does not authorize network transport, daemon transport, mailbox vote delivery, or new P2 lifecycle behavior.

P3c-2 may use existing P2-supported external-signal and promise mechanics only if the design remains inside the already-supported P2 contract.

If automatic promise-backed routing requires remote forwarding, network transport, daemon transport, or mailbox wait semantics, implementation must stop.

---

## 10. P3c-2 vs P3c-N Architectural Split

### 10.1 P3c-2

P3c-2 is the next consensus substage.

It is authorized only after RFC approval.

It should define:

```text id="aud-p3c2-split-001"
durable ticket resolution event
resolution request convention over existing SuspendExpr request_value
resolution signal convention over existing P2 resume signal
engine-owned final consensus recomputation
closed-schema replay consumption of resolution event
projection transition from pending to resolved
idempotency through existing P2 suspension_id + signal_value_hash
```

It must not define:

```text id="aud-p3c2-split-002"
mailbox-backed vote delivery
network-backed transport
daemon-backed transport
new P2 suspension reasons
new parser syntax
new AST nodes
new lexer tokens
public ticket API
production distributed consensus protocol behavior
overall P3c closure
```

### 10.2 P3c-N

P3c-N is deferred.

It should eventually define:

```text id="aud-p3c2-split-003"
mailbox-backed vote delivery
receive-based vote collection
mailbox wait replay
mailbox timeout semantics
possible scheduler timeout semantics
possible transport/daemon interaction
```

P3c-N is blocked until P2 supports durable mailbox waiting.

### 10.3 Why Combining Them Is Not Allowed

Combining P3c-2 and P3c-N would mix:

```text id="aud-p3c2-split-004"
ticket resolution
P2 durable lifecycle expansion
mailbox wait semantics
receive timeout semantics
possibly scheduler/daemon/network semantics
```

These are separate product contracts with separate failure semantics.

They require separate RFCs and separate approval gates.

### 10.4 Multiple Concurrent Tickets

A program may create multiple deferred consensus tickets.

P3c-2 must support multiple pending tickets in `consensus_tickets`.

Each ticket must be independently resolvable by `ticket_id`.

The resolution of one ticket must not mutate, resolve, invalidate, or reorder another pending ticket.

P3c-2 must preserve these invariants:

```text id="aud-p3c2-split-005"
each deferred consensus ticket has a deterministic unique ticket_id;
each resolution signal targets exactly one ticket_id;
each distributed_consensus_ticket_resolved event references exactly one ticket_id;
resolving ticket A must not alter pending ticket B;
replay must reconstruct the same independent projection state for each ticket.
```

Multiple concurrent tickets are a P3c-2 requirement, not a P3c-N feature.

P3c-N concerns delivery of votes via mailbox/receive; P3c-2 concerns resolution of already-created tickets through existing P2-supported boundaries.

---

## 11. Future P2 Expansion Considerations

P3c-N is deferred because P2 does not currently support durable mailbox wait semantics.

If P2 durable lifecycle is later expanded to support:

```text id="aud-p3c2-future-p2-001"
awaiting_message
awaiting_message_or_timeout
mailbox wait replay
mailbox timeout durable boundary
signal inbox or durable mailbox inbox
scheduler timeout semantics
```

then P3c-N may be revisited by a separate RFC.

Such an expansion would require:

```text id="aud-p3c2-future-p2-002"
new or expanded P2 suspension reason registration
P2 artifact compatibility analysis
P2 replay semantics for mailbox waits
P2 timeout semantics
P2 failure and idempotency behavior
separate approval gate before any P3c-N implementation depends on it
```

This audit does not evaluate the feasibility, cost, implementation design, or product desirability of P2 mailbox lifecycle expansion.

This audit only records that current P2 does not support the reasons required by P3c-N.

---

## 12. Required P3c-2 Design Decisions

### 12.1 Ticket Lifecycle Vocabulary

P3c-1 avoided a durable `status` field.

P3c-2 should continue this discipline.

Recommended rule:

```text id="aud-p3c2-design-001"
distributed_consensus_ticket_created means pending by construction.
distributed_consensus_ticket_resolved means resolved by construction.
```

Projection may store:

```text id="aud-p3c2-design-002"
projection_state = "pending"
projection_state = "resolved"
```

But durable event payloads must not include `status` or `projection_state`.

### 12.2 Resolution Is Terminal

A successful P3c-2 resolution is terminal.

The only P3c-2 transition is:

```text id="aud-p3c2-design-003"
pending -> resolved
```

P3c-2 does not support:

```text id="aud-p3c2-design-004"
resolved -> pending
resolved -> cancelled
resolved -> expired
resolved -> failed
resolved -> finalized
re-resolution with different votes
```

Duplicate resolution with identical semantic content may be treated as an idempotent no-op. Conflicting duplicate resolution must fail closed.

### 12.3 Resolution Event

Recommended event type:

```text id="aud-p3c2-design-005"
distributed_consensus_ticket_resolved
```

This event represents the later durable act of resolving an already-created ticket.

It is not adjacent to `distributed_consensus_ticket_created`.

Expected lifecycle shape:

```text id="aud-p3c2-design-006"
distributed_consensus_decided
distributed_consensus_ticket_created
... arbitrary later durable history ...
distributed_consensus_ticket_resolved
```

### 12.4 Resolution Transport

P3c-2 should use the existing P2 external-signal boundary through `SuspendExpr`.

Correct transport shape:

```text id="aud-p3c2-design-007"
existing SuspendExpr request_value:
{
  "kind": "consensus_ticket_resolution",
  "ticket_id": "...",
  "missing_participants": [...],
  "votes_hash": "..."
}

existing injected signal value:
{
  "kind": "consensus_ticket_resolution",
  "ticket_id": "...",
  "votes": {
    "participant_a": "yes",
    "participant_b": "abstain"
  }
}
```

`Suspension.payload` itself remains the existing P2 shape:

```text id="aud-p3c2-design-008"
{
  "promise_id": "...",
  "request": request_value
}
```

### 12.5 Public Binding

P3c-2 must not automatically re-bind the original variable that received the deferred result.

The original binding remains:

```text id="aud-p3c2-design-009"
outcome = deferred
reason = pending_missing_votes
ticket_id = <ticket_id>
```

Resolution is observable through:

```text id="aud-p3c2-design-010"
execution_history
consensus_tickets projection
replay reconstruction
```

A future public ticket observation API requires its own RFC.

### 12.6 Suspend Return Value

P3c-2 must not change the meaning of `SuspendExpr`.

If a consensus-ticket-resolution convention is carried through `SuspendExpr`, the expression still returns the injected signal value unchanged.

Resolution side effects are:

```text id="aud-p3c2-design-011"
distributed_consensus_ticket_resolved history event
consensus_tickets projection update
```

Return value is:

```text id="aud-p3c2-design-012"
the original injected signal value
```

### 12.7 Idempotency

P3c-2 must reuse P2 idempotency:

```text id="aud-p3c2-design-013"
suspension_id + signal_value_hash
```

It must not introduce a competing primary idempotency surface such as:

```text id="aud-p3c2-design-014"
ticket_id + vote_value_hash
```

However, P3c-2 must also defend against duplicate domain-level resolution attempts.

If a resolution signal is received for an already resolved ticket:

```text id="aud-p3c2-design-015"
If incoming resolution votes match the already resolved final votes and final hashes,
the operation is idempotent and must not append a duplicate event.

If incoming resolution votes conflict with the existing resolved projection,
the operation must fail closed.
```

---

## 13. Replay Resolution Matching

Resolution event matching must be strict.

The resolution event is not adjacent to `distributed_consensus_ticket_created`.

However, when replay reaches the resolution boundary, the replay path must not arbitrarily search across unrelated semantic events until it finds a matching `ticket_id`.

Correct rule:

```text id="aud-p3c2-replay-001"
At the resolution boundary, replay extracts ticket_id from trusted request_value.
Replay may skip only events that are already classified by the existing replay policy
as replay-skippable metadata.
The next replay-significant event consumed for this boundary must be
distributed_consensus_ticket_resolved with the same ticket_id.
If another semantic event appears first, replay must fail closed.
```

This ensures correct event consumption when:

```text id="aud-p3c2-replay-002"
multiple pending tickets exist;
arbitrary intermediate history exists between ticket creation and ticket resolution;
resolution events are not adjacent to ticket creation;
interleaved ticket resolutions occur.
```

The trusted `ticket_id` for matching must be extracted from `request_value`, not from the untrusted injected signal.

The injected signal’s `ticket_id` is used only as a consistency check against the request value.

---

## 14. Ticket Payload Contract

P3c-2 resolution logic depends on the P3c-1 ticket payload.

The ticket payload passed to resolution logic is derived from `distributed_consensus_ticket_created` and the `consensus_tickets` projection.

It must contain:

```json id="aud-p3c2-ticket-payload-001"
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

P3c-2 must use the existing `votes` field from P3c-1.

P3c-2 must not rename it to `known_votes`.

The `votes` map contains known votes and missing markers at the time the ticket was created.

The `missing_participants` list defines exactly which participants must be resolved by the later signal.

If implementation cannot rely on this structure, it must stop.

Stop gate:

```text id="aud-p3c2-ticket-payload-002"
BLOCKED — TICKET_PAYLOAD_STRUCTURE_UNDEFINED
```

---

## 15. Projection Consistency Invariant

Projection state must be derived from durable history.

For every `ticket_id` in `consensus_tickets`:

```text id="aud-p3c2-projection-001"
If projection_state == "pending":
  distributed_consensus_ticket_created exists in history.
  distributed_consensus_ticket_resolved does not exist in history for the same ticket_id.

If projection_state == "resolved":
  distributed_consensus_ticket_created exists in history.
  distributed_consensus_ticket_resolved exists in history.
  Both events have matching ticket_id.
  Both events have matching proposal_id.
  Both events have matching statement_identity.
```

If replay reconstructs a projection that violates this invariant, replay must fail closed.

Stop gate:

```text id="aud-p3c2-projection-002"
BLOCKED — PROJECTION_STATE_HISTORY_MISMATCH
```

---

## 16. Hash Computation Requirements

P3c-2 must reuse the P3a/P3c canonical hash profile.

### 16.1 Final Votes Hash

`votes_hash_final` must be computed from an ordered participant list, not from an unordered JSON object.

Required preimage shape:

```json id="aud-p3c2-hash-001"
{
  "schema_version": "consensus.votes.v1",
  "votes": [
    ["A", "yes"],
    ["B", "no"],
    ["C", "abstain"]
  ]
}
```

Ordering must follow the canonical participant order established by the consensus proposal.

### 16.2 Final Result Hash

`result_hash_final` must mirror the existing P3a result preimage structure:

```json id="aud-p3c2-hash-002"
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

`result_hash_final` must not introduce a new hash profile unless a future RFC explicitly approves that change.

Interpreter must not compute these hashes.

ConsensusEngine owns hash computation.

---

## 17. Interpreter Boundary Rule

P3c-2 must explicitly prevent interpreter monolith expansion.

The correct ownership model is:

```text id="aud-p3c2-boundary-001"
ConsensusEngine:
  owns final consensus mathematics

synapse/runtime/consensus_ticket_resolution.py:
  owns resolution event constants, resolution signal validation,
  closed-schema event validation, projection helper functions,
  terminal duplicate-resolution checks, and stable validation errors

Interpreter:
  owns only adapter operations:
    detect convention
    extract trusted ticket_id from request_value
    call module validators
    call engine
    append event
    update projection
    rollback cursor/projection on replay failure
```

Any implementation that moves resolution domain logic into `Interpreter` must stop.

Stop gate:

```text id="aud-p3c2-boundary-002"
BLOCKED — INTERPRETER_MONOLITH_EXPANSION_FOR_TICKET_RESOLUTION
```

---

## 18. Required Stop Gates for RFC-CONSENSUS-P3C2

The RFC must include at least:

```text id="aud-p3c2-stop-001"
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
BLOCKED — PRODUCTION_PROTOCOL_CLAIM
BLOCKED — CAPABILITY_OVERCLAIM
BLOCKED — MATRIX_UPDATE_ATTEMPTED_BEFORE_EVIDENCE
BLOCKED — EVIDENCE_CREATED_BEFORE_IMPLEMENTATION_MERGE
BLOCKED — SUSPEND_EXPR_PAYLOAD_SHAPE_CHANGE_REQUIRED
BLOCKED — PROJECTION_STATE_HISTORY_MISMATCH
BLOCKED — TICKET_PAYLOAD_STRUCTURE_UNDEFINED
BLOCKED — DUPLICATE_RESOLUTION_APPENDS_SECOND_EVENT
BLOCKED — CONFLICTING_DUPLICATE_RESOLUTION
BLOCKED — RESOLUTION_EVENT_MATCHING_AMBIGUOUS
BLOCKED — HASH_PROFILE_MISMATCH
```

---

## 19. Evidence Policy

After P3c-2 implementation merge, evidence must be appended to the existing accumulated file:

```text id="aud-p3c2-evidence-001"
docs/evidence/P3C_EVIDENCE.md
```

The capability matrix may be updated only after evidence closure.

No separate per-substage P3c evidence document is authorized by this audit.

Evidence must preserve the accumulated P3c evidence model.

---

## 20. Recommended Next Artifact Sequence

1. Add `docs/audit/P3C2_P3CN_AUDIT.md`
2. Draft `docs/RFC-CONSENSUS-P3C2.md`
3. Approval-gate PR for RFC-CONSENSUS-P3C2
4. Implementation PR for P3c-2
5. Evidence PR updating:
   - `docs/evidence/P3C_EVIDENCE.md`
   - `docs/CAPABILITY_MATURITY_MATRIX.md`

Parallel implementation work on P3c-N is not authorized by this audit.

---

## 21. Audit Findings Summary

| ID | Severity | Finding | Status |
|---|---|---|---|
| AUDIT-P3C2-01 | EVIDENCE_BACKED | P3c-2 is the correct next consensus stage | Accepted |
| AUDIT-P3C2-02 | EVIDENCE_BACKED | P3c-N is blocked by unsupported P2 mailbox reasons | Accepted |
| AUDIT-P3C2-03 | EVIDENCE_BACKED | P2 idempotency must be reused | Accepted |
| AUDIT-P3C2-04 | EVIDENCE_BACKED | Existing `SuspendExpr` / `awaiting_external_signal` is the correct current boundary | Accepted |
| AUDIT-P3C2-05 | STRUCTURE_BACKED | P3c-2 must not alter `Suspension.payload` shape | Accepted |
| AUDIT-P3C2-06 | STRUCTURE_BACKED | P3c-2 must not expand parser / AST / lexer | Accepted |
| AUDIT-P3C2-07 | STRUCTURE_BACKED | Resolution lifecycle should remain implicit through event sequence | Accepted |
| AUDIT-P3C2-08 | STRUCTURE_BACKED | `distributed_consensus_ticket_resolved` is the candidate event | Accepted |
| AUDIT-P3C2-09 | STRUCTURE_BACKED | No automatic re-binding of original program variable | Accepted |
| AUDIT-P3C2-10 | PREVENTIVE | Dedicated resolution module is required to avoid interpreter domain expansion | Accepted |
| AUDIT-P3C2-11 | REQUIRED | `SuspendExpr` return value must remain injected signal unchanged | Accepted |
| AUDIT-P3C2-12 | REQUIRED | Multiple concurrent pending tickets must resolve independently | Accepted |
| AUDIT-P3C2-13 | REQUIRED | Replay resolution matching must be strict at the resolution boundary | Accepted |
| AUDIT-P3C2-14 | REQUIRED | Duplicate same-resolution must be idempotent and must not append duplicate event | Accepted |
| AUDIT-P3C2-15 | REQUIRED | Conflicting duplicate resolution must fail closed | Accepted |
| AUDIT-P3C2-16 | REQUIRED | Projection state must match durable history | Accepted |
| AUDIT-P3C2-17 | REQUIRED | P3c-2 hash computation must reuse P3a/P3c canonical profile | Accepted |

---

## 22. Final Recommendation

Proceed with:

```text id="aud-p3c2-final-001"
P3c-2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
```

Defer:

```text id="aud-p3c2-final-002"
P3c-N — Mailbox-backed Vote Delivery and Receive-based Vote Collection
```

until P2 durable lifecycle supports mailbox wait semantics.

The next repository artifact should be:

```text id="aud-p3c2-final-003"
docs/RFC-CONSENSUS-P3C2.md
```

The implementation must not begin until the RFC passes an explicit approval gate.
