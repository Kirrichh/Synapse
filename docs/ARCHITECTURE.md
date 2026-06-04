# Synapse Architecture & Rationale

## Design Principles
- Durable-by-default: event sourcing + deterministic replay.
- Governance-as-code: policies, guards, consequences встроены в язык.
- Cognitive continuity: memory, affect, habits, identity как примитивы первого класса.
- Verifiable execution: CVM snapshots, tamper-evidence, transition hashes.

## Trade-offs
- Tree-walking interpreter сохранён до v2.2 для обратной совместимости.
- Wall-clock time запрещён в пользу event-based метрик для replay-safety.
- Аффективные примитивы используют frozen snapshots в guard/consensus для детерминизма.

## Roadmap
- v2.1.4: Runtime Consolidation.
- v2.2.0-alpha: CVM Core; v2.2.0 roadmap continues toward Full CVM Compiler, Dynamic Opcodes, Cognitive Budget Enforcement.
- v2.2.1: Async Habits, Coroutine Contexts, Background Consolidation.

## RuntimeFacade Decomposition — v2.1.4-B/C

`Interpreter` теперь является тонким оркестратором AST и окружений. Доменная логика вынесена за `self.runtime.*` через `RuntimeFacade`; runtime-модули используют `host_getter`/callbacks, чтобы избежать circular imports и сохранить публичные поля интерпретатора.

| Facade Slot | Module | Runtime Contract |
|---|---|---|
| `runtime.replay` | `synapse/runtime/replay_engine.py` | `execution_history`, replay cursor, deterministic side-effects, history hashes. |
| `runtime.governance` | `synapse/runtime/governance_engine.py` | policy registry, guard execution, rollback, purity, frozen mood snapshots. |
| `runtime.affective` | `synapse/runtime/affective_runtime.py` | PAD state, thresholds, resonance, atomic affective mutations. |
| `runtime.habit` | `synapse/runtime/habit_engine.py` | habit registry facade, activation routing, recursion/depth guard, observer suppression. |
| `runtime.actor` | `synapse/runtime/actor_runtime.py` | mailboxes, send/receive, promises, spawn, migration, sync/async receive split. |
| `runtime.vm` | `synapse/runtime/vm_bridge.py` | `compile vm`, `run vm`, HOST_ABI, checkpoint/resume, tamper/sync verification. |
| VM routing | `synapse/runtime/vm_routing.py` | explicit CVM/HOST_EVAL boundary, fallback logging, `vm_coverage_ratio`. |

### CVM Evolution (v2.2.0-alpha)

v2.2.0-alpha expands the CVM execution surface from experimental `CompileVmStmt`/`RunVmStmt` to a deterministic subset compiler (`CognitiveCompiler`) and VM (`CognitiveVM`). The interpreter decomposition is preserved: `interpreter.py` remains the orchestrator, while base-language constructs compile to `BytecodeProgram.version == "2.2"`. Cognitive primitives remain in the `HOST_EVAL` path via dual-surface routing in `vm_routing.py`.

### CVM Boundary Hardening

v2.1.4-C intentionally does not expand the compiler. It makes the current hybrid execution boundary observable:

- `CompileVmStmt` and `RunVmStmt` route through `VMBridge`/CVM.
- Fixed v2.1 HOST_ABI opcodes are classified by `classify_host_opcode()`.
- All other AST execution remains tree-walking `HOST_EVAL` until v2.2.
- Fallbacks are logged as `vm_fallback` and counted by `metrics_snapshot()["vm_coverage_ratio"]`.
- Checkpoint/resume continues to use existing `history_hash_until()` and transition-hash semantics.

This gives v2.2 a measurable migration target without changing v2.1 runtime behavior.


## Alpha.3-D1 Dual History Model

Alpha.3-D1 separates replay identity from observability side effects.

