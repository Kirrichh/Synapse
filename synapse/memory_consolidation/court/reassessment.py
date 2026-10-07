"""Reassessment: recorded memory judged again under the configuration and policy now declared.

A memory learned under earlier tool contracts or an earlier court policy is not
trusted on its word. Before an owner adopts a new configuration, the court
re-judges what its live learned habits stand on, from recorded material only —
the durable histories, the gateway journal and its evidence — and never calls a
tool or a model:

* every basis episode of a live learned habit is judged again: its segment's
  first stage (environment, requirement, anchor) is re-derived from the
  recorded operations under the current contracts; a segment the first stage
  cannot decide keeps its recorded later-stage verdict, which no contract
  change touches;
* every recorded answer the episode's recovery repeated is interpreted again
  under the current contract: a repeat the gateway would now refuse before
  any effect (an effect no longer known to be none) no longer supports the
  habit;
* an episode whose recorded material no longer resolves supports nothing — a
  missing trace is never read as a verified one.

Decisions an earlier rule recorded are judged again from the record too: every
hypothesis status of an earlier check rule (its recorded check decided under the
current rule), every promotion an earlier policy decided (on the confirmed
experience of the habit's recorded fires). Every competitor resolution the
record still holds is given the basis it was found on — compared outcomes or
stand trials — so the conflict ladder keeps it only while that basis holds under
the current rules, as in any window.

A habit keeps its basis when at least as many basis episodes are still
verified as a birth requires (or all of them, for a smaller basis); otherwise
it is archived by the decision, for a changed basis. A kept habit records the
tool contracts it is now verified under, so a session loads it again; the
report names the contracts that changed since it was last verified. A habit that keeps its
basis under a new tool binding is published to Gold again with the evidence the
reassessment verified: Gold admits behavior only under the binding it was
published with, and a habit Gold does not admit again is archived too. The
result is published as the reassessment section of the report: every episode's
recorded and current standing, with the reason, and every republication.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..configuration import MemoryConfiguration
from ..learning.behavior import contracts_of
from ..tools.gateway import Gateway
from ..tools.semantics import interpret, repeat_admissible
from .automaton import reverify_promotions
from .conflicts import resolution_bases
from .habit_state import EFFECTIVE
from .hypotheses import reassess_hypotheses
from .verdicts import scopes_by_marker, stage_one
from .window import read_session

#: v2 (second review): the reassessment also decides recorded hypothesis statuses again and judges recorded
#: promotions on confirmed experience.
REASSESSMENT_V2 = "synapse.memory.reassessment/v2"
_UNAVAILABLE = {"forgotten", "rolled_up"}


def _recorded_verdicts(reports: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    verdicts: dict[tuple[str, str], dict[str, Any]] = {}
    for report in reports:
        for verdict in report.get("marker_verdicts", []):
            verdicts[(verdict["run_id"], verdict["marker_id"])] = verdict
    return verdicts


def _repeats(scopes, configuration: MemoryConfiguration) -> str | None:
    """The first repeat of a refused operation that its current contract no longer admits."""
    for scope in scopes:
        for attempt in scope["attempts"]:
            if attempt["retry_of"] is None or attempt["role"] != "action":
                continue
            refused = next((item for item in scope["attempts"] if item["op"] == attempt["retry_of"]
                            and item["attempt"] == 1 and item["op_result"] != "ok"), None)
            contract = configuration.tools.tools.get(attempt["tool"])
            if refused is None or contract is None:
                continue
            effect = interpret(contract, refused["transport"], scope["payloads"].get(refused["gw_seq"]))["effect"]
            # A repeat that carried the refused operation's own idempotency key was deduplicated by the provider.
            keyed = (contract.idempotency_key is not None and attempt["idempotency_key"] is not None
                     and attempt["idempotency_key"] == refused["idempotency_key"]) or None
            if not repeat_admissible(effect, contract, keyed)[0]:
                return f"repeat of {attempt['tool']} op {attempt['retry_of']} after effect {effect}"
    return None


class _Sessions:
    """Recorded sessions, read once each over their whole history."""

    def __init__(self, entries, read, configuration, gateway, gateway_records) -> None:
        self._entries = {entry["run_id"]: entry for entry in entries}
        self._read, self._configuration = read, configuration
        self._gateway, self._records = gateway, gateway_records
        self._facts: dict[str, Any] = {}

    def facts(self, run_id: str):
        if run_id not in self._facts:
            entry = self._entries.get(run_id)
            facts = None
            if entry is not None:
                try:
                    recorded = self._read(entry)
                except (OSError, ValueError):
                    recorded = None
                if recorded is not None:
                    history = recorded["history"]
                    facts = read_session({"run_id": run_id, "history": history, "from": 0, "to": len(history)},
                                         self._configuration, self._gateway, self._records)
            self._facts[run_id] = facts
        return self._facts[run_id]


def _episode(qid, state, sessions: _Sessions, recorded, configuration) -> dict[str, Any]:
    quantum = state["quanta"].get(qid)
    base = {"qid": qid, "run_id": None, "marker_id": None, "recorded": None, "now": "unavailable", "reason": None}
    if quantum is None or quantum["retention_state"] in _UNAVAILABLE:
        return {**base, "reason": "trace_unavailable"}
    run_id, marker_id = quantum["replay_ref"]["run_id"], quantum["syn_form"]["goal_ref"]
    verdict = recorded.get((run_id, marker_id))
    base = {**base, "run_id": run_id, "marker_id": marker_id,
            "recorded": None if verdict is None else {"verdict": verdict["verdict"], "stage": verdict["stage"]}}
    facts = sessions.facts(run_id)
    if facts is None or facts.problems or facts.evidence_problems or marker_id not in facts.markers:
        return {**base, "reason": "trace_unavailable"}
    marker, _ = facts.markers[marker_id]
    scopes = scopes_by_marker(facts).get(marker_id, [])
    replay = None if verdict is None else verdict.get("replay")
    judged = stage_one({"marker_id": marker_id, "run_id": run_id}, configuration.parameters, marker, scopes, replay)
    evidence: list[str] = []
    if judged is None:
        # The first stage cannot decide: the recorded later-stage verdict stands as it was recorded.
        confirmed = verdict is not None and verdict["stage"] in {"2", "3"} and verdict["verdict"] == "confirmed"
        now, reason = ("verified", "recorded_later_stage") if confirmed else ("not_verified", "no_recorded_verdict")
        evidence = list(verdict["evidence"]) if confirmed else []
    elif judged["verdict"] == "confirmed":
        now, reason, evidence = "verified", "anchored", list(judged["evidence"])
    else:
        now, reason = "not_verified", judged.get("criterion") or ",".join(judged.get("flags", [])) or judged["verdict"]
    refused = _repeats(scopes, configuration)
    if now == "verified" and refused is not None:
        now, reason, evidence = "not_verified", f"repeat_no_longer_admissible: {refused}", []
    return {**base, "now": now, "reason": reason, "evidence": evidence}


def reassess_bases(state, configuration: MemoryConfiguration, gateway: Gateway, gateway_records, entries,
                   read, reports) -> dict[str, Any]:
    """The reassessment of every live learned habit's basis; nothing is written."""
    sessions = _Sessions(entries, read, configuration, gateway, gateway_records)
    recorded = _recorded_verdicts(reports)
    habits = []
    for habit_id, metadata in sorted(state["habits"].items()):
        if metadata["state"] not in EFFECTIVE:
            continue
        qids = state["frozen"][habit_id]["habit"]["born_from"]["episodes"]
        episodes = [_episode(qid, state, sessions, recorded, configuration) for qid in sorted(qids)]
        verified = sum(1 for item in episodes if item["now"] == "verified")
        required = min(configuration.parameters["birth_episodes"], len(episodes))
        steps = state["frozen"][habit_id]["habit"]["action_pattern"]
        recorded_contracts = (metadata.get("verified_under") or {}).get("contracts")
        current = contracts_of(steps, configuration)
        habits.append({"habit_id": habit_id, "state": metadata["state"], "verified": verified,
                       "required": required, "episodes": episodes,
                       # Which contracts it was verified under changed: the reason its applicability is re-judged.
                       "contracts_changed": sorted(current) if recorded_contracts is None else sorted(
                           name for name in current if recorded_contracts.get(name) != current[name]),
                       "contracts": current,
                       "verdict": "basis_holds" if episodes and verified >= required else "basis_no_longer_verified"})
    # Recorded decisions of earlier rules, judged again from what was recorded (second review, F1–F4).
    return {"schema_version": REASSESSMENT_V2, "habits": habits,
            "hypotheses": reassess_hypotheses(state, configuration, gateway, gateway_records),
            "promotions": reverify_promotions(state, reports, configuration.parameters, configuration.policy["policy"]),
            "resolutions": resolution_bases(reports)}


def republication(state, reports, item) -> dict[str, Any]:
    """The claim a kept habit is published again with: its frozen records, its birth's recorded criteria and
    the evidence this reassessment verified."""
    habit_id = item["habit_id"]
    born = next((birth for report in reports for birth in report.get("births", []) if birth["habit_id"] == habit_id),
                {})
    frozen = state["frozen"][habit_id]
    return {"habit": frozen["habit"], "trigger": frozen["trigger"], "criteria": born.get("criteria"),
            "independence": born.get("independence"), "boundary": None,
            "evidence": sorted({ref for episode in item["episodes"] for ref in episode["evidence"]})}


def lost_bases(reassessment: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """The forced archival of every habit whose basis is no longer verified or that Gold no longer admits."""
    lost = {}
    for item in reassessment["habits"]:
        if item["verdict"] == "basis_no_longer_verified":
            lost[item["habit_id"]] = ("TR", f"{item['verified']} of {item['required']} basis episodes still verified")
        elif item["verdict"] == "republication_refused":
            lost[item["habit_id"]] = ("TR", f"Gold did not admit it under the new configuration: "
                                            f"{item['republication']['reason']}")
    return lost
