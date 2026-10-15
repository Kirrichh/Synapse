"""Bound materialization of one logical memory event on this host."""
from __future__ import annotations

from dataclasses import dataclass
import re

DEFAULT_MAX_EVENT_BYTES = 256 * 1024 * 1024
EVENT_BUDGET_ENV = "SYNAPSE_MEMORY_EVENT_MAX_BYTES"


class MemoryEventBudgetExceeded(ValueError):
    """The event needs more materialization capacity than this reader declares."""

    def __init__(self, required_bytes, maximum_bytes):
        self.required_bytes = required_bytes
        self.maximum_bytes = maximum_bytes
        super().__init__(f"memory event logical size {required_bytes} bytes exceeds materialization budget "
                         f"{maximum_bytes} bytes; configure {EVENT_BUDGET_ENV} for the host's capacity")


@dataclass(frozen=True)
class MemoryEventBudget:
    maximum_bytes: int

    def __post_init__(self):
        if type(self.maximum_bytes) is not int or self.maximum_bytes <= 0:
            raise ValueError("memory event materialization budget must be a positive integer byte count")

    def require(self, logical_bytes):
        if type(logical_bytes) is not int or logical_bytes <= 0:
            raise ValueError("memory event logical byte length must be a positive integer")
        if logical_bytes > self.maximum_bytes:
            raise MemoryEventBudgetExceeded(logical_bytes, self.maximum_bytes)


def resolve_event_budget(maximum_bytes=None, *, configured=None):
    """Validate the materialization capacity supplied by the storage owner."""
    if maximum_bytes is None:
        if configured is not None:
            if re.fullmatch(r"[0-9]+", configured) is None:
                raise ValueError(f"{EVENT_BUDGET_ENV} must be a positive integer byte count")
            maximum_bytes = int(configured)
        else:
            maximum_bytes = DEFAULT_MAX_EVENT_BYTES
    return MemoryEventBudget(maximum_bytes)
