# RFC-CONSENSUS-P3CN2 — Fresh DistributedConsensusStmt Mailbox Vote Request Delivery and Initial Collection

**Status:** DRAFT  
**Stage:** P3c-N2 RFC  
**Program:** Synapse Runtime Capability Integrity Program  
**Program ТЗ version:** 3.0  
**Program BASE_SHA:** `398753d48a5c742d9dcd695451a6b5d6d9f82943`  
**RFC TARGET_SHA:** `85abb6357b2540c3e33722e8b318ae37e8c4fa2e`  
**Repository mutation:** DOCUMENTATION RFC DRAFT ONLY  
**Implementation status:** NOT AUTHORIZED  
**Approval status:** NOT APPROVED FOR IMPLEMENTATION  
**Evidence status:** NOT STARTED  
**Capability status after RFC draft:** RFC_REQUIRED  
**Target capability:** Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Network / daemon delivery status:** NOT IN SCOPE  
**Remote participant delivery status:** NOT IN SCOPE  
**Parser / AST / lexer status:** NOT IN SCOPE  
**P3c-N1 status:** CLOSED / POST_MERGE_ACCEPTED / EVIDENCE CLOSED  
**P3c Ticket Lifecycle status:** CLOSED / POST_MERGE_ACCEPTED / EVIDENCE CLOSED

---

## 0. Product Statement

After P3c-N2 implementation, a fresh `DistributedConsensusStmt` that produces a pending consensus ticket because participant votes are missing can create deterministic per-participant mailbox vote requests, deliver those requests to local mailbox-capable participants, track request identity, bind later mailbox vote responses to prior requests, and replay that request/response path without re-sending messages or weakening history integrity.

---

## 1. Requirement IDs

Primary requirement:

- REQ-CONSENSUS-01 — содержательный consensus

Supporting requirements:

- REQ-HISTORY-INTEGRITY-01 — корректное понимание history hash
- REQ-CAPABILITY-SIGNAL-01 — честная сигнализация
- REQ-CROSS-NODE-01 — runtime/transport boundary

Traceability anchors:

- DEPTH-CONSENSUS-01
- DEPTH-CROSS-NODE-BOUNDARY-01
- DEPTH-ASYNC-EXECUTION-01
- DEPTH-GOVERNANCE-PROOF-01

---

## 2. Purpose

This RFC defines the draft design contract for **P3c-N2 — Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection**.

P3c-N2 exists because the current runtime can create deterministic consensus decisions and pending consensus tickets, and P3c-N1 can consume mailbox-delivered vote responses for existing pending tickets, but the runtime still does not create or deliver mailbox vote requests from a fresh `DistributedConsensusStmt`.

P3c-N2 adds the missing request-delivery layer between initial deferred consensus/ticket creation and mailbox-backed response collection.

P3c-N2 does not replace `ConsensusEngine` and does not claim production distributed consensus protocol behavior.

This is a single unified RFC draft. Separate RFC/amendment stages are not required unless a hard code contradiction is discovered before approval.

Implementation remains blocked until an approval document explicitly approves this RFC.

---

## 3. Program Constraints Applied to This RFC

This RFC follows the program rules:

1. Product problem precedes implementation.
2. Runtime contract must be approved before production code changes.
3. Tests are acceptance evidence for the approved contract; tests do not define architecture.
4. One product stage must not be mixed with unrelated documentation cleanup.
5. Evidence closure must not be mixed into implementation unless approval explicitly changes that rule.
6. Network packet construction must not be represented as network delivery.
7. Test-only wiring must not be represented as production readiness.
8. Replay must remain deterministic.
9. Historical events must not be rewritten.
10. Public signaling must match actual capability maturity.

This RFC authorizes no implementation.

---

## 4. Current Code Facts

### 4.1 Existing AST surface

The project already has a statement-level AST node `DistributedConsensusStmt` with existing fields:

```text
participants
topic
quorum
timeout
policy_ref
binding
```

This RFC does not introduce a new AST node, does not introduce `DistributedConsensusExpr`, and does not require parser, lexer, or AST changes.

### 4.2 Current fresh DistributedConsensusStmt path

