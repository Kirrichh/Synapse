# P2c Evidence — Idempotency, Multi-cycle and Concurrency Closure

Status: `P2c IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` after this S1 evidence sync merges.

This evidence record summarizes P2c implementation PR #21 and the post-merge verification state for the approved CLI durable execution scope.

## Scope

P2c closes the approved contract in:

```text
RFC-ASYNC-EXECUTION-AMENDMENT-02
```

P2c covers:

- multi-cycle durable campaigns;
- stale IDs across later boundaries;
- duplicate same-hash and different-hash resume semantics across cycles;
- late-boundary concurrent resume races;
- P2a/P2b artifact compatibility;
- schema `1.0.0` preservation;
- no migration / no rewrite-on-read;
- no new exit codes;
- no daemon, transport, scheduler, signal inbox, auto-recovery or force-unlock.

## Commit anchors

| Item | SHA / ID |
|---|---|
| Contract | `docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md` |
| Amendment-02 merge commit | `8dabc543dfa10494b0c869593c81e56589e80164` |
| P2c implementation PR | `#21` |
| P2c implementation PR head before merge | `ac6bd049950a20539d7306c6092af889c4baf2ff` |
| P2c post-merge commit on `main` | `4eb2ec86c91a5412ce183261000bdc884b1b0d85` |
| P2c S1 branch | `codex/s1-sync-after-p2c` |

## Changed files in implementation PR #21

PR #21 changed only:

```text
tests/test_durable_execution.py
```

No production files changed.

No docs/evidence files changed in the implementation PR.

No workflow files changed.

Forbidden areas remained untouched:

```text
synapse/application.py
synapse/cli.py
synapse/interpreter.py
synapse/runtime/replay_engine.py
synapse/runtime/actor_runtime.py
synapse/hardening.py
synapse/parser.py
synapse/ast.py
examples/**
.github/**
docs/RFC-ASYNC-EXECUTION*.md
docs/CAPABILITY_MATURITY_MATRIX.md
docs/ASYNC_DURABLE_EXECUTION_STATUS.md
docs/evidence/P2B_EVIDENCE.md
```

## Implementation path

Path: `Path A — tests/evidence-only`.

Reason: Phase 0 and PR #21 evidence showed that accepted P2b production mechanics already satisfy the P2c durable behavior contract. P2c required expanded acceptance/evidence coverage, not production behavior changes.

Permanent evidence placement decision in PR #21:

```text
EVIDENCE_DEFERRED_TO_POST_MERGE_S1
```

Therefore this file is created in S1 after merge, not in the implementation PR.

## Phase 0 evidence summary

PR #21 body recorded Phase 0 as PASS against exact `origin/main`:

```text
8dabc543dfa10494b0c869593c81e56589e80164
```

Phase 0 covered:

| Probe | Result |
|---|---|
| 3-cycle campaign | PASS |
| schema `1.0.0` with 3 resolved entries | PASS |
| prior same-hash duplicate | PASS |
| prior different-hash duplicate | PASS |
| unknown ID | PASS |
| output suppression across cycles | PASS |
| mixed-reason representability | PASS |
| malformed idempotency contradiction | PASS |
| late-boundary forced-overlap constructibility | PASS |
| P2a compatibility | PASS |
| P2b compatibility | PASS |
| baseline test state | PASS |

## P2c acceptance evidence

### Multi-cycle campaign

Observed lifecycle:

```text
PENDING_1 -> PENDING_2 -> PENDING_3 -> COMPLETED
```

Observed invariants:

```text
sequences = [1, 2, 3]
revisions = [1, 2, 3, 4]
resolved_suspensions count = [0, 1, 2, 3]
artifact_schema_version = "1.0.0"
final status = COMPLETED
```

Output-prefix suppression was verified cycle-by-cycle:

```text
before-1
after-1
after-2
after-3
```

### Mixed-reason campaign

A single `run_id` produced public runtime reasons:

```text
awaiting_external_signal
awaiting_promise
```

No parser, AST, interpreter, replay engine, workflow or production behavior change was required.

### Duplicate and stale matrix

Verified matrix:

| Case | Result |
|---|---|
| prior resolved same hash after one advance | stored semantic result; artifact unchanged |
| prior resolved same hash after two advances | stored semantic result; artifact unchanged |
| prior resolved different hash | exit `24`, `RESOLUTION_CONFLICT`; artifact unchanged |
| current resolved same hash | stored semantic result; artifact unchanged |
| current resolved different hash | exit `24`, `RESOLUTION_CONFLICT`; artifact unchanged |
| unknown ID | exit `23`, `STALE_OR_UNKNOWN_SUSPENSION`; artifact unchanged |
| malformed resolved entry with recomputed artifact hash | exit `21`, `ARTIFACT_INVALID_OR_INTEGRITY_FAILURE`; artifact unchanged |

No-mutation proof used:

- artifact bytes before/after;
- artifact content hash before/after;
- artifact_hash field before/after;
- revision before/after;
- resolved_suspensions count before/after.

### resume_argv policy

Verified policy:

- `operation_result` stores `artifact_path` as a required field;
- `operation_result` does not store `resume_argv`;
- `operation_result` does not store `sys.executable`;
- duplicate PENDING public response regenerates `resume_argv`;
- artifact relocation/copy path rewriting is not introduced in P2c.

Policy: `Policy B — advisory regeneration for PENDING duplicate responses`.

### Malformed idempotency proof

