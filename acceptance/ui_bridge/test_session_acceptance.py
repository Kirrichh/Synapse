"""Handshake ownership, closed dispatch, and honest control-only readiness."""
import pytest

from synapse.machine.contract import ProtocolError, validate
from synapse.machine.session import ControlSession
from machine_peer import hello, request, selection


def ready_session(limit=65536):
    session = ControlSession()
    assert session.receive(hello(max_frame_bytes=limit)) is None
    response = session.receive(selection(max_frame_bytes=limit))
    assert validate(response)["domain_ready"] is False
    return session


def test_early_mutation_is_refused_and_never_replayed():
    session = ControlSession()
    assert session.receive(request("task.submit"))["code"] == "NOT_READY"
    assert session.phase == "hello"
    session.receive(hello())
    assert session.receive(request("machine.shutdown"))["code"] == "NOT_READY"
    assert session.phase == "select"
    assert session.receive(selection())["type"] == "ready"
    assert session.receive(request("task.submit"))["code"] == "METHOD_UNSUPPORTED"
    assert session.receive(request())["result"] == {"status": "alive"}
    assert not session.closed


@pytest.mark.parametrize("peer", [hello(component="core"), hello(profile="other"),
    hello(protocol_min=2, protocol_max=3), hello(protocol_min=2, protocol_max=1)])
def test_incompatible_announcement_closes_without_readiness(peer):
    session = ControlSession()
    response = session.receive(peer)
    assert response["code"] == "PROFILE_UNSUPPORTED" and response["fatal"]
    assert session.closed and session.phase != "ready"


@pytest.mark.parametrize("select", [selection(protocol_version=2), selection(profile="other"),
    selection(max_frame_bytes=2048)])
def test_selection_cannot_expand_peer_offer(select):
    session = ControlSession()
    session.receive(hello(max_frame_bytes=1024))
    assert session.receive(select)["code"] == "PROFILE_UNSUPPORTED"
    assert session.closed


def test_schema_refusal_preserves_valid_session_and_input():
    session = ready_session(1024)
    malformed = request(extra="secret")
    assert session.receive(malformed)["code"] == "SCHEMA_INVALID"
    assert malformed["extra"] == "secret"
    assert session.receive(request(params={"ignored": True}))["code"] == "PARAMS_INVALID"
    result = session.receive(request("machine.status", request_id="status-2"))
    assert validate(result)["result"] == {"state": "ready", "domain_ready": False,
        "profile": "synapse.control.v1", "max_frame_bytes": 1024}
    assert result["request_id"] == "status-2"
    assert not session.closed


def test_wrong_sequence_and_closed_session_cannot_reenter():
    session = ControlSession()
    assert session.receive(selection())["code"] == "INVALID_SEQUENCE"
    assert session.closed
    with pytest.raises(ProtocolError, match="INVALID_SEQUENCE"):
        session.receive(hello())


def test_shutdown_is_explicit_and_terminal():
    session = ready_session()
    result = validate(session.receive(request("machine.shutdown")))
    assert result["result"] == {"status": "stopped", "pending_commands": 0}
    assert session.phase == "stopped" and session.closed
