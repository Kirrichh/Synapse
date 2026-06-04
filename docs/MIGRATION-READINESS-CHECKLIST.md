# Stable Canonical Migration Readiness Checklist

**Status:** Alpha3g P0.4.10 / SI5 — StateOverlay and Integrate flagged migrations completed  
**Runtime authorization:** StateOverlay and Integrate profile-selector integration completed behind explicit opt-in. Other consumers remain locked.  
**Parent contract:** `docs/RFC-STABLE-CANONICAL-IDENTITY.md` v1.0

This checklist defines the readiness conditions for migrating existing Alpha3g
local canonical profiles to `stable-canonical.v1`. It is intentionally a planning
artifact: no `StateOverlay`, `Integrate`, `Dream`, CVM, actor runtime, golden
fixture, or storage migration is performed by P0.4.5.


---

## 0. P0.4.6 SI3 status update

`P0.4.6 / SI3` adds `synapse/canonical_service.py` as an isolated
anti-corruption layer for migration analysis. The service can compute both the
legacy `alpha3g.local-json.v1` hash and the target `stable-canonical.v1` hash
for representative values, returning a machine-readable drift category.

Consumer migration remains blocked until drift categories are mapped for the
subsystem being migrated. No `StateOverlay`, `Integrate`, `Dream`, CVM, actor
runtime, or golden replay consumer is switched by SI3.

Current StateOverlay migration gate after P0.4.8:

```text
StateOverlay migration: COMPLETED (flagged)
Evidence: P0.4.7 drift baseline analyzed current Integrate Category B fixture payloads
Result: 14/14 analyzed payloads classified as drift_category = none
Breaking drift found: 0
P0.4.8: explicit StateOverlay profile selector implemented
P0.4.8: legacy default alpha3g.local-json.v1 preserved
P0.4.8: stable-canonical.v1 opt-in covered by dual-profile tests
Hard switch: still forbidden without separate approval
```

---

## 0.1 P0.4.7 drift baseline status

`P0.4.7 / SI4-prep` adds a read-only drift baseline over the current Integrate
Category B conformance corpus. It does not switch any consumer to
`stable-canonical.v1`; it only records the migration gate result in
`docs/MIGRATION-DRIFT-REPORT.md`.

Result: **GO — ready for feature-flagged StateOverlay migration**.

The GO applies only to a future explicit profile-selector migration. It does not
authorize a hard switch, fixture rewrite, interpreter migration, CVM/opcode work,
canonical time API, deterministic ID generation, or FunctionDescriptor /
AgentSnapshot implementation.

## 0.2 P0.4.8 StateOverlay flagged migration status

`P0.4.8 / SI4` adds an explicit `StateOverlay` value-hash profile selector.
The constructor default remains `alpha3g.local-json.v1`, preserving all existing
legacy behavior and Integrate Category B artifact interpretation.

Opt-in `profile="stable-canonical.v1"` delegates value hashing and canonical
write-set value construction through the Stable Canonical service boundary.
Stable-profile write-set entries include `value_profile: "stable-canonical.v1"`
so future event/replay migrations can identify the hash profile explicitly.

StateOverlay migration status: **COMPLETED (flagged)**.

This does not authorize an interpreter default switch, fixture rewrite,
Integrate event-profile migration, CVM/opcode work, canonical time API,
deterministic ID generation, FunctionDescriptor, or AgentSnapshot implementation.

## 1. Current profile inventory

| Profile | Current use | Status |
|---|---|---|
| `alpha3g.local-json.v1` | `StateOverlay` / Integrate value hashes | Approved local legacy profile for existing Category B artifacts |
| `alpha3g.integrate-path.v1` | Integrate write-set paths (`/env/*`, `/memory/*`) | Approved local path profile |
| `stable-canonical.v1` | Stable value serialization core, opt-in StateOverlay profile, and opt-in Integrate hash/event profile | Implemented in `synapse/canonical_values.py`; exposed through `synapse/canonical_service.py`; integrated into `StateOverlay` and Integrate only behind explicit profile selectors |

Existing Category B artifacts remain valid under their recorded profile. Migration
must be explicit; silent reinterpretation of old hashes as `stable-canonical.v1`
is forbidden.

---

## 2. General migration gates

Before any subsystem moves to `stable-canonical.v1`, the patch must demonstrate:

- [ ] The subsystem declares source and target profile IDs in code and tests.
- [ ] Existing artifacts are either left untouched or migrated through an explicit
      compatibility event / migration marker.
- [ ] Hash inputs are byte-for-byte reproducible under `canonical_json_bytes()`.
- [ ] Unsupported values fail closed with `CanonicalSerializationError`.
- [ ] Tests include both accepted values and rejected values for the subsystem.
- [ ] No runtime path silently falls back from `stable-canonical.v1` to an
      Alpha3g local profile.
- [ ] Existing golden fixtures remain readable under their original profile.

---

## 3. StateOverlay migration readiness

`StateOverlay` currently uses an Alpha3g local JSON subset. A future migration
patch must prove:

- [x] `StateOverlay.canonical_hash()` can be profile-selected without changing
      legacy artifact interpretation; default remains `alpha3g.local-json.v1`.
- [x] Stable-profile `WriteSetEntry` serialization explicitly includes
      `value_profile`; legacy entries preserve their historical shape.
- [x] `bytes`, `set`, large `int`, finite float, and Unicode edge cases produce
      stable hashes under `stable-canonical.v1` in SI1/SI2/SI4 tests.
