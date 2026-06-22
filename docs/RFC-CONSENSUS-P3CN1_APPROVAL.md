# RFC-CONSENSUS-P3CN1 Approval Record

**Status:** APPROVED  
**Stage:** P3c-N1 approval gate  
**Repository mutation:** DOCUMENTATION APPROVAL ONLY  
**Implementation status:** AUTHORIZED FOR P3c-N1 IMPLEMENTATION AFTER THIS APPROVAL PATCH MERGES  
**Implementation PR allowed:** YES, after this approval patch merges  
**Product Owner sign-off:** Кирилл Раков  
**Target RFC:** `docs/RFC-CONSENSUS-P3CN1.md`  
**Approved RFC content SHA:** `ff95d7daac3fcffad461356e7b3ad9a7b446377c`  
**RFC draft merge PR:** `#52`  
**RFC draft merge commit:** `6a033c75705710c659811eabccd95bfc9967df03`  
**Approval patch base SHA:** `6a033c75705710c659811eabccd95bfc9967df03`  
**Implementation base SHA:** the merge commit of this approval patch after it merges into `main`  
**Target implementation slice:** Local mailbox-backed vote response collection for existing pending consensus tickets.

---

## 1. Approval Summary

This document approves `RFC-CONSENSUS-P3CN1` for implementation under the constrained scope below.

Approved state after this approval patch merges:

```text
Approval status: APPROVED
Implementation status: AUTHORIZED FOR P3c-N1 IMPLEMENTATION
Approved implementation scope: LOCAL MAILBOX-BACKED VOTE RESPONSE COLLECTION FOR EXISTING PENDING CONSENSUS TICKETS
Implementation PR allowed: YES, from the merge commit of this approval patch
```

Implementation work must branch from the `main` commit produced by merging this approval patch. Implementation must not branch from the pre-approval RFC draft merge commit.

This approval does not change runtime code. It only authorizes a later implementation PR constrained by this approval record.

---

## 2. Approved Scope

The approved P3c-N1 implementation scope is:

```text
local mailbox-backed vote response collection for existing pending consensus tickets
```

The approved first implementation slice is pending-ticket-only:

- consume externally delivered P2 `mailbox_message` vote responses for coordinator `global`;
- validate consensus-domain vote response payloads;
- bind responses to an existing pending ticket;
- enforce participant-level duplicate policy;
- convert valid responses into P3c-2-compatible `resolution_votes`;
- call existing `ConsensusEngine.resolve_pending_ticket(...)` only after full `missing_participants` coverage;
- preserve existing P3a/P3b/P3c-0/P3c-1/P3c-2 contracts.

---

## 3. Explicit Non-authorization

This approval does not authorize:

```text
fresh durable DistributedConsensusStmt execution
vote request delivery
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
production distributed consensus protocol behavior
overall P3 closure
```

Any implementation that requires one of these behaviors must stop and return to RFC/approval review.

---

## 4. Approved Implementation Allowlist

A P3c-N1 implementation PR may touch only the files listed below:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_mailbox_collection_p3cn.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

### 4.1 Conditional file

`sync/application.py` is not approved.

The following file is conditionally approvable only if implementation proves that one of the listed reasons is necessary and the PR body explicitly calls it out before review:

```text
synapse/application.py
```

Allowed reasons for touching `synapse/application.py` are limited to:

```text
approved replay_state key handling
approved durable error/status mapping
approved artifact validation for new projection
```

If none of those reasons is required, `synapse/application.py` must remain unchanged.

### 4.2 Explicitly excluded file

The following file is not approved for the first P3c-N1 implementation slice:

```text
synapse/runtime/consensus_engine.py
```

Reason: P3c-N1 pending-ticket mode must use existing `ConsensusEngine.resolve_pending_ticket(...)` for final reduction. The consensus engine must not become a mailbox collector.

### 4.3 Files not allowed without separate approval

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

## 5. Approved Stop Gates

Implementation must stop if any of the following becomes necessary:

