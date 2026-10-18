# Source file and symbol index

Version 1.0. Appendix D.7 of the [code map](UI_CODE_MAP_V1.md).

This index covers 122 non-test references. The eight original acceptance entries are preserved only in the [development-only acceptance sources](development-reference/ACCEPTANCE_SOURCES.md), outside product integration.

| File and detailed entry | Symbols / integration point |
|---|---|
| [synapse/__main__.py](code-map/01-application.md#synapse__main__py) | Delegation to cli.main. |
| [synapse/cli.py](code-map/01-application.md#synapseclipy) | main; _handle_run; _handle_resume; _handle_memory; handle_project_connect; handle_project_status. |
| [synapse/application.py](code-map/01-application.md#synapseapplicationpy) | DurableRunRequest; DurableResumeRequest; execute_durable_run; execute_durable_resume; execute_file; execute_source. |
| [synapse/durable_cognitive.py](code-map/01-application.md#synapsedurable_cognitivepy) | read_cognitive_session; replay_cognitive_history; reproduce_cognitive_session. |
| [governance/stage4_ownership_v2.json](code-map/01-application.md#governancestage4_ownership_v2json) | owners; owner_components; owner_adapters; composition_roots; composition_dependencies. |
| [synapse/experiments/gold/runner_composition.py](code-map/01-application.md#synapseexperimentsgoldrunner_compositionpy) | create_gold_run_composition; execute_gold_project_run. |
| [synapse/experiments/gold/stage10_composition.py](code-map/01-application.md#synapseexperimentsgoldstage10_compositionpy) | create_stage10_production_composition; require_stage10_production_composition; execute_approval_action. |
| [synapse/experiments/gold/knowledge_environment.py](code-map/01-application.md#synapseexperimentsgoldknowledge_environmentpy) | ConnectProjectRequest; ProjectStatusRequest; execute_connect_project; execute_project_status. |
| [synapse/experiments/gold/source_ingestion.py](code-map/01-application.md#synapseexperimentsgoldsource_ingestionpy) | execute_source_ingestion; execute_source_recall. |
| [synapse/experiments/gold/library_composition.py](code-map/01-application.md#synapseexperimentsgoldlibrary_compositionpy) | Composition root for library.py. |
| [synapse/experiments/gold/replay_composition.py](code-map/01-application.md#synapseexperimentsgoldreplay_compositionpy) | Composition root for replay.py. |
| [synapse/experiments/gold/run_attempt_world.py](code-map/01-application.md#synapseexperimentsgoldrun_attempt_worldpy) | Composition root for run_environment.py. |
| [synapse/agents/__init__.py](code-map/02-agents.md#synapseagents__init__py) | Public exports: AgentAdapter, AgentAdapterFactory, AgentExecutionPort, and contracts. |
| [synapse/agents/configuration.py](code-map/02-agents.md#synapseagentsconfigurationpy) | profile_from_dict; validate_configuration; verify_configuration_runtime; registry_from_configuration; capture_definition; required_runtime_files; distribution_identity; runtime_environment. |
| [synapse/agents/registry.py](code-map/02-agents.md#synapseagentsregistrypy) | AgentAdapter; AgentAdapterFactory; CapabilityAdmission; AgentRequirements; AgentRegistry.resolve/select; discover_adapter_factories; load_admitted_adapter; ENTRY_POINT_GROUP. |
| [synapse/agents/contracts.py](code-map/02-agents.md#synapseagentscontractspy) | AgentProfile; AgentExecutionRequest/Result; AgentArtifactInput; AgentRuntimeContext; AgentDeliveryEvidence; LocalInformationPolicy; AgentExecutionStatus. |
| [synapse/agents/policy.py](code-map/02-agents.md#synapseagentspolicypy) | ResourceBudget; RuntimePolicy; IsolationKind; AgentFailureCode. |
| [synapse/agents/execution.py](code-map/02-agents.md#synapseagentsexecutionpy) | AgentExecutionPort.execute; _execute_bound; restore; cancel. |
| [synapse/agents/retention.py](code-map/02-agents.md#synapseagentsretentionpy) | InvocationStore.claim; executing; finish; event; restore; cancel. |
| [synapse/agents/runtime.py](code-map/02-agents.md#synapseagentsruntimepy) | run_process; ProcessObservation; _command; _terminate_tree. |
| [synapse/agents/stdio_adapter.py](code-map/02-agents.md#synapseagentsstdio_adapterpy) | StdioAdapterFactory; StdioAgentConfig; StdioAgentAdapter.execute; STDIO_REQUEST_V1/STDIO_RESPONSE_V1. |
| [synapse/agents/docling_adapter.py](code-map/02-agents.md#synapseagentsdocling_adapterpy) | DoclingAdapterFactory.create; extract_documents. |
| [synapse/agents/acp_adapter.py](code-map/02-agents.md#synapseagentsacp_adapterpy) | AcpAdapterFactory.create; execute_acp; ScopedClient. |
| [synapse/agents/a2a_adapter.py](code-map/02-agents.md#synapseagentsa2a_adapterpy) | A2AAdapterFactory; A2AAgentAdapter.execute/resume. |
| [synapse/agents/native_worker.py](code-map/02-agents.md#synapseagentsnative_workerpy) | main; native_response. |
| [synapse/agents/model_broker.py](code-map/02-agents.md#synapseagentsmodel_brokerpy) | ModelConnection; model_connection; ModelBroker; PublicConversation; ModelAccountingPort. |
| [synapse/agents/mcp_tools.py](code-map/02-agents.md#synapseagentsmcp_toolspy) | McpToolBinding; McpToolObservation; McpToolPort.invoke. |
| [synapse/agents/outputs.py](code-map/02-agents.md#synapseagentsoutputspy) | OutputRegistry; OutputCodec; PatchCandidateCodec; DocumentObservationCodec; SchemaOutputCodec; validate_json_schema. |
| [synapse/agents/codec.py](code-map/02-agents.md#synapseagentscodecpy) | canonical_bytes; digest; decode_json; result_from_dict. |
| [synapse/agents/worker_bridge.py](code-map/02-agents.md#synapseagentsworker_bridgepy) | AgentBackedWorkerTransport.run. |
| [synapse/experiments/gold/run_inputs.py](code-map/03-task-context.md#synapseexperimentsgoldrun_inputspy) | freeze_gold_inputs; reopen_frozen_inputs; EXPERIMENT_INPUT_SCHEMA_V4. |
| [synapse/experiments/gold/agent_selection.py](code-map/03-task-context.md#synapseexperimentsgoldagent_selectionpy) | select_coding_agent. |
| [synapse/experiments/gold/stage10/task_contract.py](code-map/03-task-context.md#synapseexperimentsgoldstage10task_contractpy) | GoverningTaskContract. |
| [synapse/experiments/gold/stage10/intent.py](code-map/03-task-context.md#synapseexperimentsgoldstage10intentpy) | IntentCandidate; EffectKind; EffectDisposition. |
| [synapse/experiments/gold/stage10/planning.py](code-map/03-task-context.md#synapseexperimentsgoldstage10planningpy) | OperationPlanCandidate. |
| [synapse/experiments/gold/stage10/plan_authority.py](code-map/03-task-context.md#synapseexperimentsgoldstage10plan_authoritypy) | AcceptedOperationPlan; ConfiguredPlanAuthority; require_human_approval. |
| [synapse/experiments/gold/stage10/approval.py](code-map/03-task-context.md#synapseexperimentsgoldstage10approvalpy) | grant_approval; revoke_approval; RunApprovalPolicy.review_request/validate. |
| [synapse/experiments/gold/stage10/plan_revalidation.py](code-map/03-task-context.md#synapseexperimentsgoldstage10plan_revalidationpy) | authorize_first_side_effect; CurrentPlanState; SideEffectAuthorization. |
| [synapse/experiments/gold/stage10/context.py](code-map/03-task-context.md#synapseexperimentsgoldstage10contextpy) | Stage 10 context. |
| [synapse/experiments/gold/stage10/context_codec.py](code-map/03-task-context.md#synapseexperimentsgoldstage10context_codecpy) | encode_canonical; decode_canonical; task/information projections. |
| [synapse/experiments/gold/stage10/record_store.py](code-map/03-task-context.md#synapseexperimentsgoldstage10record_storepy) | FileStage10RecordStore. |
| [synapse/experiments/gold/stage10/worker_context_adapter.py](code-map/03-task-context.md#synapseexperimentsgoldstage10worker_context_adapterpy) | Stage10WorkerContextAdapter; create_worker_invocation; coding_agent_capabilities. |
| [synapse/experiments/gold/stage10/worker_transport.py](code-map/03-task-context.md#synapseexperimentsgoldstage10worker_transportpy) | WorkerInvocation; WorkerCandidateResult; WorkerCandidateStatus; WorkerDeliveryEvidence. |
| [synapse/experiments/gold/stage10/delivery_verification.py](code-map/03-task-context.md#synapseexperimentsgoldstage10delivery_verificationpy) | Delivery verification. |
| [synapse/experiments/gold/runner/delivery.py](code-map/03-task-context.md#synapseexperimentsgoldrunnerdeliverypy) | Attempt authorization/delivery path. |
| [synapse/worker/local_edits.py](code-map/03-task-context.md#synapseworkerlocal_editspy) | LOCAL_EDIT_PROFILES; public_messages; LOCAL_EDIT_CORRECTION. |
| [synapse/worker/candidate_materializer.py](code-map/03-task-context.md#synapseworkercandidate_materializerpy) | Coding-candidate materialization. |
| [synapse/worker/contract.py](code-map/03-task-context.md#synapseworkercontractpy) | ExternalCodingWorkerResult; ExternalWorkerStatus; ExternalWorkerUsage. |
| [synapse/worker/input_contract.py](code-map/03-task-context.md#synapseworkerinput_contractpy) | Coding worker input contract. |
| [synapse/llm/gateway.py](code-map/04-models.md#synapsellmgatewaypy) | LLMGatewayConfig; config_from_env; LLMGateway.complete; build_gateway_from_env. |
| [synapse/builtins.py](code-map/04-models.md#synapsebuiltinspy) | LLMBackend. |
| [synapse/llm/gemini.py](code-map/04-models.md#synapsellmgeminipy) | GeminiProvider; parse_gemini_usage. |
| [synapse/llm/models.py](code-map/04-models.md#synapsellmmodelspy) | LLMResult; LLMUsage; LLMProviderStatus; LLMTokenStatus. |
| [synapse/llm/privacy.py](code-map/04-models.md#synapsellmprivacypy) | PrivacyContext; PrivacyPolicyError; enforce_privacy_for_tier. |
| [synapse/llm/http_transport.py](code-map/04-models.md#synapsellmhttp_transportpy) | provider_http_exchange; MAX_PROVIDER_BODY_BYTES. |
| [synapse/llm/capture.py](code-map/04-models.md#synapsellmcapturepy) | ProviderCapturePort; WorkerCapturePort; response_inventory; inspect_response_inventory. |
| [synapse/llm/__init__.py](code-map/04-models.md#synapsellm__init__py) | Exports from gateway, models, and privacy. |
| [synapse/llm/smoke.py](code-map/04-models.md#synapsellmsmokepy) | Diagnostic smoke module. |
| [synapse/experiments/gold/stage15/worker_accounting.py](code-map/04-models.md#synapseexperimentsgoldstage15worker_accountingpy) | selected_model_access; create_worker_accounting; WorkerAccounting.open_capture. |
| [synapse/experiments/gold/stage15/capture_store.py](code-map/04-models.md#synapseexperimentsgoldstage15capture_storepy) | CaptureStore. |
| [synapse/experiments/gold/stage15/telemetry.py](code-map/04-models.md#synapseexperimentsgoldstage15telemetrypy) | UsageProfile; Phase; reference; telemetry types. |
| [synapse/experiments/gold/stage15/resource_accounting.py](code-map/04-models.md#synapseexperimentsgoldstage15resource_accountingpy) | ResourceEvidence. |
| [synapse/experiments/gold/stage15/reconciliation.py](code-map/04-models.md#synapseexperimentsgoldstage15reconciliationpy) | reconcile_run_telemetry; reconstruct_call_records. |
| [synapse/experiments/gold/runner/budgets.py](code-map/04-models.md#synapseexperimentsgoldrunnerbudgetspy) | preparation_budget_failure. |
| [synapse/resource_usage.py](code-map/04-models.md#synapseresource_usagepy) | recording_resources; observed_operation; measure_operation; active_recorder. |
| [synapse/memory_consolidation/tools/contracts.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolscontractspy) | ToolConfiguration; ToolContract; parse_tool_configuration. |
| [synapse/memory_consolidation/tools/mcp_transport.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolsmcp_transportpy) | McpToolTransport.call; close. |
| [synapse/memory_consolidation/tools/gateway.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolsgatewaypy) | Gateway.invoke; recorded; recorded_outcome; records; head. |
| [synapse/memory_consolidation/tools/semantics.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolssemanticspy) | interpret; repeat_admissible. |
| [synapse/memory_consolidation/tools/journal.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolsjournalpy) | ChainedLog; GatewayIntegrityError. |
| [synapse/memory_consolidation/tools/evidence.py](code-map/05-tools-instructions.md#synapsememory_consolidationtoolsevidencepy) | EvidenceStore. |
| [synapse/prompt_template.py](code-map/05-tools-instructions.md#synapseprompt_templatepy) | placeholders; render. |
| [synapse/memory_consolidation/project_port.py](code-map/06-memory.md#synapsememory_consolidationproject_portpy) | ProjectMemoryCourt.consolidate. |
| [synapse/memory_consolidation/configuration.py](code-map/06-memory.md#synapsememory_consolidationconfigurationpy) | MemoryConfiguration; KnowledgePolicy; parse_memory_configuration; read_memory_configuration. |
| [synapse/memory_consolidation/policy.py](code-map/06-memory.md#synapsememory_consolidationpolicypy) | PARAMETERS; DECISION_RULES; resolve_parameters; policy_identity; signal; trust_update; sprt_step; sprt_decision; cs_radius. |
| [synapse/memory_consolidation/owner.py](code-map/06-memory.md#synapsememory_consolidationownerpy) | MemoryOwner.bound_configuration; bound_digest; state; applied; read_report; register_session; rebind. |
| [synapse/memory_consolidation/factory.py](code-map/06-memory.md#synapsememory_consolidationfactorypy) | MemoryFactory.open_session; memory_now; reassess; trial; forget; restore; resolve_memory. |
| [synapse/memory_consolidation/session.py](code-map/06-memory.md#synapsememory_consolidationsessionpy) | MemorySession; ReplaySession; ReproductionSession. |
| [synapse/memory_consolidation/court/consolidation.py](code-map/06-memory.md#synapsememory_consolidationcourtconsolidationpy) | consolidate; consolidate_exact; reassess; _reassessed. |
| [synapse/memory_consolidation/court/reassessment.py](code-map/06-memory.md#synapsememory_consolidationcourtreassessmentpy) | reassess_bases; lost_bases; republication. |
| [synapse/memory_consolidation/court/decide.py](code-map/06-memory.md#synapsememory_consolidationcourtdecidepy) | decide; _reassess. |
| [synapse/memory_consolidation/court/pool.py](code-map/06-memory.md#synapsememory_consolidationcourtpoolpy) | assess; merge_pool; independence_of. |
| [synapse/memory_consolidation/court/births.py](code-map/06-memory.md#synapsememory_consolidationcourtbirthspy) | pool_stage; make_birth; typed_check. |
| [synapse/memory_consolidation/learning/provenance.py](code-map/06-memory.md#synapsememory_consolidationlearningprovenancepy) | closure; relation; independent_witnesses. |
| [synapse/memory_consolidation/legitimacy.py](code-map/06-memory.md#synapsememory_consolidationlegitimacypy) | GoldLegitimacy. |
| [synapse/memory_consolidation/court/retention.py](code-map/06-memory.md#synapsememory_consolidationcourtretentionpy) | retention_pass; pending_acts; apply_acts. |
| [synapse/memory_consolidation/operator_acts.py](code-map/06-memory.md#synapsememory_consolidationoperator_actspy) | forget; restore. |
| [synapse/experiments/gold/project_memory_store.py](code-map/06-memory.md#synapseexperimentsgoldproject_memory_storepy) | ProjectMemoryStore. |
| [synapse/experiments/gold/project_court.py](code-map/06-memory.md#synapseexperimentsgoldproject_courtpy) | court_chain; append_decision; establish_tail. |
| [synapse/experiments/gold/project_agents.py](code-map/06-memory.md#synapseexperimentsgoldproject_agentspy) | Element-owner job lifecycle. |
| [synapse/experiments/gold/project_learning.py](code-map/06-memory.md#synapseexperimentsgoldproject_learningpy) | Four memory layers and consolidated assertions. |
| [synapse/experiments/gold/learned_habit_profile.py](code-map/06-memory.md#synapseexperimentsgoldlearned_habit_profilepy) | Learned-behavior profile. |
| [synapse/experiments/gold/learned_habit_lifecycle.py](code-map/06-memory.md#synapseexperimentsgoldlearned_habit_lifecyclepy) | Learned-behavior admission lifecycle. |
| [synapse/experiments/gold/memory_event_storage.py](code-map/06-memory.md#synapseexperimentsgoldmemory_event_storagepy) | MemoryEventStorage. |
| [synapse/experiments/gold/memory_blocks.py](code-map/06-memory.md#synapseexperimentsgoldmemory_blockspy) | Component of memory_event_storage.py. |
| [synapse/experiments/gold/memory_event_budget.py](code-map/06-memory.md#synapseexperimentsgoldmemory_event_budgetpy) | Component of memory_event_storage.py. |
| [synapse/experiments/gold/project_memory_selection.py](code-map/06-memory.md#synapseexperimentsgoldproject_memory_selectionpy) | select_run_knowledge; require_run_memory_selection. |
| [synapse/experiments/gold/runner/controller.py](code-map/07-evidence.md#synapseexperimentsgoldrunnercontrollerpy) | GoldRunController. |
| [synapse/experiments/gold/runner/models.py](code-map/07-evidence.md#synapseexperimentsgoldrunnermodelspy) | GoldRunConfig; GoldRunManifest. |
| [synapse/experiments/gold/runner/records.py](code-map/07-evidence.md#synapseexperimentsgoldrunnerrecordspy) | RunRecordStore; RecordKind. |
| [synapse/experiments/gold/runner/run_recovery.py](code-map/07-evidence.md#synapseexperimentsgoldrunnerrun_recoverypy) | RunRecordRecovery.session; RunRecordSession. |
| [synapse/experiments/gold/runner/run_progress.py](code-map/07-evidence.md#synapseexperimentsgoldrunnerrun_progresspy) | AttemptProgressPhase; load_attempt_progress; require_progress_payload. |
| [synapse/experiments/gold/runner/state_machine.py](code-map/07-evidence.md#synapseexperimentsgoldrunnerstate_machinepy) | load_run_state. |
| [synapse/experiments/gold/runner/vocabulary.py](code-map/07-evidence.md#synapseexperimentsgoldrunnervocabularypy) | GoldRunFailureCode; AttemptOutcome; closed outcomes/decisions. |
| [synapse/experiments/gold/runner/c1_boundary.py](code-map/07-evidence.md#synapseexperimentsgoldrunnerc1_boundarypy) | C1AttemptBoundary; C1EvidenceContext.read; read_c1_verification_evidence. |
| [synapse/experiments/swebench/gold_runner.py](code-map/07-evidence.md#synapseexperimentsswebenchgold_runnerpy) | run_gold_attempt. |
| [synapse/experiments/swebench/gold_attempt_writer.py](code-map/07-evidence.md#synapseexperimentsswebenchgold_attempt_writerpy) | GoldAttemptWriter. |
| [synapse/experiments/gold/stage12/outcome.py](code-map/07-evidence.md#synapseexperimentsgoldstage12outcomepy) | StructuredOutcome; final-status matrix. |
| [synapse/experiments/gold/stage12/verification.py](code-map/07-evidence.md#synapseexperimentsgoldstage12verificationpy) | Domain verification of C1/C2, plan, and binding. |
| [synapse/experiments/gold/stage13/publication.py](code-map/07-evidence.md#synapseexperimentsgoldstage13publicationpy) | Independent publication authority. |
| [synapse/experiments/gold/stage13/publication_store.py](code-map/07-evidence.md#synapseexperimentsgoldstage13publication_storepy) | Atomic write sets and committed visibility. |
| [synapse/experiments/gold/stage14/reconstruction.py](code-map/07-evidence.md#synapseexperimentsgoldstage14reconstructionpy) | Physical lineage reconstruction. |
| [synapse/experiments/gold/stage14/read_traversal.py](code-map/07-evidence.md#synapseexperimentsgoldstage14read_traversalpy) | Bounded read traversal. |
| [synapse/experiments/gold/stage15/events.py](code-map/07-evidence.md#synapseexperimentsgoldstage15eventspy) | GoldEvent; EventStream.page; open_event_stream. |
| [synapse/experiments/gold/stage15/run_observability.py](code-map/07-evidence.md#synapseexperimentsgoldstage15run_observabilitypy) | observe_completed_run; inspect_observability. |
| [synapse/experiments/gold/replay.py](code-map/07-evidence.md#synapseexperimentsgoldreplaypy) | Replay-lifecycle owner. |
| [synapse/experiments/gold/replay_vm_adapter.py](code-map/07-evidence.md#synapseexperimentsgoldreplay_vm_adapterpy) | Replay-to-VM adapter. |
| [synapse/golden_replay.py](code-map/07-evidence.md#synapsegolden_replaypy) | replay_mock_artifact; CacheOnlyLLMBackend; DeterministicReplayError; ReplayArtifactError. |
| [synapse/debugger_core.py](code-map/07-evidence.md#synapsedebugger_corepy) | ForkRegistry; GoldenArtifactTraceAdapter; find_trace_divergence. |
| [docs/UNIVERSAL_AGENT_EXECUTION.md](code-map/08-reference.md#docsuniversal_agent_executionmd) | Integration document. |
| [docs/GOLD_KNOWLEDGE_INGESTION.md](code-map/08-reference.md#docsgold_knowledge_ingestionmd) | Gold/source-knowledge document. |
| [pyproject.toml](code-map/08-reference.md#pyprojecttoml) | project.dependencies; optional-dependencies; project.scripts. |