- [x] Agent instances, host objects, functions, closures, and runtime handles are
      rejected before entering write-sets unless a later approved descriptor RFC
      authorizes them.
- [x] Empty `WriteSet` behavior remains deterministic under the legacy default and
      stable opt-in profile.
- [x] P0.4.6 service drift analysis and P0.4.7 real-fixture baseline classify
      migration risk before any profile switch is attempted.
- [x] P0.4.8 StateOverlay migration uses an explicit profile selector; hard
      switching from `alpha3g.local-json.v1` to `stable-canonical.v1` remains
      forbidden.

---

## 4. Integrate migration readiness

Integrate v1 is currently Category B under approved local profiles. Migration to
`stable-canonical.v1` must not retroactively invalidate P0.3.x artifacts.

Current Integrate migration gate after P0.4.10:

```text
Integrate hash path migration: COMPLETED (flagged)
Evidence: P0.4.9 drift baseline analyzed current Integrate Category B
         hash/event-path payload fragments.
Observed: 28 / 28 payload fragments classified as `drift_category = none`;
          breaking drift: 0; rejected payloads: 0; unexplained drift: 0.
Report: docs/INTEGRATE-MIGRATION-DRIFT-REPORT.md
P0.4.10: explicit `Interpreter.integrate_hash_profile` selector implemented
P0.4.10: legacy default `alpha3g.local-json.v1` preserved
P0.4.10: `stable-canonical.v1` opt-in covered by dual-profile LIVE/REPLAY tests
Hard switch: still forbidden without separate approval
```

The migration is complete in flagged mode only. It does not rewrite existing
recorded artifacts, change the default profile, or authorize CVM/opcode,
canonical time, deterministic ID, FunctionDescriptor, or AgentSnapshot work.

Integrate migration gates:

- [x] `integrate_committed` records the hash profile for stable-profile events
      using `hash_profile: "stable-canonical.v1"`; legacy events preserve their
      historical shape.
- [x] Stable-profile `WriteSetEntry` serialization records
      `value_profile: "stable-canonical.v1"`; legacy entries preserve their
      historical shape.
- [x] REPLAY reads the recorded event profile and rejects unknown profiles
      fail-closed.
- [x] Existing `integrate_committed` / `integrate_aborted` golden artifacts remain
      replayable under their recorded Alpha3g local profiles.
- [x] New stable-profile tests prove LIVE event emission, body-skip REPLAY, hash
      verification, abort replay, and write-set application using
      `stable-canonical.v1`.
- [ ] INT-05 genesis state hash alignment is explicitly handled or remains a
      documented blocker for Strict Layer 1.
- [ ] INT-06 durable crash-resume idempotency remains out of scope unless the
      migration patch also updates the replay checkpoint contract.
- [x] Hard switch remains forbidden; legacy default is preserved until a
      separate approval explicitly changes it.

---

## 5. Dream migration readiness

Dream replay remains Category B and is not Strict Layer 1 eligible under A2.
Stable canonical migration must respect the existing dream replay contract:

- [ ] Existing dream golden fixtures are not rewritten silently.
- [ ] Any future dream `state_delta` / `subtrace` format declares its stable
      canonical value profile.
- [ ] Closure/function captures remain rejected unless an approved descriptor
      contract exists.
- [ ] Builtins with side effects or nondeterminism remain blocked unless recorded
      and consumed through an approved event contract.

---

## 6. Function / agent boundaries

`FunctionDescriptor` and `AgentSnapshot` are not implemented in SI1/SI2.
Migration patches must not smuggle these values through generic object support.

- [ ] Functions, closures, bound methods, and native callables remain rejected.
- [ ] Agent instances remain rejected until `AgentSnapshot` is approved and
      implemented.
- [ ] Any future descriptor includes profile ID, schema version, and replay-safe
      binding rules.

---



### 6.1 FunctionDescriptor prerequisite status — P0.5.2.3

`RFC-FUNCTION-DESCRIPTOR.md` is now **APPROVED v1.0** after the Alpha3g
P0.5.2.3 structured team vote. This satisfies the FunctionDescriptor prerequisite
for the Agent Canonicalization AGENT-02 dependency at the specification level.

```text
Prerequisite: RFC-FUNCTION-DESCRIPTOR v1.0
Status: SATISFIED
Blocking for: Agent RFC AGENT-02 closure / Agent Canonicalization verification track
Satisfied by: RFC-FUNCTION-DESCRIPTOR v1.0 APPROVED (P0.5.2.3)
Agent RFC gate: READY FOR INDEPENDENT VERIFICATION (P0.5.4)
Runtime authorization: not granted by this checklist entry
```

Deferred FunctionDescriptor gates remain visible and must be closed before any
runtime implementation of a FunctionDescriptor registry, schema resolver, or
callable descriptor enforcement path:

| Deferred gate | Status | Must close before | Owner |
|---|---|---|---|
| FUNC-03 dependency manifest taxonomy / cryptographic pinning runtime policy | DEFERRED | FunctionDescriptor runtime core | TBD |
| FUNC-04 schema evolution / compatibility registry behavior | DEFERRED | FunctionDescriptor runtime core | TBD |

Approval of the RFC does not authorize runtime code, AgentSnapshot deployment,
CVM/opcode changes, function registry implementation, or executable identity v2.

### 6.2 Agent RFC verification readiness — P0.5.3

