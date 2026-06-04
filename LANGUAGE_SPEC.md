# Synapse Language Specification v1.2

## 1. Overview

**Synapse** is a domain-specific programming language for building AI agents and consequence-aware AI workflows. It treats agents, prompts, LLM calls, thought processes, memory, claims, evidence, verification gates, policies, consequences, actor messages, and durable execution state as first-class programming concepts.

Synapse v0.8 extends the durable replay runtime with **Semantic Guardrails** while preserving v0.5 Determinism Drift protection. Nondeterministic builtins such as `time()`, `random()` and `uuid()` are captured as `side_effect` events in `execution_history` and replayed through a cursor during recovery.

## 2. Lexical Structure

### Comments

```synapse
// Single-line comment
```

### Keywords

```text
agent, fn, let, if, else, return, model, memory, thought, flow,
superpose, branch, parallel, prompt, llm, while, for, in, import, as,
policy, target, guard, reject, verify, claim, consequence,
require, forbid, check, text, evidence, confidence, scope, reason, retention,
send, receive,
true, false, null, and, or, not
```

### Symbolic constants

The runtime predefines common governance constants as strings:

```text
low, medium, high, critical,
short_term, long_term, session, project,
user_controlled, system_controlled,
reversible, irreversible
```

## 3. Grammar

```text
program       ::= statement* EOF

statement     ::= agent_def | fn_def | flow_def | let_stmt | if_stmt
                | while_stmt | for_stmt | return_stmt | import_stmt
                | policy_def | verify_block | claim_def | consequence_def
                | check_stmt | send_stmt | receive_block | expr_stmt

agent_def     ::= "agent" IDENTIFIER "{" (model_decl | memory_decl | fn_def)* "}"
model_decl    ::= "model" STRING
memory_decl   ::= "memory" STRING
fn_def        ::= "fn" IDENTIFIER "(" params? ")" block
flow_def      ::= "flow" IDENTIFIER block
block         ::= "{" statement* "}"
let_stmt      ::= "let" IDENTIFIER "=" expr
if_stmt       ::= "if" expr block ("else" block)?
while_stmt    ::= "while" expr block
for_stmt      ::= "for" IDENTIFIER "in" expr block
return_stmt   ::= "return" expr?
import_stmt   ::= "import" STRING ("as" IDENTIFIER)?
expr_stmt     ::= expr

policy_def    ::= "policy" IDENTIFIER "{" policy_item* "}"
policy_item   ::= "target" expr
                | ("require" | "forbid") expr
                | "guard" block

verify_block  ::= "verify" "{" check_stmt* "}"
check_stmt    ::= "check" expr ("," expr)?

claim_def     ::= "claim" IDENTIFIER "{" claim_field* "}"
claim_field   ::= "text" expr | "evidence" expr | "confidence" expr

consequence_def ::= "consequence" IDENTIFIER "{" consequence_field* "}"
consequence_field ::= IDENTIFIER expr

governed_memory_write ::= "memory.write" "(" expr? ")" "{" governance_field* "}"
governance_field ::= ("scope" | "reason" | "retention" | IDENTIFIER) expr

send_stmt     ::= "send" expr "." IDENTIFIER "(" args? ")"
receive_block ::= "receive" "{" receive_pattern* "}"
receive_pattern ::= IDENTIFIER "=>" IDENTIFIER block
```

`guard (args) { ... }` is executable in v0.8. Guard internals run in a read-only policy context; the main durable history records only `policy_evaluated` or `policy_violation`, so policy evolution does not break replay of old workflows.

## 4. Execution Model

Synapse execution has five layers:

1. **Program layer** — top-level declarations and statements.
2. **Agent layer** — named agents with model, memory, and methods.
3. **Cognitive layer** — prompts, LLM calls, `thought`, `superpose`, and `flow`.
4. **Governance layer** — `policy`, `claim`, `verify`, `consequence`, and governed memory writes.
5. **Durable actor layer** — mailboxes, `send`, `receive`, `Suspension`, runtime snapshots, and deterministic replay.

### Auto-main convention

If a zero-argument function named `main` exists, the synchronous interpreter calls it after top-level declarations/statements have been evaluated.

## 5. Governance

### Policy

```synapse
policy FinancialControl {
    target "Worker.process"
    forbid "nuclear-launch"
}
```

Runtime behavior:

- `target "Worker.process"` applies the policy to `send Worker.process(...)`.
- `forbid "nuclear-launch"` blocks delivery if any argument contains that value.
- On violation, runtime records a `policy_violation` event and raises `PolicyViolationException`.

### Claims

```synapse
claim answer_grounded {
    text "The answer is grounded"
    evidence "conversation_history"
    confidence high
}
```

### Verify

```synapse
verify {
    check answer_grounded.confidence == high, "confidence must be high"
}
```

### Consequence

```synapse
consequence send_email {
    external_state_change true
    reversible false
    requires_confirmation true
}
```

## 6. Actor Runtime

### Send

```synapse
send Worker.process("job-42")
```

`send` passes through `Interpreter.send_message()`, where governance is enforced before the message is committed to the receiver mailbox.

### Receive

```synapse
let self = Worker
receive {
    sender => msg {
        print(msg.payload)
    }
}
```

If the current actor mailbox is empty in coroutine mode, runtime yields:

```text
Suspension(reason="awaiting_message")
```

## 7. Durable Replay

### Runtime state

The interpreter tracks:

```text
runtime_mode: LIVE | REPLAY
execution_history: ordered event log
replay_cursor: current event offset
mailboxes: actor mailbox state
actor_log: audit log
```

### Event types

```text
llm_call
message_sent
message_received
policy_evaluated
policy_violation
```

### Recovery algorithm

1. A new interpreter loads snapshot via `load_snapshot(snapshot)`.
2. The interpreter resets `global_env`.
3. Runtime enters `REPLAY` if `execution_history` is non-empty.
4. The same source code is executed from the beginning.
5. At nondeterministic operations, runtime consumes the matching history event.
6. When history is exhausted, runtime switches back to `LIVE`.

