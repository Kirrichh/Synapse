# P3C Evidence — Canonical Consensus Replay Consumption

## Status

P3c-0 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-2 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c-N1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

P3c Ticket Lifecycle POST_MERGE_ACCEPTED / EVIDENCE CLOSED

Capability for distributed consensus remains Partial.

Production distributed consensus protocol behavior is NOT claimed.

Overall P3c remains open.

## Implementation Reference

- PR number: #34
- Implementation merge commit: 16fdd5fb209a9ab387359888bf1952571cfe8fba
- Implementation head commit: 9a37d13fa8415df5bb93953516f6392ae2de98ad
- Approval-gate PR: #35
- Approval-gate merge commit: 5569aae1bb7fdeeccb87ce21b1daf46b7d6c9724
- Approved RFC content SHA: df3fb680e3fa6e4f24100966e409cfc12f35f7d9
- RFC draft PR: #33
- RFC draft merge commit: ee2b462d40f8e00023af02b6eda7c972710fc970

## Merge Facts

PR #34 was merged after being rebased onto the approval-gate merge SHA `5569aae1bb7fdeeccb87ce21b1daf46b7d6c9724`.

This satisfies the rebase mandate recorded in the RFC-CONSENSUS-P3C Approval Record.

The rebase produced zero content drift versus the pre-rebase head. The post-rebase implementation head `9a37d13fa8415df5bb93953516f6392ae2de98ad` is the same implementation artifact on the approved base.

This evidence closes only the P3c-0 replay consumption slice. Overall P3c remains open.

## Changed Files

The merged implementation changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `tests/test_consensus_adapter_p3a.py` — compatibility-preserving P3a regression contract update only, authorized by RFC-CONSENSUS-P3C §22 Implementation PR file list
- `tests/test_consensus_replay_p3c.py`

## Scope Closed

P3c-0 canonical consensus replay consumption closes the following local durable/replay behavior:

- LIVE consensus emission now writes schema_version `consensus.event.v2` carrying a normalized votes map.
- REPLAY consumes one matching `distributed_consensus_decided` event before the replay frontier.
- REPLAY fails closed on mismatch with stable `ConsensusReplayIntegrityError` messages.
- REPLAY does not call live `VoteSource`.
- REPLAY does not call `ActorMethodVoteSource`.
- REPLAY does not call any actor `consensus_vote` method.
- REPLAY does not append a duplicate event.
- REPLAY does not mutate side-effect stores.
- REPLAY uses recorded votes through an engine-owned deterministic reducer path: `ExplicitVoteSource(recorded_votes)` → `ConsensusEngine.decide`.
- REPLAY verifies primary integrity anchors `proposal_id`, `statement_identity`, `votes_hash`, and `result_hash`.
- History exhaustion preserves the existing frontier-to-LIVE behavior.
- Replay mismatch fails closed only before the frontier.
- Source labels remain provenance-only and do not enter `votes_hash`, preserving hash equivalence between live actor-method collection and replay via recorded votes.

P3c-0 does not close mailbox, promise, signal, ticket, daemon, network, or production distributed consensus lifecycle behavior.

## Explicit Non-Claims

P3c evidence does not claim or implement:

- mailbox-backed vote request delivery or fresh `DistributedConsensusStmt` mailbox flow
- daemon-backed vote collection
- network-backed vote collection
- DurablePromise-backed vote completion
- signal-injected vote resolution beyond the already closed P3c-2 ticket-resolution slice
- await/suspend vote collection beyond the already closed P3c-N1 local pending-ticket response collection
- stateful consensus ticket lifecycle beyond P3c-1 ticket creation/replay, P3c-2 resolution, P3c-N1 imported pending-ticket projection, and P3c Ticket Lifecycle terminal cancel/expire/replay-integrity scope
- live LLM vote production
- durable allowlist expansion outside approved slices
- event v1 migration / silent upgrade
- parser/AST/lexer expansion
- production distributed consensus protocol behavior
- Raft / Paxos / Tendermint / PBFT semantics
- Byzantine fault tolerance
- leader election
- view-change protocol
- network replication
- overall P3c closure

## Architecture Evidence

- `ConsensusEngine` remains the single owner of semantic consensus mathematics, hash construction, and result/event shape construction.
- The interpreter adapter does not manually rebuild public result shape during REPLAY.
- The replay branch is engine-owned: it constructs `replay_request = replace(request, vote_source=ExplicitVoteSource(recorded_votes))` and calls `ConsensusEngine.decide(replay_request)`.
- The replay branch uses `peek_next_history_event()` to classify without advancing, preserving frontier-to-LIVE behavior.
- The replay branch advances `replay_cursor` exactly once when consuming a matching event.
- `ConsensusReplayIntegrityError` subclasses `ReplayIntegrityError` to preserve compatibility with the existing replay integrity error family.
- `ConsensusValidationError` raised while reducing recorded votes is translated to `ConsensusReplayIntegrityError`.
- Source labels are not part of the `votes_hash` preimage, which is why replay through `ExplicitVoteSource` remains hash-equivalent.

