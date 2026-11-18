"""Byte-stream behavior, allocation refusal, and partial writes."""
import io
import struct

import pytest

from synapse.machine.codec import encode, read_frame, write_frame
from synapse.machine.contract import ProtocolError


class Fragmented(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 1))


def test_fragmented_multibyte_and_back_to_back_frames():
    first = b'{"text":"\xe2\x82\xac"}'
    second = b'{}'
    stream = Fragmented(struct.pack(">I", len(first)) + first + b'\0\0\0\2' + second)
    assert read_frame(stream, 100) == first
    assert read_frame(stream, 100) == second
    assert read_frame(stream, 100) is None


def test_escaped_and_literal_line_endings_preserve_formed_frame_bytes():
    payload = '{"text":"Привет🙂\\n\\r\\n\\t\\\\n"}\r\n'.encode('utf-8')
    packet = struct.pack('>I', len(payload)) + payload
    stream = Fragmented(packet + packet)
    assert read_frame(stream, 1024) == payload
    assert read_frame(stream, 1024) == payload
    destination = io.BytesIO()
    write_frame(destination, packet + packet)
    assert destination.getvalue() == packet + packet


@pytest.mark.parametrize("data,code", [(b'\0', 'TRUNCATED_FRAME'),
    (b'\0\0\0\0', 'INVALID_LENGTH'), (b'\0\0\0\2x', 'TRUNCATED_FRAME')])
def test_corruption_is_explicit(data, code):
    with pytest.raises(ProtocolError, match=code):
        read_frame(io.BytesIO(data), 100)


def test_oversize_is_rejected_before_payload_read():
    class HeaderOnly:
        def read(self, size):
            if size != 4:
                pytest.fail("Payload allocation/read occurred after an oversized header")
            return struct.pack(">I", 0xffffffff)
    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE"):
        read_frame(HeaderOnly(), 65536)


def test_exact_limit_is_accepted_and_larger_frame_is_refused():
    assert read_frame(io.BytesIO(b'\0\0\0\2{}'), 2) == b'{}'
    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE"):
        encode({"a": "long"}, 2)
    assert encode({}, 2) == b'\0\0\0\2{}'
    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE"):
        read_frame(io.BytesIO(b'\0\0\0\3{ }'), 2)
    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE"):
        encode({}, 1)


def test_partial_writes_keep_every_frame_byte():
    class ShortWriter(io.BytesIO):
        def write(self, part):
            return super().write(part[:2])
    destination = ShortWriter()
    frame = encode({"value": "\u20ac"}, 100)
    write_frame(destination, frame)
    assert destination.getvalue() == frame


@pytest.mark.parametrize("returned", [0, None])
def test_nonprogressing_writer_is_a_closed_pipe(returned):
    class ClosedWriter:
        def write(self, _):
            return returned
    with pytest.raises(ProtocolError, match="PIPE_CLOSED"):
        write_frame(ClosedWriter(), b'abc')
