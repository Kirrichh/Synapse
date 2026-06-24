"""P3c-N1 import and mailbox vote-response domain validation.

This module deliberately owns only the deterministic domain boundary around
``consensus_ticket_import`` and ``consensus_vote_response`` mailbox messages.
It neither delivers messages nor performs the final consensus reduction; the
interpreter delegates terminal reduction to ``ConsensusEngine``.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any

from synapse.hardening import canonical_json

from .consensus_ticket_resolution import (
    ConsensusTicketLifecycleError,
    ConsensusTicketResolutionError,
    validate_ticket_projection,
)


IMPORT_KIND = "consensus_ticket_import"
IMPORT_SCHEMA_VERSION = "consensus.ticket.import.v1"
IMPORT_EVENT_TYPE = "distributed_consensus_ticket_imported"
IMPORT_EVENT_SCHEMA_VERSION = "consensus.ticket.imported.event.v1"
VOTE_RESPONSE_KIND = "consensus_vote_response"
VOTE_RESPONSE_SCHEMA_VERSION = "consensus.vote.response.v1"
VOTE_RECEIVED_EVENT_TYPE = "distributed_consensus_vote_received"
VOTE_RECEIVED_EVENT_SCHEMA_VERSION = "consensus.vote.received.event.v1"
VOTE_RESPONSE_HASH_SCHEMA_VERSION = "consensus.vote.response.hash.v1"
VOTE_COLLECTION_SCHEMA_VERSION = "consensus.vote.collection.projection.v1"
LIFECYCLE_CANCEL_KIND = "consensus_ticket_cancel"
LIFECYCLE_EXPIRE_KIND = "consensus_ticket_expire"
LIFECYCLE_CANCEL_SCHEMA_VERSION = "consensus.ticket.cancel.v1"
LIFECYCLE_EXPIRE_SCHEMA_VERSION = "consensus.ticket.expire.v1"
LIFECYCLE_CANCEL_EVENT_TYPE = "distributed_consensus_ticket_cancelled"
LIFECYCLE_EXPIRE_EVENT_TYPE = "distributed_consensus_ticket_expired"
LIFECYCLE_CANCEL_EVENT_SCHEMA_VERSION = "consensus.ticket.cancelled.event.v1"
LIFECYCLE_EXPIRE_EVENT_SCHEMA_VERSION = "consensus.ticket.expired.event.v1"

COORDINATOR = "global"
VOTE_STATES = frozenset({"yes", "no", "abstain", "missing"})
RESOLUTION_VOTE_STATES = frozenset({"yes", "no", "abstain"})
APPROVED_STRATEGIES = frozenset({"MajorityVote", "UnanimousVote", "NoVetoVote"})

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TICKET_FIELDS = frozenset({
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
})
_IMPORT_FIELDS = frozenset({"kind", "schema_version", "bootstrap_id", "coordinator", "ticket"})
_IMPORT_EVENT_FIELDS = frozenset({
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "bootstrap_id",
    "coordinator",
    "votes_hash",
    "ticket_import_hash",
    "ticket",
})
_VOTE_RESPONSE_FIELDS = frozenset({
    "kind",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "participant",
    "participant_mailbox",
    "coordinator",
    "vote",
    "reason",
    "request_id",
    "response_id",
})
_VOTE_RECEIVED_EVENT_FIELDS = frozenset({
    "type",
    "schema_version",
    "ticket_id",
    "proposal_id",
    "participant",
    "participant_mailbox",
    "coordinator",
    "vote",
    "reason",
    "response_id",
    "response_hash",
})
_VOTE_COUNT_FIELDS = frozenset({"yes", "no", "abstain", "missing"})
_COLLECTION_FIELDS = frozenset({
    "schema_version",
    "ticket_id",
    "proposal_id",
    "coordinator",
    "missing_participants",
    "participant_mailboxes",
    "votes_collected",
    "responses",
    "projection_state",
})
_LIFECYCLE_COMMAND_FIELDS = frozenset({"kind", "schema_version", "ticket_id", "proposal_id", "statement_identity", "coordinator", "reason", "request_id", "action_id"})
_LIFECYCLE_EVENT_FIELDS = frozenset({"type", "schema_version", "ticket_id", "proposal_id", "statement_identity", "coordinator", "reason", "request_id", "action_id", "action_hash"})


class ConsensusMailboxCollectionError(ValueError):
    """Stable fail-closed boundary for P3c-N1 mailbox-domain data."""


def _fail(reason: str) -> None:
    raise ConsensusMailboxCollectionError("p3cn1_" + reason)


def _lifecycle_fail(reason: str) -> None:
    raise _lifecycle_error(reason)


def _lifecycle_error(reason: str) -> ConsensusTicketLifecycleError:
    return ConsensusTicketLifecycleError("consensus_ticket_lifecycle_" + reason)


def _lifecycle_strict_json(value: Any, path: str, reason: str) -> Any:
    try:
        return _strict_json(value, path)
    except ConsensusMailboxCollectionError as exc:
        raise _lifecycle_error(reason) from exc


def _lifecycle_closed_object(
    value: Any,
    fields: frozenset[str],
    path: str,
    reason: str,
) -> dict[str, Any]:
    projected = _lifecycle_strict_json(value, path, reason)
    if not isinstance(projected, dict) or set(projected) != fields:
        raise _lifecycle_error(reason)
    return projected


def _lifecycle_require_string(value: Any, reason: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or (non_empty and not value):
        raise _lifecycle_error(reason)
    return value


def _lifecycle_require_digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _lifecycle_error(reason)


def _strict_json(value: Any, path: str = "$", seen: set[int] | None = None) -> Any:
    """Deep-copy only strict JSON values, rejecting cycles and host objects."""

    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(path + "_non_finite_float")
        return value
    if isinstance(value, list):
        marker = id(value)
        if marker in seen:
            _fail(path + "_cycle")
        seen.add(marker)
        try:
            return [_strict_json(item, f"{path}[{index}]", seen) for index, item in enumerate(value)]
        finally:
            seen.remove(marker)
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            _fail(path + "_cycle")
        seen.add(marker)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    _fail(path + "_non_string_mapping_key")
                result[key] = _strict_json(item, f"{path}.{key}", seen)
            return result
        finally:
            seen.remove(marker)
    _fail(path + "_unsupported_json_value")


def _closed_object(value: Any, fields: frozenset[str], path: str) -> dict[str, Any]:
    projected = _strict_json(value, path)
    if not isinstance(projected, dict):
        _fail(path + "_not_mapping")
    if set(projected) != fields:
        _fail(path + "_schema")
    return projected


def _require_string(value: Any, reason: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or (non_empty and not value):
        _fail(reason)
    return value


def _require_digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail(reason)
    return value


def _hash(value: Any) -> str:
    """Hash a strict JSON preimage through the repository canonical encoder."""

    return "sha256:" + hashlib.sha256(canonical_json(_strict_json(value)).encode("utf-8")).hexdigest()


def recompute_vote_counts(votes: Mapping[str, str]) -> dict[str, int]:
    """Return the canonical count map for the four approved ticket vote states."""

    return {state: sum(1 for vote in votes.values() if vote == state) for state in _VOTE_COUNT_FIELDS}


def votes_hash_preimage(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Build the approved participant-order vote hash preimage."""

    return {
        "schema_version": "consensus.votes.v1",
        "votes": [[participant, ticket["votes"][participant]] for participant in ticket["participants"]],
    }


