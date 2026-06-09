
## SYN-CORE-01 controlled-change ownership

- Moved canonical controlled-change ownership to `synapse.change`.
- Added `python -m synapse.cli change apply --base <revision> --task <task-path>`.
- Kept `python -m personal_slice run --base <revision> --task <task-path>` as a compatibility entry point using the same runner.
- Documented prepared patch as the current acquisition mechanism; provider framework, LLM provider, and provider-selection CLI flags are not part of SYN-CORE-01.
- The existing `.syn` runtime launch paths are unchanged.

# Alpha3g P0.6.47 — Architectural Infra Decisions Accepted

- **Patch:** P0.6.47 — Architectural Infra Decisions Accepted.
- **Scope:** documentation-only architecture decision. No `synapse/` runtime code, production flags, backend driver, schema migration, relay worker, external sink client, or runtime wiring changes.
- **Q8:** YES — PostgreSQL selected as primary durable backend.
- **Q8a:** YES — CDC / Debezium / `pgoutput` selected as preferred relay branch; Polling Outbox remains fallback.
- **Q9:** NO — Redis rejected as primary durable backend.
- **Q10:** YES — Kafka-compatible audit sink class selected; Redpanda remains dev/open verification sink.
- **Evidence:** GitHub Actions open-stack verification with PostgreSQL, PgBouncer, Debezium, and Redpanda; result `9 passed in 6.44s`.
- **Outcome:** `ARCHITECTURAL_INFRA_DECISIONS_ACCEPTED`.
- **Still locked:** production `ENABLED`, backend implementation, schema migration, audit relay implementation, runtime wiring, production sink configuration, SLO/observability, and production rollout.


# Alpha3g P0.6.45-dev follow-up — External PostgreSQL Provider Verification Harness

- **Patch:** P0.6.45-dev follow-up — External PostgreSQL Provider Verification Harness.
- **Scope:** verification-only tests + report update. No `synapse/runtime` changes, no backend driver, no schema migration, no relay worker, no production wiring, no production `ENABLED` activation.
- **Added harness:** `tests/test_as2_postgresql_external_provider_p0645.py` validates real PostgreSQL semantics when `AS2_POSTGRES_TEST_DSN` is supplied through the environment only.
- **Covered checks:** `INSERT ... ON CONFLICT DO NOTHING`, `UPDATE ... WHERE state = expected RETURNING *`, local transaction rollback for idempotency + outbox, `FOR UPDATE SKIP LOCKED` concurrent polling claim, optional PgBouncer `SET LOCAL` isolation, optional logical replication / `pgoutput` feasibility, optional Debezium REST smoke, optional registered Debezium connector + actual outbox event -> emitted CDC event smoke, and a basic non-SLO latency sample.
- **Added CI path:** `.github/workflows/as2-postgres-open-provider-verification.yml` is a manual `workflow_dispatch` verification-only workflow that starts the Docker Compose stack on a GitHub-hosted runner for teams without a local PostgreSQL server.
- **Security boundary:** DSN/password/token are never recorded in repo files; external checks skip when env vars are absent.
- **Updated report:** `docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md` now records `EXTERNAL_PROVIDER_VERIFICATION_HARNESS_ADDED` while preserving `STATIC_VERIFICATION_CONFIRMED` and `OPEN_PROVIDER_RUNTIME_EXECUTION_BLOCKED_BY_LOCAL_RUNTIME_NO_DOCKER`.
- **Local portable runtime execution:** EDB PostgreSQL 16.14 Windows x86-64 binaries were initialized inside the workspace and used as a verification-only local PostgreSQL runtime.
- **Runtime verification result:** `OPEN_PROVIDER_SQL_RUNTIME_VERIFIED` / `LOCAL_PORTABLE_POSTGRES_RUNTIME_VERIFIED`; external harness result `6 passed, 2 skipped` with PASS for real PostgreSQL `ON CONFLICT`, conditional `UPDATE ... RETURNING`, transaction rollback, concurrent `FOR UPDATE SKIP LOCKED`, logical replication / `pgoutput` feasibility, and non-SLO latency sample. PgBouncer, Debezium REST, registered connector, and actual emitted CDC event remain `SKIP_PROVIDER_LIMITATION` in this local non-Docker environment and are covered by the manual Docker Compose workflow path.
- **Current local result:** without a configured provider DSN, the external-provider harness reports expected skips. Full suite result in this environment: `1286 passed, 12 skipped`.
- **Still locked:** no official Q8/Q8a/Q10 evidence, no production backend selection, no runtime default wiring change, no production ENABLED activation.


# Alpha3g P0.6.45-dev — PostgreSQL Mini-POC Open Provider Execution Attempt

- **Patch:** P0.6.45-dev — PostgreSQL Mini-POC Open Provider Execution Attempt.
- **Scope:** verification-only documentation + report validation tests. No runtime code changes.
- **Added report:** `docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md` records the attempt to execute the P0.6.44-dev open/local verification stack.
- **Environment result:** Docker / Docker Compose are unavailable in the current runtime (`docker: command not found`), so real PostgreSQL/PgBouncer/Debezium provider execution was not run.
- **Status:** `STATIC_VERIFICATION_CONFIRMED` and `OPEN_PROVIDER_RUNTIME_EXECUTION_BLOCKED_BY_LOCAL_RUNTIME_NO_DOCKER`.
- **Tests:** `tests/test_as2_postgresql_open_verification_execution_report_p0645.py` validates that the report is verification-only, non-production evidence, and preserves the required future checks.
- **Suite:** `1285 passed, 3 skipped`.
- **Still locked:** no official Q8/Q8a/Q10 evidence, no backend driver, no schema migration, no relay worker, no runtime wiring change, no production ENABLED activation.


# Alpha3g P0.6.44-dev — PostgreSQL Mini-POC Local/Open Verification Stack

- **Patch:** P0.6.44-dev — PostgreSQL Mini-POC Local/Open Verification Stack.
- **Status:** VERIFICATION_ONLY. This is not official Q8/Q8a/Q10 evidence and does not close infra sign-off.
- **Added compose stack:** `docker-compose.as2-postgres-mini-poc.yml` with local PostgreSQL 16, PgBouncer transaction-mode rehearsal, Redpanda, and Debezium.
- **Added documentation:** `docs/AS2-POSTGRESQL-MINI-POC-P0644-DEV.md` records the verification-only boundary, service purposes, deferred real PostgreSQL checks, and production lock status.
- **Added static tests:** `tests/test_as2_postgresql_mini_poc_local_dev_p0644.py` verifies the compose file is explicitly verification-only and contains PostgreSQL logical replication controls, PgBouncer transaction-mode controls, and optional CDC rehearsal services.
- **No production evidence:** this patch may be used for local/dev rehearsal only. Target infrastructure Q8/Q8a/Q10 evidence remains required.
- **Still locked:** no `synapse/` runtime backend driver, no schema migration, no relay worker, no external sink client, no production ENABLED activation.

# Alpha3g P0.6.43 — PostgreSQL Mini-POC Phase 1 (SQLite dev backend)

- **Patch:** P0.6.43 — PostgreSQL Mini-POC Phase 1 (SQLite dev backend).
- **Scope:** executable mini-POC tests and documentation only. No `synapse/` runtime code, backend driver, PostgreSQL/Redis client, schema migration, audit relay worker, runtime wiring, or production `ENABLED` activation.
- **New tests:** added `tests/test_as2_postgresql_mini_poc_p0643.py` using SQLite as a dependency-free dev backend to validate SQL-shape contracts for future PostgreSQL implementation.
- **reserve_if_absent:** validated `INSERT ... ON CONFLICT DO NOTHING RETURNING ...` idempotent insert behavior.
- **conditional transitions:** validated `UPDATE ... WHERE state = expected RETURNING ...`; zero rows means rejected transition.
- **local transaction linkage:** validated rollback when an idempotency transition and audit outbox insert are attempted in one transaction and outbox insert fails.
- **polling claim:** validated single-round-trip claim shape with `UPDATE ... WHERE event_id IN (SELECT ... LIMIT ...) RETURNING ...`.
- **SQLite limitations:** PgBouncer `SET LOCAL` isolation, CDC/logical replication, and p99 concurrent-load validation are explicit skipped checks requiring a real PostgreSQL target environment.
- **New report:** added `docs/AS2-POSTGRESQL-MINI-POC-P0643.md` with PASS/SKIP matrix and locked items.
- **Readiness checklist:** updated AS2 readiness checklist with P0.6.43 Phase 1 evidence and remaining PostgreSQL-only gates.
- **Still locked:** production `ENABLED`, runtime default wiring, backend implementation, schema migration, relay implementation, external sink client, and production rollout.

# Alpha3g P0.6.41c — Infra Evidence Custodian / Refresh / Escalation Template

- **Patch:** P0.6.41c — Infra Evidence Custodian / Refresh / Escalation Template.
- **Scope:** documentation-only governance hardening. No `synapse/` runtime code, tests, backend driver, database client, schema migration, audit relay worker, external sink client, runtime wiring, or production `ENABLED` activation.
- **Evidence custodian:** updated `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md` to require a named evidence custodian per Q8/Q8a/Q9/Q10 evidence artifact, responsible for storage, versioning, audit accessibility, refresh tracking, and preservation after ownership changes.
- **Evidence refresh cadence:** recorded annual refresh or immediate refresh after significant infrastructure changes, including managed-service migration, PostgreSQL major upgrade, replication topology change, PgBouncer/Odyssey change, Redis HA/persistence change, external audit sink change, or security/compliance policy change.
- **Timestamp format:** required evidence timestamps to include timezone, preferably ISO 8601 UTC with `Z` suffix.
- **Escalation template:** added an `as2-infra-blocker` / `production-readiness` ticket template with question, due date, current status, required action, evidence custodian, and P0.7.0 impact.
- **Still locked:** Q8/Q8a/Q9/Q10 answers, backend implementation, audit relay implementation, mini-POC code, runtime wiring changes, and production activation.

# Alpha3g P0.6.41b — Infra Evidence / Mini-POC Scope / Partial Answer Matrix

- **Patch:** P0.6.41b — Infra Evidence / Mini-POC Scope / Partial Answer Matrix.
- **Scope:** documentation-only governance hardening. No `synapse/` runtime code, tests, backend implementation, PostgreSQL/Redis client, schema migration, audit relay worker, runtime wiring, or production `ENABLED` activation.
- **Evidence formats:** updated `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md` with required evidence formats for Q8, Q8a, Q9, and Q10, including PostgreSQL config extracts, replication / Debezium evidence, Redis durable risk acceptance, and external sink API/auth/retention evidence.
- **SLA precision:** clarified that owner and answer deadlines are UTC calendar dates unless a stricter project date is recorded.
- **Escalation mechanism:** added `as2-infra-blocker` ticket / issue label guidance and explicit Project Lead / Architecture Review notification requirement.
- **Partial-answer handling:** documented impacts for Q8=YES/Q8a=NO, Q8=YES/Q8a=YES, Q8=NO/Q9=YES, and Q8=NO/Q9=NO without unlocking production.
- **Mini-POC split:** separated PostgreSQL mini-POC Phase 1 (pre-Q10 DB capabilities) from Phase 2 (post-Q10 sink delivery / ACK / end-to-end relay lag).
- **Still locked:** production `ENABLED`, backend implementation, schema migration, audit relay implementation, external sink client, runtime default wiring changes, and production rollout.

# Alpha3g P0.6.41 Follow-up — Infra Owner/SLA Finalization

- **Patch:** P0.6.41 follow-up — Infra Open Decisions Owner/SLA Finalization.
- **Type:** Documentation-only tracking hardening.
- **Updated:** `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md`.
- **Decision rows:** Q8/Q8a/Q9/Q10 now use `PENDING_NAMED_OWNER` rather than generic `TBD_BY_PROJECT_LEAD` as a final-looking placeholder.
- **Owner assignment SLA:** named owners due `2026-06-06` unless the project lead records a stricter project date.
- **Answer/evidence SLA:** Q8/Q8a/Q9/Q10 answers due `2026-06-18` unless the project lead records a stricter project date.
- **Escalation states:** `BLOCKED_BY_OWNER_ASSIGNMENT` and `BLOCKED_BY_OWNER_RESPONSE` are explicitly defined.
- **Clarification:** this patch does not invent owner names and does not record infra answers. Q8/Q8a/Q9/Q10 remain pending until accountable owners provide evidence.
- **Locked:** no backend implementation, no audit relay implementation, no schema migration, no runtime wiring change, no production `ENABLED` activation.

# Alpha3g P0.6.42a — Audit Relay ADR Polling Semantics Clarification

- **Patch:** P0.6.42a — Audit Relay ADR Polling Semantics Clarification.
- **Scope:** documentation-only ADR clarification. No `synapse/` runtime code, tests, backend driver, database client, schema migration, audit relay worker, external sink client, runtime wiring, or production `ENABLED` activation.
- **Polling claim pattern:** updated `docs/AS2-AUDIT-RELAY-ADR.md` to require a single-round-trip `UPDATE ... WHERE event_id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *` claim pattern instead of an unconstrained two-step `SELECT` + `UPDATE`.
- **Adaptive polling:** documented 250 ms baseline after events are found and exponential backoff up to 2 s when the outbox is empty; final values remain configurable and must be validated in mini-POC/load testing.
- **Claim lease expiry:** added 60 s configurable baseline for abandoned polling claims, alerting when claimed events exceed `2 * lease_expiry`, and stale-claim recovery guidance.
- **Cleanup strategy:** documented partition/drop-partition cleanup as the production baseline and rejected row-by-row `DELETE` as the baseline because of WAL pressure, dead tuples, and VACUUM load.
- **Backpressure/degraded mode:** clarified that durable outbox write failure blocks idempotency state transition and forces degraded mode with no new projections until outbox recovery.
- **Redis branch note:** recorded that a Redis Streams / durable Redis relay path requires a separate ADR revision if Q8=NO and Q9=YES.
- **Still locked:** production `ENABLED`, backend implementation, PostgreSQL/Redis clients, schema migration, audit relay implementation, runtime default wiring changes, external sink client, and production rollout.

# Alpha3g P0.6.42 — Audit Relay ADR Draft (CDC and Polling Branches)

- **Patch:** P0.6.42 — Audit Relay ADR Draft.
- **Scope:** documentation-only architecture draft. No backend driver, schema migration, audit relay implementation, runtime wiring change, external sink client, test change, or production `ENABLED` activation.
- **ADR updated:** expanded `docs/AS2-AUDIT-RELAY-ADR.md` from the P0.6.39 OPEN stub into a branch-aware relay design draft.
- **Branch A:** documented CDC / Logical Replication / Debezium / `pgoutput` path, with dependencies on Q8=YES, Q8a=YES, and Q10 external sink selection.
- **Branch B:** documented Polling Outbox fallback path for Q8=YES / Q8a=NO or CDC-blocked environments, including `FOR UPDATE SKIP LOCKED`, batch claiming, retry, ordering, and cleanup constraints.
- **Delivery model:** retained at-least-once delivery with idempotent consumer semantics as the baseline; exactly-once transport remains unclaimed.
- **Relay safety:** documented event identity, ordering scope, retry/backpressure policy, relay poison-event handling, monitoring/SLO signals, and security/compliance requirements.
- **Dependency tracking:** final relay branch selection remains blocked on P0.6.41 Q8a/Q10 answers; production activation remains locked.
- **Still locked:** production `ENABLED`, backend implementation, schema migration, runtime default wiring changes, audit relay worker implementation, external sink client, operator RPC, and production rollout.

# Alpha3g P0.6.40a — Golden Replay Fixture Stabilization

- **Patch:** P0.6.40a — Golden Replay Fixture Stabilization.
- **Scope:** test-infrastructure and readiness-documentation patch. No `synapse/` runtime code, backend ADR selection, infra tracking logic, runtime wiring, audit relay implementation, backend implementation, or production `ENABLED` activation.
- **Golden replay fixtures:** added contract-authored AS2 golden replay fixtures under `tests/golden_replays/` for `as2_happy_path_v1`, `as2_poison_pill_v1`, and `as2_provider_failure_v1`.
- **Contract source:** fixtures are authored from P0.6.38 Integration Harness contracts rather than live runtime dumps, so they validate stable replay semantics instead of freezing mutable runtime state.
- **Golden replay test:** updated `tests/test_golden_replay.py` to validate `alpha3g.as2_golden_replay_contract.v1` fixtures, including prepared-input canonical hashes, idempotency key hashes, expected audit/idempotency outcomes, Poison Pill behavior, and provider-failure no-reservation behavior.
- **Skipped test debt closed:** `python -m pytest tests/test_golden_replay.py -q` now runs the AS2 fixtures without the prior documented skip. Full suite result: `1273 passed, 0 skipped`.
- **Q8a follow-up:** added `REPLICA IDENTITY DEFAULT` / no `FULL` baseline and `max_slot_wal_keep_size` / managed WAL retention cap checks to the P0.6.41 Q8a CDC readiness checklist in `docs/AS2-BACKEND-VENDOR-ADR.md` and `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md`.
- **Still locked:** production `ENABLED`, backend implementation, database clients, schema migration, audit relay implementation, runtime default wiring changes, and production rollout.

# Alpha3g P0.6.41 — Infra Q8/Q8a/Q9/Q10 Resolution / Operational Sign-Off

- **Patch:** P0.6.41 — Infra Q8/Q8a/Q9/Q10 Resolution with final Backend ADR clarification.
- **Scope:** documentation/sign-off tracking only. No backend implementation, no PostgreSQL/Redis client, no schema migration, no runtime wiring change, no audit relay implementation, no golden replay fixture work, and no production `ENABLED` activation.
- **Backend ADR final clarification:** updated `docs/AS2-BACKEND-VENDOR-ADR.md` with `wal2json` legacy-fallback wording, PgBouncer transaction-mode prepared-statement nuance, expanded Q8a CDC checklist, `max_replication_slots >= number_of_connectors + 2` with AS2 baseline minimum `4`, and a mini-POC `SET LOCAL` isolation guard.
- **New infra tracking document:** added `docs/AS2-INFRA-OPEN-DECISIONS-P0641.md` to track Q8 PostgreSQL availability, Q8a PostgreSQL CDC/logical replication readiness, Q9 Redis durable approval, and Q10 external audit sink selection.
- **Named-owner requirement:** P0.6.41 requires a named human owner per open decision, not only a team name.
- **SLA and escalation:** open decisions require an explicit deadline; recommended SLA is end of current sprint with an upper bound of two weeks before escalation unless the project lead records a different agreed timeline.
- **Mini-POC scope recorded:** PostgreSQL selection remains locked until mini-POC validates `INSERT ... ON CONFLICT`, conditional `UPDATE ... RETURNING`, local transaction audit linkage, transaction-mode pooling isolation, and CDC feasibility when applicable.
- **Parallel track:** P0.6.40a Golden Replay Fixture Stabilization remains a separate parallel test-quality track.
- **Still locked:** production `ENABLED`, backend driver, schema migration, audit relay implementation, runtime default wiring changes, provider adapters, real storage attachment, and production rollout.

# Alpha3g P0.6.40c — Backend ADR Infra Review Checklist Clarification

- **Patch:** P0.6.40c — Backend ADR Infra Review Checklist Clarification.
- **Scope:** documentation-only micro-clarification. No production code, tests, runtime wiring, backend driver, schema migration, audit relay, golden replay fixture work, or production `ENABLED` activation.
- **ADR updated:** refined `docs/AS2-BACKEND-VENDOR-ADR.md` before actual infra / DBA / security review.
- **Q8a CDC checklist:** expanded PostgreSQL logical replication readiness to require `wal_level=logical`, `max_replication_slots >= 4`, `max_wal_senders >= max_replication_slots`, replication user creation, publication creation, `pgoutput` availability, `wal2json` fallback acceptance, and managed-service restriction notes.
- **Transaction-mode pooling compatibility:** documented that PgBouncer / Odyssey transaction mode forbids backend dependence on session state, prepared statements across transactions, temporary tables across transactions, and session-level `SET`; per-transaction settings must use `SET LOCAL`.
- **Outcome:** `BACKEND_ADR_READY_FOR_INFRA_REVIEW`.
- **Still locked:** production `ENABLED`, backend implementation, database clients, schema migration, audit relay implementation, runtime default wiring changes, tests, golden replay fixture work, and production rollout.

# Alpha3g P0.6.40b — Backend ADR Clarification before Infra Review

- **Patch:** P0.6.40b — Backend ADR Clarification before Infra Review.
- **Scope:** documentation-only clarification patch. No production code, tests, runtime wiring, backend driver, schema migration, audit relay, golden replay fixture work, or production `ENABLED` activation.
- **ADR updated:** clarified `docs/AS2-BACKEND-VENDOR-ADR.md` before infra/platform/security review.
- **PostgreSQL operation mapping:** separated `reserve_if_absent` (`INSERT ... ON CONFLICT DO NOTHING`) from conditional transitions (`UPDATE ... WHERE state = expected RETURNING *`); zero returned rows means rejected state mismatch.
- **PostgreSQL burst requirement:** documented transaction-level pooling (PgBouncer transaction mode, Odyssey, or equivalent) as mandatory for Standard production burst profile; session-mode pooling is insufficient for the AS2 access pattern.
- **Redis durability risks:** documented AOF rewrite latency risk against preferred p99 ≤ 10 ms and the `appendfsync=everysec` loss window of up to approximately 1,000 ms, which may permit rare duplicate projection at crash boundary.
- **Open decisions:** added Q8a for PostgreSQL logical replication / `wal_level=logical` / replication slots / CDC approval, and added an Open Decisions Resolution SLA for Q8/Q8a/Q9/Q10.
- **Outbox cleanup:** added polling/CDC cleanup and partitioning guidance, with Poison Pill outbox entries following Poison Pill retention.
- **Clock contract:** tightened guidance that PostgreSQL `NOW()` / wall-clock timestamps must not be the sole source of TTL / `STALE_IN_PROGRESS` correctness unless explicitly accepted with mitigation.
- **Still locked:** production `ENABLED`, backend implementation, database clients, schema migration, audit relay implementation, runtime default wiring changes, and production rollout.

# Alpha3g P0.6.40 — Backend Vendor ADR / Persistent Idempotency Backend Decision

- **Patch:** P0.6.40 — Backend Vendor ADR / Persistent Idempotency Backend Decision.
- **Scope:** documentation-only ADR patch. No backend driver, schema migration, Redis/PostgreSQL client, runtime wiring change, projection handoff change, audit relay implementation, golden replay fixture work, or production `ENABLED` activation.
- **New ADR:** added `docs/AS2-BACKEND-VENDOR-ADR.md` with status `DECISION REQUIRED — awaiting infra team input on Q8/Q9/Q10`.
- **Requirements profile:** documented three TPS profiles — Pilot, Standard production, and High-throughput future — plus two-level p99 targets: required ≤ 50 ms and preferred interactive ≤ 10 ms for idempotency transitions.
- **Retention policy:** documented tiered retention, including Poison Pill active safety blocking with no ordinary TTL, cold archival after 90 days, and purge only by explicit operator action with audit evidence.
- **Backend matrix:** compared PostgreSQL, Redis, SQLite, and CAS/custom backends against atomic CAS, conditional state transitions, durability, TTL/stale handling, transactional audit linkage, relay compatibility, operator queries, clock source, and operational maturity.
- **Clock contract:** separated business/monotonic time for TTL/stale logic from wall-clock timestamps for audit metadata and observability.
- **Atomic linkage:** clarified that acceptable audit/idempotency linkage is a local DB transaction or explicitly approved Saga with compensation; dual-write is rejected.
- **Conditional recommendation:** PostgreSQL is preferred if operationally available and approved; Redis is an alternative only if approved as durable state with explicit risk acknowledgment and secondary-index/archive strategy; SQLite remains dev/local; CAS/custom requires separate ADR.
- **Readiness update:** updated `docs/AS2-PRODUCTION-READINESS-VOTE-P0639.md` to mark Backend Vendor ADR as in progress while production `ENABLED` remains locked.
- **Still locked:** production `ENABLED`, backend implementation, schema migration, runtime default wiring changes, audit relay implementation, golden replay fixture stabilization, real provider adapters, real storage attachment, and production rollout.

# Alpha3g P0.6.39 — Production ENABLED Readiness Vote / Audit Gate