The current fresh `DistributedConsensusStmt` path evaluates participants, topic, quorum, timeout and policy reference, builds a `ConsensusRequest`, selects a `VoteSource`, calls `ConsensusEngine.decide(...)`, appends `distributed_consensus_decided`, appends `distributed_consensus_ticket_created` when outcome is deferred with `reason = pending_missing_votes`, projects a pending consensus ticket, and binds the decision result to the statement binding.

Current behavior creates a pending ticket when votes are missing.

Current behavior does not emit `distributed_consensus_vote_requested`, does not send mailbox messages with method `consensus_vote_request`, does not create per-participant request identifiers, and does not track participant request delivery.

P3c-N2 adds those missing contracts.

### 4.3 ConsensusEngine boundary

`ConsensusEngine` is the side-effect-free semantic core. It owns proposal preparation, participant normalization, strategy resolution, quorum derivation, timeout normalization, proposal identity, vote counting, outcome/reason derivation, `votes_hash`, `result_hash`, pending ticket creation, and pending ticket resolution semantics.

It must not own mailbox delivery, actor routing, `send_message`, execution history append, replay cursor mutation, interpreter state mutation, request projection storage, or request transport effects.

P3c-N2 may add a public semantic proposal-preparation helper to `ConsensusEngine`, but it must not alter consensus mathematics.

### 4.4 ActorRuntime boundary

`ActorRuntime.send_message(...)` already provides the existing local actor message path and also has remote forwarding behavior when the receiver route is not local.

P3c-N2 must not rely on `send_message(...)` to reject remote routes. It must perform local-route validation before calling `send_message(...)`.

If the route is not local, P3c-N2 must fail closed before any remote forwarding behavior is reached.

P3c-N2 uses the existing send path only after local-route validation. It does not claim remote actor forwarding as consensus vote delivery.

`actor_runtime.py` is not in the P3c-N2 implementation allowlist unless later code audit proves an unavoidable contradiction with this RFC.

### 4.5 P3c-N1 compatibility

P3c-N1 already consumes `consensus_vote_response` for existing pending tickets.

P3c-N1 imported-ticket response collection does not require a prior `consensus_vote_request`.

P3c-N2 must preserve this compatibility. Fresh P3c-N2 responses require prior request tracking. Imported P3c-N1 flow remains compatible unless a later RFC explicitly opts that path into request tracking.

### 4.6 P3c Ticket Lifecycle compatibility

P3c Ticket Lifecycle already defines terminal ticket states:

```text
resolved
cancelled
expired
```

P3c-N2 must not send vote requests for terminal tickets, must not accept fresh-path responses for terminal tickets, and must reject late responses after terminal lifecycle by deterministic terminal-state checks.

No additional request-invalidation event is introduced by this RFC.

---

## 5. Canonical Runtime Path

```text
DistributedConsensusStmt
  -> evaluate_distributed_consensus
  -> ConsensusRequest
  -> ConsensusEngine semantic proposal/decision path
  -> distributed_consensus_decided
  -> distributed_consensus_ticket_created when deferred
  -> P3c-N2 request projection
  -> distributed_consensus_vote_requested per missing participant
  -> local-route check
  -> send_message(..., method="consensus_vote_request")
  -> message_sent
  -> P2 mailbox wait / message_received
  -> P3c-N1-style response validation with P3c-N2 request binding
  -> distributed_consensus_vote_received
  -> distributed_consensus_ticket_resolved when coverage is complete
```

P3c-N2 request delivery occurs after initial deferred consensus/ticket creation and before mailbox-backed response collection / terminal resolution.

P3c-N2 does not send vote requests before the initial pending ticket exists.

P3c-N2 does not perform final resolution before mailbox-backed response collection provides the missing votes.

---

## 6. Scope

### 6.1 In scope

P3c-N2 includes:

1. Constrained durable classification support for existing `DistributedConsensusStmt`.
2. Public semantic proposal-preparation helper in `ConsensusEngine`, if required.
3. Fresh pending-ticket request projection.
4. `distributed_consensus_vote_requested` domain event.
5. `consensus_vote_request` mailbox method/message.
6. Local-only vote request delivery through the existing actor mailbox path.
7. Request batch id plus per-participant request id.
8. Per-participant request hash.
9. Request/response binding for fresh P3c-N2 responses.
10. Replay validation for vote request events.
11. Replay reconstruction of request projection.
12. Terminal ticket rejection for request delivery and fresh-path responses.
13. Failure taxonomy for vote request delivery.
14. Regression preservation for P3c-0, P3c-1, P3c-2, P3c-N1, P2 mailbox wait, and P3c Ticket Lifecycle.

