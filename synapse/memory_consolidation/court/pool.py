"""The candidate pool and the six birth criteria (stage 5).

Reaction episodes whose slow path completed accumulate in candidates keyed by
their event type and typed action sequence. An episode supports a candidate
only when its slow path succeeded, the failed operation was settled by the
journal and the segment did not fail; a failure is kept as an explicit
contradiction and an uncertain effect never counts as support. Copies of one
recorded evidence count once.

A verified contradiction is a contrast: it shows where the same procedure
does not work. The applicability learned from the positives and contrasts
excludes it, and it no longer contradicts the procedure inside its scope; a
contradiction the boundary cannot explain, or one never verified, still does
(refinement §12).

The criteria are all mandatory: repeatability, several tasks, completion
inside the learned scope, concreteness (a derivable binding whose result
references agree with the declared tool schemas), verifiability (a verified
re-execution or matching anchor evidence) and pairwise independent witnesses
in the declared provenance graph. A declared learning request adds its
frequency and stability thresholds. Each unmet criterion is a machine-readable
reason.
"""
from __future__ import annotations

import copy
from typing import Any

from ..learning.applicability import BoundaryUnavailable, generalize
from ..learning.behavior import BindingUnavailable, check_binding, derive_binding, event_fields_read
from ..learning.dependencies import check_types
from ..learning.provenance import independent_witnesses
from ..learning.triggers import covers
from ..records import digest


def support(reaction) -> str | None:
    """How one reaction episode bears on its candidate, or ``None`` when it is no material."""
    slow = reaction["slow"]
    if slow is None or not slow["completed"] or not slow["calls"]:
        return None
    if "environmental_failure" in reaction["segment_flags"]:
        return None
    if (slow["outcome"] == "success" and reaction["failed_settled"] is True
            and reaction["segment_verdict"] not in {"failed", "uncertain"}):
        return "success"
    if slow["outcome"] == "failure" or reaction["segment_verdict"] == "failed" or reaction["failed_settled"] is False:
        return "contradiction"
    return "uncertain"


def candidate_key(event_type: str, steps) -> str:
    return "sha256:" + digest({"event_type": event_type, "steps": [dict(item) for item in steps]})


def _episode(reaction, kind, window) -> dict[str, Any]:
    slow = reaction["slow"]
    return {"qid": reaction["qid"], "steps": reaction["steps_range"], "run_id": reaction["run_id"],
            "event_id": reaction["event_id"], "task": reaction["task_id"] or f"run:{reaction['run_id']}",
            "context": reaction["context"], "calls": slow["calls"], "origins": slow["origins"],
            "fields": reaction["context"]["fields"],
            "failed_args": reaction["failed"]["args"], "witnesses": slow["witnesses"], "evidence": slow["evidence"],
            "copy": slow["evidence_preexisting"], "support": kind, "window": window,
            "verified": reaction["replay"] == "replay_verified" or reaction["anchored_evidence"],
            "seconds": slow.get("seconds"), "tokens": slow.get("tokens", 0), "reaction": reaction["reaction"]}


def _candidate(pool, key, reaction, window) -> dict[str, Any] | None:
    entry = pool.setdefault(key, {"candidate_key": key, "event_type": reaction["context"]["event_type"],
                                  "steps": reaction["slow"]["steps"], "episodes": [], "first_seen": window,
                                  "last_seen": window, "status": "accumulating", "reasons": [], "born_habit": None})
    if entry["status"] == "born":
        return None
    if entry["status"] == "expired":
        entry.update(status="accumulating", reasons=[], first_seen=window)
    return entry


def merge_pool(state, draft, parameters, window) -> tuple[dict, list]:
    """The pool after this window's reaction episodes (pure; the evaluation foresees with it)."""
    pool = copy.deepcopy(state["pool"])
    touched = set()
    for reaction in sorted(draft["reactions"], key=lambda item: (item["run_id"], item["event_id"])):
        kind = support(reaction)
        if kind is None:
            continue
        key = candidate_key(reaction["context"]["event_type"], reaction["slow"]["steps"])
        entry = _candidate(pool, key, reaction, window)
        if entry is None or any(item["run_id"] == reaction["run_id"] and item["event_id"] == reaction["event_id"]
                                for item in entry["episodes"]):
            continue
        entry["episodes"].append(_episode(reaction, kind, window))
        entry["last_seen"] = window
        touched.add(key)
    return pool, sorted(touched)


def _request_for(requests, condition) -> dict | None:
    for request in sorted(requests, key=lambda item: item["habit_id"]):
        for area in request["area"]:
            probe = {key: area[key] for key in ("event_types", "context", "when", "not_when")}
            if covers(probe, condition):
                return request
    return None


def _threshold_holds(spec, value) -> bool:
    if spec is None:
        return True
    target = spec["value"]
    return {">": value > target, ">=": value >= target, "<": value < target, "<=": value <= target,
            "==": value == target}.get(spec["op"], False)


def _distinct(episodes) -> list[dict[str, Any]]:
    """Episodes with their own evidence; copies and repeats of one evidence count once."""
    distinct, seen = [], set()
    for item in episodes:
        identity = tuple(sorted(item["evidence"]))
        if not item["copy"] and identity not in seen:
            seen.add(identity)
            distinct.append(item)
    return distinct


