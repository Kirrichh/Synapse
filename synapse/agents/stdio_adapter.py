"""Generic local STDIO adapter for external agents.

The child process is not trusted with Synapse authority. It receives one exact
JSON request on stdin and must emit one exact JSON response on stdout; logs go
to stderr. OS/network isolation is deliberately outside this transport and may
be supplied by an OCI-backed adapter using the same AgentAdapter contract.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

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
_MAX_STDOUT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class StdioAgentConfig:
    command: tuple[str, ...]
    timeout_seconds: int
    profile: AgentProfile
    environment: tuple[tuple[str, str], ...] = ()

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


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_b64(value: object) -> bytes:
    if type(value) is not str or not value:
        raise ValueError("STDIO agent output payload must be base64url text")
    encoded = value.encode("ascii")
    raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
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
    if len(raw) > _MAX_STDOUT_BYTES:
        raise ValueError("STDIO agent response exceeds the bounded output size")
    try:
        value = json.loads(raw.decode("utf-8"))
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

    def __init__(self, config: StdioAgentConfig) -> None:
        if type(config) is not StdioAgentConfig:
            raise TypeError("STDIO adapter requires an exact StdioAgentConfig")
        self._config = config

    @property
    def profile(self) -> AgentProfile:
        return self._config.profile

    @property
    def config(self) -> StdioAgentConfig:
        return self._config

    def execute(self, request: AgentExecutionRequest, runtime: AgentRuntimeContext) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest or type(runtime) is not AgentRuntimeContext:
            raise TypeError("STDIO adapter requires exact request and runtime records")
        request.__post_init__()
        runtime.__post_init__()
        child_env = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "COMSPEC", "WINDIR", "TEMP", "TMP", "TMPDIR",
            "USERPROFILE", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE", "VIRTUAL_ENV",
        }}
        child_env.update(dict(self._config.environment))
        transport_name = "synapse-agent-stdio/v1"
        try:
            completed = subprocess.run(
                self._config.command,
                cwd=runtime.worktree_path,
                input=_request_payload(request),
                capture_output=True,
                timeout=self._config.timeout_seconds,
                check=False,
                env=child_env,
            )
        except subprocess.TimeoutExpired:
            return AgentExecutionResult(
                invocation_id=request.invocation_id,
                agent_profile_id=self.profile.profile_id,
                status=AgentExecutionStatus.TIMEOUT,
                outputs=(),
                usage=AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False),
                diagnostics={},
                report=AgentReport(failure_reason="agent_process_timeout"),
                delivery_evidence=AgentDeliveryEvidence(
                    invocation_id=request.invocation_id, context_id=request.context_id,
                    task_sha256=request.task_sha256, task_byte_length=request.task_byte_length,
                    envelope_sha256=request.envelope_sha256, transport_name=transport_name,
                    process_started=True, information_sha256=request.information_sha256,
                    information_byte_length=request.information_byte_length,
                ),
            )
        except OSError:
            return AgentExecutionResult(
                invocation_id=request.invocation_id,
                agent_profile_id=self.profile.profile_id,
                status=AgentExecutionStatus.ERROR,
                outputs=(),
                usage=AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False),
                diagnostics={},
                report=AgentReport(failure_reason="agent_process_not_started"),
                delivery_evidence=AgentDeliveryEvidence(
                    invocation_id=request.invocation_id, context_id=request.context_id,
                    task_sha256=request.task_sha256, task_byte_length=request.task_byte_length,
                    envelope_sha256=request.envelope_sha256, transport_name=transport_name,
                    process_started=False, information_sha256=request.information_sha256,
                    information_byte_length=request.information_byte_length,
                ),
            )
        if completed.returncode != 0 and not completed.stdout:
            return AgentExecutionResult(
                invocation_id=request.invocation_id,
                agent_profile_id=self.profile.profile_id,
                status=AgentExecutionStatus.ERROR,
                outputs=(),
                usage=AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False),
                diagnostics={"returncode": completed.returncode},
                report=AgentReport(failure_reason="agent_process_failed_without_protocol_response"),
                delivery_evidence=AgentDeliveryEvidence(
                    invocation_id=request.invocation_id, context_id=request.context_id,
                    task_sha256=request.task_sha256, task_byte_length=request.task_byte_length,
                    envelope_sha256=request.envelope_sha256, transport_name=transport_name,
                    process_started=True, information_sha256=request.information_sha256,
                    information_byte_length=request.information_byte_length,
                ),
            )
        result = _parse_response(
            completed.stdout,
            request=request,
            profile=self.profile,
            transport_name=transport_name,
            process_started=True,
        )
        if result.outputs and any(item.output_schema not in self.profile.output_profiles for item in result.outputs):
            raise ValueError("STDIO agent emitted an output schema outside its admitted profile")
        return result


__all__ = [
    "STDIO_REQUEST_V1",
    "STDIO_RESPONSE_V1",
    "StdioAgentAdapter",
    "StdioAgentConfig",
]
