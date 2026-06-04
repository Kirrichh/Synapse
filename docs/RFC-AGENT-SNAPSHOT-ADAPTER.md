# RFC: AgentSnapshot Flagged Adapter (AS2)

**Status:** APPROVED — Alpha3g P0.6.3 final team approval complete  
**Version:** v1.0  
**Target milestone:** Alpha3g / P0.6.x  
**Patch:** P0.6.3 AS2 RFC Final Approval  
**Runtime scope authorized:** none — documentation only  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Depends on:** `RFC-AGENT-CANONICALIZATION.md` v1.0, `RFC-FUNCTION-DESCRIPTOR.md` v1.0, `RFC-STABLE-CANONICAL-IDENTITY.md` v1.0, `AGENTRUNTIME-TODICT-DRIFT-REPORT.md`, `AGENTSNAPSHOT-RUNTIME-PLAN.md`, `MIGRATION-READINESS-CHECKLIST.md`  
**Related gates:** AS2-01..AS2-10, AGENT-06, AGENT-07, AGENT-08, AGENT-11, FUNC-03, FUNC-04

This RFC defines the design contract for a future **flagged** adapter that can
project legacy `AgentRuntime` state into canonical `AgentSnapshot v1` payloads.
It is a design artifact only. It does not implement the adapter, does not add a
profile selector, does not modify `AgentRuntime.to_dict()`, and does not change
legacy serialization.

P0.6.1 hardens the original P0.6.0 draft. The adapter is no longer described as
an intelligent mapper over live runtime state. It is a pure deterministic
projection from explicit, canonical inputs to a canonical `AgentSnapshot` plus a
non-state audit derivation record. The hardening closes AS2-01..AS2-05 as
`RESOLVED` in P0.6.1, independently `VERIFIED` in P0.6.2, and `APPROVED v1.0` in P0.6.3.

---

## 1. Normative language

The words **MUST**, **MUST NOT**, **SHOULD**, **MAY**, and **FORBIDDEN** are
normative within this RFC.

---

## 2. Non-goals for P0.6.1 / P0.6.2 / P0.6.3

P0.6.1, P0.6.2, and P0.6.3 are documentation only. The following remain out of scope:

```text
adapter implementation
profile selector code
AgentRuntime.to_dict() migration
Environment serialization changes
interpreter.py changes
actor_runtime.py changes
MemoryPalace / memory backend changes
CVM/opcode changes
golden fixture rewrites
FunctionDescriptor runtime registry
central schema registry
schema compatibility registry
subagent canonicalization
hard switch to stable-canonical.v1 for legacy agents
new tests or runtime probes
```

Runtime code and test files remain locked for this patch.

---

## 3. Core AS2 philosophy: pure deterministic projection

The AS2 adapter is a pure, side-effect-free, deterministic projection:

```text
Adapter = f(AdapterIdentityContext,
            StaticModelRegistry,
            MemoryRefSource,
            CapabilityGrantSource,
            legacy AgentRuntime read surface)
        -> AgentSnapshot v1 + AdapterDerivationRecord
```

The legacy runtime object may be read only for the explicit legacy fields already
classified by `AGENTRUNTIME-TODICT-DRIFT-REPORT.md`. All canonical authority,
identity, memory references, model references, and capability grants must be
provided by explicit AS2 inputs.

The adapter MUST NOT perform or rely on:

```text
I/O
storage writes
CAS writes
wall-clock time
UUID generation
process id / id() / memory address
global state reads
runtime/interpreter direct reads for identity
dynamic provider lookup
registered tool namespace inspection
tools.keys() inspection
callable / decorator / Python signature inspection
scheduler queues
mailbox content
actor handles
timers
open files / sockets / provider clients
```

Any attempt to introduce ambient authority or runtime introspection into the
adapter path is an `AdapterAmbientAuthorityError`.

---

## 4. Adapter profile and feature flag

A future implementation MUST be gated by an explicit flag/profile. The RFC
reserves the logical name:

