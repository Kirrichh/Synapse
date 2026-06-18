# P2b Evidence — Resume and Boundary Reconstruction

This document is the permanent S1 evidence summary for P2b after PR #18 was merged.

It records post-merge verification, accepted PR-head CI, Phase 0 evidence status, reviewer-side addendum and the remaining P2c boundary. It does not change parser, AST, interpreter, runtime semantics, durable schemas, CLI behavior, tests or workflows.

## Commit anchors

| Item | SHA |
|---|---|
| P2a + S1 main before P2b | `9f146f0e931301fa549304fa7e4c9eca9e97926c` |
| PR #18 head before merge | `6979e57c29bd2857ddde6721844bab90270af475` |
| PR #18 post-merge commit | `743e4fbc3cc6545745713d26625d4f4cd9a4d34c` |
| PR #18 URL | `https://github.com/Kirrichh/Synapse/pull/18` |

Comparison from PR head `6979e57...` to `main` shows one merge commit and zero file differences, so the merged tree matches the reviewed PR-head tree.

## P2b status after S1 sync

| Scope | Status |
|---|---|
| P2a — Durable Initial Run | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` |
| P2b — Resume and Boundary Reconstruction | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` |
| P2c — Idempotency, Multi-cycle and Concurrency Closure | `RFC_REQUIRED / NOT IMPLEMENTED` |
| P2 overall | `PARTIAL` |

P2b is closed only after post-merge verification and S1 documentation synchronization.

## PR-head CI evidence

The PR-head commit `6979e57c29bd2857ddde6721844bab90270af475` passed required CI before merge.

| Workflow | Run ID | Job | Job ID | Conclusion |
|---|---:|---|---:|---|
| P2 Durable Initial Run | `27751331659` | `p2a-initial-run-windows-latest` | `82102276999` | success |
| P2 Durable Initial Run | `27751331659` | `p2a-initial-run-ubuntu-latest` | `82102277074` | success |
| Version Sync Check | `27751331647` | `sync` | `82102276984` | success |

There was no separate automatic GitHub Actions run directly on the merge commit `743e4fbc...`; post-merge verification is therefore recorded as manual/team verification against `origin/main` plus accepted PR-head CI.

## Post-merge verification record

Verification target:

```text
origin/main = 743e4fbc3cc6545745713d26625d4f4cd9a4d34c
```

Commands:

```bash
git fetch origin --prune
git rev-parse origin/main

python -m pytest -q tests/test_durable_execution.py
python -m pytest -q tests/test_system_execution_path.py
python -m pytest --collect-only -q
python -m pytest -q
```

Recorded results:

| Command | Result |
|---|---|
| `python -m pytest -q tests/test_durable_execution.py` | `71 passed, 1 skipped` |
| `python -m pytest -q tests/test_system_execution_path.py` | `15 passed` |
| `python -m pytest --collect-only -q` | `1507 tests collected` |
| `python -m pytest -q` | `1488 passed, 13 skipped, 6 failed` |

`new_failures = []`.

Known retained baseline failures:

- `tests/test_controlled_change_hardening.py` x4 Windows/Git path-mode cases;
- `tests/test_ref_cas_and_linked_worktree_safety.py` x2 Windows/Git ref/path cases.

These known failures are not introduced by P2b and remain outside P2b closure.

## CLI smoke verification

The post-merge CLI smoke uses a fresh isolated temporary directory to avoid stale locks or old artifacts.

Canonical public path:

```bash
python -m synapse run <tmp>/p2b_smoke.syn --durable --state-dir <tmp>/state --run-id p2b-post-merge-smoke
python -m synapse resume --state-file <artifact.json> --suspension-id <id> --signal-file <tmp>/signal.json
```

Expected and recorded result:

```text
run --durable -> PENDING
resume -> COMPLETED
```

This verifies the supported user/runtime path on the merged `origin/main` tree: CLI argument parsing, state-file resolution, artifact read, lock acquisition, signal file loading, replay to boundary, same-generator continuation and atomic artifact commit.

## P2b implemented contract evidence

P2b provides:

- canonical `python -m synapse resume` CLI;
- strict state-file policy: existing regular non-symlink `.json`, canonical resolved path and filename matching artifact `run_id`;
- fail-closed rejection of non-regular state files after symlink rejection;
- strict signal JSON loader from UTF-8-sig file or stdin;
- artifact integrity validation including schema version, mandatory fields, artifact hash, versions, embedded source ownership, initial binding hash, history chain, boundary self-consistency, suspension ID, output state and idempotency entry integrity;
- deterministic replay from `replay_state.source_code` to saved boundary;
- natural REPLAY to LIVE transition before signal injection;
- full replay cursor consumption;
- output-prefix verification and output-prefix suppression;
- same-generator continuation via `generator.send(signal)`;
- atomic commit of COMPLETED, ERROR or next PENDING outcome;
- terminal duplicate same-signal idempotency without replay or artifact mutation;
- conflicting duplicate handling with exit `24`;
- stale/unknown suspension handling with exit `23`;
- process-level resume lock race proof with observed exit pair `[0, 26]`;
- PENDING to PENDING next suspension mechanics with sequence-aware IDs.

## P2b-Fix-01 evidence

P2b-Fix-01 closed the two review blockers discovered before merge:

- `_resolve_state_file()` now rejects non-regular state-file paths after symlink rejection;
- POSIX FIFO state-file input is rejected without hang;
- true two-process resume lock race is covered through `subprocess.Popen`;
- observed/proven exit codes are `[0, 26]`;
- after the race, `resolved_suspensions` count is `1`;
- after the race, artifact status is `COMPLETED`;
- artifact hash remains valid.

Relevant test node IDs:

- `tests/test_durable_execution.py::test_resume_rejects_non_regular_state_file_without_hanging`;
- `tests/test_durable_execution.py::test_resume_concurrent_os_process_race_acquires_lock_exclusively`.

## Phase 0 evidence status

Executor-side Phase 0 file hashes and local paths were recorded in PR #18 evidence comment. Raw files were not committed because P2b scope forbade new tracked evidence files. Product Owner accepted Phase 0 for technical purposes, supported by reviewer-side addendum.

Recorded hashes:

| File | SHA-256 |
|---|---|
| `phase0_summary.json` | `635df7d03fcf9c5744f77d211a5b79f915d34e1744e6fa718a18befa55961e22` |
| `phase0_probe.py` | `817771c1694ddd865a581d31de5b231394f5060873e1215ed24a825319a8d722` |

This document does not claim raw files are attached or tracked. It records the accepted evidence status honestly.

## Reviewer-side addendum summary

Independent review confirmed:

- artifact schema/hash integrity;
- boundary self-consistency;
- natural REPLAY to LIVE transition;
- full history consumption;
- same-generator continuation;
- PENDING to PENDING mechanics and PENDING #2 reconstruction;
- output suppression after persisted prefix;
- actor/side-effect replay safety;
- idempotency schema headroom;
- baseline and `new_failures = []`.

## Known future candidates

The following findings are not P2b blockers, but they must remain visible for future planning:

- Distributed consensus remains a semantic facade; the observed `with [] quorum 1 -> committed=True` behavior is a candidate for P3 RFC/evidence work.
- Affective ID nondeterminism remains known behavior for future status/evidence tracking.

## P2c boundary

P2c remains responsible for:

- full multi-cycle campaigns;
- stale old IDs after later boundaries;
- duplicate same/different signals across multi-cycle;
- process-level concurrent resume closure beyond P2b;
- compatibility policy across P2a/P2b artifacts where needed.

Out of P2c:

- signal inbox;
- daemon;
- network delivery;
- scheduler timeout;
- auto stale-lock recovery;
- force unlock;
- distributed signal transport;
- new exit codes unless separately approved.

P2c code must not start before `RFC-ASYNC-EXECUTION-AMENDMENT-02` is approved.

## Stop gates

Post-merge verification and S1 sync completed with:

```text
[]
```

Future S1/P2c work must stop on:

- `BLOCKED — NEW_PLATFORM_REGRESSION`;
- `BLOCKED — USER_PATH_NOT_VERIFIED`;
- `BLOCKED — UNAPPROVED_SCOPE_EXPANSION`;
- `BLOCKED — EVIDENCE_MISMATCH`;
- `BLOCKED — P2C_SCOPE_LEAK`.

## Future merge-gate

Future product PRs must synchronize PR body with final head SHA, final test counts, CI run IDs, known failures and final review status before merge. This is now part of the project evidence discipline.
