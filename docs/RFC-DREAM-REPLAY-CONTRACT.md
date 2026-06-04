# RFC: Dream Replay Contract

**Status:** APPROVED  
**Version:** v2  
**Target milestone:** Alpha3g / RFC-01  
**Approved implementation branch:** `feature/dream-replay-impl-alpha3g`  
**Contract:** Path A + A2 (`execute_and_verify`)  
**Runtime scope authorized after approval:** `evaluate_dream()` replay contract and tests only  
**Depends on:** `docs/DETERMINISM_CONTRACT.md`, `docs/ALPHA3F_PLANNING_GATE.md`

This RFC defines the approved replay contract for `DreamBlock` so that dream
execution can become replay-verifiable without adding new CVM opcodes.

---

## 1. Problem statement

`DreamBlock` was identified by the Alpha3f determinism audit as Category C:
useful, but not strict-golden-safe.

Previous behavior verified against `synapse/interpreter.py`:

- `evaluate_dream()` executed the dream body in a sandboxed environment.
- LIVE execution recorded an event of type `dream_completed`.
- legacy `dream_completed` contained raw `scenario`, `config`, and `result`.
- `hash_event_chain()` hashed the full event payload.
- REPLAY execution did not consume `next_history_event("dream_completed")`.
- REPLAY re-executed the dream body and returned the recomputed result.
- REPLAY did not append a new `dream_completed` event, but downstream state could
  still drift because the returned value was not sourced from the recorded event.

The goal of this RFC is to make the dream result replay-verifiable while
preserving the existing linear `execution_history` cursor.

---

## 2. Approved strategy: Path A

The approved strategy is **Path A**:

```text
Tree-walker implementation + next_history_event("dream_completed")
```

Rejected for this milestone:

```text
Path B: DREAM_ENTER / DREAM_EXIT CVM opcodes
```

Reason: `DreamBlock` is still a tree-walker cognitive primitive. Its current
replay problem is missing recorded-event consumption, not missing bytecode.
Adding dream-specific CVM opcodes would add unnecessary continuation and cursor
complexity before the simpler contract is proven insufficient.

---

## 3. Approved nested event policy: A2 / Execute and Verify

The first implementation must use:

```text
nested_event_policy = "execute_and_verify"
```

This is **Variant A2**.

### Why A1 was rejected

A1 would skip dream body execution in REPLAY and immediately consume
`dream_completed`.

That is unsafe with the current linear history model. If LIVE execution produced
nested events inside dream, such as `llm_call`, the recorded order is:

```text
0: llm_call
1: dream_completed
```

If REPLAY skipped the body and immediately expected `dream_completed`, the replay
cursor would see `llm_call` and desynchronize.

### A2 contract

In REPLAY, the interpreter executes the dream body **only** to preserve cursor
linearity and consume legal nested replay events. The body execution does not
choose the canonical result.

The canonical result returned to user code is always:

```text
event.result
```

from the recorded `dream_completed` event, after verification.

---

## 4. Nested events allowed in current code

The RFC must reflect current runtime facts.

Current code permits:

- `LLMCall` inside `dream`; it has replay consumption through
  `next_history_event("llm_call")`.

Current code forbids:

- `affective_resonance` inside `dream` via `AffectiveIsolationViolation`.
- `fracture` inside `dream` via `DreamIsolationViolation`.

Current code treats:

- `superpose` as a deterministic selector in builtins, not a provider-backed LLM
  event and not a standalone history event.

Future RFCs may allow more Category B events inside dream. If so, each such event
must either provide a replay path compatible with `execute_and_verify`, or be
explicitly forbidden in strict replay.

---

## 5. `dream_key` schema

Every strict `dream_completed` event must include a structured `dream_key`.

Required fields:

```json
{
  "scenario_hash": "sha256(...) ",
  "config_hash": "sha256(...) ",
  "body_hash": "sha256(...) ",
  "bound_variables_hash": "sha256(...) ",
  "parent_history_hash": "...",
  "runtime_version": "..."
}
```

### Field meanings

| Field | Meaning |
|---|---|
| `scenario_hash` | Hash of evaluated scenario value. |
| `config_hash` | Hash of evaluated dream config. |
| `body_hash` | Hash of canonical DreamBlock body structure. |
| `bound_variables_hash` | Hash of variables actually captured/read by the DreamBlock at entry time. |
| `parent_history_hash` | Hash of the history prefix at dream entry. |
| `runtime_version` | Runtime/spec version participating in replay identity. |

`bound_variables_hash` must not blindly hash the entire enclosing environment.
It represents a canonical snapshot of variables captured/read by the dream body
at entry time. This prevents unrelated environment values from destabilizing the
key while still protecting against replaying a result from the wrong lexical
context.

---

## 6. `dream_completed` event schema

Strict `dream_completed` events must include:

```json
{
  "type": "dream_completed",
  "scenario": "...",
  "config": {},
  "result": "...",
  "dream_key": {
    "scenario_hash": "...",
    "config_hash": "...",
    "body_hash": "...",
    "bound_variables_hash": "...",
    "parent_history_hash": "...",
    "runtime_version": "..."
  },
  "result_hash": "sha256(canonical_result)",
  "nested_event_policy": "execute_and_verify"
}
```

