# RFC-ASYNC-MAILBOX-WAIT

## Durable Lifecycle for Mailbox Receive Waiting

**Program:** Synapse Runtime Capability Integrity Program  
**RFC ID:** RFC-ASYNC-MAILBOX-WAIT  
**Stage:** P2 durable lifecycle expansion prerequisite for future P3c-N  
**Status:** DRAFT / REVIEW REQUIRED / NOT APPROVED FOR IMPLEMENTATION  
**Implementation authorization:** NO  
**Base SHA:** `5088587b0fd3757e30fa6cb92c5ec1ddf6750461`  
**Patch type:** documentation-only  
**Primary owner:** P2 durable execution lifecycle  
**Primary implementation owner after approval:** `synapse/application.py`  
**Secondary runtime owner after approval:** `synapse/runtime/actor_runtime.py`  
**Recommended validation module after approval:** `synapse/runtime/mailbox_wait.py`  
**Blocked future stage:** P3c-N — mailbox-backed vote delivery and receive-based vote collection  
**Non-claim:** This RFC does not implement production distributed consensus, mailbox-backed consensus voting, network delivery, daemon delivery, persistent signal inbox, early signal delivery, wall-clock scheduler, or durable timer service.

---

## 1. Purpose

This RFC defines the durable lifecycle contract required before mailbox-based receive waiting can become a supported P2 durable execution boundary.

The runtime already contains local actor mailbox mechanics. Actors can be spawned. Messages can be sent to a local actor mailbox. A receive block can consume a message and record `message_received`. A receive block with timeout can record `receive_timeout`. Async receive can yield a suspension when the target mailbox is empty.

However, this local runtime behavior is not currently authorized as a P2 durable boundary. The P2 durable path currently rejects `ReceiveBlock` and `ReceivePattern` during durable AST validation, and the supported durable suspension reason set does not include `awaiting_message` or `awaiting_message_or_timeout`.

This RFC answers the P2 lifecycle question:

```text id="rfc-p2-mailbox-purpose-001"
How should a durable run save, resume, validate, replay, and commit a mailbox receive wait boundary?
```

This RFC does not answer the future P3c-N consensus question:

```text id="rfc-p2-mailbox-purpose-002"
How should distributed consensus use actor mailboxes to deliver vote requests and collect vote responses?
```

P3c-N remains blocked until this P2 contract is approved and implemented.

---

## 2. Current code facts

### 2.1 Supported P2 suspension reasons

The current P2 durable execution path supports only:

```text id="rfc-p2-mailbox-code-001"
awaiting_external_signal
awaiting_promise
awaiting_llm
```

The current supported reason set does not include:

```text id="rfc-p2-mailbox-code-002"
awaiting_message
awaiting_message_or_timeout
```

Therefore, a durable run or durable resume that reaches an async mailbox wait boundary cannot currently be accepted as a supported durable lifecycle step.

### 2.2 ReceiveBlock durable classification

The current durable classifier rejects:

```text id="rfc-p2-mailbox-code-003"
ReceiveBlock
ReceivePattern
```

`ReceiveBlock` is classified as an unsupported execution-engine node because message receive suspension is deferred. `ReceivePattern` is also classified as unsupported.

This means a future implementation cannot simply add two suspension reason strings. It must also update durable AST validation.

The required current code surfaces are:

```text id="rfc-p2-mailbox-code-004"
_DURABLE_AST_CLASSIFICATIONS
_DURABLE_UNSUPPORTED_CLASSIFICATIONS
_validate_durable_ast
_validate_node
```

If the implementation chooses to introduce a dedicated classifier object or class, that refactor must be explicitly approved. This RFC does not require a new classifier class. It requires a clear extension of the existing durable AST validation mechanism.

### 2.3 Existing local mailbox mechanics

The current actor runtime already contains local mailbox mechanics:

```text id="rfc-p2-mailbox-code-005"
spawn_actor
send_message
evaluate_receive
evaluate_async_receive
message_sent
message_forwarded
message_received
receive_timeout
mailboxes
actor_log
execution_history
```

The local `send_message` path appends to a local mailbox for local receivers and records `message_sent` in execution history. For non-local receivers it builds a forward packet and records `message_forwarded`.

The sync receive path can replay `message_received` and `receive_timeout` from recorded history. When live and a mailbox contains messages, it consumes FIFO and records `message_received`.

The async receive path, when the target mailbox is empty, yields a suspension with one of these reasons:

```text id="rfc-p2-mailbox-code-006"
awaiting_message
awaiting_message_or_timeout
```

### 2.4 Existing strict JSON validation

The durable application layer already contains strict JSON validation and projection helpers. These validators reject:

```text id="rfc-p2-mailbox-code-007"
non-string mapping keys
non-finite floating point numbers
tuples as persisted values
cycles
unsupported host objects
```

The resume signal input path already reads signal JSON through strict JSON parsing and validates it before computing a signal hash.

Mailbox resume must preserve this strict JSON discipline. No mailbox resume path may append an unvalidated host object directly into `mailboxes`.

### 2.5 Existing boundary fingerprint and suspension identity

The current durable boundary projection computes a payload hash and a boundary fingerprint from:

```text id="rfc-p2-mailbox-code-008"
source_hash
initial_bindings_hash
history_event_count
history_hash
reason
node_type
line
column
promise_id
payload_hash
output_line_count
output_digest
```

The `suspension_id` is derived from:

```text id="rfc-p2-mailbox-code-009"
run_id
suspension sequence
boundary_fingerprint
```

A mailbox wait boundary does not require a new primary boundary identity model. However, mailbox-specific metadata may still be added as a validation guard inside the suspension payload.

### 2.6 Promise identity for mailbox waits

Current promise extraction applies to `awaiting_external_signal` and `awaiting_promise`. Mailbox wait suspensions do not own a promise.

For mailbox wait reasons:

```text id="rfc-p2-mailbox-code-010"
active_suspension.promise_id MUST be null
```

The boundary fingerprint remains valid because it still includes reason, node type, source location, payload hash, history hash, output digest, and suspension sequence.

### 2.7 Existing idempotency model

P2 currently stores resolved suspensions under:

```text id="rfc-p2-mailbox-code-011"
idempotency.resolved_suspensions[suspension_id]
```

Each resolved entry contains:

```text id="rfc-p2-mailbox-code-012"
signal_hash
committed_revision
committed_status
operation_result
```

If the same `suspension_id` is resumed again with the same `signal_hash`, the stored operation result is returned. If the same `suspension_id` is resumed with a different `signal_hash`, durable resume returns a resolution conflict.