P0.5.3 synchronizes `RFC-AGENT-CANONICALIZATION.md` and its review notes with
the approved FunctionDescriptor prerequisite.

```text
Gate: Agent RFC verification
Status: COMPLETED — APPROVAL-CANDIDATE
Reason: AGENT-01, AGENT-02, and AGENT-03 independently VERIFIED in P0.5.4
Next patch: P0.5.5 final team vote / Agent RFC approval
Runtime authorization: not granted
```

P0.5.4 independently verified AGENT-01, AGENT-02, and AGENT-03 and moved
`RFC-AGENT-CANONICALIZATION.md` to `APPROVAL-CANDIDATE v0.4-AC`. AgentSnapshot
runtime remains locked until final RFC approval and separate runtime planning.


### 6.3 Agent RFC approval status — P0.5.5

`RFC-AGENT-CANONICALIZATION.md` is now **APPROVED v1.0** after the Alpha3g
P0.5.5 structured team vote. This satisfies the Agent Canonicalization
specification prerequisite for future AgentSnapshot runtime planning.

```text
Prerequisite: RFC-AGENT-CANONICALIZATION v1.0
Status: APPROVED
Blocking for: AgentSnapshot runtime planning / Agent canonicalization implementation track
Satisfied by: RFC-AGENT-CANONICALIZATION v1.0 APPROVED (P0.5.5)
Runtime authorization: not granted by this checklist entry
Next gate: scoped AgentSnapshot runtime planning and drift/audit patch
```

Deferred Agent RFC gates remain visible and must be closed before any runtime
implementation that depends on them:

| Deferred gate | Status | Must close before | Owner |
|---|---|---|---|
| AGENT-04 capability grant policy linkage | DEFERRED | Capability grant runtime enforcement beyond v1 minimum attenuation | TBD |
| AGENT-05 CVM visibility acceptance criteria | DEFERRED | Any CVM opcode consumes AgentSnapshot data | TBD |
| AGENT-06 provider/model descriptor drift table | DEFERRED | Provider/model descriptor runtime compatibility | TBD |
| AGENT-07 AgentRuntime compatibility migration plan | DEFERRED | `AgentRuntime.to_dict()` / `from_dict()` migration | TBD |
| AGENT-08 subagent snapshot boundary | DEFERRED | Subagent snapshot runtime | TBD |
| AGENT-11 schema version registry | DEFERRED | AgentSnapshot runtime deployment | TBD |

Approval of the RFC does not authorize runtime code, AgentSnapshot deployment,
CVM/opcode changes, function registry implementation, or golden fixture rewrites.

### 6.4 AgentSnapshot runtime planning status — P0.5.6

P0.5.6 adds the scoped AgentSnapshot runtime planning and field-audit baseline
after both prerequisite RFCs reached `APPROVED v1.0`. This satisfies the
planning prerequisite for a read-only drift/audit patch, but does not authorize
runtime implementation.

```text
Gate: AgentSnapshot runtime planning
Status: COMPLETED — READY FOR READ-ONLY DRIFT AUDIT
Planning artifact: docs/AGENTSNAPSHOT-RUNTIME-PLAN.md
Field audit artifact: docs/AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md
Next patch: P0.5.7 AgentSnapshot Runtime Drift Report
Runtime authorization: not granted
Standalone schema/value core authorization: blocked until drift report GO
```

Immediate AgentSnapshot runtime blockers remain visible:

| Gate | Status | Must close before | Owner |
|---|---|---|---|
| AGENT-07 legacy `AgentRuntime.to_dict()` / `from_dict()` migration plan | ACTIVE | any replacement of legacy agent serialization | TBD |
| AGENT-11 schema version registry | ACTIVE | AgentSnapshot runtime deployment | TBD |
| AGENT-04 capability grant policy linkage | DEFERRED | full capability enforcement beyond v1 minimum attenuation | TBD |
| AGENT-05 CVM visibility acceptance criteria | DEFERRED | any CVM opcode consumes AgentSnapshot data | TBD |
| AGENT-06 provider/model descriptor drift table | DEFERRED | provider/model descriptor runtime compatibility | TBD |
| AGENT-08 subagent snapshot boundary | DEFERRED | subagent snapshot runtime | TBD |

P0.5.7 must be read-only and must produce a GO/NO-GO report before any
AgentSnapshot schema/value core patch is authorized. Existing `AgentRuntime`
legacy serialization remains unchanged until a separate flagged migration patch.


---

## 7. Canonical time and identity boundaries

SI1/SI2 do not implement canonical time or deterministic identity generation.
Future patches must satisfy RFC-STABLE-CANONICAL-IDENTITY v1.0:

- [ ] `runtime.get_canonical_time()` uses either recorded-and-consumed `time_read`
      events or an approved deterministic logical clock.
- [ ] Host wall clocks, host monotonic clocks, and implicit process-local counters
      remain forbidden.
- [ ] Deterministic identity generation declares collision domain, seed material,
      profile ID, and replay behavior.

---

## 8. Release checklist for first integration patch

The first patch that integrates `stable-canonical.v1` into any runtime path must
include:

- [ ] scoped design note naming exactly which subsystem migrates;
- [ ] before/after profile table;
- [ ] tests for accepted values, rejected values, profile mismatch, and legacy
      artifact compatibility;
- [ ] full test suite result;
- [ ] changelog and planning-gate entries;
- [ ] explicit DENY list for unrelated subsystems.


