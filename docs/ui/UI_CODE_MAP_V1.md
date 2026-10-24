# UI source-code integration map

Version 1.0. Complete English Appendix D of the
[bridge specification](SYNAPSE_UI_BRIDGE_SPEC_V1.md).

This map connects UI areas and specification sections 2 and 11–14 to existing
files, symbols, responsibilities, and boundaries. Integrate through those
mechanisms rather than building competing ones (MAP-4/5).

Navigation is project-relative path → symbol → role → permitted route. Source
navigation does not depend on repository URLs, commit IDs, or line numbers.
Protocol, configuration, run, and evidence identities retain their own versions:
those are execution contracts, not navigation metadata.

Entries distinguish definition/call evidence, manifest assignments, and
documentation-only relationships. Proposed external operations remain separate;
a path or symbol does not mean a UI button is implemented. The scope includes
the agent package, both LLM contexts, coding-task preparation/verification, MCP,
memory/court ownership, evidence, canonical entries, and acceptance references.
Service internals are not direct UI APIs.

The supplied JSON pointer had the same 130 entries and was derivative, not an
independent contract. This edition preserves 122 non-test entries here and eight
acceptance entries in a separate
[development-only reference](development-reference/ACCEPTANCE_SOURCES.md).
Tests are not product integration points. They supply no UI imports, assets,
navigation targets, runtime data, startup hooks, or implementation behavior.
See [provenance](SOURCE_MANIFEST.md).

## D.1. What is connected

Keep five objects distinct: the UI machine adapter accepts commands and returns
projections; AgentAdapter executes external-agent tasks; ModelBroker/LLMGateway
use models within their own contexts; McpToolPort calls individual tools;
ProjectMemoryCourt/MemoryFactory connect to the existing memory owner. None
confers another's authority.

Route UI commands through the canonical machine API, approved composition,
domain owner, and required adapter. Never instantiate these classes in the
renderer. Machine handlers/schemas still need implementation; Python functions
are not ready-made IPC commands.

## D.2. Canonical chains

### D.2.1. External-agent connection and selection

```text
Operator configuration: profiles, preferred_profiles
 → agents/configuration.validate_configuration
 → verify_configuration_runtime
 → registry_from_configuration
 → built-in factory or load_admitted_adapter
 → AgentRegistry with separate CapabilityAdmission records
 → Gold agent_selection.select_coding_agent
 → frozen selection identity in run_inputs
```

capture_definition records supplied runtime/evidence and admitted capabilities;
it does not run acceptance and award itself successful admission.
discover_adapter_factories lists installed synapse.agent_adapters entry points
without automatic loading/admission.

### D.2.2. Gold task execution

```text
python -m synapse → synapse.__main__ → synapse.cli.main
 → project run/resume
 → runner_composition.execute_gold_project_run(court=ProjectMemoryCourt())
 → existing preparation, snapshot, admission, plan/context, retention
 → runner/delivery and stage10/plan_revalidation
 → Stage10WorkerContextAdapter
 → AgentBackedWorkerTransport.run
 → AgentExecutionPort.execute
 → selected AgentAdapter.execute
 → typed PatchCandidate
 → existing delivery verification, C1, Stage 12, publication, memory
```

Arrows describe boundary order, not necessarily direct adjacent function calls.
Detailed entries give actual roles. stage10_composition assembles the execution
port, transport, and context adapter; the UI does not duplicate that assembly.

### D.2.3. A local external agent's model request

```text
runner_composition → WorkerAccounting
 → StdioAgentAdapter with model_access
 → per-invocation ModelBroker
 → PublicConversation when local information is present
 → provider_http_exchange and capture
 → external or explicitly selected loopback Chat Completions endpoint
```

The broker uses a local HTTP port and per-invocation capability; agents do not
receive provider credentials. The no-network-port rule applies to the UI/core
bridge, not every existing Synapse network component. The broker is not a local
model installation/download manager. It requires explicit endpoint, model,
credential_env, timeout, and a nonempty credential; streaming and n≠1 are rejected.
Expose these restrictions honestly.

