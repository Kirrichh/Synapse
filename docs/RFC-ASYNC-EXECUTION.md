# RFC-ASYNC-EXECUTION

## Canonical Async Durable Execution Contract

**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Program:** Synapse Runtime Capability Integrity Program  
**Stage:** `P2 — Canonical Async Durable Execution`  
**RFC revision:** `1.0-draft`  
**Artifact schema:** `synapse.durable-run/1.0.0`  
**Status:** `DRAFT — REVIEW REQUIRED`  
**Reference TARGET_SHA:** `202db508cb22ce99e3f4ace9c3921354ce9db17e`  
**Patch unit:** `P2-RFC-01`  
**RFC patch scope:** `docs/RFC-ASYNC-EXECUTION.md` only

---

## 1. Product Statement

Synapse предоставляет канонический CLI-путь для запуска `.syn`-программ, содержащих поддерживаемые durable suspension points.

Программа:

1. запускается через основной `python -m synapse run`;
2. выполняется существующим tree-walker `Interpreter.interpret_async()`;
3. доходит до completion, ошибки либо поддерживаемой suspension boundary;
4. при suspension атомарно сохраняет Durable Run Artifact;
5. завершает OS-процесс со статусом `PENDING`;
6. позднее возобновляется отдельной командой `python -m synapse resume`;
7. восстанавливает committed state через embedded source, initial bindings и deterministic replay;
8. проверяет совпадение фактической и сохранённой suspension boundary;
9. инжектирует внешний result;
10. продолжает выполнение до следующей suspension либо terminal outcome.

P2 не сериализует Python generator frame и не заявляет exactly-once для внешних действий.

P2 гарантирует восстановление от последней успешно зафиксированной durable boundary только для программ, прошедших durable-safety validation, и только для effects, replay-safety которых доказана настоящим RFC.

---

## 2. Product Problem

В Runtime существует внутренняя coroutine-модель:

```text
Interpreter.interpret_async()
→ yield Suspension
→ flow.send(value)
```

Она продолжает живой Python generator внутри одного процесса.

Канонический CLI использует sync path:

```text
python -m synapse run
→ synapse.application.execute_file()
→ synapse.run()
→ Interpreter.interpret()
```

Sync path честно отклоняет `await`, `suspend` и другие async-only operations.

Отсутствуют:

- поддерживаемый CLI durable mode;
- формализованный `PENDING` result;
- production Durable Run Artifact;
- restart/resume operation;
- suspension operation identity;
- application-level scheduler step;
- cross-process idempotency;
- output-delta contract;
- durable-safe program validation;
- формальные exit codes P2.

---

## 3. Goals

P2 обязан предоставить:

1. канонический CLI `run --durable`;
2. отдельную команду `resume`;
3. короткоживущую process-per-step модель;
4. формализованные `PENDING`, `COMPLETED`, `ERROR`;
5. versioned Durable Run Artifact;
6. embedded source ownership;
7. initial bindings ownership;
8. deterministic replay до suspension boundary;
9. `suspension_id`;
10. deterministic boundary fingerprint;
11. multi-cycle resume;
12. idempotent duplicate resolution;
13. fail-closed conflicting resolution;
14. cross-process single-writer protection;
15. atomic artifact replacement;
16. output-delta semantics;
17. replay-safe effect matrix;
18. static durable-safety validation;
19. честно ограниченную crash/retry guarantee;
20. совместимость существующего sync CLI.

---

## 4. Non-Goals

P2 не реализует:

- resident daemon;
- background worker;
- signal inbox;
- early signals;
- automatic polling;
- внутренний timer service;
- wall-clock timeout decisions;
- mailbox delivery;
- network delivery;
- migration активного кода;
- code upgrade активного run;
- CVM durable execution;
- bytecode continuation;
- сериализацию Python generator/frame;
- несколько одновременных active suspensions;
- parallel fork/join waits;
- distributed artifact store;
- NFS/distributed-filesystem locking;
- automatic stale-lock recovery;
- encryption-at-rest;
- exactly-once external effects;
- автоматический retry;
- validator cache;
- warning-only execution неподдерживаемых операций;
- изменение ReplayEngine;
- изменение snapshot/checkpoint formats;
- изменение parser или AST;
- изменение ActorRuntime;
- изменение Interpreter.

---

## 5. Canonical Ownership

| Контракт | Канонический владелец |
|---|---|
| CLI parsing | `synapse.cli` |
| Durable JSON rendering | `synapse.cli` |
| Durable execution lifecycle | `synapse.application` |
| Static durable-safety validation | `synapse.application` |
| Artifact persistence | `synapse.application` |
| Artifact locking | `synapse.application` |
| Async AST execution | `Interpreter.interpret_async()` |
| Suspension runtime semantics | `Interpreter` |
| Promise creation/resolution | `ActorRuntime` |
| Replay cursor and event matching | `ReplayEngine` |
| Execution history payload | `Interpreter.execution_history` |
| History integrity | existing `hash_event_chain` / `verify_event_chain` |
| External signal production | user or external scheduler |
| Retry decision | external caller |
| Network delivery | external transport component |
| Timeout policy | external scheduler |

`cli.py` не содержит scheduler logic.

`application.py` не переопределяет AST semantics, promise semantics или replay event matching.

---

## 6. Terminology

### 6.1. Run

Один durable lifecycle, идентифицированный `run_id`.

Run может проходить несколько последовательных suspension/resume cycles.

### 6.2. Step

Одно выполнение OS-процесса: initial `run` либо `resume`.

Step заканчивается `PENDING`, `COMPLETED` или `ERROR`.

### 6.3. Durable boundary

Успешно сохранённое состояние после suspension, completion или deterministic runtime error.

### 6.4. Active suspension

Единственная suspension, ожидающая external result.

