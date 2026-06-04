# AS2 Backend Vendor ADR — Persistent Idempotency Backend Decision

**Patch:** P0.6.40  
**Status:** DECISION REQUIRED — awaiting infra team input on Q8/Q9/Q10  
**Outcome target:** BACKEND_REQUIREMENTS_AND_DECISION_MATRIX_ACCEPTED  
**Production status:** Production `ENABLED` remains LOCKED.

## 1. Purpose

This ADR defines the production backend requirements and candidate comparison for the AS2 Persistent Idempotency Store and its transactional linkage to the audit outbox.

P0.6.40 does **not** implement a backend driver, schema migration, Redis/PostgreSQL client, runtime wiring hook, audit relay, or production activation. It records the decision surface that must be resolved before production materialization can proceed.

## 2. Scope

This ADR covers:

- persistent idempotency record storage;
- atomic `reserve_if_absent` and conditional state transitions;
- duplicate and Poison Pill detection;
- durable `result_ref` retention;
- operator inspection and forensic query requirements;
- audit outbox transaction linkage;
- backend clock contract for TTL / `STALE_IN_PROGRESS`;
- candidate backend comparison for PostgreSQL, Redis, SQLite, and CAS/custom storage;
- relay compatibility implications.

This ADR does not cover:

- concrete backend implementation;
- schema DDL;
- database driver selection;
- runtime default wiring;
- production `ENABLED` activation;
- audit relay implementation;
- golden replay fixture stabilization.

## 3. Requirements Profile

The following values are reference planning assumptions for P0.6.40 and must be validated by production environment owners and production load testing before implementation or activation.

### 3.1 TPS profiles

| Profile | Steady-state | Peak burst | Purpose |
|---|---:|---:|---|
| Pilot | 10–100 TPS | 500 TPS | first controlled deployment |
| Standard production | 100–1,000 TPS | 5,000 TPS | MVP backend selection target |
| High-throughput future | 1,000+ TPS | 10,000+ TPS | future scaling profile |

MVP backend selection targets Standard production. The MVP backend must not architecturally prevent migration to the High-throughput profile, but may require horizontal scaling, partitioning, schema changes, or a later backend evolution at that stage.

### 3.2 p99 latency targets

| Operation | Required for MVP | Preferred interactive target |
|---|---:|---:|
| `reserve_if_absent` | ≤ 50 ms p99 | ≤ 10 ms p99 |
| `complete_if_state` | ≤ 50 ms p99 | ≤ 10 ms p99 |
| `fail_if_state` / `cancel_if_state` | ≤ 50 ms p99 | ≤ 10 ms p99 |
| duplicate lookup | ≤ 50 ms p99 | ≤ 10 ms p99 |
| Poison Pill detection | ≤ 50 ms p99 | ≤ 10 ms p99 |

The required target avoids prematurely excluding PostgreSQL. The preferred target preserves a path for interactive / real-time workloads where the AS2 idempotency boundary should not consume a significant fraction of the projection latency budget.

### 3.3 Retention policy

| State / record type | Active retention | Archive / purge policy |
|---|---|---|
| `COMPLETED` | 7–30 days | purge or archive after retry/audit windows close |
| `CANCELLED` | 7–30 days | purge or archive after operator review window closes |
| `FAILED` | 30–90 days | archive based on incident/audit policy |
| `FAILED(reason_code=POISON_PILL)` | indefinite active safety block; no automatic TTL | move to cold storage after 90 days; purge only by explicit operator action with an audit log entry |
| `STALE_IN_PROGRESS` | until operator resolution | archive/purge only after explicit resolution evidence |

Poison Pill records are security evidence. Ordinary TTL cleanup must not silently remove them. If active storage pressure requires relocation, the record moves to cold forensic storage first and remains blocked until an operator explicitly clears or supersedes it according to the runbook.

### 3.4 `result_ref` limits

`result_ref` stores references, not payloads.

- Target size: ≤ 1 KiB.
- Preferred inline hard cap: ≤ 2 KiB.
- Absolute hard cap: ≤ 8 KiB.
- Typical contents: `snapshot_hash`, `derivation_record_hash`, optional compact metadata.
- Full payloads, dumps, provider-native fragments, or large error contexts are forbidden in `result_ref`.
- Large data must be externalized to CAS/blob storage with a stable reference stored in `result_ref`.

