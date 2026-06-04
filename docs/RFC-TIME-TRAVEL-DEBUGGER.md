# RFC: Cognitive Time-Travel Debugger

Status: APPROVED — Implementation Allowed within Approved Scope.

Implementation gate: see `docs/ALPHA3F_PLANNING_GATE.md`.

This document is the approved Alpha3f contract for Time-Travel Debugger implementation.
Implementation is allowed only within the scope recorded in
`docs/ALPHA3F_PLANNING_GATE.md`. Work outside that approved scope still requires
a separate RFC and planning gate.

## Goals

- Load and verify Alpha3e Golden Replay artifacts.
- Address replay points by `history_hash` / transition hash.
- Fork execution from verified history without mutating canonical history.
- Inject deterministic events only at explicit, policy-governed safe points.
- Compare traces and identify divergence points.
- Preserve Golden Replay determinism, capability boundaries, and guard semantics.

## Non-goals

- No automatic live fallback during deterministic replay.
- No debugger ability to mint capabilities, bypass guards, or rewrite history.
- No user-visible `.syn` syntax changes.
- No `DISCARD_FORK` opcode or VM bytecode-level fork lifecycle primitive.
- No Time-Travel Debugger implementation until this RFC is approved.

## 1. Replay Modes

The debugger supports two conceptually distinct modes.

### 1.1 Deterministic replay mode

`deterministic-replay` is the default mode for replay and forked replay.

Rules:

- The source of truth is the recorded replay artifact: program hash, host ABI
  version, execution history, VM snapshot, stable state fields, and embedded
  mocks.
- External providers, wall-clock time, random sources, live actor mailboxes, and
  live host effects are not consulted.
- A missing recorded host event or missing embedded mock entry is a deterministic
  failure.
- The debugger must not silently continue in live mode.

Failure class:

```text
DeterministicReplayError
```

### 1.2 Exploratory live fork mode

`exploratory-live` is an explicit opt-in debugger mode for creating a new,
non-canonical fork lineage from a verified replay point.

Rules:

- It must be selected explicitly by the user or orchestrator.
- It creates a new `fork_id` and an isolated fork-local history suffix.
- It must record a mode transition event in the fork-local suffix.
- It is not a Golden Replay artifact and cannot update Golden Replay baselines.
- It remains subject to capability and governance enforcement.

The debugger must never automatically enter exploratory live mode from
`deterministic-replay`.

## 2. Host-Call Replay Policy

Host-call replay is strict.

### 2.1 Missing event policy

If replay reaches a host-call boundary and the matching resolved event is absent
from the replay artifact, replay fails with:

```text
DeterministicReplayError: missing recorded host event
```

This applies to, at minimum:

- `LLM_REQUEST`
- `SYS_STDOUT`
- `SYS_MEMORY_WRITE`
- actor message send/receive effects
- time/random-like host sources
- provider-backed policy checks

### 2.2 LLM replay policy

LLM calls in deterministic replay must be served from embedded content-addressed
cache entries. Missing cache entries are fatal deterministic errors.

Forbidden:

- calling an external LLM provider during replay;
- regenerating an LLM response from a model;
- silently accepting a different `model_version`;
- falling back to live provider dispatch.

### 2.3 Time and randomness policy

Replay must not read host wall-clock time or process-local random state.
Time/random-like values must come from the execution history or deterministic
virtual-clock contract established by Golden Replay.

## 3. Fork Identity

Every fork is identified as a first-class debug lineage object.

Required fields:

```json
{
  "fork_id": "...",
  "parent_fork_id": null,
  "parent_history_hash": "...",
  "created_at_event_id": "...",
  "mode": "deterministic-replay",
  "status": "active"
}
```

### 3.1 Fork-local suffix

Canonical history remains append-only and immutable. A fork may share an immutable
history prefix, but all new events are written to a fork-local suffix tagged by
`fork_id`.

### 3.2 Fork modes

Allowed values:

```text
deterministic-replay
exploratory-live
```

`exploratory-live` lineages are non-canonical and cannot update Golden Replay
artifacts.

## 4. Copy-on-Write State Diffing

Forking must not deep-copy full VM state at every step. The debugger uses overlay
state with write barriers.

### 4.1 Memory overlay

Memory is represented as:

```text
fork-local overlay -> parent memory view
```

Rules:

- Reads first consult the fork-local overlay and then the parent view.
- Writes always materialize into the fork-local overlay.
- Nested mutable values must not be shared and mutated directly.
- A write barrier must copy, freeze, or content-address nested mutable values
  before mutation.

A naive shallow `ChainMap` is insufficient unless nested mutability is blocked or
protected by a write barrier.

### 4.2 Locals overlay

Locals are overlaid per call frame, not as a single global dictionary.

Required model:

```text
frame_id -> locals_overlay
```

Reads cascade from current frame overlay to parent frame locals. Writes affect the
current frame overlay only.

### 4.3 Stack model

The operand stack is fork-local. A fork may shallow-copy stack value references,
but push/pop structure belongs to the fork.

Large values on the stack should be immutable, frozen, or content-addressed.

### 4.4 Guard stack model

`GuardFrame` values are frozen. The guard stack may therefore use a persistent
linked-stack representation:

```text
head -> parent -> parent ...
```

Forking shares the current head pointer. `GUARD_ENTER` creates a new frozen node;
`GUARD_EXIT` advances the head to the parent.

### 4.5 Context and policy stacks

Context and policy frames may use the persistent-stack model only if their frames
are immutable. Otherwise they must be copied on fork or protected by a write
barrier.

### 4.6 Mailbox view

Mailbox replay uses recorded mailbox order as the source of truth.

A forked mailbox view consists of:

```text
recorded mailbox order + fork-local injected message overlay
```

Live mailbox polling is forbidden in deterministic replay.

## 5. Fork Lifecycle and Garbage Collection

Fork lifecycle belongs to debugger runtime, not VM bytecode.

### 5.1 Disposal API

The lifecycle command is debugger-level:

```text
debug.dispose(fork_id)
```

A VM opcode such as `DISCARD_FORK` is forbidden for Alpha3f debugger design.
Program bytecode must not manage debugger fork memory.

### 5.2 Status values

```text
active
disposed
completed
failed
```

### 5.3 Cleanup rules

- Explicit `debug.dispose(fork_id)` releases fork overlays.
- Closing the debug session releases all active forks owned by the session.
- Completed forks may be released at debug-session boundaries.
- Timeout-based garbage collection is forbidden as the default because it is
  nondeterministic.
- Orphan forks without a live debug-session reference are cleaned up at session
  close.

### 5.4 Resource limits

Optional limits may be configured:

```text
max_active_forks
max_overlay_bytes
```

Exceeding limits raises a deterministic debugger error:

```text
ForkResourceLimitError
```

The runtime must not silently evict forks by heuristic timeout.

## 6. Event Injection Policy

Event injection is allowed only in fork-local debug lineages and only at explicit
safe points. It must not mutate canonical history.

### 6.1 Required injected-event metadata

Injected events must carry:

```json
{
  "fork_id": "...",
  "parent_history_hash": "...",
  "injection_index": 0,
  "payload_hash": "..."
}
```

### 6.2 Allowed injection types

Allowed, subject to capability and governance checks:

- synthetic actor message;
- synthetic affective event;
- synthetic external read result, if the read is modeled as a host event;
- synthetic cached LLM response in an explicit exploratory fork;
- new `GUARD_ENTER` evaluation path with a new `guard_hash` in fork-local
  lineage.

### 6.3 Forbidden injection types

Forbidden:

- direct history-hash rewrite;
- direct program-hash rewrite;
- direct host ABI version rewrite;
- direct capability grant;
- direct policy bypass;
- direct `GUARD_VIOLATION_ACK` insertion;
- direct `GUARD_VERDICT_OVERRIDE` or synthetic `GUARD_PASS` for a guard that
  actually failed in the recorded history.

### 6.4 Guard verdict override distinction

A debugger must distinguish between:

```text
forbidden override:
  recorded guard failed -> debugger injects synthetic PASS

allowed new evaluation path:
  fork creates a new guard evaluation with a new guard_hash and fork-local suffix
```

The second case is permitted only as a new fork lineage and must preserve normal
capability/governance checks.

### 6.5 New Guard Evaluation Path (Fork-Local Only)

A user may create a new guard evaluation path only through explicit fork-local
event injection:

```text
debug inject-event --type GUARD_ENTER --guard_hash <new_hash>
```

Semantics:

- The injected `GUARD_ENTER` belongs only to the current `fork_id`.
- The fork must already have a fixed `parent_history_hash`.
- The CVM evaluates the guard body in fork-local context.
- The resulting verdict is appended only to the fork-local history suffix.
- Parent history, canonical history, and Golden Replay artifacts are not modified.
- This is not `GUARD_VERDICT_OVERRIDE` and not a governance bypass.

Explicitly forbidden:

- `GUARD_VERDICT_OVERRIDE` injection;
- `GUARD_VIOLATION_ACK` injection;
- capability grant injection;
- `program_hash` rewrite;
- `history_hash` rewrite.

## 7. Golden Replay Interaction

Golden Replay artifacts are immutable release baselines.

### 7.1 Immutable baseline

Layer 1 strict Golden Replay artifacts must not be modified by debugger sessions.
They may be read as a verified starting point.

### 7.2 Forking from golden

A Golden Replay artifact is a read-only baseline. A fork from a Golden Replay
artifact creates a new debug session lineage:

```text
parent_history_hash = golden_artifact.final_history_hash
```

The resulting fork is not itself a Golden Replay artifact. Any injected events,
deterministic replay suffixes, or explicit exploratory-live execution are written
to a separate non-canonical debug lineage. Layer 1 strict Golden Replay artifacts
remain unchanged and are not affected by debug forks.

### 7.3 Strict replay rule

Strict Golden Replay validation cannot use exploratory-live mode. Missing cache
entries, missing host-call events, or trace drift are deterministic failures.

### 7.4 Updating golden artifacts

Golden artifacts may be regenerated only through a maintainer-approved workflow.
Debugger fork output must never overwrite Golden Replay baselines.

## 8. Security and Governance Boundary

The debugger is an observability and controlled-forking tool. It is not a
capability minting mechanism.

Forbidden:

- minting new capabilities;
- bypassing capability checks;
- bypassing `guard_violation_active`;
- rewriting `program_hash`;
- rewriting `history_hash`;
- injecting `GUARD_VIOLATION_ACK` directly;
- converting a failed guard verdict to pass;
- hiding side-effecting host calls from replay history.

All debug injection and fork execution must preserve existing capability and
governance checks.

## Implementation Gate Checklist

Implementation is allowed for the approved scope because the following gate items are true:

- [x] RFC status is `APPROVED`.
- [x] Copy-on-Write write-barrier contract is reviewed.
- [x] Event injection matrix is approved.
- [ ] Fork lifecycle and GC policy are approved.
- [x] Golden Replay Layer 1 interaction is approved.
- [x] Security boundary is approved.
- [x] `docs/ALPHA3F_PLANNING_GATE.md` is updated to allow implementation.

Implementation remains limited to the scope approved in `docs/ALPHA3F_PLANNING_GATE.md`. Any work outside that scope remains blocked until a separate RFC/gate approval.
