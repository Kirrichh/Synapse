# Synapse Current Implementation Status

- **Document status:** Active audited status register
- **Document authority:** Current implementation status, code evidence,
  verification evidence, active guarantees,
  boundaries, explicitly absent components, replay eligibility, and named
  design targets
- **Audit base commit:** `c941e41ac4ebd2c59a6c7b7db3b6acea1f1e2f28`
- **Audit date:** 2026-07-14
- **Audited branch/ref:** `origin/main` at the audit base commit
- **Status vocabulary version:** `synapse.status-register/v1`
- **Scope:** Repository implementation and committed verification evidence at
  the audit base; no live-provider or production-infrastructure inference
- **Relationship to other core documents:** Architecture owns data flow and module ownership; Roadmap
  owns future sequencing and gates; Changelog owns chronology; specifications,
  RFCs, and subsystem documents own their narrow contracts
- **Docsync rule:** Any change that alters a listed contour's status,
  guarantee, boundary, absent component, replay eligibility, owner, or
  governing evidence must update this register in the same PR or record an
  explicit `DOCSYNC_REQUIRED` result

## 1. Purpose and Authority

This register answers six current-state questions for each audited contour:

1. Is there an implementation path?
2. Which module owns it and how is it reached?
3. What committed evidence supports the status?
4. What does that evidence guarantee?
5. What remains outside the guarantee?
6. Is the contour eligible for canonical replay, conditionally replayable, or
   non-canonical?

This document is authoritative for those answers. It is not the architecture,
roadmap, changelog, language specification, determinism contract, or a
replacement for subsystem contracts. It does not promote verification-only
evidence to production sign-off.

## 2. Audit Metadata

| Field | Value |
| --- | --- |
| Repository | `Kirrichh/Synapse` |
| Audit base | `c941e41ac4ebd2c59a6c7b7db3b6acea1f1e2f28` |
| Audit date | 2026-07-14 |
| Ref inspected | `origin/main` |
| Package language version | `2.2.0-alpha3e` |
| Package runtime version | `0.22.0-alpha3e` |
| Package specification version | `2.2.0-alpha3e` |
| Audit inputs | Production modules, tests, examples, reports, specifications, RFCs, and CLI help committed at the audit base |
| Excluded inference | Uncommitted files, launcher-worktree state, unrecorded external services, release tags not represented by package metadata, and future design intent |

`Last verified commit: Audit base` in the implementation matrix means the full
audit-base SHA above. It is not a promise that every contour changed in that
commit; it records the repository state against which the row was checked.

## 3. Status Vocabulary

| Status | Meaning |
| --- | --- |
| `IMPLEMENTED` | At the pinned code base, an executable path exists for the stated local responsibility and is supported by corresponding contracts and verification evidence. Use this only for that exact local responsibility. |
| `IMPLEMENTED_WITH_BOUNDARIES` | A real implementation path and evidence exist, but durable scope, replay class, provider authority, integration, production readiness, or another named boundary limits the claim. |
| `EXPERIMENTAL` | An executable prototype or verification path exists, but it is not canonical production authority. |
| `DESIGN_TARGET` | The repository names the future architecture or contract, but no integrated runtime authority exists. |
| `NOT_IMPLEMENTED` | The named component or authority is absent. Reserved enums, validators, boundary records, and documentation do not change this status. |
| `HISTORICAL` | The description belongs to a past release, patch, or planning checkpoint and is not current-state authority. |
| `BLOCKED` | Work or promotion is prohibited until a named owner, architecture, evidence, or approval gate is satisfied. |

No status in this register means semantic correctness beyond the governing
oracle, production readiness, economic benefit, or unrestricted replay unless
the row says so explicitly.

## 4. Evidence Rules

Evidence is ranked by what it can establish:

1. **Production implementation plus focused tests** can establish a bounded
   code path and its tested contract.
2. **Golden artifacts and mock replay tests** can establish replay behavior for
   recorded fixtures and the governing determinism contract.
3. **External verification harnesses** can establish the exercised provider or
   infrastructure semantics only under the recorded environment and inputs.
4. **Static audits, schemas, validators, and contract tests** can establish
   shape and fail-closed boundaries, not runtime integration.
5. **Documentation and RFCs** can establish approved intent or a design target,
   not implementation.

Negative evidence is first-class. A reserved status, missing gateway, rejected
scope expansion, unsupported durable node, provider skip, or fail-closed path
is recorded as a boundary rather than omitted.

### Evidence states

Every row in the implementation matrix carries one of these explicit states:

- `EXECUTED_PASS`: the exact command, test, fixture, or verification path was
  executed during this corrective audit and passed;
- `INSPECTED`: the exact path exists and was inspected, but was not executed
  during this corrective audit;
- `HISTORICAL_PASS`: a committed dated report records a pass; this is not a
  fresh execution claim;
- `NOT_RUN`: executable evidence was not run and no committed pass report is
  claimed.

The implementation matrix uses `INSPECTED` conservatively. The corrective
audit executed only the bounded commands listed in the PR validation record;
existence of a focused test file is never represented as a fresh pass.

### Exact evidence groups

Matrix rows reference these groups by ID. Every path below was resolved at the
audit base. A group is an exact path index, not a claim that every listed test
was executed in this corrective audit.

- **`EV-LANGUAGE`**: `tests/test_lexer.py`, `tests/test_parser.py`,
  `tests/test_interpreter.py`, `examples/hello_agent.syn`,
  `examples/consequence_aware.syn`.
- **`EV-EXECUTION`**: `tests/test_cvm_foundation.py`,
  `tests/test_cvm_conformance.py`, `tests/test_cvm_guard_blocks_alpha3e.py`,
  `tests/test_cvm_llm_bridge_alpha3e.py`, `tests/test_durable_execution.py`.
- **`EV-REPLAY-DEBUG`**: `tests/test_golden_replay.py`,
  `tests/test_golden_replay_alpha3e.py`, `tests/test_replay_governance.py`,
  `tests/test_debugger_core_alpha3f.py`,
  `tests/test_debugger_injection_alpha3f.py`,
  `tests/golden_replays_alpha3e/strict/print_math/manifest.json`.
- **`EV-ACTOR`**: `tests/test_durable_actor.py`,
  `tests/test_durable_mailbox_wait.py`, `tests/test_spawn_suspend_promises.py`,
  `tests/test_swarm_mobility.py`, `tests/test_swarm_promises.py`.
