"""Stage 6 of the court: the effectiveness automaton of learned habits.

Transitions T1–T9, TC and TS as in spec part 2 §4.6, checked by the declared
decision rule: ``threshold`` tests means against thresholds; ``sprt`` runs
Wald's test on the usefulness of each counted signal, reset on every state
change, while T2 and T5 still fire after M consolidations without fires. A key
habit (no other executable habit with its expected outcome covers it) is not
archived by T9 at once: it is held and reviewed after ``N_pivot`` windows.
Forbidden jumps do not exist: every return of trust passes through probation.
Declared habits never enter the automaton.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..learning.triggers import condition_key, covers
from ..policy import sprt_decision, sprt_step
from .habit_state import EFFECTIVE, enter_state, mean


@dataclass
class _Step:
    """One habit's view of the window."""

    habit_id: str
    metadata: dict[str, Any]
    fires: list
    signals: list[float]
    participated: bool = False
    hypothesis: str | None = None


def _move(context, step: _Step, to: str, rule: str, basis: str, trust: float | None = None) -> None:
    context.report["transitions"].append({"habit_id": step.habit_id, "from": step.metadata["state"], "to": to,
                                          "rule": rule, "basis": basis})
    enter_state(step.metadata, to, context.window)
    if trust is not None:
        step.metadata["trust"] = trust
        step.metadata["context_trust"] = {step.metadata["trigger_id"]: trust}


def _observe(context, step: _Step) -> None:
    metadata, parameters = step.metadata, context.parameters
    metadata["fires_in_state"] += len(step.fires)
    metadata["fires_since_birth"] += len(step.fires)
    metadata["tasks_since_birth"] = sorted(set(metadata["tasks_since_birth"])
                                           | {fire["task_id"] or f"run:{fire['run_id']}" for fire in step.fires})
    metadata["signals_in_state"] += len(step.signals)
    metadata["signal_sum_in_state"] += sum(step.signals)
    metadata["idle_windows"] = 0 if step.fires else metadata["idle_windows"] + 1
    step.participated = bool(step.fires)
    for value in step.signals:
        metadata["sprt_llr"] += sprt_step(parameters, value)
    if context.configuration.decision_rule == "sprt":
        step.hypothesis = sprt_decision(parameters, metadata["sprt_llr"])


def _gave_up(context, step: _Step) -> str | None:
    """T2/T5 basis: H1, two participating consolidations without success, or M idle ones."""
    metadata = step.metadata
    if step.participated:
        metadata["participations_in_state"] += 1
    if step.hypothesis == "H1":
        return "SPRT accepted H1"
    if context.configuration.decision_rule == "threshold" and metadata["participations_in_state"] >= 2:
        return "participating consolidations without a promotion"
    if metadata["idle_windows"] >= context.parameters["m_idle"]:
        return f"{metadata['idle_windows']} consolidations without fires"
    return None


def _born(context, step: _Step) -> None:
    metadata, parameters = step.metadata, context.parameters
    promoted = (metadata["trust"] >= parameters["t1_trust"] and metadata["fires_since_birth"] >= parameters["t1_fires"]
                and len(metadata["tasks_since_birth"]) >= parameters["t1_tasks"])
    if context.configuration.decision_rule == "sprt":
        promoted = promoted and step.hypothesis == "H0"
    if promoted:
        _move(context, step, "active", "T1", f"trust {metadata['trust']:.4f}, {metadata['fires_since_birth']} fires "
                                             f"in {len(metadata['tasks_since_birth'])} tasks")
        return
    basis = _gave_up(context, step)
    if basis is not None:
        _move(context, step, "probation", "T2", basis)


def key_habit(habit_id, frozen, habits, legitimacy) -> bool:
    """No other executable habit with the same expected outcome provably covers this one."""
    own = frozen[habit_id]
    for other in sorted(habits):
        if other == habit_id or habits[other]["state"] not in EFFECTIVE:
            continue
        if legitimacy.get(other, {}).get("admitted") is False or other not in frozen:
            continue
        if (frozen[other]["habit"]["expected_outcome"] == own["habit"]["expected_outcome"]
                and covers(condition_key(frozen[other]["trigger"]), condition_key(own["trigger"]))):
            return False
    return True


