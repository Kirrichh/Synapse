"""Internal subprocess entry for native libraries. Not a Synapse run command.

Only the supervisor invokes this module. Libraries and their dependencies live
inside its sandbox; stdout remains a single bounded protocol response.
"""

import base64
from contextlib import redirect_stdout
import hashlib
import json
import os
import sys

from .codec import canonical_bytes, decode_json


MAX_FRAME_BYTES = 32 * 1024**2


def native_response(request, status, outputs=(), *, failure=None, usage=None):
    return {
        "schema_version": "synapse.agent.stdio-response/v1", "invocation_id": request["invocation_id"],
        "status": status, "outputs": [
            {"output_schema": schema, "media_type": "application/json",
             "payload_base64url": base64.urlsafe_b64encode(canonical_bytes(value)).rstrip(b"=").decode("ascii")}
            for schema, value in outputs],
        "usage": usage or {"token_status": "UNAVAILABLE", "input_tokens": None, "output_tokens": None,
            "thinking_tokens": None, "total_tokens": None, "thinking_included": False, "diagnostics": {}},
        "diagnostics": {} if failure is None else {"failure_code": failure},
        "report": {"summary": None, "failure_reason": failure},
    }


def main():
    protocol = sys.stdout.buffer
    length = sys.stdin.buffer.read(4)
    if len(length) != 4 or int.from_bytes(length, "big") > MAX_FRAME_BYTES:
        raise ValueError("invalid native request frame")
    raw = sys.stdin.buffer.read(int.from_bytes(length, "big"))
    request = decode_json(raw)
    if request.get("schema_version") != "synapse.agent.stdio-request/v1":
        raise ValueError("unknown native request schema")
    task = request["task"]["text"].encode("utf-8")
    if hashlib.sha256(task).hexdigest() != request["task"]["sha256"] or len(task) != request["task"]["byte_length"]:
        raise ValueError("native task binding differs")
    configuration = decode_json(os.environ["SYNAPSE_AGENT_NATIVE_CONFIGURATION"].encode("utf-8"))
    with redirect_stdout(sys.stderr):
        try:
            if sys.argv[1:] == ["docling"]:
                from .docling_adapter import extract_documents
                result = extract_documents(request, configuration)
            elif sys.argv[1:] == ["acp"]:
                from .acp_adapter import execute_acp
                result = execute_acp(request, configuration)
            else:
                raise ValueError("unknown native library driver")
        except Exception as exc:
            # Private exception text stays local; no foreign exception is a verdict.
            print(type(exc).__name__, file=sys.stderr)
            result = native_response(request, "ERROR", failure="NATIVE_PROTOCOL_ERROR")
    payload = canonical_bytes(result)
    if len(payload) > MAX_FRAME_BYTES:
        payload = canonical_bytes(native_response(request, "ERROR", failure="RESOURCE_LIMIT"))
    protocol.write(len(payload).to_bytes(4, "big") + payload)
    protocol.flush()


if __name__ == "__main__":
    main()
