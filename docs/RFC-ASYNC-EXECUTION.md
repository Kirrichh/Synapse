# RFC-ASYNC-EXECUTION

## Canonical Async Durable Execution Contract

**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Stage:** `P2 — Canonical Async Durable Execution`  
**RFC revision:** `1.1-draft`  
**Artifact schema:** `synapse.durable-run/1.0.0`  
**Status:** `DRAFT — SELF-REVIEW CORRECTED; APPROVAL REQUIRED`  
**Reference TARGET_SHA:** `202db508cb22ce99e3f4ace9c3921354ce9db17e`  
**Patch unit:** `P2-RFC-02`  
**Scope:** `docs/RFC-ASYNC-EXECUTION.md` only

---

## 1. Product Contract

Synapse предоставляет канонический process-per-step CLI lifecycle для `.syn`-программ с утверждёнными durable suspension points:

```text
python -m synapse run <program.syn> --durable --state-dir <dir>
→ Interpreter.interpret_async()
→ PENDING | COMPLETED | ERROR

python -m synapse resume --state-file <artifact> \
  --suspension-id <id> --signal-file <json|->
→ deterministic replay to committed boundary
→ generator.send(signal)
→ PENDING | COMPLETED | ERROR
```

P2 не сериализует Python generator/frame, не создаёт daemon, timer или signal inbox и не заявляет exactly-once для external effects.

Durable recovery гарантируется только для программ, прошедших fail-closed validator, и только для effects, перечисленных как replay-safe в настоящем RFC.

---

## 2. Ownership and Scope

| Contract | Owner |
|---|---|
| CLI grammar and JSON rendering | `synapse.cli` |
| Durable lifecycle, validator, artifact, lock | `synapse.application` |
| Async AST execution | existing `Interpreter.interpret_async()` |
| Suspension semantics | existing `Interpreter` |
| Promise/actor semantics | existing `ActorRuntime` |
| Replay cursor/event matching | existing `ReplayEngine` |
| History payload | `Interpreter.execution_history` |
| History algorithm and seed | existing `hash_event_chain` / `verify_event_chain` contract |
| Signal production and retry decision | external caller/scheduler |

Production implementation may change only:

```text
synapse/application.py
synapse/cli.py
```

`synapse/__init__.py` is conditional on an approved public API. Core Runtime, parser, AST, builtins, ReplayEngine, ActorRuntime, history serialization, seed, snapshot/checkpoint/mobility formats and CVM are forbidden scope.

---

## 3. Lifecycle and Identifiers

```text
START → RUNNING → PENDING → RUNNING → ... → COMPLETED | ERROR
```

Persisted statuses are `PENDING`, `COMPLETED`, `ERROR`; `RUNNING` is process-local.

`run_id` user grammar:

```text
^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
```

Generated ID:

```text
run-<uuid4-hex>
```

First suspension has sequence `1`; each new committed suspension increments it.

```text
suspension_id = "susp-" + sha256(
  "synapse-p2-suspension-v1"
  || run_id
  || suspension_sequence
  || boundary_fingerprint
)
```

`suspension_id` is not a promise ID or replay cursor.

---

## 4. CLI and Filesystem Preconditions

Initial run:

```text
python -m synapse run <program.syn>
  --durable
  --state-dir <existing-directory>
  [--run-id <id>]
  [--correlation-id <id>]
  [--input-file <bindings.json|->]
```

Resume:

```text
python -m synapse resume
  --state-file <artifact.json>
  --suspension-id <id>
  --signal-file <payload.json|->
```

Forbidden: `run --resume`, inline signal flags, `--force`, `--cleanup`, automatic artifact lookup, source replacement on resume, `--durable --record`, and durable `-c/--source`.

`state-dir` must be an already existing writable directory on a deployment-approved local filesystem. Application validates existence/type/writability but does not claim reliable runtime detection of NFS, clustered SMB, FUSE or object-storage mounts. Invalid input exits `2` before execution.

Layout:

```text
<state-dir>/<run-id>.json
<state-dir>/<run-id>.json.lock/
```

---

## 5. Initial Bindings

`--input-file` accepts a strict JSON object and is persisted/reapplied before every replay.

A key is valid only when it:

1. matches identifier grammar;
2. is not in module-level `synapse.lexer.KEYWORDS`;
3. is not in `BUILTINS`;
4. is not created by `bootstrap_global_env()`;
5. does not start with `__synapse_`;
6. is not in the conservative source-owned identifier set.

