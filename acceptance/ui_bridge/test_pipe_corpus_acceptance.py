"""Canonical pipe corpus; also runs through the external installed native relay.

The control profile refuses arbitrary parameters instead of echoing domain data.
Exact output bytes are checked for every correlated refusal and normal reply.
"""
import errno
import json
from pathlib import Path
import struct

import pytest

from machine_peer import MachinePeer, hello, selection, request, wire


def raw_frame(payload):
    return struct.pack('>I', len(payload)) + payload


def response(peer):
    value = peer.receive()
    assert peer.frames.get(timeout=1) == wire(value)
    return value


def connect(peer, limit=65536):
    assert response(peer)['type'] == 'hello'
    peer.send_bytes(wire(hello(max_frame_bytes=limit)) + wire(selection(max_frame_bytes=limit)))
    assert response(peer)['type'] == 'ready'


@pytest.mark.parametrize('fragment', [1, 2, 3, 7, 4096])
def test_native_pipe_multibyte_escapes_and_consecutive_frames(tmp_path, fragment):
    with MachinePeer(tmp_path) as peer:
        connect(peer)
        values = ['\u041f\u0440\u0438\u0432\u0435\u0442', '\U0001f642\u6f22', '\n', '\r\n', '\t', r'\n', '\\"{}[]']
        frames = b''.join(wire(request(request_id=f'probe_{index}', params={'text': text}))
                          for index, text in enumerate(values))
        for offset in range(0, len(frames), fragment):
            peer.send_bytes(frames[offset:offset + fragment])
        for index in range(len(values)):
            assert response(peer) == {'schema_version': '1.0', 'type': 'error',
                'code': 'PARAMS_INVALID', 'fatal': False, 'request_id': f'probe_{index}'}
        peer.send(request('machine.shutdown'))
        assert response(peer)['result']['status'] == 'stopped'
        assert peer.process.wait(timeout=5) == 0
        assert peer.process.stderr.read() == b''


@pytest.mark.parametrize('limit', [1024, 65536])
def test_exact_limit_and_next_byte_over_limit(tmp_path, limit):
    with MachinePeer(tmp_path) as peer:
        connect(peer, limit)
        base = json.dumps(request(params={'text': ''}), separators=(',', ':')).encode()
        exact = base.replace(b'"text":""', b'"text":"' + b'x' * (limit - len(base)) + b'"')
        assert len(exact) == limit
        try:
            peer.send_bytes(raw_frame(exact))
        except OSError as error:
            # A premature header refusal can close the pipe during this write.
            # Still inspect the actual refusal; it must not become an infra kill.
            if error.errno not in (errno.EPIPE, errno.EINVAL):
                raise
        assert response(peer)['code'] == 'PARAMS_INVALID'
        peer.send_bytes(struct.pack('>I', limit + 1))
        assert response(peer)['code'] == 'FRAME_TOO_LARGE'
        assert peer.process.wait(timeout=5) == 2
        assert peer.process.stderr.read() == b'machine: FRAME_TOO_LARGE\n'


INVALID_CORPUS = [row for row in json.loads(Path(__file__).with_name('corpus.json').read_text())
                  if not row['valid']]


@pytest.mark.parametrize('case', INVALID_CORPUS, ids=lambda case: case['behavior'])
def test_shared_invalid_corpus_never_gains_authority(tmp_path, case):
    with MachinePeer(tmp_path) as peer:
        connect(peer)
        peer.send_bytes(raw_frame(case['raw'].encode('utf-8')))
        refused = response(peer)
        assert refused['type'] == 'error'
        assert refused['code'] in {'SCHEMA_INVALID', 'SCHEMA_UNSUPPORTED', 'INVALID_JSON', 'JSON_LIMIT'}
        if refused['fatal']:
            assert peer.process.wait(timeout=5) == 2
            with pytest.raises(EOFError):
                peer.receive()
        else:
            peer.send(request())
            assert response(peer)['result'] == {'status': 'alive'}
            peer.send(request('machine.shutdown'))
            assert response(peer)['result']['status'] == 'stopped'
            assert peer.process.wait(timeout=5) == 0