### D.2.4. Language-runtime LLM

```text
Canonical runtime → LLMBackend in builtins.py
 → LLMGateway → privacy policy → GeminiProvider
```

This is an existing separate use context, not a newly invented bypass for agent
operations. Neither gateway nor broker substitutes for the other. Shared-looking
forms must preserve target and accounting semantics.

### D.2.5. Memory-bound tools

```text
MemoryConfiguration.tools → MemoryFactory.gateway
 → tools/Gateway.invoke: identity, STARTED/RESULT/REJECTED, repeat policy
 → McpToolTransport.call → agents/McpToolPort.invoke
 → official MCP client and admitted server
```

Effect truth belongs to gateway/domain records, not transport. MCP tools,
ACP/A2A agents, and API models are distinct connection kinds.

### D.2.6. Project memory and policy changes

```text
CLI project run/resume
 → ProjectMemoryCourt supplied to execute_gold_project_run
 → ProjectMemoryCourt.consolidate
 → existing MemoryOwner/MemoryFactory and unified court chain

CLI memory reassess → _handle_memory → read_memory_configuration
 → MemoryFactory.reassess → court/consolidation.reassess
 → existing report/decision/boundaries/retention → MemoryOwner.rebind
```

The port preserves dependency direction: inject it at Gold composition rather
than importing memory internals into Gold for a new window.

## D.3. Required settings grounded in these files

**Agents.** Expose profile_id, agent_id/version, adapter_id/version, factory,
transport, capabilities versus admitted subset, media/output profiles,
local-information policy, effects, runtime identity, isolation, network, and
limits. Separate saved definition, runtime availability, identity check,
admission evidence, and task eligibility. Assembly configuration contains factory,
profile, native, runtime_files, distributions, runtime_environment, and admission.
Runtime/version changes require rechecking and can invalidate use. Settings cannot
rebind a frozen run. CONN-1–6/AGT-1–6 define administration; new read/acceptance
surfaces use existing validation/assembly as their foundation.

**Models.** Separate language-runtime settings from a selected agent's
model_access. Broker fields are model, endpoint, credential_env,
timeout_seconds, with `synapse.agent.model-broker/v1`. AgentProfile provider_name
differs from ModelConnection's wire-endpoint provider. Compatible Chat Completions
servers must satisfy the exact contract. This establishes no ready Ollama/llama.cpp
manager, catalogue, downloads, streaming, keyless mode, or automatic fallback.
Those need controlled runtime/probe contracts, not simulated connection success.

**Tools and instructions.** Tool configuration uses contracts, gateway, and
MCP transport. prompt_template is substitution, not a skills registry. Text
cannot grant authority; MCP servers are not automatically agents. INST-1–3
define required instruction administration; registry ownership and acceptance
records remain open.

**Memory, limits, and chart.** Read active policy from bound_configuration,
not arbitrary memory.json. PARAMETERS/configuration supply fields;
MemoryFactory/court supply acceptance; reports/chain supply reasons. Distinguish
agent ResourceBudget.memory_bytes, raw_capacity trace count, and proposed
project disk/cache limits. Never connect all controls to one number. Stage 15
measurements do not automatically establish hard quotas or habit utility.

## D.4. Integration limitations

1. No special built-in Mini route: the inspected worker package has no
   mini_adapter.py; documentation records replacement with admitted profiles.
   Mini or another coding agent needs an explicitly admitted compatible adapter,
   not another runner.
2. Adapter transport is not Gold domain compatibility. Docling returns
   DocumentObservationSet; the coding consumer requires PatchCandidate,
   repository.edit, LOCAL_ONLY. Document integration needs its domain in the same
   lifecycle, not fake patches or a separate document runner.
3. Local information remains local. Separate executor inputs do not permit
   external transmission; the neutral PROVIDER_VISIBLE_EXPLICIT enum cannot
   weaken Gold's strict profile.
