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
  first and answered only after the delay (a window for a process crash);
  with ``"until": name`` as well, it is answered as soon as a file of that
  name appears next to the world (the scenario's signal), at the latest
  after the delay;
* ``{"payload": {...}, "act": {...}, "otherwise": {...}}`` — answered from the
  world's objects: ``create`` stores a new object under a fresh identifier
  (random, or sequential for a predictable service) and answers it; ``read``
  answers the object named by an argument; ``transition`` moves it from one
  state to another (``refuse`` names states answered with a refusal instead);
  ``consume`` uses up one object matching the call; ``put`` stores (or
  replaces) the object named by an argument, keeping the arguments ``keep``
  names; ``flags`` add booleans that say
  whether the object is in a state; ``requires`` names other objects that
  must exist (in a state) first, each with the answer given when one does
  not;
* a tool may declare ``dedupe`` (``{"field", "retention_s"}``): the provider
  keeps each idempotency key it receives with the call's essential arguments
  and answer; a repeat with the same key within the retention is answered
  again without an effect, the same key with other arguments is refused
  (``IDEMPOTENCY_KEY_REUSED``), and an older key is forgotten;
* ``{"payload": {...}, "embed": {...}}`` — answers an embedding of the
  ``text`` argument: one component per declared concept, counting the words
  of that concept in the text. Synonyms and translations share a concept;
  a word of no concept (a negation, a name) changes nothing — as dense
  retrievers are known to behave. When the object does not exist (or is not in the required state) the
  ``otherwise`` answer is given and no effect applies.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
import uuid
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


def _create(objects, act, arguments, then):
    ids = act.get("ids", "random")
    identifier = f"job-{len(objects) + 1}" if ids == "sequential" else f"job-{uuid.uuid4().hex[:12]}"
    item = {act["key"]: identifier, "state": act["state"], **{name: arguments.get(name) for name in act["keep"]},
            **act.get("set", {})}
    objects[identifier] = item
    return {**then, "payload": {**then["payload"], act["key"]: identifier, "state": act["state"]}}


def _required(state, act, arguments):
    """The answer of the first object ``requires`` names that is missing (or in another state), if any."""
    for need in act.get("requires", []):
        item = state.setdefault("objects", {}).setdefault(need["objects"], {}).get(arguments.get(need["key"]))
        if item is None or ("state" in need and item.get("state") != need["state"]):
            return {"payload": need["otherwise"]}
    return None


def _consume(state, objects, act, arguments, then):
    wanted = {name: arguments.get(value["arg"]) if isinstance(value, dict) else value
              for name, value in act["match"].items()}
    for identifier in sorted(objects):
        item = objects[identifier]
        if not item.get("consumed") and all(item.get(name) == value for name, value in wanted.items()):
            refused = _required(state, act, arguments)
            if refused is not None:
                return refused
            item["consumed"] = True
            return then
    return None


def _put(objects, act, arguments, then):
    identifier = arguments.get(act["key"])
    objects[identifier] = {act["key"]: identifier, "state": act["state"],
                           **{name: arguments.get(name) for name in act.get("keep", [])}}
    return {**then, "payload": {**then["payload"], **objects[identifier]}}


def _act(state, act, arguments, then):
    """The answer of a rule answered from the world's objects, or ``None`` when its object is missing."""
    verb = next(name for name in ("create", "read", "transition", "consume", "put") if name in act)
    objects = state.setdefault("objects", {}).setdefault(act[verb], {})
    if verb == "create":
        return _create(objects, act, arguments, then)
    if verb == "consume":
        return _consume(state, objects, act, arguments, then)
    if verb == "put":
        return _put(objects, act, arguments, then)
    item = objects.get(arguments.get(act["key"]))
    if item is None:
        return None
    if verb == "transition":
        if item["state"] in act.get("refuse", {}):
            return {"payload": {**act["refuse"][item["state"]], act["key"]: item[act["key"]],
                                **{name: item.get(name) for name in act.get("echo", [])}}}
        allowed = act["from"] if isinstance(act["from"], list) else [act["from"]]
        if item["state"] not in allowed:
            return None
        item["state"] = act["to"]
    flags = {name: item.get("state") == state for name, state in act.get("flags", {}).items()}
    return {**then, "payload": {**then["payload"], **item, **flags}}


def _embedding(embed, arguments) -> dict:
    words = [word for word in re.findall(r"\w+", str(arguments.get("text", "")).casefold())]
    vector = [sum(1 for word in words if word in set(members)) for _, members in embed["concepts"]]
    return {"ok": True, "vector": vector, "model": embed["model"]}


def _deduplicated(state, name, arguments, dedupe):
    """A provider's idempotency: a kept key answers again without an effect; the same key with other
    essential arguments is refused; a key older than the provider's retention is forgotten."""
    key = arguments[dedupe["field"]]
    kept = state.get("keys", {}).get(name, {}).get(key)
    if kept is None or time.time() - kept["at"] >= dedupe["retention_s"]:
        return None
    essential = {item: value for item, value in arguments.items() if item != dedupe["field"]}
    if essential != kept["args"]:
        return {"payload": {"ok": False, "err": "IDEMPOTENCY_KEY_REUSED"}}
    return dict(kept["answer"])


class World:
    """The server's own record of calls, applied effects and objects, shared by every run."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def call(self, name, arguments, choose, dedupe=None):
        with open(self.path.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {"calls": [], "effects": []}
            if dedupe is not None and arguments.get(dedupe["field"]) is not None:
                kept = _deduplicated(state, name, arguments, dedupe)
                if kept is not None:
                    state["calls"].append({"tool": name, "args": arguments})
                    self.path.write_text(json.dumps(state, sort_keys=True))
                    return kept
            key = _canonical([name, arguments])
            count = sum(1 for item in state["calls"] if _canonical([item["tool"], item["args"]]) == key)
            answer = choose(count)
            if "embed" in answer:
                answer = {"payload": _embedding(answer["embed"], arguments)}
            if "act" in answer:
                acted = _act(state, answer["act"], arguments, {key: value for key, value in answer.items()
                                                                 if key not in {"act", "otherwise"}})
                answer = acted if acted is not None else answer["otherwise"]
            state["calls"].append({"tool": name, "args": arguments})
            if answer.get("effect"):
                state["effects"].append({"tool": name, "args": arguments, "effect": answer["effect"]})
            if dedupe is not None and arguments.get(dedupe["field"]) is not None and "payload" in answer:
                # The provider keeps what it answered for the key: the answer itself, never its effect or delay.
                essential = {key: value for key, value in arguments.items() if key != dedupe["field"]}
                state.setdefault("keys", {}).setdefault(name, {})[arguments[dedupe["field"]]] = {
                    "args": essential, "answer": {"payload": answer["payload"]}, "at": time.time()}
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
        answer = world.call(params.name, arguments, lambda count: _answer(tool, arguments, count), tool.get("dedupe"))
        if answer.get("delay"):
            signal = Path(world_path).parent / answer["until"] if answer.get("until") else None
            waited = 0.0
            while waited < answer["delay"] and not (signal is not None and signal.exists()):
                await anyio.sleep(0.1)
                waited += 0.1
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
