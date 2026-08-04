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

**What this does not yet prove.** Both verdicts are committed before the
capability exists, so a capability implies committed decisions. It does not
imply they are *still* committed at the moment of the write: a rollback between
mint and write would go unnoticed, because the library holds no journal and
cannot re-verify a receipt. `admit_for_consumption` closes the same window on
the consumption side by re-checking receipts at the point of use, and closing it
here means giving the library owner a journal port. That is a larger change than
this one and is not claimed to have been made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

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
)
from .canonicalization import ContentKey, HashBoundRef, library_subject_ref
from .contracts import (
    GateDecisionKind,
    GateKind,
    LibraryWriteAdmission,
    RecordId,
    _mint_library_write_admission,
)

#: Names taken from another owner's private surface. Enumerated so the seam is a
#: recorded decision rather than an accident: the minting factory is private
#: precisely so that only this module can reach it, and the tripwire that
#: enforces "only this module" reads the declaration below.
ADAPTER_PRIVATE_SEAM = ("_mint_library_write_admission",)


class WriteAdmissionFailureCode(str, Enum):
    """Why a library write was not admitted."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    INGESTION_REFUSED = "INGESTION_REFUSED"
    PUBLICATION_REFUSED = "PUBLICATION_REFUSED"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    DECISION_NOT_DURABLE = "DECISION_NOT_DURABLE"


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
    content_key: ContentKey,
    manifest_id: RecordId,
    requested: RequestedEnvelope,
    journal: DecisionJournalPort,
    trusted_clock: Callable[[], datetime],
) -> WriteAdmissionEvidence:
    """Run the first two §22 gates over one object and mint the write capability.

    The order is the §22 order and is not incidental: publication is evaluated
    with ingestion as its declared predecessor, so a publication verdict cannot
    exist without the ingestion verdict it followed. Both are committed before
    the capability is minted, because a permission that is not durable is a
    permission that a restart can silently forget.
    """

    require_configured_gate_controller(controller)
    if not callable(trusted_clock):
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    subject = write_subject_ref(content_key=content_key, manifest_id=manifest_id)
    subjects = (subject,)

    ingestion = evaluate_ingestion_gate(controller, subject_refs=subjects)
    _require_admit(ingestion, gate=GateKind.INGESTION, code=WriteAdmissionFailureCode.INGESTION_REFUSED)
    publication = evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    _require_admit(publication, gate=GateKind.PUBLICATION, code=WriteAdmissionFailureCode.PUBLICATION_REFUSED)
    for decision in (ingestion, publication):
        require_dimension_evidence(decision)

    receipts = tuple(
        commit_gate_decision(decision, journal=journal, trusted_clock=trusted_clock)
        for decision in (ingestion, publication)
    )
    now = trusted_clock()
    if type(now) is not datetime:
        raise _fail(WriteAdmissionFailureCode.TYPE_MISMATCH, "trusted clock did not return a datetime")

    admission = _mint_library_write_admission(
        subject_ref_sha256=subject.sha256,
        blob_digest_sha256=content_key.digest_sha256,
        manifest_digest_sha256=manifest_id.digest_sha256,
        policy_version=controller.policy_version,
        ingestion_decision_id_sha256=ingestion.gate_decision_id.digest_sha256,
        publication_decision_id_sha256=publication.gate_decision_id.digest_sha256,
        admitted_at_utc=now,
    )

    evidence = object.__new__(WriteAdmissionEvidence)
    object.__setattr__(evidence, "admission", admission)
    object.__setattr__(evidence, "ingestion", ingestion)
    object.__setattr__(evidence, "publication", publication)
    object.__setattr__(evidence, "receipts", receipts)
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
