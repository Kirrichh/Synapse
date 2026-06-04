"""
Synapse Habit Engine.

Extracted Living Habits orchestration from Interpreter without changing
semantics. The low-level HabitRegistry/HabitEvaluator/HabitActivationEngine
remain the canonical implementation; this facade owns orchestration and
interpreter-facing callbacks.
"""
from __future__ import annotations

from typing import Any, Dict, List

class HabitEngine:
    """Facade for Living Habits runtime orchestration.

    Responsibilities:
    - current PAD snapshot for habit evaluation
    - habit durable event emission
    - habit body execution
    - LIVE-only event processing, candidate selection, fatigue/recovery
    - observer suppression proxy for the underlying activation engine

    This class deliberately uses a host getter and callbacks to avoid importing
    Interpreter and to preserve existing mutable state/reassignment semantics.
    """

    def __init__(self, host_getter, live_mode):
        self._host_getter = host_getter
        self.live_mode = live_mode

    @property
    def host(self):
        return self._host_getter()

    @property
    def suppress_observers(self) -> bool:
        h = self.host
        activation_engine = getattr(h, "habit_engine", None)
        return bool(getattr(activation_engine, "_suppress_observers", False))

    def current_pad_for_habits(self) -> Dict[str, float]:
        h = self.host
        if h.affective_states:
            state = list(h.affective_states.values())[-1]
            return dict(getattr(state, "current", {}) or {})
        return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}

    def emit_habit_event(self, event: Dict[str, Any]):
        h = self.host
        event.setdefault("event_id", h.next_event_id())
        h.execution_history.append(event)

    def execute_habit_body(self, body: List[Any]):
        h = self.host
        # Habit bodies execute from the runtime registry, not from palace.procedural.
        h.execute_block(body, h.make_environment(h.global_env))

    def process_habits_on_event(self, event: Dict[str, Any]):
        """v2.1.3-C: candidate evaluation + body execution + recovery.

        LIVE-only. REPLAY reconstructs habit lifecycle from durable events. Phase C
        closes the loop by executing body from HabitRegistry, consuming energy,
        and advancing fatigue/recovery while preserving Phase B's O(1) lookup.
        """
        h = self.host
        if h.runtime_mode != self.live_mode:
            return
        if not getattr(h, "habit_registry", None):
            return
        h._habit_event_depth += 1
        try:
            if getattr(h, "habit_engine", None):
                h.habit_engine.tick_recovery()
            candidates = h.habit_registry.get_candidates(event.get("type", ""))
            for habit in candidates:
                status = h.habit_evaluator.evaluate(habit, event)
                if status == "suppressed":
                    self.emit_habit_event({
                        "type": "habit_suppressed",
                        "habit_name": habit.name,
                        "reason": "suppress_when_matched",
                        "pad_snapshot": self.current_pad_for_habits(),
                    })
                elif status == "insufficient_energy":
                    self.emit_habit_event({
                        "type": "habit_suppressed",
                        "habit_name": habit.name,
                        "reason": "insufficient_energy",
                        "pad_snapshot": self.current_pad_for_habits(),
                    })
                elif status == "resting":
                    self.emit_habit_event({
                        "type": "habit_suppressed",
                        "habit_name": habit.name,
                        "reason": "habit_resting",
                        "pad_snapshot": self.current_pad_for_habits(),
                    })
                elif status == "candidate":
                    current_cost = h.habit_engine.current_cost(habit) if getattr(h, "habit_engine", None) else float(habit.energy_cost or 0.0)
                    self.emit_habit_event({
                        "type": "habit_candidate_suggested",
                        "habit_name": habit.name,
                        "trigger": event.get("type", "unknown"),
                        "energy_cost_current": current_cost,
                        "state": habit.state,
                        "priority": habit.priority,
                    })
                    if getattr(h, "habit_engine", None):
                        h.habit_engine.execute_candidate(habit.name, event, mode_is_live=True)
        finally:
            h._habit_event_depth -= 1