### 6.5. `suspension_id`

Operation token конкретной suspension. Он предъявляется caller при `resume`, но не является runtime cursor и не содержится в `Suspension`.

### 6.6. Boundary fingerprint

Детерминированный hash наблюдаемой runtime boundary, повторно вычисляемый после replay.

### 6.7. Committed effect

Effect, результат и связанная history которого вошли в успешно сохранённый artifact.

### 6.8. In-flight effect

Effect, выполненный после последней committed boundary, но до следующего atomic artifact commit.

---

## 7. Lifecycle State Machine

```text
START --run --durable--> RUNNING
RUNNING --supported suspension--> PENDING
RUNNING --completion--> COMPLETED
RUNNING --deterministic error--> ERROR
PENDING --resume--> RUNNING
```

Инварианты:

1. OS-процесс не остаётся ждать signal.
2. Одновременно существует одна `active_suspension`.
3. После каждого нового `PENDING` создаётся новый `suspension_id`.
4. `COMPLETED` и `ERROR` являются terminal artifact states.
5. Terminal artifact не удаляется автоматически.
6. Process-local `RUNNING` не является persisted artifact status.
7. Несохранённый step не считается committed.

---

## 8. Canonical CLI

### 8.1. Первичный запуск

```text
python -m synapse run <program.syn>
  --durable
  --state-dir <directory>
  [--run-id <run-id>]
  [--correlation-id <external-id>]
  [--input-file <bindings.json|->]
```

Пример:

```powershell
python -m synapse run examples/cross_node_promise.syn `
  --durable `
  --state-dir C:\synapse_states `
  --input-file C:\inputs\cross-node.json
```

`cross-node.json`:

```json
{
  "promise_token": "remote-job-42"
}
```

**Важно:** P2 не выполняет cross-node delivery. Внешний scheduler, оператор или transport component должен получить результат и вызвать `synapse resume`. Название `cross_node_promise.syn` демонстрирует ожидание внешнего результата, а не встроенную сетевую доставку.

### 8.2. Resume

```text
python -m synapse resume
  --state-file <artifact.json>
  --suspension-id <suspension-id>
  --signal-file <payload.json|->
```

Пример:

```powershell
python -m synapse resume `
  --state-file C:\synapse_states\run-a91f.json `
  --suspension-id susp-3b773f... `
  --signal-file C:\signals\result.json
```

Stdin:

```powershell
Get-Content C:\signals\result.json |
  python -m synapse resume `
    --state-file C:\synapse_states\run-a91f.json `
    --suspension-id susp-3b773f... `
    --signal-file -
```

### 8.3. CLI restrictions

Для P2 запрещены:

- `run --resume`;
- `--signal-value`;
- `--value-json`;
- `--force`;
- `--cleanup`;
- `--show-sensitive`;
- `--verbose-secrets`;
- automatic artifact lookup по `run_id`;
- `--durable` вместе с `--record`;
- `--durable` вместе с `-c/--source`;
- изменение source при resume;
- resume без `suspension_id`;
- signal для несуществующего `PENDING` artifact.

### 8.4. State layout

P2 использует плоский layout:

```text
<state-dir>/<run-id>.json
<state-dir>/<run-id>.json.lock/
```

Поддиректория на каждый run не создаётся.

---

## 9. Initial Bindings Contract

`--input-file`:

- принимает strict JSON object;
- `-` обозначает stdin;
- не принимает scalar или array;
- сохраняется в artifact;
- повторно применяется перед каждым replay;
- не выводится полностью в stdout;
- является частью boundary integrity.

Initial binding key разрешён, только если:

1. соответствует lexical grammar обычного identifier;
2. не входит в `Lexer.KEYWORDS`;
3. не входит в текущий builtin registry;
4. не совпадает с уже существующим binding после `bootstrap_global_env()`;
5. не совпадает с именем top-level agent/function, объявленным source;
6. не начинается с application-reserved prefix `__synapse_`.

Не запрещаются обычные имена с `_`; `promise_token` является допустимым binding.

При initial run application применяет binding через:

```python
interpreter.global_env.define(name, deep_copied_json_value)
```

При resume:

1. `load_snapshot(replay_state)` сбрасывает и bootstraps `global_env`;
2. application повторно применяет persisted initial bindings через `global_env.define()`;
3. затем запускается `interpret_async()`.

Поле `initial_bindings` обязательно даже при пустом object.

---

## 10. Identifier Contract

### 10.1. `run_id`

`run_id` является collision-resistant identifier, создаваемым один раз и сохраняемым в artifact.

Он не обязан быть детерминированным.

Пользовательское значение:

```text
^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
```

Запрещены path separators, `..`, drive prefixes, control characters, leading dot, существующий artifact или lock.

Сгенерированное значение:

```text
run-<uuid4-hex>
```

### 10.2. `correlation_id`

`correlation_id` является опциональным внешним identifier, не используется для artifact path и не участвует в idempotency.

### 10.3. `suspension_sequence`

Первая suspension получает `sequence = 1`.

Каждая следующая committed suspension получает `previous sequence + 1`.

Sequence не увеличивается при duplicate resume, validation failure, integrity failure или crash до commit.

### 10.4. `suspension_id`

```text
suspension_id =
  "susp-" + sha256(
    "synapse-p2-suspension-v1"
    || run_id
    || suspension_sequence
    || boundary_fingerprint
  )
