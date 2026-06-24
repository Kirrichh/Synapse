# RFC-CONSENSUS-P3CN2 — Fresh DistributedConsensusStmt Mailbox Vote Request Delivery and Initial Collection

**Status:** DRAFT  
**Stage:** P3c-N2 RFC  
**Program:** Synapse Runtime Capability Integrity Program  
**Program ТЗ version:** 3.0  
**Program BASE_SHA:** `398753d48a5c742d9dcd695451a6b5d6d9f82943`  
**RFC TARGET_SHA:** `85abb6357b2540c3e33722e8b318ae37e8c4fa2e`  
**Repository mutation:** DOCUMENTATION RFC DRAFT ONLY  
**Implementation status:** NOT AUTHORIZED  
**Approval status:** NOT APPROVED FOR IMPLEMENTATION  
**Evidence status:** NOT STARTED  
**Capability status after RFC draft:** RFC_REQUIRED  
**Target capability:** Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection  
**Production distributed consensus protocol status:** NOT CLAIMED  
**Network / daemon delivery status:** NOT IN SCOPE  
**Remote participant delivery status:** NOT IN SCOPE  
**Parser / AST / lexer status:** NOT IN SCOPE  
**P3c-N1 status:** CLOSED / POST_MERGE_ACCEPTED / EVIDENCE CLOSED  
**P3c Ticket Lifecycle status:** CLOSED / POST_MERGE_ACCEPTED / EVIDENCE CLOSED

---

## 0. Product Statement

After P3c-N2 implementation, a fresh DistributedConsensusStmt that produces a pending consensus ticket because participant votes are missing can create deterministic per-participant mailbox vote requests, deliver those requests to local mailbox-capable participants, track request identity, bind later mailbox vote responses to prior requests, and replay that request/response path without re-sending messages or weakening history integrity.

---

## 1. Requirement IDs

Primary requirement:

REQ-CONSENSUS-01 — содержательный consensus

Supporting requirements:

REQ-HISTORY-INTEGRITY-01 — корректное понимание history hash
REQ-CAPABILITY-SIGNAL-01 — честная сигнализация
REQ-CROSS-NODE-01 — runtime/transport boundary

Traceability anchors:

DEPTH-CONSENSUS-01
DEPTH-CROSS-NODE-BOUNDARY-01
DEPTH-ASYNC-EXECUTION-01
DEPTH-GOVERNANCE-PROOF-01

---

## 2. Purpose

This RFC defines the approved design contract for P3c-N2 — Fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection.

P3c-N2 exists because the current runtime can create deterministic consensus decisions and pending consensus tickets, and P3c-N1 can consume mailbox-delivered vote responses for existing pending tickets, but the runtime still does not create or deliver mailbox vote requests from a fresh DistributedConsensusStmt.

P3c-N2 adds the missing request-delivery layer between initial deferred consensus/ticket creation and mailbox-backed response collection.

P3c-N2 does not replace ConsensusEngine.

P3c-N2 does not claim production distributed consensus protocol behavior.

P3c-N2 is a single unified RFC. Separate RFC/amendment stages are not required unless a hard code contradiction is discovered before approval.

Implementation remains blocked until an approval document explicitly approves this RFC.