### 3.5 Operator query patterns

Production backend must support these canonical queries efficiently enough for incident response:

1. Inspect by full idempotency key.
2. Find by `correlation_id`.
3. List by `agent_id` when agent evidence is available.
4. List by state, especially `STALE_IN_PROGRESS`.
5. List `FAILED` / `POISON_PILL` records by time window.
6. Count/group by state and reason code for dashboards.
7. Lookup by `prepared_inputs_hash` for forensic investigation.
8. List records older than TTL threshold requiring operator review.

### 3.6 Deployment targets

| Environment | Target |
|---|---|
| Dev / CI | single-node acceptable |
| Staging | single-node or small HA setup acceptable |
| Production | cluster / HA / cloud-managed preferred |

A single-node deployment without HA must not become the default production target for the idempotency store.

## 4. Required AS2 Invariants

The selected backend must preserve these invariants already proven by P0.6.35–P0.6.39 skeletons:

1. No audit record → no idempotency state transition.
2. `reserve_if_absent` is atomic.
3. Conditional transitions only succeed when current state matches expected state.
4. Duplicate same `correlation_id + prepared_inputs_hash` does not run projection again.
5. Same `correlation_id` with different `prepared_inputs_hash` becomes terminal `FAILED(reason_code=POISON_PILL)`.
6. `STALE_IN_PROGRESS` requires operator review and does not auto-retry.
7. Store unavailable is fail-closed.
8. `result_ref` is durable enough to serve duplicate responses without recomputing projection.
9. Operator inspection is possible without direct runtime access.
10. Backend semantics must not require production runtime to use dual-write.

## 5. Candidate Backends

Candidates for this ADR:

- PostgreSQL
- Redis with durable configuration
- SQLite
- CAS/custom backend, including DynamoDB/S3-like conditional-write systems

## 6. Candidate Comparison Matrix

| Criterion | PostgreSQL | Redis durable | SQLite | CAS/custom |
|---|---|---|---|---|
| Atomic `reserve_if_absent` | strong via unique constraint / `INSERT ... ON CONFLICT DO NOTHING` | strong via `SET NX` / Lua | local strong via unique constraint + transaction | varies |
| Conditional update by state | strong via `UPDATE ... WHERE state = expected RETURNING *` | Lua/scripted atomic update | local transaction + `WHERE` | varies |
| Transactional audit linkage | strong, same local transaction | partial/custom; difficult if audit sink external | local strong | usually custom / weak |
| Durability | WAL / ACID policy | AOF/RDB config-dependent | file-backed local | design-dependent |
| Native TTL | no row TTL | native key TTL | no | varies |
| `STALE_IN_PROGRESS` scan | SQL query / partial index | scan, sorted sets, or custom index | SQL local | custom |
| Operator queries | excellent SQL / indexes | weaker without secondary index layer | local only | custom |
| `result_ref` storage | JSONB/text, strong | memory-backed value, size/memory pressure | local | custom |
| Relay compatibility | polling table / CDC / WAL tailing | Streams / consumer groups / custom cursor | polling only | custom cursor/log |
| Clock source | DB server timestamp or app-supplied | server-managed TTL / app-supplied | app/local | app/logical |
| Concurrency model | MVCC and transactions | single-threaded command execution per shard | writer lock limits | varies |
| Schema evolution | mature migrations | custom key schema | simple local migrations | custom |
| Production ops maturity | high; requires pooling for burst | high if durable-approved; persistence must be governed | low for distributed production | depends |
| Burst connection strategy | transaction-mode pool required for Standard production | command pipeline/cluster sizing required | not suitable for cluster burst | custom |
| Persistence risk | low with WAL/HA policy | AOF rewrite latency and appendfsync data-loss windows must be accepted | local disk risk | varies |
| Initial production fit | preferred if operationally available | alternative if durable-approved | dev/local only | future consideration |

## 7. Clock Contract

AS2 distinguishes business/monotonic time from wall-clock time.

### 7.1 Business / monotonic time

Used for:

- TTL decisions;
- `STALE_IN_PROGRESS` detection;
- durations;
- retry/stale windows;
- internal age comparisons.

### 7.2 Wall-clock time

Used for:

