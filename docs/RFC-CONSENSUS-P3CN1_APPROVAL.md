# RFC-CONSENSUS-P3CN1 Approval Record

**Status:** DRAFT / NOT APPROVED  
**Stage:** P3c-N1 approval gate  
**Repository mutation:** DOCUMENTATION APPROVAL DRAFT ONLY  
**Implementation status:** NOT AUTHORIZED  
**Implementation PR allowed:** NO  
**Product Owner sign-off:** PENDING  
**Target RFC:** `docs/RFC-CONSENSUS-P3CN1.md`  
**Target implementation slice:** Local mailbox-backed vote response collection for existing pending consensus tickets.

---

## 1. Approval Summary

This document is a companion approval record for `RFC-CONSENSUS-P3CN1`.

Current state:

```text
Approval status: DRAFT / NOT APPROVED
Implementation status: NOT AUTHORIZED
Approved implementation scope: NONE
Implementation PR allowed: NO
```

This approval record does not authorize runtime implementation.

P3c-N1 implementation remains blocked until this document is updated by a separate approval patch that explicitly sets approval to `APPROVED` and records the approved RFC content SHA, implementation base SHA, file allowlist and stop gates.

---

## 2. Scope Under Review

The proposed P3c-N1 RFC covers only:

```text
local mailbox-backed vote response collection for existing pending consensus tickets
```

The proposed first implementation slice is pending-ticket-only:

- consume externally delivered P2 `mailbox_message` vote responses for coordinator `global`;
- validate consensus-domain vote response payloads;
- bind responses to an existing pending ticket;
- enforce participant-level duplicate policy;
- convert valid responses into P3c-2-compatible `resolution_votes`;
- call existing `ConsensusEngine.resolve_pending_ticket(...)` only after full `missing_participants` coverage;
- preserve existing P3a/P3b/P3c-0/P3c-1/P3c-2 contracts.

---

## 3. Explicit Non-authorization

This approval draft does not authorize:

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

---

## 4. Draft Implementation Allowlist

If approved later, the proposed P3c-N1 implementation allowlist is expected to be constrained to:

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

Allowed reasons for touching `synapse/application.py` would be limited to:

```text
approved replay_state key handling
approved durable error/status mapping
approved artifact validation for new projection
```

Not in the first P3c-N1 implementation allowlist unless separately justified:

```text
synapse/runtime/consensus_engine.py
```

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

## 5. Draft Stop Gates

Implementation must remain blocked if any of the following becomes necessary without explicit approval:

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

## 6. Approval Transition Requirements

A later approval patch must update this file with:

```text
Approval status: APPROVED
Implementation status: AUTHORIZED FOR P3c-N1 IMPLEMENTATION
Product Owner sign-off: Кирилл Раков
Approved RFC content SHA: <sha>
Implementation base SHA: <merge-sha-of-approval-pr>
Approved file allowlist: <explicit list>
Approved stop-gates: <explicit list>
```

Until that approval patch merges, implementation is not authorized.

---

## 7. Current Decision

```text
Decision: DRAFT ONLY
Implementation PR: BLOCKED
Runtime code changes: NOT AUTHORIZED
```
