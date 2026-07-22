from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.compatibility import CompatibilityDecisionKind
from synapse.experiments.gold.retrieval import (
    RANKING_PROFILE_V1,
    RETRIEVAL_POLICY_V1,
    CandidateDisposition,
    RetrievalFailureCode,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalViolation,
    configure_ranking_feature_provider,
    configure_retriever,
    create_retrieval_query,
    retrieve_and_load,
    retrieval_query_from_dict,
    revalidate_loaded_before_consumption,
    validate_ranking_feature_observation,
    validate_retrieval_decision,
)

from tests.test_stage4_gold_compatibility import NOW, _make_harness, _ref


def _configured_retriever(
    harness,
    *,
    scorer,
    descriptor_resolver=None,
    score_input_resolver=None,
):
    provider = configure_ranking_feature_provider(
        component_id="semantic-score-provider",
        component_version="synapse.stage4.semantic-score-provider/v1",
        scoring_profile=RANKING_PROFILE_V1,
        scorer=scorer,
        input_ref_resolver=(
            score_input_resolver
            if score_input_resolver is not None
            else lambda query_id, descriptor_id: _ref(
                f"score-{query_id.value[-8:]}-{descriptor_id.value[-8:]}",
                RefKind.ARTIFACT,
            )
        ),
        actor_identity=harness.evaluator.score_provider_actor,
    )
    resolver = (
        descriptor_resolver
        if descriptor_resolver is not None
        else lambda entry: harness.descriptor
    )
    retriever = configure_retriever(
        authority_handle=harness.handle,
        evaluator=harness.evaluator,
        evaluator_declaration=harness.declaration,
        retrieval_policy=RETRIEVAL_POLICY_V1,
        trusted_clock=lambda: NOW,
        descriptor_resolver=resolver,
        conflict_proposal_resolver=lambda context, decisions, descriptors: (),
        ranking_provider=provider,
        library=harness.library,
    )
    query = create_retrieval_query(
        retriever=retriever,
        context=harness.context,
        requested_behavior_kinds=(harness.unit.core.behavior_kind,),
        required_binding_targets=(),
        selected_set_limit=1,
    )
    return retriever, provider, query


def test_s4_p6_acc_retrieval_01_compatibility_precedes_score_provider_and_ranking(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, revoked=True)
    score_calls: list[str] = []

    def scorer(query_id, descriptor_id, score_input):
        score_calls.append(descriptor_id.value)
        return 1_000_000

    retriever, _, query = _configured_retriever(harness, scorer=scorer)
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.REVOKED
    assert score_calls == []
    assert len(result.decision.considered_candidates) == 1
    assert result.decision.considered_candidates[0].compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert result.decision.considered_candidates[0].disposition is CandidateDisposition.REJECTED
    assert result.decision.selected_candidate_ids == ()


def test_s4_p6_acc_retrieval_02_all_considered_candidates_and_rejections_remain_in_audit(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_unresolved=2)
    entries = harness.library.search_index()
    known_key = harness.entry.content_key

    def descriptor_resolver(entry):
        if entry.content_key == known_key:
            return harness.descriptor
        raise RetrievalViolation(RetrievalFailureCode.DESCRIPTOR_MISSING, "descriptor absent from catalog")

    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        descriptor_resolver=descriptor_resolver,
    )
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert len(entries) == 3
    assert query.selected_set_limit == 1
    assert len(result.decision.considered_candidates) == 3
    assert len(result.decision.selected_candidate_ids) == 1
    unavailable = tuple(
        item
        for item in result.decision.considered_candidates
        if item.disposition is CandidateDisposition.DESCRIPTOR_UNAVAILABLE
    )
    assert len(unavailable) == 2
    assert unavailable[0].failure_code is RetrievalFailureCode.DESCRIPTOR_MISSING
    assert unavailable[1].failure_code is RetrievalFailureCode.DESCRIPTOR_MISSING


def test_s4_p6_acc_retrieval_03_semantic_score_never_grants_eligibility(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, revoked=True)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1_000_000,
    )
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    candidate = result.decision.considered_candidates[0]
    assert candidate.compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert candidate.ranking_feature_id is None
    assert candidate.ranking_key is None
    assert candidate.disposition is CandidateDisposition.REJECTED
    assert result.decision.selected_candidate_ids == ()


def test_s4_p6_acc_retrieval_04_revoked_candidate_is_never_selected(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, revoked=True)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1_000_000,
    )
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert result.decision.outcome is RetrievalOutcome.NO_CANDIDATES
    assert result.decision.considered_candidates[0].compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert result.decision.selected_candidate_ids == ()
    assert result.load_decisions == ()


