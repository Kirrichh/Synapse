"""Runtime-backed P3b-0 vote sources.

This module deliberately depends on a narrow callback context supplied by the
interpreter so it never imports or reaches into interpreter implementation.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from synapse.builtins import AgentRuntime, DurableActorRef

from .consensus_engine import ConsensusRequest, VoteRecord, VoteSource
from .consensus_proposal_view import ProposalViewMutationError


class ActorMethodVoteSource(VoteSource):
    """Explicitly enabled synchronous local ``consensus_vote(proposal)`` source."""

    def __init__(self, context: Any):
        self._context = context
        self.last_vote_diagnostics: dict[str, str] = {}

    def collect_votes(
        self, request: ConsensusRequest, participants: Sequence[str]
    ) -> Iterable[VoteRecord]:
        participant_values = self._participant_values(request, participants)
        snapshot = self._context.begin_consensus_vote_query(participant_values.values())
        self.last_vote_diagnostics = {}
        try:
            return tuple(
                self._collect_participant_vote(
                    participant, participant_values[participant], request.proposal_view
                )
                for participant in participants
            )
        finally:
            self._context.end_consensus_vote_query(snapshot, participant_values.values())

    def _participant_values(
        self, request: ConsensusRequest, participants: Sequence[str]
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for value in request.participants:
            identity = self._identity_for_value(value)
            if identity is not None:
                values[identity] = value
        return {participant: values.get(participant) for participant in participants}

    @staticmethod
    def _identity_for_value(value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, AgentRuntime):
            return value.name.strip() if isinstance(value.name, str) else None
        if isinstance(value, DurableActorRef):
            return value.actor_name.strip() if isinstance(value.actor_name, str) else None
        return None

    def _record(self, participant: str, vote: str, source_label: str) -> VoteRecord:
        self.last_vote_diagnostics[participant] = source_label
        return VoteRecord(participant, vote, source_label)

    def _collect_participant_vote(
        self, participant: str, participant_value: Any, proposal_view: Any
    ) -> VoteRecord:
        if isinstance(participant_value, (str, DurableActorRef)) or participant_value is None:
            return self._record(participant, "missing", "actor_not_local")
        if not isinstance(participant_value, AgentRuntime) or participant_value.env is None:
            return self._record(participant, "missing", "actor_method_missing")

        try:
            method = participant_value.env.get_function("consensus_vote")
        except Exception:
            return self._record(participant, "missing", "actor_method_missing")

        try:
            result = self._context.invoke_actor_vote_method(
                participant_value, method, proposal_view
            )
        except ProposalViewMutationError:
            raise
        except self._context.registry_mutation_error_type:
            raise
        except self._context.side_effect_error_type:
            raise
        except Exception:
            return self._record(participant, "missing", "actor_method_exception")

        vote = self._normalize_vote_result(result)
        if vote is None:
            return self._record(participant, "missing", "actor_method_invalid")
        return self._record(participant, vote, "actor_method")

    @staticmethod
    def _normalize_vote_result(result: Any) -> str | None:
        if type(result) is str:
            return result if result in {"yes", "no", "abstain", "missing"} else None
        if type(result) is not dict or set(result) - {"vote", "reason"}:
            return None
        vote = result.get("vote")
        if type(vote) is not str or vote not in {"yes", "no", "abstain", "missing"}:
            return None
        if "reason" in result and type(result["reason"]) is not str:
            return None
        return vote


__all__ = ["ActorMethodVoteSource"]
