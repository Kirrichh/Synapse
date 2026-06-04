# RFC-INTEGRATE Structured Review Notes

**Status:** APPROVED — Alpha3g P0.2.7 verification complete; governed by `RFC-PROCESS.md`  
**Review patch:** Alpha3g P0.2.4  
**Governance patch:** Alpha3g P0.2.5  
**Blocker-resolution patch:** Alpha3g P0.2.6  
**Verification patch:** Alpha3g P0.2.7  
**Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`  
**Runtime scope authorized:** none in this patch — documentation only  
**Implementation gate:** future implementation patches may begin only within the
approved RFC scope; affected runtime behavior must satisfy deferred MAJOR gates
before merge.

This document converts the team review feedback for
`RFC-INTEGRATE-REPLAY-APPLIER.md` into a structured review registry. As of
Alpha3g P0.2.5, finding lifecycle, blocker resolution, dependency policy,
and source-of-truth rules are governed by `docs/RFC-PROCESS.md`.

The registry is intentionally linked to the shared canonicalization hooks in
`docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` v2. Dream strict eligibility,
Integrate replay, and Stable Canonical Identity must share one vocabulary for:

```text
function serialization
canonical time
builtin and nondeterminism barriers
state-delta hashing
nested-event origin
canonical genesis state
canonical value serialization
```

---

## Governance metadata

**Process source of truth:** `docs/RFC-PROCESS.md`  
**Finding lifecycle:** `OPEN -> RESOLVED -> VERIFIED`, with `REOPENED` allowed on new evidence  
**Current RFC status:** `APPROVED — Alpha3g P0.2.7`  
**Approval gate:** INT-01..INT-03 are `VERIFIED`; remaining MAJOR/MINOR findings are explicitly deferred or tracked under `RFC-PROCESS.md`  
**Review notes role:** process artifact / audit trail  
**RFC role:** product artifact / final technical source of truth  
**No-code lock:** P0.2.7 contains no runtime code; future implementation must cite the approved RFC and satisfy deferred gates

### Blocker resolution process

For each BLOCKER finding:

1. The RFC author or assigned owner revises `RFC-INTEGRATE-REPLAY-APPLIER.md`.
2. This review note entry is updated from `OPEN` to `RESOLVED` with a resolution summary and exact section references.
3. An independent reviewer verifies the revised text.
4. The finding changes to `VERIFIED`.
5. Only after all BLOCKER findings are `VERIFIED` may the RFC move to `APPROVAL-CANDIDATE` or, by team vote, `APPROVED`.

Alpha3g P0.2.7 applies this process to INT-01..INT-03 and records them as `VERIFIED`.

Self-verification is forbidden. `RFC-INTEGRATE-REPLAY-APPLIER.md` is the product source of truth after approval; this file remains the audit trail.

---

## Severity legend

| Severity | Meaning |
|---|---|
| BLOCKER | RFC cannot be approved until this is resolved. Runtime code would have undefined behavior, broken determinism, or unsound replay semantics. |
| MAJOR | RFC may proceed only with explicit errata and an implementation requirement. Must be resolved before runtime merge. |
| MINOR | Recommendation or forward-compatibility issue. Does not block RFC approval by itself. |

## Finding status legend

| Status | Meaning |
|---|---|
| OPEN | Finding is active and not yet resolved. |
| RESOLVED | RFC text has been revised by the author/owner and awaits independent verification. |
| VERIFIED | Independent reviewer confirmed the resolution. |
| DEFERRED | Non-BLOCKER finding is accepted as an explicit implementation gate or future-compatibility note. |
| REOPENED | Previously resolved/verified finding was reopened by new evidence. |

---

## Summary table

| ID | Finding | Severity | Status | Target section | Blocks approval? | Related IDs |
|---|---|---:|---|---|---|---|
| INT-01 | Function serialization in `write_set` | BLOCKER | VERIFIED | §15.1 / §15.2 | No | DREAM-closure, STABLE-function-canonicalization |
| INT-02 | Memory key encoding beyond RFC 6901 | BLOCKER | VERIFIED | §4.2 / §6 | No | STABLE-string-normalization, DREAM-shared-canonicalization |
| INT-03 | Habit activation during integrate | BLOCKER | VERIFIED | §12 | No | DREAM-nested-origin, STABLE-barrier |
| INT-04 | Promise orphaning on abort | MAJOR | DEFERRED | §8 / §10 | No; implementation-gated | STABLE-resource-identity |
| INT-05 | Genesis state hash for cold start | MAJOR | DEFERRED | §11 / Stable Identity | No; implementation-gated | DREAM-genesis-hook, STABLE-genesis |
| INT-06 | Replay applier idempotency | MAJOR | DEFERRED | §11 / §16 | No; implementation-gated | STABLE-lineage |
| INT-07 | Agent instance canonicalization | MAJOR | DEFERRED | §15 / future agent canonicalization | No; implementation-gated | STABLE-agent-canonicalization |
| INT-08 | Namespace path ambiguity | MAJOR | DEFERRED | §6 | No; implementation-gated | STABLE-paths |
| INT-09 | Timing side-channel outside integrate | MINOR | ACKNOWLEDGED — v1 boundary documented | §12 / §13 | No | DREAM-canonical-time, STABLE-time |
| INT-10 | StateOverlay snapshot format | MINOR | ACKNOWLEDGED — v1 non-persistence boundary documented | §9 / §20 | No | STABLE-state-delta |

---

## INT-01 — Function serialization in `write_set`

**Severity:** BLOCKER  
**Status:** VERIFIED — Alpha3g P0.2.7 independent verification complete  
**Target RFC section:** §15.1 Function serialization boundary; §15.2 future FunctionCanonicalization schema  
**Related IDs:** DREAM-closure, STABLE-function-canonicalization  
**Cross-reference:** `RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` §6 and §12;
`RFC-STABLE-CANONICAL-IDENTITY.md` canonical value serialization.  
**Resolution plan:** v1 rejects function/closure/native/builtin callable values in changed paths with `CanonicalSerializationError` and `integrate_aborted`; future function schemas require a separate RFC.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** complete; future changes require reopening INT-01 with evidence.

### P0.2.6 resolution summary

`RFC-INTEGRATE-REPLAY-APPLIER.md` now defines the v1 behavior explicitly:
functions, closures, native callables, builtin functions, bound methods, and host
callables are unsupported in integrate write sets. If a changed top-level path
contains such a value, commit fails with `CanonicalSerializationError`, records
`integrate_aborted`, and uses `abort_reason = "serialization_error"`.

The RFC also defines the minimum future `FunctionCanonicalization` schema using
`ast_hash`, `closure_bindings`, and `is_builtin`, while keeping it out of scope
for v1.

### P0.2.7 verification result

VERIFIED. §15.1 closes INT-01 for v1 by rejecting executable values fail-closed
instead of attempting host serialization. §15.2 is explicitly future-scoped and
therefore does not authorize partial function canonicalization in v1.

### Problem

`.syn` functions are first-class values. A program can bind or move function
values through the environment:

```synapse
let f = my_handler
```

If an `integrate` body writes such a value into `/env/f`, the `write_set` must
serialize a function value. Current canonical JSON rules cannot serialize raw
AST nodes, closures, host callables, or captured runtime bindings safely.

The current RFC rejects unsupported functions in v1, which is safe for runtime,
but it does not yet define the approval path or canonical failure boundary well
enough for a future version.

### Required fix

Add an explicit **FunctionCanonicalization** contract to the RFC, even if v1
continues to reject functions:

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

Required invariants:

- `ast_hash` MUST be computed from canonical AST bytes, not Python `repr()`.
- `closure_bindings` MUST contain only canonical-serializable captured values.
- Non-serializable captures MUST fail commit with `CanonicalSerializationError`.
- Native/builtin functions such as `print`, `time`, `random`, and `uuid` MUST be
  forbidden in `write_set` unless a future RFC explicitly whitelists them with
  `deterministic=True`, `side_effects=False`, and a canonical serialization.
- v1 may retain the current rule: functions are unsupported and cause
  `integrate_aborted` with `abort_reason = "serialization_error"`.

### Approval condition

RESOLVED in P0.2.6. Independent reviewer must verify that §15.1 / §15.2 clearly
define rejection semantics for v1 and do not authorize implicit host-function
serialization.

---

## INT-02 — Memory key encoding beyond RFC 6901

**Severity:** BLOCKER  
**Status:** VERIFIED — Alpha3g P0.2.7 independent verification complete  
**Target RFC section:** §4.2 Memory Key Canonical Encoding; §6 Supported namespaces  
**Related IDs:** STABLE-string-normalization, STABLE-paths, DREAM-shared-canonicalization  
**Cross-reference:** Stable canonical identity string normalization and path
encoding; Dream RFC §12 shared canonicalization hooks.  
**Resolution plan:** v1 applies `canonical_key_encode()` before constructing `/memory/<key>` paths and rejects ambiguous/non-canonical paths.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** complete; future changes require reopening INT-02 with evidence.

### P0.2.6 resolution summary

`RFC-INTEGRATE-REPLAY-APPLIER.md` now separates namespace path syntax from
runtime key canonicalization. Memory keys are validated as Unicode scalar
sequences, normalized to NFC, UTF-8 encoded, and percent-encoded with uppercase
`%XX` for bytes outside `[A-Za-z0-9_.-]`. The empty memory key is explicitly
represented as `/memory/`; `/memory` without a key is invalid.

### P0.2.7 verification result

VERIFIED. §4.2 defines a deterministic key encoder and §6 requires strict
namespace validation. The `/memory/` empty-key rule and `/memory` invalid-path
rule are sufficient to prevent the ambiguity identified by INT-02 in v1.

### Problem

RFC 6901 JSON Pointer escaping handles `~` and `/`, but runtime memory keys may
be arbitrary strings. Edge cases include:

```text
empty string
NUL-like/control characters
non-NFC Unicode
surrogate-like invalid sequences from external sources
literal strings such as "~1" and "~0"
characters outside the expected path-safe set
```

A path contract that relies only on RFC 6901 escaping is not enough for memory
or palace keys that are arbitrary user data.

### Required fix

Add **Memory Key Canonical Encoding** before path construction:

```text
canonical_key_encode(key):
  1. validate as canonical Unicode scalar sequence
  2. normalize to NFC
  3. encode to UTF-8 bytes
  4. for characters outside [A-Za-z0-9_.-], use percent-encoding or base64url
  5. construct path as /memory/<encoded_key>