### 6.4 AgentSnapshot gate closure status — P0.5.7

P0.5.7 resolves the immediate sequencing question before standalone AgentSnapshot
code. It is a documentation + read-only canary patch, not a runtime patch.

```text
Gate: AgentSnapshot standalone schema/value core
Status: READY FOR STANDALONE CORE (P0.5.8)
Evidence: docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md
Canary: tests/test_agentsnapshot_canary_p057.py
Runtime authorization: standalone value objects only; no integration
```

Gate interpretation after P0.5.7:

| Gate | Status | Blocks standalone core? | Blocks deployment / integration? |
|---|---|---:|---:|
| FUNC-03 dependency manifest taxonomy | PARTIAL — not required for standalone AgentSnapshot carrying approved descriptor refs | No | Yes |
| FUNC-04 schema evolution registry | PARTIAL — local fail-closed allowlist required | No, if implemented | Yes |
| AGENT-11 schema registry | PARTIAL — local AgentSnapshot schema allowlist required | No, if implemented | Yes |
| AGENT-07 legacy serialization migration | ACTIVE | No | Yes |

The minimum P0.5.8 local allowlist is:

```text
alpha3g.agent_snapshot.v1
alpha3g.agent_definition_ref.v1
alpha3g.agent_id.v1
alpha3g.memory_ref.v1
alpha3g.memory_space_id.v1
alpha3g.capability_grant.v1
alpha3g.function_descriptor.v1
stable-canonical.v1
```

P0.5.8 may create standalone AgentSnapshot schema/value code only if unknown
schema/profile values fail closed and legacy `AgentRuntime.to_dict()` remains
unchanged and non-canonical.

### 6.5 AgentSnapshot standalone schema/value core — P0.5.8

P0.5.8 implements the first standalone AgentSnapshot code under the P0.5.7
local fail-closed allowlist. It is a value-core patch only, not deployment or
integration.

```text
Gate: AgentSnapshot standalone schema/value core
Status: COMPLETED (standalone only)
Evidence: synapse/agent_snapshot.py, tests/test_agentsnapshot_core_p058.py
Runtime integration: NOT AUTHORIZED
```

Implemented standalone constraints:

- [x] Unknown schema/profile ids fail closed with `UnknownSchemaVersionError`.
- [x] AgentSnapshot serializes only the approved v1 allowlist fields.
- [x] Runtime-envelope fields such as `tools`, `llm`, `env`, `memory`, mailbox,
      scheduler, sockets, files, and process/thread ids are rejected.
- [x] `memory_ref.access_mode` is limited to `read`, `write`, or `read-write`.
- [x] Snapshot hashing uses `stable-canonical.v1` over the canonical payload.
- [x] Legacy `AgentRuntime.to_dict()` remains unchanged and non-canonical.

Still blocked after P0.5.8:

| Gate | Status | Blocks |
|---|---|---|
| AGENT-07 legacy serialization migration | ACTIVE | any adapter or replacement of `AgentRuntime.to_dict()` |
| AGENT-11 central schema/profile registry | ACTIVE for deployment | actor/interpreter integration, production registry behavior |
| FUNC-03 dependency manifest taxonomy | DEFERRED | FunctionDescriptor runtime registry and dependency validation |
| FUNC-04 schema evolution registry | DEFERRED | schema/profile compatibility registry |
| AGENT-05 CVM visibility acceptance criteria | DEFERRED | any CVM opcode consuming AgentSnapshot data |

Next authorized patch: P0.5.9 standalone AgentSnapshot hardening / edge-case
coverage. Integration, adapters, and legacy migration remain locked.


### 6.6 AgentSnapshot standalone hardening — P0.5.9

P0.5.9 hardens the SA1 standalone core against edge cases. It does not extend
the public surface or open any integration path.

```text
Gate: AgentSnapshot standalone hardening
Status: COMPLETED (standalone only)
Evidence: synapse/agent_snapshot.py (point fixes), tests/test_agentsnapshot_hardening_p059.py (35 tests)
Runtime integration: NOT AUTHORIZED
FunctionDescriptorRef value object: NOT IMPLEMENTED (blocked by FUNC-03)
```

Hardening invariants now enforced:

- [x] `snapshot_hash()` stable under external mutation of any mapping or list
      passed into `config`, `canonical_fields`, or `model_ref` (including nested).
- [x] Duplicate `memory_refs` entries fail closed.
- [x] Conflicting `access_mode` on the same `(memory_space_id, memory_key)`
      address fails closed.
- [x] Duplicate `capability_grants` per `tool_namespace` fail closed.
- [x] Whitespace-only `memory_key` fails closed.
- [x] `AgentIdSeed.alias` normalizes `""` and whitespace-only strings to `None`,
      eliminating the identity-drift surface.
- [x] Round-trip validator (`validate_agent_snapshot_payload`) enforces the
      same invariants on payloads loaded from JSON.

Next gate: legacy `AgentRuntime.to_dict()` drift analysis (P0.5.10, AS2-prep).


### 6.7 AgentRuntime.to_dict() drift analysis — P0.5.10 (AS2-prep)

P0.5.10 captures and classifies the actual `AgentRuntime.to_dict()` shape
against the canonical AgentSnapshot v1 allowlist. It is documentation +
read-only test only. No adapter, no profile selector, no runtime changes.

