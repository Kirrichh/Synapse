"""The court's decisions (``integrate``, stages 3–6): deterministic and cheap.

No model is asked here (И6): the advice and similarity this stage uses were
recorded by the evaluation. Given the previous state, the evaluation draft, the
declared parameters and the legitimacy Gold reports for existing behaviors,
the result is fully determined; ties are broken by identity, never by order of
arrival or dictionary order.

* stage 3 — trust by the delta rule per trigger, with minimum evidence and
  pending accumulation; conserved and provisional evidence waits, expired
  evidence is reported; declared habits are observed only;
* stage 4 — the conflict ladder among learned competitors: an established
  trust gap, then recorded counterfactual advice, otherwise both on probation
  and the trigger slow-only until a later verified verdict;
* stage 5 — cold anchors first, then the candidate pool, the six birth
  criteria with three-valued provenance independence, the typed check against
  existing habits, arbitration, and boundary successors (narrow, widen);
* stage 6 — the effectiveness automaton T1–T9, TC and TS, threshold or SPRT,
  with key habits kept past T9.

An emergency consolidation records verdicts and signals as pending evidence
and changes no trust, state, pool or birth (fail-closed).
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import records
from .behavior import SAME, BindingUnavailable, derive_binding, step_similarity
from .operations import canonical, digest
from .policy import sprt_decision, sprt_step, trust_update
from .provenance import independent_witnesses
from .quanta import weight
from .triggers import anti_unify, condition_key, context_template, covers, make_trigger, matches, narrowed, widened

EFFECTIVE = ("born", "active", "probation")


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
def new_metadata(parameters, *, habit_id: str, trigger_id: str, state: str, trust: float, window: int,
                 consolidation_id: str, energy_cost: float, supersedes: str | None) -> dict[str, Any]:
    return {"habit_id": habit_id, "trigger_id": trigger_id, "state": state, "trust": trust,
            "context_trust": {trigger_id: trust}, "counted": 0, "pending": [], "state_since": window,
            "fires_in_state": 0, "signals_in_state": 0, "signal_sum_in_state": 0.0, "participations_in_state": 0,
            "fires_since_birth": 0, "tasks_since_birth": [], "idle_windows": 0, "cold_windows": 0,
            "sprt_llr": 0.0, "key_hold_until": None, "votes": 0,
            "exec_summary": {"fires_total": 0, "successes": 0, "failures": 0, "uncertain": 0},
            "energy_cost": energy_cost, "priority": parameters["learned_priority_class"],
            "born_in": consolidation_id, "supersedes": supersedes, "superseded_by": None, "recent": []}


def _declared_metadata(habit_id: str) -> dict[str, Any]:
    return {"habit_id": habit_id, "context_trust": {}, "counted": {}, "pending": [], "recent": [],
            "exec_summary": {"fires_total": 0, "successes": 0, "failures": 0, "uncertain": 0}}


def _summary(summary: dict[str, int], outcome: str | None) -> None:
    summary["fires_total"] += 1
    if outcome == "success":
        summary["successes"] += 1
    elif outcome == "failure":
        summary["failures"] += 1
    else:
        summary["uncertain"] += 1


def _mean(values):
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# stage 3: trust
# ---------------------------------------------------------------------------
def _accumulate(parameters, metadata, fires, trigger_of, window, verified_runs, report):
    """Pending accumulation and delta-rule updates of one habit's triggers."""
    pending = [dict(item) for item in metadata["pending"]]
    for entry in pending:
        if entry.get("provisional") and entry["run_id"] in verified_runs:
            entry["provisional"] = False
    fresh = []
    for fire in fires:
        entry = {"event_id": fire["event_id"], "run_id": fire["run_id"], "trigger_id": trigger_of(fire),
                 "signal": fire["signal"], "window": window, "why": fire["why"],
                 "provisional": fire["why"] == "provisional"}
        if fire["status"] == "excluded":
            report["excluded_signals"].append({"habit_id": metadata["habit_id"], "event_id": fire["event_id"],
                                               "why": fire["why"]})
            continue
        fresh.append(entry)
    horizon = window - parameters["pending_max_windows"]
    expired = [item for item in pending if item["window"] <= horizon]
    for item in expired:
        report["expired_pending"].append({"habit_id": metadata["habit_id"], "event_id": item["event_id"],
                                          "window": item["window"], "why": item["why"]})
    pending = [item for item in pending if item["window"] > horizon] + fresh
    updates = []
    for trigger_id in sorted({item["trigger_id"] for item in pending}):
        ready = [item for item in pending if item["trigger_id"] == trigger_id and item["signal"] is not None
                 and not item["provisional"]]
        if len(ready) < parameters["min_evidence"]:
            continue
        updates.append((trigger_id, ready))
        pending = [item for item in pending if item not in ready]
    return pending, updates


