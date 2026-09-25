"""The court's evaluation (``dream``): facts, verdicts, signals and advice.

Evaluation reads and derives; it changes no memory (spec part 2 §1.2). Every
outcome it uses is re-derived from the gateway journal and the durable
history, never read from a runtime label (И5):

* input integrity — history chains, gateway and side-log chains, resolvable
  evidence, the executor of every episode, and equality of every outcome the
  history recorded with the journal's own outcome;
* stage 1 — a verdict per task marker: the environment, the requirement fixed
  at formation, the anchor evidence, then similarity (stage 2) and bounded
  double advice (stage 3); stage 1b — a verified durable re-execution of each
  session with its recorded results;
* stage 2 — the signal of every habit fire, or its explicit exclusion or
  conservation;
* advice for stage 4 (recorded counterfactual) and material for stage 5
  (reaction episodes, arbitration), and the cases substage 7a quantizes.

Advice and similarity are ``reason`` calls through the gateway under the
consolidation's own run identity, so a repeated evaluation of the same inputs
consumes the recorded answers instead of asking again. An answer outside the
bounded vocabulary counts as disagreement; disagreement is uncertainty (И7).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from synapse.hardening import hash_event_chain

from . import records
from .behavior import step_similarity, typed_steps
from .configuration import MemoryConfiguration
from .episodes import (
    EvidenceUnavailable,
    derive_scope,
    environmental_failure,
    group_scopes,
    local_outcome,
    requirement_outcome,
)
from .formation import TaskContractViolation, verify_plan
from .gateway import Gateway, GatewayIntegrityError
from .operations import canonical, corroborating
from .policy import signal as signal_of
from .quanta import case_quantum, trace_ref
from .triggers import context_template, matches, render_template, typed_context

VERDICTS = ("confirmed", "partial", "failed")
_REACTIONS = {"habit_activated": "activated", "habit_near_miss": "near_miss", "habit_miss": "miss"}


class CourtRefusal(RuntimeError):
    """The court cannot run on these inputs at all (spec part 2 §3.2)."""


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


class Counsel:
    """Bounded questions to the configured advisor and scorer, recorded by the gateway."""

    def __init__(self, inputs: DreamInputs) -> None:
        self.inputs = inputs
        self.ordinal = 0
        self.questions = 0
        self.agreed = 0

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ordinal += 1
        scope = f"court:{self.ordinal}"
        outcome = self.inputs.gateway.invoke({
            "run_id": f"court:{self.inputs.consolidation_id}", "ordinal": f"reason:{self.ordinal}",
            "episode": "court", "op_scope": scope, "path": "court", "tool": tool, "args": arguments,
            "retry_of": None, "task_id": None, "segment_marker_id": None, "habit_id": None})
        return outcome

    def similarity(self, left: str, right: str) -> float | None:
        scorer = self.inputs.configuration.scorer
        if scorer is None:
            return None
        view = self._call(scorer.tool, {"a": left, "b": right})["view"]
        value = view["payload"].get("similarity") if view["ok"] and isinstance(view["payload"], dict) else None
        if type(value) not in {int, float} or isinstance(value, bool) or not 0.0 <= value <= 1.0:
            return None
        return float(value)

    def ask(self, question: str, answers: tuple[str, ...], variants: tuple[dict, dict]) -> dict[str, Any]:
        """Two differently phrased and ordered calls; agreement or explicit disagreement."""
        advisor = self.inputs.configuration.advisor
        if advisor is None:
            return {"asked": False, "calls": [], "answer": None, "agreed": False, "reasons": [], "refs": []}
        calls, reasons, refs = [], [], []
        for index, variant in enumerate(variants):
            outcome = self._call(advisor.tool, {"question": question, "variant": index,
                                                "answers": list(answers), "input": variant})
            view = outcome["view"]
            payload = view["payload"] if view["ok"] and isinstance(view["payload"], dict) else {}
            answer = payload.get("answer")
            calls.append(answer if answer in answers else None)
            reason = payload.get("reason")
            reasons.append(reason[:512] if isinstance(reason, str) else None)
            refs.append(outcome["ref"])
        self.questions += 1
        agreed = calls[0] is not None and calls[0] == calls[1]
        self.agreed += int(agreed)
        return {"asked": True, "calls": calls, "answer": calls[0] if agreed else None, "agreed": agreed,
                "reasons": reasons, "refs": refs, "component": advisor.to_dict()}


def _index(history: list[Mapping[str, Any]], start: int, end: int) -> dict[str, list]:
    found: dict[str, list] = {}
    for position in range(start, end):
        event = history[position]
        if isinstance(event, Mapping):
            found.setdefault(event.get("type"), []).append((position, event))
    return found


def _plans(session: Mapping[str, Any], configuration: MemoryConfiguration) -> dict[str, dict[str, Any]]:
    """Every plan the session fixed, re-derived from its recorded contract."""
    plans: dict[str, dict[str, Any]] = {}
    for event in session["history"][:session["to"]]:
        if isinstance(event, Mapping) and event.get("type") == "task_plan_declared":
            plan = verify_plan(event["plan"], event["contract"], configuration)
            plans[plan["task_id"]] = plan
    return plans


def _integrity(inputs: DreamInputs, gateway_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Input checks; a failure of an existing source moves the court to emergency mode."""
    problems: list[str] = []
    for session in inputs.sessions:
        chain = hash_event_chain(session["history"][:session["to"]])
        cursor = inputs.state["cursors"].get(session["run_id"])
        if cursor is not None and cursor["to"] > 0:
            if cursor["to"] > session["to"] or chain[cursor["to"] - 1]["hash"] != cursor["head"]:
                problems.append(f"history of {session['run_id']} differs from its consolidated prefix")
        if session.get("integrity_error"):
            problems.append(f"history of {session['run_id']}: {session['integrity_error']}")
    for record in gateway_records:
        body = record["body"]
        if record["kind"] == "STARTED" and body["executor"] != inputs.executor:
            raise CourtRefusal("an episode was executed by another executor than this configuration")
    try:
        inputs.gateway.side_records()
    except GatewayIntegrityError as exc:
        raise CourtRefusal("the side time log lost its chain") from exc
    return {"ok": not problems, "problems": problems}