### 6.2 Out of scope

P3c-N2 explicitly excludes production distributed consensus protocol behavior, network vote delivery, daemon vote delivery, remote participant vote request delivery, durable timer behavior, scheduler behavior, persistent durable inbox, parser/lexer/AST changes, live LLM vote production, user-authored receive loops for consensus collection, public ticket API, automatic rebinding of original deferred consensus variables, changes to `ConsensusEngine` vote mathematics, actor transport redesign, remote `message_forwarded` consensus delivery claim, unrelated Windows portability debt, and unrelated documentation cleanup.

---

## 7. Protected Boundary Decisions

### 7.1 Durable classification decision

`DistributedConsensusStmt` is allowed in the approved P3c-N2 durable subset only under constrained P3c-N2 conditions.

The durable validator must reject `DistributedConsensusStmt` unless the statement satisfies the P3c-N2 constraints:

- existing `DistributedConsensusStmt` AST only;
- no parser/AST/lexer expansion;
- no user-authored receive loop;
- participant list must resolve to local mailbox-capable participants;
- topic/proposal view must be strict JSON-compatible;
- remote participant delivery is not allowed;
- no network/daemon transport;
- no durable timer/scheduler claim;
- no persistent durable inbox claim;
- no production distributed consensus protocol claim.

Implementation may touch `synapse/application.py` only for this constrained classification support.

The intended classification style is `SUPPORTED_WITH_CRASH_BOUNDARY` with a dedicated validator such as `_validate_distributed_consensus(...)`. The exact function name is implementation detail. The contract is that durable support is conditional, explicit, and fail-closed.

### 7.2 Engine proposal-preparation boundary

`ConsensusEngine` remains side-effect-free.

P3c-N2 may add a public semantic helper such as `prepare_proposal_for_delivery(request: ConsensusRequest) -> PreparedConsensusProposal`.

The helper must expose deterministic proposal data required by request delivery: `proposal_id`, proposal preimage, proposal view, normalized participants, strategy, policy, quorum, timeout, statement identity, and coordinator.

The helper must not collect mailbox responses, send messages, append execution history, mutate interpreter state, mutate actor runtime state, mutate request projections, change `ConsensusEngine.decide(...)` semantics, or change vote mathematics.

Implementation may touch `synapse/runtime/consensus_engine.py` only for this public semantic helper.

### 7.3 Actor delivery boundary

P3c-N2 delivery is local-only. Remote routing is out of scope.

Before calling `send_message(...)`, P3c-N2 must resolve participant mailbox location. If the participant route is not local, the runtime must fail closed with `p3cn2_remote_participant_not_supported`.

P3c-N2 may use the existing `send_message(...)` path only after local-route validation. It must preserve existing send governance and `message_sent` transport evidence.

P3c-N2 must not claim remote `message_forwarded` as consensus vote delivery.

`actor_runtime.py` is not an implementation target for P3c-N2 unless later audit proves the local-only contract cannot be implemented through existing public hooks.

### 7.4 P3c-N1 compatibility boundary

Fresh P3c-N2 response handling requires prior request tracking.

Imported-ticket P3c-N1 response handling remains compatible without prior request tracking.

The runtime must distinguish fresh P3c-N2 ticket/request projection from imported P3c-N1 pending ticket.

If a fresh ticket has a P3c-N2 request projection, then `consensus_vote_response.request_id` must match the known request id for that participant.

If an imported pending ticket does not have a P3c-N2 request projection, existing P3c-N1 behavior remains valid.

---

## 8. Production Ownership

