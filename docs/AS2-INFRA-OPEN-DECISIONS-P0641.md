# AS2 Infra Open Decisions — P0.6.41

Status: `INFRA_SIGNOFF_IN_PROGRESS`

Latest governance hardening: `P0.6.41c — Evidence Custodian / Refresh / Escalation Template`.

Outcome target: one of:

- `INFRA_DECISIONS_RECORDED`
- `INFRA_DECISIONS_PARTIAL_BLOCKERS_RECORDED`
- `INFRA_DECISIONS_UNRESOLVED_ESCALATED`

## 1. Scope

P0.6.41 records operational answers and named-owner accountability for the backend and audit infrastructure decisions that block AS2 production materialization.

This document tracks the open decisions raised by `docs/AS2-BACKEND-VENDOR-ADR.md` after P0.6.40c and the final P0.6.41 clarification pass.

## 2. Non-goals

P0.6.41 does not:

- implement a backend driver;
- add PostgreSQL, Redis, SQLite, or CAS clients;
- add schema migrations;
- implement audit relay;
- modify `synapse/` runtime code;
- modify `as2_runtime_wiring.py`;
- modify `as2_projection_handoff.py`;
- close golden replay fixtures unless a separate P0.6.40a patch is merged;
- activate production `ENABLED`.

## 3. Reference Documents

- `docs/AS2-BACKEND-VENDOR-ADR.md`
- `docs/AS2-AUDIT-RELAY-ADR.md`
- `docs/AS2-PRODUCTION-READINESS-VOTE-P0639.md`
- `docs/AS2-OPERATOR-RUNBOOK-DRAFT.md`
- `docs/AS2-SLO-TARGETS-DRAFT.md`

## 4. Open Decision Tracking Table

| ID | Question | Named Owner | Supporting Teams | Required Answer | Evidence / Source | Deadline | Status | Blocks |
|---|---|---|---|---|---|---|---|---|
| Q8 | Is PostgreSQL operationally available and approved for AS2 persistent state? | `PENDING_NAMED_OWNER` — must be a named Infra / Platform / DBA person, not a team alias. | INFRA / PLATFORM / DBA | YES/NO plus HA, backup, monitoring, connection-pooling, and managed-service constraints. | `PENDING_EVIDENCE` — DBA/platform ticket, managed-service config extract, or signed platform response. | Owner due: `2026-06-06`; answer due: `2026-06-18` unless project lead records a stricter date. | OWNER_PENDING | Backend selection, mini-POC, production materialization. |
| Q8a | Is PostgreSQL logical replication / CDC available for audit relay planning? | `PENDING_NAMED_OWNER` — must be a named Infra / Platform / DBA / Data Platform person, not a team alias. | INFRA / PLATFORM / DBA / DATA PLATFORM | YES/NO plus completed CDC checklist in Section 5. | `PENDING_EVIDENCE` — logical replication settings, slot/sender limits, plugin availability, WAL retention, and access-control evidence. | Owner due: `2026-06-06`; answer due: `2026-06-18` unless project lead records a stricter date. | OWNER_PENDING | Audit Relay ADR, PostgreSQL CDC relay option, production activation planning. |
| Q9 | Is Redis approved as durable storage, not only cache, with explicit persistence/failover risk acceptance? | `PENDING_NAMED_OWNER` — must be a named Infra / Platform lead plus a Security risk-acceptance owner. | INFRA / PLATFORM / SECURITY | YES/NO plus Redis durability checklist, data-loss window acceptance, AOF rewrite latency risk, backup/failover, and operator-query strategy. | `PENDING_EVIDENCE` — Redis platform policy, persistence/failover settings, and signed Security risk decision. | Owner due: `2026-06-06`; answer due: `2026-06-18` unless project lead records a stricter date. | OWNER_PENDING | Redis backend eligibility, fallback strategy, security risk acceptance. |
| Q10 | Is the external audit sink chosen? | `PENDING_NAMED_OWNER` — must be a named Security/Audit/Compliance owner plus a Data Platform delivery owner. | SECURITY / AUDIT / COMPLIANCE / DATA PLATFORM | YES/NO plus sink type, delivery interface, retention, immutability/compliance expectations, and ownership. | `PENDING_EVIDENCE` — sink decision record, compliance retention decision, delivery/auth model, or explicit unresolved blocker. | Owner due: `2026-06-06`; answer due: `2026-06-18` unless project lead records a stricter date. | OWNER_PENDING | Audit Relay ADR finalization, production audit posture, production activation planning. |

