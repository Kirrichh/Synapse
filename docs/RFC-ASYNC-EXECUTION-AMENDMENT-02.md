# RFC-ASYNC-EXECUTION-AMENDMENT-02

**Title:** P2c — Multi-cycle, Stale-ID and Concurrent Resume Closure  
**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Parent RFC:** `docs/RFC-ASYNC-EXECUTION.md`  
**Prior amendment:** `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md`  
**Patch unit:** `P2-RFC-AMENDMENT-02`  
**Document status:** `DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED`

## Commit anchors

| Anchor | SHA / Status |
|---|---|
| Parent RFC approval anchor | `b5d49959c66c2970bdf85d5ce2290ee9250ed30f` |
| Amendment-01 approval anchor | `4251928381d3e1f2c58f610ad07f282f25230197` |
| P2a + S1 base before P2b | `9f146f0e931301fa549304fa7e4c9eca9e97926c` |
| PR #18 P2b head before merge | `6979e57c29bd2857ddde6721844bab90270af475` |
| P2b post-merge anchor | `743e4fbc3cc6545745713d26625d4f4cd9a4d34c` |
| PR #19 S1 sync head before merge | `2dfc9afe00be1795745c4da737a868392a6fed86` |
| S1 sync merge anchor | `cd714073164407d2d9118f643360cba465a79700` |
| Amendment-02 base SHA | `cd714073164407d2d9118f643360cba465a79700` |

The distinction between `P2b post-merge anchor` and `Amendment-02 base SHA` is normative. `743e4fbc3cc6545745713d26625d4f4cd9a4d34c` is the post-merge anchor for P2b implementation. `cd714073164407d2d9118f643360cba465a79700` is the S1-synchronized `main` state after P2b closure and is the base for this amendment.

---

# 1. Amendment rule

This document is an additive normative amendment to the approved RFC and Amendment-01.

This amendment does not replace, shorten, reorganize, or delete any existing approved contract. All clauses of the parent RFC and Amendment-01 remain in force except where this amendment explicitly extends, narrows, or clarifies a P2c-specific subject.

If this amendment conflicts with the parent RFC or Amendment-01, the numbered clauses in this amendment take precedence only for the stated P2c subject. All unrelated contracts remain governed by the parent RFC and Amendment-01.

Production implementation of P2c is unauthorized until this amendment is reviewed, approved, and merged.

No production code may be written, modified, or merged under the label `P2c implementation` before this amendment reaches `APPROVED` status on `main`.

If implementation starts before this amendment is approved, the process must stop with:

```text
BLOCKED — P2C_PREMATURE_IMPLEMENTATION
```

---

# 2. Current program state

P2 currently has the following status after P2b and S1 sync:

| Stage | Status |
|---|---|
| P2a — Durable Initial Run | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` |
| P2b — Resume and Boundary Reconstruction | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` |
| P2c — Idempotency, Multi-cycle and Concurrency Closure | `RFC_REQUIRED / NOT IMPLEMENTED` |
| P2 overall | `PARTIAL` |

P2b closed the canonical single-resume path:

```text
python -m synapse resume
  --state-file <artifact.json>
  --suspension-id <id>
  --signal-file <json-file|->
```

P2b established:

1. canonical resume CLI;
2. strict state-file policy;
3. strict signal JSON loading;
4. artifact integrity validation;
5. embedded source ownership validation;
6. deterministic replay to saved boundary;
7. natural transition from replay mode to live mode;
8. same-generator continuation through `generator.send(signal)`;
9. output-prefix verification and suppression;
10. atomic commit of `COMPLETED`, `ERROR`, or next `PENDING`;
11. terminal duplicate idempotency;
12. conflicting duplicate rejection;
13. stale/unknown suspension rejection;
14. PENDING-to-PENDING sequence-aware next suspension mechanics;
15. process-level resume lock race proof for the P2b boundary.

P2b does not close the entire async durable execution lifecycle. P2c is required for full multi-cycle campaigns, stale IDs across later boundaries, cross-cycle duplicate handling, and extended process-level concurrent resume closure.

---

# 3. P2c product problem

P2c addresses the following product problem:

A user or external orchestrator may drive a durable workflow through multiple suspension boundaries over time. The workflow may suspend, resume, suspend again, resume again, and finally complete. During this lifecycle, external systems may repeat signals, submit stale suspension IDs, submit a signal intended for an earlier boundary, or race two resume processes against the same artifact.

The user-visible contract must guarantee that:

1. a workflow can progress through multiple consecutive suspension boundaries within one `run_id`;
2. each accepted signal is applied at most once to the intended suspension boundary;
3. repeated same-signal requests return the already committed result without mutating the artifact;
4. repeated different-signal requests for a resolved suspension fail closed;
5. stale or unknown suspension IDs fail closed;
6. two OS-level resume processes cannot both commit divergent outcomes for the same suspension;
7. no new daemon, inbox, scheduler, network transport, or auto-recovery behavior is introduced under the P2c scope.

P2c does not provide background orchestration, distributed delivery, signal queues, network semantics, timeout decisions, automatic stale-lock recovery, or external effect exactly-once guarantees. P2c closes the runtime artifact semantics needed before any such higher-level layers can be safely specified.

---

# 4. Scope of P2c

## 4.1. In scope

P2c includes the following contract areas:

1. full multi-cycle resume campaigns;
2. sequence and revision evolution across three or more consecutive boundaries;
3. stale ID semantics across later boundaries;
4. duplicate same-signal semantics across current and prior cycles;
5. duplicate different-signal conflict semantics across current and prior cycles;
6. process-level concurrent resume race matrix for P2c scenarios;
7. compatibility with P2a and P2b artifacts using the current durable artifact schema;
8. composition of locking, idempotency, history integrity, boundary reconstruction, and output suppression over extended lifecycles;
9. evidence requirements for multi-cycle and concurrency behavior;
10. P2c-specific stop-gates.

## 4.2. Out of scope

The following are explicitly out of P2c scope:

1. signal inbox;
2. early signal delivery;
3. resident daemon;
4. background worker;
5. network delivery;
6. distributed transport;
7. scheduler timeout decisions;
8. wall-clock timeout semantics inside the runtime;
9. logical-time timeout semantics inside the runtime;
10. automatic stale-lock recovery;
11. force-unlock operator command;
12. lock owner metadata;
13. fencing token protocol;
14. distributed artifact store;
15. distributed locks;
16. new exit codes;
17. artifact schema version bump;
18. artifact migration commands;
19. automatic schema upgrade on read;
20. rewrite-in-place of historical artifacts;
21. changes to history seed;
22. changes to history hash algorithm;
23. changes to strict canonical JSON serialization;
24. changes to snapshot/checkpoint formats;
25. changes to golden replay formats;
26. changes to interpreter semantics;
27. changes to parser or AST;
28. changes to durable-safety validator allowlist unless separately approved;
29. changes to external effect exactly-once claims.

## 4.3. Forbidden implementation areas for P2c

P2c must not modify the following files or areas unless a later explicit approval changes the scope:

```text
synapse/interpreter.py
synapse/runtime/replay_engine.py
synapse/runtime/actor_runtime.py
synapse/hardening.py
synapse/parser.py
synapse/ast.py
synapse/builtins.py
synapse/lexer.py
synapse/cvm.py
synapse/bytecode.py
examples/**
.github/**
docs/RFC-ASYNC-EXECUTION.md
docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md
```

If P2c cannot be implemented without modifying one of these areas, implementation must stop with:

```text
BLOCKED — P2C_SCOPE_LEAK
```

If P2c requires a durable artifact schema version bump, implementation must stop with:

```text
BLOCKED — P2C_REQUIRES_SCHEMA_EVOLUTION
```

---

# 5. Existing P2b artifact model inherited by P2c

P2c inherits the P2b artifact model and must not redefine it.

The artifact schema remains:

```text
artifact_schema_version == "1.0.0"
```

P2c continues to rely on the following existing artifact fields:

```text
status
revision
run_id
correlation_id
execution_engine
source
initial_bindings
replay_state
history_integrity
active_suspension
idempotency
output_state
terminal
versions
artifact_hash
```

The `idempotency.resolved_suspensions` field is treated as an existing map capable of holding multiple resolved suspension entries. P2c may populate multiple entries in this map without changing the artifact schema version.

P2c must not introduce a new top-level history index, new schema version, new canonical serialization format, or new durable artifact root field unless separately approved by a later amendment.

---

# 6. Correction E — Multi-cycle resume contract

## 6.1. Problem closed by Correction E

P2b established single-step resume and demonstrated PENDING-to-PENDING mechanics. It did not normatively close the behavior of a full campaign with three or more consecutive suspension boundaries within one `run_id`.

P2c closes this gap.

## 6.2. Multi-cycle lifecycle definition

A multi-cycle durable campaign is a single `run_id` that progresses through at least three suspension boundaries before final terminal completion.

The canonical lifecycle is:

```text
run --durable
  -> PENDING_1

resume suspension_id_1 with signal_1
  -> PENDING_2

resume suspension_id_2 with signal_2
  -> PENDING_3

resume suspension_id_3 with signal_3
  -> COMPLETED
```

A P2c implementation may support more than three suspension boundaries, but the evidence requirement is at least three boundaries in one campaign.

## 6.3. Sequence invariant

For a single `run_id`, suspension sequence numbers must be strictly monotonic, dense, and one-based.

For every issued suspension boundary `k`:

```text
sequence[k] == k
```

For a campaign with `K` issued suspension boundaries:

```text
sequence[1] == 1
sequence[2] == 2
...
sequence[K] == K
```

There must be no skipped sequence values. There must be no duplicate sequence values.

If a P2c campaign produces sequence values that are not dense and monotonic, the implementation fails with:

```text
BLOCKED — MULTI_CYCLE_INVARIANT_NOT_PROVEN
```

## 6.4. Revision invariant

For every successful resume that mutates the artifact, the artifact revision must increase exactly once.

If an artifact at revision `R` is resumed and the outcome is committed, then the next artifact revision must be:

```text
revision_after == revision_before + 1
```

For a multi-cycle campaign, revision must be strictly monotonic. A single accepted resume must not increment revision more than once, and two concurrent resumes must not each increment the same boundary independently.

## 6.5. Suspension ID invariant

The suspension ID remains sequence-aware.

For a given boundary:

```text
suspension_id = sha256(
  "synapse-p2-suspension-v1",
  run_id,
  sequence,
  boundary_fingerprint
)
```

The exact serialization used for this hash remains the approved strict canonical serialization already used by P2b. P2c does not change the serialization profile.

Every suspension ID within one `run_id` must be unique.

If two different boundaries produce the same `suspension_id`, the implementation fails with:

```text
BLOCKED — MULTI_CYCLE_INVARIANT_NOT_PROVEN
```

## 6.6. Boundary fingerprint invariant

The `boundary_fingerprint` remains sequence-neutral. Sequence must not be included in the boundary fingerprint. Sequence must be included only in the `suspension_id` preimage.

This preserves the P2b distinction:

```text
boundary_fingerprint = identity of the replay boundary
suspension_id = identity of a concrete issued suspension instance
```

P2c must not change this split.

## 6.7. Resolved entry invariant

For every accepted resume of suspension `k`, the runtime must atomically commit the semantic outcome of that resume into:

```text
idempotency.resolved_suspensions[suspension_id[k]]
```

The resolved entry must contain enough information to support future duplicate same-signal returns and duplicate different-signal conflict detection.

The resolved entry must include:

```text
signal_hash
committed_revision
committed_status
operation_result
```

If P2c extends this internal entry with additional fields, those fields must be additive, must not require schema version bump, and must not change the semantics of the existing P2b fields.

## 6.8. Multi-cycle mixed-reason requirement

P2c must demonstrate at least one multi-cycle campaign using at least two distinct supported suspension reasons within the same `run_id`.

The implementation must use the canonical reason names actually emitted by the runtime. It must not fabricate a reason value in tests or evidence.

If the current approved public language/runtime path cannot produce two distinct supported suspension reasons within one `run_id` without expanding scope or modifying forbidden layers, P2c implementation must stop with:

```text
BLOCKED — MIXED_REASON_CAMPAIGN_NOT_PROVEN
```

The implementation must not modify parser, AST, interpreter, deep runtime layers, validator allowlists, or unrelated production behavior solely to manufacture mixed-reason evidence.

This criterion verifies composition over runtime-supported reasons; it does not authorize new reason semantics.

## 6.9. Multi-cycle output requirement

