"""Mini's public conversation: exact task roots and actual provider replies.

Local observations have no implicit public projection. An unknown message,
including a tool result or an exception depending on local data, refuses the
next request before the provider inventory is opened. This policy does not
interpret local knowledge or authorize a tool effect.
"""

from __future__ import annotations

import json

from .input_contract import WorkerInputViolation


def _wire_message(message: dict, *, keep_extra=False) -> bytes:
    if type(message) is not dict:
        raise WorkerInputViolation("provider message must be an exact object")
    # Mini's extra field is local accounting/trajectory data. Its installed
    # model removes it at the API boundary as well.
    value = message if keep_extra else {key: item for key, item in message.items() if key != "extra"}
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return raw.encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise WorkerInputViolation("provider message is outside the data profile") from exc


def _matches_public_wire(expected, observed) -> bool:
    """The SDK may omit an already-public null; it cannot add message fields."""
    if type(expected) is not type(observed):
        return False
    if type(expected) is dict:
        if observed.keys() - expected.keys():
            return False
        return all(
            _matches_public_wire(value, observed[key]) if key in observed else value is None
            for key, value in expected.items()
        )
    if type(expected) is list:
        return len(expected) == len(observed) and all(
            _matches_public_wire(left, right) for left, right in zip(expected, observed)
        )
    return expected == observed


class PublicProviderConversation:
    """One Mini model's ordered public history; copies never retain aliases."""

    def __init__(self, initial_messages: list[dict], *, provider_wire=False):
        if (type(initial_messages) is not list or len(initial_messages) != 2
                or any(type(item) is not dict for item in initial_messages)
                or [item.get("role") for item in initial_messages] != ["system", "user"]):
            raise WorkerInputViolation("public conversation requires its configured task roots")
        self._provider_wire = provider_wire
        self._history = [_wire_message(item) for item in initial_messages]

    def require_public(self, messages: list[dict]) -> list[dict]:
        if type(messages) is not list:
            raise WorkerInputViolation("provider history must be an exact list")
        # At the actual HTTP boundary every supplied field is retained. Only
        # omissions of null fields already present in public replies are allowed.
        projected = [_wire_message(item, keep_extra=self._provider_wire) for item in messages]
        matches = projected == self._history
        if self._provider_wire:
            matches = len(projected) == len(self._history) and all(
                _matches_public_wire(json.loads(expected), json.loads(observed))
                for expected, observed in zip(self._history, projected)
            )
        if not matches:
            raise WorkerInputViolation("message has no permitted public provenance")
        return [json.loads(item) for item in projected]

    def record_provider_messages(self, *messages: dict) -> None:
        """Called by the model only for its actual response/format-error path."""
        self._history.extend(_wire_message(item) for item in messages)
