# Synapse Architecture Overview

- **Status:** Current as of Track C conclusion (Alpha3f)
- **Scope:** Documentation only — describes what exists in code today
- **Purpose:** A single top-down mental map of how a `.syn` program flows
  through the system, which module owns each stage, and where the boundary
  between canonical and exploratory execution lies.

Every claim in this document is anchored to a real module. If a statement says
"the CVM executes bytecode," it refers to `synapse/cvm.py`. Nothing here
describes a planned or hypothetical feature; deferred work is named explicitly
in the final section.

---

## 1. What Synapse is

Synapse is a DSL and runtime for programming **AI-agent behavior** — agents,
LLM calls, memory, reasoning flows, policies, checked assertions, and durable
actor execution. It is not a trainable model; it orchestrates external LLMs and
records every meaningful step into an auditable, replayable history.

The defining property is **replay-verifiability**: a recorded run can be
replayed without calling live nondeterministic producers, and two runs can be
compared at the level of a tamper-evident hash chain. The rules for what may
enter that chain are defined in `docs/DETERMINISM_CONTRACT.md`.

---

## 2. The data flow, end to end

```
  .syn source
      │
      ▼
  Lexer            synapse/lexer.py        (class Lexer)
      │  tokens
      ▼
  Parser → AST     synapse/parser.py       (class Parser)
      │  AST nodes  synapse/ast.py
      ▼
  ┌───────────────────────────────────────────────┐
  │  Execution                                     │
  │                                                │
  │  Tree-walking Interpreter  synapse/interpreter.py
  │       │                                        │
  │       │  compiles / lowers to bytecode where   │
  │       │  supported                             │
  │       ▼                                        │
  │  CognitiveVM (CVM)         synapse/cvm.py      │
  │       │  bytecode          synapse/bytecode.py │
  │       ▼                                        │
  │  VMBridge (capability /    synapse/runtime/    │
  │  security boundary)        vm_bridge.py        │
  └───────────────────────────────────────────────┘
      │  every meaningful step appended to
      ▼
  execution_history  (ordered event stream)
      │
      ▼
  hash_event_chain()          synapse/hardening.py
      │  tamper-evident forensic chain
      ▼
  Golden artifact             synapse/golden_replay.py
      │  manifest.json + history.json + snapshots + llm_cache.mock.json
      ▼
  GoldenArtifactTraceAdapter  synapse/debugger_core.py
      │  immutable TraceContextProtocol view
      ▼
  find_trace_divergence()     synapse/debugger_core.py
      │  first point two traces diverge (by chain hash)
      ▼
  synapse debug compare       synapse/cli.py
         structured JSON + exit code
```

---

## 3. Layers and responsibilities

**Language layer** — `lexer.py`, `parser.py`, `ast.py`. Turns `.syn` source
into an AST. Soft-keyword rules let language keywords be used as identifiers
where unambiguous.

**Execution layer** — `interpreter.py` (tree-walker) and `cvm.py` (bytecode
VM). The interpreter orchestrates; constructs that are lowered to bytecode run
on the CVM with gas metering. Bytecode definitions live in `bytecode.py`.
Guard opcodes (`GUARD_ENTER`, `GUARD_CHECK_RESULT`, `GUARD_EXIT`,
`GUARD_VIOLATION_ACK`) are executed here as a deterministic enforcement
boundary.

**Bridge layer** — `runtime/vm_bridge.py`. The capability/security boundary for
host calls. Classifies host symbols as side-effecting or pure (fail-closed),
enforces guard-violation blocking, and routes LLM requests through a
content-addressable cache so that replay never calls a live provider.

**History and hashing** — events land in `execution_history`;
`hardening.py:hash_event_chain()` derives a tamper-evident chain where each
position's hash also depends on every preceding event.

**Golden replay** — `golden_replay.py` records a run into an artifact directory
(`manifest.json`, `history.json`, VM snapshots, mock LLM cache) and can replay
it deterministically using only embedded mocks.

**Debugger core** — `debugger_core.py`. Fork identity and lifecycle
(`ForkRegistry`, `ForkRecord`), copy-on-write state isolation (`OverlayMap`,
`ForkedVMState`), the trace protocol (`TraceContextProtocol`,
`GoldenArtifactTraceAdapter`), and the divergence engine
(`find_trace_divergence`, `TraceDivergenceResult`).

**CLI** — `cli.py`. Thin transport. Parses arguments, loads artifacts,
delegates all forensic logic to the core, formats JSON, maps exit codes. The
CLI never computes a hash itself.

---

## 4. Where the LLM lives

LLM calls are made through the interpreter's `LLMCall` path, which routes
through the bridge's content-addressable cache. On replay, `LLMCall` consumes
the recorded `llm_call` event via `next_history_event("llm_call")` and does not
call the provider. This is why LLM nondeterminism is controllable: the response
is a recorded resource, not a live regeneration. Determinism is achieved by
recording and consumption, not by `temperature=0` (see the determinism
contract, §5).

