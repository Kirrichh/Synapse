"""Synapse v2.0 Affective Runtime.

Computational emotion is represented as PAD state: valence, arousal,
dominance. The module is deterministic and JSON-safe; it does not claim
subjective feeling. It exposes prioritization/modulation signals for the
runtime.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List
import hashlib, json, time

PAD_KEYS = ("valence", "arousal", "dominance")


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


@dataclass
class AffectiveState:
    name: str
    dimensions: Dict[str, Any] = field(default_factory=dict)
    baseline: Dict[str, float] = field(default_factory=lambda: {"valence": 0.0, "arousal": 0.4, "dominance": 0.5})
    current: Dict[str, float] = field(default_factory=dict)
    decay: float = 0.0
    decay_unit: str = "minute"
    events: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.current:
            self.current = {k: float(self.baseline.get(k, 0.0 if k == "valence" else 0.5)) for k in PAD_KEYS}
        self.current["valence"] = clamp(self.current.get("valence", 0.0), -1.0, 1.0)
        self.current["arousal"] = clamp(self.current.get("arousal", 0.0), 0.0, 1.0)
        self.current["dominance"] = clamp(self.current.get("dominance", 0.0), 0.0, 1.0)

    def apply_event(self, event_name: str, delta: Dict[str, float], duration: int = 0, trace_id: str = "") -> Dict[str, Any]:
        before = dict(self.current)
        self.current["valence"] = clamp(self.current.get("valence", 0.0) + float(delta.get("valence", 0.0)), -1.0, 1.0)
        self.current["arousal"] = clamp(self.current.get("arousal", 0.0) + float(delta.get("arousal", 0.0)), 0.0, 1.0)
        self.current["dominance"] = clamp(self.current.get("dominance", 0.0) + float(delta.get("dominance", 0.0)), 0.0, 1.0)
        tag = {
            "id": "aff-" + hashlib.sha256(json.dumps({"n": event_name, "b": before, "d": delta, "t": trace_id}, sort_keys=True, default=str).encode()).hexdigest()[:12],
            "event": event_name,
            "delta": {k: float(delta.get(k, 0.0)) for k in PAD_KEYS},
            "before": before,
            "after": dict(self.current),
            "duration": int(duration or 0),
            "trace_id": trace_id,
        }
        self.events.append(tag)
        return tag

    def decay_toward_baseline(self, steps: int = 1) -> Dict[str, float]:
        if self.decay <= 0:
            return dict(self.current)
        rate = clamp(self.decay * steps, 0.0, 1.0)
        for k in PAD_KEYS:
            self.current[k] = self.current[k] + (float(self.baseline.get(k, self.current[k])) - self.current[k]) * rate
        return dict(self.current)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "affective_state", "name": self.name, "dimensions": self.dimensions, "baseline": self.baseline, "current": self.current, "decay": self.decay, "decay_unit": self.decay_unit, "events": list(self.events)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AffectiveState":
        return cls(name=data.get("name", "mood"), dimensions=data.get("dimensions", {}), baseline=data.get("baseline", {}), current=data.get("current", {}), decay=data.get("decay", 0.0), decay_unit=data.get("decay_unit", "minute"), events=data.get("events", []))


def modulation_from_state(state: Dict[str, float]) -> Dict[str, Any]:
    valence = float(state.get("valence", 0.0))
    arousal = float(state.get("arousal", 0.0))
    dominance = float(state.get("dominance", 0.5))
    suppress, elevate = [], []
    if valence < -0.5:
        suppress.extend(["migrate", "spawn", "risky_action"])
        elevate.extend(["reflect", "dream", "fracture"])
    if arousal > 0.8:
        elevate.append("fracture")
    if dominance < 0.3:
        elevate.append("request_approval")
    return {"suppress": sorted(set(suppress)), "elevate": sorted(set(elevate)), "caution_delta": round(max(0.0, -valence) * 0.3, 3), "state": dict(state)}


def affective_bridge(user_profile: Dict[str, Any], state: Dict[str, float], dampen: Dict[str, float] = None) -> Dict[str, Any]:
    dampen = dampen or {}
    tone = (((user_profile or {}).get("aspects") or {}).get("emotional_tone") or {}).get("value", "neutral")
    arousal_shift = 0.0
    valence_shift = 0.0
    if tone in {"anxious", "urgent"}:
        arousal_shift = -abs(float(dampen.get("arousal", 0.2)))
        valence_shift = 0.1
    elif tone == "curious":
        valence_shift = 0.05
    return {"mirrored": tone, "regulation": {"valence": valence_shift, "arousal": arousal_shift}, "recommendation": "empathic regulation" if tone in {"anxious", "urgent"} else "baseline resonance"}
