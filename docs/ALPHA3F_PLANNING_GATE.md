# Alpha3f Planning Gate

This document is an **append-only governance trail** for Alpha3f planning. Do not
delete or rewrite passed gates. Add new dated sections for future approvals.

## Current baseline

- Stable baseline: `v2.2.0-alpha3e`
- Required release gates: `make test`, `make lint`, `make audit`, `make test-golden`
- Golden Replay artifacts are the deterministic runtime baseline.

---

## 2026-05-28 — PASSED: RFC-TIME-TRAVEL-DEBUGGER

- **Status:** PASSED
- **Approved RFC:** `docs/RFC-TIME-TRAVEL-DEBUGGER.md`
- **RFC Status:** `APPROVED — Implementation Allowed within Approved Scope`
- **Approval Date:** 2026-05-28
- **Approval Artifact SHA-256:** `e97e27fea51af841f5e28e89c6eefac03ec04fbc946ac40fc27ac32f003127ae`
- **Approval basis:** team architecture quorum; final clarification accepted for fork-local `GUARD_ENTER` injection and immutable Golden Replay lineage.

### Approved implementation scope

The following implementation branches are allowed to open after this gate:

- `feature/debugger-core`
  - `ForkRecord`
  - `ForkRegistry`
  - Overlay-based Copy-on-Write primitives
  - `ForkedVMState` adapter
  - deterministic replay/fork runtime primitives
- `feature/debug-cli-surface`
  - `synapse debug` command surface
  - debug REPL / command adapters
  - record/replay bridge integration
- `feature/event-injection-validator`
  - allowed/forbidden event injection matrix enforcement
  - governance/capability preservation checks

### Approved implementation boundaries

Allowed work must remain inside the Time-Travel Debugger scope approved by the
RFC. The first implementation patch should start with fork identity and isolated
CoW primitives, not invasive VM rewrites.

Allowed in the first debugger-core patch:

- `ForkRecord` dataclass;
- `ForkRegistry`;
- standalone `OverlayMap` utility;
- write-barrier tests;
- `ForkedVMState` adapter that wraps base `VMState` without global rewrite;
- deterministic replay error policy tests.

Not allowed in the first debugger-core patch:

- VM opcode changes;
- parser or lowering changes;
- CLI surface;
- global `VMState` rewrite;
- pre-commit, branch-orchestrator, or CI policy changes.

### Still blocked — separate RFC/gate required

- `policy enforce { ... }` block lowering;
- `throws GUARD_VIOLATION`;
- non-throwing guard syntax;
- Habit Interrupt Tokens implementation;
- Soulprint / Acoustic / Swarm routing features;
- any VM opcode changes outside debugger-approved scope;
- pre-commit or branch-orchestrator enforcement changes.

### Governance note

This passed gate remains as the audit trail for Alpha3f entry. Implementation
PRs must reference this gate and the approved RFC in their descriptions. Future
Alpha3f approvals must add new dated sections below this one rather than editing
or deleting this approval record.

---

## Track C Determinism Audit: CONCLUDED (P8, doc-only)

- **Document:** `docs/DETERMINISM_CONTRACT.md`
- **Status:** DRAFT → APPROVED (fact-based, reviewed against code)
- **Phase summary:**
  - P5: `GoldenArtifactTraceAdapter` — forensic bridge to real golden artifacts.
  - P6: `find_trace_divergence` — pure read-only divergence engine over chain hashes.
  - P7: `synapse debug compare` CLI wiring — structured JSON, exit-code contract.
  - P8: `DETERMINISM_CONTRACT.md` — three-category determinism contract.
- **Key findings (verified in code):**
  - `DreamBlock` is Category C: `dream_completed` recorded but not replay-consumed
    (`interpreter.py:1131-1157`); replay re-executes body, does not append event.
  - `affective_resonance` is Category B: `ares-` UUID `event_id`
    (`affective_runtime.py:361-382`), replay consumes recorded event; live-vs-live
    unstable.
  - `fracture`/`debate`/`superpose` are Category B (deterministic identity via
    `derive_fracture_id`, nested LLM replay-safe), NOT Category C.
  - LLM forbidden inside `integrate` by design (`interpreter.py:952`).
- **Layer 1 audit (Appendix A):** all 6 strict golden programs clean. No Category
  C construct present. No fixture relocation required. Strict gate passes with
  zero drift (16 checks).
- **Scope:** doc-only. No runtime changes.

### Alpha3g backlog (deferred, separate RFC required)

- Dream replay contract — **Path A** (tree-walker + `next_history_event("dream_completed")`);
  Path B (CVM `DREAM_ENTER/EXIT` opcodes) rejected as overengineering.
- Stable identity policy (replace UUID-bound canonical identities).
- Integrate replay-applier semantics.
- Affective event identity stabilization.
- Builtin nondeterminism policy (`time`/`random`/`uuid`).
- Persistence determinism audit.

These remain blocked behind alpha3g RFCs. P8 does not authorize any runtime change.

---

## Product Clarity Pass: COMPLETED (P9, doc-only)

- **Documents added:** `docs/ARCHITECTURE_OVERVIEW.md`, `docs/DEBUGGER_USER_GUIDE.md`.
- **README:** audited against code and corrected — stale version header fixed,
  obsolete v0.2–v1.3.1 changelog blocks removed (now in `docs/CHANGELOG.md`),
  Track C status and documentation links added. Language-reference body left
  intact.
- **Method:** every architectural claim anchored to a real module; every CLI
  command and exit code verified against `synapse/cli.py`; README version line
  verified by `test_readme_version`.
- **Scope:** doc-only. Zero changes to `synapse/` or `tests/` runtime code.
  606 passed, 1 skipped.

### P8b decision (recorded)

- **Deterministic Replay Runner (P8b) is deferred to Alpha3g.** Reason: it
  depends on replay-applier semantics for dream/integrate and on the stable
  identity policy, all of which are Alpha3g RFC candidates behind this gate.
  Building it now would yield a runner that handles only Category A + LLMCall
  and silently fails or stubs the rest — a partial runtime that creates a false
  impression of readiness. The conflicting authorization in the P8b proposal is
  withdrawn; P8b becomes the natural first code patch of Alpha3g, built on top
  of the dream/integrate replay contracts rather than before them.

Track C (Time-Travel Debugger) core/trace phase and its product-clarity pass
are both CONCLUDED. Next: Alpha3g (Execution Runtime), starting from the
dream replay contract RFC.

---

## Product Clarity Tutorial: COMPLETED (P10, examples/docs-only)

- **Documents added:** `docs/tutorials/TRACE_COMPARE_TUTORIAL.md`.
- **Examples added:** `examples/tutorial_trace_compare/baseline.syn`,
  `examples/tutorial_trace_compare/modified.syn`, and
  `examples/tutorial_trace_compare/README.md`.
- **Verified workflow:** record → replay --mock → debug compare.
- **Verified facts:**
  - baseline artifact replay returns `drift: 0`;
  - modified artifact replay returns `drift: 0`;
  - comparing an artifact with itself returns exit code `0` and `reason: equal`;
  - comparing baseline against modified returns exit code `7`,
    `reason: hash_mismatch`, and `first_divergence_index: 0`.
- **Audit report:** `reports/corpus_fallback_alpha3e.json` refreshed after adding two tutorial `.syn` examples; coverage `0.93389`, fallback `103`.
- **Scope:** examples/docs-only plus generated audit-report refresh. No runtime or test code changes.

This closes the Product Clarity sequence after P9. The current Track C workflow
is now both documented and demonstrated with a verified runnable scenario.
Next technical work remains Alpha3g RFC-driven; P10 does not authorize replay
runner, session persistence, dream/integrate fixes, or VM/parser/opcode changes.

---

## Alpha3g RFC-01: Dream Replay Contract — DRAFT OPENED

- **Status:** DRAFT — Team Review Required.
- **Document:** `docs/RFC-DREAM-REPLAY-CONTRACT.md`.
- **Scope:** documentation-only planning patch.
- **Reason:** P8 determinism audit identified `DreamBlock` as Category C: `dream_completed` records raw result in canonical history, but replay re-executes the dream body instead of consuming the recorded event.
- **Approved direction for review:** Path A — keep dream in the tree-walker and add recorded consumption via `next_history_event("dream_completed")` after RFC approval.
- **Rejected for Alpha3g RFC-01:** Path B — CVM `DREAM_ENTER` / `DREAM_EXIT` opcodes. Deferred as overengineering until the tree-walker replay contract is proven insufficient.
- **Still blocked:** runtime implementation, replay runner, strict Layer 1 dream fixtures, dream/integrate code changes, parser/opcode changes, and hash-chain changes.

This entry opens Alpha3g planning. It does not authorize implementation.

---

## Alpha3g Implementation Patch 1: Dream Replay — CONCLUDED

- **RFC:** `docs/RFC-DREAM-REPLAY-CONTRACT.md`
- **RFC Status:** APPROVED, v2
- **Implementation strategy:** Path A + A2 (`execute_and_verify`)
- **Implemented scope:** `evaluate_dream()` strict replay contract, `dream_key`, `bound_variables_hash`, `result_hash`, and `ReplayIntegrityError` tests.
- **Validation:** `pytest`, `make lint`, `make audit`, and `make test-golden` passed.
- **Still blocked behind separate RFCs:** integrate replay-applier, deterministic identity cleanup for habits/evolution/consensus, affective event identity stabilization, replay runner, session persistence, and any dream CVM-opcode design.

---

## Alpha3g P0.1 Dream Sandbox Hardening: CONCLUDED

- **Patch:** P0.1 — Strict Dream Sandbox Hardening
- **Status:** CONCLUDED
- **Implementation:** `DreamSandboxEnvironment` with local assignment shadowing and clone-on-first-read for `list`, `dict`, `set`, and `tuple` parent-scope values.
- **Error boundary:** unsupported custom/runtime objects accessed from parent scope inside dream raise `DreamSandboxIsolationError`.
- **Validation:** `pytest` passed (`620 passed, 1 skipped`); `make lint`, `make audit`, and `make test-golden` passed.
- **Scope constraints:** no parser, CVM opcode, integrate, actor, stable identity, CLI, debugger-core, or hash-chain changes.
- **Still blocked:** RFC-INTEGRATE-REPLAY-APPLIER, RFC-STABLE-CANONICAL-IDENTITY, actor causal history, stable canonical IDs/time, replay runner, and canonical object serialization.

---

## Alpha3g P0.2 RFC Drafts: OPENED

- **Patch:** P0.2 — Integrate Replay Contracts
- **Status:** DRAFT OPENED — documentation only
- **Documents added:**
  - `docs/RFC-STABLE-CANONICAL-IDENTITY.md`
  - `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`