- **Patch:** P0.6.39 — Production `ENABLED` Readiness Vote / Audit Gate.
- **Outcome:** `READINESS_ACCEPTED_BUT_PRODUCTION_LOCKED`.
- **Scope:** readiness/documentation-focused patch with one test-hygiene change for golden replay fixture absence. No production `ENABLED` activation, no `as2_runtime_wiring.py` change, no `synapse/runtime` code change, no backend vendor selection, no audit relay implementation, no concurrent execution, no real provider adapters, and no real I/O.
- **New readiness document:** added `docs/AS2-PRODUCTION-READINESS-VOTE-P0639.md` capturing P0.6.35–P0.6.38 evidence, readiness checklist, blocking production activation items, non-blocking acknowledged follow-ups, and the final locked outcome.
- **New operator runbook draft:** added `docs/AS2-OPERATOR-RUNBOOK-DRAFT.md` covering Poison Pill triage, `STALE_IN_PROGRESS` review, audit chain break investigation, projection failure handling, idempotency outage response, rollback/safe-disable planning, and escalation.
- **Poison Pill policy:** correlation_id is terminally blocked automatically and a security/operator alert is required; full agent-level hold remains an operator/security decision based on evidence.
- **New audit relay ADR draft:** added `docs/AS2-AUDIT-RELAY-ADR.md` with OPEN status, at-least-once preferred delivery model, retry/backpressure/open decisions, and production activation blocking status.
- **New SLO targets draft:** added `docs/AS2-SLO-TARGETS-DRAFT.md` with draft RED metrics, observability dimensions, alert candidates, and clock contract guidance separating monotonic/business clock from wall-clock metadata.
- **Golden replay hygiene:** updated `tests/test_golden_replay.py` so missing JSON fixtures produce an explicit module-level documented skip instead of an unexplained collection problem.
- **Boundary guard tightening:** updated `tests/test_as2_architectural_fitness.py` to keep the P0.6.38 integration harness free from direct concurrency primitives while concurrent provider execution remains locked.
- **Readiness checklist:** updated `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` with P0.6.39 readiness evidence, production activation blockers, and acknowledged follow-ups.
- **Still locked:** production `ENABLED`, runtime default wiring changes, projection handoff mutation, backend vendor selection, real storage, audit relay implementation, concrete provider adapters, network/file/DB/queue I/O, operator RPC, automatic stale retry, concurrent provider execution, schema migration, and production rollout.

# Alpha3g P0.6.38 — Integration Harness Skeleton

- **Patch:** P0.6.38 — Integration Harness under `ENABLED_FOR_TEST`.
- **Scope:** production-facing, additive integration harness that connects the existing ProviderAggregator, Host Pre-Stage bridge conversion, PreparedAS2Inputs hashing, PersistentIdempotencyStore, and Projection Handoff skeletons. No default runtime wiring change, production `ENABLED` activation, real I/O, concrete provider adapters, persistent backend, GateController mutation, audit relay, concurrent execution, or golden replay readiness fix is included.
- **New module:** added `synapse/runtime/as2_integration_harness.py` with `AS2IntegrationHarness`, `IntegrationSuccess`, `IntegrationProviderFailure`, `IntegrationFailure`, `IntegrationDuplicate`, `IntegrationPoisonPill`, and `IntegrationResult`.
- **Execution flow:** `execute(...)` checks explicit `enabled_for_test`, calls `AS2ProviderAggregatorSkeleton.aggregate(...)`, converts `AggregatorSuccess.payload` through `prepare_as2_inputs_from_host_prestage(...)`, hashes `PreparedAS2Inputs.to_validate_kwargs()` with `stable_canonical_hash`, reserves the `IdempotencyKey`, calls `AS2ProjectionHandoffSkeleton.execute_projection(...)`, and completes or fails the idempotency record according to the projection result.
- **Provider/normalization failure behavior:** `AggregatorFailure` returns `IntegrationProviderFailure` and no idempotency reservation is made.
- **Bridge conversion failure behavior:** bridge exceptions return `IntegrationFailure(reason="bridge_conversion_failed:<ErrorType>")` and no idempotency reservation is made.
- **Duplicate behavior:** same `correlation_id` + same prepared-input hash returns `IntegrationDuplicate` with the existing idempotency record/result reference and does not call projection again.
- **Poison Pill behavior:** same `correlation_id` + different prepared-input hash returns `IntegrationPoisonPill`, leaves the idempotency record terminally `FAILED(reason_code=POISON_PILL)`, and does not call projection.
- **Audit/idempotency rollback evidence:** reserve audit failure leaves no record; complete audit failure preserves the previous `IN_PROGRESS` record. Successful integration proves the idempotency outbox chain `idempotency_reserved -> idempotency_completed` with a valid previous-hash link.
- **Clock injection:** the harness requires an explicit `clock: Callable[[], float]` and imports no ambient time. Integration STALE tests use the same fake clock for harness construction and `InMemoryIdempotencyStore` TTL behavior; audit timestamps remain explicit caller/envelope data and are not generated by the harness.
- **Projection execution:** P0.6.38 uses the existing approved `AS2ProjectionHandoffSkeleton` through dependency injection. `as2_projection_handoff.py` and its `_dedup_index` remain unchanged.
- **Tests:** added `tests/test_as2_integration_harness_p0638.py` covering happy path, provider failure before reservation, normalization failure before reservation, duplicate no-second-projection, Poison Pill terminal handling, reserve/complete audit rollback, projection failure -> idempotency FAILED, STALE_IN_PROGRESS with fake clock, explicit gate denial, idempotency audit-chain validation, boundary source terms, and explicit-clock/no-truthiness DI evidence.
- **Boundary guards:** updated `tests/test_as2_architectural_fitness.py` to protect `as2_integration_harness.py` from legacy runtime imports, real I/O libraries, direct projection-core calls, AgentSnapshot construction/imports, direct `time` import, runtime wiring imports, audit sink/outbox write coupling, and GateController mutation surfaces.
- **Readiness:** updated `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` to mark the P0.6.38 integration harness and related executable evidence as complete.
- **Explicit TODO:** `tests/test_golden_replay.py` remains a pre-existing golden replay fixture collection issue to resolve before the P0.6.39 readiness vote or in a separate stabilization patch; P0.6.38 does not mix that cleanup into the integration harness scope.
- **Still locked:** production `ENABLED`, `as2_runtime_wiring.py` default path changes, `as2_projection_handoff.py` `_dedup_index` changes, real storage backend, real provider adapters, network/file/DB/queue I/O, concurrent provider execution, operator RPC, automatic stale retry, backend vendor selection, schema migration, production rollout, GateController state mutations, and a new audit relay layer.

# Alpha3g P0.6.37 — ProductionProviderAggregator Skeleton

- **Patch:** P0.6.37 — ProductionProviderAggregator Skeleton under `ENABLED_FOR_TEST`.
- **Scope:** production-facing provider aggregation skeleton + canonical provider-name enum + tests + boundary guards + readiness documentation updates. No concrete provider adapters, real I/O, runtime wiring change, projection handoff change, idempotency store coupling, audit relay, concurrent execution, or production `ENABLED` activation.
- **ProviderName enum:** added `AS2ProviderName` to `synapse/runtime/as2_provider_ports.py` with canonical provider names for deterministic aggregation and future concurrent tie-breaking.
- **New module:** added `synapse/runtime/as2_provider_aggregator.py` with `AS2ProviderAggregatorSkeleton`, `AggregatorSuccess`, `AggregatorFailure`, `AggregationResult`, `BRIDGE_PAYLOAD_KEYS`, `PROVIDER_ORDER`, `FAILURE_PRIORITY`, `select_representative_failure`, `classify_failure_scope`, and provider-fragment normalization helpers.
- **Sequential fail-fast:** `aggregate(...)` calls provider ports in fixed order — Identity, ModelSelection, Definition, StaticModelRegistry, MemoryReference, CapabilityGrant — and stops on the first provider or normalization failure.
- **Failure Priority Matrix:** implemented as a pure deterministic selector for future concurrent execution. Runtime `aggregate(...)` does not collect failures from remaining providers in P0.6.37.
- **Tie-breaker:** equal-priority failures resolve by lexicographic provider name value, with new P0.6.37 tests proving input-order independence.
- **Normalization:** provider-native outputs are validated for required fields and stripped to bridge-valid fragments. The P0.6.30 regression is covered: `{"model": "mock-agent-model", "selection_source": "p0628_fake"}` becomes `{"model": "mock-agent-model"}`.
- **Agent-scoped INVALID_INPUT:** `classify_failure_scope(...)` returns `"agent"` only when `reason_code == INVALID_INPUT`, `agent_id` is present, and `agent_scoped is True`; otherwise failures remain `"systemic"`. GateController mutation remains locked.
- **Dependency injection rule:** aggregator provider mapping uses explicit `is not None` defaulting and avoids truthiness-based DI fallbacks.
- **Tests:** added `tests/test_as2_provider_aggregator_skeleton_p0637.py` covering all-six-provider success, non-bridge field stripping, missing required field schema failure, fail-fast stop behavior, Failure Priority Matrix, enum tie-breaker, input-order independence, agent/systemic scope classification, enum string compatibility, DI defaulting, and no direct storage/projection/I/O coupling terms.
- **Test import hygiene:** added `tests/__init__.py` as a project-local package marker so `tests.support` imports resolve deterministically in environments that also install an unrelated third-party `tests` package.
- **Boundary guards:** updated `tests/test_as2_architectural_fitness.py` to forbid legacy runtime imports, real I/O drivers, projection calls, projected artifact imports, idempotency-store imports, audit storage imports, runtime wiring imports, and direct wall-clock terms in `as2_provider_aggregator.py`.
- **Readiness:** updated `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` to mark ProviderAggregator skeleton, provider output normalization, Failure Priority Matrix, ProviderName enum tie-breaker, sequential-first behavior, INVALID_INPUT scope mapping, and boundary guards as complete.
- **Still locked:** real provider adapters, network/file/DB/queue I/O, `as2_runtime_wiring.py` changes, `as2_projection_handoff.py` changes, idempotency store integration, audit relay, concurrent execution implementation, automatic retries/backoff, provider-side caching, GateController mutations, and production `ENABLED`.

# Alpha3g P0.6.36 — PersistentIdempotencyStore Skeleton

- **Patch:** P0.6.36 — PersistentIdempotencyStore Skeleton under `ENABLED_FOR_TEST`.
- **Scope:** production-facing in-memory idempotency store skeleton + tests + boundary guards + readiness documentation updates. No real storage, database schema, projection handoff replacement, runtime wiring change, provider aggregator implementation, operator RPC, automatic stale retry, or production `ENABLED` activation.
- **New module:** added `synapse/runtime/as2_idempotency_store.py` with `IdempotencyKey`, `IdempotencyRecord`, `IdempotencyRecordState`, `IdempotencyFailureReason`, reservation/transition result types, and `InMemoryIdempotencyStore`.
- **Conditional updates:** implemented CAS-like operations `reserve_if_absent`, `complete_if_state`, `fail_if_state`, `cancel_if_state`, and `mark_stale_if_expired` under a local `RLock`.
- **Rollback-safe atomic linkage:** state-changing transitions emit the associated `AS2AuditEvent` before committing the in-memory state; if outbox append fails, the idempotency record remains unchanged.
- **Poison Pill:** same `correlation_id` with a different `prepared_inputs_hash` records `FAILED(reason_code=POISON_PILL)` and terminally blocks later state-changing operations for that correlation except inspection.
- **STALE handling:** `IN_PROGRESS` records can be marked `STALE_IN_PROGRESS` via injected-clock TTL evaluation; late completion is rejected by conditional state checks and automatic retry remains locked.
- **Fail-closed store availability:** `available=False` simulates store outage and raises `IdempotencyStoreUnavailable` before any state change or audit append.
- **Determinism guard:** added regression coverage proving `stable_canonical_hash(PreparedAS2Inputs.to_validate_kwargs())` is stable for semantically identical inputs with different mapping key order.
- **Tests:** added `tests/test_as2_idempotency_store_skeleton_p0636.py` covering reservation, duplicates, Poison Pill, terminal failure, completion, cancellation, stale TTL handling, store outage, rollback-safe audit linkage, concurrent reservation, and canonical prepared-input hashing.
- **Boundary guards:** updated AS2 architectural fitness tests to forbid real I/O, legacy runtime imports, projection calls, AgentSnapshot construction/imports, and provider-port imports in `as2_idempotency_store.py`, and to guard against direct `time.time()` calls in the module.
- **Readiness:** updated `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` to mark PersistentIdempotencyStore skeleton, boundary evidence, conditional updates, rollback-safe audit linkage, Poison Pill handling, STALE handling, and `to_validate_kwargs()` determinism as complete.
- **Still locked:** real storage backend, Redis/PostgreSQL/SQLite/CAS implementation, database schema, `as2_projection_handoff.py` dedup replacement, `as2_runtime_wiring.py` changes, provider aggregator implementation, production `ENABLED`, operator RPC, automatic retry for `STALE_IN_PROGRESS`, direct projection calls, and provider calls from the idempotency store.

# Alpha3g P0.6.35 — Audit Persistence / OutboxAuditSink Skeleton

- **Patch:** P0.6.35 — Audit Persistence / OutboxAuditSink Skeleton.
- **Scope:** production-facing in-memory audit outbox skeleton + tests + boundary guards + documentation updates. No real storage, relay, runtime wiring, idempotency implementation, provider aggregator implementation, or production `ENABLED` activation.
- **New module:** added `synapse/runtime/as2_audit_outbox.py` with `CHAIN_START`, `OutboxAuditEnvelope`, `OutboxAppendResult`, `AuditChainValidationResult`, and `InMemoryOutboxAuditSink`.
- **Envelope/Payload split:** `AS2AuditEvent` remains the deterministic payload and owns `record_hash()`; `OutboxAuditEnvelope` carries metadata such as `event_id`, `sequence_number`, `wall_clock_timestamp`, `partition_key`, `relay_attempt_count`, `ingestion_node_id`, and `schema_version` without affecting payload hashes.
- **Event IDs:** `InMemoryOutboxAuditSink` generates `event_id` at append time, deterministic for a given `payload.record_hash()` + `sequence_number` pair; full cross-retry relay idempotency remains deferred until a production logical event key is designed.
- **Hash-chain semantics:** explicit `CHAIN_START` first-record sentinel is enforced; `None` is treated as `MISSING_PREVIOUS`, and arbitrary mismatched hashes are treated as `BROKEN_PREVIOUS_HASH`.
- **Backpressure skeleton:** optional bounded queue support uses tiered behavior: critical events fail closed when full, while diagnostic events are best-effort dropped. Unknown event types default to critical.
- **Tests:** added `tests/test_as2_audit_outbox_skeleton_p0635.py` covering append order, payload hash preservation, envelope metadata isolation, CHAIN_START semantics, previous-hash links, NoOp default preservation, deterministic event ids, schema version propagation, None-vs-CHAIN_START distinction, bounded queue critical/diagnostic behavior, and monotonic sequence numbers.
- **Boundary guards:** updated AS2 architectural fitness tests to forbid real I/O, legacy runtime imports, projection calls, AgentSnapshot construction/imports, and provider-port imports in `as2_audit_outbox.py`.
- **Readiness:** updated `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` to mark OutboxAuditSink skeleton and boundary evidence as complete.
- **Still locked:** real storage backends, database schema, AuditRelay, network/file/DB/CAS/queue I/O, `as2_runtime_wiring.py` changes, idempotency store implementation, provider aggregator implementation, production `ENABLED`, changes to `NoOpAuditSink` behavior, AS2GateController default sink behavior, and AS2ProjectionHandoff audit emission logic.

# Alpha3g P0.6.34 — Implementation Skeletons Planning / Gap Analysis

- **Patch:** P0.6.34 — Implementation Skeletons Planning / Gap Analysis.
- **Scope:** Doc-only planning for implementation skeleton materialization; no production code, tests, runtime behavior, storage, adapters, or backend selection changes.
- **New planning document:** added `docs/AS2-IMPLEMENTATION-SKELETONS-PLANNING-P0634.md` to define the dependency graph, implementation order, backend capability requirements, skeleton acceptance gates, boundary guard matrix, observability requirements, testing strategy, rollback model, and locked list.
- **Readiness checklist:** added `docs/AS2-SKELETONS-READINESS-CHECKLIST.md` to separate designed RFC evidence from implemented skeleton evidence and to make production `ENABLED` readiness dependent on executable proof.
- **Future backlog:** added `docs/AS2-FUTURE-RFC-BACKLOG.md` to track deferred topics including Byzantine Poison Pill detection, stale/completed races, provider typed contracts, ProviderName enum materialization, schema upcasting, observability, operator runbooks, backend selection ADR, and concrete provider adapter RFC.
- **Implementation sequence:** fixed the next planned sequence as P0.6.35 OutboxAuditSink Skeleton, P0.6.36 PersistentIdempotencyStore Skeleton, P0.6.37 ProviderAggregator Skeleton, P0.6.38 Integration Harness under `ENABLED_FOR_TEST`, and P0.6.39 Production `ENABLED` Readiness Vote.
- **Canonical input pre-condition:** recorded that `PreparedAS2Inputs.to_validate_kwargs()` must own deterministic canonical output formation before persistent idempotency is accepted; storage layers must not normalize prepared inputs.
- **ProviderName requirement:** recorded that future provider aggregator tie-breaking must use canonical provider-name enum values, not raw strings.
- **Boundary planning:** defined future guard expectations for OutboxAuditSink, PersistentIdempotencyStore, and ProviderAggregator skeletons before code is added.
- **Still locked:** `synapse/` and `tests/` code changes, backend vendor selection, production `ENABLED`, real I/O, concrete provider adapters, provider adapter namespace creation, audit storage implementation, idempotency store implementation, production aggregator implementation, operator RPC, degraded mode, CAS artifact storage, CVM/LLM expansion, and changes to existing AS2 core behavior.

# Alpha3g P0.6.33 — Production Provider Aggregator RFC

- **Patch:** P0.6.33 — Production Provider Aggregator RFC.
- **Scope:** Doc-only RFC for production provider aggregation and normalization; no production code, tests, adapters, real I/O, or runtime behavior changes.
- **New RFC:** added `docs/AS2-PRODUCTION-PROVIDER-AGGREGATOR-RFC-P0633.md`.
- **Aggregator role:** defined the Production Provider Aggregator as an Anti-Corruption Layer between Host Provider Ports and the AS2 bridge-valid Host Pre-Stage payload.
- **Normalization:** documented provider-native output normalization, required-field validation, non-bridge field stripping, and fail-closed handling for schema violations.
- **Shape drift:** recorded the P0.6.30 model-selection shape drift (`selection_source` extra metadata) as the motivating design debt for a production normalization layer.
- **Failure determinism:** defined a Failure Priority Matrix for concurrent aggregation and required lexicographic `provider_name` tie-breaking for equal-priority failures.
- **Execution model:** described both sequential fail-fast and future concurrent aggregation while requiring the selected failure to remain deterministic and replay-safe.
- **Agent-scoped refinement:** specified future `INVALID_INPUT` semantics: unknown scope remains systemic, while `agent_id` plus agent-scoped evidence may route to agent quarantine.
- **Idempotency linkage:** clarified that aggregator output links to persistent idempotency only through canonical `PreparedAS2Inputs` and `prepared_inputs_hash = stable_canonical_hash(prepared_inputs.to_validate_kwargs())`; the idempotency store is not a projection-result cache.
- **Future topics:** recorded concrete provider adapters, typed provider payload contracts, Poison Pill tenant escalation, and stale/completed idempotency races as future design/implementation topics.
- **Still locked:** `synapse/` and `tests/` code changes, production provider aggregator implementation, concrete provider adapters, provider adapter namespace creation, real I/O, audit storage implementation, persistent idempotency implementation, runtime wiring changes, projection behavior changes, and production `ENABLED`.

# Alpha3g P0.6.32 — Persistent Idempotency Store RFC

- **Patch:** P0.6.32 — Persistent Idempotency Store RFC.
- **Scope:** Doc-only RFC for durable projection-handoff idempotency; no production code, tests, storage, or runtime behavior changes.
- **New RFC:** added `docs/AS2-PERSISTENT-IDEMPOTENCY-STORE-RFC-P0632.md`.
- **Scope boundary:** idempotency applies only to projection handoff, not provider calls, gate transitions, diagnostic events, or general runtime wiring.
- **Key model:** defined `idempotency_key = correlation_id + prepared_inputs_hash` and distinguished `correlation_id`, `prepared_inputs_hash`, `event_id`, `snapshot_hash`, and `derivation_record_hash`.
- **State model:** specified `IN_PROGRESS`, `COMPLETED`, `FAILED`, `CANCELLED`, and `STALE_IN_PROGRESS` as the recommended persistent idempotency lifecycle.
- **Poison Pill:** specified same `correlation_id` with different `prepared_inputs_hash` as a fail-closed Poison Pill; RFC recommends `FAILED(reason_code=POISON_PILL)` over a separate `FAILED_POISON_PILL` state.
- **Distributed correctness:** required future backends to support atomic conditional update semantics to prevent concurrent double-projection.
- **Transactional Outbox linkage:** required idempotency state transitions and audit outbox events to be committed atomically for state-changing transitions.
- **Restart recovery:** specified `STALE_IN_PROGRESS` plus operator review for expired `IN_PROGRESS` records; automatic retry remains forbidden.
- **Failure policy:** idempotency store unavailable means fail-closed for projection.
- **Schema/replay:** added schema versioning, schema upcasting, TTL/retention concepts, and replay/forensic reconciliation requirements at RFC level.
- **Still locked:** real idempotency storage, SQL/Redis/PostgreSQL/SQLite/CAS implementation, `synapse/` and `tests/` code changes, `as2_projection_handoff.py` behavior changes, `as2_runtime_wiring.py` changes, production provider aggregator, concrete adapters, audit storage implementation, and production `ENABLED`.

# Alpha3g P0.6.31 — Audit Persistence / Transactional Outbox RFC

- **Patch:** P0.6.31 — Audit Persistence / Transactional Outbox RFC.
- **Scope:** Doc-only RFC for durable AS2 audit persistence; no production code, storage, relay, or runtime behavior changes.
- **New RFC:** added `docs/AS2-AUDIT-PERSISTENCE-OUTBOX-RFC-P0631.md`.
- **Audit port:** confirmed `AS2AuditSink` as the production-facing audit port and described future `NoOpAuditSink`, `InMemoryAuditSink`, `OutboxAuditSink`, and relay roles.
- **Pipeline design:** separated `OutboxAuditSink`, `AuditRelay`, and external store responsibilities; backend selection remains deferred.
- **Transactional Outbox:** documented the preferred production model requiring state transition and audit event persistence to be committed atomically.
- **Event structure:** separated deterministic Event Payload from transport/storage Event Envelope; envelope metadata does not participate in `record_hash()`.
- **Hash chain:** required `previous_state_hash` for persisted chains and introduced explicit `CHAIN_START` first-record semantics.
- **Replay/forensics:** defined RFC-level replay/verification API contracts and verification failure categories, without implementation.
- **Schema evolution:** added persisted `AS2AuditEvent` schema versioning requirements.
- **Failure policy:** defined tiered audit failure semantics: fail-closed for critical state-changing events and best-effort for diagnostic events.
- **Future refinements:** recorded provider shape normalization, concurrent aggregation Failure Priority Matrix, and agent-scoped `INVALID_INPUT` refinement as P0.6.33 topics.
- **Still locked:** production code changes, `synapse/` implementation changes, `OutboxAuditSink`, `AuditRelay`, real storage, database/file/CAS writes, queue/broker design, persistent idempotency, production provider aggregator, runtime behavior changes, and production `ENABLED`.

# Alpha3g P0.6.30 — Stage 3 Provider Fakes + Integration Hardening

- **Patch:** P0.6.30 — Stage 3 Provider Fakes + Integration Hardening.
- **Scope:** Stage 3 test fakes and integration hardening for MemoryReferenceProviderPort and CapabilityGrantProviderPort; no production runtime wiring changes and no real I/O.
- **Provider fakes:** added full in-memory `FakeMemoryReferenceProvider` and `FakeCapabilityGrantProvider` with success, empty-success, missing-context, invalid-input, not-found, and cancelled paths.
- **Empty data semantics:** empty `memory_ref_source.refs` and empty `capability_grant_source.grants` are valid `ProviderSuccess` outcomes, not `NOT_FOUND`.
- **Control Plane taxonomy:** added explicit `INVALID_INPUT` and `CANCELLED` provider failure reason handling; `INVALID_INPUT` routes to systemic disable while `CANCELLED` is observed without gate transition.
- **Safe routing:** updated test-support ProviderReasonCode → AS2ProviderFailureReasonCode routing so `INVALID_INPUT` and `CANCELLED` use explicit Stage 3 mappings instead of fallback behavior.
- **Aggregator:** expanded test-only `HostPreStageProviderHarness` to all six provider ports and documented sequential fail-fast aggregation plus future production concurrency considerations.
- **Integration hardening:** added test-only end-to-end coverage for all-six-provider payload assembly and handoff into `process_host_prestage(...)` as a ready Mapping.
- **Boundary guards:** existing provider support guards continue to forbid legacy runtime imports, real I/O, projection calls, and projected artifact construction in provider fakes/routing/harness support.
- **Still locked:** `as2_runtime_wiring.py` production changes, provider dependencies in runtime wiring, real I/O, concrete provider adapters, production aggregator, production `ENABLED`, audit storage, CAS/storage, persistent idempotency, AgentRuntime/Environment imports, and LLM/CVM wiring.

