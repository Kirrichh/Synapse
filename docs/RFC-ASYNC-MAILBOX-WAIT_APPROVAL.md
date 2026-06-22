# RFC-ASYNC-MAILBOX-WAIT APPROVAL RECORD

## Approval Gate for Durable Lifecycle of Mailbox Receive Waiting

**RFC ID:** RFC-ASYNC-MAILBOX-WAIT  
**Approval record ID:** RFC-ASYNC-MAILBOX-WAIT_APPROVAL  
**Status:** DRAFT — APPROVAL NOT GRANTED  
**Implementation authorization:** NO  
**Patch type:** documentation-only  
**Base SHA:** `5088587b0fd3757e30fa6cb92c5ec1ddf6750461`  
**Target implementation PR:** TO BE CREATED AFTER APPROVAL  
**Target future blocked capability:** P3c-N — mailbox-backed vote delivery and receive-based vote collection  
**Approval record purpose:** Record the architecture decision required before any implementation of `awaiting_message` or `awaiting_message_or_timeout` is permitted in P2 durable execution.

---

## 1. Decision status

Current decision state:

```text id="approval-p2-mailbox-status-001"
DRAFT — APPROVAL NOT GRANTED
```

Implementation is blocked.

Decision checklist:

```text id="approval-p2-mailbox-status-002"
[ ] APPROVED FOR IMPLEMENTATION
[ ] REJECTED
[ ] RETURNED FOR REVISION
```

This document is currently in draft status. Implementation cannot begin until this approval record is updated and merged with an explicit approved status.

---

## 2. Explicit decisions pending approval

| Decision area | Proposed decision | Approval status |
|---|---|---|
| `awaiting_message` support | Support as externally resolved mailbox receive boundary | PENDING |
| `awaiting_message_or_timeout` support | Support as externally resolved mailbox receive or external timeout boundary | PENDING |
| Artifact schema version | Preserve `artifact_schema_version = 1.0.0` if no new required top-level artifact fields are introduced | PENDING |
| External message schema | Args-only external schema; `message.payload` rejected in resume input | PENDING |
| Internal payload policy | Runtime may derive internal `payload = args[0] if len(args) == 1 else args` after validation | PENDING |
| Mailbox hash profile | Normalized reason-specific mailbox signal hash mandated | PENDING |
| Raw full-signal hash | Prohibited for mailbox wait reasons | PENDING |
| Persisted mailbox policy | No early durable inbox; no ghost mailbox consumption without current resume payload | PENDING |
| `ReceiveBlock` classifier update | Allow only constrained single-pattern receive after approval | PENDING |
| `ReceivePattern` classifier update | Allow only inside approved constrained `ReceiveBlock` | PENDING |
| Timeout expression validation | Dedicated deterministic timeout-expression validator required | PENDING |
| `else_body` validation | Recursive durable AST validation required | PENDING |
| `promise_id` policy | `active_suspension.promise_id` must be null for mailbox wait reasons | PENDING |
| Timeout ownership | External timeout injection only; no runtime wall-clock scheduler | PENDING |
| Dedicated validation module | Prefer `synapse/runtime/mailbox_wait.py` | PENDING |
| P3c-N authorization | Still blocked until this P2 RFC is approved and implemented | PENDING |

---

## 3. Required approvers

This RFC requires review and approval from the following ownership areas:

```text id="approval-p2-mailbox-approvers-001"
1. P2 durable lifecycle owner
2. replay / history integrity reviewer
3. ActorRuntime owner
4. independent architecture reviewer
5. future P3c-N domain reviewer for compatibility only
```

The future P3c-N reviewer does not approve consensus implementation in this record. They only confirm that this P2 contract is a valid prerequisite.

---

## 4. Implementation authorization statement

This approval record does not authorize implementation.

The following work remains blocked:

```text id="approval-p2-mailbox-auth-001"
code changes for awaiting_message
code changes for awaiting_message_or_timeout
ReceiveBlock durable classifier changes
ActorRuntime mailbox injection changes
new mailbox_wait validation module
P3c-N implementation
consensus mailbox vote delivery
receive-based vote collection
network or daemon transport
durable timer service
persistent durable inbox
```

Implementation may begin only after this document is updated with:

```text id="approval-p2-mailbox-auth-002"
Status: APPROVED FOR IMPLEMENTATION
```

and after all approval sections are completed.

---

## 5. Approved implementation scope after approval

If this RFC is approved, the implementation PR is expected to be limited to:

```text id="approval-p2-mailbox-allowlist-001"
synapse/application.py
synapse/runtime/actor_runtime.py
synapse/runtime/mailbox_wait.py
tests/test_durable_mailbox_wait.py
```

