"""Admit CVM message envelopes through their canonical actor owner."""
from __future__ import annotations

from synapse.cvm import VMHostError
from synapse.cvm_messaging import SEND_ARGS_SCHEMA_V1


def _invalid(detail: str) -> VMHostError:
    return VMHostError(code="MESSAGE_FORMAT_ERROR", message=detail, symbol="SYS_MSG_SEND")


def _address(message, primary, alias):
    value = message.get(primary, message.get(alias))
    if not isinstance(value, str) or not value:
        raise _invalid(f"Message {primary} must be a nonempty string")
    if alias in message and message[alias] != value:
        raise _invalid(f"Message {primary} disagrees with {alias}")
    return value


def bind_sender(host, vm, env=None, *, restoring=False):
    """Bind new send instructions to the invoking DSL actor, independently of capabilities."""
    actor_name = getattr(host, "current_actor_name", None)
    sender = actor_name(env if env is not None else host.global_env) if callable(actor_name) else None
    if any(instruction.op == "MSG_SEND_ARGS" for instruction in vm.program.instructions):
        if restoring and vm.state.sender_id is None:
            raise VMHostError(code="MESSAGE_CONTEXT_MISSING", message="Saved argument-send VM has no caller binding",
                              symbol="SYS_MSG_SEND")
        check_sender_binding(vm, sender)
    return sender


def check_sender_binding(vm, sender):
    """Refuse a captured caller change before a versioned host send can act."""
    if sender is None:
        raise VMHostError(code="MESSAGE_CONTEXT_MISSING", message="CVM send requires its invoking actor context",
                          symbol="SYS_MSG_SEND")
    if vm.state.sender_id is not None and vm.state.sender_id != sender:
        raise VMHostError(code="MESSAGE_SENDER_MISMATCH", message="Saved VM sender differs from its invoking actor",
                          symbol="SYS_MSG_SEND")
    vm.state.sender_id = sender


def _arguments(message):
    """Recover the exact source argument vector from its declared arity."""
    if "schema_version" not in message:
        if "args" in message or "argc" in message or "payload" not in message:
            raise _invalid("Legacy MSG_SEND requires one opaque payload")
        return [message["payload"]]
    if message["schema_version"] != SEND_ARGS_SCHEMA_V1 or "args" in message or "payload" not in message:
        raise _invalid("Unknown or incomplete CVM send-args envelope")
    count, payload = message.get("argc"), message["payload"]
    if type(count) is not int or count < 0:
        raise _invalid("Message argc must be a nonnegative integer")
    if count == 1:
        return [payload]
    if type(payload) is not list or len(payload) != count:
        raise _invalid("Message payload does not match its declared arity")
    return payload


def _request(vm, arguments):
    if type(arguments) is not list or len(arguments) != 1 or type(arguments[0]) is not dict:
        raise _invalid("SYS_MSG_SEND requires one message envelope")
    message = arguments[0]
    sender = _address(message, "sender_id", "sender")
    receiver = _address(message, "target_id", "receiver")
    method = _address(message, "msg_type", "method")
    state = vm.state
    expected_sender = state.actor_stack[-1] if state.actor_stack else state.agent_id or "default_agent"
    args = _arguments(message)
    if "schema_version" in message and not state.actor_stack:
        expected_sender = state.sender_id or expected_sender
    if sender != expected_sender:
        raise VMHostError(code="MESSAGE_SENDER_MISMATCH", message="Message sender differs from its VM context",
                          symbol="SYS_MSG_SEND")
    return sender, receiver, method, args


def send_from_vm(host, vm, arguments, actor_runtime, *, sender=None):
    """Translate the call without granting the bridge delivery authority."""
    if actor_runtime is None or not callable(getattr(actor_runtime, "send_message", None)):
        raise VMHostError(code="ACTOR_RUNTIME_MISSING", message="ActorRuntime is required for SYS_MSG_SEND",
                          symbol="SYS_MSG_SEND")
    if type(arguments) is list and arguments and isinstance(arguments[0], dict) and "schema_version" in arguments[0]:
        if sender is None:
            actor_name = getattr(host, "current_actor_name", None)
            sender = actor_name(host.global_env) if callable(actor_name) else None
        check_sender_binding(vm, sender)
    request = _request(vm, arguments)
    governance = getattr(getattr(host, "runtime", None), "governance", None)
    policy_error = getattr(governance, "PolicyViolationException", ())
    try:
        delivered = actor_runtime.send_message(*request)
    except policy_error as exc:
        raise VMHostError(code="POLICY_VIOLATION", message=str(exc), symbol="SYS_MSG_SEND") from exc
    # Transport/replay flags describe this invocation, not the message value.
    # Keeping them out of the VM stack preserves the recorded transition hash.
    return {key: delivered[key] for key in ("sender", "receiver", "method", "args", "payload")}
