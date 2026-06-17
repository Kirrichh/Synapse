# Статус P2 — Canonical Async Durable Execution

Статус программы: **Partial / implementation in progress**

Канонический контракт:

- `docs/RFC-ASYNC-EXECUTION.md` — утверждён через PR #13;
- `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md` — утверждён через PR #15.

Историческая `DRAFT`-metadata внутри этих файлов не отменяет факт их утверждения и не является stop-gate. Утверждённые контрактные документы не переписываются этим S1-патчем.

## Текущее состояние этапов

| Этап | Статус | Evidence |
|---|---|---|
| RFC P2 | `APPROVED` | PR #13 и PR #15 находятся в `main`. |
| P2a — Durable Initial Run | `MERGED` | PR #16, merge commit `edd8bf7177aa4d5ade0c9ea6d9f03b2b75a73f60`. |
| P2b — Resume and Boundary Reconstruction | `NOT IMPLEMENTED` | Каноническая команда `resume`, replay до boundary и signal injection отсутствуют. |
| P2c — Idempotency, Multi-cycle and Concurrency Closure | `NOT IMPLEMENTED` | Duplicate resolution, multi-cycle и concurrent resume отсутствуют. |
| P2 целиком | `PARTIAL` | Initial durable run production-reachable; полный durable lifecycle ещё не замкнут. |

## Доступный пользовательский путь после P2a

```text
python -m synapse run <program.syn>
  --durable
  --state-dir <existing-directory>
  [--run-id <id>]
  [--correlation-id <id>]
  [--input-file <json-file|->]
```

Поддерживаемые исходы:

| Exit code | Статус | Смысл |
|---:|---|---|
| `0` | `COMPLETED` | Программа завершилась; terminal artifact сохранён. |
| `1` | `ERROR` | Controlled runtime/artifact failure. |
| `2` | `ERROR` | Невалидный durable input или state directory. |
| `20` | `PENDING` | Первая поддерживаемая suspension boundary сохранена. |
| `25` | `ERROR` | Durable-safety validator отклонил неподдерживаемую операцию или reason. |
| `26` | `ERROR` | Artifact уже существует либо sibling lock занят. |

Durable stdout содержит один JSON document. Диагностика не должна раскрывать raw request, prompt, signal или initial-binding secret.

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

## Нереализованная граница

P2a **не** предоставляет:

- рабочую команду `python -m synapse resume`;
- загрузку и проверку существующего artifact для продолжения;
- deterministic replay до сохранённой suspension boundary;
- предъявление и проверку `suspension_id`;
- signal injection;
- output-prefix suppression после replay;
- resolved-suspension idempotency;
- conflicting-resolution failure semantics;
- несколько последовательных suspension/resume cycles;
- concurrent resume closure;
- signal inbox, daemon, timeout service или network delivery.

Поле `resume_argv` в PENDING result является канонической advisory projection будущего P2b CLI, но сама операция `resume` появится только после реализации P2b.

## Post-merge verification

Проверено после merge PR #16:

- PR #16 имеет статус `merged`;
- merge commit: `edd8bf7177aa4d5ade0c9ea6d9f03b2b75a73f60`;
- merged head: `8036276b6566e9b0000d18d547eaa2e672c00e63`;
- сравнение merged head с текущим `main` показывает один merge commit и **ноль файловых различий**;
- следовательно, файловое дерево `main` совпадает с деревом, прошедшим P2a CI;
- S1 status-sync branch создана непосредственно от merge commit и изменяет только `docs/`;
- post-merge P2 Durable Initial Run run `27710602628` завершён успешно на Ubuntu и Windows;
- post-merge Version Sync Check run `27710602441` завершён успешно;
- owning durable tests перед merge: `47 passed`;
- combined owning/system path: `62 passed`;
- full suite: `1464 passed, 12 skipped, 6 известных baseline Windows/Git failures`;
- новые failures отсутствовали.

Workflow `p2-durable.yml` не создаёт отдельный автоматический run непосредственно на merge commit, поскольку настроен на `pull_request` и `workflow_dispatch`. Однако S1 documentation PR основан на этом merge commit, не меняет production/test/workflow-файлы и повторно прогнал owning tests на Ubuntu и Windows. Тем самым merged production tree получил фактическую post-merge cross-platform проверку.

## Следующий этап

Следующий implementation stage: **P2b — Resume and Boundary Reconstruction**.

P2 не получает статус `CLOSED`, пока P2b и P2c не реализованы, не проверены и не синхронизированы документационно.
