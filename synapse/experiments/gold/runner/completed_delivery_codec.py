"""Canonical codec for one completed Stage 10 worker delivery.

The adapter serializes and restores the delivery owner's sealed records.  It
does not dispatch a worker, decide admission, or own any delivery identity.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import unicodedata

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.stage10.context_codec import (
    decode_base64url,
    decode_canonical,
    encode_base64url,
    encode_canonical,
)
from synapse.experiments.gold.stage10.delivery_verification import (
    decode_delivery_receipt,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateReport,
    WorkerCandidateResult,
    WorkerCandidateStatus,
    WorkerCandidateUsage,
    WorkerDeliveryEvidence,
    WorkerDeliveryStatus,
    WorkerInvocation,
    WorkerTokenStatus,
    WORKER_INVOCATION_SCHEMA_V1,
    WORKER_INVOCATION_SCHEMA_V2,
)

from .delivery import (
    AttemptUpstreamEvidence,
    CompletedWorkerDelivery,
    _make_attempt_upstream_evidence_from_refs,
    _make_completed_worker_delivery,
    require_attempt_upstream_evidence,
    require_completed_worker_delivery,
)
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ADAPTER_PRIVATE_SEAM = {
    "synapse.experiments.gold.runner.delivery": frozenset(
        {"_make_completed_worker_delivery", "_make_attempt_upstream_evidence_from_refs"}
    )
}

COMPLETED_WORKER_DELIVERY_SCHEMA_V1 = (
    "synapse.stage4.gold.runner.completed-worker-delivery/v1"
)
COMPLETED_WORKER_DELIVERY_SCHEMA_V2 = "synapse.stage4.gold.runner.completed-worker-delivery/v2"
COMPLETED_WORKER_DELIVERY_SCHEMA_V3 = "synapse.stage4.gold.runner.completed-worker-delivery/v3"
_MEDIA_TYPE = "application/json"


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _upstream_payload(value: AttemptUpstreamEvidence) -> dict[str, object]:
    checked = require_attempt_upstream_evidence(value)
    return {
        "knowledge_snapshot_ref": checked.knowledge_snapshot_ref.to_dict(),
        "retrieval_ref": checked.retrieval_ref.to_dict(),
        "retrieval_decision_ref": checked.retrieval_decision_ref.to_dict(),
        "replay_ref": checked.replay_ref.to_dict(),
        "intent_ref": checked.intent_ref.to_dict(),
        "plan_ref": checked.plan_ref.to_dict(),
    }


def _invocation_payload(value: WorkerInvocation) -> dict[str, object]:
    if type(value) is not WorkerInvocation:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "worker invocation must be exact")
    payload = {
        "invocation_id": value.invocation_id,
        "attempt_id": value.attempt_id,
        "context_id": value.context_id,
        "payload_text": value.payload_text,
        "payload_sha256": value.payload_sha256,
        "payload_byte_length": value.payload_byte_length,
        "envelope_sha256": value.envelope_sha256,
        "allowed_scope": list(value.allowed_scope),
        "capabilities": list(value.capabilities),
    }
    if value.schema_version == WORKER_INVOCATION_SCHEMA_V2:
        payload.update(schema_version=value.schema_version, information_text=value.information_text,
                       information_sha256=value.information_sha256,
                       information_byte_length=value.information_byte_length)
    return payload


def _data_only(value: object, *, field: str) -> object:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            f"{field} contains a non-finite number",
        )
    if type(value) in (tuple, list):
        return [_data_only(item, field=field) for item in value]
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                f"{field} has a non-string key",
            )
        return {
            key: _data_only(item, field=field)
            for key, item in sorted(value.items())
        }
    raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{field} is not canonical data")


def _worker_result_payload(value: WorkerCandidateResult) -> dict[str, object]:
    if type(value) is not WorkerCandidateResult:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "worker result must be exact")
    evidence = value.delivery_evidence
    payload = {
        "status": value.status.value,
        "diff_text": value.diff_text,
        "touched_files": list(value.touched_files),
        "usage": {
            "token_status": value.usage.token_status.value,
            "input_tokens": value.usage.input_tokens,
            "output_tokens": value.usage.output_tokens,
            "thinking_tokens": value.usage.thinking_tokens,
            "total_tokens": value.usage.total_tokens,
            "thinking_included": value.usage.thinking_included,
            "diagnostics": _data_only(
                value.usage.diagnostics,
                field="worker usage diagnostics",
            ),
        },
        "diagnostics": _data_only(value.diagnostics, field="worker diagnostics"),
        "report": {
            "summary": value.report.summary,
            "failure_reason": value.report.failure_reason,
        },
        "delivery_evidence": {
            "invocation_id": evidence.invocation_id,
            "context_id": evidence.context_id,
            "payload_sha256": evidence.payload_sha256,
            "payload_byte_length": evidence.payload_byte_length,
            "envelope_sha256": evidence.envelope_sha256,
            "status": evidence.status.value,
            "transport_name": evidence.transport_name,
        },
    }
    if evidence.input_schema_version == WORKER_INVOCATION_SCHEMA_V2:
        payload["delivery_evidence"].update(input_schema_version=evidence.input_schema_version,
            information_sha256=evidence.information_sha256, information_byte_length=evidence.information_byte_length)
    return payload


def _completed_payload(value: CompletedWorkerDelivery) -> dict[str, object]:
    checked = require_completed_worker_delivery(value)
    worker = _worker_result_payload(checked.worker_result)
    exact_text = _contains_non_nfc_text(worker)
    result = {
        "schema_version": (COMPLETED_WORKER_DELIVERY_SCHEMA_V2
            if checked.invocation.schema_version == WORKER_INVOCATION_SCHEMA_V2 else COMPLETED_WORKER_DELIVERY_SCHEMA_V1),
        "upstream": _upstream_payload(checked.upstream),
        "worker_context_id": checked.worker_context_id,
        "worker_context_audit_sha256": checked.worker_context_audit_sha256,
        "worker_context_audit_ref": checked.worker_context_audit_ref.to_dict(),
        "delivery_envelope_ref": checked.delivery_envelope_ref.to_dict(),
        "plan_bundle_sha256": checked.plan_bundle_sha256,
        "invocation": _invocation_payload(checked.invocation),
        "worker_result": worker,
        "delivery_receipt_base64url": encode_base64url(
            checked.delivery_receipt.canonical_bytes()
        ),
        "delivery_receipt_ref": checked.delivery_receipt_ref.to_dict(),
    }
    if exact_text:
        # The outer Gold record stays canonical; foreign patch/report strings
        # must not be NFC-normalized into different program or evidence bytes.
        result["schema_version"] = COMPLETED_WORKER_DELIVERY_SCHEMA_V3
        result["worker_result_base64url"] = encode_base64url(_exact_result_json(worker))
        del result["worker_result"]
    return result


def _contains_non_nfc_text(value):
    if type(value) is str:
        return unicodedata.normalize("NFC", value) != value
    if type(value) is list:
        return any(_contains_non_nfc_text(item) for item in value)
    if type(value) is dict:
        return any(_contains_non_nfc_text(key) or _contains_non_nfc_text(item) for key, item in value.items())
    return False


def _exact_result_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def completed_worker_delivery_bytes(value: CompletedWorkerDelivery) -> bytes:
    return encode_canonical(_completed_payload(value))


def completed_worker_delivery_ref(value: CompletedWorkerDelivery) -> HashBoundRef:
    payload = completed_worker_delivery_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=decode_canonical(payload)["schema_version"],
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


def _restore_upstream(value: object) -> AttemptUpstreamEvidence:
    data = _exact_dict(
        value,
        {
            "knowledge_snapshot_ref",
            "retrieval_ref",
            "retrieval_decision_ref",
            "replay_ref",
            "intent_ref",
            "plan_ref",
        },
        "completed delivery upstream",
    )
    return _make_attempt_upstream_evidence_from_refs(
        **{name: HashBoundRef.from_dict(item) for name, item in data.items()}
    )


def _restore_invocation(value: object) -> WorkerInvocation:
    split = type(value) is dict and value.get("schema_version") == WORKER_INVOCATION_SCHEMA_V2
    data = _exact_dict(
        value,
        {
            "invocation_id", "attempt_id", "context_id", "payload_text",
            "payload_sha256", "payload_byte_length", "envelope_sha256",
            "allowed_scope", "capabilities",
        } | ({"schema_version", "information_text", "information_sha256", "information_byte_length"} if split else set()),
        "completed delivery invocation",
    )
    if type(data["allowed_scope"]) is not list or type(data["capabilities"]) is not list:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "invocation collections are invalid",
        )
    return WorkerInvocation(
        invocation_id=data["invocation_id"],
        attempt_id=data["attempt_id"],
        context_id=data["context_id"],
        payload_text=data["payload_text"],
        payload_sha256=data["payload_sha256"],
        payload_byte_length=data["payload_byte_length"],
        envelope_sha256=data["envelope_sha256"],
        allowed_scope=tuple(data["allowed_scope"]),
        capabilities=tuple(data["capabilities"]),
        schema_version=data.get("schema_version", WORKER_INVOCATION_SCHEMA_V1),
        information_text=data.get("information_text"),
        information_sha256=data.get("information_sha256"),
        information_byte_length=data.get("information_byte_length"),
    )


def _restore_worker_result(value: object) -> WorkerCandidateResult:
    data = _exact_dict(
        value,
        {
            "status", "diff_text", "touched_files", "usage", "diagnostics",
            "report", "delivery_evidence",
        },
        "completed delivery worker result",
    )
    usage = _exact_dict(
        data["usage"],
        {
            "token_status", "input_tokens", "output_tokens", "thinking_tokens",
            "total_tokens", "thinking_included", "diagnostics",
        },
        "worker usage",
    )
    report = _exact_dict(data["report"], {"summary", "failure_reason"}, "worker report")
    split = (type(data["delivery_evidence"]) is dict
        and data["delivery_evidence"].get("input_schema_version") == WORKER_INVOCATION_SCHEMA_V2)
    evidence = _exact_dict(
        data["delivery_evidence"],
        {
            "invocation_id", "context_id", "payload_sha256",
            "payload_byte_length", "envelope_sha256", "status", "transport_name",
        } | ({"input_schema_version", "information_sha256", "information_byte_length"} if split else set()),
        "worker delivery evidence",
    )
    if type(data["touched_files"]) is not list:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "worker touched files are invalid",
        )
    return WorkerCandidateResult(
        status=WorkerCandidateStatus(data["status"]),
        diff_text=data["diff_text"],
        touched_files=tuple(data["touched_files"]),
        usage=WorkerCandidateUsage(
            token_status=WorkerTokenStatus(usage["token_status"]),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            thinking_tokens=usage["thinking_tokens"],
            total_tokens=usage["total_tokens"],
            thinking_included=usage["thinking_included"],
            diagnostics=usage["diagnostics"],
        ),
        diagnostics=data["diagnostics"],
        report=WorkerCandidateReport(
            summary=report["summary"],
            failure_reason=report["failure_reason"],
        ),
        delivery_evidence=WorkerDeliveryEvidence(
            invocation_id=evidence["invocation_id"],
            context_id=evidence["context_id"],
            payload_sha256=evidence["payload_sha256"],
            payload_byte_length=evidence["payload_byte_length"],
            envelope_sha256=evidence["envelope_sha256"],
            status=WorkerDeliveryStatus(evidence["status"]),
            transport_name=evidence["transport_name"],
            input_schema_version=evidence.get("input_schema_version", WORKER_INVOCATION_SCHEMA_V1),
            information_sha256=evidence.get("information_sha256"),
            information_byte_length=evidence.get("information_byte_length"),
        ),
    )


def restore_completed_worker_delivery(
    value: bytes,
    *,
    expected_ref: HashBoundRef,
) -> CompletedWorkerDelivery:
    """Restore a WORKER_COMPLETED payload only under its exact durable ref.

    This remains one linear codec operation: the hash-bound ref, exact schema,
    nested authority values, and byte-for-byte round-trip jointly establish one
    trusted object. Splitting those checks would expose a partially validated
    recovery value.
    """

    if type(value) is not bytes or type(expected_ref) is not HashBoundRef:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "completed delivery recovery inputs must be exact",
        )
    digest = hashlib.sha256(value).hexdigest()
    if (
        expected_ref.kind is not RefKind.ARTIFACT
        or expected_ref.schema_id not in {COMPLETED_WORKER_DELIVERY_SCHEMA_V1,
                                         COMPLETED_WORKER_DELIVERY_SCHEMA_V2, COMPLETED_WORKER_DELIVERY_SCHEMA_V3}
        or expected_ref.ref_id != digest
        or expected_ref.sha256 != digest
        or expected_ref.byte_length != len(value)
        or expected_ref.media_type != _MEDIA_TYPE
    ):
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "completed delivery ref differs from exact bytes",
        )
    try:
        decoded = decode_canonical(value)
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "completed delivery checkpoint is not canonical",
        ) from exc
    exact_text = expected_ref.schema_id == COMPLETED_WORKER_DELIVERY_SCHEMA_V3
    data = _exact_dict(
        decoded,
        {
            "schema_version", "upstream", "worker_context_id",
            "worker_context_audit_sha256", "worker_context_audit_ref",
            "delivery_envelope_ref", "plan_bundle_sha256", "invocation",
            "worker_result_base64url" if exact_text else "worker_result",
            "delivery_receipt_base64url", "delivery_receipt_ref",
        },
        "completed delivery",
    )
    if (data["schema_version"] != expected_ref.schema_id
            or not exact_text and ((data["schema_version"] == COMPLETED_WORKER_DELIVERY_SCHEMA_V2)
                != (type(data["invocation"]) is dict and data["invocation"].get("schema_version") == WORKER_INVOCATION_SCHEMA_V2))):
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "completed delivery schema is unknown",
        )
    try:
        if exact_text:
            worker_bytes = decode_base64url(data["worker_result_base64url"])
            worker = json.loads(worker_bytes.decode("utf-8"))
            if _exact_result_json(worker) != worker_bytes:
                raise ValueError("worker result is not exact deterministic JSON")
        else:
            worker = data["worker_result"]
        restored = _make_completed_worker_delivery(
            upstream=_restore_upstream(data["upstream"]),
            worker_context_id=data["worker_context_id"],
            worker_context_audit_sha256=data["worker_context_audit_sha256"],
            worker_context_audit_ref=HashBoundRef.from_dict(
                data["worker_context_audit_ref"]
            ),
            delivery_envelope_ref=HashBoundRef.from_dict(
                data["delivery_envelope_ref"]
            ),
            plan_bundle_sha256=data["plan_bundle_sha256"],
            invocation=_restore_invocation(data["invocation"]),
            worker_result=_restore_worker_result(worker),
            delivery_receipt=decode_delivery_receipt(
                decode_base64url(data["delivery_receipt_base64url"])
            ),
            delivery_receipt_ref=HashBoundRef.from_dict(data["delivery_receipt_ref"]),
        )
    except GoldRunViolation:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "completed delivery checkpoint contains invalid fields",
        ) from exc
    if completed_worker_delivery_bytes(restored) != value:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "completed delivery did not round-trip exactly",
        )
    return restored


__all__ = [
    "COMPLETED_WORKER_DELIVERY_SCHEMA_V1",
    "COMPLETED_WORKER_DELIVERY_SCHEMA_V2",
    "COMPLETED_WORKER_DELIVERY_SCHEMA_V3",
    "completed_worker_delivery_bytes",
    "completed_worker_delivery_ref",
    "restore_completed_worker_delivery",
]
