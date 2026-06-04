"""Synapse v2.0 Somatic Marker runtime."""
from typing import Any, Dict, List


def compute_gut_feeling(name: str, affective_events: List[Dict[str, Any]], threshold: float = 0.4, explicit: float = None) -> Dict[str, Any]:
    if explicit is not None:
        gut = float(explicit)
    else:
        recent = affective_events[-10:]
        if not recent:
            gut = 0.0
        else:
            vals = []
            for ev in recent:
                after = ev.get("after") or {}
                delta = ev.get("delta") or {}
                vals.append(float(after.get("valence", delta.get("valence", 0.0))))
            gut = sum(vals) / len(vals)
    threshold = abs(float(threshold))
    escalation = None
    if gut < -threshold:
        escalation = "fracture"
    elif gut > threshold:
        escalation = "fast_path"
    return {"name": name, "gut_feeling": round(gut, 3), "threshold": threshold, "escalate_to": escalation, "negative": gut < -threshold}
