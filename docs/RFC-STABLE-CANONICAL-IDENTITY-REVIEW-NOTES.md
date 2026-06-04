# RFC-STABLE-CANONICAL-IDENTITY Structured Review Notes

**Status:** VERIFIED / APPROVED — Alpha3g P0.4.3 team verification complete  
**Review patch:** Alpha3g P0.4.1  
**Target RFC:** `docs/RFC-STABLE-CANONICAL-IDENTITY.md`  
**Target RFC status:** APPROVED v1.0 — Alpha3g P0.4.3  
**Process source of truth:** `docs/RFC-PROCESS.md`  
**Runtime scope authorized:** none — documentation only  
**Implementation lock:** P0.4.3 approves the parent RFC, but runtime work remains locked until separately scoped implementation patches are approved.

This document is the structured review registry for
`RFC-STABLE-CANONICAL-IDENTITY.md`. It is a process artifact and audit trail. The
RFC remains the product artifact and source of truth once approved.

The purpose of this review is to determine whether `stable-canonical.v1` is
specific enough to become the parent contract for:

```text
Integrate deferred gates INT-01 / INT-02 / INT-07
Dream strict-eligibility future state-delta/subtrace work
canonical time and deterministic identity
agent/resource/event identity across replay and future CVM execution
```

---

## Governance metadata

**Finding lifecycle:** `OPEN -> RESOLVED -> VERIFIED`, with `REOPENED` allowed on new evidence.  
**Current RFC status:** APPROVED v1.0 — Alpha3g P0.4.3.  
**Approval gate:** STABLE-01..03 are `VERIFIED`; `RFC-STABLE-CANONICAL-IDENTITY.md` is approved as v1.0.  
**Review notes role:** process artifact / audit trail.  
**RFC role:** product artifact / final technical source of truth.  
**No-code lock:** P0.4.3 contains no runtime changes; future runtime work must cite the approved RFC and satisfy applicable deferred findings.

### Blocker resolution process

For each BLOCKER finding:

1. The RFC author or assigned owner revises `RFC-STABLE-CANONICAL-IDENTITY.md`.
2. This review note entry is updated from `OPEN` to `RESOLVED` with a resolution summary and exact section references.
3. An independent reviewer verifies the revised text.
4. The finding changes to `VERIFIED`.
5. Only after all BLOCKER findings are `VERIFIED` may the RFC move to approval. For this RFC, P0.4.3 completed that transition.

Self-verification is forbidden under `RFC-PROCESS.md`.

---

## Severity legend

| Severity | Meaning |
|---|---|
| BLOCKER | RFC cannot be approved until this is resolved. Leaving the issue open would break replay determinism, forensic guarantees, or make the contract unimplementable. |
| MAJOR | RFC may proceed only with explicit deferral or revision. Runtime implementation for affected scope is blocked until resolved or explicitly deferred. |
| MINOR | Clarification, maintainability, portability, or future-compatibility issue. Does not block approval by itself. |

## Finding status legend

| Status | Meaning |
|---|---|
| OPEN | Finding is active and not yet addressed. |
| RESOLVED | RFC text has been revised by the author/owner and awaits independent verification. |
| VERIFIED | Independent reviewer confirmed the resolution. |
| ACKNOWLEDGED | Accepted as a v1 boundary or future work item, not blocking RFC approval by itself. |
| DEFERRED | Accepted as an implementation gate or future milestone. |
| REOPENED | Previously resolved/verified finding was reopened by new evidence. |

---

## Summary table

| ID | Finding | Severity | Status | Target area | Blocks approval? | Related IDs |
|---|---|---:|---|---|---|---|
| STABLE-01 | Canonical time replay source is underspecified | BLOCKER | VERIFIED | canonical time API | No — verified | INT-09, DREAM-time, INTEGRATE-time |
| STABLE-02 | Builtin allowlist side-effect policy needs explicit fail-closed replay rule | BLOCKER | VERIFIED | builtin allowlist / barriers | No — verified | INT-03, DREAM-builtins |
| STABLE-03 | Unknown schema/profile version handling must be fail-closed | MAJOR | VERIFIED | profile registry / applier registry | No — verified | INT-06, replay-applier |
| STABLE-04 | FunctionDescriptor viability and v1 boundary need sharper criteria | MAJOR | DEFERRED | function canonicalization | No; implementation-gated | INT-01, DREAM-closure |
| STABLE-05 | Agent snapshot canonicalization must define excluded runtime fields | MAJOR | DEFERRED | agent canonicalization | No; implementation-gated | INT-07 |
| STABLE-06 | Deterministic identity seed material needs collision-domain rules | MAJOR | DEFERRED | deterministic ID generation | No; implementation-gated | actor-id, event-id |
| STABLE-07 | Migration from Alpha3g local profiles to `stable-canonical.v1` needs artifact compatibility rule | MAJOR | DEFERRED | migration / artifact compatibility | No; implementation-gated | integrate-golden, dream-golden |
| STABLE-08 | Genesis state hash must align with Integrate `pre_state_hash` | MAJOR | DEFERRED | genesis state | No; implementation-gated | INT-05 |
| STABLE-09 | Allowlist policy needs testable acceptance criteria | MINOR | ACKNOWLEDGED | acceptance criteria | No | canonical-tests |
| STABLE-10 | Profile registry needs owner/lifecycle/deprecation rules | MINOR | ACKNOWLEDGED | governance / registry | No | RFC-PROCESS |

