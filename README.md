# Synapse — язык программирования для AI

> **Synapse** — DSL/runtime для программирования AI-поведения: агентов, LLM-вызовов, памяти, потоков рассуждения, суперпозиции вариантов, политик, проверяемых утверждений, последствий действий и устойчивого actor-выполнения.

Текущая версия: v2.2.0-alpha3e (release line). Track C (Time-Travel Debugger) завершён в Alpha3f.

## Статус проекта

- **Язык и рантайм:** стабильны на линии alpha3e (lexer → parser → tree-walker / CVM → bridge).
- **Track C — Time-Travel Debugger:** инфраструктура trace/replay/compare завершена.
  - Golden artifact → trace bridge (`GoldenArtifactTraceAdapter`)
  - Движок расхождений (`find_trace_divergence`)
  - CLI `synapse debug compare` со структурированным JSON и стабильными exit-кодами
  - Контракт детерминизма (`docs/DETERMINISM_CONTRACT.md`)
- **Alpha3g в работе:** `DreamBlock` replay-контракт **реализован** (Path A, `dream_completed` replay-consumed с верификацией `dream_key`/`result_hash`) — `DreamBlock` теперь Category B (см. `docs/DETERMINISM_CONTRACT.md` §6.1). Strict Layer 1 eligibility для dream закрыта default-deny under A2: нужен будущий consume-only/subtrace/state-delta replay model. Integrate replay-applier, stable identity policy, deterministic replay runner и session persistence остаются за gate. См. `docs/ALPHA3F_PLANNING_GATE.md`.

## Документация

- `docs/ARCHITECTURE_OVERVIEW.md` — сквозная карта данных source → CVM → history → replay → compare с привязкой к модулям.
- `docs/DEBUGGER_USER_GUIDE.md` — практическое руководство: record / replay / compare, exit codes, ограничения.
- `docs/tutorials/TRACE_COMPARE_TUTORIAL.md` — end-to-end tutorial с фактическим record → replay → compare выводом.
- `docs/DETERMINISM_CONTRACT.md` — какие события могут попадать в canonical hash chain (категории A/B/C).
- `docs/RFC-DREAM-REPLAY-CONTRACT.md` — Alpha3g RFC (APPROVED, implemented): как `DreamBlock` стал replay-safe через recorded `dream_completed`.
- `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` — Alpha3g+ RFC (DRAFT, doc-only): почему `DreamBlock` остаётся вне Strict Layer 1 under A2 и какие consume-only/subtrace/state-delta условия нужны для будущей eligibility.
- `docs/CHANGELOG.md` — полная история изменений по версиям и патчам (включая предыдущие v0.x–v1.x записи, ранее жившие в этом README).

## Возможности

- **Агенты** как объекты первого класса — создавайте, компонуйте и делегируйте.
- **Цепочки рассуждений** (`thought`) — декларативное описание многошагового AI-процесса.
- **Суперпозиция** (`superpose`) — параллельные ветви решения с выбором результата.
- **Память** (`memory`) — краткосрочная память агентов и управляемые записи.
- **LLM-вызовы** (`llm`) — прямой доступ к языковым моделям из кода.
- **Потоки** (`flow`) — именованные конвейеры исполнения.
- **Governance primitives** — `policy`, `claim`, `verify`, `consequence`.
- **Actor primitives** — `send`, `receive`, mailbox state, suspension points.
- **Durable replay** — event-sourced history для надежного восстановления.
- **Полный MVP-интерпретатор** — lexer, parser, AST, runtime, tests, examples, VS Code syntax highlighting.

## Release gates

Stable Alpha3e requires:

```bash
make test
make lint
make audit
make test-golden
```

## Быстрый старт

```bash
python main.py examples/hello_agent.syn
python main.py examples/consequence_aware.syn
python main.py examples/durable_actor.syn
python main.py examples/replay_governance.syn
python main.py examples/side_effects_checkpoint.syn
python main.py examples/receive_timeout.syn
python main.py examples/fifo_audit.syn
python main.py --repl
```

## Минимальный пример

```synapse
agent Greeter {
    model "mock"

    fn greet(name) {
        let p = prompt "hello"
        return llm(p)
    }
}

fn main() {
    let bot = Greeter()
    print(bot.greet("World"))
}
```

