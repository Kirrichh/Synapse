"""A real §22 write admission for suites whose subject is not admission.

`BehaviorLibrary.put_behavior` now demands a `LibraryWriteAdmission`, and the
only honest way for the library and compatibility suites to satisfy that is to
run the ingestion and publication gates for real. Handing back a hand-made
object would defeat the barrier those suites now sit behind, and there is no
hand-made object to hand back — the capability is sealed.

So the probes here are permissive and the gates are genuine. The pre-existing
assertions go on measuring what they always measured, while every write in those
suites crosses the two gates the way a production write does.

This module is deliberately not named ``test_*``: it is a fixture, not a suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold import library_admission as LA
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    GateKind,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)

GATE_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
GATE_POLICY_VERSION = "policy-v1"


def write_gate_controller(
    *,
    admit_ingestion: bool = True,
    admit_publication: bool = True,
) -> tuple[A.ConfiguredGateController, A.RequestedEnvelope]:
    """A configured controller whose probes admit unless told otherwise.

    ``admit_ingestion=False`` drives a refusal through the provenance probe and
    ``admit_publication=False`` through the lifecycle probe, so a blocked write
    is blocked by a real gate reaching a real verdict rather than by a flag read
    somewhere downstream.
    """

    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="write-attester"),
        builder_actor=ActorIdentity(value="write-builder"),
        taint_classifier_authority=AuthorityIdentity(value="write-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="write-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="write-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="write-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="write-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="write-gate-authority"),
        evaluator_component_id="write-gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version=GATE_POLICY_VERSION,
        trusted_clock=lambda: GATE_NOW,
    )
    grant = A.GrantEnvelope(
        scopes=("repo:x",),
        capabilities=("read",),
        oracles=("swebench",),
        policy_version=GATE_POLICY_VERSION,
    )
    requested = A.RequestedEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",)
    )
    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version=GATE_POLICY_VERSION,
        trusted_clock=lambda: GATE_NOW,
        taint_probe=lambda item: A.TaintFinding(
            consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
        ),
        provenance_probe=lambda item: admit_ingestion,
        lifecycle_probe=lambda item: admit_publication,
        compatibility_probe=lambda item, ctx: A.CompatibilityFinding(
            compatible=True, evidence_complete=True, drifted=False,
            conflicts_unresolved=False, subject_ref=item, consumer_context_ref=ctx,
        ),
        boundary_probe=lambda item: True,
        grant_probe=lambda: grant,
        head_reader=lambda: {"boundary_ref": None, "heads": {}},
        producer_actor=ActorIdentity(value="write-producer"),
        retriever_actor=ActorIdentity(value="write-retriever"),
        consumer_actor=ActorIdentity(value="write-consumer"),
    )
    return controller, requested


def write_admission_evidence(
    unit,
    manifest,
    *,
    journal_root: Path,
    admit_ingestion: bool = True,
    admit_publication: bool = True,
) -> LA.WriteAdmissionEvidence:
    """Run both gates over one object and return the whole evidence record."""

    controller, requested = write_gate_controller(
        admit_ingestion=admit_ingestion, admit_publication=admit_publication
    )
    # One journal per subject: these suites write many objects and a shared file
    # would make the durable record of one write depend on the order of another.
    leaf = hashlib.sha256(unit.content_key.value.encode()).hexdigest()[:16]
    journal = FileAdmissionJournal(journal_root / "gate-journals" / f"{leaf}.journal")
    return LA.admit_library_write(
        controller,
        content_key=unit.content_key,
        manifest_id=manifest.manifest_id,
        requested=requested,
        journal=journal,
        trusted_clock=lambda: GATE_NOW,
    )


def write_admission(unit, manifest, *, journal_root: Path):
    """The sealed capability alone, for the common case."""

    return write_admission_evidence(unit, manifest, journal_root=journal_root).admission