def _trust_stage(parameters, state, draft, mode, report) -> tuple[dict, dict, dict]:
    habits = copy.deepcopy(state["habits"])
    declared = copy.deepcopy(state["declared"])
    window = state["window"] + 1
    verified_runs = {run for run, result in draft["replay"].items() if result.get("status") == "replay_verified"}
    by_habit: dict[str, list] = {}
    for fire in draft["fires"]:
        by_habit.setdefault(fire["habit_id"], []).append(fire)
    window_signals: dict[str, list[float]] = {}
    for habit_id in sorted(set(habits) | set(by_habit)):
        if habit_id not in habits:
            report["excluded_signals"].extend({"habit_id": habit_id, "event_id": fire["event_id"],
                                               "why": "habit_unknown_to_court"} for fire in by_habit[habit_id])
            continue
        metadata = habits[habit_id]
        fires = sorted(by_habit.get(habit_id, []), key=lambda item: item["event_id"])
        for fire in fires:
            if fire["status"] != "excluded":
                _summary(metadata["exec_summary"], fire["outcome"])
                metadata["recent"] = (metadata["recent"] + [{"outcome": fire["outcome"],
                                                             "segment_verdict": fire["segment_verdict"],
                                                             "fields": fire["context"]["fields"],
                                                             "task_id": fire["task_id"]}])[-parameters["recent_fires"]:]
        pending, updates = _accumulate(parameters, metadata, fires, lambda fire: fire["trigger_id"], window,
                                       verified_runs, report)
        metadata["pending"] = pending
        window_signals[habit_id] = [fire["signal"] for fire in fires if fire["status"] == "counted"]
        if mode == "emergency" or metadata["state"] not in EFFECTIVE:
            if updates and metadata["state"] not in EFFECTIVE:
                metadata["pending"] = pending + [item for _, ready in updates for item in ready]
            continue
        for trigger_id, ready in updates:
            observed = _mean([item["signal"] for item in ready])
            old = metadata["context_trust"].get(trigger_id, metadata["trust"])
            new = trust_update(parameters, old, observed, metadata["state"])
            metadata["context_trust"][trigger_id] = new
            metadata["counted"] += len(ready)
            report["trust_decisions"].append({
                "habit_id": habit_id, "trigger_id": trigger_id, "counted": len(ready), "signal": observed,
                "lr": parameters["lr"][metadata["state"]], "trust_old": old, "trust_new": new,
                "events": [item["event_id"] for item in ready]})
        metadata["trust"] = metadata["context_trust"].get(metadata["trigger_id"], metadata["trust"])
        if pending:
            report["pending_evidence"].append({"habit_id": habit_id, "accumulated": len(pending),
                                               "events": [item["event_id"] for item in pending]})
    declared_fires: dict[str, list] = {}
    for fire in draft["declared_fires"]:
        declared_fires.setdefault(fire["habit_id"], []).append(fire)
    for habit_id, fires in sorted(declared_fires.items()):
        metadata = declared.setdefault(habit_id, _declared_metadata(habit_id))
        for fire in fires:
            if fire["status"] != "excluded":
                _summary(metadata["exec_summary"], fire["outcome"])
        pending, updates = _accumulate(parameters, metadata, sorted(fires, key=lambda item: item["event_id"]),
                                       lambda fire: fire["trigger_id"], window, verified_runs, report)
        metadata["pending"] = pending
        if mode == "emergency":
            metadata["pending"] = pending + [item for _, ready in updates for item in ready]
            continue
        for trigger_id, ready in updates:
            observed = _mean([item["signal"] for item in ready])
            old = metadata["context_trust"].get(trigger_id, parameters["resurrection_trust"])
            new = trust_update(parameters, old, observed, "active")
            metadata["context_trust"][trigger_id] = new
            metadata["counted"][trigger_id] = metadata["counted"].get(trigger_id, 0) + len(ready)
            report["declared_observations"].append({"habit_id": habit_id, "trigger_id": trigger_id,
                                                    "counted": len(ready), "signal": observed,
                                                    "trust_old": old, "trust_new": new})
            if observed < parameters["t3_signal"] and len(ready) >= parameters["t3_fires"]:
                report["recommendations"].append({
                    "habit_id": habit_id, "layer": 1, "recommendation": "review_declared_habit",
                    "basis": f"signal {observed:.4f} below {parameters['t3_signal']} over {len(ready)} fires",
                    "authority": "GOVERNING_HUMAN"})
    return habits, declared, window_signals