The source-owned set includes:

- `LetStmt.name`;
- `AssignStmt.target`;
- `AgentDef.name`;
- `FnDef.name` and params;
- `FlowDef.name`;
- every parser-produced string field named `binding`;
- every other declaration/write identifier listed by the complete AST inventory.

A new declaration/write node without classification triggers a stop-gate. False rejection is allowed; ownership ambiguity is not.

A bootstrap-only `Interpreter` may be created to inspect actual bootstrap bindings. Before successful AST validation, application must not call `interpret()`, `interpret_async()`, `evaluate()` or execute source effects.

Application applies values with:

```python
interpreter.global_env.define(name, deep_copied_json_value)
```

---

## 6. Strict JSON Profile

Artifact, bindings, signal, idempotency record and public result allow only:

```text
null, boolean, integer, finite float, string,
list, object with string keys
```

Forbidden: non-finite numbers, bytes, set, tuple as a distinct type, callable, Python object, opaque repr, non-string key and cycles.

Canonical bytes:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"),
           ensure_ascii=False, allow_nan=False).encode("utf-8")
```

Before every artifact hash and commit, application recursively validates the complete artifact projection, including history, promises, mailboxes, outbound packets, actor state, suspension payload, terminal payload and idempotency results. No `default=str` or silent coercion is permitted.

If new runtime state cannot be represented, the new step is not committed and the previous artifact remains canonical.

P2 does not publish or persist arbitrary tree-walker `program_result`; completion exposes only the durable status, hashes and `output_delta`.

---

## 7. Artifact Schema

Required top-level shape:

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
  "source": {"path": "program.syn", "hash": "sha256:..."},
  "initial_bindings": {"value": {}, "hash": "sha256:..."},
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
  "history_integrity": {"event_count": 0, "chain": [], "final_hash": ""},
  "active_suspension": null,
  "idempotency": {"resolved_suspensions": {}},
  "output_state": {"line_count": 0, "digest": "sha256:..."},
  "terminal": null,
  "versions": {"runtime": "...", "language": "...", "spec": "..."}
}
```

`replay_state` is a curated projection accepted by existing `load_snapshot()`, not a direct `snapshot()` dump and not a serialized continuation.

Canonical source is `replay_state.source_code`; external source is provenance only and is never reread during resume.

`request_hash` is not part of P2. Suspension request/prompt integrity is covered by `payload_hash`; promise identity is covered by `promise_id`.

---

## 8. Exact Integrity Invariants

```text
source.hash
  == sha256(replay_state.source_code UTF-8 bytes)

initial_bindings.hash
  == sha256(strict-canonical(initial_bindings.value))

history_integrity.event_count
  == len(replay_state.execution_history)

history_integrity.chain
  == hash_event_chain(
       replay_state.execution_history,
       seed=<existing runtime history-chain seed>
     )

history_integrity.final_hash
  == history_integrity.chain[-1]["hash"]
     if history_integrity.chain else ""
```

`artifact_hash` is the SHA-256 of strict-canonical artifact bytes with `artifact_hash` omitted.

Any mismatch exits `21` before replay. Artifact hash is corruption detection, not authentication.

---

## 9. Suspension Boundary

Persisted active suspension:

```json
{
  "sequence": 1,
  "suspension_id": "susp-...",
  "reason": "awaiting_promise",
  "node_type": "AwaitExpr",
  "line": 7,
  "column": 21,
  "promise_id": "await:remote-job-42",
  "payload_hash": "sha256:...",
  "boundary_fingerprint": "sha256:..."
}
```

Fingerprint:

```text
sha256(strict-canonical({
  version,
  source_hash,
  initial_bindings_hash,
  history_event_count,
  history_hash,
  reason,
  node_type,
  line,
  column,
  promise_id,
  payload_hash,
  output_line_count,
  output_digest
}))
```

Supported reasons:

- `awaiting_external_signal`;
- `awaiting_promise`;
- `awaiting_llm` as manual string resolution.

Receive waits, timeout waits, migration and unknown reasons are unsupported (`25`). P2 never calls an LLM provider after `awaiting_llm`; resume must supply a JSON string, including an allowed empty string.

---

## 10. Initial Run Algorithm and Lock