```text
agent_snapshot_adapter_profile = "alpha3g.as2.flagged_adapter.v1"
```

This name is a design identifier only. P0.6.1/P0.6.2 do not implement it.

Rules:

```text
Default behavior: legacy AgentRuntime.to_dict() unchanged.
Opt-in behavior: AS2 adapter path may emit AgentSnapshot v1 payload only after approval.
Hard switch: forbidden without a later approval gate.
Unknown adapter profile: fail closed.
```

---

## 5. Explicit AS2 inputs

### 5.1 AdapterIdentityContext

`AdapterIdentityContext` is the only source of canonical identity for AS2.
Legacy `name` may be used only as alias / routing hint after canonical alias
normalization. It MUST NOT be used as the source of `agent_id`.

`AdapterIdentityContext` is complete-or-absent. Partial context is forbidden.
If canonical emission requires identity and the context is absent or incomplete,
the adapter MUST fail closed with `AdapterIdentityContextMissingError` or
`AdapterIdentityContextIncompleteError`.

The context separates identity from audit metadata:

```text
identity_seed: fields that affect agent_id
  parent_anchor
  definition_hash
  spawn_nonce
  namespace
  alias

audit_context: provenance metadata that does not affect agent_id
  soulprint
  identity_version
```

`soulprint` and `identity_version` belong to `audit_context`. They are excluded
from the canonical `AgentSnapshot` state hash. If audit metadata is needed for
forensic traceability, it must be recorded in the adapter derivation/audit
record, not in the logical state hash.

The context MUST be self-describing. Implementations SHOULD use explicit field
presence markers in future schemas so that omission, empty value, and `null` do
not become ambiguous during replay.

### 5.2 StaticModelRegistry

Legacy `model` is a bare string. AS2 maps it to `model_ref.v1` only through a
local immutable `StaticModelRegistry` snapshot.

`StaticModelRegistry` rules:

```text
append-only: historical mappings are never overwritten;
content-addressed: registry snapshot carries registry_snapshot_hash;
static: no live provider lookup, endpoint probing, or deployment discovery;
allowlisted: provider_namespace is mock | anthropic | openai | local | custom;
custom: requires an explicit registry entry and is not a wildcard fallback;
unknown model: ModelRefUnknownError.
```

The registry snapshot hash MUST be recorded in the `AdapterDerivationRecord`.
Registry updates do not retroactively affect existing replay artifacts.

### 5.3 MemoryRefSource

`MemoryRefSource` is the only source of canonical `memory_refs`. The adapter MUST
NOT serialize inline legacy memory dumps into `config`, `canonical_fields`, or
`memory_refs`.

A missing or `null` `MemoryRefSource` is not the same as empty memory. Empty
memory MUST be represented by an explicit empty array:

```text
memory_refs = []
```

Omitted or `null` memory source MUST raise `MemoryRefSourceMissingError` when
canonical emission requires memory handling.

### 5.4 CapabilityGrantSource

`CapabilityGrantSource` is the only source of canonical `capability_grants`.
The adapter MUST NOT inspect live tools, `tools.keys()`, callable objects,
Python signatures, decorators, or runtime tool registries.

Capability grant entries in AS2 v1 must be declarative and compatible with the
`RFC-FUNCTION-DESCRIPTOR.md` v1.0 reference schema. AS2 v1 does not require a
live FunctionDescriptor runtime registry; existence/authority enforcement remains
blocked by FUNC-03/FUNC-04 and AGENT-11 implementation gates.

If live tools exist but no declarative `CapabilityGrantSource` is provided, the
canonical adapter path MUST fail closed with `CapabilityGrantSourceMissingError`.
Invalid descriptor references MUST raise `CapabilityGrantInvalidRefError`.

---

## 6. Input and output envelopes

### 6.1 Legacy input shape

The legacy input shape is the `AgentRuntime.to_dict()` shape proven by P0.5.10:

```text
{name, model, trust_level, trust_scope, memory}
```

with nested memory:

```text
{short_term, long_term, capacity}
```

This legacy shape is not a canonical AgentSnapshot. It is only the legacy read
surface used as one input to the explicit AS2 projection. The adapter MUST NOT
inspect any live runtime state beyond this approved legacy read surface unless a
future approved RFC explicitly authorizes a new source.

### 6.2 Canonical output shape

The adapter output, when authorized in a future implementation patch, MUST be a
valid `AgentSnapshot v1`:

```text
agent_id
definition_ref
config
canonical_fields
memory_refs
model_ref
capability_grants
profile = stable-canonical.v1
schema_version = alpha3g.agent_snapshot.v1
```

### 6.3 Legacy envelope conflict

The AS2 canonical envelope MUST NOT reuse the legacy envelope:

```json
{"__type__": "agent", "data": {}}
```

The legacy `__type__` / `data` marker remains a legacy serialization marker. AS2
must use `AgentSnapshot v1` fields (`type`, `schema_version`, and `profile`) and
must not produce a mixed payload containing both legacy and canonical envelope
markers. Any future attempt to reuse or mix the legacy envelope is an
`AdapterEnvelopeConflictError`.

---

## 7. Two-phase memory externalization and validation invariant

Canonical memory projection follows a strict two-phase protocol.

### 7.1 Phase 1 — host preparation

Before invoking the pure adapter, the host/runtime is responsible for preparing
memory references:

```text
1. Construct AdapterIdentityContext.
2. Compute agent_id from AdapterIdentityContext.identity_seed using AGENT-01.
3. Derive memory_space_id from agent_id and memory_space_policy_version.
4. Externalize legacy inline memory into canonical storage/CAS.
5. Construct MemoryRefSource with canonical memory_ref entries.
```

The adapter does not perform this phase. It must not persist memory, mint storage
records, or derive refs from inline content.

### 7.2 Phase 2 — adapter projection and strict validation

During adapter projection, the adapter MUST recompute the expected memory
boundary:

```python
expected_agent_id = derive_agent_id(AdapterIdentityContext.identity_seed)
expected_memory_space_id = derive_memory_space_id(
    expected_agent_id,
    memory_space_policy_version,
)
```

The adapter MUST verify that every single `memory_ref` in `MemoryRefSource`
satisfies:

```text
memory_ref.memory_space_id == expected_memory_space_id
```

If any `memory_ref` is missing `memory_space_id`, uses a different
`memory_space_id`, uses an unregistered `memory_space_policy_version`, or mixes
multiple memory spaces inside one canonical projection, the adapter MUST fail
closed with `AdapterMemorySpaceMismatchError`.

The adapter MUST NOT silently filter, rewrite, normalize, or repair memory refs.
`MemoryRefSource` is a strict read-only input. Any mismatch indicates a host
preparation error or attempted cross-agent memory contamination.

### 7.3 Memory-space derivation purity

`derive_memory_space_id` MUST be a pure function of exactly two inputs:

```text
agent_id
memory_space_policy_version
```

Additional ambient inputs such as host node, deployment region, timestamp,
process id, random seed, or storage backend identity are forbidden. If a host
policy requires those facts, they must be resolved before adapter invocation and
represented through explicit canonical inputs, not ambient derivation.

The memory-space derivation function MUST be deterministic, versioned, and
recorded in the adapter derivation record. Changing the derivation function
requires a new `memory_space_policy_version` and MUST NOT retroactively affect
existing replay artifacts.

### 7.4 AS2 v1 limitation: no mixed memory spaces

AS2 v1 forbids mixed memory spaces in canonical AgentSnapshot projection. All
`memory_refs` must belong to the single expected memory space derived from the
agent's identity. Cross-agent memory sharing, if required later, must be modeled
as explicit capability delegation in a future RFC/gate, not as mixed
`memory_refs` in AS2 v1.