def _scope_records(gateway_records, run_ids, limit_seq):
    grouped = group_scopes(item for item in gateway_records
                           if item["body"].get("run_id") in run_ids and item["seq"] <= limit_seq)
    return grouped


def _decoded(scope: Mapping[str, Any]) -> str:
    """The decoded form of a case for stage 2: its typed steps, without free text."""
    parts = []
    for attempt in scope["attempts"]:
        if attempt["role"] != "action":
            continue
        result = "ok" if attempt["op_result"] == "ok" else (attempt["op_err"] or attempt["op_result"])
        parts.append(f"{attempt['tool']} -> {result}")
    return "; ".join(parts) + f"; outcome {scope['outcome']}"


def _anchor_evidence(marker, scopes) -> list[str]:
    anchor = marker["external_anchor"]
    found = []
    for scope in scopes:
        for attempt in scope["attempts"]:
            payload = scope["payloads"].get(attempt["gw_seq"])
            if (attempt["tool"] == anchor["tool"] and attempt["op_result"] == "ok" and attempt["role"] == "action"
                    and isinstance(payload, dict)
                    and all(canonical(payload.get(key)) == canonical(value) and key in payload
                            for key, value in anchor["fields"].items())):
                found.append(attempt["evidence_ref"])
    return found


def _verdict(counsel: Counsel, parameters, marker, plan, scopes, events, run, replay, later_executed, ending):
    """Stage 1 (with 1b), stage 2 and stage 3 for one marker in one run."""
    base = {"marker_id": marker["id"], "run_id": run, "op_scopes": sorted(scope["op_scope"] for scope in scopes),
            "flags": [], "evidence": [], "similarity": None, "advice": None, "criterion": None, "replay": None}
    anchored = marker["external_anchor"] is not None
    if not events and not scopes:
        if not ending:
            return None
        verdict = "failed" if later_executed else "skipped"
        return {**base, "verdict": verdict, "stage": "1",
                "criterion": "session moved past the segment" if later_executed else "segment not reached"}
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
    ceiling = None
    if anchored:
        evidence = _anchor_evidence(marker, scopes)
        if evidence:
            verdict = {**base, "verdict": "confirmed", "stage": "1", "evidence": evidence, "replay": replay}
            if replay == "replay_diverged":
                return {**verdict, "verdict": "uncertain", "stage": "1b", "flags": ["replay_diverged"]}
            return verdict
        if scopes:
            return {**base, "verdict": "failed", "stage": "1", "flags": ["evidence_mismatch"]}
        ceiling = "partial"
    decoded = "; ".join(_decoded(scope) for scope in scopes) or "no recorded action"
    similarity = counsel.similarity(marker["intent"], decoded)
    base["similarity"] = similarity
    if similarity is not None:
        if similarity >= parameters["verify_confirm_similarity"]:
            return {**base, "verdict": ceiling or "confirmed", "stage": "2"}
        if similarity >= parameters["verify_partial_similarity"]:
            return {**base, "verdict": "partial", "stage": "2"}
    adjacent = [item["intent"] for item in plan["markers"] if item["id"] != marker["id"]]
    advice = counsel.ask("segment_role_fulfilled", VERDICTS, (
        {"intent": marker["intent"], "plan": adjacent, "decoded": decoded, "similarity": similarity},
        {"decoded": decoded, "similarity": similarity, "plan": list(reversed(adjacent)), "intent": marker["intent"]}))
    base["advice"] = advice
    if not advice["agreed"]:
        return {**base, "verdict": "uncertain", "stage": "3", "flags": ["advice_disagreed" if advice["asked"]
                                                                        else "no_advisor"]}
    answer = advice["answer"]
    if ceiling == "partial" and answer == "confirmed":
        answer = "partial"
    return {**base, "verdict": answer, "stage": "3"}


