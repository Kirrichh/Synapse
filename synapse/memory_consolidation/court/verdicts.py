"""Stage 1 of the court: a verdict for every task marker of a window.

Stage 1 decides from the environment, the requirement fixed at formation and
the anchor evidence; stage 1b lowers an anchored confirmation to uncertain when
the verified re-execution of its session diverged; stage 2 compares the intent
with the decoded case by the configured scorer; stage 3 asks the bounded
double advice. An anchored marker is never confirmed without matching
evidence, and without evidence it reaches at most ``partial``.
"""
from __future__ import annotations

from typing import Any, Mapping

from .. import records
from ..records import canonical
from ..tools.episodes import environmental_failure, requirement_outcome
from .counsel import Counsel
from .window import BOUND_KINDS, SessionFacts

VERDICTS = ("confirmed", "partial", "failed")


def decoded_form(scope: Mapping[str, Any]) -> str:
    """The decoded form of a case: its typed steps, without free text."""
    parts = []
    for attempt in scope["attempts"]:
        if attempt["role"] == "action":
            result = "ok" if attempt["op_result"] == "ok" else (attempt["op_err"] or attempt["op_result"])
            parts.append(f"{attempt['tool']} -> {result}")
    return "; ".join(parts) + f"; outcome {scope['outcome']}"


def anchor_evidence(marker, scopes) -> list[str]:
    """Recorded results of the anchor tool that carry every anchored field."""
    anchor = marker["external_anchor"]
    found = []
    for scope in scopes:
        for attempt in scope["attempts"]:
            payload = scope["payloads"].get(attempt["gw_seq"])
            if (attempt["tool"] == anchor["tool"] and attempt["op_result"] == "ok" and attempt["role"] == "action"
                    and isinstance(payload, dict)
                    and all(key in payload and canonical(payload[key]) == canonical(value)
                            for key, value in anchor["fields"].items())):
                found.append(attempt["evidence_ref"])
    return found


def _stage_one(base, parameters, marker, scopes, replay):
    """Environment, requirement and anchor; ``None`` passes on to stages 2 and 3."""
    environment = environmental_failure(scopes, parameters["environmental_run"])
    if environment is not None:
        return {**base, "verdict": "failed", "stage": "1", "flags": ["environmental_failure"],
                "criterion": environment["criterion"], "evidence": [str(seq) for seq in environment["gw_seqs"]]}
    if scopes:
        requirement = requirement_outcome(scopes[-1], marker)
        if requirement["fulfilled"] is False:
            return {**base, "verdict": "failed", "stage": "1", "flags": ["requirement_unfulfilled"],
                    "criterion": requirement["basis"]}
        if requirement["fulfilled"] is None:
            return {**base, "verdict": "uncertain", "stage": "1", "flags": ["uncertainty_retained"],
                    "criterion": requirement["basis"]}
    if marker["external_anchor"] is None:
        return None
    evidence = anchor_evidence(marker, scopes)
    if evidence:
        if replay == "replay_diverged":
            return {**base, "verdict": "uncertain", "stage": "1b", "flags": ["replay_diverged"],
                    "evidence": evidence, "replay": replay}
        return {**base, "verdict": "confirmed", "stage": "1", "evidence": evidence, "replay": replay}
    if scopes:
        return {**base, "verdict": "failed", "stage": "1", "flags": ["evidence_mismatch"]}
    return None


def _later_stages(base, counsel: Counsel, parameters, marker, plan, scopes):
    """Stage 2 similarity, then stage 3 bounded double advice."""
    ceiling = "partial" if marker["external_anchor"] is not None else None
    decoded = "; ".join(decoded_form(scope) for scope in scopes) or "no recorded action"
    similarity = counsel.similarity(marker["intent"], decoded)
    base = {**base, "similarity": similarity}
    if similarity is not None and similarity >= parameters["verify_confirm_similarity"]:
        return {**base, "verdict": ceiling or "confirmed", "stage": "2"}
    if similarity is not None and similarity >= parameters["verify_partial_similarity"]:
        return {**base, "verdict": "partial", "stage": "2"}
    adjacent = [item["intent"] for item in plan["markers"] if item["id"] != marker["id"]]
    advice = counsel.ask("segment_role_fulfilled", VERDICTS, (
        {"intent": marker["intent"], "plan": adjacent, "decoded": decoded, "similarity": similarity},
        {"decoded": decoded, "similarity": similarity, "plan": list(reversed(adjacent)), "intent": marker["intent"]}))
    base["advice"] = advice
    if not advice["agreed"]:
        return {**base, "verdict": "uncertain", "stage": "3",
                "flags": ["advice_disagreed" if advice["asked"] else "no_advisor"]}
    answer = "partial" if ceiling == "partial" and advice["answer"] == "confirmed" else advice["answer"]
    return {**base, "verdict": answer, "stage": "3"}


def _verdict(counsel, parameters, marker, plan, scopes, events, run, replay, later_executed, ending):
    base = {"marker_id": marker["id"], "run_id": run, "op_scopes": sorted(scope["op_scope"] for scope in scopes),
            "flags": [], "evidence": [], "similarity": None, "advice": None, "criterion": None, "replay": None}
    if not events and not scopes:
        if not ending:
            return None
        return {**base, "verdict": "failed" if later_executed else "skipped", "stage": "1",
                "criterion": "session moved past the segment" if later_executed else "segment not reached"}
    return (_stage_one(base, parameters, marker, scopes, replay)
            or _later_stages(base, counsel, parameters, marker, plan, scopes))


def _executed_positions(facts: SessionFacts, by_marker, events_by_marker) -> dict[str, set[int]]:
    executed: dict[str, set[int]] = {}
    for marker_id in set(by_marker) | set(events_by_marker):
        if marker_id in facts.markers:
            marker, plan = facts.markers[marker_id]
            executed.setdefault(plan["task_id"], set()).add([item["id"] for item in plan["markers"]].index(marker_id))
    return executed


def judge_markers(counsel: Counsel, parameters, facts: SessionFacts, replay: str | None,
                  ending: bool) -> dict[str, dict[str, Any]]:
    """Verdict records of one session's markers, by marker id."""
    by_marker: dict[str, list] = {}
    for scope in facts.scopes.values():
        if scope["segment"] is not None:
            by_marker.setdefault(scope["segment"], []).append(scope)
    events_by_marker: dict[str, list] = {}
    for kind in BOUND_KINDS:
        for _, event in facts.found.get(kind, []):
            if event.get("segment_marker_id"):
                events_by_marker.setdefault(event["segment_marker_id"], []).append(event)
    executed = _executed_positions(facts, by_marker, events_by_marker)
    verdicts: dict[str, dict[str, Any]] = {}
    for marker_id, (marker, plan) in sorted(facts.markers.items()):
        position = [item["id"] for item in plan["markers"]].index(marker_id)
        later = any(index > position for index in executed.get(plan["task_id"], ()))
        scopes = sorted(by_marker.get(marker_id, []), key=lambda item: item["gw_refs"][0])
        verdict = _verdict(counsel, parameters, marker, plan, scopes, events_by_marker.get(marker_id, []),
                           facts.run, replay, later, ending)
        if verdict is not None:
            verdicts[marker_id] = records.make("marker_verdict", **verdict)
    return verdicts