- audit metadata;
- operator-visible timestamps;
- observability dashboards;
- incident reports.

### 7.3 Backend-specific notes

- PostgreSQL server-side `NOW()`, `transaction_timestamp()`, or other wall-clock timestamps are not monotonic and must not be the sole source of TTL / `STALE_IN_PROGRESS` correctness. PostgreSQL may be used as backend-authoritative business time only if a later backend decision explicitly accepts DB-server time as the source of truth and documents clock-skew / NTP mitigation.
- Redis native TTL is server-managed and avoids application-worker clock skew for key expiration, but the ADR must still define forensic retention and inspectability because key expiry must not delete safety evidence such as Poison Pill records.
- CAS/custom systems require an explicit logical clock, version, or app-supplied timestamp contract.

Preferred implementation direction: application-provided monotonic/business time for TTL and stale-age decisions, plus wall-clock timestamps for audit metadata and operator observability. Wall-clock must not be the sole source of TTL/stale correctness unless the backend decision explicitly accepts and mitigates that risk.

## 8. Atomic CAS Requirement

`reserve_if_absent` MUST be atomic.

Read-then-write implementations are forbidden.

Acceptable patterns include:

- PostgreSQL reserve path: unique index + `INSERT ... ON CONFLICT DO NOTHING`, or equivalent transaction-protected insert.
- PostgreSQL transition path: `UPDATE ... WHERE state = expected RETURNING *` for `complete_if_state`, `fail_if_state`, and `cancel_if_state`. If `RETURNING` yields zero rows, the state did not match and the operation is rejected. This is optimistic conditional update, not `ON CONFLICT`.
- Redis: `SET NX` / Lua script / transactionally scripted state transition.
- SQLite: unique constraint + local transaction, for dev/local only.
- CAS/custom: conditional write with proven single-writer/compare-and-swap semantics.

The backend must also support correlation-level conflict detection for Poison Pill: same `correlation_id`, different `prepared_inputs_hash`.

Mini-POC note: PostgreSQL validation must separately prove `INSERT ... ON CONFLICT DO NOTHING` for reservation and `UPDATE ... WHERE state = expected RETURNING *` for conditional transition rejection.

## 9. PostgreSQL Operation Mapping and Pooling Requirement

For PostgreSQL, the MVP production materialization must treat reservation and transition as two separate database patterns:

```sql
-- reserve_if_absent
INSERT INTO as2_idempotency_records (...)
VALUES (...)
ON CONFLICT DO NOTHING;

-- complete_if_state / fail_if_state / cancel_if_state
UPDATE as2_idempotency_records
SET state = $new_state, updated_at = $updated_at, result_ref = $result_ref
WHERE idempotency_key = $key
  AND state = $expected_state
RETURNING *;
```

Zero rows returned from the conditional `UPDATE` means state mismatch and must be treated as rejected transition, not as success.

For the Standard production burst profile, PostgreSQL requires transaction-level connection pooling such as PgBouncer in transaction mode, Odyssey, or an equivalent managed pooling layer. Session-mode pooling is insufficient for the AS2 access pattern because high worker counts can still translate into excessive backend session pressure. Transaction-level pooling is a mandatory operational requirement for PostgreSQL MVP production materialization, not an optional optimization.

Transaction-mode pooling compatibility constraints:

- backend driver code must not depend on session state;
- SQL-level `PREPARE` statements that persist across transactions are not allowed for the initial backend path;
- protocol-level prepared statements may be supported by PgBouncer 1.21+ through `max_prepared_statements`, but this must be validated in mini-POC before use and must not be assumed by the initial implementation;
- temporary tables that must survive across transactions are not allowed;
- session-level `SET` assumptions are not allowed;
- per-transaction settings must use `SET LOCAL` inside the transaction boundary.

These constraints are part of the PostgreSQL mini-POC acceptance scope. A backend implementation that depends on session-level state is incompatible with the mandatory transaction-mode pooling requirement.

Mini-POC pooling-isolation guard:

1. Open transaction A through the production-equivalent pool.
2. Execute a harmless `SET LOCAL` statement inside transaction A.
3. Commit transaction A.
4. Open transaction B through the same pool topology.
5. Verify transaction B does not inherit transaction A's local setting.
6. Verify the backend driver does not rely on session-level state.