## Actor + governance пример

```synapse
policy FinancialControl {
    target "Worker.process"
    forbid "nuclear-launch"
}

agent Worker {
    model "mock"
}

send Worker.process("job-42")
```

Если payload совпадает с запрещенным значением, runtime выбросит `PolicyViolationException` до записи сообщения в mailbox.

## Durable replay lifecycle

```python
from synapse import compile_to_ast
from synapse.interpreter import Interpreter, Suspension

source = '''
let p = prompt "durable question"
let answer = llm(p)
print(answer)
'''

ast = compile_to_ast(source)

# Server A
interpreter_a = Interpreter()
flow_a = interpreter_a.interpret_async(ast)
status = next(flow_a)
assert isinstance(status, Suspension)

try:
    flow_a.send("stored answer")
except StopIteration:
    pass

snapshot = interpreter_a.snapshot()

# Server B
interpreter_b = Interpreter()
interpreter_b.load_snapshot(snapshot)
flow_b = interpreter_b.interpret_async(ast)
try:
    next(flow_b)
except StopIteration:
    pass

assert "stored answer" in interpreter_b.get_output()
```

## Проверка

```bash
python -m py_compile synapse/*.py
python tests/test_lexer.py
python tests/test_parser.py
python tests/test_interpreter.py
python tests/test_durable_actor.py
python tests/test_replay_governance.py
python tests/test_determinism_checkpoint.py
python tests/test_receive_timeout_audit.py
```

## Архитектурное ограничение v0.5

Synapse v0.5 не сериализует Python frames/generators. Вместо этого он сохраняет **историю недетерминированных событий** и переисполняет исходный код. Это более надежная модель для кросс-процессного восстановления, чем попытка сериализовать внутренности host runtime.

## Determinism Drift protection example

```synapse
let chance = random()

if chance > 0.5 {
    let response = llm(prompt "Execute strategy A")
    print(response)
} else {
    print("Skip execution")
}
```

В LIVE первый результат `random()` сохраняется как:

```json
{
  "type": "side_effect",
  "name": "random",
  "result": 0.8
}
```

В REPLAY runtime не вызывает настоящий генератор случайных чисел. Он берет историческое значение из `execution_history`, поэтому ветка исполнения остается той же.

## State checkpoint artifact

```python
checkpoint = interpreter.create_state_checkpoint("after-critical-section")
snapshot = interpreter.snapshot()
```

Checkpoint в v0.5 является JSON-safe артефактом состояния и history offset. Он подготавливает почву для будущей log compaction, но не выдает себя за полноценный instruction pointer. Истинный middle-of-program resume потребует continuation cursor или bytecode layer.

---

## Что нового в v0.6

Synapse v0.6 добавляет **Semantic Guardrails** — исполняемые блоки `guard (args) { ... }` внутри `policy`.

Ключевой паттерн v0.6: внутренние шаги guard не загрязняют основной `execution_history`. В LIVE-режиме guard выполняется в read-only контексте, а в журнал пишется только атомарный вердикт:

- `policy_evaluated` — политика пропустила действие;
- `policy_violation` — политика заблокировала действие.

В REPLAY-режиме guard-код не исполняется заново. Runtime читает исторический вердикт политики, поэтому изменение промпта или алгоритма guard в новой версии политики не ломает воспроизводимость старых логов.

```synapse
policy SafetyGov {
    target "Worker.process"

    guard (args) {
        let analysis = llm(prompt "Classify request safety")
        if args[0].contains("unsafe") {
            reject "Semantic policy violation"
        }
    }
}
```

Ограничения guard-контекста:

- разрешены локальные `let`-переменные и проверки;
- разрешены nondeterministic builtins/`llm`, но их внутренние события не попадают в основной workflow log;
- запрещены `send`, `memory.write`, `memory.clear` и присваивание во внешние переменные;
- итог guard фиксируется только как атомарный durable verdict.


## Synapse v0.8: Swarm Mobility & Location Transparency

Synapse now supports a first mobility layer for distributed AI runtimes. The runtime does not serialize Python frames. Instead it emits a portable **mobility envelope** containing source code, deterministic execution history, mailboxes, actor audit state and routing metadata. A remote node restores by replaying source + history.

