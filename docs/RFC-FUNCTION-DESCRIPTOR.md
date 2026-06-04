# RFC: Function Descriptor

**Status:** APPROVED — Alpha3g P0.5.2.3 final team vote complete  
**Version:** v1.0  
**Target milestone:** Alpha3g / P0.5.x  
**Patch:** P0.5.2.3 Function Descriptor RFC final team vote & approval  
**Runtime scope authorized:** none — documentation only  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Depends on:** `docs/RFC-STABLE-CANONICAL-IDENTITY.md` v1.0, `docs/RFC-AGENT-CANONICALIZATION.md` v0.2  
**Related findings:** AGENT-02, STABLE-04, STABLE-05
**Approval record:** `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md` — Approval Vote Record, Alpha3g P0.5.2.3

This RFC defines the canonical descriptor boundary for functions, methods, and
callable contracts used by Agent Canonicalization and future deterministic
runtime features.

The core rule is deliberately conservative:

```text
FunctionDescriptor v1 identifies a callable contract and capability boundary.
FunctionDescriptor v1 does not identify the executable implementation body.
```

Executable body identity requires a future approved contract for canonical AST,
canonical CVM image, or another host-independent executable representation. That
future work is tracked as FunctionDescriptor v2 / executable identity.

---

## 1. Normative language

The words **MUST**, **MUST NOT**, **SHOULD**, **MAY**, **FORBIDDEN**, and
**REQUIRED** are normative within Alpha3g design documents.

---

## 2. Motivation

`RFC-AGENT-CANONICALIZATION.md` split `AGENT-02` because agent definition
identity cannot safely depend on Python runtime artifacts such as bytecode,
`inspect.getsource()`, closure cells, file-system paths, or live function
objects.

Current runtime code can contain host-derived state and runtime object references.
Those are acceptable as runtime envelope details, but they are not acceptable as
canonical identity inputs.

This RFC provides the missing prerequisite for agent definitions:

```text
A stable, declarative function contract descriptor that can participate in
stable-canonical.v1 hashes without depending on Python implementation details.
```

---

## 3. Non-goals for P0.5.2

P0.5.2 is documentation only. The following remain out of scope:

```text
FunctionDescriptor runtime class
AST normalizer implementation
CVM bytecode compiler / canonical image builder
interpreter.py changes
actor_runtime.py changes
agent snapshot runtime implementation
golden fixture updates
function registry implementation
schema registry implementation
capability policy runtime enforcement
```

---

## 4. Two-tier model

Function identity is split into two tiers.

### 4.1 FunctionDescriptor v1 — declarative contract identity

FunctionDescriptor v1 identifies:

```text
callable namespace
symbol name
declared version
input schema
output schema
capability requirements
effect policy
dependency manifest
explicit captured environment declaration, if any
```

It does **not** identify:

```text
implementation body
Python source text
Python AST as parsed by a host runtime
Python bytecode
closure cell values
runtime function pointer
compiled extension binary
JIT cache artifact
```

Therefore, v1 is suitable for:

```text
routing
agent manifest linkage
capability boundary verification
compatibility checks
declarative effect preflight
static-manifest-only agent definitions
```

It is not sufficient for strict behavioral identity.

### 4.2 FunctionDescriptor v2 / future — executable identity

Executable identity requires a separate approved contract. Candidate future
models include:

```text
canonical language-independent AST graph
canonical Synapse AST after parser/lowering stabilization
canonical CVM image hash
content-addressed logic module produced by a deterministic compiler
```

A future executable identity contract MUST solve:

```text
closure serialization
import alias resolution
syntax lowering stability
compiler/parser version pinning
capability binding to executable body
zero host-path leakage
cross-host reproducibility
```

Until that contract is approved, executable body identity remains outside
FunctionDescriptor v1.

---

## 5. FunctionDescriptor v1 schema

A v1 descriptor MUST be serializable by `stable-canonical.v1`.

Minimum descriptor shape:

```json
{
  "type": "function_descriptor",
  "schema_version": "alpha3g.function_descriptor.v1",
  "namespace": "agent.greeter",
  "symbol_name": "respond",
  "declared_version": "1.0.0",
  "input_schema_hash": "sha256:...",
  "output_schema_hash": "sha256:...",
  "capability_schema_hash": "sha256:...",
  "effect_policy_hash": "sha256:...",
  "dependency_manifest_hash": "sha256:...",
  "captured_env_hash": "sha256:...",
  "profile": "stable-canonical.v1"
}
```

`function_descriptor_hash` MUST be computed as:

```text
function_descriptor_hash = sha256(stable-canonical.v1(function_descriptor))
```

The descriptor MUST NOT contain live runtime values. It must contain hashes of
canonical manifests and schemas only.

### 5.1 Required fields

| Field | Requirement |
|---|---|
| `type` | MUST be `function_descriptor`. |
| `schema_version` | MUST be `alpha3g.function_descriptor.v1`. |
| `namespace` | Stable logical namespace. MUST NOT be a host file path. |
| `symbol_name` | Stable exported symbol name. MUST be NFC-normalized. |
| `declared_version` | Explicit immutable version string. Floating values are FORBIDDEN. |
| `input_schema_hash` | Hash of canonical input schema manifest. |
| `output_schema_hash` | Hash of canonical output schema manifest. |
| `capability_schema_hash` | Hash of required capability schema manifest. |
| `effect_policy_hash` | Hash of declared effect policy. |
| `dependency_manifest_hash` | Hash of explicit dependency manifest. |
| `captured_env_hash` | Hash of explicit captured binding manifest, or hash of empty binding manifest. |
| `profile` | MUST be `stable-canonical.v1`. |

---

## 6. Canonical schema hashing

Input and output schemas MUST be canonical data, not raw JSON text.

`input_schema_hash` and `output_schema_hash` MUST be computed as:

```text
schema_hash = sha256(stable-canonical.v1(schema_manifest))
```

A schema manifest SHOULD have this shape:

```json
{
  "type": "function_io_schema",
  "schema_version": "alpha3g.function_io_schema.v1",
  "name": "respond.input",
  "fields": [
    {
      "name": "prompt",
      "value_type": "string",
      "required": true
    }
  ],
  "profile": "stable-canonical.v1"
}
```

### 6.1 Canonical JSON Schema subset

If JSON Schema-like structures are used, the project MUST use a canonical subset:

```text
comments are forbidden (`$comment`, free-form doc comments)
all strings must be NFC-normalized
object keys must be stable-canonical.v1 strings
non-string keys are forbidden
unordered maps are serialized only through stable-canonical.v1
floating versions / implicit defaults are forbidden
```

Schema formatting, key order in a source file, indentation, comments, and host
parser behavior MUST NOT influence schema hashes.

---

## 7. Capability schema and mandatory attenuation

`capability_schema_hash` binds the function contract to the capabilities it may
use. A capability schema is declarative; it MUST NOT serialize live tool objects.

A capability schema SHOULD have this shape:

```json
{
  "type": "function_capability_schema",
  "schema_version": "alpha3g.function_capability_schema.v1",
  "capabilities": [
    {
      "grant_type": "capability_grant",
      "tool_namespace": "fs_read",
      "scope_hash": "sha256:...",
      "policy_ref": "policy:stable-id"
    }
  ],
  "profile": "stable-canonical.v1"
}
```

`scope_hash` MUST be derived from canonical scope material:

```text
scope_hash = sha256(stable-canonical.v1(scope_definition))
```

where `scope_definition` declares:

```text
permitted argument schemas
return constraints
resource boundaries
allowed path / namespace patterns, if applicable
rate / quota policy, if applicable
```

`scope_hash` MUST NOT be derived from live function introspection, runtime tool
objects, sockets, open files, database handles, process IDs, or host file paths.

Runtime Envelope MUST enforce mandatory attenuation:

```text
A function call MUST be rejected if the attempted tool/capability call is not
covered by the declared capability schema, even if the host runtime has a live
object that could perform the call.
```

---

## 8. Effect policy

`effect_policy_hash` declares the permitted effect boundary for a function.
The effect policy is a canonical contract, not a comment. It MUST be serialized
through `stable-canonical.v1` before hashing.

