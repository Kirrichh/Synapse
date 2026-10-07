# Synapse Agent Guide

This repository contains the Synapse DSL/runtime and AS2 verification work.

## Project Scope

- Keep changes focused and behavior-preserving unless the task explicitly asks for a larger refactor.
- Treat AS2 production enablement as locked unless a task explicitly updates the production readiness path.
- Do not treat verification-only Docker Compose evidence as official production sign-off.
- Prefer existing patterns in `synapse/`, `synapse/runtime/`, `tests/`, and `docs/`.

## Python Module Boundaries

- A module owns one cohesive responsibility. Split by contracts, invariants,
  ownership, lifecycle, failure semantics and dependency boundaries, not LOC.
- There is no numeric file-size limit or LOC merge gate. Size may prompt review;
  it does not establish an architectural defect. This is Stage 4 NR-04.
- A large cohesive module is acceptable when splitting would obscure ownership
  or control/data flow, duplicate invariants, or introduce cycles or artificial
  interfaces.
- Do not introduce helpers, utils, part2 modules, aliases, shims or pass-through
  wrappers merely to shorten a file. An adapter must join real contracts and
  perform necessary translation/validation with explicit failure semantics.
- A transferred responsibility has one replacement owner. Remove the previous
  runtime path and redundant logic; do not retain parallel implementations.
- Acceptance tests remain outside product semantics and imports. Keep heavy
  scenarios in separate acceptance files so CI can schedule them independently;
  do not test LOC, file names or file counts as architectural correctness.
- Tests, fixtures, scenario builders and acceptance harnesses belong only to
  the acceptance/test layer. Product code must never import them, depend on
  them, or contain an alternate implementation used to satisfy a test.

## Local Setup

Use Python 3.10 or newer. In local Windows workspaces, a virtual environment may already exist at `.venv/`.

Recommended local commands:

```bash
python -m pytest -q
python -m pytest -q tests/test_as2_postgresql_external_provider_p0645.py
```

If running on Linux/macOS with Make available:

```bash
make test
make lint
make audit
make test-golden
```

## Verification Status

Recorded final full-suite baseline for Stage 4 Patch 2 implementation commit
`b4d2c2ecadc87d63a9cba47ded524bf74496fa72` on Windows with Python 3.14:

```text
6 failed, 2296 passed, 13 skipped in 158.43s
2315 total collected/executed items
```

The six failures are the previously known Windows-specific cases:

- `tests/test_controlled_change_hardening.py::test_symlink_candidate_digest_uses_link_target_not_external_contents`
- `tests/test_controlled_change_hardening.py::test_real_git_ls_tree_z_modes_and_exact_paths`
- `tests/test_controlled_change_hardening.py::test_real_git_status_z_preserves_special_pathnames_and_backslash`
- `tests/test_controlled_change_hardening.py::test_real_git_backslash_patch_is_applied_then_rejected_by_scope`
- `tests/test_ref_cas_and_linked_worktree_safety.py::test_dangling_symbolic_evidence_ref_is_replaced_without_creating_target_branch`
- `tests/test_ref_cas_and_linked_worktree_safety.py::test_parser_against_real_git_raw_bytes`

They pass on Linux and are not permission to hide new regressions, weaken
assertions, or add skips/xfails.

The exact targeted Stage 4 invocation covering the three Patch 2 suites and
the Patch 1 contract suite completed for the Patch 2 correctness follow-up on
Windows as:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
py -3.14 -B -m pytest -q -p no:cacheprovider --tb=short `
  tests/test_stage4_gold_behavior.py `
  tests/test_stage4_gold_canonicalization.py `
  tests/test_stage4_gold_compiler_binding.py `
  tests/test_stage4_gold_contracts.py
