# P3a Evidence — Semantic Consensus Core Closure

Status: `P3a IMPLEMENTED / VERIFIED_ON_MAIN / S1/S2 EVIDENCE CLOSED` after this evidence closure PR merges.

This evidence record summarizes P3a implementation PR #27 and the post-merge verification state for the approved RFC-CONSENSUS-P3 P3a scope.

P3a closes the semantic-facade finding for local deterministic content-sensitive consensus semantics. It does not claim production distributed consensus protocol behavior, network consensus, daemon delivery, actor-mailbox voting, durable replay closure, or live LLM voting.

## Scope

P3a covers:

- deterministic semantic consensus engine;
- configured `VoteSource -> interpreter adapter -> ConsensusEngine` path;
- participant identity normalization;
- vote states `yes`, `no`, `abstain`, `missing`;
- `MajorityVote`, `UnanimousVote`, and `NoVetoVote` strategies;
- strict request validation;
- canonical `proposal_id`, `votes_hash`, and `result_hash`;
- canonical `distributed_consensus_decided` event;
- fail-closed validation behavior;
- removal of facade auto-commit semantics;
- no hidden current-actor vote injection;
- no durable ticket lifecycle.

Out of P3a scope:

- production distributed consensus protocol claims;
- Raft/Paxos/Tendermint/PBFT semantics;
- network voting;
- daemon packet voting;
- actor mailbox waiting;
- live LLM voting;
- durable replay consumption;
- stateful consensus ticket lifecycle;
- runtime `VoteSource` registry;
- parser, lexer, or AST expansion.

## Commit anchors

| Item | SHA / ID |
|---|---|
| Approved RFC contract | `docs/RFC-CONSENSUS-P3.md` |
| Approved RFC contract SHA | `be468025cfa59e146b22670dd8d1b75548b4b3e0` |
| P3a implementation base SHA | `8a375023fffed942677818a8dfd041f0107ba994` |
| P3a implementation PR | `#27` |
| P3a implementation PR final head before merge | `ca4dc9aecd6d5a37c670bac0eb871874de6ff2d0` |
| P3a merge commit on `main` | `60db4d3aa610c0cab6ec19cf532b47b7107de136` |
| P3a evidence closure branch | `checkout--b-p3a-docs` |

## Changed files in implementation PR #27

PR #27 changed only:

```text
synapse/interpreter.py
synapse/runtime/consensus_engine.py
tests/test_collective_intelligence.py
tests/test_consensus_adapter_p3a.py
tests/test_consensus_engine_p3a.py
```

No documentation, status, evidence, parser, AST, lexer, durable inventory, canonical-values, examples, or workflow files changed in PR #27.

Guard files remained unchanged in PR #27:

```text
docs/RFC-CONSENSUS-P3.md
docs/CAPABILITY_MATURITY_MATRIX.md
docs/evidence/*
synapse/parser.py
synapse/ast.py
synapse/lexer.py
synapse/application.py
synapse/canonical_values.py
examples/**
.github/**
```

## Implementation summary

PR #27 introduced `synapse/runtime/consensus_engine.py` as the deterministic semantic core.

The interpreter path was refactored to:

```text
configured VoteSource -> interpreter adapter -> deterministic ConsensusEngine
```

The interpreter adapter owns AST/environment extraction, request construction, exception translation, binding, and event append. The engine owns participant/vote/quorum/strategy/outcome/hash semantics.

## Semantic behavior verified

P3a verifies:

- default `NullVoteSource` returns `missing` votes and does not auto-commit;
- explicit `VoteSource` commits only when votes satisfy the selected strategy;
- current actor is not injected as a voter;
- top-level execution does not invent a hidden voter;
- participants are normalized from supported identities only;
- `DurableActorRef.actor_name` is semantic identity, not `process_id`;
- duplicate participants are rejected;
- unsupported participant objects are rejected;
- unknown participant votes are rejected;
- unknown vote states are rejected;
- duplicate identical supplied votes are rejected as `duplicate_vote`;
- repeated different supplied vote states are rejected as `conflicting_vote`;
- malformed `ExplicitVoteSource` iterable records fail closed;
- `policy_ref is None` resolves to `MajorityVote`;
- `policy "MajorityVote"` is not shadowed by arbitrary environment bindings;
- Governance Policy object names are extracted without executing guard bodies;
- quorum and timeout use strict Python type guards, with `bool` rejected before `int` handling;
- `MajorityVote` and `NoVetoVote` omitted quorum defaults to `participant_count // 2 + 1`;
- `UnanimousVote` requires all participants to vote `yes`;
- validation errors do not bind results or append events.