- **`EV-GOVERNANCE`**: `tests/test_semantic_guardrails.py`,
  `tests/test_intent_trust_observe.py`,
  `tests/test_compiler_guard_lowering_alpha3e_b1.py`,
  `tests/test_replay_governance.py`, `tests/test_interpreter.py`.
- **`EV-MEMORY`**: `tests/test_memory_palace_intention.py`,
  `tests/test_affective_memory.py`, `tests/test_intent_trust_observe.py`.
- **`EV-COGNITIVE`**: `tests/test_dream_sandbox_p01_alpha3g.py`,
  `tests/test_dream_replay_alpha3g.py`,
  `tests/test_integrate_i2_hardening_p032.py`,
  `tests/test_v1_4_1_replay_safe_integrate.py`, `tests/test_inner_life.py`,
  `tests/test_fracture_self.py`, `tests/test_fracture_polish.py`,
  `tests/test_resonance_inter_subjectivity.py`,
  `tests/test_cognitive_primitives.py`.
- **`EV-AFFECTIVE`**: `tests/test_affective_memory.py`,
  `tests/test_affective_vm.py`, `tests/test_reactive_affective.py`.
- **`EV-HABITS`**: `tests/test_living_habits_phase_a.py`,
  `tests/test_living_habits_phase_b.py`,
  `tests/test_living_habits_phase_c.py`.
- **`EV-OPERATIONS`**: `tests/test_production_hardening.py`,
  `tests/test_vm_fallback_audit_metrics.py`.
- **`EV-CONTROLLED-CHANGE`**: `tests/test_controlled_change_hardening.py`,
  `tests/test_controlled_change_outcomes.py`,
  `tests/test_ref_cas_and_linked_worktree_safety.py`.
- **`EV-SWEBENCH`**: `tests/test_swebench_stage3a_baseline.py`,
  `tests/test_swebench_stage3a_carry.py`,
  `tests/test_swebench_gold_runner.py`,
  `tests/test_swebench_gold_evidence.py`,
  `tests/test_swebench_gold_oracle_binding.py`,
  `tests/test_swebench_paired_measurement_contract.py`,
  `tests/test_swebench_measurement_output_boundary.py`.
- **`EV-GOLD-DESIGN`**:
  `synapse/experiments/swebench/measurement_output.py`,
  `synapse/experiments/swebench/paired_measurement.py`,
  `synapse/experiments/swebench/gold_runner.py`, `docs/ROADMAP.md`.
- **`EV-AS2`**: `tests/test_as2_architectural_fitness.py`,
  `tests/test_as2_postgresql_external_provider_p0645.py`,
  `docs/AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md`,
  `.github/workflows/as2-postgres-open-provider-verification.yml`.

## 5. Cross-Document Authority Map

| Question | Authoritative document |
| --- | --- |
| What is implemented now, with what evidence and limits? | This status register |
| How does source and data flow through modules? | [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) |
| What should happen next and behind which gate? | [ROADMAP.md](ROADMAP.md) |
| When did a release or patch announcement occur? | [CHANGELOG.md](CHANGELOG.md) |
| What syntax and semantics does the language define? | [SPEC.md](SPEC.md) and governing RFCs |
| Which events are replay-eligible? | [DETERMINISM_CONTRACT.md](DETERMINISM_CONTRACT.md) |
| What does a golden artifact guarantee? | [GOLDEN_REPLAY.md](GOLDEN_REPLAY.md) |
| How does controlled change behave? | [`synapse/change/`](../synapse/change/) contracts and focused tests |
| What does an AS2 verification report prove? | The named AS2 report and its explicit verification-only boundary |

## 6. Current Implementation Matrix

The table deliberately repeats boundaries. A row is not a claim about adjacent
rows.

