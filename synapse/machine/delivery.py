"""Bounded delivery scheduling, independent of domain acceptance and history.

Routing owns subscription authorization and encodes envelopes before enqueueing.
This scheduler owns only memory, scheduling and explicit loss notification. A
successful enqueue is never an acceptance receipt. No source record is rewritten.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from queue import Empty, Full
from threading import Condition
from time import monotonic


class DeliveryClass(Enum):
    STATE_SYNC = "StateSync"
    EVIDENTIAL = "Evidential"
    LIVE = "Live"


class DeliveryClosed(Exception):
    pass


@dataclass(frozen=True)
class Delivery:
    payload: bytes
    stream: str | None = None
    kind: DeliveryClass | None = None
    recovery: str | None = None
    gap: bool = False
    completion: object = None


@dataclass
class _Stream:
    kind: DeliveryClass
    recovery: str | None = None
    gap: bool = False


class DeliveryQueues:
    """One output consumer; producers never wait for projection/channel capacity.

    Stream IDs are opaque routing-issued subscription/epoch references. Reuse is
    refused: a new epoch registers a new reference. Loss state has one bounded
    slot per registered stream, so a full payload queue cannot lose invalidation.
    """

    def __init__(self, *, control_capacity: int, projection_capacity: int,
                 stream_capacity: int, max_payload_bytes: int, control_burst: int):
        limits = (control_capacity, projection_capacity, stream_capacity,
                  max_payload_bytes, control_burst)
        if any(type(value) is not int or value < 1 for value in limits):
            raise ValueError("invalid delivery limits")
        self._control_capacity, self._projection_capacity = limits[:2]
        self._stream_capacity, self._max_payload, self._burst = limits[2:]
        self._controls: deque[Delivery] = deque()
        self._projections: deque[Delivery] = deque()
        self._streams: dict[str, _Stream] = {}
        self._condition = Condition()
        self._closed = False
        self._control_run = 0

    def _open(self):
        if self._closed:
            raise DeliveryClosed()

    def _payload(self, payload: bytes):
        if type(payload) is not bytes or not 0 < len(payload) <= self._max_payload:
            raise ValueError("invalid delivery payload")

    def register(self, stream: str, kind: DeliveryClass) -> None:
        if type(stream) is not str or not 1 <= len(stream) <= 256 or type(kind) is not DeliveryClass:
            raise ValueError("invalid delivery stream")
        with self._condition:
            self._open()
            if stream in self._streams:
                raise ValueError("delivery stream already registered")
            if len(self._streams) >= self._stream_capacity:
                raise Full()
            self._streams[stream] = _Stream(kind)

    def unsubscribe(self, stream: str) -> None:
        with self._condition:
            self._streams.pop(stream, None)
            self._projections = deque(item for item in self._projections if item.stream != stream)
            # This releases delivery resources only; no invocation is cancelled.
            self._condition.notify_all()

    def control(self, payload: bytes, *, completion: object = None,
                timeout: float = 0) -> None:
        self._payload(payload)
        deadline = monotonic() + max(0, timeout)
        with self._condition:
            self._open()
            while len(self._controls) >= self._control_capacity:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise Full()
                self._condition.wait(remaining)
                self._open()
            self._controls.append(Delivery(payload, completion=completion))
            self._condition.notify_all()

    def project(self, stream: str, payload: bytes) -> bool:
        self._payload(payload)
        with self._condition:
            self._open()
            state = self._streams.get(stream)
            if state is None:
                raise ValueError("unknown delivery stream")
            if state.recovery is not None:
                return False
            if len(self._projections) >= self._projection_capacity:
                if state.kind is DeliveryClass.LIVE:
                    state.gap = True
                else:
                    state.recovery = ("RESNAPSHOT" if state.kind is DeliveryClass.STATE_SYNC else "REREAD")
                    self._projections = deque(item for item in self._projections if item.stream != stream)
                self._condition.notify_all()
                return False
            self._projections.append(Delivery(payload, stream, state.kind))
            self._condition.notify_all()
            return True

    def take(self, timeout: float | None = None) -> Delivery:
        deadline = None if timeout is None else monotonic() + max(0, timeout)
        with self._condition:
            while True:
                self._open()
                invalid = next(((key, value) for key, value in self._streams.items()
                                if value.recovery is not None and value.recovery != "NOTIFIED"), None)
                projected = bool(self._projections) or invalid is not None
                if self._controls and (self._control_run < self._burst or not projected):
                    self._control_run = min(self._burst, self._control_run + 1)
                    item = self._controls.popleft()
                elif invalid is not None:
                    stream, state = invalid
                    item = Delivery(b"", stream, state.kind, recovery=state.recovery)
                    state.recovery = "NOTIFIED"
                    self._control_run = 0
                elif self._projections:
                    item = self._projections.popleft()
                    state = self._streams[item.stream]
                    if state.gap:
                        item = replace(item, gap=True)
                        state.gap = False
                    self._control_run = 0
                else:
                    remaining = None if deadline is None else deadline - monotonic()
                    if remaining is not None and remaining <= 0:
                        raise Empty()
                    self._condition.wait(remaining)
                    continue
                self._condition.notify_all()
                return item

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._controls.clear()
            self._projections.clear()
            self._streams.clear()
            self._condition.notify_all()