| Contract element | Canonical owner |
|---|---|
| Consensus semantic proposal identity | ConsensusEngine |
| Vote mathematics | ConsensusEngine |
| Pending ticket creation | ConsensusEngine + interpreter adapter append/projection |
| Request delivery orchestration | P3c-N2 interpreter adapter |
| Request event/message schema | consensus_vote_request_delivery.py |
| Request hash | consensus_vote_request_delivery.py |
| Request projection | consensus_vote_request_delivery.py + interpreter runtime state |
| Local route precheck | P3c-N2 adapter using existing actor runtime route lookup |
| Local message delivery | existing ActorRuntime.send_message(...) |
| Response validation for fresh request binding | consensus_mailbox_collection.py integrated with P3c-N2 request projection |
| Terminal ticket rejection | existing ticket lifecycle / ticket projection guards plus P3c-N2 request checks |
| Replay request validation | consensus_vote_request_delivery.py + interpreter replay integration |
| Public signaling | capability matrix / evidence after implementation merge |

No field may have two independent sources of truth.

---

## 9. Participant to Mailbox Binding

P3c-N2 requires participants to resolve to local mailbox-capable actor identities.

Allowed participant forms:

1. `DurableActorRef`;
2. local actor name string resolved through existing runtime actor/mailbox state;
3. spawned process id string if already present in runtime mailbox state.

The implementation must produce a deterministic binding map from normalized `participant_id` to the local receiver key accepted by the actor mailbox path.

This RFC does not standardize a new mailbox id syntax and does not require mailbox ids to use an `Inbox#...` shape.

The runtime must fail closed when participant evaluation fails, participant evaluates to `None`, participant has no local mailbox-capable identity, participant resolves to a remote route, duplicate participant identities appear after normalization, or participant is not in the pending ticket’s `missing_participants`.

Stable failure reasons:

```text
p3cn2_vote_request_participant_mailbox
p3cn2_remote_participant_not_supported
p3cn2_vote_request_participant_not_missing
```

---

## 10. Request ID Model

P3c-N2 uses both `request_batch_id` and `request_id`.

`request_batch_id` identifies the request set for one fresh consensus statement and its pending ticket. It must be deterministic and derived from a closed canonical preimage containing at least schema version, ticket id, proposal id, statement identity, coordinator, participants, and participant mailboxes.

`request_id` identifies one participant request within the batch. It must be deterministic and derived from a closed canonical preimage containing at least schema version, request batch id, ticket id, proposal id, participant, and participant mailbox.

`request_hash` validates the full request payload. It must be deterministic and derived from a closed canonical preimage containing the full domain request event content except `request_hash` itself.

`request_hash` is used for replay integrity and conflicting duplicate detection.

---

## 11. Domain Event Contract

P3c-N2 introduces the domain event `distributed_consensus_vote_requested`.

Schema version:

```text
consensus.vote.request.event.v1
```

Required closed-schema fields:

```text
type
schema_version
ticket_id
proposal_id
statement_identity
coordinator
participant
participant_mailbox
request_batch_id
request_id
request_hash
proposal_view_hash
strategy
policy
quorum
timeout
```

The domain event is the source of truth for replay. It must be emitted once per missing participant after the pending ticket is created, must not be emitted for participants that already have terminal votes, and must not be emitted for terminal tickets.

---

## 12. Mailbox Message Contract

P3c-N2 introduces the mailbox method/message `consensus_vote_request` with schema version `consensus.vote.request.v1`.

The mailbox message must include the same request identity anchors as the domain event plus the strict JSON-compatible `proposal_view` payload.

The mailbox message must be strict JSON-compatible, closed-schema validated, match the corresponding domain event, and be delivered only to a local mailbox-capable participant.

The mailbox message is transport payload; it is not the replay source of truth.

---

## 13. Request Projection

P3c-N2 introduces an internal request projection, preferably `_consensus_vote_requests[ticket_id]`.

The projection records schema version, ticket id, proposal id, statement identity, coordinator, request batch id, requested participants, participant mailboxes, request ids, request hashes, delivery status, and projection state.

Allowed projection states:

```text
collecting
completed
terminal
```

The projection is internal runtime state. It is reconstructed during replay from domain request events.

The projection must not overload `consensus_tickets` unless implementation audit proves the separate projection cannot satisfy replay and response-binding contracts.

The projection must be immutable with respect to terminal ticket states.

---

## 14. Runtime Event Order

Canonical order for fresh P3c-N2 deferred consensus:

1. `distributed_consensus_decided`
2. `distributed_consensus_ticket_created`
3. `distributed_consensus_vote_requested`
4. `message_sent`
5. `message_received`
6. `distributed_consensus_vote_received`
7. `distributed_consensus_ticket_resolved`

P3c-N2 request delivery occurs after initial deferred consensus/ticket creation and before mailbox-backed response collection / terminal resolution.

`distributed_consensus_vote_requested` is the domain source of truth. `message_sent` is transport evidence. Replay validates both where both are recorded. `message_sent` does not replace `distributed_consensus_vote_requested`.

---

## 15. Observable Contract

Approved inputs include the existing `DistributedConsensusStmt`, local mailbox-capable participants, strict JSON-compatible topic/proposal view, approved consensus strategy, approved quorum, approved timeout value, and optional policy reference resolved through existing consensus policy mechanics.

Rejected inputs include remote participants, unresolvable participants, `None` participants, duplicate participants after normalization, non-JSON-compatible topic/proposal view, terminal ticket request attempts, user-authored receive-loop request collection, and parser/AST/lexer-expanded consensus syntax.

Observable runtime outputs include `distributed_consensus_decided`, `distributed_consensus_ticket_created`, `distributed_consensus_vote_requested`, `message_sent`, `message_received`, `distributed_consensus_vote_received`, `distributed_consensus_ticket_resolved`, and the runtime binding result from `DistributedConsensusStmt`.

Approved state mutation includes appending the request domain event and local `message_sent`, creating/updating `_consensus_vote_requests`, preserving `consensus_tickets`, preserving P3c-N1 response collection projection, and projecting terminal resolution through the existing P3c-2/P3c-N1 path.

Forbidden state mutation includes actor runtime transport redesign, remote forwarding as consensus delivery, parser/AST/lexer state changes, historical event rewrite, legacy event mutation, and consensus mathematics changes.

---

## 16. Replay Contract

Replay must consume and validate existing history. It must not re-send mailbox messages, poll live mailboxes, create new request events, or silently repair missing request history.

Replay must reconstruct `_consensus_vote_requests` from `distributed_consensus_vote_requested` events.

Replay must consume `distributed_consensus_decided`, adjacent `distributed_consensus_ticket_created` when outcome is deferred, one request event per requested missing participant, matching `message_sent` transport evidence where present, later `message_received` response events through existing mailbox replay mechanics, `distributed_consensus_vote_received`, and `distributed_consensus_ticket_resolved` when coverage becomes terminal.

Replay must fail closed on missing, malformed, mismatched, wrong-ticket, wrong-proposal, wrong-statement, wrong-participant, wrong-mailbox, wrong-request-id, wrong-request-hash or out-of-order request events; response without matching known request for the fresh P3c-N2 path; or terminal ticket mutation attempt.

Replay failures become `ConsensusReplayIntegrityError`.

Replay cursor and internal projections must be restored or preserved consistently on replay failure.

---

## 17. Request / Response Binding

Fresh P3c-N2 response handling requires prior request identity.

For fresh P3c-N2 tickets, `consensus_vote_response.request_id` is required and must match the known request id for that participant. Participant, ticket id, proposal id and pending ticket state must match; response must not arrive after terminal lifecycle.

For imported P3c-N1 tickets without request projection, existing P3c-N1 behavior remains compatible and nullable `request_id` remains accepted unless a future RFC opts that path into request tracking.

This split preserves already-closed P3c-N1 evidence.

---

## 18. Lifecycle Interaction

P3c-N2 must respect terminal lifecycle states.

No vote request delivery and no fresh-path vote response acceptance are allowed for:

```text
resolved
cancelled
expired
```

Late response after terminal event must fail closed or be rejected deterministically using P3c-N2 taxonomy.

Terminal lifecycle state is enough to invalidate outstanding request projection.

P3c-N2 does not introduce a new `vote_request_invalidated` event and does not reopen cancelled or expired tickets.

---

## 19. Duplicate and Idempotency Policy

Exact duplicate request event for the same participant, same request id, same request hash, same ticket, and same mailbox is idempotent.

