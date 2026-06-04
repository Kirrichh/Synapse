# ADR: AS2 Audit Relay

Status: **DRAFT — DEPENDENT ON Q8a / Q10 INFRA ANSWERS**

Patch: **P0.6.42 — Audit Relay ADR Draft**

Outcome target: **AUDIT_RELAY_ADR_DRAFT_READY_FOR_INFRA_DEPENDENCY_REVIEW**

Production status: **LOCKED**

---

## 1. Context

P0.6.35 introduced the in-memory `OutboxAuditSink` skeleton. P0.6.36 linked idempotency state transitions to audit append through audit-first ordering. P0.6.38 proved that the Integration Harness can connect ProviderAggregator, bridge conversion, PreparedAS2Inputs hashing, IdempotencyStore, Projection Handoff, and audit evidence without changing production runtime wiring.

P0.6.39 created the first Audit Relay ADR draft and explicitly marked relay design as blocking for production activation. P0.6.40 through P0.6.41 then clarified backend and infrastructure dependencies, especially:

```text
Q8  — PostgreSQL operational availability
Q8a — PostgreSQL logical replication / CDC readiness
Q9  — Redis durable approval
Q10 — external audit sink selection
```

This ADR draft defines the **audit relay contract** and two viable implementation branches:

```text
Branch A — CDC / Logical Replication / Debezium / pgoutput
Branch B — Polling Outbox
```

Final branch selection is intentionally deferred until Q8a and Q10 are answered.

---

## 2. Non-goals

P0.6.42 does **not** implement audit relay.

It does not add:

```text
backend driver
PostgreSQL client
Redis client
schema migration
runtime wiring change
audit relay worker
message broker integration
external audit sink client
production ENABLED activation
```

It also does not select the final relay branch before Q8a/Q10 answers are recorded.

---

## 3. Required invariant

The AS2 audit relay must preserve the P0.6.35–P0.6.38 audit/idempotency safety invariant:

```text
no audit record → no idempotency state transition
```

Production materialization must ensure that an idempotency state transition and its audit outbox insert are written as one local atomic unit or by an explicitly approved equivalent.

Accepted linkage model:

```text
Preferred:
  local DB transaction containing:
    - idempotency state transition
    - audit outbox insert

Allowed only by separate ADR:
  Saga with explicit compensation and operator-visible unresolved state

Rejected:
  dual-write: state write + separate audit publish
```

---

## 4. Relay delivery model

Baseline delivery model:

```text
at-least-once delivery + idempotent downstream consumer
```

The relay must not promise transport-level exactly-once delivery unless a later ADR proves the guarantee end-to-end across:

```text
outbox storage
relay cursor / checkpoint
transport
external audit sink
consumer deduplication
operator replay
```

The accepted production baseline is therefore **effectively-once processing through idempotent consumption**, not exactly-once transport.

---

## 5. Event identity and idempotent consumer contract

Candidate event identity:

| Candidate | Status | Notes |
| --- | --- | --- |
| `event_id` | Preferred baseline | Stable outbox envelope identity. Suitable for downstream deduplication. |
| `record_hash()` | Supporting evidence | Payload hash / chain evidence. Not sufficient alone if equivalent payload appears in multiple positions. |
| logical event key | Future option | May combine event type, correlation_id, sequence, and record hash after production schema is selected. |

Baseline requirement:

```text
External audit sink / consumer must be able to deduplicate by stable event identity.
```

Initial recommended dedup key:

```text
event_id
```

Every emitted relay payload must include:

```text
event_id
record_hash
previous_state_hash
correlation_id, if available
event_type / transition type
created_at / audit wall-clock metadata
sequence / ordering field, once production schema is selected
```

---

## 6. Ordering scope

The relay must define ordering scope explicitly. Global total order is not required for initial production unless Q10 external sink demands it.

Initial ordering target:

```text
partition order by outbox sequence / monotonic id
correlation_id order for events sharing the same correlation_id
```

Ordering must not rely only on wall-clock `created_at`, because wall-clock timestamps can collide, drift, or reorder across hosts.

Production outbox schema must provide a stable ordering field, for example:

```text
BIGSERIAL / identity sequence
monotonic outbox sequence
backend-specific log sequence when CDC branch is selected
```

---

## 7. Branch A — CDC / Logical Replication / Debezium / pgoutput

