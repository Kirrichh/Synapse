# RFC: Dream Strict Layer 1 Eligibility

**Status:** DRAFT — Team Review Required; Alpha3g P0.2.3 errata applied  
**Version:** v2  
**Target milestone:** Alpha3g+ / strict replay architecture gate  
**Runtime scope authorized by this RFC:** none — doc-only eligibility verdict  
**Depends on:** `docs/RFC-DREAM-REPLAY-CONTRACT.md`, `docs/DETERMINISM_CONTRACT.md`, `docs/RFC-STABLE-CANONICAL-IDENTITY.md`, `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`  
**Blocks:** admitting `DreamBlock` to Strict Layer 1 golden replay fixtures

This RFC defines whether `DreamBlock` can be admitted to Strict Layer 1 after
Alpha3g Dream Replay. It does **not** authorize runtime changes. It records the
architecture verdict and the acceptance criteria for a future implementation
model that could make dream strict-golden-eligible.

P0.2.3 errata clarifies builtin resolution, forbidden builtins, future replay
model options, strict `dream_completed` invariants, and shared canonicalization
hooks required by Dream, Integrate, and Stable Canonical Identity.

---

## 1. Decision summary

`DreamBlock` is **not Strict Layer 1 eligible under the current Alpha3g A2 replay
model**.

Alpha3g correctly moved `DreamBlock` from the old Category C state to Category B:
replay consumes `dream_completed`, verifies `dream_key` and `result_hash`, and
returns the recorded result. This closes the old recompute-drift gap.

However, A2 replay still executes the dream body before consuming
`dream_completed` so the linear history cursor can consume nested events in
order. That body re-execution can produce observable host effects. Therefore the
runtime cannot claim strict observational identity for dream replay.

The correct verdict is:

```text
Not in A2.
Possible later through a different replay model: consume-only, state-delta,
recorded subtrace, or a hybrid of state-delta plus recorded subtrace.
```

This RFC explicitly rejects the stronger claim that `DreamBlock` is impossible
forever. It is blocked by the current A2 replay model, not by the concept of
dream replay itself.

---

## 2. Background: what Alpha3g already fixed

Alpha3g implemented Path A / A2 from `RFC-DREAM-REPLAY-CONTRACT`.

Verified against `synapse/interpreter.py:1328-1392`:

- LIVE records `dream_completed` with:
  - `dream_key`;
  - `result`;
  - `result_hash`;
  - `nested_event_policy="execute_and_verify"`.
- REPLAY executes the body for cursor synchronization, then consumes
  `next_history_event("dream_completed")` at `interpreter.py:1357`.
- REPLAY verifies:
  - the event exists (`interpreter.py:1358-1359`);
  - `dream_key` matches (`interpreter.py:1360-1361`);
  - recorded result re-hashes to the recorded `result_hash`
    (`interpreter.py:1362-1366`);
  - freshly computed result hash matches the recorded result hash
    (`interpreter.py:1367-1368`);
  - `nested_event_policy == "execute_and_verify"`
    (`interpreter.py:1369-1370`).
- The returned value is `recorded_result`, not the freshly computed value
  (`interpreter.py:1371`).

This is sufficient for Category B: replay-safe recorded nondeterminism with
result-hash verification.

It is not sufficient for Strict Layer 1.

---

## 3. Why A2 blocks Strict Layer 1

Strict Layer 1 must be default-deny. A construct may enter only when replay is
observationally identical with respect to canonical user-visible behavior and
host-visible effects allowed by the strict fixture contract.

A2 replay has this shape:

```text
REPLAY dream:
  execute dream body
  consume dream_completed
  verify dream_key/result_hash
  return recorded result
```

The computed value is discarded, but the body execution is real. It re-enters the
normal evaluator and builtin dispatch path. Therefore A2 is not a consume-only
verification model.

The phrase "body re-execution" must not be reduced to harmless CPU work. In the
current interpreter, body re-execution can reach host effects and
nondeterministic host values.

---

## 4. Concrete observable side-effect: `print` inside dream

The simplest audited example is `print(...)` inside a dream body.

Important correction: `print` inside dream does **not** normally flow through
`Interpreter._print()` / `output_buffer`.

The actual current path is:

1. `Interpreter.__init__()` defines global `print` as `self._print`
   (`interpreter.py:550-551`).
2. `eval_call()` first attempts to resolve the callable through
   `env.get(fn_name)`, before the fallback builtin dispatch.
3. Inside dream, `env` is a `DreamSandboxEnvironment`. Its `get("print")` reads
   from the parent environment (`interpreter.py:351`).
4. The parent value is a callable runtime handle, not a supported container and
   not a supported immutable. `_is_supported_immutable()` only allows
   `None`, `str`, `int`, `float`, and `bool` (`interpreter.py:297-299`).
