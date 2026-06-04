# Synapse Determinism Contract

- **Status:** Approved (fact-based, reviewed against code)
- **Target milestone:** Alpha3f / P8
- **Scope:** Documentation only
- **Runtime changes:** None
- **Purpose:** Define which execution paths are replay-safe, which are
  experimental, and which constructs must not enter strict golden replay until
  their deterministic contract is fixed.

---

## 1. Purpose

Synapse is designed around the principle that AI-agent behavior must be
auditable, replayable, and governable.

This document defines the determinism contract for all events that enter
`execution_history` and therefore participate in trace comparison, golden
replay, and hash-chain based forensic verification.

The core rule is:

> Everything that enters canonical `execution_history` and participates in the
> hash chain must be replay-verifiable.

A value may be replay-verifiable because it is:

- computed deterministically;
- read from a recorded event;
- retrieved from a content-addressable cache;
- represented by a stable content key or result hash;
- derived from a deterministic virtual clock or event index;
- explicitly scoped to a non-canonical exploratory lineage.

Any value produced by live randomness, real host time, provider
nondeterminism, UUID generation, or unstable external state must not silently
enter canonical history without a replay contract.

---

## 2. Definitions

### 2.1 Canonical execution history

`execution_history` is canonical when it is used as the basis for golden
replay, trace comparison, forensic hash-chain verification, CI regression
checks, or replay-safe debugging. Canonical history must be stable enough that
a recorded run can be replayed without calling live nondeterministic producers.

### 2.2 Hash chain

`hash_event_chain()` computes the forensic chain over the ordered event stream.
The current implementation hashes the full event payload through canonical JSON
serialization. Therefore any unstable value inside an event affects that
event's hash and all subsequent chain hashes.

### 2.3 Replay-safe event

An event is replay-safe if replay can reconstruct the same observable behavior
without invoking the original live nondeterministic producer. Replay-safe
events may contain values originally produced by nondeterministic sources, but
replay must consume the recorded value rather than regenerate it.

**The only approved mechanism for replay-safety is recorded consumption:**
replay reads the event from history rather than regenerating it. Excluding
events from hash comparison by policy or metadata flag is **not** an approved
pattern for canonical traces (see §10).

### 2.4 Strict golden replay

Strict golden replay is the highest-confidence baseline. Programs included in
strict golden Layer 1 must avoid constructs that are currently experimental,
non-replay-applied, or dependent on live UUID/time/random identity unless those
values are recorded and replay-consumed under an approved contract.

### 2.5 Exploratory lineage

Exploratory lineage is a non-canonical debug branch. It may contain synthetic
events, new guard paths, or live exploratory results. It must never mutate a
golden artifact or canonical parent history.

---

## 3. Determinism categories

Synapse uses three determinism categories.

### 3.1 Category A — Canonical deterministic

A construct belongs here when its output is deterministic by construction.

Requirements:

- no live uuid, random, real host time, provider call, or external
  nondeterministic source enters the event;
- all identities are stable, content-addressed, event-indexed, or derived from
  deterministic input;
- replay does not need to call a live producer;
- independent live-runs of the same inputs produce the same canonical event
  stream.

Examples: pure CVM computation; deterministic bytecode operations; guard
enter/check/exit events with stable identifiers; deterministic memory events
with stable keys and values; event-indexed IDs (provided earlier history is
deterministic).

### 3.2 Category B — Replay-safe recorded nondeterminism

A construct belongs here when live execution may produce a nondeterministic
value, but replay consumes the recorded event or cache entry and does not
regenerate it.

This category is replay-safe for `record → replay` but not necessarily stable
for `live run → independent live run`.

Requirements:

- live execution records the nondeterministic result;
- replay consumes the recorded event or cache entry;
- replay does not call the live provider/generator;
- the event has enough identity to validate cache/replay correctness.

Examples: `LLMCall` (replay consumes `next_history_event("llm_call")`); LLM
Bridge calls using content-addressable cache; `affective_resonance_applied`
(replay consumes the recorded event); `superpose` / `debate` / `fracture`
(deterministic identity, nested LLM calls replay-mediated — see §12).