Additional test files may be touched only if they are existing durable execution regression suites directly affected by the implementation.

---

## 6. Implementation scope denylist

The implementation PR must not touch:

```text id="approval-p2-mailbox-denylist-001"
parser
lexer
AST node definitions
synapse/runtime/consensus_engine.py
synapse/runtime/consensus_ticket_resolution.py
network transport
daemon transport
public ticket APIs
P3 evidence documents
P3 capability matrix
examples
workflow configuration
production distributed consensus documentation
```

Any expansion of this denylist requires a new approval record or an explicit amendment to this approval record.

---

## 7. Contract decisions to be finalized

### 7.1 Supported reasons

Pending approval:

```text id="approval-p2-mailbox-contract-001"
awaiting_message
awaiting_message_or_timeout
```

Decision required:

```text id="approval-p2-mailbox-contract-002"
[ ] approve both reasons
[ ] approve awaiting_message only
[ ] approve neither
[ ] return RFC for revision
```

### 7.2 Artifact schema

Proposed decision:

```text id="approval-p2-mailbox-contract-003"
Preserve artifact_schema_version = 1.0.0 if mailbox wait is implemented without new required top-level artifact fields.
```

Decision required:

```text id="approval-p2-mailbox-contract-004"
[ ] preserve 1.0.0
[ ] require schema bump
[ ] return RFC for artifact migration design
```

### 7.3 External resume message schema

Proposed decision:

```text id="approval-p2-mailbox-contract-005"
External mailbox_message resume schema uses message.args only.
External message.payload is rejected.
Internal payload may be derived after validation.
```

Decision required:

```text id="approval-p2-mailbox-contract-006"
[ ] approve args-only external schema
[ ] reject and return RFC for revision
```

### 7.4 Hash profile

Proposed decision:

```text id="approval-p2-mailbox-contract-007"
Mailbox wait reasons use normalized reason-specific signal hash.
Raw full-signal hashing is prohibited for awaiting_message and awaiting_message_or_timeout.
```

Decision required:

```text id="approval-p2-mailbox-contract-008"
[ ] approve normalized reason-specific signal hash
[ ] reject and return RFC for revision
```

### 7.5 Persisted mailbox policy

Proposed decision:

```text id="approval-p2-mailbox-contract-009"
No early durable inbox in this scope.
No ghost mailbox message may be consumed without current resume payload or recorded execution_history.
```

Decision required:

```text id="approval-p2-mailbox-contract-010"
[ ] approve no-early-delivery policy
[ ] require durable inbox RFC first
[ ] return RFC for revision
```

### 7.6 Timeout policy

Proposed decision:

```text id="approval-p2-mailbox-contract-011"
Timeout is externally resolved by mailbox_timeout resume payload.
No wall-clock scheduler.
No internal timer service.
```

Decision required:

```text id="approval-p2-mailbox-contract-012"
[ ] approve external timeout injection
[ ] require durable timer RFC first
[ ] return RFC for revision
```

### 7.7 ReceiveBlock classifier policy

Proposed decision:

```text id="approval-p2-mailbox-contract-013"
Allow only constrained single-pattern ReceiveBlock after approval.
ReceiveBlock.timeout must pass deterministic timeout-expression validation.
ReceivePattern body and else_body must recursively pass durable AST validation.
```

Decision required:

```text id="approval-p2-mailbox-contract-014"
[ ] approve constrained ReceiveBlock support
[ ] reject ReceiveBlock support
[ ] return RFC for revision
```

### 7.8 Promise identity policy

Proposed decision:

```text id="approval-p2-mailbox-contract-015"
Mailbox wait suspensions do not own promise_id.
active_suspension.promise_id must be null for awaiting_message and awaiting_message_or_timeout.
```

Decision required:

```text id="approval-p2-mailbox-contract-016"
[ ] approve null promise_id policy
[ ] return RFC for revision
```

---

## 8. Stop-gates clearance checklist

Implementation cannot begin until all applicable stop-gates from the RFC are cleared.

