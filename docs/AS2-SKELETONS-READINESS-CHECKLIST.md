# AS2 Skeletons Readiness Checklist

Status: **planning artifact**  
Introduced by: **P0.6.34**  
Purpose: **evidence tracking for future Production ENABLED readiness**

This checklist separates design completion from executable evidence. A Production `ENABLED` readiness vote is not valid until the implemented/evidence column is complete and green.

---

## 1. Readiness Rule

```text
Designed is not implemented.
Implemented is not production-enabled.
Production ENABLED requires executable evidence, boundary guards, integration proof, and operator readiness.
```

P0.6.31-P0.6.33 completed the design column for audit persistence, persistent idempotency, and production provider aggregation. P0.6.35-P0.6.38 filled the implementation/evidence column for the approved skeleton sequence; P0.6.39 remains the readiness vote, not production activation. P0.6.40a closes the previously documented golden replay fixture gap with contract-authored AS2 replay fixtures.

---

## 2. Designed vs Implemented Matrix

| Area | Designed / RFC exists | Implemented / executable evidence |
|---|---:|---:|
| Audit Persistence / Transactional Outbox | ✓ P0.6.31 | ✓ P0.6.35 OutboxAuditSink skeleton |
| Persistent Idempotency Store | ✓ P0.6.32 | ✓ P0.6.36 PersistentIdempotencyStore skeleton |
| Production Provider Aggregator | ✓ P0.6.33 | ✓ P0.6.37 ProviderAggregator skeleton |
| Implementation dependency plan | ✓ P0.6.34 | ✓ P0.6.35-P0.6.38 completed |
| Boundary guards for OutboxAuditSink | planned | ✓ P0.6.35 implemented and green |
| Boundary guards for IdempotencyStore | planned | ✓ P0.6.36 implemented and green |
| Boundary guards for ProviderAggregator | planned | ✓ P0.6.37 implemented and green |
| Atomic audit/idempotency linkage | designed | ✓ P0.6.36 rollback-safe audit-first ordering tested |
| Conditional update semantics | designed | ✓ P0.6.36 tested |
| CHAIN_START persisted chain semantics | designed | ✓ P0.6.35/P0.6.38 tested |
| STALE_IN_PROGRESS restart recovery | designed | ✓ P0.6.36 tested |
| Poison Pill handling | designed | ✓ P0.6.36 tested |
| Provider output normalization | designed | ✓ P0.6.37 tested |
| Failure Priority Matrix | designed | ✓ P0.6.37 tested as pure deterministic selector |
| ProviderName enum tie-breaker | planned | ✓ P0.6.37 AS2ProviderName implemented and tested |
| `to_validate_kwargs()` canonical determinism | planned | ✓ P0.6.36 verified |
| Integration harness under ENABLED_FOR_TEST | planned | ✓ P0.6.38 implemented and green |
| Production ENABLED readiness vote | locked | □ not eligible yet |
| Golden replay fixture readiness | documented debt in P0.6.39 | ✓ P0.6.40a contract-authored AS2 fixtures, green with 0 skipped |

---

## 3. P0.6.35 — OutboxAuditSink Skeleton Checklist

Required evidence:

```text
✓ NoOpAuditSink remains unchanged and available.
✓ OutboxAuditSink skeleton is injectable and default-off.
✓ Skeleton uses in-memory append-only behavior only.
✓ No real network/file/DB/CAS/queue I/O exists.
✓ Event Payload vs Event Envelope split is preserved.
✓ record_hash uses deterministic payload fields only.
✓ wall-clock timestamp remains envelope metadata only.
✓ CHAIN_START first-record semantics are represented.
✓ schema_version is carried in persisted/enqueued records.
✓ append-only behavior is covered by tests.
✓ boundary guards forbid provider imports, projection calls, AgentSnapshot, AgentRuntime, Environment, and real I/O drivers.
✓ rollback path to NoOpAuditSink is documented and tested.
```

---

## 3.1. P0.6.35 Evidence Summary

```text
Implemented: synapse/runtime/as2_audit_outbox.py
Tests: tests/test_as2_audit_outbox_skeleton_p0635.py
Boundary guards: tests/test_as2_architectural_fitness.py
Evidence: append order, payload hash preservation, envelope metadata isolation, CHAIN_START/None/broken distinction, bounded queue policy, NoOp default preservation, and no-real-I/O guard.
```

## 4. P0.6.36 — PersistentIdempotencyStore Skeleton Checklist

Required evidence:

```text
✓ State model implemented: IN_PROGRESS, COMPLETED, FAILED, CANCELLED, STALE_IN_PROGRESS.
✓ FAILED(reason_code=POISON_PILL) is represented and terminal.
✓ idempotency_key = correlation_id + prepared_inputs_hash.
✓ prepared_inputs_hash uses stable_canonical_hash(prepared_inputs.to_validate_kwargs()).
✓ PreparedAS2Inputs.to_validate_kwargs() canonical determinism is verified.
✓ Conditional update semantics are implemented.
✓ IN_PROGRESS -> COMPLETED succeeds only with expected state.
✓ IN_PROGRESS -> STALE_IN_PROGRESS is covered.
✓ completed-after-stale conditional update rejection is covered.
✓ Store unavailable behavior is modeled as fail-closed for projection.
✓ Store does not cache AgentSnapshot or projection artifacts.
✓ Store does not call projection.
✓ Atomic linkage surface with OutboxAuditSink is represented with audit-first commit ordering.
✓ Boundary guards forbid provider imports, projection calls, AgentSnapshot, AgentRuntime, Environment, and real storage drivers.
✓ existing handoff-local in-memory dedup remains unchanged; integration replacement remains deferred.
```

---


## 4.1. P0.6.36 Evidence Summary

```text
Implemented: synapse/runtime/as2_idempotency_store.py
Tests: tests/test_as2_idempotency_store_skeleton_p0636.py
Boundary guards: tests/test_as2_architectural_fitness.py
Evidence: state model, conditional updates, rollback-safe audit-first atomic linkage, Poison Pill terminal behavior, STALE_IN_PROGRESS TTL handling with injected clock, store-unavailable fail-closed behavior, concurrent reservation guardrail, and PreparedAS2Inputs hash determinism.
```

## 5. P0.6.37 — ProductionProviderAggregator Skeleton Checklist

Required evidence:

```text
✓ Aggregator uses provider port interfaces only.
✓ Aggregator accepts provider-native output shapes.
✓ Aggregator validates required fields.
✓ Aggregator strips non-bridge fields.
✓ Aggregator constructs bridge-valid Host Pre-Stage payload.
✓ Failure Priority Matrix is implemented as a pure deterministic selector.
✓ AS2ProviderName enum exists in provider ports contract.
✓ Equal-priority failures use deterministic ProviderName tie-breaker.
✓ Sequential fail-fast behavior is implemented.
✓ Concurrent model compatibility is documented through selector separation.
✓ INVALID_INPUT with agent_id + agent_scoped=True maps to agent-scoped outcome semantics.
✓ Aggregator does not call projection.
✓ Aggregator does not construct AgentSnapshot.
✓ Aggregator does not call idempotency store directly.
✓ Aggregator does not write audit storage.
✓ Aggregator performs no real I/O.
✓ Boundary guards enforce the above.
✓ rollback path remains existing ready Mapping payload path; no runtime wiring is changed.
```

---


## 5.1. P0.6.37 Evidence Summary

```text
Implemented: synapse/runtime/as2_provider_aggregator.py
Contract update: synapse/runtime/as2_provider_ports.py adds AS2ProviderName.
Tests: tests/test_as2_provider_aggregator_skeleton_p0637.py
Test import hygiene: tests/__init__.py pins project-local tests.support imports in shadowed environments.
Boundary guards: tests/test_as2_architectural_fitness.py
Evidence: sequential fail-fast aggregation, bridge-valid payload construction, provider-native output normalization, required-field validation, non-bridge field stripping, P0.6.30 model-selection regression, pure Failure Priority Matrix selector, enum tie-breaker, input-order-independent representative failure selection, agent-scoped INVALID_INPUT classification, explicit dependency-injection None check, and no direct projection/idempotency/audit-storage/real-I/O coupling.
```

## 6. P0.6.38 — Integration Harness Checklist

Required evidence:

```text
✓ ProviderAggregator -> bridge-valid payload tested.
✓ bridge-valid payload -> PreparedAS2Inputs tested through existing bridge conversion.
✓ PreparedAS2Inputs -> prepared_inputs_hash determinism reused through stable_canonical_hash(to_validate_kwargs()).
✓ PersistentIdempotencyStore IN_PROGRESS reservation tested.
✓ Projection Handoff under ENABLED_FOR_TEST tested through AS2ProjectionHandoffSkeleton DI.
✓ PersistentIdempotencyStore COMPLETED transition tested.
✓ Duplicate same prepared-input hash returns existing record/result refs without second projection.
✓ Poison Pill path tested: same correlation_id + changed prepared-input hash returns terminal FAILED and no projection.
✓ Provider and normalization failures abort before idempotency reservation.
✓ Projection failure maps to idempotency FAILED.
✓ Reserve audit failure leaves no record.
✓ Complete audit failure preserves previous IN_PROGRESS state.
✓ STALE_IN_PROGRESS path tested with shared FakeClock/injected-clock TTL behavior.
✓ Idempotency outbox chain idempotency_reserved -> idempotency_completed is validated.
✓ Boundary guards remain green for as2_integration_harness.py.
✓ Existing runtime behavior remains unchanged; as2_runtime_wiring.py is not modified.
✓ Production ENABLED remains locked.
```

