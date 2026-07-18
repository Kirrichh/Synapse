# Synapse Agent Guide

This repository contains the Synapse DSL/runtime and AS2 verification work.

## Project Scope

- Keep changes focused and behavior-preserving unless the task explicitly asks for a larger refactor.
- Treat AS2 production enablement as locked unless a task explicitly updates the production readiness path.
- Do not treat verification-only Docker Compose evidence as official production sign-off.
- Prefer existing patterns in `synapse/`, `synapse/runtime/`, `tests/`, and `docs/`.

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
Current recorded Stage 4 targeted test count: 179 passed.

The last actually observed Linux full-suite baseline remains the Stage 4
Patch 1 implementation commit `71fd70bcabe929e68878ecb099fcc1a2b8d29f4c`:

```text
2276 passed, 12 skipped
```

No Linux full suite was run for Patch 2. A recorded baseline is evidence of an
observed run, not a command to rerun the full suite before each patch.

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

When AS2 verification behavior changes, update the relevant docs:

- `docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md`
- `docs/CHANGELOG.md`

When changing project behavior, update tests first or alongside the implementation.
