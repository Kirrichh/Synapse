"""Retention of experience by significance (memory spec part 3 §8.2–§8.5, И9; refinement §9).

A separate step after an applied consolidation, under the same owner session
— the one writer. It executes the retention plan the reports scheduled:

* a medium case whose raw trace is due leaves custody only after its session,
  re-executed from the replay data and the recorded results alone, reproduces
  the case's exact events at their recorded positions (``replay_only``); any
  other result keeps the trace and raises the case to high, with the reason;
* a low case is reduced to its tail form (identity, canonical part, statistics)
  and tail quanta older than ``k_rollup`` windows are rolled up into aggregates
  that name their qids;
* nothing is removed while the case is the basis of a live habit (blocked, and
  high); a declared ``raw_capacity`` compacts eligible cases early and
  publishes the detail it lost, never a decision basis.

A case without its raw trace stays pinned to the executor that recorded it:
the court refuses a journal with episodes of another executor, so its replay
data is always reproduced by the same executor.

Every pass is recorded in the project journal before any body leaves store D,
so "removed but not recorded" never exists; an interrupted pass is completed
from its record. The next consolidation applies the recorded acts to the
quanta — retention never edits memory state in place.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .custody import materialize, reproduce_session, reproduces
from .habit_state import EFFECTIVE

#: Acts whose body leaves store D, and the reason its marker names.
_REMOVING = {"raw_deleted": "compacted", "tail": "compacted"}


def pending_acts(passes: list[Mapping[str, Any]], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recorded acts the memory state has not applied yet, in pass order."""
    return [dict(act, window=item["window"]) for item in passes[state["retention"]["cursor"]:]
            for act in item["acts"]]


def _removed(entry, **changes) -> dict[str, Any]:
    """A quantum whose raw trace left custody; ``raw_ref`` keeps naming the trace it was."""
    return {**copy.deepcopy(entry), "retention": None, **changes}


def apply_acts(state: Mapping[str, Any], acts: list[Mapping[str, Any]], passes: int) -> tuple[dict, dict]:
    """Quanta updated by recorded acts, and the retention section after them (pure)."""
    quanta = {qid: copy.deepcopy(entry) for qid, entry in state["quanta"].items()}
    retention = copy.deepcopy(dict(state["retention"]))
    touched: dict[str, dict[str, Any]] = {}
    for act in acts:
        qid, kind = act.get("qid"), act["act"]
        entry = quanta.get(qid)
        if kind == "raw_deleted":
            entry = _removed(entry, retention_state="replay_only", replay_status="replay_verified",
                             executor=act["executor"])
        elif kind == "tail":
            entry = _removed(entry, retention_state="tail", syn_form=None, replay_ref=None, tail_since=act["window"])
        elif kind in {"kept", "blocked"}:
            entry = {**entry, "tier": "high", "tier_rule": act["reason"], "retention": None}
        elif kind == "rolled_up":
            retention["rollups"].append({"cluster": act["cluster"], "qids": act["qids"], "window": act["window"]})
            for member in act["qids"]:
                touched[member] = quanta[member] = {**quanta[member], "retention_state": "rolled_up"}
            continue
        elif kind == "forgotten":
            entry = _removed(entry, retention_state="forgotten", syn_form=None, replay_ref=None, raw_ref=None,
                             evidence_refs=[], tombstone=act["tombstone"])
            retention["tombstones"][qid] = {"tombstone": act["tombstone"], "reason": act["reason"],
                                            "authority": act["authority"], "window": act["window"]}
        elif kind == "restored":
            entry = {**entry, "retention_state": "full", "tier": "medium", "tier_rule": "restored",
                     "retention": act["retention"]}
        else:
            continue  # "capacity" is a published fact about the owner, not about one quantum
        touched[qid] = quanta[qid] = entry
    retention["cursor"] = passes
    return touched, retention


def _live_bases(state) -> dict[str, list[str]]:
    bases: dict[str, list[str]] = {}
    for habit_id, metadata in sorted(state["habits"].items()):
        if metadata["state"] in EFFECTIVE:
            for qid in state["frozen"][habit_id]["habit"]["born_from"]["episodes"]:
                bases.setdefault(qid, []).append(habit_id)
    return bases