# Alpha3g P0.6.29 — Stage 2 Provider Fakes + Integration Tests

- **Patch:** P0.6.29 — Stage 2 Provider Fakes + Integration Tests.
- **Scope:** Stage 2 test fakes and harness support for HostDefinitionProviderPort and StaticModelRegistryProviderPort; no real I/O and no runtime wiring expansion.
- **Provider fakes:** added full in-memory `FakeHostDefinitionProvider` and `FakeStaticModelRegistryProvider` with success, missing-context, schema-mismatch, and not-found paths.
- **Safe routing:** added test-support safe ProviderReasonCode → AS2ProviderFailureReasonCode translation; raw Enum(value) casting is not used on provider failures.
- **Control Plane taxonomy:** staged `NOT_FOUND` support routes Stage 2 definition/registry gaps to systemic disable; existing UNAVAILABLE/BACKPRESSURE/SCHEMA mappings remain covered.
- **Type narrowing:** added `is_provider_success()` and `is_provider_failure()` TypeGuard helpers for ProviderOutcome consumers.
- **Aggregator:** added test-only `HostPreStageProviderHarness` that assembles Stage 1 + Stage 2 Host Pre-Stage payload fragments and returns the first ProviderFailure as a value.
- **Boundary guards:** added light hermetic guards for provider fakes/routing/aggregator support to forbid legacy runtime, real I/O, projection calls, and AgentSnapshot construction.
- **Still locked:** `as2_runtime_wiring.py` expansion, real I/O, concrete provider adapters in `synapse/`, production ENABLED, audit storage, CAS/storage, persistent idempotency, AgentRuntime/Environment imports, and LLM/CVM wiring.

# Changelog

## P0.6.44-dev compose static contract alignment

- **Verification-only compose alignment:** corrected the local PgBouncer rehearsal environment keys and container port declaration in `docker-compose.as2-postgres-mini-poc.yml` so the committed compose file matches the existing static verification contract while preserving `as2.production: "false"`.

## P0.6.28 — Host Provider Ports Harness + Skeleton Interfaces

- Added `synapse/runtime/as2_provider_ports.py` to materialize all six Host Provider Port interfaces from the P0.6.21 RFC: identity, definition, static model registry, memory reference, capability grant, and model selection.
- Added `HostProviderRequestContext`, `ProviderReasonCode`, `ProviderSuccess[T]`, `ProviderFailure`, and `ProviderOutcome[T]` as the production-facing provider contract surface.
- Added Stage 1 in-memory fakes in `tests/support/as2_provider_fakes.py` for `HostIdentityProviderPort` and `ModelSelectionProviderPort`; Stage 2/3 concrete behavior remains staged for later patches.
- Added P0.6.28 provider harness tests covering Stage 1 success paths, missing request context as a typed `ProviderFailure`, provider failure routing through `AS2GateControllerSkeleton`, timeout threshold handling, operator review mapping, protocol runtime checks, and separation of ProviderFailure from ProjectionFailure paths.
- Extended architectural fitness tests so provider port contracts remain free of legacy runtime imports, projected artifact imports, projection calls, and real I/O driver imports.
- Scope remains controlled: real I/O, concrete provider adapters, CAS/storage, audit storage, persistent idempotency, production `ENABLED`, AgentRuntime/Environment imports, projection calls from provider modules, AgentSnapshot construction, degraded mode, and LLM/CVM wiring remain locked.

## P0.6.27 — Runtime Wiring Expansion under gate

- Expanded `synapse/runtime/as2_runtime_wiring.py` with an explicit opt-in projection handoff path guarded by `projection_handoff_enabled=False` by default. Existing P0.6.18-P0.6.26 `WiringSuccess` behavior is preserved unless the caller explicitly enables handoff and injects a handoff dependency.
- Added `WiringProjectionCompleted` and `WiringProjectionSkipped` typed outcomes. `COMPLETED` handoff results map to projection-completed success metadata; `DENIED` and `DUPLICATE` map to skipped outcomes and do not trigger systemic disable or agent quarantine.
- Runtime wiring delegates only to `AS2ProjectionHandoffSkeleton.execute_projection(...)`; it does not import or call `project_validated_as2_inputs(...)`, does not import `AgentSnapshot`, and does not construct or retain projected artifacts.
- Hardened `AS2ProjectionHandoffSkeleton` dedup behavior before wiring expansion: added `_dedup_lock`, two-phase `in_progress` dedup entries, rollback on approval/projection failure, and completed dedup writes only after successful projection return.
- Clarified the `DUPLICATE` handoff contract as hash-only: `snapshot=None` and `derivation_record=None` by design; callers receive `snapshot_hash` / `derivation_record_hash` for future CAS lookup after CAS is approved.
- Added P0.6.27 regression tests for disabled-by-default wiring expansion, delegation to handoff, denied/duplicate mapping, dedup write-after-success, concurrent dedup safety, and duplicate hash-only behavior.
- Extended architectural fitness tests so projection calls remain allowed only from `synapse/runtime/as2_projection_handoff.py`; `as2_runtime_wiring.py` may participate in handoff delegation but remains forbidden from importing projection functions or projected artifact symbols.
- Scope remains controlled: direct projection from runtime wiring, production Host providers, persistence/CAS/storage I/O, audit storage, operator RPC, production `ENABLED`, degraded mode, AgentRuntime/Environment imports, persistent idempotency storage, compensation mechanisms, and LLM/CVM wiring remain locked.

## P0.6.26 — Runtime Projection Handoff Skeleton under ENABLED_FOR_TEST

- Added `synapse/runtime/as2_projection_handoff.py` as the only approved production-namespace caller of `project_validated_as2_inputs(...)`.
- Added `AS2ProjectionHandoffSkeleton` with dependency-injected `AS2GateController` and `AS2AuditSink`; the handoff requests fresh Control Plane approval immediately before projection and remains limited to `ENABLED_FOR_TEST`.
- Added typed projection handoff results, projection failure reason codes, and `AS2ProjectionDedupPolicy` with in-memory hash-only dedup behavior; persistent idempotency storage remains locked.
- Added strict projection audit ordering: `projection_requested` -> `projection_approved` / `projection_denied` -> `projection_started` -> `projection_completed` / `projection_failed`.
- Projection audit events are emitted through the existing `AS2AuditSink` and include `snapshot_hash` / `derivation_record_hash` on completion; no audit storage or CAS/storage I/O was added.
- Added projection failure mapping: internal/core failures become systemic failures, interrupted/cancelled failures use a distinct interrupted systemic reason code, and agent-scoped failures become agent quarantine outcomes.
- Added P0.6.26 tests for real projection handoff success, approval denial, event ordering, internal/interrupted/agent-scoped projection failure mapping, in-memory dedup, Poison Pill correlation reuse, no artifact retention, and concurrent provider threshold regression.
- Extended architectural fitness tests so `project_validated_as2_inputs(...)` is allowed only from `synapse/runtime/as2_projection_handoff.py`; bridge, runtime wiring, and gate controller remain projection-free.
- Scope remains controlled: bridge/skeleton projection calls, runtime wiring expansion, production Host providers, persistence/CAS/storage I/O, audit storage, operator RPC, production `ENABLED`, degraded mode, AgentRuntime/Environment imports, and Integrate/Dream/CVM wiring remain locked.

## P0.6.25 — Production AS2GateController Skeleton

- Added `synapse/runtime/as2_gate_controller.py` as a production-facing AS2 Control Plane skeleton derived from the P0.6.17/P0.6.22 RFCs and validated against P0.6.24 harness expectations.
- Added `synapse/runtime/as2_audit_sink.py` with `AS2AuditSink`, `AS2AuditEvent`, and `NoOpAuditSink`; the sink interface is injectable and the default implementation performs no I/O or persistence.
- Added typed gate-controller contracts for decisions, transition requests/results, provider-failure reason mapping, and wiring-outcome handling.
- Implemented skeleton-only in-memory/no-I/O behavior for `WiringSystemicDisableRequest`, `WiringAgentQuarantineRequest`, `WiringBridgeDisabled`, provider timeout threshold handling, missing request context, schema mismatch, unauthorized/forbidden, and backpressure observations.
- Added deterministic audit-event hashing with explicit timestamp guardrails: timestamps are caller-supplied only and excluded from `record_hash()` until production audit semantics are approved.
- Added P0.6.25 tests for controller decisions, injectable/no-op audit sink behavior, deterministic hash regression, provider failure mapping, audit chain linkage, and function-scoped fixture guardrails.
- Extended AS2 architectural fitness tests to include the new gate-controller and audit-sink modules, enforce import direction, and keep projection, projected artifacts, legacy runtime layers, storage/I/O drivers, and test-support imports out of production AS2 control-plane modules.
- Scope remains controlled: projection, `AgentSnapshot` construction, Runtime Projection Handoff, Runtime Wiring Expansion, production Host providers, persistence/CAS/storage I/O, audit storage, operator RPC, degraded mode, production `ENABLED`, and Integrate/Dream/CVM wiring remain locked.

## P0.6.24 — AS2GateController Harness + Projection Integration Tests

- Added strict test-only executable harness for the P0.6.21 Provider Ports, P0.6.22 Control Plane, and P0.6.23 Projection Handoff RFC stack.
- Added `tests/support/as2_control_plane_fake.py` as a working in-memory AS2GateController fake with configurable provider failure thresholds, restart simulation, and append-only audit records linked by `previous_state_hash`.
- Added `tests/support/as2_projection_test_orchestrator.py` as a test-only projection handoff orchestrator. It is the only new path that calls `project_validated_as2_inputs(...)`, and it lives under `tests/`.
- Added P0.6.24 harness tests for: Control Plane provider failure mapping, threshold escalation, WiringBridgeDisabled config events, projection authorization, race-condition denial, strict idempotency, Poison Pill correlation reuse, projection failure classification, audit linkage, AdapterDerivationRecord audit references, and no bridge/skeleton artifact retention.
- Added `tests/fixtures/as2_thresholds.py` with `DEFAULT_PROVIDER_FAILURE_THRESHOLD = 2` as a test harness baseline only, not production policy.
- Strengthened AS2 architectural fitness tests to keep bridge and runtime skeleton free of projection calls and projected artifact symbol imports while allowing projection only from test-only harness paths.
- Scope remains test-only: `synapse/` unchanged; no production AS2GateController, no production Host providers, no runtime expansion, no state persistence, no CAS/storage I/O, no audit storage, no degraded mode, and no production `ENABLED` state.


## Alpha3g P0.6.23 — AS2 Projection Handoff Design RFC

- **Status:** COMPLETED — added a doc-only RFC for AS2 Projection Handoff design.
- **Scope:** added `docs/AS2-PROJECTION-HANDOFF-RFC-P0623.md`; no `synapse/` runtime, projection, persistence, provider, or Control Plane implementation changes.
- **Handoff boundary:** RFC defines that projection is called only by an approved Host/Pipeline Projection Handoff Layer after `WiringSuccess` and fresh Control Plane authorization; bridge and runtime skeleton remain preparation/validation layers.
- **Authorization:** RFC states that `WiringSuccess` is necessary but not sufficient for projection and requires a fresh gate/Control Plane check immediately before `project_validated_as2_inputs(...)`.
- **Audit lifecycle:** RFC defines projection audit events, including requested, approved, denied, started, completed, and failed, with `snapshot_hash`, `AdapterDerivationRecord` reference fields, and production audit-chain linkage requirements.
- **Artifact ownership:** RFC defines `AgentSnapshot` and `AdapterDerivationRecord` ownership after successful projection and forbids bridge/skeleton reference retention.
- **Replay/failure semantics:** RFC defines idempotency/replay-safety requirements, a strict-idempotency-vs-compensation decision point, and `Systemic Core Failure` classification for projection internal failures.
- **Still locked:** projection implementation, `project_validated_as2_inputs(...)` runtime calls, `AgentSnapshot` construction in runtime wiring, idempotency/compensation implementation, production Host providers, AS2GateController implementation, state/audit persistence, CAS/storage I/O, degraded mode, production `ENABLED`, runtime wiring expansion, and Integrate/Dream/CVM wiring.

## Alpha3g P0.6.22 — AS2GateController RFC / Control Plane Design

- **Status:** COMPLETED — added a doc-only RFC for AS2GateController / Control Plane design.
- **Scope:** added `docs/AS2-GATE-CONTROLLER-RFC-P0622.md`; no `synapse/` runtime, persistence, provider, or projection implementation changes.
- **Verified facts:** RFC records that `AS2GateController` is RFC-level only, `ModelSelectionConflictError` is not executable AS2 input, removed selector aliases remain only guard targets, and `WiringBridgeDisabled` is a configuration/operator event.
- **Control Plane contract:** defined transition authority, wiring/provider outcome mapping, retry ownership, quarantine ownership, sticky `DISABLED_SYSTEMIC`, operator reset workflow, audit record schema, and explicit non-inputs.
- **Decisions required:** RFC requires explicit future decisions for in-memory vs persisted gate state, restart behavior, audit persistence, future persistence hook, and repeated-failure thresholds.
- **Still locked:** production `AS2GateController`, state persistence, audit storage, operator RPC, production Host providers, Host Provider Ports Harness, projection, `AgentSnapshot` construction, runtime wiring expansion, CAS/storage I/O, degraded mode, production `ENABLED`, and Integrate/Dream/CVM wiring.

## Alpha3g P0.6.21 — AS2 Host Provider Ports RFC

- **Status:** COMPLETED — added a doc-only RFC for production-facing AS2 Host Provider Ports.
- **Scope:** added `docs/AS2-HOST-PROVIDER-PORTS-RFC-P0621.md`; no `synapse/` runtime or production provider implementation changes.
- **Ports defined:** `HostIdentityProviderPort`, `HostDefinitionProviderPort`, `StaticModelRegistryProviderPort`, `MemoryReferenceProviderPort`, `CapabilityGrantProviderPort`, and `ModelSelectionProviderPort`.
- **Request context:** RFC requires request/correlation continuity through `HostProviderRequestContext` while deferring the concrete carrier/signature mechanism.
- **Provider outcomes:** RFC defines Result-style `ProviderSuccess` / `ProviderFailure` with `correlation_id` and `latency_ms`, including `MISSING_REQUEST_CONTEXT` as typed failure rather than exception-driven normal flow.
- **I/O boundary:** RFC defines timeout/deadline, cancellation, backpressure, and no-implicit-retry requirements while deferring sync/async implementation posture.
- **Bridge disabled semantics:** `WiringBridgeDisabled` is classified as a configuration/operator boundary event: no retry, no quarantine, no systemic provider outage classification.
- **Still locked:** production Host providers, production `AS2GateController`, projection, `AgentSnapshot` construction, runtime wiring expansion, gate-state persistence, CAS/storage I/O, degraded mode, production `ENABLED`, and Integrate/Dream/CVM wiring.

## Alpha3g P0.6.20 — AS2 Naming Debt Contract / Legacy Alias Removal

- **Status:** COMPLETED — executed the Contract phase for AS2 model-selection naming.
- **Migration evidence:** P0.6.19 static audit confirmed removed selector aliases were outside primary code paths before Contract.
- **Validation API:** `validate_as2_inputs(...)` now accepts only the canonical `model_selection_source` selector input.
- **Adapter cleanup:** removed expand-phase compatibility fallback, deprecation-warning path, and canonical-vs-legacy conflict handling from model-selection resolution.
- **Bridge cleanup:** Host Pre-Stage payload parsing now accepts only `model_selection_source`; compatibility selector alias handling was removed.
- **Permanent guard:** converted migration audit into `tests/test_as2_legacy_reintroduction_guard.py`, preventing removed selector aliases from reappearing in code or primary tests.
- **Still locked:** projection, `AgentSnapshot` construction, production Host providers, production `AS2GateController`, persisted gate mutation, CAS/storage I/O, degraded mode, production `ENABLED`, and runtime wiring expansion.

## Alpha3g P0.6.19 — Runtime Wiring Hardening

- Hardened `synapse/runtime/as2_runtime_wiring.py` with strict `AS2WiringReasonCode` diagnostics.
- Added dedicated `WiringBridgeDisabled` outcome for the `AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False` safety layer.
- Added P0.6.19 hardening tests for gate/bridge matrix behavior, correlation-id propagation/fallback, negative payload classification, and systemic failure mapping.
- Added static migration-audit coverage proving `legacy_agent_runtime_to_dict` is confined to approved expand-phase owner/compatibility scopes.
- Kept projection, `AgentSnapshot` construction, production Host providers, production `AS2GateController`, persisted gate mutation, degraded mode, and Contract phase locked.


## Alpha3g P0.6.18 — Runtime Wiring Skeleton under ENABLED_FOR_TEST Gate

- **Status:** COMPLETED — added the first runtime-owned AS2 wiring skeleton under the explicit `ENABLED_FOR_TEST` gate.
- **Scope:** added `synapse/runtime/as2_runtime_wiring.py`, `tests/test_as2_runtime_wiring_p0618.py`, and `docs/AS2-RUNTIME-WIRING-SKELETON-P0618.md`; updated AS2 architectural fitness tests and this changelog.
- **Gate evaluator:** added skeleton-level `AS2WiringGateEvaluator` and five-state `AS2WiringGateState` without a production `ENABLED` state.
- **Outcome model:** added immutable typed wiring outcomes carrying `correlation_id`: `WiringSuccess`, `WiringGateClosed`, `WiringAgentQuarantineRequest`, and `WiringSystemicDisableRequest`.
- **Skeleton pipeline:** added `process_host_prestage(...)`: gate check → `prepare_as2_inputs_from_host_prestage(...)` → `validate_as2_inputs(...)` → typed outcome.
- **Boundary hardening:** extended AS2 architectural fitness tests to include the runtime skeleton module.
- **Still locked:** production `AS2GateController`, production activation, projection, `AgentSnapshot` construction, production Host providers, gate-state persistence, CAS/storage I/O, degraded mode, Contract phase, and legacy runtime imports.

## Alpha3g P0.6.17 — AS2 Runtime Feature Gate RFC + Boundary Hardening

- **Status:** COMPLETED — added the AS2 runtime feature gate RFC and shared AS2 architectural fitness tests.
- **Scope:** added `docs/AS2-FEATURE-GATE-RFC-P0617.md`, `tests/support/as2_boundary_guards.py`, and `tests/test_as2_architectural_fitness.py`; updated this changelog.
- **Gate design:** defined a five-state AS2 runtime gate state machine with explicit transition rules, sticky `DISABLED_SYSTEMIC`, operator-reset-only recovery, and an RFC-level `AS2GateController` control-plane contract.
- **Boundary hardening:** added standard-library AST checks for forbidden legacy runtime imports and forbidden bridge calls, including both direct `ast.Name` and attribute `ast.Attribute` call forms.
- **Locked:** no production gate implementation, no runtime wiring, no production Host providers, no Contract phase, no degraded mode, no `AgentRuntime` / `Environment` changes, no CAS/storage I/O, no projection call from bridge, and no `AgentSnapshot` construction inside bridge.

## Alpha3g P0.6.16 — AS2 Naming Debt Cleanup / Expand-Contract Refactor

- **Status:** COMPLETED — performed the expand phase for AS2 model-selection naming cleanup before runtime wiring.
- **Scope:** updated `synapse/agent_snapshot_adapter.py`, `synapse/agent_snapshot_bridge.py`, AS2 validation/bridge fixtures and tests, and added `docs/AS2-NAMING-DEBT-CLEANUP-P0616.md`.
- **Canonical input:** `validate_as2_inputs(...)` now accepts `model_selection_source` as the canonical model-selection input.
- **Compatibility alias:** `legacy_agent_runtime_to_dict` remains accepted only as a deprecated expand-phase alias and emits `DeprecationWarning` when used without the canonical input.
- **Fail-closed conflict handling:** centralized `_resolve_model_selection(...)` rejects conflicting canonical and legacy selectors with `ModelSelectionConflictError`; equal values preserve compatibility while canonical wins.
- **Bridge cleanup:** `PreparedAS2Inputs.to_validate_kwargs()` now emits `model_selection_source` directly and no longer emits the legacy selector or `_MODEL_SELECTOR_DEBT_NOTE` shim.
- **Fixture/test migration:** primary AS2 and bridge fixtures now use `model_selection_source`; legacy alias behavior is isolated to compat tests, including warning and conflict coverage.
- **Still locked:** Contract-phase alias removal, runtime wiring, runtime feature flags, production Host ports/providers, CAS/storage I/O, degraded mode, bridge-side projection, `AgentSnapshot` construction inside bridge, and `AgentRuntime`/`Environment` changes.

## Alpha3g P0.6.15 — Runtime Wiring Harness / Host Provider Mocks

- **Status:** COMPLETED — added a test-only executable contract for the P0.6.14 AS2 runtime wiring design.
- **Scope:** new `tests/support/as2_runtime_wiring_harness.py`, `tests/fixtures/as2_runtime_wiring/p0615_runtime_wiring_contract.json`, `tests/test_as2_runtime_wiring_harness_p0615.py`, and `docs/AS2-RUNTIME-WIRING-HARNESS-P0615.md`; no `synapse/` production code changes.
- **Provider ports:** added test-scope `typing.Protocol` contracts and matching mock Host providers for identity, definition, static model registry, memory externalization, capability grant manifest, and model selection.
- **Payload builder:** added a test-only Host Pre-Stage payload builder that emits only production success keys and prefers `model_selection_source` over the deprecated `model_selector` alias.
- **Bridge path:** harness calls `prepare_as2_inputs_from_host_prestage(...)` on mocked Host payloads and stops at `PreparedAS2Inputs`; projection and `AgentSnapshot` construction remain forbidden.
- **Boundary coverage:** added happy-path, missing-input, payload-classification, forbidden-runtime-read, unknown-key, deterministic-preparation, forbidden-import, and no-projection guard tests.
- **Failure policy:** simulated the two P0.6.14-approved outcomes: per-agent quarantine for bad payloads and global wiring disable for systemic provider failures.
- **Still locked:** runtime wiring, runtime feature flag system, CAS/storage I/O, degraded mode, naming-debt cleanup, production Host provider APIs, `AgentRuntime`/`Environment` imports, Integrate/Dream/CVM wiring, and bridge-side projection.

## Alpha3g P0.6.14 — Runtime Wiring Design RFC

- **Status:** COMPLETED — added doc-only runtime wiring design for the hardened AS2 Host Pre-Stage bridge.
- **Scope:** new `docs/AS2-RUNTIME-WIRING-DESIGN.md` plus process-document updates; no code or test changes authorized.
- **Execution graph:** defined `Host providers -> Host Pre-Stage payload -> prepare_as2_inputs_from_host_prestage(payload) -> PreparedAS2Inputs -> Host/Pipeline projection -> AgentSnapshot + AdapterDerivationRecord`.
- **Projection boundary:** bridge remains a preparation/validation layer; `project_validated_as2_inputs(...)` and `AgentSnapshot(...)` remain forbidden inside bridge.
- **Responsibility map:** documented which Host provider must supply each AS2 input without using forbidden legacy runtime reads.
- **Payload classification:** separated production success keys, test/failure modelling keys, compatibility aliases, and diagnostic notes.
- **Forbidden reads:** runtime wiring design must avoid `AgentRuntime.to_dict()`, `AgentRuntime.name`, `AgentRuntime.model` as direct `model_ref`, `AgentRuntime.tools`, `Environment._json_safe()`, actor mailbox, scheduler/timers, and live handles.
- **Failure handling:** specified local per-agent quarantine for payload-specific failures and systemic AS2 wiring shutdown for shared provider/wiring failures; legacy canonical fallback remains forbidden.
- **Debt register:** recorded bridge docstring drift, `model_selector` alias, `legacy_agent_runtime_to_dict` naming debt, and production/test key separation as future work.
- **Still locked:** runtime wiring code, runtime feature flag system, CAS/storage I/O, Integrate/Dream/CVM wiring, bridge projection calls, AgentRuntime/Environment imports, and naming-debt cleanup in code.

