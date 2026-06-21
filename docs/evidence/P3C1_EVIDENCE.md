# P3C1 Evidence — Durable Consensus Ticket Creation and Replay

## Status

P3c-1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

Capability for distributed consensus remains Partial.

Production distributed consensus protocol behavior is NOT claimed.

Overall P3c remains open.

## Implementation Reference

- Implementation PR: #39
- Implementation branch: `codex/p3c-1-durable-consensus-ticket-replay`
- Implementation base SHA: `46d85b168a6661a401793dd9b31d6d15b5d79bac`
- Implementation head SHA before merge: `299793bf2b005d9e71afb1b5df37219a2d8afe8a`
- Implementation merge SHA: `88210654223b19a52bfddf9f3715e1a95af90367`
- Approved RFC: `docs/RFC-CONSENSUS-P3C1.md`
- Approved RFC content SHA: `a44df8dddd32c0bbacd4ce2ae8b2678728083e16`
- Approval record: `docs/RFC-CONSENSUS-P3C1_APPROVAL.md`
- Approval record content SHA: `ef8e965fa2fb5b762aabeb4411c008684b2496b5`
- RFC draft PR: #37
- RFC draft merge SHA: `9085d864df812f89984f88de3f503e2243f5cc58`
- Approval-gate PR: #38
- Approval-gate merge/base SHA: `46d85b168a6661a401793dd9b31d6d15b5d79bac`

## Merge Facts

PR #39 was merged after the post-review follow-up at head `299793bf2b005d9e71afb1b5df37219a2d8afe8a`.

The merge commit is `88210654223b19a52bfddf9f3715e1a95af90367`.

The implementation PR changed exactly four files and did not modify docs, RFCs, matrix, evidence, parser, AST, lexer, workflows, examples, or durable allowlist files.

## Changed Files

The merged implementation changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `tests/test_consensus_adapter_p3a.py`
- `tests/test_consensus_replay_p3c.py`

## Scope Closed

P3c-1 closes the following local durable/replay behavior:

- Deferred consensus with `reason = pending_missing_votes` creates a deterministic `ticket_id` from the engine-owned `consensus.ticket.v1` preimage.
- `ConsensusEngine` owns ticket identity, ticket payload construction, missing participant derivation, and ticket-event shape.
- LIVE deferred evaluation appends `distributed_consensus_decided` followed immediately by `distributed_consensus_ticket_created`.
- LIVE deferred evaluation validates the deferred-ticket invariant before any history append.
- `distributed_consensus_ticket_created` participates in normal ordered history and the existing hash-chain machinery.
- REPLAY consumes the deferred decision and the raw-adjacent ticket event as a two-event durable unit.
- REPLAY advances `replay_cursor` by two only after both events pass validation.
- REPLAY fails closed if any event appears between `distributed_consensus_decided` and `distributed_consensus_ticket_created`, including normally skippable audit/event types.
- REPLAY fails closed on missing, malformed, non-mapping, non-string-key, extra-field, or missing-field ticket events.
- REPLAY validates ticket anchors and values including `ticket_id`, `proposal_id`, `statement_identity`, `participants`, `missing_participants`, `votes`, `vote_counts`, `votes_hash`, `strategy`, `policy`, `quorum`, and `timeout`.
- REPLAY restores `replay_cursor` and leaves `consensus_tickets` unchanged on ticket validation or projection failure.
- `consensus_tickets` is a projection store only and uses a deep-copy boundary.
- Legacy deferred history without an adjacent ticket event fails closed; no silent upgrade or synthetic ticket creation occurs.
- Terminal committed/rejected outcomes and insufficient quorum outcomes do not create tickets.

## Explicit Non-Claims

P3c-1 does not claim or implement:

- ticket resolution
- ticket finalization
- ticket cancellation
- ticket expiration
- ticket lifecycle state machine
- public ticket API
- mailbox-backed vote collection
- daemon-backed vote collection
- network-backed vote collection
- DurablePromise-backed vote completion
- signal-injected vote resolution
- await/suspend vote collection
- live LLM vote production
- durable allowlist expansion
- event v1 migration or silent upgrade
- parser, AST, or lexer expansion
- production distributed consensus protocol behavior
- overall P3c closure

