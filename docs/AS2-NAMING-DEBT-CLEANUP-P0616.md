# P0.6.16 — AS2 Naming Debt Cleanup / Expand-Contract Refactor

Status: **COMPLETED — EXPAND PHASE; CONTRACT COMPLETED IN P0.6.20**

P0.6.16 cleans the AS2 model-selection naming boundary before runtime wiring.
It introduces the canonical `model_selection_source` validation input while
retaining a deprecated compatibility alias until the later P0.6.20 Contract phase.

**P0.6.20 update:** the Contract phase is now complete. The validation
boundary accepts only `model_selection_source`, and removed selector aliases are
blocked by a permanent reintroduction guard.

## Scope

P0.6.16 changes the AS2 validation/bridge contract only:

- `synapse/agent_snapshot_adapter.py`
- `synapse/agent_snapshot_bridge.py`
- AS2 fixtures and tests that exercise validation/bridge preparation
- this document and `docs/CHANGELOG.md`

## Expand-phase contract

P0.6.16 introduced the canonical validation input:

```text
model_selection_source = {"model": "<static-registry-key>"}
```

During the expand window, a deprecated compatibility input remained accepted
only as a temporary alias. P0.6.20 removes that compatibility path.

## Contract-phase result

P0.6.20 removes the deprecated alias from the AS2 validation API.
`_resolve_model_selection(...)` now resolves only `model_selection_source.model`
and fails closed when the canonical selector is absent or malformed.

## Bridge behavior

`PreparedAS2Inputs.to_validate_kwargs()` now emits the canonical key:

```text
model_selection_source
```

It no longer emits `legacy_agent_runtime_to_dict` and no longer carries the
previous `_MODEL_SELECTOR_DEBT_NOTE` shim.

## Fixture and test migration

Primary AS2 and bridge fixtures were migrated to `model_selection_source`.
Legacy alias behavior is now isolated to compatibility tests that assert the
expand-phase deprecation signal.

Additional coverage includes:

- canonical validation path;
- expand-phase legacy compatibility coverage before P0.6.20;
- Contract-phase removal coverage after P0.6.20;
- bridge DTO canonical emission;
- absence of `_MODEL_SELECTOR_DEBT_NOTE`;
- preservation of the no-projection and no-`AgentSnapshot` bridge boundary.

## Explicitly not included

P0.6.16 did not perform the Contract phase. P0.6.20 has now removed the compatibility path.

Still locked:

```text
runtime wiring
runtime feature flag system
production Host providers
production Provider Protocols
project_validated_as2_inputs(...) inside bridge
AgentSnapshot construction inside bridge
AgentRuntime / Environment changes
CAS/storage I/O
degraded mode
Integrate/Dream/CVM wiring
```

## Contract phase completion

P0.6.20 completes the Contract phase after P0.6.19 migration evidence. Removed
selector aliases must not reappear in code or primary tests;
`tests/test_as2_legacy_reintroduction_guard.py` enforces this permanently.