## Alpha3g P0.6.13 — Host Pre-Stage Bridge Hardening

- **Status:** COMPLETED — hardened the P0.6.12 Host Pre-Stage bridge boundary without expanding bridge responsibility.
- **Scope:** `synapse/agent_snapshot_bridge.py`, new `tests/test_as2_bridge_hardening_p0613.py`, and documentation updates.
- **Strict payload contract:** Host Pre-Stage payloads now fail closed on unknown top-level fields through `HostPreStageUnexpectedFieldError`.
- **Nested field discipline:** approved nested AS2 input structures reject unexpected bridge-boundary fields, including identity-seed runtime leakage, inline memory fields, and live callable markers.
- **Missing/null/empty semantics:** missing or `null` required Host Pre-Stage sources raise the specific `HostPreStageMissing*Error`; present-but-empty or wrong-shaped sources raise `HostPreStageInvalidAS2InputsError`.
- **Mutation safety:** bridge DTOs defensively freeze nested structures and `PreparedAS2Inputs.to_validate_kwargs()` returns fresh mutable copies for standalone AS2 validation; external payload mutation cannot affect prepared outputs.
- **Feature discipline:** local bridge guard remains `AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False`; no runtime feature-flag system or production environment guard was introduced.
- **Still locked:** runtime wiring, `project_validated_as2_inputs(...)` calls inside bridge, AgentSnapshot construction inside bridge, AgentRuntime/Environment imports, runtime profile selector, `legacy_agent_runtime_to_dict` rename, bridge schema v2, caching, and performance optimization.
- **Tests:** added deterministic adversarial bridge-hardening tests while preserving the existing baseline skip.

## Alpha3g P0.6.12 — Flagged Host Pre-Stage Bridge Skeleton

- **Status:** COMPLETED — added first bridge-code skeleton under a local disabled-by-default flag.
- **Scope:** new isolated `synapse/agent_snapshot_bridge.py`, new `tests/test_as2_bridge_implementation_p0612.py`, and documentation updates.
- **Bridge entrypoint:** `prepare_as2_inputs_from_host_prestage(payload)` accepts Host Pre-Stage mappings, parses them into frozen DTOs, validates the prepared AS2 inputs with existing `validate_as2_inputs(...)`, and returns `PreparedAS2Inputs`.
- **Module boundary:** bridge code lives outside `agent_snapshot_adapter.py` and imports no `AgentRuntime`, `Environment`, interpreter, actor runtime, storage, provider registry, or runtime wiring modules.
- **Feature guard:** `AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False` is local to the bridge module; tests enable it explicitly. No runtime feature-flag system was introduced.
- **Naming debt isolation:** public bridge data uses `model_selection_source`; an internal shim maps it to the current `legacy_agent_runtime_to_dict.model` selector required by `validate_as2_inputs(...)` without authorizing `AgentRuntime.to_dict()` as canonical source.
- **Bridge errors:** added bridge-local typed error hierarchy rooted at `AS2BridgeError`; host-stage failures are separated from standalone `AS2AdapterError` failures.
- **Tests:** all 16 P0.6.11 bridge fixtures exercise the bridge skeleton; positive fixtures produce `PreparedAS2Inputs` that pass standalone AS2 validation, and negative fixtures raise the expected bridge error classes.
- **Still locked:** AgentRuntime/Environment imports, runtime wiring, `project_validated_as2_inputs(...)` calls inside bridge, AgentSnapshot construction inside bridge, runtime profile selector, Integrate/Dream/CVM paths, real provider registry, FunctionDescriptor runtime registry, golden fixture migration, and parameter rename of `validate_as2_inputs(...)`.

## Alpha3g P0.6.11 — AS2 Bridge Fixture Corpus / Host Pre-Stage Harness

- **Status:** COMPLETED — added test-only/data bridge fixture corpus and Host Pre-Stage harness.
- **Scope:** `tests/fixtures/as2_bridge/`, `tests/test_as2_bridge_harness_p0611.py`, and documentation updates. No `synapse/` changes.
- **Fixture corpus:** 16 bridge fixtures: 4 positive Host Pre-Stage outputs and 12 negative host/legacy-boundary failures.
- **Schema:** all bridge fixtures use `alpha3g.as2_bridge_fixture.v1` and deterministic sorted JSON form.
- **Code alignment:** positive fixtures include the current `legacy_agent_runtime_to_dict.model` selector required by `validate_as2_inputs(...)`; this is documented as naming debt and does **not** authorize `AgentRuntime.to_dict()` as canonical source.
- **Harness:** positive bridge fixtures validate `expected_as2_inputs` through existing standalone `validate_as2_inputs(...)`; the bridge harness does not call `project_validated_as2_inputs(...)`.
- **Coverage:** Forbidden Reads Registry coverage is 100%; Host Pre-Stage Protocol Step 0-10 coverage is 100%.
- **Negative cases:** bridge-specific failures use string identifiers only; no Python bridge exception classes were introduced.
- **Legacy isolation:** no AgentRuntime, Environment, runtime wiring, feature flag implementation, bridge code, storage/CAS I/O, real provider registry, FunctionDescriptor runtime registry, Integrate/Dream/CVM, or golden fixture migration.
- **Tests:** full suite passes with the existing baseline skip.

## Alpha3g P0.6.10 — AS2 Legacy Bridge Design RFC / Host Pre-Stage Protocol

- **Status:** COMPLETED — added doc-only AS2 legacy bridge design and Host Pre-Stage Protocol.
- **Scope:** `docs/AS2-LEGACY-BRIDGE-DESIGN.md` plus process documentation updates. No `synapse/` or `tests/` changes.
- **Design model:** Airlock Pattern — Host prepares explicit AS2 inputs; AS2 standalone validation/projection remains the only canonical path.
- **Future bridge entrypoint reserved:** `prepare_as2_inputs_from_host_prestage(...)`; `to_agent_snapshot()` remains forbidden.
- **Host responsibility:** capability verification, definition source preparation, registry selection, memory externalization, declarative capability grant preparation.
- **Bridge responsibility:** consume Host-prepared AS2 inputs, validate with `validate_as2_inputs(...)`, project with `project_validated_as2_inputs(...)`; no storage/CAS I/O.
- **Forbidden Reads Registry:** documented runtime sources that must not be used as canonical AS2 inputs (`AgentRuntime.to_dict()`, live tools, `Environment._json_safe()`, runtime handles, mailbox/timers, hidden interpreter state).
- **Future flag reserved:** `AS2_HOST_PRESTAGE_BRIDGE_ENABLED` for future implementation; not introduced in code.
- **Staging:** P0.6.11 bridge fixture corpus / Host Pre-Stage harness; P0.6.12 flagged bridge implementation, both requiring separate authorization.
- **Tests:** full suite passes with the existing baseline skip.


## Alpha3g P0.6.9 — AS2ViolationContext / Forensic Error Attribution

- **Status:** COMPLETED — AS2 fail-closed errors now carry structured forensic context through `AS2ViolationContext`.
- **Scope:** `synapse/agent_snapshot_adapter.py`, all negative AS2 fixtures, `tests/test_as2_violation_context_p069.py`, and process/RFC docs.
- **Context shape:** `rfc_reference`, `violated_field`, `fixture_case_id`, `expected_value`, and `actual_value`.
- **RFC references:** strict canonical format enforced (`RFC-...md §...`).
- **Fixture coverage:** every negative fixture now declares `expected_error_context`; tests assert subset context matching on the raised leaf exception.
- **Success/failure separation:** `AS2ViolationContext` is not mixed into `AdapterDerivationRecord` and does not affect `AgentSnapshot.snapshot_hash()`.
- **Legacy isolation:** no AgentRuntime, Environment, interpreter, actor runtime, Integrate/Dream/CVM, storage/CAS, profile selector, real provider registry, FunctionDescriptor runtime registry, or golden fixture migration.
- **Tests:** full suite passes with the existing baseline skip.

## Alpha3g P0.6.8 — AS2 AdapterDerivationRecord Hashing / Merkle-Transparent Audit

- **Status:** COMPLETED — AdapterDerivationRecord now carries real stable-canonical input hashes for the AS2 standalone projection path.
- **Scope:** `synapse/agent_snapshot_adapter.py`, `tests/test_as2_adapter_derivation_p068.py`, positive AS2 fixture update, and process/RFC docs.
- **Canonical hash path:** all derivation input hashes are computed through existing `synapse.canonical_service.stable_canonical_hash`; no adapter-specific hasher was introduced.
- **Five required input hashes:** identity context, static model registry, adapter definition source, memory ref source, and capability grant source.
- **State/audit separation:** `AdapterDerivationRecord` remains a forensic audit artifact and does not affect `AgentSnapshot.snapshot_hash()`.
- **R8-A preserved:** capability-grant projection semantics are unchanged; no core schema bump and no AgentSnapshot v2.
- **Legacy isolation:** no AgentRuntime, Environment, interpreter, actor runtime, Integrate/Dream/CVM, storage/CAS, profile selector, real provider registry, FunctionDescriptor runtime registry, or golden fixture migration.
- **Tests:** full suite passes with derivation-record hashing coverage and unchanged baseline skip.

## Alpha3g P0.6.7 — AS2 Fixture-Driven Minimal Standalone Projection

- **Status:** COMPLETED — first standalone AS2 projection from explicit validated inputs into the existing AgentSnapshot core.
- **Scope:** `synapse/agent_snapshot_adapter.py`, `tests/test_as2_adapter_projection_p067.py`, positive AS2 fixture update, and process docs.
- **Projection function:** `project_validated_as2_inputs(...)` implemented; `to_agent_snapshot()` remains absent/forbidden.
- **R9 closed:** added explicit `AdapterDefinitionSource` for `AgentDefinitionRef`, `config`, and `canonical_fields`, keeping `AdapterIdentityContext` identity-only.
- **R8 resolved for v1:** AS2 CapabilityGrant is canonically projected to core CapabilityGrant via deterministic `scope_hash` while preserving `tool_namespace`; no core schema bump and no AgentSnapshot v2.
- **Derivation record:** synthetic/form-level population only; real input hash computation remains deferred.
- **Legacy isolation:** no AgentRuntime, Environment, interpreter, actor runtime, Integrate/Dream/CVM, storage/CAS, profile selector, real provider registry, or golden fixture migration.
- **Tests:** full suite passes with projection coverage.

## Alpha3g P0.6.6 — AS2 Validation Hardening / Fixture-Driven Boundary Enforcement

- Vote A (validation hardening only) consolidated across four team reviewers; opened `feature/as2-validation-hardening-p066`.
- Hardened `validate_as2_inputs` and focused validators against seven edge-case gaps identified by adversarial probing of the P0.6.5 skeleton:
  - whitespace-only `alias` in identity seed now raises `AdapterIdentityContextIncompleteError` (identity drift surface);
  - negative or `bool` `identity_version` now raises `AdapterIdentityContextIncompleteError`;
  - duplicate `legacy_model` entries in `StaticModelRegistry` now raise `ModelRefUnknownError` (lookup-ambiguity defense);
  - duplicate `(memory_space_id, memory_key, access_mode)` refs now raise `AdapterMemorySpaceMismatchError`, aligning AS2 boundary with P0.5.9 standalone-core invariants;
  - conflicting `access_mode` on the same `(memory_space_id, memory_key)` address now raises `AdapterMemorySpaceMismatchError`;
  - duplicate `tool_namespace` in `CapabilityGrantSource` now raises `CapabilityGrantInvalidRefError`;
  - **any** legacy `__type__` envelope marker (not only `"agent"`) now raises `AdapterEnvelopeConflictError`, closing RFC §6.3 coverage against `durable_actor_ref`, `durable_promise`, `opaque`.
- Added `tests/test_as2_adapter_validation_p066.py` with 83 new tests: fixture-driven dispatch matrix for all 11 fixtures with exact-leaf-class assertions, exhaustive edge-case coverage for identity context / model registry / memory ref source / capability grant source, extended R7 envelope coverage, subagent presence semantics, ambient authority markers, inline memory boundary, and discipline anchors that block accidental introduction of `to_agent_snapshot`, projection names, `AgentSnapshot` construction, or feature flag machinery.
- AS2 RFC updated:
  - §17 reserves `project_validated_as2_inputs` as the future projection function name and forbids `to_agent_snapshot`, `build_snapshot_from_as2_inputs`, `build_snapshot_from_validated_inputs`, and other `to_`/`build_` variants in the AS2 module;
  - §18 records R8 — the shape gap between AS2 `CapabilityGrant` (function_descriptor_ref + effect_policy_hash + policy_ref) and standalone-core `CapabilityGrant` (scope_hash + policy_ref) — with resolution options R8-A (lossy deterministic reduction, default), R8-B (core schema bump), R8-C (separate AgentSnapshot v2). Resolution deferred to P0.6.7 design.
- Drift report `AGENTRUNTIME-TODICT-DRIFT-REPORT.md` §9 gained R8 entry; R8 is **not** a P0.6.6 blocker because P0.6.6 is validation-only.
- Scope: `synapse/agent_snapshot_adapter.py` (validation hardening only, no API expansion), `tests/test_as2_adapter_validation_p066.py` (new), 5 docs updates.
- `AS2ViolationContext` proposed by external reviewer is deferred to P0.6.7 to avoid surface expansion in a validation-only patch; the deferral is recorded in the implementation plan.
- No changes to `synapse/builtins.py`, `synapse/interpreter.py`, `synapse/actor_runtime.py`, `synapse/agent_snapshot.py`, memory, CVM, golden fixtures, or any legacy serialization path. `synapse/` outside `agent_snapshot_adapter.py` is byte-identical to P0.6.5.1.
- Full suite: **920 passed, 1 skipped** (P0.6.5.1 baseline 837 + 83 hardening tests, zero regression). Skip baseline preserved at 1.
- Next gate: P0.6.7 fixture-driven minimal standalone projection (separate team vote required). Legacy AgentRuntime bridge remains LOCKED through P0.6.8+ minimum.

## Alpha3g P0.6.5.1 — AS2 Skeleton Test Skip Cleanup

- Removed the artificial `pytest.skip()` branch from `tests/test_as2_adapter_skeleton_p065.py`.
- Negative-fixture validation now parametrizes only negative AS2 fixtures instead of skipping the positive fixture at runtime.
- Scope: test cleanup only. No `synapse/` changes, no adapter behavior changes, no projection logic, no `to_agent_snapshot()`.
- Expected result: skipped count returns to the existing baseline skip from `test_golden_replay.py`; P0.6.5 no longer introduces an additional skip.

## Alpha3g P0.6.5 — AS2 Flagged Adapter Skeleton

- Added `synapse/agent_snapshot_adapter.py` as an isolated AS2 skeleton module. It contains only typed error hierarchy, explicit input value skeletons, and validation-only boundary functions.
- Materialized the P0.6.1/P0.6.4 AS2 fail-closed taxonomy as local adapter exceptions under `AS2AdapterError`, with input/mapping/integrity subgroups.
- Added `validate_as2_inputs(...)` and focused validator functions for identity context, static model registry, memory reference source, and capability grant source. These functions do not build `AgentSnapshot`, do not compute snapshot hashes, and do not call legacy runtime.
- Added `tests/test_as2_adapter_skeleton_p065.py` to verify module quarantine, error hierarchy coverage against P0.6.4 fixtures, validation-only failure paths, absence of `to_agent_snapshot()`, and absence of legacy/ambient-authority imports.
- Scope: skeleton-only. No `AgentRuntime.to_dict()` changes, no `Environment._json_safe()` changes, no interpreter/actor/CVM/Integrate/Dream paths, no real provider registry, no FunctionDescriptor runtime registry, no runtime profile selector, no golden fixture changes.
- Next step: structured review of the skeleton boundary before any fixture-driven projection work. Full AS2 projection remains locked until a separate P0.6.6+ vote.

## Alpha3g P0.6.4 — AS2 Implementation Planning / Fixture Harness Design

- Added `docs/AS2-IMPLEMENTATION-PLAN.md` to define the implementation staging, P0.6.5 pre-flight gate checklist, and runtime locks for the future AS2 adapter.
- Added `docs/AS2-DRIFT-HARNESS-DESIGN.md` to define the data-only invariant harness boundary: fixture/schema validation only, no adapter imports, no `to_agent_snapshot()`, no runtime behavior tests.
- Added `docs/AS2-FIXTURE-CORPUS-SPEC.md` to pin the `alpha3g.as2_fixture.v1` fixture schema, naming convention, required corpus, and AdapterDerivationRecord fixture shape.
- Added the P0.6.4 AS2 fixture corpus under `tests/fixtures/as2/`: one positive minimal projection-input case and ten negative cases covering blocker-level fail-closed paths.
- Added `tests/test_as2_fixture_matrix_p064.py`, a passive test-only fixture matrix validator. The test validates JSON structure, expected error names, mock-only model registry entries, memory-space mismatch encoding, legacy envelope conflict encoding, and ambient-authority metadata without importing or calling an adapter.
- Scope: docs + test-only fixture/invariant harness. No `synapse/`, no adapter implementation, no `to_agent_snapshot()`, no legacy serialization changes, no profile selector, no real provider mappings.
- Next step: structured review of P0.6.4 artifacts and explicit team vote before P0.6.5 flagged adapter skeleton can begin.

## Alpha3g P0.6.3 — AS2 RFC Final Approval

- Approved `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md` as `APPROVED v1.0` after structured role-based team vote.
- Added final approval vote record, scope of approval, accepted known limitations, and future gates registry to `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` and `docs/ALPHA3F_PLANNING_GATE.md` to record AS2 RFC final approval and keep runtime implementation locked.
- Scope: documentation/process only. No `synapse/`, no `tests/`, no adapter implementation, no profile selector, no legacy serialization changes.
- Next step: P0.6.4 implementation planning / drift harness design. Adapter implementation remains locked until explicit P0.6.5 vote.

## Alpha3g P0.6.2 — AS2 Independent Verification Matrix

- Added `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md` as the doc-only independent verification artifact for the P0.6.1 AS2 RFC hardening.
- Verified AS2-01..AS2-05 and moved them from `RESOLVED` to `VERIFIED` in `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.
- Recorded the AS2 document authority stack: `RFC-AGENT-SNAPSHOT-ADAPTER.md` after P0.6.1 is normative for AS2 v1; older drift/planning wording is historical if superseded.
- Documented non-blocking watch items for AdapterIdentityContext presence markers and superseded historical wording.
- Scope: documentation only. No `synapse/`, no `tests/`, no adapter implementation, no profile selector, no legacy serialization changes. Next step: P0.6.3 final approval.


## Alpha3g P0.6.1 — AS2 RFC Hardening & Blocker Closure

- Revised `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md` as a hardened, doc-only AS2 blocker-closure patch. Runtime and tests remain locked.
- Closed AS2-01..AS2-05 as `RESOLVED` (not `VERIFIED`): explicit `AdapterIdentityContext`, immutable `StaticModelRegistry`, two-phase memory externalization, explicit `CapabilityGrantSource`, and canonical envelope isolation.
- Removed the unsafe P0.6.0 permission to inspect live tool namespaces. The RFC now forbids `tools.keys()`, callable/signature/decorator inspection, runtime tool registries, provider probing, wall-clock, UUIDs, I/O, storage writes, and ambient runtime authority inside the adapter.
- Added the AS2 memory-space validation invariant: host performs Phase 1 externalization; the pure adapter recomputes `expected_memory_space_id`, validates every `memory_ref`, forbids rewrite/filter/repair, and fails closed with `AdapterMemorySpaceMismatchError` on mixed/foreign/missing memory-space data.
- Added `AdapterDerivationRecord` as provenance metadata containing input hashes, model registry snapshot hash, memory-space policy version, and expected memory-space id. This audit record does not alter `AgentSnapshot` canonical state hash.
- Added typed fail-closed AS2 error taxonomy and documented AS2 v1 limitations: no mixed memory spaces, no subagent snapshots, no FunctionDescriptor runtime registry enforcement, no authority verification against live tools, no Environment dual-emission changes.
- Updated `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md` to mark AS2-01..05 `RESOLVED`, AS2-08 `ACKNOWLEDGED`, AS2-09 `RESOLVED`; AS2-06/07/10 remain open gates.
- Scope: documentation only. No `synapse/`, no `tests/`, no adapter implementation, no profile selector, no legacy serialization changes. Next step: P0.6.2 independent verification.

## Alpha3g P0.6.0 — AS2 Flagged Adapter RFC Draft

- Opened `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md` as the design-only RFC for a future flagged adapter from legacy `AgentRuntime` state to canonical `AgentSnapshot v1`.
- Added `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md` with structured findings `AS2-01..AS2-10`.
- Addressed P0.5.10 drift risks R1..R7 at the draft-design level:
  - identity requires AgentIdSeed/context sourcing;
  - bare `model` must resolve to `model_ref.v1` or fail closed;
  - inline memory dumps are forbidden;
  - capability grants must be declarative, not live callable serialization;
  - R5 selects Strategy B: identity state requires a dedicated read-only runtime/interpreter source;
  - schema/profile registry remains a deployment gate;
  - canonical envelope must not reuse legacy `__type__` marker.
- Reaffirmed P0.5.11 decisions: AGENT-06 is a `model_ref.v1` design boundary; AGENT-08/subagents remain out of AS2 v1.
- Scope: documentation only. No `synapse/`, no `tests/`, no adapter implementation, no profile selector, no legacy serialization changes.

## [Alpha3g] — P0.5.11 AgentSnapshot Pre-RFC Gate Closure (AGENT-06 / AGENT-08)

- Completed a doc-only pre-RFC gate-closure patch for AS2 flagged adapter RFC readiness. No runtime code, no tests, no `synapse/` changes.
- Partially closed `AGENT-06` only for AS2 RFC design by defining a minimal `model_ref.v1` boundary: `provider_namespace`, `model_id`, `model_version`, `capability_profile_hash`, `schema_version`, and `profile`.
- Restricted `provider_namespace` to the allowlisted enum `mock | anthropic | openai | local | custom`. Unknown provider namespaces must fail closed. `custom` is reserved for explicit provider manifests and is not a silent fallback.
- Explicitly excluded `endpoint_class` and `deterministic_mode_hash` from `model_ref.v1`; deployment transport, recorded inference, and deterministic provider replay remain future runtime/replay contracts.
- Partially closed `AGENT-08` only for AS2 RFC scoping: subagents are out of AS2 v1 scope, `SubAgentDef` is currently AST-level and not a legacy `AgentRuntime.to_dict()` surface, and no `subagent_snapshot_ref` is reserved in `AgentSnapshot v1`.
- Updated `docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md` to reference P0.5.11 closures for R2/R5/R7 and to require AS2 RFC to choose exactly one identity-sourcing strategy for R5 and reject reuse of the legacy `__type__` envelope for R7.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` and `docs/AGENTSNAPSHOT-RUNTIME-PLAN.md` to mark P0.5.11 as sufficient for opening AS2 RFC after explicit team vote, while leaving adapter implementation, provider drift runtime, subagent runtime, central registry, and deployment locked.

## [Alpha3g] — P0.5.10 AgentRuntime.to_dict() Drift Analysis (AS2-prep, read-only)

