"""The court's evaluation (``dream``): the draft the decision consumes.

Evaluation reads and derives; it changes no memory (spec part 2 §1.2). It
checks the inputs, re-executes each session (stage 1b, except in emergency
mode), judges markers (stage 1), derives cases, reaction episodes and fire
signals (stage 2), and asks the advice stages 4 and 5 will need. Every outcome
it uses is re-derived from the gateway journal and the durable history, never
read from a runtime label (И5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..configuration import MemoryConfiguration
from ..tools.gateway import Gateway
from .advice import arbitration, conflict_advice
from .cases import build_cases
from .hypotheses import hypothesis_events
from .counsel import Counsel
from .reactions import REACTIONS, build_reactions
from .signals import fire_signal
from .verdicts import judge_markers
from .window import check_integrity, durations, learning_requests, read_session


@dataclass
class DreamInputs:
    consolidation_id: str
    mode: str
    sessions: list[dict[str, Any]]
    state: dict[str, Any]
    configuration: MemoryConfiguration
    gateway: Gateway
    executor: str
    replay: Callable[[Mapping[str, Any]], dict[str, Any]]
    ending: frozenset[str]


def _empty_draft(inputs: DreamInputs, integrity) -> dict[str, Any]:
    return {"consolidation_id": inputs.consolidation_id, "mode": inputs.mode, "integrity": integrity,
            "evidence_problems": [], "replay": {}, "verdicts": [], "fires": [], "declared_fires": [],
            "reactions": [], "near_misses": [], "misses": [], "suppressed": [], "cases": [], "requests": [],
            "hypotheses": [],
            "stats": {"events": 0, "activated": 0, "near_miss": 0, "miss": 0, "slow_path": 0}}


def _replay(inputs: DreamInputs, session) -> dict[str, Any]:
    if inputs.mode == "emergency":
        return {"status": "not_run", "reason": "emergency mode"}
    return inputs.replay(session)


def _reactions_into(draft, reactions, parameters, mode) -> None:
    for kind, event, entry in reactions:
        draft["stats"][REACTIONS[kind]] += 1
        draft["reactions"].append(entry)
        if kind == "habit_activated":
            fire = fire_signal(entry, event, parameters, mode)
            draft["fires" if event.get("layer") == 2 else "declared_fires"].append(fire)
        else:
            draft["near_misses" if kind == "habit_near_miss" else "misses"].append(entry)


def _session_into(draft, inputs: DreamInputs, counsel: Counsel, session, gateway_records, seconds) -> None:
    configuration = inputs.configuration
    facts = read_session(session, configuration, inputs.gateway, gateway_records)
    if facts.problems:
        draft["integrity"]["ok"] = False
        draft["integrity"]["problems"].extend(facts.problems)
    draft["evidence_problems"].extend(facts.evidence_problems)
    replay = _replay(inputs, session)
    draft["replay"][facts.run] = replay
    draft["stats"]["events"] += session["to"] - session["from"]
    draft["stats"]["slow_path"] += len(facts.found.get("slow_path_used", []))
    draft["requests"].extend(learning_requests(facts))
    verdicts = judge_markers(counsel, configuration.parameters, facts, replay.get("status"),
                             facts.run in inputs.ending)
    draft["verdicts"].extend(verdicts.values())
    cases = build_cases(facts, verdicts, seconds, replay.get("status"))
    draft["cases"].extend(cases.values())
    draft["suppressed"].extend({"run_id": facts.run, "habit_id": event.get("habit_id"),
                                "reason": event.get("reason"), "trigger_event_id": event.get("trigger_event_id")}
                               for _, event in facts.found.get("habit_suppressed", []))
    draft["hypotheses"].extend(hypothesis_events(facts.found, facts.run))
    reactions = build_reactions(facts, verdicts, replay.get("status"), cases, seconds)
    _reactions_into(draft, reactions, configuration.parameters, inputs.mode)


def evaluate(inputs: DreamInputs) -> dict[str, Any]:
    """The draft the decision stage consumes; nothing is written."""
    gateway_records = inputs.gateway.records()
    integrity = check_integrity(inputs.sessions, inputs.state, inputs.gateway, gateway_records, inputs.executor)
    counsel = Counsel(inputs.gateway, inputs.configuration, inputs.consolidation_id)
    seconds = durations(inputs.gateway)
    draft = _empty_draft(inputs, integrity)
    for session in inputs.sessions:
        _session_into(draft, inputs, counsel, session, gateway_records, seconds)
    parameters = inputs.configuration.parameters
    draft["conflict_advice"] = conflict_advice(counsel, inputs.state, parameters, draft["fires"], draft["reactions"])
    draft["arbitration"] = ({} if inputs.mode == "emergency"
                            else arbitration(counsel, inputs.state, inputs.configuration, draft))
    draft["counsel"] = {"questions": counsel.questions, "agreed": counsel.agreed}
    draft["gateway_head"] = gateway_records[-1]["hash"] if gateway_records else None
    return draft