## 10. Transactional Audit Linkage

AS2 requires the idempotency transition and audit outbox append to be one local atomic unit, or a strictly defined proven equivalent.

Preferred model:

- Local database transaction containing both:
  - idempotency state write/update;
  - audit outbox insert.

Proven equivalent means one of:

1. Local DB transaction. Preferred and expected for initial production materialization.
2. Saga with explicit compensation and operator-visible unresolved state. Allowed only if local transaction is impossible and must be approved by a separate ADR.

Explicitly rejected:

- Dual-write: state write followed by separate audit publish.
- Audit publish followed by independent state write with no atomic linkage.
- Any design where runtime success can leave state changed but audit evidence absent.

Dual-write violates the AS2 invariant: no audit record → no idempotency state transition.

## 11. TTL / STALE_IN_PROGRESS Model

The backend must support:

- selecting `IN_PROGRESS` records older than the stale threshold;
- marking them `STALE_IN_PROGRESS` conditionally;
- preserving audit evidence for the stale transition;
- preventing late `complete_if_state(IN_PROGRESS)` once state is stale;
- listing stale records for operator review.

Native TTL may be used only for cleanup after retention rules allow it. Native TTL must not silently remove records required for operator review or security investigation.

## 12. Durable `result_ref` Model

`result_ref` must be durable enough to support duplicate requests without rerunning projection.

Required fields are compact references, not full payloads. If future result metadata grows beyond the preferred inline hard cap, large content must move to external CAS/blob storage and the idempotency record must retain only stable references. Full error context and provider-native payloads must not be embedded in `result_ref`.

## 13. Operator Inspection Model

The backend must allow an operator or admin tool to answer:

- What happened to this `correlation_id`?
- Is this record completed, failed, cancelled, stale, or Poison Pill?
- What was the original `prepared_inputs_hash`?
- What conflicting hash caused Poison Pill?
- What audit outbox event proves the state transition?
- Which stale records require review?
- Which agents have repeated incidents?

PostgreSQL satisfies these patterns most directly through indexes and SQL. Redis requires a deliberate secondary-index strategy. CAS/custom systems require explicit admin-query design.

## 14. Relay Compatibility

Relay strategy depends on the selected backend.

| Backend | Relay options |
|---|---|
| PostgreSQL | polling outbox table, CDC, WAL tailing / Debezium |
| Redis | Redis Streams, consumer groups, custom sorted-set cursor |
| SQLite | polling only, dev/single-node |
| CAS/custom | custom cursor / append log |

Baseline relay model remains: at-least-once delivery with idempotent downstream handling using an event key such as outbox `event_id` or record hash.

True exactly-once delivery is not promised by P0.6.40. Effectively-once processing requires at-least-once relay plus idempotent consumers.

## 15. Outbox Retention & Cleanup Strategy

The selected backend must define how audit outbox rows remain relay-readable without unbounded hot-storage growth.

- Polling relay: rows may be marked processed or deleted only after confirmed delivery. Default posture is a daily cleanup job for processed events after external sink confirmation and the configured retention window.
- CDC relay: rows must remain long enough for CDC consumption. PostgreSQL production designs should consider partitioning by `created_at` and dropping or archiving old partitions after the retention window.
- Audit-sensitive mode: do not delete outbox rows until external sink confirmation and audit retention requirements are satisfied.
- Poison Pill outbox entries follow the Poison Pill retention policy and must not be removed by ordinary auto-delete jobs.


## 15a. Redis Durability and Latency Risk Acknowledgment

If Redis remains a candidate for durable idempotency storage, Q9 must explicitly answer more than "Redis exists". It must confirm whether Redis is approved as durable state storage and which persistence, replication, failover, and backup modes are allowed.

Required Redis durability checklist:

- AOF policy: `appendfsync=everysec` or `appendfsync=always`;
- replication / HA topology;
- backup and restore strategy;
- memory-pressure and eviction policy;
- secondary-index strategy for operator queries;
- cold archive strategy for forensic records;
- monitoring for persistence lag, AOF rewrite, memory pressure, and failover events.

Redis AOF rewrite (`BGREWRITEAOF`) may cause latency spikes that violate the preferred p99 ≤ 10 ms SLO. Mitigation requires dedicated I/O scheduling, AOF rewrite throttling, or an explicitly accepted degraded-latency envelope. Target managed-service constraints must also be checked; some offerings restrict which persistence modes can be enabled together.

