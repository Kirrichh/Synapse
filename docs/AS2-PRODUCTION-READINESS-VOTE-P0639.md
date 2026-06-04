# AS2 Production ENABLED Readiness Vote — P0.6.39

Status: **READINESS_ACCEPTED_BUT_PRODUCTION_LOCKED**

Patch class: readiness / governance / audit gate.

P0.6.39 records the executable evidence produced by P0.6.35 through P0.6.38 and separates readiness acceptance from production activation. This patch does **not** enable production behavior and does **not** change the default runtime path.

---

## 1. Scope

P0.6.39 verifies and documents that the AS2 skeleton track has produced enough executable evidence to begin the production materialization track.

Covered evidence:

| Milestone | Evidence |
| --- | --- |
| P0.6.35 | `OutboxAuditSink` skeleton, envelope/payload split, chain validation, bounded queue behavior. |
| P0.6.36 | `PersistentIdempotencyStore` skeleton, conditional transitions, Poison Pill, STALE, audit-first rollback. |
| P0.6.37 | `ProviderAggregator` skeleton, provider output normalization, enum tie-breaker, Failure Priority Matrix. |
| P0.6.38 | `IntegrationHarness` skeleton, end-to-end controlled pipeline under `ENABLED_FOR_TEST`. |

---

## 2. Non-goals

P0.6.39 intentionally does not perform any production activation.

Locked in this milestone:

```text
production ENABLED activation
as2_runtime_wiring.py default-path changes
as2_projection_handoff.py dedup-index replacement
backend vendor selection
real persistent storage implementation
audit relay implementation
real provider adapters
network/file/DB/queue I/O
operator RPC implementation
automatic STALE_IN_PROGRESS retry
concurrent provider execution
schema migration
production rollout
```

---

## 3. Readiness checklist

| Area | Designed | Implemented | Evidence | Status |
| --- | --- | --- | --- | --- |
| Audit outbox skeleton | ✓ | ✓ | P0.6.35 tests + boundary guards | accepted |
| Idempotency store skeleton | ✓ | ✓ | P0.6.36 tests + boundary guards | accepted |
| Provider aggregator skeleton | ✓ | ✓ | P0.6.37 tests + boundary guards | accepted |
| Integration harness skeleton | ✓ | ✓ | P0.6.38 tests + boundary guards | accepted |
| Golden replay fixture handling | ✓ | partial | collection-safe documented skip when fixtures absent | non-blocking readiness debt |
| Backend vendor decision | ✓ | no | status acknowledged as OPEN | blocking for production activation |
| Audit relay ADR | ✓ | draft only | `docs/AS2-AUDIT-RELAY-ADR.md` | blocking for production activation |
| Operator runbook | ✓ | draft only | `docs/AS2-OPERATOR-RUNBOOK-DRAFT.md` | blocking for production activation |
| Rollback plan | ✓ | draft only | runbook rollback section | blocking for production activation |
| SLO targets | ✓ | draft only | `docs/AS2-SLO-TARGETS-DRAFT.md` | blocking for production activation |
| Runtime production wiring | planned | no | explicitly locked | blocking for production activation |

---

## 4. Test evidence

Current expected verification status after P0.6.39:

```text
full pytest collection must complete
all AS2 boundary guards must remain green
all skipped tests must be explicit and documented
no hidden xfail or unexplained skip is accepted
```

The golden replay fixture gap is now documented by a module-level skip in `tests/test_golden_replay.py` when no JSON replay fixtures are present. This is accepted only as **non-blocking readiness debt**, not as production replay readiness.

---

## 5. Boundary guard evidence

P0.6.39 preserves all previous AS2 boundary guard expectations and adds the readiness constraint that the P0.6.38 integration harness remains sequential and does not import concurrency primitives.

The integration harness remains forbidden from importing:

```text
AgentRuntime / Environment / interpreter / actor_runtime
real I/O drivers
pathlib / time
threading / asyncio / concurrent.futures / multiprocessing
as2_runtime_wiring
GateController mutation surfaces
Audit sink concrete implementations
```

---

## 6. Blocking items for production activation

The following items block production `ENABLED`, even though P0.6.39 readiness is accepted:

1. **Backend vendor ADR**
   - Status: OPEN.
   - Required: durable backend decision for idempotency state and audit linkage.
   - Required backend capability: atomic compare-and-swap reservation and conditional update by expected state.

2. **Audit relay ADR**
   - Status: OPEN / draft.
   - Required: delivery model, retry policy, idempotent event key, backpressure behavior, external sink strategy.

3. **Operator runbook approval**
   - Status: DRAFT.
   - Required: Poison Pill, STALE_IN_PROGRESS, audit chain break, rollback, and escalation procedures.

4. **SLO and observability targets**
   - Status: DRAFT.
   - Required: RED metrics, alert thresholds, dashboard ownership, event visibility.

5. **Runtime activation patch**
   - Status: NOT STARTED.
   - Required: separate patch after readiness acceptance; must not be mixed with this vote.

6. **Golden replay production readiness**
   - Status: DOCUMENTED DEBT.
   - Required: real fixtures or explicit production replay gate before production activation.

---

## 7. Non-blocking acknowledged items

These are acknowledged and deferred. They do not invalidate P0.6.39, but they should be tracked before production activation or early production hardening:

```text
chaos/failure injection for side-effect-before-completion failure
clock contract ADR: monotonic clock vs wall-clock metadata
IntegrationDuplicate result_ref contract strengthening
distributed lock / CAS verification in selected backend
concurrent provider execution RFC
operator RPC design
```

---

## 8. Poison Pill readiness decision

P0.6.39 adopts the following operator-facing policy:

```text
Automatic: terminally block the affected correlation_id.
Automatic: emit security/operator alert with correlation_id and conflicting hashes.
Not automatic: agent-wide block.
Agent-level hold/quarantine requires explicit operator/security decision based on evidence.
```

This avoids allowing one toxic request to automatically paralyze legitimate operations for the whole agent while still treating the event as a security-significant signal.

---

## 9. Outcome

```text
READINESS_ACCEPTED_BUT_PRODUCTION_LOCKED
```

Meaning:

```text
The AS2 architecture, skeleton implementations, integration harness, and boundary evidence are accepted as ready for the production materialization track.

Production activation remains locked until backend, audit relay, runbook approval, SLO/observability, rollback, and activation-patch requirements are satisfied.
```


## P0.6.40 Backend Vendor ADR Status

- **Status:** `IN PROGRESS / DECISION REQUIRED`.
- **Document:** `docs/AS2-BACKEND-VENDOR-ADR.md`.
- **Decision scope:** Persistent Idempotency Store backend and transactional audit outbox linkage.
- **Current blocker:** awaiting infra/platform answers for Q8/Q9/Q10:
  - Is PostgreSQL operationally available and approved for AS2 persistent state?
  - Is Redis approved as durable storage, not only cache?
  - Is the external audit sink chosen?
- **Production status:** production `ENABLED` remains locked until backend ADR acceptance, backend materialization, audit relay ADR/implementation, runbook approval, golden replay readiness, rollback plan, SLO/observability approval, and runtime activation design.