## Architecture Evidence

- `ConsensusEngine` remains the canonical owner of ticket identity and ticket payload construction.
- `ConsensusDecision` remains backward compatible for non-ticket consumers while exposing optional `ticket_id` and `ticket_payload` for deferred pending-missing-votes outcomes.
- The interpreter adapter owns LIVE append ordering, REPLAY raw adjacency, replay cursor movement, ticket projection, and local error translation.
- The interpreter does not use generic replay helper skipping for ticket adjacency.
- The ticket replay validator enforces closed event schema before field access and before projection.
- Ticket projection is not a durable event schema; `projection_state` is added only to the internal `consensus_tickets` projection.
- Ticket events do not contain `status`, `result_hash`, `previous_hash`, runtime UUIDs, source labels, or projection-only fields.
- Hash-chain behavior remains external to ticket payload construction and was not changed by P3c-1.

## Post-Merge Verification

Post-merge verification state for `IMPLEMENTATION_MERGE_SHA = 88210654223b19a52bfddf9f3715e1a95af90367`:

- PR #39 head `299793bf2b005d9e71afb1b5df37219a2d8afe8a` is included in main through merge commit `88210654223b19a52bfddf9f3715e1a95af90367`.
- The implementation base was `46d85b168a6661a401793dd9b31d6d15b5d79bac`.
- The implementation changed exactly four files.
- No docs, RFC, matrix, evidence, parser, AST, lexer, or durable allowlist file was touched in the implementation PR.
- Code review verified deferred-ticket invariant preflight before LIVE append.
- Code review verified closed-schema ticket replay validation before raw event field access.
- Code review verified replay cursor rollback and projection rollback on ticket replay failure.
- Code review found no remaining merge blocker after follow-up head `299793bf2b005d9e71afb1b5df37219a2d8afe8a`.

## Test Results

Final PR #39 follow-up report recorded the following validation at head `299793bf2b005d9e71afb1b5df37219a2d8afe8a`:

- `python -m compileall synapse tests`: passed
- Focused P3c replay (`tests/test_consensus_replay_p3c.py`): 49 passed
- P3 regression suite (`tests/test_consensus_engine_p3a.py` + `tests/test_consensus_adapter_p3a.py` + `tests/test_consensus_actor_method_p3b.py` + `tests/test_consensus_replay_p3c.py`): 101 passed
- Consensus selection (`python -m pytest tests -k consensus -q`): 105 passed, 1510 deselected
- Full suite: 1596 passed, 13 skipped, 6 known Windows/Git filesystem failures
- `git diff --check`: passed
- new consensus failures = []

Earlier independent Linux verification before the P3c-1 follow-up recorded:

- Full suite: 1592 passed, 12 skipped, 0 failed
- Targeted P3c: 38 passed
- P3a + P3b regression: 52 passed

The post-review follow-up expanded the P3c replay test count and preserved the no-new-consensus-failures boundary.

## Known Baseline Boundaries

Six known Windows/Git filesystem baseline failures are platform-dependent and reproduce on main outside the consensus path.

The known baseline class covers symlink target representation, executable-bit mode, reserved/special pathnames, dangling symbolic refs, and slash normalization.

These failures are not regressions from P3c-1.

## Capability Impact

Distributed consensus capability extends from:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed`

To:

`Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed`

Production distributed consensus protocol behavior remains explicitly NOT claimed.

Overall P3c remains open.

## Review Verdict

- POST_MERGE_ACCEPTED
- EVIDENCE CLOSED
- Code follow-up: completed before merge
- Remaining code blocker: none for P3c-1 scope

## Next Allowed Work

The following future stages remain blocked behind their own RFC and approval gates and are not authorized by this evidence closure:

- P3c-2 — DurablePromise-backed vote completion
- P3c-N — mailbox-backed vote delivery and receive-based vote collection
- P3d — LLM-assisted voting
- future RFC — network/daemon vote transport
- future RFC — production distributed consensus protocol claims
- future RFC — parser/AST/lexer vote syntax
- future RFC — ticket resolution, finalization, cancellation, expiration, or lifecycle state machine
