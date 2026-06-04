# RFC: Actor Definition Structural CVM Wrapper

**Status:** ACCEPTED FOR IMPLEMENTATION PLAN ONLY  
**Target:** v2.2.0-alpha3d3-rfc  
**Base:** v2.2.0-alpha3d2 + Corpus Telemetry Sprint  
**Scope:** AgentDef / SubAgentDef / PolicyDef structural wrapping  
**Non-scope:** SendStmt, ReceiveBlock, ReceivePattern, LLMCall, HabitStmt

## 1. Motivation and corpus evidence

The Alpha.3-D2-S1 corpus telemetry report identifies actor-related nodes as
the dominant fallback family:

- `AgentDef`: 29 fallbacks
- `SubAgentDef`: 10 fallbacks
- `SendStmt`: 11 fallbacks
- `ReceiveBlock`: 6 fallbacks
- `ReceivePattern`: 6 fallbacks

`HabitStmt` appears only 3 times and is not a priority blocker. Actor structure
is therefore the next data-driven target.

This RFC intentionally starts with actor definitions, not messaging. Definition
nodes create actor structure and registry metadata; messaging nodes introduce
mailbox scheduling and should be specified separately.

## 2. Goals

1. Remove `AgentDef` and `SubAgentDef` from static fallback distribution by
   compiling them as structural runtime wrappers.
2. Preserve actor-runtime ownership of actor registry, mailbox state, lifecycle
   state, scheduling, and spawn semantics.
3. Keep CVM a pure structural/computational substrate. CVM must not learn actor registry internals.
4. Preserve parity with tree-walker actor definition behavior.
5. Keep `SendStmt`, `ReceiveBlock`, and `ReceivePattern` out of this RFC.

## 3. Non-goals

This RFC does not implement or specify:

- actor message send/receive compilation;
- mailbox pattern matching;
- actor scheduler changes;
- multiple concurrent pending calls;
- LLMCall or PromptExpr compilation;
- HabitStmt or cognitive primitive compilation;
- FALLBACK_HOST;
- dynamic opcode plugins;
- hot code migration.

## 4. Primitive classification

Actor definitions are classified as **structural runtime primitives**.

They are similar to `ContextBlock` in that the CVM may enter and leave a runtime
structure, while the host/runtime remains authoritative for domain state.

The RFC introduces the conceptual host symbols:

- `SYS_ACTOR_ENTER`
- `SYS_ACTOR_EXIT`
- `SYS_SUBAGENT_ENTER`
- `SYS_SUBAGENT_EXIT`
- `SYS_POLICY_ENTER`
- `SYS_POLICY_EXIT`

These symbols are `VM_STRUCTURAL_RUNTIME` operations. They are bridge-dispatched
for host parity, but they are not capability-gated in this RFC because definition
wrapping is a structural execution boundary, not a user-initiated actor action.
Future actor actions such as `SendStmt` may require capabilities.

## 5. Structural wrapper shape

The allowed CVM shape for an actor definition is:

```text
ACTOR_ENTER(name, metadata)   -> bridge-dispatched structural event
compiled body                 -> CVM for computational subset only
ACTOR_EXIT(name)              -> bridge-dispatched structural event
```

For sub-agents:

```text
SUBAGENT_ENTER(name, metadata)
compiled body
SUBAGENT_EXIT(name)
```

For policies attached to actors:

```text
POLICY_ENTER(name, metadata)
compiled policy declarations where supported
POLICY_EXIT(name)
```

Any actor-specific node inside the body that is not supported by CVM remains a
statement-level HOST_EVAL fallback. No `FALLBACK_HOST` opcode is introduced.

## 6. VM state extension

The implementation may add:

```python
actor_stack: list[str]
```

to `VMState`, parallel to `context_stack`.

`actor_stack` must be serialized through `VMState.to_dict()` and restored through
`VMState.from_dict()`. If introduced, it must be included in `transition_hash` so
checkpoints inside actor definition wrappers remain deterministic.