```text
Gate: legacy AgentRuntime.to_dict() drift analysis (AS2-prep)
Status: COMPLETED
Evidence: docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md
Canary: tests/test_agentruntime_todict_drift_p0510.py (26 read-only tests)
Runtime authorization: none — adapter design RFC may be proposed only after
                       explicit team vote
```

Probed legacy shape (invariant across 9 configurations):

```text
top-level: {name, model, trust_level, trust_scope, memory}
memory:    {short_term, long_term, capacity}
```

Field classification:

| Field | Status |
|---|---|
| `name` | `requires_transform` |
| `model` | `requires_transform` |
| `trust_level` | `migrates_as_is` |
| `trust_scope` | `migrates_as_is` |
| `memory.short_term` | `requires_transform` |
| `memory.long_term` | `requires_transform` |
| `memory.capacity` | `requires_transform` |
| `memory_config` (constructor) | `excluded_from_canonical` until AS2 clarifies |

Canonical AgentSnapshot v1 fields absent from legacy:

```text
agent_id, definition_ref, config, canonical_fields, memory_refs,
model_ref, capability_grants, profile, schema_version
```

AS2 adapter design risks R1..R7 recorded in drift report §9. Subagent /
fracture path has no current `to_dict()` surface; AGENT-08 remains DEFERRED.

Next gate: P0.6.x AS2 flagged adapter RFC (design only).


### 6.8 AgentSnapshot pre-RFC gate closure — P0.5.11

P0.5.11 partially closes `AGENT-06` and `AGENT-08` only to the extent required
to open the AS2 flagged adapter RFC after a separate team vote.

| Gate | P0.5.11 status | Sufficient for AS2 RFC? | Still blocks deployment/runtime? |
|---|---|---:|---:|
| `AGENT-06` provider/model descriptor | PARTIAL — `model_ref.v1` design boundary with allowlisted `provider_namespace` | Yes | Yes |
| `AGENT-08` subagent snapshot boundary | PARTIAL — subagents explicitly out of AS2 v1 scope | Yes | Yes |

`model_ref.v1` minimum fields for AS2 design:

```text
provider_namespace: mock | anthropic | openai | local | custom
model_id
model_version
capability_profile_hash
schema_version: alpha3g.model_ref.v1
profile: stable-canonical.v1
```

Out of scope for P0.5.11 and AS2 v1:

```text
endpoint_class
deterministic_mode_hash
provider drift/deprecation table
subagent_snapshot_ref
subagent runtime identity
adapter code
central registry
```

Next gate: explicit team vote to open P0.6.0 AS2 flagged adapter RFC (design
only). Adapter implementation remains not authorized.



### 6.9 AS2 flagged adapter RFC opened — P0.6.0

P0.6.0 opens the AS2 flagged adapter RFC as a design-only patch.

| Gate | P0.6.0 status | Blocking for |
|---|---|---|
| AS2 RFC draft | OPENED | Adapter implementation review |
| AS2-01 identity source contract | OPEN | Adapter approval |
| AS2-02 model_ref resolver behavior | OPEN | Adapter approval |
| AS2-03 memory mapping strategy | OPEN | Adapter approval |
| AS2-04 capability grant sourcing | OPEN | Adapter approval |
| AS2-05 envelope separation | OPEN | Adapter approval |
| AS2 runtime implementation | LOCKED | Requires approved AS2 RFC and separate implementation vote |

Readiness statement:

```text
P0.6.0 authorizes design review only.
No runtime adapter, profile selector, Environment emission, AgentRuntime.to_dict() migration, or golden fixture changes are authorized.
```

### 6.10 AS2 RFC hardening and blocker closure — P0.6.1

P0.6.1 revises the AS2 flagged adapter RFC as a hardened, documentation-only
blocker-closure patch. It does not authorize runtime implementation, tests,
adapter code, profile selectors, legacy serialization changes, or fixture
rewrites.

| Gate | P0.6.1 status | Blocking for |
|---|---|---|
| AS2-01 identity source contract | RESOLVED — explicit complete-or-absent AdapterIdentityContext only | P0.6.2 verification |
| AS2-02 model mapping | RESOLVED — immutable append-only StaticModelRegistry; unknown model fails closed | P0.6.2 verification |
| AS2-03 memory mapping | RESOLVED — two-phase externalization, per-ref memory-space validation, no rewrite/filter/repair | P0.6.2 verification |
| AS2-04 capability grants | RESOLVED — explicit CapabilityGrantSource only; zero live tool introspection | P0.6.2 verification |
| AS2-05 envelope/audit separation | RESOLVED — canonical envelope isolation; audit_context excluded from state hash | P0.6.2 verification |
| AS2-06 schema/profile registry boundary | OPEN | AS2 approval / runtime deployment |
| AS2-07 memory capacity mapping | OPEN | AS2 implementation design |
| AS2-10 Environment dual-emission boundary | OPEN | Environment integration |

P0.6.1 explicitly records `AdapterDerivationRecord` as provenance metadata, not
logical state. It also records `AdapterMemorySpaceMismatchError` and the strict
memory-space policy version requirement.

Next gate: P0.6.2 independent verification of AS2-01..AS2-05. No adapter
implementation is authorized until AS2 RFC verification, final approval, and a
separate implementation-planning gate complete.

## Alpha3g P0.6.2 — AS2 Independent Verification Matrix