## 5. Q8a CDC / Debezium Readiness Checklist

The Q8a answer must not be a generic “CDC is supported”. It must explicitly answer each item below.

- [ ] `wal_level=logical` available and changeable.
- [ ] `max_replication_slots >= number_of_connectors + 2`.
  - AS2 audit relay planning baseline minimum: `4`.
- [ ] `max_wal_senders >= max_replication_slots`.
- [ ] Replication user creation permitted.
- [ ] Publication creation permitted.
- [ ] `pgoutput` plugin available.
- [ ] `wal2json` accepted only as legacy fallback if `pgoutput` is unavailable and the selected Debezium/CDC version still supports it.
- [ ] `wal_keep_size >= 1GB` or an environment-specific WAL retention policy accepted by DBA.
- [ ] Heartbeat table permitted for Debezium / CDC health.
- [ ] Replication slot lag monitoring available, for example `restart_lsn` vs `pg_current_wal_lsn()` or managed-service equivalent.
- [ ] `max_slot_wal_keep_size` configured, or an equivalent managed-service WAL retention cap documented.
- [ ] Outbox table uses `REPLICA IDENTITY DEFAULT` or an approved key-based identity; `REPLICA IDENTITY FULL` is rejected for the AS2 baseline because it can amplify WAL volume.
- [ ] `pg_hba.conf` or managed-service equivalent permits replication connections from the CDC host.
- [ ] Managed-service restrictions documented.

## 6. PostgreSQL Mini-POC Acceptance Notes

If Q8 is YES and PostgreSQL remains preferred, PostgreSQL is not selected for implementation until a mini-POC validates the AS2 access patterns in a production-equivalent environment.

The mini-POC must include:

1. `INSERT ... ON CONFLICT DO NOTHING` reservation latency under representative load.
2. `UPDATE ... WHERE state = expected RETURNING *` conditional transition latency and zero-row rejection semantics.
3. One local transaction containing idempotency update plus audit outbox insert.
4. PgBouncer / Odyssey transaction-mode `SET LOCAL` isolation test:
   - transaction A executes harmless `SET LOCAL`;
   - transaction A commits;
   - transaction B verifies that the setting did not leak;
   - the driver proves no dependency on session-level state.
5. Logical replication slot creation or explicit confirmation that the chosen relay path does not need CDC.
6. p99 latency under the Standard production profile, with burst conditions represented or explicitly deferred.

## 7. Redis Durable Approval Notes

If Q9 is YES, Redis eligibility requires written acceptance of the following:

- Redis is approved as durable state storage, not cache only.
- AOF policy is specified: `appendfsync=everysec` or `appendfsync=always`.
- `appendfsync=everysec` may lose up to approximately 1,000 ms of recent idempotency reservations on hardware/node failure.
- `BGREWRITEAOF` may cause latency spikes that violate the preferred p99 ≤ 10 ms target.
- Replication, failover, backup, restore, eviction, memory pressure, persistence-lag monitoring, cold archive, and secondary-index strategies are documented.
- Security accepts or rejects the data-loss window explicitly.

If Redis is approved only as cache, Redis is not eligible as the sole idempotency backend.

## 8. External Audit Sink Notes

Q10 must identify the external audit sink or explicitly record it as unresolved.

The answer should include:

- sink type, for example Kafka, S3/object storage, SIEM/Splunk/Datadog, database, or managed audit platform;
- delivery interface and authentication model;
- retention and immutability requirements;
- expected delivery model: at-least-once with idempotent consumer remains the baseline;
- backpressure and retry expectations;
- Data Platform and Security/Audit owners.

## 9. Evidence Format Requirements

P0.6.41b records the minimum acceptable evidence format for each open decision. Verbal confirmation or a team-channel acknowledgement is not enough for P0.6.41 closure.

| ID | Required Evidence Format | Notes |
|---|---|---|
| Q8 | `SHOW wal_level` output where applicable; managed-service documentation or configuration extract; DBA/platform ticket; or signed platform response confirming PostgreSQL availability, HA, backups, monitoring, and connection pooling. | If PostgreSQL is available but constrained, constraints must be explicit rather than implicit. |
| Q8a | `pg_replication_slots` output where applicable; logical-replication setting extract; Debezium / CDC connector test result; publication / replication-user evidence; or explicit statement that CDC is unavailable. | Q8a answers must map back to the Section 5 CDC checklist. |
| Q9 | Signed Redis durable risk acceptance form or explicit rejection; AOF / persistence / replication / backup / failover configuration evidence; Security sign-off for the data-loss and latency risks. | Redis cache-only approval is not enough for durable idempotency. |
| Q10 | External sink decision record; sink API documentation; delivery/auth model; ACK semantics; retention / immutability decision; Data Platform and Security/Audit ownership evidence. | Q10 must identify the external audit sink or record a formal unresolved blocker. |