# ---------------------------------------------------------------------------
# stage 4: conflicts
# ---------------------------------------------------------------------------
def competitors(state, parameters) -> list[tuple[str, str]]:
    """Pairs of live learned habits with one applicability, one expected outcome, other actions."""
    live = sorted(habit_id for habit_id, metadata in state["habits"].items() if metadata["state"] in EFFECTIVE)
    pairs = []
    for index, left in enumerate(live):
        for right in live[index + 1:]:
            a, b = state["frozen"][left], state["frozen"][right]
            if (canonical(condition_key(a["trigger"])) == canonical(condition_key(b["trigger"]))
                    and a["habit"]["expected_outcome"] == b["habit"]["expected_outcome"]
                    and step_similarity(a["habit"]["action_pattern"], b["habit"]["action_pattern"])
                    < parameters["action_same"]):
                pairs.append((left, right))
    return pairs


def _conflict_stage(parameters, state, habits, draft, report, forced):
    slow_only = [dict(item) for item in state["slow_only"]]
    view = {"habits": habits, "frozen": state["frozen"]}
    for left, right in competitors(view, parameters):
        a, b = habits[left], habits[right]
        senior, junior = (left, right) if (-a["trust"], left) <= (-b["trust"], right) else (right, left)
        gap = abs(a["trust"] - b["trust"])
        condition = condition_key(state["frozen"][left]["trigger"])
        entry = {"trigger": condition, "habits": {"A": senior, "B": junior},
                 "trust": {"A": habits[senior]["trust"], "B": habits[junior]["trust"]}, "gap": gap}
        blocked = condition in slow_only
        if gap >= parameters["conflict_gap"] and not blocked:
            report["conflicts"].append({**entry, "step": 1, "resolution": "A_selected_by_trust_gap"})
            continue
        advice = draft["conflict_advice"].get(f"{min(left, right)}|{max(left, right)}", {"basis": "not_asked",
                                                                                         "answer": None})
        answer = advice.get("answer")
        if answer is not None:
            # The question asks whether B (the second in identity order) would have been better.
            better = max(left, right) if answer == "yes" else min(left, right)
            loser = right if better == left else left
            forced.setdefault(loser, ("TC", f"lost counterfactual step 2 on {condition['event_types']}"))
            if blocked:
                slow_only = [item for item in slow_only if item != condition]
            report["conflicts"].append({**entry, "step": 2, "advice": _advice_view(advice),
                                        "resolution": f"{better}_stays_{loser}_probation",
                                        "slow_only_lifted": blocked})
            continue
        for habit_id in (senior, junior):
            forced.setdefault(habit_id, ("TC", "unresolved conflict, step 3"))
        if condition not in slow_only:
            slow_only.append(condition)
        report["conflicts"].append({**entry, "step": 3, "advice": _advice_view(advice),
                                    "resolution": "both_probation_trigger_slow_only"})
    return sorted(slow_only, key=canonical)


def _advice_view(advice: Mapping[str, Any]) -> dict[str, Any]:
    return {key: advice.get(key) for key in ("basis", "asked", "calls", "answer", "agreed", "reasons", "refs",
                                             "component")}


# ---------------------------------------------------------------------------
# stage 5: pool, births, boundaries
# ---------------------------------------------------------------------------
def _support(reaction) -> str | None:
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


def merge_pool(state, draft, parameters, window) -> tuple[dict, list]:
    """The pool after this window's reaction episodes (pure; the evaluation foresees with it)."""
    pool = copy.deepcopy(state["pool"])
    touched = []
    for reaction in sorted(draft["reactions"], key=lambda item: (item["run_id"], item["event_id"])):
        support = _support(reaction)
        if support is None:
            continue
        slow = reaction["slow"]
        key = candidate_key(reaction["context"]["event_type"], slow["steps"])
        entry = pool.setdefault(key, {"candidate_key": key, "event_type": reaction["context"]["event_type"],
                                      "steps": slow["steps"], "episodes": [], "first_seen": window,
                                      "last_seen": window, "status": "accumulating", "reasons": [],
                                      "born_habit": None})
        if entry["status"] in {"born", "expired"}:
            if entry["status"] == "expired":
                entry.update(status="accumulating", reasons=[], first_seen=window)
            else:
                continue
        episode = {"qid": reaction["qid"], "steps": reaction["steps_range"], "run_id": reaction["run_id"],
                   "event_id": reaction["event_id"], "task": reaction["task_id"] or f"run:{reaction['run_id']}",
                   "context": reaction["context"], "calls": slow["calls"], "fields": reaction["context"]["fields"],
                   "failed_args": reaction["failed"]["args"], "witnesses": slow["witnesses"],
                   "evidence": slow["evidence"], "copy": slow["evidence_preexisting"],
                   "verified": reaction["replay"] == "replay_verified" or reaction["anchored_evidence"],
                   "support": support, "window": window, "seconds": slow.get("seconds"),
                   "tokens": slow.get("tokens", 0), "reaction": reaction["reaction"]}
        if any(item["run_id"] == episode["run_id"] and item["event_id"] == episode["event_id"]
               for item in entry["episodes"]):
            continue
        entry["episodes"].append(episode)
        entry["last_seen"] = window
        touched.append(key)
    return pool, sorted(set(touched))


