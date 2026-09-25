"""Stage 3 of the court: trust by the delta rule, with minimum evidence.

``trust_new = trust_old + lr(state) · (signal − trust_old)`` per trigger. With
fewer than ``min_evidence`` ready signals a trigger accumulates pending
evidence; at the threshold it receives one update from their mean. Conserved
fires wait with no signal; provisional signals of an emergency window wait
until their session's re-execution is verified; evidence older than
``pending_max_windows`` expires and is reported. Dormant and extinct habits are
not updated. Declared habits are observed the same way and may only receive a
recommendation to their author.
"""
from __future__ import annotations

import copy
from typing import Any

from ..policy import trust_update
from .habit_state import EFFECTIVE, count_fire, declared_metadata, mean


def _fresh(fires, window, habit_id, report) -> list[dict[str, Any]]:
    fresh = []
    for fire in fires:
        if fire["status"] == "excluded":
            report["excluded_signals"].append({"habit_id": habit_id, "event_id": fire["event_id"], "why": fire["why"]})
            continue
        fresh.append({"event_id": fire["event_id"], "run_id": fire["run_id"], "trigger_id": fire["trigger_id"],
                      "signal": fire["signal"], "window": window, "why": fire["why"],
                      "provisional": fire["why"] == "provisional"})
    return fresh


def accumulate(parameters, metadata, fires, window, verified_runs, report):
    """Pending evidence after this window, and the triggers ready for one update each."""
    pending = [dict(item) for item in metadata["pending"]]
    for entry in pending:
        if entry.get("provisional") and entry["run_id"] in verified_runs:
            entry["provisional"] = False
    horizon = window - parameters["pending_max_windows"]
    for item in pending:
        if item["window"] <= horizon:
            report["expired_pending"].append({"habit_id": metadata["habit_id"], "event_id": item["event_id"],
                                              "window": item["window"], "why": item["why"]})
    pending = [item for item in pending if item["window"] > horizon]
    pending += _fresh(fires, window, metadata["habit_id"], report)
    updates = []
    for trigger_id in sorted({item["trigger_id"] for item in pending}):
        ready = [item for item in pending if item["trigger_id"] == trigger_id and item["signal"] is not None
                 and not item["provisional"]]
        if len(ready) >= parameters["min_evidence"]:
            updates.append((trigger_id, ready))
            pending = [item for item in pending if item not in ready]
    return pending, updates


def _remember(parameters, metadata, fires) -> None:
    for fire in fires:
        if fire["status"] == "excluded":
            continue
        count_fire(metadata["exec_summary"], fire["outcome"])
        metadata["recent"] = (metadata["recent"] + [{"outcome": fire["outcome"], "task_id": fire["task_id"],
                                                     "segment_verdict": fire["segment_verdict"],
                                                     "fields": fire["context"]["fields"]}])[-parameters["recent_fires"]:]


def _update_learned(parameters, habit_id, metadata, updates, report) -> None:
    for trigger_id, ready in updates:
        observed = mean([item["signal"] for item in ready])
        old = metadata["context_trust"].get(trigger_id, metadata["trust"])
        new = trust_update(parameters, old, observed, metadata["state"])
        metadata["context_trust"][trigger_id] = new
        metadata["counted"] += len(ready)
        report["trust_decisions"].append({
            "habit_id": habit_id, "trigger_id": trigger_id, "counted": len(ready), "signal": observed,
            "lr": parameters["lr"][metadata["state"]], "trust_old": old, "trust_new": new,
            "events": [item["event_id"] for item in ready]})
    metadata["trust"] = metadata["context_trust"].get(metadata["trigger_id"], metadata["trust"])


def _learned(parameters, habits, fires_by_habit, window, verified, mode, report) -> dict[str, list[float]]:
    signals: dict[str, list[float]] = {}
    for habit_id in sorted(set(habits) | set(fires_by_habit)):
        fires = sorted(fires_by_habit.get(habit_id, []), key=lambda item: item["event_id"])
        if habit_id not in habits:
            report["excluded_signals"].extend({"habit_id": habit_id, "event_id": fire["event_id"],
                                               "why": "habit_unknown_to_court"} for fire in fires)
            continue
        metadata = habits[habit_id]
        _remember(parameters, metadata, fires)
        pending, updates = accumulate(parameters, metadata, fires, window, verified, report)
        signals[habit_id] = [fire["signal"] for fire in fires if fire["status"] == "counted"]
        if mode == "emergency" or metadata["state"] not in EFFECTIVE:
            metadata["pending"] = pending + [item for _, ready in updates for item in ready]
            continue
        metadata["pending"] = pending
        _update_learned(parameters, habit_id, metadata, updates, report)
        if pending:
            report["pending_evidence"].append({"habit_id": habit_id, "accumulated": len(pending),
                                               "events": [item["event_id"] for item in pending]})
    return signals


def _observe_declared(parameters, habit_id, metadata, updates, report) -> None:
    for trigger_id, ready in updates:
        observed = mean([item["signal"] for item in ready])
        old = metadata["context_trust"].get(trigger_id, parameters["resurrection_trust"])
        new = trust_update(parameters, old, observed, "active")
        metadata["context_trust"][trigger_id] = new
        metadata["counted"][trigger_id] = metadata["counted"].get(trigger_id, 0) + len(ready)
        report["declared_observations"].append({"habit_id": habit_id, "trigger_id": trigger_id, "counted": len(ready),
                                                "signal": observed, "trust_old": old, "trust_new": new})
        if observed < parameters["t3_signal"] and len(ready) >= parameters["t3_fires"]:
            report["recommendations"].append({
                "habit_id": habit_id, "layer": 1, "recommendation": "review_declared_habit",
                "basis": f"signal {observed:.4f} below {parameters['t3_signal']} over {len(ready)} fires",
                "authority": "GOVERNING_HUMAN"})


def _declared(parameters, declared, fires, window, verified, mode, report) -> None:
    by_habit: dict[str, list] = {}
    for fire in fires:
        by_habit.setdefault(fire["habit_id"], []).append(fire)
    for habit_id, items in sorted(by_habit.items()):
        metadata = declared.setdefault(habit_id, declared_metadata(habit_id))
        items = sorted(items, key=lambda item: item["event_id"])
        for fire in items:
            if fire["status"] != "excluded":
                count_fire(metadata["exec_summary"], fire["outcome"])
        pending, updates = accumulate(parameters, metadata, items, window, verified, report)
        if mode == "emergency":
            metadata["pending"] = pending + [item for _, ready in updates for item in ready]
            continue
        metadata["pending"] = pending
        _observe_declared(parameters, habit_id, metadata, updates, report)


def trust_stage(parameters, state, draft, mode, report) -> tuple[dict, dict, dict]:
    """Learned and declared metadata after stage 3, and each learned habit's counted window signals."""
    habits = copy.deepcopy(state["habits"])
    declared = copy.deepcopy(state["declared"])
    window = state["window"] + 1
    verified = {run for run, result in draft["replay"].items() if result.get("status") == "replay_verified"}
    by_habit: dict[str, list] = {}
    for fire in draft["fires"]:
        by_habit.setdefault(fire["habit_id"], []).append(fire)
    signals = _learned(parameters, habits, by_habit, window, verified, mode, report)
    _declared(parameters, declared, draft["declared_fires"], window, verified, mode, report)
    return habits, declared, signals