**Important distinction:** replay-safe recorded nondeterminism is acceptable
for replay, but may still cause two independent live-runs to diverge if the
recorded event contains UUID/time/random-bound fields.

### 3.3 Category C — Experimental / non-strict-golden-safe

A construct belongs here when it currently enters canonical history without a
complete replay contract, or when it contains unstable identity that may affect
the hash chain. These constructs may be allowed in Layer 2 smoke tests, but
must not be included in strict golden Layer 1 until their determinism contract
is approved.

Current examples identified by audit: `DreamBlock` / `dream_completed`
(**superseded — as of Alpha3g this is Category B under the strict dream schema;
see §6.1. Only legacy pre-Alpha3g dream_completed events without
`dream_key`/`result_hash` remain Category C**);
`integrate_committed` / `integrate_rollback` (until replay-applier semantics are
specified); `evolution_ticket_created` with generated `evo-*` UUID;
`habit_registered` / `habit_formed` with generated `habit-*` / `Habit-*` IDs;
deferred distributed consensus paths with generated `consensus-*` ticket IDs;
user-visible `time`, `random`, and `uuid` builtins when their values enter
canonical history; storage-generated UUID/time values if surfaced into
canonical artifacts.

---

## 4. Contagion rule: nondeterminism propagates through the hash chain

Hash-chain nondeterminism is contagious.

If an event at index N contains a live UUID, random value, real timestamp, or
unstable result, then the hash of event N changes, every later hash from N+1
onward changes, and later events may appear divergent even if they are locally
deterministic.

Therefore small unstable fields such as `event_id`, `ticket_id`, or generated
names are not harmless if they enter canonical history.

> **Rule:** No live nondeterministic identity may enter canonical history
> unless it is recorded and replay-consumed, or explicitly marked as
> non-canonical.

---

## 5. LLM determinism

LLM determinism is not guaranteed by `temperature=0`. Provider behavior may
still change because of model version changes, backend changes, tokenizer
changes, system prompt changes, provider-side infrastructure changes, or
undocumented sampling/safety behavior.

Synapse therefore treats deterministic LLM replay as a recorded-resource
problem:

- live execution may call the provider;
- live execution records the response and associated identity;
- replay must not call the provider;
- replay must consume the recorded response or cache entry.

The stable identity of an LLM request should include: prompt/template hash;
input variables hash; schema hash; engine parameters; model version; content
key; validated response or response hash. This is the model implemented by the
deterministic LLM bridge (Track A).

---

## 6. Dream and integrate contract

### 6.1 Current DreamBlock status (updated — Alpha3g Dream Replay implemented)

As of the Alpha3g Dream Replay implementation (RFC-DREAM-REPLAY-CONTRACT,
Path A "Execute & Verify"), `DreamBlock` is no longer the old "recorded but not
replay-consumed" case. Current behavior, verified in
`interpreter.py:1328-1392`:

- In **LIVE** mode `evaluate_dream()` records `dream_completed` with a
  deterministic `dream_key`, the `result`, a `result_hash`, and
  `nested_event_policy="execute_and_verify"` (the LIVE append branch).
- In **REPLAY** mode the interpreter executes the dream body to synchronize the
  linear history cursor (so nested replay events such as `llm_call` are consumed
  in order), then consumes `next_history_event("dream_completed")`
  (line 1357) and verifies, raising `ReplayIntegrityError` on any mismatch:
  - the recorded event exists (line 1359);
  - `dream_key` matches (line 1361);
  - the recorded `result_hash` matches a re-hash of the recorded result
    (line 1365–1366);
  - the freshly computed result hash matches the recorded `result_hash`
    (line 1367–1368);
  - `nested_event_policy == "execute_and_verify"` (line 1369).
- The canonical result returned to user code is always the **recorded**
  `event.result`, not the freshly recomputed value.

The precise consequence:

- Replay consumes `dream_completed` and verifies it; the canonical result is
  sourced from the record, which closes the old recompute-drift gap.
- `DreamBlock` is therefore **replay-safe with respect to recorded result
  integrity** (`result_hash` verified).

Therefore:

> As of Alpha3g, `DreamBlock` is **Category B (replay-safe recorded
> nondeterminism), result-hash constrained**. Strict Layer 1 eligibility is
> **NOT** granted by this update — see §6.1.1 and §9.1.

**Legacy nuance:** `dream_completed` events recorded *before* the Alpha3g strict
schema (i.e. without `dream_key` / `result_hash` /
`nested_event_policy="execute_and_verify"`) are **not** strict-replay-safe and
remain Category C. Only artifacts recorded under the Alpha3g strict dream schema
get the Category B classification above.

#### 6.1.1 Why Category B, not strict Layer 1 — resolved eligibility verdict

`result_hash` replay-safety is a narrower guarantee than strict Layer 1
eligibility. `RFC-DREAM-STRICT-LAYER1-ELIGIBILITY` resolves the Alpha3g
eligibility question as follows:

> `DreamBlock` is **not Strict Layer 1 eligible under A2**. A2 replay executes
> the dream body to synchronize nested events before consuming
> `dream_completed`; body re-execution can produce observable host effects.
> Future eligibility requires a different replay model: consume-only,
> state-delta, recorded subtrace replay, or a hybrid model. P0.2.3 errata
> also makes builtin leakage and shared canonicalization hooks explicit.

The blocking facts are:

1. **Observable body re-execution.** Replay still executes the dream body to
   synchronize the cursor, then discards the computed value in favor of the
   recorded one. Body execution is therefore observable even though the result
   is sourced from the record. The concrete audited example is `print` inside a
   dream body: the sandbox rejects parent-scope `Interpreter._print`,
   `eval_call()` swallows `DreamSandboxIsolationError`, then the call falls
   through to `BUILTINS["print"]`, which writes to host stdout. This is not
   bit-/observationally-identical to consume-only replay.
2. **Closure / global mutation isolation.** Audited in code: a `.syn` function
   read from the parent scope inside a dream is currently **blocked** —
   `DreamSandboxEnvironment.get()` only clones supported containers and passes
   supported immutables (`_is_supported_immutable`: None/str/int/float/bool); a
   `FnDef` is neither, so the sandbox raises `DreamSandboxIsolationError`. So
   there is **no closure-mutation leak today**. However, this block is a side
   effect of the type check, not an explicit designed contract, and the raised
   `DreamSandboxIsolationError` is swallowed by `except RuntimeError: pass` in
   `eval_call` (line 3785) and surfaces as a misleading "Undefined function"
   message. The eligibility RFC v2 requires future runtime work to (a) make
   function/closure exclusion an explicit, tested contract so a future widening
   of `_is_supported_immutable` cannot silently open a leak, and (b) stop
   swallowing the isolation error.


**P0.2.3 errata note:** the strict-dream boundary must also explicitly classify
`BUILTINS`. `print`, `time`, `random`, `uuid`, and any builtin with
`side_effects=True` or `deterministic=False` are forbidden for future strict
dream eligibility unless an approved recorded-and-consumed effect contract
exists. The same canonicalization hooks for functions, canonical time,
nondeterminism barriers, state-delta hashing, and genesis state are shared with
`RFC-INTEGRATE-REPLAY-APPLIER` and `RFC-STABLE-CANONICAL-IDENTITY`.

3. **Deterministic nested-event origin.** Nested events inside a dream body must
   be generated by the body itself, not injected by an external async trigger;
   otherwise the replay cursor can desynchronize. This invariant is not yet
   formally proven and must be established by the eligibility RFC.

### 6.2 Future DreamBlock strict-eligibility contract

Alpha3g already defines the Category B replay contract for `DreamBlock`:
`dream_key`, scenario/config hash, body hash, parent history hash, result hash,
recorded result, and `next_history_event("dream_completed")` verification.

