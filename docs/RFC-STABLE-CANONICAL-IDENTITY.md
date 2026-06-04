# RFC: Stable Canonical Identity

**Status:** APPROVED — Alpha3g P0.4.3  
**Version:** v1.0  
**Target milestone:** Alpha3g / P0.4.x  
**Patch:** P0.4.3 Stable Canonical Identity team verification and approval gate  
**Runtime scope authorized:** none — documentation only  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Depends on:** `docs/DETERMINISM_CONTRACT.md`, `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md`, `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`  
**Consumers:** Dream strict-eligibility work, Integrate deferred gates, future actor/agent identity, future replay runner

This RFC defines the parent contract for canonical bytes, canonical identity, and
stable replay-facing value representation. Its purpose is to prevent Dream,
Integrate, agents, actors, storage, and future CVM replay from inventing
incompatible hashing and identity rules.

The current runtime already contains limited local canonicalization:

```text
synapse/state_overlay.py      Alpha3g I1 local canonical JSON subset
synapse/canonical_path.py     Alpha3g integrate-v1 canonical path profile
synapse/interpreter.py        integrate_committed / integrate_aborted hashes
```

Those implementations are valid for the Alpha3g integrate-v1 surface, but they
are **not** the final Stable Canonical Identity runtime. This RFC is the parent
specification that future patches must implement or explicitly subset.

---

## 1. Normative language

The words **MUST**, **MUST NOT**, **SHOULD**, **MAY**, and **FORBIDDEN** are
normative within Alpha3g design documents.

---

## 2. Design goals

Stable Canonical Identity has five goals:

1. Give every replay-relevant value one canonical byte representation.
2. Give every replay-relevant identity one deterministic derivation rule.
3. Separate canonical state from host metadata.
4. Provide a shared vocabulary for Dream, Integrate, agents, and future CVM
   replay.
5. Make unsupported values fail closed with `CanonicalSerializationError` rather
   than silently entering history, state hashes, or write-sets.

Non-goals for P0.4.0:

```text
runtime implementation
CVM/opcode integration
actor runtime migration
agent snapshot implementation
storage backend migration
OS sandboxing
external plugin trust policy
```

---

## 3. Canonical bytes are the source of truth

Any value participating in replay decisions, event hashes, state hashes,
`old_value_hash`, `new_value_hash`, `result_hash`, `pre_state_hash`,
`post_state_hash`, `write_set_hash`, `state_delta_hash`, or `subtrace_hash` MUST
be converted to canonical bytes before hashing.

```text
canonical_hash(value) = sha256(canonical_json_bytes(canonicalize(value)))
```

Forbidden inputs to canonical hashes:

```text
Python hash()
Python repr() for semantic values
object memory addresses
host-specific stack traces
absolute file paths
hostnames
PIDs
thread ids
process handles
runtime object ids
random UUIDs unless recorded and replay-consumed
wall-clock timestamps unless recorded and replay-consumed
```

Canonical bytes MUST be stable across:

```text
Python process restarts
PYTHONHASHSEED changes
host operating systems
filesystem path separator differences
future non-Python runners
```

---

## 4. Canonicalization profiles

A canonical payload MUST declare or inherit a canonicalization profile. A profile
identifies the exact canonicalization rules used by an event or artifact.

Initial profiles:

| Profile | Status | Purpose |
|---|---|---|
| `alpha3g.local-json.v1` | IMPLEMENTED subset | Current `StateOverlay` / Integrate v1 value hashing |
| `alpha3g.integrate-path.v1` | IMPLEMENTED subset | Current `/env/*` and `/memory/*` write-set paths |
| `stable-canonical.v1` | DRAFT | Target parent profile defined by this RFC |

Child RFCs MAY use a narrower profile only if they declare the subset and fail
closed outside that subset. They MUST NOT silently pretend that a narrower local
profile is the complete Stable Canonical Identity contract.

### 4.1 Profile and schema version handling

Stable Canonical Identity is fail-closed by default. A reader, replay runner,
canonicalizer, or applier MUST reject any profile or schema it cannot prove it
understands.

Version handling rules:

```text
unknown profile id       -> fail closed with PROFILE_UNSUPPORTED
missing profile id       -> fail closed unless the child RFC explicitly declares a legacy default
unknown major version    -> fail closed with PROFILE_VERSION_UNSUPPORTED
unknown minor version    -> fail closed unless an approved compatibility table marks it compatible
unknown schema version   -> fail closed with EVENT_SCHEMA_UNSUPPORTED
unknown applier mapping  -> fail closed with EVENT_APPLIER_UNSUPPORTED
```

Forward compatibility is not implicit. A future profile MAY declare a
compatibility table, but the table itself MUST be versioned and approved by the
owning RFC. Silent fallback from `stable-canonical.v2` to
`stable-canonical.v1`, or from a stable profile to an Alpha3g local subset, is
forbidden.

---

## 5. Canonical value allowlist

Canonical serialization is allowlist-based. A value is canonical-serializable
only if it is explicitly listed here or by an approved child RFC.

Allowed baseline values:

```text
null
bool
string
safe integer
supported finite float
list / tuple as ordered array
dict with canonical string keys
typed set wrapper
typed bytes wrapper
typed large-int wrapper
```

Forbidden by default:

```text
functions
closures
native builtins
bound methods
runtime handles
actor references
promises
agent instances
provider/backend objects
file handles
sockets
threads/tasks
custom Python objects without approved to_canonical()
objects with host identity only
```

A forbidden value that reaches canonical serialization MUST raise
`CanonicalSerializationError`.

---

## 6. JSON canonicalization baseline

Stable canonical JSON MUST follow the spirit of RFC 8785 / JSON Canonicalization
Scheme for JSON-native values:

- object properties sorted recursively by canonical string key;
- arrays preserve order;
- no insignificant whitespace;
- deterministic UTF-8 output;
- valid JSON only;
- no host-specific formatting;
- no implicit Python type coercion.

Canonical JSON bytes MUST be generated with a fixed separator and escaping
policy. Python implementation details such as insertion order MUST NOT affect
canonical bytes.

---

## 7. Unicode strings

All canonical strings MUST be normalized to Unicode NFC before serialization.

This applies to:

```text
string values
dict keys
path segments
memory keys
source locations
model names / provider ids when canonical
identity seed fields
schema-version strings
```

Invalid Unicode strings and lone surrogate values MUST fail canonical
serialization with `CanonicalSerializationError`.

If a caller provides non-NFC text for a value, the canonicalizer MAY normalize it
before serialization. If a caller provides a path or identity seed that must
already be canonical, the canonicalizer MAY reject non-NFC input to avoid hiding
multiple spellings of the same identity.

---

## 8. Numbers

### 8.1 Integers

JSON number emission is allowed only for integers in the JavaScript-safe integer
range:

```text
-(2^53 - 1) <= n <= (2^53 - 1)
```

Integers outside that range MUST be encoded as a typed decimal string:

```json
{ "__type__": "int", "value": "9223372036854775808" }
```

The `value` field MUST be base-10 with no leading `+`, no leading zeros except
literal `"0"`, and a leading `-` only for negative values.

### 8.2 Floats

`NaN`, `Infinity`, and `-Infinity` are forbidden in canonical paths and MUST
raise `CanonicalSerializationError`.

`-0.0` MUST be normalized to `0.0` before canonical serialization.

Floating-point serialization remains a portability boundary. Stable Canonical
Identity v1 allows finite floats only when the implementation can emit a stable
round-trip representation. Otherwise it MUST reject the value. Future RFCs MAY
introduce a canonical decimal type for strict financial/scientific workloads.

---

## 9. Containers

### 9.1 Lists and tuples

Lists are ordered arrays. Tuple values MAY be serialized as arrays only when the
child RFC states that tuple/list distinction is not semantically relevant.
Otherwise tuples MUST use a typed wrapper.

### 9.2 Dicts

Dict keys MUST be strings after canonicalization. Non-string dict keys are
forbidden because JSON would otherwise coerce keys such as `1` and `"1"` into
ambiguous payloads.

Rules:

```text
key MUST be str
key MUST be Unicode scalar text
key MUST be NFC-normalized for canonical bytes
values recursively canonicalized
properties sorted by canonical key
```

### 9.3 Sets

JSON has no native set type. Sets MUST be encoded as:

```json
{ "__type__": "set", "items": [ ... ] }
```

Sorting rule:

```text
PRIMARY SORT KEY: canonical_json_bytes(canonicalized_element)
FORBIDDEN: Python repr()
FORBIDDEN: Python hash()
```

Algorithm:

1. Recursively canonicalize each set element.
2. Serialize each canonicalized element to canonical JSON bytes.
3. Sort elements lexicographically by those byte strings.
4. Emit the sorted canonical values into `items`.

If any element is not canonical-serializable, the entire set serialization MUST
fail with `CanonicalSerializationError`.

### 9.4 Cycles

Circular references MUST be detected. A cycle MUST raise
`CanonicalSerializationError` with a canonical cycle path. Silent recursion
failure, host stack traces, or Python recursion-limit errors are forbidden in
canonical events.

---

## 10. Bytes

JSON has no native bytes type. Bytes MUST be encoded as:

```json
{
  "__type__": "bytes",
  "encoding": "base64url-nopad",
  "data": "SGVsbG8"
}
```

Rules:

- encoding is RFC 4648 base64url;
- no line breaks;
- padding is omitted in v1;
- decoder MUST reject non-canonical padded forms;
- value hash for bytes SHOULD be `sha256(raw_bytes)`, not hash of base64 text;
- canonical JSON representation uses the typed wrapper.

Large binary object externalization is out of scope for Alpha3g unless an
approved future RFC defines content-addressed storage semantics.

---

## 11. Canonical paths

Stable Canonical Identity separates two concerns:

1. logical path identity;
2. concrete path encoding profile.

Every canonical path MUST contain an explicit namespace prefix. Bare paths are
forbidden.

Baseline namespace pattern:

```text
/<namespace>/<encoded-key>
```

Current known path profiles:

| Profile | Used by | Encoding |
|---|---|---|
| RFC 6901 JSON Pointer | general JSON documents / future children | `~ -> ~0`, `/ -> ~1` |
| `alpha3g.integrate-path.v1` | current Integrate write-set paths | UTF-8 percent encoding `%XX` over NFC memory keys |

`RFC-INTEGRATE-REPLAY-APPLIER` v1 has already selected the
`alpha3g.integrate-path.v1` profile for `/memory/<key>` paths. That profile is
valid for Integrate v1 and is implemented in `synapse/canonical_path.py`.

Future child RFCs MUST declare which path profile they use. A replay runner MUST
NOT interpret a path without knowing the path profile associated with the event
schema.

---

## 12. Source locations and diagnostics

Any canonical `source_span_hash`, `failure_point`, or diagnostic location MUST be
derived from host-independent data:

```text
relative_path_from_project_root
forward slash separator (/), including on Windows
line
column
node_type
canonical node content hash when available
```

Forbidden in canonical diagnostic data:

```text
absolute host paths
home directories
temporary directories
hostnames
PIDs
Python stack traces
Python object ids
memory addresses
```

Host-specific debugging information MAY be stored as metadata, but MUST NOT
participate in canonical hashing or replay decisions.

---

## 13. Function and closure canonicalization

### 13.1 v1 policy

Stable Canonical Identity v1 does **not** approve general function or closure
serialization.

For Alpha3g Integrate v1, functions, closures, native builtins, bound methods,
and host callables in write-sets are forbidden and MUST raise
`CanonicalSerializationError`.

This matches the current Integrate implementation: `StateOverlay.set()` performs
eager canonical validation and rejects callables before they can enter overlay
state.

### 13.2 Future `FunctionDescriptor`

`FunctionDescriptor` is **not** part of `stable-canonical.v1`. It remains a
future extension and an implementation gate for function canonicalization. No
runtime patch may serialize functions, closures, bound methods, native builtins,
or host callables as canonical values until a future RFC approves the descriptor
profile and its conformance tests.

A future RFC MAY introduce a canonical function value only through a typed
descriptor such as:

```json
{
  "__type__": "function",
  "schema_version": "stable-function.v1",
  "compiler_profile": "synapse-parser-vX",
  "name": "handler",
  "module_id": "...",
  "ast_hash": "sha256:...",
  "bytecode_hash": "sha256:...",
  "closure_bindings_hash": "sha256:...",
  "closure_bindings": {
    "x": { "...": "canonical value" }
  }
}
```

Minimum future requirements before any `FunctionDescriptor` may enter canonical
state:

- AST and/or bytecode must have a canonical serialization independent of parser
  object ids and host memory addresses;