```

Boundary fingerprint вычисляется до `suspension_id`.

`suspension_id`:

- не создаётся Interpreter;
- не является promise ID;
- не является replay cursor;
- сохраняется в `active_suspension`;
- обязателен для `resume`.

---

## 11. CLI Result Contract

Durable-команда пишет в stdout ровно один JSON document. Diagnostics идут в stderr.

### 11.1. PENDING

```json
{
  "result_schema_version": "1.0.0",
  "status": "PENDING",
  "exit_code": 20,
  "run_id": "run-a91f",
  "correlation_id": null,
  "artifact_path": "C:/synapse_states/run-a91f.json",
  "artifact_revision": 1,
  "suspension_id": "susp-3b773f...",
  "suspension_reason": "awaiting_promise",
  "promise_id": "await:remote-job-42",
  "history_hash": "sha256:...",
  "source_hash": "sha256:...",
  "request_hash": "sha256:...",
  "output_delta": [],
  "resume_argv": [
    "<current sys.executable>",
    "-m",
    "synapse",
    "resume",
    "--state-file",
    "C:/synapse_states/run-a91f.json",
    "--suspension-id",
    "susp-3b773f...",
    "--signal-file",
    "<path|->"
  ]
}
```

`resume_argv` является advisory projection и формируется при ответе из текущего `sys.executable`. Абсолютный executable path не является durable compatibility owner и не сохраняется как обязательное artifact field.

### 11.2. COMPLETED

```json
{
  "result_schema_version": "1.0.0",
  "status": "COMPLETED",
  "exit_code": 0,
  "run_id": "run-a91f",
  "correlation_id": null,
  "artifact_path": "C:/synapse_states/run-a91f.json",
  "artifact_revision": 2,
  "history_hash": "sha256:...",
  "source_hash": "sha256:...",
  "output_delta": ["approved"],
  "program_result": null
}
```

### 11.3. ERROR

```json
{
  "result_schema_version": "1.0.0",
  "status": "ERROR",
  "exit_code": 22,
  "run_id": "run-a91f",
  "correlation_id": null,
  "artifact_path": "C:/synapse_states/run-a91f.json",
  "artifact_revision": 1,
  "error": {
    "code": "RESUME_BOUNDARY_MISMATCH",
    "message": "Replayed suspension boundary does not match the committed artifact"
  }
}
```

Public JSON не содержит stack trace, environment dump, request body, signal body, secrets или raw exception object.

---

## 12. Exit Codes

| Code | Outcome |
|---:|---|
| `0` | `COMPLETED` |
| `1` | `RUNTIME_EXECUTION_ERROR` |
| `2` | `INVALID_CLI_INPUT` |
| `20` | `PENDING` |
| `21` | `ARTIFACT_INVALID_OR_INTEGRITY_FAILURE` |
| `22` | `RESUME_BOUNDARY_MISMATCH` |
| `23` | `STALE_OR_UNKNOWN_SUSPENSION` |
| `24` | `RESOLUTION_CONFLICT` |
| `25` | `UNSUPPORTED_DURABLE_OPERATION_OR_REASON` |
| `26` | `ARTIFACT_EXISTS_OR_LOCKED` |

Код `25` применяется как при pre-execution validator rejection, так и при unsupported runtime suspension reason.

---

## 13. Durable Run Artifact

### 13.1. Artifact identity

```text
<state-dir>/<run-id>.json
```

Lock path:

```text
<state-dir>/<run-id>.json.lock/
```

### 13.2. Artifact schema

```json
{
  "artifact_schema_version": "1.0.0",
  "artifact_hash": "sha256:...",
  "status": "PENDING",
  "revision": 1,
  "run_id": "run-a91f",
  "correlation_id": null,
  "execution_engine": "tree-walker",
  "persistence_profile": "windows-file-fsync-replace-v1",
  "source": {
    "path": "examples/cross_node_promise.syn",
    "hash": "sha256:..."
  },
  "initial_bindings": {
    "value": {
      "promise_token": "remote-job-42"
    },
    "hash": "sha256:..."
  },
  "replay_state": {
    "node_id": "local",
    "source_code": "...",
    "routing_table": {},
    "outbound_packets": [],
    "mailboxes": {"global": []},
    "actor_log": [],
    "execution_history": [],
    "policies": {},
    "claims": {},
    "consequences": {},
    "verification_results": [],
    "memory_audit": [],
    "checkpoints": [],
    "spawned_actors": {},
    "promises": {},
    "promise_routes": {},
    "promise_tombstones": {},
    "llm_context_cache": {},
    "intents": {},
    "intent_audit": []
  },
  "history_integrity": {
    "event_count": 0,
    "chain": [],
    "final_hash": ""
  },
  "active_suspension": {
    "sequence": 1,
    "suspension_id": "susp-...",
    "reason": "awaiting_promise",
    "node_type": "AwaitExpr",
    "line": 7,
    "column": 21,
    "promise_id": "await:remote-job-42",
    "payload_hash": "sha256:...",
    "request_hash": "sha256:...",
    "boundary_fingerprint": "sha256:..."
  },
  "idempotency": {
    "resolved_suspensions": {}
  },
  "output_state": {
    "line_count": 0,
    "digest": "sha256:..."
  },
  "terminal": null,
  "versions": {
    "runtime": "...",
    "language": "...",
    "spec": "..."
  }
}
```

`replay_state` — curated, versioned input projection, совместимая с существующим `Interpreter.load_snapshot()`. Она не является прямым dump результата `snapshot()` и не является serialized continuation.

---

## 14. Strict P2 JSON Profile

Artifact, initial bindings, signal и public result используют strict JSON profile.

Разрешены:

- `null`;
- boolean;
- integer;
- finite float;
- string;
- list;
- object со string keys.

Запрещены:

- `NaN`;
- `Infinity`;
- `-Infinity`;
- bytes;
- set;
- tuple как отдельный type;
- Python object;
- callable;
- opaque repr;
- non-string dictionary key;
- cyclic structure.

Canonical bytes:

```python
json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

Этот профиль не меняет существующий `canonical_json` execution history.

---

## 15. Replay State Projection and `load_snapshot()`

`replay_state` содержит ровно поля, которые текущий `load_snapshot()` восстанавливает:

```text
node_id
source_code
routing_table
outbound_packets
mailboxes
actor_log
execution_history
policies
claims
consequences
verification_results
memory_audit
checkpoints
spawned_actors
promises
promise_routes
promise_tombstones
llm_context_cache
intents
intent_audit
```

Не являются durable owners P2:

- `global_env`;
- opaque `repr`;
- Python functions;
- bound methods;
- Python generator;
- `runtime_mode`;
- persisted `replay_cursor`;
- `metrics`;
- `Suspension.env`;
- VM instruction pointer.

Resume использует:

```text
new Interpreter
→ interpreter.load_snapshot(replay_state)
→ load_snapshot resets and bootstraps global_env
→ application reapplies initial_bindings with global_env.define()
→ parse embedded source
→ durable-safety validation
→ interpret_async()
→ replay to committed boundary
```

Application не создаёт отдельный manual restore helper и не становится вторым владельцем snapshot semantics.

`execution_history` обязательно. History chain без event payload недостаточна для replay, поскольку ReplayEngine читает event fields и persisted results.

### 15.1. Snapshot movement gate

Перед production implementation исполнитель обязан:

1. прочитать `load_snapshot()` полностью;
2. зафиксировать все читаемые keys;
3. доказать 100% coverage projection schema;
4. подтвердить reset/bootstrap semantics `global_env`;
5. остановиться при несовместимом изменении.

Stop-gate:

```text
BLOCKED — SNAPSHOT_CONTRACT_MOVED
```

---

## 16. Source Ownership

Канонический source owner:

```text
artifact.replay_state.source_code
```

`source.path` хранится только для provenance.

При resume:

- внешний `.syn` файл не читается;
- отсутствие внешнего файла не блокирует resume;
- изменение внешнего файла не влияет на resume;
- source нельзя заменить CLI-флагом;
- code migration не выполняется.

Проверка:

```text
sha256(replay_state.source_code UTF-8 bytes) == source.hash
```

Несовпадение является `ARTIFACT_INVALID_OR_INTEGRITY_FAILURE`.

---

## 17. Artifact and History Integrity

### 17.1. Source integrity

```text
source.hash = sha256(replay_state.source_code UTF-8 bytes)
```

### 17.2. Initial bindings integrity

```text
initial_bindings.hash = sha256(strict canonical initial bindings bytes)
```

### 17.3. History integrity

Application вызывает существующий ReplayEngine для проверки `history_integrity.chain` против `replay_state.execution_history`.

### 17.4. Artifact hash

`artifact_hash` вычисляется по strict canonical artifact без поля `artifact_hash`.

Artifact hash:

- обнаруживает случайное повреждение;
- не является digital signature;
- не защищает от атакующего с write access;
- не заменяет history chain;
- не вводится в execution history.

---

## 18. Suspension Boundary

Runtime `Suspension.to_dict()` предоставляет reason, node type, line, column, payload и environment.

P2 использует reason, node type, line, column и strict payload hash. `Suspension.env` не сохраняется как continuation.

### 18.1. Boundary fingerprint

```text
boundary_fingerprint = sha256(strict_canonical({
  "version": "1",
  "source_hash": source.hash,
  "initial_bindings_hash": initial_bindings.hash,
  "history_event_count": history_integrity.event_count,
  "history_hash": history_integrity.final_hash,
  "reason": suspension.reason,
  "node_type": suspension.node_type,
  "line": suspension.line,
  "column": suspension.column,
  "promise_id": extracted_promise_id_or_null,
  "payload_hash": sha256(strict_canonical(suspension.payload)),
  "output_line_count": output_state.line_count,
  "output_digest": output_state.digest
}))
```

### 18.2. Promise ID extraction

- `awaiting_external_signal`: `payload.promise_id`;
- `awaiting_promise`: `payload.promise_id`;
- `awaiting_llm`: `null`.

Promise ID и suspension ID имеют разные назначения.

### 18.3. Resume validation

```text
caller suspension_id == artifact.active_suspension.suspension_id
```

После replay:

```text
observed boundary_fingerprint == persisted boundary_fingerprint
```

Первая ошибка означает stale/unknown suspension. Вторая означает replay drift или artifact corruption.

---

## 19. Supported Suspension Reasons

| Runtime reason | P2 |
|---|---|
| `awaiting_external_signal` | Supported |
| `awaiting_promise` | Supported |
| `awaiting_llm` | Supported как manual resolution |
| `awaiting_message` | Unsupported |
| `awaiting_message_or_timeout` | Unsupported |
| migration request | Unsupported |
| неизвестное значение | Unsupported |

### 19.1. External signal

Signal может быть любым strict JSON value, включая `null`.

### 19.2. Awaiting promise

Signal является promise result и сохраняется существующим promise resolution path.

### 19.3. Awaiting LLM

Signal обязан быть JSON string. Пустая строка допустима. `null`, object, array, number и boolean запрещены.

P2 не вызывает LLM provider автоматически. Structured provider-error contract относится к отдельному LLM Gateway RFC.

---

## 20. Resume Algorithm

### 20.1. Before replay

1. Нормализовать artifact path.
2. Проверить local-filesystem boundary.
3. Acquire sibling exclusive lock.
4. Прочитать artifact после получения lock.
5. Проверить JSON parsing.
6. Проверить artifact schema version.
7. Проверить artifact hash.
8. Проверить source hash.
9. Проверить initial bindings hash.
10. Проверить history chain.
11. Проверить execution engine.
12. Проверить artifact status.
13. Проверить caller `suspension_id`.
14. Проверить idempotency table.
15. Прочитать и strict-validate signal.

### 20.2. Reconstruct committed boundary

