"""Production hardening utilities: history integrity and stress harness."""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Dict, Iterable, List, Optional


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def hash_event_chain(events: Iterable[Dict[str, Any]], seed: str = "synapse-v1.7") -> List[Dict[str, Any]]:
    """Return a tamper-evident hash chain for an event stream."""
    prev = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    chain = []
    for idx, event in enumerate(events):
        payload = canonical_json({"idx": idx, "prev": prev, "event": event})
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        chain.append({"idx": idx, "type": event.get("type"), "hash": digest, "prev": prev})
        prev = digest
    return chain


def verify_event_chain(events: Iterable[Dict[str, Any]], chain: Iterable[Dict[str, Any]], seed: str = "synapse-v1.7") -> bool:
    return hash_event_chain(list(events), seed=seed) == list(chain)


class ChaosResult(dict):
    pass


class RuntimeStressHarness:
    """Deterministic chaos harness for runtime event streams.

    It does not try to fuzz Python internals. It mutates high-level durable data
    structures to test whether integrity checks, storage adapters and replay
    tooling surface suspicious states.
    """

    def __init__(self, seed: int = 0):
        self.random = random.Random(seed)
        self.seed = seed

    def drop_random_event(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not events:
            return []
        clone = [dict(e) for e in events]
        idx = self.random.randrange(len(clone))
        del clone[idx]
        return clone

    def duplicate_random_event(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not events:
            return []
        clone = [dict(e) for e in events]
        idx = self.random.randrange(len(clone))
        clone.insert(idx, dict(clone[idx]))
        return clone

    def run_integrity_scenarios(self, events: List[Dict[str, Any]]) -> ChaosResult:
        baseline_chain = hash_event_chain(events)
        dropped = self.drop_random_event(events)
        duplicated = self.duplicate_random_event(events)
        return ChaosResult({
            "seed": self.seed,
            "events_total": len(events),
            "baseline_valid": verify_event_chain(events, baseline_chain),
            "drop_detected": not verify_event_chain(dropped, baseline_chain),
            "duplicate_detected": not verify_event_chain(duplicated, baseline_chain),
        })