## 7. CallFrame RAII snapshot

If `actor_stack` is introduced, `CallFrame` must include:

```python
actor_stack_snapshot: list[str]
```

On `CALL`, CVM captures:

```python
frame.actor_stack_snapshot = list(vm.state.actor_stack)
```

On `RETURN`, CVM unwinds any actor frames above the snapshot through bridge-side
cleanup, then restores the caller snapshot.

This mirrors `context_stack_snapshot` and prevents dangling actor definition
wrappers on early return.

## 8. Bridge/runtime parity

`SYS_ACTOR_ENTER` / `SYS_ACTOR_EXIT` must delegate to actor-runtime primitives
rather than appending ad-hoc events.

Required parity properties:

- actor runtime remains authoritative for registry state;
- event IDs are assigned through the same host event-id source as tree-walker;
- execution history shape matches tree-walker behavior for actor definition
  enter/exit events;
- current actor scope is synchronized between host runtime and `VMState.actor_stack`;
- exceptions during structural cleanup are best-effort/no-throw and must not mask
  the original VM error.

## 9. Snapshot and restore invariants

Snapshots taken inside actor definitions must preserve:

- `VMState.actor_stack`;
- `CallFrame.actor_stack_snapshot` for active frames;
- transition hash including actor-stack state;
- program hash and host ABI checks inherited from Alpha.3-D1/D2.

Restore must fail closed if actor-stack state cannot be reconciled with host
actor-runtime state.

## 10. Corpus coverage target

After implementation:

- `AgentDef` must disappear from `corpus_fallback_by_node_type`;
- `SubAgentDef` should disappear if included in the implementation scope;
- `SendStmt`, `ReceiveBlock`, and `ReceivePattern` are expected to remain until
  the messaging RFC;
- static corpus coverage should increase above the Alpha.3-D2-S1 baseline of
  `0.837069`.

The expected target is approximately `0.85+`, but the exact value must be based
on regenerated `reports/corpus_fallback_alpha3d3.json`.

## 11. Parse-error precondition

The Alpha.3-D2-S1 report contains 3 parse failures. Actor implementation may
proceed, but any implementation PR must not increase parse failures.

A separate parser-compatibility mini-sprint is recommended before beta if those
3 files represent intended syntax rather than deprecated examples.

## 12. Runtime coverage follow-up

This RFC is based on static all-AST-node telemetry. Before final implementation
acceptance, a runtime corpus audit should be added or the implementation PR must
explain why static telemetry is sufficient for this phase.

## 13. Deferred messaging RFC

The following are explicitly deferred to a separate actor messaging RFC:

- `SendStmt`
- `ReceiveBlock`
- `ReceivePattern`
- mailbox matching semantics
- actor wake/suspend semantics for message arrival
- message delivery ordering
- capability gates for actor send/receive

## 14. Acceptance checklist for implementation

An implementation PR must include:

1. `AgentDef` in the v2.2 CVM routing surface, or an explicit structural wrapper
   routing decision.
2. Bridge-dispatched structural actor enter/exit operations.
3. Optional `actor_stack` in `VMState` if actor scope is represented in VM state.
4. Optional `actor_stack_snapshot` in `CallFrame` if actor scope can be opened
   during calls.
5. Tests for normal enter/exit parity with tree-walker behavior.
6. Tests for snapshot/restore inside actor definition scope if `actor_stack` is
   introduced.
7. Tests proving `SendStmt` and `ReceiveBlock` remain deferred and do not silently
   become unsupported partial compilations.
8. Regenerated corpus report showing `AgentDef` removed or reduced.
9. No regression in Alpha.3-D1/D2 promise tests.
10. No regression in ContextBlock coverage regression guard.

## 15. Decision

Proceed with **Track 1: Structural Agent Definitions** first.

Do not implement messaging in the same patch. Messaging requires its own RFC
because it touches mailbox ordering, actor scheduling, delivery guarantees, and
capability-gated cross-actor effects.