---

## 5. Where guards and policy live

Guards are a deterministic enforcement boundary in the CVM. A guarded effect
lowers to `GUARD_ENTER → GUARD_CHECK_RESULT → effect → GUARD_EXIT`. A failed
guard sets `guard_violation_active`, which blocks side-effecting host calls
until a compiler-inserted `GUARD_VIOLATION_ACK` (emitted inside
`catch(GUARD_VIOLATION)`) clears it. Guard frames are immutable
(`GuardFrame`, frozen) so snapshots never share mutable references. On replay,
a guard verdict is taken from the recorded `GUARD_EXIT` event rather than
re-evaluated.

---

## 6. Canonical vs exploratory

**Canonical** execution produces the `execution_history` used for golden
replay, trace comparison, and forensic verification. Everything in the
canonical chain must be replay-verifiable.

**Exploratory** execution is a non-canonical debug branch — a fork. Forks use
copy-on-write state (`OverlayMap`) so that a speculative branch never mutates
its parent or the golden artifact. Injected events must pass the
`EventInjectionValidator`; forbidden injections (guard verdict override,
capability grant, hash rewrite, direct ACK injection) stay forbidden.

The copy-on-write layer is what makes forks safe: a fork reads through to its
parent for unchanged keys but writes only to its own overlay, and mutable
parent values are materialized into the overlay before mutation. This is why
two forks from the same baseline are fully isolated in state.

---

## 7. Module reference

| Module | Responsibility |
|--------|----------------|
| `synapse/lexer.py` | Tokenize `.syn` source |
| `synapse/parser.py` | Build AST (soft-keyword aware) |
| `synapse/ast.py` | AST node definitions |
| `synapse/interpreter.py` | Tree-walking orchestration; lowering to bytecode |
| `synapse/cvm.py` | Bytecode VM, gas metering, guard opcodes, `VMState`/`GuardFrame` |
| `synapse/bytecode.py` | Bytecode program, opcodes, guard cleanup table |
| `synapse/runtime/vm_bridge.py` | Capability boundary, host-symbol classification, LLM cache |
| `synapse/hardening.py` | `hash_event_chain`, canonical JSON |
| `synapse/golden_replay.py` | Record / replay golden artifacts |
| `synapse/debugger_core.py` | Forks, trace protocol, divergence engine |
| `synapse/cli.py` | CLI transport (`run`, `replay`, `debug`) |
| `docs/DETERMINISM_CONTRACT.md` | Which events may enter the canonical chain |

---

## 8. What is NOT built yet (deferred to Alpha3g)

To keep this map honest, the following are explicitly **not** implemented and
are blocked behind Alpha3g RFCs (see `docs/ALPHA3F_PLANNING_GATE.md`):

- **Deterministic Replay Runner** (step-loop that executes a recorded artifact
  with cache injection and chain validation). Still deferred — integrate golden
  fixtures now exist (I6) but durable crash-resume, genesis baseline, and
  resource cleanup (INT-04/05/06) are not yet implemented.
- **Dream replay contract** — *implemented as of Alpha3g* (RFC-DREAM-REPLAY-CONTRACT,
  Path A): `dream_completed` is now replay-consumed and verified via
  `dream_key`/`result_hash` (`interpreter.py:1328-1392`). DreamBlock is Category B
  (result-hash replay-safe). Strict Layer 1 eligibility is denied under A2 by
  RFC-DREAM-STRICT-LAYER1-ELIGIBILITY; future eligibility requires a
  consume-only/subtrace/state-delta replay model. See
  `docs/DETERMINISM_CONTRACT.md` §6.1.1.
- **Integrate replay-applier** — *implemented as of Alpha3g I1–I6*
  (RFC-INTEGRATE-REPLAY-APPLIER.md APPROVED): `integrate_committed` /
  `integrate_aborted` recorded in LIVE, body skipped in REPLAY, write-set
  applied with hash verification (`interpreter.py:1986-2038`). Integrate is now
  Category B (replay-safe). Strict Layer 1 eligibility pending 5 deferred MAJOR
  gates (INT-04..INT-08, see `docs/RFC-INTEGRATE-REVIEW-NOTES.md`).
- **Stable identity policy** — several events carry UUID-bound identity
  (`ares-`, `evo-`, `habit-`, `consensus-`) that is replay-safe but not
  stable across independent live-runs.
- **Affective event-id stabilization**, builtin `time`/`random`/`uuid` policy,
  persistence determinism audit.
- **Session persistence / daemon / REPL** — `compare` currently works on
  artifact directories within a single process, not across shell invocations.

---

## Practical tutorial

For the smallest verified end-to-end workflow — record two artifacts, replay
them with embedded mocks, and compare the traces — see
`docs/tutorials/TRACE_COMPARE_TUTORIAL.md`.
