"""Generic local STDIO adapter for external agents.

The child process is not trusted with Synapse authority. It receives one
length-prefixed UTF-8 JSON frame on stdin and must emit one exact framed response
on stdout; logs belong on stderr. The shared supervisor owns OS/network
isolation, cancellation, resource limits and retained process evidence.

An admitted profile with LOCAL_BROKER network reaches its model only through
Synapse's per-invocation model broker; the process receives the broker address
and capability, never a provider credential. A profile may declare the Synapse
local-edit protocol it speaks; Synapse then interprets its proposal.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from dataclasses import replace

from synapse.llm.capture import inspect_response_inventory

from .codec import decode_json
from .model_broker import (CAPABILITY_ENVIRONMENT, ENDPOINT_ENVIRONMENT, MODEL_ENVIRONMENT, ModelBroker,
                           ModelConnection, PublicConversation, model_connection)
from .runtime import run_process
from .policy import AgentExecutionError, AgentFailureCode
from .execution import failure_result

from .contracts import (
    AgentDeliveryEvidence,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentOutputEnvelope,
    AgentProfile,
    AgentReport,
    AgentRuntimeContext,
    AgentTokenStatus,
    AgentUsage,
)


STDIO_REQUEST_V1 = "synapse.agent.stdio-request/v1"
STDIO_RESPONSE_V1 = "synapse.agent.stdio-response/v1"
_MAX_FRAME_BYTES = 32 * 1024 * 1024
_FRAME_HEADER_BYTES = 4


@dataclass(frozen=True)
class StdioAgentConfig:
    command: tuple[str, ...]
    timeout_seconds: int
    profile: AgentProfile
    environment: tuple[tuple[str, str], ...] = ()
    protocol: str | None = None
    model_access: ModelConnection | None = None

    def __post_init__(self) -> None:
        if type(self.command) is not tuple or not self.command or any(
            type(item) is not str or not item or "\x00" in item for item in self.command
        ):
            raise ValueError("STDIO agent command must be non-empty exact argv tokens")
        if type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise ValueError("STDIO agent timeout must be a positive exact integer")
        if type(self.profile) is not AgentProfile:
            raise TypeError("STDIO agent requires an exact AgentProfile")
        if type(self.environment) is not tuple or any(
            type(item) is not tuple or len(item) != 2
            or type(item[0]) is not str or not item[0]
            or type(item[1]) is not str
            for item in self.environment
        ):
            raise TypeError("STDIO agent environment must be exact key/value pairs")
        keys = tuple(item[0] for item in self.environment)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("STDIO agent environment keys must be sorted and unique")
        if {CAPABILITY_ENVIRONMENT, ENDPOINT_ENVIRONMENT, MODEL_ENVIRONMENT} & set(keys):
            raise ValueError("model broker variables belong to Synapse, not to the declaration")
        if self.protocol is not None:
            from synapse.worker.local_edits import LOCAL_EDIT_PROFILES
            if self.protocol not in LOCAL_EDIT_PROFILES:
                raise ValueError("STDIO agent declares an unknown Synapse protocol")
        if self.model_access is not None and type(self.model_access) is not ModelConnection:
            raise TypeError("STDIO model access must be an exact ModelConnection")
        if (self.model_access is not None) != (self.profile.runtime_policy.network == "LOCAL_BROKER"):
            raise ValueError("model access and the LOCAL_BROKER network policy require each other")
        if self.model_access is not None and self.profile.model_name != self.model_access.model:
            raise ValueError("STDIO profile model differs from its declared model access")


def _frame(payload: bytes) -> bytes:
    if type(payload) is not bytes or len(payload) > _MAX_FRAME_BYTES:
        raise ValueError("STDIO protocol frame exceeds its bounded size")
    return len(payload).to_bytes(_FRAME_HEADER_BYTES, "big") + payload


def _unframe(raw: bytes) -> bytes:
    if type(raw) is not bytes or len(raw) < _FRAME_HEADER_BYTES:
        raise ValueError("STDIO agent response lacks a complete frame header")
    length = int.from_bytes(raw[:_FRAME_HEADER_BYTES], "big")
    if length > _MAX_FRAME_BYTES:
        raise ValueError("STDIO agent response frame exceeds its bounded size")
    if len(raw) != _FRAME_HEADER_BYTES + length:
        raise ValueError("STDIO agent response contains truncated or trailing protocol bytes")
    return raw[_FRAME_HEADER_BYTES:]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_b64(value: object) -> bytes:
    if type(value) is not str or not value:
        raise ValueError("STDIO agent output payload must be base64url text")
    try:
        encoded = value.encode("ascii")
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("STDIO agent output payload is invalid base64url") from exc
    if _b64(raw) != value:
        raise ValueError("STDIO agent output payload is not canonical base64url")
    return raw


def _request_payload(request: AgentExecutionRequest) -> bytes:
    value = {
        "schema_version": STDIO_REQUEST_V1,
        "invocation_id": request.invocation_id,
        "attempt_id": request.attempt_id,
        "context_id": request.context_id,
        "task": {
            "text": request.task_text,
            "sha256": request.task_sha256,
            "byte_length": request.task_byte_length,
        },
        "envelope_sha256": request.envelope_sha256,
        "required_capabilities": list(request.required_capabilities),
        "required_output_profile": request.required_output_profile,
        "allowed_scope": list(request.allowed_scope),
        "required_effect_classes": list(request.required_effect_classes),
        "allowed_effects": list(request.allowed_effects or ()),
        "information_policy": request.information_policy.value,
        "local_information": None if request.information_text is None else {
            "text": request.information_text,
            "sha256": request.information_sha256,
            "byte_length": request.information_byte_length,
        },
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "media_type": item.media_type,
                "sha256": item.sha256,
                "byte_length": item.byte_length,
                "path": item.path,
                "access_mode": item.access_mode,
            }
            for item in request.artifacts
        ],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _usage(value: object) -> AgentUsage:
    if type(value) is not dict or set(value) != {
        "token_status", "input_tokens", "output_tokens", "thinking_tokens",
        "total_tokens", "thinking_included", "diagnostics"
    }:
        raise ValueError("STDIO agent usage has an unknown shape")
    return AgentUsage(
        token_status=AgentTokenStatus(value["token_status"]),
        input_tokens=value["input_tokens"],
        output_tokens=value["output_tokens"],
        thinking_tokens=value["thinking_tokens"],
        total_tokens=value["total_tokens"],
        thinking_included=value["thinking_included"],
        diagnostics=value["diagnostics"],
    )


def _parse_response(raw: bytes, *, request: AgentExecutionRequest, profile: AgentProfile,
                    transport_name: str, process_started: bool) -> AgentExecutionResult:
    payload_bytes = _unframe(raw)
    try:
        value = decode_json(payload_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("STDIO agent response is not valid UTF-8 JSON") from exc
    if type(value) is not dict or set(value) != {
        "schema_version", "invocation_id", "status", "outputs", "usage", "diagnostics", "report"
    } or value["schema_version"] != STDIO_RESPONSE_V1:
        raise ValueError("STDIO agent response has an unknown shape")
    if value["invocation_id"] != request.invocation_id:
        raise ValueError("STDIO agent response invocation differs from request")
    outputs_value = value["outputs"]
    if type(outputs_value) is not list:
        raise ValueError("STDIO agent outputs must be a list")
    outputs: list[AgentOutputEnvelope] = []
    for item in outputs_value:
        if type(item) is not dict or set(item) != {"output_schema", "media_type", "payload_base64url"}:
            raise ValueError("STDIO agent output has an unknown shape")
        payload = _decode_b64(item["payload_base64url"])
        outputs.append(AgentOutputEnvelope(
            output_schema=item["output_schema"],
            media_type=item["media_type"],
            payload=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_length=len(payload),
        ))
    report = value["report"]
    if type(report) is not dict or set(report) != {"summary", "failure_reason"}:
        raise ValueError("STDIO agent report has an unknown shape")
    if type(value["diagnostics"]) is not dict:
        raise ValueError("STDIO agent diagnostics must be an object")
    return AgentExecutionResult(
        invocation_id=request.invocation_id,
        agent_profile_id=profile.profile_id,
        status=AgentExecutionStatus(value["status"]),
        outputs=tuple(outputs),
        usage=_usage(value["usage"]),
        diagnostics=value["diagnostics"],
        report=AgentReport(summary=report["summary"], failure_reason=report["failure_reason"]),
        delivery_evidence=AgentDeliveryEvidence(
            invocation_id=request.invocation_id,
            context_id=request.context_id,
            task_sha256=request.task_sha256,
            task_byte_length=request.task_byte_length,
            envelope_sha256=request.envelope_sha256,
            transport_name=transport_name,
            process_started=process_started,
            information_sha256=request.information_sha256,
            information_byte_length=request.information_byte_length,
        ),
    )


class StdioAgentAdapter:
    """Run one explicitly configured local agent over the bounded STDIO protocol."""

    def __init__(self, config: StdioAgentConfig, *, accounting=None) -> None:
        if type(config) is not StdioAgentConfig:
            raise TypeError("STDIO adapter requires an exact StdioAgentConfig")
        if accounting is not None and config.model_access is None:
            raise ValueError("model accounting requires declared model access")
        self._config = config
        self._accounting = accounting

    @property
    def profile(self) -> AgentProfile:
        return self._config.profile

    @property
    def config(self) -> StdioAgentConfig:
        return self._config

    @property
    def local_edit_protocol(self) -> str | None:
        return self._config.protocol

    @property
    def accounting(self):
        return self._accounting

    def execute(self, request: AgentExecutionRequest, runtime: AgentRuntimeContext) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest or type(runtime) is not AgentRuntimeContext:
            raise TypeError("STDIO adapter requires exact request and runtime records")
        request.__post_init__()
        runtime.__post_init__()
        request_frame = _frame(_request_payload(request))
        bounded_request = replace(request, resource_budget=replace(request.resource_budget,
            timeout_seconds=min(request.resource_budget.timeout_seconds, self._config.timeout_seconds)))
        if self._config.model_access is None:
            observation = run_process(request=bounded_request, runtime=runtime, profile=self.profile,
                argv=self._config.command, input_bytes=request_frame, environment=self._config.environment)
            return self._result(request, observation)
        if self._accounting is None:
            raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "model access requires its bound accounting owner")
        conversation = None
        if request.information_text is not None:
            # Local information never reaches a provider outside the protocol's
            # public conversation; without a protocol there is none.
            if self._config.protocol is None:
                raise AgentExecutionError(AgentFailureCode.LOCAL_INFORMATION_POLICY_VIOLATION,
                    "local information with model access requires a Synapse protocol")
            from synapse.worker.local_edits import LOCAL_EDIT_CORRECTION, public_messages
            conversation = PublicConversation(public_messages(request.task_text, self._config.protocol),
                                              correction=LOCAL_EDIT_CORRECTION)
        capture = self._accounting.open_capture(invocation={
            "invocation_id": request.invocation_id, "attempt_id": request.attempt_id,
            "context_id": request.context_id, "payload_sha256": request.task_sha256,
            "payload_byte_length": request.task_byte_length, "envelope_sha256": request.envelope_sha256,
        }, connection=self._config.model_access)
        broker = ModelBroker(connection=self._config.model_access, capture=capture, conversation=conversation)
        started = False
        try:
            with broker:
                started = True
                observation = run_process(request=bounded_request, runtime=runtime, profile=self.profile,
                    argv=self._config.command, input_bytes=request_frame,
                    environment=tuple(sorted((*self._config.environment, *broker.environment()))))
        except BaseException:
            try:
                broker.finish(raw=None, process_status="INTERRUPTED" if started else "NOT_STARTED", inventory=None)
            except Exception:
                pass  # The original failure is the invocation's outcome.
            raise
        result = self._result(request, observation)
        try:
            inventory = inspect_response_inventory(result.diagnostics.get("response_inventory"))
        except ValueError:
            inventory = None  # Reconciliation reports the missing account; it never guesses one.
        status = ("EXITED" if observation.failure is None
                  else "TIMEOUT" if observation.failure is AgentFailureCode.TIMEOUT else "INTERRUPTED")
        try:
            broker.finish(raw=observation.stdout, process_status=status, inventory=inventory)
        except Exception:
            # The agent's effect cannot be undone by an accounting outage; the
            # retained open prefix makes completeness fail closed.
            pass
        return result

    def _result(self, request, observation):
        if observation.failure is not None:
            return failure_result(request, self.profile, observation.failure, started=True,
                                  evidence_refs=observation.evidence_refs)
        if observation.returncode != 0 and not observation.stdout:
            return failure_result(request, self.profile, AgentFailureCode.NATIVE_PROTOCOL_ERROR,
                                  started=True, evidence_refs=observation.evidence_refs)
        try:
            result = _parse_response(observation.stdout, request=request, profile=self.profile,
                                    transport_name="synapse-agent-stdio/v1", process_started=True)
        except (ValueError, TypeError, KeyError, UnicodeError):
            return failure_result(request, self.profile, AgentFailureCode.NATIVE_PROTOCOL_ERROR,
                                  started=True, evidence_refs=observation.evidence_refs)
        if observation.returncode != 0 and result.status is AgentExecutionStatus.COMPLETED:
            return failure_result(request, self.profile, AgentFailureCode.NATIVE_PROTOCOL_ERROR,
                                  started=True, evidence_refs=observation.evidence_refs)
        return replace(result, evidence_refs=observation.evidence_refs)


__all__ = [
    "STDIO_REQUEST_V1",
    "STDIO_RESPONSE_V1",
    "StdioAgentAdapter",
    "StdioAgentConfig",
]


class StdioAdapterFactory:
    def create(self, configuration):
        from .configuration import profile_from_dict
        from .contracts import AgentTransportKind, LocalInformationPolicy
        from .policy import IsolationKind
        profile = profile_from_dict(configuration['profile'])
        native = configuration['native']
        if profile.transport is not AgentTransportKind.STDIO:
            raise ValueError('STDIO factory requires its exact transport profile')
        # A trusted process is admitted by the operator's retained acceptance
        # evidence like every profile; third-party agents use OS isolation.
        if profile.runtime_policy.isolation not in (IsolationKind.BUBBLEWRAP, IsolationKind.OCI,
                                                    IsolationKind.TRUSTED_PROCESS):
            raise ValueError('local STDIO agents require OS isolation or trusted admission')
        if (type(native) is not dict or not {'command', 'environment'} <= set(native)
                or set(native) - {'command', 'environment', 'protocol', 'model_access'}):
            raise ValueError('STDIO configuration requires exact argv and environment')
        if type(native['command']) is not list or type(native['environment']) is not dict:
            raise ValueError('invalid STDIO native configuration')
        protocol = native.get('protocol')
        if protocol is not None and profile.local_information_policy is not LocalInformationPolicy.LOCAL_ONLY:
            raise ValueError('a Synapse protocol requires local information delivery')
        access = None if 'model_access' not in native else model_connection(native['model_access'])
        context = configuration.get('context') or {}
        accounting = context.get('model_accounting') if access is not None else None
        return StdioAgentAdapter(StdioAgentConfig(command=tuple(native['command']),
            environment=tuple(sorted(native['environment'].items())),
            timeout_seconds=profile.resource_limits.timeout_seconds, profile=profile,
            protocol=protocol, model_access=access), accounting=accounting)
