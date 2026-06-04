"""Synapse v1.9 intention cascade and plan weave helpers."""
from __future__ import annotations
import time, uuid
from typing import Any, Dict, List

LEVELS = ["mission", "objective", "task", "action"]

class IntentionCascade:
    def __init__(self, name: str, levels: Dict[str, Any]):
        self.name = name
        self.levels = {k: levels.get(k) for k in LEVELS if k in levels}
        self.id = f"cascade-{uuid.uuid4().hex[:12]}"
        self.created_at = time.time()
        self.status = "planned"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "levels": self.levels, "status": self.status, "created_at": self.created_at}


def weave_plan(cascade: Dict[str, Any], participants: List[str], checkpoint_every: int = 3) -> Dict[str, Any]:
    steps = []
    levels = cascade.get("levels", {}) if isinstance(cascade, dict) else {}
    for idx, (level, value) in enumerate(levels.items(), start=1):
        steps.append({"step": idx, "level": level, "value": value, "checkpoint": idx % max(1, checkpoint_every) == 0})
    return {"status": "completed", "participants": participants, "cascade": cascade, "steps": steps, "checkpoints": [s for s in steps if s["checkpoint"]]}
