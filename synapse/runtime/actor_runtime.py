"""Actor runtime facade extracted from Interpreter.

This module intentionally keeps actor-owned mutable structures on the host
Interpreter for backward compatibility. ActorRuntime orchestrates existing
semantics through host_getter callbacks and does not own AST dispatch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from synapse.ast import CallExpr, Variable, MemberAccess, ReceiveBlock, Node
from synapse.builtins import AgentRuntime, DurableActorRef, DurablePromise


class ActorRuntime:
    def __init__(self, host_getter, live_mode, replay_mode):
        self.get_host = host_getter
        self.live_mode = live_mode
        self.replay_mode = replay_mode


    def _actor_stack(self) -> List[str]:
        h = self.get_host()
        if not hasattr(h, "actor_stack"):
            h.actor_stack = []
        return h.actor_stack

    @property
    def current(self) -> Optional[str]:
        stack = self._actor_stack()
        return stack[-1] if stack else None

    def enter_event(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Canonical structural actor-enter event used by CVM parity path."""
        stack = self._actor_stack()
        stack.append(name)
        return {
            "type": "actor_entered",
            "actor": name,
            "label": name,
            "metadata": metadata or {},
            "depth": len(stack),
        }

    def exit_event(self, name: str) -> Dict[str, Any]:
        """Canonical structural actor-exit event used by CVM parity path."""
        stack = self._actor_stack()
        if stack and stack[-1] == name:
            stack.pop()
        elif name in stack:
            stack.remove(name)
        return {
            "type": "actor_exited",
            "actor": name,
            "label": name,
            "depth": len(stack),
        }

    def sync_from_cvm_stack(self, actor_stack: List[str]) -> None:
        """Hydrate host actor stack from restored VM actor_stack without emitting events."""
        h = self.get_host()
        h.actor_stack = list(actor_stack or [])
        if hasattr(h, "current_actor"):
            h.current_actor = self.current

    def receiver_name(self, receiver: Any) -> str:
        if isinstance(receiver, DurableActorRef):
            return receiver.process_id
        if isinstance(receiver, AgentRuntime):
            return receiver.name
        return str(receiver)

    def spawn_actor(self, node, env, async_mode: bool = False) -> DurableActorRef:
        h = self.get_host()
        callee = node.callee
        actor_name = None
        actor_value = None
        if isinstance(callee, CallExpr) and isinstance(callee.callee, Variable):
            actor_name = callee.callee.name
            actor_value = env.get(actor_name)
        else:
            actor_value = h.evaluate(callee, env)
            actor_name = getattr(actor_value, "name", str(actor_value))

        if not isinstance(actor_value, AgentRuntime):
            raise h.runtime_error(f"spawn expects an agent constructor/reference, got {type(actor_value).__name__}")

        if h.runtime_mode == self.replay_mode:
            event = h.next_history_event("actor_spawned")
            if event is not None:
                process_id = event.get("process_id")
                node_id = event.get("node", h.node_id)
                h.spawned_actors[process_id] = {
                    "process_id": process_id,
                    "actor_name": event.get("actor", actor_name),
                    "node": node_id,
                    "model": actor_value.model,
                    "status": "running",
                    "replayed": True,
                }
                h.mailboxes.setdefault(process_id, [])
                h.routing_table.setdefault(process_id, "local")
                return DurableActorRef(actor_name=event.get("actor", actor_name), process_id=process_id, node=node_id)

        process_id = f"{actor_name}#{uuid.uuid4().hex[:12]}"
        ref = DurableActorRef(actor_name=actor_name, process_id=process_id, node=h.node_id)
        h.spawned_actors[process_id] = {
            "process_id": process_id,
            "actor_name": actor_name,
            "node": h.node_id,
            "model": actor_value.model,
            "status": "running",
        }
        h.mailboxes.setdefault(process_id, [])
        h.routing_table.setdefault(process_id, "local")
        event = {"type": "actor_spawned", "actor": actor_name, "process_id": process_id, "node": h.node_id}
        h.actor_log.append(event)
        h.execution_history.append(event)
        return ref


    def suspend_on_promise(self, actor_id: str, call_id: str):
        """Mark an actor as suspended on a bridge-side promise.

        D2 keeps this hook deliberately small: it records actor scheduling
        intent without introducing a new scheduler or multiple-promise runtime.
        """
        h = self.get_host()
        h.spawned_actors.setdefault(actor_id, {"process_id": actor_id, "status": "running"})
        h.spawned_actors[actor_id]["status"] = "suspended"
        h.spawned_actors[actor_id]["pending_call_id"] = call_id
        event = {"type": "actor_suspended_on_promise", "actor_id": actor_id, "call_id": call_id}
        h.actor_log.append(event)
        return event

    def wake_on_resolve(self, actor_id: str, call_id: str, value: Any):
        """Wake an actor after promise resolution via a mailbox notification."""
        h = self.get_host()
        h.spawned_actors.setdefault(actor_id, {"process_id": actor_id, "status": "suspended"})
        h.spawned_actors[actor_id]["status"] = "running"
        h.spawned_actors[actor_id].pop("pending_call_id", None)
        message = {
            "type": "promise_resolved",
            "receiver": actor_id,
            "call_id": call_id,
            "value": value,
        }
        h.mailboxes.setdefault(actor_id, []).append(message)
        h.actor_log.append(message)
        return message

    def wake_on_reject(self, actor_id: str, call_id: str, error: Any):
        """Wake an actor after promise rejection via a mailbox notification."""
        h = self.get_host()
        h.spawned_actors.setdefault(actor_id, {"process_id": actor_id, "status": "suspended"})
        h.spawned_actors[actor_id]["status"] = "running"
        h.spawned_actors[actor_id].pop("pending_call_id", None)
        message = {
            "type": "promise_rejected",
            "receiver": actor_id,
            "call_id": call_id,
            "error": error,
        }
        h.mailboxes.setdefault(actor_id, []).append(message)
        h.actor_log.append(message)
        return message
    def create_durable_promise(self, reason: str, request: Any = None) -> DurablePromise:
        h = self.get_host()
        if h.runtime_mode == self.replay_mode:
            event = h.next_history_event("promise_created")
            if event is not None:
                promise = DurablePromise(
                    promise_id=event.get("promise_id"),
                    reason=event.get("reason", reason),
                    request=event.get("request", request),
                )
                h.promises[promise.promise_id] = promise.to_dict()
                return promise

        promise_id = f"promise-{uuid.uuid4().hex[:16]}"
        promise = DurablePromise(promise_id=promise_id, reason=reason, request=request)
        h.promises[promise_id] = promise.to_dict()
        h.execution_history.append({
            "type": "promise_created",
            "promise_id": promise_id,
            "reason": reason,
            "request": h.global_env._json_safe(request),
        })
        return promise

    def resolve_promise(self, promise_id: str, result: Any, source_node: Optional[str] = None):
        h = self.get_host()
        record = h.promises.get(promise_id) or {"promise_id": promise_id, "reason": "external_signal"}
        record.update({"status": "resolved", "result": result, "resolved_by": source_node or h.node_id})
        h.promises[promise_id] = record
        event = {"type": "promise_resolved", "promise_id": promise_id, "result": result, "source_node": source_node or h.node_id}
        h.execution_history.append(event)
        h.actor_log.append(event)
        return record

    def register_promise_owner(self, promise_id: str, node_address: str):
        self.get_host().promise_routes[promise_id] = node_address

    def register_promise_tombstone(self, promise_id: str, node_address: str):
        self.get_host().promise_tombstones[promise_id] = node_address

    def resolve_promise_location(self, promise_id: str) -> str:
        h = self.get_host()
        if promise_id in h.promise_tombstones:
            return h.promise_tombstones[promise_id]
        return h.promise_routes.get(promise_id, "local")

    def build_resolve_promise_packet(self, promise_id: str, result: Any, target_node: str) -> Dict[str, Any]:
        h = self.get_host()
        return {
            "type": "resolve_promise",
            "version": "1.0.0",
            "source_node": h.node_id,
            "target_node": target_node,
            "promise_id": promise_id,
            "value": result,
        }

    def emit_or_apply_promise_resolution(self, promise_id: str, result: Any) -> Dict[str, Any]:
        h = self.get_host()
        location = self.resolve_promise_location(promise_id)
        if location != "local" and location != h.node_id:
            packet = self.build_resolve_promise_packet(promise_id, result, location)
            h.outbound_packets.append(packet)
            event = {"type": "promise_resolution_forwarded", "promise_id": promise_id, "target_node": location}
            h.execution_history.append(event)
            h.actor_log.append(event)
            return {"status": "forwarded", "promise_id": promise_id, "target_node": location}
        return self.resolve_promise(promise_id, result)

    def promise_id_from_await_target(self, expr: Node, env) -> str:
        h = self.get_host()
        if isinstance(expr, CallExpr) and isinstance(expr.callee, MemberAccess):
            target = h.evaluate(expr.callee.obj, env)
            method = expr.callee.member
            name = self.receiver_name(target)
            return f"await:{name}.{method}"
        value = h.evaluate(expr, env)
        if isinstance(value, DurablePromise):
            return value.promise_id
        return f"await:{str(value)}"

    def register_route(self, actor_name: str, node_address: str):
        self.get_host().routing_table[actor_name] = node_address

    def resolve_actor_location(self, actor_name: str) -> str:
        return self.get_host().routing_table.get(actor_name, "local")

    def build_forward_packet(self, message: Dict[str, Any], node_address: str) -> Dict[str, Any]:
        h = self.get_host()
        return {
            "type": "forward_message",
            "version": "1.0.0",
            "source_node": h.node_id,
            "target_node": node_address,
            "target_actor": message.get("receiver"),
            "message": message,
        }

    def send_message(self, sender: str, receiver: str, method: str, args: List[Any]) -> Dict[str, Any]:
        h = self.get_host()
        if h.runtime_mode == self.replay_mode:
            # Governance verdicts are durable events and must be consumed during
            # replay, but delivery itself must not mutate mailboxes or emit
            # network packets again.
            h.check_send_governance(sender, receiver, method, args)
            return {
                "sender": sender,
                "receiver": receiver,
                "method": method,
                "args": args,
                "payload": args[0] if len(args) == 1 else args,
                "replayed": True,
            }

        h.check_send_governance(sender, receiver, method, args)
        message = {
            "sender": sender,
            "receiver": receiver,
            "method": method,
            "args": args,
            "payload": args[0] if len(args) == 1 else args,
        }
        location = self.resolve_actor_location(receiver)
        if location != "local":
            packet = self.build_forward_packet(message, location)
            h.outbound_packets.append(packet)
            event = {
                "type": "message_forwarded",
                "message": message,
                "target_node": location,
            }
            h.actor_log.append(event)
            h.execution_history.append(event)
            h.emit_runtime_event(event, h.global_env)
            return {**message, "forwarded": True, "target_node": location}

        h.mailboxes.setdefault(receiver, []).append(message)
        h.actor_log.append(message)
        event = {"type": "message_sent", "message": message}
        h.execution_history.append(event)
        h.emit_runtime_event(event, h.global_env)
        return message

    def apply_receive_patterns(self, node: ReceiveBlock, message: Dict[str, Any], env, async_mode: bool = False):
        h = self.get_host()
        if not node.patterns:
            return message
        pattern = node.patterns[0]
        receive_env = h.make_environment(env)
        receive_env.define(pattern.sender_var, message.get("sender"))
        receive_env.define(pattern.target_var, message)
        if async_mode:
            return h.execute_block_async(pattern.body, receive_env)
        return h.execute_block(pattern.body, receive_env)

    def evaluate_receive(self, node: ReceiveBlock, env):
        h = self.get_host()
        actor_name = h.current_actor_name(env)

        replay_event = h.peek_next_history_event()
        if replay_event and replay_event.get("type") == "message_received":
            event = h.next_history_event("message_received")
            return self.apply_receive_patterns(node, event.get("message"), env)
        if replay_event and replay_event.get("type") == "receive_timeout":
            h.next_history_event("receive_timeout")
            return h.execute_block(node.else_body, h.make_environment(env)) if node.else_body else None

        mailbox = h.mailboxes.setdefault(actor_name, [])
        if not mailbox:
            if node.timeout is not None:
                timeout_value = h.evaluate(node.timeout, env)
                event = {"type": "receive_timeout", "actor": actor_name, "timeout": timeout_value}
                h.execution_history.append(event)
                h.actor_log.append(event)
                h.emit_runtime_event(event, env)
                return h.execute_block(node.else_body, h.make_environment(env)) if node.else_body else None
            return None
        message = mailbox.pop(0)
        event = {"type": "message_received", "actor": actor_name, "message": message}
        h.execution_history.append(event)
        h.emit_runtime_event(event, env)
        return self.apply_receive_patterns(node, message, env)

    def evaluate_async_receive(self, node: ReceiveBlock, env):
        h = self.get_host()
        actor_name = h.current_actor_name(env)

        replay_event = h.peek_next_history_event()
        if replay_event and replay_event.get("type") == "message_received":
            event = h.next_history_event("message_received")
            message = event.get("message")
            return (yield from self.apply_receive_patterns(node, message, env, async_mode=True))
        if replay_event and replay_event.get("type") == "receive_timeout":
            h.next_history_event("receive_timeout")
            return (yield from h.execute_block_async(node.else_body, h.make_environment(env))) if node.else_body else None

        mailbox = h.mailboxes.setdefault(actor_name, [])
        timeout_value = None
        if node.timeout is not None:
            timeout_value = yield from h.evaluate_async(node.timeout, env)
        if not mailbox:
            reason = "awaiting_message_or_timeout" if node.timeout is not None else "awaiting_message"
            injected = yield h.Suspension(node, env, reason=reason, payload={"actor": actor_name, "timeout": timeout_value})
            if isinstance(injected, dict) and injected.get("timeout") is True:
                event = {"type": "receive_timeout", "actor": actor_name, "timeout": timeout_value}
                h.execution_history.append(event)
                h.actor_log.append(event)
                return (yield from h.execute_block_async(node.else_body, h.make_environment(env))) if node.else_body else None
            if injected is not None:
                mailbox.append(injected)
        if not mailbox:
            if node.timeout is not None:
                event = {"type": "receive_timeout", "actor": actor_name, "timeout": timeout_value}
                h.execution_history.append(event)
                h.actor_log.append(event)
                return (yield from h.execute_block_async(node.else_body, h.make_environment(env))) if node.else_body else None
            return None
        message = mailbox.pop(0)
        h.execution_history.append({"type": "message_received", "actor": actor_name, "message": message})
        return (yield from self.apply_receive_patterns(node, message, env, async_mode=True))

    def request_migration_async(self, node, env, target):
        h = self.get_host()
        event = {"type": "migration_requested", "target": target, "actor": h.current_actor_name(env)}
        h.execution_history.append(event)
        h.actor_log.append(event)
        return (yield h.Suspension(node, env, reason="migration_requested", payload={"target": target, "actor": h.current_actor_name(env)}))