---

## 6.1. P0.6.38 Evidence Summary

```text
Implemented: synapse/runtime/as2_integration_harness.py
Tests: tests/test_as2_integration_harness_p0638.py
Boundary guards: tests/test_as2_architectural_fitness.py
Evidence: explicit ENABLED_FOR_TEST policy, ProviderAggregator -> bridge conversion -> PreparedAS2Inputs hash -> IdempotencyStore reservation -> ProjectionHandoff -> idempotency completion/failure, duplicate no-second-projection, Poison Pill terminal behavior, provider/normalization failure before reservation, reserve/complete audit rollback, STALE_IN_PROGRESS with injected fake clock, idempotency audit-chain validation, no direct time import, no default runtime wiring change, no projection handoff mutation, no real I/O, and no GateController mutation.
Explicit TODO before P0.6.39 or separate stabilization: tests/test_golden_replay.py fixture collection readiness remains outside P0.6.38 scope.
```

---

## 7. Observability Readiness Checklist

Design-level expectations for each skeleton:

```text
□ RED metrics design noted: Rate, Errors, Duration.
□ Structured logs include correlation_id.
□ Structured logs include request_id when available.
□ ProviderAggregator logs include provider_name. P0.6.37 preserves provider_name in failures; structured logging remains deferred.
□ Idempotency logs include idempotency_key.
□ Audit logs include event_type and chain metadata.
□ OpenTelemetry span boundaries are identified.
□ No fallback correlation_id generation is introduced.
```

Implementation may remain deferred, but skeleton designs must not make observability impossible.

---

## 8. Rollback Readiness Checklist

```text
□ OutboxAuditSink can be replaced by NoOpAuditSink through dependency injection.
□ PersistentIdempotencyStore can be disabled without changing production ENABLED state.
✓ ProviderAggregator can be bypassed by using existing ready Mapping payload path; runtime wiring remains unchanged.
✓ Integration harness is guarded by explicit ENABLED_FOR_TEST policy.
✓ No skeleton changes existing AS2 core behavior by default.
✓ All skeletons are additive and default-off.
```

---

## 9. Production ENABLED Gate

Production `ENABLED` remains locked until the following are complete:

```text
✓ P0.6.35 OutboxAuditSink skeleton accepted.
✓ P0.6.36 PersistentIdempotencyStore skeleton accepted.
✓ P0.6.37 ProductionProviderAggregator skeleton accepted.
✓ P0.6.38 integration harness accepted.
✓ Boundary guards green across all new modules.
□ No real I/O or backend vendor selected without explicit decision.
□ Operator review path specified for STALE_IN_PROGRESS and Poison Pill.
□ Readiness vote completed and accepted.
```

---

## 10. P0.6.39 Production ENABLED Readiness Vote Evidence

```text
Status: READINESS_ACCEPTED_BUT_PRODUCTION_LOCKED
```

P0.6.39 is a readiness/audit gate. It does not activate production behavior and does not change default runtime wiring.

```text
✓ P0.6.35 OutboxAuditSink skeleton accepted.
✓ P0.6.36 PersistentIdempotencyStore skeleton accepted.
✓ P0.6.37 ProductionProviderAggregator skeleton accepted.
✓ P0.6.38 IntegrationHarness skeleton accepted.
✓ P0.6.39 readiness vote document added.
✓ Operator runbook draft added.
✓ Audit relay ADR draft added and marked OPEN.
✓ SLO targets draft added and marked DRAFT.
✓ Golden replay fixture gap documented with collection-safe skip.
✓ P0.6.40a closes golden replay skip with contract-authored AS2 replay fixtures.
✓ Production ENABLED remains locked.
✓ as2_runtime_wiring.py remains unchanged.
```

### Blocking items for production activation

```text
□ Backend vendor decision / ADR approval.
□ Production persistent idempotency backend implementation.
□ Audit relay ADR approval and implementation plan.
□ Operator runbook approval.
□ SLO targets, dashboards, alerts, and owners approved.
□ Runtime activation patch defined and reviewed separately.
□ Rollback / safe-disable procedure approved.
✓ Golden replay production fixture readiness closed by P0.6.40a contract-authored AS2 replay fixtures.
```

### Non-blocking acknowledged follow-ups

```text
□ Chaos/failure injection around side-effect-before-completion failure.
□ Stronger IntegrationDuplicate result_ref contract tests.
□ Clock contract ADR for monotonic vs wall-clock separation.
□ Future concurrent provider execution RFC.
□ Distributed CAS/lock validation in the selected backend ADR.
```


