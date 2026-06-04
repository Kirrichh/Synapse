from __future__ import annotations

import copy
import uuid
from typing import Any, Callable, Dict, List, Optional

from synapse.ast import *
from synapse.affective import AffectiveState, modulation_from_state, clamp
from synapse.threshold import ThresholdPurityViolation


class AffectiveRuntime:
    """Affective runtime helper extracted from Interpreter.

    The interpreter remains the AST orchestrator and owner of mutable state.  This
    engine receives a host object by reference and performs a 1:1 lift-and-shift
    of the existing affective semantics: PAD state, thresholds, modulation, and
    atomic affective resonance.  It does not import Interpreter, preventing
    circular module dependencies.
    """

    def __init__(
        self,
        *,
        host_getter: Callable[[], Any],
        live_mode: Any,
        replay_mode: Any,
        affective_isolation_exception: type[Exception],
        orphaned_identity_exception: type[Exception],
        frozen_mood_factory: Callable[[Optional[Dict[str, Any]]], Any],
    ):
        self.get_host = host_getter
        self.live_mode = live_mode
        self.replay_mode = replay_mode
        self.AffectiveIsolationViolation = affective_isolation_exception
        self.OrphanedIdentityException = orphaned_identity_exception
        self.make_frozen_mood = frozen_mood_factory

    # --- v2.1.2 Reactive Affective Thresholds ---
    def evaluate_affective_threshold_def(self, node: AffectiveThresholdDef, env: Any) -> Dict[str, Any]:
        h = self.get_host()
        self.validate_threshold_action_purity(node.action or [])
        rec = h.threshold_registry.register(node)
        event = {
            "type": "affective_threshold_registered",
            "threshold": node.name,
            "for_events": rec.for_events,
            "cooldown": rec.cooldown,
            "priority": rec.priority,
        }
        h.threshold_audit.append(event)
        h.execution_history.append(event)
        env.define(node.name, {"type": "threshold", "name": node.name})
        return event

    def validate_threshold_action_purity(self, body: List[Node]):
        forbidden = []
        def walk(n: Node):
            if n is None:
                return
            if isinstance(n, SendStmt):
                forbidden.append("send")
            elif isinstance(n, MigrateStmt):
                forbidden.append("migrate")
            elif isinstance(n, DeclareIntentStmt):
                forbidden.append("declare intent")
            elif isinstance(n, ImprintStmt):
                forbidden.append("imprint")
            elif isinstance(n, GovernedMemoryWrite):
                forbidden.append("memory.write")
            elif isinstance(n, GovernedMemoryForget):
                forbidden.append("memory.forget")
            elif isinstance(n, MemoryAccess) and getattr(n, "operation", "read") in {"write", "forget", "clear"}:
                forbidden.append(f"memory.{n.operation}")
            # Recurse through common node containers.
            for attr in ("body", "then_body", "else_body", "action", "statements"):
                child = getattr(n, attr, None)
                if isinstance(child, list):
                    for item in child:
                        walk(item)
            for attr in ("expr", "value", "condition", "request"):
                child = getattr(n, attr, None)
                if isinstance(child, Node):
                    walk(child)
        for stmt in body:
            walk(stmt)
        if forbidden:
            raise ThresholdPurityViolation("Threshold action forbids: " + ", ".join(sorted(set(forbidden))))

    def _eval_affective_condition(self, condition: Node, pad_snapshot: Dict[str, float]) -> bool:
        if isinstance(condition, AffectiveFilterExpr):
            if condition.kind == "tagged":
                return True
            if condition.kind == "untagged":
                return False
            if condition.kind == "and":
                return self._eval_affective_condition(condition.left, pad_snapshot) and self._eval_affective_condition(condition.right, pad_snapshot)
            if condition.kind == "comparison":
                key = str(condition.left)
                aliases = {"pleasure": "valence", "energy": "arousal", "control": "dominance"}
                key = aliases.get(key, key)
                left = float(pad_snapshot.get(key, 0.0))
                right = float(condition.right)
                op = str(condition.op)
                return {
                    "<": left < right,
                    ">": left > right,
                    "<=": left <= right,
                    ">=": left >= right,
                    "==": left == right,
                    "!=": left != right,
                    "eq": left == right,
                    "neq": left != right,
                    "lt": left < right,
                    "gt": left > right,
                    "lte": left <= right,
                    "gte": left >= right,
                }.get(op, False)
        return False

    def process_affective_thresholds(self, env: Optional[Any] = None):
        h = self.get_host()
        if h.runtime_mode != self.live_mode:
            return []
        if getattr(h, "_threshold_action_depth", 0) > 0:
            return []
        if not getattr(h, "threshold_registry", None) or not h.threshold_registry.records:
            return []
        pad = h._current_mood_snapshot()
        ready = h.threshold_registry.tick(self._eval_affective_condition, pad)
        results = []
        for rec in ready:
            event = {
                "type": "affective_threshold_triggered",
                "threshold": rec.name,
                "condition": str(getattr(rec.condition, "left", "pad_condition")),
                "pad_snapshot": dict(pad),
                "stable_for_events": rec.for_events,
                "priority": rec.priority,
            }
            h.execution_history.append(event)
            h.threshold_audit.append(event)
            h.process_habits_on_event(event)
            results.append(event)
            try:
                self.execute_threshold_action(rec, env or h.global_env)
            except Exception as exc:
                failure = {"type": "affective_threshold_action_failed", "threshold": rec.name, "error": f"{type(exc).__name__}: {exc}"}
                h.execution_history.append(failure)
                h.threshold_audit.append(failure)
        return results

    def execute_threshold_action(self, rec, env: Any):
        h = self.get_host()
        # Re-validate at execution to defend against programmatic AST mutation.
        self.validate_threshold_action_purity(rec.action or [])
        h._threshold_action_depth += 1
        h.threshold_registry.mark_processing(True)
        try:
            action_env = h.make_environment(env)
            action_env.define("mood", h._current_mood_snapshot())
            for stmt in rec.action or []:
                # suspend is allowed as an internal emergency pause request; in sync mode
                # we record it instead of throwing the normal durable-suspension error.
                if isinstance(stmt, ExprStmt) and isinstance(stmt.expr, SuspendExpr):
                    request = h.describe_request(stmt.expr.request, action_env)
                    h.execution_history.append({"type": "threshold_suspend_requested", "threshold": rec.name, "request": request})
                    continue
                if isinstance(stmt, SuspendExpr):
                    request = h.describe_request(stmt.request, action_env)
                    h.execution_history.append({"type": "threshold_suspend_requested", "threshold": rec.name, "request": request})
                    continue
                h.evaluate(stmt, action_env)
        finally:
            h.threshold_registry.mark_processing(False)
            h._threshold_action_depth -= 1

    # --- v2.0 Affective Runtime & Cognitive VM ---
    def evaluate_affective_state(self, node: AffectiveStateDef, env: Any) -> Dict[str, Any]:
        h = self.get_host()
        baseline = {k: float(h.evaluate(v, env) if hasattr(v, 'line') else v) for k, v in (node.baseline or {}).items()}
        dimensions = {}
        for k, rng in (node.dimensions or {}).items():
            try:
                lo, hi = rng
                lo = h.evaluate(lo, env) if hasattr(lo, 'line') else lo
                hi = h.evaluate(hi, env) if hasattr(hi, 'line') else hi
                dimensions[k] = [float(lo), float(hi)]
            except Exception:
                dimensions[k] = rng
        state = AffectiveState(name=node.name, dimensions=dimensions, baseline=baseline, decay=float(node.decay or 0.0), decay_unit=node.decay_unit)
        h.affective_states[node.name] = state
        env.define(node.binding, state.to_dict())
        env.define(node.name, state.to_dict())
        event = {"type": "affective_state_initialized", "name": node.name, "state": state.to_dict(), "trace_id": h.current_trace_id()}
        h.execution_history.append(event)
        return state.to_dict()

    def _current_affective_state(self, env: Any) -> AffectiveState:
        h = self.get_host()
        # Prefer the most recent explicit state; otherwise lazily create a baseline mood.
        if h.affective_states:
            return list(h.affective_states.values())[-1]
        state = AffectiveState(name="AgentMood")
        h.affective_states[state.name] = state
        env.define("mood", state.to_dict())
        return state

    def evaluate_affective_event(self, node: AffectiveEventStmt, env: Any) -> Dict[str, Any]:
        h = self.get_host()
        state = self._current_affective_state(env)
        fields = {k: h.evaluate(v, env) for k, v in (node.fields or {}).items()}
        delta = {k: float(fields.get(k, 0.0) or 0.0) for k in ("valence", "arousal", "dominance")}
        tag = state.apply_event(node.name, delta, int(fields.get("duration", 0) or 0), str(fields.get("trace_id", h.current_trace_id())))
        h.affective_events.append(tag)
        env.define(node.binding, tag)
        env.define(state.name, state.to_dict())
        event = {"type": "affective_event_tagged", "name": node.name, "tag": tag, "trace_id": tag.get("trace_id")}
        h.execution_history.append(event)
        h.process_habits_on_event(event)
        # Affective tags are memory metadata candidates.
        h.memory_audit.append(event)
        return tag

    def evaluate_affective_modulation(self, node: AffectiveModulationStmt, env: Any) -> Dict[str, Any]:
        h = self.get_host()
        state = self._current_affective_state(env)
        profile = modulation_from_state(state.current)
        # Execute explicit rule body in a sandbox-ish child env for compatibility.
        rule_results = []
        for stmt in node.rules or []:
            try:
                rule_results.append(h.evaluate(stmt, h.make_environment(env)))
            except Exception as exc:
                rule_results.append({"error": str(exc)})
        profile["rules_evaluated"] = len(node.rules or [])
        profile["rule_results"] = rule_results
        env.define(node.binding, profile)
        event = {"type": "affective_modulation_applied", "profile": profile, "trace_id": h.current_trace_id()}
        h.execution_history.append(event)
        return profile

    def _lookup_resonance_profile_for_target(self, target: str, env: Any) -> Dict[str, Any]:
        """Return the most relevant resonance profile for affective bridge evaluation."""
        h = self.get_host()
        # Prefer an explicitly-bound profile in the current environment, matching the common pattern:
        #   resonate with @user { ... bind profile }
        try:
            profile = env.get("profile")
            if isinstance(profile, dict) and profile.get("aspects"):
                return profile
        except Exception:
            pass
        # Fall back to the latest durable resonance profile for the same target.
        for event in reversed(h.execution_history):
            if event.get("type") == "resonance_profile_computed" and str(event.get("target")) == str(target):
                profile = event.get("profile") or {}
                if isinstance(profile, dict):
                    return profile
        return {"target": str(target), "aspects": {"emotional_tone": {"value": "neutral", "confidence": 0.5}}}

    def _compute_affective_resonance_deltas(self, node: AffectiveResonanceStmt, env: Any, state: AffectiveState, target: str) -> List[Dict[str, Any]]:
        """Compute all resonance deltas without mutating PAD state.

        This keeps mirror/regulate/dampen atomic: thresholds, observers and habits never see
        intermediate PAD values.
        """
        h = self.get_host()
        profile = self._lookup_resonance_profile_for_target(target, env)
        aspects = (profile or {}).get("aspects") or {}
        tone = ((aspects.get(str(node.mirror or "emotional_tone")) or aspects.get("emotional_tone") or {}).get("value") or "neutral")
        deltas: List[Dict[str, Any]] = []

        # mirror emotional_tone: limited empathic mirror, not unregulated mimicry.
        if node.mirror:
            if str(node.mirror) not in {"emotional_tone", "tone"}:
                # Parser accepts contextual aspect names; runtime safely no-ops unknown mirror aspects.
                deltas.append({"name": f"mirror_{node.mirror}_noop", "delta": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}, "reason": "unknown_mirror_aspect"})
            elif tone in {"anxious", "urgent"}:
                deltas.append({"name": "user_anxiety_mirrored", "delta": {"valence": -0.2, "arousal": 0.15, "dominance": 0.0}})
            elif tone == "curious":
                deltas.append({"name": "user_curiosity_mirrored", "delta": {"valence": 0.05, "arousal": 0.03, "dominance": 0.0}})
            else:
                deltas.append({"name": "user_neutral_mirrored", "delta": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}})

        # Compute projected state after mirror so regulation can decide from the true batch state.
        projected = dict(state.current)
        for item in deltas:
            for k, v in (item.get("delta") or {}).items():
                lo, hi = (-1.0, 1.0) if k == "valence" else (0.0, 1.0)
                projected[k] = clamp(float(projected.get(k, 0.0)) + float(v), lo, hi)

        # regulate valence: if mirror pushes valence too negative, move toward baseline in one folded delta.
        for key in node.regulate or []:
            canonical = {"pleasure": "valence", "energy": "arousal", "control": "dominance"}.get(str(key), str(key))
            if canonical == "valence" and projected.get("valence", 0.0) < -0.4:
                baseline = float((state.baseline or {}).get("valence", 0.0))
                # Spec says decay_toward_baseline(steps=2); fold it into a single deterministic delta.
                rate = clamp(float(getattr(state, "decay", 0.0) or 0.0) * 2, 0.0, 1.0)
                if rate <= 0:
                    rate = 0.5
                desired = projected["valence"] + (baseline - projected["valence"]) * rate
                delta = desired - projected["valence"]
                deltas.append({"name": "valence_regulated", "delta": {"valence": round(delta, 6), "arousal": 0.0, "dominance": 0.0}, "baseline": baseline})
                projected["valence"] = clamp(projected["valence"] + delta, -1.0, 1.0)
            else:
                deltas.append({"name": f"{canonical}_regulation_noop", "delta": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}, "reason": "threshold_not_crossed"})

        # dampen arousal/dominance/valence by explicit numeric delta.
        for key, expr in (node.dampen or {}).items():
            canonical = {"pleasure": "valence", "energy": "arousal", "control": "dominance"}.get(str(key), str(key))
            amount = abs(float(h.evaluate(expr, env)))
            delta = {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
            if canonical in delta:
                delta[canonical] = -amount
            deltas.append({"name": f"{canonical}_dampened", "delta": delta})
        return deltas

    def _apply_affective_resonance_event(self, event: Dict[str, Any], env: Any) -> Dict[str, Any]:
        """Apply a precomputed atomic affective resonance event exactly once."""
        h = self.get_host()
        event_id = event.get("event_id") or event.get("trace_id") or event.get("id") or f"res-{len(h.execution_history)}"
        if event_id in h._applied_affective_resonance_events:
            return event.get("bridge") or {"duplicate": True, "event_id": event_id}
        state = self._current_affective_state(env)
        before = dict(state.current)
        total = {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
        for item in event.get("events_applied", []):
            for k, v in (item.get("delta") or {}).items():
                if k in total:
                    total[k] += float(v)
        # Single atomic mutation of PAD state.
        state.current["valence"] = clamp(float(state.current.get("valence", 0.0)) + total["valence"], -1.0, 1.0)
        state.current["arousal"] = clamp(float(state.current.get("arousal", 0.0)) + total["arousal"], 0.0, 1.0)
        state.current["dominance"] = clamp(float(state.current.get("dominance", 0.0)) + total["dominance"], 0.0, 1.0)
        after = dict(state.current)
        tag = {
            "id": event_id,
            "event": "affective_resonance",
            "delta": {k: round(v, 6) for k, v in total.items()},
            "before": before,
            "after": after,
            "trace_id": event.get("trace_id", h.current_trace_id()),
        }
        state.events.append(tag)
        h.affective_events.append(tag)
        h._applied_affective_resonance_events.add(event_id)
        bridge = copy.deepcopy(event.get("bridge") or {})
        bridge.update({"before": before, "after": after, "final_pad": after, "events_applied": event.get("events_applied", []), "atomic": True})
        return bridge

    def evaluate_affective_resonance(self, node: AffectiveResonanceStmt, env: Any) -> Dict[str, Any]:
        h = self.get_host()
        if h.dream_depth > 0:
            raise self.AffectiveIsolationViolation("affective resonance forbidden inside dream")
        if h.fracture_depth > 0 and h.is_in_subagent():
            raise self.OrphanedIdentityException("affective resonance forbidden inside sub-agent")

        # REPLAY: consume durable event and apply saved deltas; never recompute from live profile.
        if h.runtime_mode == self.replay_mode:
            replay_event = h.next_history_event("affective_resonance_applied")
            if replay_event is not None:
                bridge = self._apply_affective_resonance_event(replay_event, env)
                env.define(node.binding, bridge)
                return bridge

        target = h.evaluate(node.target, env) if node.target else "@user"
        state = self._current_affective_state(env)
        before = dict(state.current)
        events_applied = self._compute_affective_resonance_deltas(node, env, state, str(target))

        # Build the event first, then apply it once. No intermediate PAD is observable.
        event_id = "ares-" + uuid.uuid4().hex[:12]
        bridge = {
            "target": str(target),
            "mirror": node.mirror,
            "regulate": list(node.regulate or []),
            "dampen": list((node.dampen or {}).keys()),
            "recommendation": "empathic regulation",
        }
        event = {
            "type": "affective_resonance_applied",
            "event_id": event_id,
            "source": "resonate_with_user",
            "target": str(target),
            "events_applied": events_applied,
            "bridge": bridge,
            "before": before,
            "atomic": True,
            "trace_id": h.current_trace_id(),
        }
        final_bridge = self._apply_affective_resonance_event(event, env)
        event["after"] = final_bridge.get("after")
        event["bridge"] = {k: v for k, v in final_bridge.items() if k not in {"events_applied"}}
        env.define(node.binding, final_bridge)
        h.execution_history.append(event)
        h.process_habits_on_event(event)
        return final_bridge

    def current_mood_snapshot(self) -> Any:
        """Return frozen PAD snapshot; neutral when no affective state exists."""
        h = self.get_host()
        state = None
        if h.affective_states:
            # Prefer the most recently defined state. Existing runtime stores named states.
            state = next(reversed(h.affective_states.values()))
        if isinstance(state, AffectiveState):
            cur = getattr(state, "current", {}) or {}
            return self.make_frozen_mood({
                "valence": cur.get("valence", 0.0),
                "arousal": cur.get("arousal", 0.0),
                "dominance": cur.get("dominance", 0.0),
            })
        if isinstance(state, dict):
            return self.make_frozen_mood(state)
        return self.make_frozen_mood(None)