### `migrate`

```synapse
agent Worker {
    model "mock"
}

let self = Worker
migrate "node-b:9000"
```

In coroutine mode this yields:

```text
Suspension(reason="migration_requested", payload={"target": "node-b:9000", "actor": "Worker"})
```

### Mobility envelope

```python
envelope = interpreter.dump_state(
    source_code=source,
    actor_name="Worker",
    target_node="node-b:9000",
    reason="migration_requested",
)
```

The envelope is JSON-safe and contains no host-language stack or raw Python frame.

### Location-transparent send

```python
interpreter.register_route("Worker", "node-b:9000")
```

Then:

```synapse
send Worker.process("remote-job")
```

creates a `forward_message` packet rather than mutating the local mailbox.

### `synapsed.py`

A minimal asyncio Swarm Node daemon is included. It accepts:

- `migrate_actor` packets;
- `forward_message` packets.

This is a prototype transport boundary, not yet a production network layer. Production requires authentication, persistence, retries and backpressure.


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

## Synapse v1.3 — Inner Life Runtime

Synapse v1.3 introduces protected identity and sandboxed simulation primitives:

- `soulprint` defines an agent's protected value matrix, memory identity type, style guide, and durable identity version.
- `dream { ... } integrate { ... }` executes a sandboxed simulation; external side effects such as actor sends, migration, and memory mutation are blocked inside the dream body and must be performed explicitly in `integrate`.
- `evolve self when ... after ... with ... { ... }` records governed identity evolution through `soulprint_evolved` events.
- `reflect on self|memory|values { ... }` adds focused self-audit queries over identity, memory, or value state.

Example:

```synapse
agent Guide {
    model "mock"
    soulprint {
        values: [ curiosity: 0.94, integrity: 1.0 ]
        memory: long_term
        style: "precise, cautious, evidence-first"
    }
}

let self = Guide

let insight = dream {
    scenario "stress-test a risky deployment"
    temperature 0.8
    depth deep
    return llm "Imagine hidden failure modes"
} integrate {
    memory.write(dream_result) {
        reason "integrated dream insight"
        retention user_controlled
    }
}

evolve self when true after 10 with "AlignmentPolicy" {
    let note = "review identity drift"
}
```

## v1.4: Transactional Dream Integration & Evolution Policy

Synapse v1.4 adds a transactional boundary between simulated insight generation and real-state mutation.

New primitives:

- `assert condition, "message"`
- `integrate dream_result { ... } on fail rollback|warn|halt`
- `evolve self when condition after N events under PolicyName { ... }`
- policy-as-code fields: `trigger:`, `cooldown:`, `max_delta:`, `guard:`, `require_approval:`

Design invariant:

```text
dream     -> inference allowed, mutations forbidden
integrate -> mutations allowed, inference/external async effects forbidden
```

This keeps `dream` causally isolated while letting selected insights enter real state under transaction semantics.


## v1.4.1: Replay-Safe Integrate & Governance Enforcement

Synapse v1.4.1 hardens the transactional identity layer introduced in v1.4.

- `integrate` rollback now trims durable/audit/output tails created inside the failed transaction: `execution_history`, `actor_log`, `memory_audit`, `verification_results`, `output_buffer`, and related audit buffers.
- A failed `integrate` records exactly one durable terminal event: `integrate_rollback`. Dead inner events are not replay-visible.
- `integrate` supports `reason "..."` as explicit audit metadata inside the transaction body.
- `evolve ... under Policy` now enforces `max_delta` over `soulprint.values.*` with atomic rollback on violation.
- Regression tests cover rollback history cleanup, output rollback, integrate reason logging, and max-delta blocking.

## Synapse v1.5 — Fracture Self MVP

Synapse v1.5 introduces controlled identity fracture for multi-perspective cognition. A base agent can temporarily split into isolated sub-agents, collect their positions, and integrate a consensus without allowing sub-agents to mutate durable state directly.

```synapse
fracture self into {
    Analyst {
        return llm "Analyze the rational case"
    }

    Guardian {
        assert false, "safety concern"
    }
} consensus weighted integrate {
    print(consensus.deaths.Guardian)
}
```

Death contract:

- `NATURAL`: sub-agent returned a position.
- `ABORTED`: local assert failed; base agent continues.
- `KILLED`: policy/isolation violation; base agent continues and consensus receives a blocking signal.
- `PANIC`: unexpected runtime error; the entire fracture aborts.

Sub-agents may call `llm`, but cannot `memory.write`, `memory.forget`, `send`, `migrate`, `evolve`, `integrate`, `dream`, or nested `fracture` in the v1.5 MVP.

## v1.5.1 Fracture Polish

This patch hardens `fracture self`:

- nested fracture is allowed up to depth 2;
- nested fracture cannot integrate;
- sub-agent deaths are granular (`KILLED_MEMORY`, `KILLED_NETWORK`, `KILLED_NESTED`, etc.);
- sub-agent histories are compacted into `ephemeral_summary`;
- replay skips from `identity_fractured` to `identity_integrated` when possible;
- `evolve` policy `cooldown` creates deferred tickets instead of crashing the workflow.


## Synapse v1.6 — Resonance & Inter-subjectivity

Synapse v1.6 adds read-only inter-subjective calibration primitives:

```synapse
resonate with @user {
    depth deep
    aspects ["emotional_tone", "knowledge_level", "urgency"]
    window 20
    bind profile
}

reflect on fractures { last 10 events }

measure identity_coherence {
    window 50
    metrics ["soulprint_stability", "fracture_consensus_rate", "resonance_drift"]
    bind coherence
}
```

Runtime invariants:

- `resonate` is read-only and deterministic over `execution_history`.
- `resonate` is forbidden inside `dream`.
- `resonate` is forbidden inside `fracture`; in sub-agent context it terminates only that sub-agent as `KILLED_ISOLATION`.
- Profiles are cached by target, aspects, window, and history hash.
- Unknown aspects return `{value: null, confidence: 0.0, error: "unknown_aspect"}`.

## Synapse v1.7 — Production Hardening

v1.7 adds operational foundations for running Synapse as a durable runtime rather than only a local interpreter:

- `SQLiteStorage` and `InMemoryStorage` storage backends for JSON-safe runtime snapshots and event batches.
- Interpreter APIs: `attach_storage()`, `save_runtime_state()`, `load_runtime_state()`, `append_runtime_events()`.
- Tamper-evident event history hash chains: `history_hash_chain()` and `verify_history_chain()`.
- Runtime metrics: `metrics_snapshot()` and Prometheus-compatible `metrics_text()`.
- `RuntimeStressHarness` for deterministic event-stream chaos checks.

This patch intentionally does not introduce a bytecode VM yet. It creates the storage, metrics and provenance boundary needed before middle-of-program continuation work.


## Synapse v1.8 — Collective Intelligence

Adds production-safe collective cognition primitives:

- `collective dream with [...] under Policy { ... }` — asynchronous shared blackboard-style sandbox with signed consensus document.
- `distributed consensus with [...] on topic { quorum ... }` — governance vote primitive with durable commit/deferred events.
- `swarm fracture with [...] under Policy { ... }` — coordinated cross-agent fracture boundary with role assignment.
- Cross-agent `resonate with Agent` now requires explicit `resonance_readable: true` policy; privacy is deny-by-default.
- Collective events carry deterministic trace/span IDs and signatures for provenance and observability.

Example: `examples/collective_intelligence.syn`.


## Synapse v1.9 — Cognitive Continuity on Production Spine

Synapse v1.9 adds a durable cognitive memory and planning layer on top of the v1.7 production spine and v1.8 collective intelligence layer.

### Memory Palace

```synapse
memory palace "AgentMemory" {
    rooms { episodic semantic procedural }
    decay_policy {
        episodic -> 30 days
        semantic -> never
        procedural -> 90 days
    }
    consolidate during dream
    backend sqlite
    bind palace
}
```

Rooms:
- `episodic`: events, trace_id, source, confidence, contextual evidence.
- `semantic`: durable facts and knowledge graph-ready assertions.
- `procedural`: habits, skills, activation triggers and optimized patterns.

### Imprint / Recall

```synapse
imprint into palace.semantic {
    content "User prefers Russian language"
    confidence 0.97
    source "resonate_with_user"
    bind imprint_id
}

recall from palace.semantic {
    query "Russian language"
    threshold 0.4
    limit 3
    bind memories
}
```

