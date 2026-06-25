# P3C Evidence — Canonical Consensus Replay Consumption

## Status

P3c-0 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-2 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-N1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c Ticket Lifecycle POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-N2 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

Capability for distributed consensus remains Partial.

Production distributed consensus protocol behavior is NOT claimed.

Overall P3c remains open.

## P3c-0 Evidence Closure — Canonical Consensus Replay Consumption

### Implementation Reference

- PR number: #34
- Implementation merge commit: 16fdd5fb209a9ab387359888bf1952571cfe8fba
- Implementation head commit: 9a37d13fa8415df5bb93953516f6392ae2de98ad
- Approval-gate PR: #35
- Approval-gate merge commit: 5569aae1bb7fdeeccb87ce21b1daf46b7d6c9724
- Approved RFC content SHA: df3fb680e3fa6e4f24100966e409cfc12f35f7d9

### Scope Closed

P3c-0 closes local replay consumption for `distributed_consensus_decided`, fail-closed replay mismatch handling, non-duplication of live events during replay, and engine-owned replay reduction through recorded votes.

### Test Results

- Targeted P3c: 17 passed
- P3a + P3b regression: 52 passed
- Collective regression: 8 passed
- Full Linux suite: 1571 passed, 12 skipped, 0 failed
- `compileall synapse`: passed
- `git diff --check`: passed
- new_failures = []

### Capability Impact

Distributed consensus capability extends to:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

## P3c-1 Evidence Closure — Durable Ticket Creation and Replay

### Implementation Reference

- PR number: #39
- Implementation base SHA: 46d85b168a6661a401793dd9b31d6d15b5d79bac
- Implementation head commit before merge: 299793bf2b005d9e71afb1b5df37219a2d8afe8a
- Implementation merge commit: 88210654223b19a52bfddf9f3715e1a95af90367
- Approved RFC content SHA: a44df8dddd32c0bbacd4ce2ae8b2678728083e16
- Approval record content SHA: ef8e965fa2fb5b762aabeb4411c008684b2496b5

### Scope Closed

P3c-1 closes deterministic durable ticket creation and replay anchoring for deferred consensus with `reason = pending_missing_votes`: deterministic `ticket_id`, adjacent `distributed_consensus_decided` / `distributed_consensus_ticket_created`, raw-adjacent replay consumption, fail-closed ticket schema validation, replay cursor rollback, and `consensus_tickets` projection.

### Changed Files

PR #39 changed exactly:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `tests/test_consensus_adapter_p3a.py`
- `tests/test_consensus_replay_p3c.py`

No docs, RFC, matrix, evidence, parser, AST, lexer, workflows, examples, or durable allowlist file was touched in the implementation PR.

### Test Results

- `python -m compileall synapse tests`: passed
- Focused P3c replay: 49 passed
- P3 regression suite: 101 passed
- Consensus selection: 105 passed, 1510 deselected
- Full suite: 1596 passed, 13 skipped, 6 known Windows / Git-filesystem failures
- `git diff --check`: passed
- new consensus failures = []

### Capability Impact

Distributed consensus capability extends to:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

## P3c-2 Evidence Closure — Durable Consensus Ticket Resolution

### Implementation Reference

- PR number: #45
- Implementation base SHA: 9e62118ef5b033e68e4bd5ad02d2fb7b5a5c6aeb
- Implementation head commit before merge: 56f3cc854d874edcd27cff126ccdaccad238a983
- Implementation merge commit: c5b129711ef76f919f263ac4dc6d35637890a347
- Approved RFC content SHA: 20e859633a6e835b67cae50464f2ed9667cd4b1b
- Approval record: `docs/RFC-CONSENSUS-P3C2_APPROVAL.md`

### Scope Closed

P3c-2 closes durable consensus ticket resolution through the existing P2 `SuspendExpr` / `awaiting_external_signal` resume boundary: strict request/signal validation, engine-owned final vote merge/counts/outcome/reason/hashes, closed-schema `distributed_consensus_ticket_resolved`, pending -> resolved projection, duplicate idempotency, conflicting duplicate rejection, and replay validation with cursor/projection rollback.

### Changed Files

