"""The §22 ingestion and publication gates, in front of a library write.

`BehaviorLibrary.put_behavior` asked for a `PublisherIdentity` and nothing else.
A publisher identity says *who* is writing; it says nothing about whether the
candidate may be extracted from its source at all, or whether a verified object
may be published. Those are the first two §22 gates, and a write path that never
asks them is the bypass NR-09 forbids — the gates existed, and the one operation
that puts an object into the library did not consult them.

This owner is the adapter that puts them in front of it. It evaluates both gates
over the object's §22 subject reference, refuses anything that is not an ADMIT,
commits both verdicts durably, and only then mints the `LibraryWriteAdmission`
the library demands. The capability is minted nowhere else: `library.py` cannot
build one, and the factory in `contracts.py` is private and held to a single
importer by a tripwire. That is the difference between a gate in front of a path
and a gate beside it — the distinction the retrieval loader had to be repaired
for in round 11, and the same repair applied here before it could be repeated.

**Direction.** `library.py` is earlier than `admission.py` in the §38 order, so
the arrow must not point back. It does not: the library depends only on the
shared vocabulary record in `contracts.py`, and this adapter is the single place
that knows both sides — exactly the shape `gate_findings.py` already has.

**Atomic publication.** Gate evaluation, durable decision commits, capability
minting, and the library transaction run under one shared exclusive guard and
one mutation interval. The library registers the exact objects and receipts for
that interval, consumes the capability for that write, and closes it before the
interval can settle. There is therefore no reusable admission object and no
rollback window between mint and write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .admission import (
    ConfiguredGateController,
    DecisionCommitReceipt,
    DecisionJournalPort,
    GateDecision,
    RequestedEnvelope,
    commit_gate_decision,
    evaluate_ingestion_gate,
    evaluate_publication_gate,
    require_configured_gate_controller,
    require_dimension_evidence,
    derive_independence_proof,
    require_entitled_decision,
)
from .admission_journal import require_live_guard
from .behavior import BehaviorBlob, BehaviorManifest, SynapseBehaviorUnit
from .canonicalization import ContentKey, HashBoundRef, library_subject_ref
from .contracts import (
    GateDecisionKind,
    GateKind,
    LibraryWriteAdmission,
    RecordId,
    _mint_library_write_admission,
    validate_library_write_admission,
)
from .coordination import (
    SnapshotFencePort,
    require_same_coordinator,
    require_snapshot_fence,
    settle_exclusive_mutation,
)
from .library import BehaviorLibrary, LibraryViolation, PublisherIdentity, PutResult
from .persistence import store_transaction

#: Names taken from another owner's private surface. Enumerated so the seam is a
#: recorded decision rather than an accident: the minting factory is private
#: precisely so that only this module can reach it, and the tripwire that
#: enforces "only this module" reads the declaration below.
ADAPTER_PRIVATE_SEAM = (
    "_mint_library_write_admission",
)


class WriteAdmissionFailureCode(str, Enum):
    """Why a library write was not admitted."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    INGESTION_REFUSED = "INGESTION_REFUSED"
    PUBLICATION_REFUSED = "PUBLICATION_REFUSED"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    DECISION_NOT_DURABLE = "DECISION_NOT_DURABLE"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"