def _request_for(candidate, requests, condition) -> dict | None:
    for request in sorted(requests, key=lambda item: item["habit_id"]):
        for area in request["area"]:
            probe = {"event_types": area["event_types"], "context": area["context"], "when": area["when"],
                     "not_when": area["not_when"]}
            if covers(probe, condition):
                return request
    return None


def _threshold_holds(spec, value) -> bool:
    if spec is None:
        return True
    target = spec["value"]
    return {">": value > target, ">=": value >= target, "<": value < target, "<=": value <= target,
            "==": value == target}.get(spec["op"], False)


def assess(candidate, state, parameters, configuration, requests) -> dict[str, Any]:
    """The six birth criteria of one candidate, each with its machine-readable result."""
    episodes = candidate["episodes"]
    distinct, seen = [], set()
    for item in episodes:
        identity = tuple(sorted(item["evidence"]))
        if item["copy"] or identity in seen:
            continue
        seen.add(identity)
        distinct.append(item)
    success = [item for item in distinct if item["support"] == "success"]
    contradictions = [item for item in distinct if item["support"] == "contradiction"]
    reasons, condition, binding = [], None, None
    if success:
        condition = anti_unify(item["context"] for item in success)
    request = _request_for(candidate, requests, condition) if condition is not None else None
    tasks = sorted({item["task"] for item in success})
    frequency_ok = True
    stability_ok = True
    if request is not None:
        frequency_ok = _threshold_holds(request["frequency"], len(success))
        share = len(success) / len(distinct) if distinct else 0.0
        stability_ok = _threshold_holds(request["stability"], share)
    if len(success) < parameters["birth_episodes"]:
        reasons.append("repeatability_below_threshold")
    if len(tasks) < parameters["birth_tasks"]:
        reasons.append("single_task")
    if request is None and (contradictions or len(success) != len(distinct)):
        reasons.append("not_all_episodes_succeeded")
    if request is not None and not (frequency_ok and stability_ok):
        reasons.append("learning_request_thresholds_unmet")
    try:
        if success:
            binding = derive_binding([{"calls": item["calls"], "fields": item["fields"],
                                       "failed_args": item["failed_args"]} for item in success])
    except BindingUnavailable as exc:
        reasons.append("binding_not_derivable")
        binding = {"unavailable": str(exc)}
    if any(not item["verified"] for item in success):
        reasons.append("episode_not_verified")
    witnesses = sorted({source for item in success for source in item["witnesses"]})
    independence = independent_witnesses(configuration.tools.provenance, witnesses, parameters["birth_sources"])
    if independence["verdict"] == "dependent":
        reasons.append("sources_dependent")
    elif independence["verdict"] == "not_established":
        reasons.append("independence_not_established")
    return {"criteria": {"episodes": len(success), "distinct_episodes": len(distinct),
                         "copies": len(episodes) - len(distinct), "tasks": len(tasks),
                         "contradictions": len(contradictions),
                         "all_success": not contradictions and len(success) == len(distinct),
                         "all_verifiable": all(item["verified"] for item in success),
                         "concrete": binding is not None and "unavailable" not in binding,
                         "independence": independence["verdict"], "request": None if request is None
                         else request["habit_id"]},
            "independence": independence, "reasons": reasons, "condition": condition,
            "binding": binding, "success": success}


def typed_check(candidate_steps, condition, state, parameters) -> list[dict[str, Any]]:
    """Relations of a candidate to every known habit (live and archived), in identity order."""
    relations = []
    for habit_id in sorted(state["frozen"]):
        frozen = state["frozen"][habit_id]
        metadata = state["habits"].get(habit_id, {"state": "extinct"})
        similarity = step_similarity(candidate_steps, frozen["habit"]["action_pattern"])
        existing = condition_key(frozen["trigger"])
        same = canonical(existing) == canonical(condition)
        archived = metadata["state"] not in EFFECTIVE
        if same and similarity >= parameters["action_same"]:
            relation = "archived_duplicate" if archived else "duplicate"
        elif covers(existing, condition) and similarity >= parameters["action_same"]:
            relation = "archived_absorbed" if archived else "absorbed"
        elif same and similarity < parameters["action_different"]:
            relation = "competitor"
        elif same:
            relation = "arbitration_action"
        else:
            relation = "distinct"
        relations.append({"habit_id": habit_id, "relation": relation, "action_similarity": similarity,
                          "archived": archived, "template": frozen["trigger"]["context_template"]})
    return relations


def _pattern(steps) -> list[dict[str, Any]]:
    return [dict(item) for item in steps]


def _expected_outcome(steps) -> str:
    return "failed_operation_recovered" if any(item.get("tool") == SAME for item in steps) else "reaction_completed"


