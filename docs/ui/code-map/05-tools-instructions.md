# MCP tools, instructions, and external-action semantics

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/memory_consolidation/tools/contracts.py`

- **Symbols:** ToolConfiguration; ToolContract; parse_tool_configuration.
- **Role:** Admitted MCP servers/tools, schemas, roles, and effect conditions.
- **UI use:** Memory-tool server, argv, environment, schema, descriptor settings.
- **Boundary:** Idempotency and outside are contract properties, not convenience toggles.
- **Basis:** configuration/gateway/mcp_transport imports and tree.

## `synapse/memory_consolidation/tools/mcp_transport.py`

- **Symbols:** McpToolTransport.call; close.
- **Role:** MCP STDIO sessions in a separate event-loop thread, using McpToolPort; no effect journal of its own.
- **UI use:** Tool-call delivery and proper process shutdown.
- **Boundary:** Transport, not another scheduler. Integrate server lifecycle; close alone is not termination evidence.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/tools/gateway.py`

- **Symbols:** Gateway.invoke; recorded; recorded_outcome; records; head.
- **Role:** Single external-action gateway for memory-bound runs; STARTED/RESULT/REJECTED records and contract/issued-identity retry decisions.
- **UI use:** Tool events, learning evidence, and authorized history reads.
- **Boundary:** UI cannot bypass it via direct McpToolTransport. Reason responses are not independent action witnesses.
- **Basis:** Definition/call in the supplied map.

## `synapse/memory_consolidation/tools/semantics.py`

- **Symbols:** interpret; repeat_admissible.
- **Role:** Interprets result effects and retry admissibility.
- **UI use:** Explain tool/retry refusal.
- **Boundary:** Interpretation belongs to the core, not UI templates.
- **Basis:** Confirmed gateway/reassessment calls.

## `synapse/memory_consolidation/tools/journal.py`

- **Symbols:** ChainedLog; GatewayIntegrityError.
- **Role:** Chained gateway log.
- **UI use:** Bottom-panel source records.
- **Boundary:** Graphical logs cannot rewrite this history.
- **Basis:** Confirmed construction in gateway.py.

## `synapse/memory_consolidation/tools/evidence.py`

- **Symbols:** EvidenceStore.
- **Role:** Gateway evidence storage.
- **UI use:** Evidence referenced by tool events.
- **Boundary:** Permission to view is not permission to transmit externally.
- **Basis:** Confirmed construction in gateway.py.

## `synapse/prompt_template.py`

- **Symbols:** placeholders; render.
- **Role:** Simple shared template language for interpreter/VM.
- **UI use:** Possible low-level instruction display/editing component.
- **Boundary:** Not a skills registry or user-instruction version/admission mechanism; administration remains to be defined.
- **Basis:** Definition/call in the supplied map.