### Intention Cascade and Plan Weave

```synapse
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
```

### Habit Formation

```synapse
habit from pattern {
    frequency > 3
    stability > 0.9
    promote_to palace.procedural
    energy_cost 0.3
    bind habit_id
}
```

The v1.9 reference implementation is dependency-free: SQLite is available immediately; PostgreSQL/Redis-compatible boundaries are represented by adapters that can be replaced by real drivers in production deployments.

## Synapse v2.0 — Affective Runtime & Cognitive VM

v2.0 introduces computational emotion and a VM execution boundary:

- `affective state` — PAD state: valence, arousal, dominance.
- `affective event` — durable emotional tags for runtime events and memory.
- `affective modulation` — runtime hints that suppress/elevate cognitive actions.
- `affective resonance` — regulated emotional bridge with `@user` or another target.
- `somatic marker` — heuristic decision marker that can escalate to `fracture`.
- `compile vm` / `run vm` — bytecode boundary with gas metering and transition hashes.

The VM in v2.0 is intentionally conservative: it coexists with the tree-walking interpreter while defining the serializable execution substrate needed for future middle-of-program resume.


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

## Synapse v2.1.1 — CVM Foundation

This patch adds the first canonical Cognitive VM checkpoint/resume layer:

- `compile vm { source ... bind code }`
- `run vm { source code gas N cognitive_budget M checkpoint "label" at_ip N bind result }`
- `run vm { resume_from "label" gas N cognitive_budget M bind result }`
- canonical CVM snapshot fields: IP, stack, locals, gas, cognitive budget, transition hash, event cursor, palace cursor, mood snapshot, current context and history hash.
- fixed Host ABI only; custom opcodes are rejected until v2.2.
- tamper detection and resume sync errors are durable events.

Example:

```synapse
compile vm { source "let x = 1" bind code }
run vm { source code gas 100 checkpoint "after_init" at_ip 1 bind partial }
run vm { resume_from "after_init" gas 100 bind final }
```

### v2.1.2 Reactive Affective Thresholds

Synapse now supports named reactive PAD thresholds:

```synapse
affective threshold "HighStress" {
    when arousal > 0.7 and valence < -0.4
    for 2 events
    cooldown 10 events
    priority high
    action {
        suspend emergency_pause("high_stress_detected")
    }
}
```

Threshold actions are purity-checked. They may suspend for internal emergency pause flows, but cannot `send`, `migrate`, `imprint`, write/forget memory, or declare intents directly.


## v2.1.2-B Reactive Affective Guard & Consensus

This patch extends the reactive affective layer with three replay-safe mechanisms:

- `mood` is injected into policy `guard (args)` as a frozen read-only PAD snapshot. Mutation raises `GuardMutationError`.
- `fracture ... consensus affective_weighted(mood) { ... }` requires explicit `bias` mapping per branch or `Default bias ...`; missing bias raises `ConsensusBiasMissingError`.
- `debate ... affective_bias(mood)` does not change branch weights; it injects PAD-aware guidance into the judge prompt.

`affective resonance` and Living Habits remain outside this subpatch.

### v2.1.2-C Atomic Affective Resonance

This patch completes the reactive affective layer with atomic `affective resonance`: bridge deltas are batched, applied once to PAD state, and logged as `affective_resonance_applied`. Replay uses the stored event rather than recomputing live resonance.

### v2.1.3-A: Living Habits foundation

Adds event-based `energy_pool` and durable `context "label" { ... }` blocks. This phase intentionally does not execute habit bodies yet; it provides the energy/rest and context substrate for v2.1.3-B/C.

## v2.1.3-C Living Habits

Living Habits now execute registered `body { ... }` blocks from the runtime `HabitRegistry`. The procedural room in Memory Palace remains declarative metadata only. Phase C adds energy consumption, recursion locks, failure semantics, fatigue/recovery, and durable lifecycle events.

Example:

```synapse
habit "DeepAnalysis" from pattern {
    energy_cost 2
    activate when { context "analysis_task" }
    fatigue after 2 activations {
        energy_cost_multiplier 1.5
        require_rest 2 events
    }
    body { print("habit body executed") }
    bind habit_id
}
```