5. The sandbox raises `DreamSandboxIsolationError`
   (`interpreter.py:360-364`).
6. `eval_call()` catches local `RuntimeError` broadly and swallows it
   (`interpreter.py:3773-3786`). `DreamSandboxIsolationError` is a
   `DreamIsolationViolation`, and `DreamIsolationViolation` subclasses
   `RuntimeError` (`interpreter.py:47-52`).
7. After swallowing the isolation error, `eval_call()` falls back to
   `BUILTINS["print"]` (`interpreter.py:3788-3790`).
8. `BUILTINS["print"]` is Python host `print(*args)`
   (`builtins.py:231-232`).

Therefore a `print` inside a dream body can write to host stdout during LIVE and
again during A2 REPLAY.

The critical failure mode is not merely that builtins exist. It is that the
sandbox detects an isolation violation, then `eval_call()` masks that violation
and proceeds to a host builtin. This is fail-open behavior at the dream boundary.

This is an observable host side-effect caused by replay-time body execution. It
is enough to block Strict Layer 1 under A2.

---

## 5. Builtin leakage risk inside dream

The `print` case is not just an isolated UX problem. It demonstrates that the
sandbox boundary and builtin fallback currently interact in a way that re-enters
host builtins from inside dream.

Current `BUILTINS` include several names that are unsafe in strict dream replay:

| Builtin | Strict eligibility risk |
|---|---|
| `print` | Host stdout side-effect during replay. |
| `time` | Nondeterministic wall-clock value and timing side channel. |
| `random` | Nondeterministic value and RNG-state mutation. |
| `uuid` | Nondeterministic identity generation. |

For `time`, `random`, and `uuid`, the practical path is also fail-open:
lookup reaches a callable builtin through parent `Environment.get()`, the dream
sandbox rejects it as an unsupported callable, `eval_call()` swallows the local
`RuntimeError`, and then builtin fallback invokes the host builtin directly.

If such values flow into the dream result, Alpha3g `result_hash` can detect a
mismatch. That is Category B integrity checking, not strict replay identity. If
the builtin produces host-visible effects or mutates host/runtime state while
the eventual recorded result still verifies, A2 still re-executed an observable
operation that strict replay should not perform.

The eligibility model must therefore treat builtin dispatch as part of the dream
side-effect surface, not as a harmless expression path.

### 5.1 Mandatory builtin classification for strict dreams

Future strict dream eligibility requires an explicit builtin registry with at
least these classes:

| Class | Rule |
|---|---|
| `pure_deterministic` | May be allowed if arguments and return values use canonical serialization. |
| `side_effects=True` | Forbidden in strict dream replay unless recorded-and-consumed by an approved effect contract. |
| `deterministic=False` | Forbidden in strict dream replay unless recorded-and-consumed by an approved nondeterminism contract. |
| `runtime_handle=True` | Forbidden unless represented by a canonical handle contract. |

The following names are explicitly forbidden in strict dream bodies until a
future approved contract states otherwise:

```text
print, time, random, uuid
```

A strict dream implementation must not allow fallback from sandbox isolation
failure to host builtins.

---

## 6. Closure and function isolation audit

Current code blocks parent-scope `.syn` functions inside dream, but the block is
incidental rather than an explicit contract.

Facts:

- `DreamSandboxEnvironment.get()` allows only cloned containers and supported
  immutables from the parent (`interpreter.py:351-364`).
- `_is_supported_immutable()` permits only `None`, `str`, `int`, `float`, and
  `bool` (`interpreter.py:297-299`).
- A `.syn` function value (`FnDef`) is neither a supported container nor a
  supported immutable, so the sandbox raises `DreamSandboxIsolationError`.
- `eval_call()` currently swallows that error through `except RuntimeError: pass`
  (`interpreter.py:3785-3786`), causing misleading fallback behavior.

Conclusion:

- There is no known closure-mutation leak today for parent-scope `.syn`
  functions inside dream.
- The protection is not yet an explicit eligibility contract.
- Future widening of `_is_supported_immutable()` or callable handling could open
  a leak unless the contract is tested and fail-closed.

Strict eligibility requires function/closure behavior to be defined in one of
these ways:

1. functions are forbidden inside strict dreams with a first-class isolation
   diagnostic; or
2. functions are allowed only through canonical serialization and a restricted
   side-effect-free call model; or
3. the future consume-only/subtrace model records and verifies any function
   execution effects without re-entering ambient runtime handles.

This problem overlaps with integrate write-set canonicalization. Function values
must not receive one canonicalization rule in dream and a different rule in
integrate.

---

## 7. Nested-event origin requirement

