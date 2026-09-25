"""The memory owner: a connected Gold project and its records in the project journal.

Memory records live in the project's existing element-owner journal (store B)
under its one owner session: the bound memory configuration, each durable
session's opening, consolidation reports and snapshot boundaries. The
applied decisions themselves are Gold's court chain; the current memory state
is the fold of the reports that chain names, in order. The gateway of the
owner's runs lives beside the journal. An owner binds one memory configuration;
another configuration is another policy and is refused, never mixed in.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from synapse.experiments.gold.project_court import court_chain
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore

from . import records
from .configuration import MemoryConfiguration, parse_memory_configuration
from .court.projection import fold

OWNER_BINDING_V1 = "synapse.memory.owner-binding/v1"
SESSION_OPENED_V1 = "synapse.memory.session-opened/v1"
RETENTION_PASS_V1 = "synapse.memory.retention-pass/v1"


class MemoryOwnerViolation(ValueError):
    """The memory owner's records contradict each other or the request."""


def _key(*parts: str) -> str:
    return hashlib.sha256(records.canonical(list(parts))).hexdigest()


def consolidation_key(consolidation_id: str) -> str:
    return consolidation_id.partition("_")[2]


class MemoryOwner:
    """Records of one connected project's memory, read and written under its owner session."""

    def __init__(self, state_root: Path, *, read_only: bool = False, store: ProjectMemoryStore | None = None) -> None:
        self.state_root = Path(state_root).resolve()
        raw = (self.state_root / "project.json").read_bytes()
        self.identity = hashlib.sha256(raw).hexdigest()
        # A court reached from Gold's own owner session writes through that session's store.
        self.store = store if store is not None else ProjectMemoryStore(self.state_root, read_only=read_only)
        self.gateway_root = self.state_root / "memory-gateway"

    # -- the bound configuration ----------------------------------------
    def bind(self, guard, configuration: MemoryConfiguration) -> None:
        """Bind the owner's one configuration; a different one is refused."""
        bound = self.bound_configuration(guard=guard)
        if bound is not None and bound.configuration_sha256 != configuration.configuration_sha256:
            raise MemoryOwnerViolation("this memory owner is bound to another memory configuration")
        self.store.put(kind="MEMORY_BOUND", job_key=_key("synapse.memory.owner", self.identity), guard=guard,
                       payload={"schema_version": OWNER_BINDING_V1, "project_identity": self.identity,
                                "configuration": dict(configuration.raw),
                                "configuration_sha256": configuration.configuration_sha256})

    def bound_configuration(self, *, guard=None) -> MemoryConfiguration | None:
        key = _key("synapse.memory.owner", self.identity)
        for event, _ in self.store.inventory(guard=guard):
            if event["kind"] == "MEMORY_BOUND" and event["job_key"] == key:
                configuration = parse_memory_configuration(event["payload"]["configuration"])
                if configuration.configuration_sha256 != event["payload"]["configuration_sha256"]:
                    raise MemoryOwnerViolation("bound memory configuration differs from its digest")
                return configuration
        return None

    # -- sessions ---------------------------------------------------------
    def register_session(self, guard, run: dict[str, Any], configuration: MemoryConfiguration) -> dict:
        payload = {"schema_version": SESSION_OPENED_V1, "project_identity": self.identity,
                   "run_id": run["run_id"], "artifact_path": run["artifact_path"], "source_hash": run["source_hash"],
                   "configuration_sha256": configuration.configuration_sha256}
        return self.store.put(kind="SESSION_OPENED", job_key=_key("synapse.memory.session", self.identity,
                                                                  run["run_id"]), payload=payload, guard=guard)

    def sessions(self, *, guard=None) -> list[dict[str, Any]]:
        found = [event["payload"] for event, _ in self.store.inventory(guard=guard)
                 if event["kind"] == "SESSION_OPENED" and event["payload"]["project_identity"] == self.identity]
        return sorted(found, key=lambda item: item["run_id"])

    # -- reports, state and boundaries ---------------------------------------
    def put_report(self, guard, report: dict[str, Any]) -> dict:
        records.verify(report, "consolidation_report")
        return self.store.put(kind="CONSOLIDATION_REPORTED", guard=guard, payload={"report": report},
                              job_key=consolidation_key(report["report"]["consolidation_id"]))

    def read_report(self, receipt) -> dict[str, Any]:
        event = self.store.read(receipt)
        if event["kind"] != "CONSOLIDATION_REPORTED":
            raise MemoryOwnerViolation("a court decision names no consolidation report")
        return records.verify(event["payload"]["report"], "consolidation_report")["report"]

    def applied(self, *, guard=None) -> list[dict[str, Any]]:
        """The court chain with each decision's consolidation report (``None`` for exact-only decisions)."""
        chain, _ = court_chain(self.store, project_identity=self.identity, guard=guard)
        result = []
        for payload, receipt in chain:
            consolidation = payload.get("consolidation")
            report = None
            if consolidation is not None and consolidation["report"] is not None:
                report = self.read_report(consolidation["report"])
                if report["consolidation_id"] != consolidation["consolidation_id"]:
                    raise MemoryOwnerViolation("a decision names another consolidation's report")
            result.append({"decision": payload, "receipt": receipt, "report": report})
        return result

    def state(self, *, guard=None) -> dict[str, Any]:
        """The memory state: the fold of every applied report, in chain order."""
        return fold(item["report"] for item in self.applied(guard=guard) if item["report"] is not None)

    # -- retention passes ------------------------------------------------------
    def put_retention(self, guard, sequence: int, window: int, acts: list[dict[str, Any]]) -> dict:
        """Record one retention pass; its acts are the facts the next report applies."""
        if sequence != len(self.retention_passes(guard=guard)):
            raise MemoryOwnerViolation("retention passes are recorded in order")
        return self.store.put(kind="MEMORY_RETENTION", job_key=_key("synapse.memory.retention", self.identity,
                                                                    str(sequence)), guard=guard,
                              payload={"schema_version": RETENTION_PASS_V1, "project_identity": self.identity,
                                       "sequence": sequence, "window": window, "acts": acts})

    def retention_passes(self, *, guard=None) -> list[dict[str, Any]]:
        passes = sorted((event["payload"] for event, _ in self.store.inventory(guard=guard)
                         if event["kind"] == "MEMORY_RETENTION"
                         and event["payload"]["project_identity"] == self.identity),
                        key=lambda item: item["sequence"])
        if [item["sequence"] for item in passes] != list(range(len(passes))):
            raise MemoryOwnerViolation("retention passes are not a contiguous sequence")
        return passes

    def put_boundary(self, guard, boundary: dict[str, Any]) -> dict:
        records.verify(boundary, "snapshot_boundary")
        consolidation_id = boundary["boundary"]["consolidation_id"]
        return self.store.put(kind="SNAPSHOT_BOUNDARY", job_key=consolidation_key(consolidation_id), guard=guard,
                              payload={"boundary": boundary})

    def boundary(self, consolidation_id: str, *, guard=None) -> dict[str, Any] | None:
        key = consolidation_key(consolidation_id)
        for event, _ in self.store.inventory(guard=guard):
            if event["kind"] == "SNAPSHOT_BOUNDARY" and event["job_key"] == key:
                return records.verify(event["payload"]["boundary"], "snapshot_boundary")
        return None
