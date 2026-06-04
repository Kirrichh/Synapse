# RFC: Integrate Replay Applier

**Status:** APPROVED — Alpha3g P0.2.7 team verification gate  
**Version:** v1.2 approved — Alpha3g P0.2.7  
**Target milestone:** Alpha3g / P0.2b  
**Scope:** replay-safe transaction semantics for `integrate`  
**Runtime scope authorized:** future implementation patches may begin only within this approved RFC scope; this patch remains documentation-only  
**Depends on:** `docs/RFC-PROCESS.md`, `docs/RFC-STABLE-CANONICAL-IDENTITY.md`, `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` v2, `docs/RFC-DREAM-REPLAY-CONTRACT.md`, `docs/DETERMINISM_CONTRACT.md`

This RFC defines the replay contract for `integrate` blocks.

The central rule is:

```text
LIVE executes integrate in an isolated overlay and records a transaction event.
REPLAY does not execute the integrate body.
REPLAY consumes the recorded event and applies or verifies it.
```

This document is doc-only. It does not authorize runtime changes to
`evaluate_integrate()`.

---

## 1. Motivation and goals

`integrate` is a transactional cognitive primitive. Unlike `dream`, it is
intended to merge changes into durable state. Re-executing an `integrate` body in
REPLAY would repeat side effects, recompute state, and risk cursor drift.

Goals:

- make `integrate` replay-safe without body re-execution;
- record committed state deltas in a forensic-grade transaction event;
- record aborted transactions as forensic markers without applying partial
  overlay state;
- prevent nested events and nondeterminism inside transactions;
- use a path-aware schema that can evolve beyond top-level deltas later.

---

## 2. Current integrate behavior and replay gap

Current `integrate` behavior is tree-walker based and not yet backed by a replay
applier contract. The implementation can snapshot and restore state, but it does
not yet define a stable event schema for transaction replay.

Replay gap:

```text
LIVE: body executes and may change state.
REPLAY: without a replay applier, body re-execution would recompute changes.
```

For `integrate`, body re-execution is unacceptable because transactions are
state mutations. Replay must apply a recorded delta.

---

## 3. Transaction event model: committed vs aborted

Two event families are defined:

```text
integrate_committed -> contains an applyable write_set.
integrate_aborted   -> contains forensic metadata only.
```

`integrate_committed` is a state-transition event. REPLAY applies its `write_set`
after verification.

`integrate_aborted` is an audit event. REPLAY consumes/verifies the abort fact
but MUST NOT apply `overlay_summary`.

---

## 4. Path format and canonical key encoding

All paths MUST begin with an explicit namespace prefix and MUST be constructed
from canonical path segments. Bare paths are invalid.

P0.2.6 resolves INT-02 by separating two concerns that were previously conflated:

1. **Namespace path syntax** — the RFC 6901 / JSON-Pointer-like structure used by
   the replay applier.
2. **Runtime key canonicalization** — the deterministic encoding applied to user
   memory keys before they become path segments.

### 4.1 Namespace path syntax

Alpha3g v1 paths use this shape:

```text
/env/<canonical_env_name>
/memory/<canonical_memory_key>
```

The namespace segment is not user data. It MUST be one of the approved namespace
prefixes in §6. Unknown namespace prefixes are invalid.

After namespace selection, each user-data path segment MUST be valid Unicode
scalar text, normalized to NFC, and then encoded according to the namespace's
canonical segment encoder.

RFC 6901 escaping remains valid for JSON-Pointer-compatible structural escaping:

```text
"~" -> "~0"
"/" -> "~1"
```

However, RFC 6901 escaping alone is not sufficient for arbitrary memory keys.
Memory keys MUST follow §4.2 before being placed under `/memory/`.

### 4.2 Memory Key Canonical Encoding — resolves INT-02

Memory keys may be arbitrary user strings. They can include empty strings,
Unicode combining sequences, strings that look like JSON Pointer escapes, or
control characters. The replay applier MUST NOT depend on platform-specific
string normalization, Python `repr()`, or ambiguous unescaping.

