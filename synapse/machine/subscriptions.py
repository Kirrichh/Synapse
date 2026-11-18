"""Authorized, bounded pull delivery over the application's existing pipe.

Collection and consumption are separate: a slow consumer cannot turn a source
read into execution backpressure. The session serializes these operations;
owners remain the only source of state and retained records. No worker, timer,
execution world or plugin is created by subscribing or rereading.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from queue import Empty, Full
from time import monotonic
from uuid import uuid4

from synapse.access.records import AccessRefused
from synapse.commands.binding import CommandRefused
from synapse.commands.jcs import canonical_bytes, decode, InvalidCommandValue
from .contract import PROFILE
from .delivery import DeliveryClass, DeliveryQueues


SOURCES = {
    "invocation.state": (DeliveryClass.STATE_SYNC, "invocation.snapshot"),
    "invocation.record": (DeliveryClass.EVIDENTIAL, "invocation.inspect"),
    "connection.health": (DeliveryClass.LIVE, "invocation.inspect"),
}


class DeliveryRefused(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class Subscription:
    identity: dict
    objects: tuple[str, ...]
    generation: str | None = None
    position: int | None = None
    snapshotted: bool = False
    invalid: bool = False


class Subscriptions:
    def __init__(self, invocations):
        self.invocations = invocations
        self.session_id = str(uuid4())
        self.streams: dict[str, Subscription] = {}
        self.queue = DeliveryQueues(control_capacity=PROFILE["result_control_capacity"],
            projection_capacity=PROFILE["projection_capacity"],
            stream_capacity=PROFILE["subscription_capacity"],
            max_payload_bytes=PROFILE["max_frame_bytes"], control_burst=PROFILE["control_burst"])

    def close(self):
        self.queue.close()
        self.streams.clear()

    def _get(self, reference):
        stream = self.streams.get(reference)
        if stream is None:
            raise DeliveryRefused("SUBSCRIPTION_UNKNOWN")
        return stream

    def _forget(self, reference):
        self.queue.unsubscribe(reference)
        self.streams.pop(reference, None)

    def _hold(self, connection, stream, method=None):
        identity = stream.identity
        return self.invocations.observation(connection, identity["grant_id"], identity["grant_version"],
            method or SOURCES[identity["source"]][1], stream.objects)

    @contextmanager
    def response(self, connection, method, params, grant_id=None, version=None):
        if method == "delivery.subscribe":
            source, objects = params["source"], tuple(params["invocation_ids"])
            kind, permission = SOURCES[source]
            if kind is not DeliveryClass.STATE_SYNC and len(objects) != 1:
                raise CommandRefused("COMMAND_INVALID")
            with self.invocations.observation(connection, grant_id, version, permission, objects) as (scope, owner):
                basis = owner.snapshot(objects) if kind is DeliveryClass.STATE_SYNC else None
                reference = str(uuid4())
                identity = {"session_id": self.session_id, "subscription_id": reference,
                    "epoch": str(uuid4()), "workspace": scope.workspace,
                    "grant_id": grant_id, "grant_version": version, "source": source,
                    "delivery_class": kind.value, "invocation_ids": list(objects)}
                stream = Subscription(identity, objects)
                if basis is not None:
                    stream.generation, stream.position = basis["generation"], basis["position"]
                    identity["generation"] = stream.generation
                try:
                    self.queue.register(reference, kind)
                except Full:
                    raise DeliveryRefused("BUSY") from None
                self.streams[reference] = stream
                yield {"type": "subscription.confirm", "identity": identity,
                       "deliver_from": stream.position}
            return
        if method == "delivery.next":
            try:
                item = self.queue.take(timeout=0)
            except Empty:
                with self.invocations.authority.visible_grants(connection):
                    yield {"type": "delivery.empty"}
                return
            stream = self._get(item.stream)
            try:
                with self._hold(connection, stream):
                    if item.recovery:
                        stream.invalid = True
                        yield {"type": "delivery.invalidated", "identity": stream.identity,
                               "reason": item.recovery}
                    else:
                        yield {"type": "delivery.data", "identity": stream.identity,
                               "gap": item.gap, "payload": decode(item.payload)}
            except (AccessRefused, CommandRefused):
                self._forget(item.stream)
                raise
            return
        reference = params["subscription_id"]
        stream = self._get(reference)
        if method == "delivery.unsubscribe":
            # Disposal never needs a still-valid grant and never cancels work.
            self._forget(reference)
            yield {"type": "delivery.closed", "subscription_id": reference}
            return
        if stream.invalid:
            raise CommandRefused("CURSOR_UNAVAILABLE")
        source = stream.identity["source"]
        permission = "invocation.changes" if source == "invocation.state" and method == "delivery.collect" else None
        try:
            with self._hold(connection, stream, permission) as (_, owner):
                if method == "delivery.snapshot":
                    if source != "invocation.state" or stream.snapshotted:
                        raise CommandRefused("COMMAND_INVALID")
                    snapshot = owner.snapshot(stream.objects)
                    if snapshot["generation"] != stream.generation or snapshot["position"] < stream.position:
                        raise CommandRefused("CURSOR_UNAVAILABLE")
                    stream.position, stream.snapshotted = snapshot["position"], True
                    yield {"type": "state.snapshot", "identity": stream.identity, "snapshot": snapshot}
                    return
                if source == "invocation.state":
                    if not stream.snapshotted:
                        raise DeliveryRefused("NOT_READY")
                    payload = owner.after(stream.generation, stream.position, stream.objects)
                elif source == "invocation.record":
                    # Reread every time, including records previously unavailable
                    # behind a cursor. Retained bytes and hashes remain unchanged.
                    payload = owner.inspect(stream.objects[0])
                else:
                    # Connection observation only; never invocation liveness.
                    payload = {"core": "RESPONDING", "monotonic_ms": int(monotonic() * 1000)}
                try:
                    encoded = canonical_bytes(payload)
                except InvalidCommandValue:
                    self._forget(reference)
                    raise DeliveryRefused("RESPONSE_TOO_LARGE") from None
                queued = self.queue.project(reference, encoded)
                if source == "invocation.state" and queued:
                    stream.position = payload["to"]
                if not queued and source != "connection.health":
                    stream.invalid = True
                yield {"type": "delivery.collected", "subscription_id": reference, "queued": queued}
        except (AccessRefused, CommandRefused) as error:
            if error.code not in ("NOT_READY", "COMMAND_INVALID"):
                self._forget(reference)
            raise