### LLM replay

During LIVE execution:

```text
llm(prompt) -> Suspension(awaiting_llm) -> result -> append llm_call event
```

During REPLAY execution:

```text
llm(prompt) -> read previous llm_call.result from execution_history
```

### Receive replay

During LIVE execution:

```text
receive -> pop mailbox -> append message_received event
```

During REPLAY execution:

```text
receive -> read previous message_received.message from execution_history
```

## 8. Explicit Non-Goal

Synapse v0.5 does **not** serialize Python stack frames, generator internals, or VM-specific continuation state. Durable resume is achieved by deterministic replay of source code plus event history.


## 13. Determinism Drift Protection

The following builtins are treated as deterministic leaks and are intercepted by the runtime:

```text
time(), random(), uuid()
```

LIVE mode behavior:

```json
{
  "type": "side_effect",
  "name": "random",
  "args": [],
  "result": 0.7
}
```

REPLAY mode behavior:

- the builtin is not called;
- `next_history_event("side_effect", name="random")` returns the historical result;
- control flow remains branch-stable.

This prevents replay divergence in programs where random/time/uuid values affect `if`, `while`, policy decisions, message sends, or LLM calls.

## 14. State Checkpoint Artifacts

`Interpreter.create_state_checkpoint(label)` creates a JSON-safe artifact containing:

- current `global_env`;
- current `mailboxes`;
- `history_offset`;
- actor log length;
- optional label.

The checkpoint is also recorded as a `checkpoint` event. Replay skips checkpoint events because they are deterministic metadata, not executable external effects.

Current limitation: v0.5 checkpoints are compaction artifacts, not full instruction-pointer continuations. A future version can pair them with a continuation cursor or bytecode VM to resume from the checkpoint without replaying from program start.


## v0.8 Semantic Guardrails

A policy may include an executable guard block:

```synapse
policy SafetyGov {
    target "Worker.process"
    guard (args) {
        let analysis = llm(prompt "Analyze safety")
        if args[0].contains("unsafe") {
            reject "Semantic policy violation"
        }
    }
}
```

### Atomic policy verdicts

Guard internals are intentionally not recorded as normal workflow events. LIVE execution emits exactly one durable verdict per applied policy:

- `policy_evaluated` when the guard passes;
- `policy_violation` when the guard rejects.

During REPLAY, the interpreter consumes the historical verdict and does not re-run the guard body. This isolates business workflow replay from later policy-prompt or policy-algorithm changes.

### Guard purity

Inside a guard, local `let` bindings are allowed. Hidden workflow mutations are rejected:

- `send ...` is rejected;
- `memory.write(...)` and `memory.clear(...)` are rejected;
- assignment to an existing variable is rejected.

This keeps policies as semantic filters, not hidden actor workflows.


## v0.8 Receive Timeout & Actor Audit

### Receive timeout

```synapse
receive timeout 3600 {
    sender => msg {
        print(msg.payload)
    }
} else {
    print("approval timeout")
}
```

Semantics:

- If the current actor mailbox contains a message, `receive` consumes the oldest message first.
- If the mailbox is empty and `timeout` is present, runtime records a durable `receive_timeout` event and executes the `else` branch.
- In coroutine mode, `receive timeout` yields `Suspension(reason="awaiting_message_or_timeout")`. The orchestrator may resume it with either an actor message or `{"timeout": true}`.
- In replay mode, `receive_timeout` is read from `execution_history`; the timer is not re-evaluated as a wall-clock wait.

### Actor audit invariants

- Actor mailboxes are FIFO.
- `message_sent`, `message_received`, and `receive_timeout` are durable audit events.
- Replay peeking skips audit-only events such as `message_sent`, `policy_evaluated`, and checkpoints when selecting the next executable nondeterministic step.


# v0.8 Swarm Mobility & Location Transparency

## `migrate` statement

```synapse
migrate "node-b:9000"
```

`migrate` is a durable suspension point. In synchronous execution it is rejected, because migration requires an orchestrator. In coroutine execution it yields a `Suspension` with reason `migration_requested`.

## Mobility envelope

A mobility envelope is the wire-safe state transfer unit:

```json
{
  "type": "synapse_mobility_envelope",
  "version": "1.0",
  "actor_name": "Worker",
  "source_code": "...",
  "runtime": {
    "execution_history": [],
    "mailboxes": {},
    "actor_log": [],
    "routing_table": {}
  }
}
```

The envelope intentionally stores source + durable history rather than a Python frame. A receiving node restores by compiling the source and replaying history.

## Location transparency

The runtime can route actor messages without requiring source code to know where the actor lives. If `Worker` is registered as remote, `send Worker.process(...)` emits a `forward_message` packet. If `Worker` is local, the same statement writes to the local FIFO mailbox.

## Swarm daemon prototype

`synapsed.py` defines `SwarmNodeDaemon`, a minimal asyncio daemon for accepting `migrate_actor` and `forward_message` packets. The daemon is a protocol prototype, not yet a hardened distributed system.


## Patch 1.0 — Durable Promises, Spawn, Suspend and Async Actor Send

This patch extends the Swarm mobility layer with process-oriented actor primitives while preserving the deterministic replay model.

### New syntax

```synapse
agent Analyst {
    model "mock"
}

let analyst_proc = spawn Analyst()
analyst_proc => queue_task("process_logs")

let approved = suspend await_human_approval("deployment plan")
let result = await analyst_proc.get_response()
```

### Runtime semantics

- `spawn Agent()` creates a serializable `DurableActorRef` with its own FIFO mailbox.
- `actor_ref => method(args)` is lowered to the same governed actor delivery path as `send`, but the sender does not need to know whether the actor is local or remote.
- `suspend external_request(...)` emits `Suspension(reason="awaiting_external_signal")` and creates a durable promise record.
- `await promise_or_actor_call` emits `Suspension(reason="awaiting_promise")` in coroutine mode.
- `dump_state()` now includes spawned actors, durable promises and an LLM prompt-hash context cache.