P2c must preserve output-prefix verification and output-prefix suppression across multiple resumes.

For every resume after the first, persisted output from earlier cycles must not be republished as new output.

The observable `output_delta` for each resume must include only output produced after the previously persisted output prefix.

If multi-cycle resume republishes already persisted output as fresh output, the implementation fails with:

```text
BLOCKED — MULTI_CYCLE_INVARIANT_NOT_PROVEN
```

---

# 7. Correction F — Stale-ID semantics across later boundaries

## 7.1. Problem closed by Correction F

P2b defined stale or unknown suspension handling for the currently active suspension. P2c must define behavior across a chain of multiple issued suspension IDs.

The key distinction is between:

1. a legitimate duplicate of a previously resolved suspension;
2. a conflicting duplicate of a previously resolved suspension;
3. a stale ID not accepted for the current boundary;
4. an unknown ID never issued by the current artifact;
5. artifact corruption where the artifact contradicts itself.

## 7.2. Stale versus duplicate decision order

After lock acquisition, artifact integrity validation, signal parsing, and signal hash calculation, P2c must apply the following decision order:

1. Look up supplied `suspension_id` in `idempotency.resolved_suspensions`.
2. If found, compare supplied `signal_hash` to the stored `signal_hash`.
3. If found and same hash, return saved semantic result without replay and without artifact mutation.
4. If found and different hash, return `RESOLUTION_CONFLICT` with exit `24`, without replay and without artifact mutation.
5. If not found, compare supplied `suspension_id` with current `active_suspension.suspension_id`.
6. If it matches active suspension, proceed with normal resume.
7. If it does not match active suspension, return `STALE_OR_UNKNOWN_SUSPENSION` with exit `23`, without replay and without artifact mutation.
8. If the artifact itself contains contradictory evidence that a historically issued suspension was resolved but its resolved entry is missing, malformed, or hash-inconsistent, classify the artifact as invalid with exit `21`.

## 7.3. Decision matrix

| Resolved entry exists? | Signal hash relation | Active suspension matches? | Outcome | Exit | Artifact mutation |
|---|---|---|---|---:|---|
| Yes | Same hash | Not relevant | Return saved semantic result | Previous outcome exit | No |
| Yes | Different hash | Not relevant | `RESOLUTION_CONFLICT` | `24` | No |
| No | Not relevant | Yes | Normal resume | `0`, `20`, or runtime error | Yes if outcome commits |
| No | Not relevant | No | `STALE_OR_UNKNOWN_SUSPENSION` | `23` | No |
| Contradictory artifact evidence | Not relevant | Not relevant | `ARTIFACT_INVALID_OR_INTEGRITY_FAILURE` | `21` | No |

## 7.4. Unknown IDs

An ID that is not present in `resolved_suspensions` and does not match `active_suspension.suspension_id` is classified as stale or unknown.

P2c does not need to distinguish user-provided random IDs from old IDs whose resolved entries are absent, unless the artifact contains internal evidence proving corruption.

The public outcome for this class is:

```text
STALE_OR_UNKNOWN_SUSPENSION
exit 23
```

## 7.5. Artifact contradiction rule

P2c must not infer corruption from the mere absence of a supplied ID in `resolved_suspensions`.

Corruption is recognized only when the artifact’s own persisted state contradicts itself. Examples of contradiction include:

1. a malformed `resolved_suspensions` entry;
2. a resolved entry whose stored operation result does not match its committed status/revision identity;
3. an idempotency structure that fails approved shape validation;
4. an artifact hash that does not match the artifact payload;
5. an active suspension whose sequence, ID, or boundary fingerprint fails recomputation;
6. an impossible revision/sequence relationship inside the artifact itself.

If no such contradiction exists, a non-resolved, non-active ID is stale/unknown and exits `23`.

This rule prevents P2c from requiring an unapproved historical index of every issued ID.

---

# 8. Correction G — Duplicate signal matrix across cycles

## 8.1. Problem closed by Correction G

P2b proved duplicate handling for the accepted resume path around a single active or terminal state. P2c must prove duplicate behavior across current and prior cycles in a multi-cycle chain.

## 8.2. Duplicate same-signal rule

If a supplied `suspension_id` exists in `resolved_suspensions` and the supplied `signal_hash` equals the stored `signal_hash`, the runtime must return the saved semantic result.

The runtime must not:

1. replay the artifact;
2. inject the signal again;
3. append new execution history;
4. change `output_state`;
5. change `artifact_hash`;
6. modify the artifact file;
7. update `mtime`;
8. increment revision;
9. create a new suspension;
10. change terminal state.

The returned result is idempotent.

## 8.3. Duplicate different-signal rule

If a supplied `suspension_id` exists in `resolved_suspensions` and the supplied `signal_hash` differs from the stored `signal_hash`, the runtime must return:

```text
RESOLUTION_CONFLICT
exit 24
```

The runtime must not replay or mutate the artifact.

## 8.4. Current active duplicate semantics

For the current active suspension, same-signal duplicate behavior may only be observed after one process has already committed an outcome for that current suspension.

Before the first commit, two concurrent same-signal attempts are governed by the process-level race matrix in Correction H.

After the first commit, the duplicate path is governed by the resolved-suspension lookup.

## 8.5. Prior-cycle duplicate semantics

For a prior cycle `k < current_sequence`, if `suspension_id[k]` exists in `resolved_suspensions`, duplicate semantics are identical to current resolved semantics.

| Prior cycle case | Signal hash | Outcome |
|---|---|---|
| Resolved prior suspension | Same hash | Return saved semantic result |
| Resolved prior suspension | Different hash | Exit `24`, artifact unchanged |

## 8.6. Operation result return policy

P2c must return the stored semantic result for a duplicate.

The returned payload must preserve the semantic outcome committed for the original resolution. Advisory fields may be regenerated only if they are explicitly defined as non-semantic projections.

The following rule applies:

```text
Duplicate returns the stored semantic result.
Advisory resume_argv MAY be regenerated from the current executable and current artifact path.
Advisory resume_argv is not part of byte-equivalence.
All durable semantic fields remain equivalent to the stored operation_result.
```

Durable semantic fields include:

```text
status
exit_code
run_id
correlation_id
artifact_revision
history_hash
source_hash
output_delta
active_suspension
terminal error code/message where applicable
```

