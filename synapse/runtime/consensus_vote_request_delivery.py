"""P3c-N2 deterministic vote request delivery domain helpers.

This module owns request event/message schemas, request identity hashes,
request projection shape, and fresh request/response binding checks.  It does
not deliver messages, mutate interpreter state, or compute consensus results.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any

from synapse.hardening import canonical_json


REQUEST_EVENT_TYPE = "distributed_consensus_vote_requested"
REQUEST_EVENT_SCHEMA_VERSION = "consensus.vote.request.event.v1"
REQUEST_MESSAGE_KIND = "consensus_vote_request"
REQUEST_MESSAGE_SCHEMA_VERSION = "consensus.vote.request.v1"
REQUEST_BATCH_SCHEMA_VERSION = "consensus.vote.request.batch.v1"
REQUEST_ID_SCHEMA_VERSION = "consensus.vote.request.id.v1"
REQUEST_HASH_SCHEMA_VERSION = "consensus.vote.request.hash.v1"
PROPOSAL_VIEW_SCHEMA_VERSION = "consensus.vote.request.proposal.v1"
PROPOSAL_VIEW_HASH_SCHEMA_VERSION = "consensus.vote.request.proposal.hash.v1"
REQUEST_PROJECTION_SCHEMA_VERSION = "consensus.vote.request.projection.v1"

REQUEST_PROJECTION_STATES = frozenset({"collecting", "completed", "terminal"})

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUEST_EVENT_FIELDS = frozenset({
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "coordinator",
    "participant",
    "participant_mailbox",
    "request_batch_id",
    "request_id",
    "request_hash",
    "proposal_view_hash",
    "strategy",
    "policy",
    "quorum",
    "timeout",
})
_REQUEST_MESSAGE_FIELDS = frozenset({
    "kind",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "coordinator",
    "participant",
    "participant_mailbox",
    "request_batch_id",
    "request_id",
    "request_hash",
    "proposal_view_hash",
    "strategy",
    "policy",
    "quorum",
    "timeout",
    "proposal_view",
})
_PROJECTION_FIELDS = frozenset({
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "coordinator",
    "request_batch_id",
    "requested_participants",
    "participant_mailboxes",
    "request_ids",
    "request_hashes",
    "delivery_status",
    "responses",
    "projection_state",
})


class ConsensusVoteRequestError(ValueError):
    """Stable fail-closed boundary for P3c-N2 request delivery."""


def _fail(reason: str) -> None:
    if reason.startswith("p3cn2_"):
        raise ConsensusVoteRequestError(reason)
    raise ConsensusVoteRequestError("p3cn2_" + reason)


def _strict_json(value: Any, path: str = "$", seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if value is None or type(value) in {str, bool}:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("vote_request_schema")
        return value
    if type(value) is list:
        marker = id(value)
        if marker in seen:
            _fail("vote_request_schema")
        seen.add(marker)
        try:
            return [_strict_json(item, f"{path}[{index}]", seen) for index, item in enumerate(value)]
        finally:
            seen.remove(marker)
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            _fail("vote_request_schema")
        seen.add(marker)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    _fail("vote_request_schema")
                result[key] = _strict_json(item, f"{path}.{key}", seen)
            return result
        finally:
            seen.remove(marker)
    _fail("vote_request_schema")


def _closed_object(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    projected = _strict_json(value)
    if type(projected) is not dict or set(projected) != fields:
        _fail(reason)
    return projected


def _require_digest(value: Any, reason: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _fail(reason)
    return value


def _require_string(value: Any, reason: str, *, non_empty: bool = False) -> str:
    if type(value) is not str or (non_empty and not value):
        _fail(reason)
    return value


def _hash(value: Any) -> str:
    payload = _strict_json(value)
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def proposal_view_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return _strict_json({
        "schema_version": PROPOSAL_VIEW_SCHEMA_VERSION,
        "proposal_id": result["proposal_id"],
        "topic": result["topic"],
        "participants": list(result["participants"]),
        "strategy": result["strategy"],
        "policy": result["policy"],
        "quorum": result["quorum"],
        "timeout": result["timeout"],
        "statement_identity": result.get("statement_identity")
        or result.get("proposal_statement_identity")
        or "",
    })


def compute_proposal_view_hash(proposal_view: Mapping[str, Any]) -> str:
    return _hash({
        "schema_version": PROPOSAL_VIEW_HASH_SCHEMA_VERSION,
        "proposal_view": proposal_view,
    })


def compute_request_batch_id(ticket: Mapping[str, Any], coordinator: str, participant_mailboxes: Mapping[str, str]) -> str:
    participants = list(ticket["missing_participants"])
    return _hash({
        "schema_version": REQUEST_BATCH_SCHEMA_VERSION,
        "ticket_id": ticket["ticket_id"],
        "proposal_id": ticket["proposal_id"],
        "statement_identity": ticket["statement_identity"],
        "coordinator": coordinator,
        "participants": participants,
        "participant_mailboxes": [[participant, participant_mailboxes[participant]] for participant in participants],
    })


def compute_request_id(
    *,
    request_batch_id: str,
    ticket_id: str,
    proposal_id: str,
    participant: str,
    participant_mailbox: str,
) -> str:
    return _hash({
        "schema_version": REQUEST_ID_SCHEMA_VERSION,
        "request_batch_id": request_batch_id,
        "ticket_id": ticket_id,
        "proposal_id": proposal_id,
        "participant": participant,
        "participant_mailbox": participant_mailbox,
    })


def compute_request_hash(event_without_hash: Mapping[str, Any]) -> str:
    return _hash({
        "schema_version": REQUEST_HASH_SCHEMA_VERSION,
        "request": event_without_hash,
    })


def build_vote_request_events(
    ticket: Mapping[str, Any],
    *,
    coordinator: str,
    participant_mailboxes: Mapping[str, str],
    proposal_view: Mapping[str, Any],
) -> list[dict[str, Any]]:
    participants = list(ticket["missing_participants"])
    if set(participants) != set(participant_mailboxes):
        _fail("p3cn2_vote_request_participant_mailbox")
    request_batch_id = compute_request_batch_id(ticket, coordinator, participant_mailboxes)
    proposal_view_hash = compute_proposal_view_hash(proposal_view)
    events: list[dict[str, Any]] = []
    for participant in participants:
        participant_mailbox = participant_mailboxes[participant]
        request_id = compute_request_id(
            request_batch_id=request_batch_id,
            ticket_id=ticket["ticket_id"],
            proposal_id=ticket["proposal_id"],
            participant=participant,
            participant_mailbox=participant_mailbox,
        )
        event_without_hash = {
            "type": REQUEST_EVENT_TYPE,
            "schema_version": REQUEST_EVENT_SCHEMA_VERSION,
            "ticket_id": ticket["ticket_id"],
            "proposal_id": ticket["proposal_id"],
            "statement_identity": ticket["statement_identity"],
            "coordinator": coordinator,
            "participant": participant,
            "participant_mailbox": participant_mailbox,
            "request_batch_id": request_batch_id,
            "request_id": request_id,
            "proposal_view_hash": proposal_view_hash,
            "strategy": ticket["strategy"],
            "policy": ticket["policy"],
            "quorum": ticket["quorum"],
            "timeout": ticket["timeout"],
        }
        events.append({**event_without_hash, "request_hash": compute_request_hash(event_without_hash)})
    return events


def validate_vote_request_event(event: Any) -> dict[str, Any]:
    value = _closed_object(event, _REQUEST_EVENT_FIELDS, "p3cn2_vote_request_event_schema")
    if value["type"] != REQUEST_EVENT_TYPE:
        _fail("p3cn2_vote_request_event_schema")
    if value["schema_version"] != REQUEST_EVENT_SCHEMA_VERSION:
        _fail("p3cn2_vote_request_event_schema")
    for field in ("ticket_id", "proposal_id", "request_batch_id", "request_id", "request_hash", "proposal_view_hash"):
        _require_digest(value[field], "p3cn2_vote_request_event_schema")
    for field in ("statement_identity", "coordinator", "participant", "participant_mailbox", "strategy"):
        _require_string(value[field], "p3cn2_vote_request_event_schema", non_empty=True)
    if value["policy"] is not None:
        _require_string(value["policy"], "p3cn2_vote_request_event_schema", non_empty=True)
    if value["quorum"] is not None and (type(value["quorum"]) is not int or value["quorum"] < 1):
        _fail("p3cn2_vote_request_event_schema")
    if type(value["timeout"]) is not int or value["timeout"] < 0:
        _fail("p3cn2_vote_request_event_schema")
    expected_hash = compute_request_hash({key: value[key] for key in value if key != "request_hash"})
    if value["request_hash"] != expected_hash:
        _fail("p3cn2_vote_request_replay_mismatch")
    return copy.deepcopy(value)


def build_vote_request_message(event: Mapping[str, Any], proposal_view: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_vote_request_event(event)
    message = {
        "kind": REQUEST_MESSAGE_KIND,
        "schema_version": REQUEST_MESSAGE_SCHEMA_VERSION,
        "ticket_id": value["ticket_id"],
        "proposal_id": value["proposal_id"],
        "statement_identity": value["statement_identity"],
        "coordinator": value["coordinator"],
        "participant": value["participant"],
        "participant_mailbox": value["participant_mailbox"],
        "request_batch_id": value["request_batch_id"],
        "request_id": value["request_id"],
        "request_hash": value["request_hash"],
        "proposal_view_hash": value["proposal_view_hash"],
        "strategy": value["strategy"],
        "policy": value["policy"],
        "quorum": value["quorum"],
        "timeout": value["timeout"],
        "proposal_view": copy.deepcopy(_strict_json(proposal_view)),
    }
    validate_vote_request_message(message, value)
    return message


def validate_vote_request_message(message: Any, event: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = _closed_object(message, _REQUEST_MESSAGE_FIELDS, "p3cn2_vote_request_schema")
    if value["kind"] != REQUEST_MESSAGE_KIND or value["schema_version"] != REQUEST_MESSAGE_SCHEMA_VERSION:
        _fail("p3cn2_vote_request_schema")
    for field in (
        "ticket_id",
        "proposal_id",
        "statement_identity",
        "coordinator",
        "participant",
        "participant_mailbox",
        "request_batch_id",
        "request_id",
        "request_hash",
        "proposal_view_hash",
        "strategy",
        "policy",
        "quorum",
        "timeout",
    ):
        if event is not None and value[field] != event[field]:
            _fail("p3cn2_vote_request_schema")
    if compute_proposal_view_hash(value["proposal_view"]) != value["proposal_view_hash"]:
        _fail("p3cn2_vote_request_schema")
    return copy.deepcopy(value)


def new_request_projection(ticket: Mapping[str, Any], events: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not events:
        _fail("p3cn2_vote_request_participant_not_missing")
    validated = [validate_vote_request_event(event) for event in events]
    request_batch_ids = {event["request_batch_id"] for event in validated}
    if len(request_batch_ids) != 1:
        _fail("p3cn2_vote_request_duplicate")
    requested = [event["participant"] for event in validated]
    if requested != list(ticket["missing_participants"]) or len(set(requested)) != len(requested):
        _fail("p3cn2_vote_request_participant_not_missing")
    return {
        "schema_version": REQUEST_PROJECTION_SCHEMA_VERSION,
        "ticket_id": ticket["ticket_id"],
        "proposal_id": ticket["proposal_id"],
        "statement_identity": ticket["statement_identity"],
        "coordinator": validated[0]["coordinator"],
        "request_batch_id": validated[0]["request_batch_id"],
        "requested_participants": requested,
        "participant_mailboxes": {event["participant"]: event["participant_mailbox"] for event in validated},
        "request_ids": {event["participant"]: event["request_id"] for event in validated},
        "request_hashes": {event["participant"]: event["request_hash"] for event in validated},
        "delivery_status": {event["participant"]: "requested" for event in validated},
        "responses": {},
        "projection_state": "collecting",
    }


def validate_request_projection(projection: Any) -> dict[str, Any]:
    value = _closed_object(projection, _PROJECTION_FIELDS, "p3cn2_vote_request_schema")
    if value["schema_version"] != REQUEST_PROJECTION_SCHEMA_VERSION:
        _fail("p3cn2_vote_request_schema")
    if value["projection_state"] not in REQUEST_PROJECTION_STATES:
        _fail("p3cn2_vote_request_schema")
    requested = value["requested_participants"]
    if type(requested) is not list or not requested or not all(type(item) is str and item for item in requested):
        _fail("p3cn2_vote_request_schema")
    expected = set(requested)
    for field in ("participant_mailboxes", "request_ids", "request_hashes", "delivery_status"):
        if type(value[field]) is not dict or set(value[field]) != expected:
            _fail("p3cn2_vote_request_schema")
    if type(value["responses"]) is not dict or not set(value["responses"]).issubset(expected):
        _fail("p3cn2_vote_request_schema")
    return copy.deepcopy(value)


def apply_request_event(projection: Mapping[str, Any] | None, event: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_vote_request_event(event)
    if projection is None:
        return {
            "schema_version": REQUEST_PROJECTION_SCHEMA_VERSION,
            "ticket_id": value["ticket_id"],
            "proposal_id": value["proposal_id"],
            "statement_identity": value["statement_identity"],
            "coordinator": value["coordinator"],
            "request_batch_id": value["request_batch_id"],
            "requested_participants": [value["participant"]],
            "participant_mailboxes": {value["participant"]: value["participant_mailbox"]},
            "request_ids": {value["participant"]: value["request_id"]},
            "request_hashes": {value["participant"]: value["request_hash"]},
            "delivery_status": {value["participant"]: "requested"},
            "responses": {},
            "projection_state": "collecting",
        }
    next_projection = validate_request_projection(projection)
    for field in ("ticket_id", "proposal_id", "statement_identity", "coordinator", "request_batch_id"):
        if next_projection[field] != value[field]:
            _fail("p3cn2_vote_request_duplicate")
    participant = value["participant"]
    if participant in next_projection["request_ids"]:
        if (
            next_projection["request_ids"][participant] == value["request_id"]
            and next_projection["request_hashes"][participant] == value["request_hash"]
            and next_projection["participant_mailboxes"][participant] == value["participant_mailbox"]
        ):
            return next_projection
        _fail("p3cn2_vote_request_duplicate")
    next_projection["requested_participants"].append(participant)
    next_projection["participant_mailboxes"][participant] = value["participant_mailbox"]
    next_projection["request_ids"][participant] = value["request_id"]
    next_projection["request_hashes"][participant] = value["request_hash"]
    next_projection["delivery_status"][participant] = "requested"
    return next_projection


def validate_fresh_response_binding(response: Mapping[str, Any], projection: Mapping[str, Any] | None, ticket: Mapping[str, Any]) -> None:
    if projection is None:
        if response.get("request_id") is None:
            return
        _fail("p3cn2_unsolicited_response")
    value = validate_request_projection(projection)
    if ticket.get("projection_state") != "pending":
        _fail("p3cn2_vote_request_terminal_ticket")
    if response.get("ticket_id") != value["ticket_id"] or response.get("proposal_id") != value["proposal_id"]:
        _fail("p3cn2_unsolicited_response")
    participant = response.get("participant")
    if participant not in value["requested_participants"]:
        _fail("p3cn2_unsolicited_response")
    request_id = response.get("request_id")
    if type(request_id) is not str or not request_id:
        _fail("p3cn2_unsolicited_response")
    if request_id != value["request_ids"][participant]:
        _fail("p3cn2_unsolicited_response")
    participant_mailbox = response.get("participant_mailbox")
    if participant_mailbox is not None and participant_mailbox != value["participant_mailboxes"][participant]:
        _fail("p3cn2_unsolicited_response")


def mark_response_received(projection: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_request_projection(projection)
    participant = response["participant"]
    if participant not in value["requested_participants"]:
        _fail("p3cn2_unsolicited_response")
    value["responses"][participant] = {
        "request_id": response["request_id"],
        "response_id": response["response_id"],
    }
    if set(value["responses"]) == set(value["requested_participants"]):
        value["projection_state"] = "completed"
    return value


__all__ = [
    "ConsensusVoteRequestError",
    "REQUEST_EVENT_TYPE",
    "REQUEST_EVENT_SCHEMA_VERSION",
    "REQUEST_MESSAGE_KIND",
    "REQUEST_MESSAGE_SCHEMA_VERSION",
    "apply_request_event",
    "build_vote_request_events",
    "build_vote_request_message",
    "compute_proposal_view_hash",
    "compute_request_batch_id",
    "compute_request_hash",
    "compute_request_id",
    "mark_response_received",
    "new_request_projection",
    "proposal_view_payload",
    "validate_fresh_response_binding",
    "validate_request_projection",
    "validate_vote_request_event",
    "validate_vote_request_message",
]