---

## STABLE-01 — Canonical time replay source is underspecified

**Severity:** BLOCKER  
**Status:** VERIFIED — Alpha3g P0.4.3 team verification accepted  
**Target RFC area:** canonical time API, logical clock progression, replay consumption  
**Related IDs:** INT-09 timing side-channel, DREAM canonical time, INTEGRATE canonical time  
**Resolution plan:** specify the source of canonical time in LIVE and REPLAY, including whether values are recorded in events, derived from event index/hash-chain/logical clock, or forbidden until a recorded-and-consumed effect contract exists.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** independent team verification of §15 resolution.

### Problem

`RFC-STABLE-CANONICAL-IDENTITY.md` introduces canonical time principles and a future
`runtime.get_canonical_time()` direction. The review needs the RFC to say where
time values come from during REPLAY.

If replay calls host wall-clock time, replay diverges. If replay derives time from
implicit runtime state without recording or deterministic derivation, different
runners may compute different values.

### Required resolution

The RFC MUST define a fail-closed v1 rule such as:

```text
Canonical time in replay MUST be either:
1. recorded in a replay event and consumed by cursor, or
2. deterministically derived from documented canonical material such as event
   index, session genesis, and hash-chain state, or
3. unavailable, causing NondeterminismBarrierViolation / CanonicalTimeUnavailable.
```

The RFC MUST forbid host wall-clock reads from participating in canonical state
unless wrapped by an approved recorded-and-consumed effect contract.

### P0.4.2 resolution

Resolved in `RFC-STABLE-CANONICAL-IDENTITY.md` §15. The RFC now defines two
approved replay sources: recorded-and-consumed `time_read` events and
deterministic logical time derived from approved canonical material. Missing or
mismatched time sources fail closed with `CanonicalTimeUnavailable`,
`NondeterminismBarrierViolation`, or `CANONICAL_TIME_REPLAY_MISMATCH`.

---

## STABLE-02 — Builtin allowlist side-effect policy needs explicit fail-closed replay rule

**Severity:** BLOCKER  
**Status:** VERIFIED — Alpha3g P0.4.3 team verification accepted  
**Target RFC area:** builtin allowlist, side-effect classification, replay contracts  
**Related IDs:** INT-03 nondeterminism barrier, DREAM builtin leakage, Integrate barrier policy  
**Resolution plan:** define that any builtin with `side_effects=True`, `deterministic=False`, or no explicit replay contract is forbidden from canonical execution and canonical value serialization.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** independent team verification of §13.3 resolution.

### Problem

The RFC introduces builtin allowlist metadata, including `deterministic`,
`side_effects`, and `replay_contract`. The review needs the RFC to state the
fail-closed decision rule.

This is a direct continuation of the Dream and Integrate findings where builtins
such as `print`, `time`, `random`, and `uuid` can break determinism or leak host
side effects if not blocked or recorded.

### Required resolution

The RFC MUST define a rule equivalent to:

```text
A builtin MAY participate in canonical execution only if:
- deterministic == true;
- side_effects == false;
- replay_contract is one of the approved replay-safe forms; and
- all arguments and results are stable-canonical values.

Otherwise the builtin is FORBIDDEN in strict/canonical contexts and MUST fail
closed before host execution.
```

I/O-bound builtins MUST be forbidden unless a future RFC defines a recorded
resource contract.

### P0.4.2 resolution

Resolved in `RFC-STABLE-CANONICAL-IDENTITY.md` §13.3. The RFC now defines a
fail-closed builtin decision rule: builtins must be deterministic, side-effect
free, allowlisted, have an approved replay contract, and accept/return canonical
values. Otherwise they fail before host execution.

---

## STABLE-03 — Unknown schema/profile version behavior must be fail-closed

**Severity:** MAJOR  
**Status:** VERIFIED — Alpha3g P0.4.3 team verification accepted  
**Impact category:** DETERMINISM / FORENSICS / OPERABILITY  
**Target RFC area:** schema-version registry, profile registry, applier registry  
**Related IDs:** INT-06 replay idempotency, integrate/dream artifact migration  
**Resolution plan:** define major/minor/profile compatibility rules and the default behavior for unknown versions.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** independent team verification of §4.1 / §18 resolution.