def _active(context, step: _Step, habits, legitimacy) -> None:
    metadata, parameters = step.metadata, context.parameters
    if context.configuration.decision_rule == "threshold":
        demoted = len(step.signals) >= parameters["t3_fires"] and mean(step.signals) < parameters["t3_signal"]
    else:
        demoted = step.hypothesis == "H1"
    if demoted:
        _move(context, step, "probation", "T3", f"window signal {(mean(step.signals) or 0):.4f} over "
                                                f"{len(step.signals)} fires")
        return
    if metadata["idle_windows"] < parameters["n_t9"]:
        return
    if key_habit(step.habit_id, context.state["frozen"], habits, legitimacy):
        hold = metadata["key_hold_until"]
        if hold is None or context.window >= hold:
            metadata["key_hold_until"] = context.window + parameters["n_pivot"]
            context.report["transitions"].append({"habit_id": step.habit_id, "from": "active", "to": "active",
                                                  "rule": "T9_key_hold", "basis": "key habit kept for review"})
        return
    _move(context, step, "dormant", "T9", f"{metadata['idle_windows']} consolidations without fires")


def _probation(context, step: _Step) -> None:
    metadata, parameters = step.metadata, context.parameters
    average = metadata["signal_sum_in_state"] / metadata["signals_in_state"] if metadata["signals_in_state"] else None
    if context.configuration.decision_rule == "threshold":
        promoted = (metadata["fires_in_state"] >= parameters["t4_fires"] and average is not None
                    and average >= parameters["t4_signal"])
    else:
        promoted = step.hypothesis == "H0"
    if promoted:
        _move(context, step, "active", "T4", "SPRT accepted H0" if average is None else
              f"{metadata['fires_in_state']} fires in probation, mean {average:.4f}")
        return
    basis = _gave_up(context, step)
    if basis is not None:
        _move(context, step, "dormant", "T5", basis)


def _forced(context, step: _Step, forced) -> bool:
    rule, basis = forced[step.habit_id]
    current = step.metadata["state"]
    if rule == "TS" and current in EFFECTIVE:
        _move(context, step, "dormant", "TS", basis)
        step.metadata["superseded_by"] = next(item["successor"] for item in context.report["supersessions"]
                                              if item["predecessor"] == step.habit_id)
        return True
    if rule == "TC" and current in {"born", "active"}:
        _move(context, step, "probation", "TC", basis)
        return True
    return False


def _one(context, step: _Step, habits, forced, wakes, legitimacy) -> None:
    current = step.metadata["state"]
    if step.habit_id in wakes:
        rule, basis = wakes[step.habit_id]
        _move(context, step, "probation", rule, basis, trust=context.parameters["resurrection_trust"])
        return
    if current in EFFECTIVE:
        _observe(context, step)
    if step.habit_id in forced and _forced(context, step, forced):
        return
    if current == "born":
        _born(context, step)
    elif current == "active":
        _active(context, step, habits, legitimacy)
    elif current == "probation":
        _probation(context, step)
    elif current == "dormant":
        step.metadata["cold_windows"] += 1
        if step.metadata["cold_windows"] >= context.parameters["k_extinct"]:
            _move(context, step, "extinct", "T7", f"{step.metadata['cold_windows']} consolidations "
                                                  f"without a cold match")


def automaton(context, habits, window_signals, forced, wakes, legitimacy) -> None:
    """Apply at most one transition to every learned habit (in identity order)."""
    fires_by_habit: dict[str, list] = {}
    for fire in context.draft["fires"]:
        if fire["status"] != "excluded":
            fires_by_habit.setdefault(fire["habit_id"], []).append(fire)
    for habit_id in sorted(habits):
        step = _Step(habit_id, habits[habit_id], fires_by_habit.get(habit_id, []), window_signals.get(habit_id, []))
        _one(context, step, habits, forced, wakes, legitimacy)