Before constructing a `/memory/<key>` path, the implementation MUST apply:

```text
canonical_key_encode(key):
  1. Validate key as a Unicode scalar sequence.
  2. Reject invalid Unicode and lone-surrogate input.
  3. Normalize to NFC.
  4. Encode the normalized key to UTF-8 bytes.
  5. Emit characters in [A-Za-z0-9_.-] literally.
  6. Percent-encode every other byte as uppercase %XX.
```

Examples:

```text
memory key: session_token -> /memory/session_token
memory key: a/b           -> /memory/a%2Fb
memory key: a~b           -> /memory/a%7Eb
memory key: ""            -> /memory/
memory key: "~1"          -> /memory/%7E1
memory key: "é"           -> /memory/%C3%A9  (after NFC)
```

The percent sign itself is outside the literal allowlist and therefore encoded
as `%25`. This prevents double-decoding ambiguity.

The empty memory key is valid and represented exactly as `/memory/`. A parser
MUST distinguish `/memory/` from the namespace prefix `/memory` with no key;
`/memory` without the trailing slash is invalid as a write-set path.

### 4.3 Environment names

Environment binding names are language identifiers in normal `.syn` source, but
replay artifacts MUST still treat path construction canonically. `/env/<name>`
uses the same validation and NFC normalization rules as §4.1. If a future syntax
allows non-identifier environment keys, that future RFC MUST define an explicit
encoder instead of inheriting the memory-key encoder by accident.

### 4.4 Parser invariants

Path parsers MUST:

- validate the namespace prefix before decoding user data;
- reject bare paths and unknown namespaces;
- reject invalid percent escapes;
- reject decoded values that fail canonical Unicode validation;
- reject paths that have multiple encodings for the same logical key;
- compare paths only after canonical encoding, never after ad-hoc unescaping.

---

## 5. Write set granularity: top-level v1, path-aware schema

Alpha3g v1 uses **top-level path granularity**.

Valid v1 operations affect whole top-level bindings:

```text
/env/<name>
/memory/<key>
```

Any mutation to a nested value marks the owning top-level binding as dirty. The
resulting `write_set` entry replaces the full top-level value.

The schema is path-aware and JSON-Pointer-like, but fine-grained JSON Pointer or
JSON Patch deltas are explicitly out of scope for v1.

`write_set` MUST be sorted lexicographically by `path` after canonical escaping.

`write_set` MUST NOT contain duplicate paths. Duplicate paths detected during
LIVE commit are an `IntegrateCommitError`; the transaction aborts and an
`integrate_aborted` event is recorded.

`write_set: []` is valid and required for successful no-op integrate execution.
It proves that the transaction executed and changed no state.

---

## 6. Supported namespaces

Alpha3g v1 supports only:

```text
/env/*
/memory/*
```

Examples:

```text
/env/user
/memory/session_token
/memory/a%2Fb
```

Namespace prefix rules are strict:

```text
/env/<name>       -> environment binding
/memory/<key>     -> memory slot after canonical_key_encode(key)
/agent/<id>/...   -> invalid in v1; future RFC only
<bare path>       -> invalid
```

`/env/memory` is always an environment binding named `memory`. It is never the
memory namespace. `/memory/env` is always a memory key named `env`. The first
path segment alone defines the namespace.

Unknown namespace prefixes are invalid. Path parsers MUST reject paths that omit
a namespace prefix or that rely on contextual interpretation.

Future namespaces such as `/agent/*`, `/actor/*`, `/storage/*`, or `/log/*`
require future RFCs.

Persistent indexed storage backends are out of scope for v1. Integrate over
SQLite, PostgreSQL, Redis, vector indexes, inverted indexes, or external storage
is forbidden until a `StorageOverlay` / `IndexOverlay` transaction API is
approved.

---

## 7. Event schema: `integrate_committed`

