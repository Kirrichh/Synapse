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
    domain = None
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
            incoming = decode(payload)
            if type(incoming) is dict and incoming.get("schema_version") == "synapse.machine.domain/v1":
                from . import domain_contract
                from synapse.access.records import AccessRefused
                domain_contract.validate(incoming)
                if incoming["type"] != "request":
                    raise ProtocolError("INVALID_SEQUENCE")
                if session.phase != "ready":
                    transport.send(domain_contract.refusal(incoming, "NOT_READY"))
                    continue
                if domain is None:
                    from .application import domain_session
                    try:
                        domain = domain_session()
                    except AccessRefused as error:
                        transport.send(domain_contract.refusal(incoming, error.code))
                        continue
                with domain.response(incoming, ready=True) as response:
                    transport.send(response)
                continue
            response = session.receive(incoming)
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
        if domain is not None:
            domain.close()
        transport.close()
