"""Trials of two learned competitors in independent copies of one initial state (review R5 §4–5).

When the environment allows a safe experiment, the operator runs each
competitor alone — two exam runs in mode B on one snapshot, each naming its
trial habit (``--exam-trial``) — on a stand restored to the same initial state
before each arm; the arms share no mutable resource. ``synapse memory trial``
then records the trial; this module judges it from what was recorded, with the
court's own machinery and without calling any tool or model:

* the arms are exams of one snapshot, each running a different one of the two
  competitors, whose first fire reacted to the same situation (the same typed
  context of the failure); a later fire reacts to the arm's own failure;
* the arms started from one initial state as far as it was observed: every
  recorded answer before the habit acted is the same in both (the same tools,
  arguments and recorded results, in order);
* each arm's outcome is decided by the environment alone — the segment's
  anchored verdict and the body's own result; a segment the anchor cannot
  decide leaves the arm undecided (no advisor is asked), and a body the gateway
  refused something its contracts forbid has failed;
* the situation lies inside the transfer scope the operator declares for the
  stand: the context values the stand reproduces. A trial says nothing outside
  that scope.

The record names its basis (``stand_trial``) and the stand, so its conclusion
is never mistaken for an observation of the real environment;
``comparison.compare_trials`` carries it to the conflict ladder only for
triggers inside the scope.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .. import records
from ..configuration import MemoryConfiguration
from ..records import canonical
from ..session import opening_of
from ..tools.gateway import Gateway
from .cases import build_cases
from .comparison import TRIAL_BASIS
from .consolidation import session_window
from .reactions import build_reactions
from .signals import fire_signal
from .verdicts import judge_markers
from .window import durations, read_session

TRIAL_V1 = "synapse.memory.trial/v1"


class TrialViolation(ValueError):
    """The runs named are not two arms of one trial."""


def _scope(value: Any) -> dict[str, list]:
    if (type(value) is not dict or set(value) != {"fields"} or type(value["fields"]) is not dict
            or not value["fields"] or any(type(name) is not str or type(values) is not list or not values
                                          for name, values in value["fields"].items())):
        raise TrialViolation("a trial's transfer scope names the context fields and values its stand reproduces")
    return {"fields": {name: sorted(values, key=canonical) for name, values in sorted(value["fields"].items())}}


def within(scope: Mapping[str, Any], fields: Mapping[str, Any]) -> bool:
    """Whether a situation's typed context lies inside the declared transfer scope."""
    return all(name in fields and canonical(fields[name]) in {canonical(item) for item in values}
               for name, values in scope["fields"].items())


def arm_outcome(fire: Mapping[str, Any]) -> tuple[str, str]:
    """An arm's outcome and why, decided by the environment alone: a body the gateway refused something its
    contracts forbid has failed whatever its segment's verdict; otherwise the anchored verdict decides, and a
    segment the anchor cannot decide leaves the arm undecided."""
    if fire["refusals"]:
        return "failure", "contract_violation"
    if fire["status"] == "counted":
        return ("success", "anchored") if fire["signal"] > 0 else ("failure", "anchored")
    return "undecided", fire["why"]


def _arm(state, configuration, gateway, gateway_records, seconds, recorded) -> dict[str, Any]:
    history = recorded["history"]
    opening = opening_of(history) or {}
    if opening.get("exam") != "B" or not opening.get("trial"):
        raise TrialViolation("a trial arm is an exam in mode B that names its trial habit")
    habit_id, run_id = opening["trial"], recorded["run_id"]
    # The whole recorded arm is its window: an exam is never consolidated, so no cursor precedes it.
    session = session_window({"run_id": run_id, "source_hash": recorded["source_hash"]}, recorded,
                             {"cursors": {}}, ending=True)
    facts = read_session(session, configuration, gateway, gateway_records)
    if facts.problems or facts.evidence_problems:
        raise TrialViolation(f"trial arm {run_id} does not resolve from its record")
    # The environment decides: without an advisor a segment the anchor cannot decide stays uncertain.
    verdicts = judge_markers(None, configuration.parameters, facts, None, True)
    cases = build_cases(facts, verdicts, seconds, None)
    fires = [fire_signal(entry, event, configuration.parameters, "full")
             for kind, event, entry in build_reactions(facts, verdicts, None, cases, seconds, state["frozen"],
                                                       configuration)
             if kind == "habit_activated" and event.get("habit_id") == habit_id]
    if not fires:
        raise TrialViolation(f"trial arm {run_id} never ran its habit")
    fire = fires[0]  # The arm's reaction to the failure it was run for; later fires react to its own failures.
    outcome, why = arm_outcome(fire)
    habit_episode = next((scope for scope in facts.scopes.values()
                          if f"{fire['event_id']}|habit" in scope["episodes"]), None)
    first = min(habit_episode["episodes"][f"{fire['event_id']}|habit"]) if habit_episode else None
    # What the arm observed before its habit acted, in journal order: tool, arguments and recorded result.
    observed = [[item["tool"], records.digest(item["args"]), item["evidence_ref"]]
                for item in sorted((item for scope in facts.scopes.values() for item in scope["attempts"]
                                    if first is None or item["gw_seq"] < first), key=lambda item: item["gw_seq"])]
    return {"habit_id": habit_id, "run_id": run_id, "snapshot": opening.get("boundary"), "outcome": outcome,
            "why": why, "segment_verdict": fire["segment_verdict"], "refusals": fire["refusals"],
            "context": fire["context"], "observed": observed}


def judge_trial(state, configuration: MemoryConfiguration, gateway: Gateway, recorded_arms: Sequence[Mapping[str, Any]],
                *, stand: str, scope: Any) -> dict[str, Any]:
    """The trial record of two recorded arms; refused (raises) when they are not two arms of one trial."""
    if type(stand) is not str or not stand.strip():
        raise TrialViolation("a trial names the stand it ran on")
    scope = _scope(scope)
    if len(recorded_arms) != 2:
        raise TrialViolation("a trial has exactly two arms")
    gateway_records, seconds = gateway.records(), durations(gateway)
    arms = [_arm(state, configuration, gateway, gateway_records, seconds, item) for item in recorded_arms]
    left, right = sorted(arms, key=lambda item: item["habit_id"])
    if left["habit_id"] == right["habit_id"] or left["snapshot"] != right["snapshot"]:
        raise TrialViolation("the arms of a trial run two different habits on one snapshot")
    if canonical(left["context"]) != canonical(right["context"]):
        raise TrialViolation("the arms of a trial reacted to different situations")
    same_start = left["observed"] == right["observed"]
    fields = (left["context"] or {}).get("fields") or {}
    inside = within(scope, fields)
    decided = same_start and inside and all(item["outcome"] != "undecided" for item in (left, right))
    reason = ("arms_started_from_different_states" if not same_start else
              "situation_outside_transfer_scope" if not inside else
              "an_arm_undecided" if not decided else None)
    body = {"schema_version": TRIAL_V1, "basis": TRIAL_BASIS, "stand": stand, "scope": scope,
            # One trial is one copy of one initial state: the situation is the context and that observed start.
            "snapshot": left["snapshot"], "situation": records.digest({"context": left["context"],
                                                                       "start": left["observed"]}),
            "fields": fields,
            "arms": {item["habit_id"]: {key: item[key] for key in ("run_id", "outcome", "why", "segment_verdict",
                                                                  "refusals")} for item in (left, right)},
            "same_initial_state": same_start, "decided": decided, "reason": reason}
    return {"id": "trl_" + records.digest(body), **body}