4. Existing process supervision remains. Tauri Jobs/systemd must cover real
   descendants/MCP servers, not delete runtime.py controls or substitute renderer
   shell.execute.
5. UI support for Windows/Linux does not prove equal agent sandbox availability.
   Verify actual Bubblewrap/libseccomp/prlimit, OCI, and TRUSTED_PROCESS profiles.
   OCI clients/containers are not simply Tauri descendant PIDs.
6. Read-only contracts are not arbitrary constructor calls. Some factories
   create/open writable stores or recover boundaries; readers need established
   read boundaries without assembling execution just to show a panel.
7. Stage 15 events are retained assessments, not an operational live stream.
   Use owner projections for live state and preserve source-specific IDs/cursors.
8. Agent STDIO is one request/result with its own schema/limit; UI STDIO is a
   negotiated long-lived command/event/snapshot session. Equal framing does not
   make contracts interchangeable.

## D.5. UI integration mapping

| UI area | Code basis | Remaining UI contract |
|---|---|---|
| Agents | configuration, registry, contracts, policy | Read/edit, controlled probes/admission, versioning, machine methods |
| Models | model_broker, llm/gateway, WorkerAccounting | Role-specific administration, secrets, local runtime, capabilities |
| Tools | ToolConfiguration → Gateway → McpToolTransport → McpToolPort | Definition save/validation administration |
| Skills/instructions | prompt_template as low-level support; habits/contracts separate | Versioned user registry and owner |
| Task creation/execution | CLI, runner composition, run_inputs, selection, Stage 10 | Machine handlers, durable task acceptance, task-wide control |
| Documents/diff | outputs, worker_bridge, C1EvidenceContext; separate project reader | Document-domain consumer and authorized general reads |
| Permissions | stage10_composition approval; separate revalidation | Refusal and durable once-binding, beyond timed grants |
| Memory | ProjectMemoryCourt, owner/factory, policy/configuration/court | Isolated preview, command identity, math invariants, byte quota/cache |
| Log/metrics | InvocationStore, Gateway, court chain, Stage 15, capture/reconciliation | Authorized reads, all source cursors, owner live snapshots |
| Lifecycle | Outer Tauri group, agents/runtime, MCP, owner recovery | Platform shutdown/old-executor acceptance without a second writer |

SEC-8/acceptance case 27 map specifically to separate task/local_information in
worker_bridge/STDIO and PublicConversation's public-only external requests.
These are not two ways to send memory to an external LLM. This map does not
replace visual requirements: sliders, equalizer, four axes, limits, and measured
usage remain mandatory UI behavior.

## D.6. Detailed file map

Each product chapter preserves symbols, role, UI use, boundary, and evidence
basis. The original 130 entries remain translated: 122 non-test references below
and eight developer-only acceptance references separately. Their usage field is
"Acceptance use", never "UI use". One stale acceptance reference is identified
there and in the progress register.

| Chapter | Entries |
|---|---:|
| [Canonical application and roots](code-map/01-application.md) | 12 |
| [Agents and extensions](code-map/02-agents.md) | 18 |
| [Tasks and local information](code-map/03-task-context.md) | 19 |
| [Models and accounting](code-map/04-models.md) | 16 |
| [MCP and instructions](code-map/05-tools-instructions.md) | 7 |
| [Memory and learning](code-map/06-memory.md) | 25 |
| [Evidence and replay](code-map/07-evidence.md) | 22 |
| [Documentation and packaging](code-map/08-reference.md) | 3 |

The eight [acceptance references](development-reference/ACCEPTANCE_SOURCES.md)
belong exclusively to development and acceptance planning, outside the product
integration map.

## D.7. File index

The [product file and symbol index](CODE_MAP_INDEX.md) links 122 non-test
references. The development-only document contains the other eight paths and
their symbol index. Together they preserve every original D.7 entry. Their size
is a documentation cross-check, not product acceptance evidence.