def evaluate(inputs: DreamInputs) -> dict[str, Any]:
    """The draft the decision stage consumes; nothing is written."""
    configuration, parameters = inputs.configuration, inputs.configuration.parameters
    gateway_records = inputs.gateway.records()
    integrity = _integrity(inputs, gateway_records)
    counsel = Counsel(inputs)
    replay: dict[str, dict[str, Any]] = {}
    verdicts, fires, reactions, near_misses, misses, suppressed, cases, requests = [], [], [], [], [], [], [], []
    declared_fires: list[dict[str, Any]] = []
    evidence_problems: list[str] = []
    stats = {"events": 0, "activated": 0, "near_miss": 0, "miss": 0, "slow_path": 0}
    for session in inputs.sessions:
        run = session["run_id"]
        history, start, end = session["history"], session["from"], session["to"]
        stats["events"] += end - start
        try:
            plans = _plans(session, configuration)
        except TaskContractViolation as exc:
            integrity["ok"] = False
            integrity["problems"].append(f"{run}: {exc}")
            plans = {}
        markers = {marker["id"]: (marker, plan) for plan in plans.values() for marker in plan["markers"]}
        if inputs.mode == "emergency":
            replay[run] = {"status": "not_run", "reason": "emergency mode"}
        else:
            replay[run] = inputs.replay(session)
        found = _index(history, start, end)
        for _, event in found.get("habit_learning_requested", []):
            requests.append({"run_id": run, "habit_id": event["habit_id"], "area": event["area"],
                             "frequency": event["frequency"], "stability": event["stability"]})
        referenced = []
        for position, event in found.get("external_action", []):
            seq = event["outcome"]["ref"]["gw_seq"]
            try:
                recorded = inputs.gateway.recorded_outcome(seq, gateway_records)
            except (GatewayIntegrityError, EvidenceUnavailable, PermissionError) as exc:
                integrity["ok"] = False
                integrity["problems"].append(f"{run}@{position}: {exc}")
                continue
            if canonical(recorded) != canonical(event["outcome"]):
                integrity["ok"] = False
                integrity["problems"].append(f"{run}@{position}: history outcome differs from the gateway")
                continue
            referenced.append(seq)
        limit = max(referenced, default=-1)
        scopes: dict[str, dict[str, Any]] = {}
        for (run_id, scope_name), items in _scope_records(gateway_records, {run}, limit).items():
            if not any(item["seq"] in referenced for item in items):
                continue
            try:
                facts = derive_scope(items, configuration.tools, inputs.gateway.evidence)
            except EvidenceUnavailable as exc:
                evidence_problems.append(f"{run}/{scope_name}: {exc}")
                continue
            scopes[scope_name] = facts
        seconds = _durations(inputs.gateway)
        by_marker: dict[str, list] = {}
        for facts in scopes.values():
            if facts["segment"] is not None:
                by_marker.setdefault(facts["segment"], []).append(facts)
        events_by_marker: dict[str, list] = {}
        for kind in ("external_error", "habit_activated", "habit_near_miss", "habit_miss", "slow_path_used",
                     "habit_suppressed"):
            for _, event in found.get(kind, []):
                if event.get("segment_marker_id"):
                    events_by_marker.setdefault(event["segment_marker_id"], []).append(event)
        executed_order = {}
        for marker_id in set(by_marker) | set(events_by_marker):
            if marker_id in markers:
                marker, plan = markers[marker_id]
                executed_order.setdefault(plan["task_id"], set()).add(
                    [item["id"] for item in plan["markers"]].index(marker_id))
        ending = run in inputs.ending
        run_verdicts: dict[str, dict[str, Any]] = {}
        for marker_id, (marker, plan) in sorted(markers.items()):
            position = [item["id"] for item in plan["markers"]].index(marker_id)
            later = any(index > position for index in executed_order.get(plan["task_id"], ()))
            scope_list = sorted(by_marker.get(marker_id, []), key=lambda item: item["gw_refs"][0])
            verdict = _verdict(counsel, parameters, marker, plan, scope_list, events_by_marker.get(marker_id, []),
                               run, replay[run].get("status"), later, ending)
            if verdict is None:
                continue
            record = records.make("marker_verdict", **verdict)
            run_verdicts[marker_id] = record
            verdicts.append(record)
        case_by_scope = {}
        for scope_name, facts in sorted(scopes.items()):
            case = _case(session, facts, scope_name, found, markers, run_verdicts, seconds, replay[run])
            cases.append(case)
            case_by_scope[scope_name] = case
        errors = {event["event_id"]: (position, event) for position, event in found.get("external_error", [])}
        slow = {event["trigger_event_id"]: event for _, event in found.get("slow_path_used", [])}
        stats["slow_path"] += len(slow)
        for _, event in found.get("habit_suppressed", []):
            suppressed.append({"run_id": run, "habit_id": event.get("habit_id"), "reason": event.get("reason"),
                               "trigger_event_id": event.get("trigger_event_id")})
        for kind in ("habit_activated", "habit_near_miss", "habit_miss"):
            for _, event in found.get(kind, []):
                stats[_REACTIONS[kind]] += 1
                error = errors.get(event.get("trigger_event_id"))
                if error is None:
                    continue
                reaction = _reaction(kind, event, error[1], scopes, slow, run, run_verdicts, replay[run],
                                     case_by_scope, seconds)
                reactions.append(reaction)
                if kind == "habit_activated":
                    entry = _fire(reaction, event, parameters, run_verdicts, inputs.mode)
                    (fires if event.get("layer") == 2 else declared_fires).append(entry)
                elif kind == "habit_near_miss":
                    near_misses.append(reaction)
                else:
                    misses.append(reaction)
    advice_conflicts = _conflict_advice(counsel, inputs, fires, reactions)
    partial = {"consolidation_id": inputs.consolidation_id, "reactions": reactions, "requests": requests,
               "replay": replay}
    arbitration = {} if inputs.mode == "emergency" else _arbitration(counsel, inputs, partial)
    return {"consolidation_id": inputs.consolidation_id, "mode": inputs.mode, "integrity": integrity,
            "evidence_problems": evidence_problems, "replay": replay, "verdicts": verdicts, "fires": fires,
            "declared_fires": declared_fires, "reactions": reactions, "near_misses": near_misses, "misses": misses,
            "suppressed": suppressed, "cases": cases, "requests": requests, "conflict_advice": advice_conflicts,
            "arbitration": arbitration,
            "counsel": counsel, "stats": stats,
            "gateway_head": gateway_records[-1]["hash"] if gateway_records else None}


