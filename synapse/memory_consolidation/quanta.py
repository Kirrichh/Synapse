"""Case quanta, their hierarchy, weight and significance (substage 7a).

One execution of a plan segment is one case quantum; off-plan actions of one
run form one off-plan quantum. The immutable part is content-addressed: task
contract, marker, program, executor, the knowledge boundary at session start,
the ordered recorded results, the trace of the case's events and its outcome
class. The same case yields the same ``qid`` and only increases its statistics.
Vectors, weights, verdicts, wall time and durations never enter the identity.

A case's element part and element keep Merkle roots over sorted ``qid`` values
and sorted part roots, so membership is provable with a logarithmic path and
every report records the element's memory history as roots.

Weight ``W = C × R × k_dir`` and the significance tier follow spec part 3 §8
and §9. Time lives only in the gateway side log; a case without a measured
duration is excluded from time-dependent quantities and the coverage says so.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from . import records
from .records import canonical, digest

EXECUTOR_REF = "synapse.durable.cognitive/v1"


def trace_ref(events: Iterable[Mapping[str, Any]]) -> str:
    """Hash of a case's canonical recorded events, in order."""
    return "sha256:" + digest([dict(item) for item in events])


def case_quantum(*, task_contract_ref: str | None, segment_marker_id: str | None, program_ref: str,
                 start_snapshot_ref: str | None, recorded_results: list[str], trace: str,
                 outcome_class: str) -> dict[str, Any]:
    return records.make("case_quantum", canonical={
        "task_contract_ref": task_contract_ref, "segment_marker_id": segment_marker_id,
        "program_ref": program_ref, "executor_ref": EXECUTOR_REF, "start_snapshot_ref": start_snapshot_ref,
        "recorded_results": list(recorded_results), "trace_ref": trace, "outcome_class": outcome_class})


def merkle_root(domain: str, name: str, leaves: Iterable[str]) -> str:
    """Root over sorted leaves, domain- and name-separated; odd nodes are carried up."""
    level = [hashlib.sha256(canonical([domain, name, "leaf", leaf])).hexdigest() for leaf in sorted(set(leaves))]
    if not level:
        return "sha256:" + hashlib.sha256(canonical([domain, name, "empty"])).hexdigest()
    while len(level) > 1:
        paired = []
        for index in range(0, len(level), 2):
            if index + 1 < len(level):
                paired.append(hashlib.sha256(canonical([domain, "node", level[index], level[index + 1]])).hexdigest())
            else:
                paired.append(level[index])
        level = paired
    return "sha256:" + level[0]


def membership_path(domain: str, name: str, leaves: Iterable[str], leaf: str) -> list[dict[str, str]]:
    """Sibling hashes from one leaf to the root (⌈log₂ n⌉ per level)."""
    ordered = sorted(set(leaves))
    if leaf not in ordered:
        raise ValueError("leaf is not a member")
    level = [hashlib.sha256(canonical([domain, name, "leaf", item])).hexdigest() for item in ordered]
    index, path = ordered.index(leaf), []
    while len(level) > 1:
        sibling = index ^ 1
        if sibling < len(level):
            path.append({"side": "left" if sibling < index else "right", "hash": level[sibling]})
        paired = []
        for position in range(0, len(level), 2):
            if position + 1 < len(level):
                paired.append(hashlib.sha256(canonical([domain, "node", level[position], level[position + 1]])).hexdigest())
            else:
                paired.append(level[position])
        level, index = paired, index // 2
    return path


def verify_membership(domain: str, name: str, leaf: str, path: list[Mapping[str, str]], root: str) -> bool:
    current = hashlib.sha256(canonical([domain, name, "leaf", leaf])).hexdigest()
    for step in path:
        pair = [step["hash"], current] if step["side"] == "left" else [current, step["hash"]]
        current = hashlib.sha256(canonical([domain, "node", *pair])).hexdigest()
    return "sha256:" + current == root


def part_record(element_id: str, part_id: str, qids: Iterable[str]) -> dict[str, Any]:
    return records.make("element_part", element_id=element_id, part_id=part_id,
                        root=merkle_root("synapse.memory.element-part", f"{element_id}/{part_id}", qids))


def element_record(element_id: str, part_roots: Iterable[str]) -> dict[str, Any]:
    return records.make("element_root", element_id=element_id,
                        root=merkle_root("synapse.memory.element", element_id, part_roots))


def weight(parameters: Mapping[str, Any], *, steps: int, external_calls: int, branches: int, retries: int,
           tokens: int, seconds: float | None, gas: int) -> dict[str, Any]:
    """``W = C × R × k_dir``; ``None`` for R and W when time was not measured."""
    k = parameters["complexity"]
    complexity = 1 + k["a"] * steps + k["b"] * external_calls + k["c"] * branches + k["d"] * retries
    if seconds is None:
        return {"C": complexity, "R": None, "W": None, "measured_time": False}
    v, norms = parameters["resources"], parameters["norms"]
    resources = (v["v_tok"] * tokens / norms["tokens"] + v["v_time"] * seconds / norms["seconds"]
                 + v["v_gas"] * gas / norms["gas"])
    return {"C": complexity, "R": resources, "W": complexity * resources * parameters["k_dir"], "measured_time": True}


def tier(parameters: Mapping[str, Any], *, replay_status: str, basis: bool, anchored_confirmed: bool,
         weight_value: float | None, verdict: str | None, in_pool: bool, trust_basis: bool) -> tuple[str, str]:
    """Significance tier and the first rule that assigned it (spec part 3 §8.1)."""
    if replay_status == "replay_diverged":
        return "high", "replay_diverged"
    if basis:
        return "high", "decision_basis"
    if anchored_confirmed:
        return "high", "anchored_confirmed"
    if weight_value is not None and weight_value >= parameters["w_high"]:
        return "high", "weight"
    if verdict in {"confirmed", "partial", "uncertain"} or in_pool or trust_basis:
        return "medium", "verdict_pool_or_trust"
    return "low", "default"


def retention_plan(parameters: Mapping[str, Any], *, qid: str, tier_name: str, window: int,
                   provisional: bool, replay_status: str) -> dict[str, Any] | None:
    """What raw data may later be removed, and only after a verified replay (И9)."""
    if provisional or tier_name == "high":
        return None
    if tier_name == "medium":
        return {"qid": qid, "tier": "medium", "action": "delete_raw_trace_after_replay",
                "not_before_window": window + parameters["n_medium_windows"],
                "requires": "replay_verified", "replay_status": replay_status}
    return {"qid": qid, "tier": "low", "action": "keep_tail_form",
            "not_before_window": window + parameters["n_low_windows"], "requires": "none",
            "replay_status": replay_status}