- **Stable Identity skeleton scope:** canonical bytes, NFC strings, RFC 8785-style canonical JSON, RFC 6901 path escaping, typed set/bytes/large-int wrappers, NaN/Infinity rejection, `-0.0` normalization, schema-version applier registry principle, and canonical genesis state hash.
- **Integrate RFC scope:** state-delta journaling, `integrate_committed`, `integrate_aborted`, top-level `/env/*` and `/memory/*` paths, `op=replace/delete`, empty write sets, aliasing semantics, static + dynamic nondeterminism barrier, no nested events, schema-version replay applier, and write-set size limits.
- **Runtime lock:** `evaluate_integrate()`, `StateOverlay`, `hash_event_chain()`, parser, CVM opcodes, CLI, and runtime behavior remain unchanged and blocked until the RFCs are reviewed and approved.

---

## Alpha3g P0.2.1: Dream Determinism Contract Sync (doc-only, append-only)

- **Context:** The Alpha3g Dream Replay implementation (RFC-DREAM-REPLAY-CONTRACT,
  Path A) landed in code, but several normative docs still classified
  `DreamBlock` as Category C ("recorded but not replay-consumed"). That earlier
  P8 classification is **superseded** by the implementation.
- **Resolution (doc-only):** `DreamBlock` is now documented as **Category B**
  (result-hash replay-safe), verified against `interpreter.py:1328-1392`.
  Strict Layer 1 eligibility is **NOT** granted — §9.1 keeps DreamBlock excluded
  under an explicit pending-audit invariant.
- **Audited closure fact:** parent-scope functions are not accessible inside a
  dream (sandbox raises `DreamSandboxIsolationError` via the type check); no
  closure-mutation leak exists today. This protection is incidental (type-check
  side effect) and the error is swallowed in `eval_call` — both are recorded as
  items for the eligibility RFC, not fixed here.
- **New backlog item:** `RFC-DREAM-STRICT-LAYER1-ELIGIBILITY` must close three
  open items before §9.1 may admit DreamBlock to Strict Layer 1:
  (1) observable body re-execution during replay;
  (2) closure/global mutation isolation as an explicit, tested contract
      (and stop swallowing `DreamSandboxIsolationError`);
  (3) deterministic nested-event origin.
- **Scope:** doc-only. No runtime/code/test changes. §9.1 not opened.

Historical entries above are unchanged. This addendum supersedes only the dream
classification, not the audit-trail records.
---

## Alpha3g P0.2.2: Dream Strict Layer 1 Eligibility RFC (doc-only, append-only)

- **Context:** P0.2.1 created the backlog item
  `RFC-DREAM-STRICT-LAYER1-ELIGIBILITY` after documenting Alpha3g `DreamBlock`
  as Category B but still excluded from Strict Layer 1. The unresolved question
  was whether A2 replay could ever satisfy the strict bar while re-executing the
  dream body.
- **Resolution (doc-only):** Added
  `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md`. The verdict is default-deny:
  `DreamBlock` is **not Strict Layer 1 eligible under A2** because A2 replay
  executes the dream body before consuming `dream_completed`. Future eligibility
  is possible only through a consume-only, state-delta, or recorded subtrace
  replay model.
- **Audited side-effect fact:** `print` inside `dream` does not flow through
  `Interpreter._print()` / `output_buffer`; the sandbox rejects the parent-scope
  callable, `eval_call()` swallows `DreamSandboxIsolationError`, and the call
  falls through to `BUILTINS["print"]`, producing host stdout during replay-time
  body execution. This concrete host-visible effect blocks strict admission
  under A2.
- **Scope:** doc-only. No runtime/code/test changes. §9.1 remains closed;
  `DreamBlock` remains Category B until a future replay model removes
  replay-time body execution and closes builtin, closure/function, and
  nested-event-origin boundaries.

Historical entries above are unchanged. This addendum resolves the P0.2.1
eligibility backlog item by denying A2 strict admission, not by declaring dream
strictness impossible forever.


---

## Alpha3g P0.2.3: Dream Strict RFC Errata & Shared Canonicalization Hooks (doc-only, append-only)

- **Context:** P0.2.2 accepted the central verdict that `DreamBlock` is not
  Strict Layer 1 eligible under A2, while leaving future eligibility open through
  consume-only, state-delta, or recorded-subtrace replay. Team review requested
  clearer builtin-resolution wording and explicit shared contracts before the
  Integrate RFC review.
- **Resolution (doc-only):** Updated
  `docs/RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` to v2 with errata. The RFC now
  clarifies the exact `print` path (`env.get()` → sandbox rejection → swallowed
  `DreamSandboxIsolationError` → `BUILTINS["print"]` fallback), classifies
  forbidden strict-dream builtins (`print`, `time`, `random`, `uuid`), compares
  future replay model families, defines strict `dream_completed` invariants, and
  adds shared canonicalization hooks for Dream, Integrate, and Stable Canonical
  Identity.
- **Scope:** doc-only. No runtime/code/test/example changes. `DreamBlock`
  remains Category B under A2 and excluded from Strict Layer 1. Integrate code
  remains blocked pending approved RFC review.

Historical entries above are unchanged. This addendum applies errata to the
P0.2.2 Dream Strict Eligibility RFC and prepares the ground for P0.2.4
`RFC-INTEGRATE-REPLAY-APPLIER` structured review.
---

## Alpha3g P0.2.4: RFC-INTEGRATE Structured Review — REVIEW FEEDBACK APPLIED

- **Patch:** P0.2.4 — RFC-INTEGRATE Structured Review
- **Status:** REVIEW FEEDBACK APPLIED — `RFC-INTEGRATE-REPLAY-APPLIER.md` remains DRAFT / NEEDS REVISION
- **New review registry:** `docs/RFC-INTEGRATE-REVIEW-NOTES.md`
- **Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`
- **Severity outcome:** three BLOCKER findings, five MAJOR findings, two MINOR findings
- **Approval blockers:**
  1. Function serialization in `write_set`
  2. Memory key canonical encoding beyond RFC 6901
  3. Habit activation / background runtime mutation during integrate
- **Shared-contract linkage:** review items explicitly depend on the shared canonicalization hooks in `RFC-DREAM-STRICT-LAYER1-ELIGIBILITY.md` v2 and `RFC-STABLE-CANONICAL-IDENTITY.md`.
- **Runtime lock:** `evaluate_integrate()`, `StateOverlay`, replay appliers, CVM/opcode work, parser, actor runtime, CLI, and stable-identity runtime implementation remain blocked until the revised RFC reaches APPROVED status.
- **Scope constraints:** documentation only; no runtime behavior changes.


---

## Alpha3g P0.2.5: RFC Process & Review Registry Governance — PROCESS BASELINE ESTABLISHED

- **Patch:** P0.2.5 — RFC Process & Review Registry Governance
- **Status:** PROCESS BASELINE ESTABLISHED — doc-only
- **New process document:** `docs/RFC-PROCESS.md`
- **Updated review registry:** `docs/RFC-INTEGRATE-REVIEW-NOTES.md`
- **Purpose:** Standardize how RFCs move through `DRAFT`, `NEEDS REVISION`, `APPROVAL-CANDIDATE`, `APPROVED`, `DEPRECATED`, `SUPERSEDED`, `ARCHIVED`, and `REJECTED` states.
- **Finding lifecycle:** `OPEN -> RESOLVED -> VERIFIED`, with `REOPENED` allowed on new evidence. Self-verification is forbidden.
- **Approval gate:** an RFC cannot move to `APPROVAL-CANDIDATE` while any associated BLOCKER finding is not `VERIFIED`.
- **Source of truth:** RFC files are product artifacts and become the normative technical contract after approval. Review notes are process artifacts and remain as audit trail.
- **Dependency policy:** RFC dependencies must be declared, status-compatible, and version-pinned where breaking changes are possible.
- **Integrate status:** `RFC-INTEGRATE-REPLAY-APPLIER.md` remains `DRAFT / NEEDS REVISION`; INT-01, INT-02, and INT-03 remain OPEN BLOCKER findings. Their technical resolution is deferred to P0.2.6.
- **Runtime lock:** `evaluate_integrate()`, `StateOverlay`, replay appliers, CVM/opcode work, parser, actor runtime, CLI, and stable-identity runtime implementation remain blocked until the revised RFC reaches `APPROVED`.
- **Scope constraints:** documentation only; no runtime behavior changes.
---

## Alpha3g P0.2.6: RFC-INTEGRATE Blocker Resolution Revision — APPROVAL-CANDIDATE

- **Patch:** P0.2.6 — RFC-INTEGRATE Blocker Resolution Revision
- **Status:** AUTHOR RESOLUTION APPLIED — `RFC-INTEGRATE-REPLAY-APPLIER.md` is now `APPROVAL-CANDIDATE — Team Verification Required`
- **Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`
- **Review registry:** `docs/RFC-INTEGRATE-REVIEW-NOTES.md`
- **Resolved blockers pending verification:**
  1. INT-01 Function serialization in `write_set` — resolved by §15.1 / §15.2.
  2. INT-02 Memory key encoding beyond RFC 6901 — resolved by §4.2 / §6.
  3. INT-03 Habit activation during integrate — resolved by §12 habit/background mutation barrier.
- **Process note:** Under `docs/RFC-PROCESS.md`, P0.2.6 is an author-resolution patch. INT-01 through INT-03 are `RESOLVED`, not `VERIFIED`. Independent team verification is required in P0.2.7 before RFC approval.
- **Runtime lock:** `evaluate_integrate()`, `StateOverlay`, replay appliers, CVM/opcode work, parser, actor runtime, CLI, and stable-identity runtime implementation remain blocked until the revised RFC reaches `APPROVED`.
- **Scope constraints:** documentation only; no runtime behavior changes.
---

## Alpha3g P0.2.7: RFC-INTEGRATE Team Verification & Approval Gate — APPROVED

