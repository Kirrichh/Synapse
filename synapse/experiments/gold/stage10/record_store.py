"""Immutable filesystem storage for Stage 10 authority and context records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import re

from ..canonicalization import HashBoundRef, RefKind
from ..persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    require_directory,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    write_staged_bytes,
)
from .context_codec import decode_base64url, decode_canonical, encode_base64url, encode_canonical
from .context import (
    ContextPersistenceEvidence,
    WorkerContextRecord,
    _make_context_persistence_evidence,
    validate_worker_context,
)
from .plan_revalidation import (
    PlanPersistenceEvidence,
    _make_plan_persistence_evidence,
)
from .intent import IntentCandidate, validate_intent_candidate
from .plan_authority import AcceptedOperationPlan, validate_accepted_operation_plan
from .delivery_verification import DeliveryReceipt, validate_delivery_receipt
from .influence import InfluenceAssessment, validate_influence_assessment

ADAPTER_PRIVATE_SEAM = {
    "synapse.experiments.gold.stage10.context": frozenset(
        {"_make_context_persistence_evidence"}
    ),
    "synapse.experiments.gold.stage10.plan_revalidation": frozenset(
        {"_make_plan_persistence_evidence"}
    ),
}


STAGE10_STORE_SCHEMA_V1 = "synapse.stage4.gold.stage10.immutable-record-store/v1"
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_SAFE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}\Z")


class Stage10RecordKind(str, Enum):
    INTENT = "intent"
    PLAN_PROPOSAL = "plan-proposal"
    PLAN_DECISION = "plan-decision"
    ACCEPTED_PLAN = "accepted-plan"
    WORKER_CONTEXT_AUDIT = "worker-context-audit"
    WORKER_DELIVERY_ENVELOPE = "worker-delivery-envelope"
    DELIVERY_RECEIPT = "delivery-receipt"
    INFLUENCE_EVIDENCE = "influence-evidence"


class RecordStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    INVALID_KEY = "INVALID_KEY"
    NON_CANONICAL = "NON_CANONICAL"
    RECORD_CONFLICT = "RECORD_CONFLICT"
    RECORD_CORRUPT = "RECORD_CORRUPT"
    RECORD_UNKNOWN = "RECORD_UNKNOWN"


class Stage10RecordStoreViolation(ValueError):
    def __init__(self, failure_code: RecordStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not RecordStoreFailureCode:
            raise TypeError("failure_code must be an exact RecordStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: RecordStoreFailureCode, detail: str) -> Stage10RecordStoreViolation:
    return Stage10RecordStoreViolation(code, detail)


def _record_key(value: object) -> str:
    if type(value) is not str or _SAFE_KEY.fullmatch(value) is None:
        raise _fail(RecordStoreFailureCode.INVALID_KEY, "record key is malformed")
    return value


@dataclass(frozen=True)
class StoredStage10Record:
    kind: Stage10RecordKind
    record_key: str
    payload: bytes
    ref: HashBoundRef


def _stored_payload(
    *,
    kind: Stage10RecordKind,
    record_key: str,
    payload: bytes,
) -> dict[str, object]:
    return {
        "schema_version": STAGE10_STORE_SCHEMA_V1,
        "kind": kind.value,
        "record_key": record_key,
        "payload_base64url": encode_base64url(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_byte_length": len(payload),
    }


class FileStage10RecordStore:
    """Content-addressed immutable store bound to one mutation coordinator."""

    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(RecordStoreFailureCode.TYPE_MISMATCH, "store root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
            coordinator_id = mutation_fence.coordinator_id()
        except (TypeError, ValueError) as exc:
            raise _fail(
                RecordStoreFailureCode.TYPE_MISMATCH,
                "store requires a valid mutation fence",
            ) from exc
        if type(coordinator_id) is not str or not coordinator_id:
            raise _fail(
                RecordStoreFailureCode.TYPE_MISMATCH,
                "store mutation fence has no exact coordinator identity",
            )
        self._root = root
        self._mutation_fence = mutation_fence
        self._coordinator_id = coordinator_id
        ensure_directory(root)
        for kind in Stage10RecordKind:
            ensure_directory(root / kind.value)

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def record_root(self) -> Path:
        return self._root

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    def put(
        self,
        *,
        kind: Stage10RecordKind,
        record_key: str,
        canonical_payload: bytes,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        if type(kind) is not Stage10RecordKind or type(canonical_payload) is not bytes:
            raise _fail(RecordStoreFailureCode.TYPE_MISMATCH, "record kind and payload must be exact")
        key = _record_key(record_key)
        require_ticket_of_coordinator(
            ticket,
            coordinator_id=self._coordinator_id,
        )
        decode_canonical(canonical_payload)
        if not canonical_payload or len(canonical_payload) > _MAX_RECORD_BYTES:
            raise _fail(RecordStoreFailureCode.NON_CANONICAL, "record payload exceeds store bounds")
        wrapper = encode_canonical(
            _stored_payload(kind=kind, record_key=key, payload=canonical_payload)
        )
        digest = hashlib.sha256(wrapper).hexdigest()
        directory = self._root / kind.value
        require_directory(directory)
        destination = directory / f"{digest}.stage10"
        if destination.exists() or destination.is_symlink():
            existing = self._read_path(destination, expected_kind=kind)
            if existing.record_key != key or existing.payload != canonical_payload:
                raise _fail(RecordStoreFailureCode.RECORD_CONFLICT, "immutable record destination conflicts")
            return existing.ref
        staged = write_staged_bytes(
            directory,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=wrapper,
            maximum_bytes=_MAX_RECORD_BYTES * 2,
            ticket=ticket,
        )
        try:
            publish_immutable(staged, destination, ticket=ticket)
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.DESTINATION_EXISTS:
                existing = self._read_path(destination, expected_kind=kind)
                if existing.record_key == key and existing.payload == canonical_payload:
                    return existing.ref
            raise
        return self._read_path(destination, expected_kind=kind).ref

    def get(self, *, kind: Stage10RecordKind, ref: HashBoundRef) -> StoredStage10Record:
        if type(kind) is not Stage10RecordKind or type(ref) is not HashBoundRef:
            raise _fail(RecordStoreFailureCode.TYPE_MISMATCH, "record lookup arguments must be exact")
        if ref.kind is not RefKind.ARTIFACT or ref.schema_id != STAGE10_STORE_SCHEMA_V1:
            raise _fail(RecordStoreFailureCode.RECORD_UNKNOWN, "record ref uses the wrong kind or schema")
        path = self._root / kind.value / f"{ref.ref_id}.stage10"
        try:
            restored = self._read_path(path, expected_kind=kind)
        except PersistenceViolation as exc:
            raise _fail(RecordStoreFailureCode.RECORD_UNKNOWN, "record is unavailable") from exc
        if restored.ref != ref:
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "record ref differs from restored bytes")
        return restored

    def persist_worker_context(
        self,
        context: WorkerContextRecord,
        *,
        ticket: StoreMutationTicket,
    ) -> ContextPersistenceEvidence:
        """Persist and read back the exact audit and delivery records."""

        validate_worker_context(context)
        audit_ref = self.put(
            kind=Stage10RecordKind.WORKER_CONTEXT_AUDIT,
            record_key=context.context_id,
            canonical_payload=context.canonical_bytes(),
            ticket=ticket,
        )
        delivery_ref = self.put(
            kind=Stage10RecordKind.WORKER_DELIVERY_ENVELOPE,
            record_key=context.delivery_envelope.envelope_sha256,
            canonical_payload=context.delivery_envelope.canonical_bytes(),
            ticket=ticket,
        )
        restored_audit = self.get(
            kind=Stage10RecordKind.WORKER_CONTEXT_AUDIT,
            ref=audit_ref,
        )
        restored_delivery = self.get(
            kind=Stage10RecordKind.WORKER_DELIVERY_ENVELOPE,
            ref=delivery_ref,
        )
        return _make_context_persistence_evidence(
            context=context,
            audit_store_ref=audit_ref,
            delivery_store_ref=delivery_ref,
            restored_audit_payload=restored_audit.payload,
            restored_delivery_payload=restored_delivery.payload,
        )

    def persist_plan_bundle(
        self,
        *,
        intent: IntentCandidate,
        accepted_plan: AcceptedOperationPlan,
        ticket: StoreMutationTicket,
    ) -> PlanPersistenceEvidence:
        """Persist and read back every §24 record required before dispatch."""

        validate_intent_candidate(intent)
        validate_accepted_operation_plan(accepted_plan)
        records = (
            (Stage10RecordKind.INTENT, intent.proposal_id.record_id.digest_sha256, encode_canonical(intent.to_dict())),
            (Stage10RecordKind.PLAN_PROPOSAL, accepted_plan.candidate.proposal_id.record_id.digest_sha256, encode_canonical(accepted_plan.candidate.to_dict())),
            (Stage10RecordKind.PLAN_DECISION, accepted_plan.decision.decision_id.record_id.digest_sha256, encode_canonical(accepted_plan.decision.to_dict())),
            (Stage10RecordKind.ACCEPTED_PLAN, accepted_plan.accepted_plan_id.record_id.digest_sha256, encode_canonical(accepted_plan.to_dict())),
        )
        refs: list[HashBoundRef] = []
        restored: list[bytes] = []
        for kind, key, payload in records:
            ref = self.put(
                kind=kind,
                record_key=key,
                canonical_payload=payload,
                ticket=ticket,
            )
            refs.append(ref)
            restored.append(self.get(kind=kind, ref=ref).payload)
        return _make_plan_persistence_evidence(
            intent=intent,
            accepted_plan=accepted_plan,
            store_refs=tuple(refs),
            restored_payloads=tuple(restored),
        )

    def persist_delivery_receipt(
        self,
        receipt: DeliveryReceipt,
        *,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        validate_delivery_receipt(receipt)
        payload = encode_canonical(receipt.to_dict())
        ref = self.put(
            kind=Stage10RecordKind.DELIVERY_RECEIPT,
            record_key=receipt.receipt_sha256,
            canonical_payload=payload,
            ticket=ticket,
        )
        if self.get(kind=Stage10RecordKind.DELIVERY_RECEIPT, ref=ref).payload != payload:
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "delivery receipt read-back differs")
        return ref

    def persist_influence_assessment(
        self,
        assessment: InfluenceAssessment,
        *,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        validate_influence_assessment(assessment)
        payload = encode_canonical(
            {
                "assessment_sha256": assessment.assessment_sha256,
                "payload": decode_canonical(assessment.canonical_bytes()),
            }
        )
        ref = self.put(
            kind=Stage10RecordKind.INFLUENCE_EVIDENCE,
            record_key=assessment.assessment_sha256,
            canonical_payload=payload,
            ticket=ticket,
        )
        if self.get(kind=Stage10RecordKind.INFLUENCE_EVIDENCE, ref=ref).payload != payload:
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "influence assessment read-back differs")
        return ref

    def _read_path(self, path: Path, *, expected_kind: Stage10RecordKind) -> StoredStage10Record:
        raw = read_regular_bytes(path, maximum_bytes=_MAX_RECORD_BYTES * 2)
        decoded = decode_canonical(raw)
        required = {
            "schema_version",
            "kind",
            "record_key",
            "payload_base64url",
            "payload_sha256",
            "payload_byte_length",
        }
        if type(decoded) is not dict or set(decoded) != required:
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "stored record has an unknown shape")
        if decoded["schema_version"] != STAGE10_STORE_SCHEMA_V1 or decoded["kind"] != expected_kind.value:
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "stored record kind or schema differs")
        key = _record_key(decoded["record_key"])
        payload = decode_base64url(decoded["payload_base64url"])
        decode_canonical(payload)
        if decoded["payload_byte_length"] != len(payload):
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "stored payload length differs")
        if decoded["payload_sha256"] != hashlib.sha256(payload).hexdigest():
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "stored payload hash differs")
        digest = hashlib.sha256(raw).hexdigest()
        if path.name != f"{digest}.stage10":
            raise _fail(RecordStoreFailureCode.RECORD_CORRUPT, "stored record path is not content-addressed")
        ref = HashBoundRef(
            kind=RefKind.ARTIFACT,
            ref_id=digest,
            schema_id=STAGE10_STORE_SCHEMA_V1,
            sha256=digest,
            byte_length=len(raw),
            media_type="application/json",
        )
        return StoredStage10Record(expected_kind, key, payload, ref)
