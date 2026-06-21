"""Deterministic P3a semantic consensus engine.

The engine is a side-effect-free functional core.  It validates all semantic
inputs, derives deterministic identifiers, and returns result/event payloads
for the interpreter adapter to bind or append.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

from synapse.builtins import AgentRuntime, DurableActorRef
from synapse.hardening import canonical_json

from .consensus_proposal_view import ProposalViewValueError, freeze_json_value
from .consensus_ticket_resolution import (
    ConsensusTicketResolutionError,
    RESOLUTION_EVENT_TYPE,
    RESOLUTION_SCHEMA_VERSION,
    validate_resolution_event_schema,
    validate_resolution_signal_payload,
    validate_ticket_projection,
)


APPROVED_STRATEGIES = {"MajorityVote", "UnanimousVote", "NoVetoVote"}
ALLOWED_VOTE_STATES = {"yes", "no", "abstain", "missing"}
ALLOWED_VOTE_SOURCE_LABELS = {
    "explicit_map",
    "test_controlled",
    "recorded_test",
    "missing",
    "actor_method",
    "actor_method_missing",
    "actor_method_exception",
    "actor_method_invalid",
    "actor_not_local",
}
SEMANTIC_REASON_VALUES = {
    "quorum_reached",
    "unanimity_reached",
    "no_veto_quorum_reached",
    "explicit_no_vote",
    "unanimity_broken_by_no",
    "unanimity_broken_by_abstain",
    "insufficient_quorum",
    "pending_missing_votes",
}


class ConsensusValidationError(Exception):
    """Stable validation boundary for malformed P3a consensus requests."""


@dataclass(frozen=True)
class VoteRecord:
    participant: Any
    vote: str
    source_label: str = "explicit_map"


class VoteSource:
    """Side-effect-free vote provider seam used by the interpreter adapter."""

    def collect_votes(
        self, request: "ConsensusRequest", participants: Sequence[str]
    ) -> Iterable[VoteRecord]:
        raise NotImplementedError


class NullVoteSource(VoteSource):
    """Default source: every normalized participant has a missing vote."""

    def collect_votes(
        self, request: "ConsensusRequest", participants: Sequence[str]
    ) -> Iterable[VoteRecord]:
        return tuple(VoteRecord(participant, "missing", "missing") for participant in participants)


class ExplicitVoteSource(VoteSource):
    """Deterministic in-memory vote source for embeddings and tests."""

    def __init__(
        self,
        votes: Mapping[Any, str] | Iterable[VoteRecord | Tuple[Any, str] | Tuple[Any, str, str]],
        source_label: str = "explicit_map",
    ):
        if source_label not in ALLOWED_VOTE_SOURCE_LABELS:
            raise ConsensusValidationError("invalid_request: unsupported_vote_source")
        if isinstance(votes, Mapping):
            records = [
                VoteRecord(participant, vote, source_label)
                for participant, vote in votes.items()
            ]
        else:
            records = []
            for entry in votes:
                if isinstance(entry, VoteRecord):
                    records.append(entry)
                elif isinstance(entry, (tuple, list)) and len(entry) == 2:
                    participant, vote = entry
                    records.append(VoteRecord(participant, vote, source_label))
                elif isinstance(entry, (tuple, list)) and len(entry) == 3:
                    participant, vote, label = entry
                    records.append(VoteRecord(participant, vote, label))
                else:
                    raise ConsensusValidationError("invalid_request: malformed_vote_record")
        self._records = tuple(records)

    def collect_votes(
        self, request: "ConsensusRequest", participants: Sequence[str]
    ) -> Iterable[VoteRecord]:
        return self._records


NULL_VOTE_SOURCE = NullVoteSource()


@dataclass(frozen=True)
class ConsensusRequest:
    topic: Any
    participants: Sequence[Any]
    quorum: Optional[Any] = None
    timeout: Optional[Any] = None
    policy_ref: Optional[Any] = None
    coordinator: Optional[str] = None
    statement_identity: str = ""
    vote_source: Optional[VoteSource] = None
    proposal_view: Optional[Any] = None


@dataclass(frozen=True)
class ConsensusDecision:
    result: Dict[str, Any]
    event_payload: Dict[str, Any]
    proposal_preimage: Dict[str, Any]
    votes_preimage: Dict[str, Any]
    result_preimage: Dict[str, Any]
    ticket_id: Optional[str] = None
    ticket_payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ConsensusTicketResolution:
    """Engine-owned deterministic result of resolving a pending ticket."""

    event_payload: Dict[str, Any]
    votes_preimage: Dict[str, Any]
    result_preimage: Dict[str, Any]
    votes_final: Dict[str, str]
    vote_counts_final: Dict[str, int]
    outcome: str
    reason: str
    votes_hash_final: str
    result_hash_final: str


@dataclass(frozen=True)
class _PreparedConsensusProposal:
    """Engine-owned normalized proposal identity shared by LIVE and REPLAY."""

    participants: list[str]
    policy: Optional[str]
    strategy: str
    quorum: Optional[int]
    timeout: int
    statement_identity: str
    coordinator: str
    proposal_id: str
    proposal_preimage: Dict[str, Any]
    proposal_view: Any


class ConsensusEngine:
    """Pure reducer for P3a content-sensitive consensus semantics."""

    def decide(self, request: ConsensusRequest) -> ConsensusDecision:
        prepared = self._prepare_proposal(request)
        participants = prepared.participants
        policy = prepared.policy
        strategy = prepared.strategy
        quorum = prepared.quorum
        timeout = prepared.timeout
        statement_identity = prepared.statement_identity
        coordinator = prepared.coordinator
        proposal_preimage = prepared.proposal_preimage
        proposal_id = prepared.proposal_id
        vote_request = replace(request, proposal_view=prepared.proposal_view)
        votes = self._collect_votes(vote_request, participants)
        vote_counts = self._count_votes(votes)
        outcome, reason = self._evaluate_outcome(strategy, quorum, len(participants), vote_counts)

        votes_preimage = {
            "schema_version": "consensus.votes.v1",
            "votes": [[participant, votes[participant]] for participant in participants],
        }
        votes_hash = self._hash_payload(votes_preimage)

        result_preimage = {
            "schema_version": "consensus.result.v1",
            "proposal_id": proposal_id,
            "outcome": outcome,
            "reason": reason,
            "participants": participants,
            "strategy": strategy,
            "policy": policy,
            "quorum": quorum,
            "timeout": timeout,
            "vote_counts": vote_counts,
            "votes_hash": votes_hash,
        }
        result_hash = self._hash_payload(result_preimage)

        result = {
            "schema_version": "consensus.result.v1",
            "proposal_id": proposal_id,
            "outcome": outcome,
            "committed": outcome == "committed",
            "reason": reason,
            "topic": request.topic,
            "participants": participants,
            "coordinator": coordinator,
            "strategy": strategy,
            "policy": policy,
            "votes": {participant: votes[participant] for participant in participants},
            "vote_counts": vote_counts,
            "quorum": quorum,
            "timeout": timeout,
            "deferred": outcome == "deferred",
            "ticket_id": None,
            "votes_hash": votes_hash,
            "result_hash": result_hash,
        }

        ticket_id = None
        ticket_payload = None
        if outcome == "deferred":
            if reason != "pending_missing_votes":
                raise ConsensusValidationError("invalid_request: unsupported_deferred_reason")
            missing_participants = sorted(
                participant for participant in participants if votes[participant] == "missing"
            )
            ticket_preimage = {
                "schema_version": "consensus.ticket.v1",
                "proposal_id": proposal_id,
                "statement_identity": statement_identity,
                "missing_participants": missing_participants,
                "votes_hash": votes_hash,
            }
            ticket_id = self._hash_payload(ticket_preimage)
            ticket_payload = {
                "type": "distributed_consensus_ticket_created",
                "schema_version": "consensus.ticket.event.v1",
                "ticket_id": ticket_id,
                "proposal_id": proposal_id,
                "statement_identity": statement_identity,
                "participants": list(participants),
                "missing_participants": missing_participants,
                "votes": {participant: votes[participant] for participant in participants},
                "vote_counts": dict(vote_counts),
                "votes_hash": votes_hash,
                "strategy": strategy,
                "policy": policy,
                "quorum": quorum,
                "timeout": timeout,
            }
            result["ticket_id"] = ticket_id
            self._validate_json_payload(ticket_payload)

        event_payload = {
            "type": "distributed_consensus_decided",
            "schema_version": "consensus.event.v2",
            "proposal_id": proposal_id,
            "statement_identity": statement_identity,
            "outcome": outcome,
            "reason": reason,
            "participants": participants,
            "coordinator": coordinator,
            "strategy": strategy,
            "policy": policy,
            "quorum": quorum,
            "timeout": timeout,
            "votes": {participant: votes[participant] for participant in participants},
            "vote_counts": vote_counts,
            "votes_hash": votes_hash,
            "result_hash": result_hash,
        }
        self._validate_json_payload(event_payload)
        self._validate_json_payload(result)
        return ConsensusDecision(
            result=result,
            event_payload=event_payload,
            proposal_preimage=proposal_preimage,
            votes_preimage=votes_preimage,
            result_preimage=result_preimage,
            ticket_id=ticket_id,
            ticket_payload=ticket_payload,
        )

    def resolve_pending_ticket(
        self,
        ticket_payload: Mapping[str, Any],
        resolution_votes: Mapping[str, str],
    ) -> ConsensusTicketResolution:
        """Deterministically merge final votes and build the durable resolution event."""
        try:
            ticket = validate_ticket_projection(ticket_payload, allow_resolved=False)
            resolution_votes = validate_resolution_signal_payload(
                {
                    "kind": "consensus_ticket_resolution",
                    "ticket_id": ticket["ticket_id"],
                    "votes": resolution_votes,
                },
                ticket["ticket_id"],
                ticket,
            )
        except ConsensusTicketResolutionError as exc:
            raise ConsensusValidationError(str(exc)) from exc

        participants = list(ticket["participants"])
        votes_final = {participant: ticket["votes"][participant] for participant in participants}
        votes_final.update(resolution_votes)
        vote_counts_final = self._count_votes(votes_final)
        outcome, reason = self._evaluate_outcome(
            ticket["strategy"],
            ticket["quorum"],
            len(participants),
            vote_counts_final,
        )
        if outcome == "deferred" or vote_counts_final["missing"] != 0:
            raise ConsensusValidationError("invalid_request: consensus_ticket_resolution_non_terminal")

        votes_preimage = {
            "schema_version": "consensus.votes.v1",
            "votes": [[participant, votes_final[participant]] for participant in participants],
        }
        votes_hash_final = self._hash_payload(votes_preimage)
        result_preimage = {
            "schema_version": "consensus.result.v1",
            "proposal_id": ticket["proposal_id"],
            "outcome": outcome,
            "reason": reason,
            "participants": participants,
            "strategy": ticket["strategy"],
            "policy": ticket["policy"],
            "quorum": ticket["quorum"],
            "timeout": ticket["timeout"],
            "vote_counts": vote_counts_final,
            "votes_hash": votes_hash_final,
        }
        result_hash_final = self._hash_payload(result_preimage)
        event_payload = {
            "type": RESOLUTION_EVENT_TYPE,
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "ticket_id": ticket["ticket_id"],
            "proposal_id": ticket["proposal_id"],
            "statement_identity": ticket["statement_identity"],
            "resolution_votes": dict(resolution_votes),
            "votes_final": votes_final,
            "vote_counts_final": vote_counts_final,
            "outcome": outcome,
            "reason": reason,
            "votes_hash_final": votes_hash_final,
            "result_hash_final": result_hash_final,
        }
        try:
            validate_resolution_event_schema(event_payload)
        except ConsensusTicketResolutionError as exc:
            raise ConsensusValidationError(str(exc)) from exc
        self._validate_json_payload(event_payload)
        return ConsensusTicketResolution(
            event_payload=event_payload,
            votes_preimage=votes_preimage,
            result_preimage=result_preimage,
            votes_final=votes_final,
            vote_counts_final=vote_counts_final,
            outcome=outcome,
            reason=reason,
            votes_hash_final=votes_hash_final,
            result_hash_final=result_hash_final,
        )

    def _prepare_proposal(self, request: ConsensusRequest) -> _PreparedConsensusProposal:
        """Normalize and identify a proposal without collecting any votes."""
        participants = self._normalize_participants(request.participants)
        policy, strategy = self._resolve_strategy(request.policy_ref)
        quorum = self._derive_quorum(strategy, request.quorum, len(participants))
        timeout = self._normalize_timeout(request.timeout)
        statement_identity = self._normalize_statement_identity(request.statement_identity)
        coordinator = self._normalize_advisory_coordinator(request.coordinator)

        proposal_preimage = {
            "schema_version": "consensus.proposal.v1",
            "topic": request.topic,
            "participants": participants,
            "quorum": quorum,
            "timeout": timeout,
            "policy": policy,
            "strategy": strategy,
            "statement_identity": statement_identity,
        }
        proposal_id = self._hash_payload(proposal_preimage)
        proposal_view = self._build_proposal_view(
            proposal_id=proposal_id,
            topic=request.topic,
            participants=participants,
            strategy=strategy,
            policy=policy,
            quorum=quorum,
            timeout=timeout,
            statement_identity=statement_identity,
        )
        return _PreparedConsensusProposal(
            participants=participants,
            policy=policy,
            strategy=strategy,
            quorum=quorum,
            timeout=timeout,
            statement_identity=statement_identity,
            coordinator=coordinator,
            proposal_id=proposal_id,
            proposal_preimage=proposal_preimage,
            proposal_view=proposal_view,
        )

    def _build_proposal_view(
        self,
        *,
        proposal_id: str,
        topic: Any,
        participants: Sequence[str],
        strategy: str,
        policy: Optional[str],
        quorum: Optional[int],
        timeout: int,
        statement_identity: str,
    ) -> Any:
        try:
            return freeze_json_value(
                {
                    "schema_version": "consensus.proposal.v1",
                    "proposal_id": proposal_id,
                    "topic": topic,
                    "participants": list(participants),
                    "strategy": strategy,
                    "policy": policy,
                    "quorum": quorum,
                    "timeout": timeout,
                    "statement_identity": statement_identity,
                }
            )
        except ProposalViewValueError as exc:
            raise ConsensusValidationError("invalid_request: unsupported_canonical_value") from exc

    def _normalize_participants(self, participants: Sequence[Any]) -> list[str]:
        if not participants:
            raise ConsensusValidationError("invalid_request: empty_participants")
        normalized = [self._normalize_participant_identity(value) for value in participants]
        if len(set(normalized)) != len(normalized):
            raise ConsensusValidationError("invalid_request: duplicate_participant")
        return sorted(normalized)

    def _normalize_participant_identity(self, value: Any) -> str:
        if value is None:
            raise ConsensusValidationError("invalid_request: unresolved_participant")
        if isinstance(value, str):
            identity = value
        elif isinstance(value, AgentRuntime):
            identity = value.name
        elif isinstance(value, DurableActorRef):
            identity = value.actor_name
        else:
            raise ConsensusValidationError("invalid_request: unsupported_participant_identity")
        if not isinstance(identity, str):
            raise ConsensusValidationError("invalid_request: unsupported_participant_identity")
        identity = identity.strip()
        if not identity:
            raise ConsensusValidationError("invalid_request: unresolved_participant")
        return identity

    def _resolve_strategy(self, policy_ref: Any) -> tuple[Optional[str], str]:
        if policy_ref is None:
            return None, "MajorityVote"
        if isinstance(policy_ref, str):
            policy = policy_ref
        elif isinstance(policy_ref, dict):
            policy = policy_ref.get("name")
        elif hasattr(policy_ref, "name"):
            policy = getattr(policy_ref, "name")
        else:
            raise ConsensusValidationError("invalid_request: unknown_strategy")
        if not isinstance(policy, str) or not policy:
            raise ConsensusValidationError("invalid_request: unknown_strategy")
        if policy not in APPROVED_STRATEGIES:
            raise ConsensusValidationError("invalid_request: unknown_strategy")
        return policy, policy

    def _derive_quorum(self, strategy: str, quorum: Any, participant_count: int) -> Optional[int]:
        if quorum is None:
            if strategy in {"MajorityVote", "NoVetoVote"}:
                return participant_count // 2 + 1
            return None
        if isinstance(quorum, bool) or not isinstance(quorum, int):
            raise ConsensusValidationError("invalid_request: non_integer_quorum")
        if quorum < 1 or quorum > participant_count:
            raise ConsensusValidationError("invalid_request: quorum_out_of_bounds")
        return quorum

    def _normalize_timeout(self, timeout: Any) -> int:
        if timeout is None:
            return 0
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise ConsensusValidationError("invalid_request: non_integer_timeout")
        if timeout < 0:
            raise ConsensusValidationError("invalid_request: negative_timeout")
        return timeout

    def _normalize_statement_identity(self, statement_identity: str) -> str:
        if not isinstance(statement_identity, str) or not statement_identity:
            raise ConsensusValidationError("invalid_request: missing_statement_identity")
        return statement_identity

    def _normalize_advisory_coordinator(self, coordinator: Optional[str]) -> str:
        if coordinator is None:
            return "global"
        if not isinstance(coordinator, str):
            raise ConsensusValidationError("invalid_request: unsupported_coordinator")
        return coordinator

    def _collect_votes(self, request: ConsensusRequest, participants: Sequence[str]) -> Dict[str, str]:
        vote_source = request.vote_source or NULL_VOTE_SOURCE
        records = tuple(vote_source.collect_votes(request, participants))
        votes = {participant: "missing" for participant in participants}
        seen_supplied_votes: dict[str, str] = {}
        for record in records:
            if not isinstance(record, VoteRecord):
                raise ConsensusValidationError("invalid_request: malformed_vote_record")
            if record.source_label not in ALLOWED_VOTE_SOURCE_LABELS:
                raise ConsensusValidationError("invalid_request: unsupported_vote_source")
            participant = self._normalize_participant_identity(record.participant)
            if participant not in votes:
                raise ConsensusValidationError("invalid_request: vote_for_unknown_participant")
            if not isinstance(record.vote, str) or record.vote not in ALLOWED_VOTE_STATES:
                raise ConsensusValidationError("invalid_request: unknown_vote_state")
            if participant in seen_supplied_votes:
                if seen_supplied_votes[participant] != record.vote:
                    raise ConsensusValidationError("invalid_request: conflicting_vote")
                raise ConsensusValidationError("invalid_request: duplicate_vote")
            seen_supplied_votes[participant] = record.vote
            votes[participant] = record.vote
        return votes

    def _count_votes(self, votes: Mapping[str, str]) -> Dict[str, int]:
        return {
            "yes": sum(1 for vote in votes.values() if vote == "yes"),
            "no": sum(1 for vote in votes.values() if vote == "no"),
            "abstain": sum(1 for vote in votes.values() if vote == "abstain"),
            "missing": sum(1 for vote in votes.values() if vote == "missing"),
        }

    def _evaluate_outcome(
        self,
        strategy: str,
        quorum: Optional[int],
        participant_count: int,
        vote_counts: Mapping[str, int],
    ) -> tuple[str, str]:
        yes_count = vote_counts["yes"]
        no_count = vote_counts["no"]
        abstain_count = vote_counts["abstain"]
        missing_count = vote_counts["missing"]

        if strategy == "MajorityVote":
            assert quorum is not None
            if yes_count >= quorum:
                return "committed", "quorum_reached"
            if yes_count + missing_count < quorum:
                return "rejected", "insufficient_quorum"
            return "deferred", "pending_missing_votes"

        if strategy == "UnanimousVote":
            if yes_count == participant_count:
                return "committed", "unanimity_reached"
            if no_count > 0:
                return "rejected", "unanimity_broken_by_no"
            if abstain_count > 0:
                return "rejected", "unanimity_broken_by_abstain"
            return "deferred", "pending_missing_votes"

        if strategy == "NoVetoVote":
            assert quorum is not None
            if no_count > 0:
                return "rejected", "explicit_no_vote"
            if yes_count >= quorum:
                return "committed", "no_veto_quorum_reached"
            if yes_count + missing_count < quorum:
                return "rejected", "insufficient_quorum"
            return "deferred", "pending_missing_votes"

        raise ConsensusValidationError("invalid_request: unknown_strategy")

    def _hash_payload(self, payload: Dict[str, Any]) -> str:
        self._validate_json_payload(payload)
        payload_text = canonical_json(payload)
        return "sha256:" + hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    def _validate_json_payload(self, value: Any) -> None:
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ConsensusValidationError("invalid_request: unsupported_canonical_value")
            return
        if isinstance(value, list):
            for item in value:
                self._validate_json_payload(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ConsensusValidationError("invalid_request: unsupported_canonical_value")
                self._validate_json_payload(item)
            return
        raise ConsensusValidationError("invalid_request: unsupported_canonical_value")


__all__ = [
    "ConsensusDecision",
    "ConsensusEngine",
    "ConsensusTicketResolution",
    "ConsensusRequest",
    "ConsensusValidationError",
    "ExplicitVoteSource",
    "NULL_VOTE_SOURCE",
    "NullVoteSource",
    "SEMANTIC_REASON_VALUES",
    "VoteRecord",
    "VoteSource",
]