PR #45 changed exactly:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `synapse/runtime/consensus_ticket_resolution.py`
- `tests/test_consensus_resolution_p3c2.py`

No docs, RFC, matrix, evidence, parser, AST, lexer, workflows, examples, `synapse/application.py`, P2 artifact schema, or durable suspension-reason file was touched in the implementation PR.

### Test Results

- Focused P3c-2 resolution: 23 passed
- P3 regression suite: 101 passed
- P2 durable regressions: 77 passed, 1 skipped
- Consensus selection: 128 passed, 1510 deselected
- Full suite: 1619 passed, 13 skipped, 6 known Windows / Git-filesystem failures
- Independent Linux full suite: 1626 passed, 12 skipped, 0 failed
- new consensus failures = []

### Capability Impact

Distributed consensus capability extends to:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

## P3c-N1 Evidence Closure — Pending-ticket Import and Local Mailbox Vote Response Collection

### Implementation Reference

- PR number: #58
- Implementation base SHA: dd1037010c17449a2cc9852aedc1517ef3023701
- Implementation head commit before merge: 3e94af25376cd8d6d25b56b321fc8be0a37c611e
- Implementation merge commit: a9497aa26b4450f40a541e16b6260129d36bb4f2
- Approved contract bundle:
  - `docs/RFC-CONSENSUS-P3CN1.md`
  - `docs/RFC-CONSENSUS-P3CN1_APPROVAL.md`
  - `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_AMENDMENT.md`
  - `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_APPROVAL.md`

### Scope Closed

P3c-N1 closes the approved local pending-ticket import and mailbox vote response collection slice: strict import validation, `vote_counts` / `votes_hash` recomputation, deterministic `ticket_import_hash`, durable `distributed_consensus_ticket_imported`, import idempotency/conflict policy, replay reconstruction, strict `consensus_vote_response` validation, participant binding, duplicate policy, `distributed_consensus_vote_received`, full-coverage-only terminal reduction, and generic non-consensus receive preservation.

P3c-N1 closes only pending-ticket import plus local mailbox-backed vote response collection. It does not close fresh durable `DistributedConsensusStmt` execution, vote request delivery, network or daemon transport, automatic timeout/scheduler behavior, persistent inbox behavior, parser/AST/lexer expansion, production distributed consensus protocol behavior, or overall P3c closure.

### Changed Files

PR #58 changed exactly:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_mailbox_collection.py`
- `tests/test_consensus_mailbox_collection_p3cn.py`

No `ConsensusEngine`, `actor_runtime.py`, `application.py`, parser, AST, lexer, network, daemon, timer, scheduler, persistent inbox, artifact schema, `_REPLAY_STATE_KEYS`, matrix, or evidence file was touched in the implementation PR.

### Test Results

- Focused P3c-N1 collection: 43 passed
- P3c-2 regression: 23 passed
- P2 mailbox wait regression: 16 passed
- Consensus/mailbox/P3c/P2 durable selector: 281 passed, 1 skipped, 1415 deselected
- `git diff --check`: passed
- new failures = []

### Capability Impact

Distributed consensus capability extends to:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

## P3c Ticket Lifecycle Evidence Closure — Terminal Cancel / Expire and Replay Integrity

### Implementation Reference

- PR number: #61
- Implementation base SHA: 66c52a70e16e8d238681fe82e8e820eb6236133b
- Implementation head commit before merge: 71feec6610c19defc3c7b1efad28ebbc822d8a2b
- Implementation merge commit: 8ff834bdeebd195ad7689af5c2137b04792b3025
- Approved contract bundle:
  - `docs/RFC-CONSENSUS-P3C-TICKET-LIFECYCLE.md`
  - `docs/RFC-CONSENSUS-P3C-TICKET-LIFECYCLE_APPROVAL.md`

### Scope Closed

P3c Ticket Lifecycle closes the approved terminal cancel/expire and replay-integrity lifecycle scope:

- lifecycle command extraction from mailbox messages
- strict lifecycle command validation
- lifecycle terminal event construction
- lifecycle terminal event validation
- deterministic lifecycle action hash validation
- pending -> cancelled projection transition
- pending -> expired projection transition
- lifecycle-specific command/event error taxonomy
- exact duplicate terminal-action idempotency
- cancel/expire terminal conflict rejection in both orderings
- rejection of cancel/expire for non-existing tickets
- rejection of cancel/expire for already resolved tickets
- fail-closed replay behavior for missing/malformed/mismatched/out-of-order terminal lifecycle events
- replay cursor and projection preservation on lifecycle replay failure
- post-terminal rejection for vote-response, import, collection creation, and collection update mutation paths
- preservation of generic non-lifecycle durable mailbox replay errors as generic durable mailbox RuntimeError

P3c Ticket Lifecycle closes only the approved internal cancel/expire and replay-integrity lifecycle scope.
It does not close fresh `DistributedConsensusStmt` mailbox-backed request delivery, network/daemon transport, automatic timeout/scheduler behavior, persistent inbox behavior, public ticket API, parser/AST/lexer expansion, production distributed consensus protocol behavior, or overall P3c closure.

### Closed Defects

- P3C_LIFECYCLE_ERROR_TAXONOMY_DEFECT
- P3C_LIFECYCLE_REPLAY_DEFECT

### Changed Files

PR #61 changed exactly:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_mailbox_collection.py`
- `synapse/runtime/consensus_ticket_resolution.py`
- `tests/test_consensus_ticket_lifecycle_p3c.py`

No docs, RFC, matrix, evidence, parser, AST, lexer, workflows, examples, dependency, config, network, daemon, scheduler, timer, or durable schema file was touched in the implementation PR.

### Acceptance Evidence

| Area | Evidence |
|---|---|
| A | non-existing cancel/expire rejection |
| B | resolved-ticket cancel/expire rejection |
| C | cancel→expire and expire→cancel mailbox conflicts |
| D | same action identity with different terminal semantics |
| E | missing terminal replay event fails closed |
| F | malformed terminal replay event fails closed |
| G | mismatched terminal replay event fails closed |
| H | post-terminal vote-response/import rejection |
| I | cancelled/expired collection creation and update rejection |

### Test Results

- Baseline lifecycle: 24 passed
- Final lifecycle: 40 passed
- P3c-N1 mailbox collection: 43 passed
- P3c-2 resolution: 23 passed
- P2 durable mailbox wait: 16 passed
- `git diff --check`: passed
- Full suite: 1718 passed, 13 skipped, 6 known Windows / Git-filesystem failures
- new lifecycle failures = []

### Capability Impact

Distributed consensus capability extends to:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed; P3c Ticket Lifecycle terminal cancel/expire and replay integrity closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

## P3c-N2 Evidence Closure — Fresh DistributedConsensusStmt Mailbox Vote Request Delivery and Initial Collection

### Implementation Reference

- stage: P3c-N2
- status: CLOSED
- implementation status: MERGED
- evidence status: PASS
- PR number: #64
- Implementation branch: `p3cn2-fresh-mailbox-impl`
- Implementation base SHA: 448c2040f6979a654e215a5b388530fec86278b6
- Implementation head commit before merge: 0975af20446e48694e490825c1886b66bac0db95
- Implementation merge commit: 83db81ec3e41226406009df194dec320632cb3f2
- Post-merge main SHA: 83db81ec3e41226406009df194dec320632cb3f2
- Approved RFC source: `docs/RFC-CONSENSUS-P3CN2.md`
- Program governance: Synapse Runtime Capability Integrity Program ТЗ v3.0

### Requirement Traceability

Requirement IDs:

- REQ-CONSENSUS-01
- REQ-HISTORY-INTEGRITY-01
- REQ-CAPABILITY-SIGNAL-01
- REQ-CROSS-NODE-01

Traceability anchors:

- DEPTH-CONSENSUS-01
- DEPTH-CROSS-NODE-BOUNDARY-01
- DEPTH-ASYNC-EXECUTION-01
- DEPTH-GOVERNANCE-PROOF-01

### Implementation Scope Evidence

The implementation changed only:

- `synapse/application.py`
- `synapse/interpreter.py`
- `synapse/runtime/consensus_mailbox_collection.py`
- `synapse/runtime/consensus_vote_request_delivery.py`
- `tests/test_consensus_fresh_mailbox_p3cn2.py`