class WriteAdmissionViolation(ValueError):
    """A typed, fail-closed write-admission error carrying no object payload."""

    def __init__(self, failure_code: WriteAdmissionFailureCode, detail: str) -> None:
        if type(failure_code) is not WriteAdmissionFailureCode:
            raise TypeError("failure_code must be an exact WriteAdmissionFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: WriteAdmissionFailureCode, detail: str) -> WriteAdmissionViolation:
    return WriteAdmissionViolation(code, detail)


def write_subject_ref(*, content_key: ContentKey, manifest_id: RecordId) -> HashBoundRef:
    """The §22 name of the object that is about to be written.

    The same reference the retrieval side computes for the same object once it
    is stored. That is not a convenience: a §22 chain binds four decisions over
    one subject set with exact equality, so an object named one way at the write
    and another way at the read could never carry a chain from ingestion through
    to consumption.
    """

    if type(content_key) is not ContentKey or type(manifest_id) is not RecordId:
        raise _fail(
            WriteAdmissionFailureCode.TYPE_MISMATCH,
            "the subject must be named by an exact ContentKey and RecordId",
        )
    # Reading ``.value`` is the seal check on both: each property revalidates its
    # own trusted-object consistency before it will produce text.
    return library_subject_ref(
        content_key=content_key.value,
        manifest_id=manifest_id.value,
        blob_digest_sha256=content_key.digest_sha256,
        manifest_digest_sha256=manifest_id.digest_sha256,
    )


@dataclass(frozen=True, init=False)
class WriteAdmissionEvidence:
    """What the two gates decided, kept alongside the capability they produced.

    The capability names the decisions by identity and carries no verdict of its
    own, which is what keeps `contracts.py` free of gate semantics. An auditor
    still needs the verdicts themselves, so they travel here — beside the write
    path, never on it.
    """

    admission: LibraryWriteAdmission
    ingestion: GateDecision
    publication: GateDecision
    receipts: tuple[DecisionCommitReceipt, ...]
    result: PutResult
    entry_epoch: int
    final_epoch: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> WriteAdmissionEvidence:
        raise TypeError("WriteAdmissionEvidence is produced only by admit_library_write")


_EVIDENCE_SEAL = object()


def validate_write_admission_evidence(value: WriteAdmissionEvidence) -> WriteAdmissionEvidence:
    if (
        type(value) is not WriteAdmissionEvidence
        or getattr(value, "_trusted_seal", None) is not _EVIDENCE_SEAL
    ):
        raise _fail(
            WriteAdmissionFailureCode.TYPE_MISMATCH,
            "write admission evidence is not factory sealed",
        )
    validate_library_write_admission(value.admission)
    if type(value.receipts) is not tuple or len(value.receipts) != 2:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "write evidence needs two receipts")
    if type(value.result) is not PutResult:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "write evidence needs the library result")
    if type(value.entry_epoch) is not int or value.entry_epoch < 0 or value.entry_epoch % 2:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "write evidence needs a settled entry epoch")
    if type(value.final_epoch) is not int or value.final_epoch < 0 or value.final_epoch % 2:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "write evidence needs a settled final epoch")
    return value


def _require_admit(decision: GateDecision, *, gate: GateKind, code: WriteAdmissionFailureCode) -> None:
    """Refuse anything that is not this gate's unambiguous ADMIT.

    `admitted` alone is not the test. A decision carries a gate kind and a
    decision kind, and a REQUIRE_REVIEW or a QUARANTINE is a definite verdict
    that is not an admission — treating "not rejected" as "admitted" is exactly
    the substitution NR-10 forbids.
    """

    if decision.gate_kind is not gate:
        raise _fail(code, f"expected a {gate.value} verdict, received {decision.gate_kind.value}")
    if decision.decision_kind is not GateDecisionKind.ADMIT or not decision.admitted:
        raise _fail(code, f"the {gate.value} gate did not admit this object")


