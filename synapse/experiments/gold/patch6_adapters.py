"""Sealed adapters over subordinate Patch 6 evidence capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from .canonicalization import ContentKey, HashBoundRef
from .compatibility import (
    CompatibilityContext,
    CompatibilityConflictScan,
    CompatibilityDecision,
    CompatibilityEvidence,
    CompatibilitySubjectDescriptor,
    ConfiguredCompatibilityEvaluator,
    ConflictEvidenceProposal,
    evaluate_compatibility,
    evaluate_conflicts,
    require_configured_compatibility_evaluator,
    validate_compatibility_decision,
)
from .contracts import RecordId
from .library import (
    IndexEntry,
    LibrarySnapshot,
    SnapshotVerification,
    VerifiedBehaviorRecord,
)
from .retrieval import (
    ConfiguredRetriever,
    RankingFeatureObservation,
    RetrievalQuery,
    observe_retrieval_ranking_feature,
    require_configured_retriever,
    require_retriever_evaluator,
    resolve_retrieval_conflict_proposals,
    resolve_retrieval_descriptor,
)


_COMPATIBILITY_ADAPTER_SEAL = object()
_RETRIEVAL_ADAPTER_SEAL = object()


@dataclass(frozen=True, init=False)
class ConfiguredPatch6CompatibilityAdapter:
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> ConfiguredPatch6CompatibilityAdapter:
        raise TypeError("ConfiguredPatch6CompatibilityAdapter is factory-created")

    def evaluate(
        self,
        *,
        context: CompatibilityContext,
        descriptor: CompatibilitySubjectDescriptor,
        index_entry: IndexEntry,
        subject_ref: HashBoundRef,
        source_evidence_refs: tuple[HashBoundRef, ...],
    ) -> tuple[CompatibilityEvidence, CompatibilityDecision]:
        require_patch6_compatibility_adapter(self)
        if type(subject_ref) is not HashBoundRef:
            raise TypeError("subject_ref must be an exact HashBoundRef")
        HashBoundRef.from_dict(subject_ref.to_dict())
        if (
            type(source_evidence_refs) is not tuple
            or not source_evidence_refs
            or any(
                type(item) is not HashBoundRef
                for item in source_evidence_refs
            )
        ):
            raise TypeError(
                "source_evidence_refs must be a non-empty exact tuple"
            )
        for item in source_evidence_refs:
            HashBoundRef.from_dict(item.to_dict())
        decision = evaluate_compatibility(
            evaluator=self._evaluator,
            context=context,
            descriptor=descriptor,
            index_entry=index_entry,
        )
        validate_compatibility_decision(
            decision,
            evaluator=self._evaluator,
            context=context,
            descriptor=descriptor,
        )
        return decision.evidence, decision


def configure_patch6_compatibility_adapter(
    evaluator: ConfiguredCompatibilityEvaluator,
) -> ConfiguredPatch6CompatibilityAdapter:
    require_configured_compatibility_evaluator(evaluator)
    result = object.__new__(ConfiguredPatch6CompatibilityAdapter)
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _COMPATIBILITY_ADAPTER_SEAL)
    require_patch6_compatibility_adapter(result)
    return result


def require_patch6_compatibility_adapter(
    value: ConfiguredPatch6CompatibilityAdapter,
    *,
    expected: ConfiguredPatch6CompatibilityAdapter | None = None,
) -> ConfiguredCompatibilityEvaluator:
    if (
        type(value) is not ConfiguredPatch6CompatibilityAdapter
        or getattr(value, "_trusted_seal", None) is not _COMPATIBILITY_ADAPTER_SEAL
    ):
        raise TypeError("Patch 6 compatibility adapter is not factory sealed")
    if expected is not None and value is not expected:
        raise TypeError("Patch 6 compatibility adapter instance differs")
    require_configured_compatibility_evaluator(value._evaluator)
    return value._evaluator


@dataclass(frozen=True, init=False)
class ConfiguredPatch6RetrievalAdapter:
    _retriever: ConfiguredRetriever
    _compatibility_adapter: ConfiguredPatch6CompatibilityAdapter
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> ConfiguredPatch6RetrievalAdapter:
        raise TypeError("ConfiguredPatch6RetrievalAdapter is factory-created")

    def resolve_descriptor(
        self,
        index_entry: IndexEntry,
    ) -> CompatibilitySubjectDescriptor:
        require_patch6_retrieval_adapter(self)
        return resolve_retrieval_descriptor(
            retriever=self._retriever,
            index_entry=index_entry,
        )

    def resolve_conflict_proposals(
        self,
        *,
        context: CompatibilityContext,
        decisions: tuple[CompatibilityDecision, ...],
        descriptors: tuple[CompatibilitySubjectDescriptor, ...],
    ) -> tuple[ConflictEvidenceProposal, ...]:
        require_patch6_retrieval_adapter(self)
        return resolve_retrieval_conflict_proposals(
            retriever=self._retriever,
            context=context,
            decisions=decisions,
            descriptors=descriptors,
        )

    def evaluate_conflicts(
        self,
        *,
        context: CompatibilityContext,
        decisions: tuple[CompatibilityDecision, ...],
        descriptors: tuple[CompatibilitySubjectDescriptor, ...],
        considered_index_entries: tuple[IndexEntry, ...],
        proposals: tuple[ConflictEvidenceProposal, ...],
    ) -> CompatibilityConflictScan:
        require_patch6_retrieval_adapter(self)
        evaluator = require_patch6_compatibility_adapter(
            self._compatibility_adapter
        )
        return evaluate_conflicts(
            evaluator=evaluator,
            context=context,
            decisions=decisions,
            descriptors=descriptors,
            considered_index_entries=considered_index_entries,
            proposals=proposals,
        )

    def observe_ranking_feature(
        self,
        *,
        query: RetrievalQuery,
        context: CompatibilityContext,
        descriptor: CompatibilitySubjectDescriptor,
    ) -> RankingFeatureObservation:
        require_patch6_retrieval_adapter(self)
        return observe_retrieval_ranking_feature(
            retriever=self._retriever,
            query=query,
            context=context,
            descriptor=descriptor,
        )

    def current_library_snapshot(
        self,
        *,
        trusted_prior: LibrarySnapshot | None = None,
    ) -> SnapshotVerification:
        require_patch6_retrieval_adapter(self)
        evaluator = require_patch6_compatibility_adapter(
            self._compatibility_adapter
        )
        return evaluator.library.current_snapshot(trusted_prior=trusted_prior)

    def load_verified_behavior(
        self,
        *,
        content_key: ContentKey,
        manifest_id: RecordId,
    ) -> VerifiedBehaviorRecord:
        require_patch6_retrieval_adapter(self)
        evaluator = require_patch6_compatibility_adapter(
            self._compatibility_adapter
        )
        return evaluator.library.get_verified_behavior(content_key, manifest_id)


def configure_patch6_retrieval_adapter(
    *,
    retriever: ConfiguredRetriever,
    compatibility_adapter: ConfiguredPatch6CompatibilityAdapter,
) -> ConfiguredPatch6RetrievalAdapter:
    require_configured_retriever(retriever)
    evaluator = require_patch6_compatibility_adapter(compatibility_adapter)
    if require_retriever_evaluator(retriever) is not evaluator:
        raise TypeError("Patch 6 adapters use different evaluator capabilities")
    result = object.__new__(ConfiguredPatch6RetrievalAdapter)
    object.__setattr__(result, "_retriever", retriever)
    object.__setattr__(result, "_compatibility_adapter", compatibility_adapter)
    object.__setattr__(result, "_trusted_seal", _RETRIEVAL_ADAPTER_SEAL)
    require_patch6_retrieval_adapter(result)
    return result


def require_patch6_retrieval_adapter(
    value: ConfiguredPatch6RetrievalAdapter,
    *,
    expected: ConfiguredPatch6RetrievalAdapter | None = None,
) -> ConfiguredRetriever:
    if (
        type(value) is not ConfiguredPatch6RetrievalAdapter
        or getattr(value, "_trusted_seal", None) is not _RETRIEVAL_ADAPTER_SEAL
    ):
        raise TypeError("Patch 6 retrieval adapter is not factory sealed")
    if expected is not None and value is not expected:
        raise TypeError("Patch 6 retrieval adapter instance differs")
    require_patch6_compatibility_adapter(value._compatibility_adapter)
    require_configured_retriever(value._retriever)
    if (
        require_retriever_evaluator(value._retriever)
        is not value._compatibility_adapter._evaluator
    ):
        raise TypeError("Patch 6 retrieval adapter evaluator changed")
    return value._retriever