def test_s4_p6_acc_retrieval_05_identity_bound_scores_reproduce_order_and_conflicting_scores_fail_closed(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    observed_scores = [700_000, 700_000, 700_001]

    def scorer(query_id, descriptor_id, score_input):
        return observed_scores.pop(0)

    retriever, provider, query = _configured_retriever(harness, scorer=scorer)
    first = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    second = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    first_feature = first.decision.ranking_feature_observations[0]
    second_feature = second.decision.ranking_feature_observations[0]
    assert first_feature.semantic_score_micros == 700_000
    assert first_feature.observation_id == second_feature.observation_id
    assert first.decision.considered_candidates[0].ranking_key == second.decision.considered_candidates[0].ranking_key
    validate_ranking_feature_observation(first_feature, provider=provider)
    with pytest.raises(RetrievalViolation) as exc:
        retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert exc.value.failure_code is RetrievalFailureCode.RANKING_INPUT_INCONSISTENT


@pytest.mark.parametrize("score", [True, -1, 1_000_001, 0.5, "100"])
def test_s4_p6_acc_retrieval_score_01_exact_bounded_integer_only(tmp_path: Path, score: object) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: score,
    )
    with pytest.raises(RetrievalViolation) as exc:
        retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert exc.value.failure_code is RetrievalFailureCode.RANKING_INPUT_MALFORMED


def test_s4_p6_acc_retrieval_loading_01_stage2_precedes_verified_load_and_stage3_executes_nothing(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 400_000,
    )
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert result.decision.outcome is RetrievalOutcome.SELECTED
    assert len(result.load_decisions) == 1
    load = result.load_decisions[0]
    assert load.loaded_content_key == harness.descriptor.content_key.value
    assert load.loaded_manifest_id == harness.descriptor.manifest_id.value
    stage3 = revalidate_loaded_before_consumption(
        retriever=retriever,
        context=harness.context,
        retrieval_decision=result.decision,
        load_decision=load,
    )
    assert stage3.prior_revalidation_id == load.before_loading_revalidation_id
    assert stage3.failure_code is None


def test_s4_p6_acc_retrieval_loading_02_publication_during_score_blocks_load(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)

    def scorer(query_id, descriptor_id, score_input):
        harness.publish_extra("parallel-publication")
        return 900_000

    retriever, _, query = _configured_retriever(harness, scorer=scorer)
    with pytest.raises(RetrievalViolation) as exc:
        retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    assert exc.value.failure_code is RetrievalFailureCode.SNAPSHOT_DRIFT


def test_s4_p6_acc_retrieval_query_01_strict_context_bound_transport_and_sealed_records(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1,
    )
    assert retrieval_query_from_dict(query.to_dict(), retriever=retriever, context=harness.context).to_dict() == query.to_dict()
    altered = query.to_dict()
    altered["selected_set_limit"] = 2
    with pytest.raises(RetrievalViolation):
        retrieval_query_from_dict(altered, retriever=retriever, context=harness.context)
    with pytest.raises(TypeError):
        RetrievalQuery()
    with pytest.raises((TypeError, ValueError)):
        replace(query, selected_set_limit=0)


def test_s4_p6_acc_retrieval_audit_01_decision_binds_conflict_and_complete_candidate_records(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 123_456,
    )
    result = retrieve_and_load(retriever=retriever, context=harness.context, query=query)
    validate_retrieval_decision(
        result.decision,
        retriever=retriever,
        query=query,
        context=harness.context,
    )
    assert len(result.decision.considered_candidates) == len(harness.library.search_index())
    assert len(result.decision.conflict_records) == 1
    assert result.decision.conflict_records[0].compatibility_scan_id == result.decision.conflict_scan_id
    assert result.decision.ranking_feature_observations[0].observation_id == result.decision.considered_candidates[0].ranking_feature_id


def test_s4_p6_acc_retrieval_fixture_01_literal_case_registry_is_versioned_and_complete() -> None:
    fixture = Path(__file__).parent / "fixtures" / "gold" / "retrieval_cases_v1.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["schema_version"] == "synapse.stage4.gold.retrieval-acceptance-cases/v1"
    assert data["compatibility_policy"] == "synapse.stage4.gold.compatibility-policy/v1"
    assert data["retrieval_policy"] == RETRIEVAL_POLICY_V1
    assert data["ranking_profile"] == RANKING_PROFILE_V1
    names = tuple(item["name"] for item in data["cases"])
    assert len(names) == 21
    assert len(set(names)) == 21
    assert "fully-compatible-candidate" in names
    assert "revoked-lifecycle" in names
    assert "conflicting-score-observations" in names
    assert "toctou-before-consumption" in names
