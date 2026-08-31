"""Create the genuine durable retrieval evidence used by one Stage 11 attempt."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.retrieval import (
    RANKING_PROFILE_V1,
    RETRIEVAL_POLICY_V1,
    RetrievalAdmission,
    RetrievalResult,
    configure_durable_retrieval_persistence,
    configure_ranking_feature_provider,
    configure_retriever,
    create_retrieval_query,
    enumerate_retrieval_candidates_durably,
    gate_selectable_candidates,
    select_and_load_durably,
)


_RANKING_INPUT_SCHEMA = "acceptance.stage4.runner/ranking-input/v1"


@dataclass(frozen=True)
class DurablePointOfUseRetrieval:
    """The exact retrieval gate capability and the causal record it produced."""

    admission: RetrievalAdmission
    result: RetrievalResult


def _ranking_input_ref(query_id, descriptor_id) -> HashBoundRef:
    raw = f"{query_id.value}\0{descriptor_id.value}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=_RANKING_INPUT_SCHEMA,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def durable_retrieval_factory(root: Path):
    """Return the callback used while the shared point-of-use world is minted.

    The callback uses the already configured compatibility evaluator and the
    exact committed snapshot supplied by that world.  Its retrieval decision is
    subsequently embedded into the world's four-stage admission chain, so the
    causal record and the fresh consumption admission name the same decision.
    """

    def create(**inputs) -> DurablePointOfUseRetrieval:
        supported = inputs["supported"]
        evaluator = inputs["evaluator"]
        context = inputs["compatibility_context"]
        fence = inputs["mutation_fence"]
        case_root = root / fence.coordinator_id()
        case_root.mkdir(parents=True, exist_ok=True)
        descriptor_by_key = {entry.content_key: descriptor for _, descriptor, entry in supported}
        provider = configure_ranking_feature_provider(
            component_id="stage11-acceptance-score-provider",
            component_version="synapse.stage11.acceptance-score-provider/v1",
            scoring_profile=RANKING_PROFILE_V1,
            scorer=lambda query_id, descriptor_id, score_input: 500_000,
            input_ref_resolver=_ranking_input_ref,
            actor_identity=evaluator.score_provider_actor,
        )
        retriever = configure_retriever(
            authority_handle=inputs["world"].handle,
            evaluator=evaluator,
            evaluator_declaration=evaluator.declaration,
            retrieval_policy=RETRIEVAL_POLICY_V1,
            trusted_clock=inputs["trusted_clock"],
            descriptor_resolver=lambda entry: descriptor_by_key[entry.content_key],
            conflict_proposal_resolver=lambda context, decisions, descriptors: (),
            ranking_provider=provider,
            library=inputs["world"].library,
        )
        kinds = tuple(
            sorted(
                {unit.core.behavior_kind for unit, _, _ in supported},
                key=lambda item: item.value,
            )
        )
        query = create_retrieval_query(
            retriever=retriever,
            context=context,
            requested_behavior_kinds=kinds,
            required_binding_targets=(),
            selected_set_limit=len(supported),
        )
        compatibility = FileCompatibilityStore(
            case_root / "compatibility",
            mutation_fence=fence,
        )
        gate_journal = FileAdmissionJournal(
            case_root / "retrieval-gate" / "decisions.journal",
            fence,
        )
        persistence = configure_durable_retrieval_persistence(
            compatibility_history=compatibility,
            admission_causal_history=inputs["causal_history"],
        )
        enumeration = enumerate_retrieval_candidates_durably(
            retriever=retriever,
            context=context,
            query=query,
            frozen=inputs["frozen"],
            persistence=persistence,
        )
        admission = gate_selectable_candidates(
            controller=inputs["controller"],
            candidates=enumeration.subject_refs,
            consumer_context_ref=inputs["consumer_context_ref"],
            boundary_ref=inputs["boundary_ref"],
            frozen=inputs["frozen"],
            requested=inputs["requested"],
            publication_decision=inputs["publication_decision"],
            entitlements=inputs["entitlements"],
            journal=gate_journal,
            trusted_clock=inputs["trusted_clock"],
        )
        result = select_and_load_durably(
            retriever=retriever,
            context=context,
            query=query,
            enumeration=enumeration,
            admission=admission,
            persistence=persistence,
        )
        return DurablePointOfUseRetrieval(admission=admission, result=result)

    return create


__all__ = ["DurablePointOfUseRetrieval", "durable_retrieval_factory"]