If implementation cannot clearly distinguish semantic fields from advisory fields, it must return the stored `operation_result` as committed and must not regenerate any field.

## 8.7. Duplicate matrix

| Cycle addressed | Supplied ID state | Signal hash relation | Expected outcome | Exit | Artifact mutated? |
|---|---|---|---|---:|---|
| Current active | Not yet resolved | New signal | Normal resume | `0`, `20`, or runtime error | Yes if committed |
| Current active after commit | Resolved | Same hash | Stored semantic result | Original committed exit | No |
| Current active after commit | Resolved | Different hash | `RESOLUTION_CONFLICT` | `24` | No |
| Prior cycle | Resolved | Same hash | Stored semantic result from that prior resolution | Original committed exit | No |
| Prior cycle | Resolved | Different hash | `RESOLUTION_CONFLICT` | `24` | No |
| Never issued or not retained in artifact evidence | Not resolved and not active | Any | `STALE_OR_UNKNOWN_SUSPENSION` | `23` | No |
| Contradictory artifact state | Invalid | Any | `ARTIFACT_INVALID_OR_INTEGRITY_FAILURE` | `21` | No |

---

# 9. Correction H — Process-level concurrent resume race matrix

## 9.1. Problem closed by Correction H

P2b demonstrated a process-level lock conflict for the single-resume boundary. P2c must prove concurrency over multi-cycle and duplicate/conflict conditions without weakening lock evidence.

P2c must distinguish two separate classes of evidence:

1. lock-overlap race proof;
2. post-commit duplicate proof.

These are not interchangeable.

## 9.2. Lock-overlap race proof

A lock-overlap race test proves that two independent OS processes cannot both enter the critical section for the same artifact at the same time.

For a forced-overlap race, expected outcomes must include one lock conflict:

```text
[0, 26]
[20, 26]
[1, 26]
```

depending on the winner’s semantic outcome.

A result pair of:

```text
[0, 0]
[20, 20]
```

does not prove lock acquisition collision. It may prove post-commit idempotency, but it does not satisfy lock-overlap race proof.

This distinction is mandatory.

If a P2c acceptance criterion claims process-level race proof using only `[0,0]`, `[20,20]`, or equivalent idempotent pair without one lock conflict under forced overlap, the criterion fails with:

```text
BLOCKED — RACE_MATRIX_NOT_PROVEN_ON_BOTH_PLATFORMS
```

## 9.3. Post-commit duplicate proof

A post-commit duplicate test proves that once a winning process has committed, a later process presenting the same `suspension_id` and same `signal_hash` receives the saved semantic result without mutation.

Post-commit duplicate proof may legitimately observe:

```text
[0, 0]
[20, 20]
```

when the second result is served from `resolved_suspensions`.

The evidence must explicitly show that the second process did not replay, did not mutate the artifact, and did not increment revision.

## 9.4. Same-ID same-hash race

For two independent OS processes resuming the same artifact with the same `suspension_id` and the same signal value:

### Forced-overlap mode

The expected result is:

```text
one winner outcome + one exit 26
```

Example:

```text
[0, 26]
```

or:

```text
[20, 26]
```

depending on whether the winner completes or reaches next PENDING.

### Post-commit duplicate mode

A second process launched after the first committed may return the stored semantic result with the same exit code as the original committed outcome.

This is idempotency evidence, not lock-overlap evidence.

## 9.5. Same-ID different-hash race

For two independent OS processes resuming the same artifact with the same `suspension_id` but different signal values:

### Forced-overlap mode

Expected result:

```text
one winner outcome + one exit 26
```

### Post-commit conflict mode

If the loser evaluates after the winner has committed, expected result:

```text
winner outcome + exit 24
```

The artifact must reflect the winner only. The losing signal must not modify the artifact.

## 9.6. Multi-cycle boundary race

The same-ID race requirements apply at every active boundary in a multi-cycle campaign.

At boundary `N`, two concurrent processes must not both commit `sequence[N+1]`.

The invariant is:

```text
count(committed outcomes for a given (run_id, suspension_id, sequence)) <= 1
```

If two processes commit different outcomes for the same `run_id`, `suspension_id`, and `sequence`, the implementation fails with:

```text
BLOCKED — CONCURRENT_IDEMPOTENCY_VIOLATION
```

## 9.7. Late-boundary forced-overlap proof

P2c must prove process-level forced-overlap race at a later active boundary after at least one successful prior resume has already advanced the artifact.

A first-boundary race inherited from P2b is not sufficient for P2c closure.

The required late-boundary proof is:

```text
PENDING_1
resume -> PENDING_2
then force two OS processes to race on suspension_id_2
```

Expected evidence must include one winner outcome and one lock conflict:

```text
[0, 26]
[20, 26]
[1, 26]
```

If race evidence exists only for the first boundary, implementation must stop with:

```text
BLOCKED — LATE_BOUNDARY_RACE_NOT_PROVEN
```

## 9.8. Required process-level platform coverage

Every P2c race criterion must be exercised on both:

```text
ubuntu-latest
windows-latest
```

The race proof must use independent OS processes, such as `subprocess.Popen`, not threads.

Thread-based tests may be supplementary, but they do not satisfy process-level race criteria.

## 9.9. Crash-interruption cases

Crash-interruption cases are important future hardening evidence, but they are not mandatory P2c core closure criteria.

The following remain outside mandatory P2c core acceptance:

1. process killed between signal injection and artifact commit;
2. process killed after artifact commit but before lock release;
3. stale-lock owner metadata;
4. PID/hostname/timestamp lock metadata;
5. automatic stale-lock recovery;
6. force-unlock operator flow;
7. scheduler handling of stale locks.

These are future hardening candidates and require a separate approval before becoming required acceptance criteria.

P2c must not smuggle crash-recovery policy into the implementation under the name of concurrency closure.

---

# 10. Correction I — Compatibility policy across P2a/P2b artifacts

## 10.1. Problem closed by Correction I

P2c must extend P2 behavior without invalidating artifacts created by P2a or P2b.

P2c must not require migration of existing durable artifacts.

## 10.2. Schema stability

P2c must operate on:

```text
artifact_schema_version == "1.0.0"
```

P2c must not bump the artifact schema version.

If a schema bump is needed, the implementation must stop with:

```text
BLOCKED — P2C_REQUIRES_SCHEMA_EVOLUTION
```