Initial run must acquire the sibling lock before checking artifact existence and before any execution:

```text
validate CLI/state-dir
→ read and parse source/input
→ determine run_id and paths
→ atomic mkdir(<artifact>.lock/)
→ check artifact absent
→ collect source-owned names
→ validate bindings and complete AST
→ create execution Interpreter
→ apply bindings
→ interpret_async to boundary/outcome
→ project and strict-validate artifact
→ compute history/fingerprint/hash
→ atomic commit
→ release lock
→ publish one JSON result
```

Two processes with the same `run_id` cannot both execute effects. The loser exits `26`.

---

## 11. Resume and Idempotency Algorithm

Resume order is normative:

```text
acquire lock before artifact read
→ parse artifact and verify all integrity invariants
→ parse/strict-validate signal
→ compute signal_hash
→ lookup supplied suspension_id in resolved_suspensions
```

If a resolved entry exists:

- same signal hash: return its saved semantic result without replay or mutation;
- different hash: exit `24` without mutation.

This lookup occurs before requiring `status == PENDING`; therefore duplicate resolution works after `COMPLETED`, `ERROR`, or later suspension cycles.

For a new resolution:

```text
require status == PENDING and active_suspension != null
→ require supplied ID == active ID, else 23
→ load_snapshot(replay_state)
→ reapply bindings
→ parse embedded source and validate complete AST
→ interpret_async to expected committed boundary
→ require full history consumption
→ compare output prefix and boundary fingerprint
→ generator.send(signal)
→ execute to next boundary/outcome
→ create canonical operation result and idempotency entry
→ strict-validate and atomically commit new artifact
```

Completion before expected boundary, another boundary, output drift or fingerprint mismatch exits `22`; signal is not applied and artifact is unchanged.

Idempotency entry:

```json
{
  "signal_hash": "sha256:...",
  "committed_revision": 2,
  "committed_status": "COMPLETED",
  "operation_result": {
    "result_schema_version": "1.0.0",
    "status": "COMPLETED",
    "exit_code": 0,
    "run_id": "run-a91f",
    "correlation_id": null,
    "artifact_path": "C:/states/run-a91f.json",
    "artifact_revision": 2,
    "history_hash": "sha256:...",
    "source_hash": "sha256:...",
    "output_delta": ["approved"]
  }
}
```

`operation_result` excludes raw signal/request/prompt/bindings and excludes advisory `resume_argv`. For a duplicated saved `PENDING`, CLI regenerates `resume_argv` from current `sys.executable`.

---

## 12. Durable-Safety Validator

Validation order:

```text
parse source
→ inventory-aware validate entire AST
→ create execution Interpreter
→ execute
```

`UNCLASSIFIED == UNSUPPORTED`; false rejection is allowed, false acceptance is forbidden.

### 12.1. Complete supported first-implementation nodes

| Node | Constraint |
|---|---|
| `Program`, `ExprStmt` | all descendants validated |
| `LetStmt`, `AssignStmt` | value validated; identifier ownership proven |
| `Literal`, `Variable` | strict value / statically known identifier |
| `BinaryExpr`, `UnaryExpr` | operands validated; existing operator |
| `ListExpr`, `DictExpr` | descendants validated; dict keys strings |
| `IfStmt` | condition and both branches validated |
| `AffectivePadLiteral`, `DecayExpr` | finite/scalar pure value only |
| `PromptExpr` | string template; args validated |
| `AssertStmt` | pure condition/message; no suspension descendant |
| `AgentDef` | methods empty; no stateful energy/soulprint options |
| `CallExpr` | only direct allowlisted builtin or approved context below |
| `SpawnExpr` | approved constructor context below |
| `SendStmt` | proven spawned actor-ref receiver |
| `AwaitExpr` | approved await context below |
| `SuspendExpr` | validated request; strict runtime value |
| `LLMCall` | validated prompt; manual suspension only |

Every other current `Node` subclass is unsupported, including functions/flows, loops, return/try-catch, imports, receive, migration, memory, policy/governance, cognition, collective operations, affective mutation, habits, VM and every general sync fallback.

### 12.2. Ordinary `CallExpr`

Allowed direct `Variable(name)` calls:

```text
len, range, type, str, int, float, list, dict,
abs, sum, max, min, sorted, reversed,
enumerate, zip, any, all,
time, random, uuid, print
```