def _energy(parameters, episodes) -> float:
    values = []
    for item in episodes:
        if item.get("seconds") is None:
            continue
        measured = weight(parameters, steps=len(item["calls"]), external_calls=len(item["calls"]), branches=0,
                          retries=0, tokens=item.get("tokens", 0), seconds=item["seconds"], gas=0)
        values.append(measured["R"])
    if not values:
        return float(parameters["unmeasured_energy_cost"])
    return _mean(values) * parameters["energy_per_R"]


def _birth(parameters, consolidation_id, window, condition, steps, binding, episodes, *, supersedes=None,
           trust=None, state_name="born", basis=None, template=None, source_episodes=None, basis_episodes=None,
           energy_cost=None):
    if template is None:
        template = context_template(condition, [item["context"] for item in episodes])
    if source_episodes is None:
        source_episodes = [{"qid": item["qid"], "steps": item["steps"]} for item in episodes]
    trigger = make_trigger(condition, template=template, born_from=consolidation_id, source_episodes=source_episodes)
    habit = records.make("learned_habit", origin="learned", layer=2, trigger=trigger["id"],
                         action_pattern=_pattern(steps), binding=binding, expected_outcome=_expected_outcome(steps),
                         born_from={"consolidation": consolidation_id, "episodes": sorted(
                             set(basis_episodes) if basis_episodes is not None else {item["qid"] for item in episodes})},
                         supersedes=supersedes)
    metadata = new_metadata(parameters, habit_id=habit["id"], trigger_id=trigger["id"], state=state_name,
                            trust=parameters["resurrection_trust"] if trust is None else trust, window=window,
                            consolidation_id=consolidation_id,
                            energy_cost=_energy(parameters, episodes) if energy_cost is None else energy_cost,
                            supersedes=supersedes)
    return {"habit": habit, "trigger": trigger, "metadata": metadata, "basis": basis or [
        {"qid": item["qid"], "steps": item["steps"], "event_id": item["event_id"]} for item in episodes]}


def _cold_stage(state, draft, parameters, legitimacy, report):
    """Cold anchors: archived triggers that window demand matches at least twice."""
    wakes = {}
    contexts = [reaction for reaction in draft["reactions"] if reaction["reaction"] in {"miss", "near_miss"}]
    for habit_id in sorted(state["habits"]):
        metadata = state["habits"][habit_id]
        if metadata["state"] not in {"dormant", "extinct"}:
            continue
        trigger = state["frozen"][habit_id]["trigger"]
        matched = [reaction["event_id"] for reaction in contexts
                   if matches(trigger, reaction["context"])[0] == "applicable"]
        entry = {"habit_id": habit_id, "state": metadata["state"], "matches": matched}
        if len(matched) >= parameters["cold_matches"]:
            if metadata["state"] == "dormant":
                wakes[habit_id] = ("T6", f"{len(matched)} window events matched the archived trigger")
            elif legitimacy.get(habit_id, {}).get("compatible"):
                wakes[habit_id] = ("T8a", f"{len(matched)} window events matched; fresh compatibility admitted")
            else:
                entry["refused"] = "compatibility_not_admitted"
        report["cold_checks"]["dormant_matches" if metadata["state"] == "dormant" else "extinct_matches"].append(entry)
    return wakes


