# AS2 PostgreSQL Mini-POC - P0.6.45-dev Open Provider Execution Attempt

Status: **OPEN_PROVIDER_EXECUTION_ATTEMPT_RECORDED**

Additional status: **EXTERNAL_PROVIDER_VERIFICATION_HARNESS_ADDED**

Static status: **STATIC_VERIFICATION_CONFIRMED**

Runtime status: **OPEN_PROVIDER_RUNTIME_EXECUTION_BLOCKED_BY_LOCAL_RUNTIME_NO_DOCKER**

SQL runtime status: **OPEN_PROVIDER_SQL_RUNTIME_VERIFIED**

Local portable runtime status: **LOCAL_PORTABLE_POSTGRES_RUNTIME_VERIFIED**

Patch: **P0.6.45-dev - PostgreSQL Mini-POC Open Verification Execution Attempt**

This document records the local/open PostgreSQL verification path for the
P0.6.43 deferred checks. It includes the original Docker execution attempt and
the new optional external-provider pytest harness.

This report is **verification-only**. It is **not official Q8 / Q8a / Q10 evidence**.
It does not close infra sign-off. It does not select a production backend. It
does not activate production.

Production ENABLED remains **LOCKED**.

---

## 1. Execution Environment

The current execution environment does not provide Docker / Docker Compose:

```text
$ docker --version
bash: docker: command not found

$ docker compose version
bash: docker: command not found
```

The local/open Docker stack could not be started from this environment.

A local portable PostgreSQL runtime was then created from EDB PostgreSQL 16.14
Windows x86-64 binaries inside the workspace. This is not official
infrastructure and not a production server. It is a verification-only local
runtime used to produce a real PostgreSQL DSN for the external-provider harness
without requiring a user-owned server.

Provider type:

```text
local portable PostgreSQL binaries
```

Provider name:

```text
EDB PostgreSQL 16.14 Windows x86-64 binaries
```

DSN:

```text
NOT RECORDED
```

External-provider verification is now represented by:

```text
tests/test_as2_postgresql_external_provider_p0645.py
```

The external-provider harness requires a PostgreSQL DSN provided only through an
environment variable. No DSN, password, token, or provider secret is recorded in
this repository.

Required for external PostgreSQL checks:

```text
AS2_POSTGRES_TEST_DSN
```

Optional:

```text
AS2_PGBOUNCER_TEST_DSN
AS2_ENABLE_CDC_VERIFICATION=1
AS2_POSTGRES_LATENCY_SAMPLE_SIZE
AS2_DEBEZIUM_URL
AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE=1
AS2_REDPANDA_CONTAINER
```

Safe provider classes for verification-only execution:

```text
local Docker Compose stack
Neon / Supabase / Aiven Free PostgreSQL / other managed PostgreSQL trial
```

There is no safe credential-free public PostgreSQL target for this harness. A
managed provider must be accessed through a DSN supplied out-of-band.

For teams without a local server, the repository now includes a manual GitHub
Actions workflow that can run the verification-only Docker Compose stack on a
GitHub-hosted runner:

```text
.github/workflows/as2-postgres-open-provider-verification.yml
```

This workflow is `workflow_dispatch` only. It does not run on normal push /
pull_request events and does not provide official infra evidence.

---

## 2. Commands Executed

Static project suite before external-provider harness:

```text
python -m pytest -q
```

Recorded result:

```text
1285 passed, 3 skipped
```

External-provider harness collection without DSN:

```text
python -m pytest --collect-only -q tests/test_as2_postgresql_external_provider_p0645.py
```

Recorded result:

```text
7 tests collected
```

External-provider harness execution without DSN:

```text
python -m pytest -q tests/test_as2_postgresql_external_provider_p0645.py
```

Recorded result:

```text
7 skipped
```

The skips are expected when `AS2_POSTGRES_TEST_DSN` is not configured.

External-provider harness execution against the local portable PostgreSQL
runtime:

```text
AS2_POSTGRES_TEST_DSN=<temporary local loopback DSN>
AS2_ENABLE_CDC_VERIFICATION=1
AS2_POSTGRES_LATENCY_SAMPLE_SIZE=30
python -m pytest -q tests/test_as2_postgresql_external_provider_p0645.py
```

