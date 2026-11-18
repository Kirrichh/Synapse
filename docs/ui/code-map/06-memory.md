# Memory and learning: existing owners

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/memory_consolidation/project_port.py`

- **Symbols:** ProjectMemoryCourt.consolidate.
- **Role:** Canonical unified-memory-court port supplied by CLI to execute_gold_project_run, using the same owner session and decision chain.
- **UI use:** Connect Gold tasks to memory owner/settings/state.
- **Boundary:** Inject the port at composition; Gold does not import the whole memory subsystem for UI.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/configuration.py`

- **Symbols:** MemoryConfiguration; KnowledgePolicy; parse_memory_configuration; read_memory_configuration.
- **Role:** Reads court/tools/advisor/scorer/knowledge/identity and computes configuration digest.
- **UI use:** Parse proposed configuration and describe fields; read active binding separately from MemoryOwner.
- **Boundary:** A file is not necessarily active policy; active reads begin with bound_configuration.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/policy.py`

- **Symbols:** PARAMETERS; DECISION_RULES; resolve_parameters; policy_identity; signal; trust_update; sprt_step; sprt_decision; cs_radius.
- **Role:** Primary parameter/formula registry and policy identity.
- **UI use:** Rule selector, sliders/equalizer, and mathematical validation.
- **Boundary:** Not a complete UI description or all semantic constraints. raw_capacity counts traces, not bytes.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/owner.py`

- **Symbols:** MemoryOwner.bound_configuration; bound_digest; state; applied; read_report; register_session; rebind.
- **Role:** Project memory owner, active configuration, and fold of evidence reports.
- **UI use:** Settings, policy history, habits/candidates, and source decisions.
- **Boundary:** memory.json alone does not prove active policy; writes operate under owner sessions.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/factory.py`

- **Symbols:** MemoryFactory.open_session; memory_now; reassess; trial; forget; restore; resolve_memory.
- **Role:** Canonical durable-session memory assembly, gateway/legitimacy injection, court invocation.
- **UI use:** Policy acceptance and other permitted memory actions through machine mode.
- **Boundary:** reassess mutates live state; exams do not sandbox the whole object. Recovery under another configuration may fail.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/session.py`

- **Symbols:** MemorySession; ReplaySession; ReproductionSession.
- **Role:** Memory views for normal, replay, and reproduction sessions.
- **UI use:** Running-session memory context.
- **Boundary:** UI settings cannot replace pinned memory mid-execution.
- **Basis:** MemoryFactory types and package tree.

## `synapse/memory_consolidation/court/consolidation.py`

- **Symbols:** consolidate; consolidate_exact; reassess; _reassessed.
- **Role:** Coordinated evaluation, report/decision/boundary publication, retention, and configuration rebind.
- **UI use:** Policy acceptance/result reads; shared domain calculation for future preview.
- **Boundary:** _reassessed is not command_id. Effect order needs durable command binding and failure acceptance.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/court/reassessment.py`

- **Symbols:** reassess_bases; lost_bases; republication.
- **Role:** Rechecks retained bases, promotions, hypotheses, and conflicts.
- **UI use:** Consequences of policy changes for existing habits.
- **Boundary:** Neither future-learning prediction nor an independent dry-run endpoint.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/court/decide.py`

- **Symbols:** decide; _reassess.
- **Role:** Deterministic court decisions; reassess creates no births.
- **UI use:** Actual transitions/reasons.
- **Boundary:** Do not duplicate decision formulas in UI.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/court/pool.py`

- **Symbols:** assess; merge_pool; independence_of.
- **Role:** Candidate accumulation and six birth criteria, including provenance.
- **UI use:** Explain why a candidate has not become a habit.
- **Boundary:** Example count is not independent confirmation count.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/court/births.py`

- **Symbols:** pool_stage; make_birth; typed_check.
- **Role:** Habit proposals, basis links, and typed checks.
- **UI use:** Habit provenance and waiting reasons.
- **Boundary:** Court birth still passes Gold admission through the existing port.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/learning/provenance.py`

- **Symbols:** closure; relation; independent_witnesses.
- **Role:** Source independence through provenance graphs.
- **UI use:** Read-only explanations of excluded/dependent witnesses.
- **Boundary:** A confidence slider cannot change graph/verdict; required=0/1 need domain restrictions.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/legitimacy.py`