class _Pass:
    """One retention pass over the applied state (window ``state["window"]``)."""

    def __init__(self, state, parameters, ports, handled) -> None:
        self.state, self.parameters, self.ports = state, parameters, ports
        self.window = state["window"]
        self.handled = set(handled)
        self.bases = _live_bases(state)
        self.acts: list[dict[str, Any]] = []
        self.reproduced: dict[str, dict[str, Any]] = {}

    def _held(self) -> list[tuple[str, dict]]:
        return [(qid, entry) for qid, entry in sorted(self.state["quanta"].items())
                if qid not in self.handled and entry["retention_state"] == "full"]

    def _reproduction(self, data_ref: str) -> dict[str, Any]:
        if data_ref not in self.reproduced:
            self.reproduced[data_ref] = reproduce_session(self.ports.reproduce, self.ports.gateway.evidence, data_ref)
        return self.reproduced[data_ref]

    def _verified(self, entry) -> tuple[bool, str]:
        replay = entry["replay_ref"]
        result = self._reproduction(replay["data_ref"])
        if result["status"] != "reproduced":
            return False, result["status"]
        if not reproduces(result["history"], replay["positions"], entry["canonical"]["trace_ref"]):
            return False, "replay_diverged"
        return True, "replay_verified"

    def _act(self, **act) -> None:
        self.acts.append(act)
        if "qid" in act:
            self.handled.add(act["qid"])

    def _compact(self, qid, entry, *, reason) -> bool:
        """Remove a held raw trace when its plan allows; ``False`` when it must stay."""
        if qid in self.bases:
            self._act(act="blocked", qid=qid, reason="decision_basis", habits=self.bases[qid])
            return False
        plan = entry["retention"]
        if plan is None:
            return False
        if plan["action"] == "keep_tail_form":
            self._act(act="tail", qid=qid, raw_ref=entry["raw_ref"], reason=reason)
            return True
        verified, status = self._verified(entry)
        if verified:
            self._act(act="raw_deleted", qid=qid, raw_ref=entry["raw_ref"], replay=status,
                      executor=self.ports.executor, reason=reason)
            return True
        self._act(act="kept", qid=qid, reason=f"retention_{status}")
        return False

    def due(self) -> None:
        for qid, entry in self._held():
            plan = entry["retention"]
            if plan is not None and plan["due"] <= self.window:
                self._compact(qid, entry, reason="due")

    def capacity(self) -> None:
        """A declared limit on held raw traces: early compaction of eligible cases, the loss published."""
        limit = self.parameters["raw_capacity"]
        held = self._held()
        if not limit or len(held) <= limit:
            return
        lost, excess = [], len(held) - limit
        for qid, entry in sorted(held, key=lambda item: (item[1]["born_in"], item[0])):
            if excess == 0:
                break
            if entry["retention"] is not None and qid not in self.bases and self._compact(qid, entry,
                                                                                          reason="capacity"):
                lost.append(qid)
                excess -= 1
        self._act(act="capacity", limit=limit, held=len(held), held_after=len(held) - len(lost),
                  compacted_early=lost, over_limit=excess)

    def rollup(self) -> None:
        clusters: dict[str, list[str]] = {}
        for qid, entry in sorted(self.state["quanta"].items()):
            if (qid not in self.handled and entry["retention_state"] == "tail"
                    and self.window - entry["tail_since"] >= self.parameters["k_rollup"]):
                clusters.setdefault(f"{entry['element_id']}|{entry['canonical']['outcome_class']}", []).append(qid)
        for cluster, qids in sorted(clusters.items()):
            self._act(act="rolled_up", cluster=cluster, qids=qids)
            self.handled.update(qids)


def complete(ports, acts: list[Mapping[str, Any]]) -> None:
    """Carry out the physical changes recorded acts decided (idempotent).

    Only the last recorded pass can be incomplete: every pass completes the one
    before it is recorded, so an interrupted pass is completed here, from its
    record, and never decided again.
    """
    evidence = ports.gateway.evidence
    for act in acts:
        reason = _REMOVING.get(act["act"])
        if reason is not None:
            evidence.discard(act["raw_ref"], reason)
        elif act["act"] == "forgotten":
            for ref in act["refs"]:
                evidence.discard(ref, "forgotten", tombstone=act["tombstone"])
        elif act["act"] == "restored" and evidence.get(act["raw_ref"]) is None:
            restored = reproduce_session(ports.reproduce, evidence, act["data_ref"])
            if restored["status"] == "reproduced":
                materialize(evidence, act, restored["history"])


def retention_pass(owner, configuration, ports, guard) -> list[dict[str, Any]]:
    """Execute the due retention plan once, after an applied consolidation."""
    state = owner.state(guard=guard)
    passes = owner.retention_passes(guard=guard)
    if passes:
        complete(ports, passes[-1]["acts"])
    waiting = pending_acts(passes, state)
    step = _Pass(state, configuration.parameters, ports, {act["qid"] for act in waiting if "qid" in act})
    step.due()
    step.capacity()
    step.rollup()
    if not step.acts:
        return []
    owner.put_retention(guard, len(passes), state["window"], step.acts)
    complete(ports, step.acts)
    return step.acts