```json
{
  "type": "integrate_committed",
  "schema_version": "alpha3g.integrate.v1",
  "pre_state_hash": "sha256(...)" ,
  "post_state_hash": "sha256(...)" ,
  "write_set": [
    {
      "path": "/env/user",
      "granularity": "top_level",
      "op": "replace",
      "old_value_hash": "sha256(...)" ,
      "new_value": { "name": "Ada" },
      "new_value_hash": "sha256(...)"
    },
    {
      "path": "/env/temp_buffer",
      "granularity": "top_level",
      "op": "delete",
      "old_value_hash": "sha256(...)" ,
      "new_value": null,
      "new_value_hash": null
    }
  ],
  "nondeterminism_barrier_violated": false
}
```

Valid v1 operations:

```text
replace
delete
```

For `replace`, `new_value` and `new_value_hash` are mandatory.

For `delete`, `old_value_hash` is mandatory and `new_value_hash` MUST be null.
`new_value` MUST be null or omitted by implementation policy. `delete` means the
binding is absent, not that it has JSON value `null`.

`schema_version` is mandatory.

`new_value` MUST be canonical-serializable under
`RFC-STABLE-CANONICAL-IDENTITY`.

Each serialized write-set entry MUST be no larger than 1 MiB in v1.
Exceeding this limit raises `IntegrateCommitError`. Blob externalization is out
of scope.

---

## 8. Event schema: `integrate_aborted` and `overlay_summary`

```json
{
  "type": "integrate_aborted",
  "schema_version": "alpha3g.integrate.v1",
  "pre_state_hash": "sha256(...)" ,
  "abort_reason": "barrier_violation",
  "barrier_op": "random",
  "failure_point": {
    "kind": "ast_node",
    "node_id": "node:sha256(...)" ,
    "source_span_hash": "sha256(...)"
  },
  "overlay_summary": {
    "dirty_count": 1,
    "dirty_paths": [
      {
        "path": "/env/user",
        "granularity": "top_level",
        "old_value_hash": "sha256(...)" ,
        "overlay_value_hash": "sha256(...)"
      }
    ],
    "orphaned_resources_count": 0
  }
}
```

Allowed `abort_reason` values include:

```text
barrier_violation
exception
guard_violation
out_of_gas
commit_error
serialization_error
```

`overlay_summary` is forensic metadata. It MUST NOT contain `new_value` and MUST
NOT be applied in REPLAY.

`overlay_summary` MAY include hashes of intermediate overlay values for
forensic analysis, but REPLAY MUST NOT require recomputation of those overlay
hashes because the body is not re-executed.

Canonical hash allowlists for `integrate_aborted` MUST exclude host-specific
stack traces, absolute paths, PIDs, Python object ids, and raw intermediate
values.

---

## 9. StateOverlay v1 architecture

`StateOverlay` is the transaction isolation layer for LIVE execution.

Responsibilities:

- expose an isolated view of `/env/*` and `/memory/*`;
- prevent writes from leaking into base state before commit;
- track dirty top-level paths;
- preserve repeated-read clone consistency;
- preserve top-level aliasing semantics;
- provide data for `write_set` or `overlay_summary`;
- discard overlay state on abort.

The overlay is not persisted as a standalone artifact in v1. Only
`integrate_committed.write_set` and `integrate_aborted.overlay_summary` are
persisted.

Implementation MAY use conservative dirty-on-read for mutable top-level values
in v1. The semantic goal is dirty-on-write, but conservative dirty-on-read is an
acceptable implementation strategy if it preserves correctness.

If cache keys use object identity, the cache MUST NOT rely on bare `id(obj)`
without identity validation. A cache entry MUST retain or otherwise validate the
original object reference so that CPython id reuse cannot return a stale clone in
long-running processes.

---

## 10. LIVE mode algorithm

