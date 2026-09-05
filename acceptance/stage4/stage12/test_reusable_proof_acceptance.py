"""Admission is checked against owners, not a candidate's string or label."""

import copy
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from acceptance.stage4.stage12._reusable_case import reusable_case
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage12.outcome import inspect_outcome
from synapse.experiments.gold.stage12.reusable import register_reusable_candidate, verify_reusable_candidate
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold import admission as A
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.provenance import configure_platform_attester, behavior_attestation_to_ref
from synapse.experiments.gold.lifecycle import LifecycleContext
from tests.test_stage4_gold_compatibility import _append_admitted


@pytest.fixture(scope="module")
def admitted(tmp_path_factory):
    case = reusable_case(tmp_path_factory.mktemp("reusable-proof"))
    with case.world.composition.record_recovery.session() as session:
        register_reusable_candidate(session=session, authority=case.authority, manifest=case.world.manifest,
            context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1,
            unit=case.unit, behavior_manifest=case.behavior_manifest,
            attestation=case.attestation, write_evidence=case.write)
    stored = case.world.composition.record_store.get(kind=RecordKind.REUSABLE_CANDIDATE, key="1")
    return case, stored.payload


@pytest.mark.parametrize("field,value", [
    ("publication", {"admission_id": "approved"}), ("ingestion", None),
    ("journal_anchor", "0" * 64), ("journal_sequence", 0), ("context_sha256", "0" * 64),
    ("domain", {"scope": "all-repositories"}), ("lifecycle_context", {"scope": "all"}),
])
def test_foreign_missing_or_widened_proof_cannot_establish_partial(admitted, field, value):
    case, payload = admitted
    changed = copy.deepcopy(payload)
    changed[field] = value
    with pytest.raises(ValueError):
        verify_reusable_candidate(changed, authority=case.authority, manifest=case.world.manifest,
            context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1)


def test_a_relabelled_executable_is_not_the_verified_guard(admitted):
    case, payload = admitted
    changed = copy.deepcopy(payload)
    changed["unit"]["core"]["behavior_kind"] = "procedure"
    with pytest.raises(ValueError):
        verify_reusable_candidate(changed, authority=case.authority, manifest=case.world.manifest,
            context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1)


def test_an_admit_label_does_not_grant_a_different_future_use_domain(admitted):
    case, _ = admitted
    with pytest.raises(ValueError, match="exact grant"):
        A.require_publication_grant(case.write.publication, granted=A.GrantEnvelope(
            ("all-repositories",), (), (), case.world.manifest.versions.policy_version,
        ))


def test_committed_attestation_cannot_substitute_another_oracle_identity(admitted):
    case, payload = admitted
    original = case.attestation
    attester = configure_platform_attester(authority_handle=case.project.authority_handle,
        builder_runtime_identity=original.builder_runtime_identity, trusted_clock=lambda: datetime.now(timezone.utc))
    observed = attester.observe(authority_handle=case.project.authority_handle,
        repository_revision=original.repository_revision, base_revision=original.base_revision,
        task_contract_ref=original.task_contract_ref, policy_inputs=original.policy_inputs,
        environment_inputs=original.environment_inputs, tool_inputs=original.tool_inputs,
        source_refs=original.source_refs, verification_refs=original.verification_refs,
        oracle_observation=replace(original.oracle_observation, oracle_identity=ActorIdentity("foreign-oracle")))
    foreign = attester.attest(authority_handle=case.project.authority_handle, observed=observed,
        subject_content_key=original.subject_content_key, producer_run_id=original.producer_run_id,
        producer_attempt_id=original.producer_attempt_id, producer_actor_ids=original.producer_actor_ids)
    case.project.attestation_store.append(authority_handle=case.project.authority_handle, attestation=foreign)
    _append_admitted(case.project.lifecycle_store, case.project.authority_handle,
        behavior_attestation_to_ref(foreign), LifecycleContext.from_dict(payload["lifecycle_context"]))
    changed = copy.deepcopy(payload)
    changed["attestation"] = foreign.to_dict()
    with pytest.raises(ValueError, match="independently verified output"):
        verify_reusable_candidate(changed, authority=case.authority, manifest=case.world.manifest,
            context=case.prefix.context, task_contract_ref=case.task_ref, c1=case.c1)


def test_missing_committed_admission_lowers_the_real_verification(admitted):
    case, _ = admitted
    journal = case.project.admission_journal.path
    retained = journal.read_bytes()
    try:
        journal.write_bytes(b"corrupt retained admission")
        record = verify_attempt(manifest=case.world.manifest, context=case.prefix.context,
            run_store=case.world.composition.record_store, boundary=case.world.boundary,
            record_store=case.world.stage10_composition.record_store,
            profile=case.world.attempt_inputs.plan_profile, run_root=case.world.run_root,
            reusable_authority=case.authority)
        assert record.payload()["reusable_candidates"] == []
        assert "REUSABLE_PROOF_INVALID" in record.payload()["failure_codes"]
    finally:
        journal.write_bytes(retained)


def test_rehashed_run_cannot_upgrade_partial_to_task_success(admitted):
    case, _ = admitted
    result = case.world.execute()
    changed = copy.deepcopy(result.structured_outcome)
    changed["payload"]["status"] = "FULL"
    raw = encode_canonical(changed["payload"])
    digest = hashlib.sha256(raw).hexdigest()
    changed["outcome_ref"].update(ref_id=digest, sha256=digest, byte_length=len(raw))
    with pytest.raises(ValueError, match="terminal attempt"):
        inspect_outcome(changed)
