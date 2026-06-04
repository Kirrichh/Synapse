# AgentRuntime.to_dict() Drift Report

**Status:** READ-ONLY DRIFT ANALYSIS — Alpha3g P0.5.10 (AS2-prep)
**Version:** v0.1
**Patch:** P0.5.10 legacy `AgentRuntime.to_dict()` drift analysis
**Runtime scope authorized:** none — documentation + read-only canary/probe test only
**Depends on:** `AGENTSNAPSHOT-RUNTIME-PLAN.md`, `AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md`, `AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md`, `synapse/agent_snapshot.py`
**Output gate:** AS2 flagged adapter design (P0.6.x) may be proposed after this report and an explicit team vote.

P0.5.10 is the AS2-prep step from the approved staging (R4): a read-only
comparison of the actual `AgentRuntime.to_dict()` shape against the canonical
AgentSnapshot v1 allowlist established in P0.5.8 and hardened in P0.5.9.

This report does not implement an adapter, does not introduce a profile
selector, does not modify `synapse/builtins.py`, and does not authorize any
runtime migration. Its purpose is to give the team an evidence-based map of
the drift surface before the adapter RFC opens.

---

## 1. Probe methodology

The actual `AgentRuntime.to_dict()` shape was probed by instantiating
`AgentRuntime` in nine representative configurations and inspecting both the
top-level dict and the nested `memory` substructure:

```text
1. Minimal agent (constructor defaults only).
2. Full constructor (name, model, trust_level, trust_scope).
3. Agent with live memory content (short_term and long_term).
4. Agent with live tools registered.
5. Agent after .think() calls (LLM backend state).
6. Agent with .env assigned to a host object.
7. Agent with soulprint and identity_version attached by the interpreter.
8. Agent constructed with each memory_config value.
9. Round-trip AgentRuntime.from_dict(AgentRuntime.to_dict()) idempotence.
```

The probe runs without any runtime mutation outside the instances created in
the probe itself. No interpreter, actor runtime, CVM, or storage backend is
involved.

The probe lives in `tests/test_agentruntime_todict_drift_p0510.py` as a
read-only enforcement: any silent change to the legacy shape produced by
`AgentRuntime.to_dict()` will fail this test.

---

## 2. Observed legacy shape

`AgentRuntime.to_dict()` produces exactly five top-level fields in every
observed configuration:

```text
name           : str
model          : str
trust_level    : str        (default "medium")
trust_scope    : list[str]  (default [])
memory         : dict
                 ├── short_term : list
                 ├── long_term  : dict
                 └── capacity   : int (default 100)
```

The set of top-level keys is **invariant** across all nine probed
configurations. Live handles never leak into the dict: `tools`, `llm`, `env`,
`soulprint`, and `identity_version` are absent from `to_dict()` even when
those attributes exist on the live `AgentRuntime` instance. This matches the
P0.5.6 field audit and the P0.5.7 canary.

Constructor signature for reference:

```python
AgentRuntime(
    name: str,
    model: str,
    memory_config: Optional[str] = None,
    trust_level: Optional[str] = None,
    trust_scope: Optional[List[str]] = None,
)
```

`memory_config` is accepted by the constructor but does not influence the
serialized `memory.capacity` in the current implementation (`capacity` stays
at the hard-coded default of 100 for every probed `memory_config` value).
This is documented here as a drift surface to resolve in the AS2 adapter
design: either `memory_config` becomes a meaningful `config` field in the
canonical snapshot, or it is dropped.

---

## 3. Field-level drift classification

Each legacy field is classified against the AgentSnapshot v1 allowlist from
P0.5.8 (`agent_id`, `definition_ref`, `config`, `canonical_fields`,
`memory_refs`, `model_ref`, `capability_grants`, `profile`, `schema_version`).

| Legacy field | Type | Canonical destination | Status |
|---|---|---|---|
| `name` | `str` | `canonical_fields.name` (or alias on `AgentIdSeed`) | `requires_transform` |
| `model` | `str` | `model_ref` (descriptor object, not bare string) | `requires_transform` |
| `trust_level` | `str` | `config.trust_level` (allowlisted scalar) | `migrates_as_is` |
| `trust_scope` | `list[str]` | `config.trust_scope` (allowlisted array) | `migrates_as_is` |
| `memory.short_term` | `list` | `memory_refs[*]` (address-only) + recorded events | `requires_transform` |
| `memory.long_term` | `dict` | `memory_refs[*]` (address-only) | `requires_transform` |
| `memory.capacity` | `int` | `config.memory_capacity` or descriptor field | `requires_transform` |
| `memory_config` (constructor) | `str` | currently dead (no effect on `to_dict()`) | `excluded_from_canonical` until clarified by AS2 |