```text
effect_policy_hash = sha256(stable-canonical.v1(effect_policy))
```

A v1 effect policy MUST use this schema family:

```json
{
  "type": "effect_policy",
  "schema_version": "alpha3g.effect_policy.v1",
  "determinism_policy": "pure",
  "nondeterminism_barrier_class": "none",
  "allowed_effects": [
    {
      "effect_namespace": "memory_read",
      "allowed": true,
      "scope_bound": true,
      "scope_hash": "sha256:..."
    },
    {
      "effect_namespace": "io_write",
      "allowed": false,
      "scope_bound": false,
      "scope_hash": null
    }
  ],
  "idempotency": "required",
  "profile": "stable-canonical.v1"
}
```

### 8.1 Required fields

| Field | Requirement |
|---|---|
| `type` | MUST be `effect_policy`. |
| `schema_version` | MUST be `alpha3g.effect_policy.v1`. |
| `determinism_policy` | MUST be one of `pure`, `recorded`, `live_only`, `forbidden`. |
| `nondeterminism_barrier_class` | MUST be one of the enum values in §8.2. Free-form strings are FORBIDDEN. |
| `allowed_effects` | MUST be a deterministic array of declared effect entries. Empty array is valid only for `pure`. |
| `idempotency` | MUST be one of `required`, `optional`, `none`. |
| `profile` | MUST be `stable-canonical.v1`. |

### 8.2 Nondeterminism barrier class enum

The barrier class is an explicit enum so future runtimes do not parse prose:

```text
none
memory_read
memory_write
llm
dream
integrate
io
network
actor
promise
host
forbidden
```

Mapping to runtime contexts:

| Descriptor barrier class | Meaning | Integrate context default | Dream strict context default |
|---|---|---|---|
| `none` | Pure deterministic computation only. | allowed | allowed only if no body re-execution hazard exists |
| `memory_read` | Reads through declared memory refs only. | allowed if declared and scoped | requires recorded/consume-only model |
| `memory_write` | Writes through overlay/write-set boundary. | allowed only in mutation-capable context | rejected |
| `llm` | Recorded LLM call. | rejected inside current Integrate barrier unless recorded-and-consumed contract exists | recorded subtrace required |
| `dream` | Dream invocation. | rejected inside Integrate v1 barrier | n/a |
| `integrate` | Nested integrate. | rejected in Integrate v1 barrier | rejected unless future transaction nesting RFC exists |
| `io` | Filesystem / stdout / stderr / external IO. | rejected | rejected |
| `network` | Network effects. | rejected | rejected |
| `actor` | Actor spawn/send/migrate. | rejected until resource cleanup contract exists | rejected |
| `promise` | Promise create/await/settle. | rejected until resource cleanup contract exists | rejected |
| `host` | Any host runtime object or direct system call. | rejected | rejected |
| `forbidden` | Explicitly forbidden effect. | rejected | rejected |

### 8.3 Permitted effect namespaces

The initial `effect_namespace` vocabulary is:

```text
pure
memory_read
memory_write
llm_call
dream_call
integrate_call
io_read
io_write
network_read
network_write
actor_spawn
actor_send
actor_migrate
promise_create
promise_await
promise_settle
host_call
forbidden
```

New effect namespaces require either:

```text
an approved RFC that extends this vocabulary, or
an approved schema registry compatibility entry.
```

Unregistered effect namespaces MUST be rejected.

### 8.4 Runtime enforcement rule

A runtime context MAY be stricter than the descriptor. It MUST NOT be looser.

```text
context_effect_budget <= descriptor_declared_effects
```

If a descriptor declares `io_write`, an Integrate context may reject it. If a
descriptor declares only `pure`, the runtime must still reject any attempted
undeclared IO, network, actor, promise, wall-clock, random, UUID, or host call.

Normative pseudocode:

```text
verify_effect_policy(descriptor, runtime_context):
    policy = load_registered_effect_policy(descriptor.effect_policy_hash)

    if policy.schema_version is unknown:
        raise UnknownSchemaVersionError

    if policy.nondeterminism_barrier_class not in REGISTERED_BARRIER_CLASSES:
        raise EffectPolicyViolation

    if not runtime_context.allows(policy.nondeterminism_barrier_class):
        raise NondeterminismBarrierViolation

    for attempted_effect in runtime_context.observed_effects_before_execution:
        if attempted_effect not in policy.allowed_effects:
            raise NondeterminismBarrierViolation

    execute_inside_envelope(policy)

    for observed_effect in runtime_context.observed_effects_after_execution:
        if observed_effect not in policy.allowed_effects:
            raise NondeterminismBarrierViolation
```

The exact exception class is implemented later, but any violation MUST be
fail-closed and SHOULD align with `NondeterminismBarrierViolation`.

### 8.5 Valid / invalid examples

Valid pure policy:

```json
{
  "type": "effect_policy",
  "schema_version": "alpha3g.effect_policy.v1",
  "determinism_policy": "pure",
  "nondeterminism_barrier_class": "none",
  "allowed_effects": [],
  "idempotency": "required",
  "profile": "stable-canonical.v1"
}
```

Invalid live IO policy for Integrate v1:

```json
{
  "type": "effect_policy",
  "schema_version": "alpha3g.effect_policy.v1",
  "determinism_policy": "live_only",
  "nondeterminism_barrier_class": "io",
  "allowed_effects": [
    {"effect_namespace": "io_write", "allowed": true, "scope_bound": false, "scope_hash": null}
  ],
  "idempotency": "none",
  "profile": "stable-canonical.v1"
}
```

The invalid policy may exist as a descriptor for a live-only host feature, but it
MUST be rejected in any Integrate/Dream strict context.

## 9. Dependency manifest

`dependency_manifest_hash` binds a function descriptor to its declared logical
dependencies. It is analogous to an input-addressed dependency manifest: the
identity includes explicit inputs, not host-discovered imports.

```text
dependency_manifest_hash = sha256(stable-canonical.v1(dependency_manifest))
```

A dependency manifest SHOULD have this shape:

```json
{
  "type": "dependency_manifest",
  "schema_version": "alpha3g.dependency_manifest.v1",
  "dependencies": [
    {
      "ref_type": "function_descriptor",
      "ref_hash": "sha256:...",
      "declared_version": "1.0.0",
      "weak_pin": false
    },
    {
      "ref_type": "capability_grant",
      "ref_hash": "sha256:...",
      "declared_version": "1.0.0",
      "weak_pin": false
    },
    {
      "ref_type": "external_schema",
      "ref_hash": "sha256:...",
      "declared_version": "1.0.0",
      "weak_pin": false
    }
  ],
  "profile": "stable-canonical.v1"
}
```

`dependencies` MUST be an ordered array with deterministic ordering. The
ordering rule for v1 is:

```text
sort by (ref_type, ref_hash, declared_version) lexicographically after NFC
normalization and stable-canonical string validation.
```

### 9.1 `ref_type` taxonomy

Initial `ref_type` values are:

```text
function_descriptor
capability_grant
external_schema
effect_policy
captured_env_manifest
agent_definition_ref
memory_ref_schema
model_contract
policy_manifest
```

New `ref_type` values require schema registry approval. Unknown values are
fail-closed.

### 9.2 Cryptographic pinning

`declared_version` is human-readable metadata. It is not sufficient for forensic
identity by itself.

Each dependency MUST include `ref_hash`, a cryptographic hash of the canonical
content of the dependency artifact. Exact semver pinning without `ref_hash` is a
weak pin.

Weak pins are forbidden for internal project dependencies. Weak pins MAY be used
only for external system boundaries if all of the following are true:

```text
weak_pin: true is explicit
external boundary is declared in the capability/effect policy
runtime records the resolved artifact in the event log or envelope audit trail
replay either consumes the recorded artifact hash or fails closed
```

The manifest MUST NOT contain:

```text
floating versions (`latest`, `*`, ranges)
implicit transitive dependencies
host file paths
virtualenv paths
package manager cache paths
live module objects
runtime import handles
```

If a dependency is required at runtime but absent from the manifest, the runtime
MUST reject execution in any canonical context.

