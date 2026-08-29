"""Durable, coordinator-bound activity-policy decision history for Stage 4 replay."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import json

from .activity_policy import (
    ActivityPolicyDecision,
    ConfiguredActivityPolicyEvaluator,
    activity_policy_decision_from_dict,
    activity_policy_decision_ref,
    require_activity_policy_evaluator,
    validate_activity_policy_decision,
)
from .activity_provenance import (
    ActivityConsumptionProvenance,
    ActivityProductionProvenance,
    ActivityProvenanceViolation,
    activity_consumption_provenance_from_dict,
    activity_production_provenance_from_dict,
    activity_provenance_ref,
    require_activity_production_subject_for_record,
    require_activity_provenance_ref,
    validate_activity_consumption_provenance,
    validate_activity_production_provenance,
)
from .activities import RecordedActivity
from .canonicalization import HashBoundRef
from .contracts import SchemaVersion
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    require_open_mutation_ticket,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
)

ACTIVITY_POLICY_STORE_V1 = "synapse.stage4.gold.activity-policy-store/v1"
ACTIVITY_POLICY_JOURNAL_V1 = "activity-policy-decisions.journal"
ACTIVITY_PROVENANCE_STORE_V1 = "synapse.stage4.gold.activity-provenance-store/v1"
ACTIVITY_PRODUCTION_PROVENANCE_JOURNAL_V1 = "activity-production-provenance.journal"
_MAX_JOURNAL_PAYLOAD = 512 * 1024
_ANCHOR_PREFIX = ACTIVITY_POLICY_STORE_V1.encode("utf-8") + b"\x00"
_PROVENANCE_ANCHOR_PREFIX = ACTIVITY_PROVENANCE_STORE_V1.encode("utf-8") + b"\x00"


class ActivityPolicyStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_FORKED = "HISTORY_FORKED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECORD_UNKNOWN = "RECORD_UNKNOWN"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"


class ActivityPolicyStoreViolation(ValueError):
    def __init__(self, failure_code: ActivityPolicyStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not ActivityPolicyStoreFailureCode:
            raise TypeError("failure_code must be an exact ActivityPolicyStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: ActivityPolicyStoreFailureCode, detail: str
) -> ActivityPolicyStoreViolation:
    return ActivityPolicyStoreViolation(code, detail)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _ref_key(value: HashBoundRef) -> str:
    if type(value) is not HashBoundRef:
        raise _fail(ActivityPolicyStoreFailureCode.TYPE_MISMATCH, "an exact reference is required")
    return _canonical(value.to_dict()).decode("utf-8")


@dataclass(frozen=True)
class ActivityPolicyDecisionFrame:
    sequence: int
    coordinator_id: str
    parent_anchor: str
    decision_ref: HashBoundRef
    declaration: dict[str, object]
    actor_set: dict[str, object]
    independence_proof: dict[str, object]
    consumption_provenance: ActivityConsumptionProvenance
    decision: dict[str, object]
    frame_bytes: bytes


def _frame_payload(
    *,
    sequence: int,
    coordinator_id: str,
    parent_anchor: str,
    decision_ref: HashBoundRef,
    declaration: dict[str, object],
    actor_set: dict[str, object],
    independence_proof: dict[str, object],
    consumption_provenance: dict[str, object],
    decision: dict[str, object],
) -> bytes:
    return _canonical(
        {
            "schema_version": ACTIVITY_POLICY_STORE_V1,
            "sequence": sequence,
            "coordinator_id": coordinator_id,
            "parent_anchor": parent_anchor,
            "decision_ref": decision_ref.to_dict(),
            "declaration": declaration,
            "actor_set": actor_set,
            "independence_proof": independence_proof,
            # The consuming half of §9.4 provenance, kept beside the decision it
            # was made for. The producing half is on the activity record itself,
            # which the decision names by reference — so between the two records
            # a later reader can reconstruct every actual party without asking
            # anybody to remember.
            "consumption_provenance": consumption_provenance,
            "decision": decision,
        }
    )


def _anchor_chain(frames: tuple[bytes, ...]) -> tuple[str, ...]:
    anchors = [hashlib.sha256(_ANCHOR_PREFIX).hexdigest()]
    for payload in frames:
        anchors.append(
            hashlib.sha256(
                _ANCHOR_PREFIX
                + bytes.fromhex(anchors[-1])
                + hashlib.sha256(payload).digest()
            ).hexdigest()
        )
    return tuple(anchors)


@dataclass(frozen=True)
class ActivityProductionProvenanceFrame:
    sequence: int
    coordinator_id: str
    parent_anchor: str
    provenance_ref: HashBoundRef
    provenance: ActivityProductionProvenance
    frame_bytes: bytes


def _provenance_frame_payload(
    *,
    sequence: int,
    coordinator_id: str,
    parent_anchor: str,
    provenance_ref: HashBoundRef,
    provenance: dict[str, object],
) -> bytes:
    return _canonical(
        {
            "schema_version": ACTIVITY_PROVENANCE_STORE_V1,
            "sequence": sequence,
            "coordinator_id": coordinator_id,
            "parent_anchor": parent_anchor,
            "provenance_ref": provenance_ref.to_dict(),
            "provenance": provenance,
        }
    )


def _provenance_anchor_chain(frames: tuple[bytes, ...]) -> tuple[str, ...]:
    anchors = [hashlib.sha256(_PROVENANCE_ANCHOR_PREFIX).hexdigest()]
    for payload in frames:
        anchors.append(
            hashlib.sha256(
                _PROVENANCE_ANCHOR_PREFIX
                + bytes.fromhex(anchors[-1])
                + hashlib.sha256(payload).digest()
            ).hexdigest()
        )
    return tuple(anchors)


def _require_production_evaluator_binding(
    value: ActivityProductionProvenance,
    evaluator: ConfiguredActivityPolicyEvaluator,
) -> None:
    validate_activity_production_provenance(value)
    actors = evaluator.actor_set
    if (
        value.configuration_id != evaluator.declaration.configuration_id
        or value.actor_set_id != actors.actor_set_id
        or value.policy_version != evaluator.declaration.policy_version
        or value.actors()
        != (
            actors.producer_actor,
            actors.recorder_actor,
            actors.worker_actor,
            actors.model_actor,
        )
    ):
        raise _fail(
            ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH,
            "production provenance names another evaluator configuration",
        )


def _require_consumption_evaluator_binding(
    value: ActivityConsumptionProvenance,
    evaluator: ConfiguredActivityPolicyEvaluator,
) -> None:
    validate_activity_consumption_provenance(value)
    actors = evaluator.actor_set
    if (
        value.configuration_id != evaluator.declaration.configuration_id
        or value.actor_set_id != actors.actor_set_id
        or value.policy_version != evaluator.declaration.policy_version
        or value.actors()
        != (
            actors.replay_executor_actor,
            actors.machine_adapter_actor,
            actors.consumer_actor,
        )
    ):
        raise _fail(
            ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH,
            "consumption provenance names another evaluator configuration",
        )


class FileActivityPolicyStore:
    """Append-only policy decisions that can be resolved after restart."""

    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(ActivityPolicyStoreFailureCode.TYPE_MISMATCH, "policy store root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _fail(
                ActivityPolicyStoreFailureCode.TYPE_MISMATCH,
                "the policy store requires a mutation fence",
            ) from exc
        self._root = root
        self._mutation_fence = mutation_fence
        ensure_directory(root)
        self._frames()
        self._production_frames()

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def journal_path(self) -> Path:
        return self._root / ACTIVITY_POLICY_JOURNAL_V1

    @property
    def production_provenance_journal_path(self) -> Path:
        return self._root / ACTIVITY_PRODUCTION_PROVENANCE_JOURNAL_V1

    def _decode(self, payload: bytes) -> ActivityPolicyDecisionFrame:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy frame is not JSON") from exc
        fields = {
            "schema_version", "sequence", "coordinator_id", "parent_anchor",
            "decision_ref", "declaration", "actor_set", "independence_proof",
            "consumption_provenance", "decision",
        }
        if type(data) is not dict or set(data) != fields:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy frame has an invalid shape")
        if data["schema_version"] != ACTIVITY_POLICY_STORE_V1:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy frame has an unknown schema")
        try:
            reference = HashBoundRef.from_dict(data["decision_ref"])
        except (TypeError, ValueError) as exc:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy reference is invalid") from exc
        if reference.schema_id != SchemaVersion.ACTIVITY_POLICY_DECISION_V1.value:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy reference names another schema")
        for field in (
            "declaration", "actor_set", "independence_proof",
            "consumption_provenance", "decision",
        ):
            if type(data[field]) is not dict:
                raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, f"{field} is not a record")
        frame_bytes = _frame_payload(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            decision_ref=reference,
            declaration=data["declaration"],
            actor_set=data["actor_set"],
            independence_proof=data["independence_proof"],
            consumption_provenance=data["consumption_provenance"],
            decision=data["decision"],
        )
        if frame_bytes != payload:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy frame is not canonical")
        decision_bytes = _canonical(data["decision"])
        if (
            hashlib.sha256(decision_bytes).hexdigest() != reference.sha256
            or len(decision_bytes) != reference.byte_length
        ):
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "a policy decision differs from its reference")
        try:
            consumption = activity_consumption_provenance_from_dict(
                data["consumption_provenance"]
            )
            decision_consumption_ref = HashBoundRef.from_dict(
                data["decision"]["payload"]["consumption_provenance_ref"]
            )
            require_activity_provenance_ref(decision_consumption_ref, consumption)
        except (ActivityProvenanceViolation, TypeError, ValueError, KeyError) as exc:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "stored consumption provenance is invalid or belongs to another decision",
            ) from exc
        return ActivityPolicyDecisionFrame(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            decision_ref=reference,
            declaration=data["declaration"],
            actor_set=data["actor_set"],
            independence_proof=data["independence_proof"],
            consumption_provenance=consumption,
            decision=data["decision"],
            frame_bytes=frame_bytes,
        )

    def _frames(self) -> tuple[ActivityPolicyDecisionFrame, ...]:
        try:
            scanned = scan_journal(self.journal_path)
        except PersistenceViolation as exc:
            code = (
                ActivityPolicyStoreFailureCode.HISTORY_TORN
                if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL
                else ActivityPolicyStoreFailureCode.HISTORY_CORRUPT
            )
            raise _fail(code, "the policy journal could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _fail(ActivityPolicyStoreFailureCode.HISTORY_TORN, "the policy journal has a torn tail")
        frames = tuple(self._decode(item.payload) for item in scanned.frames)
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        seen: set[str] = set()
        coordinator_id = self._mutation_fence.coordinator_id()
        for index, frame in enumerate(frames, start=1):
            if type(frame.sequence) is not int or frame.sequence != index:
                raise _fail(ActivityPolicyStoreFailureCode.SEQUENCE_GAP, "the policy sequence has a gap")
            if frame.parent_anchor != anchors[index - 1]:
                raise _fail(ActivityPolicyStoreFailureCode.HISTORY_FORKED, "a policy frame does not extend its prefix")
            if frame.coordinator_id != coordinator_id:
                raise _fail(ActivityPolicyStoreFailureCode.COORDINATOR_MISMATCH, "the policy journal belongs to another coordinator")
            key = _ref_key(frame.decision_ref)
            if key in seen:
                raise _fail(ActivityPolicyStoreFailureCode.RECORD_DUPLICATE, "the policy journal repeats a decision")
            seen.add(key)
        return frames

    def _decode_production_provenance(
        self, payload: bytes
    ) -> ActivityProductionProvenanceFrame:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "a production provenance frame is not JSON",
            ) from exc
        fields = {
            "schema_version",
            "sequence",
            "coordinator_id",
            "parent_anchor",
            "provenance_ref",
            "provenance",
        }
        if type(data) is not dict or set(data) != fields:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "a production provenance frame has an invalid shape",
            )
        if data["schema_version"] != ACTIVITY_PROVENANCE_STORE_V1:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "a production provenance frame has an unknown schema",
            )
        try:
            reference = HashBoundRef.from_dict(data["provenance_ref"])
            provenance = activity_production_provenance_from_dict(data["provenance"])
            require_activity_provenance_ref(reference, provenance)
        except (ActivityProvenanceViolation, TypeError, ValueError) as exc:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "stored production provenance is invalid or substituted",
            ) from exc
        frame_bytes = _provenance_frame_payload(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            provenance_ref=reference,
            provenance=data["provenance"],
        )
        if frame_bytes != payload:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
                "a production provenance frame is not canonical",
            )
        return ActivityProductionProvenanceFrame(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            provenance_ref=reference,
            provenance=provenance,
            frame_bytes=frame_bytes,
        )

    def _production_frames(self) -> tuple[ActivityProductionProvenanceFrame, ...]:
        try:
            scanned = scan_journal(self.production_provenance_journal_path)
        except PersistenceViolation as exc:
            code = (
                ActivityPolicyStoreFailureCode.HISTORY_TORN
                if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL
                else ActivityPolicyStoreFailureCode.HISTORY_CORRUPT
            )
            raise _fail(code, "the production provenance journal could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _fail(
                ActivityPolicyStoreFailureCode.HISTORY_TORN,
                "the production provenance journal has a torn tail",
            )
        frames = tuple(self._decode_production_provenance(item.payload) for item in scanned.frames)
        anchors = _provenance_anchor_chain(tuple(item.frame_bytes for item in frames))
        seen: set[str] = set()
        coordinator_id = self._mutation_fence.coordinator_id()
        for index, frame in enumerate(frames, start=1):
            if type(frame.sequence) is not int or frame.sequence != index:
                raise _fail(
                    ActivityPolicyStoreFailureCode.SEQUENCE_GAP,
                    "the production provenance sequence has a gap",
                )
            if frame.parent_anchor != anchors[index - 1]:
                raise _fail(
                    ActivityPolicyStoreFailureCode.HISTORY_FORKED,
                    "a production provenance frame does not extend its prefix",
                )
            if frame.coordinator_id != coordinator_id:
                raise _fail(
                    ActivityPolicyStoreFailureCode.COORDINATOR_MISMATCH,
                    "the production provenance journal belongs to another coordinator",
                )
            key = _ref_key(frame.provenance_ref)
            if key in seen:
                raise _fail(
                    ActivityPolicyStoreFailureCode.RECORD_DUPLICATE,
                    "the production provenance journal repeats a record",
                )
            seen.add(key)
        return frames

    def current_anchor(self) -> str:
        return _anchor_chain(tuple(item.frame_bytes for item in self._frames()))[-1]

    def current_sequence(self) -> int:
        return len(self._frames())

    def append_production_provenance(
        self,
        provenance: ActivityProductionProvenance,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        require_activity_policy_evaluator(evaluator)
        _require_production_evaluator_binding(provenance, evaluator)
        reference = activity_provenance_ref(provenance)
        frames = self._production_frames()
        if any(_ref_key(item.provenance_ref) == _ref_key(reference) for item in frames):
            return reference
        anchors = _provenance_anchor_chain(tuple(item.frame_bytes for item in frames))
        payload = _provenance_frame_payload(
            sequence=len(frames) + 1,
            coordinator_id=self._mutation_fence.coordinator_id(),
            parent_anchor=anchors[-1],
            provenance_ref=reference,
            provenance=provenance.to_dict(),
        )
        if len(payload) > _MAX_JOURNAL_PAYLOAD:
            raise _fail(
                ActivityPolicyStoreFailureCode.RESOURCE_LIMIT_EXCEEDED,
                "a production provenance frame exceeds its ceiling",
            )
        append_journal_payload(
            self.production_provenance_journal_path, payload, ticket=ticket
        )
        return reference

    def append_decision(
        self,
        decision: ActivityPolicyDecision,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
        consumption: ActivityConsumptionProvenance,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(ticket, coordinator_id=self._mutation_fence.coordinator_id())
        require_activity_policy_evaluator(evaluator)
        validate_activity_policy_decision(decision)
        # The provenance the decision names must be the provenance stored beside
        # it: a frame carrying one record and pointing at another would let a
        # reader reconstruct a consuming party that never decided anything.
        _require_consumption_evaluator_binding(consumption, evaluator)
        if (
            decision.consumption_provenance_ref.to_dict()
            != activity_provenance_ref(consumption).to_dict()
        ):
            raise _fail(
                ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH,
                "the decision names another consumption provenance",
            )
        if (
            decision.declaration_id != evaluator.declaration.declaration_id
            or decision.configuration_id != evaluator.declaration.configuration_id
            or decision.actor_set_id != evaluator.actor_set.actor_set_id
            or decision.proof_id != evaluator.independence_proof.proof_id
        ):
            raise _fail(ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH, "decision names another authority")
        reference = activity_policy_decision_ref(decision)
        frames = self._frames()
        if any(_ref_key(item.decision_ref) == _ref_key(reference) for item in frames):
            # Already durable, and therefore already this exact decision: the
            # record is content-addressed, so "present" cannot mean "a different
            # decision under the same name". Two phases of one attempt asking the
            # same authority the same question in the same world get one answer,
            # and refusing the second would make the reference phase and the
            # governed phase unable to share a world — which they must, because
            # a manifest is only meaningful for the run it is measured against.
            return reference
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        payload = _frame_payload(
            sequence=len(frames) + 1,
            coordinator_id=self._mutation_fence.coordinator_id(),
            parent_anchor=anchors[-1],
            decision_ref=reference,
            declaration=evaluator.declaration.to_dict(),
            actor_set=evaluator.actor_set.to_dict(),
            independence_proof=evaluator.independence_proof.to_dict(),
            consumption_provenance=consumption.to_dict(),
            decision=decision.to_dict(),
        )
        if len(payload) > _MAX_JOURNAL_PAYLOAD:
            raise _fail(ActivityPolicyStoreFailureCode.RESOURCE_LIMIT_EXCEEDED, "a policy frame exceeds its ceiling")
        append_journal_payload(self.journal_path, payload, ticket=ticket)
        return reference

    def require_decision(
        self,
        reference: HashBoundRef,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
    ) -> ActivityPolicyDecision:
        require_activity_policy_evaluator(evaluator)
        if type(reference) is not HashBoundRef or reference.schema_id != SchemaVersion.ACTIVITY_POLICY_DECISION_V1.value:
            raise _fail(ActivityPolicyStoreFailureCode.TYPE_MISMATCH, "an exact policy decision reference is required")
        for frame in self._frames():
            if _ref_key(frame.decision_ref) != _ref_key(reference):
                continue
            if (
                frame.declaration != evaluator.declaration.to_dict()
                or frame.actor_set != evaluator.actor_set.to_dict()
                or frame.independence_proof != evaluator.independence_proof.to_dict()
            ):
                raise _fail(ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH, "stored decision names another authority")
            _require_consumption_evaluator_binding(
                frame.consumption_provenance, evaluator
            )
            try:
                restored = activity_policy_decision_from_dict(frame.decision, evaluator=evaluator)
            except (TypeError, ValueError) as exc:
                raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "stored policy decision is invalid") from exc
            if _ref_key(activity_policy_decision_ref(restored)) != _ref_key(reference):
                raise _fail(ActivityPolicyStoreFailureCode.HISTORY_CORRUPT, "restored policy decision changed identity")
            return restored
        raise _fail(ActivityPolicyStoreFailureCode.RECORD_UNKNOWN, "no durable policy decision carries this reference")

    def require_production_provenance(
        self,
        reference: HashBoundRef,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
    ) -> ActivityProductionProvenance:
        require_activity_policy_evaluator(evaluator)
        if (
            type(reference) is not HashBoundRef
            or reference.schema_id
            != SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1.value
        ):
            raise _fail(
                ActivityPolicyStoreFailureCode.TYPE_MISMATCH,
                "an exact production provenance reference is required",
            )
        for frame in self._production_frames():
            if _ref_key(frame.provenance_ref) != _ref_key(reference):
                continue
            _require_production_evaluator_binding(frame.provenance, evaluator)
            require_activity_provenance_ref(reference, frame.provenance)
            return frame.provenance
        raise _fail(
            ActivityPolicyStoreFailureCode.RECORD_UNKNOWN,
            "no durable production provenance carries this reference",
        )

    def require_production_provenance_for_activity(
        self,
        reference: HashBoundRef,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
        activity: RecordedActivity,
    ) -> ActivityProductionProvenance:
        provenance = self.require_production_provenance(
            reference, evaluator=evaluator
        )
        try:
            require_activity_production_subject_for_record(provenance, activity)
        except ActivityProvenanceViolation as exc:
            raise _fail(
                ActivityPolicyStoreFailureCode.CONFIGURATION_MISMATCH,
                "stored production provenance names another activity occurrence",
            ) from exc
        return provenance

    def require_consumption_provenance(
        self,
        decision_reference: HashBoundRef,
        *,
        evaluator: ConfiguredActivityPolicyEvaluator,
    ) -> ActivityConsumptionProvenance:
        require_activity_policy_evaluator(evaluator)
        for frame in self._frames():
            if _ref_key(frame.decision_ref) != _ref_key(decision_reference):
                continue
            _require_consumption_evaluator_binding(
                frame.consumption_provenance, evaluator
            )
            return frame.consumption_provenance
        raise _fail(
            ActivityPolicyStoreFailureCode.RECORD_UNKNOWN,
            "no durable policy decision carries this consumption provenance",
        )

    def recorded_decision_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(item.decision_ref for item in self._frames())

    def recorded_production_provenance_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(item.provenance_ref for item in self._production_frames())


__all__ = [
    "ACTIVITY_POLICY_JOURNAL_V1",
    "ACTIVITY_POLICY_STORE_V1",
    "ACTIVITY_PRODUCTION_PROVENANCE_JOURNAL_V1",
    "ACTIVITY_PROVENANCE_STORE_V1",
    "ActivityPolicyDecisionFrame",
    "ActivityProductionProvenanceFrame",
    "ActivityPolicyStoreFailureCode",
    "ActivityPolicyStoreViolation",
    "FileActivityPolicyStore",
]
