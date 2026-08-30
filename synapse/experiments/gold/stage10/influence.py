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
        if type(self.worker_actor) is not ActorIdentity or type(self.kind) is not AcknowledgementKind:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "acknowledgement fields must be exact")
        if type(self.referenced_item_ids) is not tuple or any(type(item) is not str or not item for item in self.referenced_item_ids):
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "referenced item ids must be a tuple")
        if self.referenced_item_ids != tuple(sorted(set(self.referenced_item_ids))):
            raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "referenced item ids must be sorted and unique")
        if self.kind is AcknowledgementKind.PARSED and self.referenced_item_ids:
            raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "parsed acknowledgement cannot claim references")


@dataclass(frozen=True)
class PlatformInfluenceEvidence:
    context_id: str
    delivery_receipt_sha256: str
    worker_actor: ActorIdentity
    observer_actor: ActorIdentity
    output_artifact_ref: HashBoundRef
    evidence_ref: HashBoundRef

    def __post_init__(self) -> None:
        if type(self.worker_actor) is not ActorIdentity or type(self.observer_actor) is not ActorIdentity:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence actors must be exact")
        if self.worker_actor == self.observer_actor:
            raise _fail(InfluenceFailureCode.EVIDENCE_NOT_INDEPENDENT, "worker cannot attest its own influence")
        if type(self.output_artifact_ref) is not HashBoundRef or self.output_artifact_ref.kind is not RefKind.ARTIFACT:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "output artifact ref must be exact")
        if type(self.evidence_ref) is not HashBoundRef or self.evidence_ref.kind is not RefKind.SOURCE_EVIDENCE:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "platform evidence ref must be source evidence")


@dataclass(frozen=True)
class InfluenceAssessment:
    schema_version: str
    assessment_sha256: str
    context_id: str
    delivery_receipt_sha256: str
    stage: ConsumptionStage
    acknowledgement: WorkerConsumptionAcknowledgement | None
    platform_evidence: PlatformInfluenceEvidence | None

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
        if type(acknowledgement) is not WorkerConsumptionAcknowledgement:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "acknowledgement must be exact")
        WorkerConsumptionAcknowledgement(**acknowledgement.__dict__)
        if acknowledgement.context_id != receipt.context_id or acknowledgement.delivery_receipt_sha256 != receipt.receipt_sha256:
            raise _fail(InfluenceFailureCode.ACKNOWLEDGEMENT_MISMATCH, "acknowledgement belongs to another delivery")
    if platform_evidence is not None:
        if type(platform_evidence) is not PlatformInfluenceEvidence or acknowledgement is None:
            raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "platform evidence requires an acknowledgement")
        PlatformInfluenceEvidence(**platform_evidence.__dict__)
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
    provisional = InfluenceAssessment(assessment_sha256="0" * 64, **fields)
    digest = hashlib.sha256(encode_canonical(_assessment_payload(provisional))).hexdigest()
    result = InfluenceAssessment(assessment_sha256=digest, **fields)
    validate_influence_assessment(result)
    return result


def validate_influence_assessment(value: InfluenceAssessment) -> None:
    if type(value) is not InfluenceAssessment or type(value.stage) is not ConsumptionStage:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence assessment must be exact")
    if value.schema_version != INFLUENCE_ASSESSMENT_SCHEMA_V1:
        raise _fail(InfluenceFailureCode.TYPE_MISMATCH, "influence schema is unknown")
    if type(value.assessment_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.assessment_sha256) is None:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment hash is malformed")
    if value.stage is ConsumptionStage.INFLUENCED_PROVEN and value.platform_evidence is None:
        raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "proven influence requires platform evidence")
    if value.platform_evidence is not None and value.acknowledgement is None:
        raise _fail(InfluenceFailureCode.EVIDENCE_MISMATCH, "platform evidence requires acknowledgement")
    expected = hashlib.sha256(encode_canonical(_assessment_payload(value))).hexdigest()
    if value.assessment_sha256 != expected:
        raise _fail(InfluenceFailureCode.IDENTITY_MISMATCH, "assessment hash does not match payload")