### Problem

Stable canonical profiles will be embedded in replay artifacts and state hashes.
If a runner accepts an unknown profile or unknown version optimistically, it may
compute different bytes than the recorder.

### Required resolution

The RFC SHOULD define:

```text
unknown profile id      -> fail closed
unknown major version   -> fail closed
unknown minor version   -> fail closed unless explicitly marked compatible
missing profile version -> fail closed
```

Forward compatibility MAY be added only through an explicit compatibility table.

### P0.4.2 resolution

Resolved in `RFC-STABLE-CANONICAL-IDENTITY.md` §4.1 and §18. The RFC now defines
fail-closed behavior for unknown profiles, missing profiles, unknown major/minor
versions, missing event schema versions, and missing applier registry entries.

---

## STABLE-04 — FunctionDescriptor viability and v1 boundary need sharper criteria

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Impact category:** DETERMINISM / MAINTAINABILITY  
**Target RFC area:** future FunctionDescriptor, v1 function rejection, closure bindings  
**Related IDs:** INT-01 function serialization, DREAM closure isolation  
**Resolution plan:** clarify whether FunctionDescriptor is future-only and define minimum criteria before it may enter canonical state.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** future FunctionDescriptor RFC or implementation plan.

### Problem

The RFC correctly bans functions and closures from v1 canonical values, while
sketching a future `FunctionDescriptor`. The review needs the boundary to be
unambiguous so implementation patches do not treat the sketch as approved
runtime behavior.

### Required resolution

The RFC SHOULD state that `FunctionDescriptor` is not part of `stable-canonical.v1`
unless and until a future RFC verifies:

```text
canonical AST / bytecode hash
closure binding canonicalization
builtin/native callable exclusion or explicit descriptor
module/source identity
versioned compiler profile
```

### P0.4.2 disposition

Deferred as an implementation gate. `RFC-STABLE-CANONICAL-IDENTITY.md` §13.2 now
states that `FunctionDescriptor` is future-only and not part of
`stable-canonical.v1`.

---

## STABLE-05 — Agent snapshot canonicalization must define excluded runtime fields

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Impact category:** DETERMINISM / SECURITY / FORENSICS  
**Target RFC area:** agent canonicalization, runtime handles, snapshots  
**Related IDs:** INT-07 agent instance canonicalization  
**Resolution plan:** define the excluded field classes for future agent snapshots and the minimum snapshot boundary for v1/vfuture.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** future AgentSnapshot RFC or implementation plan.

### Problem

Agent snapshots are required to close INT-07 eventually, but agent instances may
include runtime handles, caches, provider state, actor mailboxes, process-local
IDs, file/network handles, or host object references.

### Required resolution

The RFC SHOULD explicitly exclude from canonical agent snapshots:

```text
runtime handles
open connections
process/thread IDs
host object references
ephemeral caches
provider response objects without recorded resource contract
mailbox transient scheduling metadata
```

Future snapshot support MUST fail closed when encountering non-canonical fields.

### P0.4.2 disposition

Deferred as an implementation gate. `RFC-STABLE-CANONICAL-IDENTITY.md` §14 keeps
agent instances non-canonical in v1 and excludes runtime handles, provider
clients, sockets, threads, mailboxes, caches, open files, and process IDs.

---

## STABLE-06 — Deterministic identity seed material needs collision-domain rules

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Impact category:** DETERMINISM / FORENSICS / SECURITY  
**Target RFC area:** deterministic identity generation, canonical seed material, domain separation  
**Related IDs:** actor IDs, event IDs, resource IDs, future CVM identity  
**Resolution plan:** specify domain-separated seed material and collision behavior for deterministic IDs.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** future deterministic identity implementation plan.

### Problem

Deterministic identity generation requires clear seed material and domain
separation. Without it, different identity classes can collide or reuse seed
material accidentally.

### Required resolution

The RFC SHOULD require identity derivations to include:

```text
profile id
identity kind/domain
session genesis hash
parent event hash or canonical parent identity
stable namespace
ordinal / event index where applicable
canonical payload hash where applicable
```

Collision behavior MUST be fail-closed; silent fallback to UUID/random is
forbidden in canonical contexts.

### P0.4.2 disposition

Deferred as an implementation gate. `RFC-STABLE-CANONICAL-IDENTITY.md` §16 now
defines domain-separated seed material and fail-closed collision behavior.

---

## STABLE-07 — Migration from Alpha3g local profiles to `stable-canonical.v1` needs artifact compatibility rule

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Impact category:** OPERABILITY / FORENSICS / PORTABILITY  
**Target RFC area:** migration strategy, artifact compatibility, profile pinning  
**Related IDs:** integrate golden artifacts, dream golden artifacts, local-json profile  
**Resolution plan:** define how artifacts recorded under `alpha3g.local-json.v1` and `alpha3g.integrate-path.v1` remain replayable after `stable-canonical.v1` exists.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** future migration / artifact compatibility implementation plan.