16. Создать новый `Interpreter`.
17. Передать `replay_state` в существующий `load_snapshot()`.
18. Повторно применить persisted initial bindings.
19. Parse embedded `source_code`.
20. Выполнить conservative durable-safety validation всего AST.
21. Создать generator через `interpret_async()`.
22. Выполнить generator до первой suspension либо completion.
23. Потребовать полного потребления committed history.
24. Вычислить observed output prefix.
25. Сравнить output line count и digest.
26. Вычислить observed boundary fingerprint.
27. Сравнить с persisted fingerprint.

Completion до ожидаемой suspension либо другая suspension означают `RESUME_BOUNDARY_MISMATCH`.

### 20.3. Continue step

28. Передать validated signal через `generator.send(signal)`.
29. Продолжить execution до следующей supported suspension, completion, deterministic error либо unsupported suspension.

### 20.4. Commit

30. Сформировать новый `replay_state`.
31. Сформировать history chain существующим механизмом.
32. Сформировать next active suspension либо terminal state.
33. Записать resolved idempotency entry.
34. Вычислить output delta.
35. Увеличить artifact revision.
36. Вычислить artifact hash.
37. Выполнить atomic artifact write.
38. Только после успешного commit вывести result JSON.
39. Release lock.

---

## 21. Idempotency Contract

Ключ операции:

```text
suspension_id + signal_value_hash
```

```text
signal_value_hash = sha256(strict canonical signal bytes)
```

### 21.1. Active suspension + новое значение

Если supplied ID равен active suspension ID и resolution отсутствует, выполнить resume и сохранить committed operation result.

### 21.2. Resolved suspension + тот же hash

- не запускать replay;
- не менять artifact;
- вернуть ранее сохранённый public operation result;
- вернуть тот же exit code.

### 21.3. Resolved suspension + другой hash

`RESOLUTION_CONFLICT`, exit code `24`; artifact не изменяется.

### 21.4. Unknown или stale suspension

`STALE_OR_UNKNOWN_SUSPENSION`, exit code `23`; artifact не изменяется.

### 21.5. Multi-cycle protection

Старый suspension ID никогда не применяется к следующей active suspension того же run.

---

## 22. Concurrency and Locking

Каждый artifact имеет sibling lock directory:

```text
<artifact>.lock/
```

Lock создаётся **до чтения artifact** через atomic directory creation.

Если lock существует, операция немедленно завершается `ARTIFACT_EXISTS_OR_LOCKED`, exit code `26`.

P2:

- не определяет lease;
- не определяет heartbeat;
- не удаляет stale lock автоматически;
- не продолжает execution при сомнении;
- не определяет distributed lock.

Lock contract поддерживается только на local filesystem.

AC проверяется отдельными OS processes, а не threads:

- Windows CI;
- Linux CI;
- local temporary filesystem;
- ровно один process получает lock;
- второй получает exit code `26`.

---

## 23. Atomic Artifact Persistence

Required sequence:

```text
1. create unique temporary file in the artifact directory
2. write complete UTF-8 JSON
3. flush Python buffers
4. fsync temporary file
5. os.replace(temp, artifact)
6. POSIX profile: fsync parent directory
```

Temporary file не должен находиться на другом filesystem.

Persistence profiles:

```text
posix-file-and-directory-fsync-replace-v1
windows-file-fsync-replace-v1
```

P2 не выполняет silent downgrade persistence guarantee. Ошибка обязательного `fsync` либо `replace` блокирует successful commit.

Если требуемый persistence profile недоступен:

```text
BLOCKED — ATOMIC_WRITE_CONTRACT_NOT_PROVEN
```

P2 гарантирует atomic visibility полного artifact в рамках заявленного platform profile, но не universal power-loss durability, transactional external effects или distributed storage consistency.

---

## 24. Crash and Retry Semantics

### 24.1. Committed boundary guarantee

После успешного artifact commit Runtime восстанавливается от этой boundary.

Committed effects, для которых доказан replay contract, не выполняются физически повторно во время boundary reconstruction.

### 24.2. In-flight window

```text
signal injection
→ program execution
→ next artifact commit
```

Если процесс падает внутри окна:

- предыдущий artifact остаётся каноническим;
- lock может остаться;
- новый outcome не считается committed;
- автоматический retry запрещён;
- внешние effects могли произойти;
- exactly-once не заявляется.

External operations между boundaries должны использовать stable external idempotency keys.

---

## 25. Output-Delta Semantics

Текущий `_print()` преобразует arguments через `str`, соединяет их одним пробелом и добавляет одну строку в `output_buffer`.

Persisted output state:

```text
output_state.line_count = len(interpreter.output_buffer)
output_state.digest = sha256(strict_canonical_json(
  [str(line) for line in interpreter.output_buffer]
))
```

После replay application:

1. проверяет line count;
2. проверяет digest;
3. не публикует replayed prefix.

После signal injection:

```text
output_delta = output_buffer[persisted_line_count:]
```

Application сначала сохраняет artifact, затем публикует JSON result.

`Interpreter._print()` и `get_output()` не изменяются.

---

## 26. Replay-Safe Effect Model

P2 различает committed replay safety и in-flight retry safety.

