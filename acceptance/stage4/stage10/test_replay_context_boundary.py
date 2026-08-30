from __future__ import annotations

import pytest

from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import (
    AttemptId,
    RepositoryRevision,
    RunId,
    compute_envelope_binding_sha256,
    create_common_envelope,
)
from synapse.experiments.gold.replay import (
    ReplayObservation,
    replay_observation_from_dict,
    validate_replay_observation,
)
from synapse.experiments.gold.stage10.context import (
    ContextFailureCode,
    ContextViolation,
    build_worker_context,
)
from tests import stage4_gold_replay_support as replay_support


@pytest.fixture(scope="module")
def genuine_unadmitted_observation() -> ReplayObservation:
    prepared, _transitions = replay_support.scripted_prepared(
        ["stage10-foreign-observation"]
    )
    return prepared.run().observations[0]


def test_worker_prose_cannot_substitute_for_a_typed_replay_observation() -> None:
    worker_claim = {
        "transcript_matched": True,
        "summary": "I replayed it and everything passed",
    }

    with pytest.raises(ValueError):
        validate_replay_observation(worker_claim)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ReplayObservation()


def test_genuine_observation_for_an_unadmitted_behavior_is_rejected(
    stage10_delivery_world,
    genuine_unadmitted_observation: ReplayObservation,
) -> None:
    observation = genuine_unadmitted_observation
    current = stage10_delivery_world.context
    current_envelope = current.admitted_knowledge.envelope

    assert observation.envelope.run_id == current_envelope.run_id
    assert observation.envelope.attempt_id == current_envelope.attempt_id
    assert observation.envelope.repository_revision == current_envelope.repository_revision
    assert observation.envelope.policy_version == current_envelope.policy_version
    assert (
        observation.envelope.environment_profile_id
        == current_envelope.environment_profile_id
    )

    with pytest.raises(ContextViolation) as rejected:
        build_worker_context(
            intent=current.intent,
            accepted_plan=current.accepted_plan,
            attempt_id=current.attempt_id,
            admitted_knowledge=current.admitted_knowledge,
            knowledge_selection=current.knowledge_selection,
            knowledge_items=current.knowledge_items,
            replay_observations=(observation,),
        )
    assert rejected.value.failure_code is ContextFailureCode.KNOWLEDGE_NOT_ADMITTED


@pytest.mark.parametrize(
    "foreign_field",
    (
        "run_id",
        "attempt_id",
        "repository_revision",
        "policy_version",
        "environment_profile_id",
    ),
)
def test_foreign_execution_identity_is_rejected_before_behavior_matching(
    stage10_delivery_world,
    genuine_unadmitted_observation: ReplayObservation,
    foreign_field: str,
) -> None:
    observation = genuine_unadmitted_observation
    stored = observation.to_dict()
    payload = stored["payload"]
    payload_bytes = canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    original_envelope = observation.envelope
    foreign_envelope = create_common_envelope(
        schema_version=original_envelope.schema_version,
        identity_domain=original_envelope.record_id.domain,
        canonical_payload_bytes=payload_bytes,
        run_id=(
            RunId("stage10-foreign-run")
            if foreign_field == "run_id"
            else original_envelope.run_id
        ),
        attempt_id=(
            AttemptId("stage10-foreign-attempt")
            if foreign_field == "attempt_id"
            else original_envelope.attempt_id
        ),
        created_at_utc=original_envelope.created_at_utc,
        producer_component=original_envelope.producer_component,
        repository_revision=(
            RepositoryRevision.git_commit("f" * 40)
            if foreign_field == "repository_revision"
            else original_envelope.repository_revision
        ),
        policy_version=(
            "stage10.foreign.policy:v1"
            if foreign_field == "policy_version"
            else original_envelope.policy_version
        ),
        environment_profile_id=(
            "stage10.foreign.environment:v1"
            if foreign_field == "environment_profile_id"
            else original_envelope.environment_profile_id
        ),
        lineage_parent_ids=original_envelope.lineage_parent_ids,
    )
    foreign_observation = replay_observation_from_dict(
        {
            "envelope": foreign_envelope.to_dict(),
            "envelope_binding_sha256": compute_envelope_binding_sha256(
                foreign_envelope
            ),
            "payload": payload,
        }
    )
    current = stage10_delivery_world.context

    with pytest.raises(ContextViolation) as rejected:
        build_worker_context(
            intent=current.intent,
            accepted_plan=current.accepted_plan,
            attempt_id=current.attempt_id,
            admitted_knowledge=current.admitted_knowledge,
            knowledge_selection=current.knowledge_selection,
            knowledge_items=current.knowledge_items,
            replay_observations=(foreign_observation,),
        )

    assert rejected.value.failure_code is ContextFailureCode.AUTHORIZATION_MISMATCH
