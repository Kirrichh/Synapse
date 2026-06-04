# RFC: Actor Messaging in CVM (Alpha.3-D5)

**STATUS: IMPLEMENTED in Alpha.3-D5**

This RFC defines the Actor Messaging CVM surface for Alpha.3-D5. It is now
implemented as the D5 transport substrate. The implementation follows this RFC
without expanding scope into payload destructuring, external brokers, LLM calls,
or actor runtime internals.

The core design rule is strict separation of concerns:

- **CVM** owns deterministic stack execution, durable VM state, opcode sequencing,
  gas accounting, and snapshot/replay metadata.
- **VMBridge** owns CVM-to-host synchronization and dispatch.
- **actor_runtime** remains the canonical authority for live mailbox delivery,
  actor identity, mailbox mutation, and actor scheduling side effects.

D5 closes the actor vertical started by AgentDef/SubAgentDef structural wrappers.
Actors can enter CVM as structural scopes in D3; D5 gives them a deterministic
internal messaging substrate without pulling external LLM or network semantics
into the VM.

---

## 1. Goals and Non-Goals

### Goals

1. Compile actor-to-actor send/receive statements into a small CVM messaging
   surface.
2. Preserve the existing parser grammar for `SendStmt`, `ReceiveBlock`, and
   `ReceivePattern`.
3. Introduce a dedicated logical messaging pause state:
   `STATUS_PAUSED_MESSAGING`.
4. Keep D1/D2 host-call and promise invariants clean by using a separate
   `pending_message_receive` envelope instead of reusing `pending_host_call`.
5. Represent mailbox state in `VMState` only as a durable snapshot view while
   leaving live delivery authority in `actor_runtime`.
6. Provide deterministic replay of consumed messages using content-addressed
   `message_consumed_id` events, not positional history indexes.
7. Reduce corpus fallbacks by classifying `SendStmt`, `ReceiveBlock`, and
   `ReceivePattern` as part of the v2.2 CVM surface.

### Non-Goals

The following are explicitly out of scope for D5:

- Payload destructuring or payload-field binding opcodes.
- New parser grammar for header-pattern syntax.
- `MSG_UNPACK`, `MSG_MATCH_PAYLOAD`, `MSG_BIND_FIELD`, or equivalent payload
  unpacking opcodes.
- Reusing `STATUS_PAUSED_HOST_CALL` for message receives.
- Direct CVM mutation of `actor_runtime.registry`, `interpreter.mailboxes`, or
  any host mailbox structure.
- Positional replay by mailbox index or execution-history index.
- External brokers, sockets, distributed queues, priorities, timeouts, or
  cancellation.
- `LLMCall`, `PromptExpr`, streaming, or external API integration.

---

## 2. Syntax Mapping: Current Grammar Is Preserved

D5 uses **Variant A**: it preserves the current parser and AST shape.

Current `SendStmt` shape:

```python
SendStmt(receiver=<expr>, method=<identifier>, args=[...])
```

Current receive syntax maps to the current `ReceivePattern` shape:

```text
receive {
    sender => payload {
        ...
    }
}
```

Current `ReceivePattern` shape:

```python
ReceivePattern(sender_var="sender", target_var="payload", body=[...])
```

D5 mapping:

- `SendStmt.method` becomes `msg_type`.
- The current actor context becomes `sender_id`; it is supplied by the bridge /
  host runtime and is not encoded by new syntax.
- `SendStmt.receiver` resolves to `receiver_id` via the existing actor runtime
  name/ref resolution path.
- `SendStmt.args` become the message payload. In D5, if there is one argument,
  that argument is the payload; otherwise the payload is the list of arguments,
  matching existing `actor_runtime.send_message` behavior.
- `ReceivePattern.sender_var` binds the sender reference / sender id.
- `ReceivePattern.target_var` binds the complete message dictionary.

D5 does **not** add case-style header grammar such as:

```text
case { msg_type: "ping", sender_id: "actor_a" } => { ... }
```

That grammar can be proposed in a future language RFC. D5 is a substrate patch,
not a parser evolution patch.

---

## 3. VM State Extensions

D5 introduces mailbox snapshot state and a separate messaging pending envelope.

```python
@dataclass
class VMState:
    mailbox_inbound: list[dict] = field(default_factory=list)
    mailbox_outbound: list[dict] = field(default_factory=list)
    pending_message_receive: Optional[dict] = None
```

### Mailbox state role

`VMState.mailbox_inbound` and `VMState.mailbox_outbound` are **snapshot views**.
They exist to support deterministic snapshots, transition hashes, and debugging.
They are not the canonical live queues.

### Canonical authority

`actor_runtime remains the canonical authority` for live mailbox mutation and
message delivery. CVM never mutates host mailbox internals directly. VMBridge is
the synchronization boundary between the VM snapshot view and actor_runtime.

### Status semantics