| Operation | Committed replay-safe | In-flight retry-safe | P2 first implementation |
|---|---:|---:|---|
| Pure literals/expressions | Да | Да | Supported |
| Let/assign/control flow | Да при validated subtree | Да | Supported |
| Pure allowlisted builtins | Да | Да | Supported |
| `time/random/uuid` | Да через `side_effect` event | Нет общей гарантии до commit | Supported with crash boundary |
| `print` | Да через output reconstruction | Да при publish-after-commit | Supported |
| `LLMCall` manual resolution | Да через `llm_call` | Да для того же injected value | Supported |
| `suspend` | Да | Да до следующего effect | Supported |
| `await` promise | Да | Зависит от следующего step | Supported |
| Agent definition | Да | Да | Supported |
| Actor spawn | Да: committed replay повторно использует persisted `process_id` из `actor_spawned` | Нет гарантии до commit | Supported |
| Actor send/outbound intent | Да: REPLAY не повторяет mailbox/network mutation | Нет exactly-once guarantee | Supported as local/outbound intent only |
| Network delivery | Не принадлежит Runtime | Нет | Out of scope |
| Receive/mailbox wait | Частичная runtime-механика | Не входит в P2 lifecycle | Unsupported |
| Migration | Отдельный lifecycle | Нет | Unsupported |
| Memory write/forget/clear | Не доказано | Нет | Unsupported |
| Dynamic agent method | Не доказано | Нет | Unsupported |
| Agent `think()` | LLM + memory mutation | Нет | Unsupported |
| Tool call | Arbitrary host effect | Нет | Unsupported |
| Arbitrary Python member call | Не доказано | Нет | Unsupported |
| General sync fallback node | Не доказано | Не доказано | Unsupported |
| Integrate/Dream/Evolve/Fracture | Не входит в P2 audit | Не доказано | Unsupported |
| Habit/Affective/Consensus operations | Не входит в P2 audit | Не доказано | Unsupported |
| CVM operations | Другой engine | Отдельный контракт | Unsupported |

Actor support не означает встроенную network delivery.

---

## 27. Durable-Safety Validator

### 27.1. Timing

```text
parse source
→ validate entire AST
→ only then create Interpreter and execute
```

Runtime execution начинается только после успешной статической проверки.

### 27.2. Conservative approximation

Если validator не может статически доказать допустимость узла и всех достижимых descendants, операция считается `UNSUPPORTED`.

```text
False rejection безопасной программы допускается.
False acceptance недоказанной операции запрещается.
UNCLASSIFIED == UNSUPPORTED.
```

### 27.3. Implementation boundary

AST nodes являются открытыми dataclass-структурами. Validator в `synapse.application` может рекурсивно обходить `Node`, dataclass fields, lists, tuples и dictionary values без изменения parser/AST/Interpreter.

Отдельный visitor framework и validator cache не создаются.

### 27.4. Classification

```text
SUPPORTED_PURE
SUPPORTED_REPLAY_RECORDED
SUPPORTED_APPLICATION_PROJECTION
SUPPORTED_WITH_CRASH_BOUNDARY
UNSUPPORTED_MUTATION
UNSUPPORTED_HOST_EFFECT
UNSUPPORTED_EXECUTION_ENGINE
UNCLASSIFIED
```

### 27.5. Supported structural nodes

При рекурсивной проверке descendants допускаются структурные nodes, необходимые для pure/control-flow subset и утверждённых actor/promise examples.

### 27.6. CallExpr policy

Call разрешён только если syntactic callee статически доказан:

#### Pure builtins

```text
len, range, type, str, int, float, list, dict,
abs, sum, max, min, sorted, reversed,
enumerate, zip, any, all
```

#### Recorded side-effect builtins

```text
time, random, uuid
```

#### Captured print

```text
print
```

Запрещены:

- arbitrary callable variable;
- first-class function call;
- dynamic member call;
- arbitrary Python method;
- agent `think`;
- registered tool;
- unknown builtin;
- user-defined call без доказанного call-graph analysis;
- module import/dynamic loading/host-language evaluation construct, если такой node существует сейчас либо появится позднее и не будет отдельно audited.

### 27.7. Explicitly rejected operations

- governed memory write/forget/clear;
- receive/mailbox waits;
- migration;
- integrate/dream/evolve/fracture;
- collective/consensus primitives;
- Habit operations;
- Affective mutation operations;
- VM compile/run;
- dynamic host/tool calls;
- любой неаудированный node.

### 27.8. Validation failure

```text
UNSUPPORTED_DURABLE_OPERATION_OR_REASON
exit code 25
```

При failure:

- Interpreter не создаётся;
- artifact не создаётся;
- effect не выполняется;
- warning-only continuation запрещён.

### 27.9. Complete node inventory gate

Phase 0 production implementation должен сравнить:

```text
all current Node subclasses
vs
all validator classifications
```

Неохваченный класс означает stop-gate до execution.

Если validator невозможно реализовать без core changes:

```text
BLOCKED — ASYNC_REPLAY_CONTRACT_REQUIRES_CORE_CHANGE
```

---

## 28. Promise Resolution Semantics

### 28.1. `suspend`

Existing runtime:

```text
create durable promise
→ promise_created
→ awaiting_external_signal
→ injected value
→ promise_resolved
```

### 28.2. Await existing DurablePromise

Promise ID берётся из DurablePromise. Resolved result читается из existing promise state/history.

### 28.3. Synthetic await target

Если await target не является DurablePromise, existing runtime формирует deterministic synthetic ID:

```text
await:<string representation>
```

Для initial binding:

```json
{"promise_token": "remote-job-42"}
```

`await promise_token` получает ID:

```text
await:remote-job-42
```

### 28.4. No automatic continuation

Только explicit `resume` изменяет artifact.

---

## 29. Timeout Contract

P2:

- не сохраняет deadline;
- не читает wall clock для решения;
- не создаёт timer;
- не создаёт `timeout_triggered`;
- не имеет timeout exit code;
- не ожидает время в CLI;
- не вызывает resume автоматически.

External scheduler может передать обычный signal payload, например `{"kind": "timeout"}`. Интерпретация принадлежит `.syn`-программе.

---

## 30. Early Signals and Signal Inbox

P2 принимает signal только если artifact имеет `status == PENDING` и supplied `suspension_id` совпадает с active suspension.

Не поддерживаются signal до первой suspension, signal для будущей suspension, signal queue, inbox ordering, batching или replacement.

---

