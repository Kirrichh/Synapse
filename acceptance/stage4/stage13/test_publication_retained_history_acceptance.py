"""Rollback preserves previously admitted content and its complete provenance."""

import json
import subprocess
import sys

import pytest

from acceptance.stage4.stage12._reusable_case import reusable_case
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import _builder_runtime_identity, open_gold_project
from synapse.experiments.gold.provenance import behavior_attestation_to_ref
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold.stage13.publication import PublicationAuthority
from synapse.experiments.gold.stage13.publication_store import PublicationStore


def test_interrupted_new_provenance_preserves_existing_admission(tmp_path, monkeypatch):
    case = reusable_case(tmp_path)
    world, project = case.world, case.project
    root = project.declaration.state_root
    old_ref = behavior_attestation_to_ref(case.attestation)
    old_context = project.lifecycle_store.records()[-1].context
    old_state = project.lifecycle_store.require_consumable(subject_ref=old_ref, context=old_context)
    old_index = project.library.search_index()
    retained = {path: path.read_bytes() for directory in
        ("library", "lifecycle", "attestations", "admission", "taint")
        for path in (root / directory).rglob("*") if path.is_file()}
    verification = verify_attempt(manifest=world.manifest, context=case.prefix.context,
        run_store=world.composition.record_store, boundary=world.boundary,
        record_store=world.stage10_composition.record_store, profile=world.attempt_inputs.plan_profile,
        run_root=world.run_root, reusable_authority=case.authority)
    authority = PublicationAuthority(case.authority, project.taint_store,
        _builder_runtime_identity(project.declaration), (ActorIdentity("worker"),))
    publisher = PublicationStore(root=root / "publications", authority=authority)
    request = authority.prepare(verification=verification, manifest=world.manifest,
        context=case.prefix.context, c1=case.c1)
    assert request.unit.content_key == case.unit.content_key
    assert behavior_attestation_to_ref(request.attestation) != old_ref
    phase = publisher._phase

    def interrupt(tx, name, ticket):
        phase(tx, name, ticket)
        if name == "INDEXED":
            raise SystemExit("acceptance publication interruption")

    monkeypatch.setattr(publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        publisher.publish(request)
    command = """
import json, sys
from pathlib import Path
from synapse.experiments.gold.knowledge_environment import open_gold_project
project = open_gold_project(Path(sys.argv[1]))
print(json.dumps({'entries': len(project.library.search_index()), 'epoch': project.fence.current_epoch()}))
"""
    recovered = subprocess.run([sys.executable, "-c", command, str(root)], capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stderr
    observation = json.loads(recovered.stdout)
    assert observation["entries"] == len(old_index) == 1
    assert observation["epoch"] % 2 == 0
    assert {path: path.read_bytes() for path in retained} == retained
    reopened = open_gold_project(root)
    assert reopened.library.search_index() == old_index
    assert reopened.lifecycle_store.require_consumable(subject_ref=old_ref, context=old_context) == old_state
    assert reopened.attestation_store.contains(authority_handle=reopened.authority_handle, attestation=case.attestation)
    assert not reopened.attestation_store.contains(authority_handle=reopened.authority_handle, attestation=request.attestation)
    assert reopened.fence.current_epoch() == observation["epoch"]
    assert publisher.read_quarantine(request.transaction_id)["state"] == "ROLLED_BACK"
