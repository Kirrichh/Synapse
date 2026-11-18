# Models, providers, and accounting

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/llm/gateway.py`

- **Symbols:** LLMGatewayConfig; config_from_env; LLMGateway.complete; build_gateway_from_env.
- **Role:** Existing language-runtime product LLM gateway; inspected real-provider implementation targets Gemini and has its own privacy checks.
- **UI use:** Runtime model settings, separate from external-agent model_access.
- **Boundary:** Not a universal ready-made provider registry; chat cannot bypass execution through it.
- **Basis:** Definition/call in the supplied map.

## `synapse/builtins.py`

- **Symbols:** LLMBackend.
- **Role:** Language LLM boundary accepting LLMGateway, used by runtime built-ins.
- **UI use:** Deliver configuration to the existing runtime backend.
- **Boundary:** Not the owner of durable main-UI conversation history.
- **Basis:** Definitions/usage confirmed by LLMGateway code search.

## `synapse/llm/gemini.py`

- **Symbols:** GeminiProvider; parse_gemini_usage.
- **Role:** Concrete Gemini implementation for LLMGateway.
- **UI use:** This provider's configuration and diagnostics.
- **Boundary:** Distinct from the agent Chat Completions broker.
- **Basis:** Gateway imports/calls and llm tree.

## `synapse/llm/models.py`

- **Symbols:** LLMResult; LLMUsage; LLMProviderStatus; LLMTokenStatus.
- **Role:** Provider result and usage-availability types.
- **UI use:** Errors, results, confirmed/missing consumption.
- **Boundary:** Unknown token usage is never zero by substitution.
- **Basis:** Gateway imports and llm tree.

## `synapse/llm/privacy.py`

- **Symbols:** PrivacyContext; PrivacyPolicyError; enforce_privacy_for_tier.
- **Role:** Data classification and gateway eligibility checks.
- **UI use:** External-transmission restrictions and understandable refusals.
- **Boundary:** Does not replace Gold's stricter local-information boundary.
- **Basis:** LLMGateway.complete call and llm tree.

## `synapse/llm/http_transport.py`

- **Symbols:** provider_http_exchange; MAX_PROVIDER_BODY_BYTES.
- **Role:** Shared bounded HTTP exchange used by the agent broker.
- **UI use:** Owner-mediated provider diagnostics.
- **Boundary:** Do not add a direct renderer HTTP route.
- **Basis:** model_broker call and llm tree.

## `synapse/llm/capture.py`

- **Symbols:** ProviderCapturePort; WorkerCapturePort; response_inventory; inspect_response_inventory.
- **Role:** Neutral durable pre-request capture and response retention before consumer delivery.
- **UI use:** References to actual model calls.
- **Boundary:** Valid inventory structure does not prove truth; capture failure does not mean zero cost.
- **Basis:** Definition/call in the supplied map.

## `synapse/llm/__init__.py`

- **Symbols:** Exports from gateway, models, and privacy.
- **Role:** Public LLM-package exports.
- **UI use:** Navigation to existing types.
- **Boundary:** Not an independent UI API.
- **Basis:** Tree and import search.

## `synapse/llm/smoke.py`

- **Symbols:** Diagnostic smoke module.
- **Role:** Auxiliary provider-connection diagnostics.
- **UI use:** Source material for a separately evaluated real probe.
- **Boundary:** Never run automatically as a supposedly effect-free check without examining behavior.
- **Basis:** Tree confirms file; scenario was not executed in the source review.

## `synapse/experiments/gold/stage15/worker_accounting.py`

- **Symbols:** selected_model_access; create_worker_accounting; WorkerAccounting.open_capture.
- **Role:** Binds the selected profile's model broker to a frozen run's CaptureStore.
- **UI use:** External-agent usage/limits and bottom-panel evidence.
- **Boundary:** Not a new model dispatcher; canonical composition creates and supplies accounting.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/stage15/capture_store.py`

- **Symbols:** CaptureStore.
- **Role:** Stores physical provider/resource observations and retained prefixes.
- **UI use:** Actual cost evidence rather than invented statistics.
- **Boundary:** An open record does not establish a successful response.
- **Basis:** worker_accounting call and manifest.

## `synapse/experiments/gold/stage15/telemetry.py`

- **Symbols:** UsageProfile; Phase; reference; telemetry types.
- **Role:** Observation semantics and explicit provider accounting.
- **UI use:** Resource-table data and metric explanations.
- **Boundary:** Telemetry cannot decide result correctness.
- **Basis:** Manifest and inspected Stage 15 imports/usage.

## `synapse/experiments/gold/stage15/resource_accounting.py`

- **Symbols:** ResourceEvidence.
- **Role:** Physical owner-operation measurements, separate accounting, and unfinished-work reconciliation.
- **UI use:** Measured indicators and chart evidence.
- **Boundary:** Sampled usage is not a hard cache/storage quota.
- **Basis:** Manifest and run_observability usage.

## `synapse/experiments/gold/stage15/reconciliation.py`

- **Symbols:** reconcile_run_telemetry; reconstruct_call_records.
- **Role:** Reconciles physical call inventory with reports.
- **UI use:** Accounting incompleteness/discrepancies.
- **Boundary:** Never fill missing accounting with estimates labeled as fact.
- **Basis:** run_observability imports and manifest.

## `synapse/experiments/gold/runner/budgets.py`

- **Symbols:** preparation_budget_failure.
- **Role:** Between-attempt admission based on durable clocks and completed worker checkpoints.
- **UI use:** One basis for budget.get's already-accounted costs.
- **Boundary:** Not a shared money/token reservation service before each HTTP request; such limits need another contract.
- **Basis:** Definition/call in the supplied map.

## `synapse/resource_usage.py`

- **Symbols:** recording_resources; observed_operation; measure_operation; active_recorder.
- **Role:** Physical-operation instrumentation.
- **UI use:** Supporting measurement basis.
- **Boundary:** Keep accounting ownership outside UI; measurement and enforcement are different.
- **Basis:** model_broker/run_observability imports and tree.
