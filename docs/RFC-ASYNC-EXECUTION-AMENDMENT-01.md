# RFC-ASYNC-EXECUTION — Amendment 01

## Normative corrections after technical self-review

**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Parent RFC:** `docs/RFC-ASYNC-EXECUTION.md`  
**Parent RFC commit:** `b5d49959c66c2970bdf85d5ce2290ee9250ed30f`  
**Amendment status:** `DRAFT — PRODUCT OWNER APPROVAL REQUIRED`  
**Patch unit:** `P2-RFC-AMENDMENT-01`

---

## 1. Amendment rule

This document is an additive normative amendment to the approved RFC.

It does **not** replace, shorten, reorganize or delete `docs/RFC-ASYNC-EXECUTION.md`.

All clauses of the parent RFC remain in force except where this amendment explicitly overrides or narrows them. In case of conflict, the numbered amendment clauses below take precedence only for the stated subject.

Production implementation remains unauthorized until this amendment is reviewed and merged.

---

## 2. Correction A — exact durable-safety validator subset

### 2.1. Problem corrected

The parent RFC requires fail-closed AST validation but leaves context-sensitive actor constructs insufficiently explicit. The production implementer must not decide the accepted language subset independently.

### 2.2. Fail-closed rule

```text
UNCLASSIFIED == UNSUPPORTED
```

If the validator cannot statically prove the node, its context and all descendants safe under this contract, the program is rejected before execution.

False rejection is permitted. False acceptance is forbidden.

### 2.3. First-implementation allowlist

The following node classes are allowed only under the stated constraints:

| Node | Constraint |
|---|---|
| `Program`, `ExprStmt` | all descendants validated |
| `LetStmt`, `AssignStmt` | value validated; identifier ownership proven |
| `Literal`, `Variable` | strict value or statically known identifier |
| `BinaryExpr`, `UnaryExpr` | operands validated; existing operator |
| `ListExpr`, `DictExpr` | descendants validated; dict keys are strings |
| `IfStmt` | condition and both branches validated |
| `AffectivePadLiteral`, `DecayExpr` | pure finite/scalar value only |
| `PromptExpr` | template and arguments validated |
| `AssertStmt` | pure condition/message; no suspension descendant |
| `AgentDef` | methods empty; no stateful energy/soulprint options |
| `CallExpr` | only direct allowlisted builtin or approved contextual form below |
| `SpawnExpr` | only the restricted constructor form below |
| `SendStmt` | only a statically proven spawned actor reference |
| `AwaitExpr` | only an approved await form below |
| `SuspendExpr` | request subtree validated; runtime request strict JSON |
| `LLMCall` | prompt validated; manual external resolution only |

Every other current `Node` subclass is unsupported in the first implementation, including functions/flows, loops, return/try-catch, imports, receive, migration, memory operations, policy/governance, cognition, collective operations, affective mutation, habits, VM operations and every general sync fallback.

### 2.4. Ordinary `CallExpr`

Allowed direct `Variable(name)` calls:

```text
len, range, type, str, int, float, list, dict,
abs, sum, max, min, sorted, reversed,
enumerate, zip, any, all,
time, random, uuid, print
```

`map` and `filter` are forbidden because they require a first-class callable.

Arbitrary callable variables, user functions, tools, agent methods and Python member calls are forbidden.

### 2.5. Restricted `SpawnExpr`

Allowed only as:

```text
SpawnExpr.callee = CallExpr(
  callee=Variable(<top-level restricted AgentDef name>),
  args=[]
)
```

The nested call is a constructor marker and does not authorize general user-defined calls.

### 2.6. Restricted `SendStmt`

The receiver must be a variable whose dataflow is statically proven to originate from an approved `SpawnExpr`. Method is a parser-provided identifier and all arguments belong to the accepted subtree.

If actor-reference provenance is ambiguous, the node is rejected.

### 2.7. Restricted `AwaitExpr`

Allowed forms:

```text
await <pure strict-JSON or synthetic target>
```

or:

```text
AwaitExpr.expr = CallExpr(
  callee=MemberAccess(
    obj=Variable(<proven spawned actor reference>),
    member=<identifier>
  ),
  args=[]
)
```

This member-call exception exists only inside `AwaitExpr` for the current synthetic promise-ID path. It does not permit arbitrary member execution.

### 2.8. Inventory gate

Phase 0 must compare every current `Node` subclass with an explicit classification. An unclassified node or declaration/write field triggers:

```text
BLOCKED — AST_INVENTORY_INCOMPLETE
```

---

## 3. Correction B — terminal- and multi-cycle-aware idempotency

### 3.1. Required resume order

After lock and artifact integrity validation, the runtime must parse and strict-validate the signal, compute its hash, and look up the supplied `suspension_id` in `resolved_suspensions` **before** requiring `artifact.status == PENDING` or comparing the active suspension.

```text
acquire lock
→ read and verify artifact
→ parse and strict-validate signal
→ compute signal_hash
→ lookup resolved_suspensions[suspension_id]
```

If a resolved entry exists:

- same signal hash: return the saved semantic result without replay or mutation;
- different signal hash: exit `24` without mutation.

This rule applies after `COMPLETED`, `ERROR` and after later suspension cycles.

