# Approval Record — RFC-CONSENSUS-P3C2

**RFC:** `docs/RFC-CONSENSUS-P3C2.md`  
**Stage:** P3c-2 approval gate  
**Approval status:** APPROVED FOR P3c-2 IMPLEMENTATION AFTER THIS APPROVAL PR IS MERGED  
**Repository mutation:** DOCUMENTATION APPROVAL RECORD ONLY  
**Runtime code changes:** NOT INCLUDED  
**Test changes:** NOT INCLUDED  
**Capability matrix changes:** NOT INCLUDED  
**Evidence changes:** NOT INCLUDED

---

## 1. Approved RFC Content

The approved RFC text is the current draft in `docs/RFC-CONSENSUS-P3C2.md`.

```text
APPROVED_RFC_PATH: docs/RFC-CONSENSUS-P3C2.md
APPROVED_RFC_CONTENT_SHA: 20e859633a6e835b67cae50464f2ed9667cd4b1b
```

The RFC content is approved as written in `docs/RFC-CONSENSUS-P3C2.md` at the approved content SHA above.

This approval record does not rewrite, shorten, simplify, or reinterpret the approved RFC text.

---

## 2. Product Owner Approval

```text
PRODUCT_OWNER_DECISION: APPROVED
APPROVED_STAGE: P3c-2 — Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
APPROVAL_SCOPE: RFC-CONSENSUS-P3C2 implementation authorization after approval merge
IMPLEMENTATION_AUTHORIZATION: AUTHORIZED ONLY AFTER THIS APPROVAL PR IS MERGED
```

This approval confirms that P3c-2 may proceed to an implementation PR after this approval PR is merged.

Implementation must follow the approved RFC exactly.

---

## 3. Implementation Base SHA

```text
P3C2_IMPLEMENTATION_BASE_SHA: <approval merge SHA after this PR is merged>
```

The implementation branch must be based on the merge commit of this approval PR.

An implementation PR opened before this approval PR is merged is not authorized.

---

## 4. Approved Scope

P3c-2 implementation is authorized only for the approved RFC scope:

```text
Durable Consensus Ticket Resolution via Existing P2 Resume Boundary
```

The implementation may implement:

```text
- strict consensus_ticket_resolution request recognition through the existing SuspendExpr request channel
- strict consensus_ticket_resolution signal recognition through the existing P2 resume signal channel
- trusted ticket_id selection from request_value rather than injected signal authority
- validation that the injected signal targets the pending ticket selected by the trusted request_value
- validation that all previously missing participants provide final votes
- validation that resolution votes contain only allowed final vote states
- validation that extra, missing, malformed, or non-mapping vote payloads fail closed
- ConsensusEngine-owned final consensus computation for resolved tickets
- distributed_consensus_ticket_resolved durable event creation
- consensus_tickets projection update for resolved tickets
- deterministic replay consumption of distributed_consensus_ticket_resolved
- replay fail-closed behavior for malformed, missing, mismatched, or out-of-order resolution events
- compatibility-preserving regression tests explicitly required by the approved RFC
```

---

## 5. Explicit Non-Authorization

This approval does not authorize:

```text
- mailbox-backed vote delivery
- network-backed vote transport
- daemon-backed vote transport
- live large language model vote production
- parser expansion
- abstract syntax tree expansion
- lexer expansion
- public ticket inspection API
- ticket cancellation
- ticket expiration
- ticket lifecycle status field in durable event
- automatic re-binding of program variables after resolution
- P2 contract expansion
- new P2 suspension reason
- change to SuspendExpr payload shape
- change to P2 artifact schema
- change to P2 exit code mapping
- modification of _SUPPORTED_SUSPENSION_REASONS
- production distributed consensus protocol behavior
- Raft semantics
- Paxos semantics
- Tendermint semantics
- PBFT semantics
- Byzantine fault tolerance
- leader election
- view-change protocol
- network replication
- overall P3c closure
```

Any implementation requiring one of the above items must stop and return the corresponding blocked status under the approved RFC stop-gates.

---

## 6. Capability Statement

This approval does not update the capability matrix.

After implementation merge and evidence closure, the expected capability wording becomes:

```text
Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution closed via existing P2 resume boundary
```

This approval does not claim:

```text
P3c closed
ticket lifecycle closed
distributed consensus complete
Production distributed consensus
```

---

## 7. Evidence Requirement

Implementation alone will not close P3c-2 evidence.

After implementation merge, a separate evidence PR is required before the capability matrix may be updated.

The evidence PR must verify the implementation against the approved RFC and must remain documentation-only unless separately authorized.

Evidence must include:

```text
- final implementation merge commit SHA
- approved RFC content SHA
- implementation base SHA
- changed file list
- verification commands
- focused P3c-2 test results
- P3 regression test results
- relevant durable resume compatibility results
- full suite result or explicitly approved substitute evidence
- known baseline failures, if any
- new failure assessment
- final review verdict
```

---

## 8. Approval Verdict

```text
RFC-CONSENSUS-P3C2: APPROVED FOR IMPLEMENTATION AFTER APPROVAL MERGE
OVERALL P3C: OPEN
P3C-2 EVIDENCE: NOT CLOSED
P3C-2 IMPLEMENTATION: NOT YET MERGED
```