```text
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

These stop gates are implementation-blocking. Passing tests does not override them.

---

## 6. Required Implementation Contract

The later implementation PR must implement only the approved contract below.

### 6.1 Canonical path

```text
1. Existing P3c mechanics create a pending consensus ticket.
2. A durable run is suspended at a P2 mailbox wait boundary for coordinator "global".
3. External mailbox_message resume delivers a consensus_vote_response message to actor="global".
4. P2 validates the generic mailbox envelope and injects the internal message through the existing receive path.
5. P3c-N1 runtime-domain module validates that the internal message carries a consensus_vote_response for an existing pending ticket.
6. P3c-N1 validates participant, ticket_id, proposal_id, vote state and duplicate policy.
7. P3c-N1 updates collection state only if validation succeeds.
8. If all missing_participants are covered by valid yes/no/abstain votes, P3c-N1 calls ConsensusEngine.resolve_pending_ticket(ticket_payload, resolution_votes).
9. If coverage is incomplete, the ticket remains pending and no distributed_consensus_ticket_resolved event is emitted.
```

### 6.2 Required vote response constraints

P3c-N1 vote responses must be strict JSON and must use:

```text
kind == consensus_vote_response
schema_version == consensus.vote.response.v1
ticket_id: required, non-null
proposal_id: required
participant: required
coordinator: global
vote: yes | no | abstain
response_id: required
```

The vote state `missing` is not accepted as a mailbox-collected resolution vote.

### 6.3 Required coverage rule

Before calling `ConsensusEngine.resolve_pending_ticket(...)`, implementation must prove:

```text
set(resolution_votes.keys()) == set(ticket_payload["missing_participants"])
```

Partial vote collection must not call terminal resolution.

### 6.4 Timeout rule

P3c-N1 does not implement automatic wall-clock timeout.

P2 `mailbox_timeout` is externally supplied. If timeout arrives before full missing-participant coverage, it is non-terminal:

```text
no distributed_consensus_ticket_resolved event
pending ticket remains pending
no partial terminal resolution
```

### 6.5 Domain event policy

P3c-N1 may add:

```text
distributed_consensus_vote_received
```

This event may be emitted only after consensus-domain validation succeeds.

P3c-N1 must not add:

```text
distributed_consensus_vote_requested
```

because vote request delivery is not approved in this stage.

---

## 7. Required Tests

The later implementation PR must include focused tests for:

```text
valid vote response schema
invalid kind/schema/missing field/extra field
null ticket_id rejected
wrong ticket_id rejected
wrong proposal_id rejected
wrong coordinator rejected
unknown participant rejected
participant not in missing_participants rejected
invalid vote state rejected
missing vote state rejected
response_hash mismatch rejected
single response converts to resolution_votes mapping
multiple responses cover all missing_participants
partial collection remains non-terminal
full collection calls resolve_pending_ticket
partial timeout remains non-terminal
conflicting duplicate fails before projection mutation
same response_hash duplicate is idempotent no-op
message_received remains transport evidence
distributed_consensus_vote_received emitted only after domain validation
replay does not poll live mailbox
replay does not send vote requests
replay consumes domain events in recorded order
P2 mailbox wait regressions
P3c-2 ticket resolution regressions
```

The PR body must report exact test commands and counts.

---

## 8. Evidence Requirements

The later implementation PR must record:

```text
implementation base SHA = merge commit of this approval patch
implementation final head SHA
changed file list
test command list
exact test counts
known failures
new failures
scope non-claims
review verdict
```

After implementation merge, a separate docs/evidence patch must update:

```text
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

Distributed consensus must remain `Partial` unless a later approved production protocol stage is also completed.

---

## 9. Current Decision

```text
Decision: APPROVED FOR P3c-N1 IMPLEMENTATION AFTER THIS APPROVAL PATCH MERGES
Implementation PR: AUTHORIZED AFTER THIS APPROVAL PATCH MERGES
Runtime code changes in this approval patch: NONE
```

This approval becomes active only after merge into `main`.