| ID | Name | Status | Responsibility | Owner(s) | Execution path | Exact evidence group | Evidence state | Guarantee | Boundary | Explicitly absent | Replay | Governing docs | Last verified commit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LANG-01 | Synapse language surface | `IMPLEMENTED_WITH_BOUNDARIES` | Defines `.syn` syntax and domain constructs | `synapse/lexer.py`, `parser.py`, `ast.py` | source -> tokens -> AST | [EV-LANGUAGE](#exact-evidence-groups) | `INSPECTED` | Audited syntax produces typed AST nodes | AST presence does not prove execution on every engine | Universal durable/CVM coverage | Parse is deterministic for fixed source | `LANGUAGE_SPEC.md` | Audit base |
| LANG-02 | Lexer | `IMPLEMENTED` | Tokenization and source positions | `synapse/lexer.py` | source -> `Lexer.tokenize()` | [EV-LANGUAGE](#exact-evidence-groups) | `INSPECTED` | Fixed source yields token stream or lexical error | Language-version compatibility still applies | Incremental/streaming lexer authority | Category A | `LANGUAGE_SPEC.md` | Audit base |
| LANG-03 | Parser | `IMPLEMENTED_WITH_BOUNDARIES` | Grammar and soft-keyword parsing | `synapse/parser.py` | tokens -> `Parser.parse()` | [EV-LANGUAGE](#exact-evidence-groups) | `INSPECTED` | Supported grammar yields AST or fail-closed parse error | Experimental syntax may remain RFC-gated | Universal backward grammar compatibility | Category A | `LANGUAGE_SPEC.md` | Audit base |
| LANG-04 | AST | `IMPLEMENTED_WITH_BOUNDARIES` | Typed program representation | `synapse/ast.py` | parser -> AST dataclasses | [EV-LANGUAGE](#exact-evidence-groups) | `INSPECTED` | Nodes represent audited language forms | Nodes do not guarantee lowering or runtime authority | Automatic durable/replay eligibility | Structural Category A | `LANGUAGE_SPEC.md` | Audit base |
| EXEC-01 | Tree-walking interpreter | `IMPLEMENTED_WITH_BOUNDARIES` | Broad orchestration semantics | `synapse/interpreter.py` | AST -> evaluator branches -> runtime state/history | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Executes the broad audited language surface | Some paths are non-durable or conditionally replayable | Uniform strict replay | `A/B/C` by construct | `ARCHITECTURE_OVERVIEW.md`, determinism contract | Audit base |
| EXEC-02 | Cognitive VM | `IMPLEMENTED_WITH_BOUNDARIES` | Deterministic bytecode execution, gas, snapshots, guards | `synapse/cvm.py` | bytecode -> CVM step loop | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Executes supported opcodes with bounded state transitions | Not every AST node lowers to CVM | Full cognitive-runtime ownership | Category A for pure opcodes; host calls B | CVM RFCs, determinism contract | Audit base |
| EXEC-03 | Bytecode/compiler | `IMPLEMENTED_WITH_BOUNDARIES` | Lower supported syntax to serializable instructions | `synapse/bytecode.py` | AST -> compiler -> bytecode program | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Supported nodes compile to defined opcodes | Runtime-only nodes remain routed/fallback | Universal compiler coverage | Follows opcode class | CVM RFCs | Audit base |
| EXEC-04 | VMBridge | `IMPLEMENTED_WITH_BOUNDARIES` | Capability-aware host dispatch and result return | `synapse/runtime/vm_bridge.py` | CVM host request -> bridge -> host result | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Supported host requests cross an explicit bridge | External provider/environment remains outside VM proof | Universal provider isolation | B when result recorded | bridge RFCs, determinism contract | Audit base |
| EXEC-05 | Host ABI | `IMPLEMENTED_WITH_BOUNDARIES` | Stable host-call envelope and capability contract | `synapse/runtime/host_abi.py` | VMBridge <-> host adapter | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Known ABI forms are validated fail-closed | No arbitrary host compatibility promise | Dynamic opcode/plugin authority | B for recorded effects | Host ABI docs/RFCs | Audit base |
| EXEC-06 | Durable application subset | `IMPLEMENTED_WITH_BOUNDARIES` | Run/resume and durable state boundary | `synapse/application.py` | CLI/application -> durable engine -> state/history | [EV-EXECUTION](#exact-evidence-groups) | `INSPECTED` | Supported subset resumes from JSON-safe state/history | Unsupported AST inventory is explicit | Serialized Python frames; universal continuation cursor | Conditional B | architecture, determinism contract | Audit base |
| REC-01 | Execution history | `IMPLEMENTED_WITH_BOUNDARIES` | Ordered runtime event record | interpreter, CVM, actor/runtime modules | execution -> append event | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Eligible meaningful transitions are recorded | Not every internal action belongs in canonical history | Complete external-world capture | A/B by event | determinism contract | Audit base |
| REC-02 | Event hashing | `IMPLEMENTED` | Canonical tamper-evident chain | `synapse/hardening.py` | canonical event + prior hash -> next hash | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Mutation/reordering changes verified chain result | Hash integrity is not semantic correctness | External timestamp/notary authority | Category A | determinism contract | Audit base |
| REC-03 | Golden artifacts | `IMPLEMENTED_WITH_BOUNDARIES` | Bundle source/history/snapshots/mock resources | `synapse/golden_replay.py` | recorded run -> artifact directory | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Contracted artifacts can be loaded and checked | Artifact includes only declared resources | Universal environment snapshot | A/B fixture-bound | `GOLDEN_REPLAY.md` | Audit base |
| REC-04 | Mock replay | `IMPLEMENTED_WITH_BOUNDARIES` | Re-execute/consume recorded resources without live providers | `synapse/golden_replay.py`, interpreter/CVM | artifact -> `replay --mock` | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Eligible fixtures replay without provider calls | Strict eligibility is construct-specific | Live-provider equivalence | A/B conditional | golden replay and determinism docs | Audit base |
| REC-05 | Time-travel debugger | `IMPLEMENTED_WITH_BOUNDARIES` | Artifact diagnostics and first divergence | `synapse/debugger_core.py`, `cli.py` | artifact -> trace adapter -> compare | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Reports equality or first structured divergence | Diagnostic comparison is not canonical mutation | Cross-process daemon/session authority | Read-only canonical diagnostics | debugger guide | Audit base |
| REC-06 | Exploratory forks | `IMPLEMENTED_WITH_BOUNDARIES` | Copy-on-write speculative branches and injection checks | `synapse/debugger_core.py` | canonical trace/state -> fork overlay | [EV-REPLAY-DEBUG](#exact-evidence-groups) | `INSPECTED` | Fork writes do not mutate parent state | Fork result is non-canonical | Automatic promotion to evidence | Exploratory/non-canonical | debugger guide | Audit base |
| ACT-01 | Agents and sub-agents | `IMPLEMENTED_WITH_BOUNDARIES` | Agent definitions, instances, methods, identity metadata | AST/interpreter/actor runtime | parse -> register/instantiate -> invoke | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Supported agent behavior executes through runtime | Definition wrappers do not put registry internals in CVM | Universal distributed lifecycle | Conditional A/B | actor RFCs | Audit base |
| ACT-02 | Actor messaging | `IMPLEMENTED_WITH_BOUNDARIES` | Governed local/route-aware message delivery | interpreter, actor runtime | `send`/async send -> policy -> mailbox/forward packet | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Supported sends preserve policy and audit ordering | Network delivery has prototype boundary | Production distributed delivery guarantee | Conditional B | actor/mobility docs | Audit base |
| ACT-03 | Mailbox and receive | `IMPLEMENTED_WITH_BOUNDARIES` | FIFO mailbox, patterns, timeout/suspension | actor runtime/interpreter | mailbox -> receive matcher -> continuation/suspension | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Tested local ordering and receive semantics | Durable/network combinations are subset-bound | Global distributed ordering | Conditional B | actor docs | Audit base |
| ACT-04 | Spawn | `IMPLEMENTED_WITH_BOUNDARIES` | Create serializable actor references | interpreter/actor runtime | `spawn` -> actor ref + mailbox state | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Supported spawn state is JSON-safe | Remote production lifecycle is not guaranteed | Production scheduler authority | Conditional B | actor docs | Audit base |
| ACT-05 | Suspension | `IMPLEMENTED_WITH_BOUNDARIES` | Represent external wait/migration/promise pause | interpreter/application | evaluator -> `Suspension` -> caller persistence/resume | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Supported suspension reasons are explicit | No Python-frame serialization | Universal instruction cursor | Conditional B | architecture | Audit base |
| ACT-06 | Durable promises | `IMPLEMENTED_WITH_BOUNDARIES` | Track pending/resolved external results | interpreter/actor runtime/application | suspend -> promise record -> resolve -> resume | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Tested promise state survives supported snapshots | External resolver durability is deployment-owned | Exactly-once global provider guarantee | Conditional B | promise RFCs | Audit base |
| ACT-07 | Await | `IMPLEMENTED_WITH_BOUNDARIES` | Suspend until promise/actor result | parser/interpreter | `await` -> promise lookup -> value or suspension | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Supported awaited values resume correctly | Durable subset and owner routing apply | General async Python interop | Conditional B | promise RFCs | Audit base |
| ACT-08 | Mobility envelopes | `EXPERIMENTAL` | Serialize source/history/mailbox/promise/routing metadata | interpreter/mobility helpers | migration request -> envelope -> remote restore prototype | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Envelope is JSON-safe and excludes host frames | Authentication, durable transport, retries, backpressure absent | Production mobility | Conditional B/prototype | mobility history/RFCs | Audit base |
| ACT-09 | Network node prototype | `EXPERIMENTAL` | Accept migration and forwarded-message packets | `synapsed.py` and mobility runtime | asyncio packet -> prototype handler | [EV-ACTOR](#exact-evidence-groups) | `INSPECTED` | Prototype packet handling exists | Not hardened production networking | Production security/SLO authority | Non-canonical external boundary | mobility docs | Audit base |
| GOV-01 | Policies | `IMPLEMENTED_WITH_BOUNDARIES` | Attach governed rules to actions/intents | parser/interpreter/CVM wrappers | action/intent -> applicable policy -> verdict | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Supported policy paths fail closed on violation | Policy semantics vary by execution path | Universal external-policy engine | A/B by guard | determinism contract | Audit base |
| GOV-02 | Guards | `IMPLEMENTED_WITH_BOUNDARIES` | Evaluate and enforce guarded effects | bytecode/CVM/interpreter/bridge | guard enter/check -> effect or violation -> exit | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Supported static/dynamic guards enforce recorded verdict boundary | Dynamic producer needs recorded result | Universal strict replay for arbitrary guard code | A static; B dynamic | determinism contract | Audit base |
| GOV-03 | Intents | `IMPLEMENTED_WITH_BOUNDARIES` | Declare prospective action before execution | AST/interpreter | intent declaration -> policy -> history | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Rejected intent blocks downstream supported action | Does not automatically wrap every host effect | Universal action interception | Conditional B | language/governance docs | Audit base |
| GOV-04 | Claims | `IMPLEMENTED_WITH_BOUNDARIES` | Represent auditable assertions | AST/interpreter | claim -> verification context | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Claim identity and lifecycle are recorded | Claim text is not truth | Universal proof authority | Conditional B | language docs | Audit base |
| GOV-05 | Verification records | `IMPLEMENTED_WITH_BOUNDARIES` | Record oracle/check outcome | interpreter and verification buffers | verify -> result record -> history | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Tested verifier outcomes are recorded | Bound by selected verifier/oracle | Semantic correctness beyond oracle | Conditional B | language docs | Audit base |
| GOV-06 | Consequences | `IMPLEMENTED_WITH_BOUNDARIES` | Trigger bounded follow-up from verdicts | AST/interpreter | verification/policy result -> consequence | [EV-GOVERNANCE](#exact-evidence-groups) | `INSPECTED` | Supported branches execute from recorded state | No universal compensation transaction | Automatic external rollback | Conditional B | language docs | Audit base |
| MEM-01 | Memory Palace | `IMPLEMENTED_WITH_BOUNDARIES` | Named memory rooms and policies | interpreter/memory runtime/storage | declaration -> palace registry -> room operations | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Supported palace state is queryable and persistable | Adapters are not verified knowledge admission | RepositoryKnowledge authority | Conditional B | memory docs/history | Audit base |
| MEM-02 | Episodic memory | `IMPLEMENTED_WITH_BOUNDARIES` | Store event/context memories | memory runtime | imprint -> episodic room | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Structured entries and audit metadata are retained | Truth/admission not implied | Automatic long-term verification | Conditional B | memory docs | Audit base |
| MEM-03 | Semantic memory | `IMPLEMENTED_WITH_BOUNDARIES` | Store fact-like memory entries | memory runtime | imprint/consolidate -> semantic room | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Structured entries can be recalled | Entry is not verified merely by room choice | Repository truth authority | Conditional B | memory docs | Audit base |
| MEM-04 | Procedural memory | `IMPLEMENTED_WITH_BOUNDARIES` | Store skill/habit metadata | memory/habit runtime | consolidation/habit metadata -> procedural room | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Procedural metadata is retained | Room metadata does not execute or prove a skill | Verified reusable behavior | Conditional B/C | memory/habit docs | Audit base |
| MEM-05 | Imprint | `IMPLEMENTED_WITH_BOUNDARIES` | Write structured memory with provenance/confidence | interpreter/memory runtime | `imprint` -> validate -> room entry/history | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Supported entries preserve declared metadata | Caller-supplied confidence is not proof | Evidence admission gate | Conditional B | memory docs | Audit base |
| MEM-06 | Recall | `IMPLEMENTED_WITH_BOUNDARIES` | Query/filter/sort memory | interpreter/memory runtime | `recall` -> room query -> bounded result | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Supported filters produce deterministic results over fixed state | External vector/database parity not guaranteed | Universal semantic retrieval | A/B based on backend/state | memory docs | Audit base |
| MEM-07 | Consolidation | `IMPLEMENTED_WITH_BOUNDARIES` | Promote/route entries between rooms | memory runtime | consolidate -> policy/routing -> entries/events | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Supported routing records decisions and cost | Not evidence validation | Automatic knowledge admission | Conditional B | memory docs | Audit base |
| MEM-08 | Governed forgetting | `IMPLEMENTED_WITH_BOUNDARIES` | Delete/expire memory with audit semantics | interpreter/memory runtime | forget/decay -> policy -> removal event | [EV-MEMORY](#exact-evidence-groups) | `INSPECTED` | Supported deletion is policy-aware and recorded | External replicas/backups are outside runtime proof | Universal erasure guarantee | Conditional B | memory docs | Audit base |
| COG-01 | Dream | `IMPLEMENTED_WITH_BOUNDARIES` | Sandboxed simulation and recorded result | interpreter | dream body -> isolated result -> `dream_completed` | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | External mutations are blocked and result can be replay-consumed | Strict Layer 1 state-delta model absent | Universal strict replay | Category B; not strict Layer 1 | dream RFCs, determinism contract | Audit base |
| COG-02 | Integrate | `IMPLEMENTED_WITH_BOUNDARIES` | Transactional admission of selected dream result into runtime state | interpreter/state overlay | integrate -> overlay -> commit/abort record | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported write set commits atomically or rolls back | Deferred strict replay gates remain | General application-memory admission | Category B conditional | integrate RFCs | Audit base |
| COG-03 | Soulprint | `IMPLEMENTED_WITH_BOUNDARIES` | Protected identity/value/style metadata | interpreter | agent definition -> soulprint state | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported identity state is versioned/audited | Independent-live-run stable identity is limited | Cryptographic identity authority | Conditional B/C | identity RFCs | Audit base |
| COG-04 | Evolve | `IMPLEMENTED_WITH_BOUNDARIES` | Governed identity mutation | interpreter | trigger/policy -> ticket or atomic evolution | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported changes enforce policy/delta boundaries | Deferred ticket identity may vary across live runs | Autonomous trusted self-modification | Category C or conditional B | identity RFCs | Audit base |
| COG-05 | Fracture | `IMPLEMENTED_WITH_BOUNDARIES` | Isolated multi-perspective sub-agent execution | interpreter | fracture -> isolated branches -> consensus/integrate | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Branch mutation limits and death states are enforced | Branch results are not independent proof | Arbitrary nested/distributed authority | Category B/C | fracture docs/history | Audit base |
| COG-06 | Resonate | `IMPLEMENTED_WITH_BOUNDARIES` | Read-only inter-subjective profile/calibration | interpreter | target/history -> profile/cache | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported reads respect isolation/privacy gates | Profile is inference, not verified identity | External user-state authority | Conditional B | resonance docs | Audit base |
| COG-07 | Debate | `IMPLEMENTED_WITH_BOUNDARIES` | Multi-round argument branches and judge | interpreter | branches -> rounds/history -> judge LLM | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported debate records branch/judge inputs and result | Judge output is oracle/model-bound | Objective truth | Category B | determinism contract | Audit base |
| COG-08 | Reflect | `IMPLEMENTED_WITH_BOUNDARIES` | Read-only audit queries over history/identity/memory | interpreter | reflection query -> current recorded state | [EV-COGNITIVE](#exact-evidence-groups) | `INSPECTED` | Supported query does not mutate workflow state | Result is limited to available state | External observability completeness | A over fixed state | language docs | Audit base |
| AFF-01 | Affective state | `IMPLEMENTED_WITH_BOUNDARIES` | PAD state and modulation inputs | interpreter/affective runtime | events/operations -> PAD update | [EV-AFFECTIVE](#exact-evidence-groups) | `INSPECTED` | Supported PAD transitions are bounded and auditable | Computational affect is not human emotion | Clinical/psychological authority | Conditional B | affective docs/history | Audit base |
| AFF-02 | Affective events and memory | `IMPLEMENTED_WITH_BOUNDARIES` | Tag runtime/memory with PAD metadata and decay | affective/memory runtime | affective event -> tag -> imprint/decay/recall | [EV-AFFECTIVE](#exact-evidence-groups) | `INSPECTED` | Supported tags, decay, filters, and events are recorded | Tag provenance is caller/runtime-bound | Truth of emotional interpretation | Conditional B | affective docs | Audit base |
| AFF-03 | Reactive thresholds | `IMPLEMENTED_WITH_BOUNDARIES` | Trigger purity-checked action after PAD condition duration | affective runtime/interpreter | PAD history -> threshold -> action/cooldown | [EV-AFFECTIVE](#exact-evidence-groups) | `INSPECTED` | Supported thresholds enforce action restrictions | No arbitrary side effects from threshold body | Production alerting/SLO system | Conditional B | affective docs | Audit base |
| AFF-04 | Atomic affective resonance | `IMPLEMENTED_WITH_BOUNDARIES` | Apply a batched resonance delta once | interpreter | bridge delta -> atomic PAD update -> event | [EV-AFFECTIVE](#exact-evidence-groups) | `INSPECTED` | Replay consumes recorded applied delta | Live interpretation remains producer-dependent | Universal affective truth | Category B | determinism contract | Audit base |
| AFF-05 | Somatic markers | `IMPLEMENTED_WITH_BOUNDARIES` | Heuristic decision markers and escalation hints | interpreter/affective runtime | observation -> marker -> decision/fracture path | [EV-AFFECTIVE](#exact-evidence-groups) | `INSPECTED` | Supported markers affect bounded runtime decisions | Heuristic is not proof | Clinical or safety certification | Conditional B/C | affective docs | Audit base |
| HAB-01 | Habits | `IMPLEMENTED_WITH_BOUNDARIES` | Register and activate recurring procedural behavior | habit registry/interpreter | pattern -> registration -> guarded activation -> body | [EV-HABITS](#exact-evidence-groups) | `INSPECTED` | Supported body executes with locks and lifecycle events | Registry state is runtime-owned, not full CVM | Verified reusable knowledge | Category C/conditional replay | habit docs/history | Audit base |
| HAB-02 | Context | `IMPLEMENTED_WITH_BOUNDARIES` | Scope contextual labels for activation/energy | interpreter | context block -> runtime context stack | [EV-HABITS](#exact-evidence-groups) | `INSPECTED` | Supported context scopes activation and state | No universal environment context | External context truth | Category A/B | habit docs | Audit base |
| HAB-03 | Energy | `IMPLEMENTED_WITH_BOUNDARIES` | Event-based cognitive resource accounting | habit/affective runtime | activation -> energy cost/update | [EV-HABITS](#exact-evidence-groups) | `INSPECTED` | Supported costs update deterministic runtime state | Not provider token/cost accounting | Economic/resource billing authority | Category A/B | habit docs | Audit base |
| HAB-04 | Fatigue and recovery | `IMPLEMENTED_WITH_BOUNDARIES` | Adjust activation cost and rest eligibility | habit runtime | activation count -> fatigue -> recovery events | [EV-HABITS](#exact-evidence-groups) | `INSPECTED` | Supported lifecycle is recorded and enforced | Not physiological measurement | Real-world fatigue inference | Conditional B/C | habit docs | Audit base |
| OPS-01 | Storage | `IMPLEMENTED_WITH_BOUNDARIES` | Persist JSON-safe snapshots and event batches | `synapse/persistence.py` and adapters | runtime state/events -> storage adapter | [EV-OPERATIONS](#exact-evidence-groups) | `INSPECTED` | In-memory/SQLite-shaped contracts persist supported values | Production backend operations are deployment-specific | Universal distributed durability | Storage-bound | storage docs/history | Audit base |
| OPS-02 | Metrics | `IMPLEMENTED_WITH_BOUNDARIES` | Runtime counters/snapshots/text exposition | `synapse/metrics.py` and runtime hooks | execution -> metrics snapshot/text | [EV-OPERATIONS](#exact-evidence-groups) | `INSPECTED` | Supported counters expose runtime observations | Metrics are not canonical C-stage provider accounting | Token/cost/performance comparison authority | Non-canonical diagnostics | metrics docs/history | Audit base |
| CHG-01 | Controlled Change | `IMPLEMENTED_WITH_BOUNDARIES` | Apply committed candidate under task/scope/verification/report contract | `synapse/change/` | committed base/task/patch -> worktree -> verify -> ref/report | [EV-CONTROLLED-CHANGE](#exact-evidence-groups) | `INSPECTED` | Tested inputs, candidate, scope, verified commit, evidence ref, and report are bound | Subprocess argv discipline is not OS sandboxing | Universal command isolation/live benchmark authority | Evidence-bound, not replay engine | `synapse/change/` contracts | Audit base |
| SWE-01 | Applied SWE-bench verification | `IMPLEMENTED_WITH_BOUNDARIES` | Baseline and Gold experiment adapters over bounded tasks/oracles | `synapse/experiments/swebench/` | task/candidate -> controlled change/oracle -> record | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Tested fixture paths classify candidate/oracle outcomes | No long-suite or live-provider authority | Live SWE-bench proof | Experiment evidence only | SWE-bench contracts | Audit base |
| SWE-02 | GoldEvidence | `IMPLEMENTED_WITH_BOUNDARIES` | Bind base/task/patch/report/application/evidence ref to verified commit | `gold_evidence.py` | controlled result + report root -> validation | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Valid evidence requires report hash, trusted inputs, APPLIED lifecycle, and ref resolution | Evidence proves its contract, not benchmark semantics beyond oracle | FULL verification | Evidence object, not replay | GoldEvidence contract | Audit base |
| SWE-03 | Gold external oracle binding | `IMPLEMENTED_WITH_BOUNDARIES` | Derive harness patch from single-parent verified commit pair | `gold_oracle_binding.py` | detached verified commit -> parent diff -> harness report | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Clean verified commit can invoke report-authoritative oracle | Merge/root commits fail closed; live harness not proven here | Universal SWE-bench authority | External oracle result | oracle binding contract | Audit base |
| SWE-04 | Paired measurement | `IMPLEMENTED_WITH_BOUNDARIES` | Compare already-produced Baseline/Gold success outcomes | `paired_measurement.py` | members -> hard identity checks -> success-only record | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Matching task/base/replicate/oracle config can form success-only diagnostic | Token/cost/wall-clock/performance remain non-reusable | Paired execution harness/economic comparison | Diagnostic only | paired measurement contract | Audit base |
| SWE-05 | Measurement output/admission boundary | `IMPLEMENTED_WITH_BOUNDARIES` | Label success-only output, reject overclaim, classify evidence candidate | `measurement_output.py` | pair/evidence/mapping -> pure boundary records | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Reserved reusable/FULL/carry/runtime states fail closed | No telemetry, admission, carry, or memory runtime integration | Gateway, append, Gold-with-carry | Contract only | measurement output contract | Audit base |
| SWE-06 | Single-attempt Gold runner | `IMPLEMENTED_WITH_BOUNDARIES` | Materialize an already-obtained worker result and classify one controlled attempt | `gold_runner.py`, Gold attempt writer | worker result -> committed bridge -> controlled change -> evidence -> oracle -> JSONL | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | APPLIED plus valid evidence invokes the injected oracle and writes bounded status | Does not invoke workers, pair arms, run long suites, or implement multi-attempt lifecycle | Integrated Gold execution and multi-attempt authority | Evidence/oracle path, not replay | Gold runner contract | Audit base |
| CARRY-01 | RawTranscriptCarry / `BASELINE_RAW_RETRY_CARRY` | `IMPLEMENTED_WITH_BOUNDARIES` | Carry raw baseline attempt transcript into later baseline retry context | `carry.py`, baseline experiment path | baseline attempt -> raw entry -> next baseline attempt context | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Bounded raw retry context is available within baseline semantics | Not validated Gold evidence; not admitted/trusted knowledge; not Gold memory; not mechanism proof; not Stage 4 snapshot | DistilledEvidenceCarry and RepositoryKnowledge | Non-canonical diagnostic context | baseline/carry contracts | Audit base |
| TEL-01 | Canonical provider telemetry boundary | `IMPLEMENTED_WITH_BOUNDARIES` | Reject premature reusable accounting claims and validate future candidate shape | `measurement_output.py`, paired contract | pure mapping -> candidate validation/output label | [EV-SWEBENCH](#exact-evidence-groups) | `INSPECTED` | Requires exact accounting fields and keeps token-bearing output non-reusable | Validator is not a gateway and does not intercept provider calls | Canonical runtime telemetry gateway | Non-canonical contract | measurement output contract | Audit base |
| KNOW-01 | Verified reusable knowledge | `DESIGN_TARGET` | Validated, scoped, distilled, invalidatable knowledge for later runs | No runtime owner; boundary candidate only | Future evidence admission -> repository/application/session owner | [EV-GOLD-DESIGN](#exact-evidence-groups) | `INSPECTED` | No runtime guarantee | GoldEvidence candidate classification stops before append | EvidenceAdmissionGate, DistilledEvidenceCarry, RepositoryKnowledge, invalidation/replay | Not implemented | roadmap/C-phase design | Audit base |
| GOLD-01 | Gold Execution integrated runtime | `DESIGN_TARGET` | Execute Gold with admitted reusable evidence and complete measurement authority | No integrated owner | Future gateway + admission + carry + execution + oracle | [EV-GOLD-DESIGN](#exact-evidence-groups) | `INSPECTED` | No integrated-runtime guarantee | Prerequisites must not be composed into an implied runtime | Gold-with-carry, application/session append, FULL promotion, CVM knowledge replay | Not implemented | roadmap/C-phase design | Audit base |
| AS2-01 | AS2 adapter/provider verification | `IMPLEMENTED_WITH_BOUNDARIES` | Validate projection, capability, idempotency, persistence, and external-provider contracts | AS2 modules, tests, workflow, verification docs | gated adapter/harness -> provider/verification stack | [EV-AS2](#exact-evidence-groups) | `INSPECTED` | Exercised contracts and verification evidence exist | Production enablement is locked; verification stack is not sign-off | Production backend rollout and operational authority | Verification-specific | AS2 RFCs/reports | Audit base |

## 7. Replay Eligibility Matrix

| Class | Eligible behavior | Required evidence | Examples | Exclusions |
| --- | --- | --- | --- | --- |
| Category A | Pure deterministic computation over fixed state | Source/bytecode/state plus deterministic transition contract | lexer/parser, pure CVM opcodes, fixed-state reflection/query operations | Live providers, external clocks, unrecorded host effects |
| Category B | Nondeterministic or host-mediated behavior whose authoritative result is recorded and consumed | Bound request/result or verdict, ordered history, hashes, and replay contract | LLM cache consumption, dynamic guard verdicts, `dream_completed`, integrate commit/abort, atomic affective resonance | Re-running the live producer and calling it replay |
| Category C | Behavior with unstable identity, deferred tickets, orchestration internals, or missing consume model | Additional identity/state-delta/subtrace contract required | some evolution, habit, consensus, distributed/prototype paths | Strict canonical replay until the governing gate closes |
| Exploratory | Read-only diagnostics or copy-on-write forks | Parent artifact identity plus non-canonical marker | debugger forks and event-injection experiments | Promotion to canonical history or evidence |
| Contract-only | Pure validation/classification without execution | Input mapping/dataclass and validator result | paired measurement/output/admission candidate/telemetry candidate | Runtime gateway, memory append, or replay authority |

Construct-level rules in [DETERMINISM_CONTRACT.md](DETERMINISM_CONTRACT.md)
override a broad category summary. In particular, `dream` and `integrate` have
recorded Category B paths but are not thereby universally strict Layer 1
eligible.

## 8. Implemented-with-Boundaries Register

### Language, execution, and replay

The complete source-to-diagnostics spine exists. Boundaries are per-construct
CVM lowering, durable-subset eligibility, Category B resource capture, and
Category C identity/state semantics. Mock replay proves use of recorded
resources for eligible artifacts; it does not prove equivalence to a new live
provider call.

### Actor and cognitive runtime

Agents, messaging, mailbox, spawn, suspension, promises, await, governance,
memory, cognitive primitives, affective mechanisms, and habits have real
tree-walker/runtime paths and focused tests. They do not all share the same
durable engine, CVM coverage, independent-live-run identity, or replay class.

### Controlled change and Gold prerequisites

Controlled change, the Gold runner, GoldEvidence validation, verified-commit
oracle binding, success-only paired measurement, and C2-S3 output boundaries
are implemented as separate contracts. Their composition does not create:

- a canonical provider telemetry gateway;
- reusable token/cost/performance evidence;
- runtime evidence admission or distilled carry;
- Gold-with-carry;
- application/session/RepositoryKnowledge append;
- Cognitive VM replay of admitted knowledge;
- `GOLD_FULL_VERIFIED`.

### Raw carry boundary

`RawTranscriptCarry` and `BASELINE_RAW_RETRY_CARRY` are implemented only as raw
Baseline retry context. They are not verified knowledge, Gold memory, admitted
evidence, trusted context, a mechanism proof, or a Stage 4 reusable snapshot.
Any future path that crosses that boundary requires validated Gold evidence,
scope checks, a distilled representation, explicit ownership, invalidation,
and replay semantics.

### AS2 verification boundary

AS2 projection, runtime-gating, provider-port, idempotency, persistence/outbox,
and verification artifacts exist. External PostgreSQL/PgBouncer/
Debezium/Redpanda evidence exercises named semantics under the verification
stack. Production enablement, backend migrations, relay operations, SLOs,
credentials, and owner sign-off remain locked.

## 9. Experimental Register

Each `Contour ID` identifies one local responsibility and has one formal status.
Secondary registers must not redefine that responsibility or assign a different
status to the same ID.

| ID | Experimental contour | Available evidence | Why it remains experimental |
| --- | --- | --- | --- |
| ACT-08 | Mobility envelopes and location-transparent routing | JSON-safe envelope and routing tests | No production authentication, persistence, retries, backpressure, or deployment authority |
| ACT-09 | `synapsed.py` network node | Prototype packet handlers | No production network, security, durability, or SLO contract |
| AS2-EXP-01 | Open external-provider verification deployment | AS2 verification workflow, provider tests, and verification reports | The verification stack exists, but production enablement, operational ownership, credentials, migrations, relay operations, and SLO authority remain locked |

## 10. Design-Target Register

| ID | Design target | Required prerequisites | Current stopping boundary |
| --- | --- | --- | --- |
| KNOW-01 | Verified reusable knowledge | Valid GoldEvidence, scope admission, distilled form, ownership, invalidation, replay contract | C2-S3 can classify an admissible contract candidate only; it never appends |
| KNOW-02 | EvidenceAdmissionGate | Explicit application/session integration and fail-closed policy | `validation_ok` is caller input to a pure boundary function, not a runtime gate |
| KNOW-03 | DistilledEvidenceCarry | Admitted evidence schema and no raw transcript authority | No runtime carry implementation |
| KNOW-04 | RepositoryKnowledge | Repository-scoped ownership, provenance, invalidation, query, and replay | No repository knowledge store or admission path |
| TEL-02 | Canonical provider telemetry gateway | All in-scope provider calls pass exact accounting schema and failure semantics | Candidate record validator only |
| GOLD-01 | Integrated Gold execution | Telemetry gateway, evidence admission, distilled carry, runtime selection, paired execution | Existing C1/C2 contracts are prerequisites only |
| GOLD-02 | Gold-with-carry measurement | Integrated Gold execution plus comparable Baseline/Gold protocol | `GOLD_WITH_CARRY` remains reserved/fail-closed |
| REPLAY-02 | Cognitive VM replay of admitted knowledge | Stable knowledge identity, snapshot/invalidation, deterministic injection | No admitted knowledge exists |
| FULL-01 | FULL promotion | Separately approved end-to-end authority | `GOLD_FULL_VERIFIED` remains reserved/forbidden |

### Integrated Gold Design-Target Decomposition

The integrated Gold target is not one implicit feature. Each of the following
is independently `DESIGN_TARGET` until its own runtime owner, contract, and
evidence exist:

| Target | Required future responsibility |
| --- | --- |
| Behavior Library | Own discoverable reusable behavior units without treating transcripts as behavior |
| Typed reusable behavior units | Define inputs, outputs, effects, compatibility, scope, and failure semantics |
| Provenance and attestation lifecycle | Preserve evidence lineage, validation state, invalidation, and revocation |
| Compatibility evidence | Bind a reusable unit to repository/base/runtime/oracle compatibility claims |
| Publication admission | Admit only validated, scoped evidence through a named authority |
| Immutable CAS publication | Publish admitted units under content-addressed immutable identity |
| Retrieval authority | Select reusable units under explicit repository/task/policy scope |
| Atomic RepositoryKnowledgeSnapshot | Supply one immutable, provenance-bound view to an attempt |
| Governed CognitiveVM behavior replay | Execute an admitted typed unit under deterministic capability and replay rules |
| Typed worker context | Pass admitted units without raw prompt/transcript authority |
| Multi-attempt Gold lifecycle | Represent complete attempt selection, retries, terminal state, and anti-cherry-pick evidence |
| Canonical telemetry completeness | Prove all in-scope provider calls cross the accounting gateway |
| Execution lineage | Bind mechanism selection and execution events to the resulting attempt evidence |
| Observed mechanism-use proof | Prove that retrieved knowledge was actually used, not merely present in context |
| Net-benefit proof | Compare paired outcomes using canonical token/cost/performance evidence without overclaim |

## 11. Not-Implemented Register

The following are explicitly absent at the audit base:

- a canonical runtime provider telemetry gateway;
- token-bearing reusable Baseline/Gold measurement;
- token savings, cost savings, ROI, performance improvement, wall-clock
  speedup, throughput improvement, or economic calibration authority;
- runtime-integrated `EvidenceAdmissionGate`;
- runtime-integrated `DistilledEvidenceCarry`;
- application/session append of Gold evidence;
- `SessionKnowledgeBase`, `RunMemorySnapshot`, or `ForkMemory` authority for
  the C-phase knowledge path;
- RepositoryKnowledge admission and invalidation;
- Gold-with-carry execution or measurement;
- Cognitive VM replay of admitted reusable knowledge;
- integrated Gold Execution runtime;
- `GOLD_FULL_VERIFIED` or FULL promotion;
- production networking authority for mobility prototypes;
- production AS2 enablement/sign-off from the verification-only stack;
- universal Python-frame serialization or a continuation cursor for every
  construct;
- OS-level sandbox proof for controlled-change subprocess execution;
- live SWE-bench or long-suite authority unless a dated report explicitly
  records such a run.

## 12. Cross-Cutting Limitations

1. **Version identifiers and merged work differ.** Package metadata remains
   `alpha3e`; later merged Alpha3g features do not silently establish a new
   release or completion of the Alpha3g workline.
2. **Replay is contract-scoped.** Recording a result can make a Category B path
   replayable without making the underlying live producer deterministic.
3. **Oracle results are bounded.** SWE-bench report resolution is authority for
   that oracle result, not semantic correctness beyond the oracle.
4. **Metrics are not economics.** Runtime counters and Stage 3A token records
   do not form canonical paired telemetry.
5. **Raw context is not knowledge.** Raw transcript carry remains unvalidated
   Baseline retry context.
6. **Verification is not deployment.** External-provider and Docker Compose
   evidence does not unlock production AS2 or Gold runtime paths.
7. **Structural wrappers preserve ownership.** CVM routing for a declaration
   does not move actor, policy, memory, affective, or habit internals into the
   VM.
8. **No sandbox overclaim.** Shell-free argv subprocess execution is useful but
   is not an isolation proof.

## 13. Docsync Requirements

A patch must update this register when it:

- adds, removes, or promotes a contour;
- changes module ownership or the canonical execution path;
- changes replay category or strict eligibility;
- changes an evidence requirement, validation source, oracle, or failure
  classification;
- enables a provider/runtime gateway, carry, admission, memory append, Gold
  mode, or FULL status;
- changes production-readiness or verification-only boundaries;
- changes package version metadata in a way that affects current status.

If the register cannot be synchronized in the same PR, the patch must record
an explicit `DOCSYNC_REQUIRED` result rather than silently leave stale status.

README summaries must link here rather than duplicate audit SHAs or volatile
test counts. Architecture must describe flow and ownership. Roadmap must
describe future gates. Changelog must retain chronology. A subsystem document
may be more detailed, but it must not silently contradict the status row.

## 14. Audit Method

The 2026-07-14 audit used the following read-only or non-mutating checks:

1. resolved and fetched `origin/main` and pinned the audit base;
2. inspected language, interpreter, CVM, bridge, actor, history, replay,
   debugger, controlled-change, SWE-bench, storage, metrics, and AS2 modules;
3. inspected focused tests, committed fixtures, reports, RFCs, and
   specifications as evidence without treating tests as production runtime;
4. checked `synapse/version.py` for package identifiers;
5. executed CLI help for package, run, replay, debug compare, and controlled
   change surfaces;
6. executed `examples/math.syn` and mock-backed `examples/hello_agent.syn`;
7. replayed the committed strict `print_math` golden artifact with `--mock`;
8. compared the `print_math` artifact with itself and observed equality;
9. classified negative paths and reserved statuses as boundaries rather than
   implementation;
10. did not call live providers, Docker, SWE-bench, controlled-change, or
    production infrastructure.

The audit establishes documentation accuracy at the pinned repository state.
It is not a substitute for CI, live-provider verification, release signing, or
production sign-off.

## 15. Change History

| Date | Change | Audit base |
| --- | --- | --- |
| 2026-07-14 | Created the authoritative current implementation status register; separated architecture, roadmap, changelog, and README responsibilities; classified replay and C-phase knowledge/telemetry boundaries fail-closed | `c941e41ac4ebd2c59a6c7b7db3b6acea1f1e2f28` |
