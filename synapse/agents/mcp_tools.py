"""MCP SDK tool boundary for an already isolated agent runtime.

Tools retain their own schemas and never acquire agent-selection, Gold verdict,
publication or memory authority. The enclosing runtime owns network/filesystem
limits; this port does not launch a second orchestration loop.
"""

from dataclasses import dataclass

from .codec import canonical_bytes, digest
from .outputs import validate_json_schema


@dataclass(frozen=True)
class McpToolBinding:
    name: str
    input_schema_bytes: bytes
    output_schema_bytes: bytes
    descriptor_sha256: str


@dataclass(frozen=True)
class McpToolObservation:
    tool_name: str
    value_json: bytes
    native_result_json: bytes
    is_error: bool


class McpToolPort:
    def __init__(self, client, bindings: tuple[McpToolBinding, ...]):
        from mcp import Client
        if type(client) is not Client:
            raise TypeError("MCP tool port requires the official SDK client")
        if len({b.name for b in bindings}) != len(bindings):
            raise ValueError("MCP tool bindings must be unique")
        self.client = client
        self.bindings = {b.name: b for b in bindings}

    async def invoke(self, name: str, arguments) -> McpToolObservation:
        from .codec import decode_json
        binding = self.bindings.get(name)
        if binding is None:
            raise PermissionError("MCP tool was not admitted for this runtime")
        validate_json_schema(arguments, decode_json(binding.input_schema_bytes))
        cursor, descriptor = None, None
        seen = set()
        while True:
            tools = await self.client.list_tools(cursor=cursor, cache_mode="bypass")
            matches = [tool for tool in tools.tools if tool.name == name]
            if matches:
                if len(matches) != 1 or descriptor is not None:
                    raise ValueError("MCP inventory repeats a tool identity")
                descriptor = matches[0]
            cursor = tools.next_cursor
            if cursor is None:
                break
            if cursor in seen or len(seen) >= 128:
                raise ValueError("MCP inventory pagination is not bounded")
            seen.add(cursor)
        if descriptor is None or digest(descriptor.model_dump(mode="json", by_alias=True, exclude_none=True)) != binding.descriptor_sha256:
            raise ValueError("MCP tool descriptor changed since admission")
        result = await self.client.call_tool(name, arguments)
        if not result.is_error:
            validate_json_schema(result.structured_content, decode_json(binding.output_schema_bytes))
        return McpToolObservation(name, canonical_bytes(result.structured_content),
                                 canonical_bytes(result.model_dump(mode="json", by_alias=True)), bool(result.is_error))