---

## 8. AdapterDerivationRecord

To support replay-safe audit without polluting the canonical `AgentSnapshot`
state hash, the adapter MUST emit an `AdapterDerivationRecord` in future
implementations.

```json
{
  "schema_version": "alpha3g.adapter_derivation.v1",
  "input_hashes": {
    "identity_context_hash": "sha256:...",
    "model_registry_snapshot_hash": "sha256:...",
    "memory_ref_source_hash": "sha256:...",
    "capability_grant_source_hash": "sha256:..."
  },
  "memory_space_policy": {
    "policy_version": "alpha3g.memory_space_policy.v1",
    "expected_memory_space_id": "sha256:..."
  },
  "profile": "stable-canonical.v1"
}
```

This record is provenance metadata. It does not replace or alter the canonical
`AgentSnapshot` state hash. `audit_context` may be represented in the derivation
record using a canonical empty audit placeholder when absent, but audit metadata
MUST NOT alter logical snapshot state identity.

Even when `MemoryRefSource` is the explicit empty array, the derivation record
must include the memory-space policy section and computed
`expected_memory_space_id`. This distinguishes intentional empty memory from a
missing memory source.

---

## 9. Resolution of P0.5.10 risks R1..R7

### R1 — identity asymmetry

Resolved by `AdapterIdentityContext`. `agent_id` is derived from the canonical
identity seed, never from legacy `name`, `UUID`, `id()`, process id, or wall-clock
time. Legacy `name` can only become normalized alias/routing metadata.

### R2 — model wrapping

Resolved by `StaticModelRegistry` and `model_ref.v1`. Unknown models fail closed.
`custom` is explicit-only and not a wildcard.

### R3 — memory dereference

Resolved by strict two-phase externalization and MemoryRefSource validation.
Inline memory dumps are forbidden. Memory-space mismatch or mixed memory spaces
raise `AdapterMemorySpaceMismatchError`.

### R4 — capability grant sourcing

Resolved by explicit `CapabilityGrantSource`. Live tool/callable inspection is
forbidden. FunctionDescriptor registry enforcement remains a later implementation
gate; AS2 v1 requires descriptor-compatible declarative refs.

### R5 — identity state sourcing

Resolved by separating `identity_seed` from `audit_context`. `soulprint` and
`identity_version` are provenance metadata, not `agent_id` seed inputs. Audit data
must not change canonical state hash.

### R6 — schema registry dependency

P0.5.7 and P0.5.11 permit local fail-closed schema/profile allowlists for
standalone unit scope and AS2 RFC design. Deployment still requires central
schema/profile registry behavior (AGENT-11, FUNC-04). Unknown schema/profile must
fail closed.

### R7 — envelope conflict

Resolved by envelope isolation. AS2 canonical payloads MUST NOT use or merge with
legacy `{"__type__": "agent", "data": ...}` envelopes.

---

## 10. Field mapping table

| Legacy field / source | AS2 canonical destination | Rule |
|---|---|---|
| `name` | alias / routing hint only | Cannot be sole identity; never source of `agent_id`. |
| `model` | `model_ref` | Requires `StaticModelRegistry`; unknown fails closed. |
| `trust_level` | `config.trust_level` | May migrate if scalar remains canonical-serializable. |
| `trust_scope` | `config.trust_scope` | May migrate if values are canonical strings. |
| `memory.short_term` | external storage -> `memory_refs` | Inline dump forbidden; host pre-stages refs. |
| `memory.long_term` | external storage -> `memory_refs` | Inline dump forbidden; host pre-stages refs. |
| `memory.capacity` | future config decision | Requires future AS2 implementation decision; not an identity source. |
| `tools` live registry | none directly | `CapabilityGrantSource` only; live inspection forbidden. |
| `soulprint` / `identity_version` | `audit_context` / derivation metadata | Excluded from canonical state hash. |
| `env`, `llm`, actor ids, mailboxes, scheduler, timers | none | Runtime Envelope only; forbidden. |

