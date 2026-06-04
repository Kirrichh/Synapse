"""Synapse v1.9 Memory Palace engine."""
from __future__ import annotations
import hashlib, json, time
from typing import Any, Dict, List, Optional
from .storage_backends import InMemoryCognitiveStorage, SQLiteCognitiveStorage, PostgreSQLCognitiveStorage, CognitiveStorageBackend


def make_storage(backend: str) -> CognitiveStorageBackend:
    name = (backend or "sqlite").lower()
    if name in {"postgres", "postgresql"}:
        return PostgreSQLCognitiveStorage()
    if name.startswith("sqlite:"):
        return SQLiteCognitiveStorage(name.split(":", 1)[1])
    if name == "sqlite":
        return SQLiteCognitiveStorage(":memory:")
    return InMemoryCognitiveStorage()


class MemoryPalace:
    def __init__(self, name: str, rooms: List[str], decay_policy: Optional[Dict[str, Any]] = None, backend: str = "sqlite", consolidate_during_dream: bool = False):
        self.name = name
        self.rooms = rooms or ["episodic", "semantic", "procedural"]
        self.decay_policy = decay_policy or {}
        self.backend_name = backend
        self.backend = make_storage(backend)
        self.consolidate_during_dream = consolidate_during_dream

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "memory_palace", "name": self.name, "rooms": self.rooms, "decay_policy": self.decay_policy, "backend": self.backend_name, "consolidate_during_dream": self.consolidate_during_dream}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryPalace":
        return cls(data.get("name", "palace"), data.get("rooms", []), data.get("decay_policy", {}), data.get("backend", "sqlite"), bool(data.get("consolidate_during_dream", False)))

    def imprint(self, room: str, record: Dict[str, Any]) -> str:
        if room not in self.rooms:
            self.rooms.append(room)
        payload = dict(record)
        payload.setdefault("trace_id", self._trace_id(payload))
        payload.setdefault("confidence", 1.0)
        payload.setdefault("created_at", time.time())
        payload.setdefault("room", room)
        return self.backend.put_memory(self.name, room, payload)

    def recall(self, room: str, query: str, threshold: float = 0.0, limit: int = 10,
               affective_filter: Any = None, affective_sort: Any = None, current_event_index: Optional[int] = None) -> List[Dict[str, Any]]:
        # Pull a slightly larger candidate set before affective filtering/sorting.
        candidates = self.backend.query_memory(self.name, room, query or "", threshold, max(int(limit or 10), 1000))
        prepared = [self._apply_affective_expiration(dict(item), current_event_index) for item in candidates]
        if affective_filter is not None:
            prepared = [m for m in prepared if self._matches_affective_filter(m, affective_filter)]
        if affective_sort:
            key, direction = affective_sort
            prepared.sort(key=lambda m: abs(float(self._pad_values(m).get(key, 0.0))), reverse=(direction == "desc"))
        return prepared[: int(limit or 10)]

    def all_records(self, room: str) -> List[Dict[str, Any]]:
        return self.backend.query_memory(self.name, room, "", 0.0, 100000)

    def consolidate(self, rooms: Optional[List[str]] = None, affective_routing: Any = None, current_event_index: Optional[int] = None) -> Dict[str, Any]:
        selected = rooms or list(self.rooms)
        summary = {"palace": self.name, "rooms": selected, "status": "consolidated", "semantic_promotions": 0, "promoted": [], "kept": 0, "energy_cost": 0}
        if affective_routing:
            # MVP: route records from selected rooms in declaration order; first matching rule wins.
            for room in selected:
                for record in self.all_records(room):
                    record = self._apply_affective_expiration(dict(record), current_event_index)
                    matched = False
                    tags = []
                    for rule in affective_routing:
                        cond = getattr(rule, "condition", None)
                        if not self._matches_affective_filter(record, cond):
                            continue
                        matched = True
                        for action in getattr(rule, "actions", []) or []:
                            if getattr(action, "kind", "") == "tag":
                                tags.append(getattr(action, "tag", None))
                            elif getattr(action, "kind", "") == "keep":
                                summary["kept"] += 1
                            elif getattr(action, "kind", "") == "promote_to":
                                target = getattr(action, "target", None)
                                if target:
                                    promoted = dict(record)
                                    promoted.pop("id", None)
                                    promoted["source_room"] = room
                                    promoted["routing_tags"] = [t for t in tags if t]
                                    new_id = self.imprint(target, promoted)
                                    summary["promoted"].append({"imprint_id": record.get("id"), "new_id": new_id, "from": room, "to": target, "tag": tags[-1] if tags else None})
                                    if target == "semantic":
                                        summary["semantic_promotions"] += 1
                        break
                    if not matched:
                        summary["kept"] += 1
            summary["energy_cost"] = 10 + len(summary["promoted"])
        return summary

    def _pad_values(self, record: Dict[str, Any]) -> Dict[str, float]:
        tag = record.get("affective_tag_snapshot") or record.get("affective_tag") or {}
        if isinstance(tag, dict) and "delta" in tag and isinstance(tag["delta"], dict):
            tag = tag["delta"]
        return {"valence": float(tag.get("valence", 0.0) or 0.0), "arousal": float(tag.get("arousal", 0.0) or 0.0), "dominance": float(tag.get("dominance", 0.0) or 0.0)}

    def _apply_affective_expiration(self, record: Dict[str, Any], current_event_index: Optional[int]) -> Dict[str, Any]:
        expires = record.get("affective_expires_at_event")
        if expires is not None and current_event_index is not None and current_event_index >= int(expires):
            record["affective_tag"] = None
            record["affective_tag_snapshot"] = None
            record["affective_expired"] = True
        return record

    def _matches_affective_filter(self, record: Dict[str, Any], expr: Any) -> bool:
        if expr is None:
            return True
        kind = getattr(expr, "kind", None)
        if kind == "tagged":
            return bool(record.get("affective_tag_snapshot") or record.get("affective_tag"))
        if kind == "untagged":
            return not bool(record.get("affective_tag_snapshot") or record.get("affective_tag"))
        if kind == "and":
            return self._matches_affective_filter(record, getattr(expr, "left", None)) and self._matches_affective_filter(record, getattr(expr, "right", None))
        if kind == "comparison":
            values = self._pad_values(record)
            if not (record.get("affective_tag_snapshot") or record.get("affective_tag")):
                return False
            left = values.get(getattr(expr, "left", "valence"), 0.0)
            right = float(getattr(expr, "right", 0.0) or 0.0)
            op = getattr(expr, "op", "==")
            return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right, "==": left == right, "!=": left != right}.get(op, False)
        return True

    def _trace_id(self, payload: Dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
