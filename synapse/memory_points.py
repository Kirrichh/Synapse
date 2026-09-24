"""Adapter points the Synapse runtime core offers to the memory subsystem.

The core (interpreter and durable application) evaluates no experience, changes
no trust and knows no court. It owns only the narrow typed points below; the
memory subsystem implements them and the canonical durable launch composes the
two. Without a bound session none of these points exists: ``tool`` is not
callable, habits keep their Living Habits semantics and palace consolidation
stays local to the palace backend.

Points (spec part 3 §10.2):

* the durable event: subsystem fields (task, segment, trigger, outcome) are bound
  to every memory-significant event before it is recorded;
* the habit registry load: learned habits enter the Living Habits registry only
  as entries built from the last complete snapshot boundary;
* ``consolidate`` and the end or recovery of a session: the court runs through
  the session;
* the external action: every call leaves the process through the session's
  gateway, is recorded before its result is used and is consumed, never
  repeated, when a durable run is reconstructed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

DURABLE_COGNITIVE_PROFILE = "synapse.durable.cognitive/v1"

#: The event a failed external action raises before any habit may react to it.
ACTION_FAILURE_EVENT = "external_error"
#: The error kind a program catches to handle an unrecovered failed action.
ACTION_FAILED = "ACTION_FAILED"


class ActionFailed(Exception):
    """A failed external action that no habit recovered.

    ``catch (ACTION_FAILED as failure)`` is the slow path of the reactive event;
    ``failure`` is the strict JSON view of the recorded action and event. It is
    deliberately not a ``RuntimeError``: generic runtime-error recovery in the
    interpreter must never swallow an unhandled failed action.
    """

    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__(f"{ACTION_FAILED}: {failure.get('tool')}")
        self.failure = failure


@dataclass(frozen=True)
class TypedCondition:
    """One ``{field, op, value}`` condition of a typed trigger (spec part 1 §6)."""

    field: str
    op: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class LearnedHabitEntry:
    """A learned habit as the registry sees it: typed trigger plus identity.

    The executable body stays with the subsystem; the registry receives only
    what selection needs. Built from the last complete snapshot boundary.
    """

    habit_id: str
    trigger_id: str
    event_types: tuple[str, ...]
    context: tuple[str, ...]
    when: tuple[TypedCondition, ...]
    not_when: tuple[TypedCondition, ...]
    priority: str
    context_trust: float
    energy_cost: float


@dataclass(frozen=True)
class ActionPorts:
    """What a learned habit body may do: call through the recorded action path, wait."""

    invoke: Callable[[str, Mapping[str, Any], "int | None"], dict[str, Any]]
    wait: Callable[[float], None]


class MemorySession(Protocol):
    """One durable session bound to its memory owner."""

    def declare_task(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        """Formation contour: validate a TaskContract and fix its segment markers."""

    def bind_event(self, event: Mapping[str, Any], working: Mapping[str, Any]) -> dict[str, Any]:
        """Return the subsystem fields of one event from the working memory."""

    def registry_entries(self) -> tuple[LearnedHabitEntry, ...]:
        """Learned habits admitted by the last complete snapshot boundary."""

    def declared_trust(self, habit_identity: str) -> float | None:
        """Observed context trust of a declared (layer 1) habit, if the court has one."""

    def veto_similarity(self, trigger_id: str, event: Mapping[str, Any]) -> float | None:
        """Configured similarity of an applicable candidate; it can only veto."""

    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Perform one external action through the gateway (LIVE only)."""

    def run_learned_body(self, habit_id: str, event: Mapping[str, Any], ports: ActionPorts) -> dict[str, Any]:
        """Execute a learned habit's frozen action pattern through ``ports``."""

    def consolidate(self, mode: str, *, history: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the court for this session (``full`` or ``summary``)."""


class MemorySessionFactory(Protocol):
    """How the durable application opens and recovers sessions of one memory owner."""

    def descriptor(self) -> dict[str, Any]:
        """Strict JSON binding recorded in the run artifact."""

    def open_session(self, run: Mapping[str, Any]) -> MemorySession:
        """Open the session of a durable run (new or reconstructed)."""

    def recover(self, run: Mapping[str, Any], *, history: list[dict[str, Any]]) -> dict[str, Any]:
        """Emergency consolidation of a crashed session tail, before it continues."""
