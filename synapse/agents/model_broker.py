"""Synapse-owned model access for admitted local agents (network LOCAL_BROKER).

One broker serves one agent invocation on the loopback interface. The agent
receives only a per-invocation capability; the provider credential stays in
this process. Every physical request is registered and retained through the
caller's capture port before its response is delivered, so a capture failure
latches the broker closed instead of becoming zero usage. An agent retries an
unsuccessful logical request by naming it; each retry is a new physical call. When the invocation
carries local information, only the protocol's public conversation may reach
the provider: its roots, the provider's own replies and the protocol's fixed
correction message. No agent SDK, tool format or trajectory is interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import os
import re
import secrets
import threading
from typing import Protocol
from urllib.parse import urlsplit

from synapse.llm.capture import CaptureUnavailable, WorkerCapturePort
from synapse.llm.http_transport import MAX_PROVIDER_BODY_BYTES, provider_http_exchange
from synapse.resource_usage import active_recorder, observed_operation, recording_resources

#: Accounting profile of calls made through this broker; agents name it in
#: their response inventory and reconciliation compares both.
MODEL_BROKER_PROFILE = "synapse.agent.model-broker/v1"
GEMINI_CHAT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
#: What the agent process receives: where to call and the invocation capability.
ENDPOINT_ENVIRONMENT = "SYNAPSE_MODEL_ENDPOINT"
CAPABILITY_ENVIRONMENT = "SYNAPSE_MODEL_CAPABILITY"
MODEL_ENVIRONMENT = "SYNAPSE_MODEL_NAME"
LOGICAL_CALL_HEADER = "X-Synapse-Logical-Call"
_REQUEST_FIELDS = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "temperature",
    "max_tokens", "max_completion_tokens", "top_p", "seed", "stop", "presence_penalty", "frequency_penalty",
    "response_format", "service_tier", "reasoning_effort", "user", "stream", "stream_options", "metadata",
    "store", "n", "logprobs", "top_logprobs"}


@dataclass(frozen=True)
class ModelConnection:
    """The frozen provider binding an admitted agent profile declares."""

    model: str
    endpoint: str
    credential_env: str
    timeout_seconds: int = 60

    def __post_init__(self):
        if type(self.model) is not str or not self.model or "/" in self.model:
            raise CaptureUnavailable("model access requires an explicit model without a routing prefix")
        if type(self.credential_env) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.credential_env) is None:
            raise CaptureUnavailable("provider credential environment key is invalid")
        if type(self.endpoint) is not str:
            raise CaptureUnavailable("provider endpoint must be exact text")
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
        if type(self.timeout_seconds) is not int or not 0 < self.timeout_seconds <= 600:
            raise CaptureUnavailable("provider timeout is outside the bounded profile")

    @property
    def provider(self) -> str:
        # The endpoint selects the remote provider; the wire is Chat Completions.
        return "gemini" if urlsplit(self.endpoint).path == "/v1beta/openai/chat/completions" else "openai"


def model_connection(value: object) -> ModelConnection:
    """Decode an agent's declared model access; credentials are never part of it."""
    if (type(value) is not dict or set(value) != {"profile", "model", "endpoint", "credential_env", "timeout_seconds"}
            or value["profile"] != MODEL_BROKER_PROFILE):
        raise ValueError("model access declaration has an unknown profile")
    return ModelConnection(model=value["model"], endpoint=value["endpoint"],
                           credential_env=value["credential_env"], timeout_seconds=value["timeout_seconds"])


class ModelAccountingPort(Protocol):
    """The caller's durable capture owner for one agent invocation."""

    def open_capture(self, *, invocation: dict, connection: ModelConnection) -> WorkerCapturePort: ...


def _wire(message: object) -> bytes:
    try:
        return json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("provider message is outside the data profile") from exc