```

Required invariants:

- invalid Unicode input MUST be rejected before path construction;
- empty key MUST be explicitly documented, for example `/memory/`;
- path parser MUST distinguish encoded memory keys from namespace delimiters;
- bare paths without a namespace prefix MUST be rejected.

### Approval condition

RESOLVED in P0.2.6. Independent reviewer must verify that §4.2 and §6 define a
deterministic encoding for arbitrary memory keys and unambiguous namespace
parsing.

---

## INT-03 — Habit activation during integrate

**Severity:** BLOCKER  
**Status:** VERIFIED — Alpha3g P0.2.7 independent verification complete  
**Target RFC section:** §12 Habit suspension / background mutation barrier  
**Related IDs:** DREAM-nested-origin, STABLE-barrier  
**Cross-reference:** Dream RFC §12 builtin/nondeterminism barrier hooks.  
**Resolution plan:** v1 establishes a habit barrier: suspend automatic activation, defer events, and abort on observed background mutation.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** complete; future changes require reopening INT-03 with evidence.

### P0.2.6 resolution summary

`RFC-INTEGRATE-REPLAY-APPLIER.md` now requires the runtime to establish a frozen
transaction span. Habit activation and background runtime mutation must be
suspended or deferred before the body begins. If a habit activation is observed
inside the span, the transaction aborts with `abort_reason = "barrier_violation"`
and `barrier_op = "habit_activation"` before state or history is dirtied.

### P0.2.7 verification result

VERIFIED. §12 closes INT-03 by requiring habit suspension/deferment for the full
transaction span and by defining a fail-closed `barrier_violation` /
`habit_activation` abort path when background mutation is observed.

### Problem

`integrate` is intended to be an isolated transaction. However, a background
habit subsystem can mutate state while an integrate body is executing. Examples
include `EnergyPool`, `AffectiveState`, habit counters, or scheduled activation
metadata.

If such mutation happens between two operations inside the integrate body, the
mutation may bypass `StateOverlay` and therefore escape the recorded `write_set`.
Replay would then apply only the recorded transaction and miss the background
mutation.

### Required fix

Addressed in P0.2.6 by adding an explicit **Habit Suspension / Deferral Contract**:

- During integrate body execution, habit activation MUST be suspended.
- Habit events generated during an integrate span MUST be deferred to
  post-commit or pre-abort.
- Pre-existing habit state before integrate begins is included in
  `pre_state_hash`.
- Any habit mutation during integrate body execution is a
  `NondeterminismBarrierViolation`.
- Deferred habit events MUST NOT appear as nested events inside an
  `integrate_committed` event.

### Approval condition

RESOLVED in P0.2.6. Independent reviewer must verify that §12 explicitly
defines habit suspension, deferral, and abort semantics for observed background
runtime mutation.

---

## INT-04 — Promise orphaning on abort

**Severity:** MAJOR  
**Status:** DEFERRED — accepted as implementation gate after P0.2.7 approval  
**Target RFC section:** §8 `integrate_aborted` and `overlay_summary`; §10 LIVE
mode algorithm  
**Related IDs:** STABLE-resource-identity  
**Cross-reference:** actor runtime resource cleanup; Dream RFC §12 shared
barrier semantics.  
**Resolution plan:** add resource cleanup requirements or defer as MAJOR with milestone and risk acceptance.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, OPERABILITY, FORENSICS

### Problem

An integrate body can create runtime resources before aborting. A promise,
actor spawn, temporary mailbox, or runtime allocation is not a normal env value.
If `integrate` aborts after resource creation, a simple `StateOverlay` rollback
may discard env changes while leaving the runtime resource alive.

This creates orphaned promises/resources and can desynchronize replay: LIVE may
leave a pending resource, while REPLAY consumes an `integrate_aborted` marker and
never creates that resource.

### Required fix

Add a **Resource Cleanup Contract**:

- `StateOverlay` rollback MUST be paired with a `ResourceCleanupPhase`.
- Promises, actor spawns, temporary memory allocations, and transient runtime
  resources created inside integrate MUST be tracked in `overlay_resources`.
- On abort, `overlay_resources` MUST be explicitly cancelled or destroyed.
- `integrate_aborted.overlay_summary` SHOULD include
  `orphaned_resources_count` and resource categories for forensic review.
- A failure to clean up resources MUST fail closed.

### Approval condition

This can be accepted as explicit errata, but runtime implementation must not
merge until cleanup behavior is tested.

### P0.2.7 deferral

DEFERRED as an implementation gate. RFC approval may proceed, but any runtime
implementation that can allocate promises, actor spawns, temporary mailboxes, or
other resources inside integrate MUST implement and test ResourceCleanupPhase
before merge.

---

## INT-05 — Genesis state hash for cold start

**Severity:** MAJOR  
**Status:** DEFERRED — accepted as implementation gate after P0.2.7 approval  
**Target RFC section:** §11 REPLAY mode algorithm; Stable Canonical Identity  
**Related IDs:** DREAM-genesis-hook, STABLE-genesis  
**Cross-reference:** Dream RFC §12 canonical genesis state hook.  
**Resolution plan:** add genesis baseline requirements or defer as MAJOR with milestone and risk acceptance.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, PORTABILITY

### Problem

The first `integrate` in a program needs a `pre_state_hash`. At cold start there
may be no previous state-transition event from which to derive a baseline.
Different runners could compute “empty state” differently if no genesis contract
exists.

### Required fix

Add a **session genesis event** or equivalent canonical baseline:

```json
{
  "type": "session_genesis",
  "empty_state_hash": "sha256(canonical empty runtime state)",
  "schema_version": "alpha3g.session.v1"
}
```

The empty state hash MUST cover a canonical representation of:

```text
empty env
empty memory / memory palace state
empty agent registry
empty actor runtime state or explicit actor-runtime genesis marker
runtime/schema versions that affect canonical state
```

The first `integrate` MUST use this baseline as `pre_state_hash` when no earlier
state-transition event exists.

### Approval condition

This can be accepted as errata, but the replay algorithm must not leave cold
start baseline implicit.

### P0.2.7 deferral

DEFERRED as an implementation gate. RFC approval may proceed, but durable replay
or first-transaction replay implementation MUST define the session genesis
baseline before merge.

---

## INT-06 — Replay applier idempotency

**Severity:** MAJOR  
**Status:** DEFERRED — accepted as implementation gate after P0.2.7 approval  
**Target RFC section:** §11 REPLAY mode algorithm; §16 Schema versioning and
applier registry  
**Related IDs:** STABLE-lineage  
**Resolution plan:** add idempotency requirements or defer as MAJOR with milestone and risk acceptance.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, OPERABILITY

### Problem

A replay runner may crash after applying an `integrate_committed.write_set` but
before writing a checkpoint. If replay resumes and applies the same write-set
again, non-idempotent effects can corrupt state.

Even when v1 operations are mostly `replace`/`delete`, future operations and
resource application phases require an explicit at-least-once safety contract.

### Required fix

Add an **Idempotency Contract**:

- Every `integrate_committed` event MUST carry a monotonic `commit_nonce` within
  the session or lineage.
- The replay applier MUST persist `last_applied_commit_nonce` or equivalent
  checkpoint state.
- The applier applies a write-set only if
  `commit_nonce > last_applied_commit_nonce`.
- If `commit_nonce <= last_applied_commit_nonce`, replay returns
  `REPLAY_ALREADY_APPLIED` / silent success after verifying event hash and
  schema compatibility.
- Nonce gaps MUST fail with `REPLAY_COMMIT_NONCE_GAP` unless explicitly
  supported by a future sparse lineage model.

### Approval condition

This can be accepted as errata, but crash-resume semantics must be explicit
before a durable replay runner is shipped.

### P0.2.7 deferral

DEFERRED as an implementation gate. RFC approval may proceed, but crash-resume
or persistent replay checkpoint implementation MUST define commit idempotency
before merge.

---

## INT-07 — Agent instance canonicalization

**Severity:** MAJOR  
**Status:** DEFERRED — accepted as implementation gate after P0.2.7 approval  
**Target RFC section:** §15 Canonical serialization requirements  
**Related IDs:** STABLE-agent-canonicalization  
**Cross-reference:** Stable Canonical Identity; Dream RFC §12 function and
object canonicalization hooks.  
**Resolution plan:** add agent canonicalization/rejection requirements or defer as MAJOR with milestone and risk acceptance.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, MAINTAINABILITY

### Problem

A `.syn` program may create agent instances:

```synapse
let bot = Greeter()
```

If an integrate body changes `/env/bot` or a field under an agent namespace,
canonical replay must know how to serialize the agent state. Agent instances may
contain durable fields, model config, memory references, actor mailbox state,
provider handles, caches, or runtime IDs.

### Required fix

Add **AgentInstanceCanonicalization**:

```json
{
  "type": "agent",
  "class": "Greeter",
  "state_hash": "sha256(canonical agent fields)",
  "fields": {
    "model": "...",
    "config": {},
    "memory_refs": []
  }
}
```

Required invariants:

- Agent snapshots include only canonical durable fields.
- Model name, config, policy references, and memory refs must have canonical
  representation.
- Runtime handles, open connections, provider objects, ephemeral caches,
  process IDs, thread IDs, host file descriptors, and mailbox implementation
  details MUST be excluded or rejected.
- Non-canonical runtime data causes `CanonicalSerializationError` at commit.
- v1 may continue to reject agent instances, but that rejection must be explicit.

### Approval condition

This can be accepted as errata, but v1 implementation must reject or canonicalize
agent instances deterministically.

### P0.2.7 deferral

DEFERRED as an implementation gate. RFC approval may proceed, but any integrate
implementation that allows changed paths containing agent instances MUST either
reject them fail-closed or implement an approved canonical snapshot contract.

---

## INT-08 — Namespace path ambiguity

**Severity:** MAJOR  
**Status:** DEFERRED — accepted as implementation gate after P0.2.7 approval  
**Target RFC section:** §6 Supported namespaces  
**Related IDs:** STABLE-paths  
**Resolution plan:** add strict namespace parser validation or defer as MAJOR with milestone and risk acceptance.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, MAINTAINABILITY

### Problem

Paths must be unambiguous. Without strict namespace validation, a string like
`/env/memory` or `/memory/env` may be misread by humans or future appliers.
Bare or shorthand paths could collide with actual namespace names or memory
keys.

### Required fix

Add strict path namespace rules:

```text
/env/<name>            env variable
/memory/<encoded_key>  memory slot
/agent/<id>/<field>    future agent field
```

Required invariants:

- Path parser MUST validate the namespace prefix.
- Bare paths MUST be rejected.
- Unknown namespaces MUST fail with `INTEGRATE_PATH_NAMESPACE_UNSUPPORTED`.
- `/env/memory` is always an env variable named `memory`, not the memory
  namespace.
- `/memory/<encoded_key>` is always a memory key after canonical key decoding.

### Approval condition

This can be accepted as errata, but path parsing must fail closed.

### P0.2.7 deferral

DEFERRED as an implementation gate. RFC approval may proceed because INT-02 now
covers canonical memory-key encoding and strict namespace parsing; implementation
MUST still test fail-closed parsing for all supported namespaces before merge.

---

## INT-09 — Timing side-channel outside integrate

**Severity:** MINOR  
**Status:** OPEN  
**Target RFC section:** §12 nondeterminism barrier; §13 external effects  
**Related IDs:** DREAM-canonical-time, STABLE-time  
**Cross-reference:** Dream RFC §12 canonical time hook.  
**Resolution plan:** document timing side-channel boundary and future metadata/replay handling.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** DETERMINISM, SECURITY  
**Impact if ignored:** time-sensitive programs may branch differently between LIVE and REPLAY; elapsed-time side channels remain a known limitation.

### Problem

Even if `time()` is forbidden inside integrate, caller code can observe elapsed
wall-clock time around an integrate block:

```synapse
let t1 = time()
integrate { ... }
let t2 = time()
let elapsed = t2 - t1
```

If LIVE executes the body and REPLAY consumes a recorded event without body
execution, elapsed time differs. Program logic that branches on elapsed time can
still diverge.

### Required fix

Add a timing side-channel note:

- Integrate execution duration MUST NOT be semantically observable unless it is
  explicitly recorded as metadata.
- If exposed, `integrate_duration` MUST be recorded in LIVE and consumed in
  REPLAY.
- Programs MUST NOT derive deterministic state from host elapsed time unless the
  value is recorded-and-consumed.

### Approval condition

This does not block RFC approval, but it should be tracked before strict replay
runners are used for timing-sensitive programs.

### P0.3.6 (I7) acknowledgement

ACKNOWLEDGED for v1. The timing side-channel risk is documented in
`DETERMINISM_CONTRACT.md` §6.3 (deferred MAJOR gate INT-09, related to
STABLE-time). Programs MUST NOT derive deterministic state from elapsed time
around integrate blocks unless `integrate_duration` is explicitly recorded and
consumed. Tracking remains active until STABLE-time canonical time hook is
specified.

---

## INT-10 — StateOverlay snapshot format

**Severity:** MINOR  
**Status:** OPEN  
**Target RFC section:** §9 StateOverlay v1 architecture; §20 future RFCs  
**Related IDs:** STABLE-state-delta  
**Resolution plan:** document v1 non-persistence boundary and future overlay snapshot RFC path.  
**Verification owner:** independent reviewer / TBD  
**Impact category:** MAINTAINABILITY, FORENSICS  
**Impact if ignored:** debugger/fork persistence may grow incompatible ad-hoc overlay formats later.

### Problem

`StateOverlay` is defined as an abstraction, but its standalone persistence
format is not specified. v1 may not need to persist overlays independently from
`integrate_committed.write_set`, but debugger forks and crash recovery may need
this later.

### Required fix

Add an explicit v1 boundary:

```json
{
  "namespace": "env",
  "bindings": {
    "x": {
      "old_hash": "...",
      "new_value": "<canonical_value>",
      "new_value_hash": "..."
    }
  }
}
```

Recommended v1 rule:

- StateOverlay is not persisted as an independent artifact in v1.
- Only `integrate_committed.write_set` is durable.
- Standalone `OverlaySnapshot` for debugger/fork reuse is a future RFC.

### Approval condition

This does not block RFC approval, but the v1 non-persistence boundary must be
clear.

---

## Approval decision after P0.2.7

`RFC-INTEGRATE-REPLAY-APPLIER.md` is now:

```text
APPROVED — Alpha3g P0.2.7
```

P0.2.7 performs the independent verification gate required by
`docs/RFC-PROCESS.md`:

```text
INT-01 -> VERIFIED
INT-02 -> VERIFIED
INT-03 -> VERIFIED
```

The remaining findings are handled as follows:

```text
INT-04..INT-08 -> DEFERRED MAJOR implementation gates
INT-09..INT-10 -> OPEN MINOR future-compatibility notes
```

Runtime code remains unchanged in P0.2.7. Future implementation patches may begin
only within the approved RFC scope and must satisfy the deferred MAJOR gates
before merging affected runtime behavior.

### P0.3.6 (I7) acknowledgement

ACKNOWLEDGED for v1. `StateOverlay` is not persisted as an independent artifact
in v1; only `integrate_committed.write_set` is durable
(`DETERMINISM_CONTRACT.md` §6.3). Standalone `OverlaySnapshot` for
debugger/fork reuse is explicitly a future RFC. Tracking active until
RFC-STABLE-CANONICAL-IDENTITY defines the state-delta format.

---

## Approval decision after P0.2.7

`RFC-INTEGRATE-REPLAY-APPLIER.md` is now:

```text
APPROVED — Alpha3g P0.2.7
```

P0.2.7 performs the independent verification gate required by
`docs/RFC-PROCESS.md`:

```text
INT-01 -> VERIFIED
INT-02 -> VERIFIED
INT-03 -> VERIFIED
```

The remaining findings are handled as follows:

```text
INT-04..INT-08 -> DEFERRED MAJOR implementation gates
INT-09..INT-10 -> ACKNOWLEDGED (v1 boundary documented in P0.3.6 / I7)
```

Runtime code remains unchanged in P0.2.7. Future implementation patches may begin
only within the approved RFC scope and must satisfy the deferred MAJOR gates
before merging affected runtime behavior.

## Next action (updated P0.3.6 / I7)

**Completed as of I7:** implementation I1–I6 done; docs synced to code.  
**Still blocked:** durable crash-resume (INT-06), resource cleanup (INT-04),
genesis baseline (INT-05), agent canonicalization (INT-07) — must satisfy before
strict Layer 1 eligibility. Next milestone: RFC-STABLE-CANONICAL-IDENTITY for
shared identity contracts across integrate/dream/agents.