## 31. Terminal Artifacts

После `COMPLETED` и deterministic `ERROR` artifact сохраняется с `active_suspension = null`.

Terminal artifact нужен для audit, history verification, idempotency, duplicate resume response и terminal outcome evidence.

При integrity failure исходный artifact не изменяется.

Automatic cleanup и `--cleanup` отсутствуют.

---

## 32. Security and Privacy

### SECURITY WARNINGS

1. Artifact integrity is not authentication.
2. P2 does not provide encryption-at-rest.
3. Initial bindings, signals, prompts and replay state may contain secrets or PII.
4. Writer с доступом к state directory может изменить artifact и пересчитать `artifact_hash`.
5. Digital signatures, remote attestation and trusted storage находятся вне P2.
6. State directory не должен находиться под web root либо public file serving.
7. Требуются OS-level ACL, backup и retention policy deployment layer.

stdout не содержит raw initial bindings, signal, full request, LLM prompt, environment, memory либо stack trace.

Artifact хранит replay-required values без redaction.

---

## 33. Platform Requirements

P2 production implementation требует:

```text
Python >= 3.10
local filesystem
exclusive directory creation
file flush
os.fsync(file descriptor)
same-filesystem os.replace
```

Доказанными deployment targets не являются:

- NFS;
- clustered SMB;
- object-storage mounts;
- FUSE без подтверждённых semantics;
- mobile/WebAssembly platforms.

---

## 34. Backward Compatibility

- `python -m synapse run program.syn` без `--durable` сохраняет текущее поведение.
- `-c/--source` остаётся sync-only.
- Golden recording/replay не изменяются.
- Durable Run Artifact не совместим с Golden Replay Artifact.
- `snapshot()`, `restore_snapshot()`, `load_snapshot()`, checkpoint и mobility formats не изменяются.
- Existing history serialization, seed, algorithm и event schemas не изменяются.

---

## 35. Production Implementation Scope

После утверждения RFC разрешены:

```text
synapse/application.py
synapse/cli.py
```

Условно:

```text
synapse/__init__.py
```

только для утверждённого public Python API.

Owning tests расширяются в существующих modules. Новый `tests/test_durable_execution.py` допускается только при отсутствии подходящего owning module и должен быть прямо разрешён implementation prompt.

---

## 36. Forbidden Production Scope

Запрещено изменять:

```text
synapse/interpreter.py
synapse/runtime/actor_runtime.py
synapse/runtime/replay_engine.py
synapse/hardening.py
synapse/parser.py
synapse/ast.py
synapse/builtins.py
synapse/cvm.py
synapse/bytecode.py
synapse/persistence.py
synapse/storage_backends.py
golden replay implementation
checkpoint formats
snapshot formats
mobility formats
CVM ABI
HOST ABI
network transport
AS2
```

Запрещено добавлять daemon, background thread, polling loop, signal inbox, timer service, new durable event type, general schema registry, new scheduler framework, new replay engine, compatibility shim, alias CLI path или test-only execution path.

---

## 37. Phase 0 Mandatory Proofs for Future Implementation

До изменения production files исполнитель обязан подтвердить:

1. актуальный `origin/main` и exact patch base;
2. current CLI grammar and exit behavior;
3. full `interpret_async()` dispatch and sync fallback topology;
4. full `load_snapshot()` key coverage;
5. replay_state projection compatibility;
6. current `Suspension` shape;
7. current Promise/Actor replay semantics;
8. all current AST `Node` subclasses;
9. complete validator classification coverage;
10. existing output buffer behavior;
11. existing history hash/verify boundary;
12. local filesystem lock proof on current OS;
13. atomic write capability profile;
14. baseline targeted/full tests;
15. no core file change required.

---

## 38. Stop-Gates

```text
BLOCKED — BASE_MISMATCH
BLOCKED — BASE_CONTRACT_MOVED
BLOCKED — DIRTY_WORKTREE
BLOCKED — SCHEDULER_OWNER_UNDEFINED
BLOCKED — PROMISE_RESOLUTION_SOURCE_UNDEFINED
BLOCKED — RESUME_CONTRACT_UNDEFINED
BLOCKED — REPLAY_ARTIFACT_UNDEFINED
BLOCKED — CANONICAL_CLI_PATH_UNDEFINED
BLOCKED — EXIT_CODE_CONTRACT_UNDEFINED
BLOCKED — SNAPSHOT_CONTRACT_MOVED
BLOCKED — ASYNC_REPLAY_CONTRACT_REQUIRES_CORE_CHANGE
BLOCKED — DURABLE_VALIDATOR_REQUIRES_AST_CHANGE
BLOCKED — HISTORY_INTEGRITY_CHANGE_REQUIRED
BLOCKED — CANONICAL_SERIALIZATION_CHANGE_REQUIRED
BLOCKED — SNAPSHOT_FORMAT_CHANGE_REQUIRED
BLOCKED — ACTOR_RUNTIME_CHANGE_REQUIRED
BLOCKED — REPLAY_ENGINE_CHANGE_REQUIRED
BLOCKED — UNSUPPORTED_EFFECT_REQUIRED_BY_ACCEPTANCE
BLOCKED — CROSS_PLATFORM_LOCK_NOT_PROVEN
BLOCKED — ATOMIC_WRITE_CONTRACT_NOT_PROVEN
BLOCKED — OUTPUT_DELTA_NOT_PROVEN
BLOCKED — INITIAL_BINDINGS_CONTRACT_NOT_PROVEN
BLOCKED — TARGET_MOVED_AFTER_IMPLEMENTATION
BLOCKED — NEW_PLATFORM_REGRESSION
```

Stop-gate нельзя обходить расширением scope.

---

## 39. Acceptance Criteria for Future Implementation

### AC-P2-01 — Sync compatibility