def _boundary_stage(state, habits, draft, parameters, consolidation_id, window, report, births, forced, refused):
    """Narrowing and widening successors of live learned habits."""
    for habit_id in sorted(habits):
        metadata = habits[habit_id]
        if metadata["state"] not in EFFECTIVE or metadata["superseded_by"] is not None or habit_id in forced:
            continue
        frozen = state["frozen"][habit_id]
        failures = [item for item in metadata["recent"] if item["outcome"] == "failure"
                    and item["segment_verdict"] is not None]
        successes = [item for item in metadata["recent"] if item["outcome"] == "success"]
        successor = None
        if len(failures) >= parameters["min_evidence"] and successes:
            shared = set.intersection(*({(k, canonical(v)) for k, v in item["fields"].items()} for item in failures))
            for name, value in sorted(shared):
                if any(canonical(item["fields"].get(name)) == value for item in successes):
                    continue
                subcontext = {"field": name, "op": "==", "value": next(item["fields"][name] for item in failures)}
                try:
                    condition = narrowed(frozen["trigger"], subcontext)
                except ValueError:
                    continue
                successor = ("narrow", condition, f"{len(failures)} verified failures grouped in {name}")
                break
        if successor is None:
            near = [reaction for reaction in draft["near_misses"]
                    if reaction["habit_id"] == habit_id and reaction["slow"] is not None
                    and _support(reaction) == "success"
                    and step_similarity(reaction["slow"]["steps"], frozen["habit"]["action_pattern"])
                    >= parameters["action_same"]]
            conditions = {}
            for reaction in near:
                failed = reaction["failed_condition"]
                conditions.setdefault(canonical({k: failed.get(k) for k in ("field", "op", "value", "forbidden")}),
                                      []).append(reaction)
            for key in sorted(conditions):
                group = conditions[key]
                if len(group) < parameters["min_evidence"]:
                    continue
                try:
                    condition = widened(frozen["trigger"], group[0]["failed_condition"])
                except ValueError:
                    continue
                successor = ("widen", condition, f"{len(group)} near misses recovered by the same action")
                break
        if successor is None:
            continue
        kind, condition, basis = successor
        if any(canonical(condition_key(item["trigger"])) == canonical(condition) for item in state["frozen"].values()):
            continue
        birth = _birth(parameters, consolidation_id, window, condition, frozen["habit"]["action_pattern"],
                       frozen["habit"]["binding"], [], supersedes=habit_id, trust=metadata["trust"],
                       state_name="probation", template=frozen["trigger"]["context_template"],
                       source_episodes=frozen["trigger"]["source_episodes"],
                       basis_episodes=frozen["habit"]["born_from"]["episodes"],
                       energy_cost=metadata["energy_cost"], basis=[{"boundary": kind, "basis": basis}])
        birth["boundary"] = {"kind": kind, "basis": basis, "predecessor": habit_id}
        if birth["habit"]["id"] in refused:
            report["refused_births"].append({"habit_id": birth["habit"]["id"], "predecessor": habit_id,
                                             "reason": refused[birth["habit"]["id"]]})
            continue
        births.append(birth)
        forced[habit_id] = ("TS", f"superseded by a {kind}ed successor")
        report["supersessions"].append({"predecessor": habit_id, "successor": birth["habit"]["id"], "kind": kind,
                                        "basis": basis, "trigger": condition})


def _pool_stage(state, draft, parameters, configuration, consolidation_id, window, report, births, consumed,
                refused):
    pool, touched = merge_pool(state, draft, parameters, window)
    arbitration = draft.get("arbitration", {})
    updates: dict[str, Any] = {}
    born_conditions = {canonical(condition_key(item["trigger"])) for item in births}
    for key in sorted(pool):
        entry = pool[key]
        if entry["status"] in {"born"}:
            continue
        if key not in touched:
            if window - entry["last_seen"] >= parameters["candidate_max_windows"]:
                updates[key] = None
                report["pool_updates"].append({"candidate_key": key, "status": "expired",
                                               "episodes_total": len(entry["episodes"])})
            continue
        entry["episodes"] = [item for item in entry["episodes"] if item["event_id"] not in consumed]
        assessment = assess(entry, state, parameters, configuration, draft["requests"])
        reasons = list(assessment["reasons"])
        relation = None
        if not reasons:
            relation, verdict_reasons = _typed_relation(key, entry, assessment, state, parameters, arbitration, report)
            reasons.extend(verdict_reasons)
            if canonical(assessment["condition"]) in born_conditions:
                reasons.append("successor_owns_trigger")
        entry["reasons"] = reasons
        if reasons:
            entry["status"] = "arbitration_pending" if "arbitration_unresolved" in reasons else "accumulating"
            updates[key] = entry
            report["pool_updates"].append({"candidate_key": key, "status": entry["status"],
                                           "episodes_total": len(entry["episodes"]), "reasons": reasons,
                                           "criteria": assessment["criteria"]})
            continue
        birth = _birth(parameters, consolidation_id, window, assessment["condition"], entry["steps"],
                       assessment["binding"], assessment["success"])
        if birth["habit"]["id"] in refused:
            entry["reasons"] = [f"gate_refused:{refused[birth['habit']['id']]}"]
            entry["status"] = "accumulating"
            updates[key] = entry
            report["refused_births"].append({"habit_id": birth["habit"]["id"], "candidate_key": key,
                                             "reason": refused[birth["habit"]["id"]]})
            report["pool_updates"].append({"candidate_key": key, "status": "accumulating",
                                           "episodes_total": len(entry["episodes"]), "reasons": entry["reasons"],
                                           "criteria": assessment["criteria"]})
            continue
        birth["candidate_key"] = key
        birth["criteria"] = assessment["criteria"]
        birth["independence"] = assessment["independence"]
        birth["typed_check"] = "distinct" if relation is None else relation["relation"]
        birth["arbitration"] = None if relation is None else relation.get("arbitration")
        births.append(birth)
        born_conditions.add(canonical(assessment["condition"]))
        entry["status"] = "born"
        entry["born_habit"] = birth["habit"]["id"]
        updates[key] = entry
    return updates