1. Compute `pre_state_hash` over canonical current state.
2. Create `StateOverlay` for supported namespaces.
3. Run static barrier validation before executing the body.
4. Execute the body inside the overlay.
5. Dynamic barrier traps any indirect forbidden operation.
6. Suspend/defer background runtime activity during the integrate window under the habit barrier in §12.
7. Collect dirty top-level paths.
8. Preserve aliasing by marking all top-level aliases of dirty mutable values.
9. Build sorted `write_set` with `old_value_hash`, `new_value`, and
   `new_value_hash`.
10. Reject duplicate paths.
11. Enforce per-entry size limit.
12. Compute `post_state_hash` after applying the overlay result in canonical
    order.
13. Append `integrate_committed`.
14. Atomically apply `write_set` to base state.

If any error occurs before commit, discard the overlay and append
`integrate_aborted`.

---

## 11. REPLAY mode algorithm

1. Consume the next integrate event.
2. Select replay applier by `(event.type, event.schema_version)`.
3. If schema is unsupported, raise `EVENT_SCHEMA_UNSUPPORTED`.
4. Verify `current_state_hash == event.pre_state_hash`.
5. If event is `integrate_committed`:
   - verify `write_set` is sorted by path;
   - verify no duplicate paths;
   - for each entry:
     - read current value at `path`;
     - verify `hash(current_value) == old_value_hash`;
     - apply `replace` or `delete`;
     - verify `new_value_hash` for replace;
   - verify resulting state hash equals `post_state_hash`.
6. If event is `integrate_aborted`:
   - verify abort reason and canonical failure point when possible;
   - do not apply `overlay_summary`;
   - leave state unchanged;
   - reproduce the recorded abort condition according to caller semantics.
7. Any mismatch raises `REPLAY_INTEGRITY_ERROR`.

The first integrate in a session uses canonical genesis state if no prior state
exists. The canonical genesis hash is defined by
`RFC-STABLE-CANONICAL-IDENTITY`.

Crash-resume idempotency and persistent replay checkpointing are out of scope for
v1. Future replay runners MAY add commit nonces or checkpoint state.

---

## 12. Static + dynamic nondeterminism barrier

`integrate` has a two-level barrier.

### Static barrier

Before execution, AST/compile-time analysis MUST reject integrate bodies that
contain forbidden constructs directly.

Forbidden in v1:

```text
LLMCall
DreamBlock
Fracture
Actor spawn/send/migrate/receive
Affective resonance
async / await / suspension points
external storage calls
time / random / uuid host calls
nested integrate
```

### Dynamic barrier

Runtime MUST trap indirect calls, dynamic symbol dispatch, or any edge cases the
static barrier cannot prove.

A forbidden operation inside integrate raises `NondeterminismBarrierViolation`
and records `integrate_aborted`.

Barrier checks MUST run before a forbidden operation can append nested events to
`execution_history`.

### Habit suspension / background mutation barrier — resolves INT-03

`integrate` requires a frozen transaction span. Background runtime mutation is
forbidden while the integrate body executes and while the write set is being
committed.

During an integrate span, the runtime MUST establish a habit barrier:

```text
1. Suspend automatic habit activation before the body begins.
2. Defer habit events that would otherwise fire during the span.
3. Prevent habit-triggered mutation of EnergyPool, AffectiveState, memory,
   counters, activation metadata, or any other canonical state.
4. Resume or process deferred habit work only after commit or abort has fully
   completed.
```

If a habit activation or any other background runtime mutation is observed inside
the integrate span, the transaction MUST abort with:

```text
abort_reason = "barrier_violation"
barrier_op   = "habit_activation"
```

The abort MUST occur before the background mutation appends nested events or
modifies base state. If the mutation cannot be prevented before side effects are
visible, the implementation is not eligible for integrate v1 approval.

Pre-existing habit state is allowed. It contributes to `pre_state_hash` before
the integrate span begins. Mutation during the span is forbidden unless a future
RFC defines a recorded-and-consumed habit subtrace or transaction-aware habit
queue.

---

## 13. No nested events invariant

No event-producing operation may append to `execution_history` from inside an
`integrate` body.

This is stronger than rollback. The forbidden operation must not dirty the log
in the first place.

