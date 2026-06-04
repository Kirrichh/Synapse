from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class GovernanceEngine:
    """Governance/policy helper extracted from Interpreter.

    The interpreter remains the orchestrator and owner of mutable runtime state.
    This engine receives getters, setters, callbacks, and exception classes so it
    can preserve the original policy semantics without importing Interpreter or
    AST dispatch code.
    """

    def __init__(
        self,
        *,
        policies_getter: Callable[[], Dict[str, Dict[str, Any]]],
        runtime_mode_getter: Callable[[], Any],
        replay_cursor_getter: Callable[[], int],
        replay_cursor_setter: Callable[[int], None],
        live_mode: Any,
        replay_mode: Any,
        peek_history_event_fn: Callable[[], Optional[Dict[str, Any]]],
        execution_history_getter: Callable[[], List[Dict[str, Any]]],
        actor_log_getter: Callable[[], List[Dict[str, Any]]],
        mailboxes_getter: Callable[[], Dict[str, List[Dict[str, Any]]]],
        mailboxes_setter: Callable[[Dict[str, List[Dict[str, Any]]]], None],
        memory_audit_getter: Callable[[], List[Dict[str, Any]]],
        global_env_getter: Callable[[], Any],
        environment_factory: Callable[[Any], Any],
        execute_block_fn: Callable[[List[Any], Any], Any],
        emit_runtime_event_fn: Callable[[Dict[str, Any], Any], None],
        actor_trust_record_fn: Callable[[str], Any],
        trust_at_least_fn: Callable[..., bool],
        current_mood_snapshot_fn: Callable[[], Any],
        policy_guard_depth_getter: Callable[[], int],
        policy_guard_depth_setter: Callable[[int], None],
        policy_violation_exception: type[Exception],
        reject_exception: type[Exception],
        resonance_privacy_exception: type[Exception],
    ):
        self.get_policies = policies_getter
        self.get_runtime_mode = runtime_mode_getter
        self.get_replay_cursor = replay_cursor_getter
        self.set_replay_cursor = replay_cursor_setter
        self.live_mode = live_mode
        self.replay_mode = replay_mode
        self.peek_history_event = peek_history_event_fn
        self.get_execution_history = execution_history_getter
        self.get_actor_log = actor_log_getter
        self.get_mailboxes = mailboxes_getter
        self.set_mailboxes = mailboxes_setter
        self.get_memory_audit = memory_audit_getter
        self.get_global_env = global_env_getter
        self.make_environment = environment_factory
        self.execute_block = execute_block_fn
        self.emit_runtime_event = emit_runtime_event_fn
        self.actor_trust_record = actor_trust_record_fn
        self.trust_at_least = trust_at_least_fn
        self.current_mood_snapshot = current_mood_snapshot_fn
        self.get_policy_guard_depth = policy_guard_depth_getter
        self.set_policy_guard_depth = policy_guard_depth_setter
        self.PolicyViolationException = policy_violation_exception
        self.RejectException = reject_exception
        self.ResonancePrivacyException = resonance_privacy_exception

    def policy_target_matches(self, target: Optional[str], receiver: str, method: str) -> bool:
        if not target:
            return False
        return target == f"{receiver}.{method}" or target == f"*.{method}" or target == receiver or target == method or target == "*"

    def applicable_policies(self, receiver: str, method: str) -> List[Dict[str, Any]]:
        active = []
        for policy in self.get_policies().values():
            if not isinstance(policy, dict):
                continue
            if self.policy_target_matches(policy.get("target"), receiver, method):
                active.append(policy)
        return active

    def check_intent_governance(self, name: str, record: Dict[str, Any]) -> None:
        for policy in self.get_policies().values():
            target = policy.get("target") if isinstance(policy, dict) else None
            if target not in {f"intent.{name}", name, "intent.*", "*"}:
                continue
            # Use the existing atomic policy verdict path with the intent record as argument.
            self.execute_policy_guard(policy, "global", "intent", name, [record])

    def execute_policy_guard(self, policy: Dict[str, Any], sender: str, receiver: str, method: str, args: List[Any]) -> Dict[str, Any]:
        """Evaluate a policy as one durable, replay-safe verdict.

        The internal guard body is intentionally not written to the main
        execution_history. LIVE mode executes it in a read-only policy context
        and then stores one atomic event: policy_evaluated or policy_violation.
        REPLAY mode consumes that event and never re-runs the guard, so policy
        evolution cannot corrupt historical workflow replay.
        """
        target_path = f"{receiver}.{method}"
        policy_name = policy.get("name")

        if self.get_runtime_mode() == self.replay_mode:
            event = self.peek_history_event()
            if event and event.get("type") in {"policy_evaluated", "policy_violation"} and event.get("policy") == policy_name:
                self.set_replay_cursor(self.get_replay_cursor() + 1)
                if event.get("type") == "policy_violation":
                    raise self.PolicyViolationException(event.get("reason") or f"Governance Policy Blocked execution of {target_path}")
                return event
            # Backward-compatible replay of v0.4/v0.5 logs, where successful
            # policy evaluations were not represented as durable events.
            return {
                "type": "policy_evaluated",
                "policy": policy_name,
                "target": target_path,
                "result": "pass",
                "replayed_without_event": True,
            }

        history = self.get_execution_history()
        actor_log = self.get_actor_log()
        global_env = self.get_global_env()

        # Static forbid rules stay as a cheap pre-filter, but their verdict is
        # still represented atomically in the history.
        for rule in policy.get("rules", []):
            if not isinstance(rule, dict) or rule.get("kind") != "forbid":
                continue
            forbidden = rule.get("value")
            if forbidden in args or any(str(forbidden) in str(arg) for arg in args):
                violation = {
                    "type": "policy_violation",
                    "policy": policy_name,
                    "target": target_path,
                    "sender": sender,
                    "receiver": receiver,
                    "method": method,
                    "args": args,
                    "reason": f"forbidden value: {forbidden}",
                }
                history.append(violation)
                actor_log.append(violation)
                self.emit_runtime_event(violation, global_env)
                raise self.PolicyViolationException(f"Governance Policy Blocked execution of {target_path}: {forbidden}")

        guard_body = policy.get("guard_body") or []
        rejected = False
        reason = None
        history_len = len(history)
        actor_log_len = len(actor_log)
        mailbox_snapshot = {k: [dict(m) for m in v] for k, v in self.get_mailboxes().items()}
        memory_audit = self.get_memory_audit()
        memory_audit_len = len(memory_audit)

        if guard_body:
            guard_env = self.make_environment(global_env)
            params = policy.get("guard_params") or []
            if params:
                guard_env.define(params[0], args)
                for index, param in enumerate(params[1:], start=1):
                    guard_env.define(param, args[index - 1] if index - 1 < len(args) else None)
            else:
                guard_env.define("args", args)
            guard_env.define("sender", sender)
            guard_env.define("receiver", receiver)
            guard_env.define("method", method)
            guard_env.define("target", target_path)
            guard_env.define("source", self.actor_trust_record(sender))
            guard_env.define("trust_at_least", self.trust_at_least)
            guard_env.define("mood", self.current_mood_snapshot())

            self.set_policy_guard_depth(self.get_policy_guard_depth() + 1)
            try:
                self.execute_block(guard_body, guard_env)
            except self.RejectException as exc:
                rejected = True
                reason = exc.message
            finally:
                self.set_policy_guard_depth(self.get_policy_guard_depth() - 1)
                # Discard internal guard effects from the main durable stream.
                # The only durable fact is the final policy verdict.
                del history[history_len:]
                del actor_log[actor_log_len:]
                self.set_mailboxes(mailbox_snapshot)
                del memory_audit[memory_audit_len:]

        if rejected:
            violation = {
                "type": "policy_violation",
                "policy": policy_name,
                "target": target_path,
                "sender": sender,
                "receiver": receiver,
                "method": method,
                "args": args,
                "reason": reason or "semantic guard rejected action",
            }
            history.append(violation)
            actor_log.append(violation)
            self.emit_runtime_event(violation, global_env)
            raise self.PolicyViolationException(violation["reason"])

        evaluated = {
            "type": "policy_evaluated",
            "policy": policy_name,
            "target": target_path,
            "sender": sender,
            "receiver": receiver,
            "method": method,
            "args": args,
            "result": "pass",
        }
        history.append(evaluated)
        self.emit_runtime_event(evaluated, global_env)
        return evaluated

    def check_send_governance(self, sender: str, receiver: str, method: str, args: List[Any]) -> None:
        for policy in self.applicable_policies(receiver, method):
            self.execute_policy_guard(policy, sender, receiver, method, args)

    def policy_allows(self, policy_name: Optional[str] = None, target: Optional[str] = None, field: str = "allow") -> bool:
        candidates = []
        policies = self.get_policies()
        if policy_name and policy_name in policies:
            candidates.append(policies[policy_name])
        if target:
            for policy in policies.values():
                if str(policy.get("target")) in {target, "*", target.split(".")[0] + ".*"}:
                    candidates.append(policy)
        for policy in candidates:
            fields = policy.get("fields", {}) or {}
            raw = fields.get(field)
            if getattr(raw, "__class__", None).__name__ == "Literal":
                if bool(raw.value):
                    return True
            elif raw is True or str(raw).lower() in {"true", "allowed", "allow"}:
                return True
            # policy record may contain evaluated user fields in future versions.
            if policy.get(field) is True:
                return True
        return False

    def require_cross_agent_resonance_permission(self, target_name: str, current_actor_name_fn: Callable[[], Optional[str]]) -> None:
        if target_name.startswith("@") or target_name in {"current_user", "user"}:
            return
        current = None
        try:
            current = current_actor_name_fn()
        except Exception:
            current = None
        if target_name == current:
            return
        if not self.policy_allows(target=f"resonance.{target_name}", field="resonance_readable"):
            raise self.ResonancePrivacyException(f"Cross-agent resonance with '{target_name}' requires resonance_readable: true policy")