### Problem

Current Integrate and Dream artifacts rely on approved Alpha3g local profiles.
Future stable canonicalization must not silently reinterpret old hashes.

### Required resolution

The RFC SHOULD require:

```text
profile id stored in artifacts/events where relevant
old artifacts replayed using their recorded profile
no silent re-hash under a newer profile
migration requires explicit migrator/applier entry
unknown or missing profile fails closed unless legacy profile is explicitly declared
```

### P0.4.2 disposition

Deferred as an implementation gate. `RFC-STABLE-CANONICAL-IDENTITY.md` §19 now
defines profile-pinned artifact compatibility and explicit migration rules.

---

## STABLE-08 — Genesis state hash must align with Integrate `pre_state_hash`

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Impact category:** DETERMINISM / FORENSICS  
**Target RFC area:** genesis state, empty environment/memory/agent registry, Integrate pre-state baseline  
**Related IDs:** INT-05 genesis state hash, Integrate `pre_state_hash`  
**Resolution plan:** define the canonical genesis material and how Integrate v1/future appliers map their first `pre_state_hash` to it.  
**Verification owner:** independent reviewer / team verification  
**Next action trigger:** future genesis/session lifecycle implementation plan.

### Problem

Integrate currently computes `pre_state_hash` from the current canonicalized env
subset. Stable Identity must define a canonical genesis state for cold-start
replay and future strict runner compatibility.

### Required resolution

The RFC SHOULD define:

```text
genesis profile id
empty env representation
empty memory representation
empty agent registry representation
excluded runtime helper bindings
initial hash-chain parent value
how first Integrate pre_state_hash relates to genesis
```

### P0.4.2 disposition

Deferred as an implementation gate. `RFC-STABLE-CANONICAL-IDENTITY.md` §17 now
defines stable genesis material and requires local-profile bridges or
`session_genesis` events rather than silent equivalence.

---

## STABLE-09 — Allowlist policy needs testable acceptance criteria

**Severity:** MINOR  
**Status:** ACKNOWLEDGED — v1 acceptance-planning item  
**Impact category:** MAINTAINABILITY / OPERABILITY  
**Target RFC area:** acceptance criteria, runtime test planning  
**Related IDs:** canonical value tests, builtin allowlist tests  
**Resolution plan:** add acceptance criteria that can be mapped to future runtime tests.  
**Verification owner:** team review  
**Next action trigger:** future RFC revision or runtime implementation planning.

### Problem

The allowlist policy is directionally correct, but future implementation needs a
checklist that is directly testable.

### Suggested mitigation

Add acceptance criteria such as:

```text
canonical serializer rejects every banned type with CanonicalSerializationError
supported primitive values round-trip to stable bytes
builtin with side_effects=True is rejected before host execution
unknown profile is rejected
same value produces same bytes across insertion order variants
```

### P0.4.2 disposition

Acknowledged for v1. `RFC-STABLE-CANONICAL-IDENTITY.md` §24 lists future runtime
implementation acceptance tests; detailed test manifests remain part of the
future implementation plan.

---

## STABLE-10 — Profile registry needs owner/lifecycle/deprecation rules

**Severity:** MINOR  
**Status:** ACKNOWLEDGED — v1 governance item  
**Impact category:** GOVERNANCE / MAINTAINABILITY  
**Target RFC area:** profile registry, lifecycle, deprecation  
**Related IDs:** RFC-PROCESS, profile migration  
**Resolution plan:** add a lightweight registry lifecycle for canonicalization profiles.  
**Verification owner:** team review  
**Next action trigger:** future RFC revision or P0.4.2.

### Problem

The RFC introduces multiple profiles. The process for adding, deprecating, or
superseding profiles should be explicit enough to avoid untracked profile drift.

### Suggested mitigation

Add a small registry policy:

```text
profile id
owner RFC
status: active / deprecated / superseded / archived
compatible versions
migration/applier reference
```

### P0.4.2 disposition

Acknowledged for v1. `RFC-STABLE-CANONICAL-IDENTITY.md` §4.1, §18, and §19 now
define fail-closed profile handling, applier registry rules, and migration
requirements. A full registry ownership table remains future governance work.

---

## Current recommendation

P0.4.3 team verification marks STABLE-01..03 `VERIFIED`, keeps STABLE-04..08 as
`DEFERRED` implementation gates, and keeps STABLE-09..10 as `ACKNOWLEDGED` v1
boundaries. `RFC-STABLE-CANONICAL-IDENTITY.md` is approved as v1.0. Runtime
implementation remains locked until separate scoped implementation patches are
approved.