With `appendfsync=everysec`, Redis may lose up to approximately 1,000 ms of recent idempotency reservations on hardware/node failure. In AS2 terms, this can permit rare duplicate projection execution at the crash boundary. This risk must be explicitly accepted by Infra/Security if Redis is selected as durable idempotency storage, or `appendfsync=always` must be used with significant performance impact.


## 16. Conditional Recommendation

Conditional recommendation for initial production materialization:

1. If PostgreSQL is operationally available and approved for AS2 storage, PostgreSQL is preferred for the idempotency store and audit outbox because it supports local transactions, conditional state updates, durable result references, rich operator queries, and polling/CDC relay options.
2. If PostgreSQL is unavailable and Redis is approved as durable state storage, Redis may be used as a hot idempotency store only with explicit durability risk acknowledgment, secondary-index strategy, cold archive strategy, and audit-linkage design.
3. If Redis is approved only as cache, Redis is not acceptable as the sole idempotency store.
4. SQLite remains dev/local only for this production decision.
5. CAS/custom remains a future consideration and requires separate ADR approval.

## 17. Open Decisions

The following decisions block final backend selection:

| Question | Owner | Status |
|---|---|---|
| Q8: Is PostgreSQL operationally available and approved for AS2 persistent state? | INFRA / PLATFORM / DBA TEAM | OPEN |
| Q8a: Is PostgreSQL logical replication available for CDC/Debezium in the target environment? Required checklist: `wal_level=logical` available and changeable; `max_replication_slots >= number_of_connectors + 2` with AS2 baseline minimum `4`; `max_wal_senders >= max_replication_slots`; replication user creation permitted; publication creation permitted; `pgoutput` plugin available; `wal2json` accepted only as legacy fallback if `pgoutput` is unavailable and the selected Debezium/CDC version still supports it; `wal_keep_size >= 1GB` or DBA-approved WAL retention policy; heartbeat table permitted; replication slot lag monitoring available (`restart_lsn` vs `pg_current_wal_lsn()` or managed equivalent); `max_slot_wal_keep_size` configured or equivalent managed WAL retention cap documented; outbox table uses `REPLICA IDENTITY DEFAULT` or approved key-based identity, with `REPLICA IDENTITY FULL` rejected for the AS2 baseline because of WAL amplification; `pg_hba.conf` or managed-service equivalent permits replication connections from the CDC host; managed-service restrictions documented. | INFRA / PLATFORM / DBA TEAM | OPEN |
| Q9: Is Redis approved as durable storage, not only cache, with explicit persistence/failover risk acceptance? | INFRA / PLATFORM + SECURITY TEAM | OPEN |
| Q10: Is external audit sink chosen? | SECURITY / AUDIT / COMPLIANCE + DATA PLATFORM TEAM | OPEN |

### 17.0a Q8a CDC / Debezium Checklist Notes

For Q8a, a generic answer that "logical replication is supported" is insufficient. The DBA / Platform answer must explicitly cover the expanded CDC checklist in the table above.

`pgoutput` is the preferred and expected PostgreSQL logical decoding output plugin for the initial AS2 CDC planning path. `wal2json` is acceptable only as a legacy fallback if `pgoutput` is unavailable and the selected Debezium/CDC version still supports `wal2json`. For Debezium 2.0+ / modern PostgreSQL CDC paths, the team must assume `pgoutput` unless the Audit Relay ADR explicitly approves another connector path.

The DBA answer must also confirm WAL retention safety for stuck replication slots. `max_slot_wal_keep_size` or a managed-service equivalent WAL retention cap is required to prevent unbounded WAL growth from filling the primary database volume.

For the AS2 outbox baseline, the outbox table should use `REPLICA IDENTITY DEFAULT` or an approved key-based identity. `REPLICA IDENTITY FULL` is rejected as the baseline because it can amplify WAL volume for update-heavy relay flows.

`max_replication_slots` must be sized as `number_of_connectors + 2` to preserve failover and staging/headroom. For the AS2 audit relay planning baseline, the minimum acceptable value is `4`. The answer must also state whether replication slot lag monitoring is available and whether heartbeat-table writes are permitted to avoid WAL retention surprises during low-activity periods.