This model remains the primary P2 idempotency mechanism. Mailbox waits need a reason-specific signal hash profile, but they must not replace the existing P2 suspension idempotency contract.

### 2.8 Existing replay reconstruction model

During durable resume, the application reconstructs the boundary by:

```text id="rfc-p2-mailbox-code-013"
1. loading the committed artifact;
2. validating artifact integrity;
3. compiling embedded source;
4. validating durable AST;
5. loading replay_state into Interpreter;
6. applying initial bindings;
7. running interpret_async from the beginning;
8. consuming recorded history through replay;
9. requiring replay_cursor == len(execution_history);
10. requiring transition into LIVE at the active suspension boundary;
11. recomputing and comparing the observed boundary fingerprint.
```

Any mailbox wait design must preserve this replay model.

---

## 3. External durable workflow principles

This RFC follows durable workflow principles visible in mature orchestration systems.

### 3.1 Ordered event history as source of truth

A durable workflow replay model must use ordered event history as the source of truth. Replay should re-run workflow code and guide it with recorded events, not repeat live external effects.

For this RFC, mailbox wait replay must consume recorded `message_received` or `receive_timeout` events at the expected boundary. It must not inspect an external mailbox, poll a live queue, or call remote systems during replay.

### 3.2 Deterministic workflow decisions

Workflow code must make the same decisions when given the same recorded history. It must not depend on unrecorded current time, random values, network calls, live LLM calls, or mutable host state.

For this RFC, `ReceiveBlock.timeout` must be validated as deterministic before receive wait is allowed in durable mode.

### 3.3 External events as one-way asynchronous delivery

Durable systems often model external events as one-way asynchronous operations. A running workflow can wait for an external event. An external caller can deliver an event. The caller must not expect a synchronous application-level response from the workflow.

For this RFC, mailbox message delivery into a waiting durable run is a one-way resume operation. It resolves one durable boundary and lets the run continue. It does not provide synchronous actor RPC.

### 3.4 Durable timers and timeout decisions

Durable systems model timers as recorded events. They do not rely on native process sleep or wall-clock checks during replay.

This RFC does not introduce a durable timer service. Timeout for `awaiting_message_or_timeout` is represented as an externally supplied resume decision in this stage. A later RFC may define durable timers, scheduler ownership, or wall-clock timeout delivery.

### 3.5 Duplicate external delivery

External delivery can be repeated. Duplicate events and repeated resume attempts must have deterministic idempotency behavior.

For this RFC, idempotency exists at two layers:

```text id="rfc-p2-mailbox-principles-001"
1. P2 suspension idempotency:
   suspension_id + reason-specific signal hash

2. mailbox message identity:
   message_id + actor + sender + receiver + method + args_hash
```

---

## 4. Goals

This RFC defines the contract for supporting mailbox receive waiting in P2 durable execution.

The goals are:

```text id="rfc-p2-mailbox-goals-001"
1. Define awaiting_message as a supported durable suspension reason.
2. Define awaiting_message_or_timeout as a supported durable suspension reason.
3. Define the active suspension payload for mailbox waits.
4. Define resume payload schemas for mailbox message injection.
5. Define resume payload schemas for mailbox timeout injection.
6. Define strict JSON validation rules before any mailbox append.
7. Define deterministic replay semantics for message_received and receive_timeout.
8. Define idempotency and conflict semantics.
9. Define receiver binding validation.
10. Define timeout expression purity requirements.
11. Define single-pattern ReceiveBlock restrictions.
12. Define recursive validation for ReceivePattern body and ReceiveBlock else_body.
13. Define persisted mailbox behavior at the replay-to-live boundary.
14. Preserve existing P2 artifact schema if no incompatible top-level artifact fields are required.
15. Preserve P2a/P2b/P2c guarantees.
16. Preserve P3c-2 ticket resolution semantics.
17. Keep P3c-N blocked until this RFC is approved and implemented.
```

---

## 5. Non-goals

This RFC does not define or implement:

```text id="rfc-p2-mailbox-nongoals-001"
resident daemon
background worker
internal timer service
wall-clock timeout decisions
network delivery
production distributed consensus protocol
persistent signal inbox
early signals
delivery to durable runs that are not currently waiting
durable inbox
multiple simultaneously active durable suspensions
parallel receive waits
durable actor scheduler
remote mailbox transport
mailbox compaction
message retention policy
public actor mailbox API
public consensus ticket API
live LLM vote production
parser syntax expansion
AST node expansion
lexer token expansion
CVM durable execution
bytecode continuation
serialization of Python generator frames
exactly-once external effects
distributed artifact store
automatic stale-lock recovery
```

The first approved implementation of this RFC must not claim that the runtime supports general durable inbox behavior. It supports only externally resolved mailbox wait at the active durable suspension boundary.

---

## 6. Architectural ownership

### 6.1 P2 durable lifecycle owner

`application.py` owns:

```text id="rfc-p2-mailbox-ownership-001"
supported durable suspension reasons
durable AST validation
artifact schema validation
artifact persistence
artifact locking
replay reconstruction
resume signal loading
reason-specific signal hash dispatch
idempotency storage
public result payloads
exit code mapping
```

Any support for `awaiting_message` or `awaiting_message_or_timeout` must be authorized here.

### 6.2 ActorRuntime owner

`ActorRuntime` owns:

```text id="rfc-p2-mailbox-ownership-002"
local mailbox mechanics
send_message
evaluate_receive
evaluate_async_receive
message_sent
message_received
receive_timeout
mailbox FIFO behavior
receive pattern execution
```

ActorRuntime may execute the mailbox receive boundary, but it must not own durable artifact schema, exit code policy, or cross-process idempotency.

### 6.3 Recommended mailbox validation module

The preferred implementation shape after approval is a dedicated runtime validation module:

```text id="rfc-p2-mailbox-ownership-003"
synapse/runtime/mailbox_wait.py
```

This module should own:

```text id="rfc-p2-mailbox-ownership-004"
mailbox resume schema constants
mailbox resume validation
normalized mailbox resume hash preimage
canonical internal message construction
receiver binding checks
timeout resume checks
stable validation errors
```

The implementation may choose another file name only if the approval record explicitly allows it.

### 6.4 Interpreter owner

`Interpreter` remains the execution adapter and state holder. It may hold mutable runtime state and perform replay/live transition through existing mechanisms. It must not become the owner of P2 artifact semantics, mailbox resume schemas, hash profiles, or consensus-domain semantics.

### 6.5 Consensus owner

