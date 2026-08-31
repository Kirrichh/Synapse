"""Canonical checkpoint codec for a pre-C1 delivery failure.

Refusal and dependency unavailability remain distinct terminal kinds while
sharing one exact durable format.  The payload carries the real upstream phase
identities established before the fresh admission attempt, so recovery never
repeats admission merely to rediscover why dispatch did not happen.
"""

from __future__ import annotations

import hashlib

from synapse.experiments.gold.admission import AdmissionFailureCode
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.stage10.context_codec import (
    decode_canonical,
    encode_canonical,
)

from .delivery import (
    AttemptDeliveryRefusal,
    AttemptDeliveryUnavailable,
    AttemptUpstreamEvidence,
    _make_attempt_delivery_failure,
    _make_attempt_upstream_evidence_from_refs,
    require_attempt_delivery_refusal,
    require_attempt_delivery_unavailable,
)
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ADAPTER_PRIVATE_SEAM = {
    "synapse.experiments.gold.runner.delivery": frozenset(
        {"_make_attempt_delivery_failure", "_make_attempt_upstream_evidence_from_refs"}
    )
}

ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1 = (
    "synapse.stage4.gold.runner.attempt-delivery-failure/v1"
)
_MEDIA_TYPE = "application/json"
_REFUSED = "DELIVERY_REFUSED"
_UNAVAILABLE = "DELIVERY_UNAVAILABLE"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _upstream_payload(value: AttemptUpstreamEvidence) -> dict[str, object]:
    return {
        "knowledge_snapshot_ref": value.knowledge_snapshot_ref.to_dict(),
        "retrieval_ref": value.retrieval_ref.to_dict(),
        "retrieval_decision_ref": value.retrieval_decision_ref.to_dict(),
        "replay_ref": value.replay_ref.to_dict(),
        "intent_ref": value.intent_ref.to_dict(),
        "plan_ref": value.plan_ref.to_dict(),
    }


def _payload(
    value: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
) -> dict[str, object]:
    if type(value) is AttemptDeliveryRefusal:
        checked = require_attempt_delivery_refusal(value)
        kind = _REFUSED
    elif type(value) is AttemptDeliveryUnavailable:
        checked = require_attempt_delivery_unavailable(value)
        kind = _UNAVAILABLE
    else:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery failure must be exact refusal or unavailability",
        )
    return {
        "schema_version": ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1,
        "terminal_kind": kind,
        "upstream": _upstream_payload(checked.upstream),
        "admission_failure_code": checked.admission_failure_code.value,
        "detail": checked.detail,
    }


def attempt_delivery_failure_bytes(
    value: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
) -> bytes:
    return encode_canonical(_payload(value))


def attempt_delivery_failure_ref(
    value: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
) -> HashBoundRef:
    payload = attempt_delivery_failure_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1,
        sha256=digest,
        byte_length=len(payload),
        media_type=_MEDIA_TYPE,
    )


def _exact_dict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            f"{name} has an unknown shape",
        )
    return value


def restore_attempt_delivery_failure(
    value: bytes,
    *,
    expected_ref: HashBoundRef,
) -> AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    """Restore a failure only under its exact checkpoint reference.

    The decoder stays linear because ref verification, closed-shape decoding,
    enum restoration, and canonical round-trip are one identity proof. Partial
    decoder entry points would allow callers to trust an incompletely verified
    checkpoint.
    """

    if type(value) is not bytes or type(expected_ref) is not HashBoundRef:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "delivery failure recovery inputs must be exact",
        )
    digest = hashlib.sha256(value).hexdigest()
    if (
        expected_ref.kind is not RefKind.ARTIFACT
        or expected_ref.schema_id != ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1
        or expected_ref.ref_id != digest
        or expected_ref.sha256 != digest
        or expected_ref.byte_length != len(value)
        or expected_ref.media_type != _MEDIA_TYPE
    ):
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure ref differs from checkpoint bytes",
        )
    try:
        decoded = decode_canonical(value)
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure checkpoint is not canonical",
        ) from exc
    data = _exact_dict(
        decoded,
        {
            "schema_version",
            "terminal_kind",
            "upstream",
            "admission_failure_code",
            "detail",
        },
        "delivery failure",
    )
    if data["schema_version"] != ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure schema is unknown",
        )
    if data["terminal_kind"] not in {_REFUSED, _UNAVAILABLE}:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure terminal kind is unknown",
        )
    upstream = _exact_dict(
        data["upstream"],
        {
            "knowledge_snapshot_ref",
            "retrieval_ref",
            "retrieval_decision_ref",
            "replay_ref",
            "intent_ref",
            "plan_ref",
        },
        "delivery failure upstream",
    )
    try:
        failure_code = AdmissionFailureCode(data["admission_failure_code"])
        restored_upstream = _make_attempt_upstream_evidence_from_refs(
            **{name: HashBoundRef.from_dict(item) for name, item in upstream.items()}
        )
        restored = _make_attempt_delivery_failure(
            upstream=restored_upstream,
            failure_code=failure_code,
            detail=data["detail"],
            unavailable=data["terminal_kind"] == _UNAVAILABLE,
        )
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure checkpoint contains invalid fields",
        ) from exc
    if attempt_delivery_failure_bytes(restored) != value:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "delivery failure did not round-trip exactly",
        )
    return restored


__all__ = [
    "ATTEMPT_DELIVERY_FAILURE_SCHEMA_V1",
    "attempt_delivery_failure_bytes",
    "attempt_delivery_failure_ref",
    "restore_attempt_delivery_failure",
]