def _durations(gateway: Gateway) -> dict[int, float]:
    return {item["gw_seq"]: item["duration_ms"] / 1000.0 for item in gateway.side_records()}


def _episode_attempts(scope: Mapping[str, Any] | None, episode: str) -> list[dict[str, Any]]:
    if scope is None:
        return []
    return [item for item in scope["attempts"] if item["episode"] == episode]


def _reaction(kind, event, error, scopes, slow, run, verdicts, replay, cases, seconds) -> dict[str, Any]:
    """One reactive event with its recorded reaction: a habit episode or the slow path."""
    failed = error["failed_action"]
    scope = scopes.get(error["action_scope"])
    case = cases.get(error["action_scope"])
    habit_episode = _episode_attempts(scope, f"{error['event_id']}|habit")
    slow_event = slow.get(error["event_id"])
    slow_attempts = _episode_attempts(scope, f"{error['event_id']}|slow")
    marker_id = error.get("segment_marker_id")
    verdict = verdicts.get(marker_id) if marker_id else None
    anchored_evidence = bool(verdict and verdict["verdict"] == "confirmed" and verdict["stage"] == "1"
                             and verdict["evidence"])
    settled = None
    if scope is not None:
        failure = next((item for item in scope["failures"] if item["gw_seq"] == error["action_ref"]["gw_seq"]), None)
        settled = None if failure is None else failure["settled"]
    order = [item["gw_seq"] for item in scope["attempts"]] if scope is not None else []
    episode = slow_attempts or habit_episode
    steps_range = ([order.index(episode[0]["gw_seq"]), order.index(episode[-1]["gw_seq"])] if episode else [0, 0])
    entry = {"event_id": error["event_id"], "run_id": run, "task_id": error.get("task_id"),
             "segment_marker_id": marker_id, "off_plan": bool(error.get("off_plan")),
             "context": typed_context(error), "failed": failed, "reaction": _REACTIONS[kind],
             "habit_id": event.get("habit_id"), "trigger_id": event.get("trigger_id"),
             "layer": event.get("layer"), "failed_condition": event.get("failed_condition"),
             "op_scope": error["action_scope"], "qid": None if case is None else case["quantum"]["id"],
             "steps_range": steps_range,
             "segment_verdict": None if verdict is None else verdict["verdict"],
             "segment_flags": [] if verdict is None else verdict["flags"],
             "replay": replay.get("status"), "anchored_evidence": anchored_evidence, "failed_settled": settled,
             "habit_outcome": local_outcome(habit_episode) if kind == "habit_activated" else None,
             "habit_attempts": [item["gw_seq"] for item in habit_episode], "slow": None}
    if kind == "habit_activated":
        entry["habit_steps"] = typed_steps(habit_episode, failed)
    if slow_event is not None:
        actions = [item for item in slow_attempts if item["role"] == "action"]
        durations = [seconds.get(item["gw_seq"]) for item in slow_attempts]
        tokens = sum(scope["payloads"][item["gw_seq"]].get("tokens", 0) for item in slow_attempts
                     if isinstance(scope["payloads"].get(item["gw_seq"]), dict)
                     and type(scope["payloads"][item["gw_seq"]].get("tokens", 0)) is int)
        entry["slow"] = {
            "completed": bool(slow_event.get("completed")), "outcome": local_outcome(slow_attempts),
            "steps": typed_steps(slow_attempts, failed),
            "calls": [{"tool": "$same" if item["op"] == failed["op"] and item["tool"] == failed["tool"] else item["tool"],
                       "args": item["args"]} for item in actions],
            "witnesses": sorted({item["source"] for item in actions
                                 if item["op_result"] == "ok" and corroborating(item["role"], item["source"])}),
            "evidence": [item["evidence_ref"] for item in actions if item["evidence_ref"] is not None],
            "evidence_preexisting": any(item["evidence_preexisting"] for item in actions),
            "gw_seqs": [item["gw_seq"] for item in slow_attempts],
            "seconds": None if not durations or any(value is None for value in durations) else sum(durations),
            "tokens": tokens}
    return entry


