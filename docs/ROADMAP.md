# Current Alpha3e Checkpoint

- Code artifact version: `v2.2.0-alpha3e`
- Track A — Deterministic LLM / Prompt CVM Bridge: **closed**.
- Track B — Guard Blocks in Bytecode: **closed at CVM opcode/runtime checkpoint**.
- Current corpus report: `reports/corpus_fallback_alpha3e.json`
- Audit methodology: static AST fallbacks are split from `lowerable_to_cvm` nodes; current `runtime_only_fallbacks = 99`.
- Current tests: `484 passed, 1 skipped`.
- Next phase: generate Golden Replay Suite, then tag final `v2.2.0-alpha3e`; Time-Travel Debugger remains RFC-only until approved.

# Synapse Roadmap: Data-Driven CVM Expansion

## Current stable code baseline

- Code artifact version: `v2.2.0-alpha3e-p0`
- Runtime status: ContextBlock structural runtime primitive is compiled in CVM; Alpha.3-D2 promise resolution is implemented.
- Next decision gate: **Corpus Telemetry Sprint** before any HabitStmt or cognitive primitive RFC.

## Why the telemetry sprint exists

HabitStmt is a cognitive orchestration primitive, not a pure stack-machine construct. It touches habit registry state, affective/PAD state, energy pools, observer/activation semantics and execution history. Compiling it directly into CVM would risk turning the VM from a computational substrate into a runtime-layer god object.

Before opening a HabitStmt RFC, the project now requires a corpus-wide fallback distribution. This converts the next roadmap decision from assumption-driven to data-driven.

## Alpha.3-D2-S1: Corpus Telemetry Sprint

Artifacts added in this sprint:

- `scripts/corpus_fallback_audit.py`
- `reports/corpus_fallback_alpha3d2.json`
- `tests/test_corpus_fallback_audit.py`

The audit is static and non-invasive. It parses `.syn` files under `examples/` and `tests/`, traverses AST nodes, classifies each node with `classify_ast_node_v22()`, and aggregates CVM vs HOST_EVAL distribution.

### Important metric semantics

`reports/corpus_fallback_alpha3d2.json` uses `routing_model = "static_all_ast_nodes"`.

That means:

- It counts all parsed AST nodes, including nested nodes.
- It does not execute programs.
- It does not model branch/taken-path runtime behavior.
- It complements, but does not replace, runtime `metrics_snapshot()` coverage.

Runtime coverage still measures executed statements. Static corpus coverage measures parsed AST surface area. These metrics answer different questions.

## Current corpus audit summary

Generated report: `reports/corpus_fallback_alpha3d2.json`

Key values from the committed report:

```json
{
  "files_scanned": 44,
  "files_parse_ok": 41,
  "files_parse_failed": 3,
  "total_ast_nodes": 1160,
  "total_cvm_compilable": 971,
  "total_fallback": 189,
  "corpus_coverage_ratio": 0.837069
}
```

Top fallback blockers in the committed report:

```json
{
  "AgentDef": 29,
  "LLMCall": 14,
  "SendStmt": 11,
  "PolicyDef": 10,
  "SubAgentDef": 10,
  "AffectiveFilterExpr": 8,
  "BranchDef": 7,
  "PromptExpr": 7,
  "ReceiveBlock": 6,
  "ReceivePattern": 6,
  "PolicyRule": 5,
  "HabitStmt": 3
}
```

Parse failures are recorded in the report instead of aborting the audit. They are telemetry input for parser compatibility work, not hidden errors.

## Data-driven prioritization rule

### Option A: CVM Core Expansion

Choose this if the top blockers are computational syntax or pure data-shaping constructs, for example:

- list comprehensions
- string interpolation
- try/catch
- dict/object literal variants
- pure expression forms

Constraint: CVM remains a pure computational substrate.

### Option B: Structural Cognitive Wrapper RFC

Choose this only if cognitive orchestration nodes dominate the fallback distribution, for example:

- HabitStmt
- AffectiveEventStmt
- ResonateStmt
- DreamStmt

Constraint: CVM may compile only structural wrapper/guard portions. Runtime-layer orchestration remains in bridge/interpreter/host systems.

For HabitStmt specifically, the allowed shape is:

```text
HABIT_ENTER   -> bridge-dispatched runtime event
compiled guard/condition -> pure CVM logic
HABIT_ACTIVATE -> bridge-dispatched runtime activation
body execution -> interpreter/runtime delegation
```

CVM must not learn habit registry internals, PAD state internals, energy pool mutation rules, or observer lock semantics.

### Option C: Parallel tracks

Choose this if both computational syntax and cognitive orchestration nodes are significant blockers. Prioritize by frequency and risk:

1. Low-risk computational expansion first.
2. Cognitive primitive RFC only with structural wrapper constraints.

## Recommendation from Alpha.3-D2-S1 data

The current static corpus audit does **not** support jumping directly into full HabitStmt compilation. `HabitStmt` appears, but it is not the top corpus blocker.

The next RFC should start from the actual top blockers:

1. Decide whether `AgentDef`, `SubAgentDef`, and `BranchDef` are intended to remain runtime-only declarations or should get structural wrappers.
2. Evaluate whether `LLMCall`, `PromptExpr`, `SendStmt`, and `ReceiveBlock` belong to bridge/actor async work rather than CVM core.
3. Treat `HabitStmt` as a cognitive orchestration candidate only after the higher-frequency blockers are classified.