### Engineering boundary

Synapse still avoids serializing Python frames. Durable recovery remains based on:

```text
source_code + execution_history + mailboxes + promises + routing metadata
```

A future bytecode/continuation layer may optimize resume cursors, but the current patch keeps the runtime replay-safe and JSON-portable.


## Synapse v1.0 — Promise-aware Swarm Grid

Synapse v1.0 adds the first production-oriented network lifecycle for durable promises:

- `resolve_promise` wire packet for cross-node `await` completion.
- Promise owner routes and promise tombstones for migrated agents.
- `remote_spawn` packet boundary for creating virtual actors on another node.
- Mobility envelopes now carry `promise_routes` and `promise_tombstones` alongside `promises`.

The runtime still avoids serializing Python frames. Cross-node recovery remains based on:

```text
source_code + execution_history + mailboxes + promises + routes
```

### Wire packet: resolve_promise

```json
{
  "type": "resolve_promise",
  "version": "1.0.0",
  "source_node": "node-b",
  "target_node": "node-a",
  "promise_id": "promise-4512",
  "value": {"status": "success", "data": "AI Response"}
}
```

If the original owner has migrated, `synapsed.py` can keep a promise tombstone and forward the completion to the new node without losing the result.


---

## Synapse v1.2 — Intent, Trust, Observe & Governed Forget

Synapse v1.2 extends the governance layer from post-factum auditing to pre-action control and production monitoring.

### `intent` and `declare intent`

`intent` defines what an agent is about to do before the external action happens. `declare intent <name>` runs the intent through applicable policies before the workflow continues.

```synapse
intent send_payment {
    action "transfer funds"
    amount 5000
    target "external_account"
    reversible false
}

policy PaymentControl {
    target "intent.send_payment"
    guard (args) {
        if args[0].amount > 1000 {
            reject "large payment requires approval"
        }
    }
}

fn main() {
    declare intent send_payment
    // Execution reaches this point only if the intent passes governance.
}
```

Runtime event:

```json
{ "type": "intent_declared", "intent": "send_payment" }
```

If a policy rejects the intent, the durable history records `policy_violation` before any action can run.

### Agent trust

Agents can now declare trust level and trust scope:

```synapse
agent Validator {
    model "mock"
    trust level high
    trust scope ["finance", "legal"]
}
```

Policy guards receive a read-only `source` object:

```synapse
policy DataProcessing {
    target "Worker.process"
    guard (args) {
        if source.trust == untrusted {
            reject "source is untrusted"
        }
    }
}
```

Trust levels are ordered as:

```text
untrusted < low < medium < high < critical
```

### `observe`

`observe` registers passive audit hooks. Observers do not participate in delivery or policy decisions; they react to runtime events such as `message_sent`, `message_received`, `policy_evaluated`, `policy_violation`, `receive_timeout`, `intent_declared`, and `memory_forgotten`.

```synapse
observe Worker.process {
    on policy_evaluated => msg {
        print("policy passed: " + msg.policy)
    }
}
```

Observers are suppressed during replay and inside policy guards so they cannot contaminate deterministic execution history.

### Governed `memory.forget`

Deletion is now governed like writing:

```synapse
memory.forget("user_pii") {
    reason "GDPR deletion request"
    audit true
    irreversible true
}
```

Runtime event:

```json
{ "type": "memory_forgotten", "key": "user_pii" }
```

### Inline LLM sugar

The standard form remains valid:

```synapse
let result = llm(prompt "Analyze this")
```

v1.2 also supports:

```synapse
let result = llm "Analyze this"
```


## Synapse v1.2 Cognitive Primitives

### `debate`

`debate` extends `superpose` with explicit multi-round argumentation. Branches can inspect the debate context via `debate.round()` and `debate.history(branch_name)`. The final value is produced by a judge LLM call, with ordinary deterministic `llm_call` replay semantics.

```synapse
let decision = debate {
    branch bull {
        return llm "Argue for expansion"
    }
    branch bear {
        return llm "Argue against expansion"
    }
} judge "neutral_arbiter" rounds 2
```

### `reflect`

`reflect` queries the current `execution_history` without mutating workflow state. It is intended for self-audit and debugging.

```synapse
let calls = reflect {
    last 10 events
    filter type == "llm_call"
}
```

### Pipeline operator `|>`

`|>` is syntactic sugar for left-to-right data flow.

```synapse
data |> clean |> analyze |> summarize
```

This desugars to nested calls: `summarize(analyze(clean(data)))`.

# v1.3 Inner Life Primitives

## `soulprint`

`SoulprintDef` is allowed inside `agent` bodies and defines protected identity metadata:

```synapse
soulprint {
    values: [ curiosity: 0.94, integrity: 1.0 ]
    memory: long_term
    style: "precise"
    version: "1.0"
    protected: true
}
```

Direct assignment to `soulprint` outside `evolve` raises `IdentityCrisisError`.

## `dream`

`dream` creates a sandboxed simulation. The body cannot mutate memory, send actor messages, or migrate. The optional `integrate` clause is the only place where insights may be committed to normal runtime state.

```synapse
dream {
    scenario "counterfactual"
    temperature 0.7
    depth deep
    constraints ["no external effects"]
    return "insight"
} integrate {
    print(dream_result)
}
```

Runtime records `dream_completed`.

## `evolve`

```synapse
evolve self when score < 0.8 after 10 with "AlignmentPolicy" {
    let note = "adjust carefully"
}
```

If the condition is true, the mutation block executes under an evolution guard and records `soulprint_evolved`. In v1.3, `after` is an auditable trigger label captured in the event; executable delayed scheduling / event-count activation is reserved for v1.4.

## Extended `reflect`