- `execution_history` is the replay lookup stream for nondeterministic host-call resolution events keyed by unique `call_id`.
- `side_effect_history` is the observability stream for deterministic side effects such as `print` and structural context events.
- Deterministic pure calls are recomputed and do not need history lookup.

A replay lookup for nondeterministic host calls is call-id based, not positional. Zero matching events or duplicate matching events are both fail-closed `VMResumeSyncError` cases.

## Alpha.3-D1 VMStatus State Machine

```text
STATUS_RUNNING
  -- nondeterministic CALL_HOST pause --> STATUS_PAUSED_HOST_CALL
STATUS_PAUSED_HOST_CALL
  -- resume(call_id, value) success --> STATUS_RUNNING
STATUS_PAUSED_HOST_CALL
  -- resume failure --> STATUS_HALTED with vm.state.error
STATUS_RUNNING
  -- HALT / terminal runtime error --> STATUS_HALTED
```

`resume()` and bridge-side `resume_host_call()` never increment IP: `CALL_HOST` has already pre-incremented the instruction pointer before entering the paused state.


## Promise Resolution & Actor Integration

Alpha.3-D2 implements bridge-side promise resolution on top of the Alpha.3-D1
single pending host-call lifecycle. A promise is keyed by the durable `call_id`
created by the paused CVM host call. `VMBridge.create_promise(call_id, vm=...)`
creates a `PromiseRecord`, appends a `promise_created` event, and may suspend the
current actor through `ActorRuntime.suspend_on_promise()`.

Resolution remains history-bound: `promise_resolved` and `promise_rejected`
events are appended to `execution_history`, and replay lookup is performed by
unique `call_id`, never by positional cursor alone. For D1 compatibility the
bridge also records a `host_call_resolved` event for the same `call_id`.

Actor integration is deliberately minimal in D2: `wake_on_resolve()` and
`wake_on_reject()` enqueue mailbox notifications and mark the actor as running.
D2 preserves the single-pending-call invariant per VM; multiple concurrent
promises, cancellation, timeouts, streaming, and language-level `YIELD`/`AWAIT`
syntax remain out of scope.


## Actor Definition as Structural Wrapper

Alpha.3-D3 compiles `AgentDef` and `SubAgentDef` as structural runtime wrappers. CVM records actor-scope structure with `actor_stack` and ACTOR_ENTER/ACTOR_EXIT opcodes, while actor registry, mailbox topology and runtime scheduling remain behind `VMBridge` and `actor_runtime`. This mirrors the ContextBlock pattern and prevents actor-runtime internals from leaking into the pure CVM substrate.


## PolicyDef as Structural Runtime Wrapper

Alpha.3-D4 compiles PolicyDef and PolicyRule as structural runtime wrappers. CVM tracks only policy scope via policy_stack and delegates all host-visible events to VMBridge/governance runtime. Policy enforcement, conflict resolution, capability mutation, messaging, and async behavior remain outside the CVM substrate.

---

## Cognitive Primitive Classification

*Added in v2.2.0-alpha3e-p0. This table is the official gatekeeping
mechanism for new language features: any new primitive must fit into one
of the five categories before it can be merged.*

| Category | Execution layer | Guarantees | Examples |
|----------|----------------|------------|---------|
| **Pure Computational** | CVM only | Deterministic gas · Full VMSnapshot · 100% replay | arithmetic, if/else, while, for, fn, list/dict ops, closures |
| **Structural Wrapper** | CVM enter/exit opcodes + VMBridge boundary | Fixed-cost gas at enter/exit · Context stacks serialised · Capability check on boundary | `agent`, `policy`, `guard` (static), `context "label"`, `send`/`receive` |
| **Host-mediated Deterministic** | CVM pauses (PAUSED_HOST_CALL) + Host ABI | Replayable via `host_call_resolved` event log · No repeated external call on replay · Capability gates enforced | `LLMCall`, `PromptExpr`, LLM-based guard (planned: alpha3e Track A) |
| **Runtime Orchestration** | host / tree-walker only | No CVM gas metering · No VMSnapshot · Compiler emits `RuntimeFallback` note | `dream {} integrate {}`, `debate {} judge`, `superpose`, `resonate`, complex affective filters |
| **Experimental / Research** | opt-in runtime flag | No stability guarantees · Not used for canonical replay | `cognitive_budget` affective overlay, Acoustic Soulprints |

