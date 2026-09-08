"""Mini's public conversation: exact task roots and actual provider replies.

Local observations have no implicit public projection. An unknown message,
including a tool result or an exception depending on local data, refuses the
next request before the provider inventory is opened. This policy does not
interpret local knowledge or authorize a tool effect.
"""

from __future__ import annotations

import json

from .input_contract import WorkerInputViolation


def _wire_message(message: dict) -> bytes:
    if type(message) is not dict:
        raise WorkerInputViolation("provider message must be an exact object")
    # Mini's extra field is local accounting/trajectory data. Its installed
    # model removes it at the API boundary as well.
    value = {key: item for key, item in message.items() if key != "extra"}
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return raw.encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise WorkerInputViolation("provider message is outside the data profile") from exc


class PublicProviderConversation:
    """One Mini model's ordered public history; copies never retain aliases."""

    def __init__(self, initial_messages: list[dict]):
        if (type(initial_messages) is not list or len(initial_messages) != 2
                or any(type(item) is not dict for item in initial_messages)
                or [item.get("role") for item in initial_messages] != ["system", "user"]):
            raise WorkerInputViolation("public conversation requires its configured task roots")
        self._history = [_wire_message(item) for item in initial_messages]

    def require_public(self, messages: list[dict]) -> list[dict]:
        if type(messages) is not list:
            raise WorkerInputViolation("provider history must be an exact list")
        projected = [_wire_message(item) for item in messages]
        if projected != self._history:
            raise WorkerInputViolation("message has no permitted public provenance")
        return [json.loads(item) for item in projected]

    def record_provider_messages(self, *messages: dict) -> None:
        """Called by the model only for its actual response/format-error path."""
        self._history.extend(_wire_message(item) for item in messages)