The implementation did not change:

- `synapse/runtime/actor_runtime.py`
- `synapse/runtime/consensus_engine.py`
- `synapse/ast.py`
- `synapse/parser.py`
- `synapse/lexer.py`
- network / daemon / timer / scheduler files
- dependency / config files
- `docs/evidence/P3C_EVIDENCE.md`
- `docs/CAPABILITY_MATURITY_MATRIX.md`
- `docs/RFC-CONSENSUS-P3CN2.md`

### Runtime Behavior Evidence

Verified behavior:

- fresh `DistributedConsensusStmt` can create deterministic P3c-N2 vote request projection
- `distributed_consensus_vote_requested` event is emitted per missing participant
- `consensus_vote_request` mailbox message is delivered only after local route precheck
- `resolve_actor_location(receiver) == "local"` is checked before `send_message`
- non-local participant delivery fails closed
- `request_batch_id` is deterministic
- `request_id` is deterministic
- `request_hash` is deterministic
- `proposal_view_hash` is deterministic
- request/id/hash preimages use `canonical_json`
- `hash_event_chain` is not used for request identity or request hash computation
- local `history_hash` fields are not used for P3c-N2 request identity
- replay consumes existing request events
- replay does not re-send mailbox messages
- replay reconstructs `_consensus_vote_requests`
- replay mismatches raise the existing `ConsensusReplayIntegrityError`
- imported P3c-N1 vote response compatibility remains intact
- terminal tickets reject P3c-N2 request delivery and fresh-path response collection
- `ConsensusEngine` vote mathematics was not changed
- `ActorRuntime` was not changed

### Test Evidence

Implementation-run evidence reported:

- `tests/test_consensus_fresh_mailbox_p3cn2.py`: 22 passed
- required regression set:
  - `tests/test_consensus_mailbox_collection_p3cn.py`: 43 passed
  - `tests/test_consensus_resolution_p3c2.py`: 23 passed
  - `tests/test_consensus_ticket_lifecycle_p3c.py`: 40 passed
  - `tests/test_durable_mailbox_wait.py`: 16 passed
- discovered distributed consensus modules: 109 passed
- Windows full regression: 1740 passed, 13 skipped, 6 known Windows/Git platform failures
- no new P3c-N2 failures

Reviewer-run evidence reported:

- P3c-N2: 22 passed
- P3c-N1 + lifecycle + resolution + durable: 122 passed
- full Linux regression: 1747 passed, 12 skipped, 0 failed

Windows failures remain recorded as known Windows/Git platform baseline failures only; they are not hidden by this evidence closure.

### Non-Claims

P3c-N2 closure does not claim:

- full REQ-CONSENSUS-01 closure
- full content-sensitive consensus semantics
- production distributed consensus protocol behavior
- network vote delivery
- daemon vote delivery
- remote participant vote delivery
- parser/AST/lexer expansion
- public ticket API
- production transport behavior
- overall P3 closure
- any capability outside the approved P3c-N2 contract

### Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed; P3c Ticket Lifecycle terminal cancel/expire and replay integrity closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed; P3c Ticket Lifecycle terminal cancel/expire and replay integrity closed; P3c-N2 fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

### Closure Statement

P3c-N2 is CLOSED because the approved implementation is merged, the canonical runtime path is verified, replay behavior is verified, fresh request/response binding is verified, P3c-N1 compatibility is preserved, protected boundaries are respected, no forbidden files were changed, no new regressions were reported, and post-merge evidence has been recorded.

This closure does not state that full production distributed consensus is complete.

This closure does not state that full content-sensitive consensus is complete.

## Next Allowed Work

The following future stages remain blocked behind their own RFC and approval gates and are not authorized by this evidence closure:

- P3d — LLM-assisted voting
- future RFC — network/daemon vote transport
- future RFC — production distributed consensus protocol claims
- future RFC — parser/AST/lexer vote syntax
- future RFC — public ticket API or external lifecycle control surface beyond the approved internal command path
- future RFC — automatic rebinding of original deferred consensus variables