`map` and `filter` are forbidden because they require a first-class callable. Arbitrary callable variables, user functions, agent methods, tools and Python members are forbidden.

### 12.3. Restricted `SpawnExpr`

Allowed only as:

```text
SpawnExpr.callee = CallExpr(
  callee=Variable(<top-level restricted AgentDef>),
  args=[]
)
```

The nested call is a constructor marker and does not authorize general user-defined calls.

### 12.4. Restricted `SendStmt`

Receiver must be a variable whose dataflow is statically proven to originate from an approved `SpawnExpr`; method is an identifier and args are validated. If provenance is ambiguous, reject.

### 12.5. Restricted `AwaitExpr`

Allowed forms:

```text
await <pure strict-JSON/synthetic target>
```

or:

```text
AwaitExpr.expr = CallExpr(
  callee=MemberAccess(
    obj=Variable(<proven spawned actor ref>),
    member=<identifier>
  ),
  args=[]
)
```

The member call exception exists only inside `AwaitExpr` for the current synthetic promise-ID path. It does not permit arbitrary member execution.

### 12.6. Inventory gate

Phase 0 must compare every current `Node` subclass with an explicit validator classification. Any unclassified class or declaration/write field blocks implementation.

---

## 13. Replay Effects and Output

| Operation | Committed replay | P2 |
|---|---|---|
| Pure expressions and `If` | deterministic | supported |
| allowlisted pure builtins | deterministic | supported |
| `time/random/uuid` | existing `side_effect` history result | supported with crash boundary |
| `print` | reconstructed prefix, publish delta only | supported |
| manual `LLMCall` | existing `llm_call` result | supported |
| restricted spawn | persisted `actor_spawned.process_id` | supported |
| restricted send | REPLAY does not repeat mailbox/network mutation | supported |
| memory, tools, dynamic calls, receive, migration, VM | not proven | unsupported |

Persisted output:

```text
line_count = len(output_buffer)
digest = sha256(strict-canonical([str(line) for line in output_buffer]))
```

After replay, prefix count/digest must match and is not published. New result uses:

```text
output_delta = output_buffer[persisted_line_count:]
```

Artifact commit precedes stdout publication.

---

## 14. Lock Release and Atomic Persistence

Lock is an atomic sibling directory for both initial run and resume. No lease, heartbeat or automatic stale recovery exists.

Atomic write:

```text
create temp in artifact directory
→ write UTF-8 JSON
→ flush
→ fsync temp file
→ os.replace(temp, artifact)
→ POSIX: fsync parent directory
```

Profiles:

```text
posix-file-and-directory-fsync-replace-v1
windows-file-fsync-replace-v1
```

If commit succeeds but lock directory deletion fails:

1. committed artifact remains canonical;
2. stdout returns the committed product outcome;
3. stderr reports `STALE_LOCK_AFTER_COMMIT` and lock path;
4. later operations fail closed with `26` until manual recovery.

The committed outcome must not be converted into a false rollback/error.

---

## 15. Crash and Retry Boundary

Crash after signal injection but before the next commit leaves the previous artifact canonical. External effects may already have occurred; automatic retry is forbidden and exactly-once is not claimed. External systems must use stable idempotency keys.

---

## 16. Result and Exit Codes

Durable commands write exactly one JSON document to stdout; diagnostics go to stderr.

| Code | Meaning |
|---:|---|
| `0` | `COMPLETED` |
| `1` | deterministic/runtime execution error |
| `2` | invalid CLI/input/path |
| `20` | `PENDING` |
| `21` | invalid/corrupt artifact or integrity failure |
| `22` | replayed boundary mismatch |
| `23` | stale or unknown suspension |
| `24` | conflicting resolution |
| `25` | unsupported durable operation/reason |
| `26` | artifact exists or lock held |

Public JSON never contains raw bindings, signal, request, prompt, environment, stack trace or exception object.

---

## 17. Terminal Artifacts and Security

`COMPLETED` and deterministic `ERROR` artifacts retain `active_suspension = null`, history and resolved idempotency entries. Integrity failure never mutates the artifact.

Artifact hash is not authentication. P2 has no encryption-at-rest. Deployment must provide ACL, backup and retention; state directories must not be publicly served.

---

## 18. Stop-Gates

