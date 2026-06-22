# P2 Mailbox Wait Evidence — Durable Receive Wait Lifecycle

Status: `P2 MAILBOX WAIT IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED FOR APPROVED P2 SCOPE` after this evidence patch merges.

This evidence record summarizes implementation PR #50 and the post-merge verification state for the approved P2 durable mailbox receive wait lifecycle.

## Scope

This evidence closes only the approved P2 mailbox wait durable lifecycle contract from:

```text
RFC-ASYNC-MAILBOX-WAIT
RFC-ASYNC-MAILBOX-WAIT_APPROVAL
RFC-ASYNC-MAILBOX-WAIT_APPROVAL interpreter-path amendment
```

Closed behavior:

- `awaiting_message` is a supported P2 durable suspension reason;
- `awaiting_message_or_timeout` is a supported P2 durable suspension reason;
- constrained single-pattern `ReceiveBlock` is accepted by durable AST validation;
- `ReceivePattern` is accepted only inside an approved constrained `ReceiveBlock`;
- mailbox wait active suspensions preserve `promise_id = null`;
- mailbox wait payloads use schema `synapse.mailbox.wait.v1` inside existing artifact schema `1.0.0`;
- external `mailbox_message` resume uses args-only input and rejects external `message.payload`;
- internal mailbox payload is derived from `args` after validation;
- external `mailbox_timeout` resolves `awaiting_message_or_timeout` only;
- mailbox resume hashing uses normalized reason-specific mailbox signal hash;
- normalized mailbox signal hash is applied before P2 idempotency lookup;
- strict JSON and receiver binding are enforced before mailbox injection;
- replayed `message_received` and `receive_timeout` events are validated in the actual inline durable async `ReceiveBlock` path;
- ghost mailbox consumption is blocked before `mailbox.pop(0)`;
- sequential mailbox waits replay without cursor drift;
- deterministic strict JSON timeout values are persisted, including non-scalar strict JSON values.

Out of scope:

- P3c-N implementation;
- mailbox-backed consensus vote delivery;
- receive-based consensus vote collection;
- consensus-specific mailbox schemas;
- consensus participant validation;
- network or daemon transport;
- durable timers or wall-clock scheduler;
- persistent durable inbox;
- early mailbox delivery;
- multi-pattern receive matching;
- parser / lexer / AST expansion;
- production distributed consensus protocol behavior.

## Commit anchors

| Item | SHA / ID |
|---|---|
| RFC draft PR | `#47` |
| RFC approval PR | `#48` |
| Interpreter-path approval amendment PR | `#49` |
| Implementation PR | `#50` |
| Implementation PR base | `7445f4e2fc148860c467b0d402ba664f26d98306` |
| Implementation PR final head | `b3026ea965b8a8a1aa4707e8b647447c62401ace` |
| Implementation merge commit | `2a93ef6006ce4b86f2fe90cc4490ee3a1cefcb92` |
| Implementation branch | `p2-mailbox-wait-impl` |

## Changed files in implementation PR #50

PR #50 changed only approved files:

```text
synapse/application.py
synapse/interpreter.py
synapse/runtime/mailbox_wait.py
tests/test_durable_mailbox_wait.py
```

No parser, lexer, AST, consensus, network, daemon, timer, durable-inbox, workflow, examples or docs files were changed by the implementation PR.

## Implementation summary

### Durable lifecycle and artifact integration

PR #50 registered `awaiting_message` and `awaiting_message_or_timeout` as supported durable suspension reasons. It added constrained durable AST validation for `ReceiveBlock` / `ReceivePattern`, enforcing single-pattern receive, deterministic strict JSON timeout expression support, recursive validation of receive body and `else_body`, and failure for unsupported receive shapes.

Mailbox wait active suspensions preserve the existing artifact schema version:

```text
artifact_schema_version = 1.0.0
```

Mailbox wait suspensions use:

```text
active_suspension.promise_id = null
active_suspension.payload.mailbox_wait_schema = synapse.mailbox.wait.v1
```

No new required top-level artifact fields were introduced.

### Mailbox resume contract

PR #50 introduced `synapse/runtime/mailbox_wait.py` as the mailbox wait contract helper module. It owns:

- mailbox resume schema constants;
- strict JSON projection;
- `mailbox_message` validation;
- `mailbox_timeout` validation;
- canonical internal message construction;
- receiver binding checks;
- normalized mailbox hash preimage construction;
- replayed receive event validation.