The malformed idempotency case was constructed from a valid artifact by:

1. removing top-level `artifact_hash`;
2. mutating `idempotency.resolved_suspensions` into a contradictory entry;
3. recomputing `artifact_hash` with `synapse.application._artifact_with_hash()`;
4. running `resume` against the corrupted copy.

Observed result:

```text
exit 21
ARTIFACT_INVALID_OR_INTEGRITY_FAILURE
artifact unchanged
```

`_artifact_with_hash()` was used only for test-side corrupted copy construction.

### Late-boundary process races

Race setup:

```text
PENDING_1 -> resume -> PENDING_2
```

The setup phase completed sequentially before starting racing processes.

Forced-overlap method:

1. start process 1 through public CLI;
2. poll `<artifact>.lock/` with bounded `time.monotonic()` deadline and `time.sleep(0.02)`;
3. start process 2 only after lock directory is observed;
4. assert one winner and one exit `26`.

Same-hash late-boundary race:

```text
exit codes: [0, 26]
```

Different-hash late-boundary race:

```text
exit codes: [0, 26]
winner signal hash persisted
loser signal hash absent
```

After races:

- artifact integrity: PASS;
- history integrity: PASS;
- post-commit same-hash duplicate: stored semantic result;
- post-commit different-hash duplicate: exit `24`;
- artifact unchanged for duplicate/conflict checks.

## Test node evidence

New P2c tests added in `tests/test_durable_execution.py`:

```text
test_p2c_three_cycle_campaign_preserves_dense_sequences_history_and_output_delta
test_p2c_duplicate_stale_and_resume_argv_matrix_across_cycles
test_p2c_mixed_reason_campaign_uses_public_runtime_reasons
test_p2c_malformed_resolved_entry_with_recomputed_artifact_hash_is_integrity_failure
test_p2c_late_boundary_process_races_same_and_different_hashes
test_p2c_p2a_and_p2b_artifact_compatibility_without_schema_migration
```

AC-P2C matrix result in PR #21:

```text
31/31 PASS
```

## Verification results recorded in PR #21

| Command | Result |
|---|---|
| `python -m py_compile synapse/application.py synapse/cli.py tests/test_durable_execution.py` | PASS |
| P2c node IDs | `6 passed` |
| `python -m pytest -q tests/test_durable_execution.py` | `77 passed, 1 skipped` |
| `python -m pytest -q tests/test_system_execution_path.py` | `15 passed` |
| `python -m pytest --collect-only -q` | `1513 tests collected` |
| `python -m pytest -q` | `1494 passed, 13 skipped, 6 failed` |

Known failures were unchanged Windows/Git path/ref baseline cases.

```text
new_failing_nodeids = []
```

## CI evidence

PR-head CI for `ac6bd049950a20539d7306c6092af889c4baf2ff`:

| Workflow | Run ID | Job | Job ID | Conclusion |
|---|---:|---|---:|---|
| P2 Durable Initial Run | `27766927801` | `p2a-initial-run-ubuntu-latest` | `82156093028` | success |
| P2 Durable Initial Run | `27766927801` | `p2a-initial-run-windows-latest` | `82156093109` | success |
| Version Sync Check | `27766927965` | `sync` | `82156092996` | success |

Post-merge automatic workflow runs on merge commit `4eb2ec86c91a5412ce183261000bdc884b1b0d85`:

```text
[]
```

Therefore this post-merge S1 record does not claim direct merge-commit CI. It records:

- successful PR-head CI;
- tree-equivalence between PR head and post-merge `main`;
- exact changed-file scope after merge;
- implementation PR evidence and test results;
- final S1 evidence placement.

## Reviewer-reported verification addendum

A scope-reviewer reported an additional live verification run in a reviewer environment on post-P2c `main` at:

```text
4eb2ec86c91a5412ce183261000bdc884b1b0d85
```

Reported observations:

- 3-cycle campaign reached `COMPLETED`;
- final revision was `4`;
- `resolved_suspensions` count was `3`;
- stale unknown ID returned exit `23`;
- conflicting duplicate returned exit `24`;
- prior duplicate returned the stored semantic result;
- owning durable suite result was reported as `78 passed`;
- collect-only count was reported as `1513`.

This addendum is reviewer-reported evidence. It complements PR-head CI and tree-equivalence post-merge verification. It is not recorded as CI-proven evidence.

## Post-merge verification

Post-merge state:

```text
main = 4eb2ec86c91a5412ce183261000bdc884b1b0d85
```

Verification facts:

- PR #21 is merged;
- PR head before merge: `ac6bd049950a20539d7306c6092af889c4baf2ff`;
- compare `ac6bd049950a20539d7306c6092af889c4baf2ff` to `main`: one merge commit ahead, zero file differences;
- compare `8dabc543dfa10494b0c869593c81e56589e80164` to `main`: only `tests/test_durable_execution.py` changed;
- no production, workflow, RFC, status or prior evidence file was changed in implementation PR #21.

## Stop gates

```text
stop_gates = []
```

No stop-gate triggered during P2c implementation review.

## Closure statement

After this S1 evidence sync merges:

```text
P2c: CLOSED
P2 overall: CLOSED for approved CLI durable execution scope
```

This closure does not claim completion of future stages:

- P3 content-sensitive distributed consensus;
- P4 habit activation/suppression evidence;
- P5 CVM/tree-walker conformance;
- P6 AS2 production reachability.