---

## 11. Subagent boundary

Subagents are explicitly out of AS2 v1 scope.

`SubAgentDef` is currently an AST-level construct, not a legacy
`AgentRuntime.to_dict()` serialization surface. AS2 does not reserve
`subagent_snapshot_ref` in `AgentSnapshot v1` and does not define canonical
subagent snapshots.

If a future adapter detects a fracture runtime graph or subagent structure in a
payload that claims AS2 v1, it MUST fail closed with
`AdapterSubagentOutOfScopeError` rather than attempting recursive adaptation.

Future subagent canonicalization requires a separate RFC/gate when subagents
receive runtime identity or a serialization surface.

---

## 12. Typed fail-closed error taxonomy

P0.6.1 does not implement errors, but a future adapter MUST use specific typed
failures. Generic catch-all adapter failures are forbidden because they mask
forensic failure modes.

Recommended hierarchy:

```text
AdapterError                         base class; never raised directly
AdapterInputError                    caller-supplied input is missing/incomplete
AdapterMappingError                  registry or descriptor mapping failed
AdapterIntegrityError                adapter/host boundary violation; fatal
```

Minimum AS2-specific errors:

```text
AdapterIdentityContextMissingError
AdapterIdentityContextIncompleteError
ModelRefUnknownError
MemoryRefSourceMissingError
AdapterMemorySpaceMismatchError
CapabilityGrantSourceMissingError
CapabilityGrantInvalidRefError
AdapterEnvelopeConflictError
AdapterAmbientAuthorityError
AdapterInlineMemoryRejectedError
AdapterSubagentOutOfScopeError
```

`AdapterMemorySpaceMismatchError` covers at least these reserved subconditions:

```text
MIXED_SPACES
FOREIGN_SPACE
MISSING_SPACE_ID
UNKNOWN_POLICY
```

---

## 13. Known AS2 v1 limitations

AS2 v1 intentionally does not solve:

```text
cross-agent memory sharing
subagent recursive snapshots
FunctionDescriptor runtime registry existence checks
authority verification between CapabilityGrantSource and live runtime tools
central schema/profile registry deployment
Environment-level dual emission
hard switching legacy serialization to AgentSnapshot
```

Cross-agent memory sharing, if required, must be represented through future
capability delegation rather than mixed memory spaces in `memory_refs`.

AS2 v1 guarantees that capability grants are declarative and canonical. It does
not prove that current runtime tools actually provide those capabilities; that is
an authority-enforcement gate for later runtime work.

---

## 14. Review findings and approval gates

This RFC uses the structured review registry in
`RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.

P0.6.1 resolves the following blockers at the design level; P0.6.2 independently verifies those resolutions:

```text
AS2-01 identity source contract
AS2-02 model_ref resolver and provider registry behavior
AS2-03 memory mapping / two-phase externalization
AS2-04 capability grant sourcing
AS2-05 envelope separation and identity/audit state boundary
```

These findings are `VERIFIED` by `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md` in P0.6.2 and approved as the AS2 v1.0 design baseline by the P0.6.3 vote record in `RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.

Runtime implementation remains blocked until this RFC completes the same process
used by prior RFCs:

```text
DRAFT -> Review -> Revision -> Independent Verification -> Approval -> Implementation planning
```

---

## 15. Acceptance criteria for P0.6.1 / P0.6.2 / P0.6.3

P0.6.1 is acceptable only if it:

- [x] remains documentation-only;
- [x] performs no changes to `synapse/`, `tests/`, fixtures, or runtime code;
- [x] removes any authorization to inspect live tools / `tools.keys()`;
- [x] defines `AdapterIdentityContext` as the only identity source;
- [x] separates `identity_seed` from `audit_context`;
- [x] defines `StaticModelRegistry` as immutable, append-only, and content-addressed;
- [x] defines the two-phase memory externalization protocol;
- [x] requires per-ref memory-space validation and forbids rewrite/filter/repair;
- [x] introduces `AdapterMemorySpaceMismatchError`;
- [x] documents `AdapterDerivationRecord` for audit traceability;
- [x] P0.6.1 keeps AS2-01..AS2-05 at `RESOLVED`, not `VERIFIED`;
- [x] P0.6.2 adds independent verification matrix and moves AS2-01..AS2-05 to `VERIFIED`.
- [x] P0.6.3 records structured team approval and freezes this RFC as `APPROVED v1.0` without normative contract changes.

---

## 16. Post-RFC implementation boundary

Even after this RFC is approved, runtime implementation must be separately
authorized. A future implementation patch must provide:

```text
explicit adapter profile flag
dual-path tests: legacy unchanged + canonical opt-in
fail-closed resolver interfaces for identity, model, memory, capabilities
no hard switch
no mutation of AgentRuntime.to_dict()
no golden fixture rewrites unless separately approved
```

Next expected step after P0.6.3 is P0.6.4 implementation planning / drift harness design. Adapter
implementation remains unauthorized until a separate P0.6.5 implementation vote.

---

## 17. P0.6.6 name reservation for future projection function

The future projection function (introduced in P0.6.7 only after a separate
team vote) is name-reserved as:

```python
def project_validated_as2_inputs(
    identity_context: AdapterIdentityContext,
    model_registry: StaticModelRegistry,
    memory_ref_source: MemoryRefSource,
    capability_grant_source: CapabilityGrantSource,
) -> "AgentSnapshot":
    ...
```

The `project_` prefix is normative. It reflects the RFC §3 contract that the
adapter is a pure mathematical projection, not a conversion of a self-contained
legacy object. Naming conventions like `to_agent_snapshot()` are FORBIDDEN
permanently in the AS2 surface because the `to_` prefix in Python implies
self-conversion from `self` and would create false expectations that the
adapter accepts a single legacy `AgentRuntime` and returns its canonical form.

P0.6.6 does not implement this function. It only reserves the name and forbids
alternate names in the AS2 module to prevent naming drift between the RFC and
implementation.

The following alternate names are FORBIDDEN in `synapse/agent_snapshot_adapter.py`:

```text
to_agent_snapshot
build_agent_snapshot
build_snapshot_from_as2_inputs
build_snapshot_from_validated_inputs
adapt_to_snapshot
convert_to_snapshot
```

---

## 18. R8 — AS2 CapabilityGrant vs core CapabilityGrant shape gap

A drift surface was identified between the AS2 `CapabilityGrant` value object
(this RFC §5.4) and the standalone-core `CapabilityGrant` from P0.5.8 / P0.5.9
(`synapse/agent_snapshot.py`):

```text
AS2 CapabilityGrant (§5.4):
  tool_namespace
  function_descriptor_ref:
    namespace
    symbol
    input_schema_hash
    output_schema_hash
  effect_policy_hash
  policy_ref
  schema_version
  type

core CapabilityGrant (P0.5.8):
  tool_namespace
  scope_hash
  policy_ref
  schema_version
  type
```

The AS2 grant is richer (separates input/output schema hashes, names a function
descriptor explicitly, separates effect policy from policy reference). The core
grant is minimal (single `scope_hash`).

This gap is intentional and consistent with the RFC: AS2 §5.4 requires
FunctionDescriptor-compatible grants because canonical replay must trace tool
authority to its declared descriptor, while standalone-core P0.5.8 was sized
for the AgentSnapshot value-core only and predates the FunctionDescriptor
contract.

P0.6.6 records R8 here as a planning input. R8 is **not** a P0.6.6 blocker
because P0.6.6 is validation-only; no projection function constructs a core
`CapabilityGrant` from an AS2 grant in this patch.

R8 resolution options to be decided in P0.6.7 design (NOT in P0.6.6):

