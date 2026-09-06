"""Rollback evidence survives reopening and remains separate from task verdicts."""

from dataclasses import replace

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.stage12.outcome import evaluate_attempt_outcome
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold.stage13.publication_store import PublicationStore
from synapse.experiments.gold.stage13.run_publication import publish_attempt


@pytest.fixture(scope="module")
def attempt(tmp_path_factory):
    return negative_attempt(tmp_path_factory.mktemp("quarantine-outcome"))


def test_recovered_quarantine_is_a_verified_attempt_result(tmp_path, monkeypatch, attempt):
    case = publication_case(tmp_path / "project", attempt)
    phase = case.publisher._phase

    def interrupt(tx, name, ticket):
        phase(tx, name, ticket)
        if name == "INDEXED":
            raise SystemExit("acceptance interruption")

    monkeypatch.setattr(case.publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    project = open_gold_project(case.project.declaration.state_root)
    stores = replace(case.publisher.authority.stores, authority_handle=project.authority_handle,
        library=project.library, lifecycle_store=project.lifecycle_store, attestation_store=project.attestation_store,
        admission_journal=project.admission_journal, fence=project.fence)
    publisher = PublicationStore(root=case.publisher.root,
        authority=replace(case.publisher.authority, stores=stores, taint_store=project.taint_store))
    epoch = project.fence.current_epoch()
    world, context = attempt.world, attempt.prefix.context
    with world.composition.record_recovery.session() as session:
        publish_attempt(publisher=publisher, session=session, verification=attempt.verification,
                        manifest=world.manifest, context=context, c1=attempt.c1)
    verification = verify_attempt(manifest=world.manifest, context=context,
        run_store=world.composition.record_store, boundary=world.boundary,
        record_store=world.stage10_composition.record_store, profile=world.attempt_inputs.plan_profile,
        run_root=world.run_root, reusable_authority=stores, publication_store=publisher)
    outcome = evaluate_attempt_outcome(verification).payload()
    assert outcome["status"] == "UNRESOLVED"
    assert outcome["publication_result"] == "QUARANTINED"
    assert len(outcome["publication_refs"]) == 1
    assert outcome["created_behaviors"] == []
    assert verification.payload()["publication"]["reason_codes"] == ["INTERRUPTED_TRANSACTION_ROLLED_BACK"]
    recorded = world.composition.record_store.get(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index))
    with world.composition.record_recovery.session() as session:
        publish_attempt(publisher=publisher, session=session, verification=verification,
                        manifest=world.manifest, context=context, c1=attempt.c1)
    assert world.composition.record_store.get(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index)) == recorded
    assert project.library.search_index() == ()
    assert project.fence.current_epoch() == epoch

    path = publisher.root / "quarantine" / (case.request.transaction_id + ".json")
    raw = path.read_bytes()
    try:
        path.write_bytes(raw.replace(b'"ROLLED_BACK"', b'"COMMITTED"'))
        checked = verify_attempt(manifest=world.manifest, context=context,
            run_store=world.composition.record_store, boundary=world.boundary,
            record_store=world.stage10_composition.record_store, profile=world.attempt_inputs.plan_profile,
            run_root=world.run_root, reusable_authority=stores, publication_store=publisher)
        assert "PUBLICATION_PROOF_INVALID" in checked.payload()["failure_codes"]
        assert evaluate_attempt_outcome(checked).payload()["status"] == "INVALID_CONTRACT"
    finally:
        path.write_bytes(raw)
