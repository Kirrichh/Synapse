"""Write entitlement has no synthetic retrieval dependencies or read roles."""

import pytest

from tests.test_stage4_gold_admission import gate_declaration, NOW, CLEAN_TAINT, GRANT, SUBJECTS
from synapse.experiments.gold import admission as A, authority_config as AC
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityRole, GateKind, RunId, AttemptId


def test_write_gate_configuration_has_only_its_declared_responsibility():
    control = A.configure_gate_controller(
        declaration=gate_declaration(roles={GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
                                           GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR}),
        policy_version="policy-v1", run_id=RunId("write-run"), attempt_id=AttemptId("write-attempt"),
        repository_revision="a" * 40, environment_profile_id="write-env", trusted_clock=lambda: NOW,
        taint_probe=lambda ref: CLEAN_TAINT, provenance_probe=lambda ref: True,
        lifecycle_probe=lambda ref: True, grant_probe=lambda: GRANT, producer_actor=ActorIdentity("producer"))
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    assert ingestion.admitted
    assert control._source_actors == (ActorIdentity("producer"),)
    with pytest.raises(AC.AuthorityConfigViolation) as caught:
        A.evaluate_retrieval_gate(control, subject_refs=SUBJECTS, consumer_context_ref=None,
            boundary_ref=None, frozen_candidate_set_ref=None, requested=None, predecessor=ingestion)
    assert caught.value.failure_code is AC.AuthorityConfigFailureCode.ROLE_NOT_DECLARED