```

```text
71 passed in 16.00s
```

That targeted result contains 30 Stage 4 Patch 2 acceptance tests (23 original
Patch 2 tests plus 7 Patch 2 correctness follow-up tests) and 41 Stage 4 Patch
1 contract tests. The corrective follow-up restored compilation of typed
`REJECTED_HYPOTHESIS_GUARD` behaviors without granting authority, preserved the
empty string as a distinct typed inline value, and bounded aggregate inline
defaults by their canonical envelope. The full repository suite was not rerun
for this follow-up; this targeted evidence is not a full-suite result.
Current recorded Stage 4 targeted test count: 266 passed.

The last actually observed Linux full-suite baseline remains the Stage 4
Patch 1 implementation commit `71fd70bcabe929e68878ecb099fcc1a2b8d29f4c`:

```text
2276 passed, 12 skipped
```

No Linux full suite was run for Patch 2. A recorded baseline is evidence of an
observed run, not a command to rerun the full suite before each patch.

The latest observed Linux verification is the third review of PR #108 at
`4905d47` (N1 a resolution found in stand trials stands only while the trials
recorded so far name the same winner, decided in every ordinary consolidation;
N2 a hypothesis status is reused and kept only on the check basis it was
decided on: the checking tool's contract, the provenance relation, the identity
rules). It is a targeted run, not a full suite: Python 3.11, every
`acceptance/memory` file in its own process, four files in parallel, on the
uncommitted working tree, plus six stage16 memory court files and
`tests/test_memory_dependency_direction.py`:

```text
acceptance/memory:  79 files, 397 passed
stage16 court:      6 files, 14 passed
tests/ (memory):    1 file, 2 passed
```

The reviewer's battery (3 counterexamples, 2 positive controls, 10 earlier
regressions) passes 15/15; its N2 probe calls `reuse` with the check basis of
the configuration in force, the argument the corrected signature requires. A
mutation campaign over N1 and N2 killed all 26 mutants
(`reports/memory_second_review_mutation_evidence_v1.json`, `third_review`).

The previous observed Linux verification is the reassessment package of the
second review of PR #108 (court policy v4: memory decided under v3 is
reassessed before use, deciding again from the record hypothesis statuses,
promotions and trial-based resolutions). It is a targeted run, not a full
suite: Python 3.11, every `acceptance/memory` file in its own process, four
files in parallel, on the uncommitted working tree, plus the stage16 memory
court files and `tests/test_memory_dependency_direction.py`:

```text
acceptance/memory:  77 files, 372 passed
stage16 court:      6 files, 30 passed
tests/ (memory):    1 file, 2 passed
```

`test_memory_lifecycle_sequence_acceptance.py` first failed on two defects of
the acceptance harness, both present on `0a229eb`: the crash driver's
`boundary` point also killed a session that opens on a lagging boundary of an
earlier crash (no run artifact, nothing to resume), and the scripted world was
read without the lock it is written under. Both were fixed in the harness and
the file passed, replaying the failing example. A mutation campaign over the
reassessment killed all 26 mutants
(`reports/memory_second_review_mutation_evidence_v1.json`).

An earlier observed Linux verification is the second review of PR #108 at
`5580007` (F1 a check decides only the claim its contract binds it to; F2 a
stand trial covers a trigger only inside the tested scope; F3 contradicting
trials name no winner; F4 promotion reads confirmed experience only). It is a
targeted run, not a full suite: Python 3.11, every `acceptance/memory` file in
its own process, four files in parallel, on the uncommitted working tree,
plus the memory files of `tests/` and the stage16 memory court files:

```text
acceptance/memory:  75 files, 343 passed
tests/ (memory):    tests/test_memory_dependency_direction.py, 2 passed
stage16 court:      6 files, 30 passed
```

One expectation changed with F1 and was rerun alone:
`test_admission_identity_acceptance.py` — billing quotes the old incarnation of
a recreated database, so the check of the new incarnation stays provisional
(an added admission reason, the same abstention). The reviewer's battery
(4 findings, 5 positive controls) passes 9/9. A mutation campaign over the new
rules killed all 22 mutants (`reports/memory_second_review_mutation_evidence_v1.json`).

The latest observed full Linux run is the completed memory review package on PR
#108 (attestations of admission bases, contract violations archive a habit,
the dependency projection and forget, generalization explanations and
contract versions, stand trials, state snapshots with tail replay; Python
3.11, every `tests/` and `acceptance/` file in its own process, four files in
parallel, on the uncommitted working tree):

```text
tests/:       2 failed, 5232 passed, 12 skipped
acceptance/:  1 failed, 1061 passed, 1 skipped
```

All three failures were diagnosed:

- `acceptance/stage4/stage16/test_live_gemini_worker.py` is the explicit live
  job that requires `GEMINI_API_KEY` (absent here).
- Two scope tripwires of `tests/test_swebench_measurement_output_boundary.py`
  compared fixed historical commits and the working tree with a file list;
  they passed on the committed tree and were then removed with the other
  checks of that file that did not exercise the product.

The acceptance count includes the seven contract cases added after the run to
kill surviving mutants; the files changed then were rerun alone and passed. A
mutation campaign over the package's new memory paths killed all 55 mutants
(`reports/memory_review_mutation_evidence_v1.json`). Measurements are in
`docs/MEMORY_PERFORMANCE.md`.

The previous observed Linux run is the memory review package on PR #108
(exact entity identity and admission bases, operation-bound state checks,
unknown effects and gateway-issued idempotency keys, verified conflict
comparison with blocking before any effect, the experience-based automaton,
reassessment of recorded memory; court policy v3, tool configuration v2;
Python 3.11, every `tests/` and `acceptance/` file in its own process, four
files in parallel, on the uncommitted working tree):

```text
tests/:       2 failed, 5228 passed, 12 skipped
acceptance/:  1 failed, 999 passed, 1 skipped
```

All three failures were diagnosed:

- `acceptance/stage4/stage16/test_live_gemini_worker.py` is the explicit live
  job that requires `GEMINI_API_KEY` (absent here).
- Two tests of `tests/test_swebench_measurement_output_boundary.py` are scope
  tripwires over fixed historical commit ranges that also count uncommitted
  and untracked files; they fail on a dirty working tree only.

An earlier observed Linux run is the memory stage 6 and language stage 7
package on PR #108 (semantic knowledge with two times and hybrid search;
event-driven `parallel` graphs; Python 3.11, every `tests/` and `acceptance/`
file in its own process, four files in parallel, on the committed tree):

```text
tests/:       5224 passed, 12 skipped
acceptance/:  1 failed, 909 passed, 1 skipped
```

The one acceptance failure is `acceptance/stage4/stage16/test_live_gemini_worker.py`,
the explicit live job that requires `GEMINI_API_KEY` (absent here). The run
first found 52 failures in `tests/test_durable_execution.py` and
`tests/test_durable_mailbox_wait.py`: the P2a classifier refuses every run
while an AST class is unclassified, and the new `parallel` nodes were not
classified. They are now classified outside the P2a subset and the pinned
inventory count moved from 93 to 96; the counts above include those two files
rerun after the fix (78 and 16 passed), together with every other durable
test file and the dataflow acceptance files, all passing.

An earlier observed Linux run is the memory stage 5b package on PR #108
(composition of learned procedures with ordered alternatives, nested parts and
sequences; Python 3.11, every `tests/` and `acceptance/` file in its own
process, four files in parallel, on the committed tree):

```text
tests/:       5212 passed, 12 skipped
acceptance/:  2 failed, 874 passed, 1 skipped
```

Both failures were diagnosed:

- `acceptance/stage4/stage16/test_live_gemini_worker.py` is the explicit live
  job that requires `GEMINI_API_KEY` (absent here).
- `acceptance/stage4/stage16/test_gold_memory_slices_acceptance.py` reached the
  7200-second per-file limit of the local runner. The machine was slower than
  in the previous run (the other heavy Gold files, which do not use the memory
  subsystem, took about 1.7 times as long); rerun alone on the same tree it
  passed: `1 passed in 7373.32s`.

An earlier observed Linux run is the memory stage 5a package on PR #108
(learned applicability D1 and result references D2; Python 3.11, every
`tests/` and `acceptance/` file in its own process, four files in parallel,
on the committed tree):

```text
tests/:       5208 passed, 12 skipped
acceptance/:  1 failed, 853 passed, 1 skipped
```

The one failure is `acceptance/stage4/stage16/test_live_gemini_worker.py`, the
explicit live job that requires `GEMINI_API_KEY` (absent here).

An earlier observed Linux run is the memory stage 4 package on PR #108
(retention, hypotheses, palace admission; Python 3.11, every `tests/` and
`acceptance/` file in its own process, four files in parallel, about 100
minutes of wall time):

```text
tests/:       3 failed, 5201 passed, 12 skipped
acceptance/:  1 failed, 827 passed, 1 skipped
```

All four failures were diagnosed:

- `acceptance/stage4/stage16/test_live_gemini_worker.py` is the explicit live
  job that requires `GEMINI_API_KEY` (absent here).
- Two tests of `tests/test_swebench_measurement_output_boundary.py` are scope
  tripwires over fixed historical commit ranges that also count uncommitted
  and untracked files; they fail on a dirty working tree only and pass once the
  package is committed.
- `tests/test_system_execution_path.py` pinned the CLI help surface; the new
  `synapse memory` operator command is an intended surface change and the
  expectation was updated.

An earlier observed Linux run is the memory stage 3 package on PR #108
(commit `5641d17`, Python 3.11, `tests/` and `acceptance/` run as separate
processes; the acceptance tail was split across parallel processes):

```text
tests/:       3 failed, 5189 passed, 12 skipped in 6139.63s
acceptance/:  2 failed, 772 passed (774 collected)
```

All five failures were diagnosed and none is caused by the package:

- `tests/test_swebench_gold_production_tripwire.py::test_gold_fitness_v2_production_surface`
  failed on the base `a664492` as well; fixed afterwards in this package (the
  C1 record's arm is the writer's `ARM` constant).
- `tests/test_stage4_gold_replay_recorded_bytes.py` (two tests) fail only when
  `tests/test_stage4_gold_replay_permit_budget.py` ran earlier in the same
  process, which leaves an open mutation interval on the shared point-of-use
  world; reproduced identically on `a664492`. Each file passes alone; CI runs
  them as separate shards.
- `acceptance/stage4/stage16/test_live_gemini_worker.py` is an explicit live
  job that requires `GEMINI_API_KEY` (absent here).
- `acceptance/stage4/stage16/test_task_result_acceptance.py` failed because a
  package was installed into the same environment mid-run (Gold re-observes the
  package set at consumption); it passes when rerun in an unchanged environment.

The CI workflow now also runs every `tests/` suite that had no dedicated shard
(`repository-tests`).

The external GitHub Actions PostgreSQL/CDC verification was last observed as:

```text
9 passed in 6.44s
```

The successful run verified:

- PostgreSQL `ON CONFLICT` idempotency.
- PostgreSQL `UPDATE ... RETURNING` compare-and-swap transitions.
- Transaction rollback across idempotency and outbox writes.
- Parallel polling claims with `FOR UPDATE SKIP LOCKED`.
- PgBouncer transaction-mode behavior with `SET LOCAL`.
- Logical replication / `pgoutput` feasibility.
- Debezium REST readiness.
- Debezium connector registration.
- Actual outbox event to emitted Redpanda/Kafka CDC event.

## External Verification Stack

The verification-only stack is defined in:

```text
docker-compose.as2-postgres-mini-poc.yml
```

It starts PostgreSQL, PgBouncer, Redpanda, and Debezium Connect.

The GitHub Actions workflow is:

```text
.github/workflows/as2-postgres-open-provider-verification.yml
```

It runs on relevant pushes to `main` and can also be started manually with `workflow_dispatch`.

## Important Environment Variables

The external harness uses:

- `AS2_POSTGRES_TEST_DSN`
- `AS2_PGBOUNCER_TEST_DSN`
- `AS2_ENABLE_CDC_VERIFICATION`
- `AS2_DEBEZIUM_URL`
- `AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE`
- `AS2_REDPANDA_CONTAINER`
- `AS2_DEBEZIUM_POSTGRES_HOST`
- `AS2_DEBEZIUM_POSTGRES_PORT`
- `AS2_DEBEZIUM_POSTGRES_USER`
- `AS2_DEBEZIUM_POSTGRES_PASSWORD`
- `AS2_DEBEZIUM_POSTGRES_DB`
- `AS2_POSTGRES_LATENCY_SAMPLE_SIZE`

## Files To Keep Out Of Git

Do not commit generated local runtime data or credentials:

- `.venv/`
- `.tmp_postgres/`
- `.pytest_cache/`
- `__pycache__/`
- `.env`
- `.env.*`
- `*.log`

## Documentation Touchpoints

For Gold initial knowledge ingestion, follow the agreed process and ownership
boundaries in `docs/GOLD_KNOWLEDGE_INGESTION.md`. Update that instruction with
the implemented operator commands and verified limitations. It is not evidence
that ingestion or a live Baseline/Gold experiment has already passed acceptance.

When AS2 verification behavior changes, update the relevant docs:

- `docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md`
- `docs/CHANGELOG.md`

When changing project behavior, update tests first or alongside the implementation.