- **Symbols:** GoldLegitimacy.
- **Role:** Connects learned habits with Gold admission/publication.
- **UI use:** Show actual admission rather than mere proposal.
- **Boundary:** Preview cannot publish to live storage.
- **Basis:** MemoryFactory.gateway construction and court legitimacy calls.

## `synapse/memory_consolidation/court/retention.py`

- **Symbols:** retention_pass; pending_acts; apply_acts.
- **Role:** Retains source experience and applies recorded retention acts.
- **UI use:** Permissible reclamation planning and retention reasons.
- **Boundary:** Not a ready byte quota; archive status does not prove physical space reclaimed.
- **Basis:** factory/consolidation calls; these surfaces lack a complete byte-accounting API.

## `synapse/memory_consolidation/operator_acts.py`

- **Symbols:** forget; restore.
- **Role:** Operator actions on retained experience through MemoryFactory.
- **UI use:** Explicit forgetting/restoration within permitted limits.
- **Boundary:** Restoring configuration is not restoring memory; no hidden slider-driven cleanup.
- **Basis:** MemoryFactory calls and tree.

## `synapse/experiments/gold/project_memory_store.py`

- **Symbols:** ProjectMemoryStore.
- **Role:** Existing journal of persistent element owners and memory records.
- **UI use:** Read projections preserving receipts/source records.
- **Boundary:** No separate UI writer.
- **Basis:** Manifest and MemoryOwner code.

## `synapse/experiments/gold/project_court.py`

- **Symbols:** court_chain; append_decision; establish_tail.
- **Role:** Unified project court decision chain invoked by memory consolidation.
- **UI use:** Primary bottom-panel evidence.
- **Boundary:** Do not turn this owner module into a new composition root.
- **Basis:** owner/consolidation calls and manifest.

## `synapse/experiments/gold/project_agents.py`

- **Symbols:** Element-owner job lifecycle.
- **Role:** Owns element jobs and active-memory frames bound to court decisions.
- **UI use:** Basis of internal project roles/agents.
- **Boundary:** Distinct from external AgentAdapter; UI must distinguish executor types.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/project_learning.py`

- **Symbols:** Four memory layers and consolidated assertions.
- **Role:** Publication-based learning projections without independent admission.
- **UI use:** Learning state/memory layer.
- **Boundary:** Projection is not another truth owner.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/learned_habit_profile.py`

- **Symbols:** Learned-behavior profile.
- **Role:** Presents and independently verifies learned-habit claims at publication.
- **UI use:** Distinguish learned procedures from admitted behavior.
- **Boundary:** No manual switch to verified.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/learned_habit_lifecycle.py`

- **Symbols:** Learned-behavior admission lifecycle.
- **Role:** Published-behavior currency, supersession, and withdrawal.
- **UI use:** Explain why a previous habit no longer applies.
- **Boundary:** Historical records remain unchanged.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/memory_event_storage.py`

- **Symbols:** MemoryEventStorage.
- **Role:** Physical memory logs/shared content-addressed blocks under owner tickets.
- **UI use:** Plan volume measurement and block attribution.
- **Boundary:** Never count the same shared blocks multiple times via references.
- **Basis:** Manifest, tree, and acceptance import.

## `synapse/experiments/gold/memory_blocks.py`

- **Symbols:** Component of memory_event_storage.py.
- **Role:** Retention of shared memory-event blocks.
- **UI use:** Supporting storage-accounting layer.
- **Boundary:** Not a separate UI API.
- **Basis:** Manifest owner_components.

## `synapse/experiments/gold/memory_event_budget.py`

- **Symbols:** Component of memory_event_storage.py.
- **Role:** Internal physical-event representation/budget responsibility.
- **UI use:** Input for separate byte-quota design.
- **Boundary:** The name budget does not prove an implemented whole-owner 20 GB cap.
- **Basis:** Manifest owner_components.

## `synapse/experiments/gold/project_memory_selection.py`

- **Symbols:** select_run_knowledge; require_run_memory_selection.
- **Role:** Explicit operator selection of run memory classes without rewriting archives/admission.
- **UI use:** Task-specific memory-use choices.
- **Boundary:** Not court-policy changes or extra rights.
- **Basis:** Manifest and source_snapshot/run_inputs imports.
