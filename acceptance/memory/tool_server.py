"""Scripted MCP stdio tool server of the memory acceptance scenarios.

``python -m acceptance.memory.tool_server SCRIPT WORLD`` serves the tools a
scenario script declares over the official MCP SDK. Each answer is chosen by
the first rule whose ``when`` is a subset of the call arguments and by how
many times that exact call reached the server before (``sequence``, then
``then``). The server keeps its own world in ``WORLD``: every call it received
and every effect it applied. The acceptance checker reads that world, never
the palace's account of it.

An answer is one of:

* ``{"payload": {...}}`` — a structured answer;
* ``{"payload": {...}, "effect": "name"}`` — also applies the named effect;
* ``{"lost": true, "effect": "name" | null}`` — applies the effect (if any)
  and dies before answering, so the caller loses the answer;
* any answer may add ``"delay": seconds`` — the call is recorded in the world
  first and answered only after the delay (a window for a process crash).
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

SCRIPT_V1 = "synapse.acceptance.memory.tool-script/v1"


def descriptor(tool):
    """The MCP descriptor a scripted tool is served (and admitted) with."""
    from mcp import types

    return types.Tool(name=tool["name"], input_schema=tool["input_schema"], output_schema=tool["output_schema"])


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _answer(tool, arguments, count):
    for rule in tool["answers"]:
        if all(arguments.get(key) == value for key, value in rule.get("when", {}).items()):
            sequence = rule.get("sequence", [])
            return sequence[count] if count < len(sequence) else rule["then"]
    raise ValueError(f"scripted tool {tool['name']} has no answer for {arguments}")


class World:
    """The server's own record of calls and applied effects, shared by every run."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def call(self, name, arguments, choose):
        with open(self.path.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {"calls": [], "effects": []}
            key = _canonical([name, arguments])
            count = sum(1 for item in state["calls"] if _canonical([item["tool"], item["args"]]) == key)
            answer = choose(count)
            state["calls"].append({"tool": name, "args": arguments})
            if answer.get("effect"):
                state["effects"].append({"tool": name, "args": arguments, "effect": answer["effect"]})
            self.path.write_text(json.dumps(state, sort_keys=True))
            return answer


def main(script_path: str, world_path: str) -> None:
    import anyio
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    script = json.loads(Path(script_path).read_text())
    if script.get("schema_version") != SCRIPT_V1:
        raise SystemExit("unknown tool script")
    tools = {tool["name"]: tool for tool in script["tools"]}
    world = World(Path(world_path))

    async def listing(context, params):
        return types.ListToolsResult(tools=[descriptor(tool) for tool in script["tools"]])

    async def call(context, params):
        tool = tools[params.name]
        arguments = dict(params.arguments or {})
        answer = world.call(params.name, arguments, lambda count: _answer(tool, arguments, count))
        if answer.get("delay"):
            await anyio.sleep(answer["delay"])
        if answer.get("lost"):
            os._exit(3)  # The effect is applied; the answer never reaches the caller.
        payload = answer["payload"]
        return types.CallToolResult(content=[types.TextContent(type="text", text=_canonical(payload))],
                                    structured_content=payload)

    server = Server("synapse-memory-acceptance-tools", on_list_tools=listing, on_call_tool=call)

    async def serve():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)


if __name__ == "__main__":
    main(*sys.argv[1:3])