- Captured the actual `AgentRuntime.to_dict()` shape across 9 representative configurations and classified every legacy field against the canonical AgentSnapshot v1 allowlist established in P0.5.8 and hardened in P0.5.9.
- New artifact: `docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md`. Observed legacy top-level shape is invariant at `{name, model, trust_level, trust_scope, memory}` with `memory` containing `{short_term, long_term, capacity}`. Live handles (`tools`, `llm`, `env`, `mailbox`) never leak into legacy serialization. Identity attributes (`soulprint`, `identity_version`) are not part of `to_dict()` — they live in interpreter state.
- Drift report classifies fields as `migrates_as_is`, `requires_transform`, `legacy_only`, or `excluded_from_canonical`. `trust_level` and `trust_scope` migrate as is; `name`, `model`, and every `memory.*` field require transformation; `memory_config` is `excluded_from_canonical` pending AS2 clarification.
- Documented identity asymmetry: AgentSnapshot v1 requires `agent_id`, `definition_ref`, `capability_grants`, `model_ref`, `profile`, `schema_version`, none of which legacy `to_dict()` provides. AS2 adapter must source these from runtime state or fail closed.
- Documented adjacent legacy paths as boundaries (no tests, no modifications): `Environment._json_safe(AgentRuntime)` wraps as `{"__type__": "agent", "data": ...}`; `Environment.to_dict()` exposes agents transitively via `variables` and `agents`. Both must be addressed in AS2 design but not in this patch.
- Documented subagent / fracture status: `SubAgentDef` is an AST node, not a runtime object; no legacy `to_dict()` surface exists for sub-agents. AGENT-08 (subagent snapshot boundary) remains DEFERRED.
- Recorded AS2 adapter design risks R1..R7 in §9 of the drift report: identity asymmetry, model wrapping, memory dereference, capability grant sourcing, identity state sourcing, schema registry dependency, envelope conflict.
- New tests: `tests/test_agentruntime_todict_drift_p0510.py` (26 read-only tests). Shape invariance across all 9 configurations, field type invariants, asymmetry vs canonical AgentSnapshot v1, live handle isolation, round-trip stability, and a classification anchor that fails if the drift report falls out of sync with the actual shape.
- Full suite: 801 passed, 1 skipped (P0.5.9 baseline 775 + 26 drift probe, zero regression).
- No changes to `synapse/builtins.py`, `synapse/interpreter.py`, `synapse/actor_runtime.py`, `synapse/agent_snapshot.py`, `synapse/memory.py`, CVM, golden fixtures. Standalone isolation preserved.
- GO/NO-GO: `GO conditional on team vote` for P0.6.x AS2 flagged adapter RFC (design only). Adapter implementation remains NOT AUTHORIZED until AS2 RFC closes risks R1..R7 and team vote is recorded in `ALPHA3F_PLANNING_GATE.md`.
- Documentation: appended status blocks to `docs/AGENTSNAPSHOT-RUNTIME-PLAN.md`, `docs/ALPHA3F_PLANNING_GATE.md`, and `docs/MIGRATION-READINESS-CHECKLIST.md`.

## [Alpha3g] — P0.5.9 AgentSnapshot Standalone Hardening / Edge-Case Coverage

- Hardened `synapse/agent_snapshot.py` against edge cases discovered by adversarial probing of the SA1 surface. No new value objects, no new schema versions, no integration, no FunctionDescriptorRef.
- Defects closed in the SA1 value core:
  - external mutation of `config`, `canonical_fields`, or `model_ref` mappings (including nested mappings and lists) no longer silently shifts `snapshot_hash()`. Canonical attribute storage is now deep-frozen through `types.MappingProxyType` and tuple recursion.
  - duplicate `memory_refs` and conflicting `access_mode` on the same `(memory_space_id, memory_key)` now raise `AgentMemoryRefError`.
  - duplicate `capability_grants` per `tool_namespace` now raise `AgentCapabilityGrantError`.
  - whitespace-only `memory_key` now raises `AgentMemoryRefError`.
  - `AgentIdSeed.alias` normalizes `""` and whitespace-only strings to `None` before hashing, removing an identity-drift surface where three distinct `agent_id` values were derived for semantically identical seeds.
- `validate_agent_snapshot_payload` (round-trip from JSON) enforces the same duplicate and conflict invariants.
- New tests: `tests/test_agentsnapshot_hardening_p059.py` (35 tests). Covers mutation safety, duplicate / conflicting refs, alias normalization, whitespace-only keys, hash determinism under dict-order permutation, NaN / Infinity / non-string-key rejection, hash format strictness, runtime-envelope leakage at depth, and validator strictness for missing or wrong-typed fields.
- Full suite: 775 passed, 1 skipped (P0.5.8 baseline 740 + 35 hardening tests, zero regression).
- No changes to `synapse/__init__.py`, `builtins.py`, `interpreter.py`, `actor_runtime.py`, memory backends, CVM/opcodes, Integrate, Dream, or golden fixtures. Standalone isolation preserved.
- Documentation: appended status blocks to `docs/AGENTSNAPSHOT-RUNTIME-PLAN.md`, `docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md`, `docs/ALPHA3F_PLANNING_GATE.md`, and `docs/MIGRATION-READINESS-CHECKLIST.md`.

## [Alpha3g] — P0.5.6 AgentSnapshot Runtime Planning & Drift Audit (doc-only)

- Added `docs/AGENTSNAPSHOT-RUNTIME-PLAN.md` as the scoped post-approval planning gate for AgentSnapshot runtime work.
- Added `docs/AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md` mapping current `AgentRuntime`, `Environment`, actor runtime, memory, and storage surfaces to Canonical Snapshot vs Runtime Envelope categories.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark AgentSnapshot planning as `COMPLETED — READY FOR READ-ONLY DRIFT AUDIT` while keeping runtime implementation locked.
- Added planning-gate dependency edge for P0.5.7 AgentSnapshot runtime drift report before any standalone schema/value core is authorized.

### Scope lock

- Documentation/process only. No changes to `synapse/`, `tests/`, interpreter, actor runtime, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic IDs, schema registry implementation, or migration code.
- This patch authorizes only the next read-only drift/audit patch. It does not authorize AgentSnapshot runtime implementation.

## [Alpha3g] — P0.5.5 Agent Canonicalization Final Team Vote & Approval (doc-only)

### Changed
- Promoted `docs/RFC-AGENT-CANONICALIZATION.md` from `APPROVAL-CANDIDATE v0.4-AC` to `APPROVED v1.0` after structured team vote.
- Added role-based approval vote record, quorum criteria, no-blocking-objection result, cross-RFC alignment verification, known limitations, deferred gate inventory, and review triggers to `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark the Agent Canonicalization RFC prerequisite as APPROVED while preserving AGENT-04..08 and AGENT-11 as deferred runtime implementation gates.
- Added planning-gate dependency edge for scoped AgentSnapshot runtime planning after both Agent Canonicalization and FunctionDescriptor RFCs are approved.

### Scope
- Documentation/process only. No changes to `synapse/`, `tests/`, interpreter, actor runtime, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic IDs, or migration code.
- No normative body changes to the Agent RFC beyond approval metadata (`Status`, `Version`, `Patch`, and approval-record pointer).
- Runtime implementation remains blocked until a separate scoped runtime planning / drift-audit patch authorizes it.

## [Alpha3g] — P0.5.4 Agent RFC Independent Verification & Approval-Candidate Transition (doc-only)

### Changed
- Promoted `docs/RFC-AGENT-CANONICALIZATION.md` from `DRAFT v0.3` to `APPROVAL-CANDIDATE v0.4-AC` after independent team verification.
- Updated `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`: `AGENT-01`, `AGENT-02`, and `AGENT-03` moved from `RESOLVED` to `VERIFIED` with role-based team verification metadata.
- Recorded non-BLOCKER implementation gates for `AGENT-04` through `AGENT-08` and `AGENT-11`; `AGENT-09` and `AGENT-10` remain acknowledged v1 review boundaries.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark Agent RFC verification as `COMPLETED — APPROVAL-CANDIDATE` and preserve runtime lock.
- Added planning-gate dependency edge for P0.5.5 Agent RFC final team vote / approval.

### Scope
- Documentation/process only. No changes to `synapse/`, `tests/`, interpreter, actor runtime, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic IDs, or migration code.
- No runtime implementation is authorized by this patch; AgentSnapshot work remains blocked until Agent RFC final approval and separate scoped runtime planning.

## [Alpha3g] — P0.5.3 Agent RFC Dependency Update / AGENT-02 Prerequisite Satisfaction (doc-only)

### Changed
- Updated `docs/RFC-AGENT-CANONICALIZATION.md` to depend explicitly on `RFC-FUNCTION-DESCRIPTOR.md` v1.0 APPROVED.
- Synchronized Agent Definition text so `agent_definition_ref.manifest_hash` may be based on approved `function_descriptor_hash` values without adding new normative FunctionDescriptor requirements.
- Updated `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`: `AGENT-02` moved from `SPLIT` to `RESOLVED — prerequisite satisfied by RFC-FUNCTION-DESCRIPTOR v1.0`; independent verification remains reserved for P0.5.4.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark the FunctionDescriptor prerequisite as `SATISFIED` and the Agent RFC verification gate as `READY FOR INDEPENDENT VERIFICATION`.
- Added planning-gate dependency edge for P0.5.4 Agent RFC verification / approval-candidate transition.

### Scope
- Documentation/process only. No changes to `synapse/`, `tests/`, interpreter, actor runtime, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic IDs, or migration code.
- No Agent RFC blocker is marked `VERIFIED` in this patch; P0.5.4 remains the independent verification gate.

## [Alpha3g] — P0.5.2.3 Function Descriptor Final Team Vote & Approval (doc-only)

### Changed
- Promoted `docs/RFC-FUNCTION-DESCRIPTOR.md` from `APPROVAL-CANDIDATE v0.2-AC` to `APPROVED v1.0` after structured team vote.
- Added role-based approval vote record, quorum criteria, no-blocking-objection result, cross-RFC alignment verification, known limitations, and review triggers to `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md`.
- Updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark the FunctionDescriptor prerequisite as APPROVED while preserving `FUNC-03` / `FUNC-04` as deferred runtime implementation gates.
- Added planning-gate dependency edge for the next Agent Canonicalization verification steps.

### Scope
- Documentation/process only. No changes to `synapse/`, `tests/`, interpreter, actor runtime, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, closure/env serializer, AgentSnapshot runtime, canonical time API, or deterministic IDs.
- No normative body changes to the FunctionDescriptor RFC beyond approval metadata (`Status`, `Version`, `Patch`, and approval-record pointer).

## [Alpha3g] — P0.5.0 Agent Canonicalization RFC Draft (doc-only)

### Added
- Added `docs/RFC-AGENT-CANONICALIZATION.md` as a DRAFT contract for STABLE-05 / INT-07.
- Defined the three-layer agent model: Canonical Agent Definition, Canonical Agent Instance Snapshot, and Non-canonical Runtime Envelope.
- Added CVM Boundary Contract and Capability Grant design rules to prevent host runtime handles, tool objects, mailboxes, sockets, promises, provider clients, and caches from entering canonical snapshots.
- Added `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md` with AGENT-01..AGENT-10 structured findings and an approval gate.

### Scope
- Documentation only. No changes to `synapse/`, tests, interpreter, actor runtime, CVM/opcodes, stable canonical runtime, golden fixtures, AgentSnapshot implementation, FunctionDescriptor, canonical time, or deterministic IDs.

## [Alpha3g] — P0.4.10 Integrate Stable Canonical Migration (SI5)

### Changed
- Added explicit `Interpreter.integrate_hash_profile` selector for Alpha3g Integrate hash/event paths.
- Preserved `alpha3g.local-json.v1` as the default profile for existing Category B artifacts.
- Added opt-in `stable-canonical.v1` support for `pre_state_hash`, `post_state_hash`, `write_set_hash`, write-set value hashes, and aborted overlay summaries through the existing StateOverlay/service boundary.
- Added dual-profile Integrate tests covering LIVE event emission, body-skip REPLAY, abort replay, unknown profile fail-closed behavior, and stable-profile metadata.

### Scope
- No hard switch. No changes to `state_overlay.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, fixtures, canonical time API, deterministic IDs, FunctionDescriptor, or AgentSnapshot.

## [Alpha3g] — P0.4.5 Stable Canonical Value Review & Hardening (SI2)

### Changed
- Hardened `synapse/canonical_values.py` with explicit forensic docstrings,
  `PROFILE_VERSION`, and a stable `MAX_NESTING_DEPTH` fail-closed guard for
  excessively deep object graphs.
- Expanded `tests/test_stable_canonical_values_p044.py` with edge coverage for
  known cross-platform hash fixtures, valid Unicode scalar edge cases, deep
  nesting fail-closed behavior, typed-wrapper JSON round trips, mixed-type set
  canonical ordering, forensic error paths, and additional cycle detection.
- Added `docs/MIGRATION-READINESS-CHECKLIST.md` to define readiness gates for
  future migration of StateOverlay, Integrate, Dream, functions, agents,
  canonical time, and deterministic identities to `stable-canonical.v1`.

### Scope
- No integration into `interpreter.py`, `state_overlay.py`, `canonical_path.py`,
  CVM/opcodes, actor runtime, golden replay helpers, canonical time API,
  deterministic ID generation, `FunctionDescriptor`, or `AgentSnapshot`.
- Existing Alpha3g local profiles and Category B artifacts remain unchanged.

## [Alpha3g] — P0.4.4 Stable Canonical Value Runtime Core (SI1)

### Added
- Added standalone `synapse/canonical_values.py` implementing the approved
  `stable-canonical.v1` value serialization core.
- Added `tests/test_stable_canonical_values_p044.py` with coverage for stable
  hash determinism, NFC normalization, lone surrogate rejection, safe/large
  integer encoding, finite float boundaries, non-string dict key rejection,
  NFC key collision rejection, bytes base64url-nopad encoding, set canonical
  sorting, cycle detection, and fail-closed rejection of callables / host objects.

### Scope
- Standalone runtime core only. Zero integration into `interpreter.py`,
  `state_overlay.py`, `canonical_path.py`, CVM/opcodes, actor runtime, golden
  replay helpers, canonical time API, deterministic ID generation,
  `FunctionDescriptor`, or `AgentSnapshot`.
- Existing Alpha3g local profiles and Category B artifacts remain unchanged.

## [Alpha3g] — P0.4.2 Stable Canonical Identity RFC Revision & Blocker Closure (doc-only)

### Changed
- Revised `docs/RFC-STABLE-CANONICAL-IDENTITY.md` from DRAFT v0.2 to
  APPROVAL-CANDIDATE v0.3 pending team verification.
- Resolved STABLE-01 by defining canonical time replay sources: recorded-and-
  consumed `time_read` events or deterministic logical time derived from
  approved canonical material, with fail-closed behavior when unavailable.
- Resolved STABLE-02 by adding a fail-closed builtin allowlist rule: builtins
  must be deterministic, side-effect-free, explicitly allowlisted, replay-safe,
  and operate only on canonical values before they may participate in canonical
  execution.
- Resolved STABLE-03 by defining fail-closed schema/profile version handling and
  applier registry behavior.
- Marked STABLE-04..STABLE-08 as deferred implementation gates with explicit RFC
  section references, and STABLE-09..STABLE-10 as acknowledged v1 boundaries.
- Updated `docs/RFC-STABLE-CANONICAL-IDENTITY-REVIEW-NOTES.md` with P0.4.2
  resolution summaries.

### Scope
- Documentation only. Zero runtime/code/test changes.
- Stable Identity runtime, canonical time API, deterministic identity generation,
  FunctionDescriptor, AgentSnapshot, and migration appliers remain locked until
  RFC approval and a separate implementation scope.

## [Alpha3g] — P0.4.0 Stable Canonical Identity RFC Expansion (doc-only)

### Changed
- Replaced `docs/RFC-STABLE-CANONICAL-IDENTITY.md` skeleton v0.1 with a full
  DRAFT v0.2 parent contract. The RFC now defines allowlist-based canonical
  value policy, canonicalization profiles, function/closure v1 rejection and
  future `FunctionDescriptor` requirements, canonical time principles,
  deterministic identity generation, migration rules, and explicit acceptance
  criteria.
- Clarified that current `StateOverlay` / Integrate hashing is an
  `alpha3g.local-json.v1` subset and current Integrate paths use the
  `alpha3g.integrate-path.v1` profile implemented in `synapse/canonical_path.py`;
  neither is presented as the full Stable Canonical Identity runtime.
- Mapped Stable Canonical Identity back to Dream strict-eligibility and Integrate
  deferred gates INT-04..INT-08, especially function/closure handling, canonical
  time, deterministic resource/event identity, agent snapshots, and namespace
  semantics.

### Scope
- Documentation only. Zero runtime/code/test changes.
- No Stable Identity runtime implementation authorized by this patch.

## [Alpha3g] — P0.3.6 Integrate Release-Readiness Pass (I7, doc-only)

### Changed (documentation sync only — zero runtime changes)
- `docs/DETERMINISM_CONTRACT.md`:
  - §6.3 rewritten to the implemented Alpha3g behavior with line citations
    (`interpreter.py:1609-1780` LIVE, `interpreter.py:1986-2038` REPLAY): body
    skipped in REPLAY, `integrate_committed` / `integrate_aborted` consumed and
    verified; legacy integrate remains Category C.
  - §9.1 adds explicit integrate Strict Layer 1 CRITICAL INVARIANT (excluded
    until INT-04..INT-08 satisfied).
  - §12 table: `Integrate` moved from Category C (RFC pending) to **Category B**
    (replay-safe recorded, body skipped) with PENDING strict-eligibility note.
  - §13.3: marked "implemented as of Alpha3g I1–I6" with RFC and code references.
- `docs/ARCHITECTURE_OVERVIEW.md`: integrate replay-applier listed as
  implemented; deterministic replay runner deferred reason updated from
  "integrate semantics don't exist" to "durable gates INT-04/05/06 pending".
- `docs/SEMANTICS.md`: integrate replay cell corrected — body NOT executed in
  REPLAY; `integrate_rollback` (old name) replaced with `integrate_aborted`
  (current event type); full event schema including `pre_state_hash`,
  `post_state_hash`, `write_set_hash` documented.
- `docs/RFC-INTEGRATE-REVIEW-NOTES.md`: INT-09 and INT-10 closed from OPEN to
  ACKNOWLEDGED (v1 boundary documented); acknowledgement blocks added;
  approval decision block updated to reflect I7 completion.

### Scope
- Zero runtime/code/test changes. All edits are documentation-only.
- No new runtime gates opened. INT-04..INT-08 remain DEFERRED MAJOR.
- Existing strict Layer 1 golden suite unaffected.

## [Alpha3g] — P0.3.5 Integrate Golden Fixtures & Replay Conformance (I6)

### Added
- `record_integrate_artifact()` and `replay_integrate_artifact()` helpers in
  `synapse/golden_replay.py`. Separate from the existing `record_source()` /
  `replay_mock_artifact()` so that existing strict golden fixtures remain on the
  legacy path and are unaffected by integrate-specific interpreter flags
  (`integrate_i2_skeleton_enabled`, `RuntimeMode.REPLAY`).
- `tests/test_integrate_golden_p035.py` — 8 golden-fixture conformance tests
  covering the required I6 scenarios from INTEGRATE-IMPLEMENTATION-PLAN.md §8:
  - `integrate_committed_basic` — LIVE commit, REPLAY applies recorded write-set
  - `integrate_committed_body_skipped_in_replay` — body-skip proof: REPLAY with
    a body that writes x=999 yields x=2 from the recorded write-set; body
    print does not appear in output
  - `integrate_noop_empty_write_set` — read-only body; x not in write-set
  - `integrate_aborted_barrier_violation` — abort recorded, state unchanged in REPLAY
  - `integrate_replay_hash_mismatch_raises` — tampered history.json raises
    `DeterministicReplayError("chain broken")`
  - `integrate_state_hash_round_trip` — recorded `post_state_hash` matches env
    after successful replay
  - `integrate_golden_idempotency_guard` — second replay of same event index
    raises `ReplayIntegrityError("already applied")`
  - `existing_strict_golden_suite_unaffected` — all 6 existing strict Layer 1
    fixtures still pass (`drift == 0`)

### Scope
- P0.3.5 adds golden-fixture infrastructure and conformance tests only. It does
  not add CVM/opcodes, actor-runtime changes, agent canonicalization, durable
  crash-resume checkpointing, or Stable Identity runtime.
- `record_source()` and `replay_mock_artifact()` are unchanged.
- Fixtures are generated dynamically in tests (no pre-committed JSON blobs),
  keeping them in sync with the interpreter automatically.

## [Alpha3g] — P0.3.4 Integrate REPLAY Applier v1

### Added
- Added Alpha3g I4 REPLAY applier for opt-in integrate events.
- `integrate_committed` replay now consumes the recorded event, verifies `schema_version`, `pre_state_hash`, `write_set_hash`, per-entry old/new value hashes, applies the recorded `/env/*` write-set, and verifies `post_state_hash` without executing the integrate body.
- `integrate_aborted` replay now consumes the recorded abort event, verifies the pre-state hash, leaves state unchanged, and reproduces a deterministic abort exception without executing the body.
- Added in-run idempotency guard so a consumed integrate event cannot be applied twice in the same replay run.
- Added `tests/test_integrate_replay_applier_p034.py` covering committed replay, body-skip behavior, hash mismatches, aborted replay, idempotency, and legacy default compatibility.

### Constraints
- P0.3.4 does not implement CVM/opcodes, actor-runtime changes, Stable Identity expansion, golden replay fixtures, durable crash-resume checkpointing, or agent canonicalization.

## [Alpha3g] — P0.3.3 Integrate LIVE Commit & Event Schema Emission

### Added
- Added Alpha3g LIVE-mode `integrate_committed` event emission for the opt-in integrate path, including `schema_version`, `pre_state_hash`, `post_state_hash`, `write_set`, and `write_set_hash`.
- Added Alpha3g LIVE-mode `integrate_aborted` event emission with sanitized abort metadata and forensic `overlay_summary` that excludes concrete new values.
- Added `tests/test_integrate_event_emission_p033.py` covering commit events, abort events, empty write sets, sorted write sets, base-env application, and legacy default compatibility.

### Changed
- The opt-in Alpha3g integrate path now applies successful `/env/*` write sets to the base environment after constructing the committed event payload.
- `flatten_env_variables()` now excludes non-canonical helper bindings such as host callables from integrate state hashes while preserving parent lookup behavior for functions/builtins.
- Added deterministic `StateOverlay.overlay_summary()` support for aborted integrate events.

### Constraints
- P0.3.3 is LIVE-only. It does not implement the REPLAY applier, CVM/opcodes, actor-runtime changes, golden fixtures, Stable Identity expansion, promise cleanup registry, or agent canonicalization.

## [Alpha3g] — P0.3.1 LIVE-mode Integrate Skeleton

### Added
- Added opt-in Alpha3g I2 integrate skeleton mode through `Interpreter.integrate_i2_skeleton_enabled`.
- Added `IntegrateOverlayEnvironment`, which routes `/env/<name>` writes through `StateOverlay` while preserving parent access for functions, agents, and builtins.
- Added `Interpreter.last_integrate_write_set` as the I2 draft `WriteSet` inspection point.
- Added `tests/test_integrate_live_skeleton_p031.py` covering overlay isolation, draft write-set collection, runtime barrier failures, and legacy default compatibility.

### Changed
- `evaluate_integrate()` now dispatches to the Alpha3g I2 skeleton path only when the explicit feature flag is enabled. Legacy v1.4/v1.4.1 integrate behavior remains the default.
- Added I2 runtime nondeterminism barrier checks for forbidden builtins (`print`, `time`, `random`, `uuid`) and I2-only checks for `dream`, `evolve`, and memory mutation operations.

### Constraints
- I2 does not emit `integrate_committed` or `integrate_aborted` events.
- I2 does not apply the draft `WriteSet` to the base environment.
- I2 does not implement the replay applier, CVM/opcodes, actor runtime changes, promise cleanup, or agent canonicalization.

## [Alpha3g] — P0.3.0a StateOverlay Interface Hardening

- Hardened the standalone `StateOverlay` interface before wiring it into `evaluate_integrate()`.
- Added immutable `WriteSet` wrapper around `WriteSetEntry` so future LIVE/event-emission code does not depend on raw `list[dict]` structures.
- Clarified that `canonical_value_hash()` / `StateOverlay.canonical_hash()` implement the Alpha3g I1 local canonical JSON subset, not the full future `RFC-STABLE-CANONICAL-IDENTITY` contract.
- Documented delete tombstone serialization and canonical-hash-based no-op elision semantics.
- Expanded edge-case coverage for delete/re-set flows, set-then-delete elision, terminal discard behavior, unsupported values, non-string dict keys, hash stability, and malformed percent escapes.
- Scope remains isolated to `synapse/state_overlay.py`, I1 tests, and status documentation. No `evaluate_integrate()`, event emission, replay applier, CVM/opcode, or actor-runtime behavior was changed.

## [Alpha3g] — P0.3.0 StateOverlay Core & Canonical Path Parser

### Added
- Added `synapse/canonical_path.py` with Alpha3g canonical integrate path parsing, `/env/*` and `/memory/*` namespace validation, NFC Unicode validation, and canonical memory-key percent encoding from `RFC-INTEGRATE-REPLAY-APPLIER.md` §4.2.
- Added `synapse/state_overlay.py` with standalone copy-on-write `StateOverlay`, dirty-path tracking, canonical value hashing, draft write-set generation, discard semantics, and explicit rejection of callable/function values in changed paths.
- Added `tests/test_state_overlay_core_p030.py` covering canonical key encoding, path rejection, empty memory keys, copy-on-write isolation, no-op writes, sorted write sets, deletion, discard, hash changes, and callable rejection.

