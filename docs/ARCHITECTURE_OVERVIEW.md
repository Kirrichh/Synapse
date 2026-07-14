# Synapse Architecture Overview

- **Document authority:** data flow, module ownership, execution boundaries,
  and canonical versus exploratory paths.
- **Not authoritative for:** current implementation status, future sequencing,
  or historical chronology.
- **Current status authority:**
  [CURRENT_IMPLEMENTATION_STATUS.md](CURRENT_IMPLEMENTATION_STATUS.md).
- **Future work authority:** [ROADMAP.md](ROADMAP.md).
- **History authority:** [CHANGELOG.md](CHANGELOG.md).

This document is a top-down technical map of how `.syn` source enters the
runtime, which modules own each stage, how host effects are bounded, and how a
recorded execution becomes replay and diagnostic evidence. Status words in
this document are explanatory only; the status register owns audited status,
guarantees, boundaries, explicitly absent components, and replay eligibility.

## 1. System Definition

Synapse is a programming language and runtime for governed, durable,
reproducible, and auditable AI behavior. It is not a trainable model. It
orchestrates LLM and host capabilities through language semantics, runtime
state, capability checks, execution history, and verification boundaries.

The central architectural property is **replay-verifiability**: eligible
nondeterministic results and decisions are recorded so a mock replay can
consume history rather than invoke live producers. Eligibility is not
universal. The [determinism contract](DETERMINISM_CONTRACT.md) defines which
events may enter a canonical chain and which constructs require stronger
replay models.

## 2. End-to-End Data Flow

```text
.syn source
    |
    v
Lexer                              synapse/lexer.py
    | tokens
    v
Parser                             synapse/parser.py
    | AST
    v
AST model                          synapse/ast.py
    |
    +---------------------------+
    |                           |
    v                           v
Tree-walking Interpreter        Cognitive compiler / bytecode
synapse/interpreter.py          synapse/bytecode.py
    |                           |
    |                           v
    |                           CognitiveVM
    |                           synapse/cvm.py
    |                           |
    +-------------+-------------+
                  |
                  v
VMBridge / Host ABI              synapse/runtime/vm_bridge.py
                                 synapse/runtime/host_abi.py
                  |
                  v
execution_history                ordered runtime events
                  |
                  v
hash_event_chain()               synapse/hardening.py
                  |
                  v
golden artifact                  synapse/golden_replay.py
                  |
                  v
mock replay                      synapse/golden_replay.py
                  |
                  v
trace adapter / compare          synapse/debugger_core.py
                  |
                  v
structured diagnostics          synapse/cli.py
```

The compact project spine is:

```text
.syn -> Lexer -> Parser -> AST -> Interpreter/CVM -> VMBridge/Host ABI
     -> execution_history -> hash chain -> golden artifact -> replay
     -> trace compare -> diagnostics
```

## 3. Language Ownership

`synapse/lexer.py` tokenizes source. `synapse/parser.py` produces nodes defined
in `synapse/ast.py`; soft-keyword handling permits keywords to act as
identifiers where grammar context is unambiguous.

The AST includes ordinary computational syntax and domain constructs for:

- agents, sub-agents, actor messaging, mailbox receive, spawn, suspension,
  promises, await, and migration;
- policy, guards, intents, claims, verification, and consequences;
- memory palaces, imprint, recall, consolidation, forgetting, and planning;
- dream/integrate, soulprint/evolve, fracture, resonance, debate, reflection,
  affective runtime, and living habits;
- CVM compile/run and state-oriented operations.

An AST node proves that syntax and a structural contract exist; it does not by
itself prove durable execution, CVM lowering, strict replay, production
networking, or external-provider authority. Those distinctions belong in the
status register.

## 4. Execution Ownership

### 4.1 Tree-walking interpreter

`synapse/interpreter.py` owns broad orchestration semantics. It evaluates
language constructs, manages local runtime state, records events, and delegates
eligible computation or host work. Its surface is broader than the durable
execution subset and broader than strict replay eligibility.

### 4.2 Cognitive VM and bytecode

`synapse/bytecode.py` defines the instruction model and compiler paths.
`synapse/cvm.py` owns bytecode execution, gas and cognitive budget state,
transition hashing, VM snapshots, and deterministic guard opcodes. Structural
wrappers may route orchestration back to runtime owners; their presence does
not transfer actor, memory, affective, or habit internals into the CVM.

`synapse/runtime/vm_routing.py` classifies AST nodes for CVM or host/runtime
routing. Static corpus coverage and runtime coverage answer different
questions: one measures parsed nodes, the other measures executed statements.