## Post-Merge Verification

Independent post-merge verification on Linux at `IMPLEMENTATION_MERGE_SHA = 16fdd5fb209a9ab387359888bf1952571cfe8fba` confirmed the implementation state without relying on PR body numbers alone:

- PR #34 head `9a37d13fa8415df5bb93953516f6392ae2de98ad` is included in main through merge commit `16fdd5fb209a9ab387359888bf1952571cfe8fba`.
- The diff versus base `5569aae1bb7fdeeccb87ce21b1daf46b7d6c9724` is exactly four files.
- No docs, RFC, matrix, evidence, parser, AST, lexer, or durable allowlist file was touched in the implementation PR.
- `synapse/runtime/consensus_engine.py` contains schema_version `consensus.event.v2`.
- `synapse/interpreter.py` contains `ConsensusReplayIntegrityError` and `_consume_replayed_distributed_consensus`.

## Test Results

Linux, independently re-verified at IMPLEMENTATION_MERGE_SHA `16fdd5fb209a9ab387359888bf1952571cfe8fba`:

- Targeted P3c (`tests/test_consensus_replay_p3c.py`): 17 passed
- P3a + P3b regression (`tests/test_consensus_engine_p3a.py` + `tests/test_consensus_adapter_p3a.py` + `tests/test_consensus_actor_method_p3b.py`): 52 passed
- Collective regression (`tests/test_collective_intelligence.py`): 8 passed
- Full suite: 1571 passed, 12 skipped, 0 failed
- `compileall synapse`: passed
- `git diff --check`: passed
- new_failures = []

## Known Baseline Boundaries

Six known Windows / Git-filesystem baseline failures are platform-dependent and reproduce on main outside the consensus path.

On Linux the full suite is zero-failure.

These six Windows / Git-filesystem baseline failures are not regressions from P3c-0.

## Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

## Review Verdict

- POST_MERGE_ACCEPTED
- Code follow-up: not required

## P3c-1 Evidence Closure — Durable Ticket Creation and Replay

### Implementation Reference

- PR number: #39
- Implementation base SHA: 46d85b168a6661a401793dd9b31d6d15b5d79bac
- Implementation head commit before merge: 299793bf2b005d9e71afb1b5df37219a2d8afe8a
- Implementation merge commit: 88210654223b19a52bfddf9f3715e1a95af90367
- Approved RFC content SHA: a44df8dddd32c0bbacd4ce2ae8b2678728083e16
- Approval record content SHA: ef8e965fa2fb5b762aabeb4411c008684b2496b5

### Scope Closed

P3c-1 closes deterministic durable ticket creation and replay anchoring for deferred consensus with `reason = pending_missing_votes`:

- deterministic `ticket_id` from the engine-owned `consensus.ticket.v1` preimage
- adjacent LIVE append of `distributed_consensus_decided` and `distributed_consensus_ticket_created`
- deferred-ticket invariant preflight before any LIVE history append
- raw-adjacent two-event replay consumption
- fail-closed replay behavior for missing, malformed, non-mapping, non-string-key, extra-field, or missing-field ticket events
- replay cursor rollback on ticket validation or projection failure
- `consensus_tickets` projection with a deep-copy boundary
- legacy deferred history without adjacent ticket fails closed

P3c-1 does not close ticket resolution, finalization, cancellation, expiration, lifecycle state machine, public ticket API, mailbox voting, promise-backed vote completion, signal-injected vote completion, network or daemon transport, live LLM vote production, parser/AST/lexer expansion, production distributed consensus protocol behavior, or overall P3c closure.

### Changed Files

PR #39 changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `tests/test_consensus_adapter_p3a.py`
- `tests/test_consensus_replay_p3c.py`

No docs, RFC, matrix, evidence, parser, AST, lexer, workflows, examples, or durable allowlist file was touched in the implementation PR.

### Post-Merge Verification

- PR #39 head `299793bf2b005d9e71afb1b5df37219a2d8afe8a` is included in main through merge commit `88210654223b19a52bfddf9f3715e1a95af90367`.
- Code review verified deferred-ticket invariant preflight before LIVE append.
- Code review verified closed-schema ticket replay validation before raw event field access.
- Code review verified replay cursor rollback and projection rollback on ticket replay failure.
- Code review found no remaining merge blocker after follow-up head `299793bf2b005d9e71afb1b5df37219a2d8afe8a`.