```text
BLOCKED — BASE_MISMATCH
BLOCKED — SNAPSHOT_CONTRACT_MOVED
BLOCKED — ASYNC_REPLAY_CONTRACT_REQUIRES_CORE_CHANGE
BLOCKED — AST_INVENTORY_INCOMPLETE
BLOCKED — DURABLE_VALIDATOR_REQUIRES_AST_CHANGE
BLOCKED — INITIAL_BINDING_OWNERSHIP_UNDEFINED
BLOCKED — HISTORY_INTEGRITY_CHANGE_REQUIRED
BLOCKED — CANONICAL_SERIALIZATION_CHANGE_REQUIRED
BLOCKED — ACTOR_RUNTIME_CHANGE_REQUIRED
BLOCKED — REPLAY_ENGINE_CHANGE_REQUIRED
BLOCKED — UNSUPPORTED_EFFECT_REQUIRED_BY_ACCEPTANCE
BLOCKED — INITIAL_RUN_LOCK_NOT_PROVEN
BLOCKED — CROSS_PLATFORM_LOCK_NOT_PROVEN
BLOCKED — ATOMIC_WRITE_CONTRACT_NOT_PROVEN
BLOCKED — WHOLE_ARTIFACT_JSON_PROFILE_NOT_PROVEN
BLOCKED — OUTPUT_DELTA_NOT_PROVEN
BLOCKED — TARGET_MOVED_AFTER_IMPLEMENTATION
BLOCKED — NEW_PLATFORM_REGRESSION
```

A stop-gate cannot be bypassed by scope expansion.

---

## 19. Acceptance Criteria

1. Sync `run` remains unchanged.
2. Durable no-suspension program commits `COMPLETED`, code `0`, without arbitrary `program_result`.
3. `suspend` produces `PENDING`, code `20`, and a restartable artifact.
4. `examples/durable_promise.syn` passes the restricted spawn/send/await validator and executes `run → PENDING → resume → COMPLETED` without replayed physical spawn/send.
5. Cross-node promise example works through persisted initial binding without claiming network delivery.
6. External source deletion/change does not affect resume; embedded source corruption exits `21`.
7. Event count, chain or final-hash corruption exits `21` before replay.
8. Boundary/history/output drift exits `22` before signal application.
9. Unknown ID exits `23` without mutation.
10. Same resolved ID/hash returns the saved outcome without replay after terminal completion and after later cycles.
11. Same resolved ID with another hash exits `24` regardless of current status.
12. Sequential suspensions receive different IDs.
13. Prior output is not published again.
14. Memory, dynamic tool/member/Python calls are rejected before execution, while the exact approved spawn/await contexts pass.
15. `awaiting_llm` never invokes provider automatically and accepts only a JSON string on resume.
16. Two initial runs with one `run_id`: one executes, the other exits `26` before effects.
17. Two resumes: one obtains lock, the other exits `26`.
18. Interruption before `os.replace()` leaves the previous complete artifact.
19. Post-commit lock-release failure returns committed outcome, emits `STALE_LOCK_AFTER_COMMIT`, and leaves future operations fail closed.
20. Any non-strict value anywhere in the projected artifact blocks the new commit.
21. Initial-binding checks use module-level `synapse.lexer.KEYWORDS`, bootstrap bindings and the full source-owned set.
22. stdout contains one JSON document and no sensitive payload.
23. ReplayEngine, ActorRuntime, Interpreter, parser, AST, history algorithm/seed and existing formats remain unchanged.
24. Windows and Linux process-level lock evidence is provided.
25. `new_failing_nodeids = empty` against the approved baseline.

---

## 20. Evidence and Approval

Implementation evidence must include:

- exact base and changed files;
- complete `load_snapshot()` field proof;
- complete AST inventory/classification;
- restricted spawn/send/await dataflow evidence;
- initial-binding ownership evidence;
- exact history invariants;
- whole-artifact strict JSON evidence;
- source/boundary/output proofs;
- terminal and later-cycle idempotency proofs;
- initial-run/resume locking on Windows/Linux;
- atomic interruption and post-commit stale-lock tests;
- targeted/full differential results and CI references.

Before RFC correction merge:

```text
Runtime Architecture Review — PASS
Replay and Effects Review — PASS
CLI/Application Review — PASS
Independent Scope Review — PASS
Product Owner approval
```

Production implementation remains a separate branch, commit and PR.