```synapse
reflect on self { last 10 events }
reflect on memory { last 10 events }
reflect on values { last 10 events }
```

---

# Synapse v1.4 — Transactional Dream Integration & Evolution Policy

## `assert`

`assert` is a runtime assertion statement. Outside `integrate`, a failed assertion stops execution. Inside `integrate`, a failed assertion triggers the configured transaction failure mode.

```synapse
assert condition, "optional message"
```

## Transactional `integrate`

`dream` remains a sandbox where inference is allowed and mutations are forbidden. `integrate` is the opposite boundary: mutations are allowed, but inference and external asynchronous effects are forbidden.

```synapse
let insight = dream {
    scenario "stress-test rollback design"
    return "add rollback gate"
}

integrate insight {
    memory.write("rollback gate") { reason "dream insight" }
    assert true, "integration valid"
} on fail rollback
```

Supported failure modes:

```text
on fail rollback  # default: restore env and identity/memory snapshots
on fail warn      # keep mutations, emit warning
on fail halt    # restore then stop execution
```

Canonical syntax is `on fail <mode>`. The parser also accepts `on_fail <mode>` for compatibility with design notes.

Replay semantics: `integrate_committed` and `integrate_rollback` are audit-only durable events. They are not used as instruction pointers.

## `evolve ... under Policy`

`evolve` now supports explicit policy binding and delay units:

```synapse
evolve self when user_satisfaction < 0.8 after 10 events under AlignmentPolicy {
    let note = "increase clarity while preserving caution"
}
```

`after N events|seconds|calls` creates an auditable evolution ticket when the condition is not yet satisfied. v1.4 implements event-ticket storage; production scheduling for seconds/calls is reserved for the next runtime scheduler layer.

## Policy-as-code fields

Policies can now expose structured fields in addition to legacy `target`, `require`, `forbid`, and `guard(args) { ... }`:

```synapse
policy AlignmentPolicy {
    target "evolve.Guide"
    trigger: user_satisfaction < 0.8
    cooldown: 10 events
    max_delta: 0.05
    guard: soulprint.values.integrity >= 0.95
    require_approval: false
}
```

`guard: <expr>` is an invariant expression used by evolution enforcement. It is distinct from executable `guard(args) { ... }` used for message/intent policy checks.


## v1.4.1: Replay-Safe Integrate & Governance Enforcement

Synapse v1.4.1 hardens the transactional identity layer introduced in v1.4.

- `integrate` rollback now trims durable/audit/output tails created inside the failed transaction: `execution_history`, `actor_log`, `memory_audit`, `verification_results`, `output_buffer`, and related audit buffers.
- A failed `integrate` records exactly one durable terminal event: `integrate_rollback`. Dead inner events are not replay-visible.
- `integrate` supports `reason "..."` as explicit audit metadata inside the transaction body.
- `evolve ... under Policy` now enforces `max_delta` over `soulprint.values.*` with atomic rollback on violation.
- Regression tests cover rollback history cleanup, output rollback, integrate reason logging, and max-delta blocking.

# v1.5 Fracture Self MVP

## Syntax

```synapse
fracture self into {
    Analyst {
        focus "rational analysis"
        return "position"
    }

    Guardian {
        assert false, "safety concern"
    }
} consensus weighted integrate {
    reason "multi-perspective synthesis"
    print(consensus.positions.Analyst)
}
```

Supported consensus strategies:

- `weighted`
- `majority`
- `unanimous`

## Runtime contract

The base agent is suspended while sub-agents execute in isolated ephemeral contexts with shadow soulprints. The base mailbox is frozen and restored after integration. Sub-agent state is purged after termination.

Death types:

| Type | Meaning | Effect |
|---|---|---|
| `NATURAL` | returned position | participates fully |
| `ABORTED` | local assert failure | reduced/excluded signal |
| `KILLED` | policy/isolation violation | blocking signal, base survives |
| `PANIC` | unexpected runtime failure | aborts the fracture |

Durable events:

- `identity_fractured`
- `subagent_terminated`
- `identity_integrated`
- `fracture_panic`

Nested fracture is intentionally disabled in v1.5 MVP.

## Synapse v1.5.1 — Fracture Polish & Optimization

v1.5.1 polishes the v1.5 `fracture self` layer without changing the public philosophy of identity fracture.

### Nested fracture

A sub-agent may perform one nested fracture for recursive analysis:

```synapse
fracture self into {
    Analyst {
        let micro = fracture self into {
            MicroAnalyst { return "deep" }
            MicroCritic { return "risk" }
        } consensus weighted
        return micro
    }
} consensus weighted
```

Limits:

- top-level fracture depth = `1`;
- nested fracture depth = `2`;
- depth `> 2` is blocked with `NestedFractureException` and recorded as `KILLED_NESTED`;
- nested fracture cannot use `integrate`; it can only return a position to the parent sub-agent.

### Granular sub-agent death

Sub-agent termination now records granular death categories:

```text
NATURAL
ABORTED
KILLED_MEMORY
KILLED_NETWORK
KILLED_NESTED
KILLED_EVOLUTION
KILLED_INTEGRATION
KILLED_ISOLATION
PANIC
```

Only `PANIC` aborts the entire fracture. Policy and isolation failures terminate the offending sub-agent while the base agent continues.

### Ephemeral compaction

Sub-agent internal events are compacted into `ephemeral_summary` in the main durable log:

```json
{
  "type": "subagent_terminated",
  "death_type": "NATURAL",
  "ephemeral_summary": {
    "llm_calls": 2,
    "assertions": 0,
    "reflections": 0,
    "events_total": 2
  }
}
```

A debug trace can be enabled at runtime through `interpreter.fracture_debug_trace = True`.

### Replay skip optimization

In `REPLAY`, fracture recovery tries to skip ephemeral sub-agent logic by jumping from `identity_fractured` to the matching `identity_integrated` event. If the optimized path is not available, runtime falls back to reconstruction.

