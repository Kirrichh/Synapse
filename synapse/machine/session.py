"""Closed control-session state machine; no domain dispatch or effect authority."""
from __future__ import annotations

from synapse.version import RUNTIME_VERSION

from .contract import METHODS, PROFILE, ProtocolError, message, refusal, validate


class ControlSession:
    def __init__(self) -> None:
        self.phase = "hello"
        self.frame_limit = PROFILE["bootstrap_max_frame_bytes"]
        self.peer_limit = 0
        self.closed = False

    def announcement(self) -> dict:
        return message("hello", component="core", component_version=RUNTIME_VERSION,
                       protocol_min=PROFILE["protocol_version"],
                       protocol_max=PROFILE["protocol_version"],
                       profile=PROFILE["profile_id"], max_frame_bytes=PROFILE["max_frame_bytes"])

    def receive(self, value: object) -> dict | None:
        if self.closed:
            raise ProtocolError("INVALID_SEQUENCE")
        try:
            incoming = validate(value)
        except ProtocolError as error:
            fatal = error.code == "SCHEMA_UNSUPPORTED"
            self.closed = fatal
            return refusal(error.code, fatal=fatal)
        kind = incoming["type"]
        if kind == "request":
            return self._request(incoming)
        if self.phase == "hello" and kind == "hello":
            return self._hello(incoming)
        if self.phase == "select" and kind == "select":
            return self._select(incoming)
        self.closed = True
        return refusal("INVALID_SEQUENCE", fatal=True)

    def _hello(self, incoming: dict) -> dict | None:
        version = PROFILE["protocol_version"]
        if (incoming["component"] != "shell" or
                incoming["profile"] != PROFILE["profile_id"] or
                not incoming["protocol_min"] <= version <= incoming["protocol_max"]):
            self.closed = True
            return refusal("PROFILE_UNSUPPORTED", fatal=True)
        self.peer_limit = incoming["max_frame_bytes"]
        self.phase = "select"
        return None

    def _select(self, incoming: dict) -> dict:
        if (incoming["profile"] != PROFILE["profile_id"] or
                incoming["protocol_version"] != PROFILE["protocol_version"] or
                incoming["max_frame_bytes"] > min(self.peer_limit, PROFILE["max_frame_bytes"])):
            self.closed = True
            return refusal("PROFILE_UNSUPPORTED", fatal=True)
        self.frame_limit = incoming["max_frame_bytes"]
        self.phase = "ready"
        return message("ready", profile=PROFILE["profile_id"],
                       protocol_version=PROFILE["protocol_version"],
                       max_frame_bytes=self.frame_limit, command_classes=["control"], domain_ready=False)

    def _request(self, incoming: dict) -> dict:
        request_id, method = incoming["request_id"], incoming["method"]
        if self.phase != "ready":
            return refusal("NOT_READY", fatal=False, request_id=request_id)
        if method not in METHODS:
            return refusal("METHOD_UNSUPPORTED", fatal=False, request_id=request_id)
        if incoming["params"]:
            return refusal("PARAMS_INVALID", fatal=False, request_id=request_id)
        if method == "machine.ping":
            result = {"status": "alive"}
        elif method == "machine.status":
            result = {"state": "ready", "domain_ready": False,
                      "profile": PROFILE["profile_id"], "max_frame_bytes": self.frame_limit}
        elif method == "machine.shutdown":
            self.closed = True
            self.phase = "stopped"
            result = {"status": "stopped", "pending_commands": 0}
        else:
            # A schema/dispatcher mismatch must never become a shutdown effect.
            raise ProtocolError("INTERNAL_ERROR")
        return message("result", request_id=request_id, method=method, result=result)