def _fire(reaction, event, parameters, verdicts, mode) -> dict[str, Any]:
    """Stage 2 for one fire: counted signal, conserved or excluded, with its reason."""
    outcome = reaction["habit_outcome"]
    base = {"event_id": event.get("trigger_event_id"), "run_id": reaction["run_id"], "habit_id": event["habit_id"],
            "trigger_id": event["trigger_id"], "task_id": reaction["task_id"], "outcome": outcome,
            "runtime_outcome": event.get("outcome"), "segment_marker_id": reaction["segment_marker_id"],
            "segment_verdict": reaction["segment_verdict"], "signal": None,
            "conflict_warning": bool(event.get("conflict_warning")),
            "runner_up_habit_id": event.get("runner_up_habit_id"), "semantic_score_micros": event.get(
                "semantic_score_micros"), "context": reaction["context"], "steps": reaction.get("habit_steps", [])}
    if reaction["off_plan"] or reaction["segment_marker_id"] is None:
        return {**base, "status": "excluded", "why": "off_plan"}
    verdict = reaction["segment_verdict"]
    if verdict is None:
        return {**base, "status": "pending", "why": "segment_not_judged"}
    if "environmental_failure" in reaction["segment_flags"]:
        return {**base, "status": "excluded", "why": "environmental_failure"}
    if verdict == "skipped":
        return {**base, "status": "excluded", "why": "skipped"}
    if verdict == "uncertain":
        return {**base, "status": "pending", "why": "segment_uncertain"}
    if outcome in {"uncertain", "unclear"}:
        return {**base, "status": "pending", "why": f"outcome_{outcome}"}
    value = signal_of(parameters, outcome, verdict)
    if value is None:
        return {**base, "status": "pending", "why": "signal_undecided"}
    status = "pending" if mode == "emergency" else "counted"
    return {**base, "status": status, "signal": value, "why": "provisional" if mode == "emergency" else None}


