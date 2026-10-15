"""Official MCP SDK acceptance; tool observations do not grant Gold authority."""

import asyncio

import pytest


def test_mcp_tool_schema_and_admission_are_checked_on_every_call():
    pytest.importorskip("mcp")
    from mcp import Client, types
    from mcp.server import Server
    from synapse.agents.codec import canonical_bytes, decode_json, digest
    from synapse.agents.mcp_tools import McpToolBinding, McpToolPort

    async def scenario():
        input_schema = {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"], "additionalProperties": False}
        output_schema = {"type": "object", "properties": {"bytes": {"type": "integer"}},
                         "required": ["bytes"], "additionalProperties": False}
        descriptor = types.Tool(name="text.byte_length", input_schema=input_schema, output_schema=output_schema)
        calls = []

        async def listing(context, params):
            return types.ListToolsResult(tools=[descriptor])

        async def call(context, params):
            calls.append(params.name)
            value = {"bytes": len(params.arguments["text"].encode())}
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(value["bytes"]))],
                                        structured_content=value)

        server = Server("reference-tools", on_list_tools=listing, on_call_tool=call)
        async with Client(server) as client:
            port = McpToolPort(client, (McpToolBinding("text.byte_length", canonical_bytes(input_schema),
                canonical_bytes(output_schema), digest(descriptor.model_dump(mode="json", by_alias=True, exclude_none=True))),))
            observation = await port.invoke("text.byte_length", {"text": "Документ"})
            assert decode_json(observation.value_json) == {"bytes": 16}
            with pytest.raises(PermissionError):
                await port.invoke("arbitrary.tool", {})
            descriptor.description = "changed after admission"
            with pytest.raises(ValueError):
                await port.invoke("text.byte_length", {"text": "abc"})
            assert calls == ["text.byte_length"]

    asyncio.run(scenario())
