"""Cheap regressions for the existing authority owners used by Stage 12."""

from dataclasses import replace

import pytest

from synapse.experiments.gold.contracts import RepositoryRevision, RunId
from synapse.experiments.gold.provenance import configure_platform_attester, open_behavior_attestation_store
from tests.test_stage4_gold_provenance import _attestation, NOW
from tests.gold_store_fence import fence_for


@pytest.mark.parametrize("different_runtime", [False, True])
def test_attestation_history_accepts_new_verified_revisions_but_pins_runtime(tmp_path, different_runtime):
    first, key, builder, _, handle, attester, _ = _attestation()
    revision = RepositoryRevision.git_commit("3" * 40)
    candidate_builder = replace(builder, repository_revision=revision,
                                runtime_version="foreign-runtime/v1" if different_runtime else builder.runtime_version)
    candidate_attester = configure_platform_attester(authority_handle=handle,
        builder_runtime_identity=candidate_builder, trusted_clock=lambda: NOW)
    observation = candidate_attester.observe(authority_handle=handle, repository_revision=revision,
        base_revision=first.base_revision, task_contract_ref=first.task_contract_ref,
        policy_inputs=first.policy_inputs, environment_inputs=first.environment_inputs,
        tool_inputs=first.tool_inputs, source_refs=first.source_refs, verification_refs=first.verification_refs,
        oracle_observation=replace(first.oracle_observation, verified_repository_revision=revision))
    candidate = candidate_attester.attest(authority_handle=handle, observed=observation,
        subject_content_key=key, producer_run_id=RunId("second-run"), producer_attempt_id=first.producer_attempt_id,
        producer_actor_ids=first.producer_actor_ids)
    store = open_behavior_attestation_store(root=tmp_path, authority_handle=handle,
        platform_attester=attester, mutation_fence=fence_for(tmp_path), allow_genesis=True)
    store.append(authority_handle=handle, attestation=first)
    if different_runtime:
        with pytest.raises(ValueError, match="BUILDER_MISMATCH"):
            store.append(authority_handle=handle, attestation=candidate)
    else:
        store.append(authority_handle=handle, attestation=candidate)
        assert store.contains(authority_handle=handle, attestation=candidate)