Conflicting duplicate request event fails closed. Conflicts include same request id with different payload, same participant with different request id in the same batch, same request id with different participant, same participant with different mailbox without a new approved request batch, request for participant not in `missing_participants`, and request for terminal ticket.

Fresh P3c-N2 response duplicate behavior follows P3c-N1 duplicate semantics after request identity is validated.

---

## 20. Failure Taxonomy

P3c-N2 introduces `ConsensusVoteRequestError`.

Stable reasons:

```text
p3cn2_vote_request_schema
p3cn2_vote_request_event_schema
p3cn2_vote_request_ticket_not_found
p3cn2_vote_request_terminal_ticket
p3cn2_vote_request_participant_not_missing
p3cn2_vote_request_participant_mailbox
p3cn2_vote_request_duplicate
p3cn2_vote_request_replay_mismatch
p3cn2_unsolicited_response
p3cn2_remote_participant_not_supported
```

At interpreter boundary, `ConsensusVoteRequestError` converts to `RuntimeError`.

During replay, request mismatch converts to `ConsensusReplayIntegrityError`.

Schema validation may use existing strict JSON / closed-schema helpers, but P3c-N2 request-delivery taxonomy must not leak P3c-N1 or lifecycle reason codes as the primary failure contract.

---

## 21. Compatibility

Old histories without P3c-N2 request events remain valid for already-closed P3c-0/P3c-1/P3c-2/P3c-N1 scopes.

Imported P3c-N1 pending tickets without request projection remain compatible.

Fresh P3c-N2 writes new request events only for fresh pending tickets created by the approved P3c-N2 path.

Replay of old P3c-N1 imported-ticket flows must not require `distributed_consensus_vote_requested`.

Replay of old P3c-0/P3c-1/P3c-2 histories must remain unchanged.

Replay of new P3c-N2 histories must consume and validate request events and reconstruct request projection deterministically.

Imported-ticket response collection and fresh-ticket request/response collection coexist. Fresh-ticket response collection uses request binding. Imported-ticket response collection preserves prior compatibility.

P3c-N2 does not rewrite old events.

---

## 22. History Integrity

P3c-N2 must not modify:

```text
canonical_json
hash_event_chain
verify_event_chain
history_chain_seed
checkpoint format
snapshot format
HOST_ABI_VERSION
CVM ABI
```

New P3c-N2 events enter history as ordinary event payloads and therefore participate in existing canonical event-chain hashing.

P3c-N2 must not introduce an alternate history integrity mechanism.

Local request hashes do not replace canonical history hash.

---

## 23. Implementation File Allowlist

### 23.1 RFC-stage files

The RFC draft PR may change only:

```text
docs/RFC-CONSENSUS-P3CN2.md
```

### 23.2 Approval-stage files

The approval PR may change only:

```text
docs/RFC-CONSENSUS-P3CN2_APPROVAL.md
```

or another explicitly approved approval document name.

### 23.3 Future implementation allowlist

A future implementation PR may change only the following files unless the approval document explicitly narrows this list further:

```text
synapse/interpreter.py
synapse/application.py
synapse/runtime/consensus_engine.py
synapse/runtime/consensus_mailbox_collection.py
synapse/runtime/consensus_vote_request_delivery.py
tests/test_consensus_fresh_mailbox_p3cn2.py
```

Restrictions:

- `synapse/application.py`: only constrained durable classification for `DistributedConsensusStmt`;
- `synapse/runtime/consensus_engine.py`: only public semantic proposal-prep helper; no consensus mathematics changes;
- `synapse/runtime/consensus_mailbox_collection.py`: only request/response binding and schema integration;
- `synapse/interpreter.py`: only adapter, wiring, projection, replay integration;
- `synapse/runtime/consensus_vote_request_delivery.py`: owns request event/message schema, hash, projection, duplicate policy, and request replay validation;
- tests must prove approved behavior and must not define architecture.

### 23.4 Post-merge evidence allowlist

After implementation merge, evidence PR may update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Evidence must not be mixed into the implementation PR unless approval explicitly changes this rule.

---

## 24. Forbidden Changes

P3c-N2 forbids changes to:

```text
synapse/ast.py
synapse/parser.py
synapse/lexer.py
synapse/runtime/actor_runtime.py
network implementation files
daemon implementation files
scheduler/timer implementation files
dependency files
config files
```