def _dependency_types(binding, steps, configuration) -> list[str]:
    """Result references whose kinds the declared producer or consumer schema contradicts."""
    calls = [item for item in steps if item["step"] == "call"]
    problems = []
    for item in binding:
        for key, source in sorted((item.get("args") or {}).items()):
            if "result" in source:
                producer = configuration.tools.tools.get(calls[source["result"]["step"]]["tool"])
                consumer = configuration.tools.tools.get(calls[item["step"]]["tool"])
                problem = check_types(source["result"], producer, consumer, key)
                if problem is not None:
                    problems.append(f"call {item['step']} argument {key}: {problem}")
    return problems


def _binding(success, steps, configuration, reasons):
    if not success:
        return None
    try:
        binding = derive_binding([{"calls": item["calls"], "origins": item.get("origins"), "fields": item["fields"],
                                   "failed_args": item["failed_args"]} for item in success])
        check_binding(steps, binding)
    except BindingUnavailable as exc:
        reasons.append("binding_not_derivable")
        return {"unavailable": str(exc)}
    problems = _dependency_types(binding, steps, configuration)
    if problems:
        reasons.append("dependency_type_incompatible")
        return {"unavailable": "; ".join(problems)}
    return binding


def _applicability(success, contrasts, binding, reasons):
    """The learned scope, its explanation and the contradictions it leaves inside."""
    required = event_fields_read(binding) if binding is not None and "unavailable" not in binding else []
    try:
        return generalize(success, contrasts, required)
    except BoundaryUnavailable as exc:
        reasons.append("boundary_not_derivable")
        condition, explanation, unexplained = generalize(success, contrasts)
        return condition, {**explanation, "unavailable": str(exc)}, unexplained


def _completion(parameters, request, success, scope, contradictions, reasons) -> None:
    """Repeatability, several tasks and completion of every episode inside the learned scope."""
    tasks = {item["task"] for item in success}
    if len(success) < parameters["birth_episodes"]:
        reasons.append("repeatability_below_threshold")
    if len(tasks) < parameters["birth_tasks"]:
        reasons.append("single_task")
    if request is None and (contradictions or len(success) != len(scope)):
        reasons.append("not_all_episodes_succeeded")
    if request is not None:
        share = len(success) / len(scope) if scope else 0.0
        if not (_threshold_holds(request["frequency"], len(success))
                and _threshold_holds(request["stability"], share)):
            reasons.append("learning_request_thresholds_unmet")


def _independence(configuration, success, required) -> dict[str, Any]:
    """Independent witnesses of one claim; witnesses of different claims never make a pair."""
    by_claim: dict[str, set[str]] = {}
    for item in success:
        for witness in item["witnesses"]:
            by_claim.setdefault(witness["claim"]["tool"], set()).add(witness["source"])
    verdicts = [{**independent_witnesses(configuration.tools.provenance, sources, required), "claim": claim}
                for claim, sources in sorted(by_claim.items())]
    rank = {"independent": 2, "dependent": 1, "not_established": 0}
    if not verdicts:
        return {**independent_witnesses(configuration.tools.provenance, (), required), "claim": None}
    return max(verdicts, key=lambda item: (rank[item["verdict"]], len(item["independent_set"])))


def _address(item) -> tuple:
    return item["run_id"], item["event_id"]


def assess(candidate, parameters, configuration, requests) -> dict[str, Any]:
    """The six birth criteria of one candidate, each with its machine-readable result."""
    distinct = _distinct(candidate["episodes"])
    success = [item for item in distinct if item["support"] == "success"]
    contradictions = [item for item in distinct if item["support"] == "contradiction"]
    reasons: list[str] = []
    binding = _binding(success, candidate["steps"], configuration, reasons)
    condition, explanation, unexplained = None, None, []
    verified = [item for item in contradictions if item["verified"]]
    if success:
        condition, explanation, unexplained = _applicability(success, verified, binding, reasons)
    inside = {(ref["run_id"], ref["event_id"]) for ref in unexplained}
    contrasts = [item for item in verified if _address(item) not in inside]
    scope = [item for item in distinct if item not in contrasts]
    in_scope = [item for item in contradictions if item not in contrasts]
    request = _request_for(requests, condition) if condition is not None else None
    _completion(parameters, request, success, scope, in_scope, reasons)
    if any(not item["verified"] for item in success):
        reasons.append("episode_not_verified")
    independence = _independence(configuration, success, parameters["birth_sources"])
    if independence["verdict"] != "independent":
        reasons.append("sources_dependent" if independence["verdict"] == "dependent"
                       else "independence_not_established")
    return {"criteria": {"episodes": len(success), "distinct_episodes": len(distinct),
                         "copies": len(candidate["episodes"]) - len(distinct),
                         "tasks": len({item["task"] for item in success}), "contradictions": len(in_scope),
                         "contrasts": len(contrasts),
                         "all_success": not in_scope and len(success) == len(scope),
                         "all_verifiable": all(item["verified"] for item in success),
                         "concrete": binding is not None and "unavailable" not in binding,
                         "dependencies": _dependencies(binding),
                         "independence": independence["verdict"],
                         "request": None if request is None else request["habit_id"]},
            "independence": independence, "reasons": reasons, "condition": condition,
            "applicability": explanation, "binding": binding, "success": success}


def _dependencies(binding) -> int:
    """How many arguments of the binding read an earlier answer (D2)."""
    if binding is None or "unavailable" in binding:
        return 0
    return sum(1 for item in binding for source in (item.get("args") or {}).values() if "result" in source)
