"""Memory sessions of durable runs: the subsystem side of the core's adapter points.

A session serves one durable run of a memory owner. It fixes task plans
(formation), binds subsystem fields to events, loads learned habits only from
the complete snapshot boundary it pinned when the run started (a resumed run
keeps its original cut), sends every external action through the owner's
gateway, executes frozen learned bodies through the recorded action path, and
runs the court for the run.

An exam session (refinement §17) reads a fixed snapshot of the same memory and
never consolidates: mode ``A`` loads no accumulated experience, ``B`` loads
what Gold admits now, ``C`` loads the same habits as ``B`` with every learned
trigger slow-only, so the fast path stays closed.

A replay session serves a verified re-execution (stage 1b): the same pinned
entries as recorded, recorded answers only, and ``ReplayHorizon`` instead of
any live effect.
"""
from __future__ import annotations

from typing import Any, Mapping

from synapse.memory_points import ActionPorts, LearnedHabitEntry, ReplayHorizon, TypedCondition

from .formation import bind_event, plan_task
from .hypotheses import declare, resolve, reuse
from .learning.behavior import execute
from .learning.triggers import render_template
from .records import digest


def opening_of(history) -> Mapping[str, Any] | None:
    for event in history or ():
        if isinstance(event, Mapping) and event.get("type") == "memory_session_opened":
            return event
    return None


EXAM_MODES = ("A", "B", "C")


def _entry(item: Mapping[str, Any], trust: float | None = None, *, slow_only: bool = False) -> LearnedHabitEntry:
    trigger = item["trigger"]
    return LearnedHabitEntry(
        habit_id=item["habit_id"], trigger_id=trigger["id"], event_types=tuple(trigger["event_types"]),
        context=() if trigger["context"] == "any" else tuple(trigger["context"]),
        when=tuple(TypedCondition(**condition) for condition in trigger["when"]),
        not_when=tuple(TypedCondition(**condition) for condition in trigger["not_when"]),
        priority=item["priority"], context_trust=item["context_trust"] if trust is None else trust,
        energy_cost=item["energy_cost"], slow_only=slow_only)