## 10.3. P2a artifact compatibility

A P2c implementation must read and resume a P2a artifact with:

```text
revision == 1
status == PENDING
idempotency.resolved_suspensions == {}
active_suspension.sequence == 1
```

P2c must not rewrite the artifact on read merely to upgrade it.

The first resume of a P2a artifact under P2c must follow the same semantics as P2b resume.

## 10.4. P2b artifact compatibility

A P2c implementation must read and resume a P2b artifact with:

```text
revision >= 1
idempotency.resolved_suspensions populated with zero or more entries
status in {PENDING, COMPLETED, ERROR}
```

If the artifact is `PENDING`, P2c must proceed according to active suspension and idempotency rules.

If the artifact is terminal and the supplied ID resolves to an existing `resolved_suspensions` entry with the same signal hash, P2c must return the saved semantic result.

If the artifact is terminal and the supplied ID is neither resolved nor active, P2c must return stale/unknown or artifact integrity failure according to Correction F.

## 10.5. P2c artifact compatibility

Artifacts written by P2c must remain readable by P2c for later cycles.

P2c artifacts should remain readable by P2b for a single resume step where P2b semantics apply, unless P2b lacks multi-cycle closure logic. If P2b cannot fully operate on later-cycle artifacts, P2c must document the compatibility boundary in evidence and status documentation.

P2c must not silently corrupt P2b behavior.

## 10.6. No migration tooling

P2c does not introduce:

1. artifact migration command;
2. automatic schema upgrade;
3. rewrite-on-read;
4. background artifact repair;
5. artifact compaction;
6. retention policy for resolved entries.

If future stages require any of these, they must be specified separately.

---

# 11. Exit code policy

P2c must not introduce new exit codes.

P2c continues to use the existing P2/P2b code set:

| Exit code | Public meaning |
|---:|---|
| `0` | Completed |
| `1` | Runtime or committed controlled error |
| `2` | Invalid CLI input, invalid state file, invalid signal input |
| `20` | Pending |
| `21` | Artifact invalid or integrity failure |
| `22` | Resume boundary mismatch |
| `23` | Stale or unknown suspension |
| `24` | Resolution conflict |
| `25` | Unsupported durable operation or reason |
| `26` | Artifact exists or locked |

If implementation appears to require a new exit code, it must stop with:

```text
BLOCKED — NEW_EXIT_CODE_REQUIRED
```

A later amendment may authorize a new exit code, but P2c itself may not.

---

# 12. Artifact mutation policy

P2c must distinguish between mutating and non-mutating paths.

## 12.1. Mutating paths

A path may mutate the artifact only when:

1. the artifact is valid;
2. the lock is acquired;
3. the signal is valid;
4. the supplied `suspension_id` is not already resolved;
5. the supplied `suspension_id` matches the current active suspension;
6. replay reconstructs the saved boundary;
7. signal injection produces a supported outcome;
8. atomic commit succeeds.

## 12.2. Non-mutating paths

A path must not mutate the artifact when:

1. state file is invalid;
2. signal input is invalid;
3. artifact integrity validation fails;
4. supplied ID is stale or unknown;
5. supplied ID is resolved with same hash;
6. supplied ID is resolved with different hash;
7. boundary reconstruction fails;
8. lock acquisition fails;
9. P2c scope violation is detected.

## 12.3. Artifact hash and mtime

For non-mutating paths, artifact content hash must remain unchanged.

For idempotent duplicate returns, artifact `mtime` should remain unchanged where the filesystem exposes stable `mtime` behavior. If the platform does not guarantee stable precision, tests must assert content/hash equality as the primary invariant.

---

# 13. Acceptance criteria for P2c

## 13.1. Multi-cycle lifecycle criteria

### AC-P2C-01 — Three-boundary campaign

A single `run_id` must produce the lifecycle:

```text
PENDING_1 -> resume -> PENDING_2 -> resume -> PENDING_3 -> resume -> COMPLETED
```

The evidence must show:

1. a single `run_id`;
2. three distinct issued suspension IDs;
3. dense sequences `1`, `2`, `3`;
4. monotonic artifact revisions;
5. final terminal `COMPLETED`;
6. valid artifact hash after every step;
7. valid history integrity after every step.

### AC-P2C-02 — Mixed-reason campaign

The three-boundary campaign must include at least two different supported suspension reasons in the same `run_id`.

The implementation must first prove during Phase 0 that this mixed-reason campaign is representable through the current approved public language/runtime path.

If the campaign is representable, the implementation must provide an acceptance test using the canonical reason names emitted by the runtime.

If the campaign is not representable without scope expansion, implementation must stop with:

```text
BLOCKED — MIXED_REASON_CAMPAIGN_NOT_PROVEN
```

The implementation must not add parser, AST, interpreter, runtime, validator, daemon, transport, scheduler, or unrelated production changes solely to manufacture mixed-reason evidence.

### AC-P2C-03 — Sequence density

For every issued suspension in the campaign:

```text
sequence[k] == k
```

The evidence must show no skipped sequence values and no duplicate sequence values.

### AC-P2C-04 — Suspension ID uniqueness

Every issued `suspension_id` in the campaign must be unique.

The evidence must explicitly compare all issued IDs.

### AC-P2C-05 — Revision monotonicity

Every committed resume must increment artifact revision exactly once.

The evidence must show revision before and after each resume.

### AC-P2C-06 — Output suppression across cycles

Each resume must publish only the output delta after the persisted output prefix.

Previously emitted output must not be republished as fresh output.

## 13.2. Stale and unknown ID criteria

### AC-P2C-07 — Unknown ID

A never-issued ID must return:

```text
STALE_OR_UNKNOWN_SUSPENSION
exit 23
```

The artifact must remain unchanged.

### AC-P2C-08 — Prior resolved same-hash duplicate after one advance

After:

```text
PENDING_1 -> resume signal_1 -> PENDING_2
```

a duplicate call using `suspension_id_1` and `signal_1` must return the stored semantic result from the first resume and must not mutate the artifact.

### AC-P2C-09 — Prior resolved same-hash duplicate after two advances

After:

```text
PENDING_1 -> PENDING_2 -> PENDING_3
```

a duplicate call using `suspension_id_1` and the original signal must return the stored semantic result from cycle 1 resolution and must not mutate the artifact.