D5 adds a distinct logical status:

```text
STATUS_PAUSED_MESSAGING
```

The VM status rules become:

```python
def status(self) -> str:
    if self.state.pending_host_call:
        return "STATUS_PAUSED_HOST_CALL"
    if self.state.pending_message_receive:
        return "STATUS_PAUSED_MESSAGING"
    if self.halted:
        return "STATUS_HALTED"
    return "STATUS_RUNNING"
```

`pending_message_receive` must never be stored in `pending_host_call`.
BridgePromise may be reused as an internal bridge mechanism, but the VM state and
observability status remain message-specific.

### Transition hash

`transition_hash` must include:

- `tuple(mailbox_inbound)` after canonical JSON normalization.
- `tuple(mailbox_outbound)` after canonical JSON normalization.
- `pending_message_receive` after canonical JSON normalization.

Any change to mailbox snapshot state or pending receive state must change the VM
transition hash.

---

## 4. Opcodes

D5 introduces a minimal messaging opcode surface:

```text
MSG_SEND
MSG_RECEIVE
RECEIVE_ENTER
RECEIVE_EXIT
```

Optional internal helper opcodes are allowed only for header checks:

```text
MSG_MATCH_TYPE
MSG_MATCH_SENDER
```

They must only compare message headers. They must not destructure or bind payload
fields.

### MSG_SEND

`MSG_SEND` sends a message through VMBridge to actor_runtime.

Responsibilities:

1. Resolve receiver id / actor reference through the bridge.
2. Use `SendStmt.method` as `msg_type`.
3. Use current actor as `sender_id`.
4. Forward payload to actor_runtime.
5. Append outbound message to `VMState.mailbox_outbound` as a durable snapshot
   view.
6. Emit a canonical bridge-side send event through actor_runtime.

### RECEIVE_ENTER / RECEIVE_EXIT

`RECEIVE_ENTER` and `RECEIVE_EXIT` mark the receive block boundary. In D5 they
are no-op structural markers for snapshot-safe receive block sequencing and
event trace clarity. They do not create a new parser scope, do not maintain a
receive stack, and do not mutate actor registries.

### MSG_RECEIVE

`MSG_RECEIVE` asks VMBridge / actor_runtime for the next matching message.

If a message is available:

1. actor_runtime consumes it canonically.
2. VMBridge records a `message_consumed` event.
3. VMBridge syncs `VMState.mailbox_inbound` snapshot view.
4. The complete message dict is pushed or bound for the receive body.

If no message is available:

1. VM enters `STATUS_PAUSED_MESSAGING`.
2. `VMState.pending_message_receive` is populated.
3. A bridge-side promise may be reused internally for wake-up, but the public
   resume path is `resume_message_receive()`, not `resume_host_call()`.
4. `pending_host_call` remains `None`.

---

## 5. Bridge Dispatch and Actor Runtime Authority

D5 bridge symbols:

```text
SYS_MSG_SEND
SYS_MSG_RECEIVE
SYS_MSG_CONSUME
```

`SYS_MSG_CONSUME` may be an internal bridge-level operation used to record a
consumption event. It is not a guest-visible language primitive.

### Authority split

```text
CVM VMState mailbox_*     = durable snapshot view
VMBridge                  = synchronization and dispatch boundary
actor_runtime / mailboxes = canonical live authority
```

The bridge must be the only path from CVM messaging opcodes to actor_runtime.

Forbidden implementation forms:

- `CognitiveVM` directly reading `interpreter.mailboxes`.
- `CognitiveVM` directly mutating `actor_runtime` fields.
- `CognitiveVM` storing live actor registry objects.
- Any direct mailbox mutation outside VMBridge / actor_runtime dispatch.

### Current actor identity

The bridge obtains `sender_id` and `receiver_id` from existing actor runtime
identity helpers, not from new parser syntax.

---

## 6. FIFO Replay and Message Consumption Identity

D5 replay is based on `message_consumed_id`, not positional indexes.

Canonical id generation:

```python
def compute_message_consumed_id(receiver_id, msg_type, sender_id, transition_hash, event_id, payload_hash=""):
    seed = f"{receiver_id}|{msg_type}|{sender_id}|{transition_hash}|{event_id}|{payload_hash}"
    return "mc-" + hashlib.sha256(seed.encode()).hexdigest()[:16]
```

The CVM computes `message_consumed_id` when `MSG_RECEIVE` consumes a message
from `VMState.mailbox_inbound`. VMBridge receives the already computed event via
`SYS_MSG_CONSUME` and records it in host history. VMBridge must not replace this
identity with a positional or random identifier.

A successful receive records an event:

```json
{
  "type": "message_consumed",
  "message_consumed_id": "mc-...",
  "receiver_id": "worker_a",
  "sender_id": "coordinator",
  "msg_type": "ping",
  "payload_hash": "sha256:...",
  "message": { }
}
```

