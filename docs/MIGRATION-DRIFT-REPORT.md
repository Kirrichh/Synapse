# Stable Canonical Migration Drift Report

**Patch:** Alpha3g P0.4.7 / SI4-prep — Stable Canonical Drift Baseline & Migration Report  
**Status:** COMPLETED — read-only analysis; no consumer migration performed  
**Profile pair:** `alpha3g.local-json.v1` → `stable-canonical.v1`  
**Primary gate:** StateOverlay migration readiness for a future feature-flagged profile selector

This report is a gate artifact. It measures drift between the approved legacy
Alpha3g local value profile and the approved `stable-canonical.v1` value profile
on the current Integrate Category B fixture corpus before any StateOverlay,
Integrate, Dream, or golden-replay consumer is migrated.

---

## 1. Scope and invariants

P0.4.7 is a read-only analysis patch.

**Allowed:**

- create `tests/test_stable_canonical_drift_report_p047.py`;
- read generated Integrate golden artifact snapshots produced from the existing
  P0.3.5 conformance scenarios;
- call `synapse.canonical_service.compare_profile_hashes()`;
- update this report, the migration checklist, changelog, and planning gate.

**Forbidden:**

- switching `StateOverlay` to `stable-canonical.v1`;
- changing `state_overlay.py`, `interpreter.py`, `canonical_path.py`, or
  `golden_replay.py`;
- rewriting or migrating existing fixtures;
- changing any existing hash path;
- introducing profile flags into consumers.

---

## 2. Artifact set analyzed

The repository currently keeps Integrate golden coverage as dynamic conformance
fixtures in `tests/test_integrate_golden_p035.py` rather than committed JSON
artifact directories. P0.4.7 therefore records those scenarios into pytest
`tmp_path` directories, then reads the generated `history.json` snapshots for
analysis. The repository fixtures themselves are not modified.

Analyzed fixture scenarios:

| Fixture scenario | Event shape | Representative payloads analyzed |
|---|---|---|
| `integrate_committed_basic` | `integrate_committed` | full event, `/env/x` write-set entry, `new_value = 2` |
| `integrate_committed_body_skipped` | `integrate_committed` | full event, `/env/x` write-set entry, `new_value = 2` |
| `integrate_noop_read_only_outer_env` | `integrate_committed` | full event, `/env/read_only` write-set entry, `new_value = 1` |
| `integrate_aborted_barrier_violation` | `integrate_aborted` | full event, `overlay_summary` |
| `integrate_state_hash_round_trip` | `integrate_committed` | full event, `/env/a` write-set entry, `new_value = 99` |

Notes:

- `integrate_noop_read_only_outer_env` is named after the I6 no-op intent: the
  pre-existing outer variable is read but not reassigned. The local binding
  `read_only` is still an overlay write and is therefore present in the write-set.
- The analysis intentionally targets current real event payload shapes: event
  metadata, write-set entries, new values, and abort overlay summaries.

---

## 3. Observed drift categories

Machine-readable categories are defined by `synapse.canonical_service.DriftCategory`.

| Category | Count | Meaning |
|---|---:|---|
| `none` | 14 | Legacy local and stable canonical hashes match for the analyzed payload |
| `float_normalization` | 0 | No `-0.0` payload observed in current Integrate fixtures |
| `large_int_wrapper` | 0 | No large-int wrapper payload observed in current Integrate fixtures |
| `bytes_wrapper` | 0 | No bytes payload observed in current Integrate fixtures |
| `set_ordering` | 0 | No set/frozenset payload observed in current Integrate fixtures |
| `key_normalization` | 0 | No NFC key-normalization drift observed in current Integrate fixtures |
| `value_normalization` | 0 | No NFC value-normalization drift observed in current Integrate fixtures |
| `stable_type_rejection` | 0 | No value accepted by local but rejected by stable |
| `local_type_rejection` | 0 | No value rejected by local but accepted by stable |
| `both_rejected` | 0 | No value rejected by both profiles |
| `hash_drift` | 0 | No unexplained hash drift observed |

Summary:

```text
observed payloads: 14
breaking drift found: 0
rejected payloads: 0
unexplained hash drift: 0
```

---

## 4. Impact assessment

### Safe / expected

Current Integrate Category B event payloads are legacy-safe JSON shapes:

- strings;
- booleans;
- safe integers;
- lists;
- dictionaries with string keys;
- `None` in hash fields where a path did not previously exist.

These values are accepted by both profiles and produce identical hashes in the
analyzed corpus.

### Minor / expected drift not present in current corpus

The SI3 synthetic representative tests already classify these as expected drift
when present:

- `float_normalization` (`-0.0` → `0.0`);
- `large_int_wrapper`;
- `bytes_wrapper`;
- `set_ordering`;
- `key_normalization`;
- `value_normalization`.

None of those categories appear in current Integrate golden payloads.

### Breaking drift

No breaking drift was observed.

A future migration patch must still treat any newly observed `stable_type_rejection`,
`both_rejected`, `hash_drift`, or unexplained profile mismatch as `NO-GO` until a
specific migration or rejection policy is approved.

---

## 5. StateOverlay migration recommendation

**Verdict: GO — ready for feature-flagged StateOverlay migration.**

The current Integrate golden payload corpus shows no drift between
`alpha3g.local-json.v1` and `stable-canonical.v1` for the event/write-set shapes
that StateOverlay currently produces.

This GO does **not** authorize a hard switch. The next migration patch must still:

- add an explicit StateOverlay profile selector;
- keep `alpha3g.local-json.v1` as the default until explicitly changed;
- preserve legacy artifact interpretation;
- include dual-profile StateOverlay tests;
- reject or document any new non-JSON/stable-only values before enabling them in
  write-sets.

---

## 6. Go / No-Go rule for future drift reports

Future drift baseline reports must use this rule:

```text
GO:
  all current fixture payloads are category none, or only expected/minor categories
  with documented migration handling;
  no stable/local rejection;
  no unexplained hash_drift;
  no fixture rewrite required.

NO-GO:
  any breaking drift, type rejection, key collision, unexplained hash drift,
  fixture mutation requirement, or profile mismatch without an approved migration
  policy.
```

P0.4.7 satisfies the GO rule for the current Integrate Category B fixture corpus.