```text id="approval-p2-mailbox-stop-001"
[ ] ASYNC_MAILBOX_WAIT_APPROVAL_MISSING
[ ] RECEIVEBLOCK_DURABLE_CLASSIFICATION_UNDEFINED
[ ] RECEIVECONTRACT_DURABLE_PATTERN_RULES_UNDEFINED
[ ] AWAITING_MESSAGE_REASON_UNREGISTERED
[ ] AWAITING_MESSAGE_OR_TIMEOUT_REASON_UNREGISTERED
[ ] MAILBOX_WAIT_ARTIFACT_CONTRACT_UNDEFINED
[ ] MAILBOX_MESSAGE_RESUME_SCHEMA_UNDEFINED
[ ] MAILBOX_TIMEOUT_RESUME_SCHEMA_UNDEFINED
[ ] EXTERNAL_MESSAGE_PAYLOAD_FIELD_NOT_REJECTED
[ ] DERIVED_INTERNAL_PAYLOAD_POLICY_UNDEFINED
[ ] RECEIVE_TIMEOUT_EXPRESSION_PURITY_UNDEFINED
[ ] RECEIVE_TIMEOUT_EXPRESSION_NOT_DURABLY_VALIDATED
[ ] MAILBOX_SIGNAL_HASH_PROFILE_UNDEFINED
[ ] MESSAGE_IDENTITY_HASH_UNDEFINED
[ ] MESSAGE_ARGS_HASH_UNDEFINED
[ ] SAME_MESSAGE_ID_DIFFERENT_ARGS_POLICY_UNDEFINED
[ ] MAILBOX_INJECTION_STRICT_JSON_VALIDATION_UNDEFINED
[ ] MAILBOX_APPEND_BYPASSES_CANONICAL_JSON_VALIDATION
[ ] MULTI_CYCLE_MAILBOX_REPLAY_CURSOR_SEMANTICS_UNDEFINED
[ ] DURABLE_RECEIVE_PATTERN_MATCHING_UNDEFINED
[ ] MULTI_PATTERN_RECEIVE_UNSUPPORTED
[ ] ELSE_BODY_DURABLE_VALIDATION_UNDEFINED
[ ] MAILBOX_PROMISE_ID_POLICY_UNDEFINED
[ ] PERSISTED_MAILBOX_LIVE_CONSUMPTION_UNDEFINED
[ ] GHOST_MAILBOX_MESSAGE_CONSUMED_WITHOUT_RESUME
[ ] EARLY_MAILBOX_DELIVERY_SEMANTICS_REQUIRED
[ ] DURABLE_INBOX_CONTRACT_UNDEFINED
[ ] TIMEOUT_RESUME_CONFLICT_POLICY_UNDEFINED
[ ] P2_ARTIFACT_SCHEMA_MIGRATION_UNDECIDED
[ ] BACKGROUND_WORKER_OR_DAEMON_REQUIRED
[ ] NETWORK_DELIVERY_REQUIRED
[ ] WALL_CLOCK_SCHEDULER_REQUIRED
[ ] PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

A checked item means the approval record has decided the issue, not that code has implemented it.

---

## 9. Required test plan after approval

The implementation PR must include tests covering:

```text id="approval-p2-mailbox-tests-001"
1. durable validation for ReceiveBlock;
2. rejection of multi-pattern receive;
3. rejection of nondeterministic timeout expressions;
4. recursive validation of ReceivePattern body;
5. recursive validation of else_body;
6. PENDING artifact for awaiting_message;
7. PENDING artifact for awaiting_message_or_timeout;
8. null promise_id for mailbox reasons;
9. valid mailbox_message resume;
10. rejection of external message.payload;
11. derived internal payload behavior;
12. valid mailbox_timeout resume;
13. receiver mismatch rejection;
14. strict JSON rejection;
15. normalized signal hash idempotency;
16. same message_id with different args rejection;
17. sequential mailbox suspension replay cursor behavior;
18. ghost mailbox message rejection;
19. compatibility with existing SuspendExpr / AwaitExpr / LLMCall durable tests;
20. compatibility with P3c-2 ticket resolution tests.
```

---

## 10. Explicit non-claims

This approval record does not claim or authorize:

```text id="approval-p2-mailbox-nonclaims-001"
production distributed consensus
P3c-N implementation
mailbox-backed consensus voting
network delivery
daemon delivery
durable timers
wall-clock scheduler
persistent durable inbox
early signal delivery
multi-pattern receive matching
public ticket API
live LLM vote production
parser expansion
AST expansion
lexer expansion
```

---

## 11. Required approval metadata

Before status can become `APPROVED FOR IMPLEMENTATION`, this document must include:

```text id="approval-p2-mailbox-metadata-001"
final RFC content hash
approval record content hash
base SHA
approval date
approver identities
final implementation allowlist
final implementation denylist
final stop-gate clearance table
accepted test plan
explicit non-claims
```

---

## 12. Current final state

Current state:

```text id="approval-p2-mailbox-final-001"
DRAFT — APPROVAL NOT GRANTED
IMPLEMENTATION BLOCKED
P3c-N BLOCKED
```

No code PR may start from this document until the approval state changes.
