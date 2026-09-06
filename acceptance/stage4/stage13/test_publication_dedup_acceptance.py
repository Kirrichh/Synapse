"""Existing content gains distinct publication provenance without another blob."""

from acceptance.stage4.stage12._reusable_case import reusable_case
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import _builder_runtime_identity, open_gold_project
from synapse.experiments.gold.provenance import behavior_attestation_to_ref
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold.stage13.publication import PublicationAuthority
from synapse.experiments.gold.stage13.publication_store import PublicationStore


def test_existing_blob_receives_new_atomic_provenance(tmp_path):
    case = reusable_case(tmp_path)
    world, project = case.world, case.project
    digest = case.unit.content_key.digest_sha256
    blob = project.library.root / "objects" / "blobs" / digest[:2] / digest[2:]
    before_bytes, before_inode = blob.read_bytes(), blob.stat().st_ino
    old_ref = behavior_attestation_to_ref(case.attestation)
    old_context = project.lifecycle_store.records()[-1].context
    old_state = project.lifecycle_store.current_state(subject_ref=old_ref, context=old_context)
    lifecycle_count = len(project.lifecycle_store.records())
    verification = verify_attempt(manifest=world.manifest, context=case.prefix.context,
        run_store=world.composition.record_store, boundary=world.boundary,
        record_store=world.stage10_composition.record_store, profile=world.attempt_inputs.plan_profile,
        run_root=world.run_root, reusable_authority=case.authority)
    authority = PublicationAuthority(case.authority, project.taint_store,
                                    _builder_runtime_identity(project.declaration), (ActorIdentity("worker"),))
    publisher = PublicationStore(root=project.declaration.state_root / "publications", authority=authority)
    request = authority.prepare(verification=verification, manifest=world.manifest, context=case.prefix.context, c1=case.c1)
    assert request.unit.content_key == case.unit.content_key
    assert behavior_attestation_to_ref(request.attestation) != old_ref
    result = publisher.publish(request)
    payload = result.payload()
    assert blob.read_bytes() == before_bytes and blob.stat().st_ino == before_inode
    assert len(project.library.search_index()) == 1
    assert len(project.lifecycle_store.records()) == lifecycle_count + 7
    assert project.lifecycle_store.current_state(subject_ref=old_ref, context=request.context) is old_state
    assert payload["created_refs"]["attestation"] == behavior_attestation_to_ref(request.attestation).to_dict()
    assert len(payload["created_refs"]["lifecycle"]) == 7
    assert len(payload["created_refs"]["admission"]) == 2
    reopened = open_gold_project(project.declaration.state_root)
    assert len(reopened.library.search_index()) == 1
    assert reopened.attestation_store.contains(authority_handle=reopened.authority_handle, attestation=case.attestation)
    assert reopened.attestation_store.contains(authority_handle=reopened.authority_handle, attestation=request.attestation)
