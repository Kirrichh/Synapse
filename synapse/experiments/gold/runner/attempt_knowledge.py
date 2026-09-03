"""Own the durable knowledge and verified finding semantics for run continuation.

A completed attempt may justify another attempt only through newly admitted
knowledge, a semantically changed point-of-use revalidation, or a new verified
finding. Attempt-local provenance is retained by its domain owners but excluded
from continuation identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib

from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.compatibility import (
    CompatibilityRevalidationRecord,
    RevalidationStage,
    validate_compatibility_revalidation_record,
)

from .models import canonical_run_bytes
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V2 = "synapse.stage4.gold.attempt-knowledge-basis/v2"
KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V3 = "synapse.stage4.gold.knowledge-continuation-evidence/v3"
PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V2 = "synapse.stage4.gold.prior-attempt-evidence/v2"
_STAGE3_REVALIDATION_SEMANTIC_SCHEMA_V1 = "synapse.stage4.gold.stage3-revalidation-semantic/v1"

_BASIS_SEAL = object()
_EVIDENCE_SEAL = object()
_PRIOR_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _digest(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, f"{field_name} must be a sha256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, f"{field_name} must be a sha256 digest") from exc
    return value


def _optional_digest(value: object, field_name: str) -> str | None:
    return None if value is None else _digest(value, field_name)


def _ref_or_none(value: HashBoundRef | None) -> dict[str, object] | None:
    return None if value is None else value.to_dict()


def stage3_revalidation_semantic_sha256(value: CompatibilityRevalidationRecord) -> str:
    """Stable Stage 3 meaning, excluding timestamp and predecessor record identity."""

    validate_compatibility_revalidation_record(value)
    if value.stage is not RevalidationStage.BEFORE_CONSUMPTION:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "continuation basis requires a Stage 3 revalidation record",
        )
    payload = {
        "schema_version": _STAGE3_REVALIDATION_SEMANTIC_SCHEMA_V1,
        "context_id": value.context_id.to_dict(),
        "descriptor_id": value.descriptor_id.to_dict(),
        "original_decision_id": value.original_decision_id.to_dict(),
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "observation_sha256": value.observation_sha256,
        "outcome": value.outcome.value,
        "failure_code": None if value.failure_code is None else value.failure_code.value,
        "cause_code": None if value.cause_code is None else value.cause_code.value,
    }
    return hashlib.sha256(canonical_run_bytes(payload)).hexdigest()


def stage3_revalidation_semantic_set(
    records: tuple[CompatibilityRevalidationRecord, ...],
) -> tuple[str, ...]:
    if type(records) is not tuple:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "Stage 3 revalidation records must be a tuple")
    digests = tuple(sorted(stage3_revalidation_semantic_sha256(item) for item in records))
    if len(set(digests)) != len(digests):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "point-of-use admission contains duplicate Stage 3 semantics",
        )
    return digests


class ContinuationOutcome(str, Enum):
    CONTINUATION_BASIS = "CONTINUATION_BASIS"
    NO_CONTINUATION_BASIS = "NO_CONTINUATION_BASIS"


class ContinuationBasisKind(str, Enum):
    NEW_ADMITTED_KNOWLEDGE = "NEW_ADMITTED_KNOWLEDGE"
    NEW_REVALIDATED_KNOWLEDGE = "NEW_REVALIDATED_KNOWLEDGE"
    NEW_VERIFIED_ATTEMPT_EVIDENCE = "NEW_VERIFIED_ATTEMPT_EVIDENCE"
    NONE = "NONE"


@dataclass(frozen=True, init=False)
class AttemptKnowledgeBasis:
    """Knowledge an attempt was actually authorised to use at point of use."""

    schema_version: str
    run_id: str
    attempt_id: str
    attempt_index: int
    admitted_subject_refs: tuple[HashBoundRef, ...]
    retrieval_gate_decision_ref: HashBoundRef
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    point_of_use_admitted: bool
    revalidation_semantic_sha256s: tuple[str, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> "AttemptKnowledgeBasis":
        raise TypeError("AttemptKnowledgeBasis is factory-created")

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
            "point_of_use_admitted": self.point_of_use_admitted,
            "revalidation_semantic_sha256s": list(self.revalidation_semantic_sha256s),
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def admitted_set(self) -> frozenset[HashBoundRef]:
        return frozenset(self.admitted_subject_refs) if self.point_of_use_admitted else frozenset()


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
    point_of_use_admitted: bool = False,
    revalidation_semantic_sha256s: tuple[str, ...] = (),
) -> AttemptKnowledgeBasis:
    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    for name, value in (("run_id", run_id), ("attempt_id", attempt_id), ("policy_version", policy_version)):
        if type(value) is not str or not value:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")
    for name, ref in (
        ("retrieval_gate_decision_ref", retrieval_gate_decision_ref),
        ("consumer_context_ref", consumer_context_ref),
        ("boundary_ref", boundary_ref),
    ):
        if type(ref) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    if type(point_of_use_admitted) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "point_of_use_admitted must be exact bool")
    if type(revalidation_semantic_sha256s) is not tuple:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "revalidation semantics must be a tuple")
    semantics = tuple(_digest(item, "revalidation semantic digest") for item in revalidation_semantic_sha256s)
    if semantics != tuple(sorted(set(semantics))):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "revalidation semantics must be sorted and unique")
    if point_of_use_admitted != bool(semantics):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "successful point-of-use admission and Stage 3 semantics must be present together",
        )
    value = object.__new__(AttemptKnowledgeBasis)
    for name, item in (
        ("schema_version", ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V2),
        ("run_id", run_id),
        ("attempt_id", attempt_id),
        ("attempt_index", attempt_index),
        ("admitted_subject_refs", A.canonical_subject_refs(admitted_subject_refs)),
        ("retrieval_gate_decision_ref", retrieval_gate_decision_ref),
        ("consumer_context_ref", consumer_context_ref),
        ("boundary_ref", boundary_ref),
        ("policy_version", policy_version),
        ("point_of_use_admitted", point_of_use_admitted),
        ("revalidation_semantic_sha256s", semantics),
        ("_trusted_seal", _BASIS_SEAL),
    ):
        object.__setattr__(value, name, item)
    return value


def basis_from_payload(payload: dict[str, object]) -> AttemptKnowledgeBasis:
    fields = {
        "schema_version", "run_id", "attempt_id", "attempt_index",
        "admitted_subject_refs", "retrieval_gate_decision_ref",
        "consumer_context_ref", "boundary_ref", "policy_version",
        "point_of_use_admitted", "revalidation_semantic_sha256s",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored basis has an unknown shape")
    if payload["schema_version"] != ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored basis schema is unknown")
    try:
        admitted = payload["admitted_subject_refs"]
        semantics = payload["revalidation_semantic_sha256s"]
        if type(admitted) is not list or type(semantics) is not list:
            raise TypeError
        return create_attempt_knowledge_basis(
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            admitted_subject_refs=tuple(HashBoundRef.from_dict(item) for item in admitted),
            retrieval_gate_decision_ref=HashBoundRef.from_dict(payload["retrieval_gate_decision_ref"]),
            consumer_context_ref=HashBoundRef.from_dict(payload["consumer_context_ref"]),
            boundary_ref=HashBoundRef.from_dict(payload["boundary_ref"]),
            policy_version=payload["policy_version"],
            point_of_use_admitted=payload["point_of_use_admitted"],
            revalidation_semantic_sha256s=tuple(semantics),
        )
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored basis is malformed") from exc


@dataclass(frozen=True, init=False)
class PriorAttemptEvidence:
    """Exact provenance plus the stable semantic identity of a checked finding."""

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
    plan_semantic_sha256: str | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> "PriorAttemptEvidence":
        raise TypeError("PriorAttemptEvidence is factory-created")

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
            "plan_semantic_sha256": self.plan_semantic_sha256,
        }

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V2,
            "plan_semantic_sha256": self.plan_semantic_sha256,
            "c1_status": self.c1_status,
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.semantic_payload())).hexdigest()

    def provenance_digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def is_verified(self) -> bool:
        if (
            self.accepted_plan_ref is None
            or self.plan_semantic_sha256 is None
            or self.c1_result_ref is None
            or self.c1_status is None
        ):
            return False
        if self.oracle_invoked:
            return self.oracle_result_ref is not None and self.oracle_resolved is not None
        return self.oracle_result_ref is None and self.oracle_resolved is None


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
    plan_semantic_sha256: str | None,
) -> PriorAttemptEvidence:
    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    _digest(attempt_result_sha256, "attempt_result_sha256")
    _optional_digest(plan_semantic_sha256, "plan_semantic_sha256")
    if c1_status is not None and (type(c1_status) is not str or not c1_status or len(c1_status) > 64):
        raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "c1_status must be bounded or absent")
    if type(oracle_invoked) is not bool or (oracle_resolved is not None and type(oracle_resolved) is not bool):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle verdict fields are malformed")
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
        ("schema_version", PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V2),
        ("attempt_index", attempt_index),
        ("attempt_result_sha256", attempt_result_sha256),
        ("worker_result_ref", worker_result_ref),
        ("c1_result_ref", c1_result_ref),
        ("oracle_result_ref", oracle_result_ref),
        ("c1_status", c1_status),
        ("oracle_invoked", oracle_invoked),
        ("oracle_resolved", oracle_resolved),
        ("accepted_plan_ref", accepted_plan_ref),
        ("plan_semantic_sha256", plan_semantic_sha256),
        ("_trusted_seal", _PRIOR_SEAL),
    ):
        object.__setattr__(value, name, item)
    return value


def prior_attempt_evidence_from_result(
    result: object,
    *,
    attempt_index: int,
    accepted_plan_ref: HashBoundRef | None,
    plan_semantic_sha256: str | None,
) -> PriorAttemptEvidence:
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
        plan_semantic_sha256=plan_semantic_sha256,
    )


@dataclass(frozen=True, init=False)
class KnowledgeContinuationEvidence:
    """Durable, recomputable progress evidence for one completed attempt."""

    schema_version: str
    run_id: str
    attempt_index: int
    previous_basis_sha256: str | None
    current_basis_sha256: str
    previous_subject_refs: tuple[HashBoundRef, ...]
    current_subject_refs: tuple[HashBoundRef, ...]
    added_subject_refs: tuple[HashBoundRef, ...]
    accepted_plan_ref: HashBoundRef
    plan_semantic_sha256: str
    previous_finding_sha256: str | None
    current_finding_sha256: str | None
    basis_kinds: tuple[str, ...]
    outcome: ContinuationOutcome
    policy_version: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> "KnowledgeContinuationEvidence":
        raise TypeError("KnowledgeContinuationEvidence is factory-created")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_index": self.attempt_index,
            "previous_basis_sha256": self.previous_basis_sha256,
            "current_basis_sha256": self.current_basis_sha256,
            "previous_subject_refs": [item.to_dict() for item in self.previous_subject_refs],
            "current_subject_refs": [item.to_dict() for item in self.current_subject_refs],
            "added_subject_refs": [item.to_dict() for item in self.added_subject_refs],
            "accepted_plan_ref": self.accepted_plan_ref.to_dict(),
            "plan_semantic_sha256": self.plan_semantic_sha256,
            "previous_finding_sha256": self.previous_finding_sha256,
            "current_finding_sha256": self.current_finding_sha256,
            "basis_kinds": list(self.basis_kinds),
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()


def decide_completed_attempt_continuation(
    *,
    run_id: str,
    attempt_index: int,
    current_basis: AttemptKnowledgeBasis,
    current_basis_sha256: str,
    current_finding: PriorAttemptEvidence,
    previous_basis: AttemptKnowledgeBasis | None,
    previous_basis_sha256: str | None,
    previous_finding: PriorAttemptEvidence | None,
) -> KnowledgeContinuationEvidence:
    """Decide progress from already-durable attempts; never prepare the next one."""

    if type(run_id) is not str or not run_id or type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "continuation run identity is malformed")
    if type(current_basis) is not AttemptKnowledgeBasis or getattr(current_basis, "_trusted_seal", None) is not _BASIS_SEAL:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "current basis is not sealed")
    _digest(current_basis_sha256, "current_basis_sha256")
    if current_basis.digest() != current_basis_sha256 or current_basis.run_id != run_id or current_basis.attempt_index != attempt_index:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "current basis differs from the completed attempt")
    if type(current_finding) is not PriorAttemptEvidence or getattr(current_finding, "_trusted_seal", None) is not _PRIOR_SEAL:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "current finding is not sealed")
    if current_finding.attempt_index != attempt_index or current_finding.accepted_plan_ref is None or current_finding.plan_semantic_sha256 is None:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "current finding lacks exact plan authority")

    previous_subjects: tuple[HashBoundRef, ...] = ()
    previous_revalidations: tuple[str, ...] = ()
    previous_finding_sha256 = None
    if previous_basis is not None:
        if type(previous_basis) is not AttemptKnowledgeBasis or getattr(previous_basis, "_trusted_seal", None) is not _BASIS_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "previous basis is not sealed")
        _digest(previous_basis_sha256, "previous_basis_sha256")
        if previous_basis.digest() != previous_basis_sha256 or previous_basis.run_id != run_id:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "previous basis differs from durable history")
        if previous_basis.policy_version != current_basis.policy_version:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt bases use different policy versions")
        if previous_basis.point_of_use_admitted:
            previous_subjects = previous_basis.admitted_subject_refs
            previous_revalidations = previous_basis.revalidation_semantic_sha256s
    elif previous_basis_sha256 is not None:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "previous basis digest exists without a basis")

    if previous_finding is not None:
        if type(previous_finding) is not PriorAttemptEvidence or getattr(previous_finding, "_trusted_seal", None) is not _PRIOR_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "previous finding is not sealed")
        previous_finding_sha256 = previous_finding.digest() if previous_finding.is_verified() else None

    current_subjects = current_basis.admitted_subject_refs if current_basis.point_of_use_admitted else ()
    previous_set = frozenset(previous_subjects)
    added = () if previous_basis is None else tuple(item for item in current_subjects if item not in previous_set)
    kinds: list[str] = []
    if added:
        kinds.append(ContinuationBasisKind.NEW_ADMITTED_KNOWLEDGE.value)
    if (
        previous_basis is not None
        and current_basis.point_of_use_admitted
        and current_basis.revalidation_semantic_sha256s != previous_revalidations
    ):
        kinds.append(ContinuationBasisKind.NEW_REVALIDATED_KNOWLEDGE.value)
    current_finding_sha256 = current_finding.digest() if current_finding.is_verified() else None
    if current_finding_sha256 is not None and current_finding_sha256 != previous_finding_sha256:
        kinds.append(ContinuationBasisKind.NEW_VERIFIED_ATTEMPT_EVIDENCE.value)
    outcome = ContinuationOutcome.CONTINUATION_BASIS if kinds else ContinuationOutcome.NO_CONTINUATION_BASIS

    value = object.__new__(KnowledgeContinuationEvidence)
    for name, item in (
        ("schema_version", KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V3),
        ("run_id", run_id),
        ("attempt_index", attempt_index),
        ("previous_basis_sha256", previous_basis_sha256),
        ("current_basis_sha256", current_basis_sha256),
        ("previous_subject_refs", previous_subjects),
        ("current_subject_refs", current_subjects),
        ("added_subject_refs", added),
        ("accepted_plan_ref", current_finding.accepted_plan_ref),
        ("plan_semantic_sha256", current_finding.plan_semantic_sha256),
        ("previous_finding_sha256", previous_finding_sha256),
        ("current_finding_sha256", current_finding_sha256),
        ("basis_kinds", tuple(kinds) or (ContinuationBasisKind.NONE.value,)),
        ("outcome", outcome),
        ("policy_version", current_basis.policy_version),
        ("_trusted_seal", _EVIDENCE_SEAL),
    ):
        object.__setattr__(value, name, item)
    return value


def continuation_evidence_from_payload(payload: dict[str, object]) -> KnowledgeContinuationEvidence:
    fields = {
        "schema_version", "run_id", "attempt_index", "previous_basis_sha256",
        "current_basis_sha256", "previous_subject_refs", "current_subject_refs",
        "added_subject_refs", "accepted_plan_ref", "plan_semantic_sha256",
        "previous_finding_sha256", "current_finding_sha256", "basis_kinds",
        "outcome", "policy_version",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "continuation evidence has an unknown shape")
    if payload["schema_version"] != KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V3:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "continuation evidence schema is unknown")
    try:
        for name in ("previous_subject_refs", "current_subject_refs", "added_subject_refs", "basis_kinds"):
            if type(payload[name]) is not list:
                raise TypeError
        outcome = ContinuationOutcome(payload["outcome"])
        previous_sha = _optional_digest(payload["previous_basis_sha256"], "previous_basis_sha256")
        current_sha = _digest(payload["current_basis_sha256"], "current_basis_sha256")
        plan_sha = _digest(payload["plan_semantic_sha256"], "plan_semantic_sha256")
        previous_finding_sha = _optional_digest(payload["previous_finding_sha256"], "previous_finding_sha256")
        current_finding_sha = _optional_digest(payload["current_finding_sha256"], "current_finding_sha256")
        basis_kinds = tuple(payload["basis_kinds"])
        allowed_kinds = {item.value for item in ContinuationBasisKind}
        if not basis_kinds or any(type(item) is not str or item not in allowed_kinds for item in basis_kinds):
            raise TypeError
        value = object.__new__(KnowledgeContinuationEvidence)
        for name, item in (
            ("schema_version", KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V3),
            ("run_id", payload["run_id"]),
            ("attempt_index", payload["attempt_index"]),
            ("previous_basis_sha256", previous_sha),
            ("current_basis_sha256", current_sha),
            ("previous_subject_refs", tuple(HashBoundRef.from_dict(x) for x in payload["previous_subject_refs"])),
            ("current_subject_refs", tuple(HashBoundRef.from_dict(x) for x in payload["current_subject_refs"])),
            ("added_subject_refs", tuple(HashBoundRef.from_dict(x) for x in payload["added_subject_refs"])),
            ("accepted_plan_ref", HashBoundRef.from_dict(payload["accepted_plan_ref"])),
            ("plan_semantic_sha256", plan_sha),
            ("previous_finding_sha256", previous_finding_sha),
            ("current_finding_sha256", current_finding_sha),
            ("basis_kinds", basis_kinds),
            ("outcome", outcome),
            ("policy_version", payload["policy_version"]),
            ("_trusted_seal", _EVIDENCE_SEAL),
        ):
            object.__setattr__(value, name, item)
        if type(value.run_id) is not str or not value.run_id or type(value.attempt_index) is not int or value.attempt_index < 1:
            raise TypeError
        return value
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "continuation evidence is malformed") from exc


__all__ = [
    "ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V2",
    "KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V3",
    "AttemptKnowledgeBasis",
    "ContinuationOutcome",
    "KnowledgeContinuationEvidence",
    "basis_from_payload",
    "continuation_evidence_from_payload",
    "create_attempt_knowledge_basis",
    "decide_completed_attempt_continuation",
    "prior_attempt_evidence_from_result",
    "stage3_revalidation_semantic_set",
    "stage3_revalidation_semantic_sha256",
]
