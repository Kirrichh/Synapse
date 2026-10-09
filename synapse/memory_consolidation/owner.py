"""The memory owner: a connected Gold project and its records in the project journal.

Memory records live in the project's existing element-owner journal (store B)
under its one owner session: the bound memory configuration, each durable
session's opening, consolidation reports and snapshot boundaries. The
applied decisions themselves are Gold's court chain; the current memory state
is the fold of the reports that chain names, in order. A verified snapshot
of that fold at a known cut of the chain spares re-reading the prefix: it is
used only when its projection version, its cut (the decision and report it
was taken after) and the digest of its state all match, and the journal stays
the source the state is rebuilt from otherwise. The gateway of the
owner's runs lives beside the journal. An owner binds one memory configuration;
another configuration is another policy and is refused, never mixed in — until a
reassessment re-judges the memory under the new one and the owner adopts it.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from synapse.experiments.gold.project_court import court_chain
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore

from . import records
from .configuration import MemoryConfiguration, parse_memory_configuration
from .court.projection import PROJECTION_V2, apply_report, empty_state

OWNER_BINDING_V1 = "synapse.memory.owner-binding/v1"
#: A later binding names the configuration it supersedes, the reassessment that had adopted that one (none for
#: the first binding) and the reassessment that adopts it.
OWNER_BINDING_V2 = "synapse.memory.owner-binding/v2"
SESSION_OPENED_V1 = "synapse.memory.session-opened/v1"
#: v2 (review M6): a forget names the gateway's head it was recorded at (``after``); a v1 forget names none.
RETENTION_PASS_V2 = "synapse.memory.retention-pass/v2"
STATE_SNAPSHOT_V1 = "synapse.memory.state-snapshot/v1"
#: How many applied reports a state snapshot follows the previous one by.
SNAPSHOT_EVERY = 8


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
    def _bindings(self, guard=None) -> list[dict[str, Any]]:
        """Every binding of this owner in the order it was adopted; the last is in force.

        The first binding supersedes nothing; each later one names the configuration it superseded, so the
        order is the chain of those links, never the journal's storage order."""
        found = [event["payload"] for event, _ in self.store.inventory(guard=guard)
                 if event["kind"] == "MEMORY_BOUND" and event["payload"]["project_identity"] == self.identity]
        chain = [item for item in found if item.get("supersedes") is None]
        if len(chain) > 1:
            raise MemoryOwnerViolation("a memory owner has more than one first binding")
        left = [item for item in found if item.get("supersedes") is not None]
        while chain and left:
            last = chain[-1]
            following = [item for item in left if item["supersedes"] == last["configuration_sha256"]
                         and item["after"] == last.get("reassessment")]
            if len(following) != 1:
                raise MemoryOwnerViolation("the memory owner's bindings do not form one chain")
            chain.append(following[0])
            left.remove(following[0])
        if left:
            raise MemoryOwnerViolation("the memory owner's bindings do not form one chain")
        return chain

    def bound_digest(self, *, guard=None) -> str | None:
        """The digest of the configuration in force, read without interpreting it."""
        bindings = self._bindings(guard)
        return bindings[-1]["configuration_sha256"] if bindings else None

    def bind(self, guard, configuration: MemoryConfiguration) -> None:
        """Bind the owner's one configuration; a different one is refused (a reassessment changes it)."""
        bound = self.bound_digest(guard=guard)
        if bound is not None and bound != configuration.configuration_sha256:
            raise MemoryOwnerViolation("this memory owner is bound to another memory configuration; "
                                       "a new configuration is adopted only by 'synapse memory reassess'")
        if bound is None:
            self.store.put(kind="MEMORY_BOUND", job_key=_key("synapse.memory.owner", self.identity), guard=guard,
                           payload={"schema_version": OWNER_BINDING_V1, "project_identity": self.identity,
                                    "configuration": dict(configuration.raw),
                                    "configuration_sha256": configuration.configuration_sha256})

    def rebind(self, guard, configuration: MemoryConfiguration, *, reassessment: str) -> None:
        """Adopt ``configuration`` after the reassessment that re-judged the memory under it."""
        bindings = self._bindings(guard)
        if not bindings:
            raise MemoryOwnerViolation("only a bound memory owner is reassessed")
        previous = bindings[-1]
        if previous["configuration_sha256"] == configuration.configuration_sha256:
            return
        key = _key("synapse.memory.owner", self.identity, configuration.configuration_sha256, reassessment)
        self.store.put(kind="MEMORY_BOUND", guard=guard, job_key=key,
                       payload={"schema_version": OWNER_BINDING_V2, "project_identity": self.identity,
                                "configuration": dict(configuration.raw),
                                "configuration_sha256": configuration.configuration_sha256,
                                "supersedes": previous["configuration_sha256"],
                                "after": previous.get("reassessment"), "reassessment": reassessment})

    def bound_configuration(self, *, guard=None) -> MemoryConfiguration | None:
        bindings = self._bindings(guard)
        if not bindings:
            return None
        configuration = parse_memory_configuration(bindings[-1]["configuration"])
        if configuration.configuration_sha256 != bindings[-1]["configuration_sha256"]:
            raise MemoryOwnerViolation("bound memory configuration differs from its digest")
        return configuration

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

    def _report(self, consolidation, retained) -> dict[str, Any]:
        """The report a decision names, from the journal events the chain was validated against."""
        found = retained.get((consolidation_key(consolidation["consolidation_id"]), "CONSOLIDATION_REPORTED"))
        if found is None or found[1] != consolidation["report"]:
            raise MemoryOwnerViolation("a court decision names no consolidation report")
        report = records.verify(found[0]["payload"]["report"], "consolidation_report")["report"]
        if report["consolidation_id"] != consolidation["consolidation_id"]:
            raise MemoryOwnerViolation("a decision names another consolidation's report")
        return report

    def applied(self, *, guard=None) -> list[dict[str, Any]]:
        """The court chain with each decision's consolidation report (``None`` for exact-only decisions)."""
        chain, retained = court_chain(self.store, project_identity=self.identity, guard=guard)
        result = []
        for payload, receipt in chain:
            consolidation = payload.get("consolidation")
            report = None
            if consolidation is not None and consolidation["report"] is not None:
                report = self._report(consolidation, retained)
            result.append({"decision": payload, "receipt": receipt, "report": report})
        return result

    def require_policy(self, configuration: MemoryConfiguration, *, guard=None) -> None:
        """Memory decided under another court policy is reassessed before this one reads or extends it."""
        for item in reversed(self.applied(guard=guard)):
            if item["report"] is not None:
                recorded = item["report"]["policy"]["policy"]
                if recorded != configuration.policy["policy"]:
                    raise MemoryOwnerViolation(f"this memory was decided under {recorded}; reassess it with "
                                               f"'synapse memory reassess' before {configuration.policy['policy']}")
                return

    def state(self, *, guard=None) -> dict[str, Any]:
        """The memory state: the fold of every applied report, in chain order — from the latest verified
        snapshot on, when one matches the chain."""
        chain, retained = court_chain(self.store, project_identity=self.identity, guard=guard)
        state, start = self._snapshot(chain, retained)
        for payload, _ in chain[start:]:
            consolidation = payload.get("consolidation")
            if consolidation is not None and consolidation["report"] is not None:
                state = apply_report(state, self._report(consolidation, retained))
        return state

    def _snapshot(self, chain, retained) -> tuple[dict[str, Any], int]:
        """The latest snapshot whose version, cut and digest match the chain, and where the fold resumes."""
        snapshots = sorted((event["payload"] for (_, kind), (event, _) in retained.items()
                            if kind == "MEMORY_STATE" and event["payload"]["project_identity"] == self.identity),
                           key=lambda item: item["through"]["index"], reverse=True)
        for snapshot in snapshots:
            index, cut = snapshot["through"]["index"], snapshot["through"]
            if (snapshot["schema_version"] != STATE_SNAPSHOT_V1 or snapshot["projection"] != PROJECTION_V2
                    or not 0 <= index < len(chain) or chain[index][0].get("consolidation") is None
                    or chain[index][0]["consolidation"]["consolidation_id"] != cut["consolidation_id"]
                    or chain[index][0]["consolidation"]["report"] != cut["report"]
                    or records.digest(snapshot["state"]) != snapshot["state_sha256"]):
                continue  # Another version, a cut the chain does not hold, or damaged: never used.
            return copy.deepcopy(snapshot["state"]), index + 1
        return empty_state(), 0

    def put_state_snapshot(self, guard) -> dict | None:
        """Record the state at the chain's head every ``SNAPSHOT_EVERY`` reports (the court, under its session)."""
        chain, retained = court_chain(self.store, project_identity=self.identity, guard=guard)
        reports = [index for index, (payload, _) in enumerate(chain)
                   if payload.get("consolidation") is not None and payload["consolidation"]["report"] is not None]
        if not reports or len(reports) % SNAPSHOT_EVERY or reports[-1] != len(chain) - 1:
            return None
        head = chain[-1][0]["consolidation"]
        state = self.state(guard=guard)
        return self.store.put(kind="MEMORY_STATE", guard=guard,
                              job_key=_key("synapse.memory.state", self.identity, head["consolidation_id"]),
                              payload={"schema_version": STATE_SNAPSHOT_V1, "project_identity": self.identity,
                                       "projection": PROJECTION_V2,
                                       "through": {"index": len(chain) - 1, "consolidation_id": head["consolidation_id"],
                                                   "report": head["report"]},
                                       "state_sha256": records.digest(state), "state": state})

    # -- retention passes ------------------------------------------------------
    def put_retention(self, guard, sequence: int, window: int, acts: list[dict[str, Any]]) -> dict:
        """Record one retention pass; its acts are the facts the next report applies."""
        if sequence != len(self.retention_passes(guard=guard)):
            raise MemoryOwnerViolation("retention passes are recorded in order")
        return self.store.put(kind="MEMORY_RETENTION", job_key=_key("synapse.memory.retention", self.identity,
                                                                    str(sequence)), guard=guard,
                              payload={"schema_version": RETENTION_PASS_V2, "project_identity": self.identity,
                                       "sequence": sequence, "window": window, "acts": acts})

    def retention_passes(self, *, guard=None) -> list[dict[str, Any]]:
        passes = sorted((event["payload"] for event, _ in self.store.inventory(guard=guard)
                         if event["kind"] == "MEMORY_RETENTION"
                         and event["payload"]["project_identity"] == self.identity),
                        key=lambda item: item["sequence"])
        if [item["sequence"] for item in passes] != list(range(len(passes))):
            raise MemoryOwnerViolation("retention passes are not a contiguous sequence")
        return passes

    # -- stand trials (review R5) --------------------------------------------------
    def put_trial(self, guard, trial: dict[str, Any]) -> dict:
        """Record one judged stand trial; the court reads every recorded trial when it compares competitors."""
        return self.store.put(kind="MEMORY_TRIAL", guard=guard, job_key=_key("synapse.memory.trial", self.identity,
                                                                             trial["id"]),
                              payload={"project_identity": self.identity, "trial": trial})

    def trials(self, *, guard=None) -> list[dict[str, Any]]:
        return sorted((event["payload"]["trial"] for event, _ in self.store.inventory(guard=guard)
                       if event["kind"] == "MEMORY_TRIAL" and event["payload"]["project_identity"] == self.identity),
                      key=lambda item: item["id"])

    def put_boundary(self, guard, boundary: dict[str, Any]) -> dict:
        records.verify(boundary, "snapshot_boundary")
        consolidation_id = boundary["boundary"]["consolidation_id"]
        return self.store.put(kind="SNAPSHOT_BOUNDARY", job_key=consolidation_key(consolidation_id), guard=guard,
                              payload={"boundary": boundary})

    def boundary(self, consolidation_id: str, *, guard=None) -> dict[str, Any] | None:
        return self.boundaries(guard=guard).get(consolidation_id)

    def boundaries(self, *, guard=None) -> dict[str, dict[str, Any]]:
        """Every recorded snapshot boundary by the consolidation it was built from, read in one pass."""
        found = {}
        for event, _ in self.store.inventory(guard=guard):
            if event["kind"] == "SNAPSHOT_BOUNDARY":
                boundary = records.verify(event["payload"]["boundary"], "snapshot_boundary")
                if consolidation_key(boundary["boundary"]["consolidation_id"]) != event["job_key"]:
                    raise MemoryOwnerViolation("a snapshot boundary is filed under another consolidation")
                found[boundary["boundary"]["consolidation_id"]] = boundary
        return found