Status semantics (per acceptance criterion §2 of the P0.5.10 scope):

```text
migrates_as_is          field value transfers into the canonical allowlist
                        unchanged; only its parent container changes.

requires_transform      field value must be converted into a different
                        representation (e.g. raw string -> descriptor object,
                        memory graph dump -> address-only memory_refs).

legacy_only             field cannot be represented in canonical form and
                        must remain in the legacy serialization path.

excluded_from_canonical field must not appear in canonical output by design;
                        the adapter must drop it.
```

No legacy field is classified `legacy_only` in this report because every
field has a defined canonical destination (with the caveat that
`memory_config` is `excluded_from_canonical` only until AS2 clarifies its
intent).

---

## 4. Identity surface — what is NOT in legacy `to_dict()`

The canonical AgentSnapshot v1 allowlist requires fields that legacy
`to_dict()` does not produce:

```text
agent_id           absent — legacy uses `name` for identity, which is not
                   collision-safe. Canonical agent_id derives from
                   AgentIdSeed (parent_anchor, definition_hash, spawn_nonce,
                   alias, namespace) under stable-canonical.v1.

definition_ref     absent — legacy carries no AgentDefinitionRef. The
                   adapter must synthesize one or fail closed when the
                   four required sha256 hashes (interface, config,
                   capability, manifest) cannot be sourced.

capability_grants  absent — legacy never serializes tools. The adapter
                   must derive declarative grants from the registered
                   tool namespaces; it MUST NOT serialize live callables.

model_ref          absent — legacy carries only the bare model string. The
                   adapter must wrap it in a descriptor; AGENT-06
                   (provider/model drift table) is the upstream gate.

profile / schema_version  absent — canonical envelope fields. Adapter must
                   stamp `stable-canonical.v1` and
                   `alpha3g.agent_snapshot.v1`.
```

This asymmetry — legacy has fewer fields than canonical demands — is the
core of the AS2 design problem. The adapter cannot be a passive renamer.

---

## 5. Soulprint and identity_version drift

The probe confirmed that `soulprint` and `identity_version` attributes
attached to a live `AgentRuntime` by the interpreter (during `evolve self`,
`dream/integrate`, and identity transactions) **do not enter
`AgentRuntime.to_dict()`**. They live in interpreter state, in
`capture_agent_state()` rollback buffers, and in `execution_history` events,
but legacy serialization is blind to them.

Implications for AS2:

- The flagged adapter must decide whether canonical snapshots include
  identity state (soulprint, identity_version). If yes, it MUST source them
  from interpreter state, not from `to_dict()` — adding them to legacy
  serialization is out of scope and would mutate a Category-B-adjacent
  surface.
- A standalone adapter that consumes only `to_dict()` will produce
  canonical snapshots without identity, which is a deliberate omission and
  must be documented in the AS2 RFC as a known limitation.

Status: `legacy_only` for the legacy path; `excluded_from_canonical` for
the adapter unless AS2 explicitly defines an identity-sourcing rule from
interpreter state.

---

## 6. Adjacent legacy serialization paths

Two adjacent paths consume `AgentRuntime.to_dict()` and amplify legacy
shape. They are documented here as boundaries for AS2; this patch does not
test or modify them.

### 6.1 `Environment._json_safe(AgentRuntime)`

`Environment._json_safe` wraps an `AgentRuntime` instance as:

```python
{"__type__": "agent", "data": <AgentRuntime.to_dict()>}
```

This `__type__`/`data` envelope is a legacy serialization marker, not a
canonical AgentSnapshot envelope. AgentSnapshot v1 uses `type`,
`schema_version`, and `profile` instead. The adapter must not reuse the
`__type__` shape: producing both `__type__: "agent"` and
`schema_version: "alpha3g.agent_snapshot.v1"` in the same payload would
create profile ambiguity.

### 6.2 `Environment.to_dict().agents` / `.variables`

The current `Environment.to_dict()` produces:

```text
{
  "env_id": "<uuid>",         # runtime_envelope — must not enter canonical
  "variables": { ... },        # may contain `__type__: agent` wrappers
  "agents": { ... },           # name -> AgentRuntime.to_dict()
  "parent": <recursive>        # runtime_envelope only
}
```

`agents` and `variables[<key>]` both transitively expose
`AgentRuntime.to_dict()`. Any AS2 adapter that integrates at the
`Environment` boundary must decide whether to dual-emit (legacy + canonical)
or to gate canonical emission behind a profile flag set on `Environment`.

Both subsections are AS2 design input only. No test added.

---

## 7. Subagent / fracture path

