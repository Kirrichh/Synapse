# AS2 Architectural Infra Decisions — P0.6.47

**Patch name:** P0.6.47 — Architectural Infra Decisions Accepted

**Status:** `ARCHITECTURAL_INFRA_DECISIONS_ACCEPTED`

**Production status:** `LOCKED`

Production `ENABLED` remains `LOCKED`.

## 1. Scope

P0.6.47 records the AS2 architectural infrastructure decisions accepted by the project team for backend durability, audit relay branch, Redis eligibility, and audit sink class.

This is a documentation-only architecture decision patch. It does not implement a backend driver, add a schema migration, add an audit relay worker, add an external sink client, change runtime wiring, modify production flags, or activate production `ENABLED`.

## 2. Accepted decisions

| Question | Decision | Accepted project architecture outcome |
|---|---|---|
| Q8 | YES | PostgreSQL is selected as the primary durable backend. |
| Q8a | YES | CDC / Debezium / `pgoutput` is selected as the preferred audit relay branch; Polling Outbox remains the approved fallback branch. |
| Q9 | NO | Redis is rejected as the primary durable backend. |
| Q10 | YES | Kafka-compatible audit sink class is selected; Redpanda is the dev/open verification sink. |

## 3. Evidence considered

The project considered PostgreSQL SQL runtime verification and GitHub Actions open-stack verification.

Recorded open-stack result:

```text
9 passed in 6.44s
```

Verified path:

```text
PostgreSQL outbox insert -> Debezium connector -> Redpanda/Kafka-compatible topic -> emitted CDC event observed by event_id
```

Evidence summary:

- PostgreSQL SQL runtime verification covered the durable SQL semantics needed by AS2 idempotency and outbox planning.
- GitHub Actions open-stack verification covered PostgreSQL, PgBouncer, Debezium, and Redpanda in the verification-only stack.
- The open-stack CDC evidence showed an actual PostgreSQL outbox event emitted through Debezium into a Redpanda/Kafka-compatible topic and observed by `event_id`.
- Polling fallback evidence covered `FOR UPDATE SKIP LOCKED` claim semantics.

This evidence is sufficient to close Q8/Q8a/Q9/Q10 as project architecture decisions. It is not production activation evidence for a specific deployment.

## 4. Selected baseline

| Area | Baseline |
|---|---|
| Backend | PostgreSQL |
| Idempotency store | PostgreSQL table |
| Audit outbox | PostgreSQL table written in the same local transaction as the idempotency transition |
| Preferred relay branch | CDC with Debezium and `pgoutput` |
| Fallback relay branch | Polling Outbox with `FOR UPDATE SKIP LOCKED` |
| External audit sink class | Kafka-compatible topic/broker |
| Dev/open verification sink | Redpanda |

## 5. Rationale

- PostgreSQL preserves local transaction semantics for the idempotency state transition plus audit outbox append.
- CDC / Debezium / `pgoutput` is preferred because the open stack verified PostgreSQL -> Debezium -> Redpanda delivery.
- Polling remains the fallback because `FOR UPDATE SKIP LOCKED` claim semantics were verified.
- Redis is rejected as the primary durable backend because it requires separate durability risk acceptance.
- Kafka-compatible sink class matches the Debezium relay model.

## 6. Relation to P0.6.41

P0.6.47 supersedes the `OWNER_PENDING` posture for Q8/Q8a/Q9/Q10 as architecture decisions.

P0.6.41 remains useful for deployment-specific evidence collection, evidence custody, refresh cadence, and escalation mechanics.

Deployment-specific evidence is still required before production activation. Production `ENABLED` remains `LOCKED`.

## 7. Next patches

- P0.6.48 — Backend Vendor ADR Finalization
- P0.6.49 — Audit Relay ADR Finalization
- P0.6.50 — PostgreSQL Backend Schema / Driver Plan

## 8. Remaining production blockers

Production activation remains blocked by:

- backend implementation;
- audit relay implementation;
- deployment-specific sink configuration;
- runbook updates;
- SLO/observability wiring;
- integration tests;
- rollback/degraded-mode procedure;
- separate production activation patch.

## 9. Final outcome

```text
ARCHITECTURAL_INFRA_DECISIONS_ACCEPTED
```

Production `ENABLED` remains `LOCKED`.
