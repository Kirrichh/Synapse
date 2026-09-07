"""Neutral synchronous evidence port at the physical provider HTTP boundary.

The caller must durably register before transmission and retain the response
before delivering it. This protocol knows no experiment, outcome, or admission
types. A capture failure propagates; it must never be treated as zero usage.
"""

from typing import Protocol


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

    def retain_trajectory(self, *, raw: bytes | None, process_status: str) -> None: ...
