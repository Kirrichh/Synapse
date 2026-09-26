"""The canonical composition of a memory owner for durable runs.

``MemoryFactory`` binds durable runs to one connected Gold project with one
memory configuration. It opens sessions (registering them in the project
journal and pinning the latest complete snapshot boundary, or the boundary a
resumed run recorded), recovers crashed runs with an emergency consolidation,
and runs the court under the owner session. A lagging boundary (the decision
committed, the boundary not yet written) is rebuilt idempotently from its
report; a boundary that cannot be rebuilt identically is never guessed — the
previous complete one stays in force.

An exam factory (refinement §17) serves runs that read a fixed complete
snapshot of the same memory in mode A, B or C: it neither registers them as
sessions of the owner nor runs the court for them, even after a crash.

The run artifact records only the descriptor: the project state, the bound
configuration's digest, the executor and the exam, if any. Resolving a
descriptor on resume rebuilds the same factory or refuses.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from synapse.memory_points import DURABLE_COGNITIVE_PROFILE
from synapse.version import RUNTIME_VERSION

from .configuration import MEMORY_BINDING_V1, MemoryConfiguration
from .court.boundary import boundary_record
from .court.consolidation import CourtPorts, consolidate
from .legitimacy import GoldLegitimacy
from .owner import MemoryOwner, MemoryOwnerViolation
from .session import EXAM_MODES, MemorySession, ReplaySession, ReproductionSession, opening_of
from .tools.gateway import Gateway

EXECUTOR = f"synapse-runtime/{RUNTIME_VERSION}/{DURABLE_COGNITIVE_PROFILE}"


class MemoryFactory:
    """Durable-run sessions and the court of one memory owner."""

    def __init__(self, state_root: Path, configuration: MemoryConfiguration, *, transport=None,
                 owner: MemoryOwner | None = None, exam: Mapping[str, Any] | None = None) -> None:
        if exam is not None and (type(exam) is not dict or set(exam) != {"mode", "snapshot"}
                                 or exam["mode"] not in EXAM_MODES or type(exam["snapshot"]) is not str):
            raise MemoryOwnerViolation("an exam names its mode and the snapshot boundary it reads")
        self.owner = owner if owner is not None else MemoryOwner(state_root)
        self.configuration = configuration
        self.exam = None if exam is None else dict(exam)
        self.executor = EXECUTOR
        self._transport = transport
        self._gateway = None
        self.legitimacy = None

    @property
    def gateway(self) -> Gateway:
        if self._gateway is None:
            self._gateway = Gateway(self.owner.gateway_root, self.configuration.tools, executor=self.executor,
                                    transport=self._transport)
            self.legitimacy = GoldLegitimacy(self.owner, self._gateway.evidence, self.executor, self.configuration)
        return self._gateway

    def descriptor(self) -> dict[str, Any]:
        return {"schema_version": MEMORY_BINDING_V1, "project_state": str(self.owner.state_root),
                "configuration_sha256": self.configuration.configuration_sha256, "executor": self.executor,
                "exam": self.exam}

    # -- boundary ------------------------------------------------------------
    def _rebuilt(self, item, state, guard) -> dict | None:
        report = item["report"]
        boundary = boundary_record(state, report["legitimacy"], report["consolidation_id"])
        if boundary["id"] != report["snapshot_boundary_after"]:
            return None
        self.owner.put_boundary(guard, boundary)
        return boundary

    def latest_boundary(self, guard) -> tuple[dict | None, dict | None]:
        """The newest complete boundary and its digest, rebuilding a lagging one."""
        from .court.projection import apply_report, empty_state

        state, latest = empty_state(), (None, None)
        for item in self.owner.applied(guard=guard):
            report = item["report"]
            if report is None:
                continue
            state = apply_report(state, report)
            if report["snapshot_boundary_after"] is None:
                continue
            boundary = self.owner.boundary(report["consolidation_id"], guard=guard) or self._rebuilt(item, state, guard)
            if boundary is not None:
                latest = (boundary, report["apply"]["digest"])
        return latest

    def _pinned(self, run, guard) -> tuple[dict | None, dict | None]:
        opening = opening_of(run.get("history"))
        if opening is None:
            return self.latest_boundary(guard)
        return self._recorded_pin(opening, guard), opening.get("digest")

    def _complete(self, boundary_id: str, guard) -> tuple[dict, dict | None]:
        """A complete boundary of this owner's history and its digest (read-only; ``guard`` may be ``None``)."""
        for item in self.owner.applied(guard=guard):
            if item["report"] is not None and item["report"]["snapshot_boundary_after"] == boundary_id:
                boundary = self.owner.boundary(item["report"]["consolidation_id"], guard=guard)
                if boundary is not None:
                    return boundary, item["report"]["apply"]["digest"]
        raise MemoryOwnerViolation("a pinned boundary is not a complete boundary of its owner's history")

    def _recorded_pin(self, opening, guard) -> dict | None:
        """The boundary a run recorded at its opening."""
        return None if opening.get("boundary") is None else self._complete(opening["boundary"], guard)[0]

    def admitted_now(self, habit_id: str, item: Mapping[str, Any]) -> bool:
        """Gold admits this learned behavior for the current attempt and tool binding."""
        self.gateway  # noqa: B018 - binds the legitimacy port
        return bool(self.legitimacy.status(habit_id, {"publication": item["publication"]})["admitted"])

    # -- sessions --------------------------------------------------------------
    def open_session(self, run: Mapping[str, Any]) -> MemorySession:
        if self.exam is not None:
            return self._exam_session(run)
        with self.owner.store.session() as guard:
            self.owner.bind(guard, self.configuration)
            self.owner.register_session(guard, dict(run), self.configuration)
            boundary, digest = self._pinned(run, guard)
        return MemorySession(self, dict(run), boundary, digest)

    def _exam_session(self, run: Mapping[str, Any]) -> MemorySession:
        """An exam reads a fixed snapshot of this memory and is never registered for the court."""
        bound = self.owner.bound_configuration()
        if bound is None or bound.configuration_sha256 != self.configuration.configuration_sha256:
            raise MemoryOwnerViolation("an exam reads a memory bound to its own configuration")
        boundary, digest = self._complete(self.exam["snapshot"], None)
        if self.exam["mode"] == "A":
            boundary, digest = None, None  # Accumulated experience is switched off.
        return MemorySession(self, dict(run), boundary, digest, self.exam["mode"])

    def replay_session(self, run: Mapping[str, Any]) -> ReplaySession:
        opening = opening_of(run["history"]) or {}
        # Read-only: a re-execution runs inside the court, which already holds the owner session.
        boundary = self._recorded_pin(opening, None) if opening else None
        return ReplaySession(self, dict(run), boundary, opening.get("digest"), opening.get("learned"),
                             opening.get("exam"))

    def reproduction_session(self, run: Mapping[str, Any]) -> ReproductionSession:
        opening = opening_of(run["history"]) or {}
        boundary = self._recorded_pin(opening, None) if opening else None
        return ReproductionSession(self, dict(run), boundary, opening.get("digest"), opening.get("learned"),
                                   opening.get("exam"))

    def recover(self, run: Mapping[str, Any], *, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Emergency consolidation of a crashed session's tail, before it continues; an exam has none."""
        if self.exam is not None:
            return None
        return self.court("emergency", current={**dict(run), "history": history})

    # -- court -------------------------------------------------------------------
    def ports(self) -> CourtPorts:
        from synapse.durable_cognitive import (
            read_cognitive_session, replay_cognitive_history, reproduce_cognitive_session)

        def replay(session):
            run = {"run_id": session["run_id"], "artifact_path": "", "source_hash": session["source_hash"],
                   "source_code": session["source_code"], "initial_bindings": session["initial_bindings"],
                   "history": session["history"][:session["to"]]}
            return replay_cognitive_history(run_id=run["run_id"], source_code=run["source_code"],
                                            initial_bindings=run["initial_bindings"], history=run["history"],
                                            session=self.replay_session(run),
                                            event_budget=self.configuration.parameters["replay_event_budget"])

        def reproduce(data):
            # Replay data in custody carries the opening its session started from, and nothing more.
            run = {"run_id": data["run_id"], "artifact_path": "", "source_hash": data["source_hash"],
                   "source_code": data["source_code"], "initial_bindings": data["initial_bindings"],
                   "history": [] if data["opening"] is None else [data["opening"]]}
            return reproduce_cognitive_session(run_id=run["run_id"], source_code=run["source_code"],
                                               initial_bindings=run["initial_bindings"],
                                               session=self.reproduction_session(run),
                                               event_budget=self.configuration.parameters["replay_event_budget"])

        return CourtPorts(gateway=self.gateway, executor=self.executor, legitimacy=self.legitimacy,
                          read_session=lambda entry: read_cognitive_session(Path(entry["artifact_path"])),
                          replay=replay, reproduce=reproduce)

    def court(self, mode: str, *, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ports = self.ports()
        with self.owner.store.session() as guard:
            self.owner.bind(guard, self.configuration)
            return consolidate(self.owner, self.configuration, ports, guard, mode=mode, current=current)


    # -- the governing operator's acts on retained experience -------------------
    def forget(self, qid: str, *, reason: str, operator: str) -> list[dict[str, Any]]:
        from .operator_acts import forget

        ports = self.ports()
        with self.owner.store.session() as guard:
            self.owner.bind(guard, self.configuration)
            return forget(self.owner, ports, guard, qid, reason=reason, operator=operator)

    def restore(self, qid: str) -> dict[str, Any]:
        from .operator_acts import restore

        ports = self.ports()
        with self.owner.store.session() as guard:
            self.owner.bind(guard, self.configuration)
            return restore(self.owner, ports, guard, qid, self.configuration.parameters)


def resolve_memory(descriptor: Mapping[str, Any]) -> MemoryFactory:
    """Rebuild the factory a run recorded; a changed binding is refused."""
    if (type(descriptor) is not dict or set(descriptor) != {"schema_version", "project_state",
                                                            "configuration_sha256", "executor", "exam"}
            or descriptor["schema_version"] != MEMORY_BINDING_V1):
        raise MemoryOwnerViolation("memory binding descriptor has an unknown contract")
    owner = MemoryOwner(Path(descriptor["project_state"]), read_only=True)
    configuration = owner.bound_configuration()
    if configuration is None or configuration.configuration_sha256 != descriptor["configuration_sha256"]:
        raise MemoryOwnerViolation("the run's memory owner is bound to another configuration")
    factory = MemoryFactory(Path(descriptor["project_state"]), configuration, exam=descriptor["exam"])
    if factory.executor != descriptor["executor"]:
        raise MemoryOwnerViolation("the run was executed by another runtime executor")
    return factory