- **Status:** COMPLETED — doc-only independent verification of AS2 RFC blocker resolutions.
- **Scope:** documentation only. No `synapse/`, no `tests/`, no runtime code, no adapter implementation, no profile selector.
- **Verification artifact:** `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.
- **Verified findings:** AS2-01, AS2-02, AS2-03, AS2-04, and AS2-05 moved from `RESOLVED` to `VERIFIED`.
- **Watch items:** AdapterIdentityContext presence markers and historical superseded wording remain non-blocking watch items for P0.6.4 implementation planning.
- **Remaining blockers:** AS2-06 schema/profile registry boundary, AS2-07 memory capacity mapping, and AS2-10 Environment dual-emission boundary remain open. AS2-08 remains out of AS2 v1 scope.
- **Next gate:** P0.6.3 final approval. Adapter implementation remains NOT AUTHORIZED.

## Alpha3g P0.6.3 — AS2 RFC Final Approval

- **Status:** COMPLETED — AS2 RFC approved as v1.0 design baseline.
- **Scope:** documentation/process only. No `synapse/`, no `tests/`, no runtime code, no adapter implementation, no profile selector, no legacy serialization changes.
- **Approved artifact:** `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md` v1.0.
- **Vote record:** recorded in `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.
- **Approved scope:** design contract only — pure deterministic projection, explicit input boundaries, two-phase memory protocol, typed fail-closed taxonomy, and AdapterDerivationRecord concept.
- **Not authorized:** AS2 adapter implementation, `AgentRuntime.to_dict()` migration, `Environment._json_safe()` migration, runtime profile selector, FunctionDescriptor runtime registry, central schema registry, subagent canonicalization, cross-agent memory sharing.
- **Next gate:** P0.6.4 implementation planning / drift harness design. Adapter implementation remains LOCKED until an explicit P0.6.5 implementation vote.

| Gate | P0.6.3 status | Blocking for |
|---|---|---|
| AS2 RFC v1.0 | APPROVED | P0.6.4 implementation planning |
| AS2 adapter implementation | LOCKED | Requires accepted P0.6.4 plan and explicit P0.6.5 vote |
| Legacy serialization migration | LOCKED | Separate migration authorization |
| Environment dual emission | LOCKED | Separate Environment boundary gate |

## Alpha3g P0.6.4 — AS2 implementation planning / fixture harness

- **Status:** COMPLETED — docs + test-only fixture/invariant harness.
- **Scope:** no runtime code, no adapter implementation, no public AS2 API, no legacy serialization changes.
- **Fixture corpus:** `tests/fixtures/as2/` established with 11 cases.
- **Harness:** `tests/test_as2_fixture_matrix_p064.py` validates fixture data only.

| Gate | P0.6.4 status | Blocking for |
|---|---|---|
| AS2 implementation plan | COMPLETED | P0.6.5 vote |
| AS2 fixture corpus spec | COMPLETED | P0.6.5 fixture oracle |
| AS2 drift/invariant harness design | COMPLETED | P0.6.5 implementation guardrails |
| AS2 fixture matrix tests | COMPLETED — data-only | P0.6.5 adapter skeleton validation |
| AS2 adapter implementation | LOCKED | Requires explicit P0.6.5 team vote |
| Legacy runtime migration | LOCKED | Separate migration authorization |

P0.6.4 does not authorize adapter code. The next gate is a structured review and
explicit vote to open P0.6.5 as a flagged adapter skeleton.

## Alpha3g P0.6.5 — AS2 flagged adapter skeleton

- **Status:** COMPLETED — skeleton-only adapter boundary.
- **Scope:** `synapse/agent_snapshot_adapter.py`, `tests/test_as2_adapter_skeleton_p065.py`, and process-document updates.
- **Implemented:** local AS2 error hierarchy, explicit input value skeletons, `validate_as2_inputs(...)`, and focused validators for identity/model/memory/capability sources.
- **Verified by tests:** fixture expected-error strings map to real leaf exception classes; hierarchy is grouped as input/mapping/integrity; positive P0.6.4 fixture validates without projection; negative fixtures raise their expected skeleton errors; module does not import legacy runtime or ambient-authority modules; `to_agent_snapshot()` remains absent.

| Gate | P0.6.5 status | Blocking for |
|---|---|---|
| AS2 skeleton module | COMPLETED | P0.6.6 review |
| Typed AS2 error hierarchy | COMPLETED — local to adapter module | P0.6.6 projection planning |
| Validation-only boundary | COMPLETED | P0.6.6 fixture-driven projection vote |
| `to_agent_snapshot()` | LOCKED / ABSENT | Requires separate P0.6.6+ authorization |
| AgentSnapshot construction | LOCKED | Requires projection authorization |
| Legacy runtime integration | LOCKED | Separate migration authorization |
| Environment dual emission | LOCKED | Separate Environment boundary gate |
| Real provider registry | LOCKED | Future registry gate |
| FunctionDescriptor runtime registry | LOCKED | FUNC-03/04 runtime gates |

P0.6.5 does not authorize runtime integration or complete adapter projection. The next step must be structured review of the skeleton and explicit team authorization before any fixture-driven minimal projection patch.


## Alpha3g P0.6.5.1 — AS2 skeleton test skip cleanup

| Gate | P0.6.5.1 status | Blocking for |
|---|---|---|
| P0.6.5 artificial test skip | CLOSED | Test-report hygiene |
| AS2 skeleton semantics | UNCHANGED | P0.6.6 review |
| Runtime integration | LOCKED | Requires explicit future vote |
| Projection logic / `to_agent_snapshot()` | LOCKED | Requires explicit future vote |

