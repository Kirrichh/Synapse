"""Real SDK client/server acceptance, independent of Gold outcome authority."""

from dataclasses import replace
import hashlib
import socket
import threading
import time

import pytest


def test_official_a2a_transport_retains_result_without_redispatch(tmp_path):
    pytest.importorskip("a2a")
    from google.protobuf.json_format import MessageToDict, ParseDict
    from starlette.applications import Starlette
    import uvicorn
    from a2a import types
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore
    from synapse.agents.a2a_adapter import A2AAgentAdapter
    from synapse.agents.codec import canonical_bytes, decode_json, digest
    from synapse.agents.contracts import (
        AgentExecutionRequest, AgentExecutionStatus, AgentProfile,
        AgentRuntimeContext, AgentTransportKind, LocalInformationPolicy,
    )
    from synapse.agents.execution import AgentExecutionPort
    from synapse.agents.outputs import OutputRegistry, SchemaOutputCodec
    from synapse.agents.policy import IsolationKind, ResourceBudget, RuntimePolicy
    from synapse.agents.registry import AgentRegistry, AgentSelectionError, CapabilityAdmission

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}/"
    card = types.AgentCard(name="reference-counter", version="1", description="Transport acceptance",
        supported_interfaces=[types.AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=endpoint)],
        default_input_modes=["text/plain"], default_output_modes=["application/json"])
    calls = []

    class Counter(AgentExecutor):
        async def execute(self, context, event_queue):
            task = "".join(part.text for part in context.message.parts if part.HasField("text"))
            calls.append(task)
            part = ParseDict({"data": {"characters": len(task)}}, types.Part())
            await event_queue.enqueue_event(types.Message(message_id="reply", role=types.Role.ROLE_AGENT, parts=[part]))

        async def cancel(self, context, event_queue):
            raise RuntimeError("message already completed")

    handler = DefaultRequestHandler(agent_executor=Counter(), task_store=InMemoryTaskStore(), agent_card=card)
    server = uvicorn.Server(uvicorn.Config(Starlette(routes=create_jsonrpc_routes(handler, "/")), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        output_schema = "reference.measurement/v1"
        schema = {"type": "object", "properties": {"characters": {"type": "integer", "minimum": 0}},
                  "required": ["characters"], "additionalProperties": False}
        profile = AgentProfile("reference-counter/v1", "reference-counter", "1", "a2a", "1", AgentTransportKind.A2A,
            ("text.measure",), (), (output_schema,), LocalInformationPolicy.NOT_SUPPORTED, ("READ_ONLY",), "reference",
            runtime_policy=RuntimePolicy(isolation=IsolationKind.REMOTE, network="REMOTE_ENDPOINT"))
        adapter = A2AAgentAdapter(profile, {"agent_card": MessageToDict(card), "allowed_endpoints": [endpoint], "credential_env": None})
        registry = AgentRegistry((adapter,), admissions=(CapabilityAdmission(digest(profile), profile.capabilities,
            digest({"authority": "acceptance scenario only"})),))
        port = AgentExecutionPort(registry, evidence_root=tmp_path / "evidence",
            outputs=OutputRegistry((SchemaOutputCodec(output_schema, canonical_bytes(schema)),)))
        task = "count this task"
        request = AgentExecutionRequest("reference-invocation", "attempt", "context", task,
            hashlib.sha256(task.encode()).hexdigest(), len(task), digest(task), ("text.measure",), output_schema,
            ("READ_ONLY",), (), LocalInformationPolicy.NOT_SUPPORTED, allowed_effects=("READ_ONLY",),
            resource_budget=ResourceBudget(timeout_seconds=10, cpu_seconds=10), allowed_networks=("REMOTE_ENDPOINT",))
        result = port.execute(request=request, runtime=AgentRuntimeContext(workspace))
        assert result.status is AgentExecutionStatus.COMPLETED
        assert decode_json(result.outputs[0].payload) == {"characters": len(task)}
        assert port.restore(request.invocation_id)[1] == result
        assert port.execute(request=request, runtime=AgentRuntimeContext(workspace)) == result
        private = "local-only"
        with pytest.raises(AgentSelectionError):
            registry.select(replace(request, information_policy=LocalInformationPolicy.LOCAL_ONLY,
                information_text=private, information_sha256=hashlib.sha256(private.encode()).hexdigest(),
                information_byte_length=len(private)))
        assert calls == [task]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()