def admit_library_write(
    controller: ConfiguredGateController,
    *,
    library: BehaviorLibrary,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
    publisher_identity: PublisherIdentity,
    requested: RequestedEnvelope,
    journal: DecisionJournalPort,
    entitlements: object,
    fence: SnapshotFencePort,
    participants: tuple[object, ...] = (),
) -> WriteAdmissionEvidence:
    """Freshly decide and publish one object in one coordinated interval."""

    require_configured_gate_controller(controller)
    if type(library) is not BehaviorLibrary:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "library must be exact")
    require_snapshot_fence(fence)
    if library.mutation_fence is not fence or getattr(journal, "mutation_fence", None) is not fence:
        raise _fail(
            WriteAdmissionFailureCode.COORDINATOR_MISMATCH,
            "library, decision journal and publication path must share the exact fence",
        )
    # Reject malformed objects and a foreign publisher before any authority
    # record is appended. The library repeats this inside its transaction; this
    # preflight controls whether the coordinated write may begin at all.
    library._validate_write_inputs(unit, blob, manifest, publisher_identity)
    subject = write_subject_ref(content_key=unit.content_key, manifest_id=manifest.manifest_id)
    subjects = (subject,)
    gate_failure: Exception | None = None
    publication_failure: LibraryViolation | None = None
    ingestion: GateDecision | None = None
    publication: GateDecision | None = None
    receipts: tuple[DecisionCommitReceipt, ...] = ()
    admission: LibraryWriteAdmission | None = None
    result: PutResult | None = None

    with fence.exclusive() as coordinator_guard:
        coordinator_id = fence.coordinator_id()
        require_live_guard(coordinator_guard, coordinator_id=coordinator_id)
        require_same_coordinator(
            fence,
            participants=(library, journal) + tuple(participants),
        )
        entry_epoch = fence.current_epoch()
        if type(entry_epoch) is not int or entry_epoch < 0 or entry_epoch % 2:
            raise _fail(
                WriteAdmissionFailureCode.TYPE_MISMATCH,
                "publication did not begin from a settled authority state",
            )
        with store_transaction(fence, guard=coordinator_guard) as mutation_ticket:
            try:
                ingestion = evaluate_ingestion_gate(controller, subject_refs=subjects)
                _require_admit(
                    ingestion,
                    gate=GateKind.INGESTION,
                    code=WriteAdmissionFailureCode.INGESTION_REFUSED,
                )
                publication = evaluate_publication_gate(
                    controller,
                    subject_refs=subjects,
                    requested=requested,
                    predecessor=ingestion,
                )
                _require_admit(
                    publication,
                    gate=GateKind.PUBLICATION,
                    code=WriteAdmissionFailureCode.PUBLICATION_REFUSED,
                )
                for decision in (ingestion, publication):
                    require_dimension_evidence(decision)
                    held = (entitlements or {}).get(decision.gate_kind)
                    if held is None:
                        raise _fail(
                            WriteAdmissionFailureCode.TYPE_MISMATCH,
                            f"no verifier entitlement was supplied for the {decision.gate_kind.value} gate",
                        )
                    declaration, source_actors = held
                    require_entitled_decision(
                        decision,
                        declaration=declaration,
                        proof=derive_independence_proof(declaration, source_actors),
                        source_actors=source_actors,
                    )
            except Exception as exc:
                # No durable primitive has run. Close the empty interval normally,
                # then report the refusal after the coordinator is settled.
                gate_failure = exc

            if gate_failure is None:
                assert ingestion is not None and publication is not None
                receipts = tuple(
                    commit_gate_decision(
                        decision,
                        journal=journal,
                        trusted_clock=controller._trusted_clock,
                        ticket=mutation_ticket,
                    )
                    for decision in (ingestion, publication)
                )
                now = controller._trusted_clock()
                if type(now) is not datetime:
                    raise _fail(
                        WriteAdmissionFailureCode.TYPE_MISMATCH,
                        "trusted clock did not return a datetime",
                    )
                admission = _mint_library_write_admission(
                    subject_ref_sha256=subject.sha256,
                    blob_digest_sha256=unit.content_key.digest_sha256,
                    manifest_digest_sha256=manifest.manifest_id.digest_sha256,
                    policy_version=controller.policy_version,
                    ingestion_decision_id_sha256=ingestion.gate_decision_id.digest_sha256,
                    publication_decision_id_sha256=publication.gate_decision_id.digest_sha256,
                    ingestion_decision_digest=receipts[0].decision_digest,
                    publication_decision_digest=receipts[1].decision_digest,
                    witnessed_journal_anchor=receipts[1].journal_anchor,
                    admitted_at_utc=now,
                    coordinator_guard=coordinator_guard,
                    mutation_ticket=mutation_ticket,
                )
                library._activate_write_admission(
                    admission,
                    unit=unit,
                    blob=blob,
                    manifest=manifest,
                    publisher_identity=publisher_identity,
                    ingestion_decision=ingestion,
                    publication_decision=publication,
                    ingestion_receipt=receipts[0],
                    publication_receipt=receipts[1],
                    coordinator_guard=coordinator_guard,
                    mutation_ticket=mutation_ticket,
                )
                try:
                    try:
                        result = library.put_behavior(
                            unit,
                            blob,
                            manifest,
                            publisher_identity=publisher_identity,
                            admission=admission,
                            coordinator_guard=coordinator_guard,
                            mutation_ticket=mutation_ticket,
                        )
                    except LibraryViolation as exc:
                        # The library closes its own typed-refusal transaction
                        # normally after completing any quarantine/corruption
                        # record it chose to write. Let the shared interval close
                        # normally too, then preserve that exact refusal.
                        publication_failure = exc
                finally:
                    library._close_write_admission(admission)

        final_epoch = settle_exclusive_mutation(
            fence=fence,
            coordinator_id=coordinator_id,
            entry_epoch=entry_epoch,
            own_intervals=1,
        )

    if gate_failure is not None:
        raise gate_failure
    if publication_failure is not None:
        raise publication_failure
    assert ingestion is not None and publication is not None
    assert admission is not None and result is not None
    evidence = object.__new__(WriteAdmissionEvidence)
    object.__setattr__(evidence, "admission", admission)
    object.__setattr__(evidence, "ingestion", ingestion)
    object.__setattr__(evidence, "publication", publication)
    object.__setattr__(evidence, "receipts", receipts)
    object.__setattr__(evidence, "result", result)
    object.__setattr__(evidence, "entry_epoch", entry_epoch)
    object.__setattr__(evidence, "final_epoch", final_epoch)
    object.__setattr__(evidence, "_trusted_seal", _EVIDENCE_SEAL)
    return validate_write_admission_evidence(evidence)


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "WriteAdmissionEvidence",
    "WriteAdmissionFailureCode",
    "WriteAdmissionViolation",
    "admit_library_write",
    "validate_write_admission_evidence",
    "write_subject_ref",
]
