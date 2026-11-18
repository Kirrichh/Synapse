# Canonical application and composition roots

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/__main__.py`

- **Symbols:** Delegation to cli.main.
- **Role:** Module entry point, python -m synapse.
- **UI use:** Launch the bundled core from the shell.
- **Boundary:** Do not substitute native_worker or launch Gold from the renderer.
- **Basis:** Canonical-entry acceptance and specification.

## `synapse/cli.py`

- **Symbols:** main; _handle_run; _handle_resume; _handle_memory; handle_project_connect; handle_project_status.
- **Role:** Parses commands and delegates: project connect/status/learn/recall/run/resume, approve/revoke-approval, memory forget/restore/reassess/trial, and .syn execution.
- **UI use:** Register the future machine subcommand, reaching the same domain actions.
- **Boundary:** Machine mode is new; human-readable command stdout is not its protocol.
- **Basis:** Definition/call recorded in the supplied map.

## `synapse/application.py`

- **Symbols:** DurableRunRequest; DurableResumeRequest; execute_durable_run; execute_durable_resume; execute_file; execute_source.
- **Role:** Application operations for source execution and durable sessions imported by CLI.
- **UI use:** Run .syn and resume durable sessions.
- **Boundary:** Not a universal chat queue; only these application entries are established, not a complete public UI contract.
- **Basis:** Confirmed CLI imports/calls.

## `synapse/durable_cognitive.py`

- **Symbols:** read_cognitive_session; replay_cognitive_history; reproduce_cognitive_session.
- **Role:** Reads/replays durable cognitive sessions; MemoryFactory uses it to verify retained experience.
- **UI use:** Owner-mediated history, recovery, and memory verification.
- **Boundary:** Do not build a UI interpreter or replay as a new external effect.
- **Basis:** Calls confirmed by MemoryFactory.ports().

## `governance/stage4_ownership_v2.json`

- **Symbols:** owners; owner_components; owner_adapters; composition_roots; composition_dependencies.
- **Role:** Gold ownership and approved composition-root manifest.
- **UI use:** Determine permitted routes for future UI commands.
- **Boundary:** Being listed in owners does not make a module a composition root.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/runner_composition.py`

- **Symbols:** create_gold_run_composition; execute_gold_project_run.
- **Role:** Assembles one run controller/owners and canonical Gold run/resume; supplies agent registry and model-accounting owner.
- **UI use:** task.submit and supported resumption through a new machine handler.
- **Boundary:** The inspected Gold integration is for coding; arbitrary document runners or general chat queues are not established.
- **Basis:** Definition/call recorded in the supplied map.

## `synapse/experiments/gold/stage10_composition.py`

- **Symbols:** create_stage10_production_composition; require_stage10_production_composition; execute_approval_action.
- **Role:** Assembles FileStage10RecordStore, AgentExecutionPort, AgentBackedWorkerTransport, and Stage10WorkerContextAdapter; exposes operator grant/revoke.
- **UI use:** Approved Stage 10 integration with agents and approvals.
- **Boundary:** This composition's registry contains exactly one selected coding profile; grant/revoke are not once/refuse.
- **Basis:** Definition/call recorded in the supplied map.

## `synapse/experiments/gold/knowledge_environment.py`

- **Symbols:** ConnectProjectRequest; ProjectStatusRequest; execute_connect_project; execute_project_status.
- **Role:** Project connection/status root, integrating the existing knowledge world.
- **UI use:** Connect/open a project and inspect status.
- **Boundary:** Project status is not a file tree; project.tree.read needs an authorized read contract.
- **Basis:** CLI calls, mapped definitions, and manifest.

## `synapse/experiments/gold/source_ingestion.py`

- **Symbols:** execute_source_ingestion; execute_source_recall.
- **Role:** Root for ingesting verifiable source experience and recalling information within scope.
- **UI use:** Project sources, history, and permitted information retrieval.
- **Boundary:** Reading a document neither admits knowledge nor permits external transmission.
- **Basis:** CLI project learn/recall calls and manifest.

## `synapse/experiments/gold/library_composition.py`

- **Symbols:** Composition root for library.py.
- **Role:** Approved assembly of immutable program/behavior artifacts.
- **UI use:** Canonical library material references.
- **Boundary:** Not arbitrary filesystem access.
- **Basis:** Ownership manifest; a full read API is not established here.

## `synapse/experiments/gold/replay_composition.py`

- **Symbols:** Composition root for replay.py.
- **Role:** Approved Gold replay assembly.
- **UI use:** Owner-mediated replay and inspection.
- **Boundary:** Replay does not call an LLM again or launch an independent executor.
- **Basis:** Ownership manifest; detailed signatures require command-specific mapping.

## `synapse/experiments/gold/run_attempt_world.py`

- **Symbols:** Composition root for run_environment.py.
- **Role:** Assembles one attempt's world, snapshot, compatibility, and admission.
- **UI use:** Display attempt state from the existing run.
- **Boundary:** Does not authorize a simplified GUI-owned execution world.
- **Basis:** Ownership manifest.