### Evolve cooldown

`cooldown` inside policy-as-code is now executable. A second evolution inside the cooldown window creates a deferred evolution ticket rather than throwing an exception:

```synapse
policy CooldownPolicy {
    target "evolve.Guide"
    cooldown: 5
}
```

Runtime event:

```json
{
  "type": "evolution_deferred",
  "reason": "cooldown",
  "events_remaining": 5
}
```


## v1.6 Resonance & Inter-subjectivity

### `resonate`

`resonate` computes a read-only `ResonanceProfile` from durable history.

```synapse
resonate with @user {
    depth deep
    aspects ["emotional_tone", "knowledge_level", "urgency"]
    window 20
    bind profile
}
```

The profile is a dict-like object with dot-access:

```synapse
profile.aspects.emotional_tone.value
profile.drift_vector.urgency
profile.recommendation
```

Built-in aspects: `emotional_tone`, `knowledge_level`, `humor`, `urgency`, `trust_level`, `formality`, `creativity`. Unknown aspects are safe and return low-confidence null results.

Isolation rules:

| Context | `resonate` | Reason |
|---|---:|---|
| normal/base agent | yes | external calibration belongs to the base identity |
| dream | no | dreams are internal simulations |
| fracture/sub-agent | no | sub-agents are internal perspectives, not direct user-calibrated entities |
| integrate | yes | read-only, cached over preexisting history |

### `reflect on fractures`

```synapse
let log = reflect on fractures { last 10 events }
```

Returns fracture-related durable events: `identity_fractured`, `identity_integrated`, `subagent_terminated`, `fracture_panic`.

### `measure identity_coherence`

```synapse
measure identity_coherence {
    window 100
    metrics ["soulprint_stability", "fracture_consensus_rate", "resonance_drift"]
    bind coherence
}
```

Produces a score and per-metric diagnostics.

## v1.7 Production Hardening Runtime APIs

These are host-runtime APIs, not Synapse source-language keywords.

### Persistent state

```python
from synapse import Interpreter, SQLiteStorage

interp = Interpreter().attach_storage(SQLiteStorage("state/synapse.db"), run_id="agent-1")
interp.save_runtime_state()
restored = Interpreter().attach_storage(SQLiteStorage("state/synapse.db"), run_id="agent-1")
restored.load_runtime_state()
```

### Provenance chain

`history_hash_chain()` creates a tamper-evident hash chain over `execution_history`. `verify_history_chain(chain)` returns `False` if any event is removed, duplicated or modified.

### Metrics

`metrics_snapshot()` returns JSON-safe runtime diagnostics. `metrics_text()` returns Prometheus-style text suitable for a lightweight `/metrics` endpoint.

### Stress harness

`RuntimeStressHarness` mutates durable event streams deterministically and verifies whether provenance checks detect the mutation.


# v1.8 Collective Intelligence

## Collective dream

```synapse
policy SharedCollective {
    target "collective.*"
    collective_dream: true
}

collective dream with [Peer] under "SharedCollective" {
    scenario "resource conflict resolution"
    converge_on "shared_protocol_v2"
    depth deep
    timeout 300
    bind shared_dream
}
```

Semantics: asynchronous-by-default shared blackboard sandbox. Participants operate over a start-time snapshot, not live shared state. Runtime emits `collective_dream_initiated`, `collective_dream_position_submitted`, and either `collective_dream_consensus_reached` or `collective_dream_timeout`. Consensus documents include `document_hash` and per-participant signatures.

## Cross-agent resonance privacy

`resonate with Agent` requires explicit opt-in:

```synapse
policy PeerReadable {
    target "resonance.Peer"
    resonance_readable: true
}
```

Default is private. `resonate with @user` remains allowed.

## Distributed consensus

```synapse
distributed consensus with [Peer] on "deploy_v2" {
    quorum 2
    timeout 30
    policy "MajorityVote"
    bind vote
}
```

A non-cognitive governance primitive. If quorum is reached, emits `distributed_consensus_committed`; otherwise emits `distributed_consensus_deferred` and creates a retry ticket.

## Swarm fracture

```synapse
swarm fracture with [Peer] under "SharedCollective" {
    scenario "system failure recovery"
    roles { Peer -> Analyst }
    consensus unanimous
    timeout 60
    bind swarm_result
}
```

Swarm fracture is distinct from collective dream: collective dream produces a read-only consensus document; swarm fracture coordinates role-bearing agent positions for action-oriented consensus.


# v1.9 Cognitive Continuity on Production Spine

## Runtime Contract

`memory palace`, `intention cascade`, `plan weave`, and `habit from pattern` provide durable cognitive continuity. Memory is not treated as a transient cache; it is a structured runtime object with rooms, trace metadata, confidence, source attribution and backend selection.

The reference implementation keeps the language semantics independent from any vendor database. `backend sqlite` is immediately executable. `backend postgresql` and Redis-style time-series boundaries are exposed as adapters for production deployments.

## Syntax

```synapse
memory palace "AgentMemory" {
    rooms { episodic semantic procedural }
    decay_policy { episodic -> 30 days semantic -> never procedural -> 90 days }
    consolidate during dream
    backend sqlite
    bind palace
}

imprint into palace.semantic {
    content "User prefers Russian language"
    confidence 0.97
    source "resonate_with_user"
    trace_id "optional-trace"
    bind imprint_id
}

recall from palace.semantic {
    query "Russian language"
    threshold 0.4
    limit 3
    bind memories
}

intention cascade "ZeroDowntimeDeploy" {
    mission "Ensure continuous service"
    objective "Migrate database schema"
    task "Create consistent backup"
    action "run pg_dump --consistent"
    bind plan
}

plan weave with [self] under "SharedCollective" {
    intention plan
    checkpoint every 2 steps
    rollback on failure
    timeout 120
    bind execution
}

habit from pattern {
    frequency > 3
    stability > 0.9
    promote_to palace.procedural
    activation_condition "when deployment task repeats"
    energy_cost 0.3
    bind habit_id
}

consolidate palace {
    rooms ["episodic", "semantic", "procedural"]
    bind consolidation
}
```