P3c-N2 forbids production distributed consensus protocol claim, remote participant delivery claim, network/daemon delivery, durable timer/scheduler claim, persistent durable inbox claim, parser/AST/lexer expansion, live LLM vote production, user-authored receive-loop implementation of vote collection, public ticket API, automatic rebinding of original deferred consensus variables, ConsensusEngine mathematical semantic changes, actor transport semantic changes, changing production code only to satisfy a test, adding skip/xfail to make P3c-N2 acceptance appear green, and fixing unrelated platform baseline failures inside P3c-N2.

---

## 25. Platform Baseline

Implementation must record actual platform baseline before work starts.

Known Linux baseline at `BASE_SHA = 398753d48a5c742d9dcd695451a6b5d6d9f82943`:

```text
OS: Linux
Python: 3.12.3
pytest: 9.0.3
Git: 2.43.0
Result: 1418 passed / 12 skipped / 1430 collected
```

Known Windows baseline at `BASE_SHA = 398753d48a5c742d9dcd695451a6b5d6d9f82943`:

```text
OS: Windows 11
Python: 3.12.13
pytest: 9.0.3
Git: 2.54.0.windows.1
core.autocrlf: true
core.symlinks: false
core.filemode: false
Result: 1412 passed / 6 known failures / 12 skipped / 1430 collected
```

P3c-N2 implementation must report `TARGET_SHA` baseline, current OS, Python version, pytest version, Git version, selected test results, and `new_failures`.

Windows portability debt must not be fixed inside P3c-N2 unless directly caused by P3c-N2 changes.

---

## 26. Acceptance Criteria for Future Implementation

Future implementation must include tests that prove the approved contract:

1. Fresh `DistributedConsensusStmt` with missing votes creates pending ticket and request projection.
2. One `distributed_consensus_vote_requested` is emitted per missing participant.
3. Each request has deterministic `request_batch_id`, `request_id`, and `request_hash`.
4. Each request has a matching local `message_sent`.
5. Remote participant fails closed before forward delivery.
6. Unresolvable participant fails closed.
7. `None` participant fails closed.
8. Duplicate participant fails closed.
9. Fresh response without prior request fails closed.
10. Imported P3c-N1 response remains compatible without prior request.
11. Response with wrong request id fails closed.
12. Response with wrong ticket id fails closed.
13. Response with wrong proposal id fails closed.
14. Replay consumes request events without re-sending messages.
15. Replay fails closed on missing request event.
16. Replay fails closed on malformed request event.
17. Replay fails closed on mismatched request event.
18. Terminal ticket blocks request delivery.
19. Terminal ticket blocks fresh-path response collection.
20. P3c-0/P3c-1/P3c-2/P3c-N1/P3c Ticket Lifecycle regression tests remain green.

Tests must not use skips or expected failures for P3c-N2 acceptance coverage.

Tests must not redefine architecture.

Tests must assert behavior already approved in this RFC.

---

## 27. Stop-Gates

Work must stop on:

```text
BASE_MISMATCH
DIRTY_WORKTREE
BASE_CONTRACT_MOVED
UNAPPROVED_SCOPE_EXPANSION
CONTRACT_NOT_DEFINED
CANONICAL_OWNER_NOT_DEFINED
LEGACY_DATA_MUTATION_REQUIRED
HASH_ALGORITHM_CHANGE_REQUIRED
CANONICAL_SERIALIZATION_CHANGE_REQUIRED
ABI_CHANGE_NOT_APPROVED
REPLAY_RECOMPUTATION_REQUIRED
NEW_PLATFORM_REGRESSION
USER_PATH_NOT_DEFINED
DURABLE_EVENT_CONTRACT_UNDEFINED
FAILURE_SEMANTICS_UNDEFINED
P3CN2_REMOTE_DELIVERY_REQUIRED
P3CN2_ACTOR_RUNTIME_CHANGE_REQUIRED
P3CN2_PARSER_AST_LEXER_CHANGE_REQUIRED
P3CN2_ENGINE_MATH_CHANGE_REQUIRED
P3CN2_REQUEST_PROJECTION_OWNER_AMBIGUOUS
P3CN2_IMPORTED_TICKET_COMPATIBILITY_BREAK_REQUIRED
```

