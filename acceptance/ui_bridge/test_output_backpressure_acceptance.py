"""Heavy: stop reading output and require bounded exit from the actual CLI."""
import json
import errno
import os
import struct
import subprocess
import sys
from threading import Thread
from time import monotonic

from machine_peer import ROOT, hello, selection, request, wire


def test_unread_output_cannot_hold_the_control_process_indefinitely(tmp_path):
    process = subprocess.Popen([sys.executable, "-m", "synapse", "machine"], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)}, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    def receive():
        header = process.stdout.read(4)
        length, = struct.unpack(">I", header)
        data = bytearray()
        while len(data) < length:
            part = process.stdout.read(length-len(data))
            assert part
            data.extend(part)
        return json.loads(data)
    def flood():
        try:
            for index in range(100000):
                frame = wire(request(request_id=f"r{index}"))
                position = 0
                while position < len(frame):
                    position += process.stdin.write(frame[position:])
        except ValueError:
            pass
        except OSError as error:
            # Windows may report EINVAL when the peer closes a pipe mid-write.
            # Other failures are not expected shutdown and must remain visible.
            if error.errno not in (errno.EPIPE, errno.EINVAL):
                raise
    try:
        assert receive()["type"] == "hello"
        process.stdin.write(wire(hello()) + wire(selection()))
        assert receive()["type"] == "ready"
        started = monotonic()
        Thread(target=flood, daemon=True).start()
        assert process.wait(timeout=8) == 2
        assert monotonic() - started < 8
        assert process.stderr.read() == b"machine: WRITE_TIMEOUT\n"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
