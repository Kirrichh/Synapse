"""Synapse v1.9 intention cascade and plan weave helpers."""
from __future__ import annotations
import hashlib, json
from typing import Any, Dict, List

LEVELS = ["mission", "objective", "task", "action"]

class IntentionCascade:
    def __init__(self, name: str, levels: Dict[str, Any], *, run_id: str, position: int):
        """``position`` is the run's event index where the cascade is declared: its creation time, and with the
        run and the declaration its identity, so a replay of the run names the same cascade."""
        self.name = name
        self.levels = {k: levels.get(k) for k in LEVELS if k in levels}
        self.id = "cascade-" + hashlib.sha256(json.dumps(
            {"run": run_id, "position": position, "name": name, "levels": self.levels},
            sort_keys=True, default=str).encode()).hexdigest()[:12]
        self.created_at = position
        self.status = "planned"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "levels": self.levels, "status": self.status, "created_at": self.created_at}


def weave_plan(cascade: Dict[str, Any], participants: List[str], checkpoint_every: int = 3) -> Dict[str, Any]:
    steps = []
    levels = cascade.get("levels", {}) if isinstance(cascade, dict) else {}
    for idx, (level, value) in enumerate(levels.items(), start=1):
        steps.append({"step": idx, "level": level, "value": value, "checkpoint": idx % max(1, checkpoint_every) == 0})
    return {"status": "completed", "participants": participants, "cascade": cascade, "steps": steps, "checkpoints": [s for s in steps if s["checkpoint"]]}