def _typed_relation(key, entry, assessment, state, parameters, arbitration, report):
    """Typed check and recorded arbitration of one candidate that met every criterion."""
    relations = typed_check(entry["steps"], assessment["condition"], state, parameters)
    decisive = next((item for item in relations if item["relation"] in {
        "duplicate", "absorbed", "archived_duplicate", "archived_absorbed"}), None)
    if decisive is not None:
        if decisive["relation"] in {"duplicate", "absorbed"}:
            report["votes"].append({"candidate_key": key, "habit_id": decisive["habit_id"],
                                    "relation": decisive["relation"]})
        return decisive, [f"typed_{decisive['relation']}"]
    relation = None
    for item in relations:
        answer = arbitration.get(f"{key}|{item['habit_id']}")
        if item["relation"] == "distinct":
            similarity = None if answer is None else answer.get("similarity")
            if item["archived"] or similarity is None or similarity < parameters["arbitration_similarity"]:
                continue
        elif item["relation"] != "arbitration_action":
            continue
        verdict = None if answer is None else answer.get("answer")
        view = None if answer is None else {k: answer.get(k) for k in ("similarity", "calls", "answer", "agreed",
                                                                      "refs")}
        relation = {**item, "arbitration": view}
        if verdict == "variation":
            report["votes"].append({"candidate_key": key, "habit_id": item["habit_id"], "relation": "variation"})
            return relation, ["arbitrated_variation"]
        if verdict != "different":
            return relation, ["arbitration_unresolved"]
    return relation, []


# ---------------------------------------------------------------------------
# stage 6: automaton
# ---------------------------------------------------------------------------
def _key_habit(habit_id, state, habits, legitimacy) -> bool:
    frozen = state["frozen"][habit_id]
    for other in sorted(habits):
        if other == habit_id or habits[other]["state"] not in EFFECTIVE:
            continue
        if legitimacy.get(other, {}).get("admitted") is False:
            continue
        candidate = state["frozen"].get(other)
        if candidate is None or candidate["habit"]["expected_outcome"] != frozen["habit"]["expected_outcome"]:
            continue
        if covers(condition_key(candidate["trigger"]), condition_key(frozen["trigger"])):
            return False
    return True


def _enter(metadata, state_name, window):
    metadata.update(state=state_name, state_since=window, fires_in_state=0, signals_in_state=0,
                    signal_sum_in_state=0.0, participations_in_state=0, idle_windows=0, cold_windows=0,
                    sprt_llr=0.0, key_hold_until=None)