### Replay lookup

Replay lookup must use `message_consumed_id` or a deterministic receive call id.
It must not use:

- mailbox position,
- execution-history list index,
- number of previous receive operations,
- live queue fallback.

### FIFO invariant

FIFO ordering remains an invariant of canonical actor_runtime delivery and the
ordered `message_consumed` event stream. FIFO order is verified by event order;
it is not the lookup mechanism.

### Mismatch handling

- Missing `message_consumed` event during replay raises `VMResumeSyncError`.
- Duplicate `message_consumed_id` raises `VMResumeSyncError`.
- Payload hash mismatch raises `VMResumeSyncError`.
- Live queue fallback during replay is forbidden.

---

## 7. Security Gates

D5 reuses D1/D2 security gates where applicable:

- `agent_id` trust checks.
- exact capability checks for message-send/receive operations if the host policy
  marks them as capability-protected.
- `HOST_ABI_VERSION` validation.
- JSON-safe payload serialization for snapshot and replay.

D5 does not change LLM, prompt, external API, or policy capability routing.

### Capability scope

D5 may introduce future symbolic capabilities such as:

```text
actor.send
actor.receive
```

However, the RFC does not require changing the existing B2 capability table in
Commit 0. Any capability table update belongs to implementation review and must
not grant CVM direct access to actor_runtime internals.

---

## 8. Snapshot and Restore

Snapshots must include:

```json
{
  "mailbox_inbound": [],
  "mailbox_outbound": [],
  "pending_message_receive": null
}
```

### Snapshot semantics

- `mailbox_inbound` and `mailbox_outbound` are serialized as JSON-safe message
  dictionaries.
- `pending_message_receive` is serialized separately from `pending_host_call`.
- `transition_hash` includes mailbox and pending-message state.

### Restore semantics

On restore:

1. `VMState.mailbox_inbound` and `mailbox_outbound` are restored.
2. `pending_message_receive` is restored.
3. VM status is `STATUS_PAUSED_MESSAGING` if `pending_message_receive` is not
   null.
4. VMBridge syncs mailbox snapshot views with actor_runtime according to the
   implementation policy.
5. No `STATUS_PAUSED_HOST_CALL` state is inferred from a pending message receive.

### Resume on message delivery

A future implementation method such as `resume_message_receive(...)` must:

- validate the pending message receive envelope,
- deliver or bind the received message,
- clear `pending_message_receive`,
- preserve `pending_host_call`,
- recalculate `transition_hash`,
- return VM status to `STATUS_RUNNING` unless another terminal condition applies.

---

## 9. Red Lines and Review Blockers

The following block implementation acceptance:

1. Payload unpacking opcodes or payload destructuring in CVM:
   `MSG_UNPACK`, `MSG_MATCH_PAYLOAD`, `MSG_BIND_FIELD`, or equivalents.
2. Reuse of `STATUS_PAUSED_HOST_CALL` for message receive suspension.
3. Storing message receives in `pending_host_call`.
4. Direct CVM access to `actor_runtime.registry`, `interpreter.mailboxes`, or
   host mailbox internals.
5. Positional replay by history index or mailbox index.
6. Live queue fallback during replay.
7. New parser grammar for receive header patterns.
8. Any changes to `LLMCall` or `PromptExpr`.
9. External brokers, network sockets, priority queues, timeouts, cancellation,
   or streaming.

---

## 10. Corpus Target and Acceptance Checklist

D5 targets the actor messaging fallback group identified by corpus telemetry:

- `SendStmt`
- `ReceiveBlock`
- `ReceivePattern`

Static corpus target after implementation:

```text
total_fallback: 135 -> 112
corpus_coverage: ~0.9034
```

Implementation acceptance must include:

1. `SendStmt`, `ReceiveBlock`, and `ReceivePattern` in `CVM_AST_NODE_TYPES_V22`.
2. `STATUS_PAUSED_MESSAGING` and `pending_message_receive` in VM state.
3. mailbox snapshot state included in serialization and transition hash.
4. message consumption recorded via content-addressed `message_consumed_id`.
5. actor_runtime remains canonical mailbox authority.
6. VMBridge is the only CVM-to-actor_runtime mutation boundary.
7. Existing D1/D2 host-call and promise tests remain green.
8. Existing ActorDef and PolicyDef structural wrapper tests remain green.
9. Corpus report `reports/corpus_fallback_alpha3d5.json` confirms expected
   fallback reduction.
10. No red-line item from section 9 is present.

---

## Decision

Proceed with **Alpha.3-D5: Actor Messaging and Internal Mailbox Flow** using the
current parser grammar, dedicated messaging pause state, VM mailbox snapshot
views, actor_runtime canonical authority, and content-addressed message replay.

D5 is the transport substrate for future LLM result propagation. `LLMCall` and
`PromptExpr` remain explicitly deferred to D6.