`SubAgentDef` is an AST node, not a runtime object. The fracture path
constructs sub-agents transiently inside `fracture self { ... } consensus
... { ... }` blocks, collects their positions, and emits a single
consensus integration event. There is **no legacy serialization API** that
emits a per-sub-agent `to_dict()` payload today.

Implications for AS2:

- The adapter does not need to convert sub-agents from legacy form; there
  is no legacy form to convert.
- A future per-sub-agent AgentSnapshot variant (sub-snapshot or
  consensus-snapshot) is a separate design problem covered by AGENT-08
  (subagent snapshot boundary), which remains DEFERRED.

Status: subagent shape is **not currently a `to_dict()` drift surface**.
The adapter design must address it on its own terms after AGENT-08
closure, not as part of AS2.

---

## 8. Round-trip stability

The probe confirmed that
`AgentRuntime.from_dict(AgentRuntime.to_dict()).to_dict()` is byte-equal to
the original payload across the probed configurations. This means:

- Legacy serialization is currently idempotent.
- The AS2 adapter, when it ships, must preserve this property as a
  regression guard: if `to_dict()` round-trip ever becomes unstable, the
  canary in `tests/test_agentsnapshot_canary_p057.py` and the probe in
  `tests/test_agentruntime_todict_drift_p0510.py` will both fail.

---

## 9. AS2 adapter design risks (input only, not authorization)

This report does not authorize the adapter. It records risks for the AS2
RFC author to address before implementation.

```text
R1 identity asymmetry      legacy `name` is not a canonical agent_id; AS2
                           must define how AgentIdSeed components are
                           sourced or fail closed.

R2 model wrapping          bare `model: "mock"` must become a model_ref;
                           gated on AGENT-06.

R3 memory dereference      legacy raw `memory.long_term` content cannot
                           become canonical `memory_refs` without a
                           memory address resolver. Direct inclusion as
                           inline memory dump violates the
                           memory_ref_candidate boundary from P0.5.6.

R4 capability grants       legacy carries no tools in `to_dict()`. AS2
                           must source declarative grants from runtime
                           state, not from to_dict(), or accept empty
                           grant lists.

R5 identity state          soulprint/identity_version live outside
                           `to_dict()`. AS2 must choose exactly one of:
                           (A) omit identity as documented limitation, or
                           (B) source identity through a dedicated
                           runtime/interpreter read-only source. Hybrid or
                           partial sourcing is forbidden.

R6 schema registry         AS2 deployment is blocked by AGENT-11 (central
                           schema registry). Local allowlist (P0.5.7)
                           suffices for adapter unit tests but not for
                           runtime emission.

R7 envelope conflict       Environment._json_safe `__type__` envelope vs
                           AgentSnapshot `schema_version` envelope MUST
                           NOT coexist in the same payload. AS2 canonical
                           envelope MUST NOT reuse the legacy `__type__`
                           marker.

R8 capability_grant shape  AS2 CapabilityGrant (`function_descriptor_ref`,
   gap (P0.6.6)            `input_schema_hash`, `output_schema_hash`,
                           `effect_policy_hash`, `policy_ref`) is richer
                           than standalone-core CapabilityGrant
                           (`tool_namespace`, `scope_hash`, `policy_ref`)
                           introduced in P0.5.8. P0.6.7 projection
                           function MUST resolve this via one of options
                           R8-A (deterministic canonical projection), R8-B
                           (core schema bump to v2), or R8-C (separate
                           AgentSnapshot v2). Default expectation absent
                           explicit team vote: R8-A. See
                           `RFC-AGENT-SNAPSHOT-ADAPTER.md` §18 for details.
                           NOT a P0.6.6 blocker because P0.6.6 is
                           validation-only.
```

---

## 10. Acceptance criteria — self-check

The P0.5.10 acceptance criteria (from the team scope decision) map to this
report and the canary test:

- [x] `AgentRuntime.to_dict()` actual shape is captured and classified
      (§§ 2, 3).
- [x] Every legacy field has a status in
      `{migrates_as_is, requires_transform, legacy_only, excluded_from_canonical}`
      (§3).
- [x] Canary fails on silent legacy-shape drift
      (`tests/test_agentruntime_todict_drift_p0510.py`).
- [x] Report emits a GO/NO-GO indication for AS2 adapter design (§11
      below).
- [x] Runtime remains locked (no changes to `synapse/`, `interpreter.py`,
      `actor_runtime.py`, `agent_snapshot.py`, memory, CVM, golden
      fixtures).
- [x] AgentSnapshot standalone core from P0.5.8/P0.5.9 is unchanged.
- [x] All tests pass without regression.

---

## 11. GO / NO-GO

