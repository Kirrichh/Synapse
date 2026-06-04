# AS2 Operator Runbook Draft

Status: **DRAFT — blocking for production activation**

This runbook describes the first operator procedures for AS2 Poison Pill, `STALE_IN_PROGRESS`, audit chain, projection failure, idempotency outage, and rollback incidents.

---

## 1. Scope

This runbook applies to the AS2 production materialization track after the P0.6.35–P0.6.39 skeleton/readiness sequence.

It is not an operator RPC implementation. It is an operational decision guide and evidence checklist.

---

## 2. Incident classification

| Classification | Meaning | Retry policy |
| --- | --- | --- |
| `safe_to_retry` | No side effect is known to have occurred, and idempotency/audit evidence permits retry. | Manual or controlled retry allowed. |
| `terminal_requires_operator` | State is terminal, ambiguous, poisoned, or audit evidence is incomplete. | No automatic retry. |
| `compensation_required` | Side effect may have occurred before completion/failure was persisted. | Do not retry until compensation decision. |
| `infrastructure_fail_closed` | Store, audit, or projection dependency unavailable. | Stop path; investigate dependency. |

---

## 3. Poison Pill response

Trigger:

```text
same correlation_id + different prepared_inputs_hash
```

Automatic system behavior:

```text
1. Mark the affected idempotency record terminal FAILED(reason_code=POISON_PILL).
2. Block all further projection attempts for the affected correlation_id.
3. Do not retry automatically.
4. Preserve audit/idempotency evidence for investigation.
```

Required security/operator alert fields:

```text
correlation_id
agent_id, if available
original prepared_inputs_hash
conflicting prepared_inputs_hash
audit event id / record hash
provider failure context, if available
request_id, if available
operator-visible timestamp
```

Escalation rule:

```text
Escalate to security team if N Poison Pill incidents are observed from the same agent_id within time window T.
N and T must be configured before production activation.
```

Agent-level hold/quarantine:

```text
Do not automatically block the whole agent_id on a single Poison Pill.
Full agent-level hold/quarantine requires explicit operator/security decision based on evidence.
```

Operator evidence checklist:

```text
□ Confirm original and conflicting prepared_inputs_hash values.
□ Confirm whether the conflict is caused by client retry bug, upstream id reuse, provider drift, serialization drift, or malicious replay.
□ Inspect audit chain continuity for the affected correlation_id.
□ Inspect provider payload evidence if retained by approved surfaces.
□ Decide whether the event remains correlation-scoped or requires agent-scoped hold.
□ Record final operator decision and owner.
```

---

## 4. STALE_IN_PROGRESS response

Trigger:

```text
IN_PROGRESS record exceeds configured TTL and is marked STALE_IN_PROGRESS.
```

Default behavior:

```text
No automatic retry.
No automatic completion.
Operator review required.
```

Operator steps:

```text
1. Inspect idempotency record state and timestamps.
2. Inspect audit chain for reservation and any later transition event.
3. Inspect projection handoff evidence, if available.
4. Classify the case:
   - safe_to_retry
   - terminal_requires_operator
   - compensation_required
5. If safe_to_retry, use approved manual retry procedure after creating an operator note.
6. If ambiguous, keep terminal/manual status and escalate.
```

Clock rule:

```text
Use monotonic/business clock evidence for TTL and stale detection.
Use wall-clock only for operator-readable audit metadata and observability.
```

---

## 5. Audit chain break investigation

Trigger:

```text
audit previous_state_hash mismatch
missing expected audit transition
record transition without corresponding audit evidence
```

Operator steps:

```text
1. Stop automatic processing for affected correlation_id.
2. Capture outbox sequence window around the failure.
3. Verify CHAIN_START / previous_state_hash continuity.
4. Compare idempotency record state with latest audit event.
5. Classify as infrastructure_fail_closed unless proven safe.
6. Escalate to platform owner and security owner.
```

---

## 6. Projection failure handling

Default behavior:

```text
Projection failure maps to idempotency FAILED or CANCELLED according to approved contract.
No automatic replay if side-effect status is unknown.
```

Operator checklist:

```text
□ Determine whether projection side effect occurred.
□ Determine whether idempotency completion/failure transition was persisted.
□ Determine whether audit append succeeded.
□ If side effect occurred but terminal state did not persist, classify as compensation_required.
□ Record compensation decision before any retry.
```

---

## 7. Idempotency store unavailable

Default behavior:

```text
fail-closed
no projection execution
no state mutation
```

Operator steps:

```text
1. Confirm store availability and backend health.
2. Confirm no projection was executed without reservation.
3. Restore backend or switch to approved safe-disable mode.
4. Do not bypass idempotency protection in production.
```

---

## 8. Rollback / safe-disable plan

Production activation must provide a feature-flag or wiring rollback procedure. Until then, production activation remains locked.

Draft rollback requirements:

```text
□ Disable future production AS2 hook.
□ Revert to old/default runtime behavior.
□ Stop new projection handoff submissions.
□ Preserve idempotency and audit evidence for in-flight requests.
□ Route new requests to safe fallback/NoOp path if approved.
□ Declare whether in-flight requests are safe_to_retry, terminal_requires_operator, or compensation_required.
□ Assign incident owner and rollback approver.
```

---

## 9. Escalation matrix

| Incident | Primary owner | Escalation |
| --- | --- | --- |
| Poison Pill | Security/platform operator | Security engineering |
| STALE_IN_PROGRESS | Platform operator | Runtime owner |
| Audit chain break | Platform owner | Security + data integrity owner |
| Idempotency store outage | Storage/platform owner | Incident commander |
| Projection side-effect ambiguity | Runtime owner | Product/security owner |

---

## 10. Production activation status

This runbook is a draft and must be approved before production `ENABLED` activation.