### Test Results

Final PR #39 follow-up report recorded:

- `python -m compileall synapse tests`: passed
- Focused P3c replay: 49 passed
- P3 regression suite: 101 passed
- Consensus selection: 105 passed, 1510 deselected
- Full suite: 1596 passed, 13 skipped, 6 known Windows / Git-filesystem failures
- `git diff --check`: passed
- new consensus failures = []

Earlier independent Linux verification before the follow-up recorded:

- Full suite: 1592 passed, 12 skipped, 0 failed
- Targeted P3c: 38 passed
- P3a + P3b regression: 52 passed

### Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

## P3c-2 Evidence Closure — Durable Consensus Ticket Resolution

### Implementation Reference

- PR number: #45
- Implementation base SHA: 9e62118ef5b033e68e4bd5ad02d2fb7b5a5c6aeb
- Implementation head commit before merge: 56f3cc854d874edcd27cff126ccdaccad238a983
- Implementation merge commit: c5b129711ef76f919f263ac4dc6d35637890a347
- Approved RFC content SHA: 20e859633a6e835b67cae50464f2ed9667cd4b1b
- Approval record: `docs/RFC-CONSENSUS-P3C2_APPROVAL.md`

### Scope Closed

P3c-2 closes durable consensus ticket resolution through the existing P2 `SuspendExpr` / `awaiting_external_signal` resume boundary:

- strict `consensus_ticket_resolution` request validation before `promise_created`
- strict resolution signal validation before `promise_resolved`
- engine-owned final vote merge, vote counts, outcome/reason, `votes_hash_final`, and `result_hash_final`
- closed-schema `distributed_consensus_ticket_resolved` event emission
- pending -> resolved `consensus_tickets` projection transition
- identical duplicate resolution as an idempotent no-op
- conflicting duplicate resolution fail-closed before resolution event append
- replay consumption of `distributed_consensus_ticket_resolved` before the existing `SuspendExpr` early replay return
- replay verification of final votes, counts, outcome, reason, and final hashes
- replay cursor and projection rollback on resolution replay failure
- preserved generic non-consensus `SuspendExpr` behavior

P3c-2 does not close production distributed consensus protocol behavior, overall P3c, mailbox-backed vote delivery, network or daemon transport, live LLM vote production, ticket finalization, cancellation, expiration, lifecycle status field, public ticket API, parser/AST/lexer expansion, `synapse/application.py` durable-surface expansion, P2 artifact schema expansion, or automatic rebinding of original deferred consensus variables.

### Changed Files

PR #45 changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `synapse/runtime/consensus_ticket_resolution.py`
- `tests/test_consensus_resolution_p3c2.py`

No docs, RFC, matrix, evidence, parser, AST, lexer, workflows, examples, `synapse/application.py`, P2 artifact schema, or durable suspension-reason file was touched in the implementation PR.

### Post-Merge Verification

Final PR #45 report recorded:

- `python -m compileall synapse tests`: passed
- Focused P3c-2 resolution: 23 passed
- P3 regression suite: 101 passed
- P2 durable regressions: 77 passed, 1 skipped
- Consensus selection: 128 passed, 1510 deselected
- Full suite: 1619 passed, 13 skipped, 6 known Windows / Git-filesystem failures
- new consensus failures = []

Independent Linux verification after implementation review recorded:

- Focused P3c-2 resolution: 23 passed
- P3 regression suite: 101 passed
- P2 durable regressions: 78 passed
- Full suite: 1626 passed, 12 skipped, 0 failed
- Windows / Git-filesystem failures remain classified as platform baseline outside the consensus path

### Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

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

P3c-N1 closes the approved local pending-ticket import and mailbox vote response collection slice:

- strict `consensus_ticket_import` payload validation
- imported pending-ticket projection closed-schema validation
- `vote_counts` recomputation and verification before projection mutation
- `votes_hash` recomputation and verification with the same participant-order preimage as `ConsensusEngine`
- deterministic `ticket_import_hash`
- durable `distributed_consensus_ticket_imported` event emission only after validation
- import idempotency/conflict policy for `ticket_id`, `bootstrap_id`, and `ticket_import_hash`
- replay reconstruction of `consensus_tickets[ticket_id]` from `distributed_consensus_ticket_imported`
- strict `consensus_vote_response` validation
- deterministic response hashing without self-reference
- participant identity and optional participant-mailbox binding checks
- participant-level duplicate vote policy
- durable `distributed_consensus_vote_received` domain event
- replay validation of imported-ticket and vote-received domain events
- full-coverage-only terminal reduction through existing `ConsensusEngine.resolve_pending_ticket(...)`
- preservation of generic non-consensus `ReceiveBlock` behavior

