"""Contract helpers for P2 durable mailbox receive waits.

This module deliberately contains validation and canonicalization only.  The
durable artifact lifecycle remains in :mod:`synapse.application`, while the
interpreter remains responsible for executing the existing ReceiveBlock flow.
"""
from __future__ import annotations

import copy
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse.hardening import canonical_json


MAILBOX_WAIT_SCHEMA_VERSION = "synapse.mailbox.wait.v1"
MAILBOX_MESSAGE_KIND = "mailbox_message"
MAILBOX_TIMEOUT_KIND = "mailbox_timeout"
MAILBOX_RESUME_HASH_SCHEMA = "synapse.mailbox.resume.hash.v1"
MAILBOX_ARGS_HASH_SCHEMA = "synapse.mailbox.args.v1"
MAILBOX_WAIT_REASONS = frozenset({"awaiting_message", "awaiting_message_or_timeout"})

_MESSAGE_FIELDS = frozenset({"sender", "receiver", "method", "args"})
_MESSAGE_EVENT_FIELDS = frozenset({"sender", "receiver", "method", "args", "payload"})
_MESSAGE_RESUME_FIELDS = frozenset({"kind", "message_id", "actor", "message"})
_TIMEOUT_RESUME_FIELDS = frozenset({"kind", "actor", "timeout"})
_WAIT_PAYLOAD_FIELDS = frozenset({"mailbox_wait_schema", "actor", "timeout", "receive_shape"})
_RECEIVE_SHAPE_FIELDS = frozenset({"patterns", "has_else"})


class MailboxWaitValidationError(ValueError):
    """A mailbox resume or replay value violates the P2 mailbox contract."""


@dataclass(frozen=True)
class MailboxResume:
    """Validated payload supplied to the inline ReceiveBlock continuation."""

    kind: str
    internal_value: dict[str, Any]
    signal_hash: str


def _strict_json_projection(value: Any, path: str = "$", seen: set[int] | None = None) -> Any:
    """Return a deep strict-JSON projection without coercing host values."""

    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MailboxWaitValidationError(f"{path}: non-finite float is not strict JSON")
        return value
    if isinstance(value, list):
        marker = id(value)
        if marker in seen:
            raise MailboxWaitValidationError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        try:
            return [_strict_json_projection(item, f"{path}[{index}]", seen) for index, item in enumerate(value)]
        finally:
            seen.remove(marker)
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise MailboxWaitValidationError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        try:
            projected: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise MailboxWaitValidationError(f"{path}: mapping key is not a string")
                projected[key] = _strict_json_projection(item, f"{path}.{key}", seen)
            return projected
        finally:
            seen.remove(marker)
    raise MailboxWaitValidationError(f"{path}: {type(value).__name__} is not strict JSON")


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    projected = _strict_json_projection(value, path)
    if not isinstance(projected, dict):
        raise MailboxWaitValidationError(f"{path}: expected object")
    return projected


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    if set(value) != expected:
        raise MailboxWaitValidationError(f"{path}: invalid field set")