- **Patch:** P0.2.7 — RFC-INTEGRATE Team Verification & Approval Gate
- **Status:** APPROVED — doc-only approval gate
- **Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`
- **Review registry:** `docs/RFC-INTEGRATE-REVIEW-NOTES.md`
- **Verified blockers:**
  1. INT-01 Function serialization in `write_set` — VERIFIED.
  2. INT-02 Memory key encoding beyond RFC 6901 — VERIFIED.
  3. INT-03 Habit activation during integrate — VERIFIED.
- **Deferred MAJOR gates:** INT-04 through INT-08 remain implementation gates and must be satisfied before affected runtime behavior merges.
- **Tracked MINOR notes:** INT-09 and INT-10 remain future-compatibility notes.
- **Runtime scope:** no code changes in this patch. Future integrate implementation may begin only in later patches and only within the approved RFC scope.
- **Scope constraints:** documentation only; no runtime behavior changes.
---

## Alpha3g P0.2.8: Integrate Implementation Planning — IMPLEMENTATION PLAN ESTABLISHED

- **Patch:** P0.2.8 — Integrate Implementation Planning
- **Status:** IMPLEMENTATION PLAN ESTABLISHED — doc-only
- **New planning document:** `docs/INTEGRATE-IMPLEMENTATION-PLAN.md`
- **Target RFC:** `docs/RFC-INTEGRATE-REPLAY-APPLIER.md` (`APPROVED — Alpha3g P0.2.7`)
- **Purpose:** Decompose the approved Integrate RFC into staged implementation patches before any runtime code is changed.
- **Recommended first runtime target:** `P0.3.0 / I1 — StateOverlay Core & Canonical Path Parser`.
- **Deferred gate mapping:** INT-04 through INT-08 are mapped to the implementation milestones that must satisfy them before affected runtime behavior merges.
- **Stable Identity boundary:** Integrate implementation may proceed only for value categories whose canonical form is already defined by the approved Integrate RFC or an approved dependency. Functions, closures, agent values, stable runtime IDs, and canonical time remain unsupported unless a later approved RFC/patch explicitly opens them.
- **Golden replay plan:** future Integrate runtime patches must add golden fixtures for committed, aborted, no-op, hash-mismatch, Unicode-key, serialization-error, and idempotent replay cases.
- **Runtime lock:** P0.2.8 does not start implementation. `evaluate_integrate()`, `StateOverlay`, replay appliers, CVM/opcode work, parser, actor runtime, CLI, and stable-identity runtime implementation remain unchanged in this patch.
- **Scope constraints:** documentation only; no runtime behavior changes.


---

## Alpha3g P0.3.0: StateOverlay Core & Canonical Path Parser — I1 IMPLEMENTED

- **Patch:** P0.3.0 / I1 — StateOverlay Core & Canonical Path Parser
- **Status:** I1 IMPLEMENTED — isolated runtime infrastructure
- **New runtime modules:** `synapse/canonical_path.py`, `synapse/state_overlay.py`
- **New tests:** `tests/test_state_overlay_core_p030.py`
- **Purpose:** Implement the first approved Integrate runtime building block without wiring it into `evaluate_integrate()` or replay.
- **INT-08 coverage:** canonical path syntax validation is implemented for `/env/<identifier>` and `/memory/<canonical_key>`, including empty memory key handling, rejection of bare paths, unknown namespaces, extra segments, invalid percent escapes, non-canonical encoding, and ambiguous memory-key forms. Runtime namespace semantic validation remains for I2.
- **StateOverlay coverage:** copy-on-write reads, dirty-path tracking, no-op write elision, delete tombstones, sorted draft write-set generation, canonical value hashing, and callable/function rejection are implemented.
- **Integration boundary:** `evaluate_integrate()`, `integrate_committed`, `integrate_aborted`, REPLAY appliers, CVM/opcodes, actor runtime, promise cleanup, and agent canonicalization remain unchanged and locked for later milestones.
- **Tests:** targeted determinism gate plus P0.3.0 StateOverlay tests pass.
---

## Alpha3g P0.3.0a: StateOverlay Interface Hardening — I1.1 HARDENED

- **Patch:** P0.3.0a / I1.1 — StateOverlay Interface Hardening
- **Status:** I1.1 HARDENED — isolated runtime infrastructure stabilization
- **Changed runtime module:** `synapse/state_overlay.py`
- **Updated tests:** `tests/test_state_overlay_core_p030.py`
- **Purpose:** stabilize the public StateOverlay/WriteSet interface before `evaluate_integrate()` begins depending on it in I2.
- **Interface hardening:** `commit()` now returns immutable `WriteSet(entries=tuple[WriteSetEntry, ...])`; `WriteSet.to_list()` remains the explicit serialization boundary for future event emission.
- **Canonicalization note:** `canonical_value_hash()` and `StateOverlay.canonical_hash()` are documented as the Alpha3g I1 local canonical JSON subset, not the full future `RFC-STABLE-CANONICAL-IDENTITY` implementation.
- **Semantic documentation:** delete tombstones, terminal discard behavior, eager unsupported-value rejection, and canonical-hash-based no-op elision are explicitly documented.
- **Edge tests:** added coverage for delete/re-set, set-then-delete elision, commit-after-discard, unsupported values, non-string dict keys, canonical hash stability, and malformed percent escapes.
- **Integration boundary:** `interpreter.py`, `evaluate_integrate()`, `integrate_committed`, `integrate_aborted`, REPLAY appliers, CVM/opcodes, actor runtime, promise cleanup, and agent canonicalization remain unchanged and locked for I2+.



---

## Alpha3g P0.3.1: LIVE-mode Integrate Skeleton — I2 IMPLEMENTED

- **Patch:** P0.3.1 / I2 — minimal LIVE-mode Integrate Skeleton.
- **Status:** I2 IMPLEMENTED — opt-in runtime skeleton.
- **Changed runtime module:** `synapse/interpreter.py`.
- **New tests:** `tests/test_integrate_live_skeleton_p031.py`.
- **Purpose:** wire `StateOverlay` into an explicit Alpha3g integrate skeleton path without changing legacy default integrate behavior.
- **Feature flag:** `Interpreter.integrate_i2_skeleton_enabled` enables the I2 path; legacy v1.4/v1.4.1 integrate semantics remain default until a later approved patch flips the runtime mode.
- **I2 behavior:** creates `StateOverlay`, executes the integrate body inside `IntegrateOverlayEnvironment`, collects immutable draft `WriteSet` in `last_integrate_write_set`, and leaves the parent environment unchanged.
- **Barrier coverage:** I2 runtime barrier rejects forbidden builtins (`print`, `time`, `random`, `uuid`), nested `dream`, `evolve`, and memory mutation operations while the skeleton is active.
- **Explicitly out of scope:** no `integrate_committed` / `integrate_aborted` history events, no base-state write-set application, no REPLAY applier, no CVM/opcode work, no actor runtime changes, no promise cleanup, and no agent canonicalization.
- **Tests:** I2 skeleton tests, I1/I1.1 tests, targeted determinism gate, and full pytest pass.
---

## Alpha3g P0.3.2: Integrate Implementation I2.1 — HARDENED

- **Patch:** P0.3.2 / I2.1 — Integrate Skeleton Hardening & INT-04 Guard.
- **Status:** HARDENED — I2 skeleton is stabilized before I3 event schema emission.
- **Scope:** runtime hardening limited to `synapse/interpreter.py`, `synapse/state_overlay.py`, and dedicated tests.
- **INT-04 handling:** I2 now enforces guard-level prevention for actor/promise-producing operations. `spawn` is blocked before actor refs/promises are created; therefore no orphan promise can be produced by the I2 skeleton. Full resource cleanup remains an implementation gate for later stages that intentionally open actor/promise production.
- **I2.1 checks:** parent mutable values are clone-on-read, aliases are stable within the overlay, builtin `print` cannot fall through to host stdout, nested integrate/fracture/collective primitives are rejected, no-op transactions produce an empty immutable `WriteSet`, and overlays are discarded on ordinary exceptions.
- **Still locked:** `integrate_committed`, `integrate_aborted`, execution-history emission, base-state write-set application, REPLAY applier, CVM/opcodes, actor runtime changes, and agent canonicalization.
- **Next candidate:** P0.3.3 / I3 — Integrate Event Schema Emission, pending team review of P0.3.2.
---

## Alpha3g P0.3.3: Integrate Implementation I3 — LIVE COMMIT & EVENT EMISSION

- **Patch:** P0.3.3 / I3 — Integrate LIVE Commit & Event Schema Emission.
- **Status:** I3 IMPLEMENTED — opt-in LIVE event layer.
- **Changed runtime modules:** `synapse/interpreter.py`, `synapse/state_overlay.py`.
- **New tests:** `tests/test_integrate_event_emission_p033.py`.
- **Event emission:** the opt-in Alpha3g integrate path records `integrate_committed` with `schema_version`, `pre_state_hash`, `post_state_hash`, sorted `write_set`, and `write_set_hash`; abort paths record `integrate_aborted` with sanitized failure metadata and `overlay_summary`.
- **Base-state application:** successful `/env/*` write sets are applied atomically to the base environment after the event payload is constructed. Failed application rolls back touched environment bindings.
- **Abort safety:** barrier violations and ordinary exceptions discard the overlay, leave base env unchanged, clear `last_integrate_write_set`, and emit an abort event without host tracebacks.
- **Still locked:** REPLAY applier, CVM/opcodes, actor runtime changes, full promise cleanup registry, agent canonicalization, Stable Identity runtime, and golden replay fixtures.
- **Next candidate:** P0.3.4 / I4 — REPLAY applier v1, pending team review of P0.3.3.


---

## Alpha3g P0.3.4: Integrate Implementation I4 — REPLAY APPLIER v1

- **Patch:** P0.3.4 / I4 — Integrate REPLAY Applier v1.
- **Status:** I4 IMPLEMENTED — opt-in REPLAY event applier.
- **Changed runtime module:** `synapse/interpreter.py`.
- **New tests:** `tests/test_integrate_replay_applier_p034.py`.
- **Committed replay:** consumes recorded `integrate_committed`, verifies schema/pre-state/write-set/per-entry/post-state hashes, applies the recorded `/env/*` write-set, and does not execute the integrate body.
- **Aborted replay:** consumes recorded `integrate_aborted`, verifies pre-state, leaves state unchanged, and reproduces a deterministic abort exception without executing the integrate body.
- **INT-06 v1 handling:** in-run idempotency guard prevents the same integrate event index from being applied twice in one replay runner. Durable crash-resume checkpoints and commit nonces remain out of scope per RFC v1.
- **Still locked:** CVM/opcodes, actor runtime changes, full promise cleanup registry, Stable Identity runtime, agent canonicalization, durable replay checkpointing, and golden replay fixtures.
- **Next candidate:** P0.3.5 / I6 — Integrate golden fixtures and replay conformance artifacts, or a small I4 review-hardening patch if team review finds issues.

---

## Alpha3g P0.3.5: Integrate Golden Fixtures & Replay Conformance (I6)

- **Scope:** examples-only + `synapse/golden_replay.py` helpers + 8 new tests.
- **New helpers:** `record_integrate_artifact()` and `replay_integrate_artifact()`
  — separate from `record_source()` so existing strict Layer 1 fixtures are
  unaffected.
- **Conformance coverage:** committed replay, body-skip proof, read-only no-op,
  barrier-violation abort, hash-tamper detection, state-hash round-trip,
  idempotency guard, and unaffected-strict-suite regression.
- **Still locked:** CVM/opcodes, actor-runtime changes, Stable Identity runtime,
  agent canonicalization, durable crash-resume, and promise cleanup registry.
- **Next candidate:** P0.3.6 / I7 — full gate, audit, and docs sync (release-
  readiness pass and documentation synchronization for integrate v1).

---

## Alpha3g P0.3.6: Integrate Release-Readiness Pass (I7, doc-only)

- **Scope:** documentation sync only. Zero runtime/code/test changes.
- **What was synced:**
  - `DETERMINISM_CONTRACT.md`: integrate upgraded from Category C (RFC pending)
    to Category B (replay-applier implemented, I1–I6). §6.3 rewritten with code
    citations. §9.1 gets explicit integrate Strict Layer 1 CRITICAL INVARIANT.
    §12 table updated. §13.3 marked implemented.
  - `ARCHITECTURE_OVERVIEW.md`: integrate replay-applier listed as done; deferred
    runner reason updated.
  - `SEMANTICS.md`: integrate replay cell corrected — body not re-executed,
    `integrate_rollback` → `integrate_aborted`, full event schema.
  - `RFC-INTEGRATE-REVIEW-NOTES.md`: INT-09/10 from OPEN → ACKNOWLEDGED (v1
    boundary documented).
- **Still blocked:** INT-04 (promise cleanup), INT-05 (genesis baseline),
  INT-06 (durable idempotency), INT-07 (agent canonicalization),
  INT-08 (namespace validation) — DEFERRED MAJOR gates.
- **Integrate v1 milestone:** I1–I7 complete. Integrate is Category B.
  Strict Layer 1 eligibility requires closing INT-04..INT-08.
- **Next milestone:** RFC-STABLE-CANONICAL-IDENTITY for shared identity
  contracts across integrate / dream / agent canonicalization.

## Alpha3g P0.4.0: Stable Canonical Identity RFC Expansion (doc-only)

- **Scope:** documentation only. Zero runtime/code/test changes.
- **Changed:** `RFC-STABLE-CANONICAL-IDENTITY.md` promoted from DRAFT SKELETON
  v0.1 to full DRAFT v0.2 parent contract.
- **New contract coverage:** canonicalization profiles, allowlist-based canonical
  value policy, function/closure v1 rejection and future descriptor requirements,
  canonical time principles, deterministic identity derivation, migration rules,
  and acceptance criteria.
- **Current runtime boundary:** `StateOverlay` and Integrate v1 remain on their
  approved local canonical subsets (`alpha3g.local-json.v1`,
  `alpha3g.integrate-path.v1`). This patch does not replace them with Stable
  Identity runtime code.
- **Dependency linkage:** RFC now explicitly maps Dream and Integrate strict
  eligibility gates to Stable Identity work: functions/closures, canonical time,
  deterministic resource/event identity, agent snapshots, and namespace
  semantics.
- **Still locked:** Stable Identity runtime, CVM/opcodes, actor runtime, agent
  canonicalization, durable crash-resume identity, and storage backend migration.


---

## Alpha3g P0.4.1: Stable Canonical Identity Structured Review — REVIEW OPENED

- **Patch:** P0.4.1 — Stable Canonical Identity Structured Review.
- **Status:** REVIEW OPENED — `RFC-STABLE-CANONICAL-IDENTITY.md` remains DRAFT v0.2.
- **Added review registry:** `docs/RFC-STABLE-CANONICAL-IDENTITY-REVIEW-NOTES.md`.
- **BLOCKERs opened:** STABLE-01 canonical time replay source; STABLE-02 builtin allowlist side-effect fail-closed policy.
- **MAJORs opened:** STABLE-03..STABLE-08 covering profile/version handling, FunctionDescriptor boundary, agent snapshot exclusions, deterministic identity domains, local-profile migration, and genesis alignment.
- **MINORs opened:** STABLE-09..STABLE-10 for acceptance-test criteria and profile registry lifecycle.
- **Process:** governed by `docs/RFC-PROCESS.md`; BLOCKERs must move `OPEN -> RESOLVED -> VERIFIED` before `APPROVAL-CANDIDATE`.
- **Runtime lock:** no Stable Identity runtime, canonical time API, deterministic ID generation, function/agent canonicalization, CVM/opcode, actor runtime, or storage migration changes are authorized by this patch.
---

## Alpha3g P0.4.2: Stable Canonical Identity RFC Revision — APPROVAL-CANDIDATE

- **Patch:** P0.4.2 — Stable Canonical Identity RFC Revision & Blocker Closure.
- **Scope:** documentation only. Zero runtime/code/test changes.
- **RFC status:** `RFC-STABLE-CANONICAL-IDENTITY.md` moved to
  `APPROVAL-CANDIDATE — Alpha3g P0.4.2 team verification required`.
- **Resolved pending verification:** STABLE-01 canonical time replay source,
  STABLE-02 builtin allowlist fail-closed policy, STABLE-03 schema/profile
  version fail-closed handling.
- **Deferred implementation gates:** STABLE-04 FunctionDescriptor, STABLE-05
  AgentSnapshot, STABLE-06 deterministic identity seed domains, STABLE-07
  profile migration/artifact compatibility, STABLE-08 genesis alignment.
- **Acknowledged v1 boundaries:** STABLE-09 testable allowlist acceptance
  criteria and STABLE-10 profile registry lifecycle.
- **Next gate:** P0.4.3 team verification / approval vote. No Stable Identity
  runtime implementation is authorized before `APPROVED`.


---

## Alpha3g P0.4.3: Stable Canonical Identity RFC — APPROVED

- **Patch:** P0.4.3 — Stable Canonical Identity Team Verification & Approval Gate.
- **Status:** `RFC-STABLE-CANONICAL-IDENTITY.md` APPROVED as v1.0.
- **Verification:** STABLE-01, STABLE-02, and STABLE-03 moved from `RESOLVED` to `VERIFIED` after independent team verification.
- **Deferred gates:** STABLE-04..STABLE-08 remain implementation gates and must be resolved in the scoped runtime patches that touch their areas.
- **Acknowledged boundaries:** STABLE-09..STABLE-10 remain accepted v1 governance / acceptance-planning boundaries.
- **Runtime authorization:** approval of the RFC authorizes future separately scoped implementation patches; P0.4.3 itself contains no runtime changes.
- **Compatibility note:** existing Alpha3g local profiles remain valid for existing Category B artifacts. New stable canonical runtime work must target `stable-canonical.v1` unless explicitly declared compatibility work.
- **DENY in this patch:** no `synapse/`, tests, parser, interpreter, CVM/opcodes, actor runtime, Stable Identity runtime, canonical time API, deterministic ID generation, FunctionDescriptor, or AgentSnapshot implementation changes.
---

## Alpha3g P0.4.4: Stable Canonical Value Runtime Core (SI1) — IMPLEMENTED

- **Patch:** P0.4.4 — Stable Canonical Value Runtime Core.
- **Scope:** standalone runtime module + tests. No integration with interpreter,
  StateOverlay, canonical paths, CVM/opcodes, actor runtime, golden replay,
  canonical time, deterministic identity generation, FunctionDescriptor, or
  AgentSnapshot.
- **Added:** `synapse/canonical_values.py` implementing `stable-canonical.v1`
  value serialization primitives and hash helpers.
- **Added tests:** `tests/test_stable_canonical_values_p044.py`.
- **Covered contract:** NFC normalization, invalid Unicode rejection, safe/large
  integer encoding, finite float checks, `-0.0 -> 0.0`, non-string dict key
  rejection, bytes base64url-nopad wrapper, set canonical sorting, cycle
  detection, and fail-closed rejection for functions/callables and host objects.
- **Still locked:** canonical time API, deterministic identity generation,
  FunctionDescriptor, AgentSnapshot, migration of existing local hashes to
  `stable-canonical.v1`, and all interpreter/CVM/actor integrations.
---

## Alpha3g P0.4.5: Stable Canonical Value Review & Hardening (SI2) — HARDENED

- **Patch:** P0.4.5 — Stable Canonical Value Review & Hardening.
- **Scope:** standalone `stable-canonical.v1` value-core hardening + tests + migration readiness checklist.
- **Runtime integration:** none. `interpreter.py`, `state_overlay.py`, `canonical_path.py`, CVM/opcodes, actor runtime, golden replay, canonical time, deterministic ID generation, `FunctionDescriptor`, and `AgentSnapshot` remain locked.
- **Code hardening:** `synapse/canonical_values.py` now exports `PROFILE_VERSION`, documents forensic invariants, and rejects excessive nesting via `MAX_NESTING_DEPTH` with `CanonicalSerializationError`.
- **Test hardening:** SI tests now include known hash fixtures, typed wrapper round trips, mixed-type set ordering, deep nesting rejection, Unicode edge cases, forensic error paths, and additional cycle detection.
- **Migration readiness:** added `docs/MIGRATION-READINESS-CHECKLIST.md`; future stable canonical integration patches must satisfy the checklist before migrating existing Alpha3g local profiles.
- **Next gate:** scoped integration planning or first migration patch may begin only after SI2 review; no subsystem migration is performed by P0.4.5.


---

## Alpha3g P0.4.6: Stable Canonical Integration Service — SI3 ADDED

- **Patch:** P0.4.6 — Stable Canonical Integration Service & Migration Analysis.
- **Status:** SI3 ADDED — migration analysis boundary established; consumer migration remains blocked.
- **Added runtime module:** `synapse/canonical_service.py`.
- **Added tests:** `tests/test_stable_canonical_service_p046.py`.
- **Purpose:** provide an isolated service for `stable-canonical.v1` hashing and drift comparison against `alpha3g.local-json.v1` before any subsystem migration.
- **Drift analysis:** `compare_profile_hashes()` reports local hash, stable hash, drift flag, and machine-readable category.
- **Migration checklist:** `docs/MIGRATION-READINESS-CHECKLIST.md` now marks StateOverlay migration as planned but blocked until SI3 drift analysis is complete and compatibility gates are satisfied.
- **DENY in this patch:** no changes to `state_overlay.py`, `interpreter.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, existing Alpha3g hash paths, canonical time API, deterministic IDs, FunctionDescriptor, or AgentSnapshot.

---

## Alpha3g P0.4.7 — Stable Canonical Drift Baseline & Migration Report

- **Status:** COMPLETED — SI4-prep measurement gate.
- **Scope:** read-only drift analysis over current Integrate Category B fixture payloads.
- **Added test:** `tests/test_stable_canonical_drift_report_p047.py`.
- **Added report:** `docs/MIGRATION-DRIFT-REPORT.md`.
- **Result:** GO for future feature-flagged StateOverlay migration.
- **Evidence:** 14 / 14 analyzed payload fragments classified as `drift_category = none`; no breaking drift, no rejection, no unexplained hash drift.
- **Strict lock:** no changes to `state_overlay.py`, `interpreter.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, existing hash paths, or fixtures.
- **Next authorized direction:** SI4 may add an explicit StateOverlay profile selector in a separate scoped patch. Hard switch remains forbidden.

---

## Alpha3g P0.4.8 — StateOverlay Stable Canonical Migration (SI4): COMPLETED (flagged)

- **Patch:** P0.4.8 — StateOverlay stable-canonical profile selector.
- **Status:** COMPLETED (flagged).
- **Runtime scope:** `synapse/state_overlay.py` only.
- **Default profile:** `alpha3g.local-json.v1` remains the default for backwards compatibility.
- **Opt-in profile:** `stable-canonical.v1` is available through explicit `StateOverlay(..., profile="stable-canonical.v1")`.
- **Evidence:** dual-profile StateOverlay tests pass; legacy behavior remains unchanged; P0.4.7 drift baseline showed GO for flagged migration.
- **Checklist:** `docs/MIGRATION-READINESS-CHECKLIST.md` marks StateOverlay migration as completed in flagged mode.
- **Still denied:** hard switch, `interpreter.py` migration, Integrate/golden fixture rewrite, `canonical_path.py`, CVM/opcodes, actor runtime, canonical time API, deterministic IDs, FunctionDescriptor, and AgentSnapshot.

---

## Alpha3g P0.4.9 — Integrate Stable Canonical Analysis (SI5-prep): COMPLETED

- **Patch:** P0.4.9 — Integrate stable-canonical migration drift baseline.
- **Status:** COMPLETED — SI5-prep measurement gate.
- **Scope:** read-only analysis over current Integrate Category B hash/event-path payload fragments.
- **Added test:** `tests/test_integrate_stable_canonical_drift_p049.py`.
- **Added report:** `docs/INTEGRATE-MIGRATION-DRIFT-REPORT.md`.
- **Result:** GO for future feature-flagged Integrate hash-path migration.
- **Evidence:** 28 / 28 analyzed fragments classified as `drift_category = none`; no breaking drift, no rejection, no unexplained hash drift.
- **Strict lock:** no changes to `interpreter.py`, `evaluate_integrate()`, `state_overlay.py`, `canonical_path.py`, `golden_replay.py`, CVM/opcodes, actor runtime, existing hash paths, or fixtures.
- **Next authorized direction:** SI5 may add an explicit Integrate profile selector in a separate scoped patch. Hard switch remains forbidden.

---

## Alpha3g P0.4.10 — Integrate Stable Canonical Migration (SI5): COMPLETED (flagged)

- **Patch:** P0.4.10 — Integrate stable-canonical profile selector.
- **Status:** COMPLETED (flagged).
- **Runtime scope:** `synapse/interpreter.py` Integrate hash/event path selection only.
- **Default profile:** `alpha3g.local-json.v1` remains the default for backwards compatibility.
- **Opt-in profile:** `stable-canonical.v1` is available through explicit `Interpreter.integrate_hash_profile = "stable-canonical.v1"`.
- **Evidence:** dual-profile Integrate tests pass; legacy behavior remains unchanged; P0.4.9 drift baseline showed GO for flagged migration.
- **Checklist:** `docs/MIGRATION-READINESS-CHECKLIST.md` marks Integrate hash path migration as completed in flagged mode.
- **Still denied:** hard switch, fixture rewrite, `state_overlay.py` migration beyond the existing flag, `canonical_path.py`, golden replay helper migration, CVM/opcodes, actor runtime, canonical time API, deterministic IDs, FunctionDescriptor, and AgentSnapshot.
---

## Alpha3g P0.5.0 — Agent Canonicalization RFC: DRAFT OPENED

- **Patch:** P0.5.0 — Agent Canonicalization RFC Draft.
- **Status:** DRAFT OPENED — team review required.
- **Scope:** documentation only.
- **Added RFC:** `docs/RFC-AGENT-CANONICALIZATION.md`.
- **Added review registry:** `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md` with AGENT-01..AGENT-10.
- **Core concept:** agent canonicalization is split into Canonical Agent Definition, Canonical Agent Instance Snapshot, and Non-canonical Runtime Envelope.
- **New design hooks:** CVM Boundary Contract and declarative Capability Grants.
- **Runtime lock:** no changes to `interpreter.py`, `actor_runtime.py`, `state_overlay.py`, CVM/opcodes, stable canonical runtime, golden fixtures, AgentSnapshot implementation, FunctionDescriptor, canonical time, or deterministic IDs.
- **Next gate:** P0.5.1 structured review / blocker classification under `RFC-PROCESS.md`.


---

## Alpha3g P0.5.1 — Agent Canonicalization RFC Revision & Blocker Strategy

- **Patch:** P0.5.1 — Agent Canonicalization RFC Revision & Blocker Strategy.
- **Status:** REVISED — doc-only blocker strategy applied.
- **Scope:** documentation/process only.
- **Changed docs:** `RFC-AGENT-CANONICALIZATION.md`, `RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`, `CHANGELOG.md`, `ALPHA3F_PLANNING_GATE.md`.
- **AGENT-01:** RESOLVED — deterministic agent id derivation with `genesis_config_hash` cold-start fallback, assigned causal `spawn_nonce`, and `AgentIdCollisionError` fail-closed rule.
- **AGENT-03:** RESOLVED — canonical `memory_ref` using stable `memory_space_id`, `access_mode`, address-only dereference boundary, and `MemoryRefNotResolvedError` fail-closed rule.
- **AGENT-02:** SPLIT — executable definition identity requires prerequisite `RFC-FUNCTION-DESCRIPTOR`; Agent v1 is limited to externally declared static definition manifests.
- **AGENT-04:** PARTIALLY RESOLVED — mandatory capability attenuation clarified; policy linkage / ownership / full scope schema remains open.
- **AGENT-11:** DEFERRED — schema version registry is a MAJOR implementation gate; runtime must fail closed on unknown schema/profile before AgentSnapshot deployment.
- **Dependency graph:** P0.5.1 Agent RFC Revision -> P0.5.2 RFC-FUNCTION-DESCRIPTOR Draft -> P0.5.3 Agent RFC Verification / Approval Candidate -> P0.5.4 Agent RFC Approval -> P0.5.5 AgentSnapshot Runtime Core.
- **Runtime lock:** all `synapse/`, `tests/`, CVM/opcodes, actor runtime, interpreter, golden fixtures, AgentSnapshot runtime, FunctionDescriptor runtime, canonical time API, and deterministic ID implementation remain denied until the RFC gates are approved.

---

## Alpha3g P0.5.2 — Function Descriptor RFC: DRAFT OPENED

- **Patch:** P0.5.2 — Function Descriptor RFC Draft.
- **Status:** DRAFT OPENED — team review required.
- **Scope:** documentation/process only.
- **Added RFC:** `docs/RFC-FUNCTION-DESCRIPTOR.md`.
- **Added review registry:** `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md` with FUNC-01..FUNC-10.
- **Core concept:** FunctionDescriptor v1 identifies declarative callable contracts and capability/effect boundaries, not executable implementation bodies.
- **Two-tier strategy:** v1 contract identity now; v2 / future executable identity via approved canonical AST, CVM image, or content-addressed logic module only after a separate approval gate.
- **Forbidden identity sources:** Python bytecode, `inspect.getsource()`, `__code__`, runtime source AST, host paths, closure cells, `repr(function)`, wall-clock, UUIDs, and runtime object identity remain denied as canonical identity inputs.
- **Agent dependency:** this RFC is the prerequisite split for AGENT-02 from `RFC-AGENT-CANONICALIZATION.md`.
- **Review gate:** FUNC-01 and FUNC-02 must be RESOLVED and independently VERIFIED before this RFC can move to `APPROVAL-CANDIDATE`; FUNC-03/FUNC-04 require resolution or explicit deferred implementation gates.
- **Runtime lock:** all `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AST/CVM normalization, AgentSnapshot runtime, canonical time API, and deterministic ID implementation remain denied until RFC gates are approved.

---

## Alpha3g P0.5.2.1 — Function Descriptor RFC Revision & Blocker Closure

- **Patch:** P0.5.2.1 — Function Descriptor RFC Revision & Blocker Closure.
- **Status:** REVISED — blocker resolutions applied; independent verification pending.
- **Scope:** documentation/process only.
- **Changed docs:** `RFC-FUNCTION-DESCRIPTOR.md`, `RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md`, `CHANGELOG.md`, `ALPHA3F_PLANNING_GATE.md`.
- **FUNC-01:** RESOLVED — captured environment manifest schema is explicit, empty manifest has canonical form, implicit closures remain forbidden, runtime object bindings fail closed.
- **FUNC-02:** RESOLVED — effect policy schema now uses explicit barrier enums, registered effect namespace vocabulary, enforcement pseudocode, and fail-closed nondeterminism barrier semantics.
- **FUNC-03:** DEFERRED — dependency manifest taxonomy and cryptographic pinning are specified; runtime acceptance remains blocked on schema/profile registry enforcement.
- **FUNC-04:** DEFERRED — schema evolution / compatibility behavior is specified; runtime compatibility remains blocked on registry implementation.
- **Next gate:** independent verification of FUNC-01/FUNC-02; approval-candidate vote may proceed only after verification.
- **Runtime lock:** all `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AST/CVM normalization, AgentSnapshot runtime, canonical time API, and deterministic ID implementation remain denied until RFC gates are approved.

---

## Alpha3g P0.5.2.2 — RFC-FUNCTION-DESCRIPTOR: VERIFIED → APPROVAL-CANDIDATE

- **Status:** COMPLETED — independent verification and approval-candidate transition.
- **Scope:** documentation/process only.
- **Verified findings:** `FUNC-01`, `FUNC-02`.
- **RFC status:** `RFC-FUNCTION-DESCRIPTOR.md` is now `APPROVAL-CANDIDATE v0.2-AC`; final team vote is pending.
- **Review notes:** `FUNC-01` and `FUNC-02` moved from `RESOLVED` to `VERIFIED` with team verification metadata.
- **Deferred gates:** `FUNC-03` and `FUNC-04` remain implementation/schema-registry gates.
- **Runtime lock:** all `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AST/CVM normalization, AgentSnapshot runtime, canonical time API, and deterministic ID work remain denied until RFC approval and scoped implementation authorization.

---

## Alpha3g P0.5.2.3 — RFC-FUNCTION-DESCRIPTOR: APPROVED v1.0

- **Patch:** P0.5.2.3 — Function Descriptor Final Team Vote & Approval.
- **Status:** APPROVED v1.0.
- **Scope:** documentation/process only.
- **Vote record:** archived in `docs/RFC-FUNCTION-DESCRIPTOR-REVIEW-NOTES.md` using role-based quorum, blocking-objection, approval-rationale, and cross-RFC alignment fields.
- **RFC baseline:** `docs/RFC-FUNCTION-DESCRIPTOR.md` is now the immutable v1.0 baseline for declarative callable contract identity.
- **Cross-RFC alignment verified:** Stable Canonical Identity (`stable-canonical.v1`), Agent Canonicalization AGENT-02 bridge, and Integrate/Dream NondeterminismBarrier vocabulary.
- **Deferred gates preserved:** `FUNC-03` and `FUNC-04` remain implementation/schema-registry gates and must close before FunctionDescriptor runtime core.
- **Runtime lock:** no changes to `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AST/CVM normalization, AgentSnapshot runtime, canonical time API, or deterministic ID implementation.
- **Dependency edge:** P0.5.2.3 FunctionDescriptor APPROVED v1.0 -> P0.5.3 Agent RFC dependency update / AGENT-02 prerequisite satisfaction -> P0.5.4 Agent RFC verification / approval-candidate -> P0.5.5 Agent RFC approval -> scoped runtime planning only after both RFCs are approved.

---

## Alpha3g P0.5.3 — Agent RFC Dependency Update / AGENT-02 Prerequisite Satisfaction

- **Patch:** P0.5.3 — Agent RFC Dependency Update.
- **Status:** COMPLETED — doc-only dependency synchronization.
- **Scope:** documentation/process only.
- **Changed docs:** `RFC-AGENT-CANONICALIZATION.md`, `RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`, `MIGRATION-READINESS-CHECKLIST.md`, `CHANGELOG.md`, `ALPHA3F_PLANNING_GATE.md`.
- **Prerequisite update:** `RFC-FUNCTION-DESCRIPTOR.md` v1.0 APPROVED now satisfies the AGENT-02 prerequisite at the specification level.
- **AGENT-02:** moved from `SPLIT` to `RESOLVED`; it is not `VERIFIED` in this patch.
- **Agent RFC gate:** ready for P0.5.4 independent verification / approval-candidate transition.
- **Runtime lock:** no changes to `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic ID implementation, or migration code.
- **Dependency edge:** P0.5.3 Agent RFC dependency update / AGENT-02 RESOLVED -> P0.5.4 Agent RFC independent verification / approval-candidate -> P0.5.5 Agent RFC approval -> scoped runtime planning only after both Agent and FunctionDescriptor RFCs are approved.
---

## Alpha3g P0.5.4 — Agent RFC Independent Verification / APPROVAL-CANDIDATE

- **Patch:** P0.5.4 — Agent RFC Independent Verification & Approval-Candidate Transition.
- **Status:** COMPLETED — independent verification and approval-candidate transition.
- **Scope:** documentation/process only.
- **Changed docs:** `RFC-AGENT-CANONICALIZATION.md`, `RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`, `MIGRATION-READINESS-CHECKLIST.md`, `CHANGELOG.md`, `ALPHA3F_PLANNING_GATE.md`.
- **Verified findings:** `AGENT-01`, `AGENT-02`, and `AGENT-03` moved from `RESOLVED` to `VERIFIED` by team review.
- **RFC status:** `RFC-AGENT-CANONICALIZATION.md` is now `APPROVAL-CANDIDATE v0.4-AC`; final team vote is pending.
- **Non-blocker gates:** `AGENT-04` through `AGENT-08` and `AGENT-11` remain implementation / policy / schema-registry gates; `AGENT-09` and `AGENT-10` remain acknowledged review boundaries.
- **Runtime lock:** no changes to `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic ID implementation, or migration code.
- **Dependency edge:** P0.5.4 Agent RFC APPROVAL-CANDIDATE -> P0.5.5 Agent RFC final team vote / APPROVED v1.0 -> scoped runtime planning only after both Agent and FunctionDescriptor RFCs are approved.
---

## Alpha3g P0.5.5 — Agent RFC Final Team Vote / APPROVED v1.0

- **Patch:** P0.5.5 — Agent Canonicalization Final Team Vote & Approval.
- **Status:** APPROVED v1.0.
- **Scope:** documentation/process only.
- **Changed docs:** `RFC-AGENT-CANONICALIZATION.md`, `RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md`, `MIGRATION-READINESS-CHECKLIST.md`, `CHANGELOG.md`, `ALPHA3F_PLANNING_GATE.md`.
- **Vote record:** archived in `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md` using role-based quorum, blocking-objection, approval-rationale, known-limitations, deferred-gate, review-trigger, and cross-RFC alignment fields.
- **RFC baseline:** `docs/RFC-AGENT-CANONICALIZATION.md` is now the immutable v1.0 baseline for canonical agent identity, AgentSnapshot boundaries, memory references, Capability Grants, Runtime Envelope exclusions, and CVM visibility boundaries.
- **Cross-RFC alignment verified:** Stable Canonical Identity (`stable-canonical.v1`), FunctionDescriptor v1.0 agent-definition bridge, Integrate profile safety, CVM visibility boundary, and Capability Grant attenuation.
- **Deferred gates preserved:** `AGENT-04` through `AGENT-08` and `AGENT-11` remain implementation / policy / schema-registry gates; `AGENT-09` and `AGENT-10` remain acknowledged review boundaries.
- **Runtime lock:** no changes to `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, canonical time API, deterministic ID implementation, or migration code.
- **Dependency edge:** P0.5.5 Agent RFC APPROVED v1.0 + P0.5.2.3 FunctionDescriptor APPROVED v1.0 -> P0.5.6 scoped AgentSnapshot runtime planning / drift-audit -> runtime patches only after explicit scope authorization.



## Alpha3g P0.5.6 — AgentSnapshot Runtime Planning & Drift Audit

- **Patch:** P0.5.6 — AgentSnapshot Runtime Planning & Drift Audit.
- **Status:** COMPLETED — READY FOR READ-ONLY DRIFT AUDIT.
- **New planning artifact:** `docs/AGENTSNAPSHOT-RUNTIME-PLAN.md`.
- **New audit artifact:** `docs/AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md`.
- **Baseline:** both `RFC-AGENT-CANONICALIZATION.md` v1.0 and `RFC-FUNCTION-DESCRIPTOR.md` v1.0 are approved; AgentSnapshot runtime planning may proceed, but runtime implementation is not authorized.
- **Field-audit result:** current `AgentRuntime`, `Environment`, actor runtime, `Memory`, `MemoryPalace`, and storage surfaces contain a mix of semantic fields, legacy serialization, and runtime-envelope objects. Future AgentSnapshot code must not reuse legacy `to_dict()` payloads as canonical snapshots.
- **Next gate:** P0.5.7 read-only AgentSnapshot Runtime Drift Report with GO/NO-GO recommendation for standalone schema/value core.
- **Runtime lock:** no changes to `synapse/`, `tests/`, CVM/opcodes, interpreter, actor runtime, golden fixtures, FunctionDescriptor runtime, AgentSnapshot runtime, schema registry, canonical time API, deterministic ID implementation, or migration code.
- **Dependency edge:** P0.5.5 Agent RFC APPROVED v1.0 + P0.5.2.3 FunctionDescriptor APPROVED v1.0 -> P0.5.6 planning/audit -> P0.5.7 read-only drift report -> P0.5.8 standalone AgentSnapshot schema/value core only if GO.


## Alpha3g P0.5.7 — AgentSnapshot Runtime Gate Closure & Drift Report

- **Status:** COMPLETED — GO for P0.5.8 standalone AgentSnapshot schema/value core under local fail-closed schema/profile allowlist.
- **Scope:** documentation + read-only canary test only.
- **New artifact:** `docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md`.
- **Canary:** `tests/test_agentsnapshot_canary_p057.py` confirms legacy `AgentRuntime.to_dict()` is not a canonical AgentSnapshot shape.
- **Gate closure:** `FUNC-03`, `FUNC-04`, and `AGENT-11` are partially closed only for standalone value-core work. They remain blocking for FunctionDescriptor registry, central schema/profile registry, runtime deployment, actor/interpreter integration, and legacy serialization migration.
- **Next:** P0.5.8 standalone AgentSnapshot schema/value core may start.
- **Runtime lock:** `interpreter.py`, `actor_runtime.py`, `builtins.py`, memory backends, CVM/opcodes, Integrate, Dream, golden fixtures, FunctionDescriptor runtime registry, central schema registry, and AgentRuntime serialization remain locked.

---

## Alpha3g P0.5.8 — AgentSnapshot Standalone Schema/Value Core

- **Status:** COMPLETED — standalone AgentSnapshot schema/value core implemented.
- **Scope:** `synapse/agent_snapshot.py`, `tests/test_agentsnapshot_core_p058.py`, and documentation updates only.
- **Runtime authorization:** standalone value objects and validators only. No adapter, no actor/interpreter integration, no deployment, no hard switch.
- **Gate basis:** P0.5.7 GO under local fail-closed schema/profile allowlist.
- **Implemented local allowlist:** `alpha3g.agent_snapshot.v1`, `alpha3g.agent_definition_ref.v1`, `alpha3g.agent_id.v1`, `alpha3g.memory_ref.v1`, `alpha3g.memory_space_id.v1`, `alpha3g.capability_grant.v1`, `alpha3g.function_descriptor.v1`, `stable-canonical.v1`.
- **Next gate:** P0.5.9 AgentSnapshot standalone hardening / edge-case coverage.
- **Still locked:** legacy `AgentRuntime.to_dict()` migration, actor runtime integration, interpreter integration, FunctionDescriptor runtime registry, central schema registry, CVM visibility, Integrate/Dream/golden replay paths.


## Alpha3g P0.5.9 — AgentSnapshot Standalone Hardening / Edge-Case Coverage

- **Status:** COMPLETED — SA1 value core hardened against edge cases.
- **Scope:** `synapse/agent_snapshot.py` (point fixes only, no API expansion), `tests/test_agentsnapshot_hardening_p059.py` (new, 35 tests), documentation updates.
- **Defects closed:**
  - external mutation of mapping fields silently shifting `snapshot_hash()` (deep-freeze fix);
  - duplicate `memory_refs` accepted (now `AgentMemoryRefError`);
  - conflicting `access_mode` on same `(memory_space_id, memory_key)` accepted (now `AgentMemoryRefError`);
  - duplicate `capability_grants` per `tool_namespace` accepted (now `AgentCapabilityGrantError`);
  - whitespace-only `memory_key` accepted (now `AgentMemoryRefError`);
  - `AgentIdSeed.alias` producing distinct `agent_id` for `None` / `""` / whitespace (now normalized to `None`).
- **Validator parity:** `validate_agent_snapshot_payload` enforces the same duplicate / conflict invariants on round-trip payloads.
- **No new public surface:** no new value objects, no new schema versions, no `FunctionDescriptorRef` (still blocked by FUNC-03).
- **Tests:** 775 passed, 1 skipped (740 prior + 35 hardening, zero regression).
- **Lock surface unchanged:** legacy `AgentRuntime.to_dict()`, `builtins.py`, `interpreter.py`, `actor_runtime.py`, memory backends, CVM/opcodes, Integrate, Dream, golden fixtures, FunctionDescriptor runtime registry, central schema registry, FunctionDescriptorRef as standalone value object — all remain locked.
- **Next gate:** P0.5.10 legacy `AgentRuntime.to_dict()` drift analysis (AS2-prep, read-only).


## Alpha3g P0.5.10 — AgentRuntime.to_dict() Drift Analysis (AS2-prep)

- **Status:** COMPLETED — read-only drift analysis of legacy `AgentRuntime.to_dict()` against canonical AgentSnapshot v1 allowlist.
- **Scope:** documentation + read-only test only. New artifact: `docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md`. New test: `tests/test_agentruntime_todict_drift_p0510.py` (26 read-only tests).
- **Observed legacy shape:** invariant at `{name, model, trust_level, trust_scope, memory}` across 9 probed configurations; `memory` is `{short_term, long_term, capacity}`. Live handles (`tools`, `llm`, `env`, `soulprint`, `identity_version`) never enter `to_dict()`.
- **Field classification:**
  - `migrates_as_is`: `trust_level`, `trust_scope`.
  - `requires_transform`: `name`, `model`, `memory.short_term`, `memory.long_term`, `memory.capacity`.
  - `excluded_from_canonical`: `memory_config` constructor argument (currently dead — does not influence `to_dict()`).
- **Identity asymmetry:** canonical AgentSnapshot v1 requires `agent_id`, `definition_ref`, `capability_grants`, `model_ref`, `profile`, `schema_version` — none of which legacy provides. AS2 adapter must source these from runtime state or fail closed.
- **AS2 design risks recorded:** R1 identity asymmetry, R2 model wrapping, R3 memory dereference, R4 capability grant sourcing, R5 identity state sourcing, R6 schema registry dependency, R7 envelope conflict. Documented in §9 of the drift report.
- **Tests:** 801 passed, 1 skipped (P0.5.9 baseline 775 + 26 drift probe, zero regression).
- **Lock surface unchanged:** legacy `AgentRuntime.to_dict()`, `builtins.py`, `interpreter.py`, `actor_runtime.py`, `agent_snapshot.py`, memory backends, CVM/opcodes, Integrate, Dream, golden fixtures, FunctionDescriptor runtime registry, central schema registry, flagged adapter, profile selector — all remain locked.
- **GO/NO-GO:** `GO conditional on team vote` for P0.6.x AS2 flagged adapter RFC (design only). Adapter implementation remains NOT AUTHORIZED until the AS2 RFC closes risks R1..R7 and an explicit team vote is recorded here.
- **Next gate:** P0.6.x AS2 flagged adapter RFC (design only). Implementation requires separate approval after RFC merge.


## Alpha3g P0.5.11 — AgentSnapshot Pre-RFC Gate Closure (AGENT-06 / AGENT-08)

- **Status:** COMPLETED — doc-only pre-RFC gate closure for AS2 flagged adapter RFC readiness.
- **Scope:** documentation only. No `synapse/`, no `tests/`, no runtime code, no AS2 RFC document, no adapter implementation.
- **AGENT-06:** PARTIAL — minimal `model_ref.v1` boundary defined for AS2 RFC design. `provider_namespace` is restricted to `mock | anthropic | openai | local | custom`. Provider drift/deprecation table, endpoint routing, deterministic model execution, recorded inference, and deployment compatibility remain future gates.
- **AGENT-08:** PARTIAL — subagents are explicitly out of AS2 v1 scope. No `subagent_snapshot_ref` is reserved in `AgentSnapshot v1`. Subagent canonicalization remains a future RFC/gate.
- **R5 constraint:** AS2 RFC must choose either identity omission as a documented limitation or identity sourcing from a dedicated runtime/interpreter source. Hybrid partial sourcing is forbidden.
- **R7 constraint:** AS2 canonical envelope must not reuse legacy `{"__type__": "agent", "data": ...}` marker.
- **Next gate:** explicit team vote to open P0.6.0 AS2 flagged adapter RFC (design only). Adapter implementation remains NOT AUTHORIZED.



## Alpha3g P0.6.0 — AS2 Flagged Adapter RFC Draft

- **Status:** DRAFT OPENED — design-only RFC for future AgentRuntime -> AgentSnapshot flagged adapter.
- **New artifacts:**
  - `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md`
  - `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`
- **Scope:** documentation only. No runtime code and no tests.
- **Inputs:** P0.5.10 drift report, P0.5.11 AGENT-06/AGENT-08 partial gate closure, approved Agent/Function/Stable Canonical RFCs.
- **Design positions:**
  - AS2 uses explicit opt-in adapter profile; legacy default remains unchanged.
  - R5 selects dedicated read-only identity source (Strategy B); no inference from legacy `to_dict()`.
  - `model_ref.v1` is the AS2 model boundary; provider deployment drift remains future work.
  - subagents are out of AS2 v1.
  - canonical envelope must not reuse legacy `__type__` marker.
- **Review gates:** `AS2-01..AS2-05` are BLOCKER findings that must be resolved and verified before approval.
- **Runtime lock:** adapter implementation, profile selector, `AgentRuntime.to_dict()` migration, Environment serialization, interpreter/actor/CVM integration, golden fixtures, and registry-backed deployment remain NOT AUTHORIZED.
- **Next:** structured review and blocker resolution for `RFC-AGENT-SNAPSHOT-ADAPTER.md`.

## Alpha3g P0.6.1 — AS2 RFC Hardening & Blocker Closure

- **Status:** COMPLETED — doc-only AS2 RFC revision and blocker closure.
- **Scope:** documentation only. No `synapse/`, no `tests/`, no runtime code, no adapter implementation, no profile selector, no legacy serialization changes.
- **AS2-01:** RESOLVED — canonical identity requires explicit complete-or-absent `AdapterIdentityContext`; legacy `name` is alias only.
- **AS2-02:** RESOLVED — legacy model mapping requires immutable append-only `StaticModelRegistry`; unknown models and wildcard `custom` fallback fail closed.
- **AS2-03:** RESOLVED — two-phase memory externalization, strict per-ref memory-space validation, `memory_space_policy_version`, no rewrite/filter/repair, `AdapterMemorySpaceMismatchError`.
- **AS2-04:** RESOLVED — explicit `CapabilityGrantSource` only; live tool / `tools.keys()` / callable inspection forbidden.
- **AS2-05:** RESOLVED — AS2 canonical envelope cannot reuse legacy `__type__`; `audit_context` is provenance metadata and does not affect `AgentSnapshot` state hash.
- **Remaining gates:** AS2-06 schema/profile registry boundary, AS2-07 memory capacity mapping, AS2-10 Environment dual-emission boundary remain open. AS2-08 subagents remain out of scope; AS2-09 error taxonomy resolved at design level.
- **Next gate:** P0.6.2 independent verification. Adapter implementation remains NOT AUTHORIZED.

## Alpha3g P0.6.2 — AS2 Independent Verification Matrix

- **Status:** COMPLETED — AS2 RFC independent verification.
- **Type:** doc-only verification; no runtime or test changes.
- **Artifact:** `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.
- **Result:** AS2-01..AS2-05 independently VERIFIED.
- **Document authority:** P0.6.1 `RFC-AGENT-SNAPSHOT-ADAPTER.md` is normative for AS2 v1; older drift/planning wording is informational if superseded.
- **Next step:** P0.6.3 final approval. Runtime implementation and adapter tests remain locked.

## Alpha3g P0.6.3 — AS2 RFC Final Approval / APPROVED v1.0

- **Status:** COMPLETED — AS2 RFC final approval.
- **Artifact:** `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md` is now `APPROVED v1.0`.
- **Vote record:** structured role-based vote archived in `docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md`.
- **Scope of approval:** design contract only — pure deterministic projection, explicit adapter inputs, two-phase memory externalization, typed fail-closed errors, canonical envelope isolation, and AdapterDerivationRecord concept.
- **Known limitations:** no implementation exists yet; WATCH-01 / WATCH-02 accepted as non-blocking; authority inflation checks, cross-agent memory sharing, and AdapterDerivationRecord serialization remain future gates.
- **Runtime lock:** AS2 adapter implementation, profile selector, legacy `AgentRuntime.to_dict()` migration, `Environment._json_safe()` migration, FunctionDescriptor runtime registry, central schema registry, subagent canonicalization, and golden fixture migration remain NOT AUTHORIZED.
- **Dependency edge:** P0.6.3 AS2 RFC APPROVED v1.0 -> P0.6.4 implementation planning / drift harness design -> explicit P0.6.5 vote required before any adapter code.

## Alpha3g P0.6.4 — AS2 Implementation Planning / Fixture Harness Design

- **Status:** COMPLETED — docs + test-only fixture/invariant harness.
- **New planning docs:** `AS2-IMPLEMENTATION-PLAN.md`, `AS2-DRIFT-HARNESS-DESIGN.md`, `AS2-FIXTURE-CORPUS-SPEC.md`.
- **New fixture corpus:** `tests/fixtures/as2/` with 11 canonical JSON fixtures: one positive minimal input set and ten negative seeded-fault cases.
- **New passive harness:** `tests/test_as2_fixture_matrix_p064.py`. It validates fixture structure and invariant intent only. It does not import an adapter and does not call `to_agent_snapshot()`.
- **Runtime lock:** `synapse/agent_snapshot_adapter.py`, `AgentRuntime.to_dict()` migration, `Environment._json_safe()` migration, runtime profile selector, FunctionDescriptor runtime registry, central schema registry, Integrate/Dream paths, and golden fixture migration remain NOT AUTHORIZED.
- **P0.6.5 precondition:** explicit team vote after review of the P0.6.4 fixture corpus, harness, and pre-flight gate checklist.

```text
P0.6.3 AS2 RFC APPROVED v1.0
  -> P0.6.4 implementation planning / fixture harness design
  -> explicit team vote
  -> P0.6.5 flagged adapter skeleton only
```

## Alpha3g P0.6.5 — AS2 Flagged Adapter Skeleton

- **Status:** COMPLETED — first isolated AS2 skeleton code boundary.
- **New module:** `synapse/agent_snapshot_adapter.py`.
- **New tests:** `tests/test_as2_adapter_skeleton_p065.py`.
- **Authorized scope used:** typed adapter error hierarchy, explicit input value skeletons, and validation-only functions.
- **Runtime isolation:** no imports from `AgentRuntime`, `Environment`, `interpreter.py`, actor runtime, CVM, Integrate, Dream, provider registries, storage, wall-clock, UUID, or ambient authority modules.
- **Projection lock:** `to_agent_snapshot()` is still absent. No `AgentSnapshot` construction, no snapshot hash computation, no legacy runtime migration, no profile selector, and no integration path were added.
- **Test result:** full suite passes with the new skeleton tests.
- **Next gate:** P0.6.6 may be proposed only after team review of P0.6.5. Any fixture-driven minimal projection requires a separate explicit vote and must remain standalone.

```text
P0.6.4 fixture/invariant harness
  -> P0.6.5 isolated validation skeleton
  -> structured review
  -> explicit vote before P0.6.6 fixture-driven projection
```


## Alpha3g P0.6.5.1 — AS2 skeleton test skip cleanup

- **Status:** COMPLETED — narrow test-quality cleanup.
- Removed the artificial skip from the P0.6.5 AS2 skeleton test by filtering the parametrized negative-fixture test before execution.
- Scope remained test-only: `synapse/` is unchanged and the AS2 skeleton semantics are unchanged.
- P0.6.5.1 does not authorize projection logic, `to_agent_snapshot()`, legacy integration, or runtime profile wiring.

```text
P0.6.5 isolated validation skeleton
  -> P0.6.5.1 artificial skip cleanup
  -> structured review
  -> explicit vote before P0.6.6 fixture-driven projection
```


## Alpha3g P0.6.6 — AS2 Validation Hardening / Fixture-Driven Boundary Enforcement

- **Status:** COMPLETED — validation hardening only (Vote A consolidated across four reviewers).
- **Scope:** point fixes in `synapse/agent_snapshot_adapter.py`, new test file `tests/test_as2_adapter_validation_p066.py` (83 tests), RFC `§17` name reservation, RFC `§18` R8 capability_grant gap record, drift report R8 entry.
- **Seven edge-case gaps closed:** whitespace-only alias, negative/bool identity_version, duplicate legacy_model, duplicate memory_refs, conflicting access_mode, duplicate tool_namespace, any-`__type__` envelope conflict (not only `'agent'`).
- **R8 recorded:** AS2 `CapabilityGrant` shape is richer than standalone-core `CapabilityGrant`. Resolution options R8-A (deterministic canonical projection, default), R8-B (core schema bump), R8-C (AgentSnapshot v2). Decision deferred to P0.6.7 design. **Not** a P0.6.6 blocker.
- **AS2ViolationContext deferred:** the external reviewer's proposal to enrich exceptions with `rfc_reference` / `violated_field` / `expected` / `actual` is deferred to P0.6.7 to avoid surface expansion in this validation-only patch.
- **Naming discipline locked:** `to_agent_snapshot`, `build_snapshot_from_as2_inputs`, `build_snapshot_from_validated_inputs`, and similar names are now FORBIDDEN in the AS2 module. Future projection function name-reserved as `project_validated_as2_inputs`.
- **Tests:** 920 passed, 1 skipped (P0.6.5.1 baseline 837 + 83 hardening tests, zero regression). Skip baseline preserved at 1.
- **Lock surface unchanged:** `synapse/builtins.py`, `synapse/interpreter.py`, `synapse/actor_runtime.py`, `synapse/agent_snapshot.py`, memory backends, CVM/opcodes, Integrate, Dream, golden fixtures, legacy serialization, profile selector, central registry, FunctionDescriptor runtime registry, projection function, feature flag — all remain locked.
- **Next gate:** P0.6.7 fixture-driven minimal standalone projection. Requires separate team vote.

```text
P0.6.5.1 artificial skip cleanup
  -> P0.6.6 validation hardening / fixture-driven enforcement
  -> structured review
  -> explicit vote before P0.6.7 fixture-driven projection
```


## Alpha3g P0.6.7 — AS2 fixture-driven minimal standalone projection

- **Status:** COMPLETED.
- **Projection:** `project_validated_as2_inputs(...)` implemented for positive fixture-driven standalone projection.
- **R8:** resolved for v1 via deterministic canonical projection to core `CapabilityGrant.scope_hash`; no core schema bump.
- **R9:** closed by explicit `AdapterDefinitionSource`; `AdapterIdentityContext` remains identity-only.
- **Tests:** full suite passes; projection tests verify `AgentSnapshot` instance, deterministic `snapshot_hash()`, selected fields, and validation-before-projection for negative fixtures.
- **Still locked:** `to_agent_snapshot()`, legacy bridge, runtime wiring, feature flag, real provider registry, FunctionDescriptor runtime registry, AdapterDerivationRecord real hash computation, AS2ViolationContext, golden fixture migration.
- **Next gate:** P0.6.8 Merkle-transparent derivation audit / real input hashes, pending team review.


---

## Alpha3g P0.6.8 — AS2 AdapterDerivationRecord Hashing / Merkle-Transparent Audit

- **Status:** COMPLETED.
- **Scope:** standalone AS2 projection audit trail only.
- **Implemented:** real stable-canonical input hashes for `AdapterDerivationRecord` across identity context, static model registry, adapter definition source, memory ref source, and capability grant source.
- **State boundary:** `AgentSnapshot.snapshot_hash()` remains independent of derivation-record hashes.
- **Runtime boundary:** no legacy bridge, no runtime wiring, no feature flag, no AgentRuntime/Environment/interpreter/actor imports.
- **Next gate:** team review for the next authorized patch. Legacy bridge remains locked until separate vote.

---

## Alpha3g P0.6.9 — AS2ViolationContext / Forensic Error Attribution

Status: COMPLETED.

P0.6.9 enriches AS2 failure paths with structured forensic context while keeping
successful projection, derivation hashing, AgentSnapshot state hashing, and all
legacy/runtime boundaries unchanged.

Evidence:

```text
synapse/agent_snapshot_adapter.py
tests/test_as2_violation_context_p069.py
tests/fixtures/as2/* negative expected_error_context blocks
docs/RFC-AGENT-SNAPSHOT-ADAPTER.md §21
```

Still locked:

```text
legacy AgentRuntime bridge
Environment._json_safe() migration
AgentRuntime.to_dict() canonical usage
runtime profile selector
feature flag wiring
R8-B / R8-C schema migration
real provider registry
FunctionDescriptor runtime registry
Integrate / Dream / CVM paths
```

Next action: team review to select the next authorized patch. Bridge work remains
locked until a separate design decision and vote.


## Alpha3g P0.6.10 — AS2 Legacy Bridge Design RFC / Host Pre-Stage Protocol

Status: COMPLETED.

P0.6.10 is a doc-only bridge design patch. It does not authorize bridge code or bridge fixtures.

Completed:

```text
docs/AS2-LEGACY-BRIDGE-DESIGN.md
  - AS2 Airlock Pattern
  - Host Pre-Stage Protocol
  - Step 0 Host Capability Verification
  - Forbidden Reads Registry
  - bridge readiness checklist
  - future feature flag reservation
```

Design decisions:

```text
Future entrypoint: prepare_as2_inputs_from_host_prestage(...)
Future flag name: AS2_HOST_PRESTAGE_BRIDGE_ENABLED
Memory externalization: Host responsibility
Capability grant source: declarative only
Bridge code: LOCKED
Bridge fixture corpus: next authorized stage, not included here
```

Still locked:

```text
AgentRuntime imports
Environment imports
AgentRuntime.to_dict() canonical usage
Environment._json_safe() migration
runtime profile selector
feature flag wiring
bridge implementation
Integrate / Dream / CVM integration
```

Next proposed stage: P0.6.11 Bridge Fixture Corpus / Host Pre-Stage Harness.


## Alpha3g P0.6.11 — AS2 Bridge Fixture Corpus / Host Pre-Stage Harness

Status: COMPLETED.

P0.6.11 converts the P0.6.10 bridge design into a test-only/data fixture corpus.

Completed:

```text
tests/fixtures/as2_bridge/
  - 16 bridge fixtures total
  - 4 positive Host Pre-Stage fixtures
  - 12 negative Host/legacy-boundary fixtures

tests/test_as2_bridge_harness_p0611.py
  - schema validation
  - standalone validate_as2_inputs(...) compatibility for positives
  - Forbidden Reads Registry coverage
  - Host Pre-Stage Protocol coverage
```

Design decisions preserved:

```text
Bridge code: LOCKED
AgentRuntime imports: LOCKED
Environment imports: LOCKED
Feature flag implementation: LOCKED
project_validated_as2_inputs(...) calls in bridge harness: FORBIDDEN
Bridge errors: string identifiers only
```

Naming debt accepted for current API compatibility:

```text
legacy_agent_runtime_to_dict.model is used only as a synthetic model selector
inside positive expected_as2_inputs. It does not authorize AgentRuntime.to_dict()
as canonical input. Future rename requires separate authorization.
```

Next proposed stage: P0.6.12 flagged bridge implementation, only after explicit team vote.

## P0.6.12 gate entry — Flagged Host Pre-Stage Bridge Skeleton

P0.6.12 is completed as the first bridge-code skeleton.

Scope observed:

```text
new: synapse/agent_snapshot_bridge.py
new: tests/test_as2_bridge_implementation_p0612.py
updated: bridge/process docs
```

Boundary preserved:

```text
no AgentRuntime imports
no Environment imports
no runtime wiring
no AgentSnapshot construction inside bridge
no project_validated_as2_inputs(...) call inside bridge
local flag disabled by default
```

P0.6.12 does not authorize runtime activation. Any runtime wiring or legacy bridge activation requires a separate gate and team vote.


## P0.6.13 gate entry — Host Pre-Stage Bridge Hardening

P0.6.13 is completed as a bridge-boundary hardening patch.

Authorized scope completed:

- `synapse/agent_snapshot_bridge.py` hardened against unknown Host Pre-Stage payload fields.
- Nested AS2 boundary structures now reject unexpected bridge-boundary fields where their field contracts are known.
- Missing/null/empty/wrong-shape semantics are explicitly tested.
- `PreparedAS2Inputs` is isolated from external payload mutation.
- New deterministic adversarial hardening tests added under `tests/test_as2_bridge_hardening_p0613.py`.

Maintained locks:

```text
project_validated_as2_inputs(...) inside bridge: LOCKED
AgentSnapshot construction inside bridge: LOCKED
AgentRuntime / Environment imports: LOCKED
runtime wiring: LOCKED
runtime feature flag system: LOCKED
production environment guard: NOT INTRODUCED
legacy_agent_runtime_to_dict rename: DEFERRED
bridge fixture schema v2: NOT INTRODUCED
```

P0.6.13 does not authorize runtime activation or projection handoff. Any next bridge expansion requires a separate team vote.

## P0.6.14 gate entry — Runtime Wiring Design RFC

P0.6.14 is completed as a doc-only runtime wiring design patch.

Added:

```text
docs/AS2-RUNTIME-WIRING-DESIGN.md
```

Design decisions recorded:

```text
Host/Pipeline owns orchestration.
Bridge owns preparation and validation only.
Projection is called by Host/Pipeline after bridge success.
Bridge does not call project_validated_as2_inputs(...).
Bridge does not construct AgentSnapshot.
```

Runtime integration guardrails recorded:

```text
Host Pre-Stage Responsibility Map
Payload Key Classification Matrix
Forbidden Reads Registry Cross-Check
Failure Handling Strategy
Feature Flag Placement
Strict Structural Validator Contract
Debt Register
```

Maintained locks:

```text
synapse/ code changes: LOCKED
tests/ changes: LOCKED
runtime wiring: LOCKED
AgentRuntime / Environment imports: LOCKED
runtime feature flag system: LOCKED
CAS/storage I/O: LOCKED
Integrate / Dream / CVM wiring: LOCKED
legacy_agent_runtime_to_dict rename: DEFERRED
model_selector removal: DEFERRED
```

Next proposed gate: P0.6.15 Runtime Wiring Harness / Host Provider Mocks, only after explicit team authorization.