P3c-N1 closes only pending-ticket import plus local mailbox-backed vote response collection. It does not close fresh durable `DistributedConsensusStmt` execution, vote request delivery, `distributed_consensus_vote_requested`, network or daemon transport, automatic timeout/scheduler behavior, persistent inbox behavior, parser/AST/lexer expansion, production distributed consensus protocol behavior, or overall P3c closure.

### Changed Files

PR #58 changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_mailbox_collection.py`
- `tests/test_consensus_mailbox_collection_p3cn.py`

No `ConsensusEngine`, `actor_runtime.py`, `application.py`, parser, AST, lexer, network, daemon, timer, scheduler, persistent inbox, artifact schema, `_REPLAY_STATE_KEYS`, matrix, or evidence file was touched in the implementation PR.

### Post-Merge Verification

Final PR #58 report and independent audit recorded:

- PR #58 head `3e94af25376cd8d6d25b56b321fc8be0a37c611e` is included in main through merge commit `a9497aa26b4450f40a541e16b6260129d36bb4f2`.
- The branch was one commit ahead of approved base `dd1037010c17449a2cc9852aedc1517ef3023701` before merge.
- The changed-file allowlist was exactly `synapse/interpreter.py`, `synapse/runtime/consensus_mailbox_collection.py`, and `tests/test_consensus_mailbox_collection_p3cn.py`.
- The receive hook was verified in `synapse/interpreter.py` after `message_received` append and before `apply_receive_patterns(...)`.
- The hook preserves generic receive behavior for non-consensus messages.
- `votes_hash` recomputation was verified against the engine-owned `consensus.votes.v1` participant-order preimage and repository canonical JSON hashing.
- Replay reconstruction uses durable history events, not live state.
- `consensus_tickets` remains an in-memory projection and was not added to `_REPLAY_STATE_KEYS`.

### Test Results

Final PR #58 implementation report recorded:

- Focused P3c-N1 collection: 43 passed
- P3c-2 regression (`tests/test_consensus_resolution_p3c2.py`): 23 passed
- P2 mailbox wait regression (`tests/test_durable_mailbox_wait.py`): 16 passed
- Consensus/mailbox/P3c/P2 durable selector: 281 passed, 1 skipped, 1415 deselected
- `git diff --check`: passed
- new failures = []

Independent local audit recorded equivalent green validation:

- Focused P3c-N1 collection: 43 passed
- Consensus/mailbox/P3c/P2 durable selector: 282 passed, 1415 deselected
- new failures = []

### Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

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
It does not close P3c-N2, fresh DistributedConsensusStmt mailbox-backed vote request delivery, network/daemon transport, automatic timeout/scheduler behavior, persistent inbox behavior, public ticket API, parser/AST/lexer expansion, production distributed consensus protocol behavior, or overall P3c closure.

### Closed Defects

- P3C_LIFECYCLE_ERROR_TAXONOMY_DEFECT
- P3C_LIFECYCLE_REPLAY_DEFECT

### Changed Files

PR #61 changed exactly these implementation files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_mailbox_collection.py`
- `synapse/runtime/consensus_ticket_resolution.py`
- `tests/test_consensus_ticket_lifecycle_p3c.py`

The final §19 test-completion commit changed only:

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

### Post-Merge Verification

- PR #61 head `71feec6610c19defc3c7b1efad28ebbc822d8a2b` is included in main through merge commit `8ff834bdeebd195ad7689af5c2137b04792b3025`.
- The branch lineage contained approved base `66c52a70e16e8d238681fe82e8e820eb6236133b`.
- The replay-boundary fix changed only `synapse/interpreter.py` and `tests/test_consensus_ticket_lifecycle_p3c.py`.
- The final §19 test-completion commit changed only `tests/test_consensus_ticket_lifecycle_p3c.py`.
- Generic durable mailbox replay behavior for unrelated non-lifecycle unexpected events remains generic durable mailbox RuntimeError.

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

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 pending-ticket import and local mailbox vote response collection closed; P3c Ticket Lifecycle terminal cancel/expire and replay integrity closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

## Next Allowed Work

The following future stages remain blocked behind their own RFC and approval gates and are not authorized by this evidence closure:

- P3c-N2 — fresh `DistributedConsensusStmt` mailbox-backed vote request delivery and initial collection
- P3d — LLM-assisted voting
- future RFC — network/daemon vote transport
- future RFC — production distributed consensus protocol claims
- future RFC — parser/AST/lexer vote syntax
- future RFC — public ticket API or external lifecycle control surface beyond the approved internal command path
- future RFC — automatic rebinding of original deferred consensus variables
