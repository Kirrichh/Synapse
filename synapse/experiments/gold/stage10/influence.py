"""Separate worker acknowledgment from independently proven context influence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from ..canonicalization import HashBoundRef, RefKind
from ..contracts import ActorIdentity
from .context_codec import encode_canonical
from .delivery_verification import DeliveryReceipt, validate_delivery_receipt


INFLUENCE_ASSESSMENT_SCHEMA_V1 = "synapse.stage4.gold.stage10.influence-assessment/v1"
_INFLUENCE_ASSESSMENT_SEAL = object()
_CONTEXT_ID = re.compile(r"ctx_[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ConsumptionStage(str, Enum):
    DELIVERED = "DELIVERED"
    PARSED_CLAIMED = "PARSED_CLAIMED"
    REFERENCED_CLAIMED = "REFERENCED_CLAIMED"
    INFLUENCED_PROVEN = "INFLUENCED_PROVEN"


class AcknowledgementKind(str, Enum):
    PARSED = "PARSED"
    REFERENCED = "REFERENCED"


class InfluenceFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    ACKNOWLEDGEMENT_MISMATCH = "ACKNOWLEDGEMENT_MISMATCH"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"
    EVIDENCE_NOT_INDEPENDENT = "EVIDENCE_NOT_INDEPENDENT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class InfluenceViolation(ValueError):
    def __init__(self, failure_code: InfluenceFailureCode, detail: str) -> None:
        if type(failure_code) is not InfluenceFailureCode:
            raise TypeError("failure_code must be exact")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: InfluenceFailureCode, detail: str) -> InfluenceViolation:
    return InfluenceViolation(code, detail)


@dataclass(frozen=True)
class WorkerConsumptionAcknowledgement:
    """Worker self-report; it never proves influence."""

    worker_actor: ActorIdentity
    context_id: str
    delivery_receipt_sha256: str
    kind: AcknowledgementKind
    referenced_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_worker_consumption_acknowledgement(self)


def validate_worker_consumption_acknowledgement(
    value: WorkerConsumptionAcknowledgement,
) -> None:
    if type(value) is not WorkerConsumptionAcknowledgement:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "acknowledgement must be exact")
    if type(value.worker_actor) is not ActorIdentity or type(value.kind) is not AcknowledgementKind:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "acknowledgement fields must be exact")
    if type(value.context_id) is not str or _CONTEXT_ID.fullmatch(value.context_id) is None:
        raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "acknowledgement context id is malformed")
    if type(value.delivery_receipt_sha256) is not str or _SHA256.fullmatch(value.delivery_receipt_sha256) is None:
        raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "acknowledgement receipt id is malformed")
    if type(value.referenced_item_ids) is not tuple or any(
        type(item) is not str or _ITEM_ID.fullmatch(item) is None
        for item in value.referenced_item_ids
    ):
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "referenced item ids must be exact")
    if value.referenced_item_ids != tuple(sorted(set(value.referenced_item_ids))):
        raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "referenced item ids must be sorted and unique")
    if value.kind is AcknowledgementKind.PARSED and value.referenced_item_ids:
        raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "parsed acknowledgement cannot claim references")
    if value.kind is AcknowledgementKind.REFERENCED and not value.referenced_item_ids:
        raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "referenced acknowledgement must identify an item")


@dataclass(frozen=True)
class PlatformInfluenceEvidence:
    context_id: str
    delivery_receipt_sha256: str
    worker_actor: ActorIdentity
    observer_actor: ActorIdentity
    output_artifact_ref: HashBoundRef
    evidence_ref: HashBoundRef

    def __post_init__(self) -> None:
        validate_platform_influence_evidence(self)


def validate_platform_influence_evidence(value: PlatformInfluenceEvidence) -> None:
    if type(value) is not PlatformInfluenceEvidence:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "platform evidence must be exact")
    if type(value.context_id) is not str or _CONTEXT_ID.fullmatch(value.context_id) is None:
        raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "platform evidence context id is malformed")
    if type(value.delivery_receipt_sha256) is not str or _SHA256.fullmatch(value.delivery_receipt_sha256) is None:
        raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "platform evidence receipt id is malformed")
    if type(value.worker_actor) is not ActorIdentity or type(value.observer_actor) is not ActorIdentity:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence actors must be exact")
    if value.worker_actor == value.observer_actor:
        raise _fail(InfluenceFailureCode.EVIDENCE_NOT_INDEPENDENT, "worker cannot attest its own influence")
    if type(value.output_artifact_ref) is not HashBoundRef or value.output_artifact_ref.kind is not RefKind.ARTIFACT:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "output artifact ref must be exact")
    if type(value.evidence_ref) is not HashBoundRef or value.evidence_ref.kind is not RefKind.SOURCE_EVIDENCE:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "platform evidence ref must be source evidence")


@dataclass(frozen=True, init=False)
class InfluenceAssessment:
    schema_version: str
    assessment_sha256: str
    context_id: str
    delivery_receipt_sha256: str
    stage: ConsumptionStage
    acknowledgement: WorkerConsumptionAcknowledgement | None
    platform_evidence: PlatformInfluenceEvidence | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> InfluenceAssessment:
        raise TypeError("InfluenceAssessment is produced only by assessed evidence")

    def canonical_bytes(self) -> bytes:
        validate_influence_assessment(self)
        return encode_canonical(_assessment_payload(self))


def _ack_payload(value: WorkerConsumptionAcknowledgement) -> dict[str, object]:
    return {
        "worker_actor": value.worker_actor.to_dict(),
        "context_id": value.context_id,
        "delivery_receipt_sha256": value.delivery_receipt_sha256,
        "kind": value.kind.value,
        "referenced_item_ids": list(value.referenced_item_ids),
    }


def _platform_payload(value: PlatformInfluenceEvidence) -> dict[str, object]:
    return {
        "context_id": value.context_id,
        "delivery_receipt_sha256": value.delivery_receipt_sha256,
        "worker_actor": value.worker_actor.to_dict(),
        "observer_actor": value.observer_actor.to_dict(),
        "output_artifact_ref": value.output_artifact_ref.to_dict(),
        "evidence_ref": value.evidence_ref.to_dict(),
    }


def _assessment_payload(value: InfluenceAssessment) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "context_id": value.context_id,
        "delivery_receipt_sha256": value.delivery_receipt_sha256,
        "stage": value.stage.value,
        "acknowledgement": None if value.acknowledgement is None else _ack_payload(value.acknowledgement),
        "platform_evidence": None if value.platform_evidence is None else _platform_payload(value.platform_evidence),
    }


def assess_context_influence(
    *,
    receipt: DeliveryReceipt,
    acknowledgement: WorkerConsumptionAcknowledgement | None = None,
    platform_evidence: PlatformInfluenceEvidence | None = None,
) -> InfluenceAssessment:
    validate_delivery_receipt(receipt)
    if acknowledgement is not None:
        validate_worker_consumption_acknowledgement(acknowledgement)
        if acknowledgement.context_id != receipt.context_id or acknowledgement.delivery_receipt_sha256 != receipt.receipt_sha256:
            raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "acknowledgement belongs to another delivery")
    if platform_evidence is not None:
        if type(platform_evidence) is not PlatformInfluenceEvidence or acknowledgement is None:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "platform evidence requires an acknowledgement")
        validate_platform_influence_evidence(platform_evidence)
        if (
            platform_evidence.context_id != receipt.context_id
            or platform_evidence.delivery_receipt_sha256 != receipt.receipt_sha256
            or platform_evidence.worker_actor != acknowledgement.worker_actor
        ):
            raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "platform influence evidence belongs to another delivery")
        stage = ConsumptionStage.INFLUENCED_PROVEN
    elif acknowledgement is None:
        stage = ConsumptionStage.DELIVERED
    elif acknowledgement.kind is AcknowledgementKind.REFERENCED:
        stage = ConsumptionStage.REFERENCED_CLAIMED
    else:
        stage = ConsumptionStage.PARSED_CLAIMED
    fields = dict(
        schema_version=INFLUENCE_ASSESSMENT_SCHEMA_V1,
        context_id=receipt.context_id,
        delivery_receipt_sha256=receipt.receipt_sha256,
        stage=stage,
        acknowledgement=acknowledgement,
        platform_evidence=platform_evidence,
    )
    provisional = object.__new__(InfluenceAssessment)
    for name, item in fields.items():
        object.__setattr__(provisional, name, item)
    object.__setattr__(provisional, "assessment_sha256", "0" * 64)
    object.__setattr__(provisional, "_trusted_seal", _INFLUENCE_ASSESSMENT_SEAL)
    digest = hashlib.sha256(encode_canonical(_assessment_payload(provisional))).hexdigest()
    result = object.__new__(InfluenceAssessment)
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    object.__setattr__(result, "assessment_sha256", digest)
    object.__setattr__(result, "_trusted_seal", _INFLUENCE_ASSESSMENT_SEAL)
    validate_influence_assessment(result)
    return result


def validate_influence_assessment(value: InfluenceAssessment) -> None:
    if (
        type(value) is not InfluenceAssessment
        or getattr(value, "_trusted_seal", None) is not _INFLUENCE_ASSESSMENT_SEAL
        or type(value.stage) is not ConsumptionStage
    ):
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence assessment must be exact")
    if value.schema_version != INFLUENCE_ASSESSMENT_SCHEMA_V1:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence schema is unknown")
    if type(value.assessment_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.assessment_sha256) is None:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment hash is malformed")
    if type(value.context_id) is not str or _CONTEXT_ID.fullmatch(value.context_id) is None:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment context id is malformed")
    if type(value.delivery_receipt_sha256) is not str or _SHA256.fullmatch(value.delivery_receipt_sha256) is None:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment receipt id is malformed")
    acknowledgement = value.acknowledgement
    evidence = value.platform_evidence
    if acknowledgement is not None:
        validate_worker_consumption_acknowledgement(acknowledgement)
        if (
            acknowledgement.context_id != value.context_id
            or acknowledgement.delivery_receipt_sha256 != value.delivery_receipt_sha256
        ):
            raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "assessment acknowledgement binding differs")
    if evidence is not None:
        validate_platform_influence_evidence(evidence)
        if (
            evidence.context_id != value.context_id
            or evidence.delivery_receipt_sha256 != value.delivery_receipt_sha256
            or acknowledgement is None
            or evidence.worker_actor != acknowledgement.worker_actor
        ):
            raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "assessment platform binding differs")
    expected_stage = (
        ConsumptionStage.INFLUENCED_PROVEN
        if evidence is not None
        else ConsumptionStage.DELIVERED
        if acknowledgement is None
        else ConsumptionStage.REFERENCED_CLAIMED
        if acknowledgement.kind is AcknowledgementKind.REFERENCED
        else ConsumptionStage.PARSED_CLAIMED
    )
    if value.stage is not expected_stage:
        raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "assessment stage differs from its evidence")
    expected = hashlib.sha256(encode_canonical(_assessment_payload(value))).hexdigest()
    if value.assessment_sha256 != expected:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment hash does not match payload")
