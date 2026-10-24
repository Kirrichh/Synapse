# Task preparation, context, local information, and code results

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/experiments/gold/run_inputs.py`

- **Symbols:** freeze_gold_inputs; reopen_frozen_inputs; EXPERIMENT_INPUT_SCHEMA_V4.
- **Role:** Freezes run inputs/profile choice. Input v4 uses agents; select_coding_agent runs before freezing.
- **UI use:** Persist task and profile selection in the canonical run.
- **Boundary:** Later profile selection cannot silently change a frozen run.
- **Basis:** Code search and integration documentation confirm calls/formats.

## `synapse/experiments/gold/agent_selection.py`

- **Symbols:** select_coding_agent.
- **Role:** Selects an admitted coding profile for task contract, PATH_MODIFIED/PATH_CREATED effects, model/provider/budget; requires repository.edit, patch-candidate, LOCAL_ONLY and LOCAL_BROKER/NONE networks.
- **UI use:** Task-specific eligibility check.
- **Boundary:** The registry is general; this Gold consumer is coding-specific. A switch cannot make Docling/ACP/A2A compatible.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/stage10/task_contract.py`

- **Symbols:** GoverningTaskContract.
- **Role:** Independent governing task contract: constraints, effects, permitted capabilities.
- **UI use:** Domain meaning of task.submit and the approved scope.
- **Boundary:** Never derive constraints solely from text or card status.
- **Basis:** agent_selection imports/usage and manifest.

## `synapse/experiments/gold/stage10/intent.py`

- **Symbols:** IntentCandidate; EffectKind; EffectDisposition.
- **Role:** Typed proposed intent and expected effects.
- **UI use:** Display proposed plan/intent separately from accepted decisions.
- **Boundary:** Agent proposals confer no authority.
- **Basis:** Confirmed imports and manifest.

## `synapse/experiments/gold/stage10/planning.py`

- **Symbols:** OperationPlanCandidate.
- **Role:** Structured candidate plan.
- **UI use:** Central plan/review view.
- **Boundary:** The UI cannot declare a plan accepted.
- **Basis:** Manifest and approval/revalidation usage.

## `synapse/experiments/gold/stage10/plan_authority.py`

- **Symbols:** AcceptedOperationPlan; ConfiguredPlanAuthority; require_human_approval.
- **Role:** Independent plan authorization binding decision to inputs.
- **UI use:** Owner-sourced plan acceptance and authority state.
- **Boundary:** Do not move these checks into React.
- **Basis:** Definitions used by inspected plan_revalidation and manifest.

## `synapse/experiments/gold/stage10/approval.py`

- **Symbols:** grant_approval; revoke_approval; RunApprovalPolicy.review_request/validate.
- **Role:** Operator grants, expiry/revocation, and recorded review receipts.
- **UI use:** Grant/revoke and display the basis.
- **Boundary:** Existing timed grants do not automatically implement refusal or single use.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/stage10/plan_revalidation.py`

- **Symbols:** authorize_first_side_effect; CurrentPlanState; SideEffectAuthorization.
- **Role:** Checks current repository/snapshot/policy/admission/compatibility and human approval before the first effect.
- **UI use:** Confirmed admission source, not a button handler.
- **Boundary:** Durable once-binding is new semantics, not an existing constructor side effect.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/stage10/context.py`

- **Symbols:** Stage 10 context.
- **Role:** Assembles permitted context, separating task from local information.
- **UI use:** Correct source attachment to the executor.
- **Boundary:** Protected local information cannot become a public model request.
- **Basis:** Ownership manifest and imported bridge contract.

## `synapse/experiments/gold/stage10/context_codec.py`

- **Symbols:** encode_canonical; decode_canonical; task/information projections.
- **Role:** Exact context serialization and separate projections.
- **UI use:** Deliver task/information without rewriting source records.
- **Boundary:** New UI JSON Schema does not replace the domain codec.
- **Basis:** Approval/C1 imports and manifest.

## `synapse/experiments/gold/stage10/record_store.py`

- **Symbols:** FileStage10RecordStore.
- **Role:** Persists and exactly rereads Stage 10 records.
- **UI use:** Authorized-root plan/context read projections.
- **Boundary:** No GUI mutation ticket or direct write access.
- **Basis:** Confirmed stage10_composition construction.

## `synapse/experiments/gold/stage10/worker_context_adapter.py`

- **Symbols:** Stage10WorkerContextAdapter; create_worker_invocation; coding_agent_capabilities.
- **Role:** Converts retained authorized context into WorkerInvocation, respecting local-edit/memory profiles.
- **UI use:** Primary task-delivery boundary for the selected agent.
- **Boundary:** UI cannot bypass context by constructing invocations directly.
- **Basis:** worker_bridge/stage10_composition calls.

## `synapse/experiments/gold/stage10/worker_transport.py`

- **Symbols:** WorkerInvocation; WorkerCandidateResult; WorkerCandidateStatus; WorkerDeliveryEvidence.
- **Role:** Coding transport contracts around the general agent port.
- **UI use:** Central diff, touched_files, and delivery display.
- **Boundary:** Retains its coding contract; arbitrary JSON is not a replacement.
- **Basis:** worker_bridge imports/fields.

## `synapse/experiments/gold/stage10/delivery_verification.py`

- **Symbols:** Delivery verification.
- **Role:** Checks exact task/information and delivery receipts.
- **UI use:** Distinguish sent, delivered, and verified.
- **Boundary:** Calling execute alone does not prove exact delivery.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/runner/delivery.py`

- **Symbols:** Attempt authorization/delivery path.
- **Role:** Connects attempt phase, retained plan/context, and authorize_first_side_effect.
- **UI use:** Attempt execution transitions.
- **Boundary:** The new UI transport cannot create another lifecycle.
- **Basis:** Located authorize_first_side_effect call and manifest.

## `synapse/worker/local_edits.py`

- **Symbols:** LOCAL_EDIT_PROFILES; public_messages; LOCAL_EDIT_CORRECTION.
- **Role:** Local-edit protocol/public model messages used by StdioAgentAdapter.
- **UI use:** Protocol instructions and visible-proposal/core-processing binding.
- **Boundary:** Not a general user-skills manager; never extend public_messages with local memory.
- **Basis:** stdio_adapter calls and package tree.

## `synapse/worker/candidate_materializer.py`

- **Symbols:** Coding-candidate materialization.
- **Role:** Converts raw proposals to verifiable candidates and interprets local-edit results.
- **UI use:** Proposal and actual candidate-form inspection.
- **Boundary:** Do not apply changes directly through Monaco.
- **Basis:** Gold documentation establishes role; tree confirms file.

## `synapse/worker/contract.py`

- **Symbols:** ExternalCodingWorkerResult; ExternalWorkerStatus; ExternalWorkerUsage.
- **Role:** Coding result crossing the existing C1 boundary.
- **UI use:** Coding workflow states and actual usage.
- **Boundary:** A patch candidate is not a final accepted outcome.
- **Basis:** C1 imports and package tree.

## `synapse/worker/input_contract.py`

- **Symbols:** Coding worker input contract.
- **Role:** Coding-executor input representation boundary.
- **UI use:** Account for it when mapping future prompt/task editor actions.
- **Boundary:** No concrete GUI method is established here.
- **Basis:** Tree confirms file; full public interface not established in map.