### Changed
- Opened runtime scope only for the I1 modules authorized by `docs/INTEGRATE-IMPLEMENTATION-PLAN.md`.
- Preserved the I1 zero-integration boundary: no `evaluate_integrate()` wiring, no `integrate_committed` / `integrate_aborted` emission, no REPLAY applier behavior, no CVM/opcode work, and no actor runtime changes.

### Constraints
- First code patch after P0.2.x planning.
- `StateOverlay` remains a standalone infrastructure module and is not used by the interpreter in P0.3.0.
- Deferred gates INT-04 through INT-07 remain pending for later implementation patches; INT-08 syntax/path ambiguity is addressed at the parser layer in I1, while broader runtime semantic validation remains for I2.

## [Alpha3g] — P0.2.8 Integrate Implementation Planning

### Added
- Added `docs/INTEGRATE-IMPLEMENTATION-PLAN.md` as the implementation-planning artifact for the approved `RFC-INTEGRATE-REPLAY-APPLIER.md`.

### Changed
- Documented the staged Integrate implementation sequence: I1 `StateOverlay` core and canonical path parser, I2 LIVE-mode skeleton, I3 event schema emission, I4 replay applier v1, I5 agent/value canonicalization boundary, I6 golden fixtures, and I7 final gate/docs sync.
- Mapped deferred MAJOR findings INT-04 through INT-08 to the implementation patches that must satisfy them.
- Defined the recommended first runtime target as `P0.3.0 / I1 — StateOverlay Core & Canonical Path Parser`, explicitly excluding `evaluate_integrate()` integration, replay appliers, CVM/opcodes, and history event emission from I1.
- Recorded Stable Identity dependency boundaries and golden replay fixture obligations before Integrate replay is considered complete.

### Constraints
- Documentation-only planning patch.
- No changes to `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, actor runtime, replay applier, or runtime behavior.
- P0.2.8 does not start Integrate implementation; it only defines the implementation boundary for later patches.

## [Alpha3g] — P0.2.7 RFC-INTEGRATE Team Verification & Approval Gate

### Changed
- Verified INT-01, INT-02, and INT-03 in `docs/RFC-INTEGRATE-REVIEW-NOTES.md` under `docs/RFC-PROCESS.md`.
- Updated `docs/RFC-INTEGRATE-REPLAY-APPLIER.md` from `APPROVAL-CANDIDATE — Team Verification Required` to `APPROVED — Alpha3g P0.2.7`.
- Recorded INT-04 through INT-08 as deferred MAJOR implementation gates and INT-09 through INT-10 as tracked MINOR future-compatibility notes.

### Constraints
- Documentation-only approval-gate patch.
- No changes to `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, actor runtime, replay applier, or runtime behavior.
- Future integrate implementation may begin only in later patches and only within the approved RFC scope.

## [Alpha3g] — P0.2.6 RFC-INTEGRATE Blocker Resolution Revision

### Changed
- Revised `docs/RFC-INTEGRATE-REPLAY-APPLIER.md` from `DRAFT — Team Review Required` to `APPROVAL-CANDIDATE — Team Verification Required` as an author-resolution package for the three P0.2.4 approval blockers.
- Resolved INT-01 in the RFC text by adding an explicit v1 function serialization boundary: functions, closures, native callables, builtin functions, bound methods, and host callables in changed paths cause `CanonicalSerializationError`, record `integrate_aborted`, and use `abort_reason = "serialization_error"`.
- Resolved INT-02 in the RFC text by replacing RFC-6901-only path wording with canonical memory-key encoding: Unicode scalar validation, NFC normalization, UTF-8 bytes, uppercase percent encoding outside `[A-Za-z0-9_.-]`, explicit `/memory/` empty-key handling, and strict namespace parsing.
- Resolved INT-03 in the RFC text by adding a habit/background mutation transaction barrier: automatic habit activation is suspended or deferred during integrate spans, and observed background mutation aborts with `barrier_violation` / `habit_activation` before state or history is dirtied.
- Updated `docs/RFC-INTEGRATE-REVIEW-NOTES.md` to mark INT-01, INT-02, and INT-03 as `RESOLVED — pending independent verification` with exact section references and P0.2.6 resolution summaries.

### Constraints
- Documentation-only blocker-resolution patch.
- No changes to `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, actor runtime, replay applier, or runtime behavior.
- P0.2.6 does not mark blockers `VERIFIED` and does not approve runtime implementation. `evaluate_integrate()`, `StateOverlay`, CVM/opcode work, and replay appliers remain blocked until team verification and RFC approval.

## [Alpha3g] — P0.2.5 RFC Process & Review Registry Governance

### Added
- Added `docs/RFC-PROCESS.md` as the Alpha3g process baseline for RFC lifecycle, review registry governance, finding severity, finding status transitions, dependency rules, cross-RFC IDs, implementation locks, PoC exceptions, and source-of-truth separation.

### Changed
- Updated `docs/RFC-INTEGRATE-REVIEW-NOTES.md` to reference `RFC-PROCESS.md` as the governing process artifact.
- Added finding lifecycle fields to the Integrate review registry: `Status`, `Related IDs`, `Resolution Plan`, `Verification Owner`, `Next Action Trigger`, and impact metadata.
- Clarified that INT-01 through INT-03 remain `OPEN` BLOCKER findings; P0.2.5 does not resolve them. Their resolution is deferred to P0.2.6 under the new process.

### Constraints
- Documentation-only governance patch.
- No changes to `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, actor runtime, replay applier, or runtime behavior.
- `RFC-INTEGRATE-REPLAY-APPLIER.md` is intentionally not revised in this patch; integrate runtime work remains blocked until the RFC reaches `APPROVED`.

## [Alpha3g P0.2.2] — Dream Strict Layer 1 Eligibility RFC (doc-only)

### Added
- `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md`: new DRAFT RFC that resolves the
  Alpha3g dream eligibility question. Verdict: `DreamBlock` is **not Strict
  Layer 1 eligible under A2** because A2 replay executes the dream body; it may
  become eligible only under a future consume-only / state-delta / recorded
  subtrace model.

### Audited fact (observable replay side-effect)
- Corrected the `print` trace for dream bodies: `print` inside `dream` does not
  normally go through `Interpreter._print()` / `output_buffer`. The sandbox
  rejects the parent-scope `_print` callable, `eval_call()` swallows
  `DreamSandboxIsolationError`, then falls through to `BUILTINS["print"]`,
  which calls host Python `print`. Therefore A2 replay can repeat a host stdout
  side-effect while synchronizing the dream body.

### Changed
- `docs/DETERMINISM_CONTRACT.md`: §6.1.1 and §9.1 now point to the new RFC and
  state the resolved verdict: no Strict Layer 1 admission under A2; future
  eligibility requires consume-only/subtrace/state-delta replay.
- `docs/ALPHA3F_PLANNING_GATE.md`: append-only P0.2.2 addendum records the RFC
  decision and keeps §9.1 closed.
- `docs/ARCHITECTURE_OVERVIEW.md` and `docs/DEBUGGER_USER_GUIDE.md`: updated
  from "pending audit" wording to the resolved A2 default-deny verdict.
- `README.md`: project status and documentation index updated with the new RFC.

### Scope Lock
- Strictly doc-only. No changes to `synapse/`, `tests/`, `examples/`, parser,
  interpreter, CVM, bridge, or CLI. §9.1 remains closed.

## [Alpha3g P0.2.1] — Dream Determinism Contract Sync (doc-only)

### Changed
- Synced documentation with the implemented Dream Replay contract. After the
  Alpha3g Dream Replay implementation, `DreamBlock` is replay-consumed and
  verified (`dream_key`/`result_hash`), so the docs that still classified it as
  Category C "recorded but not replay-consumed" were lying about the code.
- `docs/DETERMINISM_CONTRACT.md`: §3.3 notes dream is now Category B under the
  strict schema; §6.1 rewritten to the implemented behavior with line citations
  (`interpreter.py:1328-1392`); new §6.1.1 lists the three open items blocking
  strict eligibility (observable body re-execution, closure isolation,
  nested-event origin); §9.1 keeps DreamBlock excluded from Strict Layer 1 with
  an explicit pending-audit invariant (NOT opened); §12 table moves DreamBlock
  to Category B with "strict Layer 1 eligibility: PENDING AUDIT".
- `docs/SEMANTICS.md`: corrected the dream replay cell — body is executed for
  cursor synchronization (not "skipped"), result sourced from the record.
- `docs/ARCHITECTURE_OVERVIEW.md`: dream replay marked implemented; replay
  runner now depends only on integrate.
- `docs/DEBUGGER_USER_GUIDE.md`: dream described as Category B but still
  excluded from Strict Layer 1; legacy dream remains Category C.
- `README.md`: Alpha3g status updated — dream replay implemented, RFC APPROVED.

### Audited fact (closure isolation)
- Verified in code: a `.syn` function read from the parent scope inside a dream
  is currently blocked — `DreamSandboxEnvironment.get()` rejects non-container,
  non-immutable values (a `FnDef` is neither), raising
  `DreamSandboxIsolationError`. So there is no closure-mutation leak today.
  However, the block is a side effect of the type check rather than an explicit
  contract, and the error is swallowed by `except RuntimeError: pass` in
  `eval_call` (surfacing as a misleading "Undefined function"). Both points are
  recorded as work for RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.

### Scope Lock
- Strictly doc-only. Zero changes to `synapse/` or `tests/`. No change to
  `evaluate_dream()`, `hash_event_chain()`, parser, CVM, or CLI. §9.1 not
  opened. 620 passed, 1 skipped.

## [Alpha3f] — Patch 9: Product Clarity (doc-only)

### Added
- `docs/ARCHITECTURE_OVERVIEW.md`: top-down data-flow map (source → lexer →
  parser → interpreter/CVM → bridge → execution_history → hash chain → golden
  artifact → trace adapter → divergence → CLI), every claim anchored to a real
  module, plus an explicit "not built yet" section for deferred Alpha3g work.
- `docs/DEBUGGER_USER_GUIDE.md`: practical record/replay/compare guide based on
  the real P7 CLI — `synapse run --record`, `synapse replay --mock`,
  `synapse debug compare`, the full exit-code table (0 equal / 7 divergence /
  1 bad input / 8 artifact integrity / 2–6 fork errors), JSON output format,
  and current limitations (no replay runner, no session persistence, no
  fork-id compare).

### Changed
- `README.md`: audited against code and corrected. Fixed stale version header,
  removed obsolete v0.2–v1.3.1 changelog blocks (history preserved in this
  CHANGELOG), added Track C status and documentation links. Language-reference
  body left intact. README version verified by `test_readme_version`.

### Decision
- Deterministic Replay Runner (P8b) deferred to Alpha3g — it depends on
  dream/integrate replay-applier contracts that are still behind the gate.

### Scope Lock
- Strictly doc-only. Zero changes to `synapse/` or `tests/` runtime code.
  606 passed, 1 skipped.



### Added
- `docs/DETERMINISM_CONTRACT.md`: fact-based determinism contract for all events
  entering canonical `execution_history`. Defines three categories — A (canonical
  deterministic), B (replay-safe recorded nondeterminism), C (experimental /
  non-strict-golden-safe) — plus the contagion rule (one unstable field at index
  N invalidates every chain hash from N+1 onward).
- Classification verified against code: `DreamBlock` is Category C
  (`dream_completed` recorded but not replay-consumed; body re-executed in
  replay); `affective_resonance` is Category B (UUID `event_id`, replay consumes
  recorded event); `fracture`/`debate`/`superpose` are Category B (deterministic
  identity, nested LLM replay-safe — NOT Category C); LLM forbidden in `integrate`
  by design (`IntegrateIsolationViolation`).
- Appendix A: read-only audit of all 6 Layer 1 strict golden programs. Result:
  all clean — no Category C construct, UUID-bound identity, or live time/random
  source present. Current strict baseline is safe; no fixtures need relocation.

### Scope Lock
- Strictly doc-only. Zero changes to `interpreter.py`, `affective_runtime.py`,
  `hash_event_chain`, builtins, golden replay, or any runtime path. All fixes
  (dream replay contract via Path A, stable identity policy, integrate
  replay-applier) deferred to alpha3g RFCs behind the planning gate.

## [Alpha3f] — Time-Travel Debugger Patch 7: CLI Compare Divergence Wiring

### Added
- Upgraded `synapse debug compare <artifact_dir_a> <artifact_dir_b>` to compare artifact-backed traces through `GoldenArtifactTraceAdapter` and the core `find_trace_divergence()` engine.
- Added structured JSON output using `TraceDivergenceResult.to_dict()`.
- Added P7 CLI tests for equal traces, divergent traces, missing paths, broken artifacts, JSON schema, and delegation to core divergence logic.

### Contracts
- `equal` traces return exit code `0`.
- `divergence found` returns exit code `7` as a valid non-zero diagnostic result for CI/shell scripts.
- malformed/missing artifact paths return exit code `1` through CLI argument handling.
- `ReplayArtifactError` and `DeterministicReplayError` during compare return exit code `8`.
- Compare inputs are artifact directories, not `fork_id`s; fork-id compare remains deferred until session/replay runtime stores fork-local histories.
- CLI does not compute hashes or call `hash_event_chain()`; forensic comparison remains owned by `find_trace_divergence()`.

### Changed
- Removed the obsolete `DIVERGENCE_MISSING_HASH` public reason; real artifacts derive forensic hashes from the replay hash chain instead of requiring a raw per-event `history_hash` field.

### Scope Lock
- No session persistence, daemon/cross-process registry, REPL, VM/parser/opcode changes, provider calls, live replay execution, policy lowering, or habit/audio/soulprint/swarm work.

## [Alpha3f] — Time-Travel Debugger Patch 6: Trace Divergence

### Added
- `find_trace_divergence(left, right)`: pure read-only function that finds the
  first point where two traces diverge. Returns a frozen `TraceDivergenceResult`.
  Accepts trace adapters/stubs or raw event sequences. Never advances any
  TraceContext cursor — works over immutable history snapshots.
- `TraceDivergenceResult` (frozen dataclass): `equal`, `reason`,
  `first_divergence_index`, `left_event`/`right_event`,
  `left_history_hash`/`right_history_hash`, plus JSON-safe `to_dict()`.
- `history_from_context()`: extracts an immutable history snapshot from a trace
  adapter/stub (via `.execution_history`) or a raw sequence, without consuming
  any cursor.
- 22 P6 tests in `tests/test_debugger_divergence_p6.py`.

### Contracts
- **Forensic key is the derived tamper-evident chain hash**, not a per-event
  field. Raw golden events carry `type`/`trace_id`/payload but no per-event
  `history_hash`; the chain is derived via the same `hash_event_chain` the
  replay engine uses, so each position's hash reflects all preceding events.
  Synthetic traces that DO carry an explicit `history_hash` on every event use
  it directly (test convenience).
- Divergence reasons: `equal`, `hash_mismatch`, `type_mismatch` (secondary),
  `length_mismatch` (one trace is a clean prefix of the other).
- compare is strictly read-only: cursors and source event dicts are never
  mutated.

### Scope Lock
- Core engine only. No CLI `compare` upgrade (existing thin surface unchanged),
  no session persistence, daemon, REPL, VM/parser/opcode changes, policy
  lowering, or habit/audio/soulprint/swarm work.



### Added
- `GoldenArtifactTraceAdapter`: production `TraceContextProtocol` implementation
  over a recorded golden artifact directory (replaces the synthetic
  `ReplayRuntimeStub` as the real data source). Loads `manifest.json` +
  `history.json`, exposes recorded `execution_history` through cursor-only
  consumption. Strictly read-only; the on-disk artifact is never modified.
- `ForkRegistry.create_fork_from_artifact()`: opens a debug fork whose
  `parent_history_hash` is bound to the artifact's `final_history_hash`,
  giving an unbroken forensic trail from the recorded golden baseline to the
  debug lineage.
- `ReplayArtifactError`: distinct from `DeterministicReplayError`. Raised for
  *input artifact* problems (missing manifest/history, unreadable JSON, wrong
  schema) rather than replay determinism mismatches.
- 18 P5 bridge tests in `tests/test_debugger_bridge_p5.py`.

### Contracts
- Fail-fast at construction: missing/unreadable manifest or history →
  `ReplayArtifactError`; malformed history (not a list of mappings) →
  `ReplayArtifactError`; broken `history_hash` chain vs
  `manifest.final_history_hash` → `DeterministicReplayError`.
- In-memory history is a defensive immutable tuple; `next_expected_event()`
  returns a per-call defensive copy so caller mutation cannot pollute the
  adapter's view or any other reader. Only the integer cursor is mutable.
- Chain verification reuses `golden_replay._history_chain_valid`; no chain
  crypto is reimplemented.

### Scope Lock
- No VM/opcode/parser changes, no provider calls, no VM execution, no session
  persistence, daemon, REPL, or CLI surface changes. P6 trace divergence and
  `compare` wiring remain deferred until P5 is merged.



### Added
- `TraceContextProtocol` as the minimal structural replay cursor contract: `next_expected_event()` and `consume_expected_event()`.
- `ReplayRuntimeStub`, an in-memory immutable trace view with cursor-only consumption.
- Trace protocol tests covering structural compliance, history immutability, EOF behavior, deterministic strict consumption, exploratory-live trace bypass, validator type hints, and malformed trace handling.

### Changed
- `EventInjectionValidator.validate_injection()` now types `trace_context` against `TraceContextProtocol | None` while preserving duck-typed behavior.

### Scope Lock
- No session persistence, daemon/cross-process registry, REPL, parser/opcode/VM changes, global `VMState` rewrite, policy lowering, habit/audio/soulprint/swarm work, or package-layout refactor is included.

## [Alpha3f Track C] — Debugger Core Patch 2 Injection Validator

### Added
- `EventInjectionValidator` with declarative `INJECTION_POLICY_MATRIX` for debugger event injection.
- Governance and lifecycle exceptions: `GovernanceViolationError`, `ForkDisposedError`, and `ForkLifecycleError`.
- Fork lifecycle state transitions in `ForkRegistry`: `active → disposed`, `active → completed`, and `active → failed`.
- Deterministic disposal of fork-local resources attached to a fork.
- Fork-local `GUARD_ENTER` validation for exploratory-live guard evaluation paths.

### Security Contracts
- `GUARD_ENTER` with a new `guard_hash` is allowed only in `exploratory-live` forks with `scope = "fork-local"`.
- Deterministic replay may only consume an already-recorded matching `GUARD_ENTER`; new guard paths raise `DeterministicReplayError`.
- Forbidden injections fail closed with `GovernanceViolationError`: `GUARD_VERDICT_OVERRIDE`, `GUARD_VIOLATION_ACK`, `CAPABILITY_GRANT`, `PROGRAM_HASH_REWRITE`, and `HISTORY_HASH_REWRITE`.
- Client-supplied fork modes or security-sensitive payload keys are rejected; fork mode is resolved from `ForkRegistry`.

### Scope Lock
- No CLI/REPL surface, parser changes, opcode changes, global `VMState` rewrite, session persistence, policy block lowering, or non-debugger runtime features are included.

## [Alpha3f Planning] — Time-Travel Debugger RFC Approved

### Changed
- `RFC-TIME-TRAVEL-DEBUGGER.md` promoted from `APPROVAL-CANDIDATE` to `APPROVED`.
- Added the final fork-local `GUARD_ENTER` injection clarification for new guard evaluation paths.
- Clarified Golden Replay interaction: Golden artifacts are immutable read-only baselines, and forks from golden artifacts create separate debug lineages.
- `ALPHA3F_PLANNING_GATE.md` updated as an append-only governance trail with PASSED status, approved implementation scope, and still-blocked work.

### Scope Lock
- `feature/debugger-core`, `feature/debug-cli-surface`, and `feature/event-injection-validator` may open only within the approved Time-Travel Debugger scope.
- `policy enforce { ... }` block lowering, `throws GUARD_VIOLATION`, non-throwing guards, Habit Interrupts, Soulprint/Acoustic/Swarm features, and non-debugger VM opcode work remain blocked pending separate RFC/gate approval.

## [v2.2.0-alpha3e] — Stable Alpha3e Final

Final Alpha3e release checkpoint. This tag promotes the RC1 line after Golden Replay acceptance.

### Added
- Golden Replay Suite with Layer 1 strict deterministic artifacts and Layer 2 corpus smoke parsing.
- `synapse run --record` / `synapse replay --mock` infrastructure through `synapse.golden_replay`.
- `make test-golden` release gate.
- Virtual-clock replay contract and stable VM-state sanity validation.

### Changed
- Version authorities promoted from `2.2.0-alpha3e-rc1` to `2.2.0-alpha3e`.
- Corpus audit final report path is `reports/corpus_fallback_alpha3e.json`.
- Golden Replay is now the required deterministic baseline for future VM/runtime changes.

### Release Gates
- `make test`
- `make lint`
- `make audit`
- `make test-golden`

### Scope Lock
- Time-Travel Debugger remains RFC-only until explicit Alpha3f approval.
- `policy enforce { ... }` block lowering, `throws GUARD_VIOLATION`, non-throwing guards, habit interrupts, Soulprint/audio/swarm features remain out of Alpha3e.

# Synapse Changelog
Все изменения языка и рантайма в хронологическом порядке.

## [Alpha3f Planning] — Time-Travel Debugger RFC Approval Candidate

### Changed
- `RFC-TIME-TRAVEL-DEBUGGER.md` promoted from `DRAFT` to `APPROVAL-CANDIDATE` for team vote.
- Added mandatory contracts for replay modes, host-call replay policy, fork identity, Copy-on-Write state diffing, fork lifecycle/GC, event injection policy, Golden Replay interaction, and security/governance boundaries.
- `ALPHA3F_PLANNING_GATE.md` updated to keep implementation branches blocked until RFC approval.

### Scope Lock
- No Time-Travel Debugger implementation is authorized by this planning update.
- No CoW manager, event injection runtime, debugger CLI, parser, opcode, or lowering changes are included.

## [v2.2.0-alpha3e] — Release Candidate Metadata, Guard Lowering, and Audit Methodology

### Added
- **Track B.1 source-level guard lowering:** inline guarded memory writes (`memory.write(...) { guard ... }`) lower to `GUARD_ENTER` → `GUARD_CHECK_RESULT` → protected `SYS_MEMORY_WRITE` → `GUARD_EXIT`.
- **Lexical checked effects:** guarded side-effect statements require a local lexical `try/catch(GUARD_VIOLATION)` recovery context. Hidden helper delegation without a local catch is rejected at compile time.
- **Compiler-inserted recovery ACK:** `GUARD_VIOLATION_ACK` is emitted as the first handler instruction and remains unavailable from `.syn` source.
- **Audit methodology v2:** corpus reports now distinguish raw AST fallbacks from `lowerable_to_cvm` nodes and true `runtime_only_fallbacks`.
- **Time-Travel Debugger RFC draft:** replay artifact schema, fork identity, event injection rules, deterministic mock mode, State Diffing & Copy-on-Write semantics, and orphan fork GC are documented for review only.

### Changed
- `LANGUAGE_VERSION`, `RUNTIME_VERSION`, `SPEC_VERSION`, and `HOST_ABI_VERSION` bumped to `2.2.0-alpha3e`.
- Corpus report path updated to `reports/corpus_fallback_alpha3e.json`.
- `corpus_fallback_audit.py` now uses `schema_version = 2` and `routing_model = static_ast_plus_lowering_status_v22`.
- `LANGUAGE_SPEC.md` explicitly marks inline guard lowering as supported and `policy enforce { ... }` block lowering as planned/not implemented.
- `RFC-TIME-TRAVEL-DEBUGGER.md` is marked `Status: DRAFT — Team Review Required. Not approved for implementation.`

### Metrics
- `total_fallback`: 103.
- `runtime_only_fallbacks`: 99.
- `lowerable_to_cvm`: `GovernedMemoryWrite = 4`.
- `corpus_coverage_ratio`: 93.32%.
- Test suite: 484 passed, 1 skipped, 0 failed.

### Known limitations
- Golden Replay Suite is intentionally deferred until after Stable Alpha3e, when the compiler-lowering shape is frozen.
- `policy enforce { ... }` block lowering, `throws GUARD_VIOLATION`, non-throwing guard syntax, Habit Interrupts, Soulprint/Acoustic runtime, and debugger implementation remain out of scope.