## Durable Events

- `memory_palace_created`
- `memory_imprinted`
- `memory_recalled`
- `memory_consolidated`
- `intention_cascade_created`
- `plan_weave_completed`
- `habit_formed`

## Metrics

`metrics_snapshot()` now includes:

- `memory_palaces_total`
- `intention_cascades_total`
- `habits_total`
- `memory_imprints_total`
- `memory_recalls_total`
- `plan_weaves_total`

---

# Synapse v2.0: Affective Runtime & Cognitive VM

v2.0 adds two production-oriented layers:

1. **Affective Runtime** — computational PAD state, affective events, modulation, affective resonance, somatic markers, and affective tags for Memory Palace.
2. **Cognitive VM boundary** — bytecode program representation, serializable VM state, gas metering, and transition hashing. This is a conservative VM prelude: it does not replace the tree-walking runtime yet, but defines the execution substrate required for future middle-of-program resume.

## Affective state

```synapse
affective state "AgentMood" {
    dimensions {
        valence [-1.0, 1.0]
        arousal [0.0, 1.0]
        dominance [0.0, 1.0]
    }
    baseline {
        valence 0.2
        arousal 0.4
        dominance 0.6
    }
    decay 0.05 per minute
    bind mood
}
```

`valence`, `arousal`, and `dominance` follow the PAD model. They are computational control signals, not claims of subjective feeling.

## Affective event

```synapse
affective event "policy_violation" {
    valence -0.8
    arousal 0.6
    dominance -0.3
    duration 300
    bind emotional_tag
}
```

The runtime logs `affective_event_tagged` and stores the tag as memory metadata candidate.

## Affective modulation

```synapse
affective modulation {
    bind modulation_rules
}
```

The runtime derives suppression/elevation hints from current PAD state. For example, strongly negative valence suppresses risky operations and elevates reflection/dream/fracture.

## Affective resonance

```synapse
affective resonance with @user {
    mirror emotional_tone
    regulate valence
    dampen arousal 0.2
    bind emotional_bridge
}
```

This extends `resonate` from a one-way perception layer to a regulated emotional bridge.

## Somatic marker

```synapse
somatic marker "deploy_decision" {
    threshold 0.4
    bind marker
}
```

A negative gut-feeling below threshold escalates to fracture-style parallel evaluation.

## Cognitive VM boundary

```synapse
compile vm { source "let x = 1" bind code }
run vm { source code gas 50 bind result }
```

The v2.0 VM supports:

- bytecode program object;
- instruction pointer;
- serializable stack/locals;
- gas metering;
- transition hash;
- host-boundary cognitive opcodes.

This is the first step toward future O(1)-style VM snapshots and middle-of-program resume.


## Synapse v2.1.0 — Affective Memory Layer

v2.1.0 intentionally implements only the first approved v2.1 subpatch. It does not include CVM checkpoints, reactive thresholds, or living habits yet.

### New memory semantics

- `imprint` can store `affective_tag` as a bound affective event or inline PAD literal.
- `affective_decay` supports `N events`, `N days`, and `never`. Day-based decay is converted into event units and logs the original value for audit.
- `recall` supports `affective_filter` and `affective_sort`.
- `consolidate` supports `affective_routing` for promoting emotionally salient episodic memories into semantic/procedural rooms.

### Example

```synapse
affective event "critical_failure" {
    valence -0.9
    arousal 0.8
    dominance -0.4
    bind failure_tag
}

imprint into palace.episodic {
    content "Production migration failed at step 3"
    confidence 0.99
    affective_tag failure_tag
    affective_decay 7 days
    bind imprint_id
}

recall from palace.episodic {
    query "migration"
    affective_filter valence < -0.5
    affective_sort arousal desc
    limit 3
    bind painful_memories
}
```

### Durable events

- `memory_imprinted` includes `affective_tag_snapshot`, `affective_expires_at_event`, and `affective_decay_original` when applicable.
- `memory_affective_tag_expired` records deterministic tag expiration.
- `memory_recalled` records the affective filter string and result count.
- `memory_consolidated` records promotions/kept count/energy cost for affective routing.

## v2.1.1 CVM Foundation

v2.1.1 adds the canonical Cognitive VM checkpoint/resume layer while keeping the legacy tree-walking interpreter available. The VM remains conservative: unsupported AST is routed through `HOST_EVAL`, and only the fixed Host ABI is allowed.

### `compile vm`

```synapse
compile vm {
    source "let x = 1"
    bind code
}
```

### `run vm` with checkpoint

```synapse
run vm {
    source code
    gas 100
    cognitive_budget 5
    checkpoint "after_init" at_ip 1
    bind partial
}
```

Supported checkpoint triggers:

- `checkpoint "label" at_ip N`
- `checkpoint "label" before_op IMPRINT`

Only one checkpoint clause is allowed per `run vm` block.

### `resume_from`

```synapse
run vm {
    resume_from "after_init"
    gas 100
    cognitive_budget 3
    bind final
}
```

`source` and `resume_from` are mutually exclusive.

### CVM snapshot

The canonical snapshot fields are:

```json
{
  "version": "2.1",
  "ip": 1,
  "stack": [],
  "locals": {},
  "gas_remaining": 99,
  "cognitive_budget_remaining": 5,
  "transition_hash": "sha256:...",
  "last_processed_event_id": "evt-00000000",
  "palace_cursor": "tx-0",
  "intention_sp": 0,
  "mood_snapshot": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
  "current_context": null,
  "history_hash": "..."
}
```

On resume, the runtime verifies the checkpoint history prefix. A mismatch raises `VMTamperDetectedError`; a log shorter than the checkpoint event raises `VMResumeSyncError`.

### Fixed Host ABI