Evidence must be linked from the tracking row or copied into an appendix. Evidence that cannot be shared in the repository must be referenced by stable ticket / document ID.


## 9.1 Evidence Custodian and Lifecycle Requirements

P0.6.41c records that every evidence artifact referenced by Q8/Q8a/Q9/Q10 must remain available after the original answer is submitted. A named owner for the answer is not enough if the evidence later becomes inaccessible, stale, or unverifiable.

Each evidence artifact MUST have a named evidence custodian.

The evidence custodian is responsible for:

- storage location and stable link / ticket ID;
- versioning and change history;
- audit accessibility for project, architecture, security, and compliance review;
- refresh tracking;
- preserving evidence after owner, team, or platform ownership changes.

Evidence custodian may be the same person as the named owner, but the responsibility must be explicit.

### 9.1.1 Evidence refresh cadence

Evidence must be refreshed:

- annually; or
- immediately after a significant infrastructure change.

Significant infrastructure change includes, but is not limited to:

- managed-service migration;
- PostgreSQL major upgrade;
- replication topology change;
- PgBouncer / Odyssey topology or mode change;
- Redis persistence, HA, backup, or failover change;
- external audit sink change;
- security, compliance, retention, or immutability policy change.

The refresh date MUST be recorded in this tracking document or in a linked evidence ticket / document.

### 9.1.2 Evidence timestamp format

All evidence artifact timestamps MUST include timezone. The preferred format is ISO 8601 UTC with `Z` suffix.

Example:

```text
2026-06-18T17:00:00Z
```

Evidence without timestamp or timezone must be treated as incomplete until the submitting owner/custodian records the missing metadata.

## 10. SLA and Escalation

Each open decision must have a named human owner and explicit deadline.

P0.6.41 follow-up fixes the default calendar control points for this tracking document:

- named owner assignment due: `2026-06-06`;
- answer / evidence due: `2026-06-18`;
- all dates are interpreted as UTC calendar dates unless the project lead records a stricter timezone-specific deadline;
- the project lead may replace these with stricter sprint dates, but must record the concrete replacement date in this document or the issue tracker;
- a team alias does not satisfy the named-owner requirement.

If a named owner is not assigned by the owner-assignment deadline:

1. the item remains `OWNER_PENDING`;
2. the item is marked `BLOCKED_BY_OWNER_ASSIGNMENT`;
3. the blocker is escalated to the project lead and architecture review;
4. mini-POC opening remains blocked unless Q8 has an independently recorded YES from an accountable owner.

If no answer is recorded by the answer deadline:

1. the item remains `OPEN` but is marked `BLOCKED_BY_OWNER_RESPONSE`;
2. P0.6.41 records the blocker explicitly;
3. the blocker is escalated to project leadership / architecture review;
4. P0.7.0 Production Activation Planning remains locked.

Escalation mechanism:

- create or update a project-tracking ticket with label `as2-infra-blocker`;
- link the relevant Q8/Q8a/Q9/Q10 row and missing evidence;
- notify / CC the project lead and architecture review owner;
- record the escalation date and ticket ID in this document or in the linked issue tracker.


### 10.1 Escalation ticket template

If owner assignment or answer/evidence submission misses the recorded deadline, the escalation ticket should use the following structure.

```text
Title:
  [AS2] Q8/Q8a/Q9/Q10 blocker — owner assignment overdue

Labels:
  as2-infra-blocker
  production-readiness

Body:
  Question:
    Q8 / Q8a / Q9 / Q10

  Due date:
    YYYY-MM-DD UTC

  Current status:
    OWNER_PENDING / BLOCKED_BY_OWNER_ASSIGNMENT / BLOCKED_BY_OWNER_RESPONSE

  Required action:
    assign named owner / provide evidence / approve risk / select sink

  Evidence custodian:
    named person/account responsible for storage, versioning, and audit access

  Impact:
    blocks P0.7.0 Production Activation Planning
    blocks backend / relay finalization
```

The ticket ID must be recorded in this document or in the linked project tracker.

## 11. Impact Matrix

