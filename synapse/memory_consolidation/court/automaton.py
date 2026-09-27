"""Stage 6 of the court: the effectiveness automaton of learned habits.

Transitions T1–T9, TC, TS and TR as in spec part 2 §4.6, checked by the
declared decision rule. Each transition has one cause, and the automaton keeps
the causes apart (review R6):

* sufficient experience promotes (T1, T4): fires counted from the state's
  entry (T1: since birth, in the declared number of tasks, at the declared
  trust);
* confirmed errors demote (T2, T3, T5): under ``threshold`` the last
  ``t3_fires`` counted signals in the state average below ``t3_signal``; under
  ``sprt`` Wald's test on the usefulness of each counted signal, reset on every
  state change, accepts H1;
* disuse archives (T2, T5 after ``m_idle`` consolidations without fires; T9;
  T7 without a cold match);
* a changed basis moves a habit by the decision that changed it (TS for a
  successor, TC for a conflict, TR for a basis a reassessment no longer
  verifies), and a cold match wakes it.

Too little experience is never a cause: a habit that has not yet fired often
enough keeps its state. Under ``threshold`` the window's fires are read one by
one in the court's order (run, then event), with the trust each had reached
(the trust stage's fixed batches): a promotion or a confirmed error happens at
the fire that reaches it — confirmed errors first when both are reached at one
fire — and the fires after it count in the new state, which may move again. The
transition names that fire. One stream therefore reaches the same transitions
in the same order however it is cut into windows. Under ``sprt`` the window is
judged once. A key habit (no other executable habit with its expected outcome
covers it) is not archived by T9 at once: it is held and reviewed after
``N_pivot`` windows. Forbidden jumps do not exist: every return of trust passes
through probation. Declared habits never enter the automaton.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..learning.triggers import condition_key, covers
from ..policy import sprt_decision, sprt_step
from .habit_state import EFFECTIVE, enter_state, mean

#: Where a confirmed error or a promotion leads, by state (threshold rule).
_ON_ERRORS = {"born": ("T2", "probation"), "active": ("T3", "probation"), "probation": ("T5", "dormant")}
_ON_PROMOTION = {"born": ("T1", "active"), "probation": ("T4", "active")}


@dataclass
class _Step:
    """One habit's view of the window: its fires in the court's order."""

    habit_id: str
    metadata: dict[str, Any]
    fires: list
    hypothesis: str | None = None


def _move(context, step: _Step, to: str, rule: str, basis: str, cause: str, trust: float | None = None,
          at: dict | None = None) -> None:
    context.report["transitions"].append({"habit_id": step.habit_id, "from": step.metadata["state"], "to": to,
                                          "rule": rule, "basis": basis, "cause": cause,
                                          **({"at": at} if at is not None else {})})
    enter_state(step.metadata, to, context.window)
    if trust is not None:
        step.metadata["trust"] = trust
        step.metadata["context_trust"] = {step.metadata["trigger_id"]: trust}


def _count(parameters, metadata, fire) -> None:
    """One fire counted in the current state, with the tail the confirmed-error rule reads."""
    metadata["fires_in_state"] += 1
    if fire["status"] == "counted":
        metadata["signals_in_state"] += 1
        metadata["signal_sum_in_state"] += fire["signal"]
        metadata["tail"] = (list(metadata.get("tail", [])) + [fire["signal"]])[-parameters["t3_fires"]:]
        metadata["sprt_llr"] += sprt_step(parameters, fire["signal"])


def _since_birth(metadata, fire) -> None:
    metadata["fires_since_birth"] += 1
    metadata["tasks_since_birth"] = sorted(set(metadata["tasks_since_birth"])
                                           | {fire["task_id"] or f"run:{fire['run_id']}"})


def _trust_track(context, step: _Step) -> tuple[float, dict[tuple[str, str], float]]:
    """The habit's trust as the window's fires began, and the trust after each fire that completed an update."""
    own = [item for item in context.report["trust_decisions"]
           if item["habit_id"] == step.habit_id and item["trigger_id"] == step.metadata["trigger_id"]]
    current = own[0]["trust_old"] if own else step.metadata["trust"]
    inside = {(fire["run_id"], fire["event_id"]) for fire in step.fires}
    after: dict[tuple[str, str], float] = {}
    for item in own:
        last = (item["events"][-1]["run_id"], item["events"][-1]["event_id"])
        if last in inside:
            after[last] = item["trust_new"]
        elif not after:
            current = item["trust_new"]  # completed by earlier evidence: in force before the first fire
    return current, after


def _reached(parameters, metadata, trust) -> tuple[str, str] | None:
    """A confirmed error or a promotion the state's counters hold now."""
    tail = metadata.get("tail", [])
    if len(tail) >= parameters["t3_fires"] and mean(tail) < parameters["t3_signal"]:
        return "errors", f"the last {len(tail)} counted signals average {mean(tail):.4f}"
    state = metadata["state"]
    if state == "born" and (trust >= parameters["t1_trust"] and metadata["fires_since_birth"] >= parameters["t1_fires"]
                            and len(metadata["tasks_since_birth"]) >= parameters["t1_tasks"]):
        return "promotion", (f"trust {trust:.4f}, {metadata['fires_since_birth']} fires in "
                             f"{len(metadata['tasks_since_birth'])} tasks")
    if state == "probation" and metadata["signals_in_state"] and metadata["fires_in_state"] >= parameters["t4_fires"]:
        average = metadata["signal_sum_in_state"] / metadata["signals_in_state"]
        if average >= parameters["t4_signal"]:
            return "promotion", f"{metadata['fires_in_state']} fires in probation, mean {average:.4f}"
    return None