The implementer must not expand scope to bypass a stop-gate.

---

## 28. Approval Requirements

Before implementation, approval must explicitly record RFC file path, RFC content hash or approved commit SHA, implementation base SHA, implementation file allowlist, forbidden files, durable classification decision, engine helper decision, actor delivery local-only decision, participant mailbox binding decision, request event schema, mailbox message schema, request id model, request projection schema, runtime event order, replay contract, failure taxonomy, lifecycle terminal interaction, P3c-N1 compatibility rule, required tests, explicit non-claims, platform baseline requirements, and stop-gates.

Implementation must not begin until approval is merged.

---

## 29. Commit / Push / PR Contract for Later Stages

### 29.1 RFC draft PR

Allowed mutation:

```text
docs/RFC-CONSENSUS-P3CN2.md
```

No runtime code. No tests. No evidence/matrix update.

### 29.2 Approval PR

Allowed mutation:

```text
docs/RFC-CONSENSUS-P3CN2_APPROVAL.md
```

No runtime code. No tests. No evidence/matrix update.

### 29.3 Implementation PR

Allowed mutation only within approved implementation allowlist.

No evidence/matrix update unless approval explicitly changes the rule. No unrelated docs cleanup. No parser/AST/lexer. No actor runtime change. No network/daemon/timer/scheduler. No dependency/config change.

### 29.4 Evidence PR

After implementation merge only.

Allowed mutation:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Evidence PR must record implementation PR number, implementation base SHA, implementation head SHA, implementation merge SHA, changed files, accepted runtime behavior, non-claims preserved, tests run, failures/new failures, status update, and remaining future work.

---

## 30. Evidence Report Format for Later Implementation

Implementation report must include:

```text
TARGET_SHA
branch
changed files
runtime files changed
test files changed
forbidden files touched: yes/no
product statement
canonical owner confirmation
canonical runtime path confirmation
observable result confirmation
durable event confirmation
replay behavior confirmation
idempotency confirmation
compatibility confirmation
failure taxonomy confirmation
test commands
test results
new_failures
known platform failures
stop-gates encountered
scope deviations
```

A report that only says tests passed is not sufficient.

---

## 31. Non-Claims

This RFC does not claim production distributed consensus protocol behavior, network/daemon vote delivery, remote participant vote delivery, durable timer/scheduler behavior, persistent durable inbox behavior, parser/AST/lexer expansion, live LLM vote production, public ticket API, automatic rebinding of original deferred consensus variables, overall P3 production status, overall P3c closure, or P3c-N2 evidence closure.

P3c-N2 closure requires implementation, tests, merge, and post-merge evidence.

---

## 32. Definition of Done for P3c-N2

P3c-N2 may be marked CLOSED only after all of the following are true:

- production contract implemented;
- canonical owner proven;
- canonical runtime path proven;
- observable result proven;
- failure paths proven;
- durable/replay semantics proven;
- backward compatibility proven;
- new regressions = 0;
- platform baseline respected;
- documentation synchronized;
- scope respected;
- evidence recorded.

P3c-N2 is not CLOSED merely because RFC is merged, approval is merged, implementation PR is merged, tests are green, evidence file exists without proof, or matrix is updated without runtime evidence.

---

## 33. Final RFC Statement

P3c-N2 is the unified design contract for fresh `DistributedConsensusStmt` mailbox-backed vote request delivery and initial collection.

P3c-N2 keeps `ConsensusEngine` as the semantic core; allows constrained durable `DistributedConsensusStmt`; prepares proposal data through a public semantic helper if needed; creates request projection for fresh pending tickets; emits `distributed_consensus_vote_requested`; sends local `consensus_vote_request` messages through existing local actor mailbox delivery; fails closed for remote participants before forwarding; uses `request_batch_id` plus per-participant `request_id`; binds fresh responses to prior requests; preserves imported P3c-N1 compatibility; preserves P3c Ticket Lifecycle terminal semantics; defines replay validation before implementation; defines failure taxonomy before implementation; and respects program-level capability integrity constraints.

Implementation remains blocked until approval.
