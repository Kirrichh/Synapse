# AS2 PostgreSQL Mini-POC — P0.6.44-dev Local/Open Verification Stack

Status: **VERIFICATION_ONLY**

Patch: **P0.6.44-dev — PostgreSQL Mini-POC Local/Open Verification Stack**

This document records a local/open-source verification harness for the PostgreSQL Mini-POC path. It is intended to let the team rehearse PostgreSQL, PgBouncer, polling, and optional CDC mechanics before target infrastructure answers are available.

This patch is **not official Q8 / Q8a / Q10 evidence**. It does **not** close infra sign-off. It does **not** select a production backend. It does **not** activate production.

Production ENABLED remains LOCKED.

Production ENABLED remains **LOCKED** for release-readiness interpretation.

---

## 1. Added Verification Stack

```text
docker-compose.as2-postgres-mini-poc.yml
```

The stack is explicitly marked as verification-only through comments and service labels:

```text
as2.scope: "verification-only"
as2.production: "false"
as2.patch: "P0.6.44-dev"
```

---

## 2. Services

| Service | Purpose | Verification Scope |
|---|---|---|
| `postgres` | Local PostgreSQL 16 | SQL semantics, local transactions, polling claim, logical-replication rehearsal |
| `pgbouncer` | Transaction-mode pooling rehearsal | PgBouncer compatibility and future `SET LOCAL` isolation test rehearsal |
| `redpanda` | Local Kafka-compatible broker | Optional CDC smoke target |
| `debezium` | CDC connector runtime | Optional PostgreSQL `pgoutput` CDC rehearsal |

---

## 3. PostgreSQL Verification Controls

The local PostgreSQL service starts with:

```text
wal_level=logical
max_replication_slots=4
max_wal_senders=4
max_slot_wal_keep_size=10GB
```

These controls are for local CDC rehearsal only. Target infrastructure must still provide Q8/Q8a evidence.

---

## 4. PgBouncer Verification Controls

The local PgBouncer service is configured with:

```text
PGBOUNCER_POOL_MODE=transaction
PGBOUNCER_MAX_CLIENT_CONN=200
PGBOUNCER_DEFAULT_POOL_SIZE=20
```

This enables a future real mini-POC to test:

```text
SET LOCAL isolation;
transaction-mode compatibility;
short transaction behavior;
absence of session-state dependency.
```

This patch only adds the verification stack and static checks. It does not add runtime code or a backend driver.

---

## 5. Optional CDC Rehearsal

The local stack includes Redpanda and Debezium so that the team can rehearse a CDC topology without depending on a corporate Kafka / sink decision.

Preferred decoder marker:

```text
AS2_CDC_DECODER_PREFERRED=pgoutput
```

This is a rehearsal-only marker. Real CDC approval still depends on Q8a evidence:

```text
wal_level=logical;
replication slot creation;
publication creation;
pgoutput availability;
replication user permissions;
WAL retention controls.
```

---

## 6. What This Patch Validates

P0.6.44-dev validates that the repository now contains a local/open-source verification stack for the next real PostgreSQL mini-POC.

Static tests verify that the compose file contains:

```text
verification-only labels;
PostgreSQL logical replication controls;
PgBouncer transaction-mode controls;
Redpanda / Debezium CDC rehearsal services;
explicit non-production wording.
```

Test file:

```text
tests/test_as2_postgresql_mini_poc_local_dev_p0644.py
```

---

## 7. What This Patch Does Not Validate

The following remain deferred until a real PostgreSQL mini-POC is executed:

```text
FOR UPDATE SKIP LOCKED runtime behavior;
PgBouncer SET LOCAL isolation;
CDC connector registration and event flow;
p99 latency under concurrent load;
real target infrastructure constraints;
external sink delivery / ACK behavior.
```

---

## 8. Official Evidence Boundary

This stack may be used for:

```text
dev rehearsal;
local verification;
mini-POC script preparation;
CDC topology exploration;
PgBouncer transaction-mode familiarization.
```

This stack must not be used as:

```text
official Q8 evidence;
official Q8a evidence;
official Q10 evidence;
production backend sign-off;
production audit relay sign-off;
production activation evidence.
```

---

## 9. Next Gate

The next gate remains P0.6.41 infra sign-off:

```text
Q8  — PostgreSQL available?
Q8a — Logical replication available?
Q9  — Redis durable approved?
Q10 — Audit sink chosen?
```

If Q8=YES, the next executable backend track may open:

```text
P0.6.44 or P0.6.45 — real PostgreSQL Mini-POC Phase 1
```

If Q8=YES and Q8a=NO, the real mini-POC continues with Polling Outbox checks.

If Q8=YES and Q8a=YES, the real mini-POC may include CDC feasibility checks.

---

## 10. Locked Items

Still locked:

```text
production ENABLED;
runtime default wiring changes;
synapse/runtime backend driver;
schema migration;
real relay worker;
external sink client;
operator RPC;
production activation patch.
```
