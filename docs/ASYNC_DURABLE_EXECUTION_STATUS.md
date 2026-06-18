# Статус P2 — Canonical Async Durable Execution

Статус программы: **Partial — P2a + P2b implemented and verified on main; P2c requires RFC**

Канонический контракт:

- `docs/RFC-ASYNC-EXECUTION.md` — утверждён через PR #13;
- `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md` — утверждён через PR #15.

Историческая `DRAFT`-metadata внутри этих файлов не отменяет факт их утверждения и не является stop-gate. Утверждённые контрактные документы не переписываются этим S1-патчем.

## Текущее состояние этапов

| Этап | Статус | Evidence |
|---|---|---|
| RFC P2 | `APPROVED` | PR #13 и PR #15 находятся в `main`. |
| P2a — Durable Initial Run | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | PR #16, merge commit `edd8bf7177aa4d5ade0c9ea6d9f03b2b75a73f60`; post-merge S1 sync commit `9f146f0e931301fa549304fa7e4c9eca9e97926c`. |
| P2b — Resume and Boundary Reconstruction | `IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | PR #18, post-merge commit `743e4fbc3cc6545745713d26625d4f4cd9a4d34c`; PR head before merge `6979e57c29bd2857ddde6721844bab90270af475`; final evidence in `docs/evidence/P2B_EVIDENCE.md`. |
| P2c — Idempotency, Multi-cycle and Concurrency Closure | `RFC_REQUIRED / NOT IMPLEMENTED` | Multi-cycle campaigns, stale IDs across later boundaries and extended duplicate/concurrent resume closure require `RFC-ASYNC-EXECUTION-AMENDMENT-02` before code. |
| P2 целиком | `PARTIAL` | P2a durable initial run and P2b canonical single-resume are production-reachable; P2c remains required for full durable lifecycle closure. |

## Доступный пользовательский путь после P2a + P2b

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

## Поддерживаемые исходы P2a/P2b

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

## Нереализованная P2c-граница

P2c remains responsible for:

- full multi-cycle campaigns;
- stale old IDs after later boundaries;
- duplicate same/different signals across multi-cycle;
- process-level concurrent resume closure beyond P2b;
- compatibility policy across P2a/P2b artifacts where needed.

Out of P2c:

- signal inbox;
- daemon;
- network delivery;
- scheduler timeout;
- auto stale-lock recovery;
- force unlock;
- distributed signal transport;
- new exit codes unless separately approved.

P2c code must not start before `RFC-ASYNC-EXECUTION-AMENDMENT-02` is approved.

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

## Evidence policy

Executor-side Phase 0 file hashes and local paths are recorded in PR #18 evidence comment. Raw files were not committed because P2b scope forbade new tracked evidence files. Product Owner accepted Phase 0 for technical purposes, supported by reviewer-side addendum. The permanent evidence summary is recorded in `docs/evidence/P2B_EVIDENCE.md`.

## Future merge-gate

Перед merge будущих product PR тело PR должно отражать final head SHA, final test counts, CI run IDs, known failures и финальный review status. Это правило предотвращает evidence mismatch между фактическим кодом и публичной записью ревью.

## Следующий этап

Следующий contract stage: **P2c — Idempotency, Multi-cycle and Concurrency Closure**.

P2c starts with `RFC-ASYNC-EXECUTION-AMENDMENT-02`; implementation PR must wait for that contract.