### 7.1 Selection condition

CDC branch may become the preferred branch only if:

```text
Q8  = YES: PostgreSQL is operationally available and approved
Q8a = YES: PostgreSQL logical replication / CDC readiness is approved
Q10 = chosen: external audit sink and delivery interface are known
```

### 7.2 Required Q8a evidence

Q8a must answer the checklist tracked in `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md` and `docs/AS2-BACKEND-VENDOR-ADR.md`, including:

```text
wal_level=logical available and changeable
max_replication_slots >= number_of_connectors + 2
AS2 planning baseline minimum: 4
max_wal_senders >= max_replication_slots
replication user creation permitted
publication creation permitted
pgoutput plugin available
wal2json only as legacy fallback if still supported by selected CDC version
wal_keep_size >= 1GB or DBA-approved WAL retention policy
max_slot_wal_keep_size configured or managed-service WAL retention cap documented
heartbeat table permitted
replication slot lag monitoring available
pg_hba.conf or managed-service equivalent permits replication connections
outbox table uses REPLICA IDENTITY DEFAULT or approved key-based identity
managed-service restrictions documented
```

`REPLICA IDENTITY FULL` is rejected as the AS2 baseline because it can amplify WAL volume for update-heavy relay flows.

### 7.3 CDC event flow

```text
1. AS2 runtime writes idempotency transition and audit outbox row in one local transaction.
2. PostgreSQL commits the transaction.
3. Logical replication stream exposes the outbox row.
4. Debezium / CDC connector reads the change.
5. Connector emits to selected external transport / sink.
6. Consumer deduplicates by event_id.
7. Monitoring tracks slot lag, relay lag, failed publishes, and sink acknowledgements.
```

### 7.4 CDC advantages

```text
low relay latency
commit-order visibility from database log
lower polling query load on OLTP database
clear fit with PostgreSQL transactional outbox pattern
```

### 7.5 CDC risks and required mitigations

| Risk | Required mitigation |
| --- | --- |
| Stuck replication slot fills disk | `max_slot_wal_keep_size` or managed WAL retention cap; slot lag alerting. |
| Quiet periods prevent slot progress | Heartbeat table / heartbeat topic strategy. |
| Plugin mismatch | `pgoutput` preferred; `wal2json` legacy only if explicitly supported and accepted. |
| Managed-service restrictions | Q8a must document what is allowed, blocked, or requires admin approval. |
| WAL amplification | Outbox table uses `REPLICA IDENTITY DEFAULT` or approved key-based identity; no `FULL` baseline. |
| Connector outage | Alert on replication slot lag, connector failure, and external sink lag. |

### 7.6 CDC cleanup strategy

CDC branch does not require a `processed` flag for relay correctness, but outbox rows still need retention/cleanup.

Production recommendation:

```text
partition outbox by created_at or sequence range
retain rows until external sink confirmation / audit retention window is satisfied
archive or drop old partitions according to retention policy
never ordinary-auto-delete Poison Pill audit evidence
```

---

## 8. Branch B — Polling Outbox

### 8.1 Selection condition

Polling branch is the fallback when:

```text
Q8  = YES but Q8a = NO
or CDC is blocked by managed-service / security policy
or Audit Relay ADR chooses polling as lower-operational-complexity MVP
```

Polling does not require logical replication, but it does require careful database load, ordering, locking, cleanup, and monitoring design.

### 8.2 Polling event flow

```text
1. AS2 runtime writes idempotency transition and audit outbox row in one local transaction.
2. Relay worker polls the outbox table for unpublished rows.
3. Worker claims a batch using row-level locking.
4. Worker publishes rows to external sink.
5. Worker records processed/delivered state after confirmed delivery.
6. Retry worker picks up failed or expired claims.
7. Monitoring tracks lag, retries, dead letters, and stuck claims.
```

### 8.3 Baseline SQL pattern

Polling implementation should use a single-round-trip claim-and-process pattern.
The claim must not be implemented as an unconstrained two-step `SELECT` followed by a later `UPDATE`, because that leaves a race window between row selection and ownership transfer.

Baseline claim statement:

```sql
UPDATE as2_audit_outbox
SET relay_status = 'claimed',
    claimed_at = $now,
    relay_worker_id = $worker_id
WHERE event_id IN (
    SELECT event_id
    FROM as2_audit_outbox
    WHERE relay_status = 'pending'
    ORDER BY outbox_sequence
    FOR UPDATE SKIP LOCKED
    LIMIT $batch_size
)
RETURNING *;
```

Required properties:

```text
claim and row retrieval happen in one database round-trip
parallel pollers skip locked rows instead of waiting
claimed rows are returned immediately for publish processing
claim transaction remains short and bounded
```

Final publish acknowledgement updates rows to `delivered` only after external sink confirmation.

### 8.4 Polling advantages

```text
works without logical replication
simpler infrastructure dependency model
transparent SQL operational inspection
easier to run in environments where CDC is blocked
```

### 8.5 Polling risks and required mitigations

| Risk | Required mitigation |
| --- | --- |
| Increased OLTP load | batch size tuning, index on `(relay_status, outbox_sequence)`, adaptive polling interval. |
| Lock contention | single-round-trip `UPDATE ... WHERE event_id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *`, short transactions, bounded batch size. |
| Out-of-order wall-clock timestamps | order by monotonic sequence / identity, not `created_at`. |
| Worker crash after claim | claim timeout / lease expiry / retry policy. |
| Publish succeeded but ACK update failed | idempotent downstream consumer by `event_id`; retry may redeliver. |
| Table bloat | partitioning and cleanup strategy; avoid unbounded row-by-row deletes. |

### 8.6 Polling interval strategy

Polling must use adaptive intervals rather than a fixed aggressive loop.

Baseline:

```text
events found:
  next poll after 250 ms baseline

empty outbox:
  exponential backoff up to 2 s

final values:
  configurable and validated in mini-POC / load test
```

This keeps low relay latency when work exists while reducing database load during quiet periods.

### 8.7 Polling claim lease expiry

Polling branch must support reclaiming abandoned claims.

Baseline:

```text
claim_lease_expiry: 60 seconds
production value: configurable
alert threshold: claimed events older than 2 * claim_lease_expiry
```

Initial stale claim recovery pattern:

```sql
UPDATE as2_audit_outbox
SET relay_status = 'pending',
    claimed_at = NULL,
    relay_worker_id = NULL
WHERE relay_status = 'claimed'
  AND claimed_at < NOW() - interval '60 seconds';
```

The final production value must be chosen from sink p99 ACK latency, batch size, retry policy, and operator SLO.
A worker may release or reclaim its own orphaned claims on startup if worker identity is stable and the action is audit-visible.

### 8.8 Polling cleanup strategy

Polling branch needs explicit cleanup because rows move through relay statuses.

Baseline:

```text
pending / claimed / failed rows retained for operator visibility
published rows retained until sink confirmation and retention window
production table is time-partitioned or sequence-range partitioned
processed-event cleanup uses DROP PARTITION / DETACH PARTITION where possible
row-by-row DELETE is rejected as the production baseline because it creates
  WAL pressure, dead tuples, and VACUUM load
row DELETE + VACUUM is allowed only as controlled low-volume dev/test fallback
Poison Pill audit evidence follows Poison Pill retention policy
```

A daily cleanup job may advance partition archival or partition drop after the retention window is satisfied.

---

## 9. Retry policy

The relay must use bounded, observable retry behavior.

Baseline retry policy:

```text
transient sink failure:
  retry with exponential backoff and jitter

persistent sink failure:
  retain event in failed / retryable state
  raise operator alert

schema/validation failure:
  mark as relay poison event candidate
  do not silently drop

unknown failure:
  fail-closed for critical audit events
```

Retry attempts must not mutate the original audit event payload.

---

## 10. Backpressure policy

Backpressure must be explicit because audit relay failure can otherwise hide production risk.

Baseline:

```text
critical audit events:
  fail-closed if durable outbox write is unavailable

durable outbox write failure:
  idempotency state transition MUST NOT proceed
  runtime enters degraded mode
  no new projections are accepted until durable outbox recovers

relay lag above threshold:
  alert and keep accepting only if durable outbox remains healthy

outbox storage near capacity:
  escalate and consider runtime safety stop according to operator runbook

diagnostic / non-critical events:
  may be best-effort only if explicitly classified as non-critical
```

No event is non-critical by default.

---

## 11. Dead-letter / poison relay event policy

A relay poison event is different from an AS2 idempotency Poison Pill.

