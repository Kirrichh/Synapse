# Documentation and packaging references

Version 1.0. Non-test references from source Appendix D.6 group 8.

[Code map](../UI_CODE_MAP_V1.md). Acceptance references are in a separate [development-only document](../development-reference/ACCEPTANCE_SOURCES.md).

## `docs/UNIVERSAL_AGENT_EXECUTION.md`

- **Symbols:** Integration document.
- **Role:** Canonical route, supported adapters, admission, network/local-information limits, and domain scope.
- **UI use:** Agent-connection domain documentation, opened by project-relative path.
- **Boundary:** Historical acceptance results are not a new run or proof that every domain is ready.
- **Basis:** Integration documentation; results apply to its stated scenarios.

## `docs/GOLD_KNOWLEDGE_INGESTION.md`

- **Symbols:** Gold/source-knowledge document.
- **Role:** Project input/source memory/admitted profile relationships; documents removal of the built-in Mini path.
- **UI use:** Explain why a new adapter is not another memory system/runner.
- **Boundary:** Do not restore removed MiniInvocationConfig/MiniWorkerTransport as UI integration entries.
- **Basis:** Documented relationship.

## `pyproject.toml`

- **Symbols:** project.dependencies; optional-dependencies; project.scripts.
- **Role:** Python core, optional agent-a2a/acp/mcp/docling dependencies, and synapse=cli.main script.
- **UI use:** Packaging and integration availability checks.
- **Boundary:** Installing extras is not runtime acceptance; user secrets are not package metadata.
- **Basis:** Definition/call in the supplied map.
