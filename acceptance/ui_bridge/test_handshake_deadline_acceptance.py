"""Heavy: a real five-second startup deadline; independently schedulable."""
from time import monotonic

from machine_peer import MachinePeer, request


def test_silent_peer_cannot_hold_bootstrap_open_forever(tmp_path):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        started = monotonic()
        assert peer.receive(timeout=8)["code"] == "HANDSHAKE_TIMEOUT"
        assert peer.process.wait(timeout=3) == 2
        assert monotonic() - started < 8


def test_invalid_requests_do_not_renew_handshake_deadline(tmp_path):
    with MachinePeer(tmp_path) as peer:
        peer.receive()
        started = monotonic()
        while True:
            peer.send(request())
            result = peer.receive()
            if result["code"] == "HANDSHAKE_TIMEOUT":
                break
            assert result["code"] == "NOT_READY"
            assert monotonic() - started < 8
        assert peer.process.wait(timeout=3) == 2
