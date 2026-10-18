"""Bounded pipe workers let the session enforce deadlines on every platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import BinaryIO

from .codec import encode, read_frame, write_frame
from .contract import PROFILE, ProtocolError, validate


@dataclass
class _Write:
    frame: bytes
    done: Event = field(default_factory=Event)
    error: ProtocolError | None = None


class PipeTransport:
    def __init__(self, source: BinaryIO, destination: BinaryIO) -> None:
        self.incoming: Queue[bytes | ProtocolError] = Queue(PROFILE["queue_capacity"])
        self.outgoing: Queue[_Write | None] = Queue(PROFILE["queue_capacity"])
        self.stopped = Event()
        self.source, self.destination = source, destination
        self.limit = PROFILE["bootstrap_max_frame_bytes"]
        Thread(target=self._read, name="machine-input", daemon=True).start()
        Thread(target=self._write, name="machine-output", daemon=True).start()

    def _deliver(self, value: bytes | ProtocolError) -> None:
        while not self.stopped.is_set():
            try:
                self.incoming.put(value, timeout=0.1)
                return
            except Full:
                continue

    def _read(self) -> None:
        try:
            while not self.stopped.is_set():
                payload = read_frame(self.source, self.limit)
                if payload is None:
                    raise ProtocolError("PIPE_CLOSED")
                self._deliver(payload)
        except ProtocolError as error:
            self._deliver(error)
        except (OSError, ValueError):
            self._deliver(ProtocolError("PIPE_CLOSED"))

    def _write(self) -> None:
        while not self.stopped.is_set():
            item = self.outgoing.get()
            if item is None:
                return
            try:
                write_frame(self.destination, item.frame)
            except (OSError, ValueError, ProtocolError):
                item.error = ProtocolError("PIPE_CLOSED")
            finally:
                item.done.set()

    def receive(self, timeout: float | None) -> bytes:
        try:
            item = self.incoming.get(timeout=timeout)
        except Empty:
            raise ProtocolError("HANDSHAKE_TIMEOUT") from None
        if isinstance(item, ProtocolError):
            raise item
        # A frame prefetched before selection still obeys the selected limit.
        if len(item) > self.limit:
            raise ProtocolError("FRAME_TOO_LARGE")
        return item

    def send(self, value: dict, timeout: float | None = None) -> None:
        validate(value)
        item = _Write(encode(value, self.limit))
        timeout = min(PROFILE["write_timeout_seconds"], timeout) if timeout is not None else PROFILE["write_timeout_seconds"]
        deadline = monotonic() + max(0, timeout)
        try:
            self.outgoing.put(item, timeout=max(0, deadline - monotonic()))
        except Full:
            raise ProtocolError("WRITE_TIMEOUT") from None
        if not item.done.wait(max(0, deadline - monotonic())):
            raise ProtocolError("WRITE_TIMEOUT")
        if item.error:
            raise item.error

    def close(self) -> None:
        self.stopped.set()
        try:
            self.outgoing.put_nowait(None)
        except Full:
            pass