## Canonical result/event evidence

P3a produces structured result payloads with:

```text
schema_version = consensus.result.v1
proposal_id
outcome
committed
reason
topic
participants
coordinator
strategy
policy
votes
vote_counts
deferred
ticket_id = None
votes_hash
result_hash
```

P3a emits canonical `distributed_consensus_decided` events for valid operational outcomes.

Legacy facade events are not emitted by new P3a evaluations:

```text
distributed_consensus_committed
distributed_consensus_deferred
```

The history event omits the full vote map; the binding result contains the full vote map.

## Determinism and side-effect boundary

P3a does not introduce:

- live LLM vote path;
- daemon vote path;
- network vote path;
- actor mailbox waiting;
- runtime `VoteSource` registry;
- ticket allocation;
- `consensus_tickets` mutation;
- `actor_log` mutation;
- durable replay consumption;
- parser/AST/lexer changes;
- durable allowlist expansion.

Hashing uses `synapse.hardening.canonical_json` with recursive pre-validation to reject unsupported host objects before canonicalization.

## Test evidence from PR #27

Initial implementation evidence recorded in PR #27:

```text
python -m pytest -q tests/test_consensus_engine_p3a.py tests/test_consensus_adapter_p3a.py
24 passed in 0.17s

python -m pytest -q tests/test_collective_intelligence.py
8 passed in 0.21s

python -m pytest --collect-only -q
1538 tests collected in 0.46s

python -m pytest -q
6 failed, 1519 passed, 13 skipped in 79.52s

python scripts/pre_commit_hook.py
Gate 1 passed: all 44 .syn files parse OK
Gate 2 passed: coverage 0.933890 >= 0.9332 (46/46 files)
pre-commit: all gates passed

python -m py_compile synapse/runtime/consensus_engine.py synapse/interpreter.py tests/test_consensus_engine_p3a.py tests/test_consensus_adapter_p3a.py tests/test_collective_intelligence.py
passed

git diff --check
passed
```

Follow-up review-fix evidence recorded in PR #27 comment `4751461717`:

```text
python -m pytest -q tests/test_consensus_engine_p3a.py tests/test_consensus_adapter_p3a.py
28 passed in 0.26s

python -m pytest -q tests/test_collective_intelligence.py
8 passed in 0.14s

python -m py_compile synapse/runtime/consensus_engine.py synapse/interpreter.py tests/test_consensus_engine_p3a.py tests/test_consensus_adapter_p3a.py tests/test_collective_intelligence.py
passed

git diff --check
passed
```

## Known baseline failures

The full-suite six failures recorded in PR #27 were reproduced on clean base and are outside P3a:

```text
tests/test_controlled_change_hardening.py::test_symlink_candidate_digest_uses_link_target_not_external_contents
tests/test_controlled_change_hardening.py::test_real_git_ls_tree_z_modes_and_exact_paths
tests/test_controlled_change_hardening.py::test_real_git_status_z_preserves_special_pathnames_and_backslash
tests/test_controlled_change_hardening.py::test_real_git_backslash_patch_is_applied_then_rejected_by_scope
tests/test_ref_cas_and_linked_worktree_safety.py::test_dangling_symbolic_evidence_ref_is_replaced_without_creating_target_branch
tests/test_ref_cas_and_linked_worktree_safety.py::test_parser_against_real_git_raw_bytes
```

PR #27 recorded clean-base reproduction result:

```text
6 failed in 1.70s
```

No new P3a-specific failures were reported after the follow-up fixes.

## Maturity transition

Before this evidence closure, the capability matrix recorded Distributed consensus as **Семантический фасад**.

After PR #27 and this evidence closure, the honest status is:

```text
Partial — P3a semantic core verified
```

Rationale:

- the previous facade auto-commit behavior has been replaced;
- content-sensitive participant votes and deterministic strategy outcomes are implemented;
- canonical result and event surfaces are verified;
- guard files and forbidden paths remain unchanged;
- production distributed consensus protocol behavior remains out of scope.

Therefore this evidence PR may update the capability matrix from **Семантический фасад** to **Partial — P3a semantic core verified**.

Do not describe this as full production distributed consensus.

## Closure statement

P3a is closed for the approved local semantic consensus core scope.

Remaining future work belongs to later stages, including network/daemon transport, actor/mailbox voting, durable replay closure, and any production distributed protocol claims.