Recorded result:

```text
6 passed, 2 skipped
```

The local server reported:

```text
wal_level = logical
```

---

## 3. Verification Status Matrix

| Check | Current status | Reason |
|---|---|---|
| Static project suite | PASS | `1285 passed, 3 skipped` before adding optional external-provider skips. |
| P0.6.44-dev compose file present | PASS | `docker-compose.as2-postgres-mini-poc.yml` exists. |
| Local Docker stack startup | BLOCKED | Docker unavailable in current runtime: `docker: command not found`. |
| External-provider harness present | PASS | `tests/test_as2_postgresql_external_provider_p0645.py` added. |
| External-provider harness without DSN | SKIP_NOT_CONFIGURED | `AS2_POSTGRES_TEST_DSN` is not configured. |
| Local portable PostgreSQL runtime | PASS | EDB PostgreSQL 16.14 binaries initialized and started locally for verification-only execution. |
| Real PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` execution | PASS | Verified against local portable PostgreSQL runtime. |
| Real PostgreSQL `UPDATE ... WHERE state = expected RETURNING *` execution | PASS | Verified against local portable PostgreSQL runtime. |
| Real transaction rollback with idempotency + outbox | PASS | Verified rollback when outbox insert fails. |
| Real `FOR UPDATE SKIP LOCKED` polling claim | PASS | Verified with 4 concurrent worker connections claiming disjoint events. |
| PgBouncer transaction-mode SET LOCAL isolation | SKIP_NOT_CONFIGURED | Requires `AS2_PGBOUNCER_TEST_DSN`. |
| CDC / logical replication feasibility | PASS | Verified `wal_level=logical`, `CREATE PUBLICATION`, and `pg_create_logical_replication_slot(..., 'pgoutput')`. |
| Debezium REST smoke | SKIP_NOT_CONFIGURED | Requires `AS2_DEBEZIUM_URL`; Docker/Redpanda/Debezium unavailable locally. |
| Registered Debezium connector | SKIP_PROVIDER_LIMITATION | Requires Debezium service plus `AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE=1`; covered by manual GitHub Actions workflow. |
| Actual outbox event -> emitted CDC event | SKIP_PROVIDER_LIMITATION | Requires Redpanda/Kafka topic consumption; covered by manual GitHub Actions workflow through `rpk topic consume`. |
| Basic non-SLO latency sample | PASS | Verified with 30 sequential operations; sample is not SLO evidence. |

---

## 4. External Provider Harness Coverage

The external-provider harness validates:

```text
reserve_if_absent:
  INSERT ... ON CONFLICT DO NOTHING
  duplicate insert returns 0 rows
  existing record remains unchanged

conditional transitions:
  UPDATE ... WHERE state = expected RETURNING *
  completed transition succeeds from in_progress
  wrong-state transition returns 0 rows
  failed transition succeeds from in_progress

local transaction rollback:
  idempotency update + audit outbox insert execute in one local transaction
  intentionally broken outbox insert rolls the whole transaction back
  no durable audit record -> no idempotency state transition

polling claim:
  UPDATE ... WHERE event_id IN (
    SELECT ... FOR UPDATE SKIP LOCKED LIMIT N
  ) RETURNING *
  4 concurrent workers claim disjoint events
  ordering is based on outbox_sequence, not wall-clock

PgBouncer SET LOCAL isolation:
  runs only when AS2_PGBOUNCER_TEST_DSN is configured
  verifies SET LOCAL does not leak into a later transaction

CDC feasibility:
  runs only when AS2_ENABLE_CDC_VERIFICATION=1
  verifies wal_level=logical
  attempts CREATE PUBLICATION
  attempts pg_create_logical_replication_slot(..., 'pgoutput')
  drops the test replication slot if it was created

Debezium REST smoke:
  runs only when AS2_DEBEZIUM_URL is configured
  verifies the Connect REST endpoint responds to /connectors

Registered Debezium connector + emitted CDC event:
  runs only when AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE=1
  creates an outbox table publication
  registers a Debezium PostgreSQL connector through Debezium REST
  waits for connector/task RUNNING status
  inserts a real audit_outbox event
  consumes the Redpanda/Kafka topic through rpk
  verifies the inserted event_id appears in the emitted CDC record

