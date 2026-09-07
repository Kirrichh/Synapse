"""Observed owner-thread work, independent of Gold schemas and authority.

An operation samples its actual monotonic and thread CPU clocks. File owners
report bytes returned by their IO primitives, and the VM adapter reports actual
host callback entries. Nested operations retain exclusive counters and parent
identities; callers must not add inclusive parent and child durations.

The installed recorder owns durability. A failed end receipt cannot undo an
effect: the durable start remains incomplete. There is no replay or recovery
policy here, and no process-wide monkey patch or background sampler.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import os
import secrets
import threading
import time


RESOURCE_PROFILE = "synapse.owner-thread-resources/v1"
OPERATIONS = {
    "runtime.execution": "LIFECYCLE",
    "runtime.recovery": "LIFECYCLE",
    "knowledge.prepare": "LIFECYCLE",
    "knowledge.setup": "LIFECYCLE",
    "knowledge.snapshot": "C_WRITE",
    "knowledge.retrieve": "C_READ",
    "knowledge.read": "C_READ",
    "replay.reference": "C_WRITE",
    "replay.execute": "C_USE",
    "replay.resume": "C_USE",
    "plan.accept": "C_USE",
    "worker.delivery": "C_USE",
    "provider.transport": "C_USE",
    "verification.execute": "C_USE",
    "publication.commit": "C_WRITE",
    "publication.recover": "LIFECYCLE",
    "lifecycle.append": "LIFECYCLE",
    "observation.evaluate": "LIFECYCLE",
    "observation.publish": "C_WRITE",
}
_RECORDER = ContextVar("synapse_resource_recorder", default=None)
_OPERATION = ContextVar("synapse_resource_operation", default=None)
_CLOCK_DOMAIN = f"owner-thread-{os.getpid()}-{secrets.token_hex(16)}"


@dataclass
class Operation:
    operation_id: str
    attempt_id: str
    started: dict
    children: list = field(default_factory=list)
    result_refs: list = field(default_factory=list)
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    host_calls: int = 0
    children_wall_ns: int = 0
    children_cpu_ns: int = 0
    finished: dict | None = None

    def bind_result(self, reference: dict) -> None:
        # The producing owner supplies its normal immutable source reference.
        # Gold validates the reference, not the instrumentation primitive.
        if type(reference) is not dict:
            raise TypeError("measurement result reference must be data")
        self.result_refs.append(dict(reference))


@contextmanager
def recording_resources(recorder):
    """Bind one recorder to this control flow; threads do not inherit it."""
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


def active_recorder():
    return _RECORDER.get()


@contextmanager
def measure_operation(name: str, *, attempt_id: str | None = None, source_refs=()):
    recorder = _RECORDER.get()
    if recorder is None:
        yield None
        return
    if name not in OPERATIONS:
        raise ValueError("unregistered measured operation")
    parent = _OPERATION.get()
    operation_id = "operation-" + secrets.token_hex(16)
    attempt_id = str(attempt_id) if attempt_id is not None else ("run" if parent is None else parent.attempt_id)
    started = {"profile": RESOURCE_PROFILE, "operation_id": operation_id,
        "name": name, "bucket": OPERATIONS[name], "attempt_id": attempt_id,
        "parent_id": None if parent is None else parent.operation_id,
        "clock_domain": _CLOCK_DOMAIN, "thread_id": str(threading.get_ident()),
        "started_unix_ns": str(time.time_ns()), "started_monotonic_ns": str(time.monotonic_ns()),
        "started_cpu_ns": str(time.thread_time_ns()), "source_refs": list(source_refs)}
    current = Operation(operation_id, attempt_id, started)
    if parent is not None:
        parent.children.append(operation_id)
    token = _OPERATION.set(current)
    status, failure = "COMPLETED", None
    try:
        recorder.started(started)
        yield current
    except BaseException as exc:
        status, failure = "FAILED", type(exc).__name__
        raise
    finally:
        ended_mono, ended_cpu = time.monotonic_ns(), time.thread_time_ns()
        current.finished = {"operation_id": operation_id, "clock_domain": _CLOCK_DOMAIN,
            "thread_id": str(threading.get_ident()), "ended_monotonic_ns": str(ended_mono),
            "ended_cpu_ns": str(ended_cpu), "status": status, "failure": failure,
            "io_read_bytes": str(current.io_read_bytes), "io_write_bytes": str(current.io_write_bytes),
            "host_calls": current.host_calls, "children": list(current.children),
            "children_wall_ns": str(current.children_wall_ns), "children_cpu_ns": str(current.children_cpu_ns),
            "result_refs": list(current.result_refs)}
        _OPERATION.reset(token)
        if parent is not None:
            parent.children_wall_ns += ended_mono - int(started["started_monotonic_ns"])
            parent.children_cpu_ns += ended_cpu - int(started["started_cpu_ns"])
        # This final receipt is charged to its parent operation, if any. The
        # terminal root receipt is the declared finite measurement seal.
        recorder.finished(current.finished)


def observed_operation(name: str, *, attempt_argument: str | None = None, result_reference=None):
    """Instrument the existing owner function; it still executes exactly once."""
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            attempt = None
            if attempt_argument is not None:
                head, *attributes = attempt_argument.split(".")
                attempt = kwargs.get(head)
                for attribute in attributes:
                    attempt = getattr(attempt, attribute, None)
            with measure_operation(name, attempt_id=attempt) as operation:
                result = function(*args, **kwargs)
                if operation is not None and result_reference is not None:
                    operation.bind_result(result_reference(result).to_dict())
                return result
        return observed
    return decorate


def record_file_io(*, read_bytes=0, written_bytes=0):
    operation = _OPERATION.get()
    if operation is not None:
        operation.io_read_bytes += read_bytes
        operation.io_write_bytes += written_bytes


def record_host_call():
    operation = _OPERATION.get()
    if operation is not None:
        operation.host_calls += 1


def validate_resource_start(value):
    fields = {"profile", "operation_id", "name", "bucket", "attempt_id", "parent_id", "clock_domain",
              "thread_id", "started_unix_ns", "started_monotonic_ns", "started_cpu_ns", "source_refs"}
    if (type(value) is not dict or set(value) != fields or value["profile"] != RESOURCE_PROFILE
            or value["name"] not in OPERATIONS or value["bucket"] != OPERATIONS[value["name"]]
            or type(value["source_refs"]) is not list):
        raise ValueError("invalid resource start contract")
    _validate_samples(value, ("thread_id", "started_unix_ns", "started_monotonic_ns", "started_cpu_ns"))
    return value


def validate_resource_finish(value):
    fields = {"operation_id", "clock_domain", "thread_id", "ended_monotonic_ns", "ended_cpu_ns",
              "status", "failure", "io_read_bytes", "io_write_bytes", "host_calls", "children",
              "children_wall_ns", "children_cpu_ns", "result_refs"}
    if (type(value) is not dict or set(value) != fields or value["status"] not in {"COMPLETED", "FAILED"}
            or (value["failure"] is None) != (value["status"] == "COMPLETED")
            or value["failure"] is not None and (type(value["failure"]) is not str or not value["failure"].isidentifier())
            or type(value["host_calls"]) is not int or not 0 <= value["host_calls"] <= 2**53 - 1
            or type(value["children"]) is not list or any(type(item) is not str for item in value["children"])
            or len(set(value["children"])) != len(value["children"])
            or type(value["result_refs"]) is not list):
        raise ValueError("invalid resource completion contract")
    _validate_samples(value, ("thread_id", "ended_monotonic_ns", "ended_cpu_ns", "io_read_bytes",
        "io_write_bytes", "children_wall_ns", "children_cpu_ns"))
    return value


def _validate_samples(value, fields):
    for name in fields:
        sample = value[name]
        if type(sample) is not str or not sample.isdecimal() or str(int(sample)) != sample or not 0 <= int(sample) <= 2**63 - 1:
            raise ValueError("resource sample must be an exact bounded decimal")
