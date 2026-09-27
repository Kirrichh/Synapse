"""Official A2A SDK transport with pinned cards and durable remote task handles."""

import asyncio
from pathlib import Path
import time

from .codec import canonical_bytes
from .contracts import (AgentTransportKind, LocalInformationPolicy, AgentExecutionResult,
    AgentExecutionStatus, AgentUsage, AgentTokenStatus, AgentReport, AgentDeliveryEvidence)
from .outputs import output_envelope
from .policy import AgentExecutionError, AgentFailureCode, IsolationKind
from .retention import InvocationStore


class A2AAdapterFactory:
    def create(self, configuration):
        from .configuration import profile_from_dict
        profile = profile_from_dict(configuration["profile"])
        native = configuration["native"]
        if (profile.transport is not AgentTransportKind.A2A
                or profile.runtime_policy.isolation is not IsolationKind.REMOTE
                or profile.local_information_policy is not LocalInformationPolicy.NOT_SUPPORTED):
            raise ValueError("A2A remote profile must reject local information")
        if set(native) != {"agent_card", "allowed_endpoints", "credential_env"}:
            raise ValueError("A2A requires a frozen card, endpoints and credential reference")
        return A2AAgentAdapter(profile, native)


class A2AAgentAdapter:
    def __init__(self, profile, configuration):
        self._profile = profile
        self._configuration_bytes = canonical_bytes(configuration)

    @property
    def profile(self):
        return self._profile

    def execute(self, request, runtime):
        return asyncio.run(self._invoke(request, runtime, resuming=False))

    def resume(self, request, runtime):
        return asyncio.run(self._invoke(request, runtime, resuming=True))

    async def _invoke(self, request, runtime, *, resuming):
        import os
        import httpx
        from a2a.client import ClientFactory, ClientConfig
        from a2a import types
        from google.protobuf.json_format import ParseDict, MessageToDict
        from .codec import decode_json
        if request.information_text is not None:
            raise AgentExecutionError(AgentFailureCode.LOCAL_INFORMATION_POLICY_VIOLATION, "A2A cannot receive local information")
        config = decode_json(self._configuration_bytes)
        card = ParseDict(config["agent_card"], types.AgentCard())
        allowed = frozenset(config["allowed_endpoints"])
        if (not allowed or any(str(httpx.URL(url).scheme) not in ("https", "http") for url in allowed)
                or any(interface.url not in allowed for interface in card.supported_interfaces)
                or card.name != self.profile.agent_id or card.version != self.profile.agent_version):
            raise AgentExecutionError(AgentFailureCode.NETWORK_DENIED, "A2A card identity or endpoint differs from admitted configuration")
        store = InvocationStore(runtime.evidence_root)
        refs = []
        remaining = request.resource_budget.stdout_bytes
        io_deadline = time.monotonic() + request.resource_budget.timeout_seconds

        async def within_deadline(operation):
            return await asyncio.wait_for(operation, timeout=max(0, io_deadline - time.monotonic()))

        class BoundedStream(httpx.AsyncByteStream):
            def __init__(self, stream):
                self.stream = stream
            async def __aiter__(self):
                nonlocal remaining
                payload = bytearray()
                iterator = self.stream.__aiter__()
                while True:
                    try:
                        chunk = await within_deadline(iterator.__anext__())
                    except StopAsyncIteration:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise AgentExecutionError(AgentFailureCode.RESOURCE_LIMIT, "A2A response budget exceeded")
                    payload.extend(chunk)
                    yield chunk
                refs.append(store.retain(bytes(payload)))
            async def aclose(self):
                await self.stream.aclose()
        class EndpointTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.AsyncHTTPTransport(retries=0)
            async def handle_async_request(self, native_request):
                if str(native_request.url) not in allowed:
                    raise AgentExecutionError(AgentFailureCode.NETWORK_DENIED, "A2A attempted an undeclared endpoint")
                response = await within_deadline(self.inner.handle_async_request(native_request))
                response.stream = BoundedStream(response.stream)
                return response
            async def aclose(self):
                await self.inner.aclose()
        headers = {}
        if config["credential_env"] is not None:
            credential = os.environ.get(config["credential_env"])
            if not credential:
                raise AgentExecutionError(AgentFailureCode.PROCESS_NOT_STARTED, "A2A credential is unavailable")
            headers["Authorization"] = "Bearer " + credential
        latest = store.latest_event(request.invocation_id, "REMOTE_TASK")
        task_id = None if latest is None else latest["task_id"]
        if resuming and task_id is None:
            raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "A2A send has no acknowledged task ID; redispatch is forbidden")
        intent = store.latest_event(request.invocation_id, "REMOTE_SEND_INTENT")
        remaining_seconds = request.resource_budget.timeout_seconds if intent is None else max(
            0, intent["deadline_unix"] - time.time())
        deadline = time.monotonic() + remaining_seconds
        io_deadline = deadline
        final = None
        dispatched = resuming
        timed_out = False
        async with httpx.AsyncClient(transport=EndpointTransport(), headers=headers, trust_env=False,
            follow_redirects=False, timeout=min(30, request.resource_budget.timeout_seconds)) as http:
            factory = ClientFactory(ClientConfig(streaming=False, polling=True, httpx_client=http, supported_protocol_bindings=["JSONRPC"]))
            async with factory.create(card) as client:
                try:
                    if not resuming:
                        parts = [types.Part(text=request.task_text)]
                        for artifact in request.artifacts:
                            raw = Path(artifact.path).read_bytes()
                            parts.append(types.Part(raw=raw, media_type=artifact.media_type, filename=artifact.artifact_id))
                        message = types.Message(message_id=request.invocation_id, role=types.Role.ROLE_USER, parts=parts)
                        from .codec import json_value
                        ParseDict({"synapse.agent.execution/v1": {
                            "invocation_id": request.invocation_id,
                            "output_profile": request.required_output_profile,
                            "required_capabilities": list(request.required_capabilities),
                            "allowed_effects": list(request.allowed_effects or ()),
                            "resource_budget": json_value(request.resource_budget),
                            "artifacts": [{"artifact_id": a.artifact_id, "sha256": a.sha256,
                                           "byte_length": a.byte_length} for a in request.artifacts],
                        }}, message.metadata)
                        params = types.SendMessageRequest(message=message)
                        params.configuration.return_immediately = True
                        params.configuration.accepted_output_modes.append("application/json")
                        refs.append(store.event(request.invocation_id, {"kind": "REMOTE_SEND_INTENT", "message_id": request.invocation_id, "deadline_unix": time.time() + remaining_seconds}))
                        dispatched = True
                        async for event in client.send_message(params):
                            refs.append(store.retain(event.SerializeToString(deterministic=True)))
                            if event.HasField("task"):
                                final = event.task
                                task_id = final.id
                                refs.append(store.event(request.invocation_id, {"kind": "REMOTE_TASK", "task_id": task_id}))
                            elif event.HasField("message"):
                                return self._result(request, event.message.parts, AgentExecutionStatus.COMPLETED, refs)
                    if task_id is None:
                        raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "A2A acknowledged no task or final message")
                    terminals = {types.TaskState.TASK_STATE_COMPLETED, types.TaskState.TASK_STATE_FAILED,
                        types.TaskState.TASK_STATE_CANCELED, types.TaskState.TASK_STATE_REJECTED}
                    while final is None or final.status.state not in terminals:
                        if store.is_cancelled(request.invocation_id) or time.monotonic() >= deadline:
                            timed_out = time.monotonic() >= deadline and not store.is_cancelled(request.invocation_id)
                            refs.append(store.event(request.invocation_id, {"kind": "REMOTE_CANCEL_REQUESTED", "task_id": task_id}))
                            # Cancellation has its own bounded acknowledgement window;
                            # it never renews the task's execution budget.
                            io_deadline = time.monotonic() + min(5, request.resource_budget.timeout_seconds)
                            final = await client.cancel_task(types.CancelTaskRequest(id=task_id))
                            if final.status.state not in terminals:
                                raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "remote cancellation is not confirmed")
                            break
                        final = await client.get_task(types.GetTaskRequest(id=task_id))
                        if final.status.state not in terminals:
                            await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))
                    refs.append(store.retain(final.SerializeToString(deterministic=True)))
                    status = {types.TaskState.TASK_STATE_COMPLETED: AgentExecutionStatus.COMPLETED,
                        types.TaskState.TASK_STATE_CANCELED: AgentExecutionStatus.CANCELLED,
                        types.TaskState.TASK_STATE_REJECTED: AgentExecutionStatus.REFUSED}.get(final.status.state, AgentExecutionStatus.ERROR)
                    if timed_out and status is AgentExecutionStatus.CANCELLED:
                        status = AgentExecutionStatus.TIMEOUT
                    parts = [part for artifact in final.artifacts for part in artifact.parts]
                    return self._result(request, parts, status, refs)
                except AgentExecutionError as exc:
                    if dispatched and exc.code is not AgentFailureCode.EXECUTION_STATE_UNKNOWN:
                        store.event(request.invocation_id, {"kind": "REMOTE_INTERRUPTED", "failure_code": exc.code.value,
                            "task_id": task_id})
                        raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN,
                            "remote completion is unconfirmed; reconcile its task instead of redispatching") from exc
                    raise
                except Exception as exc:
                    if dispatched:
                        raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN,
                            "A2A execution state is unknown; retained task must be reconciled") from exc
                    raise AgentExecutionError(AgentFailureCode.PROCESS_NOT_STARTED, "A2A request did not start") from exc

    def _result(self, request, parts, status, refs):
        from google.protobuf.json_format import MessageToDict
        from .codec import decode_json
        outputs = []
        for part in parts:
            if part.HasField("data"):
                outputs.append(output_envelope(request.required_output_profile, MessageToDict(part.data)))
            elif part.HasField("raw") and part.media_type == "application/json":
                outputs.append(output_envelope(request.required_output_profile, decode_json(part.raw)))
        return AgentExecutionResult(request.invocation_id, self.profile.profile_id, status, tuple(outputs),
            AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False), {}, AgentReport(),
            AgentDeliveryEvidence(request.invocation_id, request.context_id, request.task_sha256,
                request.task_byte_length, request.envelope_sha256, "a2a-sdk/1.1.2", True),
            evidence_refs=tuple(sorted(set(refs))))
