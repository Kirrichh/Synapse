"""Admitted MCP stdio servers as the gateway's transport.

Each server runs in one asyncio task on a dedicated loop thread and serves the
gateway's calls through the existing ``McpToolPort`` with the admitted
descriptor digests. A call returns the decoded answer or reports it lost; a
dead server loses every later call. The transport records nothing itself.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import threading
from typing import Any, Mapping

from .contracts import ToolConfiguration, ToolContract

_CALL_TIMEOUT_SECONDS = 30.0


class McpToolTransport:
    """Admitted MCP stdio servers, kept open for one process's runs."""

    def __init__(self, configuration: ToolConfiguration) -> None:
        self.configuration = configuration
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._dead: set[str] = set()
        self._guard = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="synapse-mcp-tools", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
                atexit.register(self.close)
            return self._loop

    def _server_queue(self, server_id: str) -> asyncio.Queue:
        loop = self._ensure_loop()
        with self._guard:
            queue = self._queues.get(server_id)
            if queue is not None:
                return queue
            ready: concurrent.futures.Future = concurrent.futures.Future()

            async def start():
                queue = asyncio.Queue()
                loop.create_task(self._serve(server_id, queue, ready))
                return queue

            queue = asyncio.run_coroutine_threadsafe(start(), loop).result(_CALL_TIMEOUT_SECONDS)
            self._queues[server_id] = queue
        ready.result(_CALL_TIMEOUT_SECONDS)
        return queue

    async def _serve(self, server_id: str, queue: asyncio.Queue, ready: concurrent.futures.Future) -> None:
        from mcp import Client, StdioServerParameters

        from synapse.agents.codec import canonical_bytes
        from synapse.agents.mcp_tools import McpToolBinding, McpToolPort

        server = self.configuration.servers[server_id]
        bindings = tuple(
            McpToolBinding(contract.name, canonical_bytes(dict(contract.input_schema)),
                           canonical_bytes(dict(contract.output_schema)), contract.descriptor_sha256)
            for contract in self.configuration.tools.values() if contract.server_id == server_id)
        try:
            parameters = StdioServerParameters(command=server.argv[0], args=list(server.argv[1:]),
                                               env=dict(server.env) or None, cwd=server.cwd)
            async with Client(parameters) as client:
                port = McpToolPort(client, bindings)
                if not ready.done():
                    ready.set_result(True)
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    name, arguments, future = item
                    try:
                        future.set_result(await port.invoke(name, arguments))
                    except BaseException as exc:  # noqa: BLE001 - delivered to the waiting caller
                        future.set_exception(exc)
        except BaseException as exc:  # noqa: BLE001 - a dead server fails every later call as lost
            self._dead.add(server_id)
            if not ready.done():
                ready.set_exception(exc)
            while not queue.empty():
                item = queue.get_nowait()
                if item is not None:
                    item[2].set_exception(ConnectionError("tool server stopped"))

    def call(self, contract: ToolContract, arguments: Mapping[str, Any]) -> tuple[str, Any]:
        """``("ok", payload)`` for an answered call, ``("lost", None)`` otherwise."""
        from synapse.agents.codec import decode_json

        if contract.server_id in self._dead:
            return "lost", None
        try:
            queue = self._server_queue(contract.server_id)
            future: concurrent.futures.Future = concurrent.futures.Future()
            self._loop.call_soon_threadsafe(queue.put_nowait, (contract.name, dict(arguments), future))
            observation = future.result(_CALL_TIMEOUT_SECONDS)
        except BaseException:  # noqa: BLE001 - any failure to receive an answer loses it
            return "lost", None
        return "ok", decode_json(observation.value_json)

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        for queue in list(self._queues.values()):
            loop.call_soon_threadsafe(queue.put_nowait, None)
        self._queues.clear()
        loop.call_soon_threadsafe(loop.stop)
        self._loop = None