def _case(session, facts, scope_name, found, markers, verdicts, seconds, replay) -> dict[str, Any]:
    """Material of one case for substage 7a (the quantum is computed on decision)."""
    run = session["run_id"]
    events = []
    for kind in ("external_action", "external_error", "habit_activated", "habit_near_miss", "habit_miss",
                 "slow_path_used"):
        for position, event in found.get(kind, []):
            belongs = ((kind == "external_action" and event["request"].get("op_scope") == scope_name)
                       or (kind == "external_error" and event.get("action_scope") == scope_name))
            if belongs:
                events.append((position, event))
    error_ids = {event["event_id"] for _, event in events if event["type"] == "external_error"}
    for kind in ("habit_activated", "habit_near_miss", "habit_miss", "slow_path_used"):
        for position, event in found.get(kind, []):
            if event.get("trigger_event_id") in error_ids:
                events.append((position, event))
    events.sort(key=lambda item: item[0])
    marker_id = facts["segment"]
    marker = markers.get(marker_id, (None, None))[0] if marker_id else None
    plan = markers.get(marker_id, (None, None))[1] if marker_id else None
    verdict = verdicts.get(marker_id) if marker_id else None
    actions = [item for item in facts["attempts"] if item["role"] == "action"]
    durations = [seconds.get(item["gw_seq"]) for item in facts["attempts"]]
    measured = None if any(value is None for value in durations) else sum(durations)
    quantum = case_quantum(task_contract_ref=None if plan is None else plan["task_contract_ref"],
                           segment_marker_id=marker_id, program_ref=session["source_hash"],
                           start_snapshot_ref=session.get("pinned"), recorded_results=facts["evidence_refs"],
                           trace=trace_ref(event for _, event in events), outcome_class=facts["outcome"])
    return {"quantum": quantum, "run_id": run, "op_scope": scope_name, "marker_id": marker_id,
            "task_contract_ref": None if plan is None else plan["task_contract_ref"],
            "element": None if plan is None else plan["element"],
            "element_part": None if marker is None else marker["element_part"],
            "task_id": facts["task_id"], "program_ref": session["source_hash"],
            "start_snapshot_ref": session.get("pinned"), "recorded_results": facts["evidence_refs"],
            "outcome": facts["outcome"], "verdict": None if verdict is None else verdict["verdict"],
            "anchored_confirmed": bool(verdict and verdict["verdict"] == "confirmed" and marker
                                       and marker["external_anchor"] is not None),
            "steps": [{"step": "call", "tool": item["tool"], "args_ref": "sha256:" + item["args_canon"],
                       "result_class": "ok" if item["op_result"] == "ok" else (item["op_err"] or item["op_result"])}
                      for item in actions],
            "habits": sorted({event["habit_id"] for _, event in events if event["type"] == "habit_activated"}),
            "reactions": sum(1 for _, event in events if event["type"] in {"habit_activated", "slow_path_used"}),
            "defects": [{"kind": "Defect", "tool": item["tool"], "op_err": item["op_err"], "gw_seq": item["gw_seq"],
                         "verified": replay.get("status") == "replay_verified"}
                        for item in facts["failures"] if not item["settled"]],
            "cost": facts["cost"], "seconds": measured, "replay": replay.get("status"),
            "evidence_refs": facts["evidence_refs"], "context": _case_context(events)}


def _case_context(events) -> dict[str, Any]:
    for _, event in events:
        if event["type"] == "external_error":
            return typed_context(event)["fields"]
    return {}