A future **Strict Layer 1** dream contract must go further: replay must not
execute the dream body. It must consume and verify a recorded `dream_completed`
event, recorded subtrace, and/or state delta without re-entering user code or
host builtins. The future model must also define builtin purity, closure/function
serialization or exclusion, nested-event origin, and tamper-detection for any
subtrace/state-delta hash.

### 6.3 Integrate status (updated — Alpha3g Integrate Replay implemented)

As of the Alpha3g Integrate Replay implementation (I1–I6, RFC-INTEGRATE-REPLAY-
APPLIER.md APPROVED), `integrate` replay semantics are no longer pending.

Current behavior, verified in `interpreter.py:1609-1780` (LIVE) and
`interpreter.py:1986-2038` (REPLAY):

- **LIVE** executes the integrate body in an isolated `StateOverlay`, commits or
  aborts, and records `integrate_committed` (with `schema_version`,
  `pre_state_hash`, `post_state_hash`, `write_set`, `write_set_hash`) or
  `integrate_aborted` (with `abort_reason`, `pre_state_hash`, forensic
  `overlay_summary`).
- **REPLAY** (`replay_integrate_i4_event`) consumes the recorded event without
  re-executing the integrate body. For `integrate_committed` it verifies
  `schema_version`, `pre_state_hash`, `write_set_hash`, per-entry
  `old_value_hash` and `new_value_hash`, applies the recorded `/env/*`
  write-set, and verifies `post_state_hash`. For `integrate_aborted` it
  verifies `pre_state_hash`, leaves state unchanged, and reproduces a
  deterministic abort exception. An in-run idempotency guard prevents the same
  event from being applied twice in one replay run.

LLM calls remain explicitly forbidden inside `integrate` transactions by design
(`IntegrateIsolationViolation`, verified at `interpreter.py:952`).

Therefore:

> As of Alpha3g, `integrate` is **Category B (replay-safe recorded
> nondeterminism)**: `integrate_committed` / `integrate_aborted` are recorded
> and consumed during replay; the integrate body is not re-executed. Strict
> Layer 1 eligibility is **NOT** granted by this update — five deferred MAJOR
> implementation gates (INT-04..INT-08) must be satisfied first (see below and
> RFC-INTEGRATE-REVIEW-NOTES.md).

**Deferred MAJOR gates (from RFC-INTEGRATE-REVIEW-NOTES.md):**

- **INT-04** Promise orphaning on abort — resource cleanup registry not yet
  implemented.
- **INT-05** Genesis state hash for cold start — session genesis baseline not
  yet defined.
- **INT-06** Replay applier idempotency — durable `commit_nonce` /
  crash-resume checkpointing not yet implemented (in-run guard only).
- **INT-07** Agent instance canonicalization — agent values remain unsupported
  in write-sets until `to_canonical_snapshot()` is specified.
- **INT-08** Namespace path ambiguity — canonical path parser validates
  namespace prefixes; full integration test required before strict merge.

Until these gates are satisfied, programs using integrate are **not** strict
Layer 1 eligible. Legacy `integrate` without the Alpha3g i2-skeleton schema
(no `pre_state_hash` / `write_set_hash`) remains Category C.

---

## 7. Affective primitives

### 7.1 Affective state should be deterministic by default

Affective state should be treated as normal runtime state. If affective
primitives affect guards, routing, branch choice, memory writes, or integration
decisions, their effects must be replay-verifiable. Affective decay must not
depend on real host time unless the timestamp is recorded and replay-consumed.
Preferred sources: virtual clock; event index; recorded timestamp; deterministic
runtime step.

### 7.2 Affective resonance status

Current audit findings (verified in `runtime/affective_runtime.py:361-382`):

- `affective_resonance_applied` is recorded in `execution_history`;
- live execution creates an `event_id` of `"ares-" + uuid.uuid4().hex[:12]`;
- this event enters the hash chain;
- replay has a consumption path (`next_history_event("affective_resonance_applied")`).

Therefore:

> Affective resonance is **Category B (replay-safe recorded nondeterminism)**,
> but not stable across independent live-runs while the UUID-bound `event_id`
> remains in canonical history. `record → replay` is stable; `live → live`
> strict comparison may diverge.

---

## 8. UUID, time, and random policy

### 8.1 Live nondeterministic sources

The following are nondeterministic unless explicitly virtualized, recorded, or
scoped to exploratory lineage: `uuid.uuid4()`; `random.random()` and related;
`time.time()`; `datetime.now()` or equivalent real host clock;
provider-generated IDs; storage-generated IDs and timestamps; user-level
builtins exposing live time/random/uuid.

### 8.2 Canonical history rule

Values derived from live nondeterministic sources must not enter canonical
history unless one of the following is true:

1. The value is recorded and replay consumes the recorded event.
2. The value is converted into a deterministic content key.
3. The value is replaced by a stable event-indexed or content-addressed
   identity.
4. The execution path is explicitly marked exploratory and non-canonical.

There is no approved fifth option. In particular, a "non-canonical flag" that
keeps an event in canonical history while excluding it from `hash_event_chain()`
is **not** permitted — it would create forensic blind spots (see §10).

### 8.3 Known current risk areas

| Area | Nondeterministic source | Current status |
|------|------------------------|----------------|
| `affective_resonance_applied` | `ares-*` UUID event ID | replay-safe recorded nondeterminism; not live-vs-live stable |
| `evolution_ticket_created` | `evo-*` UUID | not strict-golden-safe |
| `habit_registered` / `habit_formed` | generated `habit-*` / `Habit-*` IDs | not strict-golden-safe unless IDs are stable |
| deferred consensus | generated `consensus-*` ticket ID | not strict-golden-safe in deferred path |
| `time`, `random`, `uuid` builtins | live host sources | unsafe if values enter canonical history |
| storage backends | generated UUID/time values | requires separate persistence determinism audit |

---

## 9. Golden replay policy

### 9.1 Strict Layer 1

Strict Layer 1 must include only constructs whose replay contract is stable.
Until fixed by approved RFC, strict Layer 1 must exclude: `DreamBlock`;
experimental integrate/dream combinations; UUID-bound evolution tickets;
UUID-bound habit registration; deferred consensus paths with generated ticket
IDs; user time/random/uuid values entering canonical history; any construct
marked experimental/non-strict-golden-safe.

> ⚠️ **CRITICAL INVARIANT — DreamBlock and Strict Layer 1.** As of Alpha3g,
> `DreamBlock` is Category B (result-hash replay-safe, §6.1), but it remains
> **excluded from Strict Layer 1**. result-hash replay-safety is necessary but
> not sufficient for strict eligibility. Admission is explicitly blocked until
> RFC-DREAM-STRICT-LAYER1-ELIGIBILITY resolves the gate with a default-deny
> verdict: **no Strict Layer 1 admission under A2**. Future admission requires a
> consume-only, state-delta, or recorded subtrace replay model that removes
> replay-time dream body execution and closes the builtin, closure/function, and
> nested-event-origin boundaries. Default-deny: it is safer to keep Layer 1
> sterile than to claim eligibility the runtime does not yet prove.

> ⚠️ **CRITICAL INVARIANT — Integrate and Strict Layer 1.** As of Alpha3g
> (I1–I6), `integrate` is Category B (replay-applier implemented, §6.3), but
> it remains **excluded from Strict Layer 1** until the five deferred MAJOR
> implementation gates are satisfied (INT-04 promise cleanup, INT-05 genesis
> state hash, INT-06 durable idempotency, INT-07 agent canonicalization,
> INT-08 namespace path validation). The in-run idempotency guard is present,
> but durable crash-resume, resource cleanup, and genesis baseline are not.
> Default-deny applies: partial gate satisfaction does not unlock strict
> eligibility.

### 9.2 Layer 2 smoke

Layer 2 smoke tests may include experimental constructs for parser coverage,
basic execution coverage, and non-strict runtime smoke. Layer 2 must not claim
forensic determinism for constructs that are not strict-golden-safe.

