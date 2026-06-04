"""Synapse Living Habits runtime support.

v2.1.3-A introduced event-based energy pools and durable context scopes.
v2.1.3-B adds metadata-only HabitRegistry activation/suppression logic:
O(1) event subscriptions, suppress priority, OR/AND condition semantics, and
replay-safe candidate/suppression events. v2.1.3-C closes the loop with body execution, energy consumption, fatigue/recovery, and recursion locks.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from collections import defaultdict


class HabitState(Enum):
    FRESH = "FRESH"
    FATIGUED = "FATIGUED"
    RESTING = "RESTING"


class AgentMode(Enum):
    NORMAL = "NORMAL"
    REST = "REST"


class HabitError(Exception):
    pass


class HabitRecursionError(HabitError):
    pass


class InsufficientEnergyError(HabitError):
    pass


class ContextStackError(HabitError):
    pass


@dataclass
class EnergyPool:
    max: int
    initial: Optional[int] = None
    recharge_amount: int = 0
    recharge_every: int = 1
    rest_threshold: int = 0
    hysteresis_margin: int = 5
    current: float = field(init=False)
    mode: AgentMode = field(init=False, default=AgentMode.NORMAL)
    events_counter: int = field(init=False, default=0)

    def __post_init__(self):
        self.max = int(self.max)
        self.initial = self.max if self.initial is None else int(self.initial)
        self.recharge_amount = int(self.recharge_amount)
        self.recharge_every = max(1, int(self.recharge_every))
        self.rest_threshold = int(self.rest_threshold)
        self.hysteresis_margin = max(0, int(self.hysteresis_margin))
        self.current = float(min(self.max, max(0, self.initial)))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max": self.max,
            "initial": self.initial,
            "recharge_amount": self.recharge_amount,
            "recharge_every": self.recharge_every,
            "rest_threshold": self.rest_threshold,
            "hysteresis_margin": self.hysteresis_margin,
            "current": self.current,
            "mode": self.mode.value,
            "events_counter": self.events_counter,
        }

    def on_event(self) -> List[Dict[str, Any]]:
        emitted: List[Dict[str, Any]] = []
        self.events_counter += 1
        if self.recharge_amount and self.events_counter % self.recharge_every == 0:
            before = self.current
            self.current = min(float(self.max), self.current + float(self.recharge_amount))
            if self.current != before:
                emitted.append({
                    "type": "energy_pool_recharged",
                    "amount": self.recharge_amount,
                    "energy_pool_after": self.current,
                    "events_counter": self.events_counter,
                })
        if self.mode == AgentMode.NORMAL and self.current < self.rest_threshold:
            self.mode = AgentMode.REST
            emitted.append({"type": "agent_entered_rest", "energy_pool_current": self.current, "rest_threshold": self.rest_threshold})
        elif self.mode == AgentMode.REST and self.current >= (self.rest_threshold + self.hysteresis_margin):
            self.mode = AgentMode.NORMAL
            emitted.append({"type": "agent_exited_rest", "energy_pool_current": self.current, "hysteresis_applied": True})
        return emitted

    def try_consume(self, cost: float) -> bool:
        if cost <= 0:
            return True
        if self.current >= cost:
            self.current -= float(cost)
            return True
        return False


class ContextTracker:
    def __init__(self):
        self.stack: List[str] = []

    @property
    def current(self) -> Optional[str]:
        return self.stack[-1] if self.stack else None

    def enter_event(self, label: str) -> Dict[str, Any]:
        self.stack.append(str(label))
        return {"type": "context_entered", "label": str(label)}

    def exit_event(self, label: str) -> Dict[str, Any]:
        label = str(label)
        if not self.stack or self.stack[-1] != label:
            raise ContextStackError(f"Context mismatch: expected {label!r}, got stack {self.stack!r}")
        self.stack.pop()
        return {"type": "context_exited", "label": label}


@dataclass
class HabitRuntimeRecord:
    name: str
    activate_when: List[Any]
    suppress_when: List[Any]
    energy_cost: float = 0.0
    fatigue_threshold: int = 5
    fatigue_multiplier: float = 1.0
    fatigue_rest_events: int = 0
    priority: str = "medium"
    body: List[Any] = field(default_factory=list)
    promote_to: Any = None
    state: str = "FRESH"
    activation_count: int = 0
    events_since_activation: int = 0
    _subscribed_events: Set[str] = field(default_factory=set, init=False)


class HabitRegistry:
    """Event-driven registry: event_type -> habits. No linear all-habit scan on events."""
    def __init__(self):
        self._subscriptions: Dict[str, List[HabitRuntimeRecord]] = defaultdict(list)
        self._all_habits: List[HabitRuntimeRecord] = []
        self._by_name: Dict[str, HabitRuntimeRecord] = {}

    def register(self, habit: HabitRuntimeRecord) -> None:
        self._all_habits.append(habit)
        self._by_name[habit.name] = habit
        self._compute_subscriptions(habit)

    def _compute_subscriptions(self, habit: HabitRuntimeRecord) -> None:
        events: Set[str] = set()
        for cond in list(habit.activate_when or []) + list(habit.suppress_when or []):
            if hasattr(cond, "name"):
                events.add("affective_threshold_triggered")
            elif hasattr(cond, "context") or hasattr(cond, "pad_conditions"):
                if getattr(cond, "context", None):
                    events.add("context_entered")
                if getattr(cond, "pad_conditions", None):
                    events.add("*")
            elif isinstance(cond, dict):
                if "context" in cond:
                    events.add("context_entered")
                if cond.get("pad_checks"):
                    events.add("*")
        habit._subscribed_events = events or {"*"}
        for evt in habit._subscribed_events:
            self._subscriptions[evt].append(habit)

    def get_candidates(self, event_type: str) -> List[HabitRuntimeRecord]:
        candidates: List[HabitRuntimeRecord] = []
        candidates.extend(self._subscriptions.get(event_type, []))
        candidates.extend(self._subscriptions.get("*", []))
        seen: Set[str] = set()
        unique: List[HabitRuntimeRecord] = []
        priority_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        for h in candidates:
            if h.name not in seen:
                seen.add(h.name)
                unique.append(h)
        unique.sort(key=lambda h: priority_map.get(h.priority, 2), reverse=True)
        return unique

    @property
    def all_habits(self) -> List[HabitRuntimeRecord]:
        return list(self._all_habits)

    def get(self, name: str) -> Optional[HabitRuntimeRecord]:
        return self._by_name.get(name)

    def names(self) -> List[str]:
        return list(self._by_name.keys())


class HabitEvaluator:
    """Phase B evaluator: no body execution, no energy consumption."""
    def __init__(self, context_tracker: ContextTracker, affective_state_getter: Callable[[], Dict[str, float]], energy_pool_getter: Callable[[], Optional[EnergyPool]]):
        self.context = context_tracker
        self.get_pad = affective_state_getter
        self.get_energy_pool = energy_pool_getter

    def evaluate(self, habit: HabitRuntimeRecord, trigger_event: Dict[str, Any]) -> str:
        if self._check_conditions(habit.suppress_when, trigger_event):
            return "suppressed"
        if not self._check_conditions(habit.activate_when, trigger_event):
            return "inactive"
        if habit.state == HabitState.RESTING.value:
            return "resting"
        cost = habit.energy_cost * (habit.fatigue_multiplier if habit.state == HabitState.FATIGUED.value else 1.0)
        pool = self.get_energy_pool()
        if pool is not None and pool.current < cost:
            return "insufficient_energy"
        return "candidate"

    def _check_conditions(self, conditions: List[Any], event: Dict[str, Any]) -> bool:
        if not conditions:
            return False
        return any(self._evaluate_single(cond, event) for cond in conditions)

    def _evaluate_single(self, cond: Any, event: Dict[str, Any]) -> bool:
        if hasattr(cond, "name"):
            return event.get("type") == "affective_threshold_triggered" and event.get("threshold") == cond.name
        context = None
        pad_conditions = []
        if hasattr(cond, "context") or hasattr(cond, "pad_conditions"):
            context = getattr(cond, "context", None)
            pad_conditions = getattr(cond, "pad_conditions", []) or []
        elif isinstance(cond, dict):
            context = cond.get("context")
            pad_conditions = cond.get("pad_checks", []) or []
        if context is not None and self.context.current != context:
            return False
        pad = self.get_pad() or {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
        aliases = {"pleasure": "valence", "energy": "arousal", "control": "dominance"}
        for key, op, val in pad_conditions:
            key = aliases.get(str(key), str(key))
            left = float(pad.get(key, 0.0) or 0.0)
            right = float(val)
            if not self._compare(left, str(op), right):
                return False
        return True if (context is not None or pad_conditions) else False

    @staticmethod
    def _compare(left: float, op: str, right: float) -> bool:
        return {
            ">": left > right,
            "<": left < right,
            ">=": left >= right,
            "<=": left <= right,
            "==": left == right,
            "!=": left != right,
            "gt": left > right,
            "lt": left < right,
            "gte": left >= right,
            "lte": left <= right,
        }.get(op, False)


class HabitActivationEngine:
    """v2.1.3-C executor for habit bodies.

    The registry/evaluator from Phase B still owns candidate selection. This engine
    only executes selected candidates in LIVE mode, enforces recursion locks,
    consumes energy atomically, and advances fatigue/recovery state.
    """

    MAX_DEPTH = 3

    def __init__(self, registry: HabitRegistry, emit_fn: Callable[[Dict[str, Any]], None], energy_pool_getter: Callable[[], Optional[EnergyPool]], body_executor: Callable[[List[Any]], Any]):
        self.registry = registry
        self._emit = emit_fn
        self.get_energy_pool = energy_pool_getter
        self.body_executor = body_executor
        self._active_habits: Set[str] = set()
        self._habit_depth = 0
        self._suppress_observers = False

    def current_cost(self, habit: HabitRuntimeRecord) -> float:
        cost = float(habit.energy_cost or 0.0)
        if habit.state == HabitState.FATIGUED.value:
            cost *= float(habit.fatigue_multiplier or 1.0)
        return cost

    def execute_candidate(self, habit_name: str, trigger_event: Dict[str, Any], mode_is_live: bool = True) -> bool:
        if not mode_is_live:
            return False
        habit = self.registry.get(habit_name)
        if habit is None:
            return False
        if habit.name in self._active_habits:
            self._emit({
                "type": "habit_execution_failed",
                "habit_name": habit.name,
                "error": "HabitRecursionError: self-lock active",
                "activation_count_unchanged": True,
            })
            return False
        if self._habit_depth >= self.MAX_DEPTH:
            self._emit({
                "type": "habit_execution_failed",
                "habit_name": habit.name,
                "error": f"HabitRecursionError: depth exceeded max_habit_depth={self.MAX_DEPTH}",
                "activation_count_unchanged": True,
            })
            return False
        if habit.state == HabitState.RESTING.value:
            self._emit({"type": "habit_suppressed", "habit_name": habit.name, "reason": "habit_resting"})
            return False

        cost = self.current_cost(habit)
        pool = self.get_energy_pool()
        consumed = False
        if pool is not None:
            if not pool.try_consume(cost):
                self._emit({"type": "habit_suppressed", "habit_name": habit.name, "reason": "insufficient_energy"})
                return False
            consumed = cost > 0

        self._active_habits.add(habit.name)
        self._habit_depth += 1
        self._suppress_observers = True
        try:
            if habit.body:
                self.body_executor(habit.body)
            habit.activation_count += 1
            habit.events_since_activation = 0
            self._emit({
                "type": "habit_activated",
                "habit_name": habit.name,
                "activation_count": habit.activation_count,
                "energy_spent": cost,
                "energy_pool_after": pool.current if pool is not None else None,
            })
            if habit.fatigue_threshold and habit.activation_count >= int(habit.fatigue_threshold) and habit.state != HabitState.FATIGUED.value:
                habit.state = HabitState.FATIGUED.value
                self._emit({
                    "type": "habit_fatigued",
                    "habit_name": habit.name,
                    "activation_count": habit.activation_count,
                    "energy_cost_current": self.current_cost(habit),
                    "require_rest_events": int(habit.fatigue_rest_events or 0),
                })
            return True
        except Exception as exc:
            if consumed and pool is not None:
                pool.current = min(float(pool.max), pool.current + cost)
            self._emit({
                "type": "habit_execution_failed",
                "habit_name": habit.name,
                "error": str(exc),
                "activation_count_unchanged": True,
            })
            return False
        finally:
            self._active_habits.discard(habit.name)
            self._habit_depth -= 1
            self._suppress_observers = False

    def tick_recovery(self) -> None:
        for habit in self.registry.all_habits:
            if habit.name in self._active_habits:
                continue
            if habit.state == HabitState.FATIGUED.value:
                habit.events_since_activation += 1
                if habit.fatigue_rest_events and habit.events_since_activation == 1:
                    self._emit({
                        "type": "habit_resting",
                        "habit_name": habit.name,
                        "rest_remaining_events": int(habit.fatigue_rest_events),
                    })
                if habit.fatigue_rest_events and habit.events_since_activation >= int(habit.fatigue_rest_events):
                    habit.state = HabitState.FRESH.value
                    habit.events_since_activation = 0
                    self._emit({
                        "type": "habit_recovered",
                        "habit_name": habit.name,
                        "new_state": "FRESH",
                    })



def form_habit(fields: Dict[str, Any]) -> Dict[str, Any]:
    habit_id = f"habit-{uuid.uuid4().hex[:12]}"
    return {
        "id": habit_id,
        "status": "promoted",
        "fields": fields,
        "activation_trigger": fields.get("activation_condition"),
        "energy_cost": fields.get("energy_cost", 1.0),
        "created_at": time.time(),
    }