**Decision:** `GO with explicit team vote` for P0.6.x AS2 flagged adapter
design, conditional on the risks in §9 being addressed in the AS2 RFC
before any runtime code is opened.

P0.6.x is authorized only for an RFC + design phase. Implementation of the
adapter is **not** authorized by this report; it requires:

```text
explicit AS2 RFC approval
explicit team vote in ALPHA3F_PLANNING_GATE
all §9 risks (R1..R7) addressed in the RFC
AGENT-06 partial closure from P0.5.11 (`model_ref.v1` boundary)
AGENT-08 partial closure from P0.5.11 (subagents out of AS2 v1 scope)
R5 explicit choice: A identity omission or B dedicated identity source; no hybrid
R7 explicit canonical envelope rule: AS2 MUST NOT reuse legacy `__type__` marker
```

The following remain locked after P0.5.10:

```text
synapse/builtins.py             AgentRuntime / to_dict() migration
synapse/interpreter.py          Environment serialization changes
synapse/actor_runtime.py        actor integration
synapse/agent_snapshot.py       standalone core (no adapter wired in)
synapse/memory.py               MemoryPalace dereference
CVM / opcodes                   visibility of canonical agent fields
golden fixtures                 replay format
FunctionDescriptor registry     FUNC-03 / FUNC-04 deployment
central schema registry         AGENT-11 deployment
flagged adapter                 not implemented
profile selector                not implemented
```

---

## 12. P0.5.10 verdict

```text
Legacy AgentRuntime.to_dict() drift: ANALYZED AND CLASSIFIED
AS2 flagged adapter design (RFC):     AUTHORIZED CONDITIONAL ON TEAM VOTE
AS2 adapter implementation:           NOT AUTHORIZED
AgentRuntime.to_dict() migration:     NOT AUTHORIZED
Environment serialization migration:  NOT AUTHORIZED
Subagent canonicalization (AGENT-08): PARTIAL — OUT OF AS2 v1; future gate
Model/provider descriptor (AGENT-06): PARTIAL — model_ref boundary for AS2 RFC
Central schema registry (AGENT-11):   UNCHANGED, REMAINS DEFERRED
```


### P0.5.11 pre-RFC gate closure update

P0.5.11 removes the pre-RFC ambiguity for AS2 design but does not authorize
adapter implementation.

```text
AGENT-06: PARTIAL — `model_ref.v1` boundary exists for AS2 RFC design.
AGENT-08: PARTIAL — subagents explicitly out of AS2 v1 scope.
```

AS2 RFC must still close R1..R7 before adapter implementation is proposed.
P0.5.11 adds two mandatory AS2 RFC constraints:

1. R5 identity sourcing must choose exactly one strategy: identity omission as a
   documented limitation, or dedicated runtime/interpreter read-only sourcing.
   Hybrid partial sourcing is forbidden.
2. R7 canonical AgentSnapshot envelope must not reuse the legacy
   `{"__type__": "agent", "data": ...}` marker.


---

## 13. P0.6.0 AS2 RFC opening update

P0.6.0 opens `RFC-AGENT-SNAPSHOT-ADAPTER.md` as the design response to this
report. It does not implement the adapter.

The AS2 RFC is required to address all risks from §9 before adapter
implementation can be proposed. The draft records these initial design
positions:

```text
R1: canonical agent_id requires AgentIdSeed from adapter context; name alone is insufficient.
R2: bare legacy model must resolve to model_ref.v1 or fail closed.
R3: inline memory dump is forbidden; resolver or fail-closed strategy required.
R4: capability grants may be sourced only from declarative tool namespace mappings.
R5: Strategy B selected — identity state must come from a dedicated read-only runtime/interpreter source.
R6: local allowlist is design/unit only; deployment remains blocked by schema/profile registry gates.
R7: canonical envelope must not reuse legacy __type__ marker.
```

This update does not change the P0.5.10 conclusion: AS2 implementation remains
NOT AUTHORIZED until the RFC completes review and approval.


---

## P0.6.7 update — R9 AdapterDefinitionSource

P0.6.7 identified and closed a new projection gap, R9: `AdapterIdentityContext`
is identity-only and cannot supply all values required by the real standalone
`AgentSnapshot` constructor. The required non-identity values are
`AgentDefinitionRef`, `config`, and `canonical_fields`.

Resolution: P0.6.7 introduces explicit `AdapterDefinitionSource` as a separate
AS2 input. This preserves separation of concerns: identity remains in
`AdapterIdentityContext`, while definition/config payload enters through a
dedicated source.

This is not a legacy bridge and does not use `AgentRuntime.to_dict()` as a
canonical source.
