"""Mini's in-process Chat Completions transport and durable capture bridge.

This is scoped to one existing worker invocation, with no CLI or service
lifecycle. Provider credentials stay in the parent. The Mini SDK and its
retries cross this exact HTTP boundary; a synchronous capture failure latches
the transport closed. Only the declared non-streaming profile is supported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import base64
import hashlib
import importlib.metadata
import sys
import json
import os
import re
import secrets
from pathlib import Path
import tempfile
import threading
from typing import Protocol
from urllib.parse import urlsplit

from synapse.llm.capture import CaptureUnavailable, WorkerCapturePort
from synapse.llm.http_transport import MAX_PROVIDER_BODY_BYTES, provider_http_exchange
from synapse.resource_usage import active_recorder, recording_resources, observed_operation

MINI_ACCOUNTING_PROFILE = "mini-2.4.6-litellm-openai-chat/v1"
MINI_RUNTIME_PROFILE = "mini-2.4.6-split-input-extension/v1"
MINI_MODEL_CLASS = "synapse.worker.mini_model.MiniAccountingModel"
GEMINI_CHAT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_REQUEST_FIELDS = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "temperature",
    "max_tokens", "max_completion_tokens", "top_p", "seed", "stop", "presence_penalty", "frequency_penalty",
    "response_format", "service_tier", "reasoning_effort", "user", "stream", "stream_options", "metadata",
    "store", "n", "logprobs", "top_logprobs"}


MINI_DEPENDENCIES = {"mini-swe-agent": "2.4.6", "litellm": "1.100.0", "openai": "2.54.0"}


def require_mini_dependencies() -> dict[str, str]:
    observed = {name: importlib.metadata.version(name) for name in MINI_DEPENDENCIES}
    if observed != MINI_DEPENDENCIES:
        raise CaptureUnavailable("Mini accounting dependencies differ from the accepted profile")
    return observed


def frozen_mini_runtime(command: list[str]) -> dict:
    """Fingerprint the installed SDK code and exact CLI before a Gold effect."""
    versions = require_mini_dependencies()
    expected = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
    if len(command) != 1 or Path(command[0]).resolve() != expected.resolve() or not expected.is_file():
        raise CaptureUnavailable("captured Mini must use the installed console entry point without overrides")
    mini = importlib.metadata.distribution("mini-swe-agent")
    if [(entry.name, entry.value) for entry in mini.entry_points if entry.name == "mini"] != [("mini", "minisweagent.run.mini:app")]:
        raise CaptureUnavailable("Mini console binding differs from the accepted profile")
    entry_files = [item for item in mini.files or () if Path(mini.locate_file(item)).resolve() == expected.resolve()]
    if len(entry_files) != 1 or entry_files[0].hash is None or entry_files[0].hash.mode != "sha256":
        raise CaptureUnavailable("Mini console entry has no installed distribution integrity record")
    observed_hash = base64.urlsafe_b64encode(hashlib.sha256(expected.read_bytes()).digest()).decode().rstrip("=")
    if observed_hash != entry_files[0].hash.value:
        raise CaptureUnavailable("Mini console entry differs from its installed distribution")
    distributions = {}
    for name in versions:
        dist = importlib.metadata.distribution(name)
        digest = hashlib.sha256()
        for item in sorted(dist.files or (), key=str):
            if item.suffix not in {".py", ".json", ".yaml", ".yml"} or ".." in item.parts:
                continue
            path = Path(dist.locate_file(item))
            if path.is_symlink():
                raise CaptureUnavailable("captured SDK sources may not be symbolic links")
            digest.update(str(item).encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
        distributions[name] = {"version": versions[name], "source_sha256": digest.hexdigest()}
    return {"profile": MINI_RUNTIME_PROFILE, "distributions": distributions}


@dataclass(frozen=True)
class MiniProviderConfiguration:
    model: str
    api_key: str | None = field(default=None, repr=False)
    endpoint: str = "https://api.openai.com/v1/chat/completions"
    timeout_seconds: float = 60.0
    credential_env: str | None = None

    def __post_init__(self):
        if type(self.model) is not str or not self.model or "/" in self.model:
            raise CaptureUnavailable("the captured Mini profile requires an explicit model without a routing prefix")
        if (self.api_key is None) == (self.credential_env is None):
            raise CaptureUnavailable("exactly one provider credential source is required")
        if self.api_key is not None and (type(self.api_key) is not str or not self.api_key):
            raise CaptureUnavailable("the captured provider credential is unavailable")
        if self.credential_env is not None and (type(self.credential_env) is not str
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.credential_env) is None):
            raise CaptureUnavailable("provider credential environment key is invalid")
        parsed = urlsplit(self.endpoint)
        loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        gemini_path = parsed.path == "/v1beta/openai/chat/completions"
        if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname
                or parsed.path not in {"/v1/chat/completions", "/v1beta/openai/chat/completions"}
                or parsed.scheme != "https" and not loopback
                or gemini_path and not (self.endpoint == GEMINI_CHAT_ENDPOINT or loopback)
                or parsed.hostname == "generativelanguage.googleapis.com" and not gemini_path):
            raise CaptureUnavailable("provider endpoint does not match the frozen HTTP profile")
        if gemini_path and not self.model.startswith("gemini-"):
            raise CaptureUnavailable("the Gemini endpoint requires an explicit Gemini model")
        if not 0 < self.timeout_seconds <= 600:
            raise CaptureUnavailable("provider timeout is outside the bounded worker profile")

    @property
    def provider(self) -> str:
        # The frozen endpoint selects the remote provider. Mini still speaks
        # the same non-streaming Chat Completions protocol to this transport.
        return "gemini" if urlsplit(self.endpoint).path == "/v1beta/openai/chat/completions" else "openai"


class WorkerAccountingPort(Protocol):
    def begin_invocation(self, *, invocation_id: str, attempt_id: str, context_id: str,
                         payload_sha256: str, payload_byte_length: int,
                         envelope_sha256: str) -> MiniProviderTransport: ...


class MiniProviderTransport:
    """One parent-owned transport; its port has no execution authority."""

    def __init__(self, *, configuration: MiniProviderConfiguration, capture: WorkerCapturePort):
        self.configuration = configuration
        self.capture = capture
        self._token = secrets.token_hex(32)
        self._failure = None
        self._server = None
        self._thread = None
        self._config_directory = None

    @property
    def address(self) -> str:
        if self._server is None:
            raise CaptureUnavailable("worker model transport is not live")
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def mini_configuration_path(self) -> str:
        return str(importlib.metadata.distribution("mini-swe-agent").locate_file("minisweagent/config/mini.yaml"))

    def child_configuration(self) -> dict[str, str]:
        return {"SYNAPSE_MINI_CAPTURE_ENDPOINT": self.address,
                "SYNAPSE_MINI_CAPTURE_CAPABILITY": self._token,
                "SYNAPSE_MINI_CAPTURE_MODEL": self.configuration.model,
                "SYNAPSE_MINI_CAPTURE_PROVIDER": self.configuration.provider,
                "MSWEA_GLOBAL_CONFIG_DIR": self._config_directory.name}

    def __enter__(self):
        self._api_key = self.configuration.api_key or os.environ.get(self.configuration.credential_env, "")
        if not self._api_key:
            raise CaptureUnavailable("required provider credential is unavailable before worker dispatch")
        owner = self
        self._config_directory = tempfile.TemporaryDirectory(prefix="synapse-mini-config-")
        # Mini requires the config file to exist to avoid interactive setup.
        # Its model and local capability are supplied by the sealed adapter.
        (Path(self._config_directory.name) / ".env").write_text("MSWEA_CONFIGURED=true\n", encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            @observed_operation("provider.transport")
            def do_POST(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner._token):
                    self._send(403, {"error": {"message": "invalid invocation capability"}})
                    return
                if owner._failure is not None:
                    self._send(503, {"error": {"message": "required capture unavailable"}})
                    return
                try:
                    if self.headers.get("Transfer-Encoding") is not None:
                        raise ValueError("chunked input is outside the captured profile")
                    length = int(self.headers.get("Content-Length", "-1"))
                    if not 0 < length <= MAX_PROVIDER_BODY_BYTES:
                        raise ValueError("invalid request length")
                    self.connection.settimeout(owner.configuration.timeout_seconds)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request")
                    value = json.loads(raw)
                    if type(value) is not dict:
                        raise ValueError("request must be a JSON object")
                    if self.path == "/capture/logical":
                        if set(value) != {"request_identity", "worker_profile"} or value["worker_profile"] != MINI_ACCOUNTING_PROFILE:
                            raise ValueError("unknown model capture profile")
                        call_id = owner.capture.register_logical_call(request_identity=value["request_identity"])
                        self._send(200, {"logical_call_id": call_id})
                    elif self.path == "/capture/logical/finish":
                        if set(value) != {"logical_call_id", "status"}:
                            raise ValueError("unknown logical completion")
                        owner.capture.finish_logical_call(**value)
                        self._send(200, {"retained": True})
                    elif self.path == "/v1/chat/completions":
                        logical_id = self.headers.get("X-Synapse-Logical-Call", "")
                        if (not logical_id or value.get("model") != owner.configuration.model
                                or value.get("stream", False) is not False or type(value.get("n", 1)) is not int or value.get("n", 1) != 1
                                or set(value) - _REQUEST_FIELDS or type(value.get("messages")) is not list):
                            raise ValueError("provider request is outside the captured profile")
                        result = provider_http_exchange(url=owner.configuration.endpoint, request=raw,
                            headers={"Content-Type": "application/json", "Authorization": "Bearer " + owner._api_key},
                            timeout=owner.configuration.timeout_seconds, capture=owner.capture, logical_call_id=logical_id)
                        self.send_response(result.status_code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(result.body)))
                        if result.request_id is not None:
                            self.send_header("x-request-id", result.request_id)
                        if result.retry_after is not None:
                            self.send_header("retry-after", result.retry_after)
                        self.end_headers()
                        self.wfile.write(result.body)
                    else:
                        self._send(404, {"error": {"message": "unknown transport operation"}})
                except (ValueError, UnicodeError):
                    self._send(400, {"error": {"message": "unsupported captured request"}})
                except (BrokenPipeError, ConnectionResetError):
                    # The provider response is already retained; a failed client
                    # delivery must not erase it or trigger a provider replay.
                    return
                except BaseException as exc:
                    owner._failure = type(exc).__name__
                    self._send(503, {"error": {"message": "required capture or transport unavailable"}})

            def _send(self, status, value):
                body = json.dumps(value, separators=(",", ":")).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        recorder = active_recorder()

        def serve():
            # Propagate the recorder, never another thread's CPU clock or
            # operation stack. Each actual request is measured in this thread.
            with recording_resources(recorder):
                self._server.serve_forever(poll_interval=0.05)

        self._thread = threading.Thread(target=serve, name="mini-provider-capture", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=self.configuration.timeout_seconds + 1)
            self._server = None
        if self._config_directory is not None:
            self._config_directory.cleanup()
            self._config_directory = None

    def finish_worker(self, *, raw: bytes | None, process_status: str) -> None:
        self.__exit__()
        self.capture.retain_trajectory(raw=raw, process_status=process_status)
