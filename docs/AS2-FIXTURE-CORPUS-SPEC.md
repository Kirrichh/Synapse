# AS2 Fixture Corpus Specification — P0.6.4

**Status:** DRAFT — fixture corpus contract  
**Fixture schema:** `alpha3g.as2_fixture.v1`  
**Profile:** `stable-canonical.v1`  
**Corpus path:** `tests/fixtures/as2/`

This document specifies the AS2 fixture corpus introduced in P0.6.4. The corpus
is a data-level executable specification for the approved AS2 RFC.

---

## 1. Top-level fixture schema

Every fixture must contain:

```json
{
  "case_id": "string matching filename stem",
  "schema_version": "alpha3g.as2_fixture.v1",
  "profile": "stable-canonical.v1",
  "polarity": "positive | negative",
  "aspect": "identity | model | memory | capability | envelope | subagent | authority",
  "rfc_reference": "RFC section reference",
  "rationale": "human-readable reason",
  "inputs": {},
  "expected_result": "valid | error"
}
```

Negative fixtures must additionally contain:

```json
{
  "expected_error": "ApprovedAS2ErrorName"
}
```

Positive fixtures must not contain `expected_error`.

---

## 2. Canonical data rules

Fixture files must be deterministic and platform-stable:

```text
UTF-8
JSON object at top level
sorted keys when serialized
no trailing generated artifacts
no null where empty list/object is semantically required
no wall-clock or random values
no live provider references
```

---

## 3. Naming convention

Fixture filenames follow:

```text
{polarity}_{aspect}_{condition}.json
```

Examples:

```text
positive_minimal_valid_projection_inputs.json
negative_memory_space_mismatch.json
negative_ambient_authority_forbidden.json
```

`case_id` must equal the filename stem.

---

## 4. Required corpus

| Fixture | Purpose |
|---|---|
| `positive_minimal_valid_projection_inputs.json` | Minimal valid explicit AS2 input set plus expected derivation record shape. |
| `negative_missing_identity_context.json` | Missing identity context maps to `AdapterIdentityContextMissingError`. |
| `negative_incomplete_identity_context.json` | Partial identity seed maps to `AdapterIdentityContextIncompleteError`. |
| `negative_unknown_model_ref.json` | Unknown model string maps to `ModelRefUnknownError`. |
| `negative_missing_memory_ref_source.json` | Missing memory source maps to `MemoryRefSourceMissingError`. |
| `negative_memory_space_mismatch.json` | Foreign/mismatched memory space maps to `AdapterMemorySpaceMismatchError`. |
| `negative_missing_capability_grant_source.json` | Missing grants with live tools present maps to `CapabilityGrantSourceMissingError`. |
| `negative_legacy_envelope_conflict.json` | Legacy `__type__`/`data` envelope conflict maps to `AdapterEnvelopeConflictError`. |
| `negative_inline_memory_rejected.json` | Inline memory in canonical path maps to `AdapterInlineMemoryRejectedError`. |
| `negative_subagent_out_of_scope.json` | Subagent/fracture graph maps to `AdapterSubagentOutOfScopeError`. |
| `negative_ambient_authority_forbidden.json` | Ambient authority request maps to `AdapterAmbientAuthorityError`. |

---

## 5. AdapterDerivationRecord fixture shape

The positive fixture must include an `expected_derivation_record` with this
minimal shape:

```json
{
  "schema_version": "alpha3g.adapter_derivation.v1",
  "profile": "stable-canonical.v1",
  "input_hashes": {
    "identity_context_hash": "sha256:...",
    "adapter_definition_source_hash": "sha256:...",
    "model_registry_snapshot_hash": "sha256:...",
    "memory_ref_source_hash": "sha256:...",
    "capability_grant_source_hash": "sha256:..."
  },
  "memory_space_policy": {
    "policy_version": "alpha3g.memory_space_policy.v1",
    "expected_memory_space_id": "sha256:..."
  }
}
```

P0.6.4 fixtures do not require production serialization of this record. They
only pin the shape expected by future implementation planning.

---

## 6. StaticModelRegistry fixture scope

P0.6.4 fixtures may use only:

```text
provider_namespace = mock
```

Real provider namespaces are not permitted in this corpus. `custom` remains a
future explicit registry-entry concern and is not a wildcard fallback.

---

## 7. Memory source fixture scope

An empty memory state must be represented as:

```json
{"refs": []}
```

Missing `memory_ref_source`, `null`, or omitted memory state are not equivalent to
empty memory and are represented by negative fixtures.

---

## 8. Harness metadata

Fixtures may include `harness_metadata` to represent checks that cannot execute
until an adapter exists. Example:

```json
{
  "harness_metadata": {
    "requires_sandbox_mock": true,
    "forbidden_calls": ["time.time", "os.environ.get"]
  }
}
```

Harness metadata is specification data only. It must not trigger runtime calls in
P0.6.4.