def _read_fires(context, step: _Step) -> bool:
    """The threshold rule: every transition the window's fires reach, at the fire that reaches it."""
    metadata, parameters = step.metadata, context.parameters
    trust, after = _trust_track(context, step)
    moved = False
    for index in range(-1, len(step.fires)):
        if metadata["state"] not in EFFECTIVE:
            break  # Archived: not loaded any more.
        at = None
        if index >= 0:
            fire = step.fires[index]
            _since_birth(metadata, fire)
            _count(parameters, metadata, fire)
            trust = after.get((fire["run_id"], fire["event_id"]), trust)
            at = {"run_id": fire["run_id"], "event_id": fire["event_id"]}
        reached = _reached(parameters, metadata, trust)
        if reached is None:
            continue
        kind, basis = reached
        rule, to = (_ON_ERRORS if kind == "errors" else _ON_PROMOTION)[metadata["state"]]
        _move(context, step, to, rule, basis, "confirmed_errors" if kind == "errors" else "sufficient_experience",
              at=at)
        moved = True
    metadata["idle_windows"] = 0 if step.fires else metadata["idle_windows"] + 1
    return moved


def _read_window(context, step: _Step) -> None:
    """The sprt rule: the window's fires are counted, and Wald's test is read once."""
    metadata, parameters = step.metadata, context.parameters
    for fire in step.fires:
        _since_birth(metadata, fire)
        _count(parameters, metadata, fire)
    metadata["idle_windows"] = 0 if step.fires else metadata["idle_windows"] + 1
    step.hypothesis = sprt_decision(parameters, metadata["sprt_llr"])


def _disused(context, step: _Step) -> str | None:
    if step.metadata["idle_windows"] >= context.parameters["m_idle"]:
        return f"{step.metadata['idle_windows']} consolidations without fires"
    return None


