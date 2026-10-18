# Development-only acceptance sources

Version 1.0. Translated acceptance references from source Appendix D.6/D.7.

These eight entries are for developers planning external acceptance. They are
not UI integration points, product dependencies, navigation targets, runtime
data sources, assets, or implementations. They must not be included in product
imports, bundles, menus, startup hooks, or state feeds. Product behavior is
verified by acceptance; it never obtains behavior or truth from acceptance.

The original source grouped these paths with product integration references.
This English edition preserves their content here while separating their role,
as required by the owner's permanent rule. The specification's historical
citations likewise remain development documentation, never product wiring.

[Working documents](../README.md) · [Acceptance rules](../ACCEPTANCE_RULES.md)

## `tests/test_stage4_gold_canonical_entrypoint.py`

- **Symbols:** Canonical-entry, reachability, and composition-root acceptance.
- **Role:** Existing NR-02/CLI-to-Gold boundary checks.
- **Acceptance use:** Machine-mode acceptance must preserve those boundaries.
- **Boundary:** Acceptance layer, never a production dependency.
- **Basis:** Definition/call in the supplied map.

## `acceptance/stage4/stage16/test_agent_profile_gold_acceptance.py`

- **Symbols:** Admitted-agent-profile acceptance through Gold.
- **Role:** Canonical profile, C1, publication, and recovery route cited in documentation.
- **Acceptance use:** Reference for future installed-UI acceptance.
- **Boundary:** Not a ready Tauri test or product adapter. Editorial finding: this path is absent from the reviewed baseline; the documentation reference is retained, not treated as executable evidence.
- **Basis:** Source map cites docs/UNIVERSAL_AGENT_EXECUTION.md for path/purpose; baseline inspection found the path missing.

## `acceptance/agents/test_a2a_sdk_acceptance.py`

- **Symbols:** A2A SDK acceptance.
- **Role:** Actual A2A protocol-adapter checks.
- **Acceptance use:** Distinguish supported transport from Gold-domain readiness.
- **Boundary:** Not a source of runtime components.
- **Basis:** Path/purpose cited in integration documentation.

## `acceptance/agents/test_mcp_sdk_acceptance.py`

- **Symbols:** MCP SDK acceptance.
- **Role:** MCP tool-contract acceptance.
- **Acceptance use:** Tool-connection verification reference.
- **Boundary:** Never import the acceptance server into the product.
- **Basis:** Path/purpose cited in integration documentation.

## `acceptance/stage4/stage15/test_canonical_observability_acceptance.py`

- **Symbols:** Observability acceptance.
- **Role:** Retained events and independent observation sources.
- **Acceptance use:** Reference for UI event acceptance.
- **Boundary:** Never render the fixture history as production state.
- **Basis:** Confirmed source-search excerpts.

## `acceptance/stage4/stage15/test_observation_recovery_acceptance.py`

- **Symbols:** Observability recovery acceptance.
- **Role:** Missing records and rereading retained evidence.
- **Acceptance use:** Reference for UI log read/recovery cases.
- **Boundary:** An acceptance scenario is not a product runtime component.
- **Basis:** Confirmed source-search excerpts.

## `tests/test_llm_gateway_stage1.py`

- **Symbols:** LLMGateway, Gemini, and usage acceptance.
- **Role:** Existing gateway/provider-result checks.
- **Acceptance use:** Runtime-LLM settings regression basis.
- **Boundary:** Not universal model acceptance or a UI test.
- **Basis:** Confirmed source search.

## `acceptance/runtime/test_vm_host_calls_contract.py`

- **Symbols:** runtime host-call contract.
- **Role:** Existing language host/LLM boundary checks.
- **Acceptance use:** Runtime-integration reference, not a chat bypass.
- **Boundary:** Execution establishes a verdict, not the existence of a test file.
- **Basis:** Confirmed source search.

## Acceptance file and symbol index

| Source path | Symbols / acceptance purpose |
|---|---|
| `tests/test_stage4_gold_canonical_entrypoint.py` | Canonical-entry, reachability, and composition-root acceptance. |
| `acceptance/stage4/stage16/test_agent_profile_gold_acceptance.py` | Admitted-agent-profile acceptance through Gold. |
| `acceptance/agents/test_a2a_sdk_acceptance.py` | A2A SDK acceptance. |
| `acceptance/agents/test_mcp_sdk_acceptance.py` | MCP SDK acceptance. |
| `acceptance/stage4/stage15/test_canonical_observability_acceptance.py` | Observability acceptance. |
| `acceptance/stage4/stage15/test_observation_recovery_acceptance.py` | Observability recovery acceptance. |
| `tests/test_llm_gateway_stage1.py` | LLMGateway, Gemini, and usage acceptance. |
| `acceptance/runtime/test_vm_host_calls_contract.py` | runtime host-call contract. |
