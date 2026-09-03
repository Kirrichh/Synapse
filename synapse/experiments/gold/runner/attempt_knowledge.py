"""Define the durable knowledge and verified findings that may continue a run.

This owner answers one question: whether another attempt has a continuation
basis that the preceding attempt did not. Two independent sources may provide
that basis: newly admitted knowledge, or a verified run-local finding that
changes what the next plan knows.

Admission identity and finding identity deliberately differ. Admission records
carry attempt-local provenance and therefore change on every attempt; a verified
finding must compare equal when the same plan reaches the same structured C1 and
oracle verdict under fresh record ids. Provenance is retained for audit, while
semantic identity alone decides whether progress is new.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Protocol, runtime_checkable

from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef

from .models import canonical_run_bytes
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1 = "synapse.stage4.gold.attempt-knowledge-basis/v1"
KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.knowledge-continuation-evidence/v1"
)
PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V1 = "synapse.stage4.gold.prior-attempt-evidence/v1"

_BASIS_SEAL = object()
_EVIDENCE_SEAL = object()
_PRIOR_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _digest(value: str, field_name: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, f"{field_name} must be a sha256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _fail(
            GoldRunFailureCode.MALFORMED_IDENTITY,
            f"{field_name} must be a sha256 hex digest",
        ) from exc
    return value


class ContinuationOutcome(str, Enum):
    """Closed vocabulary for what a continuation comparison concluded."""

    CONTINUATION_BASIS = "CONTINUATION_BASIS"
    NO_CONTINUATION_BASIS = "NO_CONTINUATION_BASIS"


@dataclass(frozen=True, init=False)
class AttemptKnowledgeBasis:
    """The subjects one attempt was admitted to consume, made durable."""

    schema_version: str
    run_id: str
    attempt_id: str
    attempt_index: int
    admitted_subject_refs: tuple[HashBoundRef, ...]
    retrieval_gate_decision_ref: HashBoundRef
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptKnowledgeBasis:
        raise TypeError("AttemptKnowledgeBasis is produced only by create_attempt_knowledge_basis")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "admitted_subject_refs": [item.to_dict() for item in self.admitted_subject_refs],
            "retrieval_gate_decision_ref": self.retrieval_gate_decision_ref.to_dict(),
            "consumer_context_ref": self.consumer_context_ref.to_dict(),
            "boundary_ref": self.boundary_ref.to_dict(),
            "policy_version": self.policy_version,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def admitted_set(self) -> frozenset[HashBoundRef]:
        return frozenset(self.admitted_subject_refs)


class ContinuationBasisKind(str, Enum):
    """What authorised another attempt to run."""

    NEW_ADMITTED_KNOWLEDGE = "NEW_ADMITTED_KNOWLEDGE"
    NEW_VERIFIED_ATTEMPT_EVIDENCE = "NEW_VERIFIED_ATTEMPT_EVIDENCE"
    NONE = "NONE"


@dataclass(frozen=True, init=False)
class PriorAttemptEvidence:
    """Provenance plus semantic identity of one verified attempt finding.

    Provenance fields retain the exact records that produced the finding. They
    are intentionally excluded from ``semantic_payload`` because record ids and
    attempt ids change when the same hypothesis is checked again. The semantic
    payload instead binds the accepted plan to the structured C1/oracle verdict.
    """

    schema_version: str
    attempt_index: int
    attempt_result_sha256: str
    worker_result_ref: HashBoundRef | None
    c1_result_ref: HashBoundRef | None
    oracle_result_ref: HashBoundRef | None
    c1_status: str | None
    oracle_invoked: bool
    oracle_resolved: bool | None
    accepted_plan_ref: HashBoundRef | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PriorAttemptEvidence:
        raise TypeError("PriorAttemptEvidence is produced only by create_prior_attempt_evidence")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_index": self.attempt_index,
            "attempt_result_sha256": self.attempt_result_sha256,
            "worker_result_ref": _ref_or_none(self.worker_result_ref),
            "c1_result_ref": _ref_or_none(self.c1_result_ref),
            "oracle_result_ref": _ref_or_none(self.oracle_result_ref),
            "c1_status": self.c1_status,
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
            "accepted_plan_ref": _ref_or_none(self.accepted_plan_ref),
        }

    def semantic_payload(self) -> dict[str, object]:
        """Stable meaning of the verified finding, excluding attempt provenance."""

        return {
            "schema_version": PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V1,
            "accepted_plan_ref": _ref_or_none(self.accepted_plan_ref),
            "c1_status": self.c1_status,
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
        }

    def digest(self) -> str:
        """Semantic finding identity used only for continuation comparison."""

        return hashlib.sha256(canonical_run_bytes(self.semantic_payload())).hexdigest()

    def provenance_digest(self) -> str:
        """Audit identity over the exact records that produced the finding."""

        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def is_verified(self) -> bool:
        """Whether the structured finding is sufficient continuation authority."""

        if self.accepted_plan_ref is None or self.c1_result_ref is None or self.c1_status is None:
            return False
        if self.oracle_invoked:
            return self.oracle_result_ref is not None and self.oracle_resolved is not None
        return self.oracle_result_ref is None and self.oracle_resolved is None


def _ref_or_none(value: HashBoundRef | None) -> dict[str, object] | None:
    return None if value is None else value.to_dict()


def create_prior_attempt_evidence(
    *,
    attempt_index: int,
    attempt_result_sha256: str,
    worker_result_ref: HashBoundRef | None,
    c1_result_ref: HashBoundRef | None,
    oracle_result_ref: HashBoundRef | None,
    c1_status: str | None,
    oracle_invoked: bool,
    oracle_resolved: bool | None,
    accepted_plan_ref: HashBoundRef | None,
) -> PriorAttemptEvidence:
    """Record provenance and semantic verdict for one finished attempt."""

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    _digest(attempt_result_sha256, "attempt_result_sha256")
    if c1_status is not None and (type(c1_status) is not str or not c1_status or len(c1_status) > 64):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "c1_status must be a bounded string or absent")
    if type(oracle_invoked) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_invoked must be an exact bool")
    if oracle_resolved is not None and type(oracle_resolved) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_resolved must be a bool or absent")
    for name, ref in (
        ("worker_result_ref", worker_result_ref),
        ("c1_result_ref", c1_result_ref),
        ("oracle_result_ref", oracle_result_ref),
        ("accepted_plan_ref", accepted_plan_ref),
    ):
        if ref is not None and type(ref) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact ref or absent")

    value = object.__new__(PriorAttemptEvidence)
    for name, item in (
        ("schema_version", PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V1),
        ("attempt_index", attempt_index),
        ("attempt_result_sha256", attempt_result_sha256),
        ("worker_result_ref", worker_result_ref),
        ("c1_result_ref", c1_result_ref),
        ("oracle_result_ref", oracle_result_ref),
        ("c1_status", c1_status),
        ("oracle_invoked", oracle_invoked),
        ("oracle_resolved", oracle_resolved),
        ("accepted_plan_ref", accepted_plan_ref),
        ("_trusted_seal", _PRIOR_SEAL),
    ):
        object.__setattr__(value, name, item)
    return value


def create_attempt_knowledge_basis(
    *,
    run_id: str,
    attempt_id: str,
    attempt_index: int,
    admitted_subject_refs: tuple[HashBoundRef, ...],
    retrieval_gate_decision_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
) -> AttemptKnowledgeBasis:
    """Record what this attempt was admitted to consume."""

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    for name, value in (
        ("run_id", run_id),
        ("attempt_id", attempt_id),
        ("policy_version", policy_version),
    ):
        if type(value) is not str or not value:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")
    for name, ref in (
        ("retrieval_gate_decision_ref", retrieval_gate_decision_ref),
        ("consumer_context_ref", consumer_context_ref),
        ("boundary_ref", boundary_ref),
    ):
        if type(ref) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    ordered = A.canonical_subject_refs(admitted_subject_refs)

    value = object.__new__(AttemptKnowledgeBasis)
    object.__setattr__(value, "schema_version", ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1)
    object.__setattr__(value, "run_id", run_id)
    object.__setattr__(value, "attempt_id", attempt_id)
    object.__setattr__(value, "attempt_index", attempt_index)
    object.__setattr__(value, "admitted_subject_refs", ordered)
    object.__setattr__(value, "retrieval_gate_decision_ref", retrieval_gate_decision_ref)
    object.__setattr__(value, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(value, "boundary_ref", boundary_ref)
    object.__setattr__(value, "policy_version", policy_version)
    object.__setattr__(value, "_trusted_seal", _BASIS_SEAL)
    return value


def basis_from_payload(payload: dict[str, object]) -> AttemptKnowledgeBasis:
    """Rebuild one basis from an exact durable schema."""

    fields = {
        "schema_version",
        "run_id",
        "attempt_id",
        "attempt_index",
        "admitted_subject_refs",
        "retrieval_gate_decision_ref",
        "consumer_context_ref",
        "boundary_ref",
        "policy_version",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "stored basis has an unknown shape")
    if payload["schema_version"] != ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "stored basis names an unknown schema")
    try:
        admitted = payload["admitted_subject_refs"]
        if type(admitted) is not list:
            raise TypeError("admitted_subject_refs must be a list")
        return create_attempt_knowledge_basis(
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            admitted_subject_refs=tuple(HashBoundRef.from_dict(item) for item in admitted),
            retrieval_gate_decision_ref=HashBoundRef.from_dict(payload["retrieval_gate_decision_ref"]),
            consumer_context_ref=HashBoundRef.from_dict(payload["consumer_context_ref"]),
            boundary_ref=HashBoundRef.from_dict(payload["boundary_ref"]),
            policy_version=payload["policy_version"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "stored basis is not readable") from exc


@dataclass(frozen=True, init=False)
class KnowledgeContinuationEvidence:
    """Recomputable reason why a run may continue or must stop."""

    schema_version: str
    previous_basis_sha256: str
    next_basis_sha256: str
    previous_subject_refs: tuple[HashBoundRef, ...]
    next_subject_refs: tuple[HashBoundRef, ...]
    added_subject_refs: tuple[HashBoundRef, ...]
    outcome: ContinuationOutcome
    basis_kinds: tuple[str, ...]
    prior_evidence_sha256: str | None
    policy_version: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> KnowledgeContinuationEvidence:
        raise TypeError("KnowledgeContinuationEvidence is produced only by decide_continuation")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "previous_basis_sha256": self.previous_basis_sha256,
            "next_basis_sha256": self.next_basis_sha256,
            "previous_subject_refs": [item.to_dict() for item in self.previous_subject_refs],
            "next_subject_refs": [item.to_dict() for item in self.next_subject_refs],
            "added_subject_refs": [item.to_dict() for item in self.added_subject_refs],
            "outcome": self.outcome.value,
            "basis_kinds": list(self.basis_kinds),
            "prior_evidence_sha256": self.prior_evidence_sha256,
            "policy_version": self.policy_version,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()


def decide_continuation(
    *,
    previous: AttemptKnowledgeBasis,
    previous_basis_sha256: str,
    nxt: AttemptKnowledgeBasis,
    next_basis_sha256: str,
    prior_evidence: PriorAttemptEvidence | None = None,
    previous_prior_evidence_sha256: str | None = None,
) -> KnowledgeContinuationEvidence:
    """Compare admitted knowledge and verified semantic findings."""

    for name, value in (("previous", previous), ("next", nxt)):
        if type(value) is not AttemptKnowledgeBasis or getattr(value, "_trusted_seal", None) is not _BASIS_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} basis is not sealed")
    _digest(previous_basis_sha256, "previous_basis_sha256")
    _digest(next_basis_sha256, "next_basis_sha256")
    if previous_prior_evidence_sha256 is not None:
        _digest(previous_prior_evidence_sha256, "previous_prior_evidence_sha256")
    if previous.policy_version != nxt.policy_version:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "the two attempts were admitted under different policy versions",
        )

    previously_admitted = previous.admitted_set()
    added = tuple(item for item in nxt.admitted_subject_refs if item not in previously_admitted)
    kinds: list[str] = []
    if added:
        kinds.append(ContinuationBasisKind.NEW_ADMITTED_KNOWLEDGE.value)

    prior_digest = None
    if prior_evidence is not None:
        if type(prior_evidence) is not PriorAttemptEvidence or getattr(prior_evidence, "_trusted_seal", None) is not _PRIOR_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "prior attempt evidence is not sealed")
        prior_digest = prior_evidence.digest()
        if prior_evidence.is_verified() and prior_digest != previous_prior_evidence_sha256:
            kinds.append(ContinuationBasisKind.NEW_VERIFIED_ATTEMPT_EVIDENCE.value)

    outcome = (
        ContinuationOutcome.CONTINUATION_BASIS
        if kinds
        else ContinuationOutcome.NO_CONTINUATION_BASIS
    )
    value = object.__new__(KnowledgeContinuationEvidence)
    object.__setattr__(value, "schema_version", KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1)
    object.__setattr__(value, "previous_basis_sha256", previous_basis_sha256)
    object.__setattr__(value, "next_basis_sha256", next_basis_sha256)
    object.__setattr__(value, "previous_subject_refs", previous.admitted_subject_refs)
    object.__setattr__(value, "next_subject_refs", nxt.admitted_subject_refs)
    object.__setattr__(value, "added_subject_refs", added)
    object.__setattr__(value, "outcome", outcome)
    object.__setattr__(value, "basis_kinds", tuple(kinds) or (ContinuationBasisKind.NONE.value,))
    object.__setattr__(value, "prior_evidence_sha256", prior_digest)
    object.__setattr__(value, "policy_version", nxt.policy_version)
    object.__setattr__(value, "_trusted_seal", _EVIDENCE_SEAL)
    return value


def prior_attempt_evidence_from_result(
    result: object,
    *,
    attempt_index: int,
    accepted_plan_ref: HashBoundRef | None = None,
) -> PriorAttemptEvidence:
    """Read one finished attempt into provenance plus semantic finding evidence."""

    return create_prior_attempt_evidence(
        attempt_index=attempt_index,
        attempt_result_sha256=result.result_sha256,
        worker_result_ref=result.worker_result_ref,
        c1_result_ref=result.c1_result_ref,
        oracle_result_ref=result.oracle_result_ref,
        c1_status=result.c1_status,
        oracle_invoked=result.oracle_invoked,
        oracle_resolved=result.oracle_resolved,
        accepted_plan_ref=accepted_plan_ref,
    )


@dataclass(frozen=True)
class PreviousAttemptBinding:
    """Durable context and verified finding available to the next attempt."""

    context: object
    basis_sha256: str | None
    prior_evidence: PriorAttemptEvidence | None
    prior_evidence_sha256: str | None


@runtime_checkable
class AttemptKnowledgeBasisPort(Protocol):
    """Persistence port for one attempt's admitted knowledge basis."""

    def put_basis(self, basis: AttemptKnowledgeBasis, *, ticket: object) -> str: ...

    def get_basis(self, *, attempt_index: int) -> tuple[AttemptKnowledgeBasis, str] | None: ...


__all__ = [
    "ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1",
    "KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1",
    "AttemptKnowledgeBasis",
    "AttemptKnowledgeBasisPort",
    "ContinuationOutcome",
    "KnowledgeContinuationEvidence",
    "basis_from_payload",
    "create_attempt_knowledge_basis",
    "decide_continuation",
]