## [v2.2.0-alpha3e-track-b] — Guard Blocks in Bytecode

### Added
- **Guard bytecode boundary:** `GUARD_ENTER`, `GUARD_CHECK_RESULT`, `GUARD_EXIT`, and internal-only `GUARD_VIOLATION_ACK` opcodes.
- **Guard runtime state:** `GuardFrame`, `VMState.guard_stack`, and `VMState.guard_violation_active`, with snapshot/restore support.
- **Guard cleanup ranges:** `GuardCleanupRange` and `BytecodeProgram.guard_cleanup_table`; the table participates in `program_hash` for replay correctness.
- **Bridge-side guard enforcement:** side-effecting/unknown host symbols are blocked fail-closed while an unhandled guard violation is active.
- **Forced cleanup path:** real `VMHostError` propagation now closes active guard frames through `_forcibly_close_guard_frames()` and emits `GUARD_FORCIBLY_CLOSED` events.
- **Audit events:** `GUARD_ENTER`, `GUARD_EXIT` with `verdict`, `GUARD_FORCIBLY_CLOSED`, `GUARD_VIOLATION_ACKNOWLEDGED`, and `SIDE_EFFECT_BLOCKED_BY_GUARD`.

### Changed
- `GUARD_CHECK_RESULT` is strict bool-only: `True` passes, `False` fails, every other value raises `VMHostError(code="GUARD_RESULT_TYPE_ERROR")`.
- `GUARD_EXIT` history event now carries `verdict`; the old `GUARD_VERDICT` event name is no longer used.
- `GUARD_VIOLATION_ACK` remains bytecode-internal; source-level `acknowledge_violation()` is rejected at compile time.

### Metrics
- `total_fallback`: 103 → 103.
- `corpus_coverage_ratio`: 93.32% → 93.32%.
- Test suite at checkpoint: 476 passed, 1 skipped, 0 failed.

## [v2.2.0-alpha3e-track-a] — Deterministic LLM / Prompt CVM Bridge

### Added
- **VMBridge handler for `llm.request`** — full dispatch pipeline:
  content-addressable cache lookup → provider call → Bridge-side schema
  validation → `LLM_RESPONSE_CACHED` event in `execution_history`.
- **`PROMPT_BUILD` / `LLM_REQUEST` / `LLM_RESUME` opcodes** — now handled
  by CVM and VMBridge. `PromptExpr` and `LLMCall` are CVM-routable.
- **Content-addressable LLM response cache.**
  `content_key = SHA-256(template_hash || variables_hash || schema_hash ||
  engine_params_hash || model_version)`. `model_version` is mandatory.
- **`llm_cache_invalidation_policy`**: `never` | `model_change` (default) |
  `policy_guard`. Configurable per-agent, overridable by governance policy.
- **Replay-only LLM response lookup.** In replay mode, Bridge looks for
  `LLM_RESPONSE_CACHED` in `execution_history` by `content_key`. Provider is
  never contacted during replay.
- **Bridge-side schema validation.** CVM never sees raw LLM response. Schema
  is validated against `llm_schema_registry` before `resolve_promise()`.
- **Deterministic failure taxonomy** — five failure types, each with a stable
  error code and a deterministic `execution_history` event:
  `LLM_MISSING_MODEL_VERSION`, `CAPABILITY_DENIED`, `LLM_PROVIDER_ERROR`,
  `LLM_TIMEOUT`, `SCHEMA_MISMATCH`, `REPLAY_CACHE_MISS`.
- **`tests/test_cvm_llm_bridge_alpha3e.py`** — 24 tests covering opcodes,
  content_key, cache modes, replay, all failure types, schema validation,
  capability denial, and canonical serialization regression.

### Changed
- `PromptExpr` and `LLMCall` moved from HOST_EVAL fallback to CVM route
  (`CVM_AST_NODE_TYPES_V22` and `CVM_CORE_OPCODES_V22` updated).
- **Capability denial payload expanded** — event now includes
  `capability_missing`, `required_capability`, `agent_capabilities` (sorted
  snapshot), `agent_id`, `request_symbol`, `history_hash`.
  Two events written per denial for security audit layering.
- **Canonical serialization hardened** — all `json.dumps` calls in
  `_hash_transition`, `_hash_resume_transition`, `_payload_hash`,
  `_compute_llm_content_key`, and `variables_hash` computation now use
  `sort_keys=True, separators=(",", ":")` for stable hashes across Python
  versions and dict insertion orders.
- `HOST_ABI_VERSION` bumped to `2.2.0-alpha3e-track-a`: `llm.request` is now
  a full dispatch contract, not just a declared capability symbol.
- All version fields updated: `LANGUAGE_VERSION`, `RUNTIME_VERSION`,
  `SPEC_VERSION` → `2.2.0-alpha3e-track-a`.
- Corpus report renamed:
  `corpus_fallback_alpha3e_p0.json` → `corpus_fallback_alpha3e_track_a.json`.

### Metrics
- `total_fallback`: 132 → 103 (−29 nodes: LLMCall −18, PromptExpr −11)
- `corpus_coverage_ratio`: 91.44% → 93.32% (+1.88 p.p.)
- Test suite: 401 → 425 passed (+24 tests), 1 skipped, 0 failed

### Known limitations
- Real LLM provider adapter is host-owned; `llm_backend=None` uses mock mode.
- Replay requires pre-recorded `LLM_RESPONSE_CACHED` events.
- `synapse run --record` / `synapse replay` CLI not yet implemented
  (planned Track B prep).
- Backtick identifier escape still not implemented (planned Track 0.2).

## [v2.2.0-alpha3e-p0] - Parse Stabilisation & Metadata Sync

### Fixed
- **Parser: 3 examples now parse and execute correctly.**
  - `examples/full_demo.syn`: `RECALL` token was rejected as function name.
  - `examples/math.syn`: `MAX` token was rejected as callable expression.
  - `examples/memory_demo.syn`: `RECALL` token was rejected as function name;
    `PATTERN` token was rejected as parameter name.
- All 44 `.syn` files in `examples/` now pass the corpus parse gate (was 41/44).

### Changed
- **Parser: contextual identifiers (soft keywords).**
  Selected keyword tokens are now accepted as identifiers in name-bearing
  positions (function names, parameter names, member access targets, callable
  expressions). See `LANGUAGE_SPEC.md §A` for the full list and position rules.
  This is an official language feature, not a workaround.
- **Corpus coverage improved: 90.34% → 91.44%.**
  The three newly-parseable files add 382 AST nodes to the corpus (20 new
  fallbacks), raising raw fallback count from 112 to 132 while improving the
  coverage ratio.
- **HOST_ABI_VERSION bumped: `2.2.0-alpha3b2` → `2.2.0-alpha3e-p0`.**
  `MSG_SEND` / `MSG_RECEIVE` opcodes (added in alpha3d5) and
  `STATUS_PAUSED_MESSAGING` / `pending_message_receive` in VMSnapshot
  constitute a VM-visible host-call surface change. The b2 label was stale.
- **All version fields aligned to `2.2.0-alpha3e-p0`.**
  `version.py`, `README.md`, `docs/SPEC.md`, corpus report, golden replay
  JSON fixtures, and test assertions are now consistent.
- **Corpus report renamed:** `corpus_fallback_alpha3d5.json` →
  `corpus_fallback_alpha3e_p0.json`.

### Added
- `scripts/pre_commit_hook.py`: two-gate pre-commit script.
  Gate 0: Python >= 3.10 check. Gate 1: all examples parse. Gate 2: corpus
  coverage >= 0.9143. Install via `make init`.
- `Makefile` with `init` / `test` / `lint` / `audit` targets.
- `LANGUAGE_SPEC.md §A`: Contextual Identifiers (Soft Keywords) —
  full specification of hard vs soft keywords, allowed positions, disallowed
  positions, and tooling / LLM codegen guidance.
- `docs/ARCHITECTURE.md`: Cognitive Primitive Classification table (five
  categories: Pure Computational, Structural Wrapper, Host-mediated
  Deterministic, Runtime Orchestration, Experimental).
- `docs/ARCHITECTURE.md`: HOST_ABI Version history table with rationale.

### Known limitations
- Backtick-quoted identifier escape (for soft keywords in statement-start
  position) is specified but **not yet implemented**. Planned for alpha3e
  Track 0.2.
- Runtime-only primitive compiler diagnostic (note/warning) is not yet
  emitted. Planned for alpha3e Track A prep.
- pre-commit hook must be installed explicitly (`make init`).

### Tests
- `tests/test_version_sync.py`: updated regex and version assertions.
- `tests/test_cvm_alpha3d5_commit3.py`: updated corpus report path, version
  and fallback count assertions; updated corpus report path references.
- `tests/test_corpus_fallback_audit.py`: updated corpus report path.
- `tests/golden_replays_capability/`: all three JSON fixtures updated to
  `host_abi_version: 2.2.0-alpha3e-p0`.

## [v2.2.0-alpha3d3-rfc] - Actor definition structural CVM wrapper RFC

### Added
- Added `docs/RFC-ACTOR-DEF-CVM.md` as the data-driven RFC for structural actor definition wrapping.
- Added RFC contract tests in `tests/test_rfc_actor_def_cvm.py`.

### Decision
- Selected Track 1: Structural Agent Definitions before actor messaging.
- Deferred `SendStmt`, `ReceiveBlock`, and `ReceivePattern` to a separate actor messaging RFC.
- Deferred `HabitStmt` because corpus telemetry shows only 3 fallbacks compared with 29 `AgentDef` fallbacks.

### Notes
- This is an RFC-only patch. It changes no runtime semantics, no actor runtime behavior, no CVM opcodes, and no routing tables.


## [v2.2.0-alpha3d2-s1] - Corpus telemetry sprint

### Added
- Added `scripts/corpus_fallback_audit.py` for static corpus-wide CVM/HOST_EVAL routing telemetry across `.syn` files.
- Added canonical report `reports/corpus_fallback_alpha3d2.json` with fallback distribution, parse status, and corpus coverage metrics.
- Added `tests/test_corpus_fallback_audit.py` to ensure the audit script remains executable and the committed report remains actionable.
- Added `docs/ROADMAP.md` with data-driven prioritization rules before any HabitStmt or cognitive primitive RFC.

### Notes
- This sprint intentionally changes no VM execution semantics, bridge behavior, promise lifecycle, or routing tables.
- The committed report uses static all-AST-node coverage; runtime/taken-path coverage remains reported by `metrics_snapshot()`.


## [v2.2.0-alpha3d2] - Bridge-side promise resolution implementation

### Added
- Implemented Alpha.3-D2 bridge-side `PromiseRecord` lifecycle: create, resolve, reject, and reserved cancel.
- Added actor wake/suspend hooks for promise resolution and rejection.
- Added history-bound promise replay lookup by durable `call_id`.
- Added D2 promise-resolution tests covering lifecycle, actor integration, security gates, replay lookup, and D1 single-pending invariant.

### Preserved
- `HOST_ABI_VERSION` remains `2.2.0-alpha3b2` because D2 adds bridge-side APIs, not new VM-visible host symbols.


## [v2.2.0-alpha3d2-rfc] - Promise resolution RFC amendment
- Added RFC amendment for Alpha.3-D2 bridge-side promise resolution.
- Defined `PromiseRecord` lifecycle: PENDING → RESOLVED / REJECTED, with TIMEOUT and CANCELLED reserved.
- Specified bridge-side promise API: `create_promise()`, `resolve()`, `reject()`, reserved `cancel()`.
- Defined actor-runtime suspend/wake hooks and history-bound promise resolution events.
- Preserved D1 single-pending-call invariant; multiple concurrent VM pending calls are deferred to D3.
- Kept `HOST_ABI_VERSION = "2.2.0-alpha3b2"` unless a VM-visible promise host symbol is introduced.

## [v2.2.0-alpha3d1] - Durable single pending host-call lifecycle
- Added `VMStatus` state machine helpers for RUNNING / PAUSED_HOST_CALL / HALTED.
- Added deterministic `compute_call_id(program_hash, ip, transition_hash, event_id, frame_depth)` for replay-safe host-call identity.
- Upgraded `pending_host_call` to envelope schema v1 with program hash, transition hash, frame depth, agent id, required capabilities, determinism class and HOST_ABI version.
- Added bridge-side `resume_host_call()` with call-id matching, exact capability validation, HOST_ABI validation, FunctionObject argument validation and replay lookup by unique call_id.
- Added dual history separation: nondeterministic host resolutions remain in `execution_history`; deterministic side effects such as `print` are recorded in `side_effect_history`.
- Added two-phase snapshot copy-on-read under `_bridge_lock` to avoid snapshot/resume races without locking host dispatch.
- Added Alpha.3-D1 tests for deterministic call ids, envelope round-trip, resume invariants, replay lookup, security gates, side-effect history and snapshot during pause.


## [v2.1.4-C] - Golden Replay & CVM Boundary Hardening
- Добавлен `tests/golden_replays/` с эталонными сценариями replay/conformance.
- Добавлен `tests/test_golden_replay.py` для проверки actor FIFO, governance rollback signal, affective cooldown, habit fatigue/recovery и VM checkpoint/resume.
- Добавлен `synapse/runtime/vm_routing.py` с явной классификацией CVM/HOST_EVAL boundary.
- `VMBridge` логирует `vm_fallback` для HOST_ABI fallback paths без изменения результата исполнения.
- `metrics_snapshot()` теперь содержит `vm_fallbacks_total` и `vm_coverage_ratio`.
- Документация обновлена схемой RuntimeFacade и таблицей делегирования.

## [v2.1.4] - Runtime Consolidation
- Введён единый источник версий `synapse/version.py`.
- Монолитная спецификация разделена на `SPEC.md`, `CHANGELOG.md`, `ARCHITECTURE.md`, `SEMANTICS.md`.
- Добавлены Operational Semantics Tables для ключевых примитивов.
- Zero semantic changes; regression suite remains authoritative.

## [v2.1.3-C] - Living Habits Execution
- Исполнение `body` из HabitRegistry, energy consumption/refund.
- Recursion lock, `max_habit_depth=3`, fatigue/recovery lifecycle.
- Observer suppression во время исполнения привычек.

<!-- Далее переносится хронология v2.1.3-B → v2.1.0 → v2.0 → v1.9 → ... из LANGUAGE_SPEC.md и SYNAPSE_V2_1_SPEC.md. -->

## [v2.2.0-alpha] - CVM Core: управляющие структуры и функции
- Version authority synchronized to `2.2.0-alpha` / runtime `0.22.0-alpha`.
- `DYNAMIC_OPCODES_V22` renamed to `CVM_CORE_OPCODES_V22` to clarify that v2.2-alpha uses an expanded static core opcode set, not the future dynamic opcode plugin registry.
### Это первый шаг полного CVM компилятора (roadmap v2.2.0).

#### Новые опкоды CVM
**Управляющие:** `JUMP`, `JUMP_IF_FALSE`, `JUMP_IF_TRUE`, `RETURN`, `CALL`, `CALL_HOST`, `CALL_METHOD`, `MAKE_FUNCTION`
**Арифметика:** `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `UNARY_NEG`
**Сравнения:** `EQ`, `NEQ`, `LT`, `GT`, `LTE`, `GTE`
**Логика:** `AND`, `OR`, `NOT` (со short-circuit для AND/OR)
**Структуры:** `BUILD_LIST`, `BUILD_DICT`, `INDEX`, `MEMBER`, `DUP`
**Литералы:** `LOAD_NONE`, `LOAD_TRUE`, `LOAD_FALSE`

#### CognitiveCompiler расширен
Теперь компилирует в CVM без HOST_EVAL fallback:
- `LetStmt`, `AssignStmt`, `ExprStmt`
- `IfStmt` (с backpatching переходов)
- `WhileStmt` (loop/condition компиляция)
- `ForStmt` (итерация через индексный счётчик)
- `FnDef` + `CallExpr` (inline тела функций, CallFrame стек, рекурсия)
- `BinaryExpr`, `UnaryExpr` (включая именованные ops парсера: `eq`, `lt`, `gte` и т.д.)
- `Literal`, `Variable`, `ListExpr`, `DictExpr`, `MemberAccess`
- `AssertStmt`

#### CognitiveVM расширен
- `CallFrame` стек для вложенных вызовов (max depth 64)
- `FunctionObject` — сериализуемый объект функции
- Рекурсия через late-binding locals (fn видит саму себя через frame.locals_snapshot)
- Gas costs обновлены для всех новых опкодов
- `_output` буфер для `CALL_HOST print`
- `transition_hash` включает `stack_top` для детерминированной привязки к данным

#### vm_routing.py расширен
- `CVM_AST_NODE_TYPES_V22` — 22 типа узлов теперь маршрутизируются в CVM (было 2)
- `CVM_CORE_OPCODES_V22` — расширенный статический набор CVM core opcodes
- `classify_ast_node_v22()` и `classify_host_opcode_v22()` для v2.2

#### vm_coverage_ratio
Простые программы (без когнитивных примитивов) теперь дают `vm_coverage_ratio = 1.0`.
Программы с `dream`/`resonate`/`fracture` продолжают использовать HOST_EVAL.

#### Совместимость
- Снапшоты v2.1 (`"version": "2.1"`) принимаются VMSnapshot.from_dict().
- Все 171 существующих теста проходят без изменений.
- `BytecodeProgram.version` = `"2.2"`.

#### Новые тесты
`tests/test_cvm_v22.py` — 55 тестов в 14 категориях:
базовые инструкции, арифметика, сравнения, условия, while, for, функции, структуры данных, gas metering, transition hash, snapshot/restore, vm_routing v2.2, coverage ratio, интеграция с Interpreter.

## [v2.2.0-alpha3d3] - Actor Definition structural wrapper implementation

- Implemented AgentDef/SubAgentDef CVM structural wrappers via ACTOR_ENTER/ACTOR_EXIT.
- Added VMState.actor_stack and CallFrame.actor_stack_snapshot for RAII cleanup.
- Added bridge-dispatched SYS_ACTOR_ENTER/SYS_ACTOR_EXIT parity through actor_runtime.
- Preserved messaging, LLM, policy and HabitStmt for later RFCs.


## v2.2.0-alpha3d4

- Implemented PolicyDef/PolicyRule as structural CVM wrappers.
- Added VMState.policy_stack and CallFrame.policy_stack_snapshot RAII cleanup.
- Added POLICY_ENTER/POLICY_EXIT and POLICY_RULE_ENTER/POLICY_RULE_EXIT opcodes.
- Routed SYS_POLICY_* structural events through VMBridge while keeping governance semantics outside CVM.
- Updated corpus telemetry baseline for alpha3d4.

## v2.2.0-alpha3d5

- Implemented Actor Messaging as the internal mailbox transport substrate.
- Added `MSG_SEND`, `MSG_RECEIVE`, `RECEIVE_ENTER`, and `RECEIVE_EXIT` CVM opcodes.
- Added `STATUS_PAUSED_MESSAGING` and `pending_message_receive` as a separate pause channel from host calls.
- Preserved current `ReceivePattern(sender_var, target_var)` grammar; payload destructuring remains out of scope.
- Routed `SYS_MSG_SEND` / `SYS_MSG_CONSUME` through VMBridge while keeping `actor_runtime` as canonical mailbox authority.
- Updated corpus telemetry baseline to `reports/corpus_fallback_alpha3d5.json` with fallback count 112 and coverage 0.903448.


## [v2.2.0-alpha3e1] — Compiler Guard Lowering groundwork

### Added
- Source-level `try { ... } catch (GUARD_VIOLATION) { ... }` parsing for local guard recovery.
- Track B.1 lexical checked-effect rule in `CognitiveCompiler`: governed side-effect statements such as `memory.write(...) { ... }` compile only inside a local `catch(GUARD_VIOLATION)` recovery context.
- Source-level lowering for governed memory writes into `GUARD_ENTER` → `GUARD_CHECK_RESULT` → protected `SYS_MEMORY_WRITE` → `GUARD_EXIT`, with `guard_cleanup_table` generation.
- Compiler-inserted `GUARD_VIOLATION_ACK` as the first instruction of the `catch(GUARD_VIOLATION)` handler.
- Regression tests for lexical recovery, delegation rejection, guard opcode emission, ACK ordering, and default-passing guarded writes.

### Changed
- `guard` is now accepted as a governed memory field name for compiler-lowered checks.
- Hidden delegation is rejected in Track B.1: helper functions containing governed side effects must provide their own local `catch(GUARD_VIOLATION)` block. Caller-side recovery is not inferred.

### Non-goals
- No `throws GUARD_VIOLATION` function signatures.
- No non-throwing guard syntax.
- No interprocedural checked-effect analysis.
- No golden replay refresh until after the compiler-lowering shape is finalized.

## Golden Replay Suite — alpha3e Stable Alpha3e follow-up

Added:
- Alpha3e golden replay artifact schema and mock replay validator.
- Layer 1 strict golden replay gate and Layer 2 corpus smoke model.
- Deterministic virtual-clock contract for replay artifacts.
- Stable state sanity validation to avoid brittle VMState `__dict__` comparisons.
- `make test-golden` as a separate integration gate.

Security/replay:
- `synapse replay --mock` uses embedded LLM cache entries and must never call a provider.
- Missing LLM cache entries fail deterministically.

## [Alpha3f] — Time-Travel Debugger Core Patch 1

### Added
- Added `synapse.debugger_core` with `ForkRecord`, `ForkRegistry`, `OverlayMap`, `ForkedVMState`, and deterministic replay policy helpers.
- Added isolated tests for fork identity, copy-on-write overlays, fork-local VM state adapters, and no-live-fallback replay errors.

### Constraints
- No VM opcode changes, parser/lowering changes, CLI surface, or global `VMState` rewrite.
- `feature/debugger-core` starts with isolated primitives before integration with the CVM runtime.

## [Alpha3f] — Time-Travel Debugger Core Patch 2: Injection Matrix

### Added
- Added `EventInjectionValidator`, `ValidationResult`, and declarative `INJECTION_POLICY_MATRIX` in `synapse.debugger_core`.
- Added fail-closed governance errors for forbidden debugger event injection types, including `GUARD_VERDICT_OVERRIDE`, `GUARD_VIOLATION_ACK`, capability grants, and program/history hash rewrites.
- Added fork lifecycle operations (`dispose`, `complete`, `fail`, `transition`) with deterministic `ForkDisposedError` and `ForkLifecycleError` behavior.
- Added fork-local `GUARD_ENTER` validation: deterministic replay accepts only recorded guard events; new guard paths require explicit `exploratory-live` forks.

### Constraints
- No CLI surface, REPL, session persistence, parser/opcode changes, or global `VMState` rewrite.

## [Alpha3f] — Time-Travel Debugger Patch 3: CLI Surface

### Added
- Added thin `synapse debug` CLI transport commands for `fork`, `dispose`, `inject-event`, `compare`, and `status`.
- Added stable CLI error-code mapping for transport errors and debugger-core exceptions.
- Added integration tests proving CLI-to-core routing, raw payload forwarding, malformed JSON rejection, error mapping, disposed-fork rejection, and forensic preservation after disposal.

### Constraints
- CLI performs only argument parsing, JSON decoding, stdout/stderr formatting, and exit-code mapping.
- Structural payload validation, governance checks, replay-mode compatibility, and injection policy remain exclusively in `synapse.debugger_core`.
- No REPL, persistence/session snapshot API, daemon mode, parser/opcode changes, `vm_bridge` changes, or global `VMState` rewrite.

## [Alpha3f] — Time-Travel Debugger Patch 3.1: CLI Contract Regression

### Added
- Added explicit regression coverage for `synapse debug fork` defaulting to `deterministic-replay` when `--mode` is omitted.
- Added regression coverage ensuring malformed debug CLI arguments return exit code `1` rather than argparse's default exit code `2`.
- Added regression coverage for debug CLI help as a success path with exit code `0`.

### Changed
- Debug CLI subparsers now use a Synapse-specific `ArgumentParser` subclass that converts parser syntax errors into `CLIArgError`, preserving the stable debugger CLI error-code contract.

### Constraints
- No debugger-core changes, VM/parser/opcode changes, session persistence, REPL, or runtime feature changes.

## [Alpha3f] — Product Clarity Patch P10: End-to-End Trace Compare Tutorial

### Added
- Added `examples/tutorial_trace_compare/baseline.syn` and `modified.syn`, a minimal replay-safe pair that differs by one actor-message payload.
- Added `examples/tutorial_trace_compare/README.md` with runnable commands for record → replay → compare.
- Added `docs/tutorials/TRACE_COMPARE_TUTORIAL.md` with verified command transcripts, factual JSON output, and exit codes for equal (`0`) and divergent (`7`) comparisons.

### Changed
- Linked the tutorial from `README.md`, `docs/DEBUGGER_USER_GUIDE.md`, and `docs/ARCHITECTURE_OVERVIEW.md`.
- Refreshed `reports/corpus_fallback_alpha3e.json` after adding two tutorial `.syn` examples; coverage is now `0.93389` with fallback count `103`.

### Constraints
- Documentation/examples only. No runtime, CLI, replay, debugger-core, parser, opcode, or golden-replay changes.

## [Alpha3g] — RFC-01 Dream Replay Contract Draft

### Added
- Added `docs/RFC-DREAM-REPLAY-CONTRACT.md` as the first Alpha3g planning document.
- The RFC defines the proposed `DreamBlock` recorded-consumption contract, including `dream_key`, `result_hash`, replay behavior, nested LLM handling, and integration-clause interaction.

### Constraints
- Documentation-only planning patch.
- No runtime, interpreter, parser, VM, hash-chain, debugger-core, golden-replay, or CLI behavior changes.
- `DreamBlock` remains Category C / non-strict-golden-safe until a future approved implementation patch satisfies the RFC acceptance criteria.

## [Alpha3g] — Patch 1: Dream Replay Implementation

### Added
- Implemented the approved RFC-01 v2 Dream replay contract using Path A + A2 (`execute_and_verify`).
- Added strict `dream_key` generation with `bound_variables_hash`, `parent_history_hash`, `body_hash`, scenario/config hashes, and runtime version.
- Added `result_hash` recording and verification for `dream_completed` events.
- Added `ReplayIntegrityError` for strict Dream replay integrity failures.
- Added tests for nested LLM replay consumption, result-hash mismatch, dream-key mismatch, and bound-variable identity changes.

### Changed
- `evaluate_dream()` now records strict `dream_completed` metadata in LIVE mode.
- `evaluate_dream()` now executes the dream body in REPLAY mode to preserve the linear history cursor, consumes nested replay events, verifies `dream_completed`, and returns the recorded `event.result`.

### Constraints
- No CVM opcode changes, parser changes, CLI changes, integrate semantics changes, affective/fracture changes, or hash-chain algorithm changes.

## [Alpha3g] — Patch P0.1: Dream Sandbox Hardening

### Added
- Added `DreamSandboxEnvironment`, a strict dream-only environment that shadows all assignments locally and prevents parent-scope write-through.
- Added `canonical_deepcopy()` for Synapse-canonical primitive values (`list`, `dict`, `set`, `tuple`, scalar primitives) used by dream sandbox clone-on-first-read.
- Added `DreamSandboxIsolationError` for unsupported parent-scope objects read inside dream sandbox.
- Added regression tests for dream assignment isolation, list/dict/set mutation isolation, repeated-read clone consistency, parent alias preservation, tuple nested-mutable isolation, unsupported object rejection, and replay parent-scope preservation.

### Changed
- `evaluate_dream()` now executes DreamBlock bodies inside `DreamSandboxEnvironment` instead of a generic child `Environment`.
- Parent-scope mutable containers read from dream bodies are cloned once per original object identity and cached locally, preserving intra-dream aliasing without mutating the parent scope.

### Constraints
- P0.1 only hardens dream sandbox isolation for Synapse-canonical primitive values.
- Custom Python objects, runtime handles, actor references, promises, backend/provider objects, and arbitrary objects remain unsupported inside parent-scope dream access and raise `DreamSandboxIsolationError`.
- No parser, CVM opcode, integrate, actor, stable-identity, CLI, debugger-core, or hash-chain changes.

## [Alpha3g] — P0.2 RFC Drafts: Stable Canonical Identity Skeleton + Integrate Replay Applier

### Added
- Added `docs/RFC-STABLE-CANONICAL-IDENTITY.md` as a parent skeleton contract for canonical bytes, NFC string normalization, typed wrappers for sets/bytes/large integers, NaN/Infinity rejection, `-0.0` normalization, RFC 6901 path escaping, schema-version replay appliers, and canonical genesis state hashing.
- Added `docs/RFC-INTEGRATE-REPLAY-APPLIER.md` as the Alpha3g P0.2 draft for replay-safe `integrate` semantics.
- The integrate RFC defines top-level path-aware `write_set` journaling, `integrate_committed`, `integrate_aborted`, `overlay_summary`, RFC 6901 path escaping, `op=replace/delete`, empty write-set commits, aliasing semantics, static + dynamic nondeterminism barriers, no nested events, schema-version applier registry, and 1 MiB write-set entry limits.

### Constraints
- Documentation-only patch.
- No `evaluate_integrate()` changes, no `StateOverlay` runtime implementation, no interpreter/runtime behavior changes, no parser/opcode/CVM changes, no hash-chain changes, and no CLI changes.
- Runtime code remains locked until the RFCs are reviewed and approved.

## [Alpha3g] — P0.2.3 Dream Strict RFC Errata & Shared Canonicalization Hooks

### Changed
- Updated `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` from v1 to v2 with P0.2.3 errata.
- Clarified the audited `print` path inside `dream`: `env.get()` reaches `DreamSandboxEnvironment`, the sandbox raises `DreamSandboxIsolationError`, `eval_call()` swallows the local `RuntimeError`, and builtin fallback invokes `BUILTINS["print"]` / host stdout.
- Added explicit strict-dream builtin classification and forbidden examples: `print`, `time`, `random`, and `uuid`.
- Added a future replay model comparison table for consume-only, state-delta, recorded subtrace, and hybrid approaches.
- Added strict `dream_completed` invariants for deterministic keys, canonical result hashes, frozen `nested_event_policy`, and future subtrace/state-delta hashes.
- Added shared canonicalization hooks linking Dream eligibility to `RFC-INTEGRATE-REPLAY-APPLIER` and `RFC-STABLE-CANONICAL-IDENTITY`.

### Constraints
- Documentation-only errata patch.
- No `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, hash-chain, or runtime behavior changes.
- `DreamBlock` remains Category B under A2 and excluded from Strict Layer 1.
- Integrate runtime code remains blocked until `RFC-INTEGRATE-REPLAY-APPLIER` is reviewed and approved.

