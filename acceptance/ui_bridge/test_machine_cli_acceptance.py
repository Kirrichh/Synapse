"""Real subprocess acceptance of the canonical machine entry and its effects."""
import struct

import pytest

from machine_peer import MachinePeer, hello, request, selection, wire


def test_fatal_negotiation_refusal_exits_unsuccessfully(tmp_path):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        peer.send(hello(profile="unsupported"))
        response = peer.receive()
        assert response["code"] == "PROFILE_UNSUPPORTED"
        assert response["fatal"] is True
        assert peer.process.wait(timeout=5) == 2


def test_canonical_entry_negotiates_reports_status_and_shuts_down(tmp_path):
    with MachinePeer(tmp_path) as peer:
        ready = peer.connect(limit=1024)
        assert ready["command_classes"] == ["control"]
        peer.send(request("machine.status"))
        status = peer.receive()
        assert status["result"]["max_frame_bytes"] == 1024
        assert status["result"]["domain_ready"] is False
        peer.send(request("machine.shutdown", request_id="close"))
        assert peer.receive()["result"] == {"status": "stopped", "pending_commands": 0}
        assert peer.process.wait(timeout=5) == 0
        assert peer.process.stderr.read() == b''
        assert list(tmp_path.iterdir()) == []  # No domain owner/store is created by status.


def test_fragmentation_coalescing_and_early_request_have_no_delayed_effect(tmp_path):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        for byte in wire(request("machine.shutdown")):
            peer.send_bytes(bytes([byte]))
        assert peer.receive()["code"] == "NOT_READY"
        peer.send_bytes(wire(hello()) + wire(selection()) + wire(request()))
        assert peer.receive()["type"] == "ready"
        assert peer.receive()["result"] == {"status": "alive"}
        assert peer.process.poll() is None


@pytest.mark.parametrize("raw,code", [(struct.pack('>I', 65537), "FRAME_TOO_LARGE"),
    (b'\0\0\0\0', "INVALID_LENGTH"), (b'\0\0\0\1\xff', "INVALID_UTF8"),
    (b'\0\0\0\1{', "INVALID_JSON")])
def test_corruption_ends_stream_without_scanning_later_frames(tmp_path, raw, code):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        peer.send_bytes(raw + wire(hello()) + wire(selection()) + wire(request()))
        assert peer.receive() == {"schema_version": "1.0", "type": "error", "code": code, "fatal": True}
        assert peer.process.wait(timeout=5) == 2
        with pytest.raises(EOFError):
            peer.receive()


def test_schema_errors_and_unknown_commands_never_echo_sensitive_input(tmp_path):
    canary = "credential-canary-DO-NOT-LOG"
    with MachinePeer(tmp_path) as peer:
        peer.connect()
        peer.send(request(params={"api_key": canary}))
        refusal = peer.receive()
        assert refusal["code"] == "PARAMS_INVALID" and canary not in str(refusal)
        peer.send(request("task.submit", params={"instruction": canary}))
        assert peer.receive()["code"] == "METHOD_UNSUPPORTED"
        peer.send(request(extra=canary))
        assert peer.receive()["code"] == "SCHEMA_INVALID"
        peer.send(request())
        assert peer.receive()["result"]["status"] == "alive"
        peer.send(request("machine.shutdown"))
        peer.receive()
        assert peer.process.wait(timeout=5) == 0
        assert canary.encode() not in peer.process.stderr.read()


@pytest.mark.parametrize("tail,code", [(b'\0\0', "TRUNCATED_FRAME"),
    (b'\0\0\0\3x', "TRUNCATED_FRAME"), (b'', "PIPE_CLOSED")])
def test_eof_is_not_successful_protocol_shutdown(tmp_path, tail, code):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        peer.send_bytes(tail)
        peer.process.stdin.close()
        assert peer.receive()["code"] == code
        assert peer.process.wait(timeout=5) == 2


def test_negotiated_limit_also_rejects_prefetched_frames(tmp_path):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        oversized = request(params={"value": "x" * 1500})
        peer.send_bytes(wire(hello(max_frame_bytes=1024)) + wire(selection(max_frame_bytes=1024)) + wire(oversized))
        assert peer.receive()["type"] == "ready"
        assert peer.receive()["code"] == "FRAME_TOO_LARGE"
        assert peer.process.wait(timeout=5) == 2