`ConsensusEngine` owns consensus mathematics. This RFC does not move mailbox delivery into the consensus engine.

Future P3c-N may use the P2 mailbox wait contract. It must not require `ConsensusEngine` to own transport or mailbox lifecycle semantics.

---

## 7. Supported suspension reasons

After this RFC is approved and implemented, P2 may support:

```text id="rfc-p2-mailbox-reasons-001"
awaiting_message
awaiting_message_or_timeout
```

These reasons are valid only for durable receive waits created by `ReceiveBlock`.

### 7.1 awaiting_message

This reason means:

```text id="rfc-p2-mailbox-reasons-002"
The program reached a ReceiveBlock.
The current actor mailbox did not contain an authorized consumable message.
The receive block has no timeout expression.
The durable run is waiting for an external mailbox message injection.
```

### 7.2 awaiting_message_or_timeout

This reason means:

```text id="rfc-p2-mailbox-reasons-003"
The program reached a ReceiveBlock with a timeout expression.
The current actor mailbox did not contain an authorized consumable message.
The timeout expression was evaluated to a strict JSON-compatible deterministic value.
The durable run is waiting for either a mailbox message injection or an external timeout decision.
```

The runtime does not decide wall-clock timeout itself in this RFC.

---

## 8. Durable ReceiveBlock eligibility

`ReceiveBlock` remains unsupported unless it satisfies all constraints in this section.

### 8.1 Single-pattern restriction

The first approved scope supports only:

```text id="rfc-p2-mailbox-receive-001"
len(ReceiveBlock.patterns) == 1
```

A receive block with more than one pattern must be rejected by durable AST validation.

Reason:

```text id="rfc-p2-mailbox-receive-002"
ActorRuntime currently applies only the first receive pattern.
There is no durable matching contract for multiple receive patterns.
Supporting multiple patterns without a deterministic pattern-matching contract would create replay ambiguity.
```

Stop gates:

```text id="rfc-p2-mailbox-receive-003"
BLOCKED — DURABLE_RECEIVE_PATTERN_MATCHING_UNDEFINED
BLOCKED — MULTI_PATTERN_RECEIVE_UNSUPPORTED
```

### 8.2 Pattern structure

The single receive pattern may bind:

```text id="rfc-p2-mailbox-receive-004"
sender_var
target_var
body
```

The RFC does not introduce content-based pattern matching. The message delivered to the boundary is passed to the existing receive pattern environment.

### 8.3 Else body

`else_body` is allowed only when the receive block has a timeout expression.

If `else_body` exists without timeout, the durable classifier must reject the receive block unless a future RFC defines non-timeout else semantics.

Both the primary receive pattern body and `else_body`, if present, must recursively pass standard durable AST validation.

Unsupported effects inside `else_body` must reject the entire `ReceiveBlock` before execution.

The implementation must not allow a `ReceiveBlock` whose timeout branch can execute code that would be rejected elsewhere by durable validation.

### 8.4 Timeout expression purity

If `ReceiveBlock.timeout` exists, the durable classifier must validate its AST subtree as deterministic and durable-safe.

The implementation must extend the existing durable AST validation mechanism in `application.py`.

The required extension points are:

```text id="rfc-p2-mailbox-receive-005"
_DURABLE_AST_CLASSIFICATIONS
_DURABLE_UNSUPPORTED_CLASSIFICATIONS
_validate_node
a dedicated ReceiveBlock validation branch
a dedicated ReceivePattern validation branch or scoped ReceivePattern validation inside ReceiveBlock
a dedicated deterministic timeout-expression validator
```

The RFC does not require introducing a new classifier class unless the implementation PR explicitly chooses that refactor and it is approved.

The timeout expression validator must be stricter than generic durable `CallExpr` validation if the generic direct-call allowlist contains nondeterministic functions.

Allowed timeout expression forms should be restricted to existing pure durable expression classes, such as:

```text id="rfc-p2-mailbox-receive-006"
Literal
Variable
UnaryExpr
BinaryExpr
```

Composite forms such as `ListExpr` or `DictExpr` should be allowed only if the timeout contract needs them. The first implementation should prefer scalar deterministic timeout values.

The timeout expression must not contain:

```text id="rfc-p2-mailbox-receive-007"
LLMCall
SuspendExpr
AwaitExpr
CallExpr outside an approved deterministic timeout allowlist
random source
time source
uuid source
MemberAccess dynamic calls
PromptExpr if interpolation can include non-deterministic behavior
memory access
network access
host effects
```

If a timeout expression cannot be proven deterministic at durable validation time, durable run must fail before execution.

Recommended error classification:

```text id="rfc-p2-mailbox-receive-008"
UNSUPPORTED_EXECUTION_ENGINE
```

or, if a dedicated code is introduced by a future implementation RFC:

```text id="rfc-p2-mailbox-receive-009"
DURABLE_PURITY_VIOLATION
```

Stop gates:

```text id="rfc-p2-mailbox-receive-010"
BLOCKED — RECEIVE_TIMEOUT_EXPRESSION_PURITY_UNDEFINED
BLOCKED — RECEIVE_TIMEOUT_EXPRESSION_NOT_DURABLY_VALIDATED
```

---

## 9. Active suspension payload contract

Mailbox wait uses the existing P2 active suspension structure. The RFC does not introduce a new top-level artifact structure for mailbox waits.

The active suspension reason is one of:

```text id="rfc-p2-mailbox-payload-001"
awaiting_message
awaiting_message_or_timeout
```

The suspension payload must be strict JSON-compatible.

Recommended payload shape for `awaiting_message`:

```json id="rfc-p2-mailbox-payload-002"
{
  "mailbox_wait_schema": "synapse.mailbox.wait.v1",
  "actor": "ActorName#process",
  "timeout": null,
  "receive_shape": {
    "patterns": 1,
    "has_else": false
  }
}
```

Recommended payload shape for `awaiting_message_or_timeout`:

```json id="rfc-p2-mailbox-payload-003"
{
  "mailbox_wait_schema": "synapse.mailbox.wait.v1",
  "actor": "ActorName#process",
  "timeout": 10,
  "receive_shape": {
    "patterns": 1,
    "has_else": true
  }
}
```

### 9.1 Actor identity

The `actor` field must match the actor name or process identity used by `ActorRuntime.current_actor_name(env)` at the receive boundary.

The implementation must specify whether the canonical actor identifier is:

```text id="rfc-p2-mailbox-payload-004"
actor name
process_id
DurableActorRef.process_id
```

The first implementation should use the same identity currently used by `ActorRuntime.evaluate_async_receive` to choose the mailbox:

```text id="rfc-p2-mailbox-payload-005"
actor_name = current_actor_name(env)
mailboxes[actor_name]
```

If the actor identity model is ambiguous, implementation must stop.

Stop gate:

```text id="rfc-p2-mailbox-payload-006"
BLOCKED — MAILBOX_WAIT_ACTOR_IDENTITY_UNDEFINED
```

### 9.2 Receive shape

`receive_shape` is not the primary boundary identity. The existing boundary fingerprint already includes source hash, initial bindings hash, history hash, reason, node type, source location, payload hash, output digest, and suspension sequence.

`receive_shape` is a validation guard. It records the receive contract expected at this boundary:

```text id="rfc-p2-mailbox-payload-007"
exactly one pattern
whether else_body exists
```

If replay observes a receive boundary whose shape differs from the persisted payload, boundary reconstruction must fail.

### 9.3 Promise identity

For mailbox wait reasons:

```text id="rfc-p2-mailbox-payload-008"
active_suspension.promise_id MUST be null
```

Mailbox wait is not a durable promise boundary. It is a mailbox receive boundary.

---

## 10. Resume payload schemas

A mailbox wait can be resolved by one of two payload kinds:

```text id="rfc-p2-mailbox-resume-001"
mailbox_message
mailbox_timeout
```

### 10.1 Mailbox message resume payload

A message resume payload must be a strict JSON object.

The external resume signal must use an args-only message schema.

Required shape:

```json id="rfc-p2-mailbox-resume-002"
{
  "kind": "mailbox_message",
  "message_id": "msg-unique-stable-id",
  "actor": "ActorName#process",
  "message": {
    "sender": "SenderActor#process",
    "receiver": "ActorName#process",
    "method": "method_name",
    "args": [
      {
        "example": "payload"
      }
    ]
  }
}
```

The external resume signal must not include:

```text id="rfc-p2-mailbox-resume-003"
message.payload
```

Reason:

```text id="rfc-p2-mailbox-resume-004"
The external resume signal must not create two sources of truth for the same delivered message.
```

After validation, the runtime may construct the internal mailbox message with a derived `payload` field for compatibility with existing runtime receive behavior:

```text id="rfc-p2-mailbox-resume-005"
payload = args[0] if len(args) == 1 else args
```

This internal `payload` field is derived, never supplied by the external resume signal.

Required external fields:

```text id="rfc-p2-mailbox-resume-006"
kind
message_id
actor
message
message.sender
message.receiver
message.method
message.args
```

Validation rules:

```text id="rfc-p2-mailbox-resume-007"
kind must be mailbox_message
message_id must be a non-empty string
actor must match the active suspension actor
message.receiver must match the active suspension actor
message.sender must be a string
message.method must be a string
message.args must be a strict JSON list
message must not contain message.payload
message must not contain non-string mapping keys
message must not contain non-finite floats
message must not contain host objects
message must not exceed the existing signal size limit
```

The implementation must define an exact allowed field set and reject extra fields unless the approval record explicitly allows a forward-compatible envelope extension.

### 10.2 Mailbox timeout resume payload

A timeout resume payload must be a strict JSON object.

Required shape:

```json id="rfc-p2-mailbox-resume-008"
{
  "kind": "mailbox_timeout",
  "actor": "ActorName#process",
  "timeout": true
}
```

Validation rules:

```text id="rfc-p2-mailbox-resume-009"
kind must be mailbox_timeout
actor must match the active suspension actor
timeout must be true
active suspension reason must be awaiting_message_or_timeout
timeout resume is invalid for awaiting_message
```

A timeout resume must produce a `receive_timeout` event and must execute `else_body` if present.

The timeout value recorded in `receive_timeout` must be the timeout value from the active suspension payload, not a value trusted from the resume signal.

### 10.3 Invalid mixed payloads

The following are invalid:

```text id="rfc-p2-mailbox-resume-010"
mailbox_message with timeout: true
mailbox_timeout with message
mailbox_timeout for awaiting_message
mailbox_message with receiver mismatch
mailbox_message with actor mismatch
mailbox_message without message_id
mailbox_message with message.payload supplied externally
mailbox_message with non-JSON-safe args
```

---

## 11. Reason-specific signal hash and message identity profile

### 11.1 Existing P2 signal hash

Current P2 computes signal hash from the full strict JSON signal value.

For mailbox waits, raw full-signal hashing is prohibited because mailbox resume needs a stable reason-specific identity profile. Raw hashing of the entire resume signal can create false conflicts if non-semantic envelope variations are introduced.

### 11.2 Required decision: normalized mailbox signal hash

The implementation must use a reason-specific normalized signal hash for mailbox wait reasons.

For:

```text id="rfc-p2-mailbox-hash-001"
awaiting_message
awaiting_message_or_timeout
```

the `signal_hash` stored in:

```text id="rfc-p2-mailbox-hash-002"
idempotency.resolved_suspensions[suspension_id].signal_hash
```

must be computed from a normalized mailbox resume preimage, not from the raw full signal object.

### 11.3 mailbox_message hash preimage

For `mailbox_message`, the normalized preimage is:

```json id="rfc-p2-mailbox-hash-003"
{
  "schema": "synapse.mailbox.resume.hash.v1",
  "kind": "mailbox_message",
  "message_id": "msg-unique-stable-id",
  "actor": "ActorName#process",
  "sender": "SenderActor#process",
  "receiver": "ActorName#process",
  "method": "method_name",
  "args_hash": "sha256:..."
}
```

`args_hash` must be computed from the strict canonical JSON projection of:

```json id="rfc-p2-mailbox-hash-004"
{
  "schema": "synapse.mailbox.args.v1",
  "args": [
    {
      "example": "payload"
    }
  ]
}
```

### 11.4 mailbox_timeout hash preimage

For `mailbox_timeout`, the normalized preimage is:

```json id="rfc-p2-mailbox-hash-005"
{
  "schema": "synapse.mailbox.resume.hash.v1",
  "kind": "mailbox_timeout",
  "actor": "ActorName#process",
  "timeout": true
}
```

### 11.5 Duplicate and conflict rules

Required rules:

```text id="rfc-p2-mailbox-hash-006"
Same suspension_id + same normalized signal_hash:
    return prior operation result.

Same suspension_id + different normalized signal_hash:
    return resolution conflict.

Same message_id + same actor + same sender + same receiver + same method + same args_hash:
    same logical mailbox message.

Same message_id + same actor + same sender + same receiver + same method + different args_hash:
    fail closed.

Different message_id:
    distinct mailbox message at P2 level.
```