```text
AS2 Poison Pill:
  same correlation_id + different prepared_inputs_hash
  terminal idempotency failure
  security/operator triage

Relay poison event:
  outbox event cannot be serialized, validated, delivered, or accepted by sink
  relay-specific failure requiring operator triage
```

Relay poison events must:

```text
remain inspectable
preserve original payload and record_hash
not be ordinary-auto-deleted
raise operator alert
not block unrelated events indefinitely unless ordering policy requires it
```

---

## 12. Monitoring and SLO signals

The relay must expose at least:

```text
outbox oldest pending age
outbox pending count
outbox claimed count
outbox failed count
relay publish success rate
relay publish failure rate
relay retry count
external sink ACK latency
end-to-end relay lag
replication slot lag, for CDC branch
poll batch latency, for polling branch
consumer dedup hit rate
```

Alerts should include:

```text
oldest pending age > threshold
replication slot lag > threshold
outbox storage > threshold
relay publish failure spike
sink ACK latency above threshold
relay poison event created
```

---

## 13. Security and compliance requirements

Audit relay must preserve forensic properties:

```text
events are append-only evidence
record_hash and previous_state_hash remain unchanged
downstream sink can prove event identity / dedup identity
operator actions are logged
Poison Pill and relay poison evidence are retained according to runbook policy
```

Q10 must define:

```text
external sink type
retention / immutability expectations
authentication and authorization model
data classification
operator access model
compliance ownership
```

---

## 14. Dependency on P0.6.41 answers

| Answer | Relay implication |
| --- | --- |
| Q8=YES, Q8a=YES | CDC branch remains viable and may become preferred after Q10. |
| Q8=YES, Q8a=NO | PostgreSQL can remain backend candidate; relay branch becomes polling fallback. |
| Q8=NO, Q9=YES | Redis durable branch requires separate relay strategy; this ADR must be revised. |

If Q8=NO and Q9=YES, a Redis Streams / durable Redis relay path requires a separate ADR revision.
That revision must cover stream retention, consumer group rebalancing, memory pressure, AOF / persistence risk, lack of SQL operator queries, and external sink delivery semantics.
| Q10 chosen | Relay sink-specific delivery, auth, retention, and ACK model can be finalized. |
| Q10 unresolved | Relay ADR cannot be approved for production. |

---

## 15. Approval gates before implementation

Audit relay implementation must not begin until:

```text
Q8/Q8a/Q9/Q10 answers are recorded or explicitly escalated
external audit sink is selected or a temporary sink is explicitly approved
relay branch is chosen or implementation is explicitly scoped as branch-neutral
outbox schema requirements are accepted
retry/backpressure/dead-letter policies are approved
operator runbook is updated
mini-POC dependencies are identified when PostgreSQL is selected
```

---

## 16. Locked items

The following remain locked in this ADR draft:

```text
production ENABLED activation
runtime default wiring changes
backend driver implementation
schema migration
audit relay worker implementation
external sink client
operator RPC
automatic stale retry
concurrent provider/backend execution
```

---

## 17. Draft outcome

P0.6.42 outcome target:

```text
AUDIT_RELAY_ADR_DRAFT_READY_FOR_BRANCH_SELECTION_AFTER_Q8A_Q10
```

This ADR is ready for architecture review as a draft, but not for implementation or production approval.
---

## 24. P0.6.42a Polling Semantics Clarification Status

P0.6.42a records the polling-semantics follow-up accepted by the team after the P0.6.42 draft review.

Added clarifications:

```text
- single-round-trip polling claim using UPDATE ... WHERE event_id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *
- adaptive polling intervals: 250 ms after events are found, exponential backoff up to 2 s when empty
- claim lease expiry baseline: 60 seconds, production-configurable
- polling cleanup baseline: partitioning + DROP PARTITION / DETACH PARTITION, not row-by-row DELETE
- degraded mode: outbox write failure blocks idempotency transition and new projections
- Redis Streams branch requires separate ADR if Q8=NO and Q9=YES
```

P0.6.42a does not implement a relay worker, schema migration, backend client, runtime hook, or production `ENABLED` activation.

Outcome remains:

```text
AUDIT_RELAY_ADR_DRAFT_READY_FOR_BRANCH_SELECTION_AFTER_Q8A_Q10
```