basic latency sample:
  runs 100 sequential operations by default
  records reserve_if_absent / complete_if_state / polling claim timings
  treated as non-SLO local/open-provider signal only
```

The harness creates temporary verification tables with unique names and drops
them after each test. It does not store credentials and does not modify
`synapse/runtime`.

---

## 5. How To Run Against An Open / Managed Provider

Example for a managed PostgreSQL provider:

```bash
export AS2_POSTGRES_TEST_DSN="postgresql://USER:PASSWORD@HOST:PORT/DB?sslmode=require"
python -m pytest -q tests/test_as2_postgresql_external_provider_p0645.py
```

Example for the local Docker Compose stack:

```bash
docker compose -f docker-compose.as2-postgres-mini-poc.yml up -d
export AS2_POSTGRES_TEST_DSN="postgresql://as2:as2_dev_only@localhost:55432/as2_mini_poc?sslmode=disable"
export AS2_PGBOUNCER_TEST_DSN="postgresql://as2:as2_dev_only@localhost:56432/as2_mini_poc?sslmode=disable"
export AS2_ENABLE_CDC_VERIFICATION=1
export AS2_DEBEZIUM_URL="http://localhost:58083"
export AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE=1
export AS2_REDPANDA_CONTAINER="as2-postgres-mini-poc-redpanda"
python -m pytest -q tests/test_as2_postgresql_external_provider_p0645.py
```

Example without a local server, using GitHub-hosted runner capacity:

```text
GitHub Actions -> AS2 PostgreSQL Open Provider Verification -> Run workflow
```

Secrets must be supplied through environment variables only.

---

## 6. Success Criteria

Minimal successful result without Docker / PgBouncer / CDC:

```text
reserve_if_absent: PASS
conditional update: PASS
local transaction rollback: PASS
FOR UPDATE SKIP LOCKED: PASS
basic latency sample: PASS, non-SLO
PgBouncer: SKIP_PROVIDER_LIMITATION
CDC: SKIP_PROVIDER_LIMITATION
```

This is sufficient for:

```text
OPEN_PROVIDER_SQL_RUNTIME_VERIFIED
```

Current local portable PostgreSQL result:

```text
reserve_if_absent: PASS
conditional update: PASS
local transaction rollback: PASS
FOR UPDATE SKIP LOCKED: PASS
CDC / pgoutput feasibility: PASS
basic latency sample: PASS, non-SLO
PgBouncer: SKIP_PROVIDER_LIMITATION
Debezium REST smoke: SKIP_PROVIDER_LIMITATION
registered connector: SKIP_PROVIDER_LIMITATION
actual outbox event -> emitted CDC event: SKIP_PROVIDER_LIMITATION
```

It is not sufficient for:

```text
official Q8/Q8a/Q10 closure
production backend selection
production activation
```

---

## 7. Required Future Checks

The following checks remain required when a provider DSN or Docker runtime is
available:

```text
INSERT ... ON CONFLICT DO NOTHING
UPDATE ... WHERE state = expected RETURNING *
local transaction rollback for idempotency + outbox
FOR UPDATE SKIP LOCKED polling claim
PgBouncer transaction-mode SET LOCAL isolation
CDC smoke through pgoutput / Debezium / Redpanda
basic latency sample marked non-SLO
```

---

## 8. Non-goals

P0.6.45-dev does not:

```text
create official Q8 evidence
create official Q8a evidence
create official Q10 evidence
select production backend
implement a backend driver
implement schema migration
implement audit relay worker
modify synapse/runtime/
activate production
record provider credentials
```

---

## 9. Next Gate

If a Docker/Open provider runtime becomes available:

```text
P0.6.46-dev - PostgreSQL Mini-POC Open Provider Runtime Execution
```

If official infrastructure answers arrive first:

```text
Q8=YES -> real PostgreSQL Mini-POC Phase 1 in target or approved environment
Q8=YES, Q8a=NO -> continue with Polling branch checks
Q8=YES, Q8a=YES -> include CDC feasibility checks
```

Production ENABLED remains **LOCKED**.
