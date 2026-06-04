# AS2 Drift / Invariant Harness Design — P0.6.4

**Status:** DRAFT — fixture/invariant harness design  
**Scope:** test-only validation of static AS2 fixture data  
**Runtime scope authorized:** none

This document defines the P0.6.4 drift/invariant harness. The harness is an
executable specification guardrail. It validates the AS2 fixture corpus before
any adapter implementation exists.

---

## 1. Principle

The harness validates **data contracts**, not adapter behavior.

It must answer:

```text
Are the fixture inputs structurally aligned with the approved AS2 RFC?
Do negative fixtures encode the intended failure condition as data?
Does each negative fixture map to an approved typed error name?
Does the positive fixture contain enough data to serve as future adapter oracle?
```

It must not answer:

```text
Does to_agent_snapshot() produce a snapshot?
Does the adapter raise a Python exception?
Does AgentRuntime serialize differently?
```

Those questions are future implementation work.

---

## 2. Harness file

The P0.6.4 harness lives at:

```text
tests/test_as2_fixture_matrix_p064.py
```

It reads static JSON fixtures from:

```text
tests/fixtures/as2/
```

The harness must be deterministic:

```text
no random
no uuid
no time
no os.environ
no platform-dependent path traversal order
explicit sorted fixture loading
```

---

## 3. Allowed validations

| Validation | Purpose |
|---|---|
| Required top-level fixture fields | Prevent malformed data assets. |
| `case_id` equals filename stem | Prevent accidental fixture/metadata drift. |
| `schema_version` equals `alpha3g.as2_fixture.v1` | Pin corpus schema. |
| `profile` equals `stable-canonical.v1` | Align with canonical profile. |
| `expected_error` is in approved taxonomy | Prevent invented or generic errors. |
| Real providers are absent | Keep P0.6.4 registry mock-only. |
| Memory mismatch fixture has actual mismatch | Ensure negative fixture is meaningful. |
| Inline memory fixture contains inline memory marker | Ensure AS2-03 negative condition is represented. |
| Ambient authority fixture lists forbidden calls in metadata | Represent AS2 purity violation without executing it. |
| Positive fixture contains AdapterDerivationRecord shape | Preserve future audit oracle. |

---

## 4. Forbidden validations

The P0.6.4 harness must not:

```text
import synapse.agent_snapshot_adapter
call to_agent_snapshot()
instantiate or import AS2 exception classes
read live AgentRuntime objects
mutate fixture data
write generated snapshots
write generated derivation records
infer a production adapter API
```

---

## 5. Fixture polarity

P0.6.4 uses two fixture polarities:

```text
positive: fixture is structurally valid and represents a future valid adapter input set
negative: fixture intentionally violates one AS2 invariant and maps to expected_error
```

Negative fixtures are seeded faults. They are not expected to be consumed by an
adapter in P0.6.4; they exist so future implementation can be tested against a
stable corpus.

---

## 6. Error-name policy

`expected_error` is a string diagnostic item, not a Python class.

Allowed error names for P0.6.4:

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

The generic base name `AdapterError` is forbidden as a fixture expectation.

---

## 7. Memory-space fixture policy

P0.6.4 does not implement `derive_memory_space_id()`. Fixtures use explicit
expected values:

```json
{
  "memory_space_policy_version": "alpha3g.memory_space_policy.v1",
  "expected_memory_space_id": "sha256:..."
}
```

The harness may compare fixture refs against `expected_memory_space_id`, but it
must not compute that id.

---

## 8. StaticModelRegistry fixture policy

P0.6.4 uses only the `mock` provider namespace. Real providers are forbidden in
fixtures:

```text
openai
anthropic
local
custom without explicit registry-entry semantics
```

This validates mapping mechanics without importing live provider semantics.

---

## 9. Future implementation use

A future P0.6.5 adapter skeleton must satisfy this corpus without changing the
meaning of fixtures. If implementation work discovers missing fixture coverage,
that must be handled through a scoped fixture-corpus update or a return-to-plan
review, not silent implementation drift.
