# Agent package and extension points

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/agents/__init__.py`

- **Symbols:** Public exports: AgentAdapter, AgentAdapterFactory, AgentExecutionPort, and contracts.
- **Role:** Boundary of the general external-agent package.
- **UI use:** Public-type reference; administration uses the machine API.
- **Boundary:** Exported classes do not authorize renderer construction.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/configuration.py`

- **Symbols:** profile_from_dict; validate_configuration; verify_configuration_runtime; registry_from_configuration; capture_definition; required_runtime_files; distribution_identity; runtime_environment.
- **Role:** Validates synapse.agent.configuration/v1; binds factory/native, runtime files, dependencies, environment, and admission evidence; builds registry. Built-in factories: stdio, docling, acp, a2a.
- **UI use:** Profile drafts, definition/environment checks, and admission preparation.
- **Boundary:** capture_definition records supplied evidence, neither runs acceptance nor invents admission. Path/model/version/runtime changes can require a new profile/admission.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/registry.py`

- **Symbols:** AgentAdapter; AgentAdapterFactory; CapabilityAdmission; AgentRequirements; AgentRegistry.resolve/select; discover_adapter_factories; load_admitted_adapter; ENTRY_POINT_GROUP.
- **Role:** Discovers synapse.agent_adapters entry points, loads explicitly chosen plugins, selects admitted profiles by requirements. Tie-break: operator preference, then profile_id.
- **UI use:** Agent connections/selection. Third-party adapters implement profile and execute; factories implement create.
- **Boundary:** Installed, loaded, admitted, and task-eligible are separate facts. React cannot replace resolve.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/contracts.py`

- **Symbols:** AgentProfile; AgentExecutionRequest/Result; AgentArtifactInput; AgentRuntimeContext; AgentDeliveryEvidence; LocalInformationPolicy; AgentExecutionStatus.
- **Role:** Neutral profile/task types, separate local_information, artifacts, results, and accounting.
- **UI use:** Agent-card structure, connection fields, and execution facts.
- **Boundary:** COMPLETED describes the executor, not Gold acceptance. MCP/PROVIDER_VISIBLE_EXPLICIT enum values do not establish factories or permit protected Gold memory.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/policy.py`

- **Symbols:** ResourceBudget; RuntimePolicy; IsolationKind; AgentFailureCode.
- **Role:** Time, memory_bytes, CPU, process-count, stdout/stderr, output, and scratch bounds; NONE/LOCAL_BROKER/REMOTE_ENDPOINT network and isolation policies.
- **UI use:** Per-agent limits and unavailable-environment explanations.
- **Boundary:** memory_bytes is agent runtime memory, not project memory cache. LOCAL_BROKER requires TRUSTED_PROCESS.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/execution.py`

- **Symbols:** AgentExecutionPort.execute; _execute_bound; restore; cancel.
- **Role:** Invocation owner: adapter selection, input binding, evidence/artifact retention, execution, and typed-result validation.
- **UI use:** Invocation states/results; the single execution port for adapters.
- **Boundary:** Direct UI adapter calls bypass ownership. cancel requests invocation cancellation, not confirmed task termination.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/retention.py`

- **Symbols:** InvocationStore.claim; executing; finish; event; restore; cancel.
- **Role:** Durable invocation identity, SQLite terminal results, evidence, process events, and concurrent-execution exclusion.
- **UI use:** Run history, uncertain-outcome reconciliation, retained results.
- **Boundary:** Not a universal UI-command database; claim is not a read-only lookup.
- **Basis:** Definition/call in the supplied map.

## Invocation command and observation foundations

The pending P1 follow-up adds `invocation_commands.py` for durable invocation
cancellation receipts/status and `invocation_observation.py` for consistent
state snapshots and complete change ranges. They share retention.py's actual
SQLite transaction. Cancellation remains cooperative; neither component dispatches
an adapter or owns task stopping. Read-only opening cannot initialize a store.
These components are not yet exposed by the canonical machine application route.
They do not expand the set of authorized Gold composition roots. See the
[development ownership record](../DEVELOPMENT_NOTES.md#2026-10-19--owner-boundaries-and-incomplete-command-foundations).

## `synapse/agents/runtime.py`

- **Symbols:** run_process; ProcessObservation; _command; _terminate_tree.
- **Role:** Shared local-process supervisor: environment, sandbox, I/O, resource observation, termination.
- **UI use:** Executor diagnostics/limits and integration with application lifecycle.
- **Boundary:** Not the future Tauri Job/systemd profile. Bubblewrap, OCI, and TRUSTED_PROCESS differ; do not promise equivalent ready-made isolation on both OSes.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/stdio_adapter.py`

