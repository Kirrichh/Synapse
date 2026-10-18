"""External peer for exercising the canonical machine CLI over real pipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Queue
import struct
import subprocess
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]


def hello(**changes):
    return {"schema_version": "1.0", "type": "hello", "component": "shell",
            "component_version": "acceptance", "protocol_min": 1, "protocol_max": 1,
            "profile": "synapse.control.v1", "max_frame_bytes": 65536, **changes}


def selection(**changes):
    return {"schema_version": "1.0", "type": "select", "profile": "synapse.control.v1",
            "protocol_version": 1, "max_frame_bytes": 65536,
            "encoding": "utf-8", "framing": "u32be", **changes}


def request(method="machine.ping", request_id="r1", **changes):
    return {"schema_version": "1.0", "type": "request", "request_id": request_id,
            "method": method, "params": {}, **changes}


def wire(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return struct.pack(">I", len(payload)) + payload


class MachinePeer:
    def __init__(self, directory, *, command=None):
        self.process = subprocess.Popen(command or [sys.executable, "-m", "synapse", "machine"],
            cwd=directory, env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages = Queue()
        Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while header := self.process.stdout.read(4):
                if len(header) != 4:
                    raise AssertionError("Truncated product output header")
                length, = struct.unpack(">I", header)
                if not 0 < length <= 65536:
                    raise AssertionError("Unbounded or non-framed product output")
                payload = self.process.stdout.read(length)
                self.messages.put(json.loads(payload))
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(EOFError())

    def send_bytes(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def send(self, value):
        self.send_bytes(wire(value))

    def receive(self, timeout=8):
        result = self.messages.get(timeout=timeout)
        if isinstance(result, Exception):
            raise result
        return result

    def connect(self, limit=65536):
        announcement = self.receive()
        assert announcement["type"] == "hello"
        assert announcement["component"] == "core"
        self.send(hello(max_frame_bytes=limit))
        self.send(selection(max_frame_bytes=limit))
        ready = self.receive()
        assert ready["type"] == "ready"
        assert ready["max_frame_bytes"] == limit
        assert ready["domain_ready"] is False
        return ready

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