Invalid sequence:

```text
llm_call
integrate_aborted
```

The correct behavior is:

```text
integrate_aborted
```

with `barrier_op = "llm_call"` or equivalent.

External effects before commit are forbidden. File writes, network calls, actor
messages, promise creation, and external storage mutations cannot be rolled back
by `StateOverlay` and must be rejected or deferred until after commit by a future
RFC.

---

## 14. Aliasing semantics

`StateOverlay` MUST preserve top-level aliasing semantics.

If the same mutable object is reachable via multiple top-level paths and any
alias is dirty, all aliases MUST be included in `write_set` or
`overlay_summary`.

Example:

```text
/env/a -> shared_list
/env/b -> shared_list
```

If `a` is mutated, `/env/a` and `/env/b` are both dirty.

This prevents LIVE from seeing shared-reference mutation while REPLAY replaces
only one top-level path.

Repeated reads of the same parent mutable value inside a single integrate body
MUST return the same overlay clone/proxy.

---

## 15. Canonical serialization requirements

`old_value_hash`, `new_value_hash`, `pre_state_hash`, `post_state_hash`, and
`overlay_value_hash` MUST use `canonical_json_bytes` from
`RFC-STABLE-CANONICAL-IDENTITY`.

Key requirements inherited here:

```text
NFC strings
RFC 8785 object canonicalization
canonical path/key encoding from §4
NaN/Infinity forbidden
-0.0 normalized to 0.0
set encoding with items sorted by canonical_json_bytes(element)
bytes typed wrapper with base64url
large integers outside JS-safe range encoded as typed decimal strings
no Python hash()
no Python repr()
cycle detection required
```

Unsupported in integrate v1 write sets:

```text
functions
closures
native/builtin functions
custom Python objects
agent instances
ActorRef / Promise / runtime handles
backend/provider objects
file/socket/process handles
```

If a changed top-level path contains an unsupported value, commit fails with
`CanonicalSerializationError` and records `integrate_aborted` with
`abort_reason = "serialization_error"`.

### 15.1 Function serialization boundary — resolves INT-01

`.syn` functions are first-class language values, but Alpha3g integrate v1 does
not serialize executable logic into `write_set`. A changed path whose new value
contains a function, closure, native callable, builtin function, bound method, or
host callable MUST fail commit with `CanonicalSerializationError`.

Required v1 behavior:

```text
LIVE integrate body writes function value
  -> commit-time canonical serialization detects unsupported value
  -> discard overlay
  -> append integrate_aborted
  -> abort_reason = "serialization_error"
  -> barrier_op or failure_point identifies function serialization boundary

REPLAY sees integrate_aborted
  -> consumes/verifies abort fact
  -> does not apply overlay_summary
  -> does not recreate or serialize the function
```

The failure boundary is commit-time canonical serialization, not Python object
stringification. Implementations MUST NOT serialize functions through:

```text
repr(fn)
id(fn)
module-qualified Python name alone
pickle
Python bytecode object identity
host memory address
```

Native or builtin functions such as `print`, `time`, `random`, and `uuid` are
forbidden in write sets unless a future RFC explicitly whitelists them with all
of the following properties:

```text
deterministic = true
side_effects = false
canonical_value_schema = approved
stable_identity = approved
```

### 15.2 Future FunctionCanonicalization schema

A future RFC MAY introduce canonical function values, but it MUST use an
explicit schema rather than implicit host serialization. The minimum candidate
shape is:

```json
{
  "type": "fn",
  "ast_hash": "sha256(canonical AST)",
  "closure_bindings": {
    "x": "<canonical_value>"
  },
  "is_builtin": false
}
```

Future invariants:

- `ast_hash` MUST be computed from canonical AST bytes, not Python `repr()`.
- `closure_bindings` MUST contain only canonical-serializable captured values.
- non-serializable captures MUST fail with `CanonicalSerializationError`;
- builtin/native functions require an explicit whitelist and stable identity;
- the function schema MUST be shared with `RFC-STABLE-CANONICAL-IDENTITY` and
  the Dream strict eligibility shared canonicalization hooks.