def _require_string(value: Any, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or (non_empty and not value):
        raise MailboxWaitValidationError(f"{path}: expected {'non-empty ' if non_empty else ''}string")
    return value


def _canonical_hash(value: Any) -> str:
    """Hash strict JSON through the repository event-chain canonical encoder."""

    projected = _strict_json_projection(value)
    return "sha256:" + hashlib.sha256(canonical_json(projected).encode("utf-8")).hexdigest()


def build_mailbox_wait_payload(actor: str, timeout: Any, has_else: bool) -> dict[str, Any]:
    """Create the strict active-suspension payload for a ReceiveBlock."""

    _require_string(actor, "actor", non_empty=True)
    projected_timeout = _strict_json_projection(timeout, "timeout")
    payload = {
        "mailbox_wait_schema": MAILBOX_WAIT_SCHEMA_VERSION,
        "actor": actor,
        "timeout": projected_timeout,
        "receive_shape": {"patterns": 1, "has_else": bool(has_else)},
    }
    validate_mailbox_wait_payload(payload, "awaiting_message_or_timeout" if timeout is not None else "awaiting_message")
    return payload


def validate_mailbox_wait_payload(payload: Any, reason: str) -> dict[str, Any]:
    """Validate a persisted mailbox wait payload before it is trusted."""

    if reason not in MAILBOX_WAIT_REASONS:
        raise MailboxWaitValidationError("unsupported mailbox wait reason")
    value = _require_mapping(payload, "mailbox wait payload")
    _require_exact_fields(value, _WAIT_PAYLOAD_FIELDS, "mailbox wait payload")
    if value["mailbox_wait_schema"] != MAILBOX_WAIT_SCHEMA_VERSION:
        raise MailboxWaitValidationError("mailbox wait payload: schema mismatch")
    _require_string(value["actor"], "mailbox wait payload.actor", non_empty=True)
    shape = _require_mapping(value["receive_shape"], "mailbox wait payload.receive_shape")
    _require_exact_fields(shape, _RECEIVE_SHAPE_FIELDS, "mailbox wait payload.receive_shape")
    if shape["patterns"] != 1 or not isinstance(shape["has_else"], bool):
        raise MailboxWaitValidationError("mailbox wait payload: invalid receive shape")
    if reason == "awaiting_message" and value["timeout"] is not None:
        raise MailboxWaitValidationError("mailbox wait payload: awaiting_message requires null timeout")
    if reason == "awaiting_message_or_timeout" and value["timeout"] is None:
        raise MailboxWaitValidationError("mailbox wait payload: timeout is required")
    return value


def _canonical_internal_message(message: Any, actor: str, *, allow_payload: bool) -> dict[str, Any]:
    value = _require_mapping(message, "message")
    expected = _MESSAGE_EVENT_FIELDS if allow_payload else _MESSAGE_FIELDS
    _require_exact_fields(value, expected, "message")
    sender = _require_string(value["sender"], "message.sender")
    receiver = _require_string(value["receiver"], "message.receiver")
    method = _require_string(value["method"], "message.method")
    args = value["args"]
    if not isinstance(args, list):
        raise MailboxWaitValidationError("message.args: expected list")
    canonical = {
        "sender": sender,
        "receiver": receiver,
        "method": method,
        "args": copy.deepcopy(args),
        "payload": copy.deepcopy(args[0] if len(args) == 1 else args),
    }
    if receiver != actor:
        raise MailboxWaitValidationError("message.receiver does not match active actor")
    if allow_payload and value["payload"] != canonical["payload"]:
        raise MailboxWaitValidationError("message.payload is not derivable from message.args")
    return canonical


def _message_resume(signal: Any, payload: dict[str, Any]) -> MailboxResume:
    value = _require_mapping(signal, "mailbox_message")
    _require_exact_fields(value, _MESSAGE_RESUME_FIELDS, "mailbox_message")
    if value["kind"] != MAILBOX_MESSAGE_KIND:
        raise MailboxWaitValidationError("mailbox_message: kind mismatch")
    message_id = _require_string(value["message_id"], "mailbox_message.message_id", non_empty=True)
    actor = _require_string(value["actor"], "mailbox_message.actor", non_empty=True)
    if actor != payload["actor"]:
        raise MailboxWaitValidationError("mailbox_message.actor does not match active actor")
    message = _canonical_internal_message(value["message"], actor, allow_payload=False)
    args_hash = _canonical_hash({"schema": MAILBOX_ARGS_HASH_SCHEMA, "args": message["args"]})
    signal_hash = _canonical_hash({
        "schema": MAILBOX_RESUME_HASH_SCHEMA,
        "kind": MAILBOX_MESSAGE_KIND,
        "message_id": message_id,
        "actor": actor,
        "sender": message["sender"],
        "receiver": message["receiver"],
        "method": message["method"],
        "args_hash": args_hash,
    })
    return MailboxResume(MAILBOX_MESSAGE_KIND, message, signal_hash)


def _timeout_resume(signal: Any, reason: str, payload: dict[str, Any]) -> MailboxResume:
    value = _require_mapping(signal, "mailbox_timeout")
    _require_exact_fields(value, _TIMEOUT_RESUME_FIELDS, "mailbox_timeout")
    if value["kind"] != MAILBOX_TIMEOUT_KIND:
        raise MailboxWaitValidationError("mailbox_timeout: kind mismatch")
    actor = _require_string(value["actor"], "mailbox_timeout.actor", non_empty=True)
    if actor != payload["actor"]:
        raise MailboxWaitValidationError("mailbox_timeout.actor does not match active actor")
    if value["timeout"] is not True:
        raise MailboxWaitValidationError("mailbox_timeout.timeout must be true")
    if reason != "awaiting_message_or_timeout":
        raise MailboxWaitValidationError("mailbox_timeout is invalid for awaiting_message")
    return MailboxResume(
        MAILBOX_TIMEOUT_KIND,
        {"timeout": True},
        _canonical_hash({
            "schema": MAILBOX_RESUME_HASH_SCHEMA,
            "kind": MAILBOX_TIMEOUT_KIND,
            "actor": actor,
            "timeout": True,
        }),
    )


def validate_mailbox_resume(signal: Any, reason: str, payload: Any) -> MailboxResume:
    """Validate an external mailbox resume and create its internal flow value."""

    active_payload = validate_mailbox_wait_payload(payload, reason)
    value = _require_mapping(signal, "mailbox resume")
    kind = value.get("kind")
    if kind == MAILBOX_MESSAGE_KIND:
        return _message_resume(value, active_payload)
    if kind == MAILBOX_TIMEOUT_KIND:
        return _timeout_resume(value, reason, active_payload)
    raise MailboxWaitValidationError("mailbox resume: unsupported kind")


def normalized_mailbox_signal_hash(signal: Any) -> str:
    """Hash a mailbox envelope for duplicate lookup when its active boundary is gone."""

    value = _require_mapping(signal, "mailbox resume")
    kind = value.get("kind")
    if kind == MAILBOX_MESSAGE_KIND:
        # This validates the envelope without selecting a target suspension.
        _require_exact_fields(value, _MESSAGE_RESUME_FIELDS, "mailbox_message")
        message_id = _require_string(value["message_id"], "mailbox_message.message_id", non_empty=True)
        actor = _require_string(value["actor"], "mailbox_message.actor", non_empty=True)
        message = _canonical_internal_message(value["message"], actor, allow_payload=False)
        args_hash = _canonical_hash({"schema": MAILBOX_ARGS_HASH_SCHEMA, "args": message["args"]})
        return _canonical_hash({
            "schema": MAILBOX_RESUME_HASH_SCHEMA,
            "kind": MAILBOX_MESSAGE_KIND,
            "message_id": message_id,
            "actor": actor,
            "sender": message["sender"],
            "receiver": message["receiver"],
            "method": message["method"],
            "args_hash": args_hash,
        })
    if kind == MAILBOX_TIMEOUT_KIND:
        _require_exact_fields(value, _TIMEOUT_RESUME_FIELDS, "mailbox_timeout")
        actor = _require_string(value["actor"], "mailbox_timeout.actor", non_empty=True)
        if value["timeout"] is not True:
            raise MailboxWaitValidationError("mailbox_timeout.timeout must be true")
        return _canonical_hash({
            "schema": MAILBOX_RESUME_HASH_SCHEMA,
            "kind": MAILBOX_TIMEOUT_KIND,
            "actor": actor,
            "timeout": True,
        })
    raise MailboxWaitValidationError("mailbox resume: unsupported kind")


def is_mailbox_resume_signal(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("kind") in {MAILBOX_MESSAGE_KIND, MAILBOX_TIMEOUT_KIND}


def is_internal_timeout_marker(value: Any) -> bool:
    return isinstance(value, Mapping) and dict(value) == {"timeout": True}


def validate_replayed_message_received_event(event: Any, actor: str) -> dict[str, Any]:
    value = _require_mapping(event, "message_received event")
    if value.get("type") != "message_received":
        raise MailboxWaitValidationError("expected message_received event")
    if value.get("actor") != actor:
        raise MailboxWaitValidationError("message_received actor does not match active actor")
    return _canonical_internal_message(value.get("message"), actor, allow_payload=True)


def validate_replayed_receive_timeout_event(event: Any, actor: str, timeout: Any) -> None:
    value = _require_mapping(event, "receive_timeout event")
    if value.get("type") != "receive_timeout":
        raise MailboxWaitValidationError("expected receive_timeout event")
    if value.get("actor") != actor:
        raise MailboxWaitValidationError("receive_timeout actor does not match active actor")
    if timeout is None:
        raise MailboxWaitValidationError("receive_timeout is invalid without timeout expression")
    if value.get("timeout") != timeout:
        raise MailboxWaitValidationError("receive_timeout value does not match receive boundary")


__all__ = [
    "MAILBOX_MESSAGE_KIND",
    "MAILBOX_TIMEOUT_KIND",
    "MAILBOX_WAIT_REASONS",
    "MailboxResume",
    "MailboxWaitValidationError",
    "build_mailbox_wait_payload",
    "is_internal_timeout_marker",
    "is_mailbox_resume_signal",
    "normalized_mailbox_signal_hash",
    "validate_mailbox_resume",
    "validate_mailbox_wait_payload",
    "validate_replayed_message_received_event",
    "validate_replayed_receive_timeout_event",
]
