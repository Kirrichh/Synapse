"""One non-streaming HTTP exchange and its synchronous capture boundary.

No implicit retries, redirects, provider fallback, or result reconstruction.
An SDK retry reaches this transport again and obtains a new physical call ID.
"""

from dataclasses import dataclass
import socket
from typing import Mapping
import urllib.error
import urllib.request

from .capture import ProviderCapturePort

MAX_PROVIDER_BODY_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    request_id: str | None
    retry_after: str | None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_http_exchange(*, url: str, request: bytes, headers: Mapping[str, str],
                           timeout: float, capture: ProviderCapturePort | None = None,
                           logical_call_id: str = "uncaptured") -> ProviderHttpResponse:
    if type(request) is not bytes or not request or len(request) > MAX_PROVIDER_BODY_BYTES:
        raise ValueError("invalid provider request body")
    call_id = None if capture is None else capture.before_request(
        logical_call_id=logical_call_id, request=request)
    outbound = urllib.request.Request(url, data=request, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        try:
            response = opener.open(outbound, timeout=timeout)
        except urllib.error.HTTPError as error_response:
            response = error_response
        with response:
            raw = response.read(MAX_PROVIDER_BODY_BYTES + 1)
            if len(raw) > MAX_PROVIDER_BODY_BYTES:
                raise ValueError("provider response exceeds retained artifact bound")
            result = ProviderHttpResponse(response.code, raw,
                response.headers.get("x-request-id") or response.headers.get("request-id"),
                response.headers.get("retry-after"))
    except BaseException as exc:
        if capture is not None:
            code = "TIMEOUT" if isinstance(exc, (TimeoutError, socket.timeout)) else type(exc).__name__
            capture.request_failed(call_id=call_id, error_code=code)
        raise
    if capture is not None:
        capture.after_response(call_id=call_id, status_code=result.status_code,
            response=result.body, provider_request_id=result.request_id)
    return result