### 4.3 Durable application path

`synapse/application.py` owns package-level run and REPL entry paths and the
durable run/resume surface. Durable recovery records source, history, actor
state, mailboxes, promises, and routing metadata; it does not serialize Python
frames. Some constructs supported by the tree-walker remain outside the
durable execution subset.

### 4.4 Actor runtime

`synapse/runtime/actor_runtime.py` owns actor registry, mailbox, promise, and
related actor lifecycle state. Actor definitions may have structural CVM
wrappers, while mailbox ordering, suspension, delivery, routing, and durable
promise behavior remain runtime responsibilities.

## 5. Host and Capability Boundary

`synapse/runtime/vm_bridge.py` and `synapse/runtime/host_abi.py` form the
language/VM-to-host boundary. The bridge classifies symbols, enforces
capability and guard constraints, creates deterministic request envelopes, and
routes host results back into VM/runtime state.

LLM calls are Category B operations under the determinism contract. Replay
uses recorded/cache-bound results; deterministic replay does not mean that a
fresh live provider call is intrinsically deterministic. Missing capability,
missing cache material, malformed host responses, and policy violations fail
closed.

The AS2 adapter and external-provider verification work add further bounded
projection, identity, capability, idempotency, persistence, and audit
contracts. Verification-only PostgreSQL/PgBouncer/Debezium/Redpanda evidence
does not enable the production AS2 path and is not production infrastructure
sign-off.

## 6. Governance Boundaries

Policies and guards are defined in language/runtime layers and have bytecode
enforcement paths where supported. A guarded effect follows a shape such as:

```text
GUARD_ENTER -> GUARD_CHECK_RESULT -> effect -> GUARD_EXIT
```

A violation blocks governed effects until the runtime/compiler-managed
acknowledgement path clears the violation state. Guard frames are snapshot-safe
values. Replay consumes recorded eligible verdicts instead of re-running a
live guard producer.

Intents govern prospective action. Claims, verification records, and
consequences describe evidence and follow-up semantics. These mechanisms are
not universal proof engines: each claim is bounded by its oracle, recorded
inputs, and execution path.

## 7. Memory and Cognitive Runtime Ownership

Memory Palace, episodic/semantic/procedural rooms, imprint, recall,
consolidation, and forgetting are interpreter/runtime-owned semantics with
storage and audit boundaries. Cognitive constructs such as dream, integrate,
soulprint, evolve, fracture, resonate, debate, and reflect operate through the
tree-walker and specialized runtime state.

Important boundaries:

- `dream` isolates simulation-side effects; eligible replay consumes a recorded
  `dream_completed` result, while strict Layer 1 execution still requires a
  stronger consume-only/subtrace/state-delta model;
- `integrate` provides a transactional mutation boundary and replay-applier
  contract, but deferred strict eligibility gates remain;
- identity, fracture, resonance, affective, and habit records may be
  replay-aware without being stable across independent live runs;
- the procedural memory room is not automatically verified reusable knowledge;
- raw transcript carry is baseline retry context, not admitted evidence.

## 8. History, Hashing, and Artifacts

Meaningful runtime events are appended to `execution_history`.
`synapse/hardening.py` supplies canonical JSON and `hash_event_chain()`, where
each position depends on the canonical event and the preceding chain state.

`synapse/golden_replay.py` records artifact directories containing a manifest,
history, initial/final snapshots, source, and mock LLM cache as applicable. An
artifact is replay input and forensic evidence; it is not a universal snapshot
of every external system.

The main lifecycle is:

```text
LIVE/record
  -> append eligible nondeterministic results and decisions
  -> canonicalize and hash history
  -> write golden artifact
REPLAY/mock
  -> load source, snapshots, history, and recorded resources
  -> consume recorded Category B results
  -> compare final state/history expectations
```

State checkpoints are JSON-safe state/history artifacts. They do not imply
serialization of host frames or a general continuation cursor for every
language construct.

## 9. Debugger and Exploratory Execution

`synapse/debugger_core.py` owns:

- immutable trace views through `TraceContextProtocol` and
  `GoldenArtifactTraceAdapter`;
- first-divergence analysis through `find_trace_divergence()`;
- fork identity and lifecycle;
- copy-on-write overlays that isolate speculative state;
- event-injection validation.

Canonical execution is the recorded chain used for replay and verification.
Exploratory forks are non-canonical diagnostic branches. A fork cannot rewrite
the parent artifact, inject a capability grant, override a guard verdict, or
promote itself to canonical evidence merely because it executed.

