"""Runtime metrics and lightweight observability helpers for Synapse."""
from __future__ import annotations

import json
import math
from synapse.version import RUNTIME_VERSION
from synapse.runtime.vm_routing import coverage_ratio as vm_coverage_ratio_from_events, fallback_audit_from_events
from typing import Any, Dict, Iterable, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class SynapseMetrics:
    """Compute a stable metrics snapshot from an Interpreter instance.

    The output intentionally uses simple JSON-safe values so it can back a
    `/metrics` endpoint, a CLI diagnostic command, or tests without requiring a
    specific telemetry vendor.
    """

    FRACTURE_EVENTS = {"identity_fractured", "identity_integrated", "subagent_terminated", "fracture_panic"}

    def __init__(self, interpreter: Any):
        self.interpreter = interpreter

    def snapshot(self) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = list(getattr(self.interpreter, "execution_history", []) or [])
        events.extend(list(getattr(self.interpreter, "telemetry_events", []) or []))
        event_counts: Dict[str, int] = {}
        for event in events:
            event_type = str(event.get("type", "unknown"))
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        fallback_audit = fallback_audit_from_events(events)

        soulprints = self._collect_soulprints()
        entropy_values = [self._soulprint_entropy(sp) for sp in soulprints]
        avg_entropy = sum(entropy_values) / len(entropy_values) if entropy_values else 0.0

        resonance_events = [e for e in events if e.get("type") == "resonance_profile_computed"]
        latest_resonance = resonance_events[-1].get("profile", {}) if resonance_events else {}
        latest_drift_vector = latest_resonance.get("drift_vector", {}) if isinstance(latest_resonance, dict) else {}
        avg_resonance_drift = self._average_abs(latest_drift_vector.values()) if latest_drift_vector else 0.0

        terminations = [e for e in events if e.get("type") == "subagent_terminated"]
        death_counts: Dict[str, int] = {}
        for event in terminations:
            death = str(event.get("death_type", "unknown"))
            death_counts[death] = death_counts.get(death, 0) + 1

        history_len = len(events)
        checkpoints = list(getattr(self.interpreter, "checkpoints", []) or [])
        last_checkpoint_offset = checkpoints[-1].get("history_offset", 0) if checkpoints else 0
        replay_lag = max(0, history_len - int(last_checkpoint_offset or 0))

        collective_events = [e for e in events if str(e.get("type", "")).startswith("collective_") or str(e.get("type", "")).startswith("distributed_consensus") or str(e.get("type", "")).startswith("swarm_fracture")]
        consensus_committed = event_counts.get("distributed_consensus_committed", 0) + event_counts.get("swarm_fracture_consensus_reached", 0) + event_counts.get("collective_dream_consensus_reached", 0)

        affective_events = [e for e in events if e.get("type") == "affective_event_tagged"]
        pad_values = []
        for e in affective_events:
            tag = e.get("tag", {}) if isinstance(e.get("tag", {}), dict) else {}
            delta = tag.get("delta", tag) if isinstance(tag, dict) else {}
            if isinstance(delta, dict):
                pad_values.append(delta)

        return {
            "version": RUNTIME_VERSION,
            "runtime_mode": getattr(getattr(self.interpreter, "runtime_mode", None), "name", str(getattr(self.interpreter, "runtime_mode", "LIVE"))),
            "events_total": history_len,
            "event_counts": event_counts,
            "actors_total": len(getattr(getattr(self.interpreter, "global_env", None), "agents", {}) or {}),
            "mailboxes_total": len(getattr(self.interpreter, "mailboxes", {}) or {}),
            "mailbox_messages_total": sum(len(v) for v in (getattr(self.interpreter, "mailboxes", {}) or {}).values()),
            "policies_total": len(getattr(self.interpreter, "policies", {}) or {}),
            "promises_total": len(getattr(self.interpreter, "promises", {}) or {}),
            "open_evolution_tickets": sum(1 for t in (getattr(self.interpreter, "evolution_tickets", {}) or {}).values() if t.get("status") == "pending"),
            "fractures_total": event_counts.get("identity_fractured", 0),
            "fracture_panics_total": event_counts.get("fracture_panic", 0),
            "subagent_death_counts": death_counts,
            "resonance_profiles_total": len(resonance_events),
            "avg_resonance_drift": round(avg_resonance_drift, 6),
            "avg_soulprint_entropy": round(avg_entropy, 6),
            "replay_lag_events": replay_lag,
            "collective_events_total": len(collective_events),
            "collective_dreams_total": event_counts.get("collective_dream_initiated", 0),
            "distributed_consensus_total": event_counts.get("distributed_consensus_committed", 0) + event_counts.get("distributed_consensus_deferred", 0),
            "swarm_fractures_total": event_counts.get("swarm_fracture_initiated", 0),
            "collective_consensus_committed_total": consensus_committed,
            "memory_palaces_total": len(getattr(self.interpreter, "memory_palaces", {}) or {}),
            "intention_cascades_total": len(getattr(self.interpreter, "intention_cascades", {}) or {}),
            "habits_total": len(getattr(self.interpreter, "habits", {}) or {}),
            "memory_imprints_total": event_counts.get("memory_imprinted", 0),
            "memory_recalls_total": event_counts.get("memory_recalled", 0),
            "memory_imprints_with_affective_tag": sum(1 for e in events if e.get("type") == "memory_imprinted" and e.get("affective_tag_snapshot")),
            "memory_affective_tags_expired": event_counts.get("memory_affective_tag_expired", 0),
            "plan_weaves_total": event_counts.get("plan_weave_completed", 0),
            "affective_states_total": len(getattr(self.interpreter, "affective_states", {}) or {}),
            "affective_events_total": event_counts.get("affective_event_tagged", 0) + event_counts.get("affective_resonance_applied", 0),
            "affective_resonance_applied_total": event_counts.get("affective_resonance_applied", 0),
            "affective_thresholds_registered_total": event_counts.get("affective_threshold_registered", 0),
            "affective_thresholds_triggered_total": event_counts.get("affective_threshold_triggered", 0),
            "affective_threshold_action_failed_total": event_counts.get("affective_threshold_action_failed", 0),
            "threshold_suspend_requests_total": event_counts.get("threshold_suspend_requested", 0),
            "avg_valence": round(self._average_abs([v.get("valence", 0.0) for v in pad_values]), 6) if pad_values else 0.0,
            "avg_arousal": round(self._average_abs([v.get("arousal", 0.0) for v in pad_values]), 6) if pad_values else 0.0,
            "avg_dominance": round(self._average_abs([v.get("dominance", 0.0) for v in pad_values]), 6) if pad_values else 0.0,
            "somatic_markers_total": len(getattr(self.interpreter, "somatic_markers", {}) or {}),
            "vm_executions_total": event_counts.get("vm_executed", 0),
            "vm_checkpoints_total": event_counts.get("vm_checkpoint_saved", 0),
            "vm_resumes_total": event_counts.get("vm_resumed", 0),
            "vm_tamper_detected_total": event_counts.get("vm_tamper_detected", 0),
            "vm_resume_sync_errors_total": event_counts.get("vm_resume_sync_error", 0),
            "vm_host_calls_total": event_counts.get("vm_host_call", 0),
            "vm_host_calls_from_cache": sum(1 for e in events if e.get("type") == "vm_host_call" and e.get("from_cache")),
            "vm_fallbacks_total": fallback_audit["vm_fallbacks_total"],
            "vm_fallback_by_node_type": fallback_audit["vm_fallback_by_node_type"],
            "vm_fallback_by_reason": fallback_audit["vm_fallback_by_reason"],
            "vm_coverage_ratio": vm_coverage_ratio_from_events(events),
            "cognitive_budget_remaining": self._latest_vm_cognitive_budget(events),
            "cognitive_operations_total": event_counts.get("vm_host_call", 0),
            "energy_pool_current": getattr(getattr(self.interpreter, "energy_pool", None), "current", 0.0) if getattr(self.interpreter, "energy_pool", None) else 0.0,
            "energy_pool_max": getattr(getattr(self.interpreter, "energy_pool", None), "max", 0.0) if getattr(self.interpreter, "energy_pool", None) else 0.0,
            "agent_rest_events_total": event_counts.get("agent_entered_rest", 0),
            "context_entries_total": event_counts.get("context_entered", 0),
            "context_exits_total": event_counts.get("context_exited", 0),
            "habits_registered_total": event_counts.get("habit_registered", 0) + event_counts.get("habit_formed", 0),
            "habit_candidates_total": event_counts.get("habit_candidate_suggested", 0),
            "habit_suppressions_total": event_counts.get("habit_suppressed", 0),
            "habit_activations_total": event_counts.get("habit_activated", 0),
            "habit_fatigued_total": event_counts.get("habit_fatigued", 0),
            "habit_recovered_total": event_counts.get("habit_recovered", 0),
            "habit_recursion_errors_total": sum(1 for e in getattr(self.interpreter, "execution_history", []) if e.get("type") == "habit_execution_failed" and "HabitRecursionError" in str(e.get("error", ""))),
            "history_hash": getattr(self.interpreter, "compute_history_hash", lambda: "")(),
        }

    def prometheus_text(self) -> str:
        metrics = self.snapshot()
        lines = ["# HELP synapse_runtime_info Synapse runtime information", "# TYPE synapse_runtime_info gauge"]
        lines.append('synapse_runtime_info{version="%s",runtime_mode="%s"} 1' % (metrics["version"], metrics["runtime_mode"]))
        scalar_names = [
            "events_total", "actors_total", "mailboxes_total", "mailbox_messages_total",
            "policies_total", "promises_total", "open_evolution_tickets", "fractures_total",
            "fracture_panics_total", "resonance_profiles_total", "avg_resonance_drift",
            "avg_soulprint_entropy", "replay_lag_events", "collective_events_total",
            "collective_dreams_total", "distributed_consensus_total", "swarm_fractures_total",
            "collective_consensus_committed_total", "memory_palaces_total", "intention_cascades_total",
            "habits_total", "memory_imprints_total", "memory_recalls_total", "memory_imprints_with_affective_tag",
            "memory_affective_tags_expired", "plan_weaves_total",
            "affective_states_total", "affective_events_total", "affective_resonance_applied_total", "affective_thresholds_registered_total", "affective_thresholds_triggered_total", "affective_threshold_action_failed_total", "threshold_suspend_requests_total", "avg_valence", "avg_arousal", "avg_dominance", "somatic_markers_total", "vm_executions_total", "vm_checkpoints_total", "vm_resumes_total", "vm_tamper_detected_total", "vm_resume_sync_errors_total", "vm_host_calls_total", "vm_host_calls_from_cache", "vm_fallbacks_total", "vm_coverage_ratio", "cognitive_budget_remaining", "cognitive_operations_total", "energy_pool_current", "energy_pool_max", "agent_rest_events_total", "context_entries_total", "context_exits_total", "habits_registered_total", "habit_candidates_total", "habit_suppressions_total", "habit_activations_total", "habit_fatigued_total", "habit_recovered_total", "habit_recursion_errors_total",
        ]
        for name in scalar_names:
            lines.append(f"# TYPE synapse_{name} gauge")
            lines.append(f"synapse_{name} {metrics.get(name, 0)}")
        lines.append("# TYPE synapse_event_count counter")
        for event_type, count in sorted(metrics.get("event_counts", {}).items()):
            lines.append('synapse_event_count{type="%s"} %s' % (event_type, count))
        lines.append("# TYPE synapse_subagent_death_count counter")
        for death_type, count in sorted(metrics.get("subagent_death_counts", {}).items()):
            lines.append('synapse_subagent_death_count{death_type="%s"} %s' % (death_type, count))
        return "\n".join(lines) + "\n"

    def _latest_vm_cognitive_budget(self, events: List[Dict[str, Any]]) -> int:
        for event in reversed(events):
            if "cognitive_budget_remaining" in event:
                try:
                    return int(event.get("cognitive_budget_remaining") or 0)
                except Exception:
                    return 0
            if event.get("type") == "vm_executed":
                result = event.get("result", {}) if isinstance(event.get("result"), dict) else {}
                try:
                    return int(result.get("cognitive_budget_remaining") or 0)
                except Exception:
                    return 0
        return 0

    def _collect_soulprints(self) -> List[Dict[str, Any]]:
        env = getattr(self.interpreter, "global_env", None)
        agents = getattr(env, "agents", {}) or {}
        return [getattr(agent, "soulprint", {}) or {} for agent in agents.values()]

    def _soulprint_entropy(self, soulprint: Dict[str, Any]) -> float:
        values = soulprint.get("values", {}) if isinstance(soulprint, dict) else {}
        nums = [_safe_float(v) for v in values.values() if isinstance(v, (int, float)) or str(v).replace(".", "", 1).isdigit()]
        total = sum(abs(n) for n in nums)
        if total <= 0:
            return 0.0
        entropy = 0.0
        for n in nums:
            p = abs(n) / total
            if p > 0:
                entropy -= p * math.log(p, 2)
        return entropy

    def _average_abs(self, values: Iterable[Any]) -> float:
        nums = [abs(_safe_float(v)) for v in values]
        return sum(nums) / len(nums) if nums else 0.0