A duplicate call using `suspension_id_2` and the original signal must return the stored semantic result from cycle 2 resolution and must not mutate the artifact.

### AC-P2C-10 — Prior resolved different-hash duplicate

A call using any prior resolved `suspension_id` with a different signal hash must return:

```text
RESOLUTION_CONFLICT
exit 24
```

The artifact must remain unchanged.

### AC-P2C-11 — Active ID with different signal after commit

If an active suspension has already been resolved by a winning process, a later different signal for the same ID must return:

```text
RESOLUTION_CONFLICT
exit 24
```

The artifact must remain unchanged.

### AC-P2C-12 — Artifact contradiction integrity

If the artifact’s own persisted idempotency structure is malformed, hash-inconsistent, or contradicts its committed state, runtime must classify the artifact as invalid:

```text
ARTIFACT_INVALID_OR_INTEGRITY_FAILURE
exit 21
```

This criterion must not require the runtime to infer missing historical entries from data the artifact does not store.

## 13.3. Duplicate matrix criteria

### AC-P2C-13 — Same-hash duplicate of current resolved suspension

Same hash for a resolved current suspension returns stored semantic result, no mutation.

### AC-P2C-14 — Different-hash duplicate of current resolved suspension

Different hash for a resolved current suspension returns exit `24`, no mutation.

### AC-P2C-15 — Same-hash duplicate of prior suspension

Same hash for prior resolved suspension returns stored semantic result, no mutation.

### AC-P2C-16 — Different-hash duplicate of prior suspension

Different hash for prior resolved suspension returns exit `24`, no mutation.

### AC-P2C-17 — Advisory `resume_argv` rule

If saved operation result contains `resume_argv`, the implementation must either:

1. return the committed `resume_argv` exactly; or
2. explicitly treat `resume_argv` as advisory and regenerate it without changing durable semantic fields.

The evidence must specify which policy is implemented.

## 13.4. Process-level race criteria

### AC-P2C-18 — Forced-overlap same-ID same-hash race

Two OS processes must race on the same artifact, same `suspension_id`, and same signal.

The test must force overlap so that one process attempts to acquire the lock while the other holds it.

Expected evidence must include one lock conflict:

```text
[0, 26]
[20, 26]
[1, 26]
```

depending on the winner outcome.

A pure idempotent pair such as `[0,0]` does not satisfy this criterion.

### AC-P2C-19 — Post-commit same-ID same-hash duplicate

A second process after winner commit may return the stored semantic result with original outcome exit.

This criterion proves idempotency, not lock collision.

Evidence must show no artifact mutation by the duplicate process.

### AC-P2C-20 — Forced-overlap same-ID different-hash race

Two OS processes must race on the same artifact and same `suspension_id` with different signals.

Forced overlap must produce one winner outcome and one `26`.

The artifact must reflect the winner only.

### AC-P2C-21 — Post-commit same-ID different-hash conflict

A later process with a different signal for an already resolved ID must return exit `24`.

The artifact must remain unchanged.

### AC-P2C-22 — Multi-cycle race at later boundary

At boundary `N >= 2`, two OS processes must race against the same active suspension.

Exactly one process may commit the next outcome.

`sequence[N+1]` and `revision[N+1]` must each increment exactly once.

This criterion must prove the race after at least one successful prior resume. First-boundary race evidence inherited from P2b does not satisfy this criterion.

### AC-P2C-23 — Race evidence is process-level

Race tests must use independent OS processes, not threads.

Thread tests may supplement but may not replace process-level evidence.

### AC-P2C-24 — Cross-platform race evidence

Process-level race evidence must pass on both Ubuntu and Windows.

If platform-specific handling is needed, it must be documented without weakening the acceptance criterion.

## 13.5. Compatibility criteria

### AC-P2C-25 — P2a artifact consumed

P2c must resume a P2a-created PENDING artifact without schema migration.

### AC-P2C-26 — P2b artifact consumed

P2c must resume a P2b-created artifact with existing `resolved_suspensions` without schema migration.

### AC-P2C-27 — Schema version stability

Every P2c-written artifact must retain:

```text
artifact_schema_version == "1.0.0"
```

### AC-P2C-28 — No migration command

P2c must not introduce artifact migration command or rewrite-on-read behavior.

## 13.6. Regression criteria

### AC-P2C-29 — P2a non-regression

All P2a owning durable tests must continue to pass.

### AC-P2C-30 — P2b non-regression

All P2b owning durable tests must continue to pass.

### AC-P2C-31 — Full-suite differential

Full suite differential must show:

```text
new_failing_nodeids == []
```

Known baseline failures may remain only if they are already documented and not newly introduced by P2c.

---

# 14. Evidence requirements for P2c implementation

## 14.1. PR body evidence

The P2c implementation PR must include enough evidence in PR body for review before merge.

PR body evidence must include:

1. implementation head SHA;
2. base SHA;
3. changed files;
4. explicit scope proof;
5. acceptance matrix AC-P2C-01 through AC-P2C-31;
6. test node IDs for every AC;
7. process-level race outputs;
8. artifact hash before/after race tests;
9. targeted pytest results;
10. full-suite result;
11. collect-only count;
12. CI run IDs for Ubuntu and Windows;
13. known failures;
14. `new_failing_nodeids`;
15. stop-gate status.

## 14.2. Permanent evidence file policy

Permanent P2c evidence may be added in one of two ways:

### Option A — implementation PR evidence file

If approved in the implementation prompt, the P2c implementation PR may include:

```text
docs/evidence/P2C_EVIDENCE.md
```

alongside production changes.

### Option B — post-merge S1 evidence file

If the implementation PR is kept code/test-only, permanent evidence must be added in a follow-up S1 PR after post-merge verification.

The chosen option must be explicitly stated before implementation begins.

No implementation may silently omit permanent evidence.

## 14.3. Evidence file content

If `docs/evidence/P2C_EVIDENCE.md` is created, it must include:

1. P2c implementation PR URL;
2. implementation head SHA;
3. post-merge SHA;
4. command transcripts;
5. multi-cycle artifact lifecycle transcript;
6. stale and duplicate matrix transcript;
7. race matrix transcript;
8. artifact hash before/after each race;
9. CI run IDs;
10. baseline failures;
11. `new_failing_nodeids`;
12. P2c boundary;
13. S1 closure status.