Until such an RFC is approved, function serialization remains rejected in
integrate v1.

Function, closure, agent-instance, and canonical object serialization beyond
this rejection boundary require future RFCs.

---

## 16. Schema versioning and applier registry

Every `integrate_committed` and `integrate_aborted` event MUST include:

```text
schema_version = "alpha3g.integrate.v1"
```

Replay runners MUST choose an applier by:

```text
(event.type, event.schema_version)
```

Unknown versions fail with `EVENT_SCHEMA_UNSUPPORTED`.

A runner MUST NOT silently apply v2 rules to v1 events or v1 rules to v2 events.

Artifacts may eventually contain events from multiple schema versions. That
requires an applier registry rather than a single global integrate replay
function.

---

## 17. Size limits and blob externalization boundary

Alpha3g v1 defines:

```text
max_write_set_entry_size = 1 MiB
```

The size is measured over canonical serialized bytes of the write-set entry or
its `new_value`, as implementation specifies in the implementation patch.

If the limit is exceeded, LIVE commit fails with `IntegrateCommitError` and
records `integrate_aborted`.

Blob externalization is out of scope for v1. Future RFCs may introduce
`blob:<hash>` references, artifact-side blob storage, or IPFS/Git-LFS-like
storage.

---

## 18. Interaction with `dream_completed.result_hash`

`DreamBlock` is forbidden inside `integrate` in v1.

However, integrate may reference a previously recorded dream result by value or
by `dream_completed.result_hash` if that value is already present in the
canonical state before integrate begins.

During REPLAY, if an integrate write-set or state value claims dependency on a
dream result hash, the runner SHOULD verify that the referenced dream result was
recorded earlier in history before applying the integrate event.

This RFC does not change dream semantics.

---

## 19. Acceptance criteria

An implementation patch may be accepted only if tests prove:

- `integrate_committed` is recorded with `schema_version`.
- `write_set` is sorted by RFC 6901 path.
- `replace` and `delete` replay correctly.
- empty `write_set` records and replays as a no-op transaction.
- duplicate paths are rejected.
- unsupported serialization records an abort.
- function, closure, native callable, and builtin function values in changed paths abort with `serialization_error`.
- `integrate_aborted` never applies overlay state in replay.
- memory keys use `canonical_key_encode()` and reject ambiguous, invalid, or non-canonical path encodings.
- static barrier rejects direct forbidden constructs.
- dynamic barrier catches indirect forbidden operations.
- forbidden operations do not append nested events before abort.
- habit activation and background runtime mutation are suspended/deferred during integrate spans or abort before mutation.
- aliasing of top-level mutable objects is preserved.
- NaN/Infinity, unsupported objects, functions, and runtime handles are rejected.
- max entry size is enforced.
- schema-version applier selection rejects unknown versions.
- first integrate pre-state matches canonical genesis state when applicable.

---

## 20. Out of scope and future RFCs

Out of scope for v1:

```text
fine-grained JSON Patch / deep diff
lazy CoW proxy optimization
persistent storage transaction hooks
index overlay / vector index rollback
blob externalization implementation
actor causal DAG
async integrate
nested integrate
dream inside integrate
function / closure canonical serialization
agent instance canonical serialization
ActorRef stable identity implementation
OS-level host sandboxing
artifact migration registry implementation
crash-resume idempotent replay applier
StateOverlay standalone snapshot persistence
history checkpointing
chaos determinism CI
AST-to-CVM migration
```

These require separate RFCs.
---

## 21. Review feedback status (Alpha3g P0.2.4)

Alpha3g P0.2.4 adds a structured review registry for this RFC:

```text
docs/RFC-INTEGRATE-REVIEW-NOTES.md
```

The P0.2.4 review status was **NEEDS REVISION — Blockers Open**. Alpha3g
P0.2.6 revises this RFC to resolve the three BLOCKER findings in the product
artifact:

```text
INT-01 Function serialization in write_set      -> RESOLVED by §15.1 / §15.2
INT-02 Memory key encoding beyond RFC 6901      -> RESOLVED by §4.2 / §6
INT-03 Habit activation during integrate        -> RESOLVED by §12 habit barrier
```

Under `docs/RFC-PROCESS.md`, Alpha3g P0.2.7 independently verifies these
findings in `docs/RFC-INTEGRATE-REVIEW-NOTES.md` and records the approval gate.
This RFC is now **APPROVED — Alpha3g P0.2.7** as the product contract for
future integrate replay implementation work. P0.2.7 itself is still doc-only and
contains no runtime authorization beyond allowing later patches to implement this
approved contract.

The remaining review findings are non-blocking for approval because they are
explicitly deferred implementation gates or future-compatibility notes:

```text
MAJOR-04   Promise orphaning on abort
MAJOR-05   Genesis state hash for cold start
MAJOR-06   Replay applier idempotency
MAJOR-07   Agent instance canonicalization
MAJOR-08   Namespace path ambiguity
MINOR-09   Timing side-channel outside integrate
MINOR-10   StateOverlay snapshot format
```

These items intentionally reuse the shared canonicalization hooks introduced in
`docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` v2, especially canonical function
serialization, canonical time, builtin/nondeterminism barriers, state-delta
hashing, nested-event origin, and canonical genesis state.

Runtime implementation of `evaluate_integrate()`, `StateOverlay`, CVM opcodes,
and replay appliers remains outside this doc-only patch. Later implementation
patches may begin within the approved RFC scope, and affected runtime behavior
must satisfy the deferred MAJOR gates before merge.
---

## 22. Approval gate (Alpha3g P0.2.7)

Alpha3g P0.2.7 performs the independent team-verification gate required by
`docs/RFC-PROCESS.md`.

### Verified BLOCKER resolutions

```text
INT-01 Function serialization in write_set      -> VERIFIED
INT-02 Memory key encoding beyond RFC 6901      -> VERIFIED
INT-03 Habit activation during integrate        -> VERIFIED
```

Verification result:

- §15.1 / §15.2 provides an explicit v1 fail-closed boundary for functions,
  closures, native callables, builtin functions, bound methods, and host
  callables in write sets. The future canonical function schema is intentionally
  non-authorizing until a separate RFC approves it.
- §4.2 and §6 provide deterministic memory-key encoding and strict namespace
  parsing sufficient to remove the RFC-6901-only ambiguity identified by INT-02.
- §12 provides a transaction-span habit/background-mutation barrier with an
  explicit `barrier_violation` / `habit_activation` abort path.

### Deferred MAJOR findings

The following MAJOR findings remain visible in the review registry and block the
relevant runtime implementation merge unless their deferral terms are satisfied
or a later RFC revision closes them:

```text
INT-04 Promise orphaning on abort
INT-05 Genesis state hash for cold start
INT-06 Replay applier idempotency
INT-07 Agent instance canonicalization
INT-08 Namespace path ambiguity
```

These are accepted as explicit Alpha3g implementation gates, not silently ignored
issues. Runtime code that enters the affected scopes must either implement the
required behavior or narrow the implementation scope so the deferred finding is
not triggered.

### Tracked MINOR findings

```text
INT-09 Timing side-channel outside integrate
INT-10 StateOverlay snapshot format
```

These remain non-blocking future-compatibility notes.

### Approval decision

With INT-01 through INT-03 independently verified and the remaining findings
recorded as non-blocking implementation gates or future notes, this RFC moves
from `APPROVAL-CANDIDATE — Team Verification Required` to:

```text
APPROVED — Alpha3g P0.2.7
```

Implementation of `evaluate_integrate()`, `StateOverlay`, replay appliers, or
CVM/opcode integration may begin only in later patches and only within this
approved RFC scope. This document approval does not itself change runtime
behavior.

