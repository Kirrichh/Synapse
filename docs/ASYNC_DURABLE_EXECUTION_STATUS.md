# Статус P2 — Canonical Async Durable Execution

Статус программы: **Closed — P2a + P2b + P2c implemented and verified on main for approved CLI durable execution scope**

Канонический контракт:

- `docs/RFC-ASYNC-EXECUTION.md` — утверждён через PR #13;
- `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md` — утверждён через PR #15;
- `docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md` — утверждён через PR #20.

Историческая `DRAFT`-metadata внутри этих файлов не отменяет факт их утверждения и не является stop-gate. Утверждённые контрактные документы не переписываются этим S1-патчем.

## Текущее состояние этапов

| Этап | Статус | Evidence |
|---|---|---|
| RFC P2 | `APPROVED` | PR #13, PR #15 и PR #20 находятся в `main`. |
| P2a — Durable Initial Run | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | PR #16, merge commit `edd8bf7177aa4d5ade0c9ea6d9f03b2b75a73f60`; post-merge S1 sync commit `9f146f0e931301fa549304fa7e4c9eca9e97926c`. |
| P2b — Resume and Boundary Reconstruction | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | PR #18, post-merge commit `743e4fbc3cc6545745713d26625d4f4cd9a4d34c`; PR head before merge `6979e57c29bd2857ddde6721844bab90270af475`; final evidence in `docs/evidence/P2B_EVIDENCE.md`. |
| P2c — Idempotency, Multi-cycle and Concurrency Closure | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | PR #21, post-merge commit `4eb2ec86c91a5412ce183261000bdc884b1b0d85`; PR head before merge `ac6bd049950a20539d7306c6092af889c4baf2ff`; final evidence in `docs/evidence/P2C_EVIDENCE.md`. |
| P2 целиком | `CLOSED` | P2a durable initial run, P2b canonical resume and P2c multi-cycle/concurrency closure are production-reachable and verified for the approved CLI durable execution scope. |

## Доступный пользовательский путь после P2a + P2b + P2c

### Durable initial run

```text
python -m synapse run <program.syn>
  --durable
  --state-dir <existing-directory>
  [--run-id <id>]
  [--correlation-id <id>]
  [--input-file <json-file|->]
```

### Durable resume

```text
python -m synapse resume
  --state-file <artifact.json>
  --suspension-id <id>
  --signal-file <json-file|->
```

Durable stdout содержит один JSON document. Диагностика не должна раскрывать raw request, prompt, signal или initial-binding secret.

## Поддерживаемые исходы P2

| Exit code | Статус | Смысл |
|---:|---|---|
| `0` | `COMPLETED` | Программа завершилась; terminal artifact сохранён. |
| `1` | `ERROR` | Controlled runtime/artifact failure. |
| `2` | `ERROR` | Невалидный durable input, state directory, state file или signal input. |
| `20` | `PENDING` | Поддерживаемая suspension boundary сохранена. |
| `21` | `ERROR` | Artifact invalid or integrity failure. |
| `22` | `ERROR` | Resume boundary mismatch. |
| `23` | `ERROR` | Stale or unknown suspension. |
| `24` | `ERROR` | Resolution conflict. |
| `25` | `ERROR` | Durable-safety validator отклонил неподдерживаемую операцию или reason. |
| `26` | `ERROR` | Artifact уже существует либо sibling lock занят. |

## Реализованный контракт P2a

P2a предоставляет:

- канонический `run --durable`;
- исходы `COMPLETED`, `PENDING` и структурированный `ERROR`;
- versioned Durable Run Artifact;
- embedded source и initial bindings;
- полный dynamic AST inventory и fail-closed durable-safety validator;
- проверенный subset `AwaitExpr`, `SuspendExpr`, `LLMCall`, actor spawn/send и replay-recorded builtins;
- `suspension_id` и boundary fingerprint первой suspension;
- full-payload `payload_hash` без публикации raw payload;
- history count/chain/final-hash через существующий `hash_event_chain`;
- sibling lock до effects;
- same-directory temp write, file fsync и `os.replace`;
- process-level lock evidence на Ubuntu и Windows;
- сохранение execution identity в controlled failures;
- стабильную классификацию filesystem failures;
- запрет async descendants внутри `AssertStmt`.

## Реализованный контракт P2b

P2b предоставляет:

- каноническую команду `python -m synapse resume`;
- fail-closed state-file policy: existing regular non-symlink `.json`, canonical resolved path, filename must match artifact `run_id`;
- rejection of non-regular state files after symlink rejection;
- strict signal JSON loader from UTF-8-sig file or stdin, including rejection of NaN/Infinity/trailing/empty/oversize input;
- artifact integrity validation: schema version, mandatory fields, artifact hash, versions, embedded source ownership, initial binding hash, history chain, boundary self-consistency, suspension ID, output state and idempotency entry integrity;
- one application-level boundary projection owner where sequence stays outside `boundary_fingerprint` and inside `suspension_id`;
- resume replay from `replay_state.source_code` through saved boundary;
- natural transition from REPLAY to LIVE mode before signal injection;
- full replay cursor consumption and output-prefix verification;
- same-generator continuation via `generator.send(signal)`;
- output-prefix suppression, publishing only `output_delta` after persisted prefix;
- atomic commit of COMPLETED, ERROR or next PENDING outcome;
- terminal duplicate handling: same signal returns stored semantic result without replay or artifact mutation;
- conflicting duplicate handling with exit `24`;
- stale/unknown suspension handling with exit `23`;
- process-level two-resume lock race proof with observed exit pair `[0, 26]`;
- PENDING→PENDING next suspension mechanics with sequence-aware IDs.

## Реализованный контракт P2c

P2c предоставляет:

- full multi-cycle campaign evidence: `PENDING_1 -> PENDING_2 -> PENDING_3 -> COMPLETED` under one `run_id`;
- dense sequence evidence `[1, 2, 3]`;
- revision monotonicity evidence `[1, 2, 3, 4]`;
- unique suspension IDs across multi-cycle boundaries;
- `artifact_schema_version == "1.0.0"` across writes;
- three resolved idempotency entries after three committed resumes;
- history integrity final hash verified after every cycle;
- output-prefix suppression across cycles;
- mixed-reason campaign evidence using public runtime reasons `awaiting_external_signal` and `awaiting_promise`;
- stale old IDs after later boundaries with exit `23`;
- same-hash duplicate across cycles returning stored semantic result without artifact mutation;
- different-hash duplicate across cycles returning exit `24` without artifact mutation;
- malformed idempotency entry with recomputed top-level artifact hash returning exit `21`;
- process-level same-hash and different-hash late-boundary races on `suspension_id_2` with observed `[0, 26]` outcomes;
- winner-only artifact mutation after late-boundary race;
- loser-signal absence evidence;
- P2a/P2b artifact compatibility without migration or rewrite-on-read;
- no production code change required for P2c closure; PR #21 was tests-only evidence closure over accepted P2b mechanics.

Out of P2c and still out of scope:

- signal inbox;
- daemon;
- network delivery;
- scheduler timeout;
- auto stale-lock recovery;
- force unlock;
- distributed signal transport;
- new exit codes;
- parser, AST, interpreter, replay engine or actor runtime expansion.

## Post-merge verification P2b

Проверено после merge PR #18:

- PR #18 имеет статус `merged`;
- post-merge P2b commit: `743e4fbc3cc6545745713d26625d4f4cd9a4d34c`;
- PR head before merge: `6979e57c29bd2857ddde6721844bab90270af475`;
- base before PR #18: `9f146f0e931301fa549304fa7e4c9eca9e97926c`;
- сравнение PR head `6979e57...` с текущим `main` показывает один merge commit и ноль файловых различий;
- отдельный automatic GitHub Actions run на merge commit отсутствует, поэтому post-merge record опирается на manual/team verification against `origin/main` plus successful PR-head CI;
- PR-head P2 Durable Initial Run run `27751331659` завершён успешно на Ubuntu и Windows;
- PR-head Version Sync Check run `27751331647` завершён успешно;
- post-merge owning durable tests: `71 passed, 1 skipped`;
- post-merge system execution path tests: `15 passed`;
- post-merge collect-only: `1507 tests collected`;
- post-merge full suite: `1488 passed, 13 skipped, 6 известных baseline Windows/Git failures`;
- новые failures отсутствовали;
- post-merge CLI smoke на fresh temporary directory подтвердил `run --durable -> PENDING` и `resume -> COMPLETED`.

## Post-merge verification P2c

Проверено после merge PR #21:

- PR #21 имеет статус `merged`;
- post-merge P2c commit: `4eb2ec86c91a5412ce183261000bdc884b1b0d85`;
- PR head before merge: `ac6bd049950a20539d7306c6092af889c4baf2ff`;
- base before PR #21: `8dabc543dfa10494b0c869593c81e56589e80164`;
- `main` указывает на `4eb2ec86c91a5412ce183261000bdc884b1b0d85`;
- сравнение PR head `ac6bd049...` с текущим `main` показывает один merge commit и ноль файловых различий;
- сравнение base `8dabc543...` с текущим `main` показывает только изменение `tests/test_durable_execution.py`;
- отдельный automatic GitHub Actions run на merge commit отсутствует, поэтому post-merge record честно опирается на tree-equivalence merge verification plus successful PR-head CI;
- PR-head P2 Durable Initial Run run `27766927801` завершён успешно на Ubuntu и Windows;
- PR-head Version Sync Check run `27766927965` завершён успешно;
- post-merge workflow runs на merge commit `4eb2ec86...` отсутствуют;
- P2c owning tests before merge: `6 passed`;
- durable owning tests after P2c changes: `77 passed, 1 skipped`;
- system execution path tests: `15 passed`;
- collect-only: `1513 tests collected`;
- full suite: `1494 passed, 13 skipped, 6 известных baseline Windows/Git failures`;
- новые failures отсутствовали;
- permanent P2c evidence summary recorded in `docs/evidence/P2C_EVIDENCE.md`.

## Known future findings outside P2 closure

Future findings such as the consensus facade case `with [] quorum 1 -> committed=True` and affective ID nondeterminism are tracked outside P2c closure. They do not affect P2 closed status for the approved CLI durable execution scope and should be handled by later capability stages such as P3/P4.

## Evidence policy

Executor-side Phase 0 file hashes and local paths are recorded in PR #18 evidence comment. Raw files were not committed because P2b scope forbade new tracked evidence files. Product Owner accepted Phase 0 for technical purposes, supported by reviewer-side addendum. The permanent P2b evidence summary is recorded in `docs/evidence/P2B_EVIDENCE.md`.

P2c implementation PR #21 was tests-only and intentionally deferred permanent evidence placement to post-merge S1. The permanent P2c evidence summary is recorded in `docs/evidence/P2C_EVIDENCE.md`.

## Future merge-gate

Перед merge будущих product PR тело PR должно отражать final head SHA, final test counts, CI run IDs, known failures и финальный review status. Это правило предотвращает evidence mismatch между фактическим кодом и публичной записью ревью.

## Следующий этап

P2 canonical async durable execution is closed for the approved CLI durable execution scope. Next capability stages remain outside P2:

- P3 — content-sensitive distributed consensus RFC/evidence;
- P4 — habit activation/suppression evidence;
- P5 — CVM/tree-walker conformance;
- P6 — AS2 production reachability decision.
