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

After P3c-N2 implementation, a fresh DistributedConsensusStmt that produces a pending consensus ticket because participant votes are missing can create deterministic per-participant mailbox vote requests, deliver those requests to local mailbox-capable participants, track request identity, bind later mailbox vote responses to prior requests, and replay that request/response path without re-sending messages or weakening history integrity.

---

## 1. Requirement IDs

Primary requirement:

REQ-CONSENSUS-01 — содержательный consensus

Supporting requirements:

REQ-HISTORY-INTEGRITY-01 — корректное понимание history hash
REQ-CAPABILITY-SIGNAL-01 — честная сигнализация
REQ-CROSS-NODE-01 — runtime/transport boundary

Traceability anchors:

DEPTH-CONSENSUS-01
DEPTH-CROSS-NODE-BOUNDARY-01
DEPTH-ASYNC-EXECUTION-01
DEPTH-GOVERNANCE-PROOF-01

---

## 2. Purpose

This RFC defines the approved design contract for P3c-N2 — Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection.

P3c-N2 exists because the current runtime can create deterministic consensus decisions and pending consensus tickets, and P3c-N1 can consume mailbox-delivered vote responses for existing pending tickets, but the runtime still does not create or deliver mailbox vote requests from a fresh DistributedConsensusStmt.

P3c-N2 adds the missing request-delivery layer between initial deferred consensus/ticket creation and mailbox-backed response collection.

P3c-N2 does not replace ConsensusEngine.

P3c-N2 does not claim production distributed consensus protocol behavior.

P3c-N2 is a single unified RFC. Separate RFC/amendment stages are not required unless a hard code contradiction is discovered before approval.

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

This section records the current code facts that P3c-N2 must preserve or extend.

### 4.1 Existing AST surface

The project already has a statement-level AST node:

DistributedConsensusStmt

with the existing fields:

participants
topic
quorum
timeout
policy_ref
binding

This RFC does not introduce a new AST node.

This RFC does not introduce DistributedConsensusExpr.

This RFC does not require parser, lexer, or AST changes.

### 4.2 Current fresh DistributedConsensusStmt path

The current fresh DistributedConsensusStmt path:

1. evaluates participants;
2. evaluates topic;
3. evaluates quorum;
4. evaluates timeout;
5. resolves policy reference;
6. builds a ConsensusRequest;
7. selects a VoteSource;
8. calls ConsensusEngine.decide(...);
9. appends distributed_consensus_decided;
10. if outcome is deferred with reason pending_missing_votes, appends distributed_consensus_ticket_created;
11. projects a pending consensus ticket;
12. binds the decision result to the statement binding.

Current behavior creates a pending ticket when votes are missing.

Current behavior does not emit:

distributed_consensus_vote_requested

Current behavior does not send mailbox messages with method:

consensus_vote_request

Current behavior does not create per-participant request identifiers.

Current behavior does not track participant request delivery.

P3c-N2 adds those missing contracts.

### 4.3 ConsensusEngine boundary

ConsensusEngine is the side-effect-free semantic core.

It owns:

- proposal preparation;
- participant normalization;
- strategy resolution;
- quorum derivation;
- timeout normalization;
- proposal identity;
- vote counting;
- outcome and reason derivation;
- votes_hash;
- result_hash;
- pending ticket creation;
- pending ticket resolution semantics.

It must not own:

- mailbox delivery;
- actor routing;
- send_message;
- execution history append;
- replay cursor mutation;
- interpreter state mutation;
- request projection storage;
- request transport effects.

P3c-N2 may add a public semantic proposal-preparation helper to ConsensusEngine, but it must not alter consensus mathematics.

### 4.4 ActorRuntime boundary

ActorRuntime.send_message(...) already provides the existing local actor message path.

send_message(...) also has remote forwarding behavior when the receiver route is not local.

P3c-N2 must not rely on send_message(...) to reject remote routes.

P3c-N2 must perform local-route validation before calling send_message(...).

If the route is not local, P3c-N2 must fail closed before any remote forwarding behavior is reached.

P3c-N2 uses the existing send path only after local-route validation.

P3c-N2 does not claim remote actor forwarding as consensus vote delivery.

actor_runtime.py is not in the P3c-N2 implementation allowlist unless later code audit proves an unavoidable contradiction with this RFC.

### 4.5 P3c-N1 compatibility

P3c-N1 already consumes consensus_vote_response for existing pending tickets.

P3c-N1 imported-ticket response collection does not require a prior consensus_vote_request.

P3c-N2 must preserve this compatibility.

Fresh P3c-N2 responses require prior request tracking.