## 14.4. Unverified claims

Text-only assertions without reproducible evidence are not accepted.

Any such claim must be labeled:

```text
UNVERIFIED CLAIM — NOT ACCEPTED AS P2C EVIDENCE
```

---

# 15. Stop-gates for P2c

The following stop-gates are mandatory.

```text
BLOCKED — P2C_PREMATURE_IMPLEMENTATION
BLOCKED — P2C_SCOPE_LEAK
BLOCKED — P2C_REQUIRES_SCHEMA_EVOLUTION
BLOCKED — NEW_EXIT_CODE_REQUIRED
BLOCKED — CONCURRENT_IDEMPOTENCY_VIOLATION
BLOCKED — MULTI_CYCLE_INVARIANT_NOT_PROVEN
BLOCKED — MIXED_REASON_CAMPAIGN_NOT_PROVEN
BLOCKED — STALE_ID_MATRIX_NOT_PROVEN
BLOCKED — CROSS_CYCLE_DUPLICATE_MATRIX_NOT_PROVEN
BLOCKED — LOCK_RACE_NOT_PROVEN
BLOCKED — LATE_BOUNDARY_RACE_NOT_PROVEN
BLOCKED — RACE_MATRIX_NOT_PROVEN_ON_BOTH_PLATFORMS
BLOCKED — P2A_P2B_COMPATIBILITY_NOT_PROVEN
BLOCKED — BASE_CONTRACT_MOVED
BLOCKED — NEW_PLATFORM_REGRESSION
BLOCKED — UNAPPROVED_SCOPE_EXPANSION
BLOCKED — TRANSPORT_LEAK
BLOCKED — DAEMON_LEAK
BLOCKED — SCHEDULER_LEAK
BLOCKED — AUTO_RECOVERY_LEAK
```

## 15.1. Stop-gate explanations

### `BLOCKED — P2C_PREMATURE_IMPLEMENTATION`

Raised when P2c code is started before this amendment is approved and merged.

### `BLOCKED — P2C_SCOPE_LEAK`

Raised when P2c attempts to solve daemon, transport, scheduler, inbox, auto-recovery, force-unlock, distributed store, schema migration, or any unrelated runtime area.

### `BLOCKED — P2C_REQUIRES_SCHEMA_EVOLUTION`

Raised when implementation cannot satisfy P2c with artifact schema `1.0.0`.

### `BLOCKED — NEW_EXIT_CODE_REQUIRED`

Raised when implementation appears to require an exit code not already approved.

### `BLOCKED — CONCURRENT_IDEMPOTENCY_VIOLATION`

Raised when two processes can both commit divergent outcomes for the same `run_id`, `suspension_id`, and sequence.

### `BLOCKED — MULTI_CYCLE_INVARIANT_NOT_PROVEN`

Raised when sequence, revision, suspension ID, history, output, or artifact hash invariants are not proven across the multi-cycle campaign.

### `BLOCKED — MIXED_REASON_CAMPAIGN_NOT_PROVEN`

Raised when P2c implementation cannot demonstrate at least two distinct runtime-supported suspension reasons in one `run_id` without expanding scope or modifying forbidden layers.

### `BLOCKED — STALE_ID_MATRIX_NOT_PROVEN`

Raised when stale, unknown, prior resolved, current active, and conflict cases are not all proven.

### `BLOCKED — CROSS_CYCLE_DUPLICATE_MATRIX_NOT_PROVEN`

Raised when duplicate same/different behavior is not proven for both current and prior cycles.

### `BLOCKED — LOCK_RACE_NOT_PROVEN`

Raised when claimed race evidence does not include a forced-overlap lock conflict.

### `BLOCKED — LATE_BOUNDARY_RACE_NOT_PROVEN`

Raised when process-level forced-overlap race is proven only at the first boundary and not at a later active boundary after at least one prior resume.

### `BLOCKED — RACE_MATRIX_NOT_PROVEN_ON_BOTH_PLATFORMS`

Raised when process-level race evidence is missing on Ubuntu or Windows.

### `BLOCKED — P2A_P2B_COMPATIBILITY_NOT_PROVEN`

Raised when P2a or P2b artifacts cannot be consumed without migration.

### `BLOCKED — BASE_CONTRACT_MOVED`

Raised when approved RFC, Amendment-01, or S1 base contracts moved before implementation and the implementation prompt did not rebase or re-verify.

### `BLOCKED — NEW_PLATFORM_REGRESSION`

Raised when new failing node IDs appear relative to the approved baseline.

### `BLOCKED — UNAPPROVED_SCOPE_EXPANSION`

Raised when implementation touches files or behavior outside approved scope.

### `BLOCKED — TRANSPORT_LEAK`

Raised when signal inbox, network delivery, distributed transport, or delivery semantics enter P2c.

### `BLOCKED — DAEMON_LEAK`

Raised when resident daemon, background worker, polling loop, or background service enters P2c.

### `BLOCKED — SCHEDULER_LEAK`

Raised when scheduler timeout, wall-clock timeout, retry scheduling, or external orchestration enters P2c.

### `BLOCKED — AUTO_RECOVERY_LEAK`

Raised when automatic stale-lock recovery, force unlock, lock owner metadata, or fencing strategy is implemented under P2c without separate approval.

---

# 16. Non-goals reaffirmed

P2c does not introduce:

1. signal inbox;
2. early signal delivery;
3. resident daemon;
4. background worker;
5. polling loop;
6. network delivery;
7. distributed signal transport;
8. scheduler timeout;
9. wall-clock timeout semantics;
10. logical-time timeout semantics;
11. automatic stale-lock recovery;
12. force-unlock command;
13. lock owner metadata;
14. fencing token protocol;
15. distributed locks;
16. distributed artifact store;
17. artifact migration;
18. artifact compaction;
19. retention policy for resolved entries;
20. new exit codes;
21. schema version bump;
22. external effect exactly-once claims;
23. parser changes;
24. AST changes;
25. interpreter semantic rewrite;
26. hardening/hash algorithm changes;
27. canonical JSON changes;
28. replay engine rewrite;
29. actor runtime rewrite;
30. workflow/golden replay format changes.

The at-least-once plus idempotent-resolution model from P2b remains in force.

---

# 17. Relationship to prior contracts