```text
Option R8-A:
  AS2 projection performs explicit canonical projection of AS2 CapabilityGrant
  to core CapabilityGrant by deriving a single `scope_hash` from the AS2 grant
  fields using stable-canonical.v1 hashing. Projection is deterministic,
  documented, and recorded in AdapterDerivationRecord. Core schema unchanged.

Option R8-B:
  Core CapabilityGrant schema is bumped (P0.7.x) to alpha3g.capability_grant.v2
  with function_descriptor_ref and effect_policy_hash fields. Existing P0.5.x
  AgentSnapshot golden fixtures may require migration; significant change.

Option R8-C:
  AS2 projection emits a NEW canonical AgentSnapshot variant
  (alpha3g.agent_snapshot.v2) that uses AS2 CapabilityGrant directly without
  core schema bump. AS2 and standalone-core diverge permanently.
```

The default expectation, absent a separate team vote, is R8-A: deterministic
canonical projection with audit trail. R8-B and R8-C remain options open for
P0.6.7 design review but require schema migration approval.

This RFC section is documentation-only. P0.6.6 introduces no resolution code,
no reduction function, and no schema bump.


---

## 19. P0.6.7 R8/R9 projection decision record

P0.6.7 implements the first fixture-driven minimal standalone projection using
`project_validated_as2_inputs(...)`. The implementation is intentionally narrow:
it accepts only explicit AS2 inputs, constructs the existing standalone
`AgentSnapshot` core value object, and remains fully disconnected from legacy
`AgentRuntime`, `Environment`, interpreter, actor runtime, CVM, Integrate, Dream,
storage/CAS, and provider registries.

### 19.1 R8 resolution for P0.6.7

P0.6.7 chooses **R8-A canonical projection** for the AS2 CapabilityGrant shape
gap. The projection preserves `tool_namespace` as the first-class core
`CapabilityGrant.tool_namespace` and derives core `scope_hash` from a
stable-canonical.v1 payload containing:

```text
type = as2_capability_projection_scope
projection_version = alpha3g.as2_capability_projection.v1
tool_namespace
function_descriptor_ref
effect_policy_hash
policy_ref
profile = stable-canonical.v1
```

This does not migrate core `CapabilityGrant` schema and does not introduce an
`AgentSnapshot` v2. R8-B and R8-C remain future design options only.

### 19.2 R9 closure via AdapterDefinitionSource

A new gap was identified before P0.6.7 implementation: `AdapterIdentityContext`
is intentionally identity-only and does not contain all values required by the
real `AgentSnapshot` constructor. Specifically, projection requires:

```text
AgentDefinitionRef
config
canonical_fields
```

P0.6.7 closes this as **R9** by adding a separate explicit input:

```text
AdapterDefinitionSource
```

`AdapterDefinitionSource` carries `definition_ref`, `config`, and
`canonical_fields`. This keeps `AdapterIdentityContext` from becoming a generic
catch-all container and preserves the AS2 pattern of explicit, typed input
sources.

### 19.3 P0.6.7 limits

P0.6.7 does not authorize:

```text
to_agent_snapshot()
legacy AgentRuntime bridge
Environment._json_safe() migration
real AdapterDerivationRecord hash computation
AS2ViolationContext
feature flag machinery
FunctionDescriptor runtime registry
real provider entries
golden fixture migration
```

AdapterDerivationRecord remains synthetic/form-level in P0.6.7. Real input hash
calculation and Merkle-transparent audit are reserved for a later patch.


---

## 20. P0.6.8 AdapterDerivationRecord hashing decision record

P0.6.8 replaces the P0.6.7 synthetic AdapterDerivationRecord population with real stable-canonical input hashes for the standalone AS2 projection path. This is an audit-trail change only: projection inputs and `AgentSnapshot` construction semantics are unchanged.

### 20.1 Required derivation input hashes

The AdapterDerivationRecord input-hash set for AS2 v1 contains exactly five required entries:

```text
identity_context_hash
model_registry_snapshot_hash
adapter_definition_source_hash
memory_ref_source_hash
capability_grant_source_hash
```

Each value is computed with the existing stable-canonical profile through the project canonical service. P0.6.8 does not introduce an adapter-specific canonicalizer or hashing helper.

### 20.2 State hash isolation

AdapterDerivationRecord is a forensic audit artifact. It records how a standalone AS2 projection was derived from explicit inputs, but it is not part of logical agent state. Therefore:

```text
AgentSnapshot.snapshot_hash() MUST NOT depend on AdapterDerivationRecord.
AdapterDerivationRecord input hashes MUST NOT alter AgentSnapshot body fields.
Audit verification composes snapshot and derivation record externally.
```

This preserves the distinction between logical state identity and provenance/audit metadata.

### 20.3 P0.6.8 boundaries

P0.6.8 does not authorize:

```text
AS2ViolationContext
legacy AgentRuntime bridge
Environment._json_safe() migration
real provider registry entries
FunctionDescriptor runtime registry
profile selector
R8-B / R8-C schema migration
AgentSnapshot schema changes
Adapter projection semantic changes
```

R8-A remains the active capability-grant projection rule for the current track.

## 21. P0.6.9 AS2ViolationContext / forensic error attribution

P0.6.9 enriches AS2 fail-closed paths with structured forensic context while
leaving successful projection and derivation-record semantics unchanged.

### 21.1 Failure-path context

AS2 leaf exceptions MAY carry an immutable `AS2ViolationContext` value object.
The context is attached by composition through the existing AS2 adapter error
hierarchy; existing leaf exception class names are not renamed and the taxonomy
is not restructured.

The active context shape is:

```text
AS2ViolationContext
  rfc_reference: str
  violated_field: str | None
  fixture_case_id: str | None
  expected_value: str | None
  actual_value: str | None
```

`rfc_reference` is mandatory for constructed contexts and MUST use the canonical
section-citation format:

```text
RFC-AGENT-SNAPSHOT-ADAPTER.md §<section>[.<subsection>...]
```

The validation regex is:

```text
^RFC-[A-Z0-9-]+\.md §[0-9]+(\.[0-9]+)*$
```

Examples of valid references:

```text
RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1
RFC-AGENT-SNAPSHOT-ADAPTER.md §7.2
RFC-AGENT-SNAPSHOT-ADAPTER.md §12
```

### 21.2 Fixture context

All negative AS2 fixtures now include `expected_error_context` with at least:

```text
expected_error_context.rfc_reference
expected_error_context.violated_field
```

P0.6.9 test assertions use subset matching: the runtime context may include
additional values such as `expected_value` or `actual_value`, but it must include
all context fields declared by the fixture.

### 21.3 Success/failure separation

`AS2ViolationContext` belongs only to failure paths. It MUST NOT be mixed into
`AdapterDerivationRecord`, which remains the audit artifact for successful
projection paths. It also MUST NOT influence `AgentSnapshot.snapshot_hash()`.

### 21.4 Boundaries unchanged

P0.6.9 does not authorize:

```text
projection semantics changes
AdapterDerivationRecord hash changes
AgentSnapshot schema changes
legacy AgentRuntime bridge
Environment._json_safe() migration
feature flag wiring
R8-B / R8-C schema migration
real provider registry entries
FunctionDescriptor runtime registry
```


## 22. Legacy Bridge Boundary Reference (P0.6.10)

The standalone AS2 adapter contract remains authoritative for explicit-input validation, projection, derivation hashing, and forensic error attribution.

Legacy bridge work is governed by `docs/AS2-LEGACY-BRIDGE-DESIGN.md`. That document does not change AS2 standalone semantics. It defines the future Host Pre-Stage Protocol required before any legacy runtime integration may be implemented.

Bridge code, `AgentRuntime` imports, `Environment` imports, runtime wiring, feature flag implementation, and `to_agent_snapshot()` remain unauthorized by this RFC reference.