`synapse/cli.py` is transport: it parses commands, loads artifacts, delegates
to core APIs, emits structured diagnostics, and maps exit codes. It does not
own the hashing or divergence algorithms.

## 10. Controlled Change and SWE-bench Experiment Boundary

`synapse/change/` owns controlled task loading, committed trusted inputs,
candidate application, scope checks, command execution, verified commits,
evidence references, reports, and cleanup. Controlled change requires a
committed task and committed patch/input bridge. It is not a general sandbox
or a claim that all commands are environmentally isolated.

`synapse/experiments/swebench/` owns experiment contracts around baseline and
Gold attempts:

- the C1 Gold runner materializes an already-obtained worker result, creates a
  committed bridge, calls controlled change, validates `GoldEvidence`, invokes
  an oracle on a fresh detached verified commit, and writes the attempt;
- the Gold SWE-bench oracle binding derives `model_patch` from the verified
  single-parent commit pair, not from dirty worktree state;
- paired measurement is success-only and blocks token/cost/performance claims;
- measurement output and admission candidate modules are contract boundaries,
  not runtime telemetry, carry, application memory, or FULL verification.

Raw baseline retry carry stays separate from Gold evidence. A canonical
provider telemetry gateway, runtime evidence admission, distilled carry,
RepositoryKnowledge, Gold-with-carry, and integrated Gold runtime remain
outside current runtime ownership.

## 11. Storage, Metrics, and External Verification

Storage adapters persist JSON-safe runtime state and event batches. Runtime
metrics expose execution observations but do not by themselves establish
cross-run performance or economic comparability.

The repository also contains verification-only AS2 infrastructure exercises,
including SQLite-shaped contracts and external PostgreSQL/CDC paths. Their
purpose is to validate specified backend semantics. Production enablement,
backend rollout, operational SLOs, credentials, and official infrastructure
sign-off remain distinct gates.

## 12. Module Ownership Reference

| Module or area | Primary responsibility |
| --- | --- |
| `synapse/lexer.py` | Tokenize `.syn` source |
| `synapse/parser.py` | Parse tokens into AST |
| `synapse/ast.py` | Language node definitions |
| `synapse/interpreter.py` | Broad tree-walking orchestration and runtime semantics |
| `synapse/bytecode.py` | Bytecode and compiler paths |
| `synapse/cvm.py` | Cognitive VM, gas, transitions, snapshots, guard opcodes |
| `synapse/runtime/vm_routing.py` | CVM versus runtime routing classification |
| `synapse/runtime/vm_bridge.py` | Capability and host-call bridge |
| `synapse/runtime/host_abi.py` | Host ABI contracts |
| `synapse/runtime/actor_runtime.py` | Actor registry, mailbox, and promise state |
| `synapse/application.py` | Canonical application/CLI execution paths and durable subset |
| `synapse/hardening.py` | Canonicalization and event-chain hashing |
| `synapse/golden_replay.py` | Golden artifact recording and mock replay |
| `synapse/debugger_core.py` | Forks, trace adapters, and divergence diagnostics |
| `synapse/change/` | Controlled-change execution and applied evidence |
| `synapse/experiments/swebench/` | Baseline/Gold experiment contracts and bounded evidence adapters |
| `synapse/persistence.py` | Runtime storage adapters |
| `synapse/metrics.py` | Runtime metrics formatting and snapshots |
| `synapse/cli.py` | Command transport and structured output |

## 13. Canonical, Experimental, and Design-Target Boundaries

Architecture describes paths, not maturity labels:

- a **canonical path** is an implementation path selected by current runtime or
  artifact contracts;
- an **exploratory path** supports diagnostics or prototypes without becoming
  canonical evidence;
- a **design target** is a named future architecture with no current runtime
  authority.

For the audited classification of every contour, use the
[Current Implementation Status](CURRENT_IMPLEMENTATION_STATUS.md). For future
dependencies and gates, use the [Roadmap](ROADMAP.md). For dated changes, use
the [Changelog](CHANGELOG.md).

## 14. Practical Guides

- [Debugger User Guide](DEBUGGER_USER_GUIDE.md)
- [Trace Compare Tutorial](tutorials/TRACE_COMPARE_TUTORIAL.md)
- [Golden Replay Contract](GOLDEN_REPLAY.md)
- [Determinism Contract](DETERMINISM_CONTRACT.md)
- [Controlled Change source](../synapse/change/)