| Subject | Parent RFC | Amendment-01 | Amendment-02 |
|---|---|---|---|
| Initial durable run | Defined | Preserved | Preserved |
| Single resume | Defined as future/resume boundary | Correction for terminal-aware order | Preserved |
| Durable safety validator | Defined | Narrowed/confirmed | Preserved |
| Signal JSON strictness | Defined in P2 path | Confirmed | Preserved |
| Artifact hash/integrity | Defined | Preserved | Preserved |
| Boundary reconstruction | Defined | Refined for resume | Preserved |
| Same-generator continuation | Defined | Preserved | Preserved |
| Terminal duplicate handling | Partial | Defined for P2b | Extended across cycles |
| Multi-cycle lifecycle | Not fully defined | Not fully defined | Defined |
| Stale ID after later boundaries | Not fully defined | Partial | Defined |
| Cross-cycle duplicate matrix | Not defined | Partial | Defined |
| Process-level concurrent resume matrix | Partial | Single-boundary proof | Extended |
| Compatibility with P2a/P2b artifacts | Not fully defined | Preserved | Defined |
| Schema evolution | Not authorized | Not authorized | Still not authorized |
| New exit codes | Not authorized | Not authorized | Still not authorized |
| Daemon/transport/scheduler | Out of scope | Out of scope | Reaffirmed out of scope |

---

# 18. Amendment-02 PR scope

The PR that introduces this amendment may add only:

```text
docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md
```

It must not alter:

```text
docs/RFC-ASYNC-EXECUTION.md
docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md
docs/CAPABILITY_MATURITY_MATRIX.md
docs/ASYNC_DURABLE_EXECUTION_STATUS.md
docs/evidence/**
synapse/**
tests/**
examples/**
.github/**
README.md
```

If the amendment PR changes any file outside the single allowed file, it must be blocked with:

```text
BLOCKED — UNAPPROVED_SCOPE_EXPANSION
```

---

# 19. Review gates for Amendment-02

Before this amendment can merge, the following review gates must be satisfied:

1. Product Owner Review — PASS;
2. Runtime Architecture Review — PASS;
3. Replay and Effects Review — PASS;
4. CLI/Application Review — PASS;
5. Independent Scope Review — PASS;
6. Evidence Discipline Review — PASS;
7. P2c Boundary Review — PASS.

Each review must explicitly check that:

1. P2c does not rewrite P2b;
2. no daemon/transport/scheduler/inbox scope is introduced;
3. no schema version bump is required;
4. no new exit code is required;
5. process-level race proof remains mandatory;
6. late-boundary race proof remains mandatory;
7. post-commit duplicate proof is not confused with lock-overlap race proof;
8. mixed-reason campaign is treated as a Phase 0 proof obligation before implementation;
9. artifact compatibility remains explicit;
10. implementation remains unauthorized until amendment merge.

---

# 20. Status after Amendment-02 merge

After this amendment is approved and merged:

```text
P2c contract: APPROVED
P2c implementation: AUTHORIZED, not started
P2 overall: PARTIAL
P2a: CLOSED
P2b: CLOSED
S1 after P2b: SYNCED
```

Merging this amendment does not implement P2c.

Merging this amendment authorizes preparation of a P2c implementation prompt.

---

# 21. Definition of Done for P2c

P2c reaches `CLOSED` only when all of the following are true:

1. Amendment-02 is approved and merged into `main`;
2. P2c implementation PR is opened only after Amendment-02 merge;
3. P2c implementation PR does not violate approved scope;
4. all AC-P2C-01 through AC-P2C-31 are PASS;
5. process-level race matrix is proven on Ubuntu and Windows;
6. late-boundary race proof is proven after at least one prior successful resume;
7. mixed-reason campaign proof is satisfied or explicitly blocked before implementation;
8. P2a and P2b non-regression is proven;
9. compatibility with P2a and P2b artifacts is proven;
10. no new exit codes are introduced;
11. artifact schema remains `1.0.0`;
12. no daemon, inbox, scheduler, transport, auto-recovery, force-unlock, lock owner metadata, or fencing strategy scope is introduced;
13. targeted tests pass;
14. full suite passes with `new_failing_nodeids == []`;
15. PR body is synchronized with final head SHA, final test counts, CI run IDs, known failures, and final review status before merge;
16. P2c implementation PR is merged;
17. post-merge verification on `origin/main` passes;
18. CLI smoke on `origin/main` proves the multi-cycle lifecycle;
19. permanent evidence is recorded either in the implementation PR if approved or in a follow-up S1 sync PR;
20. S1 documentation is synchronized after merge;
21. P2c is marked `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED`;
22. P2 overall is updated according to the remaining roadmap state;
23. no stop-gate was bypassed.

Until all of these conditions are true, P2c may be `MERGED` but not `CLOSED`.

---

# 22. Required team approval response

Team reviewers must respond with explicit approval or rejection for each item:

```text
1. Amendment additive rule: APPROVE / REJECT
2. Base SHA and commit anchors: APPROVE / REJECT
3. P2c product problem: APPROVE / REJECT
4. P2c in-scope list: APPROVE / REJECT
5. P2c out-of-scope list: APPROVE / REJECT
6. No schema version bump: APPROVE / REJECT
7. No new exit codes: APPROVE / REJECT
8. Multi-cycle contract: APPROVE / REJECT
9. Mixed-reason Phase 0 obligation: APPROVE / REJECT
10. Stale-ID semantics: APPROVE / REJECT
11. Duplicate matrix: APPROVE / REJECT
12. Lock-overlap race versus post-commit duplicate distinction: APPROVE / REJECT
13. Late-boundary race proof: APPROVE / REJECT
14. Process-level race evidence requirement: APPROVE / REJECT
15. Crash-interruption cases excluded from mandatory P2c core: APPROVE / REJECT
16. Compatibility policy: APPROVE / REJECT
17. Acceptance criteria AC-P2C-01..31: APPROVE / REJECT
18. Stop-gates: APPROVE / REJECT
19. Non-goals reaffirmed: APPROVE / REJECT
20. Amendment PR allowed file list: APPROVE / REJECT
21. Review gates: APPROVE / REJECT
22. Definition of Done: APPROVE / REJECT
```

If all items are approved, the next executable task is:

```text
Open documentation-only PR adding docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md
```

No P2c implementation code may be opened until this amendment PR is merged.

---

**Status of this document:** `DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED`
