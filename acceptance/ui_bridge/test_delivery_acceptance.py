"""Observe bounded class-specific loss and scheduling using production queues."""
from queue import Empty, Full
from threading import Condition, Event, Thread

import pytest

from synapse.machine.delivery import DeliveryClass, DeliveryClosed, DeliveryQueues


def queues(**overrides):
    return DeliveryQueues(**({"control_capacity": 2, "projection_capacity": 2,
        "stream_capacity": 3, "max_payload_bytes": 16, "control_burst": 2} | overrides))


class ObservedWait(Condition):
    """Observe the real wait boundary without replacing lock/wakeup semantics."""
    def __init__(self):
        super().__init__()
        self.waiting = Event()

    def wait(self, timeout=None):
        self.waiting.set()
        return super().wait(timeout)


def test_live_flood_cannot_consume_control_slots_or_starve_shutdown():
    queue = queues()
    queue.register("live", DeliveryClass.LIVE)
    assert queue.project("live", b"first")
    assert queue.project("live", b"second")
    assert not queue.project("live", b"lost")
    ticket = object()
    queue.control(b"shutdown", completion=ticket)
    queue.control(b"heartbeat")
    with pytest.raises(Full):
        queue.control(b"refusal")
    assert queue.take(0).completion is ticket
    assert queue.take(0).payload == b"heartbeat"
    first = queue.take(0)
    assert (first.payload, first.kind, first.gap) == (b"first", DeliveryClass.LIVE, True)
    assert not queue.take(0).gap
    with pytest.raises(Empty):
        queue.take(0)


@pytest.mark.parametrize("kind,recovery", [(DeliveryClass.STATE_SYNC, "RESNAPSHOT"),
                                         (DeliveryClass.EVIDENTIAL, "REREAD")])
def test_projection_loss_invalidates_whole_epoch_without_forging_source(kind, recovery):
    queue = queues(projection_capacity=1)
    queue.register("old", kind)
    assert queue.project("old", b"source bytes")
    assert not queue.project("old", b"overflow")
    notice = queue.take(0)
    assert (notice.stream, notice.kind, notice.recovery, notice.payload, notice.gap) == (
        "old", kind, recovery, b"", False)
    assert not queue.project("old", b"late progress")
    with pytest.raises(Empty):
        queue.take(0)
    queue.unsubscribe("old")
    queue.register("fresh", kind)
    assert queue.project("fresh", b"exact retained")
    assert queue.take(0).payload == b"exact retained"


def test_live_gap_survives_pressure_from_another_stream_and_is_scoped():
    queue = queues(projection_capacity=1)
    queue.register("a", DeliveryClass.LIVE)
    queue.register("b", DeliveryClass.LIVE)
    queue.project("a", b"a1")
    assert not queue.project("b", b"b-lost")
    assert not queue.take(0).gap
    queue.project("b", b"b2")
    assert queue.take(0).gap
    queue.project("b", b"b3")
    assert not queue.take(0).gap


def test_control_burst_bounds_projection_delay_and_unsubscribe_discards_only_its_stream():
    queue = queues(control_capacity=4)
    queue.register("a", DeliveryClass.EVIDENTIAL)
    queue.register("b", DeliveryClass.LIVE)
    queue.project("a", b"retained")
    queue.project("b", b"obsolete")
    queue.unsubscribe("b")
    for value in (b"c1", b"c2", b"c3"):
        queue.control(value)
    assert [queue.take(0).payload for _ in range(4)] == [b"c1", b"c2", b"retained", b"c3"]
    with pytest.raises(ValueError):
        queue.project("b", b"late")


def test_registration_and_payload_bounds_prevent_hidden_unbounded_loss_state():
    queue = queues(stream_capacity=1)
    queue.register("one", DeliveryClass.STATE_SYNC)
    with pytest.raises(ValueError):
        queue.register("one", DeliveryClass.LIVE)
    with pytest.raises(Full):
        queue.register("two", DeliveryClass.EVIDENTIAL)
    for payload in (b"", b"x" * 17, bytearray(b"mutable")):
        with pytest.raises(ValueError):
            queue.control(payload)
        with pytest.raises(ValueError):
            queue.project("one", payload)
    assert queue.project("one", b"x" * 16)
    assert queue.take(0).payload == b"x" * 16
    for stream, kind in (("", DeliveryClass.LIVE), ("x" * 257, DeliveryClass.LIVE), ("x", "Live")):
        with pytest.raises(ValueError):
            queue.register(stream, kind)
    for name in ("control_capacity", "projection_capacity", "stream_capacity", "max_payload_bytes", "control_burst"):
        with pytest.raises(ValueError):
            queues(**{name: 0})


def test_close_wakes_blocked_consumer_and_refuses_subsequent_operations():
    queue = queues()
    queue._condition = ObservedWait()
    started, ended = Event(), Event()
    failures = []
    def consume():
        started.set()
        try:
            queue.take()
        except DeliveryClosed:
            ended.set()
        except Exception as error:
            failures.append(type(error).__name__)
    thread = Thread(target=consume, daemon=True)
    thread.start()
    assert started.wait(1)
    assert queue._condition.waiting.wait(1)
    queue.close()
    assert ended.wait(1)
    thread.join(1)
    assert not failures
    for operation in (lambda: queue.take(0), lambda: queue.control(b"late"),
                      lambda: queue.project("old", b"late"),
                      lambda: queue.register("new", DeliveryClass.LIVE)):
        with pytest.raises(DeliveryClosed):
            operation()


def test_control_capacity_is_released_by_consumption_and_wait_is_bounded():
    queue = queues(control_capacity=1)
    queue.control(b"first")
    with pytest.raises(Full):
        queue.control(b"timed out", timeout=0.01)
    started, ended = Event(), Event()
    def produce():
        started.set()
        queue.control(b"second", timeout=1)
        ended.set()
    thread = Thread(target=produce, daemon=True)
    thread.start()
    assert started.wait(1)
    assert queue.take(0).payload == b"first"
    assert ended.wait(1)
    assert queue.take(0).payload == b"second"
    thread.join(1)


def test_close_refuses_a_waiting_producer_instead_of_resurrecting_a_queue():
    queue = queues(control_capacity=1)
    queue._condition = ObservedWait()
    queue.control(b"first")
    started, ended = Event(), Event()
    def produce():
        started.set()
        try:
            queue.control(b"late", timeout=1)
        except DeliveryClosed:
            ended.set()
    thread = Thread(target=produce, daemon=True)
    thread.start()
    assert started.wait(1)
    assert queue._condition.waiting.wait(1)
    queue.close()
    assert ended.wait(1)
    thread.join(1)


def test_loss_notification_does_not_remove_other_streams_and_new_epoch_has_no_old_gap():
    queue = queues(projection_capacity=1)
    queue.register("evidence", DeliveryClass.EVIDENTIAL)
    queue.register("state", DeliveryClass.STATE_SYNC)
    queue.project("evidence", b"original bytes")
    assert not queue.project("state", b"lost state")
    assert queue.take(0).recovery == "RESNAPSHOT"
    assert queue.take(0).payload == b"original bytes"
    queue.unsubscribe("state")
    queue.register("live", DeliveryClass.LIVE)
    queue.project("live", b"old")
    assert not queue.project("live", b"lost")
    queue.unsubscribe("live")
    queue.register("new", DeliveryClass.LIVE)
    queue.project("new", b"fresh")
    assert not queue.take(0).gap
