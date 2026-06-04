# Integrate Migration Drift Report — Alpha3g P0.4.9 / SI5-prep

**Status:** COMPLETED — read-only drift baseline  
**Verdict: GO — ready for feature-flagged Integrate hash-path migration**  
**Profile pair:** `alpha3g.local-json.v1` → `stable-canonical.v1`  
**Runtime migration:** none in this patch  
**Consumer impact:** none

This report is the SI5-prep gate artifact for future Integrate hash/event-path
migration. It applies the already-approved Stable Canonical drift-analysis method
to real Integrate Category B artifact payloads before any consumer is switched to
`stable-canonical.v1`.

P0.4.9 is read-only analysis. It does not modify `interpreter.py`,
`evaluate_integrate()`, `StateOverlay`, `canonical_path.py`, golden replay
helpers, stored fixtures, event schemas, CVM/opcodes, actor runtime, or any
existing hash path.

---

## 1. Analyzed artifact set

The drift baseline uses the current I6 Integrate scenario corpus and creates
throwaway artifact snapshots under pytest temporary directories. After recording,
the test reads only `history.json` and `manifest.json` payloads.

Analyzed scenarios:

| Scenario | Event shape |
|---|---|
| `integrate_committed_basic` | `integrate_committed` with one `/env/x` write |
| `integrate_committed_body_skipped` | `integrate_committed`; body-skip replay proof corpus |
| `integrate_noop_read_only_outer_env` | `integrate_committed`; read-only outer value plus local overlay binding |
| `integrate_aborted_barrier_violation` | `integrate_aborted` with barrier metadata and overlay summary |
| `integrate_state_hash_round_trip` | `integrate_committed`; pre/post state hash round-trip corpus |

Representative payload fragments per scenario include:

- committed event hash fields: `pre_state_hash`, `post_state_hash`,
  `write_set_hash`, `schema_version`;
- full `write_set` lists;
- individual `write_set` entries;
- write-set hash fields (`old_value_hash`, `new_value_hash`, `path`, `op`);
- concrete `new_value` payloads;
- aborted event hash fields;
- `integrate_aborted.overlay_summary`;
- sanitized abort reason payloads;
- `manifest.final.state_sanity` fragments.

---

## 2. Observed result

Empirical baseline:

```text
observed payload fragments: 28
drift_category = none: 28
breaking drift found: 0
rejected payloads: 0
unexplained hash drift: 0
```

All analyzed Integrate hash/event-path fragments produced identical
`alpha3g.local-json.v1` and `stable-canonical.v1` hashes under the current
fixture corpus.

---

## 3. Drift category table

| Category | Count | Assessment |
|---|---:|---|
| `none` | 28 | Safe. No semantic or hash drift observed. |
| `float_normalization` | 0 | Not present in current Integrate corpus. Would be expected/minor if found. |
| `large_int_wrapper` | 0 | Not present in current Integrate corpus. Would require explicit migration review if found. |
| `bytes_wrapper` | 0 | Not present in current Integrate corpus. Would require explicit profile metadata if introduced. |
| `set_ordering` | 0 | Not present in current Integrate corpus. Expected only under stable profile opt-in. |
| `key_normalization` | 0 | Not observed. Any future key-normalization drift must be reviewed for collision risk. |
| `value_normalization` | 0 | Not observed. |
| `stable_type_rejection` | 0 | None. |
| `local_type_rejection` | 0 | None. |
| `both_rejected` | 0 | None. |
| `hash_drift` | 0 | None. |

---

## 4. Impact assessment

The current Integrate Category B artifact corpus is compatible with a future
feature-flagged stable-canonical Integrate migration. The baseline did not find
any of the conditions that would make SI5 a no-go:

- no structural hash drift;
- no type rejection by either profile;
- no NFC key/value normalization drift;
- no write-set shape incompatibility;
- no aborted overlay summary incompatibility;
- no manifest state-sanity incompatibility.

This does **not** authorize a hard switch. It only authorizes a future scoped
migration patch that adds an explicit Integrate profile selector or compatibility
boundary while preserving legacy replay for existing Category B artifacts.

---

## 5. Go / no-go criteria

### GO criteria

A future SI5 migration patch may start if all are true:

- all real Integrate fixture payload fragments classify as `none` or an explicitly
  documented expected/minor category;
- no `breaking` / `hash_drift` / rejected payload appears in the current corpus;
- legacy fixtures remain replayable under their recorded local profile;
- migration remains feature-flagged and explicit;
- `interpreter.py` default behavior is not changed in the analysis patch.

P0.4.9 satisfies these criteria for the current corpus.

### NO-GO criteria

Any of the following would block SI5:

- `hash_drift` on an existing committed or aborted event payload;
- `stable_type_rejection` for an existing payload;
- key normalization collision;
- unexplained write-set hash mismatch;
- requirement to rewrite golden fixtures before profile-selection support exists.

None were observed.

---

## 6. Migration recommendation

**Recommendation:** proceed to a future feature-flagged Integrate hash-path
migration.

Suggested next patch:

```text
P0.4.10 / SI5 — Integrate stable-canonical profile selector
```

Required constraints for that future patch:

- legacy default remains unchanged;
- existing Category B artifacts continue to replay under their recorded local
  profile;
- stable profile must be opt-in;
- any event/profile metadata must be explicit and fail-closed;
- no CVM/opcode, actor runtime, canonical time, deterministic ID, or AgentSnapshot
  work is included.