- the compiler/parser profile must be versioned and pinned;
- closure bindings must be canonical-serializable values;
- non-canonical captures fail with `CanonicalSerializationError`;
- native/host functions are represented only by approved symbolic ids;
- symbolic ids require an allowlist entry;
- allowlist entries must declare determinism, side effects, and replay contract;
- function descriptors must include enough source/module identity to prevent two
  different functions from colliding under the same name.

### 13.3 Builtin and native function allowlist

Native/builtin functions are forbidden as canonical values unless allowlisted.
An allowlist entry MUST include:

```text
symbol
schema_version
deterministic: true/false
side_effects: true/false
replay_contract: none | recorded-and-consumed | pure
canonical_result_type
```

Fail-closed decision rule:

```text
A builtin MAY participate in canonical execution only if:
1. deterministic == true;
2. side_effects == false;
3. replay_contract is pure OR recorded-and-consumed;
4. every argument is canonical-serializable under the active profile;
5. every result is canonical-serializable under the active profile; and
6. the builtin symbol is present in the approved allowlist for that profile.

Otherwise the builtin is FORBIDDEN in strict/canonical contexts and MUST fail
closed before host execution.
```

A builtin with `side_effects=True`, `deterministic=False`, missing allowlist
metadata, missing replay contract, or `replay_contract=none` MUST raise
`NondeterminismBarrierViolation` or `CanonicalSerializationError` before the
host callable is invoked.

The following are **not** allowlisted for canonical state in v1:

```text
print
time
random
uuid
host I/O
network I/O
filesystem I/O
provider/backend I/O
thread/task scheduling
```

They MAY be introduced only by a future recorded-and-consumed effect contract
that records the resource value in history and consumes it by replay cursor.

---

## 14. Agent and runtime object canonicalization

Agent instances are not canonical-serializable in v1. `INT-07` remains a
deferred Integrate gate because agent snapshots require a separate approved
contract.

A future `AgentSnapshot` contract MUST specify:

```text
agent class identity
schema version
canonical config
canonical memory refs
canonical model/provider identity
stable actor/agent id
excluded runtime handles
excluded caches
excluded process-local state
```

Runtime handles, provider clients, sockets, threads, local mailboxes, caches,
open files, and process ids MUST NOT enter canonical hashes.

---

## 15. Canonical time

Wall-clock time is metadata by default. Canonical program logic MUST NOT read
host wall-clock or host monotonic time directly. Canonical time has exactly two
approved replay sources in v1, both fail-closed if unavailable.

Approved replay sources:

```text
1. recorded-and-consumed time_read event
2. deterministic logical clock derived from approved canonical material
```

### 15.1 Recorded-and-consumed time

A canonical time read that observes external time MUST be recorded in LIVE and
consumed in REPLAY by cursor. REPLAY MUST NOT call the host clock.

Required event shape:

```json
{
  "event": "time_read",
  "schema_version": "stable-time.v1",
  "source": "external-recorded",
  "logical_tick": 42,
  "value": "2026-05-30T00:00:00Z",
  "value_hash": "sha256:...",
  "parent_event_hash": "sha256:..."
}
```

In REPLAY, the runner consumes the next `time_read` event, verifies
`schema_version`, `source`, `logical_tick`, `value_hash`, and parent binding, and
returns the recorded `value`. Missing, out-of-order, or mismatched `time_read`
events MUST fail closed with `CANONICAL_TIME_REPLAY_MISMATCH`.

### 15.2 Deterministic logical time

A child RFC MAY define logical time derived from canonical material without
recording each read. The derivation formula MUST be explicit and versioned.
Approved seed material MAY include:

```text
session genesis hash
event index
parent event hash
logical clock domain
profile id / schema version
```

A logical time source MUST NOT use a hidden host counter. If a counter is part of
the formula, it must be represented in canonical event/history material or be
derivable from the replay cursor.

This RFC defines the future API shape but does not implement it:

```text
runtime.get_canonical_time(source="recorded" | "logical")
```

If no approved recorded event or deterministic derivation exists, the API MUST
raise `CanonicalTimeUnavailable` or `NondeterminismBarrierViolation`.

Forbidden:

```text
time.time() in canonical control flow
host monotonic clock in canonical hashes
wall-clock elapsed duration unless recorded and replay-consumed
implicit process-local counters not represented in history
```