| Decision | Backend Selection Impact | Audit Relay Impact | Production Activation Impact |
|---|---|---|---|
| Q8 YES | PostgreSQL becomes preferred, subject to mini-POC. | Enables PostgreSQL polling/CDC options. | Still locked until mini-POC, relay ADR, runbook, SLO, rollback, and golden replay readiness close. |
| Q8 NO | PostgreSQL cannot be selected for initial production. | PostgreSQL polling/CDC path unavailable. | Forces Redis/CAS/custom reconsideration and likely delays production activation. |
| Q8a YES | CDC/Debezium path remains viable. | Audit Relay ADR may choose CDC. | Still locked until relay ADR and implementation exist. |
| Q8a NO | CDC path unavailable or blocked. | Audit Relay ADR must choose polling or non-PostgreSQL strategy. | Production activation may still proceed later if polling satisfies requirements. |
| Q9 YES | Redis can remain an alternative only with risk acceptance. | Redis Streams/consumer group relay option remains possible. | Still locked until durability and audit linkage strategy are accepted. |
| Q9 NO | Redis cannot be sole durable idempotency backend. | Redis relay/storage alternatives removed for durable path. | Reinforces PostgreSQL or CAS/custom path. |
| Q10 chosen | Audit Relay ADR can finalize delivery model. | Relay design can proceed. | Still locked until implementation and evidence exist. |
| Q10 unresolved | Relay ADR cannot finalize. | Delivery destination unknown. | P0.7.0 cannot open. |

### 11.1 Partial Answer Handling

P0.6.41b records how partial answers affect the next branch without pretending that production is unlocked.

| Partial Answer | Immediate Impact | What May Proceed | What Remains Blocked |
|---|---|---|---|
| Q8=YES, Q8a=NO | PostgreSQL remains a backend candidate; CDC is unavailable. | PostgreSQL mini-POC Phase 1, including polling-branch DB capability checks. | CDC relay branch and Audit Relay ADR final selection. |
| Q8=YES, Q8a=YES | PostgreSQL remains a backend candidate; CDC is viable and preferred for relay planning unless Q10 constrains it. | PostgreSQL mini-POC Phase 1 and CDC feasibility checks. | Audit Relay finalization until Q10. |
| Q8=NO, Q9=YES | PostgreSQL path is unavailable; Redis durable path may be explored only with risk acceptance. | Redis Streams / durable Redis relay planning in a separate ADR revision. | PostgreSQL mini-POC and current PostgreSQL-centered relay finalization. |
| Q8=NO, Q9=NO | Neither PostgreSQL nor Redis durable path is approved. | Only escalation / alternative backend discovery. | Backend selection, relay finalization, production activation. |


## 12. Mini-POC Scope Split

P0.6.41b explicitly separates PostgreSQL / backend capability validation from external sink validation so Q10 does not block work that only depends on Q8.

### 12.1 Phase 1 — pre-Q10 DB capability mini-POC

May open as soon as Q8 is recorded as YES by an accountable owner. It does not wait for Q10.

Scope:

- `INSERT ... ON CONFLICT DO NOTHING` reservation behavior and p99 latency;
- `UPDATE ... WHERE state = expected RETURNING *` conditional transition behavior and p99 latency;
- one local transaction containing idempotency update plus audit outbox insert;
- PgBouncer / Odyssey transaction-mode compatibility;
- `SET LOCAL` isolation guard;
- single-round-trip polling claim behavior with `FOR UPDATE SKIP LOCKED`;
- CDC feasibility only if Q8a is YES or explicitly under test.

Non-goals:

- external sink delivery;
- sink authentication;
- sink ACK semantics;
- end-to-end relay lag through the final sink;
- production runtime wiring or `ENABLED` activation.

### 12.2 Phase 2 — post-Q10 sink / relay validation

May open only after Q10 identifies the external audit sink or records an approved temporary sink.

Scope:

- sink delivery test;
- authentication and authorization path;
- ACK / retry behavior;
- end-to-end relay lag;
- sink-specific backpressure;
- sink-specific retention / immutability checks.

## 13. Current P0.6.41 Status

As of P0.6.41 follow-up:

- Q8: `OWNER_PENDING` until a named Infra / Platform / DBA owner is recorded.
- Q8a: `OWNER_PENDING` until a named Infra / Platform / DBA / Data Platform owner is recorded.
- Q9: `OWNER_PENDING` until named Infra / Platform and Security owners are recorded.
- Q10: `OWNER_PENDING` until named Security/Audit/Compliance and Data Platform owners are recorded.
- Backend implementation: LOCKED.
- Audit relay implementation: LOCKED.
- Production `ENABLED`: LOCKED.
- Golden replay fixture stabilization: CLOSED by P0.6.40a (`1273 passed`, `0 skipped`).
- Audit Relay ADR draft: available from P0.6.42 / P0.6.42a; final branch selection remains dependent on Q8a and Q10.

