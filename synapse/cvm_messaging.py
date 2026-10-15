"""Execute the CVM's versioned message-send instruction contracts."""
from __future__ import annotations

import copy
from typing import Any, Callable, Protocol


# A separate opcode makes older VMs refuse the extension. A legacy opaque
# payload remains opaque even when it happens to contain schema/args fields.
SEND_ARGS_SCHEMA_V1 = "synapse.cvm.send-args/v1"


class SendMachine(Protocol):
    state: Any

    def _pop(self) -> Any: ...
    def _push(self, value: Any) -> None: ...
    def _current_actor_id(self) -> str: ...
    def _payload_hash(self, value: Any) -> str: ...
    def _call_host(self, opcode: str, a: Any, b: Any) -> Any: ...
    def _raise_if_host_error_payload(self, value: Any, symbol: str) -> None: ...


def execute_send(vm: SendMachine, msg_type: str,
                 host_error: Callable[[str, str, str], Exception], *, exact_args: bool = False) -> None:
    """Deliver the selected wire form, committing the VM outbox after host success."""
    value, target_id = vm._pop(), vm._pop()
    if exact_args:
        if type(value) is not list:
            raise host_error("MESSAGE_ARGS_INVALID", "MSG_SEND_ARGS requires an exact argument list", "SYS_MSG_SEND")
        sender_id = str(vm.state.actor_stack[-1] if vm.state.actor_stack
                        else vm.state.sender_id or vm.state.agent_id or "default_agent")
        payload = value[0] if len(value) == 1 else value
    else:
        sender_id, payload = vm._current_actor_id(), value
    message = {
        "msg_type": str(msg_type),
        "method": str(msg_type),
        "sender_id": sender_id,
        "sender": sender_id,
        "target_id": str(target_id),
        "receiver": str(target_id),
        "payload": payload,
        "payload_hash": vm._payload_hash(payload),
    }
    if exact_args:
        # Arity disambiguates one list from several arguments without storing
        # the payload twice in every serialized outbound snapshot.
        message.update(schema_version=SEND_ARGS_SCHEMA_V1, argc=len(value))
    result = vm._call_host("CALL_HOST", {"symbol": "SYS_MSG_SEND", "args": [message]}, None)
    vm._raise_if_host_error_payload(result, "SYS_MSG_SEND")
    vm.state.mailbox_outbound.append(copy.deepcopy(message))
    vm._push(result)