Alpha3g A2 executes the dream body in replay to preserve the linear cursor for
nested events such as replayed `llm_call` records. That solves the immediate
cursor desynchronization problem, but it does not prove strict nested-event
origin.

Strict eligibility requires this invariant:

```text
Every nested event attributed to a dream must originate from that dream body and
its deterministic lexical context, not from an external async trigger, ambient
runtime queue, host callback, or unrelated actor activity.
```

Until this is proven, a replay cursor match is not sufficient. The event may be
in the expected order while still having the wrong origin.

A future subtrace model should bind each nested event to:

- dream identity / `dream_key`;
- body hash or bytecode hash;
- lexical capture hash;
- parent history hash at dream entry;
- local ordinal within the dream subtrace;
- canonical event type and payload hash.

---

## 8. Rejected path: "acceptable trace" under A2

This RFC rejects attempts to admit `DreamBlock` to Strict Layer 1 by defining a
looser "acceptable trace" for A2 body re-execution.

Reason: Strict Layer 1 must not become a fuzzy category. If replay may re-execute
a body that performs host-visible effects, generates nondeterministic values, or
mutates ambient runtime state, then the construct is not strict-golden-safe. It
may remain Category B if the recorded result is verified, but it must not be
classified as Strict Layer 1.

Therefore:

```text
No compromise on observable body re-execution.
No Strict Layer 1 admission under A2.
```

---

## 9. Future replay models

`DreamBlock` can become Strict Layer 1 eligible only after a future replay model
removes replay-time body execution from canonical verification.

The design family must be selected by a future approved RFC. This RFC defines
the option space and acceptance pressure, not the implementation.

| Model | REPLAY behavior | Advantages | Architectural risks |
|---|---|---|---|
| Consume-only | Consume `dream_completed` and return the recorded result without executing the body. | Maximum strict isolation; no replay-time host trace. | Loses nested-event cursor/detail unless nested events are represented elsewhere. |
| State Delta | Apply or verify a recorded canonical state diff produced by LIVE dream execution. | Handles stateful effects without body re-execution. | Requires a precise canonical diff schema, aliasing rules, rollback rules, and value serialization. |
| Recorded Subtrace | Store nested dream events as a scoped subtrace and validate them structurally without executing the body. | Preserves forensic detail for nested calls. | Requires strict origin-binding, local ordinals, subtrace hashes, and tamper detection. |
| Hybrid | Combine recorded state delta with recorded subtrace. | Most complete representation for state and nested events. | Highest complexity; requires both delta and subtrace appliers to be deterministic and idempotent. |

### 9.1 Consume-only `dream_completed`

Replay consumes `dream_completed` directly and returns the recorded result
without executing the body.

This requires solving nested events because the current linear history may have
nested events before `dream_completed`.

### 9.2 Recorded dream subtrace

LIVE records a nested subtrace under the dream event. REPLAY consumes the dream
event and validates the subtrace structurally without executing the body.

Possible shape:

```json
{
  "type": "dream_completed",
  "dream_key": { "...": "..." },
  "result": "...",
  "result_hash": "...",
  "subtrace_hash": "...",
  "subtrace": [
    { "type": "llm_call", "local_ordinal": 0, "payload_hash": "..." }
  ]
}
```

### 9.3 State-delta replay

LIVE records the canonical state delta produced by dream execution. REPLAY
applies or verifies the recorded delta without running the body.

The state-delta model must define:

- write-set schema;
- forbidden host effects;
- canonical serialization;
- function/value identity rules;
- rollback behavior;
- subtrace linkage for nested events.

---

## 10. Strict `dream_completed` invariants

A future strict model must harden `dream_completed` beyond the current Category B
schema. The following invariants are required before strict admission:

| Field | Strict invariant |
|---|---|
| `dream_key` | MUST be a deterministic function of scenario/config hash, body AST or bytecode hash, canonical bound-variable hash, parent history hash at entry, runtime/spec version, and schema version. |
| `result_hash` | MUST be computed over canonical result serialization, not Python object identity or implementation-specific formatting. |
| `nested_event_policy` | MUST be a frozen enum, not an arbitrary string. Allowed values must be versioned. |
| `subtrace_hash` | REQUIRED for any model that preserves nested events without body re-execution. It must cover local ordinals, event types, payload hashes, and origin binding. |
| `state_delta_hash` | REQUIRED for any model that applies recorded state changes. It must cover namespace, path encoding, operation type, old/new hashes, and schema version. |
| `effect_barrier_hash` | REQUIRED if the strict model records any allowed external effects. Forbidden effects must fail before commit. |

These invariants are intentionally shared with Integrate and Stable Canonical
Identity. They must not be redefined independently per feature.

---

## 11. Acceptance criteria for future Strict Layer 1 eligibility