P0.6.5.1 is a test-cleanup patch only. It does not alter `synapse/`, the AS2 skeleton module, runtime wiring, fixture schemas, or migration gates.


### 6.10 AS2 validation hardening — P0.6.6

P0.6.6 hardens the P0.6.5 AS2 skeleton against edge cases identified by
adversarial probing. Validation-only patch by team Vote A.

```text
Gate: AS2 validation hardening (Vote A)
Status: COMPLETED
Evidence: synapse/agent_snapshot_adapter.py (point fixes),
          tests/test_as2_adapter_validation_p066.py (83 tests),
          RFC §17 (name reservation), RFC §18 (R8 record),
          drift report §9 (R8 entry)
Projection authorization: none
Legacy bridge authorization: none
Feature flag authorization: none (deferred to P0.6.7+)
AS2ViolationContext: deferred to P0.6.7 to avoid surface expansion
```

Hardening invariants enforced at the AS2 boundary:

- [x] Whitespace-only `alias` fails closed (identity drift defense).
- [x] Negative or `bool` `identity_version` fails closed.
- [x] Duplicate `legacy_model` in `StaticModelRegistry` fails closed.
- [x] Duplicate `memory_refs` and conflicting access modes fail closed
      (parity with P0.5.9 standalone-core invariants).
- [x] Duplicate `tool_namespace` in `CapabilityGrantSource` fails closed.
- [x] Any legacy `__type__` envelope marker fails closed (RFC §6.3 fully
      covered, not only the `"agent"` value).
- [x] Subagent runtime graph presence (any value, including `{}` and `[]`)
      fails closed (RFC §11).
- [x] Inline memory payload presence fails closed (RFC §7.1 host-prep boundary).
- [x] Exact-leaf-class assertion: every negative fixture and every
      adversarial case raises exactly the declared leaf class, never a
      generic base.

R8 (AS2 vs core `CapabilityGrant` shape gap) is resolved for P0.6.7 by
R8-A deterministic canonical projection. R8-B (core schema bump) and R8-C
(AgentSnapshot v2) remain future options requiring schema-migration approval.

Next gate: P0.6.7 fixture-driven minimal standalone projection.


### 6.11 AS2 minimal standalone projection — P0.6.7

```text
Gate: AS2 fixture-driven minimal standalone projection
Status: COMPLETED
Evidence: synapse/agent_snapshot_adapter.py,
          tests/test_as2_adapter_projection_p067.py,
          updated positive AS2 fixture,
          RFC §19 R8/R9 projection record
Legacy bridge authorization: none
Feature flag authorization: none
AdapterDerivationRecord real hashes: deferred
```

- [x] `project_validated_as2_inputs(...)` implemented.
- [x] `to_agent_snapshot()` remains absent.
- [x] Projection uses real standalone `AgentSnapshot` core constructor.
- [x] R8-A canonical projection preserves `tool_namespace` and derives
      deterministic `scope_hash`.
- [x] R9 closed via explicit `AdapterDefinitionSource`.
- [x] Negative fixtures remain validation failures before projection.
- [x] Legacy runtime bridge remains locked.

Next gate: P0.6.8 — derivation-record hash computation and Merkle-transparent
audit design/implementation under separate authorization.


### 6.12 AS2 AdapterDerivationRecord hashing — P0.6.8

```text
Gate: AS2 AdapterDerivationRecord hashing / Merkle-transparent audit
Status: COMPLETED
Evidence: synapse/agent_snapshot_adapter.py,
          tests/test_as2_adapter_derivation_p068.py,
          positive AS2 fixture expected_derivation_record hashes,
          RFC §20 AdapterDerivationRecord hashing record
Legacy bridge authorization: none
Feature flag authorization: none
AS2ViolationContext: deferred
```

- [x] Five required input hashes are computed with `stable_canonical_hash`.
- [x] Positive fixture expected hashes match computed hashes.
- [x] Repeated projections produce identical derivation records.
- [x] `AgentSnapshot.snapshot_hash()` is independent of AdapterDerivationRecord content.
- [x] R8-A capability-grant projection remains unchanged.
- [x] Legacy bridge remains locked.

Next gate: structured team review before any bridge-design or diagnostic-enrichment patch.

### 6.13 AS2 forensic error attribution — P0.6.9

```text
Gate: AS2ViolationContext / forensic error attribution
Status: COMPLETED
Evidence: synapse/agent_snapshot_adapter.py,
          tests/test_as2_violation_context_p069.py,
          all negative AS2 fixtures expected_error_context blocks,
          RFC §21 AS2ViolationContext record
Legacy bridge authorization: none
Feature flag authorization: none
Projection semantics changes: none
Derivation hash changes: none
```

- [x] `AS2ViolationContext` added as immutable failure-path context.
- [x] Existing AS2 leaf exception class names are preserved.
- [x] Negative fixtures declare expected error context.
- [x] RFC references are validated by strict section-citation format.
- [x] Context is not mixed into `AdapterDerivationRecord`.
- [x] `AgentSnapshot.snapshot_hash()` remains unaffected.
- [x] Legacy bridge remains locked.

Next gate: structured team review before bridge-design or additional diagnostic
hardening.


### 6.14 AS2 legacy bridge design — P0.6.10

```text
Gate: AS2 Legacy Bridge Design RFC / Host Pre-Stage Protocol
Status: COMPLETED
Evidence: docs/AS2-LEGACY-BRIDGE-DESIGN.md
Runtime code authorization: none
Bridge fixture authorization: none
Feature flag implementation: none
```