## 14. P0.6.41 Exit Criteria

P0.6.41 can close only if one of the following outcomes is recorded:

1. `INFRA_DECISIONS_RECORDED` — Q8/Q8a/Q9/Q10 answers are recorded with named owners and evidence.
2. `INFRA_DECISIONS_PARTIAL_BLOCKERS_RECORDED` — some answers are recorded, unresolved items are explicitly blocked and escalated.
3. `INFRA_DECISIONS_UNRESOLVED_ESCALATED` — answers were not provided by SLA, blockers are recorded, and escalation is initiated.

## 15. P0.6.41 Follow-up — Owner and SLA Finalization

Patch: `P0.6.41-followup-owner-sla-finalization`.

Purpose: convert the open-decision tracker from generic `TBD_BY_PROJECT_LEAD` placeholders to explicit owner-assignment and answer-deadline controls without inventing names that the repository does not know.

This follow-up does not claim that Q8/Q8a/Q9/Q10 are answered. It records that:

- every open decision now has a concrete owner-assignment due date;
- every open decision now has a concrete answer/evidence due date;
- `PENDING_NAMED_OWNER` is an invalid final state and must be replaced by a named human;
- missing owner assignment becomes `BLOCKED_BY_OWNER_ASSIGNMENT`;
- missing answer/evidence becomes `BLOCKED_BY_OWNER_RESPONSE`;
- P0.7.0 Production Activation Planning remains locked until the answer set is recorded or escalated.

### 15.1 Required fields before P0.6.41 can close

Each open decision row must be updated with:

1. named owner: concrete human owner / account, not a team alias;
2. evidence source: ticket, signed response, platform extract, security decision, or explicit blocker;
3. answer: YES/NO/UNRESOLVED with rationale;
4. answer date;
5. escalation status if owner or answer is missing.

### 15.2 Immediate next actions

1. Project Lead assigns named owners by `2026-06-06` or records a stricter project date.
2. Owners provide answers and evidence by `2026-06-18` or record an explicit blocker.
3. If Q8 is recorded as YES, the PostgreSQL mini-POC may open without waiting for Q10.
4. If Q8a or Q10 remain unresolved, Audit Relay ADR finalization remains blocked.

### 15.3 Production status

Production `ENABLED` remains `LOCKED`.


## 16. P0.6.41b — Evidence, Mini-POC Scope, and Partial-Answer Matrix

Patch: `P0.6.41b-infra-evidence-mini-poc-partial-answer-matrix`.

Purpose: harden the P0.6.41 governance tracker so infra responses are evidence-backed, partial answers have deterministic architectural impact, and PostgreSQL mini-POC Phase 1 is not blocked by Q10.

This follow-up records that:

- each Q8/Q8a/Q9/Q10 answer has an explicit evidence format;
- owner and answer deadlines are calendar dates in UTC unless the project lead records a stricter replacement date;
- escalation requires a project-tracking ticket labelled `as2-infra-blocker`;
- partial answers map to specific next branches without unlocking production;
- PostgreSQL mini-POC Phase 1 may open after Q8=YES and does not require Q10;
- sink delivery / ACK / end-to-end relay lag checks remain Phase 2 and require Q10.

P0.6.41b does not answer Q8/Q8a/Q9/Q10, does not assign owners, does not implement a backend, does not implement a relay, and does not activate production.

## 17. P0.6.41c — Evidence Custodian / Refresh / Escalation Template

Patch: `P0.6.41c-infra-evidence-custodian-refresh-escalation-template`.

Purpose: make P0.6.41 evidence handling audit-grade without answering Q8/Q8a/Q9/Q10 or assigning owners on behalf of the project lead.

This follow-up records that:

- every evidence artifact must have a named evidence custodian;
- evidence custodians are responsible for storage, versioning, audit accessibility, and refresh tracking;
- evidence refresh cadence is annual or immediately after significant infrastructure change;
- evidence timestamps must include timezone, preferably ISO 8601 UTC with `Z` suffix;
- escalation tickets must use the `as2-infra-blocker` and `production-readiness` labels and include question, due date, current status, required action, evidence custodian, and impact on P0.7.0.

P0.6.41c does not answer Q8/Q8a/Q9/Q10, does not assign named owners, does not create evidence artifacts, does not implement a backend, does not implement a relay, and does not activate production.

Production `ENABLED` remains `LOCKED`.