def _matches_public(expected, observed) -> bool:
    """A client may omit an already-public null; it cannot add or change fields."""
    if type(expected) is not type(observed):
        return False
    if type(expected) is dict:
        if observed.keys() - expected.keys():
            return False
        return all(_matches_public(value, observed[key]) if key in observed else value is None
                   for key, value in expected.items())
    if type(expected) is list:
        return len(expected) == len(observed) and all(_matches_public(a, b) for a, b in zip(expected, observed))
    return expected == observed


class PublicConversation:
    """The only history a provider may receive while local information is delivered.

    It is the protocol's two roots, then for every observed provider reply that
    reply and the protocol's fixed correction message, in order.
    """

    def __init__(self, roots: list[dict], *, correction: str):
        if (type(roots) is not list or len(roots) != 2 or any(type(item) is not dict for item in roots)
                or [item.get("role") for item in roots] != ["system", "user"]
                or type(correction) is not str or not correction):
            raise ValueError("public conversation requires its protocol roots")
        self._roots = json.loads(_wire(roots))
        self._correction = {"role": "user", "content": correction}
        self._replies = []

    def require(self, messages: object) -> None:
        if type(messages) is not list:
            raise ValueError("provider history must be an exact list")
        expected = list(self._roots)
        for reply in self._replies:
            expected.extend((reply, self._correction))
        if len(messages) != len(expected) or not all(
                _matches_public(json.loads(_wire(a)), json.loads(_wire(b))) for a, b in zip(expected, messages)):
            raise ValueError("message has no permitted public provenance")

    def observe(self, body: bytes) -> None:
        """Only a retained successful single-choice reply extends the history."""
        value = json.loads(body)
        choices = value.get("choices") if type(value) is dict else None
        if type(choices) is not list or len(choices) != 1 or type(choices[0].get("message")) is not dict:
            return
        self._replies.append(json.loads(_wire(choices[0]["message"])))