def _born(context, step: _Step) -> None:
    metadata, parameters = step.metadata, context.parameters
    if step.hypothesis == "H1":
        _move(context, step, "probation", "T2", "SPRT accepted H1", "confirmed_errors")
        return
    if step.hypothesis == "H0" and (metadata["trust"] >= parameters["t1_trust"]
                                    and metadata["fires_since_birth"] >= parameters["t1_fires"]
                                    and len(metadata["tasks_since_birth"]) >= parameters["t1_tasks"]):
        _move(context, step, "active", "T1", "SPRT accepted H0", "sufficient_experience")
        return
    disused = _disused(context, step)
    if disused is not None:
        _move(context, step, "probation", "T2", disused, "disuse")


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
    if step.hypothesis == "H1":
        _move(context, step, "probation", "T3", "SPRT accepted H1", "confirmed_errors")
        return
    if metadata["idle_windows"] < parameters["n_t9"]:
        return
    if key_habit(step.habit_id, context.state["frozen"], habits, legitimacy):
        hold = metadata["key_hold_until"]
        if hold is None or context.window >= hold:
            metadata["key_hold_until"] = context.window + parameters["n_pivot"]
            context.report["transitions"].append({"habit_id": step.habit_id, "from": "active", "to": "active",
                                                  "rule": "T9_key_hold", "basis": "key habit kept for review",
                                                  "cause": "disuse"})
        return
    _move(context, step, "dormant", "T9", f"{metadata['idle_windows']} consolidations without fires", "disuse")


def _probation(context, step: _Step) -> None:
    if step.hypothesis == "H1":
        _move(context, step, "dormant", "T5", "SPRT accepted H1", "confirmed_errors")
        return
    if step.hypothesis == "H0":
        _move(context, step, "active", "T4", "SPRT accepted H0", "sufficient_experience")
        return
    disused = _disused(context, step)
    if disused is not None:
        _move(context, step, "dormant", "T5", disused, "disuse")


def _forced(context, step: _Step, forced) -> bool:
    rule, basis = forced[step.habit_id]
    current = step.metadata["state"]
    if rule == "TS" and current in EFFECTIVE:
        _move(context, step, "dormant", "TS", basis, "changed_basis")
        step.metadata["superseded_by"] = next(item["successor"] for item in context.report["supersessions"]
                                              if item["predecessor"] == step.habit_id)
        return True
    if rule == "TC" and current in {"born", "active"}:
        _move(context, step, "probation", "TC", basis, "conflict")
        return True
    if rule == "TR" and current in EFFECTIVE:
        _move(context, step, "dormant", "TR", basis, "changed_basis")
        return True
    return False


def forced_transitions(context, habits, forced) -> None:
    """Only the forced transitions, for a decision that observed no window (a reassessment)."""
    for habit_id in sorted(forced):
        if habit_id in habits:
            _forced(context, _Step(habit_id, habits[habit_id], []), forced)


def _one(context, step: _Step, habits, forced, wakes, legitimacy) -> None:
    current = step.metadata["state"]
    if step.habit_id in wakes:
        rule, basis = wakes[step.habit_id]
        _move(context, step, "probation", rule, basis, "cold_match", trust=context.parameters["resurrection_trust"])
        return
    moved = False
    if current in EFFECTIVE:
        if context.configuration.decision_rule == "threshold":
            moved = _read_fires(context, step)
        else:
            _read_window(context, step)
    if step.habit_id in forced and _forced(context, step, forced):
        return
    if moved:
        return
    state = step.metadata["state"]
    if state == "born":
        _born(context, step)
    elif state == "active":
        _active(context, step, habits, legitimacy)
    elif state == "probation":
        _probation(context, step)
    elif state == "dormant":
        step.metadata["cold_windows"] += 1
        if step.metadata["cold_windows"] >= context.parameters["k_extinct"]:
            _move(context, step, "extinct", "T7", f"{step.metadata['cold_windows']} consolidations "
                                                  f"without a cold match", "disuse")


def automaton(context, habits, forced, wakes, legitimacy) -> None:
    """Apply the transitions every learned habit reaches in this window (in identity order)."""
    fires_by_habit: dict[str, list] = {}
    for fire in sorted(context.draft["fires"], key=lambda item: (item["run_id"], item["event_id"])):
        if fire["status"] != "excluded":
            fires_by_habit.setdefault(fire["habit_id"], []).append(fire)
    for habit_id in sorted(habits):
        _one(context, _Step(habit_id, habits[habit_id], fires_by_habit.get(habit_id, [])), habits, forced, wakes,
             legitimacy)