---

## 10. Divergence comparison policy

Default trace comparison is strict.

- compare uses the forensic hash chain;
- no fuzzy comparison by default;
- no semantic tolerance by default;
- no approximate payload equivalence by default;
- hash mismatch means divergence.

If tolerant comparison is ever needed, it must be introduced as an explicit
separate mode and approved by RFC (e.g. `synapse debug compare --mode tolerant`).
This must not affect default strict comparison.

---

## 11. Debugger and exploratory execution

Exploratory execution is allowed only in explicit debug/fork contexts.

- exploratory branches must not mutate golden artifacts;
- exploratory branches must write to fork-local lineage;
- injected events must pass the event injection validator;
- forbidden injections (guard verdict override, capability grant, hash rewrite,
  direct ACK injection) remain forbidden;
- exploratory events are diagnostic, not canonical golden baseline.

---

## 12. Current classification summary

| Construct / subsystem | Current classification | Reason |
|-----------------------|------------------------|--------|
| Pure CVM operations | Category A — Canonical deterministic | Deterministic bytecode execution |
| Guard opcodes | Category A — Canonical deterministic | VM-level deterministic enforcement |
| Inline guarded memory write | Category A (if values stable) | Checked effects and explicit recovery |
| `LLMCall` | Category B — Replay-safe recorded | Recorded result consumed in replay |
| LLM Bridge | Category B — Replay-safe recorded | Content-addressable cache and schema validation |
| `affective_resonance` | Category B — Replay-safe recorded | UUID-bound live event, replay consumes recorded event |
| `fracture` | Category B — Replay-safe recorded | Identity deterministic (`source_hash + line + column + base_name`); nested LLM replay-safe; unsafe only if branch body emits non-replay-safe events |
| `debate` | Category B — Replay-safe recorded | Deterministic multi-branch debate; nested LLM calls use replay path |
| `superpose` | Category B — Replay-safe recorded | LLM-backed branch evaluation; nested LLM replay-safe; strict only if branch bodies are replay-safe |
| `DreamBlock` | Category B — Replay-safe recorded (result-hash) | As of Alpha3g, `dream_completed` is replay-consumed and verified via `dream_key`/`result_hash` (`interpreter.py:1357-1369`); canonical result sourced from the record. **Strict Layer 1 eligibility: denied under A2** (§6.1.1, RFC-DREAM-STRICT-LAYER1-ELIGIBILITY); future eligibility requires consume-only/subtrace/state-delta replay. Legacy pre-Alpha3g `dream_completed` without the strict schema remains Category C. |
| `Integrate` | Category B — Replay-safe recorded (body skipped) | As of Alpha3g I1–I6, `integrate_committed`/`integrate_aborted` are replay-consumed without body re-execution (`interpreter.py:1986-2038`). Strict Layer 1 eligibility: **PENDING** — 5 deferred MAJOR gates (INT-04..INT-08). Legacy pre-Alpha3g integrate events remain Category C. |
| Evolution tickets | Category C — Experimental | UUID-bound event identity |
| Habit registration | Category C — Experimental | UUID-bound generated IDs |
| Deferred consensus | Category C — Experimental | UUID-bound deferred ticket |
| Builtins time/random/uuid | Unsafe if entering canonical history | Live nondeterministic sources |
| Storage-generated IDs/timestamps | Requires separate audit | Risk depends on surfacing into canonical artifacts |

---

## 13. Alpha3g RFC candidates

The following items require separate RFCs before runtime changes:

- **13.1 Dream replay contract** — define `dream_key`, result recording format,
  result hash/reference, replay consumption path, integration with nested LLM
  calls, interaction with integrate. **Approved direction: Path A** — dream
  stays in the tree-walker and gains a replay contract via
  `next_history_event("dream_completed")`. Dream is an isolated cognitive
  primitive (its `dream_depth` guards already forbid memory.write/migrate/
  evolve/integrate/nested-fracture side effects), so CVM `DREAM_ENTER/EXIT`
  opcodes plus a snapshot manager (Path B) would be overengineering. Its
  problem is replay consumption, not the absence of bytecode.