Before `DreamBlock` may be admitted to Strict Layer 1, an approved implementation
RFC and runtime patch must satisfy all criteria below.

### A. No replay-time body execution

Strict replay must not execute the dream body. It must consume and verify a
recorded event, subtrace, and/or state delta.

### B. No host-visible effects during replay

Replay must not emit stdout, mutate `output_buffer`, touch wall-clock time,
advance RNG state, generate UUIDs, call providers, send messages, mutate memory,
or perform any other host/runtime side-effect from a dream body.

### C. Builtin boundary closed

Builtin dispatch inside strict dreams must be explicitly classified:

- allowed deterministic pure builtins;
- forbidden side-effectful builtins;
- forbidden nondeterministic builtins;
- recorded-and-consumed effects, if any.

Fallback from sandbox isolation failure to host builtins must be impossible in
strict dream mode.

### D. Closure/function contract explicit

Parent-scope functions, runtime handles, agents, host callables, and custom
objects must either be forbidden with first-class diagnostics or represented by a
canonical, side-effect-free serialization contract.

### E. Nested-event origin proven

Nested dream events must be scoped to the dream identity and verified by local
ordinal/subtrace binding, not merely consumed by a global linear cursor.

### F. Golden fixtures updated

Strict Layer 1 fixtures may include dream only after new golden artifacts prove:

- consume-only or subtrace replay equality;
- forbidden `print`/`time`/`random`/`uuid` behavior;
- closure/function isolation behavior;
- nested LLM subtrace behavior;
- tamper detection for `dream_key`, `result_hash`, and subtrace/state-delta hash.

---

## 12. Shared canonicalization hooks

Dream strict eligibility must share canonicalization contracts with
`RFC-INTEGRATE-REPLAY-APPLIER` and `RFC-STABLE-CANONICAL-IDENTITY`. The same
value must not be serializable in one RFC and forbidden or differently hashed in
another.

The shared hooks are:

| Hook | Required shared contract |
|---|---|
| Function serialization | `.syn` functions, closures, callbacks, and handlers require one canonical representation or one explicit forbidden diagnostic across dream and integrate. Native/host builtins are forbidden as values unless whitelisted by an approved pure-deterministic contract. |
| Canonical time | Wall-clock time must be either forbidden inside strict effects or recorded-and-consumed through a versioned event. Raw host `time()` is never strict-safe. |
| Nondeterminism barrier | `random`, `uuid`, provider calls, actor scheduling, and runtime queues require a static + dynamic barrier or recorded consumption model. |
| State delta hashing | Any future dream delta and integrate write-set must share namespace/path encoding, value hashing, op names, and schema-version applier rules. |
| Nested event origin | Dream subtraces and integrate nested-event prohibitions must use compatible origin-binding vocabulary. |
| Canonical genesis state | Any state-delta replay model must use the same empty-state hash and session genesis rules as Stable Canonical Identity. |

These hooks are normative dependencies for future RFCs. They do not authorize code
changes in this patch.

---

## 13. Current classification

As of this RFC:

| Construct | Classification | Reason |
|---|---|---|
| Legacy pre-Alpha3g `DreamBlock` | Category C | `dream_completed` lacks strict schema and replay consumption. |
| Alpha3g `DreamBlock` under A2 | Category B | Recorded result is consumed and hash-verified, but body executes during replay. |
| Future consume-only/subtrace/state-delta dream | Candidate for Strict Layer 1 | Only after this RFC's acceptance criteria are implemented and tested. |

---

## 14. Non-goals

This RFC does not:

- change `evaluate_dream()`;
- change `DreamSandboxEnvironment`;
- change `eval_call()`;
- change `BUILTINS`;
- add CVM opcodes;
- add tests;
- admit dream to Strict Layer 1;
- modify existing golden artifacts;
- approve `RFC-INTEGRATE-REPLAY-APPLIER`;
- approve `RFC-STABLE-CANONICAL-IDENTITY`.

---

## 15. Implementation backlog created by this RFC

This RFC creates the following backlog items:

1. `RFC-STATE-DELTA-FOR-DREAM` or equivalent consume-only/subtrace replay RFC.
2. Runtime patch to prevent swallowed `DreamSandboxIsolationError` from falling
   through to host builtins in dream contexts.
3. Runtime/test patch to classify dream-safe and dream-forbidden builtins.
4. Closure/function canonicalization contract shared with integrate replay.
5. Nested-event origin/subtrace binding design.
6. Strict `dream_completed` schema revision with frozen enums and subtrace /
   state-delta hashes.
7. Shared canonicalization hooks to be consumed by the Integrate structured
   review.
8. New strict golden fixtures only after the future replay model lands.

Until those items are complete, `DreamBlock` remains Category B and excluded from
Strict Layer 1.
