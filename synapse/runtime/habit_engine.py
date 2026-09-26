"""
Synapse Habit Engine.

Extracted Living Habits orchestration from Interpreter without changing
semantics. The low-level HabitRegistry/HabitEvaluator/HabitActivationEngine
remain the canonical implementation; this facade owns orchestration and
interpreter-facing callbacks.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: Trust a habit is ranked with before the court has observed it.
UNOBSERVED_CONTEXT_TRUST = 0.5
#: Two best candidates closer than this in context trust announce a conflict.
CONFLICT_WARNING_GAP = 0.05


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
        h.record_history_event(event)

    def react(self, event: Dict[str, Any], run_body: Callable[[Any, Any], Dict[str, Any]]) -> Dict[str, Any]:
        """Typed reaction to one event requiring it (memory spec part 3 §3.3).

        Candidates come from the subscription on the event type, then context,
        ``when`` and ``not_when`` in declared order. Applicable candidates are
        ordered by layer (declared before learned), priority class, context
        trust, configured similarity and finally identity, so equal ranks never
        produce an arbitrary winner. The best candidate that passes the Living
        Habits checks is executed once. With none applicable the event records a
        near miss naming its one failed condition, or a miss. The runtime never
        evaluates the result; it records facts.
        """
        from ..habit import PRIORITY_RANK

        h = self.host
        session = h.memory_session
        applicable: List[Dict[str, Any]] = []
        near: List[Dict[str, Any]] = []
        for habit, trigger in h.habit_registry.typed_candidates(event.get("type", "")):
            status, failed = trigger.match(event)
            if status == "applicable":
                score = session.veto_similarity(trigger.trigger_id, event) if session is not None else None
                if score is not None and score.get("veto"):
                    near.append({"habit": habit, "trigger": trigger, "failed": {
                        "field": "semantic_veto", "op": "<", "value": score["threshold"], "actual": score["score"]}})
                    continue
                trust = float(habit.context_trust.get(trigger.trigger_id, UNOBSERVED_CONTEXT_TRUST))
                applicable.append({"habit": habit, "trigger": trigger, "trust": trust,
                                   "score": None if score is None else score["score"],
                                   "key": (habit.layer, PRIORITY_RANK.get(habit.priority, PRIORITY_RANK["medium"]),
                                           -trust, -(score["score"] if score is not None else 0.0), habit.habit_id)})
            elif status == "near_miss":
                near.append({"habit": habit, "trigger": trigger, "failed": failed})
        applicable.sort(key=lambda item: item["key"])
        warning = (len(applicable) > 1 and applicable[0]["habit"].layer == applicable[1]["habit"].layer
                   and abs(applicable[0]["trust"] - applicable[1]["trust"]) < CONFLICT_WARNING_GAP)
        suppressed: List[str] = []
        for position, candidate in enumerate(applicable):
            habit = candidate["habit"]
            reason = self._living_habits_block(habit)
            if reason is not None:
                suppressed.append(habit.habit_id)
                self.emit_habit_event({"type": "habit_suppressed", "habit_name": habit.name,
                                       "habit_id": habit.habit_id, "reason": reason,
                                       "trigger_event_id": event.get("event_id")})
                continue
            runner = applicable[position + 1]["habit"] if position + 1 < len(applicable) else None
            trigger = candidate["trigger"]

            def activated(result, habit=habit, trigger=trigger, candidate=candidate, runner=runner, position=position):
                self.emit_habit_event(self._bound({
                    "type": "habit_activated", "habit_name": habit.name, "habit_id": habit.habit_id,
                    "trigger_id": trigger.trigger_id, "layer": habit.layer,
                    "trigger_event_id": event.get("event_id"),
                    "semantic_score_micros": None if candidate["score"] is None
                    else int(round(candidate["score"] * 1_000_000)),
                    "outcome": result["outcome"], "recovered": result["recovered"],
                    "action_refs": result["action_refs"],
                    # Why it applied, and why its body stopped if it did (refinement §12, §13).
                    "matched": trigger.explain(event), "detail": result.get("detail"),
                    "conflict_warning": bool(warning and position == 0),
                    "runner_up_habit_id": runner.habit_id if warning and position == 0 and runner else None,
                    "activation_count": habit.activation_count}, event))

            result = h.habit_engine.execute_typed(habit, lambda: run_body(habit, trigger), activated)
            if result is None:
                continue
            return {"kind": "activated", "habit": habit, "trigger": trigger, "result": result}
        if near:
            first = near[0]
            self.emit_habit_event(self._bound({
                "type": "habit_near_miss", "habit_id": first["habit"].habit_id, "layer": first["habit"].layer,
                "trigger_id": first["trigger"].trigger_id, "failed_condition": first["failed"],
                "trigger_event_id": event.get("event_id"), "outcome": None, "suppressed": suppressed}, event))
            return {"kind": "near_miss"}
        self.emit_habit_event(self._bound({
            "type": "habit_miss", "event_type": event.get("type"), "trigger_event_id": event.get("event_id"),
            "outcome": None, "suppressed": suppressed}, event))
        return {"kind": "miss"}

    def _bound(self, record: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        for name in ("task_id", "segment_marker_id", "off_plan"):
            if name in event:
                record[name] = event[name]
        return record

    def _living_habits_block(self, habit) -> Optional[str]:
        from ..habit import HabitState

        h = self.host
        if habit.slow_only:
            return "slow_only"
        if habit.state == HabitState.RESTING.value:
            return "habit_resting"
        pool = h.energy_pool
        if pool is not None and pool.current < h.habit_engine.current_cost(habit):
            return "insufficient_energy"
        return None

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
        if h.runtime_mode != self.live_mode and not h.runtime.replay.get_verified_replay():
            # Legacy replay reconstructs habit lifecycle from recorded events; a
            # verified durable replay re-executes it and checks every event.
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