Imported P3c-N1 flow remains compatible unless a later RFC explicitly opts that path into request tracking.

### 4.6 P3c Ticket Lifecycle compatibility

P3c Ticket Lifecycle already defines terminal ticket states:

resolved
cancelled
expired

P3c-N2 must not send vote requests for terminal tickets.

P3c-N2 must not accept fresh-path responses for terminal tickets.

Late responses after terminal lifecycle are rejected by deterministic terminal-state checks.

No additional request-invalidation event is introduced by this RFC.

---

## 5. Canonical Runtime Path

The canonical P3c-N2 runtime path is:

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

1. Constrained durable classification support for existing DistributedConsensusStmt.
2. Public semantic proposal-preparation helper in ConsensusEngine, if required.
3. Fresh pending-ticket request projection.
4. distributed_consensus_vote_requested domain event.
5. consensus_vote_request mailbox method/message.
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

P3c-N2 explicitly excludes:

- production distributed consensus protocol behavior;
- network vote delivery;
- daemon vote delivery;
- remote participant vote request delivery;
- durable timer behavior;
- scheduler behavior;
- persistent durable inbox;
- parser changes;
- lexer changes;
- AST changes;
- live LLM vote production;
- user-authored receive loops for consensus collection;
- public ticket API;
- automatic rebinding of original deferred consensus variables;
- change to ConsensusEngine vote mathematics;
- general actor transport redesign;
- remote message_forwarded consensus delivery claim;
- Windows portability debt unrelated to P3c-N2;
- unrelated documentation cleanup.

---

## 7. Protected Boundary Decisions

### 7.1 Durable classification decision

DistributedConsensusStmt is allowed in the approved P3c-N2 durable subset only under constrained P3c-N2 conditions.

The durable validator must reject DistributedConsensusStmt unless the statement satisfies the P3c-N2 constraints.

Required constraints:

- existing DistributedConsensusStmt AST only;
- no parser/AST/lexer expansion;
- no user-authored receive loop;
- participant list must resolve to local mailbox-capable participants;
- topic/proposal view must be strict JSON-compatible;
- remote participant delivery is not allowed;
- no network/daemon transport;
- no durable timer/scheduler claim;
- no persistent durable inbox claim;
- no production distributed consensus protocol claim.

Implementation may touch synapse/application.py only for this constrained classification support.

The intended classification style is:

SUPPORTED_WITH_CRASH_BOUNDARY

with a dedicated validator such as:

_validate_distributed_consensus(...)

The exact function name is implementation detail. The contract is that durable support is conditional, explicit, and fail-closed.

### 7.2 Engine proposal-preparation boundary

ConsensusEngine remains side-effect-free.

P3c-N2 may add a public semantic helper such as:

prepare_proposal_for_delivery(request: ConsensusRequest) -> PreparedConsensusProposal

The exact return type is implementation detail, but the helper must expose deterministic proposal data required by request delivery:

- proposal_id;
- proposal_preimage;
- proposal_view;
- normalized participants;
- strategy;
- policy;
- quorum;
- timeout;
- statement identity;
- coordinator.

The helper must not:

- collect mailbox responses;
- send messages;
- append execution history;
- mutate interpreter state;
- mutate actor runtime state;
- mutate request projections;
- change ConsensusEngine.decide(...) semantics;
- change vote mathematics.

Implementation may touch synapse/runtime/consensus_engine.py only for this public semantic helper.

### 7.3 Actor delivery boundary

P3c-N2 delivery is local-only.

Remote routing is out of scope.

Before calling send_message(...), P3c-N2 must resolve participant mailbox location. If the participant route is not local, the runtime must fail closed with:

p3cn2_remote_participant_not_supported

P3c-N2 may use the existing send_message(...) path only after local-route validation.

P3c-N2 must preserve existing send governance.

P3c-N2 must preserve existing message_sent transport evidence.

P3c-N2 must not claim remote message_forwarded as consensus vote delivery.

actor_runtime.py is not an implementation target for P3c-N2 unless later audit proves the local-only contract cannot be implemented through existing public hooks.

### 7.4 P3c-N1 compatibility boundary

Fresh P3c-N2 response handling requires prior request tracking.

Imported-ticket P3c-N1 response handling remains compatible without prior request tracking.

The runtime must distinguish:

fresh P3c-N2 ticket/request projection

from:

imported P3c-N1 pending ticket

If a fresh ticket has a P3c-N2 request projection, then consensus_vote_response.request_id must match the known request id for that participant.

If an imported pending ticket does not have a P3c-N2 request projection, existing P3c-N1 behavior remains valid.

---

## 8. Production Ownership

Each contract element has one canonical owner.

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