No HabitStmt implementation should begin until this decision is made explicitly from the report.

## Deferred tracks

These remain deferred until after the telemetry-driven decision:

- direct HabitStmt compilation
- FALLBACK_HOST opcode
- dynamic opcode plugin registry
- hot code migration
- language-level YIELD/AWAIT syntax

## Alpha.3-D3-RFC: Actor Definition Structural CVM Wrapper RFC

The Corpus Telemetry Sprint changes the next implementation priority. The
highest-frequency blocker is not `HabitStmt`; it is the actor-definition family.

The next accepted RFC is:

- `docs/RFC-ACTOR-DEF-CVM.md`

### Track selected

**Track 1: Structural Agent Definitions** is selected for detailed design before
messaging.

Rationale:

- `AgentDef` is the #1 static fallback blocker with 29 occurrences.
- `SubAgentDef` contributes 10 additional fallbacks.
- `HabitStmt` contributes only 3 fallbacks and remains deferred.
- Actor definitions can be treated as structural runtime wrappers, similar in
  spirit to `ContextBlock`, while keeping actor registry and mailbox ownership
  in `actor_runtime`.

### Track explicitly deferred

Actor messaging is not part of the actor-definition RFC. The following remain in
a separate future RFC:

- `SendStmt`
- `ReceiveBlock`
- `ReceivePattern`
- mailbox ordering
- actor wake/suspend semantics for message delivery
- actor send/receive capability gates

### Implementation gate

No actor runtime implementation should start until the RFC contract tests pass
and the implementation plan demonstrates that CVM will not learn actor registry
internals.


## v2.2.0-alpha3d3 Update

Actor definition compilation has been implemented as a structural wrapper. `AgentDef` and `SubAgentDef` move into the CVM routing surface; messaging (`SendStmt`, `ReceiveBlock`, `ReceivePattern`), LLM/prompt, policy, and HabitStmt remain separate RFC tracks.


## Alpha.3-D4 PolicyDef Structural Wrapper

D4 closes the structural policy wrapper track: PolicyDef and PolicyRule are removed from corpus fallback telemetry. The next data-driven decision remains between actor messaging, LLM/prompt bridge family, and governance enforcement semantics.


---

## Alpha3e — Active Sprint

**Baseline:** v2.2.0-alpha3e-p0 (parse stabilisation complete, all 44 examples parse OK)

### P0 / P0.1 complete
- [x] Fix 3 parse failures in examples/ (alpha3e-p0)
- [x] Corpus coverage 91.44% (44/44 files)
- [x] Version alignment across all layers
- [x] HOST_ABI_VERSION bumped with rationale
- [x] Soft keywords formalised in LANGUAGE_SPEC §A
- [x] Cognitive primitive classification table in ARCHITECTURE.md
- [x] pre-commit hook + Makefile

### Track A — Deterministic LLM / Prompt CVM Bridge
**Priority: HIGH — closes 29 fallback nodes (LLMCall=18, PromptExpr=11)**

Opcodes: `PROMPT_BUILD` / `LLM_REQUEST` / `LLM_RESUME`

Architecture:
- CVM: builds prompt value, forms deterministic request envelope, pauses (PAUSED_HOST_CALL)
- Host/Bridge: executes LLM call, writes to replay log, enforces capability gates
- Cache key: SHA-256(template_hash || variables_hash || schema_hash || engine_params_hash || model_version)
- `llm_cache_invalidation_policy`: `never` | `model_change` (default) | `policy_guard`
- CAPABILITY_DENIED writes two history events: LLM_REQUEST_DENIED + VMHostError on stack
- CI golden replays run without real tokens
- Expected HOST_ABI bump: `2.2.0-alpha3e-llm`

### Track B — Guard Blocks in Bytecode
**Priority: HIGH — closes governance technical debt from v0.6**

Opcodes: `GUARD_ENTER(policy_hash, guard_hash)` / `GUARD_EXIT(verdict)`

Architecture:
- Static guard: deterministic conditions → full CVM, fixed gas
- Dynamic guard: LLM-based → HOST call + verdict recorded in history
- Verdict enters tamper-evident execution history and transition hash
- Replay uses recorded verdict; no re-evaluation

---

## Alpha3f — Planned

### Track C — Cognitive Time-Travel Debugger
Interface to existing VMSnapshot + replay engine.
Commands: `load_snapshot(hash)`, `fork_vm_state()`, `inject_event()`, `resume_fork()`, `compare_trace()`
CLI: `synapse debug --snapshot <hash>`

### Track D — Habit Interrupt Tokens
RFC first, then implementation.
Model: cooperative preemption via `inject_interrupt(habit_id, priority)` at explicit YIELDPOINT opcodes.
No continuation cursor required.

---

## Backlog (no milestone)

- Backtick identifier escape — Track 0.2
- Runtime-only primitive compiler warning — Track A prep
- Acoustic Soulprints — separate product track
- Merkleized Soulprint — after soulprint-CVM
- quantum_superpose — only if branch interference semantics formalised
- temporal_fork as syntax — moved to debugger API (debug.fork_at(hash))
- Affective Gas Curve — research mode only (cognitive_budget overlay, alpha3f+)