The external message schema is args-only. External `message.payload` is rejected. Runtime derives internal `payload = args[0] if len(args) == 1 else args` after validation.

### Hashing and idempotency

Mailbox wait reasons use a normalized mailbox signal hash, not raw full-signal hashing. The normalized hash is computed before the `resolved_suspensions[suspension_id]` idempotency lookup, preserving duplicate/conflict behavior:

- same suspension ID and same normalized hash returns stored result;
- same suspension ID and different normalized hash returns exit `24`;
- message after timeout and timeout after message conflict under existing idempotency semantics.

The implementation reuses the existing canonical JSON function used by history-chain hashing after strict projection.

### Actual execution path

The actual durable async `ReceiveBlock` path is the inline branch in `synapse/interpreter.py`:

```text
evaluate_async_impl
isinstance(node, ReceiveBlock) branch
```

PR #50 hardened that existing path. The interpreter remains an execution/replay adapter; mailbox schemas, strict validation, normalized hash construction, receiver binding, timeout checks and canonical internal-message construction live in `synapse/runtime/mailbox_wait.py`.

### Replay and ghost mailbox policy

The inline durable async `ReceiveBlock` path now validates replayed `message_received` and `receive_timeout` events and fails closed on mismatch.

Ghost mailbox consumption is blocked before `mailbox.pop(0)` in the actual durable receive path.

A review follow-up verified that deterministic local send to a spawned actor mailbox does not satisfy top-level receive. The top-level receive waits on `global`, while durable `SendStmt` to a spawned actor writes to the spawned process mailbox key such as `Inbox#...`.

## Test evidence added in PR #50

New test file:

```text
tests/test_durable_mailbox_wait.py
```

Key coverage:

- pending artifact for `awaiting_message`;
- pending artifact for `awaiting_message_or_timeout`;
- mailbox wait `promise_id = null`;
- mailbox wait payload schema;
- valid `mailbox_message` resume;
- external `message.payload` rejection;
- receiver and actor binding rejection;
- valid `mailbox_timeout` resume;
- timeout rejected for `awaiting_message`;
- normalized mailbox hash idempotency;
- conflict semantics for different message / message-after-timeout / timeout-after-message;
- replay actor/receiver/order mismatch fail-closed behavior;
- sequential mailbox waits without replay drift;
- non-finite float / non-string key / host object strict JSON rejection;
- oversized signal rejection through existing P2 signal limit;
- ghost mailbox pre-pop rejection;
- deterministic strict JSON timeout value persistence for `[1, 2, 3]`;
- local send to spawned-process mailbox does not satisfy top-level receive mailbox.

## Verification recorded in PR #50

| Command / suite | Result |
|---|---|
| `python -m compileall synapse tests` | PASS |
| `python -m pytest tests/test_durable_mailbox_wait.py -q --tb=no` | `16 passed` |
| `python -m pytest tests/ -q -k "durable or receive or suspend" --tb=no` | `128 passed, 1 skipped` |
| Durable actor / timeout / P3c-2 regression selection | `111 passed, 1 skipped` |
| P3 consensus regression selection | `124 passed` |
| Full suite | `1635 passed, 13 skipped, 6 known Windows/Git filesystem failures` |
| `git diff --check` | PASS |

Known full-suite failures were reported as pre-existing Windows/Git filesystem baseline failures unrelated to mailbox/durable/consensus behavior.

No new mailbox, durable or consensus failures were reported.

## Post-merge state

Implementation PR #50 is merged.

```text
main includes merge commit 2a93ef6006ce4b86f2fe90cc4490ee3a1cefcb92
```

This evidence patch records the post-code documentation state. It does not claim independent CI execution after the merge commit. It records PR-head validation, merge metadata, changed-file scope and implementation evidence.

## Stop gates

```text
stop_gates = []
```

No stop-gate remains open for the approved P2 mailbox wait durable lifecycle scope.

## Closure statement

After this evidence patch merges:

```text
P2 mailbox wait durable lifecycle: CLOSED FOR APPROVED P2 SCOPE
P3c-N: STILL BLOCKED
```

This closure does not claim production distributed consensus, mailbox-backed consensus voting, network delivery, daemon delivery, durable timers, persistent durable inbox, early delivery, multi-pattern receive matching, public ticket API, live LLM vote production, parser expansion, AST expansion, or lexer expansion.
