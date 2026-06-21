"""P3c-2 validation and projection helpers for consensus ticket resolution."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any, Dict, Mapping as TypingMapping


RESOLUTION_KIND = "consensus_ticket_resolution"
RESOLUTION_EVENT_TYPE = "distributed_consensus_ticket_resolved"
RESOLUTION_SCHEMA_VERSION = "consensus.ticket.resolution.event.v1"
RESOLUTION_VOTE_STATES = {"yes", "no", "abstain"}

_ALLOWED_RESOLUTION_REQUEST_FIELDS = {
    "kind",
    "ticket_id",
    "missing_participants",
    "votes_hash",
}
_ALLOWED_RESOLUTION_SIGNAL_FIELDS = {"kind", "ticket_id", "votes"}
_ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS = {
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "resolution_votes",
    "votes_final",
    "vote_counts_final",
    "outcome",
    "reason",
    "votes_hash_final",
    "result_hash_final",
}
_REQUIRED_TICKET_FIELDS = {
    "ticket_id",
    "proposal_id",
    "statement_identity",
    "participants",
    "missing_participants",
    "votes",
    "vote_counts",
    "votes_hash",
    "strategy",
    "policy",
    "quorum",
    "timeout",
    "projection_state",
}


class ConsensusTicketResolutionError(Exception):
    """Stable validation boundary for P3c-2 request, signal, and event data."""


def _fail(reason: str) -> None:
    raise ConsensusTicketResolutionError("invalid_request: consensus_ticket_resolution_" + reason)


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("unsupported_json_value")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("non_string_mapping_key")
            _validate_json(item)
        return
    _fail("unsupported_json_value")


def _closed_mapping(value: Any, fields: set[str], reason: str) -> TypingMapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(reason + "_not_mapping")
    _validate_json(value)
    keys = set(value.keys())
    if keys != fields:
        _fail(reason + "_schema")
    return value


def validate_ticket_projection(ticket: Any, *, allow_resolved: bool = True) -> TypingMapping[str, Any]:
    if not isinstance(ticket, Mapping):
        _fail("ticket_not_found")
    _validate_json(ticket)
    if not _REQUIRED_TICKET_FIELDS.issubset(ticket.keys()):
        _fail("ticket_projection")
    if ticket.get("projection_state") not in ({"pending", "resolved"} if allow_resolved else {"pending"}):
        _fail("ticket_projection_state")
    participants = ticket.get("participants")
    missing = ticket.get("missing_participants")
    votes = ticket.get("votes")
    if not isinstance(ticket.get("ticket_id"), str) or not isinstance(ticket.get("proposal_id"), str):
        _fail("ticket_identity")
    if not isinstance(ticket.get("statement_identity"), str):
        _fail("ticket_statement_identity")
    if not isinstance(participants, list) or not all(isinstance(value, str) for value in participants):
        _fail("ticket_participants")
    if not isinstance(missing, list) or not all(isinstance(value, str) for value in missing):
        _fail("ticket_missing_participants")
    if missing != sorted(missing) or not set(missing).issubset(set(participants)):
        _fail("ticket_missing_participants")
    if not isinstance(votes, Mapping) or set(votes.keys()) != set(participants):
        _fail("ticket_votes")
    if any(votes.get(participant) != "missing" for participant in missing):
        _fail("ticket_votes")
    return ticket


def validate_resolution_request_payload(request: Any, ticket: Any) -> str:
    request, trusted_ticket_id = validate_resolution_request_shape(request)
    ticket = validate_ticket_projection(ticket)
    if trusted_ticket_id != ticket["ticket_id"]:
        _fail("request_ticket_id")
    missing = request.get("missing_participants")
    if not isinstance(missing, list) or not all(isinstance(value, str) for value in missing):
        _fail("request_missing_participants")
    if missing != ticket["missing_participants"]:
        _fail("request_missing_participants")
    if request.get("votes_hash") != ticket["votes_hash"]:
        _fail("request_votes_hash")
    return trusted_ticket_id


def validate_resolution_request_shape(request: Any) -> tuple[TypingMapping[str, Any], str]:
    request = _closed_mapping(request, _ALLOWED_RESOLUTION_REQUEST_FIELDS, "request")
    if request.get("kind") != RESOLUTION_KIND:
        _fail("request_kind")
    trusted_ticket_id = request.get("ticket_id")
    if not isinstance(trusted_ticket_id, str):
        _fail("request_ticket_id")
    missing = request.get("missing_participants")
    if not isinstance(missing, list) or not all(isinstance(value, str) for value in missing):
        _fail("request_missing_participants")
    if not isinstance(request.get("votes_hash"), str):
        _fail("request_votes_hash")
    return request, trusted_ticket_id


def validate_resolution_signal_payload(signal: Any, trusted_ticket_id: str, ticket: Any) -> Dict[str, str]:
    signal = _closed_mapping(signal, _ALLOWED_RESOLUTION_SIGNAL_FIELDS, "signal")
    if signal.get("kind") != RESOLUTION_KIND:
        _fail("signal_kind")
    if signal.get("ticket_id") != trusted_ticket_id:
        _fail("signal_ticket_id")
    ticket = validate_ticket_projection(ticket)
    votes = signal.get("votes")
    if not isinstance(votes, Mapping):
        _fail("signal_votes")
    _validate_json(votes)
    if not all(isinstance(key, str) for key in votes):
        _fail("signal_votes")
    if set(votes.keys()) != set(ticket["missing_participants"]):
        _fail("signal_vote_coverage")
    if not all(isinstance(vote, str) and vote in RESOLUTION_VOTE_STATES for vote in votes.values()):
        _fail("signal_vote_state")
    return {participant: votes[participant] for participant in ticket["missing_participants"]}


def validate_resolution_event_schema(event: Any) -> TypingMapping[str, Any]:
    event = _closed_mapping(event, _ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS, "event")
    if event.get("type") != RESOLUTION_EVENT_TYPE:
        _fail("event_type")
    if event.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        _fail("event_schema_version")
    return event


def validate_projection_transition(ticket: Any, event: Any) -> None:
    ticket = validate_ticket_projection(ticket, allow_resolved=False)
    event = validate_resolution_event_schema(event)
    for field in ("ticket_id", "proposal_id", "statement_identity"):
        if event.get(field) != ticket.get(field):
            _fail("projection_transition")


def validate_idempotent_duplicate_resolution(ticket: Any, resolution_votes: Mapping[str, str]) -> bool:
    ticket = validate_ticket_projection(ticket)
    if ticket["projection_state"] == "pending":
        return False
    existing_votes = ticket.get("resolution_votes")
    if not isinstance(existing_votes, Mapping) or dict(existing_votes) != dict(resolution_votes):
        _fail("conflicting_duplicate")
    return True


def build_resolved_projection(ticket: Any, event: Any) -> Dict[str, Any]:
    validate_projection_transition(ticket, event)
    projection = copy.deepcopy(dict(ticket))
    projection["projection_state"] = "resolved"
    for field in (
        "resolution_votes",
        "votes_final",
        "vote_counts_final",
        "outcome",
        "reason",
        "votes_hash_final",
        "result_hash_final",
    ):
        projection[field] = copy.deepcopy(event[field])
    return projection


__all__ = [
    "ConsensusTicketResolutionError",
    "RESOLUTION_EVENT_TYPE",
    "RESOLUTION_KIND",
    "RESOLUTION_SCHEMA_VERSION",
    "_ALLOWED_CONSENSUS_TICKET_RESOLVED_EVENT_FIELDS",
    "build_resolved_projection",
    "validate_idempotent_duplicate_resolution",
    "validate_projection_transition",
    "validate_resolution_event_schema",
    "validate_resolution_request_payload",
    "validate_resolution_request_shape",
    "validate_resolution_signal_payload",
    "validate_ticket_projection",
]