---

## 16. Deterministic identity generation

Host UUIDs, random ids, memory addresses, and timestamps are forbidden as
canonical identities unless recorded and replay-consumed.

Stable identities MUST be derived from canonical seed material:

```text
canonical_id(namespace, schema_version, seed_tuple) =
  namespace + ":" + base32(sha256(canonical_json_bytes(seed_tuple)))
```

A seed tuple MUST contain only canonical-serializable values and MUST include a
schema version. Seed tuples MUST also include explicit domain separation fields
so that different identity classes cannot collide by reusing the same payload.

Required seed material for future stable identities:

```text
profile id
identity kind/domain
schema version
session genesis hash or explicit session identity
parent event hash or canonical parent identity where applicable
stable namespace
ordinal / event index where applicable
canonical payload hash where applicable
```

Examples of future identity seeds:

```json
["stable-canonical.v1", "actor", "stable-actor.v1", "genesis:abc", "Greeter", "parent:abc", 0]
["stable-canonical.v1", "memory", "stable-memory.v1", "genesis:abc", "/memory/user", "session:xyz"]
["stable-canonical.v1", "event", "stable-event.v1", "genesis:abc", "integrate_committed", 17, "prev_hash"]
```

Identity derivation MUST be deterministic across replay. If uniqueness depends
on execution order, the order index MUST be part of the recorded deterministic
trace, not a host counter hidden outside history. If a derived identity collides
with an existing canonical identity in the same domain, the runtime MUST fail
closed with `CanonicalIdentityCollision`; it MUST NOT fall back to UUID, random,
wall-clock, object id, or process-local counters.

---

## 17. Canonical genesis state

The empty state for a session MUST have a deterministic canonical hash.

Baseline canonical genesis state:

```json
{
  "profile": "stable-canonical.v1",
  "schema_version": "stable-genesis.v1",
  "env": {},
  "memory": {},
  "agent_registry": {},
  "history_parent_hash": null
}
```

```text
genesis_state_hash = sha256(canonical_json_bytes(canonical_genesis_state))
```

A future session lifecycle RFC MAY add an explicit `session_genesis` event. Until
then, child RFCs MAY refer to the canonical genesis state hash for first-event
state validation.

Integrate v1 currently computes local env hashes over canonical user-visible env
bindings and excludes helper callables such as `print`, trust helpers, and host
runtime objects. This is an approved local subset, not a complete session-level
genesis contract.

For Integrate strict-eligibility work, the first `pre_state_hash` of a fresh
session MUST either:

```text
1. equal the child RFC's approved local genesis hash, if the child RFC declares a
   local profile such as alpha3g.local-json.v1; or
2. be explicitly bridged to stable_genesis_hash by a versioned migration or
   session_genesis event.
```

A runner MUST NOT silently treat an empty Python environment, helper-populated
interpreter environment, and stable canonical genesis state as interchangeable.

---

## 18. Schema versions and applier registry

Events that require replay-specific application semantics MUST carry a
`schema_version` field.

Replay runners MUST select the applier by event type and schema version:

```text
(event.type, event.schema_version) -> replay applier
```

A runner MUST NOT apply v2 semantics to a v1 event. Unknown schema versions MUST
fail with `EVENT_SCHEMA_UNSUPPORTED` rather than silently falling back.

Version policy is fail-closed:

```text
unknown event schema major version  -> EVENT_SCHEMA_UNSUPPORTED
unknown event schema minor version  -> EVENT_SCHEMA_UNSUPPORTED unless compatibility is explicitly declared
missing event schema_version        -> EVENT_SCHEMA_MISSING unless a legacy child RFC declares a default
unknown canonicalization profile    -> PROFILE_UNSUPPORTED
unknown profile major version       -> PROFILE_VERSION_UNSUPPORTED
unknown profile minor version       -> PROFILE_VERSION_UNSUPPORTED unless compatibility is explicitly declared
missing profile id where required   -> PROFILE_MISSING
missing applier registry entry      -> EVENT_APPLIER_UNSUPPORTED
```

Canonicalization profiles are part of schema semantics. If an event schema
changes canonical bytes, path encoding, or identity derivation, it MUST use a new
schema version or an explicit migration profile. Forward compatibility is
available only through an approved compatibility table owned by the profile/RFC.