- **13.2 Stable identity policy** — replace/constrain UUID-bound canonical
  identities with content-addressed IDs, event-indexed IDs,
  parent-history-derived IDs, deterministic virtual-clock IDs, or recorded-only
  IDs with replay consumption.
- **13.3 Integrate replay-applier semantics** — *implemented as of Alpha3g
  I1–I6* (RFC-INTEGRATE-REPLAY-APPLIER.md APPROVED, `interpreter.py:1986-2038`):
  replay consumes recorded `integrate_committed`/`integrate_aborted`, applies
  recorded write-set, skips transaction body. Five MAJOR deferred gates
  (INT-04..INT-08) must be satisfied before strict Layer 1 eligibility.
- **13.4 Affective event identity stabilization** — decide whether affective
  resonance event IDs should be deterministic, recorded-only replay identities,
  excluded from strict live-vs-live comparison, or represented by stable
  content keys.
- **13.5 Builtin nondeterminism policy** — define language-level restrictions
  for `time`, `random`, `uuid` (virtual clock; seeded RNG; recorded random
  stream; exploratory-only annotation; compile-time warning/error when values
  enter canonical history).
- **13.6 Persistence determinism audit** — audit storage backends for
  UUID-generated memory IDs, timestamps, provider IDs, persistence-level event
  ordering, and surfacing into replay artifacts.

---

## 14. Non-goals of this document

This document does not implement runtime fixes. It does not modify
`evaluate_dream()`, change `hash_event_chain()`, remove UUIDs from runtime
events, change LLM bridge behavior, alter `affective_runtime.py`, alter
builtins, change golden replay implementation, introduce tolerant compare, add
new VM opcodes, or change parser/language syntax. Runtime changes require
separate RFCs and patches.

---

## 15. Final rule

> Canonical behavior must be replay-verifiable.
> Exploratory behavior must be explicitly non-canonical.
> Anything that enters the hash chain must either be deterministic, recorded and
> replay-consumed, or marked unsafe for strict golden replay.

Until a construct satisfies this rule, it must remain outside strict golden
Layer 1.

---

## Appendix A — Layer 1 Strict Golden Suite Audit (read-only)

This appendix records a read-only audit of the current strict golden Layer 1
programs against the Category C / unsafe-construct list. No code or artifacts
were modified.

**Method:** each program was checked at three levels — (1) source scan for
unsafe keywords (`dream`, `integrate`, `evolve`, `habit`, `consensus`,
`resonance`, `uuid`, `random`, `time(`); (2) recorded `history.json` scan for
unsafe event types and UUID identity patterns (`ares-`, `evo-`, `habit-`,
`Habit-`, `consensus-`, `run-`); (3) `trace_id` stability check.

**Programs audited (6):** `actor_message`, `inline_guard_fail_recovery`,
`inline_guard_pass`, `llm_cached`, `nested_context`, `print_math`.

**Result: all 6 programs are CLEAN.**

| Program | Source scan | History events | Unsafe event types | UUID patterns | trace_id |
|---------|-------------|----------------|--------------------|---------------|----------|
| `actor_message` | clean | 1 | none | none | none |
| `inline_guard_fail_recovery` | clean | 0 | none | none | none |
| `inline_guard_pass` | clean | 0 | none | none | none |
| `llm_cached` | clean | 1 | none | none | none |
| `nested_context` | clean | 2 | none | none | none |
| `print_math` | clean | 0 | none | none | none |

No Category C construct, UUID-bound identity, or live time/random source is
present in any Layer 1 strict program. The current strict Layer 1 baseline is
safe under this contract; no programs need to be moved to Layer 2 at this time.

**Implication:** the Category C exclusions in §9.1 are currently
forward-looking guards (preventing future unsafe additions), not remediation of
existing fixtures. The strict golden gate (`tests/test_golden_replay_alpha3e.py`)
passes with zero drift across all 16 checks.