- [x] Standalone AS2 path declared closed for current cycle.
- [x] Airlock Pattern documented.
- [x] Host Pre-Stage Protocol documented with Step 0 and Steps 1-10.
- [x] Host owns memory externalization and pre-stage I/O.
- [x] Bridge consumes explicit AS2 inputs and does not write storage/CAS.
- [x] `AgentRuntime.to_dict()` remains forbidden as canonical source.
- [x] Live tools introspection remains forbidden.
- [x] Forbidden Reads Registry added.
- [x] `AS2_HOST_PRESTAGE_BRIDGE_ENABLED` reserved for future implementation only.
- [x] P0.6.11/P0.6.12 staging documented.
- [x] `synapse/` and `tests/` remain unchanged.

Next gate: P0.6.11 Bridge Fixture Corpus / Host Pre-Stage Harness, under separate team authorization.


### 6.15 AS2 bridge fixture corpus — P0.6.11

```text
Gate: AS2 Bridge Fixture Corpus / Host Pre-Stage Harness
Status: COMPLETED
Evidence: tests/fixtures/as2_bridge/, tests/test_as2_bridge_harness_p0611.py
Runtime code authorization: none
Bridge implementation authorization: none
Feature flag implementation: none
```

- [x] Bridge fixture schema `alpha3g.as2_bridge_fixture.v1` introduced.
- [x] 4 positive bridge fixtures added.
- [x] 12 negative bridge fixtures added.
- [x] Positive bridge fixtures validate `expected_as2_inputs` through `validate_as2_inputs(...)`.
- [x] Bridge harness does not call `project_validated_as2_inputs(...)`.
- [x] Forbidden Reads Registry coverage is 100%.
- [x] Host Pre-Stage Protocol Step 0-10 coverage is 100%.
- [x] Bridge-specific expected errors remain string identifiers.
- [x] `legacy_agent_runtime_to_dict.model` naming debt is documented as synthetic selector usage only.
- [x] `synapse/` remains unchanged.

Next gate: P0.6.12 bridge implementation authorization requires separate team vote.

## P0.6.12 readiness entry — Host Pre-Stage Bridge Skeleton

Status: completed for bridge skeleton boundary.

Verified items:

```text
[OK] New bridge module exists outside standalone AS2 adapter.
[OK] Bridge module has no AgentRuntime / Environment imports.
[OK] Bridge module is disabled by default through local flag.
[OK] Public bridge entrypoint accepts Host Pre-Stage mapping, not runtime objects.
[OK] PreparedAS2Inputs validates through standalone validate_as2_inputs(...).
[OK] Bridge tests use P0.6.11 fixture corpus.
[OK] Negative bridge fixtures raise typed bridge-local errors.
[OK] project_validated_as2_inputs(...) remains outside bridge execution.
```

Still locked:

```text
runtime wiring
AgentRuntime.to_dict() migration
Environment._json_safe() migration
runtime profile selector
Integrate / Dream / CVM integration
real provider registry
FunctionDescriptor runtime registry
golden fixture migration
```


---

## P0.6.13 readiness entry — Host Pre-Stage Bridge Hardening

Bridge readiness strengthened:

- [x] Unknown Host Pre-Stage payload fields rejected fail-closed.
- [x] Unknown nested AS2 boundary fields rejected where the bridge owns the field contract.
- [x] Missing or `null` required Host sources mapped to source-specific `HostPreStageMissing*Error`.
- [x] Present-but-empty or wrong-shaped Host sources mapped to `HostPreStageInvalidAS2InputsError`.
- [x] `PreparedAS2Inputs` isolated from external payload mutation through defensive freezing and fresh validation kwargs.
- [x] Local bridge flag remains disabled by default.
- [x] No runtime feature-flag system, environment-variable guard, projection call, AgentSnapshot construction, or legacy import introduced.

Still blocked:

```text
runtime wiring
projection call inside bridge
AgentRuntime / Environment integration
legacy_agent_runtime_to_dict parameter rename
bridge fixture schema v2
production activation
```

---

## P0.6.14 readiness entry — Runtime Wiring Design RFC

P0.6.14 is completed as a doc-only runtime wiring design stage.

Design readiness strengthened:

- [x] Runtime Wiring Execution Graph defined.
- [x] Host Pre-Stage Responsibility Map defined.
- [x] Payload Key Classification Matrix defined.
- [x] Forbidden Reads Registry Cross-Check defined.
- [x] Projection Handoff Rule defined: Host/Pipeline calls projection, bridge does not.
- [x] Failure Handling Strategy defined for per-agent quarantine and systemic AS2 shutdown.
- [x] Feature Flag Placement documented without implementing a runtime flag system.
- [x] Strict Structural Validator Contract documented.
- [x] Runtime Acceptance Checklist defined.
- [x] Debt Register created for docstring drift, `model_selector`, `legacy_agent_runtime_to_dict`, and production/test key separation.

Still blocked:

```text
runtime wiring code
project_validated_as2_inputs(...) inside bridge
AgentSnapshot construction inside bridge
AgentRuntime / Environment imports
runtime feature flag system
CAS/storage I/O implementation
Integrate / Dream / CVM wiring
legacy_agent_runtime_to_dict rename
model_selector removal
production activation
```

Next gate requires separate team authorization.
