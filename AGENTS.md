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

The full local suite was last observed as:

```text
1286 passed, 12 skipped
```

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
