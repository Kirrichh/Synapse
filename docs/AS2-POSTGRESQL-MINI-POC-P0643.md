# AS2 PostgreSQL Mini-POC Phase 1 — P0.6.43

Status: **PHASE_1_SQL_SHAPE_VALIDATED_WITH_SQLITE_DEV_BACKEND**

Patch: **P0.6.43 — PostgreSQL Mini-POC Phase 1 (SQLite dev backend)**

Scope: executable SQL-shape proof for the future PostgreSQL backend implementation path. This patch uses SQLite as a local, dependency-free development backend to validate the SQL patterns that are portable or close to the PostgreSQL target contract.

This patch does **not** select a backend vendor, does **not** implement the production backend, does **not** add PostgreSQL/Redis clients, does **not** add schema migrations, and does **not** activate production.

---

## 1. Purpose

P0.6.43 validates the first executable slice of the PostgreSQL mini-POC plan without requiring external infrastructure access.

The tests prove that the project can express the required future backend semantics as concrete SQL operations:

```text
reserve_if_absent:
  INSERT ... ON CONFLICT DO NOTHING

complete_if_state / fail_if_state:
  UPDATE ... WHERE state = expected RETURNING *

local transactional linkage:
  idempotency transition + audit outbox insert in one transaction

polling claim:
  single-round-trip UPDATE ... WHERE id IN (SELECT ...) RETURNING *
```

SQLite is used only as the local development backend. PostgreSQL-only checks remain explicitly skipped with reasons.

---

## 2. Test File

```text
tests/test_as2_postgresql_mini_poc_p0643.py
```

---

## 3. Result Summary

```text
P0.6.43 target test:
  4 passed, 3 skipped

Full suite:
  1277 passed, 3 skipped
```

The skipped checks are intentional SQLite dev-backend limitations, not silent omissions.

---

## 4. PASS / FAIL / SKIP Matrix

| Check | Result | Evidence | Notes |
|---|---:|---|---|
| `reserve_if_absent` SQL shape | PASS | `test_reserve_if_absent_uses_insert_on_conflict_do_nothing_idempotently` | Uses `INSERT ... ON CONFLICT(key_hash) DO NOTHING RETURNING ...`; duplicate insert returns no row and preserves original record. |
| `complete_if_state` / `fail_if_state` conditional transition | PASS | `test_conditional_complete_and_fail_use_update_where_state_returning` | Uses `UPDATE ... WHERE state = 'in_progress' RETURNING ...`; second transition returns zero rows and is rejected. |
| Local transaction rollback for idempotency + outbox | PASS | `test_local_transaction_rolls_back_idempotency_update_when_outbox_insert_fails` | Duplicate outbox `event_id` causes transaction rollback; idempotency state remains `in_progress`. |
| Polling claim SQL shape | PASS | `test_polling_claim_uses_single_round_trip_update_with_subquery_returning` | Uses single statement `UPDATE ... WHERE event_id IN (SELECT ... LIMIT ?) RETURNING ...`; first ordered batch becomes `claimed`. |
| PgBouncer `SET LOCAL` isolation | SKIP | `test_pgbouncer_set_local_isolation_requires_real_postgresql_pool` | Requires PostgreSQL plus PgBouncer/Odyssey transaction-mode topology. |
| CDC / logical replication | SKIP | `test_cdc_logical_replication_requires_real_postgresql` | Requires PostgreSQL `wal_level=logical`, replication slots, publication, and CDC tooling. |
| p99 concurrent-load validation | SKIP | `test_p99_concurrent_load_requires_target_postgresql_environment` | Requires target PostgreSQL environment and production-like pooling/load profile. |

---

## 5. SQL Semantics Validated

### 5.1 `reserve_if_absent`

Target PostgreSQL contract:

```sql
INSERT INTO idempotency_records (...)
VALUES (...)
ON CONFLICT(key_hash) DO NOTHING
RETURNING key_hash, state;
```

P0.6.43 validates that:

```text
first insert returns the created row;
duplicate insert returns zero rows;
duplicate insert does not update state or timestamp;
only one record exists for the idempotency key.
```

### 5.2 Conditional state transitions

Target PostgreSQL contract:

```sql
UPDATE idempotency_records
SET state = 'completed', result_ref = $1, updated_at = $2
WHERE key_hash = $3 AND state = 'in_progress'
RETURNING *;
```

P0.6.43 validates that:

```text
expected-state transition returns one row;
wrong-state transition returns zero rows;
zero rows means rejected transition, not success.
```

### 5.3 Local transaction linkage

Target production invariant:

```text
idempotency transition + audit outbox insert = one local atomic unit
```

P0.6.43 validates rollback behavior by forcing an outbox insert failure through duplicate `event_id`:

```text
outbox insert fails;
transaction rolls back;
idempotency state remains in_progress;
no phantom completed state is recorded.
```

This preserves the existing AS2 invariant:

```text
no durable audit record → no idempotency state transition
```

### 5.4 Polling claim

Target polling branch contract:

```sql
UPDATE audit_outbox
SET relay_status = 'claimed', claimed_at = $now, relay_worker_id = $worker_id
WHERE event_id IN (
  SELECT event_id
  FROM audit_outbox
  WHERE relay_status = 'pending'
  ORDER BY outbox_sequence
  LIMIT $batch_size
)
RETURNING *;
```

SQLite does not provide PostgreSQL `FOR UPDATE SKIP LOCKED`; this dev-backend test validates the single-round-trip claim shape and deterministic ordered batch claim. PostgreSQL concurrency behavior remains for the real mini-POC environment.

---

## 6. Explicit SQLite Limitations

The following are intentionally outside SQLite dev-backend capability and remain skipped:

```text
PgBouncer / Odyssey transaction-mode behavior;
SET LOCAL isolation across pooled PostgreSQL connections;
PostgreSQL CDC / logical replication;
replication slots and publications;
Debezium / pgoutput behavior;
p99 concurrent-load measurements under target infrastructure.
```

These are not closed by P0.6.43 and must be validated against a real PostgreSQL environment after Q8=YES.

---

## 7. Locked Items

```text
synapse/runtime/ changes: LOCKED
production ENABLED: LOCKED
InMemoryIdempotencyStore changes: LOCKED
backend driver implementation: LOCKED
PostgreSQL / Redis client implementation: LOCKED
schema migration: LOCKED
audit relay worker: LOCKED
external sink client: LOCKED
runtime default wiring changes: LOCKED
```

---

## 8. Next Gates

P0.6.43 does not replace P0.6.41 sign-off.

Required next gates:

```text
Q8=YES with evidence;
real PostgreSQL mini-POC environment;
PgBouncer/Odyssey transaction-mode validation;
CDC feasibility validation if Q8a=YES;
Audit Relay ADR final branch selection after Q8a/Q10;
production backend implementation patch after ADR/POC acceptance.
```

Production activation remains locked.
