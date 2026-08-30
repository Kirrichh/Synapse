"""Independent verification of exact worker-context transport evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from .worker_transport import (
    WorkerDeliveryEvidence,
    WorkerDeliveryStatus,
    WorkerInvocation,
)

from .context import WorkerContextRecord, validate_worker_context
from .context_codec import encode_canonical


DELIVERY_RECEIPT_SCHEMA_V1 = "synapse.stage4.gold.stage10.delivery-receipt/v1"
_DELIVERY_RECEIPT_SEAL = object()
_INVOCATION_ID = re.compile(r"inv_[0-9a-f]{64}\Z")
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CONTEXT_ID = re.compile(r"ctx_[0-9a-f]{64}\Z")
_TRANSPORT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


class DeliveryFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    INVOCATION_MISMATCH = "INVOCATION_MISMATCH"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    PAYLOAD_MISMATCH = "PAYLOAD_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class DeliveryViolation(ValueError):
    def __init__(self, failure_code: DeliveryFailureCode, detail: str) -> None:
        if type(failure_code) is not DeliveryFailureCode:
            raise TypeError("failure_code must be an exact DeliveryFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: DeliveryFailureCode, detail: str) -> DeliveryViolation:
    return DeliveryViolation(code, detail)


@dataclass(frozen=True, init=False)
class DeliveryReceipt:
    schema_version: str
    receipt_sha256: str
    invocation_id: str
    attempt_id: str
    context_id: str
    envelope_sha256: str
    prompt_sha256: str
    prompt_byte_length: int
    delivery_status: WorkerDeliveryStatus
    transport_name: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> DeliveryReceipt:
        raise TypeError("DeliveryReceipt is produced only by verified dispatch evidence")

    def canonical_bytes(self) -> bytes:
        validate_delivery_receipt(self)
        return encode_canonical(_receipt_payload(self))

    def to_dict(self) -> dict[str, object]:
        validate_delivery_receipt(self)
        return {"receipt_sha256": self.receipt_sha256, "payload": _receipt_payload(self)}


def _receipt_payload(value: DeliveryReceipt) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "invocation_id": value.invocation_id,
        "attempt_id": value.attempt_id,
        "context_id": value.context_id,
        "envelope_sha256": value.envelope_sha256,
        "prompt_sha256": value.prompt_sha256,
        "prompt_byte_length": value.prompt_byte_length,
        "delivery_status": value.delivery_status.value,
        "transport_name": value.transport_name,
    }


def verify_delivery(
    *,
    context: WorkerContextRecord,
    invocation: WorkerInvocation,
    evidence: WorkerDeliveryEvidence,
) -> DeliveryReceipt:
    validate_worker_context(context)
    if type(invocation) is not WorkerInvocation or type(evidence) is not WorkerDeliveryEvidence:
        raise _fail(DeliveryFailureCode.TYPE_MISMATCH, "invocation and evidence must be exact")
    if invocation.attempt_id != context.attempt_id.value:
        raise _fail(
            DeliveryFailureCode.INVOCATION_MISMATCH,
            "worker invocation belongs to another attempt",
        )
    if evidence.status is not WorkerDeliveryStatus.PROCESS_STARTED:
        raise _fail(DeliveryFailureCode.NOT_DISPATCHED, "worker process did not receive the invocation")
    if evidence.invocation_id != invocation.invocation_id:
        raise _fail(DeliveryFailureCode.INVOCATION_MISMATCH, "transport evidence belongs to another invocation")
    if evidence.context_id != context.context_id or invocation.context_id != context.context_id:
        raise _fail(DeliveryFailureCode.CONTEXT_MISMATCH, "transport evidence belongs to another context")
    envelope = context.delivery_envelope
    if evidence.envelope_sha256 != envelope.envelope_sha256 or invocation.envelope_sha256 != envelope.envelope_sha256:
        raise _fail(DeliveryFailureCode.PAYLOAD_MISMATCH, "transport envelope binding differs")
    if evidence.payload_sha256 != envelope.prompt_sha256 or invocation.payload_sha256 != envelope.prompt_sha256:
        raise _fail(DeliveryFailureCode.PAYLOAD_MISMATCH, "transport prompt hash differs")
    if evidence.payload_byte_length != envelope.prompt_byte_length or invocation.payload_byte_length != envelope.prompt_byte_length:
        raise _fail(DeliveryFailureCode.PAYLOAD_MISMATCH, "transport prompt length differs")
    fields: dict[str, object] = dict(
        schema_version=DELIVERY_RECEIPT_SCHEMA_V1,
        invocation_id=invocation.invocation_id,
        attempt_id=invocation.attempt_id,
        context_id=context.context_id,
        envelope_sha256=envelope.envelope_sha256,
        prompt_sha256=envelope.prompt_sha256,
        prompt_byte_length=envelope.prompt_byte_length,
        delivery_status=evidence.status,
        transport_name=evidence.transport_name,
    )
    provisional = object.__new__(DeliveryReceipt)
    for name, item in fields.items():
        object.__setattr__(provisional, name, item)
    object.__setattr__(provisional, "receipt_sha256", "0" * 64)
    object.__setattr__(provisional, "_trusted_seal", _DELIVERY_RECEIPT_SEAL)
    digest = hashlib.sha256(encode_canonical(_receipt_payload(provisional))).hexdigest()
    result = object.__new__(DeliveryReceipt)
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    object.__setattr__(result, "receipt_sha256", digest)
    object.__setattr__(result, "_trusted_seal", _DELIVERY_RECEIPT_SEAL)
    validate_delivery_receipt(result)
    return result


def validate_delivery_receipt(value: DeliveryReceipt) -> None:
    if (
        type(value) is not DeliveryReceipt
        or getattr(value, "_trusted_seal", None) is not _DELIVERY_RECEIPT_SEAL
    ):
        raise _fail(DeliveryFailureCode.TYPE_MISMATCH, "delivery receipt must be exact")
    if value.schema_version != DELIVERY_RECEIPT_SCHEMA_V1:
        raise _fail(DeliveryFailureCode.TYPE_MISMATCH, "delivery receipt schema is unknown")
    if type(value.receipt_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.receipt_sha256) is None:
        raise _fail(DeliveryFailureCode.IDENTITY_MISMATCH, "receipt hash is malformed")
    if type(value.invocation_id) is not str or _INVOCATION_ID.fullmatch(value.invocation_id) is None:
        raise _fail(DeliveryFailureCode.INVOCATION_MISMATCH, "receipt invocation id is malformed")
    if type(value.attempt_id) is not str or _ATTEMPT_ID.fullmatch(value.attempt_id) is None:
        raise _fail(DeliveryFailureCode.INVOCATION_MISMATCH, "receipt attempt id is malformed")
    if type(value.context_id) is not str or _CONTEXT_ID.fullmatch(value.context_id) is None:
        raise _fail(DeliveryFailureCode.CONTEXT_MISMATCH, "receipt context id is malformed")
    if type(value.prompt_byte_length) is not int or value.prompt_byte_length <= 0:
        raise _fail(DeliveryFailureCode.PAYLOAD_MISMATCH, "receipt prompt length is invalid")
    for digest in (value.envelope_sha256, value.prompt_sha256):
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise _fail(DeliveryFailureCode.PAYLOAD_MISMATCH, "receipt digest is malformed")
    if value.delivery_status is not WorkerDeliveryStatus.PROCESS_STARTED:
        raise _fail(DeliveryFailureCode.NOT_DISPATCHED, "receipt does not prove process dispatch")
    if type(value.transport_name) is not str or _TRANSPORT_NAME.fullmatch(value.transport_name) is None:
        raise _fail(DeliveryFailureCode.TYPE_MISMATCH, "receipt transport name is malformed")
    expected = hashlib.sha256(encode_canonical(_receipt_payload(value))).hexdigest()
    if value.receipt_sha256 != expected:
        raise _fail(DeliveryFailureCode.IDENTITY_MISMATCH, "delivery receipt hash does not match payload")