P3c-N may later define domain-level duplicate participant vote handling. P2 only defines mailbox delivery identity and boundary idempotency.

### 11.6 Artifact schema impact

This RFC intends to preserve the existing idempotency field set:

```text id="rfc-p2-mailbox-hash-007"
signal_hash
committed_revision
committed_status
operation_result
```

The field `signal_hash` remains the storage field. The mailbox-specific change is the hash preimage used to compute it.

If implementation cannot preserve the existing field set, it must stop and require artifact schema migration approval.

Stop gates:

```text id="rfc-p2-mailbox-hash-008"
BLOCKED — MAILBOX_SIGNAL_HASH_PROFILE_UNDEFINED
BLOCKED — MESSAGE_IDENTITY_HASH_UNDEFINED
BLOCKED — MESSAGE_ARGS_HASH_UNDEFINED
BLOCKED — SAME_MESSAGE_ID_DIFFERENT_ARGS_POLICY_UNDEFINED
BLOCKED — P2_ARTIFACT_SCHEMA_MIGRATION_UNDECIDED
```

---

## 12. Strict JSON injection requirement

No mailbox resume path may append unvalidated host objects to `mailboxes`.

Before a mailbox message is appended or converted into a `message_received` event, it must pass:

```text id="rfc-p2-mailbox-json-001"
strict JSON validation
strict JSON projection
receiver binding validation
message identity validation
args hash validation
size validation
external payload absence validation
```

The current generic resume signal parser already validates strict JSON input. The mailbox-specific implementation must preserve that guarantee when extracting the nested `message`.

The implementation must not do:

```text id="rfc-p2-mailbox-json-002"
mailbox.append(injected)
```

for mailbox wait without validating that `injected` is a canonical mailbox message envelope.

Required behavior:

```text id="rfc-p2-mailbox-json-003"
validate resume payload
project canonical args
construct canonical internal message
append canonical message to mailbox only if append is required by the execution model
record message_received event from canonical message
```

Stop gates:

```text id="rfc-p2-mailbox-json-004"
BLOCKED — MAILBOX_INJECTION_STRICT_JSON_VALIDATION_UNDEFINED
BLOCKED — MAILBOX_APPEND_BYPASSES_CANONICAL_JSON_VALIDATION
```

---

## 13. Replay semantics

### 13.1 General rule

Replay is ordered and boundary-local.

During replay, the runtime must consume recorded `message_received` and `receive_timeout` events exactly at the receive boundary where they were originally produced.

Replay must not:

```text id="rfc-p2-mailbox-replay-001"
search forward arbitrarily for a matching message_id
inspect external systems
perform network delivery
poll a daemon
wait for wall-clock timeout
generate a new receive_timeout
append a duplicate message_received event
consume ghost mailbox messages
```

### 13.2 Message replay

When replay reaches a receive boundary and the next replay-significant event is:

```text id="rfc-p2-mailbox-replay-002"
message_received
```

then:

```text id="rfc-p2-mailbox-replay-003"
event.actor must match the receive actor
event.message.receiver must match the receive actor
event.message must pass strict JSON validation
event.message.payload, if present, must be derivable from event.message.args
the event is consumed
the receive pattern body is executed with event.message
no mailbox append occurs during replay
```

### 13.3 Timeout replay

When replay reaches a receive boundary and the next replay-significant event is:

```text id="rfc-p2-mailbox-replay-004"
receive_timeout
```

then:

```text id="rfc-p2-mailbox-replay-005"
event.actor must match the receive actor
the receive block must have timeout
the event is consumed
else_body is executed if present
no wall-clock check occurs
```

### 13.4 Mismatch behavior

Replay must fail closed if:

```text id="rfc-p2-mailbox-replay-006"
expected message_received but found receive_timeout
expected receive_timeout but found message_received
expected receive event but history ended
event actor does not match active receive actor
message.receiver does not match active receive actor
event has malformed message shape
event has non-JSON-safe content
event order differs from recorded boundary
multiple patterns require undefined matching
```

Recommended public error category:

```text id="rfc-p2-mailbox-replay-007"
RESUME_BOUNDARY_MISMATCH
```

or a dedicated durable replay integrity error if one is introduced by the implementation RFC.

---

## 14. Replay cursor across sequential mailbox suspensions

The implementation must explicitly support multiple sequential mailbox waits in one actor lifecycle.

Example lifecycle:

```text id="rfc-p2-mailbox-seq-001"
run --durable:
  receive #1 with empty mailbox
  -> PENDING awaiting_message

resume #1 with mailbox_message A:
  reconstruct boundary #1
  append/record message_received A
  execute receive #1 body
  continue execution
  reach receive #2 with empty mailbox
  -> PENDING awaiting_message

resume #2 with mailbox_message B:
  replay from start
  consume prior message_received A
  reach boundary #2
  inject/record message_received B
  continue execution
```

Required invariants:

```text id="rfc-p2-mailbox-seq-002"
replay_cursor after reconstructing boundary #2 equals len(history_before_boundary_2)
message_received A is consumed once
message_received A is not appended again
message_received B is appended once
output_delta contains only new output after prior artifact output_state
suspension sequence increments exactly once per new active suspension
idempotency for suspension #1 remains intact after suspension #2
```

Stop gate:

```text id="rfc-p2-mailbox-seq-003"
BLOCKED — MULTI_CYCLE_MAILBOX_REPLAY_CURSOR_SEMANTICS_UNDEFINED
```

---

## 15. Persisted mailbox and ghost message policy

### 15.1 Current risk

`mailboxes` is already part of replay state. During boundary reconstruction, replay state is loaded into the interpreter. After replay catches up and transitions into LIVE, `evaluate_async_receive()` can inspect `mailboxes[actor]`.

If a mailbox already contains a message at that point, receive may consume it immediately without yielding a new suspension.

This can be a valid durable inbox model only if explicitly defined. It is not currently defined.

### 15.2 Chosen policy for this RFC

This RFC chooses the conservative policy:

```text id="rfc-p2-mailbox-ghost-001"
No durable early mailbox delivery in the first mailbox wait contract.
```

In this RFC, a newly reached live mailbox wait can be resolved only by the current validated resume payload for the active suspension.

A persisted mailbox message must not satisfy a newly reached live mailbox wait unless its consumption is already represented by recorded `execution_history`.

If a ghost mailbox message would be consumed without current resume payload and without recorded history, implementation must fail closed.

### 15.3 Future durable inbox

A future RFC may define:

```text id="rfc-p2-mailbox-ghost-002"
persistent durable inbox
early mailbox delivery
signal inbox
background delivery
message retention
message compaction
multiple pending messages for inactive runs
```

This RFC does not authorize those features.

Stop gates:

```text id="rfc-p2-mailbox-ghost-003"
BLOCKED — PERSISTED_MAILBOX_LIVE_CONSUMPTION_UNDEFINED
BLOCKED — GHOST_MAILBOX_MESSAGE_CONSUMED_WITHOUT_RESUME
BLOCKED — EARLY_MAILBOX_DELIVERY_SEMANTICS_REQUIRED
BLOCKED — DURABLE_INBOX_CONTRACT_UNDEFINED
```

---

## 16. Timeout semantics

### 16.1 External timeout decision

In this RFC, timeout is externally resolved.

The runtime does not evaluate wall-clock time. It does not start a timer. It does not wake itself. It does not require a daemon or scheduler.

A timeout occurs when the active suspension is resumed with:

```json id="rfc-p2-mailbox-timeout-001"
{
  "kind": "mailbox_timeout",
  "actor": "ActorName#process",
  "timeout": true
}
```

### 16.2 receive_timeout event

A timeout resume must append:

```json id="rfc-p2-mailbox-timeout-002"
{
  "type": "receive_timeout",
  "actor": "ActorName#process",
  "timeout": 10
}
```

The `timeout` field must be the deterministic timeout value from the active suspension payload, not a value trusted from the resume signal.

### 16.3 Timeout conflict

If a suspension is resumed once with a message and later with timeout, or once with timeout and later with a message, existing P2 idempotency/conflict rules apply.

If the same `suspension_id` already resolved to `message_received`, a later timeout resume is a conflict. If the same `suspension_id` already resolved to `receive_timeout`, a later message resume is a conflict.

Stop gate:

```text id="rfc-p2-mailbox-timeout-003"
BLOCKED — TIMEOUT_RESUME_CONFLICT_POLICY_UNDEFINED
```

---

## 17. Artifact schema policy

The current durable artifact schema is:

```text id="rfc-p2-mailbox-schema-001"
artifact_schema_version = 1.0.0
```

This RFC requires preserving schema version `1.0.0` if mailbox wait support can be represented using existing artifact fields:

```text id="rfc-p2-mailbox-schema-002"
active_suspension.reason
active_suspension.payload_hash
active_suspension.boundary_fingerprint
replay_state.mailboxes
replay_state.execution_history
idempotency.resolved_suspensions
terminal
output_state
```

If implementation requires new required top-level artifact fields, then schema version must change and a compatibility/migration policy must be approved.

### 17.1 Schema-preserving allowed changes

Schema-preserving implementation may add:

```text id="rfc-p2-mailbox-schema-003"
new supported reason values
new active_suspension payload content
reason-specific signal hash calculation
new strict validation rules
new allowed operation behavior
new receive events in execution_history
```

provided the artifact field set remains unchanged.

### 17.2 Schema-changing implementation

If implementation needs fields such as:

```text id="rfc-p2-mailbox-schema-004"
message_identity_index
deduplicated_messages
durable_inbox
timer_state
mailbox_delivery_log
```

then implementation must stop and require schema migration design.

Stop gate:

```text id="rfc-p2-mailbox-schema-005"
BLOCKED — P2_ARTIFACT_SCHEMA_MIGRATION_UNDECIDED
```

---

## 18. Failure and exit code behavior

### 18.1 Unsupported before approval

Before implementation approval, any durable source containing `ReceiveBlock` remains unsupported.

### 18.2 Invalid resume input

Malformed mailbox resume payload should return invalid CLI input semantics if rejected before artifact mutation.

Examples:

```text id="rfc-p2-mailbox-failure-001"
invalid JSON
non-UTF-8 signal file
oversized signal
non-strict JSON value
missing kind
unknown kind
missing message_id
missing actor
external message.payload present
```

### 18.3 Boundary mismatch

If the artifact active suspension is not a mailbox wait, but the signal is mailbox-specific, normal stale/boundary mismatch rules apply.

If replay reconstructs a different boundary, existing boundary mismatch behavior applies.

### 18.4 Resolution conflict

If the same suspension is resumed with conflicting mailbox message or timeout payload, existing resolution conflict behavior applies.

### 18.5 Runtime error

If validated mailbox message causes user program execution to fail after injection, runtime error behavior applies and must be committed consistently with existing P2 semantics.

---

## 19. Durable safety classifier changes

After this RFC is approved, durable classifier may allow `ReceiveBlock` under strict constraints.

### 19.1 Allowed ReceiveBlock

A durable-supported `ReceiveBlock` must satisfy:

```text id="rfc-p2-mailbox-classifier-001"
exactly one ReceivePattern
timeout absent or timeout expression deterministic
else_body present only with timeout unless otherwise specified
pattern body recursively durable-safe
else_body recursively durable-safe
no nested unsupported execution-engine nodes
no hidden async boundary in timeout expression
no LLMCall in timeout expression
no SuspendExpr in timeout expression
no AwaitExpr in timeout expression
```

### 19.2 ReceivePattern

`ReceivePattern` may be allowed only inside an approved `ReceiveBlock`.

Standalone or malformed `ReceivePattern` remains unsupported.

### 19.3 Pattern body and else_body validation

Both the primary pattern `body` and the `else_body`, if present, must recursively pass standard durable AST validation.

Unsupported effects inside `else_body` must reject the entire `ReceiveBlock` at compile time.

The implementation must not allow timeout resume to execute code that was not durable-validated before the run began.

### 19.4 Timeout expression validation

Timeout expression must be validated before runtime execution. If the expression contains unsupported nodes or nondeterministic calls, durable validation must fail before the run starts.

---

## 20. Compatibility with existing P2 stages

### 20.1 P2a

Initial durable run behavior remains unchanged for existing supported programs.

### 20.2 P2b

Resume semantics remain compatible:

```text id="rfc-p2-mailbox-compat-001"
state-file validation
suspension-id validation
single-writer lock
artifact hash validation
output-prefix suppression
idempotent duplicate resume
conflicting duplicate rejection
```

### 20.3 P2c

Multi-cycle durable campaigns remain compatible. Mailbox wait must participate in multi-cycle replay without changing the meaning of existing `SuspendExpr`, `AwaitExpr`, or `LLMCall`.

### 20.4 Existing unsupported behavior

Programs that use receive but fail the new durable receive constraints must remain rejected. The RFC does not make every receive program durable-safe.

---

## 21. Compatibility with P3c-2 and future P3c-N

### 21.1 P3c-2

