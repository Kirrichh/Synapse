# RFC Process & Review Registry Governance

**Status:** APPROVED — Alpha3g P0.2.5 process baseline  
**Patch:** Alpha3g P0.2.5  
**Scope:** RFC lifecycle, review registry governance, dependency policy, and implementation locks  
**Runtime scope authorized:** none — documentation only

This document governs how RFCs, review notes, findings, dependency gates, and
implementation locks are managed in this repository. It defines process only. It
MUST NOT be used to define runtime behavior, syntax, event schemas, or replay
semantics; those belong in the technical RFCs themselves.

The immediate reason for this process RFC is `RFC-INTEGRATE-REVIEW-NOTES.md`,
which introduced the first formal review registry with BLOCKER / MAJOR / MINOR
findings. Before findings can be closed, the team needs one shared definition of
what `RESOLVED`, `VERIFIED`, and `APPROVAL-CANDIDATE` mean.

---

## 1. RFC status lifecycle

RFC documents use the following finite-state machine:

```text
DRAFT
  -> NEEDS REVISION
  -> APPROVAL-CANDIDATE
  -> APPROVED
  -> DEPRECATED
  -> SUPERSEDED
  -> ARCHIVED
  -> REJECTED
```

### 1.1 Status definitions

| Status | Meaning |
|---|---|
| `DRAFT` | Initial proposal. Technical direction is not yet accepted. Runtime implementation is blocked unless an explicit PoC exception is granted. |
| `NEEDS REVISION` | Review found issues that must be addressed before approval. BLOCKER findings prevent advancement. |
| `APPROVAL-CANDIDATE` | All BLOCKER findings are `VERIFIED`; the RFC is ready for final team vote or approval. |
| `APPROVED` | The RFC is the active product contract for its scope. Implementation may begin only within the approved scope. |
| `DEPRECATED` | The RFC is still historically relevant and may still describe deployed code, but it is no longer the recommended design direction. No full replacement exists yet. |
| `SUPERSEDED` | A newer RFC fully replaces this RFC. The replacement MUST be named. |
| `ARCHIVED` | The RFC is retained as historical material only. It is not an active contract. |
| `REJECTED` | The RFC was reviewed and intentionally declined. |

### 1.2 Allowed transitions

```text
DRAFT -> NEEDS REVISION
DRAFT -> APPROVAL-CANDIDATE
DRAFT -> REJECTED

NEEDS REVISION -> APPROVAL-CANDIDATE
NEEDS REVISION -> REJECTED
NEEDS REVISION -> ARCHIVED

APPROVAL-CANDIDATE -> APPROVED
APPROVAL-CANDIDATE -> NEEDS REVISION     # new blocker found during final review
APPROVAL-CANDIDATE -> REJECTED

APPROVED -> DEPRECATED
APPROVED -> SUPERSEDED
APPROVED -> ARCHIVED

DEPRECATED -> SUPERSEDED
DEPRECATED -> ARCHIVED
```

There is no valid state named `almost approved`, `soft approved`, or `approved
except for blockers`.

---

## 2. Finding severity

Review findings use three severity levels.

| Severity | Meaning | Gate effect |
|---|---|---|
| `BLOCKER` | The RFC cannot be approved without resolution. Leaving the issue open would create undefined behavior, broken determinism, unsafe replay, or an unimplementable contract. | Blocks `APPROVAL-CANDIDATE`. |
| `MAJOR` | The issue must be addressed before runtime implementation merges. It may be explicitly deferred for RFC approval only with written justification and risk acceptance. | Does not automatically block `APPROVAL-CANDIDATE`, but blocks implementation merge unless deferred. |
| `MINOR` | Clarification, maintainability, portability, or future-compatibility issue. | Does not block approval by itself. |

Impact categories SHOULD be recorded for every `MAJOR` and `MINOR` finding:

```text
DETERMINISM
SECURITY
MAINTAINABILITY
PORTABILITY
FORENSICS
PERFORMANCE
OPERABILITY
```