---

## 11. P0.6.40a Golden Replay Fixture Stabilization Evidence

```text
Status: GOLDEN_REPLAY_FIXTURE_READINESS_CLOSED
Fixtures: tests/golden_replays/as2_happy_path_v1.json, as2_poison_pill_v1.json, as2_provider_failure_v1.json
Source: contract-authored from P0.6.38 Integration Harness contracts, not live runtime dumps.
Evidence: tests/test_golden_replay.py validates canonical prepared-input hashes, idempotency-key hashes, expected audit/idempotency outcomes, Poison Pill terminal behavior, and provider-failure no-reservation behavior.
Full suite: 1273 passed, 0 skipped.
```

Production `ENABLED` remains locked; P0.6.40a only closes the golden replay fixture gap.

---

## 12. P0.6.43 PostgreSQL Mini-POC Phase 1 Evidence

```text
Status: PHASE_1_SQL_SHAPE_VALIDATED_WITH_SQLITE_DEV_BACKEND
Tests: tests/test_as2_postgresql_mini_poc_p0643.py
Report: docs/AS2-POSTGRESQL-MINI-POC-P0643.md
Target result: 4 passed, 3 skipped for the P0.6.43 test module.
Full suite result: 1277 passed, 3 skipped.
```

P0.6.43 validates the SQL-shape contracts required by the future PostgreSQL backend implementation path using SQLite as a local development backend:

```text
✓ reserve_if_absent via INSERT ... ON CONFLICT DO NOTHING.
✓ complete_if_state / fail_if_state via UPDATE ... WHERE state = expected RETURNING *.
✓ local transaction rollback for idempotency transition + audit outbox insert.
✓ polling claim through single-round-trip UPDATE ... WHERE id IN (SELECT ...) RETURNING *.
```

Intentional SQLite limitation skips:

```text
SKIP PgBouncer / Odyssey SET LOCAL isolation.
SKIP PostgreSQL CDC / logical replication.
SKIP p99 concurrent-load validation under target PostgreSQL infrastructure.
```

P0.6.43 does not implement a backend driver, does not alter `synapse/runtime/`, does not change `InMemoryIdempotencyStore`, does not add schema migrations, and does not activate production.

Production `ENABLED` remains locked.

## 13. P0.6.44-dev Local/Open PostgreSQL Verification Stack

Status: **VERIFICATION_ONLY**

Evidence added:

```text
docker-compose.as2-postgres-mini-poc.yml
docs/AS2-POSTGRESQL-MINI-POC-P0644-DEV.md
tests/test_as2_postgresql_mini_poc_local_dev_p0644.py
```

Interpretation:

```text
Local/open-source PostgreSQL, PgBouncer, Redpanda, and Debezium verification stack is present.
The stack is explicitly marked verification-only and cannot be used as official Q8/Q8a/Q10 evidence.
```

Validated by static tests:

```text
verification-only labels and wording;
PostgreSQL logical replication controls;
PgBouncer transaction-mode controls;
optional Redpanda / Debezium CDC rehearsal services.
```

Still deferred:

```text
real PostgreSQL SKIP LOCKED runtime behavior;
PgBouncer SET LOCAL isolation;
CDC connector registration and event flow;
p99 latency under concurrent load;
target infrastructure sign-off;
external sink delivery / ACK checks.
```

Production ENABLED remains **LOCKED**.



## 14. P0.6.45-dev Open Provider Execution Attempt Evidence

Status: **OPEN_PROVIDER_EXECUTION_ATTEMPT_RECORDED**

Evidence added:

```text
docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md
tests/test_as2_postgresql_open_verification_execution_report_p0645.py
```

Interpretation:

```text
STATIC_VERIFICATION_CONFIRMED
OPEN_PROVIDER_RUNTIME_EXECUTION_BLOCKED_BY_LOCAL_RUNTIME_NO_DOCKER
```

The current execution environment does not provide Docker / Docker Compose:

```text
docker: command not found
```

Therefore the P0.6.44-dev local/open PostgreSQL/PgBouncer/Debezium stack could not be started here.

This is **not official Q8/Q8a/Q10 evidence** and does not close infra sign-off.

Remaining open-provider runtime checks:

```text
real PostgreSQL INSERT ... ON CONFLICT execution;
real UPDATE ... WHERE state = expected RETURNING * execution;
real idempotency + outbox transaction rollback;
real FOR UPDATE SKIP LOCKED polling claim;
PgBouncer SET LOCAL isolation;
CDC smoke through pgoutput / Debezium / Redpanda;
basic non-SLO latency sample.
```

Production ENABLED remains **LOCKED**.