P3c-2 durable consensus ticket resolution continues to use existing `SuspendExpr` / `awaiting_external_signal` boundary.

This RFC must not change P3c-2 request/signal conventions, event order, or projection semantics.

### 21.2 Future P3c-N

Future P3c-N may use this mailbox wait contract to build:

```text id="rfc-p2-mailbox-p3cn-001"
mailbox-backed vote request delivery
receive-based vote response collection
vote timeout handling
domain-level participant vote deduplication
ticket resolution through collected votes
```

P3c-N must still define its own consensus-specific message schemas and domain validation. This RFC defines only P2 mailbox wait mechanics.

---

## 22. Security and integrity considerations

### 22.1 Receiver spoofing

Mailbox resume must validate that:

```text id="rfc-p2-mailbox-security-001"
resume actor == active suspension actor
message.receiver == active suspension actor
```

Otherwise a signal could deliver a message to the wrong actor boundary.

### 22.2 Sender trust

P2 validates sender shape, not domain authorization.

Future P3c-N must validate whether `sender` or `participant` is authorized to vote.

### 22.3 Payload injection

P2 must reject non-strict JSON args before constructing internal message payload.

### 22.4 External payload injection

P2 must reject `message.payload` in the external resume signal. Internal payload, if needed for existing runtime compatibility, is derived from `args`.

### 22.5 Oversized messages

Mailbox resume signal size must respect the existing maximum signal size unless a later RFC changes that limit.

### 22.6 Replay tampering

A tampered `message_received` or `receive_timeout` event must be detected by history integrity and boundary replay validation.

### 22.7 Ambiguous matching

Multiple receive patterns remain unsupported to avoid ambiguous matching and future replay drift.

---

## 23. Test plan

Implementation is not authorized by this RFC until approval. If approved later, implementation must include the following tests.

### 23.1 Durable validation tests

```text id="rfc-p2-mailbox-tests-001"
ReceiveBlock remains rejected before implementation approval.
ReceiveBlock with one pattern is accepted after approval.
ReceiveBlock with more than one pattern is rejected.
ReceiveBlock with nondeterministic timeout is rejected.
ReceiveBlock with LLMCall in timeout is rejected.
ReceiveBlock with AwaitExpr in timeout is rejected.
ReceiveBlock with SuspendExpr in timeout is rejected.
ReceiveBlock with random/time/uuid source in timeout is rejected if those calls are available elsewhere.
ReceiveBlock pattern body is recursively durable-validated.
ReceiveBlock else_body is recursively durable-validated.
ReceiveBlock else_body with unsupported effect rejects entire ReceiveBlock.
ReceiveBlock else_body without timeout follows the approved policy.
```

### 23.2 PENDING artifact tests

```text id="rfc-p2-mailbox-tests-002"
run --durable reaches empty receive without timeout.
artifact status is PENDING.
active_suspension.reason is awaiting_message.
active_suspension.promise_id is null.
payload actor matches current actor.
payload_hash and boundary_fingerprint validate.

run --durable reaches empty receive with timeout.
artifact status is PENDING.
active_suspension.reason is awaiting_message_or_timeout.
active_suspension.promise_id is null.
payload timeout equals evaluated deterministic timeout value.
```

### 23.3 Message resume tests

```text id="rfc-p2-mailbox-tests-003"
resume awaiting_message with valid mailbox_message.
external message.payload is rejected.
internal payload is derived from args.
message_received is appended exactly once.
message receiver equals active actor.
receive body executes.
artifact becomes COMPLETED or next PENDING according to program.

resume awaiting_message_or_timeout with valid mailbox_message.
message_received is appended exactly once.
else_body is not executed.
```

### 23.4 Timeout resume tests

```text id="rfc-p2-mailbox-tests-004"
resume awaiting_message_or_timeout with mailbox_timeout.
receive_timeout is appended exactly once.
else_body executes.
timeout value is taken from active suspension payload.
wall-clock is not consulted.

resume awaiting_message with mailbox_timeout fails closed.
```

### 23.5 Strict JSON tests

```text id="rfc-p2-mailbox-tests-005"
mailbox_message with non-string dict key is rejected.
mailbox_message with NaN is rejected.
mailbox_message with Infinity is rejected.
mailbox_message with tuple-like host object cannot enter mailbox.
mailbox_message with unsupported object type is rejected.
oversized mailbox message is rejected.
```

### 23.6 Receiver binding tests

```text id="rfc-p2-mailbox-tests-006"
message.receiver mismatch fails closed.
resume actor mismatch fails closed.
message.receiver correct but actor incorrect fails closed.
actor correct but message.receiver incorrect fails closed.
```

### 23.7 Idempotency tests

```text id="rfc-p2-mailbox-tests-007"
same suspension_id + same normalized signal hash returns stored operation result.
same suspension_id + different normalized signal hash returns conflict.
same message_id + same actor + same sender + same receiver + same method + same args_hash is idempotent.
same message_id + same actor + same sender + same receiver + same method + different args_hash fails closed.
message after timeout conflicts.
timeout after message conflicts.
```

### 23.8 Replay tests

```text id="rfc-p2-mailbox-tests-008"
replay consumes message_received at receive boundary.
replay consumes receive_timeout at receive boundary.
replay fails if expected event is missing.
replay fails if event actor mismatches receive actor.
replay fails if event order changes.
replay does not append duplicate message_received.
replay does not inspect live external mailbox.
```

### 23.9 Sequential mailbox suspension tests

```text id="rfc-p2-mailbox-tests-009"
program has two sequential receive waits.
first run reaches receive #1 and PENDING.
resume #1 appends message_received #1 and reaches receive #2 PENDING.
resume #2 replays message_received #1, reaches receive #2, appends message_received #2.
replay_cursor has no off-by-one drift.
output_delta contains only new output.
idempotency records both suspensions correctly.
```

### 23.10 Persisted mailbox / ghost message tests

```text id="rfc-p2-mailbox-tests-010"
artifact with unexpected non-empty mailbox at live boundary follows approved policy.
ghost mailbox message cannot be consumed without current resume payload.
persisted mailbox content cannot bypass message_received event recording.
if no early mailbox delivery is selected, unexpected ghost consumption fails closed.
```

### 23.11 Compatibility tests

```text id="rfc-p2-mailbox-tests-011"
existing SuspendExpr durable tests still pass.
existing AwaitExpr durable tests still pass.
existing LLMCall durable tests still pass.
existing P3c-2 ticket resolution tests still pass.
existing receive timeout sync replay tests still pass.
```

---

## 24. Stop gates

The following gates block implementation:

