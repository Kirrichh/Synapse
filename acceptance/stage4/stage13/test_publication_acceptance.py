"""Complete publication, exact physical proof and idempotency acceptance."""

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold.stage12.reusable import verify_reusable_candidate
from synapse.experiments.gold.knowledge_environment import open_gold_project


def test_real_publication_commits_all_participants_and_reopens(tmp_path):
    attempt = negative_attempt(tmp_path / "attempt")
    case = publication_case(tmp_path / "project", attempt)
    result = case.publisher.publish(case.request)
    payload = result.payload()
    assert payload["transaction_id"] == case.request.transaction_id
    assert len(case.project.library.search_index()) == 1
    proof = verify_reusable_candidate(payload["registration"], authority=case.publisher.authority.stores,
        manifest=attempt.world.manifest, context=attempt.prefix.context,
        task_contract_ref=attempt.world.attempt_inputs.plan_profile.task_contract.reference, c1=attempt.c1)
    assert proof["publication_ref"] == result.reference.to_dict()
    before = (case.project.fence.current_epoch(), case.project.admission_journal.current_sequence(),
              case.project.lifecycle_store.current_anchor(), case.project.attestation_store.current_anchor())
    assert case.publisher.publish(case.request).reference == result.reference
    after = (case.project.fence.current_epoch(), case.project.admission_journal.current_sequence(),
             case.project.lifecycle_store.current_anchor(), case.project.attestation_store.current_anchor())
    assert before == after
    reopened = open_gold_project(case.project.declaration.state_root)
    assert reopened.library.search_index() == case.project.library.search_index()