### CVM-First Design Rule

Any primitive added to the language **must** either:

1. Have a bytecode route (CVM opcodes + VMBridge dispatch), or
2. Be explicitly declared `runtime-only` with documented limitations
   (no gas metering, no snapshot, no replay guarantee).

The compiler emits a `Note: runtime-only primitive — no gas/snapshot/replay
support` for category 4 nodes. In beta this note becomes a warning, and
`--strict-cvm` promotes it to an error.

---


## Static Audit Methodology

Alpha3e separates three different questions that were previously conflated
by the corpus report:

1. **parser-supported** — the `.syn` corpus parses into AST nodes.
2. **lowerable-to-CVM** — an AST node may still be counted as an AST fallback,
   but the compiler can lower the supported source form into CVM bytecode.
3. **runtime/tree-walker-only** — the node has no accepted CVM lowering path and
   remains a real runtime-only fallback.

`GovernedMemoryWrite` is classified as `lowerable_to_cvm` for the Track B.1
inline guard form (`memory.write(...) { guard ... }`). The static AST fallback
count remains 103 in Stable Alpha3e, but `runtime_only_fallbacks` is 99 after subtracting
lowerable nodes. This is an audit-methodology distinction only; it does not
change runtime semantics or routing tables.

## Host ABI Version

`HOST_ABI_VERSION` (in `synapse/runtime/host_abi.py`) tracks the
VM-visible host-call surface independently from the language release version.

| ABI version | When bumped | What changed |
|-------------|-------------|--------------|
| `2.2.0-alpha3b2` | alpha3b2 | Original capability enforcement surface established |
| `2.2.0-alpha3e-p0` | alpha3e-p0 | `MSG_SEND` / `MSG_RECEIVE` opcodes (alpha3d5) added VM-visible host symbols; `STATUS_PAUSED_MESSAGING` / `pending_message_receive` added to VMSnapshot contract |
| `2.2.0-alpha3e-track-a` | Track A | Deterministic LLM/Prompt Bridge: `PROMPT_BUILD`, `LLM_REQUEST`, `LLM_RESUME`, and `llm.request` dispatch contract |
| `2.2.0-alpha3e` | Alpha3e | Guard runtime/bytecode contracts plus Track B.1 inline guarded-memory lowering and audit-methodology stabilization |

**Next expected bump:** Alpha3f may introduce approved debugger/replay CLI or other VM-visible contracts.

The ABI version is checked at snapshot restore time
(`vm_bridge.py:403`). A mismatch raises `VMResumeSyncError` — snapshots
created under a different ABI cannot be resumed without explicit migration.

## Track B.1 — Source → AST → CVM guard lowering

Track B introduced guard runtime infrastructure. Track B.1 begins closing the
language loop by lowering governed memory writes into CVM guard bytecode when a
local recovery context is present.

Lowering shape:

```text
GUARD_ENTER(policy_hash, guard_hash)
  [guard expression bytecode]
  GUARD_CHECK_RESULT
  [protected side-effect body]
GUARD_EXIT(verdict)
```

For failing guard checks, the compiler branches to the local
`catch(GUARD_VIOLATION)` handler and inserts `GUARD_VIOLATION_ACK` before any
handler body instruction. The ACK remains bytecode-internal and is not available
as source syntax.

The checked-effect model is intentionally lexical in Track B.1. Interprocedural
propagation (`throws GUARD_VIOLATION`) is a future RFC, not a hidden compiler
inference.