## [Alpha3g] — P0.2.4 RFC-INTEGRATE Structured Review

### Added
- Added `docs/RFC-INTEGRATE-REVIEW-NOTES.md` as the official structured review registry for `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`.
- Classified ten review findings by severity: three BLOCKER items, five MAJOR items, and two MINOR items.
- Added approval guidance requiring INT-01 through INT-03 to be resolved before `RFC-INTEGRATE-REPLAY-APPLIER.md` can move to `APPROVAL-CANDIDATE`.
- Linked the review registry to the shared canonicalization hooks introduced by `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` v2.

### Changed
- Added a P0.2.4 review-status section to `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`, marking the RFC as `NEEDS REVISION — Blockers Open`.

### Constraints
- Documentation-only review patch.
- No `synapse/`, `tests/`, `examples/`, parser, interpreter, CVM, bridge, CLI, actor runtime, replay applier, or runtime behavior changes.
- `evaluate_integrate()`, `StateOverlay`, CVM/opcode work, and stable-identity runtime implementation remain blocked until the revised RFC is approved.
---

## Alpha3g P0.3.2: Integrate Skeleton Hardening & INT-04 Guard — I2.1 HARDENED

- **Patch:** P0.3.2 / I2.1 — Integrate Skeleton Hardening & INT-04 Guard.
- **Status:** I2.1 HARDENED — pre-I3 runtime stabilization.
- **Changed runtime modules:** `synapse/interpreter.py`, `synapse/state_overlay.py`.
- **New tests:** `tests/test_integrate_i2_hardening_p032.py`.
- **Purpose:** harden the opt-in Alpha3g I2 integrate skeleton before any history event emission or base-state application is introduced.
- **Barrier hierarchy:** added `NondeterminismBarrierViolation` as the RFC-level barrier error and made `IntegrateIsolationViolation` its I2-specific subclass.
- **Fail-closed lookup:** narrowed `IntegrateOverlayEnvironment.get()` fallback so only missing overlay/base env bindings delegate to parent functions/agents/builtins; serialization and overlay-state errors are no longer masked as lookup fallback.
- **INT-04 guard:** actor/promise-producing operations remain unreachable inside I2; `spawn` is blocked before actor refs/promises are created, so no orphan promise/resource can be produced by the I2 skeleton. Full resource cleanup registry remains deferred to later implementation stages.
- **Barrier expansion:** nested `integrate`, `fracture`, `collective_dream`, `distributed_consensus`, and `swarm_fracture` are rejected while the I2 skeleton is active.
- **Isolation hardening:** added coverage for parent mutable clone-on-read, intra-transaction alias preservation, no host stdout fallback for `print`, ordinary-exception discard, no-op empty `WriteSet`, and sorted `WriteSet` enforcement.
- **Explicitly out of scope:** no `integrate_committed` / `integrate_aborted` history events, no base-state write-set application, no REPLAY applier, no CVM/opcode work, no actor runtime changes, no full promise cleanup registry, and no agent canonicalization.


## Alpha3g P0.4.1: Stable Canonical Identity Structured Review (doc-only)

- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **Added:** `RFC-STABLE-CANONICAL-IDENTITY-REVIEW-NOTES.md` as the structured review registry for `RFC-STABLE-CANONICAL-IDENTITY.md`.
- **Review status:** RFC remains DRAFT v0.2. Review opened with 10 findings under `RFC-PROCESS.md`.
- **BLOCKER findings:** STABLE-01 canonical time replay source and STABLE-02 builtin allowlist side-effect fail-closed policy.
- **MAJOR findings:** schema/profile version fail-closed behavior, FunctionDescriptor boundary, agent snapshot exclusions, deterministic identity seed domains, local-profile migration, and genesis state alignment.
- **MINOR findings:** testable allowlist acceptance criteria and profile registry lifecycle.
- **Still locked:** Stable Identity runtime, canonical time API, deterministic ID generation, function/agent canonicalization, CVM/opcodes, actor runtime, and storage migration.

## Alpha3g P0.4.3: Stable Canonical Identity Team Verification & Approval Gate (doc-only)

- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **RFC status:** `RFC-STABLE-CANONICAL-IDENTITY.md` approved as v1.0.
- **Verified findings:** STABLE-01 canonical time replay source, STABLE-02 builtin allowlist fail-closed policy, and STABLE-03 schema/profile fail-closed version behavior moved to `VERIFIED` after team review.
- **Deferred implementation gates:** STABLE-04 FunctionDescriptor, STABLE-05 AgentSnapshot, STABLE-06 deterministic identity seed domains, STABLE-07 local-profile migration, and STABLE-08 genesis alignment remain deferred gates for future scoped implementation patches.
- **Acknowledged v1 boundaries:** STABLE-09 acceptance criteria and STABLE-10 profile registry lifecycle remain acknowledged governance / planning boundaries.
- **Cross-checks:** existing Alpha3g Integrate Category B artifacts remain valid under local profiles; new stable canonical work must target `stable-canonical.v1` unless explicitly scoped as compatibility work.
- **Still locked:** no Stable Identity runtime, canonical time API, deterministic ID generation, function/agent canonicalization, CVM/opcode, actor runtime, or storage migration changes are included in this patch.

## Alpha3g P0.4.6: Stable Canonical Integration Service & Migration Analysis (SI3)

- **Patch:** P0.4.6 — Stable Canonical Integration Service & Migration Analysis.
- **Scope:** isolated integration-service runtime module + tests + migration checklist update.
- **Added:** `synapse/canonical_service.py` as an anti-corruption layer between the standalone `stable-canonical.v1` core and future runtime consumers.
- **Added tests:** `tests/test_stable_canonical_service_p046.py` covering stable delegation, fail-closed unsupported values, drift categories, representative Integrate-shaped values, and import isolation.
- **Migration analysis:** added `compare_profile_hashes()` and `ProfileHashComparison` for measuring drift between `alpha3g.local-json.v1` and `stable-canonical.v1` without switching any consumer.
- **Consumer impact:** none. `state_overlay.py`, `interpreter.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, and existing hash paths remain untouched.
- **Next gate:** StateOverlay migration remains blocked until drift categories are mapped and an explicit profile selector / compatibility boundary is approved.

## Alpha3g P0.4.7: Stable Canonical Drift Baseline & Migration Report (SI4-prep)

- **Patch:** P0.4.7 — Stable Canonical Drift Baseline & Migration Report.
- **Scope:** read-only migration analysis + tests + documentation. No consumer migration.
- **Added:** `tests/test_stable_canonical_drift_report_p047.py` to generate temporary Integrate golden artifact snapshots from the existing I6 scenario corpus, read their `history.json` payloads, and compare `alpha3g.local-json.v1` against `stable-canonical.v1` through `compare_profile_hashes()`.
- **Added:** `docs/MIGRATION-DRIFT-REPORT.md` as the SI4-prep gate artifact.
- **Observed result:** 14 / 14 analyzed Integrate Category B payload fragments classified as `drift_category = none`; breaking drift found: 0; rejected payloads: 0; unexplained hash drift: 0.
- **Checklist:** updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark StateOverlay migration as `READY FOR FLAGGED MIGRATION`, with explicit requirements that SI4 use a profile selector / compatibility boundary and preserve the legacy default.
- **Consumer impact:** none. `state_overlay.py`, `interpreter.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, existing hash paths, and stored fixtures remain untouched.

## Alpha3g P0.4.8 — StateOverlay Stable Canonical Profile Selector (SI4)

- **Status:** COMPLETED — feature-flagged StateOverlay migration.
- **Scope:** `StateOverlay` profile selector + dual-profile tests + migration checklist update.
- **Changed runtime module:** `synapse/state_overlay.py`.
- **Default behavior:** unchanged. `StateOverlay()` still uses `alpha3g.local-json.v1` unless a profile is explicitly supplied.
- **Stable opt-in:** `StateOverlay(..., profile="stable-canonical.v1")` delegates value hashing and canonical write-set value construction through the Stable Canonical service boundary.
- **Write-set metadata:** stable-profile write-set entries include `value_profile: "stable-canonical.v1"`; legacy entries preserve their historical serialized shape.
- **Tests:** dual-profile coverage verifies dirty detection, canonical hashes, typed wrappers, stable set ordering, unsupported-profile rejection, and legacy compatibility.
- **DENY in this patch:** no `interpreter.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, hard switch, fixture rewrite, or Integrate hash-path migration.

## Alpha3g P0.4.9 — Integrate Stable Canonical Drift Analysis (SI5-prep)

- **Patch:** P0.4.9 — Integrate Stable Canonical Drift Analysis.
- **Scope:** read-only migration analysis + tests + documentation. No consumer migration.
- **Added test:** `tests/test_integrate_stable_canonical_drift_p049.py` to generate temporary Integrate artifact snapshots from the current I6 corpus and compare hash/event-path payload fragments under `alpha3g.local-json.v1` and `stable-canonical.v1`.
- **Added report:** `docs/INTEGRATE-MIGRATION-DRIFT-REPORT.md` as the SI5-prep gate artifact.
- **Observed result:** 28 / 28 analyzed Integrate hash/event-path fragments classified as `drift_category = none`; breaking drift found: 0; rejected payloads: 0; unexplained hash drift: 0.
- **Checklist:** updated `docs/MIGRATION-READINESS-CHECKLIST.md` to mark Integrate hash path migration as `READY FOR FLAGGED MIGRATION`, while keeping hard switch and default-profile changes forbidden.
- **Consumer impact:** none. `interpreter.py`, `evaluate_integrate()`, `state_overlay.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, existing hash paths, and stored fixtures remain untouched.

## Alpha3g P0.5.1: Agent Canonicalization RFC Revision & Blocker Strategy (doc-only)

- **Patch:** P0.5.1 — Agent Canonicalization RFC Revision & Blocker Strategy.
- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **RFC revision:** `RFC-AGENT-CANONICALIZATION.md` moved to DRAFT v0.2 with explicit v1 static-definition boundaries.
- **AGENT-01:** resolved by deterministic `agent_id = sha256(stable-canonical.v1(agent_id_seed))`, using `parent_event_hash` or `genesis_config_hash`, assigned/recorded/replay-consumed `spawn_nonce`, alias non-uniqueness semantics, and fail-closed `AgentIdCollisionError`.
- **AGENT-03:** resolved by `alpha3g.memory_ref.v1` with stable `memory_space_id`, canonical `memory_key`, declared `access_mode`, address-only dereference boundary, and fail-closed `MemoryRefNotResolvedError`.
- **AGENT-02:** split to prerequisite `RFC-FUNCTION-DESCRIPTOR`; Python bytecode, `inspect.getsource()`, `__code__`, host source paths, dynamic/closure definitions, and ad-hoc AST hashes remain forbidden as canonical identity sources.
- **Capability Grants:** clarified as mandatory attenuation; runtime must reject undeclared tool calls even when a live host tool object exists.
- **AGENT-11:** added as deferred MAJOR implementation gate for schema/profile registry and `UnknownSchemaVersionError` fail-closed behavior.
- **Still locked:** no changes to `synapse/`, `tests/`, `interpreter.py`, `actor_runtime.py`, `builtins.py`, `memory.py`, CVM/opcodes, golden fixtures, AgentSnapshot runtime, FunctionDescriptor runtime, canonical time, or deterministic IDs.

## Alpha3g P0.5.2: Function Descriptor RFC Draft (doc-only)

- **Patch:** P0.5.2 — Function Descriptor RFC Draft.
- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **Added RFC:** `docs/RFC-FUNCTION-DESCRIPTOR.md` as the prerequisite design contract for AGENT-02 / executable agent identity.
- **Two-tier model:** FunctionDescriptor v1 defines declarative contract identity only; executable body identity is explicitly deferred to a future canonical AST / CVM image contract.
- **Descriptor schema:** defines `function_descriptor_hash = sha256(stable-canonical.v1(function_descriptor))` using namespace, symbol, declared version, input/output schema hashes, capability schema hash, effect policy hash, dependency manifest hash, and captured environment manifest hash.
- **Forensic guardrails:** explicitly forbids Python bytecode, `inspect.getsource()`, `__code__`, runtime AST parsing of host source, host file paths, `repr(function)`, closure cells, wall-clock, UUIDs, and runtime object identity as canonical identity sources.
- **Policy hooks:** adds canonical schema hashing, dependency manifest pinning, captured environment fail-closed boundaries, effect-policy alignment with nondeterminism barriers, and the bridge to `agent_definition_ref` manifests.
- **Added review registry:** `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md` with FUNC-01..FUNC-10 findings and an approval gate.
- **Still locked:** no changes to `synapse/`, `tests/`, `interpreter.py`, `actor_runtime.py`, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AST/CVM normalizer, AgentSnapshot runtime, canonical time, or deterministic IDs.

## Alpha3g P0.5.2.1: Function Descriptor RFC Revision & Blocker Closure (doc-only)

- **Patch:** P0.5.2.1 — Function Descriptor RFC Revision & Blocker Closure.
- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **RFC revision:** `RFC-FUNCTION-DESCRIPTOR.md` moved to DRAFT v0.2 with author-level blocker resolutions applied; independent verification remains required.
- **FUNC-01:** resolved by explicit `captured_environment_manifest` schema, deterministic binding ordering, required empty-manifest canonical form, valid/invalid binding examples, and fail-closed rejection of runtime closure/object state.
- **FUNC-02:** resolved by explicit `effect_policy` schema, `nondeterminism_barrier_class` enum, registered effect namespace vocabulary, runtime enforcement pseudocode, and fail-closed barrier violation semantics.
- **FUNC-03:** deferred as a dependency-manifest / schema-registry implementation gate after adding deterministic `ref_type` taxonomy, cryptographic `ref_hash` pinning, weak-pin restrictions, and host-path/live-module exclusions.
- **FUNC-04:** deferred as a schema/profile registry implementation gate after clarifying semver/hash behavior, explicit compatibility registry decisions, and fail-closed unknown schema/profile handling.
- **Still locked:** no changes to `synapse/`, `tests/`, `interpreter.py`, `actor_runtime.py`, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AST/CVM normalizer, AgentSnapshot runtime, canonical time, or deterministic IDs.

## Alpha3g P0.5.2.2: Function Descriptor RFC Independent Verification & Approval-Candidate Transition (doc-only)

- **Patch:** P0.5.2.2 — Function Descriptor RFC Independent Verification & Approval-Candidate Transition.
- **Scope:** documentation/process only. Zero runtime/code/test changes.
- **RFC status:** `RFC-FUNCTION-DESCRIPTOR.md` moved to `APPROVAL-CANDIDATE v0.2-AC`; final team vote remains required before `APPROVED`.
- **FUNC-01:** independently VERIFIED — captured environment manifest is explicit, fail-closed, stable-canonical.v1 serializable, and blocks implicit closures/runtime object bindings.
- **FUNC-02:** independently VERIFIED — effect policy schema uses explicit barrier enums, registered effect vocabulary, context strictness rules, and fail-closed nondeterminism barrier semantics aligned with Integrate/Dream contracts.
- **FUNC-03/FUNC-04:** remain DEFERRED as implementation/schema-registry gates.
- **Still locked:** no changes to `synapse/`, `tests/`, `interpreter.py`, `actor_runtime.py`, CVM/opcodes, golden fixtures, FunctionDescriptor runtime, AST/CVM normalizer, AgentSnapshot runtime, canonical time API, or deterministic IDs.


## Alpha3g P0.5.7 — AgentSnapshot Runtime Gate Closure & Drift Report

- **Patch:** P0.5.7 — AgentSnapshot Runtime Gate Closure & Drift Report.
- **Status:** COMPLETED — GO for P0.5.8 standalone AgentSnapshot schema/value core under local fail-closed schema allowlist.
- **Scope:** documentation + one read-only canary test. No AgentSnapshot runtime code.
- **New report:** `docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md` records the field-drift result and gate interpretation for `FUNC-03`, `FUNC-04`, and `AGENT-11`.
- **Updated planning/audit docs:** `AGENTSNAPSHOT-RUNTIME-PLAN.md` and `AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md` now explicitly authorize only P0.5.8 standalone schema/value core, not integration or deployment.
- **Canary test:** `tests/test_agentsnapshot_canary_p057.py` verifies that legacy `AgentRuntime.to_dict()` remains structurally distinct from the canonical AgentSnapshot allowlist.
- **Gate interpretation:** `FUNC-03` does not block standalone AgentSnapshot value objects carrying approved descriptor refs; `FUNC-04` and `AGENT-11` require a local fail-closed schema/profile allowlist before standalone code and remain blocking for deployment/integration.
- **Still locked:** no changes to `synapse/agent_snapshot.py`, `interpreter.py`, `actor_runtime.py`, `builtins.py`, memory backends, CVM/opcodes, golden fixtures, FunctionDescriptor runtime registry, central schema registry, or AgentRuntime serialization.

## Alpha3g P0.5.8 — AgentSnapshot Standalone Schema/Value Core

- **Patch:** P0.5.8 — AgentSnapshot Standalone Schema/Value Core.
- **Status:** COMPLETED — standalone AgentSnapshot value objects implemented under the P0.5.7 local fail-closed schema/profile allowlist.
- **Scope:** new isolated module + unit tests + documentation updates. No runtime integration.
- **New module:** `synapse/agent_snapshot.py` defines standalone value objects and validators for `AgentDefinitionRef`, `AgentIdSeed`, `MemoryRef`, `CapabilityGrant`, and `AgentSnapshot`.
- **Fail-closed guards:** unknown schema/profile ids, invalid memory access modes, runtime-envelope fields, extra snapshot fields, unsupported stable-canonical values, and malformed hash fields raise explicit AgentSnapshot errors.
- **Serialization:** snapshot payloads use only the approved v1 allowlist and hash through `stable-canonical.v1`; legacy `AgentRuntime.to_dict()` remains non-canonical and untouched.
- **Tests:** `tests/test_agentsnapshot_core_p058.py` adds standalone schema/value coverage for allowlist, round-trip, hashing, unknown schema/profile rejection, runtime-envelope rejection, unsupported value rejection, and memory access-mode rejection.
- **Still locked:** `AgentRuntime.to_dict()` migration, `actor_runtime.py`, `interpreter.py`, `builtins.py`, memory dereference, CVM/opcodes, Integrate/Dream paths, golden fixtures, FunctionDescriptor runtime registry, and central schema registry.