Legacy `dream_completed` events without these fields are not strict-replay-safe
and must be rejected in strict replay.

---

## 7. LIVE algorithm

In LIVE mode:

1. Evaluate `scenario` and `config`.
2. Compute `parent_history_hash` at dream entry.
3. Compute expected `dream_key`, including `bound_variables_hash`.
4. Execute the dream body in a sandbox.
5. Legal nested events record normally in the linear `execution_history`.
6. Compute `result_hash` from the computed result.
7. Append `dream_completed` with `dream_key`, `result`, `result_hash`, and
   `nested_event_policy = "execute_and_verify"`.
8. Return the computed result.

---

## 8. REPLAY algorithm

In deterministic REPLAY mode:

1. Evaluate `scenario` and `config` needed for identity.
2. Compute expected `dream_key`, including `bound_variables_hash`.
3. Execute the dream body in replay mode.
   - This preserves the linear history cursor.
   - Legal nested replay events consume their own recorded events.
   - In current code, `LLMCall` is the primary allowed nested replay event.
4. Compute `computed_result_hash` from the body result.
5. Consume `next_history_event("dream_completed")`.
6. Verify:
   - `event.type == "dream_completed"`;
   - `event.dream_key == expected dream_key`;
   - `hash(event.result) == event.result_hash`;
   - `computed_result_hash == event.result_hash`;
   - `event.nested_event_policy == "execute_and_verify"`.
7. If any check fails, raise `REPLAY_INTEGRITY_ERROR`.
8. Return `event.result` as the canonical dream result.

Important:

> The body is executed in replay for cursor synchronization and verification,
> not for selecting the canonical result.

A2 does not hide nondeterminism. If the dream body recomputes a different result
under replay, `computed_result_hash` will differ from `event.result_hash`, and
strict replay must fail.

---

## 9. Error handling

Strict replay failures must be hard failures.

| Condition | Required result |
|---|---|
| Missing `dream_completed` | `REPLAY_INTEGRITY_ERROR` |
| Legacy event missing `dream_key` | `REPLAY_INTEGRITY_ERROR` |
| Legacy event missing `result_hash` | `REPLAY_INTEGRITY_ERROR` |
| `dream_key` mismatch | `REPLAY_INTEGRITY_ERROR` |
| `hash(event.result) != event.result_hash` | `REPLAY_INTEGRITY_ERROR` |
| `computed_result_hash != event.result_hash` | `REPLAY_INTEGRITY_ERROR` |
| Unsupported `nested_event_policy` | `REPLAY_INTEGRITY_ERROR` |

No silent fallback to live execution is allowed in strict replay.

---

## 10. Exploratory-live note

In non-canonical exploratory-live fork lineage, a missing `dream_completed` may
allow live dream execution and fork-local non-canonical event recording.

This behavior:

- does not apply to strict replay;
- must not mutate golden artifacts;
- must not be used to pass strict golden replay;
- remains subject to debugger governance.

---

## 11. Interaction with integrate

`integrate` may use `dream_completed.result_hash` as the stable identity of a
dream result.

However, integrate replay-applier semantics are outside this RFC. They require a
separate Alpha3g RFC.

This RFC does not define:

- how `integrate_committed` applies `state_diff` during replay;
- whether integrate body is re-executed or skipped;
- rollback replay semantics;
- transaction-level replay-applier behavior.

---

## 12. Strict golden eligibility

A `DreamBlock` becomes eligible for strict golden Layer 1 only after the
implementation satisfies this RFC.

Required properties:

- LIVE records `dream_completed` with strict schema.
- REPLAY consumes `dream_completed`.
- Nested `LLMCall` consumes recorded `llm_call` and does not call provider.
- `dream_key` is verified.
- `result_hash` is verified.
- mismatch/missing events fail deterministically.
- No new dream CVM opcodes are introduced.

---

## 13. Acceptance criteria for implementation

The implementation patch must include tests for:

1. LIVE `DreamBlock` records `dream_completed` with `dream_key`, `result_hash`,
   and `nested_event_policy`.
2. REPLAY executes body, consumes nested `llm_call`, consumes `dream_completed`,
   and returns recorded `event.result`.
3. REPLAY does not call the LLM provider for nested `LLMCall`.
4. `result_hash` mismatch raises `REPLAY_INTEGRITY_ERROR`.
5. `dream_key` mismatch raises `REPLAY_INTEGRITY_ERROR`.
6. `bound_variables_hash` changes when captured variables change.
7. Legacy `dream_completed` without strict fields fails in strict replay.
8. Existing strict golden Layer 1 remains green.

---

## 14. Non-goals

This RFC does not authorize:

- new CVM dream opcodes;
- changes to `hash_event_chain()`;
- replay runner implementation;
- integrate replay-applier implementation;
- affective runtime changes;
- fracture runtime changes;
- superpose runtime changes;
- parser or language syntax changes;
- session persistence or daemon work.

---

## 15. Final rule

The Dream replay contract is:

> Execute the body in replay to preserve the linear event cursor.  
> Verify the recorded `dream_completed`.  
> Return the recorded result.  
> Fail on any mismatch.  
> Do not silently fall back to live execution in strict replay.
