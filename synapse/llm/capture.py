"""Neutral synchronous evidence port at the physical provider HTTP boundary.

The caller must durably register before transmission and retain the response
before delivering it. This protocol knows no experiment, outcome, or admission
types. A capture failure propagates; it must never be treated as zero usage.
"""

from typing import Protocol

#: What an agent reports about its own provider responses. Synapse compares it
#: with the physical capture; it never parses an agent's private trajectory.
AGENT_RESPONSE_INVENTORY_V1 = "synapse.agent.response-inventory/v1"
MAX_INVENTORY_RESPONSES = 4096


class CaptureUnavailable(RuntimeError):
    """Required accounting cannot precede or retain an external effect."""


class ProviderCapturePort(Protocol):
    def before_request(self, *, logical_call_id: str, request: bytes) -> str: ...

    def after_response(self, *, call_id: str, status_code: int, response: bytes,
                       provider_request_id: str | None) -> None: ...

    def request_failed(self, *, call_id: str, error_code: str) -> None: ...


class WorkerCapturePort(ProviderCapturePort, Protocol):
    def register_logical_call(self, *, request_identity: str) -> str: ...

    def finish_logical_call(self, *, logical_call_id: str, status: str) -> None: ...

    def finish_invocation(self, *, raw: bytes | None, process_status: str,
                          inventory: dict | None = None) -> None: ...


def response_inventory(*, worker_profile: str, declared_calls: int, responses) -> dict:
    """Build the neutral inventory of one agent invocation's provider responses."""
    value = {"schema_version": AGENT_RESPONSE_INVENTORY_V1, "worker_profile": worker_profile,
             "declared_calls": declared_calls,
             "responses": [{"logical_call_id": item["logical_call_id"], "usage": item["usage"]} for item in responses]}
    return inspect_response_inventory(value)


def inspect_response_inventory(value: object) -> dict:
    """Validate an inventory's shape only; its truth is decided against the capture."""
    if (type(value) is not dict or set(value) != {"schema_version", "worker_profile", "declared_calls", "responses"}
            or value["schema_version"] != AGENT_RESPONSE_INVENTORY_V1
            or type(value["worker_profile"]) is not str or not value["worker_profile"]
            or type(value["declared_calls"]) is not int or value["declared_calls"] < 0
            or type(value["responses"]) is not list or len(value["responses"]) > MAX_INVENTORY_RESPONSES
            or any(type(item) is not dict or set(item) != {"logical_call_id", "usage"}
                   or (item["logical_call_id"] is not None and type(item["logical_call_id"]) is not str)
                   or (item["usage"] is not None and type(item["usage"]) is not dict)
                   for item in value["responses"])):
        raise ValueError("agent response inventory has an unknown contract")
    return value