Only an unresolved ID proceeds to active-suspension validation.

### 3.2. Required resolved entry

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

`operation_result` must be strict JSON and must not contain the signal, request, prompt, initial bindings or advisory `resume_argv`.

For a duplicated saved `PENDING` result, `resume_argv` is regenerated from the current `sys.executable`.

---

## 4. Correction C — initial-binding ownership

### 4.1. Keyword source

The keyword registry is the module-level:

```text
synapse.lexer.KEYWORDS
```

It is not `Lexer.KEYWORDS`.

### 4.2. Binding name acceptance

A binding key is accepted only when it:

1. follows the language identifier grammar;
2. is absent from `synapse.lexer.KEYWORDS`;
3. is absent from `BUILTINS`;
4. is absent from bindings created by `bootstrap_global_env()`;
5. does not start with `__synapse_`;
6. is absent from the conservative source-owned identifier set.

The source-owned set includes:

- `LetStmt.name`;
- `AssignStmt.target`;
- `AgentDef.name`;
- `FnDef.name` and function parameters;
- `FlowDef.name`;
- every parser-produced string field named `binding`;
- every declaration or write target found by the complete AST inventory.

If ownership cannot be proven unambiguous, reject the binding before execution.

### 4.3. Bootstrap-only interpreter

Application may create a bootstrap-only `Interpreter` to inspect real bootstrap bindings. Before the complete AST validator passes, it must not call `interpret()`, `interpret_async()`, `evaluate()` or execute source effects.

Validated values are applied with:

```python
interpreter.global_env.define(name, deep_copied_json_value)
```

---

## 5. Correction D — whole-artifact strict JSON boundary

### 5.1. Complete projection validation

Before every artifact hash and commit, application must recursively strict-validate the whole projected artifact, including:

- `replay_state.execution_history`;
- promises and promise routes;
- mailboxes;
- outbound packets;
- actor log and spawned actors;
- suspension payload projection;
- terminal payload;
- idempotency records and operation results;
- output state;
- initial bindings.

No `default=str`, opaque representation or silent coercion is allowed.

If newly produced runtime state cannot be represented in the strict profile, the new step is not committed and the previous artifact remains canonical.

### 5.2. Program result

P2 does not publish or persist an arbitrary tree-walker `program_result`. Completion exposes only the durable status, identifiers, hashes and `output_delta`.

### 5.3. Request hash

The undefined `request_hash` field is removed from the P2 contract.

Suspension request/prompt integrity is covered by `payload_hash`; promise identity is covered by `promise_id`.

Stop-gate:

```text
BLOCKED — WHOLE_ARTIFACT_JSON_PROFILE_NOT_PROVEN
```

---

## 6. Correction E — lock before initial-run execution

The same sibling lock used by resume is mandatory for initial durable run.

Required order:

```text
validate CLI and state directory
→ read and parse source/input
→ determine run_id and paths
→ atomic mkdir(<artifact>.lock/)
→ after lock, verify artifact does not exist
→ validate bindings and complete AST
→ create execution Interpreter
→ execute
→ project and atomically commit artifact
→ release lock
→ publish result
```

Two processes with one `run_id` must not both execute effects. One obtains the lock; the other exits `26` before execution.

`state-dir` must already exist, be a writable directory and be deployed on an approved local filesystem. Application validates existence/type/writability but does not claim reliable mount-type detection.

Stop-gate:

```text
BLOCKED — INITIAL_RUN_LOCK_NOT_PROVEN
```

---

## 7. Correction F — exact history-integrity invariants

All of the following must hold simultaneously:

```text
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

Application must use the existing history algorithm, canonical serialization and seed. No new algorithm, seed or event schema is introduced.

Any mismatch exits `21` before replay and does not mutate the artifact.

---

## 8. Correction G — lock-release failure after successful commit

If artifact commit succeeds but removal of the lock directory fails:

1. the committed artifact remains canonical;
2. the product outcome is not rolled back or converted into a false execution failure;
3. stdout returns the committed outcome;
4. stderr reports `STALE_LOCK_AFTER_COMMIT` and the lock path without secret payload;
5. later operations fail closed with exit code `26` until manual operator recovery.

This rule does not introduce automatic stale-lock recovery.

---

## 9. Additional acceptance evidence

Future implementation must prove:

1. exact validator acceptance of the existing durable promise example and rejection of arbitrary member/tool calls;
2. duplicate resolution after terminal completion and after a later active suspension;
3. module-level keyword use and complete source-owned binding collision checks;
4. whole-artifact rejection of non-finite or opaque values;
5. two concurrent initial runs execute effects in only one process;
6. event count, chain and final hash mismatch detection;
7. post-commit lock-release failure returns the committed outcome and leaves a detectable stale lock;
8. Windows and Linux process-level tests for initial-run and resume locking.

---

## 10. Scope and approval

This amendment patch may add only:

```text
docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md
```

It must not alter the parent RFC, production code, tests, examples, capability matrix or CI configuration.

Required reviews:

```text
Runtime Architecture Review — PASS
Replay and Effects Review — PASS
CLI/Application Review — PASS
Independent Scope Review — PASS
Product Owner approval
```

After merge, the parent RFC and this amendment together form the canonical P2 design contract. Production implementation remains a separate branch and PR.
