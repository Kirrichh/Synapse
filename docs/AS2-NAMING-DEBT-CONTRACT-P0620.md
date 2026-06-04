# P0.6.20 — AS2 Naming Debt Contract / Legacy Alias Removal

Status: **COMPLETED — CONTRACT PHASE**

P0.6.20 completes the AS2 model-selection naming migration that began in
P0.6.16. The validation and projection boundary now accepts only the canonical
`model_selection_source` input.

## Migration evidence

P0.6.19 introduced static migration-audit coverage proving that removed AS2
legacy selector aliases were outside primary code paths. P0.6.20 uses that
machine-readable evidence to authorize Contract-phase removal.

Contract decision:

```text
P0.6.16 — Expand: canonical model_selection_source added
P0.6.19 — Evidence: migration audit green
P0.6.20 — Contract: deprecated aliases removed
```

## Code changes

The Contract phase removes compatibility logic from the AS2 validation boundary:

- `validate_as2_inputs(...)` accepts only `model_selection_source` for model
  selection.
- `_resolve_model_selection(...)` contains no legacy fallback branch.
- the expand-phase deprecation warning path is removed.
- the bridge accepts only `model_selection_source` for Host Pre-Stage model
  selection.
- `PreparedAS2Inputs.to_validate_kwargs()` continues to emit only
  `model_selection_source`.

## Permanent guard

The P0.6.19 migration audit is converted into a permanent reintroduction guard:

```text
tests/test_as2_legacy_reintroduction_guard.py
```

The guard blocks removed selector aliases from reappearing in `synapse/` code or
primary tests. Documentation may still mention historical aliases when describing
past phases or migration history.

## Explicitly not included

P0.6.20 is cleanup only. It does not introduce:

```text
project_validated_as2_inputs(...) calls from bridge or skeleton
AgentSnapshot construction in bridge or skeleton
production Host providers
production AS2GateController
persisted gate mutation
CAS/storage I/O
degraded mode
production ENABLED state
runtime wiring expansion
Integrate/Dream/CVM wiring
```

## Result

The AS2 preparation/validation boundary is now canonicalized around
`model_selection_source`, and the codebase carries a permanent guard against
reintroducing the removed compatibility path.