---

## 3. Finding status lifecycle

Each finding in a review registry uses this lifecycle:

```text
OPEN -> RESOLVED -> VERIFIED
  ^                   |
  |                   v
  +----- REOPENED <---+
```

| Status | Meaning |
|---|---|
| `OPEN` | The issue is active and not yet addressed. |
| `RESOLVED` | The RFC author or assigned owner has updated the RFC or written a resolution plan that claims to address the issue. |
| `VERIFIED` | An independent reviewer has confirmed that the resolution closes the issue according to this process. |
| `REOPENED` | A previously resolved or verified issue was found incomplete, regressed, or invalidated by new evidence. |

### 3.1 Transition authority

| Transition | Allowed actor |
|---|---|
| `OPEN -> RESOLVED` | RFC author or assigned owner. |
| `RESOLVED -> VERIFIED` | Independent reviewer; MUST NOT be the same person who authored the resolution. |
| `RESOLVED -> REOPENED` | Any reviewer or team member with evidence. |
| `VERIFIED -> REOPENED` | Any reviewer or team member with evidence. |

Self-verification is forbidden. The author of a resolution MAY explain why a
finding should be closed, but MUST NOT mark their own resolution `VERIFIED`.

---

## 4. BLOCKER resolution process

A `BLOCKER` finding is closed only through the following process:

1. The RFC author revises the target RFC section or adds an explicit resolution
   section.
2. The review registry finding is updated with:
   - `Status: RESOLVED`;
   - a short `Resolution summary`;
   - exact links or section references to the revised RFC text;
   - rationale for why the blocker is closed;
   - any remaining implementation requirements.
3. An independent reviewer verifies the revised text.
4. The finding changes to `VERIFIED` only after reviewer sign-off.
5. The RFC may move to `APPROVAL-CANDIDATE` only when all associated BLOCKER
   findings are `VERIFIED`.

If a reviewer becomes unavailable, the finding does not auto-verify. The team
MAY assign a replacement reviewer. If the author and reviewer disagree, the
finding is escalated to team review or the RFC shepherd. Strict veto does not
apply indefinitely without written rationale.

---

## 5. MAJOR deferral contract

A `MAJOR` finding may be deferred past RFC approval only if the review registry
records all of the following:

```text
Deferral: accepted
Justification: <why approval can proceed safely>
Target milestone: <version or TBD with rationale>
Risk if ignored: <determinism/security/maintainability/etc.>
Implementation gate: <what is blocked until resolved>
Approver: <reviewer/team/s RFC shepherd>
```

Deferred `MAJOR` findings block runtime implementation merge for the affected
scope unless the deferral explicitly says otherwise. A deferred `MAJOR` becomes a
`BLOCKER` if its target milestone is missed and no new deferral is approved.

---

## 6. MINOR handling

`MINOR` findings SHOULD record:

```text
Impact if ignored
Suggested mitigation
Future milestone, if known
```

A `MINOR` finding does not block RFC approval, but it remains visible in the
review registry until closed, deferred, or archived with the RFC.

---

## 7. Review notes vs RFC source of truth

RFC documents and review notes have different roles.

| Artifact | Role |
|---|---|
| `docs/RFC-*.md` | Product artifact. It is the source of truth for approved technical behavior. |
| `docs/*-REVIEW-NOTES.md` | Process artifact. It is the audit trail of findings, discussions, and resolutions. |

When an RFC reaches `APPROVED`, all accepted resolutions that affect technical
behavior MUST be reflected in the RFC itself. Review notes remain as historical
audit trail, not as the normative behavioral contract.

After approval, review notes SHOULD be frozen or explicitly marked archived. A
later change to the technical behavior requires a new RFC revision, errata RFC,
or reopening of the associated finding.

---

## 8. Dependency graph rules

Every RFC that depends on another RFC MUST declare dependencies in its header or
status section.

Dependency entries SHOULD include:

```text
RFC name
required status: APPROVED or APPROVAL-CANDIDATE
schema_version or RFC version, if applicable
reason for dependency
```

An RFC MUST NOT move to `APPROVED` if a required dependency is only `DRAFT` or
`NEEDS REVISION`, unless the dependency is explicitly marked non-blocking and the
risk is accepted in the review registry.

If a dependency publishes a breaking revision, dependent RFCs MUST be reviewed
for compatibility. A dependent RFC MAY pin to a specific dependency version, for
example:

```text
Depends on: RFC-STABLE-CANONICAL-IDENTITY v1.0 or later, non-breaking changes only
```

---

## 9. Cross-RFC ID format

Findings and requirements use local, stable prefixes:

```text
INT-01      Integrate RFC review finding
DREAM-01    Dream RFC finding or requirement
STABLE-01   Stable Canonical Identity finding or requirement
PROC-01     RFC process finding or requirement
```

Cross-RFC references SHOULD use:

```text
Related IDs: INT-01, DREAM-05, STABLE-03
```

If a finding title or number changes, the review registry MUST preserve a note
that maps the old ID to the new ID. A global `SYN-*` registry is not required for
Alpha3g, but future tooling may introduce one.

---

## 10. Implementation lock: no code before APPROVED

Runtime implementation MUST NOT begin for an RFC-controlled feature until the
relevant RFC reaches `APPROVED`.

This lock applies to:

```text
synapse/
tests/ that assert new runtime behavior
parser/runtime behavior changes
CVM/opcode changes
bridge/host ABI changes
CLI behavior changes
golden/replay behavior changes
```

Documentation-only patches, review notes, and planning gate updates are allowed
while the RFC is in `DRAFT` or `NEEDS REVISION`.

### 10.1 Proof-of-concept exception

A proof-of-concept may be allowed only under all of these constraints:

- It lives outside production runtime paths, for example under `experiments/` or
  a non-merged branch.
- It does not change `synapse/`, `tests/`, `examples/`, golden fixtures, or CLI
  behavior in the main prototype archive.
- It is not used as production behavior or as an approved test oracle.
- Its purpose is to validate feasibility for a reviewer.
- The RFC or review notes explicitly mark it as non-normative.

A PoC does not by itself close a finding. The normative contract must still be
written in the RFC and verified through the review process.

---

## 11. Meta-RFC approval rule

This RFC-PROCESS document is a meta-RFC. The initial Alpha3g P0.2.5 version is
accepted by the ad-hoc team consensus that authorized the process-governance
patch. Future changes to this process document SHOULD follow the process defined
here.

If the process itself blocks an urgent correction, the team may use unanimous
consent or an explicitly named process shepherd to authorize a one-time process
errata. That exception MUST be recorded in `CHANGELOG.md` and the planning gate.

---

## 12. Reviewer checklist

A reviewer verifying a finding SHOULD check:

1. Is the target RFC text actually revised, not merely discussed in review notes?
2. Does the revised text define observable behavior, failure mode, and boundary
   conditions?
3. Are dependency RFCs named and status-compatible?
4. Are canonicalization, determinism, and replay implications explicit?
5. Is the resolution fail-closed on unsupported cases?
6. Is implementation still locked until RFC status permits it?
7. Has the finding status been changed by the correct actor?

---

## 13. Current Alpha3g application

This process applies immediately to:

```text
RFC-INTEGRATE-REPLAY-APPLIER.md
RFC-INTEGRATE-REVIEW-NOTES.md
RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md
RFC-STABLE-CANONICAL-IDENTITY.md
```

For Alpha3g P0.2.5 specifically:

- `RFC-INTEGRATE-REVIEW-NOTES.md` is updated to add finding statuses and process
  metadata.
- `RFC-INTEGRATE-REPLAY-APPLIER.md` remains unchanged in P0.2.5; blocker
  resolutions are intentionally deferred to P0.2.6.
- Integrate runtime code remains blocked.
