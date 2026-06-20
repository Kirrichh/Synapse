# P3B Evidence — Actor-Method Vote Integration

## Status

P3b actor-method vote integration evidence is closed for the merged implementation.

Capability status: Partial — P3b local actor-method vote source verified.

## Implementation Reference

- Implementation PR: #31
- Implementation branch: codex/p3b-0-actor-method-votesource
- Implementation base SHA: 362c9bc9fe386afa2b4f6a18553a10fda1d9c446
- Implementation head SHA before merge: 8193a63f816e291e2495905b1b257cfe09afe190
- Implementation merge SHA: dbdfc7252c83d9fc4be0f0b5eb2cbd2007f0e2ad

## Merge Facts

- Changed files: 5
- Diff size: +814 / -5
- Post-merge audit verdict: POST_MERGE_ACCEPTED

## Changed Files

The merged implementation changed exactly these files:

- `synapse/interpreter.py`
- `synapse/runtime/consensus_engine.py`
- `synapse/runtime/consensus_proposal_view.py`
- `synapse/runtime/consensus_vote_sources.py`
- `tests/test_consensus_actor_method_p3b.py`

## Scope Closed

P3b actor-method vote integration is:

- explicit opt-in
- synchronous
- local runtime only
- actor method based
- VoteSource based
- P3a-compatible
- non-production distributed protocol

## Explicit Non-Claims

P3b actor-method vote integration is not:

- mailbox-backed voting
- daemon-backed voting
- network-backed voting
- DurablePromise voting
- await/suspend vote collection
- live LLM vote generation
- durable replay closure
- stateful consensus ticket lifecycle
- production distributed consensus protocol behavior
- Raft/Paxos/Tendermint/PBFT support

## Architecture Evidence

- ConsensusEngine remains semantic authority.
- ActorMethodVoteSource is isolated behind VoteSource.
- Actor-method voting is explicit opt-in.
- Default behavior remains NullVoteSource.
- Explicit VoteSource override takes precedence.
- Proposal view is recursively frozen and JSON-compatible.
- Proposal mutation fails closed.
- Registry mutation during vote collection fails closed.
- Vote collection side effects fail closed.
- Participant faults become missing votes.
- Source diagnostics are local and not exposed in public result/event schema.
- Source labels do not enter proposal_id, votes_hash, result_hash, outcome, or reason.
- String participants and DurableActorRef do not gain hidden executable actor resolution.

## Post-Merge Verification

Independent post-merge verification on merged main confirmed:

- Targeted P3a + P3b tests: 52 passed
- Collective regression: 8 passed
- Full suite on Linux merged main: 1554 passed, 0 failed

## Test Results

- Targeted P3a + P3b tests: 52 passed
- Collective regression: 8 passed
- Full suite on Linux merged main: 1554 passed, 0 failed
- new_failures = []

## Known Baseline Boundaries

Prior Windows/git filesystem baseline failures were known non-P3b failures and are not treated as P3b implementation regressions.

## Capability Impact

Distributed consensus capability remains Partial — P3b local actor-method vote source verified.

The capability remains bounded to explicit opt-in, synchronous local runtime actor-method voting behind VoteSource. It does not add mailbox voting, daemon voting, network voting, durable replay closure, live LLM voting, or production distributed consensus protocol behavior.

## Review Verdict

- POST_MERGE_ACCEPTED
- Code follow-up: not required
- Evidence/capability PR: authorized

## Next Allowed Work

Future work may add separate evidence or implementation only when explicitly scoped. Any future production distributed consensus, mailbox-backed voting, daemon-backed voting, network-backed voting, DurablePromise voting, await/suspend vote collection, live LLM vote generation, durable replay closure, stateful consensus ticket lifecycle, or Raft/Paxos/Tendermint/PBFT support requires separate authorization and evidence.