The supported symbolic host opcodes are fixed for v2.1: `SEND`, `RECEIVE`, `IMPRINT`, `RECALL`, `METRICS`, `AFFECT_EVENT`, `AFFECT_STATE`, `FRACTURE_SELF`, `DREAM`, `LLM_EVAL`, `HABIT_SUGGEST`, `THRESHOLD_CHECK`, plus `HOST_EVAL` as a conservative fallback boundary. Custom opcodes are rejected with `UnknownOpcodeError`.

### Durable events

- `vm_bytecode_compiled`
- `vm_checkpoint_saved`
- `vm_resumed`
- `vm_tamper_detected`
- `vm_resume_sync_error`
- `vm_host_call`
- `vm_executed`

## Synapse v2.1.2 — Reactive Affective Thresholds

This subpatch implements the first part of the Reactive Affective Layer: named `affective threshold` blocks and threshold purity enforcement.

### Threshold definition

```synapse
affective threshold "HighStress" {
    when arousal > 0.7 and valence < -0.4
    for 5 events
    cooldown 30 events
    priority critical
    action {
        reflect on self { last 5 events }
        suspend emergency_pause("high_stress_detected")
    }
}
```

Supported condition operators use canonical PAD names and aliases normalized by the parser:

- `valence` / `pleasure`
- `arousal` / `energy`
- `dominance` / `control`

Only `and` is supported in v2.1.2. `or` is reserved for v2.2.

### Purity contract

Threshold actions are internal reactions. They may use read-only introspection and durable suspension requests, but may not directly mutate the world.

Allowed in `action { ... }`:

- `reflect`
- `suspend emergency_pause(...)`

Forbidden in `action { ... }`:

- `send`
- `migrate`
- `memory.write`
- `memory.forget`
- direct `imprint`
- any `declare intent`

Violations raise `ThresholdPurityViolation`.

### Runtime behavior

After each live top-level event, the runtime evaluates registered thresholds using a sliding event window. Ready thresholds are sorted by priority (`critical > high > medium > low`) and declaration order. Observers are suppressed while threshold actions run. Thresholds are not recomputed in `REPLAY` mode; replay consumes historical threshold events.

Durable events:

- `affective_threshold_registered`
- `affective_threshold_triggered`
- `threshold_suspend_requested`
- `affective_threshold_action_failed`


## v2.1.2-B Reactive Affective Guard & Consensus

This patch extends the reactive affective layer with three replay-safe mechanisms:

- `mood` is injected into policy `guard (args)` as a frozen read-only PAD snapshot. Mutation raises `GuardMutationError`.
- `fracture ... consensus affective_weighted(mood) { ... }` requires explicit `bias` mapping per branch or `Default bias ...`; missing bias raises `ConsensusBiasMissingError`.
- `debate ... affective_bias(mood)` does not change branch weights; it injects PAD-aware guidance into the judge prompt.

`affective resonance` and Living Habits remain outside this subpatch.

## v2.1.2-C Atomic Affective Resonance

`affective resonance with target { ... }` now mutates the active PAD state atomically. The runtime computes all `mirror`, `regulate`, and `dampen` deltas first, then applies them in one state transition and emits exactly one `affective_resonance_applied` event with `atomic: true`.

Replay semantics: in `REPLAY`, the runtime consumes `affective_resonance_applied` and applies the stored `events_applied` deltas instead of recomputing from the current resonance profile.

Isolation: `affective resonance` is forbidden in `dream` and inside fracture sub-agents. Inside sub-agents the violation terminates only the sub-agent as `KILLED_ISOLATION`.

## Synapse v2.1.3-A — Living Habits Phase A

This phase introduces the runtime foundation required by Living Habits without enabling habit activation yet.

### `energy_pool`

`energy_pool` may be declared inside an `agent` or at top level. Recharge is event-based only.

```synapse
agent Worker {
    model "mock"
    energy_pool {
        max 100
        initial 80
        recharge 5 per 100 events
        rest_threshold 15
        hysteresis_margin 5
    }
}
```

Runtime emits:

- `energy_pool_recharged`
- `agent_entered_rest`
- `agent_exited_rest`

REST hysteresis is enforced: the agent enters REST when `energy < rest_threshold` and exits only when `energy >= rest_threshold + hysteresis_margin`.

### `context`

```synapse
context "deployment_task" {
    print("inside durable context")
}
```

Runtime emits `context_entered` before the block and `context_exited` after the block. `current_context` is restored after the block.

## Synapse v2.1.3-C — Living Habits: Body Execution, Fatigue, Recovery

This phase closes the Living Habits loop introduced in v2.1.3-A/B.

### Runtime rules

- Habit bodies execute from the in-memory `HabitRegistry`, never from `palace.procedural`.
- `palace.procedural` stores declarative habit metadata only.
- Candidate evaluation remains event-driven via `event_type -> habits` lookup.
- `suppress when` retains priority over activation.
- In LIVE mode, a candidate emits `habit_candidate_suggested` and then executes immediately.
- In REPLAY mode, habit candidate evaluation and body execution are skipped; state is reconstructed from durable events.
- If an `energy_pool` exists, energy is consumed on successful body execution. If body execution fails, activation count is unchanged and energy is refunded.
- If no `energy_pool` exists, habits execute with backward-compatible free energy semantics.
- Recursive execution of the same habit is blocked with `HabitRecursionError`.
- Global habit nesting depth is capped at `max_habit_depth = 3`.
- Fatigue transitions: `FRESH -> FATIGUED -> FRESH` after `require_rest` non-activation events.
- Observers are suppressed while habit body internals execute.

### Durable events

- `habit_candidate_suggested`
- `habit_activated`
- `habit_suppressed`
- `habit_fatigued`
- `habit_resting`
- `habit_recovered`
- `habit_execution_failed`


---

## §A. Contextual Identifiers (Soft Keywords)

*Added in v2.2.0-alpha3e-p0*

### Motivation