---

## 19. Migration strategy

Stable identity migrations MUST be explicit. Silent reinterpretation of existing
history is forbidden.

A migration MAY be allowed only if:

1. the old schema and new schema are both known;
2. the migration function is deterministic;
3. the old payload hash is verified before migration;
4. the new payload hash is recorded;
5. the migration metadata does not alter historical facts;
6. replay can audit the migration step.

Recommended migration event shape:

```json
{
  "event": "canonical_migration",
  "schema_version": "stable-migration.v1",
  "from_profile": "alpha3g.local-json.v1",
  "to_profile": "stable-canonical.v1",
  "old_hash": "sha256:...",
  "new_hash": "sha256:...",
  "migration_id": "..."
}
```

Historical artifacts SHOULD remain readable through their original profile even
after a newer profile is approved.

Artifact compatibility rules:

```text
profile id stored in artifact/event where canonical bytes are replay-relevant
old artifacts replayed using their recorded profile
no silent re-hash under a newer profile
migration requires explicit migrator/applier registry entry
unknown or missing profile fails closed unless a legacy child RFC declares it
```

Alpha3g Integrate and Dream golden artifacts recorded under local profiles remain
valid under those profiles. They MUST NOT be reinterpreted as
`stable-canonical.v1` artifacts unless a `canonical_migration` event is present
and verified.

---

## 20. Security and side-effect policy

Canonicalization MUST be pure. It MUST NOT:

```text
perform network I/O
perform filesystem writes
read wall-clock time
read randomness
allocate runtime ids
invoke user functions
invoke provider/backend clients
mutate runtime state
```

Canonicalization may allocate local temporary structures needed to produce bytes,
but those allocations MUST NOT influence the result.

---

## 21. Relationship to Dream and Integrate

Dream and Integrate now both use recorded nondeterminism to achieve Category B
replay-safety:

```text
Dream:     dream_completed consumed in REPLAY
Integrate: integrate_committed / integrate_aborted consumed in REPLAY
```

Stable Canonical Identity is required before either surface can claim broader
Strict Layer 1 eligibility across complex values, functions, agents, and stable
runtime identity.

Integrate-specific dependencies:

```text
INT-04 resource cleanup / promises       requires stable resource identity before strict eligibility
INT-05 genesis baseline                  covered by §17, runtime implementation pending
INT-06 durable idempotency               requires stable event/session identity
INT-07 agent canonicalization            requires §14 future AgentSnapshot contract
INT-08 namespace semantic completeness   must bind path profile + namespace semantics
```

Dream-specific dependencies:

```text
closure/function handling                requires §13 future FunctionDescriptor
canonical time/random barriers           require §15 recorded time/random contracts
future consume-only/subtrace model       requires profile-bound state/subtrace hashes
```

---

## 22. Acceptance criteria for this RFC

This documentation-only RFC satisfied the `APPROVAL-CANDIDATE` gate in P0.4.2 and the `APPROVED` gate in P0.4.3 because:

- it explicitly distinguishes local Alpha3g subsets from the future stable
  parent contract;
- it defines an allowlist-based canonical value policy;
- it defines function/closure v1 rejection and future descriptor requirements;
- it defines canonical time replay sources and fail-closed behavior;
- it defines builtin allowlist fail-closed rules for side effects and replay
  contracts;
- it defines schema/profile version rejection behavior;
- it defines deterministic identity derivation rules with domain separation;
- it defines migration and artifact compatibility requirements;
- it maps the RFC back to Dream and Integrate deferred gates;
- it authorizes zero runtime changes.

Runtime implementation of Stable Canonical Identity is authorized only under separate approved implementation patches. P0.4.3 approves the parent RFC but includes no runtime changes.

---

## 23. Review resolution status for P0.4.2

P0.4.2 addresses the P0.4.1 structured review findings as follows:

| Finding | Status after P0.4.2 | RFC resolution |
|---|---|---|
| STABLE-01 | VERIFIED | §15 defines recorded-and-consumed time and deterministic logical time replay sources; P0.4.3 team verification accepted the fail-closed replay-source rule. |
| STABLE-02 | VERIFIED | §13.3 defines builtin allowlist fail-closed rules and forbids side-effecting builtins without recorded contracts; P0.4.3 team verification accepted the policy. |
| STABLE-03 | VERIFIED | §4.1 and §18 define fail-closed unknown profile/schema behavior; P0.4.3 team verification accepted the version drift policy. |
| STABLE-04 | DEFERRED implementation gate | §13.2 makes FunctionDescriptor future-only and non-v1. |
| STABLE-05 | DEFERRED implementation gate | §14 excludes runtime fields and keeps AgentSnapshot future-only. |
| STABLE-06 | DEFERRED implementation gate | §16 defines domain-separated seed material and collision fail-closed behavior. |
| STABLE-07 | DEFERRED implementation gate | §19 defines profile-pinned artifact compatibility and explicit migration. |
| STABLE-08 | DEFERRED implementation gate | §17 defines stable genesis and local-profile bridge requirements. |
| STABLE-09 | ACKNOWLEDGED | §24 implementation acceptance criteria remain the test planning surface. |
| STABLE-10 | ACKNOWLEDGED | §4.1, §18, and future profile registry work govern lifecycle. |

P0.4.3 independent team verification marked STABLE-01, STABLE-02, and STABLE-03 `VERIFIED` under `RFC-PROCESS.md`.

---

## 24. Approval status for P0.4.3

P0.4.3 completes team verification for this RFC and approves
`RFC-STABLE-CANONICAL-IDENTITY.md` as the v1.0 parent contract for future
Stable Canonical Identity runtime work.

The approval gate verified the following review outcomes:

| Finding | P0.4.3 result | Approval note |
|---|---|---|
| STABLE-01 | VERIFIED | Canonical time has an explicit replay source: either recorded-and-consumed `time_read` events or an approved deterministic logical-clock derivation. Host clocks and implicit process-local counters remain forbidden. |
| STABLE-02 | VERIFIED | Builtin allowlisting is fail-closed: side-effecting or nondeterministic builtins are forbidden unless an explicit recorded-and-consumed replay contract exists. |
| STABLE-03 | VERIFIED | Unknown profile ids, major versions, schemas, or missing appliers fail closed unless an approved compatibility table explicitly allows the version. |
| STABLE-04..STABLE-08 | DEFERRED | These remain implementation gates and do not block v1.0 approval. Runtime patches that touch their scope must resolve the applicable gate before merge. |
| STABLE-09..STABLE-10 | ACKNOWLEDGED | These are accepted v1 governance / acceptance-planning boundaries. |

Two cross-contract checks were also verified:

1. Existing Alpha3g Integrate Category B artifacts that use local
   `pre_state_hash` / `post_state_hash` profiles are not invalidated by v1.0.
   Formal stable genesis remains deferred under STABLE-08 / INT-05 before
   Strict Layer 1 eligibility.
2. `alpha3g.local-json.v1` and `alpha3g.integrate-path.v1` are accepted
   Alpha3g local/legacy profiles for existing Category B artifacts. New stable
   canonical work MUST target `stable-canonical.v1`; new runtime modules MUST
   NOT introduce additional canonical hash paths based on Alpha3g local profiles
   unless explicitly scoped as compatibility work.

Approval of this RFC authorizes future scoped runtime implementation patches,
but P0.4.3 itself authorizes and contains no runtime changes.

---

## 25. Implementation acceptance criteria for future patches

A future runtime implementation MUST include tests for:

```text
NFC string normalization
lone surrogate rejection
safe integer / large integer boundary
NaN / Infinity rejection
-0.0 normalization
non-string dict key rejection
set canonical sorting
bytes base64url-nopad canonical form
cycle detection
function/callable rejection
canonical path profile selection
deterministic identity derivation
schema-version applier routing
migration hash preservation
```

It MUST also verify that existing Integrate and Dream golden fixtures remain
unchanged unless a migration profile is explicitly invoked.

---

## 26. Open questions

The following remain outside P0.4.x approval and require future RFCs or implementation
plans:

```text
full FunctionDescriptor approval
AgentSnapshot contract
ActorRef / stable actor id contract
habit identity
storage backend canonical ids
canonical random API
compiler / bytecode canonical hash rules
plugin host-symbol registry
OS-level sandboxing
cross-language canonical float conformance suite
chaos determinism CI
```

These are not blockers for accepting this document as a DRAFT parent contract.
