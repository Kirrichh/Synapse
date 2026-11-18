"""Exercise bounded pipe workers and explicit transport failure propagation."""
import io
from threading import Event
from time import monotonic

import pytest
from synapse.machine.contract import ProtocolError
from synapse.machine.transport import PipeTransport
from machine_peer import request


def test_invalid_outgoing_message_never_reaches_the_pipe():
    destination = io.BytesIO()
    transport = PipeTransport(io.BytesIO(), destination)
    try:
        with pytest.raises(ProtocolError, match="SCHEMA_INVALID"):
            transport.send({**request(), "unexpected": "secret"})
        assert destination.getvalue() == b""
    finally:
        transport.close()


def test_write_deadline_is_shared_and_a_stalled_sink_returns_explicit_timeout():
    release = Event()
    entered = Event()
    class StalledSink:
        def write(self, data):
            entered.set()
            release.wait(2)
            return len(data)
        def flush(self):
            pass
    transport = PipeTransport(io.BytesIO(), StalledSink())
    try:
        started = monotonic()
        with pytest.raises(ProtocolError, match="WRITE_TIMEOUT"):
            transport.send(request(), timeout=0.05)
        assert entered.is_set()
        assert monotonic() - started < 0.5
    finally:
        release.set()
        transport.close()


def test_reader_and_writer_failures_are_reported_without_raw_exception_text():
    class FailedSink:
        def write(self, _):
            raise OSError("secret credential")
    transport = PipeTransport(io.BytesIO(b'\0\0\0\4{'), FailedSink())
    try:
        with pytest.raises(ProtocolError, match="TRUNCATED_FRAME"):
            transport.receive(1)
        with pytest.raises(ProtocolError, match="PIPE_CLOSED") as failure:
            transport.send(request())
        assert "secret" not in str(failure.value)
    finally:
        transport.close()


def test_closed_transport_refuses_new_output_without_touching_the_sink():
    destination = io.BytesIO()
    transport = PipeTransport(io.BytesIO(), destination)
    transport.close()
    with pytest.raises(ProtocolError, match="PIPE_CLOSED"):
        transport.send(request())
    assert destination.getvalue() == b""


def test_domain_reply_budget_is_named_and_keeps_control_transport_usable():
    from uuid import uuid4
    from synapse.machine import domain_contract
    from synapse.machine.codec import read_frame, decode
    request_value = {"request_id":str(uuid4()), "method":"invocation.inspect"}
    record = {"state":"TERMINAL", "invocation_id":"synthetic-invocation", "revision":"a"*64,
              "cancel_requested":False, "record":"x"*65000, "record_sha256":"b"*64}
    destination=io.BytesIO()
    transport=PipeTransport(io.BytesIO(), destination)
    transport.limit=1024
    try:
        transport.send(domain_contract.reply(request_value, record))
        transport.send(request())
        received=io.BytesIO(destination.getvalue())
        denied=decode(read_frame(received,1024))
        assert denied["code"] == "RESPONSE_TOO_LARGE" and denied["request_id"] == request_value["request_id"]
        assert decode(read_frame(received,1024))["method"] == "machine.ping"
        record["record"] = "x"*65530
        assert domain_contract.reply(request_value, record)["code"] == "RESPONSE_TOO_LARGE"
    finally:
        transport.close()
