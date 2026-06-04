"""Reactive affective threshold registry for Synapse v2.1.2.

Thresholds are deterministic, event-driven internal reactions. They evaluate
against frozen PAD snapshots and execute a restricted action body. They do not
emit observers while action bodies run.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

class ThresholdPurityViolation(RuntimeError):
    pass

@dataclass
class ThresholdRuntimeRecord:
    name: str
    condition: Any
    action: List[Any]
    for_events: int = 1
    cooldown: int = 0
    priority: str = "medium"
    declaration_index: int = 0
    recent_truths: List[bool] = field(default_factory=list)
    cooldown_remaining: int = 0
    active: bool = False

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "for_events": self.for_events,
            "cooldown": self.cooldown,
            "priority": self.priority,
            "declaration_index": self.declaration_index,
            "recent_truths": list(self.recent_truths),
            "cooldown_remaining": self.cooldown_remaining,
            "active": self.active,
        }

class ThresholdRegistry:
    def __init__(self):
        self.records: Dict[str, ThresholdRuntimeRecord] = {}
        self._order = 0
        self._processing = False

    def register(self, node: Any) -> ThresholdRuntimeRecord:
        rec = ThresholdRuntimeRecord(
            name=node.name,
            condition=node.condition,
            action=list(node.action or []),
            for_events=max(1, int(node.for_events or 1)),
            cooldown=max(0, int(node.cooldown or 0)),
            priority=str(node.priority or "medium"),
            declaration_index=self._order,
        )
        self._order += 1
        self.records[rec.name] = rec
        return rec

    def tick(self, evaluator, pad_snapshot: Dict[str, float]) -> List[ThresholdRuntimeRecord]:
        """Update windows/cooldowns and return ready thresholds.

        evaluator is a callable taking (condition, pad_snapshot) -> bool.
        """
        if self._processing:
            return []
        ready: List[ThresholdRuntimeRecord] = []
        for rec in self.records.values():
            if rec.cooldown_remaining > 0:
                rec.cooldown_remaining -= 1
            truth = bool(evaluator(rec.condition, pad_snapshot))
            rec.recent_truths.append(truth)
            if len(rec.recent_truths) > rec.for_events:
                rec.recent_truths = rec.recent_truths[-rec.for_events:]
            stable = len(rec.recent_truths) >= rec.for_events and all(rec.recent_truths[-rec.for_events:])
            if stable and rec.cooldown_remaining <= 0:
                ready.append(rec)
                rec.cooldown_remaining = rec.cooldown
                rec.active = True
            elif rec.active and not truth:
                rec.active = False
        ready.sort(key=lambda r: (-PRIORITY_ORDER.get(r.priority, 1), r.declaration_index))
        return ready

    def mark_processing(self, value: bool):
        self._processing = bool(value)