def _automaton(parameters, decision_rule, state, habits, draft, window_signals, forced, wakes, legitimacy, window,
               report):
    fires_by_habit: dict[str, list] = {}
    for fire in draft["fires"]:
        if fire["status"] != "excluded":
            fires_by_habit.setdefault(fire["habit_id"], []).append(fire)
    view = {"frozen": state["frozen"]}
    for habit_id in sorted(habits):
        metadata = habits[habit_id]
        fires = fires_by_habit.get(habit_id, [])
        signals = window_signals.get(habit_id, [])
        current = metadata["state"]

        def move(to, rule, basis, trust=None):
            report["transitions"].append({"habit_id": habit_id, "from": current, "to": to, "rule": rule,
                                          "basis": basis})
            _enter(metadata, to, window)
            if trust is not None:
                metadata["trust"] = trust
                metadata["context_trust"] = {metadata["trigger_id"]: trust}

        if habit_id in wakes:
            rule, basis = wakes[habit_id]
            move("probation", rule, basis, trust=parameters["resurrection_trust"])
            continue
        if current in EFFECTIVE:
            metadata["fires_in_state"] += len(fires)
            metadata["fires_since_birth"] += len(fires)
            metadata["tasks_since_birth"] = sorted(set(metadata["tasks_since_birth"])
                                                   | {fire["task_id"] or f"run:{fire['run_id']}" for fire in fires})
            metadata["signals_in_state"] += len(signals)
            metadata["signal_sum_in_state"] += sum(signals)
            metadata["idle_windows"] = 0 if fires else metadata["idle_windows"] + 1
            participated = bool(fires)
            for value in signals:
                metadata["sprt_llr"] += sprt_step(parameters, value)
            hypothesis = sprt_decision(parameters, metadata["sprt_llr"]) if decision_rule == "sprt" else None
        if habit_id in forced and current in {"born", "active", "probation"}:
            rule, basis = forced[habit_id]
            if rule == "TS" and current in {"active", "probation", "born"}:
                move("dormant", "TS", basis)
                metadata["superseded_by"] = next(item["successor"] for item in report["supersessions"]
                                                 if item["predecessor"] == habit_id)
                continue
            if rule == "TC" and current in {"born", "active"}:
                move("probation", "TC", basis)
                continue
        if current == "born":
            t1 = (metadata["trust"] >= parameters["t1_trust"] and metadata["fires_since_birth"] >= parameters["t1_fires"]
                  and len(metadata["tasks_since_birth"]) >= parameters["t1_tasks"])
            if decision_rule == "sprt":
                t1 = t1 and hypothesis == "H0"
            if t1:
                move("active", "T1", f"trust {metadata['trust']:.4f}, {metadata['fires_since_birth']} fires in "
                                     f"{len(metadata['tasks_since_birth'])} tasks")
                continue
            if participated:
                metadata["participations_in_state"] += 1
            if (hypothesis == "H1" or (decision_rule == "threshold" and metadata["participations_in_state"] >= 2)
                    or metadata["idle_windows"] >= parameters["m_idle"]):
                move("probation", "T2", "participating consolidations without T1" if participated or
                     hypothesis == "H1" else f"{metadata['idle_windows']} consolidations without fires")
            continue
        if current == "active":
            if decision_rule == "threshold":
                t3 = len(signals) >= parameters["t3_fires"] and _mean(signals) < parameters["t3_signal"]
            else:
                t3 = hypothesis == "H1"
            if t3:
                move("probation", "T3", f"window signal {(_mean(signals) or 0):.4f} over {len(signals)} fires")
                continue
            if metadata["idle_windows"] >= parameters["n_t9"]:
                hold = metadata["key_hold_until"]
                if _key_habit(habit_id, view, habits, legitimacy):
                    if hold is None or window >= hold:
                        metadata["key_hold_until"] = window + parameters["n_pivot"]
                        report["transitions"].append({"habit_id": habit_id, "from": "active", "to": "active",
                                                      "rule": "T9_key_hold", "basis": "key habit kept for review"})
                    continue
                move("dormant", "T9", f"{metadata['idle_windows']} consolidations without fires")
            continue
        if current == "probation":
            mean = (metadata["signal_sum_in_state"] / metadata["signals_in_state"]
                    if metadata["signals_in_state"] else None)
            if decision_rule == "threshold":
                t4 = metadata["fires_in_state"] >= parameters["t4_fires"] and mean is not None and \
                    mean >= parameters["t4_signal"]
            else:
                t4 = hypothesis == "H0"
            if t4:
                move("active", "T4", f"{metadata['fires_in_state']} fires in probation, mean {mean:.4f}"
                     if mean is not None else "SPRT accepted H0")
                continue
            if participated:
                metadata["participations_in_state"] += 1
            if (hypothesis == "H1" or (decision_rule == "threshold" and metadata["participations_in_state"] >= 2)
                    or metadata["idle_windows"] >= parameters["m_idle"]):
                move("dormant", "T5", "participating consolidations without T4" if participated or
                     hypothesis == "H1" else f"{metadata['idle_windows']} consolidations without fires")
            continue
        if current == "dormant":
            metadata["cold_windows"] += 1
            if metadata["cold_windows"] >= parameters["k_extinct"]:
                move("extinct", "T7", f"{metadata['cold_windows']} consolidations without a cold match")


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------
def decide(state, draft, configuration, legitimacy, refused: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Stages 3–6. Returns the decision sections and the metadata they produce.

    ``refused`` names births whose behavior Gold did not admit, with the
    reason; the court decides again without them, so no later stage acts on
    a birth that never happened.
    """
    refused = dict(refused or {})
    parameters = configuration.parameters
    mode = draft["mode"]
    window = state["window"] + 1
    report: dict[str, Any] = {"trust_decisions": [], "pending_evidence": [], "excluded_signals": [],
                              "expired_pending": [], "declared_observations": [], "recommendations": [],
                              "conflicts": [], "cold_checks": {"dormant_matches": [], "extinct_matches": []},
                              "votes": [], "pool_updates": [], "supersessions": [], "transitions": [],
                              "refused_births": []}
    habits, declared, window_signals = _trust_stage(parameters, state, draft, mode, report)
    births: list[dict[str, Any]] = []
    if mode == "emergency":
        return {"sections": report, "habits": habits, "declared": declared, "births": [], "pool": {},
                "slow_only": copy.deepcopy(state["slow_only"])}
    forced: dict[str, tuple[str, str]] = {}
    slow_only = _conflict_stage(parameters, state, habits, draft, report, forced)
    wakes = _cold_stage(state, draft, parameters, legitimacy, report)
    consumed = {event for habit_id in wakes for entry in report["cold_checks"]["dormant_matches"]
                + report["cold_checks"]["extinct_matches"] if entry["habit_id"] == habit_id
                for event in entry["matches"]}
    _boundary_stage(state, habits, draft, parameters, draft["consolidation_id"], window, report, births, forced,
                    refused)
    pool = _pool_stage(state, draft, parameters, configuration, draft["consolidation_id"], window, report, births,
                       consumed, refused)
    for vote in report["votes"]:
        if vote["habit_id"] in habits:
            habits[vote["habit_id"]]["votes"] += 1
    _automaton(parameters, configuration.decision_rule, state, habits, draft, window_signals, forced, wakes,
               legitimacy, window, report)
    return {"sections": report, "habits": habits, "declared": declared, "births": births, "pool": pool,
            "slow_only": slow_only}