def _conflict_advice(counsel: Counsel, inputs: DreamInputs, fires: list[dict[str, Any]],
                     reactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Recorded counterfactual advice for learned competitors (stage 4, step 2).

    Competitors share one applicability and expected outcome and differ in
    action. The question rests only on recorded outcomes: fires of both and
    slow-path episodes on the shared trigger whose steps are either habit's
    action. With fewer than the declared minimum for either side there is no
    safe comparison and no question is asked.
    """
    from .judge_decide import competitors

    state, parameters = inputs.state, inputs.configuration.parameters
    history: dict[str, list] = {habit_id: list(metadata.get("recent", []))
                                for habit_id, metadata in state["habits"].items()}
    for fire in fires:
        history.setdefault(fire["habit_id"], []).append({"outcome": fire["outcome"], "path": "habit",
                                                         "segment_verdict": fire["segment_verdict"],
                                                         "fields": fire["context"]["fields"]})
    advice: dict[str, Any] = {}
    for left, right in competitors(state, parameters):
        a, b = state["frozen"][left], state["frozen"][right]
        for reaction in reactions:
            slow = reaction["slow"]
            if slow is None or matches(a["trigger"], reaction["context"])[0] != "applicable":
                continue
            for habit_id, frozen in ((left, a), (right, b)):
                if step_similarity(slow["steps"], frozen["habit"]["action_pattern"]) >= parameters["action_same"]:
                    history.setdefault(habit_id, []).append({"outcome": slow["outcome"], "path": "slow",
                                                             "segment_verdict": reaction["segment_verdict"],
                                                             "fields": reaction["context"]["fields"]})
        key = f"{left}|{right}"
        if min(len(history.get(left, [])), len(history.get(right, []))) < parameters["counterfactual_min_pairs"]:
            advice[key] = {"basis": "insufficient_recorded_outcomes", "asked": False, "answer": None}
            continue
        variant = {"A": {"habit_id": left, "pattern": a["habit"]["action_pattern"], "outcomes": history[left]},
                   "B": {"habit_id": right, "pattern": b["habit"]["action_pattern"], "outcomes": history[right]}}
        answer = counsel.ask("would_B_outcome_be_better", ("yes", "no"),
                             (variant, {"B": variant["B"], "A": variant["A"]}))
        advice[key] = {"basis": "recorded_outcomes", **answer}
    return advice


def _arbitration(counsel: Counsel, inputs: DreamInputs, partial: Mapping[str, Any]) -> dict[str, Any]:
    """Arbitration for candidates that meet every criterion but sit in a gray band.

    The same pure pool merge and criteria the decision applies select the
    candidates; the answers are recorded here and consumed there.
    """
    from .judge_decide import assess, merge_pool, typed_check

    state, configuration = inputs.state, inputs.configuration
    parameters = configuration.parameters
    pool, touched = merge_pool(state, partial, parameters, state["window"] + 1)
    result: dict[str, Any] = {}
    for key in touched:
        assessment = assess(pool[key], state, parameters, configuration, partial["requests"])
        if assessment["reasons"]:
            continue
        relations = typed_check(pool[key]["steps"], assessment["condition"], state, parameters)
        if any(item["relation"] in {"duplicate", "absorbed", "archived_duplicate", "archived_absorbed"}
               for item in relations):
            continue
        sample = assessment["success"][0]
        event = {"fields": sample["fields"]}
        template = context_template(assessment["condition"], [item["context"] for item in assessment["success"]])
        for item in relations:
            similarity = None
            if item["relation"] == "distinct":
                if item["archived"]:
                    continue
                similarity = counsel.similarity(render_template(template, event),
                                                render_template(item["template"], event))
                if similarity is None or similarity < parameters["arbitration_similarity"]:
                    result[f"{key}|{item['habit_id']}"] = {"similarity": similarity, "answer": None,
                                                           "asked": False}
                    continue
            elif item["relation"] != "arbitration_action":
                continue
            frozen = state["frozen"][item["habit_id"]]
            question = {"candidate": {"trigger": assessment["condition"], "steps": pool[key]["steps"]},
                        "existing": {"trigger": frozen["trigger"]["when"], "steps": frozen["habit"]["action_pattern"]}}
            answer = counsel.ask("variation_or_different", ("variation", "different", "uncertain"),
                                 (question, {"existing": question["existing"], "candidate": question["candidate"]}))
            if answer["answer"] == "uncertain":
                answer = {**answer, "answer": None}
            result[f"{key}|{item['habit_id']}"] = {"similarity": similarity, **answer}
    return result
