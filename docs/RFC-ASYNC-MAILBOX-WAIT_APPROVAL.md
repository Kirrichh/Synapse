# RFC-ASYNC-MAILBOX-WAIT APPROVAL RECORD

## Approval Gate for Durable Lifecycle of Mailbox Receive Waiting

**RFC ID:** RFC-ASYNC-MAILBOX-WAIT  
**Approval record ID:** RFC-ASYNC-MAILBOX-WAIT_APPROVAL  
**Status:** APPROVED FOR IMPLEMENTATION  
**Implementation authorization:** YES — limited to the approved P2 mailbox wait scope in this document  
**Patch type:** documentation-only approval gate  
**Approval base SHA:** `dd0079442c7514a754d1725e6a07f701bb7bd564`  
**Approved RFC content blob SHA:** `1273e847e7dbc84bec92726c778eec75adf9617a`  
**Approved RFC PR:** #47 — `docs: define P2 mailbox wait durable lifecycle RFC`  
**Target implementation PR:** TO BE CREATED AFTER THIS APPROVAL RECORD IS MERGED  
**Target future blocked capability:** P3c-N — mailbox-backed vote delivery and receive-based vote collection  
**Approval record purpose:** Record the architecture decision required before any implementation of `awaiting_message` or `awaiting_message_or_timeout` is permitted in P2 durable execution.

---

## 1. Decision status

Current decision state:

```text id="approval-p2-mailbox-status-001"
APPROVED FOR IMPLEMENTATION
```

Implementation is authorized only after this approval record is merged.

Decision checklist:

```text id="approval-p2-mailbox-status-002"
[x] APPROVED FOR IMPLEMENTATION
[ ] REJECTED
[ ] RETURNED FOR REVISION
```

This approval does not authorize P3c-N implementation. It authorizes only the P2 mailbox wait durable lifecycle implementation defined by `RFC-ASYNC-MAILBOX-WAIT` and constrained by this approval record.

---

## 2. Explicit approved decisions

| Decision area | Approved decision | Approval status |
|---|---|---|
| `awaiting_message` support | Support as externally resolved mailbox receive boundary | APPROVED |
| `awaiting_message_or_timeout` support | Support as externally resolved mailbox receive or external timeout boundary | APPROVED |
| Artifact schema version | Preserve `artifact_schema_version = 1.0.0` if no new required top-level artifact fields are introduced | APPROVED |
| External message schema | Args-only external schema; `message.payload` rejected in resume input | APPROVED |
| Internal payload policy | Runtime may derive internal `payload = args[0] if len(args) == 1 else args` after validation | APPROVED |
| Mailbox hash profile | Normalized reason-specific mailbox signal hash mandated | APPROVED |
| Raw full-signal hash | Prohibited for mailbox wait reasons | APPROVED |
| Persisted mailbox policy | No early durable inbox; no ghost mailbox consumption without current resume payload | APPROVED |
| `ReceiveBlock` classifier update | Allow only constrained single-pattern receive | APPROVED |
| `ReceivePattern` classifier update | Allow only inside approved constrained `ReceiveBlock` | APPROVED |
| Timeout expression validation | Dedicated deterministic timeout-expression validator required | APPROVED |
| `else_body` validation | Recursive durable AST validation required | APPROVED |
| `promise_id` policy | `active_suspension.promise_id` must be null for mailbox wait reasons | APPROVED |
| Timeout ownership | External timeout injection only; no runtime wall-clock scheduler | APPROVED |
| Dedicated validation module | Prefer `synapse/runtime/mailbox_wait.py` | APPROVED |
| P3c-N authorization | Still blocked until P2 mailbox wait implementation is completed and evidenced | BLOCKED |

---

## 3. Required approver areas

This RFC required review and approval from the following ownership areas:

```text id="approval-p2-mailbox-approvers-001"
1. P2 durable lifecycle owner
2. replay / history integrity reviewer
3. ActorRuntime owner
4. independent architecture reviewer
5. future P3c-N domain reviewer for compatibility only
```

Approval is recorded by the merge of this approval record. The merge metadata is the authoritative approval signature for this stage.

The future P3c-N reviewer does not approve consensus implementation in this record. They only confirm that this P2 contract is a valid prerequisite.

---

## 4. Implementation authorization statement

This approval record authorizes a future implementation PR only for the approved P2 mailbox wait durable lifecycle contract.

Authorized work:

```text id="approval-p2-mailbox-auth-001"
registering awaiting_message as a supported durable suspension reason
registering awaiting_message_or_timeout as a supported durable suspension reason
constrained ReceiveBlock durable classifier support
scoped ReceivePattern validation inside approved ReceiveBlock
strict mailbox_message resume validation
strict mailbox_timeout resume validation
reason-specific normalized mailbox signal hash calculation
receiver binding validation
external message.payload rejection
derived internal payload construction from args
no-ghost-mailbox enforcement
external timeout injection handling
sequential mailbox suspension replay correctness
contract tests for the approved behavior
```

Still blocked:

```text id="approval-p2-mailbox-auth-002"
P3c-N implementation
consensus mailbox vote delivery
receive-based consensus vote collection
network or daemon transport
durable timer service
wall-clock scheduler
persistent durable inbox
early mailbox delivery
multi-pattern receive matching
parser / lexer / AST expansion
production distributed consensus claims
```

---

## 5. Approved implementation scope

The implementation PR is limited to:

```text id="approval-p2-mailbox-allowlist-001"
synapse/application.py
synapse/runtime/actor_runtime.py
synapse/runtime/mailbox_wait.py
tests/test_durable_mailbox_wait.py
```

Additional test files may be touched only if they are existing durable execution regression suites directly affected by the implementation and the PR body explains why the additional test file is required.

No production/source changes outside this allowlist are approved by this record.

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

## 7. Final contract decisions

### 7.1 Supported reasons

Approved supported reasons:

```text id="approval-p2-mailbox-contract-001"
awaiting_message
awaiting_message_or_timeout
```

Decision:

```text id="approval-p2-mailbox-contract-002"
[x] approve both reasons
[ ] approve awaiting_message only
[ ] approve neither
[ ] return RFC for revision
```

### 7.2 Artifact schema

Approved decision:

```text id="approval-p2-mailbox-contract-003"
Preserve artifact_schema_version = 1.0.0 if mailbox wait is implemented without new required top-level artifact fields.
```

Decision:

```text id="approval-p2-mailbox-contract-004"
[x] preserve 1.0.0
[ ] require schema bump
[ ] return RFC for artifact migration design
```

If implementation requires new required top-level artifact fields, implementation must stop and request a schema migration approval.

### 7.3 External resume message schema

Approved decision:

```text id="approval-p2-mailbox-contract-005"
External mailbox_message resume schema uses message.args only.
External message.payload is rejected.
Internal payload may be derived after validation.
```

Decision:

```text id="approval-p2-mailbox-contract-006"
[x] approve args-only external schema
[ ] reject and return RFC for revision
```

### 7.4 Hash profile

Approved decision:

```text id="approval-p2-mailbox-contract-007"
Mailbox wait reasons use normalized reason-specific signal hash.
Raw full-signal hashing is prohibited for awaiting_message and awaiting_message_or_timeout.
```

Decision:

```text id="approval-p2-mailbox-contract-008"
[x] approve normalized reason-specific signal hash
[ ] reject and return RFC for revision
```

### 7.5 Persisted mailbox policy

Approved decision:

```text id="approval-p2-mailbox-contract-009"
No early durable inbox in this scope.
No ghost mailbox message may be consumed without current resume payload or recorded execution_history.
```

Decision:

```text id="approval-p2-mailbox-contract-010"
[x] approve no-early-delivery policy
[ ] require durable inbox RFC first
[ ] return RFC for revision
```

### 7.6 Timeout policy

Approved decision:

```text id="approval-p2-mailbox-contract-011"
Timeout is externally resolved by mailbox_timeout resume payload.
No wall-clock scheduler.
No internal timer service.
```

Decision:

```text id="approval-p2-mailbox-contract-012"
[x] approve external timeout injection
[ ] require durable timer RFC first
[ ] return RFC for revision
```

### 7.7 ReceiveBlock classifier policy

Approved decision:

```text id="approval-p2-mailbox-contract-013"
Allow only constrained single-pattern ReceiveBlock.
ReceiveBlock.timeout must pass deterministic timeout-expression validation.
ReceivePattern body and else_body must recursively pass durable AST validation.
```

Decision:

```text id="approval-p2-mailbox-contract-014"
[x] approve constrained ReceiveBlock support
[ ] reject ReceiveBlock support
[ ] return RFC for revision
```

### 7.8 Promise identity policy

Approved decision:

```text id="approval-p2-mailbox-contract-015"
Mailbox wait suspensions do not own promise_id.
active_suspension.promise_id must be null for awaiting_message and awaiting_message_or_timeout.
```

