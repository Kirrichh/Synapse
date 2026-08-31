"""Typed inputs supplied to one Stage 11 attempt preparation.

The controller asks one port for one coherent value.  It does not accept
independent callbacks for phase references, delivery, candidate production, or
knowledge availability: those seams allowed a caller to describe one attempt
while executing another.  The values below are input claims only.  The delivery
owner validates them against the sealed admission, durable retrieval record,
Stage 10 persistence, and the actual worker dispatch before they affect a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.admission import (
    GateDecision,
    gate_decision_ref,
    validate_gate_decision,
)
from synapse.experiments.gold.point_of_use import (
    CurrentAdmittedKnowledge,
    PointOfUseAdmissionRequest,
    require_point_of_use_admission_request,
)
from synapse.experiments.gold.replay import (
    BehaviorReplayResult,
    validate_replay_result,
)
from synapse.experiments.gold.retrieval import (
    RetrievalCausalRecord,
    validate_retrieval_causal_record,
)
from synapse.experiments.gold.stage10.context import (
    AdmittedKnowledgeItem,
    ContextSizeBudget,
    ExcludedKnowledgeRef,
)
from synapse.experiments.gold.stage10.intent import (
    IntentCandidate,
    validate_intent_candidate,
)
from synapse.experiments.gold.stage10.plan_authority import (
    AcceptedOperationPlan,
    ConfiguredPlanAuthority,
    validate_accepted_operation_plan,
)
from synapse.experiments.gold.stage10.plan_revalidation import CurrentPlanState

from .models import GoldAttemptContext, GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


class AttemptInputReason(str, Enum):
    """Closed absence vocabulary returned by the attempt-input owner."""

    NO_NEWLY_ADMITTED_OR_REVALIDATED_KNOWLEDGE = (
        "NO_NEWLY_ADMITTED_OR_REVALIDATED_KNOWLEDGE"
    )
    KNOWLEDGE_DEPENDENCY_UNAVAILABLE = "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@runtime_checkable
class CurrentPlanStateReaderPort(Protocol):
    """Read authoritative state immediately before the first side effect."""

    def read_current_plan_state(
        self,
        *,
        admitted_knowledge: CurrentAdmittedKnowledge,
    ) -> CurrentPlanState: ...


@dataclass(frozen=True)
class PreparedAttemptInputs:
    """One bound set of real Stage 7--10 records for an attempt."""

    admission_request: PointOfUseAdmissionRequest
    retrieval_gate_decision: GateDecision
    retrieval_causal_record: RetrievalCausalRecord
    replay_result: BehaviorReplayResult
    intent: IntentCandidate
    accepted_plan: AcceptedOperationPlan
    plan_authority: ConfiguredPlanAuthority
    knowledge_items: tuple[AdmittedKnowledgeItem, ...]
    excluded_refs: tuple[ExcludedKnowledgeRef, ...]
    context_budget: ContextSizeBudget
    worker_worktree: Path
    current_plan_state_reader: CurrentPlanStateReaderPort

    def __post_init__(self) -> None:
        require_point_of_use_admission_request(self.admission_request)
        validate_retrieval_causal_record(self.retrieval_causal_record)
        validate_gate_decision(self.retrieval_gate_decision)
        if (
            self.retrieval_causal_record.retrieval_gate_decision_ref.to_dict()
            != gate_decision_ref(self.retrieval_gate_decision).to_dict()
        ):
            raise _fail(
                GoldRunFailureCode.IDENTITY_MISMATCH,
                "retrieval gate differs from its durable causal record",
            )
        validate_replay_result(self.replay_result)
        validate_intent_candidate(self.intent)
        validate_accepted_operation_plan(self.accepted_plan)
        if type(self.plan_authority) is not ConfiguredPlanAuthority:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "plan authority must be exact",
            )
        if type(self.knowledge_items) is not tuple or any(
            type(item) is not AdmittedKnowledgeItem for item in self.knowledge_items
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "knowledge items must be an exact tuple",
            )
        if type(self.excluded_refs) is not tuple or any(
            type(item) is not ExcludedKnowledgeRef for item in self.excluded_refs
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "excluded refs must be an exact tuple",
            )
        if type(self.context_budget) is not ContextSizeBudget:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "context budget must be exact",
            )
        if type(self.worker_worktree) is not type(Path()):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "worker worktree must be an exact platform Path",
            )
        if not isinstance(self.current_plan_state_reader, CurrentPlanStateReaderPort):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "current plan state reader does not implement its port",
            )


@dataclass(frozen=True)
class NoNewKnowledge:
    """The input owner found no admissible/revalidated input for the next attempt."""

    attempt_index: int
    previous_retrieval_ref: HashBoundRef
    reason: AttemptInputReason = (
        AttemptInputReason.NO_NEWLY_ADMITTED_OR_REVALIDATED_KNOWLEDGE
    )

    def __post_init__(self) -> None:
        if type(self.attempt_index) is not int or self.attempt_index < 2:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "no-new-knowledge result requires a later attempt index",
            )
        if (
            type(self.previous_retrieval_ref) is not HashBoundRef
            or self.previous_retrieval_ref.kind is not RefKind.ARTIFACT
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "no-new-knowledge result requires the previous retrieval ref",
            )
        if self.reason is not AttemptInputReason.NO_NEWLY_ADMITTED_OR_REVALIDATED_KNOWLEDGE:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "no-new-knowledge reason is invalid",
            )


@dataclass(frozen=True)
class KnowledgeDependencyUnavailable:
    """Knowledge preparation could not establish an authoritative answer."""

    attempt_index: int
    detail_code: str
    reason: AttemptInputReason = AttemptInputReason.KNOWLEDGE_DEPENDENCY_UNAVAILABLE

    def __post_init__(self) -> None:
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "unavailable result requires a positive attempt index",
            )
        if (
            type(self.detail_code) is not str
            or not self.detail_code
            or len(self.detail_code) > 128
        ):
            raise _fail(
                GoldRunFailureCode.BOUNDED_VALUE,
                "unavailable detail code must be bounded",
            )
        if self.reason is not AttemptInputReason.KNOWLEDGE_DEPENDENCY_UNAVAILABLE:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "knowledge-unavailable reason is invalid",
            )


AttemptInputAvailability = (
    PreparedAttemptInputs | NoNewKnowledge | KnowledgeDependencyUnavailable
)


@runtime_checkable
class AttemptInputsPort(Protocol):
    """Produce one coherent Stage 7--10 input set or typed absence."""

    def prepare(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        previous_context: GoldAttemptContext | None,
    ) -> AttemptInputAvailability: ...


def require_attempt_inputs_port(value: object) -> AttemptInputsPort:
    if not isinstance(value, AttemptInputsPort):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "attempt inputs must implement AttemptInputsPort",
        )
    return value


__all__ = [
    "AttemptInputAvailability",
    "AttemptInputReason",
    "AttemptInputsPort",
    "CurrentPlanStateReaderPort",
    "KnowledgeDependencyUnavailable",
    "NoNewKnowledge",
    "PreparedAttemptInputs",
    "require_attempt_inputs_port",
]
