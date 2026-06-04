# AS2 SLO Targets Draft

Status: **DRAFT — blocking for production activation**

P0.6.39 records draft SLO targets and observability expectations. Numeric targets must be approved before production `ENABLED` activation.

---

## 1. Draft SLO areas

| Area | Draft target placeholder | Blocking decision |
| --- | --- | --- |
| Projection handoff latency | p99 < TBD ms | target value required |
| Idempotency store availability | >= TBD % | backend-dependent target required |
| Audit outbox append failure rate | < TBD % | target and alert threshold required |
| Provider aggregation failure rate | < TBD % excluding expected denials | target required |
| Duplicate suppression correctness | 100% no-second-projection for known duplicates | must remain invariant |
| Poison Pill alerting | alert within TBD seconds | threshold N/T required |
| STALE_IN_PROGRESS age | alert after TTL + TBD margin | target required |
| Audit relay lag | p99 lag < TBD seconds | relay-dependent target required |

---

## 2. Required RED metrics

```text
Rate: requests entering AS2 integration path
Errors: provider failures, normalization failures, idempotency failures, projection failures, audit failures
Duration: aggregation latency, bridge conversion latency, reservation latency, projection latency, completion latency
```

---

## 3. Required dimensions

```text
correlation_id
request_id
agent_id, if available
provider_name
idempotency_state
failure_reason
failure_scope
event_type
```

Sensitive payload fields must not be logged unless explicitly approved.

---

## 4. Alert candidates

```text
Poison Pill count by agent_id exceeds N within window T
STALE_IN_PROGRESS count exceeds threshold
Audit append failures > threshold
Idempotency store unavailable
Audit relay lag exceeds threshold
Duplicate projection detected after completed idempotency record
```

---

## 5. Clock contract

```text
monotonic/business clock: TTL, stale detection, durations
wall-clock timestamp: audit metadata, human-readable observability
```

Production TTL/stale logic must not depend on wall-clock jumps.

---

## 6. Production activation status

This document is a draft. Production `ENABLED` remains locked until target values, owners, dashboards, and alert thresholds are approved.
