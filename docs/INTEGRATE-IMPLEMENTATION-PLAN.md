# Integrate Implementation Plan

**Status:** IMPLEMENTATION PLAN — Alpha3g P0.2.8  
**Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`  
**RFC status:** `APPROVED — Alpha3g P0.2.7`  
**Implementation status:** `NOT STARTED`  
**Patch type:** documentation-only planning artifact  
**Runtime scope:** still locked until the first implementation patch is explicitly opened  
**Governing process:** `docs/RFC-PROCESS.md`  
**Related review registry:** `docs/RFC-INTEGRATE-REVIEW-NOTES.md`

This document decomposes the approved Integrate replay-applier RFC into staged
implementation patches. It does not implement `integrate`, `StateOverlay`, CVM
opcodes, replay appliers, or runtime behavior.

---

## 1. Purpose

`RFC-INTEGRATE-REPLAY-APPLIER.md` is now approved, but approval of the RFC is not
itself a runtime merge. Implementation must proceed through scoped patches that
preserve the replay/determinism contract and satisfy the deferred implementation
gates recorded in `RFC-INTEGRATE-REVIEW-NOTES.md`.

The plan has four goals:

1. define the first safe implementation target;
2. map deferred MAJOR findings to the implementation patches that must satisfy
   them;
3. define test and golden-fixture obligations before runtime changes merge;
4. keep Stable Identity dependencies explicit so implementation does not invent
   local canonicalization rules that conflict with future RFCs.

---

## 2. Current state

| Area | Status |
|---|---|
| Integrate RFC | `APPROVED — Alpha3g P0.2.7` |
| INT-01 / INT-02 / INT-03 | `VERIFIED` blockers |
| INT-04..INT-08 | deferred MAJOR implementation gates |
| INT-09..INT-10 | tracked MINOR future-compatibility findings |
| Runtime implementation | not started |
| `evaluate_integrate()` | locked |
| `StateOverlay` | not implemented |
| Replay applier | not implemented |
| CVM/opcodes | locked |

---

## 3. Implementation lock release conditions

Runtime code may be changed only in a patch that declares its implementation
scope and satisfies the applicable gates.

A patch may touch `synapse/` for Integrate work only when all of the following
are true:

1. `RFC-INTEGRATE-REPLAY-APPLIER.md` remains `APPROVED`.
2. The patch names one implementation milestone from this document.
3. All deferred MAJOR findings required by that milestone are either satisfied in
   the patch or explicitly not in scope for that milestone.
4. The patch does not introduce behavior outside the approved RFC scope.
5. New behavior has unit tests, and replay-sensitive behavior has golden tests
   or an explicit reason why a golden fixture is deferred.
6. Existing targeted determinism gates continue to pass.

The lock re-engages if a new BLOCKER is discovered, if the patch requires a
contract not present in the approved RFCs, or if tests/golden replay fail.

---

## 4. Recommended implementation sequence

| Patch | Implementation target | Runtime code? | Required gates | Notes |
|---|---|---:|---|---|
| I1 | `StateOverlay` core + canonical path parser | Yes | INT-08 | No `evaluate_integrate()` integration yet. |
| I2 | LIVE-mode integrate skeleton | Yes | INT-03, INT-04 | Overlay creation, commit/abort shell, habit barrier, resource cleanup registry. |
| I3 | Integrate event schema emission | Yes | INT-05 | `integrate_committed`, `integrate_aborted`, genesis baseline references. |
| I4 | REPLAY applier v1 | Yes | INT-05, INT-06 | Consume/apply recorded events without body execution. |
| I5 | Agent/value canonicalization boundary | Yes | INT-07 | Agent values remain unsupported until canonical snapshots are specified. |
| I6 | Golden replay fixtures for integrate | Yes | INT-04..INT-08 | Strict replay coverage for commit/abort/hash mismatch/no-op. |
| I7 | Full gate, audit, docs sync | Maybe | all | Release-readiness pass and documentation synchronization. |

The recommended first runtime patch is **I1 — StateOverlay core + canonical path
parser**.

Rationale:

- it is infrastructure-first;
- it avoids temporary logic inside `evaluate_integrate()`;
- it addresses the path ambiguity deferred gate before transaction behavior is
  exposed;
- it provides a testable base for LIVE and REPLAY semantics.

---

## 5. I1 scope: StateOverlay core and canonical path parser

### 5.1 Allowed runtime files for I1

The exact file list may be adjusted during implementation review, but I1 should
prefer a narrow runtime boundary such as:

```text
synapse/runtime/state_overlay.py
synapse/runtime/canonical_paths.py
```

If existing modules must be touched, the patch must justify why the new logic
cannot remain isolated.

### 5.2 Explicitly out of scope for I1

I1 MUST NOT implement:

- `evaluate_integrate()` execution;
- `integrate_committed` history emission;
- `integrate_aborted` history emission;
- REPLAY applier behavior;
- CVM opcodes;
- actor runtime changes;
- promise cleanup;
- agent canonicalization;
- stable identity runtime.

### 5.3 Required I1 behavior

I1 should provide:

- copy-on-write overlay bindings;
- dirty-path tracking;
- old/new hash recording primitives;
- canonical namespace validation;
- canonical memory-key encoding from RFC §4.2;
- write-set draft generation for supported canonical values;
- rejection of bare or ambiguous paths;
- rejection of unsupported values according to RFC §15.1 / §15.2.

### 5.4 Required I1 tests

Minimum test set:

```text
test_state_overlay_dirty_tracking
test_state_overlay_copy_on_write
test_state_overlay_noop_write_not_dirty
test_canonical_key_encode_ascii_safe
test_canonical_key_encode_unicode_nfc
test_canonical_key_encode_percent_uppercase
test_memory_empty_key_path_valid
test_memory_missing_trailing_slash_invalid
test_write_set_rejects_bare_paths
test_write_set_rejects_function_values
```

I1 does not require golden fixtures unless it emits execution-history events.

---

## 6. Deferred implementation gate mapping

| Finding | Gate type | Required before | Resolution expectation |
|---|---|---|---|
| INT-04 Promise orphaning on abort | MAJOR | I2 merge | Resource cleanup registry and abort cleanup contract are implemented or the I2 scope proves promises/spawns cannot be created. |
| INT-05 Genesis state hash for cold start | MAJOR | I3/I4 merge | Deterministic session genesis baseline exists before commit events or replay applier depend on `pre_state_hash`. |
| INT-06 Replay applier idempotency | MAJOR | I4 merge | Commit nonce / last-applied guard prevents double-apply after replay restart. |
| INT-07 Agent instance canonicalization | MAJOR | I5 merge | Agent values either have canonical snapshots or remain explicitly unsupported in write sets. |
| INT-08 Namespace path ambiguity | MAJOR | I1 merge | Canonical path parser rejects bare/ambiguous paths and enforces namespace prefixes. |

Deferred means implementation-gated, not solved. A runtime patch that touches the
affected behavior must satisfy the corresponding gate.

---

## 7. Stable Identity dependency boundary

`RFC-INTEGRATE-REPLAY-APPLIER.md` depends on Stable Canonical Identity concepts,
but the Stable Identity runtime is not implemented by this plan.

Implementation rule:

```text
Integrate implementation may proceed only for value categories whose canonical
form is defined in the approved Integrate RFC or an approved dependency.
```

For Alpha3g Integrate v1:

- functions, closures, bound methods, native callables, builtin functions, and
  host callables remain unsupported in changed paths;
- agent instances remain unsupported until INT-07 is implemented;
- canonical time and stable runtime IDs are not introduced locally by Integrate;
- temporary path/key canonicalization must match RFC §4.2 and must not conflict
  with future Stable Identity revisions.

If a later implementation patch needs unsupported value categories, it must first
revise the relevant RFC or wait for `RFC-STABLE-CANONICAL-IDENTITY` approval.

---

## 8. Golden replay update plan

Golden fixtures are mandatory before Integrate replay behavior is considered
complete.

Required future fixtures:

```text
integrate_committed_basic
integrate_committed_memory_key_unicode
integrate_aborted_barrier_violation
integrate_aborted_serialization_error
integrate_noop_empty_write_set
integrate_replay_hash_mismatch
integrate_replay_idempotent_commit_nonce
```

Golden fixture rules:

- commit fixtures MUST include `pre_state_hash`, `post_state_hash`, `write_set`,
  and event hash-chain compatibility;
- abort fixtures MUST include `abort_reason` and forensic metadata but MUST NOT
  apply overlay state during replay;
- replay fixtures MUST prove that REPLAY does not execute the integrate body;
- existing golden dream/replay fixtures MUST remain unchanged unless a separate
  approved migration explicitly changes the global history schema.

---

## 9. Runtime patch acceptance checklist

Every Integrate implementation patch must answer:

- Which implementation milestone does this patch target?
- Which RFC sections does it implement?
- Which review findings are satisfied, deferred, or out of scope?
- Does it introduce new execution-history event fields?
- Does it alter hash-chain semantics?
- Does it require new golden fixtures?
- Does it touch `evaluate_integrate()`?
- Does it touch CVM/opcodes?
- Does it introduce nondeterministic behavior?
- Does it depend on Stable Identity beyond currently approved contracts?

A patch that cannot answer these questions is not ready for merge.

---

## 10. Proposed next patch after P0.2.8

Recommended next patch:

```text
P0.3.0 / I1 — StateOverlay Core & Canonical Path Parser
```

Expected scope:

- add isolated runtime modules for overlay and canonical path parsing;
- add focused unit tests;
- do not integrate with `evaluate_integrate()` yet;
- do not emit history events yet;
- do not modify CVM/opcodes.

Expected result:

```text
StateOverlay and canonical paths are testable infrastructure, but integrate
runtime behavior remains disabled until later patches.
```

---

## 11. P0.2.8 scope lock

This P0.2.8 document is planning-only.

It changes documentation only and does not modify:

- `synapse/`;
- `tests/`;
- `examples/`;
- parser;
- interpreter;
- CVM;
- bridge;
- CLI;
- actor runtime;
- replay applier;
- runtime behavior.