class MemorySession:
    """One durable run's session of its memory owner."""

    def __init__(self, factory, run: Mapping[str, Any], boundary: Mapping[str, Any] | None,
                 digest: Mapping[str, Any] | None, exam: str | None = None) -> None:
        if exam is not None and exam not in EXAM_MODES:
            raise ValueError("an exam runs in mode A, B or C")
        self.factory = factory
        self.run = run
        self.boundary = boundary
        self.digest = digest
        self.exam = exam
        self.learns = exam is None
        self.scores = 0
        habits = [] if boundary is None else boundary["boundary"]["habits"]
        self._habits = {item["habit_id"]: item for item in habits}

    # -- formation and events ------------------------------------------------
    def declare_task(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        return plan_task(contract, self.factory.configuration)

    def bind_event(self, event, working) -> dict[str, Any]:
        return bind_event(event, working)

    # -- registry load ----------------------------------------------------------
    def pinned(self) -> dict[str, Any]:
        return {"boundary": None if self.boundary is None else self.boundary["id"], "digest": self.digest}

    def registry_entries(self) -> tuple[LearnedHabitEntry, ...]:
        if self.exam == "A":
            return ()
        return tuple(_entry(item, slow_only=self.exam == "C") for habit_id, item in sorted(self._habits.items())
                     if self.factory.admitted_now(habit_id, item))

    def declared_trust(self, habit_identity: str) -> Mapping[str, float] | None:
        if self.boundary is None:
            return None
        return self.boundary["boundary"]["declared"].get(habit_identity)

    def veto_similarity(self, trigger_id: str, event: Mapping[str, Any]) -> dict[str, Any] | None:
        """Scorer similarity of the event text to a learned trigger's typical text; it only vetoes."""
        scorer = self.factory.configuration.scorer
        item = next((value for value in self._habits.values() if value["trigger"]["id"] == trigger_id), None)
        if scorer is None or item is None:
            return None
        template = item["trigger"]["context_template"]
        typical = {"fields": {condition["field"]: condition["value"] for condition in item["trigger"]["when"]}}
        self.scores += 1
        outcome = self._score({"run_id": self.run["run_id"], "ordinal": f"score:{self.scores}", "episode": "score",
                               "op_scope": "score", "path": "score", "tool": scorer.tool, "retry_of": None,
                               "args": {"a": render_template(template, event), "b": render_template(template, typical)},
                               "task_id": None, "segment_marker_id": None, "habit_id": item["habit_id"]})
        value = outcome["view"]["payload"].get("similarity") if outcome["view"]["ok"] and isinstance(
            outcome["view"]["payload"], dict) else None
        if type(value) not in {int, float} or not 0.0 <= value <= 1.0:
            return None
        threshold = self.factory.configuration.parameters["semantic_veto_below"]
        return {"score": float(value), "veto": value < threshold, "threshold": threshold}

    def _score(self, request):
        return self.factory.gateway.invoke(request)

    # -- actions ----------------------------------------------------------------
    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = {**request, "run_id": self.run["run_id"]}
        refusal = self._precondition(request)
        if refusal is not None:
            return self.factory.gateway.refuse(request, refusal)
        return self.factory.gateway.invoke(request)

    def _precondition(self, request: Mapping[str, Any]) -> str | None:
        """Why an action that relies on hypotheses may not act yet (refinement §10)."""
        contract = self.factory.configuration.tools.tools.get(request["tool"])
        requires = request.get("requires")
        if requires is None and not (contract is not None and contract.requires_established):
            return None
        if not requires:
            return "the action requires an established hypothesis and names none"
        unestablished = sorted(item["hypothesis"] for item in requires if item["status"] != "confirmed")
        if unestablished:
            return "a required hypothesis is not established: " + ", ".join(unestablished)
        return None

    # -- hypotheses (refinement §10) ---------------------------------------------
    def declare_hypothesis(self, claim, source_ref) -> dict[str, Any]:
        return declare(claim, self.factory.configuration, source_ref)

    def resolve_hypothesis(self, record, view) -> dict[str, Any]:
        return resolve(record, view, self.factory.configuration)

    def known_hypothesis(self, record) -> dict[str, Any]:
        """The court's status for this very hypothesis, if the pinned snapshot holds a fresh one."""
        if self.boundary is None:
            return {"status": None, "reason": "no_memory_snapshot"}
        boundary = self.boundary["boundary"]
        return reuse(record, boundary["hypotheses"].get(record["id"]), boundary["claims"], boundary["window"],
                     self.factory.configuration.parameters)

    def run_learned_body(self, habit_id: str, event: Mapping[str, Any], ports: ActionPorts) -> dict[str, Any]:
        habit = self._habits[habit_id]["habit"]
        return execute(habit["action_pattern"], habit["binding"], event, ports)

    # -- court ------------------------------------------------------------------
    def consolidate(self, mode: str, *, history: list[dict[str, Any]]) -> dict[str, Any]:
        return self.factory.court(mode, current={**self.run, "history": history})


class ReplaySession(MemorySession):
    """The session of a verified re-execution: recorded answers only, no live effect."""

    def __init__(self, factory, run, boundary, digest, recorded_learned, exam=None) -> None:
        super().__init__(factory, run, boundary, digest, exam)
        self.recorded_learned = list(recorded_learned or [])

    def registry_entries(self) -> tuple[LearnedHabitEntry, ...]:
        entries = []
        for item in self.recorded_learned:
            habit = self._habits.get(item["habit_id"])
            if habit is None or habit["trigger"]["id"] != item["trigger_id"]:
                raise ReplayHorizon("a recorded learned habit is not in its pinned boundary")
            entries.append(_entry(habit, item["context_trust"], slow_only=item["slow_only"]))
        return tuple(entries)

    def _score(self, request):
        recorded = self.factory.gateway.recorded(request["run_id"], request["ordinal"])
        if recorded is None:
            raise ReplayHorizon("a re-execution asked for an unrecorded similarity")
        return recorded

    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ReplayHorizon("a re-execution reached a live external action")

    def consolidate(self, mode: str, *, history: list[dict[str, Any]]) -> dict[str, Any]:
        raise ReplayHorizon("a re-execution reached a live consolidation")


class ReproductionSession(ReplaySession):
    """The session of a reproduction: a live program whose actions are answered from the record.

    Retention reproduces a session from its replay data to prove a raw trace
    reconstructible. Every action is the gateway's recorded final outcome of
    the same run and ordinal, for the same request; nothing else is answered.
    """

    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        gateway = self.factory.gateway
        recorded = gateway.recorded(self.run["run_id"], request["ordinal"])
        if recorded is None or gateway.recorded_request(self.run["run_id"], request["ordinal"]) != digest(
                {"tool": request["tool"], "args": request["args"]}):
            raise ReplayHorizon("a reproduction reached an action its record does not answer")
        return recorded