Decision:

```text id="approval-p2-mailbox-contract-016"
[x] approve null promise_id policy
[ ] return RFC for revision
```

---

## 8. Stop-gates clearance checklist

Implementation may begin after this approval record is merged because the RFC has resolved the architecture decisions below. A checked item means the design issue is cleared for implementation, not that code has implemented it.

```text id="approval-p2-mailbox-stop-001"
[x] ASYNC_MAILBOX_WAIT_APPROVAL_MISSING
[x] RECEIVEBLOCK_DURABLE_CLASSIFICATION_UNDEFINED
[x] RECEIVECONTRACT_DURABLE_PATTERN_RULES_UNDEFINED
[x] AWAITING_MESSAGE_REASON_UNREGISTERED
[x] AWAITING_MESSAGE_OR_TIMEOUT_REASON_UNREGISTERED
[x] MAILBOX_WAIT_ARTIFACT_CONTRACT_UNDEFINED
[x] MAILBOX_MESSAGE_RESUME_SCHEMA_UNDEFINED
[x] MAILBOX_TIMEOUT_RESUME_SCHEMA_UNDEFINED
[x] EXTERNAL_MESSAGE_PAYLOAD_FIELD_NOT_REJECTED
[x] DERIVED_INTERNAL_PAYLOAD_POLICY_UNDEFINED
[x] RECEIVE_TIMEOUT_EXPRESSION_PURITY_UNDEFINED
[x] RECEIVE_TIMEOUT_EXPRESSION_NOT_DURABLY_VALIDATED
[x] MAILBOX_SIGNAL_HASH_PROFILE_UNDEFINED
[x] MESSAGE_IDENTITY_HASH_UNDEFINED
[x] MESSAGE_ARGS_HASH_UNDEFINED
[x] SAME_MESSAGE_ID_DIFFERENT_ARGS_POLICY_UNDEFINED
[x] MAILBOX_INJECTION_STRICT_JSON_VALIDATION_UNDEFINED
[x] MAILBOX_APPEND_BYPASSES_CANONICAL_JSON_VALIDATION
[x] MULTI_CYCLE_MAILBOX_REPLAY_CURSOR_SEMANTICS_UNDEFINED
[x] DURABLE_RECEIVE_PATTERN_MATCHING_UNDEFINED
[x] MULTI_PATTERN_RECEIVE_UNSUPPORTED
[x] ELSE_BODY_DURABLE_VALIDATION_UNDEFINED
[x] MAILBOX_PROMISE_ID_POLICY_UNDEFINED
[x] PERSISTED_MAILBOX_LIVE_CONSUMPTION_UNDEFINED
[x] GHOST_MAILBOX_MESSAGE_CONSUMED_WITHOUT_RESUME
[x] EARLY_MAILBOX_DELIVERY_SEMANTICS_REQUIRED
[x] DURABLE_INBOX_CONTRACT_UNDEFINED
[x] TIMEOUT_RESUME_CONFLICT_POLICY_UNDEFINED
[x] P2_ARTIFACT_SCHEMA_MIGRATION_UNDECIDED
[x] BACKGROUND_WORKER_OR_DAEMON_REQUIRED
[x] NETWORK_DELIVERY_REQUIRED
[x] WALL_CLOCK_SCHEDULER_REQUIRED
[x] PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

Implementation must still prove each behavior with tests and evidence.

---

## 9. Required test plan for the implementation PR

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

The implementation PR is not complete until the required tests pass and the PR body reports the executed commands and counts.

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

## 11. Required implementation PR evidence

The future implementation PR must include:

```text id="approval-p2-mailbox-evidence-001"
base SHA
head SHA
changed files
commands run
test counts
known failures
new failures
scope summary
explicit non-claims
confirmation that P3c-N remains blocked
```

After the implementation is merged, a separate evidence patch must record the final merge SHA, test evidence, and capability impact. The capability matrix must not mark production distributed consensus as complete because this approval concerns P2 mailbox wait mechanics only.

---

## 12. Current final state

Current state after this approval record is merged:

```text id="approval-p2-mailbox-final-001"
APPROVED FOR IMPLEMENTATION
IMPLEMENTATION AUTHORIZED ONLY FOR P2 MAILBOX WAIT SCOPE
P3c-N REMAINS BLOCKED
```

The next allowed implementation stage is limited to the approved P2 mailbox wait durable lifecycle contract.