class ModelBroker:
    """One loopback provider transport for one agent invocation."""

    def __init__(self, *, connection: ModelConnection, capture: WorkerCapturePort,
                 conversation: PublicConversation | None = None):
        if type(connection) is not ModelConnection:
            raise TypeError("model broker requires an exact ModelConnection")
        if conversation is not None and type(conversation) is not PublicConversation:
            raise TypeError("model broker requires an exact public conversation")
        self.connection = connection
        self.capture = capture
        self._conversation = conversation
        self._token = secrets.token_hex(32)
        self._failure = None
        self._server = None
        self._thread = None
        self._api_key = None
        self._open = {}

    def environment(self) -> tuple[tuple[str, str], ...]:
        if self._server is None:
            raise CaptureUnavailable("model broker is not live")
        return ((CAPABILITY_ENVIRONMENT, self._token),
                (ENDPOINT_ENVIRONMENT, f"http://127.0.0.1:{self._server.server_port}"),
                (MODEL_ENVIRONMENT, self.connection.model))

    def __enter__(self):
        self._api_key = os.environ.get(self.connection.credential_env, "")
        if not self._api_key:
            raise CaptureUnavailable("required provider credential is unavailable before agent dispatch")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _authorized(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner._token):
                    self._send(403, {"error": {"message": "invalid invocation capability"}})
                    return False
                return True

            @observed_operation("provider.transport")
            def do_POST(self):
                if not self._authorized():
                    return
                if owner._failure is not None:
                    self._send(503, {"error": {"message": "required capture unavailable"}})
                    return
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": {"message": "unknown broker operation"}})
                    return
                try:
                    if self.headers.get("Transfer-Encoding") is not None:
                        raise ValueError("chunked input is outside the captured profile")
                    length = int(self.headers.get("Content-Length", "-1"))
                    if not 0 < length <= MAX_PROVIDER_BODY_BYTES:
                        raise ValueError("invalid request length")
                    self.connection.settimeout(owner.connection.timeout_seconds)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request")
                    value = json.loads(raw)
                    if (type(value) is not dict or value.get("model") != owner.connection.model
                            or value.get("stream", False) is not False or value.get("n", 1) != 1
                            or type(value.get("n", 1)) is not int or set(value) - _REQUEST_FIELDS
                            or type(value.get("messages")) is not list):
                        raise ValueError("provider request is outside the captured profile")
                    if owner._conversation is not None:
                        # Local information has no public projection: nothing but the
                        # public conversation, and no request field an agent chose.
                        if set(value) != {"model", "messages"}:
                            raise ValueError("provider request fields differ from the public profile")
                        owner._conversation.require(value["messages"])
                except (ValueError, UnicodeError):
                    self._send(400, {"error": {"message": "unsupported captured request"}})
                    return
                identity = hashlib.sha256(_wire(value["messages"])).hexdigest()
                retried = self.headers.get(LOGICAL_CALL_HEADER)
                if retried is not None and owner._open.get(retried) != identity:
                    # A retry continues one unfinished logical request with the same messages.
                    self._send(400, {"error": {"message": "unknown or finished logical request"}})
                    return
                try:
                    logical_id = retried or owner.capture.register_logical_call(request_identity=identity)
                    owner._open[logical_id] = identity
                    result = provider_http_exchange(url=owner.connection.endpoint, request=raw,
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + owner._api_key},
                        timeout=owner.connection.timeout_seconds, capture=owner.capture, logical_call_id=logical_id)
                    if result.status_code == 200:
                        # A failed attempt stays open for the agent's retry until the invocation ends.
                        owner.capture.finish_logical_call(logical_call_id=logical_id, status="COMPLETED")
                        del owner._open[logical_id]
                        if owner._conversation is not None:
                            try:
                                owner._conversation.observe(result.body)
                            except (ValueError, UnicodeError, RecursionError):
                                pass  # An unreadable reply cannot extend the public history.
                except BaseException as exc:
                    owner._failure = type(exc).__name__
                    self._send(503, {"error": {"message": "required capture or transport unavailable"}})
                    return
                try:
                    self.send_response(result.status_code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(result.body)))
                    self.send_header(LOGICAL_CALL_HEADER, logical_id)
                    if result.request_id is not None:
                        self.send_header("x-request-id", result.request_id)
                    if result.retry_after is not None:
                        self.send_header("retry-after", result.retry_after)
                    self.end_headers()
                    self.wfile.write(result.body)
                except (BrokenPipeError, ConnectionResetError):
                    # The response is already retained; a lost delivery is not a replay.
                    return

            def do_GET(self):
                if not self._authorized():
                    return
                if self.path != "/synapse/conversation" or owner._conversation is None:
                    self._send(404, {"error": {"message": "unknown broker operation"}})
                    return
                self._send(200, {"messages": owner._conversation._roots,
                                 "correction": owner._conversation._correction})

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
            # Propagate the recorder, never another thread's CPU clock.
            with recording_resources(recorder):
                self._server.serve_forever(poll_interval=0.05)

        self._thread = threading.Thread(target=serve, name="synapse-model-broker", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=self.connection.timeout_seconds + 1)
            self._server = None

    def finish(self, *, raw: bytes | None, process_status: str, inventory: dict | None) -> None:
        """Close the broker, then retain the agent's record and response inventory."""
        self.__exit__()
        for logical_id in sorted(self._open):
            # Never retried to success: the logical request ended without a response.
            self.capture.finish_logical_call(logical_call_id=logical_id, status="FAILED")
        self._open.clear()
        self.capture.finish_invocation(raw=raw, process_status=process_status, inventory=inventory)


__all__ = [
    "CAPABILITY_ENVIRONMENT", "ENDPOINT_ENVIRONMENT", "GEMINI_CHAT_ENDPOINT", "LOGICAL_CALL_HEADER",
    "MODEL_BROKER_PROFILE", "MODEL_ENVIRONMENT", "ModelAccountingPort", "ModelBroker", "ModelConnection",
    "PublicConversation", "model_connection",
]
