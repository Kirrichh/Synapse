"""Binary machine mode attached to synapse.cli.main, without another CLI."""
from __future__ import annotations

import os
import sys
from time import monotonic

from .codec import decode
from .contract import PROFILE, ProtocolError, refusal
from .session import ControlSession
from .transport import PipeTransport


def run_machine() -> int:
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    # Diagnostic codes have one LF delimiter on every platform. Text-mode CRLF
    # translation would change the code consumed by the bounded native reader.
    sys.stderr.reconfigure(newline="\n")
    # Raw descriptors avoid buffered-reader finalization locks after a timeout.
    source = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    destination = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    transport = PipeTransport(source, destination)
    session = ControlSession()
    deadline = monotonic() + PROFILE["handshake_timeout_seconds"]
    handshaking = True
    try:
        transport.send(session.announcement(), timeout=deadline - monotonic())
        while not session.closed:
            handshaking = session.phase != "ready"
            if handshaking and monotonic() >= deadline:
                raise ProtocolError("HANDSHAKE_TIMEOUT")
            timeout = max(0, deadline - monotonic()) if handshaking else None
            payload = transport.receive(timeout)
            if handshaking and monotonic() >= deadline:
                raise ProtocolError("HANDSHAKE_TIMEOUT")
            response = session.receive(decode(payload))
            transport.limit = session.frame_limit
            if response is not None:
                transport.send(response, timeout=deadline - monotonic() if handshaking else None)
        return 0 if session.phase == "stopped" else 2
    except ProtocolError as error:
        if handshaking and error.code == "WRITE_TIMEOUT" and monotonic() >= deadline:
            error = ProtocolError("HANDSHAKE_TIMEOUT")
        try:
            transport.send(refusal(error.code, fatal=True))
        except ProtocolError:
            pass
        print(f"machine: {error.code}", file=sys.stderr, flush=True)
        return 2
    except Exception:
        # Never interpolate payloads, paths, or exceptions into diagnostics.
        print("machine: INTERNAL_ERROR", file=sys.stderr, flush=True)
        return 2
    finally:
        transport.close()