```text id="rfc-p2-mailbox-stop-001"
BLOCKED — ASYNC_MAILBOX_WAIT_APPROVAL_MISSING
BLOCKED — RECEIVEBLOCK_DURABLE_CLASSIFICATION_UNDEFINED
BLOCKED — RECEIVECONTRACT_DURABLE_PATTERN_RULES_UNDEFINED
BLOCKED — AWAITING_MESSAGE_REASON_UNREGISTERED
BLOCKED — AWAITING_MESSAGE_OR_TIMEOUT_REASON_UNREGISTERED
BLOCKED — MAILBOX_WAIT_ARTIFACT_CONTRACT_UNDEFINED
BLOCKED — MAILBOX_MESSAGE_RESUME_SCHEMA_UNDEFINED
BLOCKED — MAILBOX_TIMEOUT_RESUME_SCHEMA_UNDEFINED
BLOCKED — EXTERNAL_MESSAGE_PAYLOAD_FIELD_NOT_REJECTED
BLOCKED — DERIVED_INTERNAL_PAYLOAD_POLICY_UNDEFINED
BLOCKED — RECEIVE_TIMEOUT_EXPRESSION_PURITY_UNDEFINED
BLOCKED — RECEIVE_TIMEOUT_EXPRESSION_NOT_DURABLY_VALIDATED
BLOCKED — MAILBOX_SIGNAL_HASH_PROFILE_UNDEFINED
BLOCKED — MESSAGE_IDENTITY_HASH_UNDEFINED
BLOCKED — MESSAGE_ARGS_HASH_UNDEFINED
BLOCKED — SAME_MESSAGE_ID_DIFFERENT_ARGS_POLICY_UNDEFINED
BLOCKED — MAILBOX_INJECTION_STRICT_JSON_VALIDATION_UNDEFINED
BLOCKED — MAILBOX_APPEND_BYPASSES_CANONICAL_JSON_VALIDATION
BLOCKED — MULTI_CYCLE_MAILBOX_REPLAY_CURSOR_SEMANTICS_UNDEFINED
BLOCKED — DURABLE_RECEIVE_PATTERN_MATCHING_UNDEFINED
BLOCKED — MULTI_PATTERN_RECEIVE_UNSUPPORTED
BLOCKED — ELSE_BODY_DURABLE_VALIDATION_UNDEFINED
BLOCKED — MAILBOX_PROMISE_ID_POLICY_UNDEFINED
BLOCKED — PERSISTED_MAILBOX_LIVE_CONSUMPTION_UNDEFINED
BLOCKED — GHOST_MAILBOX_MESSAGE_CONSUMED_WITHOUT_RESUME
BLOCKED — EARLY_MAILBOX_DELIVERY_SEMANTICS_REQUIRED
BLOCKED — DURABLE_INBOX_CONTRACT_UNDEFINED
BLOCKED — TIMEOUT_RESUME_CONFLICT_POLICY_UNDEFINED
BLOCKED — P2_ARTIFACT_SCHEMA_MIGRATION_UNDECIDED
BLOCKED — BACKGROUND_WORKER_OR_DAEMON_REQUIRED
BLOCKED — NETWORK_DELIVERY_REQUIRED
BLOCKED — WALL_CLOCK_SCHEDULER_REQUIRED
BLOCKED — PRODUCTION_DISTRIBUTED_CONSENSUS_CLAIM_REQUIRED
```

---

## 25. Approval record requirements

The approval record must include:

```text id="rfc-p2-mailbox-approval-001"
1. final RFC content hash;
2. current main base SHA;
3. explicit approval or rejection of awaiting_message;
4. explicit approval or rejection of awaiting_message_or_timeout;
5. artifact schema decision;
6. timeout semantics decision;
7. mailbox hash profile decision;
8. persisted mailbox policy decision;
9. ReceiveBlock classifier decision;
10. external args-only schema decision;
11. derived internal payload decision;
12. promise_id null decision;
13. ghost mailbox policy decision;
14. test plan acceptance;
15. implementation file allowlist;
16. implementation file denylist;
17. explicit non-claims.
```

---

## 26. Implementation file allowlist after approval

If this RFC is approved, the implementation PR may be allowed to modify:

```text id="rfc-p2-mailbox-impl-001"
synapse/application.py
synapse/runtime/actor_runtime.py
synapse/runtime/mailbox_wait.py
tests/test_durable_mailbox_wait.py
```

Additional test files may be allowed only if directly exercising existing durable execution regressions.

The implementation PR must not modify:

```text id="rfc-p2-mailbox-impl-002"
parser
AST definitions
lexer
ConsensusEngine
consensus ticket resolution module
P3 evidence documents
P3 capability matrix
network or daemon transport
public ticket APIs
examples
workflow configuration
```

unless a later approval record explicitly expands the scope.

---

## 27. Evidence after implementation

If an implementation PR is later approved and merged, evidence must record:

```text id="rfc-p2-mailbox-evidence-001"
base SHA
head SHA
merge SHA
changed files
test commands
test counts
known failures
new failures
scope closed
explicit non-claims
```

The capability matrix must not mark production distributed consensus as complete because this RFC concerns P2 mailbox wait mechanics only.

---

## 28. Final decision of this RFC draft

This draft proposes the following decision:

```text id="rfc-p2-mailbox-decision-001"
P2 mailbox receive wait should be modeled as an externally resolved durable boundary.

The first approved implementation should support:
- awaiting_message
- awaiting_message_or_timeout
- strict JSON mailbox_message resume with args-only external schema
- derived internal payload for compatibility
- strict JSON mailbox_timeout resume
- deterministic replay through message_received / receive_timeout
- single-pattern ReceiveBlock only
- deterministic timeout expression only
- recursive validation of pattern body and else_body
- active_suspension.promise_id = null for mailbox wait reasons
- normalized reason-specific mailbox signal hash
- no ghost mailbox consumption
- no early durable inbox
- no daemon
- no network delivery
- no wall-clock scheduler
- no production distributed consensus claim
```

Implementation remains blocked until the approval record explicitly approves this RFC.

---

## 29. Relationship to future P3c-N

Once this RFC is approved and implemented, future P3c-N may define:

```text id="rfc-p2-mailbox-p3cn-002"
distributed_consensus_vote_request message schema
distributed_consensus_vote_response message schema
vote response collection through ReceiveBlock
participant-level duplicate vote handling
missing vote timeout policy
ticket resolution through collected mailbox responses
```

Until then:

```text id="rfc-p2-mailbox-p3cn-003"
P3c-N remains blocked.
```