Synapse has a rich vocabulary of reserved words that also appear naturally
as user-defined function names, parameter names, and method names (e.g.
`recall`, `max`, `send`).  Treating them as globally forbidden identifiers
forces unnecessary renaming and breaks idiomatic code.

From alpha3e-p0 onwards the parser officially supports **contextual
identifiers** (also called *soft keywords*): tokens that are treated as
reserved words in their structural positions but are accepted as plain
identifiers in name-bearing positions.

### Hard keywords vs soft keywords

**Hard keywords** are never valid as identifiers regardless of position.
They introduce statements or blocks and would create syntactic ambiguity
if used as names:

```
fn  let  if  else  while  for  return  import
agent  policy  guard  dream  fracture  debate  superpose
weave  soulprint  resonate  evolve  intent  declare
affective  habit  memory palace  context (block)
```

**Soft / contextual keywords** may appear as identifiers in the positions
listed below:

| Token | Example hard use | Example soft use |
|-------|-----------------|-----------------|
| `recall` | `recall { ... }` (memory block) | `fn recall(x)`, `obj.recall(x)` |
| `imprint` | `imprint "fact"` (memory write) | `fn imprint(data)`, `obj.imprint(x)` |
| `max` | *(builtin call)* | `fn max(a, b)`, `let max = 10` |
| `send` | `send Agent.method()` (actor msg) | `fn send(msg)` param name |
| `receive` | `receive { ... }` (actor block) | `fn receive(msg)` param name |
| `pattern` | `from pattern { ... }` (habit) | `fn foo(pattern)` param |
| `context` | `context "label" { }` (block) | `fn context(x)` method name |
| `body` | structural field | `fn body()` method name |
| `state` | structural field | `fn state()` method name |
| `source` | structural field | `fn source()` method name |
| `action` | structural field | `fn action()` method name |
| `content` | structural field | `fn content()` method name |
| `memory` | `memory palace` / `memory.op()` | `fn memory()` method name |
| `spawn` | `spawn Agent` (actor) | `fn spawn(cfg)` param |
| `migrate` | `migrate to node` (actor) | `fn migrate(x)` method |
| `suspend` | `suspend until` (actor) | `fn suspend()` method |
| `await` | `await promise` | `fn await(p)` param |
| `evolve` | `evolve soulprint` | `fn evolve(x)` method |
| `plan` | structural field | `fn plan(x)` method |
| `filter` | builtin / habit | `fn filter(x)` method |
| `promote` | habit promote | `fn promote(x)` method |
| `keep` | habit keep | `fn keep(x)` param |
| `tag` | structural field | `fn tag(x)` method |
| `trust` | structural field | `fn trust(x)` param |
| `level` | structural field | `fn level(x)` param |
| `scope` | structural field | `fn scope(x)` param |
| `reason` | structural field | `fn reason(x)` param |

### Allowed positions for soft keywords as identifiers

1. **Function name** — immediately after `fn` keyword: `fn recall(x) { ... }`
2. **Parameter name** — inside `fn` parameter list: `fn foo(pattern, body)`
3. **Member access target** — after `.`: `obj.recall(x)`, `memory.recall(x)`
4. **Callable expression** — as a standalone call: `max(nums)`, `recall(q)`

### Disallowed positions (keyword wins)

Soft keywords retain their keyword semantics at **statement-start** and
**block-introducer** positions:

```synapse
// statement-start → keyword semantics:
recall { episodic ... }          // memory recall block
send Worker.process("job")       // actor message send

// name-bearing position → identifier semantics:
fn recall(pattern) { ... }       // OK: user-defined function
let r = obj.recall("query")      // OK: method call
```

### Planned: backtick escape (alpha3e Track 0.2)

A future patch will introduce backtick-quoted identifiers as an explicit
escape for any token in statement-start position:

```synapse
`recall`("query")   // planned: escaped call at statement-start
```

This is **not yet implemented** in alpha3e-p0.  Until then, assign to a
variable first if needed:

```synapse
let recall_fn = memory.recall
recall_fn("query")
```

### Implications for tooling and LLM code generation

- **Syntax highlighters** should highlight soft keywords as keywords by
  default; context-sensitive modes may de-highlight them in name positions.
- **LLM code generation**: when generating `.syn` code, treat soft keywords
  as reserved unless the target position is explicitly a name-bearing one
  (after `fn`, inside `()` params, after `.`).  When uncertain, use a
  different name to avoid ambiguity.

## Track B.1 — Lexical checked effects for guarded side effects

### Supported in Alpha3e

The supported source-level lowering form is the inline guarded memory write:

```synapse
try {
    memory.write("key") { guard true }
} catch (GUARD_VIOLATION) {
    print("write denied")
}
```

`policy enforce { ... }` block lowering is planned for a later RFC and is not
part of Alpha3e.

Track B.1 introduces a conservative source-level rule for guarded side effects.
A governed side-effect statement may raise `GUARD_VIOLATION`; therefore it must
appear inside a local lexical recovery block:

```synapse
try {
    memory.write("key") { guard true }
} catch (GUARD_VIOLATION) {
    print("write denied")
}
```

The compiler inserts `GUARD_VIOLATION_ACK` as the first bytecode instruction in
the handler. User source cannot call `acknowledge_violation()` or emit ACK
manually.

### Compile-time rule

A `GovernedMemoryWrite` or equivalent guarded side-effect without an enclosing
`try/catch(GUARD_VIOLATION)` ancestor before the current function boundary is a
`CompileError`.

Track B.1 deliberately performs only lexical AST checking. It does not perform
unreachable-code analysis, dataflow analysis, or interprocedural checked-effect
propagation. A helper function that contains a governed side effect must contain
its own local recovery block, even if every current caller is wrapped in a
`try/catch(GUARD_VIOLATION)` block.

Future versions may introduce `throws GUARD_VIOLATION` or a non-throwing guard
form by RFC; neither is part of Track B.1.