def recompute_votes_hash(ticket: Mapping[str, Any]) -> str:
    return _hash(votes_hash_preimage(ticket))


def ticket_import_hash_preimage(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Return the full normalized pending projection used as import integrity data."""

    return _strict_json(ticket, "ticket_import_hash_ticket")


def compute_ticket_import_hash(ticket: Mapping[str, Any]) -> str:
    return _hash(ticket_import_hash_preimage(ticket))


def response_hash_preimage(response: Mapping[str, Any]) -> dict[str, Any]:
    """Build the approved non-self-referential vote-response hash preimage."""

    return {
        "schema_version": VOTE_RESPONSE_HASH_SCHEMA_VERSION,
        "ticket_id": response["ticket_id"],
        "proposal_id": response["proposal_id"],
        "participant": response["participant"],
        "participant_mailbox": response["participant_mailbox"],
        "coordinator": response["coordinator"],
        "vote": response["vote"],
        "reason": response["reason"],
        "request_id": response["request_id"],
        "response_id": response["response_id"],
    }


def compute_response_hash(response: Mapping[str, Any]) -> str:
    return _hash(response_hash_preimage(response))


def _runtime_ticket_fields(value: Any) -> dict[str, Any]:
    """Extract the closed ticket form from a legacy or imported runtime projection."""

    if not isinstance(value, Mapping):
        _fail("ticket_not_found")
    if not _TICKET_FIELDS.issubset(value.keys()):
        _fail("ticket_projection")
    return {field: value[field] for field in _TICKET_FIELDS}


def validate_pending_ticket_projection(ticket: Any) -> dict[str, Any]:
    """Validate a closed import projection and its integrity anchors.

    ``validate_ticket_projection`` remains the compatibility gate.  This
    function adds P3c-N1's exact field-set and recomputation requirements.
    """

    value = _closed_object(ticket, _TICKET_FIELDS, "ticket")
    try:
        validate_ticket_projection(value, allow_resolved=False)
    except ConsensusTicketResolutionError as exc:
        _fail("ticket_projection_" + str(exc))

    _require_digest(value["ticket_id"], "ticket_id")
    _require_digest(value["proposal_id"], "proposal_id")
    _require_string(value["statement_identity"], "ticket_statement_identity", non_empty=True)
    participants = value["participants"]
    missing = value["missing_participants"]
    if not isinstance(participants, list) or not participants or not all(isinstance(item, str) and item for item in participants):
        _fail("ticket_participants")
    if len(set(participants)) != len(participants):
        _fail("ticket_participants")
    if not isinstance(missing, list) or not all(isinstance(item, str) and item for item in missing):
        _fail("ticket_missing_participants")
    if missing != sorted(missing) or len(set(missing)) != len(missing) or not set(missing).issubset(participants):
        _fail("ticket_missing_participants")
    if not missing:
        _fail("ticket_not_pending")

    votes = value["votes"]
    if not isinstance(votes, dict) or set(votes) != set(participants):
        _fail("ticket_votes")
    if any(vote not in VOTE_STATES for vote in votes.values()):
        _fail("ticket_vote_state")
    actual_missing = sorted(participant for participant in participants if votes[participant] == "missing")
    if actual_missing != missing:
        _fail("ticket_missing_participants")

    counts = value["vote_counts"]
    if not isinstance(counts, dict) or set(counts) != _VOTE_COUNT_FIELDS:
        _fail("ticket_vote_counts")
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts.values()):
        _fail("ticket_vote_counts")
    if counts != recompute_vote_counts(votes):
        _fail("ticket_vote_counts_mismatch")
    _require_digest(value["votes_hash"], "ticket_votes_hash")
    if value["votes_hash"] != recompute_votes_hash(value):
        _fail("ticket_votes_hash_mismatch")

    if value["strategy"] not in APPROVED_STRATEGIES:
        _fail("ticket_strategy")
    quorum = value["quorum"]
    if quorum is not None and (isinstance(quorum, bool) or not isinstance(quorum, int) or quorum < 1 or quorum > len(participants)):
        _fail("ticket_quorum")
    if value["strategy"] in {"MajorityVote", "NoVetoVote"} and quorum is None:
        _fail("ticket_quorum")
    timeout = value["timeout"]
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0):
        _fail("ticket_timeout")
    if value["projection_state"] != "pending":
        _fail("ticket_projection_state")
    return copy.deepcopy(value)


def validate_ticket_import_payload(payload: Any) -> dict[str, Any]:
    value = _closed_object(payload, _IMPORT_FIELDS, "ticket_import")
    if value["kind"] != IMPORT_KIND:
        _fail("ticket_import_kind")
    if value["schema_version"] != IMPORT_SCHEMA_VERSION:
        _fail("ticket_import_schema_version")
    _require_string(value["bootstrap_id"], "ticket_import_bootstrap_id")
    if value["coordinator"] != COORDINATOR:
        _fail("ticket_import_coordinator")
    return {
        "kind": IMPORT_KIND,
        "schema_version": IMPORT_SCHEMA_VERSION,
        "bootstrap_id": value["bootstrap_id"],
        "coordinator": COORDINATOR,
        "ticket": validate_pending_ticket_projection(value["ticket"]),
    }


def build_ticket_import_event(payload: Any) -> dict[str, Any]:
    """Build the only P3c-N1 durable owner of an imported ticket projection."""

    value = validate_ticket_import_payload(payload)
    ticket = value["ticket"]
    return {
        "type": IMPORT_EVENT_TYPE,
        "schema_version": IMPORT_EVENT_SCHEMA_VERSION,
        "ticket_id": ticket["ticket_id"],
        "proposal_id": ticket["proposal_id"],
        "bootstrap_id": value["bootstrap_id"],
        "coordinator": COORDINATOR,
        "votes_hash": ticket["votes_hash"],
        "ticket_import_hash": compute_ticket_import_hash(ticket),
        "ticket": copy.deepcopy(ticket),
    }


def validate_ticket_import_event(event: Any) -> dict[str, Any]:
    value = _closed_object(event, _IMPORT_EVENT_FIELDS, "ticket_import_event")
    if value["type"] != IMPORT_EVENT_TYPE:
        _fail("ticket_import_event_type")
    if value["schema_version"] != IMPORT_EVENT_SCHEMA_VERSION:
        _fail("ticket_import_event_schema_version")
    _require_digest(value["ticket_id"], "ticket_import_event_ticket_id")
    _require_digest(value["proposal_id"], "ticket_import_event_proposal_id")
    _require_string(value["bootstrap_id"], "ticket_import_event_bootstrap_id")
    if value["coordinator"] != COORDINATOR:
        _fail("ticket_import_event_coordinator")
    _require_digest(value["votes_hash"], "ticket_import_event_votes_hash")
    _require_digest(value["ticket_import_hash"], "ticket_import_event_hash")
    ticket = validate_pending_ticket_projection(value["ticket"])
    for field in ("ticket_id", "proposal_id", "votes_hash"):
        if value[field] != ticket[field]:
            _fail("ticket_import_event_" + field)
    if value["ticket_import_hash"] != compute_ticket_import_hash(ticket):
        _fail("ticket_import_event_hash_mismatch")
    return {**value, "ticket": ticket}


def imported_ticket_from_event(event: Any) -> dict[str, Any]:
    return copy.deepcopy(validate_ticket_import_event(event)["ticket"])


def validate_import_idempotency(
    event: Mapping[str, Any],
    prior_events: list[Any],
    existing_ticket: Any | None,
) -> bool:
    """Return ``True`` for an idempotent import and fail before mutation on conflict."""

    candidate = validate_ticket_import_event(event)
    same_ticket_hash: str | None = None
    same_bootstrap_hash: str | None = None
    for prior_event in prior_events:
        if not isinstance(prior_event, Mapping) or prior_event.get("type") != IMPORT_EVENT_TYPE:
            continue
        prior = validate_ticket_import_event(prior_event)
        if prior["ticket_id"] == candidate["ticket_id"]:
            same_ticket_hash = prior["ticket_import_hash"]
        if prior["bootstrap_id"] == candidate["bootstrap_id"]:
            same_bootstrap_hash = prior["ticket_import_hash"]
    if same_ticket_hash is not None:
        if same_ticket_hash != candidate["ticket_import_hash"]:
            _fail("ticket_import_ticket_conflict")
        return True
    if same_bootstrap_hash is not None:
        if same_bootstrap_hash != candidate["ticket_import_hash"]:
            _fail("ticket_import_bootstrap_conflict")
        return True
    if existing_ticket is not None:
        projection = validate_pending_ticket_projection(_runtime_ticket_fields(existing_ticket))
        if projection["ticket_id"] != candidate["ticket_id"]:
            _fail("ticket_import_ticket_conflict")
        if compute_ticket_import_hash(projection) != candidate["ticket_import_hash"]:
            _fail("ticket_import_ticket_conflict")
        return True
    return False


def _message_payload(message: Any, expected_method: str) -> dict[str, Any]:
    value = _strict_json(message, "mailbox_message")
    if not isinstance(value, dict):
        _fail("mailbox_message_not_mapping")
    if value.get("method") != expected_method:
        _fail("mailbox_message_method")
    args = value.get("args")
    if not isinstance(args, list) or not args:
        _fail("mailbox_message_args")
    if value.get("receiver") != COORDINATOR:
        _fail("mailbox_message_receiver")
    return _strict_json(args[0], "mailbox_message_arg")


def recognised_mailbox_method(message: Any) -> str | None:
    if not isinstance(message, Mapping):
        return None
    method = message.get("method")
    return method if method in {IMPORT_KIND, VOTE_RESPONSE_KIND, LIFECYCLE_CANCEL_KIND, LIFECYCLE_EXPIRE_KIND} else None


def ticket_import_payload_from_message(message: Any) -> dict[str, Any]:
    return validate_ticket_import_payload(_message_payload(message, IMPORT_KIND))


def validate_vote_response_payload(payload: Any) -> dict[str, Any]:
    value = _closed_object(payload, _VOTE_RESPONSE_FIELDS, "vote_response")
    if value["kind"] != VOTE_RESPONSE_KIND:
        _fail("vote_response_kind")
    if value["schema_version"] != VOTE_RESPONSE_SCHEMA_VERSION:
        _fail("vote_response_schema_version")
    _require_digest(value["ticket_id"], "vote_response_ticket_id")
    _require_digest(value["proposal_id"], "vote_response_proposal_id")
    _require_string(value["participant"], "vote_response_participant", non_empty=True)
    if value["participant_mailbox"] is not None:
        _require_string(value["participant_mailbox"], "vote_response_participant_mailbox", non_empty=True)
    if value["coordinator"] != COORDINATOR:
        _fail("vote_response_coordinator")
    if value["vote"] not in RESOLUTION_VOTE_STATES:
        _fail("vote_response_vote")
    if value["reason"] is not None:
        _require_string(value["reason"], "vote_response_reason")
    if value["request_id"] is not None:
        _require_string(value["request_id"], "vote_response_request_id")
    _require_string(value["response_id"], "vote_response_response_id", non_empty=True)
    return copy.deepcopy(value)


def vote_response_payload_from_message(message: Any) -> dict[str, Any]:
    return validate_vote_response_payload(_message_payload(message, VOTE_RESPONSE_KIND))


def _participant_mailbox_candidates(participant: str, spawned_actors: Any) -> list[str]:
    if not isinstance(spawned_actors, Mapping):
        return []
    candidates: list[str] = []
    for process_id, record in spawned_actors.items():
        if not isinstance(process_id, str) or not isinstance(record, Mapping):
            continue
        if record.get("actor_name") == participant:
            candidates.append(process_id)
    return sorted(set(candidates))


def validate_participant_mailbox_binding(response: Mapping[str, Any], spawned_actors: Any) -> None:
    """Allow null in the first path; require one replay-stable match for strings."""

    mailbox = response["participant_mailbox"]
    if mailbox is None:
        return
    candidates = _participant_mailbox_candidates(response["participant"], spawned_actors)
    if len(candidates) > 1:
        _fail("participant_mailbox_ambiguous")
    if len(candidates) != 1:
        _fail("participant_mailbox_binding")
    if mailbox != candidates[0]:
        _fail("participant_mailbox_mismatch")


def _validated_runtime_pending_ticket(ticket: Any) -> dict[str, Any]:
    projection = _runtime_ticket_fields(ticket)
    try:
        validate_ticket_projection(projection, allow_resolved=True)
    except ConsensusTicketResolutionError as exc:
        _fail("ticket_projection_" + str(exc))
    if projection.get("projection_state") != "pending":
        _fail("ticket_resolved")
    return projection


def new_collection_projection(ticket: Any, spawned_actors: Any) -> dict[str, Any]:
    ticket_value = _validated_runtime_pending_ticket(ticket)
    participant_mailboxes: dict[str, str | None] = {}
    for participant in ticket_value["missing_participants"]:
        candidates = _participant_mailbox_candidates(participant, spawned_actors)
        participant_mailboxes[participant] = candidates[0] if len(candidates) == 1 else None
    return {
        "schema_version": VOTE_COLLECTION_SCHEMA_VERSION,
        "ticket_id": ticket_value["ticket_id"],
        "proposal_id": ticket_value["proposal_id"],
        "coordinator": COORDINATOR,
        "missing_participants": list(ticket_value["missing_participants"]),
        "participant_mailboxes": participant_mailboxes,
        "votes_collected": {},
        "responses": {},
        "projection_state": "collecting",
    }


def validate_collection_projection(collection: Any, ticket: Any) -> dict[str, Any]:
    value = _closed_object(collection, _COLLECTION_FIELDS, "vote_collection")
    ticket_value = _validated_runtime_pending_ticket(ticket)
    if value["schema_version"] != VOTE_COLLECTION_SCHEMA_VERSION:
        _fail("vote_collection_schema_version")
    for field in ("ticket_id", "proposal_id"):
        if value[field] != ticket_value[field]:
            _fail("vote_collection_" + field)
    if value["coordinator"] != COORDINATOR:
        _fail("vote_collection_coordinator")
    if value["missing_participants"] != ticket_value["missing_participants"]:
        _fail("vote_collection_missing_participants")
    expected = set(ticket_value["missing_participants"])
    mailboxes = value["participant_mailboxes"]
    if not isinstance(mailboxes, dict) or set(mailboxes) != expected:
        _fail("vote_collection_participant_mailboxes")
    if any(mailbox is not None and not isinstance(mailbox, str) for mailbox in mailboxes.values()):
        _fail("vote_collection_participant_mailboxes")
    votes = value["votes_collected"]
    responses = value["responses"]
    if not isinstance(votes, dict) or not isinstance(responses, dict) or set(votes) != set(responses) or not set(votes).issubset(expected):
        _fail("vote_collection_votes")
    if any(vote not in RESOLUTION_VOTE_STATES for vote in votes.values()):
        _fail("vote_collection_vote_state")
    for participant, response in responses.items():
        if not isinstance(response, dict) or set(response) != {"response_id", "response_hash"}:
            _fail("vote_collection_response")
        _require_string(response["response_id"], "vote_collection_response_id", non_empty=True)
        _require_digest(response["response_hash"], "vote_collection_response_hash")
    expected_state = "coverage_complete" if set(votes) == expected else "collecting"
    if value["projection_state"] != expected_state:
        _fail("vote_collection_state")
    return copy.deepcopy(value)


def validate_response_for_ticket(
    response: Any,
    ticket: Any,
    spawned_actors: Any,
) -> dict[str, Any]:
    value = validate_vote_response_payload(response)
    ticket_value = _validated_runtime_pending_ticket(ticket)
    if value["ticket_id"] != ticket_value["ticket_id"]:
        _fail("vote_response_ticket_id")
    if value["proposal_id"] != ticket_value["proposal_id"]:
        _fail("vote_response_proposal_id")
    if value["participant"] not in ticket_value["missing_participants"]:
        _fail("vote_response_participant")
    validate_participant_mailbox_binding(value, spawned_actors)
    return value


def build_vote_received_event(response: Any) -> dict[str, Any]:
    value = validate_vote_response_payload(response)
    return {
        "type": VOTE_RECEIVED_EVENT_TYPE,
        "schema_version": VOTE_RECEIVED_EVENT_SCHEMA_VERSION,
        "ticket_id": value["ticket_id"],
        "proposal_id": value["proposal_id"],
        "participant": value["participant"],
        "participant_mailbox": value["participant_mailbox"],
        "coordinator": COORDINATOR,
        "vote": value["vote"],
        "reason": value["reason"],
        "response_id": value["response_id"],
        "response_hash": compute_response_hash(value),
    }


def validate_vote_received_event(event: Any) -> dict[str, Any]:
    value = _closed_object(event, _VOTE_RECEIVED_EVENT_FIELDS, "vote_received_event")
    if value["type"] != VOTE_RECEIVED_EVENT_TYPE:
        _fail("vote_received_event_type")
    if value["schema_version"] != VOTE_RECEIVED_EVENT_SCHEMA_VERSION:
        _fail("vote_received_event_schema_version")
    _require_digest(value["ticket_id"], "vote_received_event_ticket_id")
    _require_digest(value["proposal_id"], "vote_received_event_proposal_id")
    _require_string(value["participant"], "vote_received_event_participant", non_empty=True)
    if value["participant_mailbox"] is not None:
        _require_string(value["participant_mailbox"], "vote_received_event_participant_mailbox", non_empty=True)
    if value["coordinator"] != COORDINATOR:
        _fail("vote_received_event_coordinator")
    if value["vote"] not in RESOLUTION_VOTE_STATES:
        _fail("vote_received_event_vote")
    if value["reason"] is not None:
        _require_string(value["reason"], "vote_received_event_reason")
    _require_string(value["response_id"], "vote_received_event_response_id", non_empty=True)
    _require_digest(value["response_hash"], "vote_received_event_response_hash")
    return copy.deepcopy(value)


def apply_vote_received_event(collection: Any, ticket: Any, event: Any) -> tuple[dict[str, Any], bool]:
    """Apply an accepted event, or return an unchanged projection for an exact duplicate."""

    value = validate_collection_projection(collection, ticket)
    recorded = validate_vote_received_event(event)
    for field in ("ticket_id", "proposal_id", "coordinator"):
        if recorded[field] != value[field]:
            _fail("vote_received_event_" + field)
    participant = recorded["participant"]
    if participant not in value["missing_participants"]:
        _fail("vote_received_event_participant")
    existing = value["responses"].get(participant)
    if existing is not None:
        if existing["response_hash"] == recorded["response_hash"]:
            return value, False
        _fail("vote_response_duplicate_conflict")
    value["votes_collected"][participant] = recorded["vote"]
    value["responses"][participant] = {
        "response_id": recorded["response_id"],
        "response_hash": recorded["response_hash"],
    }
    if set(value["votes_collected"]) == set(value["missing_participants"]):
        value["projection_state"] = "coverage_complete"
    validate_collection_projection(value, ticket)
    return value, True


def response_is_idempotent_duplicate(collection: Any, ticket: Any, response_event: Any) -> bool:
    value = validate_collection_projection(collection, ticket)
    event = validate_vote_received_event(response_event)
    existing = value["responses"].get(event["participant"])
    return existing is not None and existing["response_hash"] == event["response_hash"]


def resolution_votes_from_collection(collection: Any, ticket: Any) -> dict[str, str]:
    value = validate_collection_projection(collection, ticket)
    expected = set(value["missing_participants"])
    if set(value["votes_collected"]) != expected:
        _fail("vote_collection_incomplete")
    resolution_votes = {participant: value["votes_collected"][participant] for participant in value["missing_participants"]}
    if not all(vote in RESOLUTION_VOTE_STATES for vote in resolution_votes.values()):
        _fail("resolution_vote_state")
    return resolution_votes


def _lifecycle_details(kind: str) -> tuple[str, str, str]:
    if kind == LIFECYCLE_CANCEL_KIND:
        return "cancelled", LIFECYCLE_CANCEL_EVENT_TYPE, LIFECYCLE_CANCEL_EVENT_SCHEMA_VERSION
    if kind == LIFECYCLE_EXPIRE_KIND:
        return "expired", LIFECYCLE_EXPIRE_EVENT_TYPE, LIFECYCLE_EXPIRE_EVENT_SCHEMA_VERSION
    _lifecycle_fail("command_schema")


def validate_lifecycle_command(payload: Any) -> dict[str, Any]:
    value = _lifecycle_closed_object(
        payload,
        _LIFECYCLE_COMMAND_FIELDS,
        "lifecycle_command",
        "command_schema",
    )
    kind = value["kind"]
    _, _, _ = _lifecycle_details(kind)
    expected_schema = LIFECYCLE_CANCEL_SCHEMA_VERSION if kind == LIFECYCLE_CANCEL_KIND else LIFECYCLE_EXPIRE_SCHEMA_VERSION
    if value["schema_version"] != expected_schema:
        _lifecycle_fail("command_schema")
    _lifecycle_require_digest(value["ticket_id"], "command_schema")
    _lifecycle_require_digest(value["proposal_id"], "command_schema")
    _lifecycle_require_string(value["statement_identity"], "command_schema", non_empty=True)
    if value["coordinator"] != COORDINATOR:
        _lifecycle_fail("identity_mismatch")
    if value["reason"] is not None and not isinstance(value["reason"], str):
        _lifecycle_fail("command_schema")
    if value["request_id"] is not None and not isinstance(value["request_id"], str):
        _lifecycle_fail("command_schema")
    _lifecycle_require_string(value["action_id"], "command_schema", non_empty=True)
    return copy.deepcopy(value)


def lifecycle_action_hash_preimage(command: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "consensus.ticket.lifecycle.action.hash.v1",
        "kind": command["kind"],
        "ticket_id": command["ticket_id"],
        "proposal_id": command["proposal_id"],
        "statement_identity": command["statement_identity"],
        "coordinator": command["coordinator"],
        "reason": command["reason"],
        "request_id": command["request_id"],
        "action_id": command["action_id"],
    }


def compute_lifecycle_action_hash(command: Mapping[str, Any]) -> str:
    return _hash(lifecycle_action_hash_preimage(command))


def lifecycle_command_from_message(message: Any) -> dict[str, Any]:
    value = _strict_json(message, "mailbox_message")
    if not isinstance(value, dict) or value.get("receiver") != COORDINATOR or not isinstance(value.get("args"), list) or not value["args"]:
        _lifecycle_fail("command_schema")
    if value.get("method") not in {LIFECYCLE_CANCEL_KIND, LIFECYCLE_EXPIRE_KIND}:
        _lifecycle_fail("command_schema")
    return validate_lifecycle_command(value["args"][0])


def build_lifecycle_event(command: Any) -> dict[str, Any]:
    value = validate_lifecycle_command(command)
    _, event_type, event_schema = _lifecycle_details(value["kind"])
    return {
        "type": event_type,
        "schema_version": event_schema,
        "ticket_id": value["ticket_id"],
        "proposal_id": value["proposal_id"],
        "statement_identity": value["statement_identity"],
        "coordinator": COORDINATOR,
        "reason": value["reason"],
        "request_id": value["request_id"],
        "action_id": value["action_id"],
        "action_hash": compute_lifecycle_action_hash(value),
    }


def validate_lifecycle_event(event: Any) -> dict[str, Any]:
    value = _lifecycle_closed_object(
        event,
        _LIFECYCLE_EVENT_FIELDS,
        "lifecycle_event",
        "event_schema",
    )
    if value["type"] == LIFECYCLE_CANCEL_EVENT_TYPE:
        kind, schema = LIFECYCLE_CANCEL_KIND, LIFECYCLE_CANCEL_EVENT_SCHEMA_VERSION
    elif value["type"] == LIFECYCLE_EXPIRE_EVENT_TYPE:
        kind, schema = LIFECYCLE_EXPIRE_KIND, LIFECYCLE_EXPIRE_EVENT_SCHEMA_VERSION
    else:
        _lifecycle_fail("event_schema")
    if value["schema_version"] != schema:
        _lifecycle_fail("event_schema")
    if value["coordinator"] != COORDINATOR:
        _lifecycle_fail("identity_mismatch")
    _lifecycle_require_digest(value["ticket_id"], "event_schema")
    _lifecycle_require_digest(value["proposal_id"], "event_schema")
    _lifecycle_require_string(value["statement_identity"], "event_schema", non_empty=True)
    if value["reason"] is not None and not isinstance(value["reason"], str):
        _lifecycle_fail("event_schema")
    if value["request_id"] is not None and not isinstance(value["request_id"], str):
        _lifecycle_fail("event_schema")
    _lifecycle_require_string(value["action_id"], "event_schema", non_empty=True)
    _lifecycle_require_digest(value["action_hash"], "event_schema")
    command = {
        "kind": kind,
        "schema_version": LIFECYCLE_CANCEL_SCHEMA_VERSION if kind == LIFECYCLE_CANCEL_KIND else LIFECYCLE_EXPIRE_SCHEMA_VERSION,
        "ticket_id": value["ticket_id"], "proposal_id": value["proposal_id"],
        "statement_identity": value["statement_identity"], "coordinator": value["coordinator"],
        "reason": value["reason"], "request_id": value["request_id"], "action_id": value["action_id"],
    }
    if value["action_hash"] != compute_lifecycle_action_hash(command):
        _lifecycle_fail("action_hash_mismatch")
    return copy.deepcopy(value)


def validate_lifecycle_transition(command: Any, ticket: Any, prior_events: list[Any]) -> bool:
    """Return true only for an exact terminal duplicate; otherwise fail closed."""
    value = validate_lifecycle_command(command)
    if ticket is None:
        _lifecycle_fail("ticket_not_found")
    try:
        validate_ticket_projection(ticket, allowed_states={"pending", "resolved", "cancelled", "expired"})
    except ConsensusTicketResolutionError as exc:
        _lifecycle_fail("invalid_transition")
    for field in ("ticket_id", "proposal_id", "statement_identity"):
        if value[field] != ticket.get(field):
            _lifecycle_fail("identity_mismatch")
    state, _, _ = _lifecycle_details(value["kind"])
    action_hash = compute_lifecycle_action_hash(value)
    for prior in prior_events:
        if not isinstance(prior, Mapping) or prior.get("type") not in {LIFECYCLE_CANCEL_EVENT_TYPE, LIFECYCLE_EXPIRE_EVENT_TYPE}:
            continue
        recorded = validate_lifecycle_event(prior)
        if recorded["action_id"] == value["action_id"] and recorded["ticket_id"] != value["ticket_id"]:
            _lifecycle_fail("action_id_conflict")
    if ticket.get("projection_state") == "pending":
        return False
    if ticket.get("projection_state") != state:
        _lifecycle_fail("terminal_conflict")
    if ticket.get("terminal_action_hash") != action_hash or ticket.get("terminal_kind") != state:
        _lifecycle_fail("terminal_conflict")
    return True


def build_lifecycle_projection(ticket: Any, event: Any) -> dict[str, Any]:
    recorded = validate_lifecycle_event(event)
    if recorded["type"] == LIFECYCLE_CANCEL_EVENT_TYPE:
        state = "cancelled"
    else:
        state = "expired"
    try:
        validate_ticket_projection(ticket, allowed_states={"pending"})
    except ConsensusTicketResolutionError as exc:
        _lifecycle_fail("not_pending")
    for field in ("ticket_id", "proposal_id", "statement_identity"):
        if ticket.get(field) != recorded[field]:
            _lifecycle_fail("identity_mismatch")
    projection = copy.deepcopy(dict(ticket))
    projection.update({
        "projection_state": state,
        "terminal_kind": state,
        "terminal_reason": recorded["reason"],
        "terminal_action_id": recorded["action_id"],
        "terminal_action_hash": recorded["action_hash"],
    })
    return projection


__all__ = [
    "COORDINATOR",
    "IMPORT_EVENT_SCHEMA_VERSION",
    "IMPORT_EVENT_TYPE",
    "IMPORT_KIND",
    "IMPORT_SCHEMA_VERSION",
    "ConsensusMailboxCollectionError",
    "LIFECYCLE_CANCEL_EVENT_TYPE",
    "LIFECYCLE_EXPIRE_EVENT_TYPE",
    "VOTE_RECEIVED_EVENT_SCHEMA_VERSION",
    "VOTE_RECEIVED_EVENT_TYPE",
    "VOTE_RESPONSE_KIND",
    "VOTE_RESPONSE_SCHEMA_VERSION",
    "apply_vote_received_event",
    "build_ticket_import_event",
    "build_lifecycle_event",
    "build_lifecycle_projection",
    "build_vote_received_event",
    "compute_response_hash",
    "compute_ticket_import_hash",
    "compute_lifecycle_action_hash",
    "imported_ticket_from_event",
    "new_collection_projection",
    "lifecycle_command_from_message",
    "recognised_mailbox_method",
    "recompute_vote_counts",
    "recompute_votes_hash",
    "resolution_votes_from_collection",
    "response_is_idempotent_duplicate",
    "ticket_import_payload_from_message",
    "validate_collection_projection",
    "validate_import_idempotency",
    "validate_lifecycle_command",
    "validate_lifecycle_event",
    "validate_lifecycle_transition",
    "validate_pending_ticket_projection",
    "validate_response_for_ticket",
    "validate_ticket_import_event",
    "validate_ticket_import_payload",
    "validate_vote_received_event",
    "validate_vote_response_payload",
    "vote_response_payload_from_message",
]