### 17.1 Open Decisions Resolution SLA

Q8/Q8a/Q9/Q10 must be answered before P0.6.41 can be closed. If Infra / Platform / Security / Audit owners cannot provide answers within the agreed team timeline, P0.6.41 must record the missing answer as an explicit blocker and escalate to project leadership / architecture review.

Production Activation Planning (P0.7.0) cannot be opened without recorded answers for Q8/Q8a/Q9/Q10.

## 18. Working Assumptions

The ADR currently assumes:

- Standard production target: 100–1,000 TPS steady-state.
- Standard burst target: 5,000 TPS.
- P0.6.40 comparison baseline: 100 TPS steady-state and 500 TPS burst.
- Required p99: ≤ 50 ms per idempotency transition.
- Preferred interactive p99: ≤ 10 ms per idempotency transition.
- `result_ref` target size ≤ 1 KiB; hard cap ≤ 8 KiB.
- Production deployment requires HA/cluster/cloud-managed posture.
- Poison Pill records are not removed by ordinary TTL.
- Backend must support operator inspection.
- Initial backend interface remains synchronous/blocking; async I/O and concurrent backend execution require separate RFC.

These values are reference planning assumptions and must be validated by production environment owners and production load testing before implementation or activation.

## 19. Production Activation Blockers

Production `ENABLED` remains locked until:

- backend vendor decision is accepted;
- backend schema/driver materialization is implemented and tested;
- audit relay ADR is accepted;
- audit relay implementation is complete;
- operator runbook is approved;
- golden replay readiness is closed;
- rollback plan is approved;
- SLO/observability targets are approved;
- runtime activation design is approved.

## 20. References

- PostgreSQL documentation: `INSERT ... ON CONFLICT` and transactions.
- Redis documentation: `SET` with `NX` / expiry options and `SETNX` semantics.
- Transactional Outbox pattern and transaction log tailing pattern.
- AS2 P0.6.35–P0.6.39 readiness documents and executable skeleton evidence.

## 21. P0.6.40b Clarification Status

P0.6.40b clarifies this ADR before infra review. It does not change production code, select a backend, introduce a schema, or activate production `ENABLED`. Clarifications added: PostgreSQL operation mapping, transaction-mode pooling requirement, Redis persistence risks, Q8a CDC/logical replication question, outbox cleanup strategy, clock-contract tightening, and open-decision SLA.

## 22. P0.6.40c Infra Review Checklist Clarification Status

P0.6.40c adds the final infra-review checklist details accepted by the team before distributing this ADR to Infra / Platform / DBA / Security / Audit owners. It does not change production code, tests, runtime wiring, backend implementation, schema, audit relay, golden replay fixtures, or production `ENABLED`. Clarifications added:

- Q8a detailed CDC checklist: `wal_level=logical`, replication slots, WAL senders, replication user creation, publication creation, `pgoutput`, `wal2json` fallback, and managed-service restrictions.
- PgBouncer / Odyssey transaction-mode compatibility warning: no session-state dependency, no prepared statements across transactions, no temporary tables across transactions, and `SET LOCAL` only for per-transaction settings.

Outcome: `BACKEND_ADR_READY_FOR_INFRA_REVIEW`.

## 23. P0.6.41 Final ADR Clarification and Infra Sign-Off Scope

P0.6.41 may update this ADR before the infra sign-off record is closed. The accepted final clarification scope is limited to:

- `wal2json` legacy-fallback wording for Debezium / PostgreSQL CDC;
- PgBouncer / Odyssey transaction-mode prepared-statement nuance;
- expanded Q8a CDC checklist for WAL retention, heartbeat table, replication slot lag monitoring, and replication access;
- `max_replication_slots >= number_of_connectors + 2`, with AS2 baseline minimum `4`;
- mini-POC `SET LOCAL` isolation guard for transaction-mode pooling compatibility.

P0.6.41 does not implement a backend, add a PostgreSQL or Redis client, add schema migrations, implement audit relay, change runtime wiring, add golden replay fixtures, or activate production `ENABLED`.

Outcome target: `INFRA_SIGNOFF_IN_PROGRESS` until Q8/Q8a/Q9/Q10 answers are recorded.