- **Symbols:** StdioAdapterFactory; StdioAgentConfig; StdioAgentAdapter.execute; STDIO_REQUEST_V1/STDIO_RESPONSE_V1.
- **Role:** Local agent with explicit argv/environment/profile, optional local-edit protocol and model_access; one framed request/result via supervisor.
- **UI use:** Connect a coding agent implementing this protocol, not merely a brand name.
- **Boundary:** Agent STDIO and persistent UI/core streams remain different despite four-byte lengths.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/docling_adapter.py`

- **Symbols:** DoclingAdapterFactory.create; extract_documents.
- **Role:** Versioned Docling-Agent 0.6.0 integration: PDF/images, explicit field schema, preinstalled models, typed source observations.
- **UI use:** Document-agent profile and extraction display.
- **Boundary:** READ_ONLY, network NONE, local information NOT_SUPPORTED, Bubblewrap/OCI only. Documents are not automatically coding Gold tasks.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/acp_adapter.py`

- **Symbols:** AcpAdapterFactory.create; execute_acp; ScopedClient.
- **Role:** ACP through a native child; read/change callbacks check workspace/approved scope and produce a patch candidate.
- **UI use:** ACP profile and limitations.
- **Boundary:** Private information unsupported; PATH_MODIFIED effect. Agent text/plans are not Synapse plans; ACP permissions cannot replace Gold grants.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/a2a_adapter.py`

- **Symbols:** A2AAdapterFactory; A2AAgentAdapter.execute/resume.
- **Role:** Remote A2A client: pinned AgentCard/endpoints, retained remote task ID, polling/cancellation confirmation; resume reconciles the old remote task.
- **UI use:** Remote-agent connection and known/unknown outcomes.
- **Boundary:** Local information forbidden. Without confirmed remote task ID, resending is not recovery. Current coding Gold requirements exclude this profile.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/native_worker.py`

- **Symbols:** main; native_response.
- **Role:** Internal Docling/ACP child driver; library output goes to stderr and one typed response is returned.
- **UI use:** Adapter-owned supervisor child, not an independent UI command.
- **Boundary:** Supporting subprocess entry, not a competing Gold entry. Do not remove by overgeneralizing NR-02.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/model_broker.py`

- **Symbols:** ModelConnection; model_connection; ModelBroker; PublicConversation; ModelAccountingPort.
- **Role:** Per-invocation loopback model broker; Synapse retains credentials, agent receives capability, capture records physical requests.
- **UI use:** model_access model/endpoint/credential_env/timeout, public-request restrictions, accounting.
- **Boundary:** Its local HTTP port is distinct from the forbidden UI network bridge. local_information restricts requests to declared public conversation. Nonempty credential required; stream=true and n≠1 rejected.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/mcp_tools.py`

- **Symbols:** McpToolBinding; McpToolObservation; McpToolPort.invoke.
- **Role:** Checks admitted MCP descriptor and input/output schemas; calls through the official client.
- **UI use:** Tool settings and admitted capabilities.
- **Boundary:** A tool is not an entire agent; this port neither plans tasks, admits Gold, nor publishes results.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/outputs.py`

- **Symbols:** OutputRegistry; OutputCodec; PatchCandidateCodec; DocumentObservationCodec; SchemaOutputCodec; validate_json_schema.
- **Role:** Typed code/document results, new-domain schemas, shape checks, and refusal of external JSON Schema resolution.
- **UI use:** Central diff/document views and future domain formats.
- **Boundary:** A new codec does not supply Gold domain verification. Built-in codecs cannot be replaced under the same schema_id.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/codec.py`

- **Symbols:** canonical_bytes; digest; decode_json; result_from_dict.
- **Role:** Encodes/restores neutral agent records.
- **UI use:** UI delivery preserving source-object identity.
- **Boundary:** Existing canonicalization is not automatically JCS. Never rehash historical records with a new transport codec.
- **Basis:** Definition/call in the supplied map.

## `synapse/agents/worker_bridge.py`

- **Symbols:** AgentBackedWorkerTransport.run.
- **Role:** Checks WorkerInvocation against retained context/plan/authorization, builds AgentExecutionRequest, calls the port, and translates the sole patch candidate into WorkerCandidateResult.
- **UI use:** Coding Gold integration with external-agent results.
- **Boundary:** Not a universal document-to-C1 bridge; never construct requests from arbitrary UI JSON.
- **Basis:** Definition/call in the supplied map.