Обычный `run` без `--durable` сохраняет текущее поведение.

### AC-P2-02 — Initial completion

Durable-программа без suspension завершается `COMPLETED`, code `0`, и сохраняет terminal artifact.

### AC-P2-03 — External suspension

Программа с `suspend` возвращает `PENDING`, code `20`, сохраняет artifact и не держит процесс открытым.

### AC-P2-04 — Promise suspension

Программа с `await` возвращает promise ID; resume применяет signal и продолжает execution.

### AC-P2-05 — Durable promise example

`examples/durable_promise.syn` выполняется `run → PENDING → resume → COMPLETED`. Committed actor spawn/send не повторяются при replay.

### AC-P2-06 — Cross-node promise example

`examples/cross_node_promise.syn` запускается с `{"promise_token": "remote-job-42"}` через `--input-file`, возвращает `PENDING`, затем завершается после resume. Это не заявляет network delivery.

### AC-P2-07 — Embedded source

Удаление либо изменение внешнего `.syn` файла после PENDING не влияет на resume.

### AC-P2-08 — Source corruption

Изменение embedded source без обновления source hash блокируется code `21`; artifact не мутируется.

### AC-P2-09 — History corruption

Изменение execution history либо chain блокируется code `21`; replay не начинается.

### AC-P2-10 — Boundary mismatch

Drift reason/node metadata/promise ID/payload/history/output даёт code `22`; signal не применяется.

### AC-P2-11 — Stale suspension

Неверный или старый `suspension_id` даёт code `23` без artifact mutation.

### AC-P2-12 — Idempotent duplicate

Повторный resume с тем же ID и signal hash не запускает replay, не меняет artifact и возвращает прежний outcome.

### AC-P2-13 — Conflicting resolution

Тот же ID с другим signal hash даёт code `24`.

### AC-P2-14 — Multi-cycle lifecycle

Две последовательные suspensions получают разные IDs; старый ID не применяется ко второй boundary.

### AC-P2-15 — Output delta

Output до предыдущей suspension не публикуется повторно после resume.

### AC-P2-16 — Unsupported memory mutation

Memory mutation отклоняется validator до исполнения, code `25`, без artifact и effect.

### AC-P2-17 — Unsupported dynamic call

Arbitrary agent/tool/Python call отклоняется до исполнения, code `25`.

### AC-P2-18 — LLM manual resolution

`awaiting_llm` не вызывает backend; resume принимает JSON string; empty string valid; `null`/non-string invalid.

### AC-P2-19 — Exclusive lock

Два параллельных processes одного artifact: один получает lock, второй code `26`; проверка на Windows и Linux.

### AC-P2-20 — Atomic write interruption

Сбой до `os.replace()` оставляет предыдущий полный artifact; partial JSON не становится каноническим.

### AC-P2-21 — Artifact retention

После `COMPLETED` и `ERROR` artifact сохраняется с `active_suspension = null`.

### AC-P2-22 — JSON channel separation

stdout содержит ровно один JSON document; diagnostics находятся в stderr.

### AC-P2-23 — Secrets boundary

Public JSON не содержит raw bindings, signal, request или prompt.

### AC-P2-24 — Existing replay/hash unchanged

Не изменены ReplayEngine, canonical history serialization, hash/verify functions, seed и event schemas.

### AC-P2-25 — Full differential gate

```text
new_failing_nodeids = empty
```

Windows failures остаются подмножеством утверждённого baseline.

---

## 40. Evidence Requirements

Implementation Evidence Report должен содержать:

- TARGET_SHA;
- environment;
- CLI contract proof;
- changed files;
- full `load_snapshot()` coverage proof;
- complete AST classification table;
- supported/unsupported effect evidence;
- history integrity proof;
- source and initial bindings ownership proof;
- boundary fingerprint proof;
- output-delta proof;
- idempotency proof;
- actor spawn/send replay proof;
- process-level lock concurrency proof on Windows and Linux;
- atomic write interruption proof;
- multi-cycle proof;
- baseline and patched test results;
- new failures;
- commit, push, PR and CI data;
- triggered stop-gates.

Тесты являются evidence утверждённого контракта, а не источником архитектуры.

---

## 41. Review and Approval

До merge RFC обязательны:

1. `Runtime Architecture Review — PASS`
2. `Replay and Effects Review — PASS`
3. `CLI/Application Review — PASS`
4. `Independent Scope Review — PASS`
5. Product Owner approval

После merge RFC:

```text
P2: RFC_APPROVED
Implementation prompt drafting: AUTHORIZED
Production implementation: NOT YET MERGED
```

Production implementation получает отдельный branch, commit и PR.

Capability Maturity Matrix не изменяется этим RFC-патчем. Она синхронизируется после production implementation в отдельном S1-контуре.

---

## 42. RFC Patch Contract

RFC-патч изменяет только:

```text
docs/RFC-ASYNC-EXECUTION.md
```

Не изменяются:

```text
docs/CAPABILITY_MATURITY_MATRIX.md
synapse/**
tests/**
examples/**
.github/**
```

Commit message:

```text
P2 RFC: define canonical async durable execution

REQ-ASYNC-CLI-01
```

RFC PR не содержит production implementation.

---

## 43. Final Decision Summary

Утверждённая модель P2:

```text
short-lived CLI process
+ application-owned lifecycle
+ tree-walker interpret_async
+ embedded source
+ persisted initial bindings
+ full committed execution history
+ boundary fingerprint
+ suspension operation ID
+ strict idempotency
+ single-writer directory lock
+ atomic artifact replacement
+ output delta
+ conservative audited durable-safe subset
+ no daemon
+ no internal timeout
+ no signal inbox
+ no exactly-once claim
+ no core runtime changes
```

P2 готов к production implementation только после merge и утверждения настоящего RFC.