## 10. Captured environment boundary

Implicit closures are forbidden in FunctionDescriptor v1.

The descriptor MUST NOT inspect or serialize:

```text
__closure__
closure cell contents
free variables from enclosing Python scopes
runtime object bindings
implicit globals
module __dict__ snapshots
```

If a function requires external values, those values MUST be represented as one
of:

```text
explicit input parameters
capability grants
memory references
static config schema fields
explicit captured environment manifest
```

### 10.1 `captured_environment_manifest` schema

The explicit captured environment manifest is declarative, not introspective.
The canonical schema is:

```json
{
  "type": "captured_environment_manifest",
  "schema_version": "alpha3g.captured_environment_manifest.v1",
  "bindings": [
    {
      "binding_name": "MAX_RETRIES",
      "binding_kind": "value",
      "type_schema_hash": "sha256:...",
      "canonical_value_hash": "sha256:...",
      "source": "static_manifest"
    },
    {
      "binding_name": "profile_memory",
      "binding_kind": "memory_ref",
      "type_schema_hash": "sha256:...",
      "memory_ref_id": "sha256:...",
      "source": "declared_memory_ref"
    },
    {
      "binding_name": "fs_read",
      "binding_kind": "capability_grant",
      "type_schema_hash": "sha256:...",
      "capability_grant_hash": "sha256:...",
      "source": "declared_capability"
    }
  ],
  "profile": "stable-canonical.v1"
}
```

`bindings` MUST be deterministically ordered by `binding_name`, then
`binding_kind`, then the relevant hash field.

### 10.2 Binding kinds

Allowed `binding_kind` values are:

```text
value
memory_ref
capability_grant
config_ref
schema_ref
```

Rules:

| Binding kind | Required hash field | Notes |
|---|---|---|
| `value` | `canonical_value_hash` | Value must be serializable by `stable-canonical.v1`. Runtime objects are forbidden. |
| `memory_ref` | `memory_ref_id` | Must reference `alpha3g.memory_ref.v1` or later approved schema. |
| `capability_grant` | `capability_grant_hash` | Must reference a declared grant; runtime tool object is not serialized. |
| `config_ref` | `config_ref_hash` | Must reference static config manifest. |
| `schema_ref` | `schema_ref_hash` | Must reference a canonical schema artifact. |

Any other binding kind is rejected.

### 10.3 Empty manifest canonical form

For functions with no captured bindings, `captured_env_hash` MUST be the hash of
this exact canonical object:

```json
{
  "type": "captured_environment_manifest",
  "schema_version": "alpha3g.captured_environment_manifest.v1",
  "bindings": [],
  "profile": "stable-canonical.v1"
}
```

`null`, omitted fields, empty strings, or a missing `captured_env_hash` are
FORBIDDEN.

```text
captured_env_hash = sha256(stable-canonical.v1(captured_environment_manifest))
```

### 10.4 Valid / invalid binding examples

Valid static value binding:

```json
{
  "binding_name": "MAX_RETRIES",
  "binding_kind": "value",
  "type_schema_hash": "sha256:retry-count-schema",
  "canonical_value_hash": "sha256:stable-canonical-value",
  "source": "static_manifest"
}
```

Invalid runtime closure binding:

```json
{
  "binding_name": "client",
  "binding_kind": "value",
  "type_schema_hash": "sha256:...",
  "python_object_repr": "<OpenAIClient object at 0x...>",
  "source": "closure_cell"
}
```

The invalid binding is forbidden because it depends on runtime object identity,
closure traversal, and host memory addresses.

### 10.5 Enforcement boundary

Before hashing a descriptor, runtime/tooling MUST reject captured environment
manifests that include:

```text
runtime object repr/str
Python object id
closure cell source
module globals snapshot
host file path
live provider/client/socket/file handle
non-stable-canonical value
```

This closes `FUNC-01` at the specification level: captured environment data is
explicit, canonical, and fail-closed.

## 11. Explicitly forbidden identity sources

FunctionDescriptor v1 MUST NOT derive identity from:

```text
Python `__code__` object
Python bytecode
`inspect.getsource()` output
`ast.parse()` of runtime source files
`repr(function)` / `str(function)`
module or file-system absolute paths
runtime memory address
`id()`
wall-clock time
UUID generation
Python closure cells / `__closure__`
decorator wrapper chain unless explicitly declared in descriptor
compiled extension module binary hash
JIT-compiled cache artifact
`.pyc` cache file
host-specific import loader metadata
process-local environment variables
```

These sources may exist in the Runtime Envelope. They MUST NOT enter canonical
FunctionDescriptor identity.

---

## 12. Schema evolution policy

`declared_version` MUST be included in `function_descriptor_hash`.

Versioning rules:

```text
floating versions are forbidden
minor version changes still produce a new function_descriptor_hash
major version mismatch is a fail-closed incompatibility
runtime compatibility may be allowed only through an approved schema/profile registry
```

Semver is used for human governance only. It does not replace cryptographic
identity.

```text
same symbol + different descriptor hash = different descriptor
same declared_version + different descriptor hash = different descriptor
same descriptor hash + unregistered schema version = fail closed
```

A runtime applier MUST NOT silently treat two descriptor hashes as compatible
because the function names match.

Compatibility is an explicit registry decision, not an inference. Any registry
entry that authorizes compatibility MUST record:

```text
source descriptor hash
target descriptor hash
compatibility mode: exact | backward_read | migration_required
approved RFC / review reference
expiry or supersession rule, if any
```

Unknown schema versions, unknown profile ids, and unknown compatibility modes
MUST raise a fail-closed registry error before execution.

### 12.1 P0.5.7 standalone AgentSnapshot gate interpretation

P0.5.7 does not close the FunctionDescriptor runtime registry gates. It defines
only the minimum boundary required for standalone AgentSnapshot schema/value core:

```text
FUNC-03 dependency manifest taxonomy:
  not blocking standalone AgentSnapshot value objects that carry already-approved
  `function_descriptor_hash` / `agent_definition_ref` values;
  still blocks FunctionDescriptor runtime registry and dependency validation.

FUNC-04 schema evolution registry:
  not blocking standalone AgentSnapshot value objects if they use a local
  fail-closed schema/profile allowlist;
  still blocks central schema/profile compatibility and runtime deployment.
```

No P0.5.8 standalone AgentSnapshot code may instantiate a FunctionDescriptor
registry, resolve dependency manifests, infer schema compatibility, or accept
unknown `ref_type`, `schema_version`, or `profile` values. Unknown values must
fail closed.

## 13. Relation to Agent Canonicalization

`RFC-AGENT-CANONICALIZATION.md` uses `agent_definition_ref` as the v1 static
agent definition boundary. This RFC supplies the future prerequisite for
executable method identity.

### 13.1 Single-method agent bridge

For an agent whose executable contract is represented by one FunctionDescriptor:

```text
agent_definition_ref.manifest_hash MAY equal function_descriptor_hash
```

if the static manifest declares that this agent definition is exactly that
function contract.

### 13.2 Multi-method agent bridge

For multi-method agents, the agent manifest SHOULD hash an ordered method list:

```json
{
  "type": "agent_method_manifest",
  "schema_version": "alpha3g.agent_method_manifest.v1",
  "methods": [
    {
      "method_name": "respond",
      "function_descriptor_hash": "sha256:..."
    },
    {
      "method_name": "reflect",
      "function_descriptor_hash": "sha256:..."
    }
  ],
  "profile": "stable-canonical.v1"
}
```

The method list MUST be deterministically ordered by the `stable-canonical.v1` string value of `method_name` in ascending lexicographic order, then by `function_descriptor_hash`. Runtime object order, declaration insertion order, memory address, import traversal order, or reflection order are FORBIDDEN ordering inputs.

This bridge does not approve executable body identity. It only connects agent
static manifests to callable contract descriptors.

---

## 14. Determinism boundary for v1

FunctionDescriptor v1 shifts behavioral determinism enforcement to host runtime
verification and golden fixtures.

The v1 descriptor hash can remain stable while a host implementation changes
its internal executable body. Therefore:

```text
v1 descriptor hash is not behavioral proof.
v1 runtime deployments MUST rely on deterministic test/golden coverage to detect
implementation drift.
Strict behavioral replay requires v2 executable identity.
```

A host implementation that changes function behavior while preserving the same
v1 descriptor has not violated descriptor identity, but it may violate runtime
release governance if golden fixtures or replay tests change.

---

## 15. Runtime Envelope boundary

FunctionDescriptor data is canonical. Runtime callable objects are envelope data.

The Runtime Envelope may contain:

```text
Python function object
bound method
provider adapter
compiled cache
loaded module object
logger / tracer
performance counter
execution sandbox handle
```

None of those envelope objects may contribute to `function_descriptor_hash`.

Runtime implementations MUST bind envelope objects to descriptors by explicit
registry lookup, not by hashing live objects.

---

## 16. Future executable identity path

A future FunctionDescriptor v2 MAY introduce executable identity if and only if
all of the following are approved:

```text
canonical AST or CVM image format
host-independent parser / lowering version pin
closure serialization contract
import/module manifest contract
capability-to-body binding rules
zero host path leakage rule
drift analysis over representative functions
compatibility / migration plan from v1 descriptors
```

Candidate strategies:

| Strategy | Status | Notes |
|---|---|---|
| Canonical AST graph | Future | Requires approved normalizer; must avoid Python-specific AST drift. |
| Canonical CVM image | Future | Strongest target for Strict Layer 1; requires stable compiler/lowering. |
| Content-addressed logic module | Future | Hash canonical IR + manifest + dependency graph. |
| Python bytecode hash | Rejected | Host/Python-version specific. |
| `inspect.getsource()` hash | Rejected | Formatting/path/decorator drift. |

---

## 17. Approval criteria

This RFC is in `APPROVAL-CANDIDATE` status after independent verification of
`FUNC-01` and `FUNC-02` in `RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md`. Final
approval still requires a team vote under `RFC-PROCESS.md`.

Minimum approval criteria, verified for approval-candidate transition:

```text
FunctionDescriptor v1 contract identity model is explicit.
Executable body identity is explicitly out of v1 scope.
Canonical schema hashing rules are defined.
Dependency manifest pinning is defined.
Captured environment policy is fail-closed with an explicit manifest schema and empty-manifest form.
Effect policy aligns with nondeterminism barriers through an explicit enum and enforcement rule.
Dependency pinning and schema evolution rules are explicit enough for implementation review.
Forbidden identity sources are complete enough for implementation review.
Agent RFC bridge is explicit.
```

Runtime implementation remains blocked until this RFC is approved and a scoped
implementation patch is authorized.

---

## 18. Review finding seeds

The initial structured review registry is `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md`.
Expected initial findings include:

```text
FUNC-01 captured environment declaration contract
FUNC-02 effect policy schema and runtime enforcement boundary
FUNC-03 dependency manifest ref_type taxonomy
FUNC-04 schema evolution policy
FUNC-05 v2 executable identity / CVM gate
FUNC-06 relation to Agent Definition Ref
```

---

## 19. P0.5.2.1 status

P0.5.2.1 revised this RFC to resolve the initial BLOCKER findings `FUNC-01` and `FUNC-02` at author level.

```text
Runtime changes: none.
FunctionDescriptor runtime: not implemented.
AgentSnapshot runtime: still blocked.
Executable identity: still blocked.
AST/CVM normalization: still blocked.
```

## 20. P0.5.2.2 status

P0.5.2.2 independently verifies the `FUNC-01` and `FUNC-02` resolutions and moves this RFC to `APPROVAL-CANDIDATE v0.2-AC`.

```text
Verification result: accepted.
Verified findings: FUNC-01, FUNC-02.
Verification method: independent specification review against v1 contract, Agent Canonicalization dependency, Stable Canonical Identity profile, and Integrate/Dream nondeterminism barrier vocabulary.
Runtime changes: none.
FunctionDescriptor runtime: not implemented.
AgentSnapshot runtime: still blocked.
Executable identity: still blocked.
AST/CVM normalization: still blocked.
```

Next expected step: final team vote for `APPROVED` status under `RFC-PROCESS.md`.
