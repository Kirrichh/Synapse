# P3C Evidence — Canonical Consensus Replay Consumption

## Status

P3c-0 POST_MERGE_ACCEPTED / EVIDENCE CLOSED

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

P3c-0 does not claim or implement:

- mailbox-backed vote collection
- daemon-backed vote collection
- network-backed vote collection
- DurablePromise-backed vote completion
- signal-injected vote resolution
- await/suspend vote collection
- stateful consensus ticket lifecycle
- live LLM vote production
- durable allowlist expansion
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

## Next Allowed Work

The following future stages remain blocked behind their own RFC and approval gates and are not authorized by this evidence closure:

- P3c-1 — durable consensus ticket lifecycle
- P3c-2 — DurablePromise-backed vote completion
- P3c-N — mailbox-backed vote delivery and receive-based vote collection
- P3d — LLM-assisted voting
- future RFC — network/daemon vote transport
- future RFC — production distributed consensus protocol claims
- future RFC — parser/AST/lexer vote syntax
