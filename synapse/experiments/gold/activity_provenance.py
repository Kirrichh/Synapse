"""Subject-bound production and consumption provenance for Stage 4 activities.

The activity-policy authority decides whether a recorded result may be used.
This owner records the facts that decision is about.  Production provenance is
therefore an exact occurrence record, not an actor-name receipt: it binds the
activity kind, canonical inputs, execution position, evaluator policy, exact
result, execution identity, trusted time and every actor present when the
effect happened.  Consumption provenance records the actors and concrete VM
adapter present when the result is served.

Both records are content addressed and have strict transport decoders.  Their
factories accept a sealed policy evaluator and derive policy, configuration,
actors and time from it; callers cannot state those authority-owned values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable
import hashlib

from .activities import (
    ACTIVITY_RESULT_BLOB_V1,
    ACTIVITY_RESULT_CODEC_V1_E1,
    ACTIVITY_RESULT_MEDIA_TYPE,
    ActivityInputs,
    ActivityKind,
    ActivityPosition,
    ActivityRecordContext,
    RecordedActivity,
    require_activity_result_ref,
    validate_activity_inputs,
    validate_recorded_activity,
)
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    AttemptId,
    ContractViolation,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    compute_record_id,
    record_id_reference_from_dict,
    validate_record_id,
)


ACTIVITY_PROVENANCE_CODEC_V1 = STABLE_CANONICAL_CODEC_ID
ACTIVITY_PROVENANCE_MEDIA_TYPE = "application/json"

_PRODUCTION_PROVENANCE_SEAL = object()
_CONSUMPTION_PROVENANCE_SEAL = object()
_ACTIVITY_PROVENANCE_AUTHORITY_SEAL = object()
_PRODUCTION_ACTOR_FIELDS = (
    "producer_actor",
    "recorder_actor",
    "worker_actor",
    "model_actor",
)
_CONSUMPTION_ACTOR_FIELDS = (
    "replay_executor_actor",
    "machine_adapter_actor",
    "consumer_actor",
)
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class ActivityProvenanceFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"


class ActivityProvenanceViolation(ValueError):
    """Typed fail-closed provenance error carrying no activity payload."""

    def __init__(self, failure_code: ActivityProvenanceFailureCode, detail: str) -> None:
        if type(failure_code) is not ActivityProvenanceFailureCode:
            raise TypeError("failure_code must be an exact ActivityProvenanceFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: ActivityProvenanceFailureCode, detail: str
) -> ActivityProvenanceViolation:
    return ActivityProvenanceViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=ACTIVITY_PROVENANCE_CODEC_V1,
    )


def _timestamp(value: object, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise _fail(ActivityProvenanceFailureCode.MALFORMED_TIMESTAMP, f"{field} must be exact UTC")
    if value.utcoffset() != timezone.utc.utcoffset(None):
        raise _fail(ActivityProvenanceFailureCode.MALFORMED_TIMESTAMP, f"{field} must be exact UTC")
    return value


def _timestamp_from_text(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise _fail(ActivityProvenanceFailureCode.MALFORMED_TIMESTAMP, f"{field} must be canonical UTC")
    try:
        return datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(
            ActivityProvenanceFailureCode.MALFORMED_TIMESTAMP,
            f"{field} must be canonical UTC",
        ) from exc


def _require_text(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > 128 or value.strip() != value:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, f"{field} is invalid")
    return value


@dataclass(frozen=True, init=False)
class ActivityProvenanceAuthority:
    """Sealed policy/configuration view from which provenance facts are derived."""

    configuration_id: RecordId
    actor_set_id: RecordId
    policy_version: str
    producer_actor: ActorIdentity
    recorder_actor: ActorIdentity
    worker_actor: ActorIdentity
    model_actor: ActorIdentity
    replay_executor_actor: ActorIdentity
    machine_adapter_actor: ActorIdentity
    consumer_actor: ActorIdentity
    _trusted_clock: Callable[[], datetime]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityProvenanceAuthority:
        raise TypeError("ActivityProvenanceAuthority is issued only by the policy authority")

    def trusted_now(self) -> datetime:
        if getattr(self, "_trusted_seal", None) is not _ACTIVITY_PROVENANCE_AUTHORITY_SEAL:
            raise _fail(ActivityProvenanceFailureCode.AUTHORITY_MISMATCH, "provenance authority is not sealed")
        return _timestamp(self._trusted_clock(), "trusted activity time")


def issue_activity_provenance_authority(
    *,
    configuration_id: RecordId,
    actor_set_id: RecordId,
    policy_version: str,
    producer_actor: ActorIdentity,
    recorder_actor: ActorIdentity,
    worker_actor: ActorIdentity,
    model_actor: ActorIdentity,
    replay_executor_actor: ActorIdentity,
    machine_adapter_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
    trusted_clock: Callable[[], datetime],
    _seal: object = None,
) -> ActivityProvenanceAuthority:
    if _seal is not _ACTIVITY_PROVENANCE_AUTHORITY_SEAL:
        raise _fail(ActivityProvenanceFailureCode.AUTHORITY_MISMATCH, "only the policy authority may issue provenance authority")
    if (
        type(configuration_id) is not RecordId
        or configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION
        or type(actor_set_id) is not RecordId
        or actor_set_id.domain is not IdentityDomain.ACTIVITY_POLICY_ACTOR_SET
        or not callable(trusted_clock)
    ):
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "provenance authority inputs are invalid")
    value = object.__new__(ActivityProvenanceAuthority)
    object.__setattr__(value, "configuration_id", configuration_id)
    object.__setattr__(value, "actor_set_id", actor_set_id)
    object.__setattr__(value, "policy_version", _require_text(policy_version, "policy_version"))
    for field, actor in (
        ("producer_actor", producer_actor),
        ("recorder_actor", recorder_actor),
        ("worker_actor", worker_actor),
        ("model_actor", model_actor),
        ("replay_executor_actor", replay_executor_actor),
        ("machine_adapter_actor", machine_adapter_actor),
        ("consumer_actor", consumer_actor),
    ):
        if type(actor) is not ActorIdentity:
            raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, f"{field} must be exact")
        object.__setattr__(value, field, actor)
    object.__setattr__(value, "_trusted_clock", trusted_clock)
    object.__setattr__(value, "_trusted_seal", _ACTIVITY_PROVENANCE_AUTHORITY_SEAL)
    return value


def require_activity_provenance_authority(value: object) -> ActivityProvenanceAuthority:
    if (
        type(value) is not ActivityProvenanceAuthority
        or getattr(value, "_trusted_seal", None) is not _ACTIVITY_PROVENANCE_AUTHORITY_SEAL
    ):
        raise _fail(ActivityProvenanceFailureCode.AUTHORITY_MISMATCH, "provenance authority is not sealed")
    return value


@dataclass(frozen=True, init=False)
class ActivityProductionProvenance:
    """The exact live occurrence that produced one recorded result."""

    schema_version: SchemaVersion
    provenance_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    kind: ActivityKind
    inputs: ActivityInputs
    position: ActivityPosition
    policy_version: str
    result_sha256: str
    result_ref: HashBoundRef
    result_codec: str
    run_id: RunId
    attempt_id: AttemptId
    repository_revision: RepositoryRevision
    environment_profile_id: str
    producer_component: str
    recorded_at_utc: datetime
    producer_actor: ActorIdentity
    recorder_actor: ActorIdentity
    worker_actor: ActorIdentity
    model_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityProductionProvenance:
        raise TypeError("ActivityProductionProvenance is produced only by its factory")

    def actors(self) -> tuple[ActorIdentity, ...]:
        validate_activity_production_provenance(self)
        return tuple(getattr(self, field) for field in _PRODUCTION_ACTOR_FIELDS)

    def to_dict(self) -> dict[str, object]:
        validate_activity_production_provenance(self)
        return _production_payload(self) | {"provenance_id": self.provenance_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_activity_production_provenance(self)
        return _canonical(_production_payload(self))


@dataclass(frozen=True, init=False)
class ActivityConsumptionProvenance:
    """The exact consuming parties and VM adapter used for a policy decision."""

    schema_version: SchemaVersion
    provenance_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    policy_version: str
    replay_executor_actor: ActorIdentity
    machine_adapter_actor: ActorIdentity
    machine_adapter_id: str
    consumer_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityConsumptionProvenance:
        raise TypeError("ActivityConsumptionProvenance is produced only by its factory")

    def actors(self) -> tuple[ActorIdentity, ...]:
        validate_activity_consumption_provenance(self)
        return tuple(getattr(self, field) for field in _CONSUMPTION_ACTOR_FIELDS)

    def to_dict(self) -> dict[str, object]:
        validate_activity_consumption_provenance(self)
        return _consumption_payload(self) | {"provenance_id": self.provenance_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_activity_consumption_provenance(self)
        return _canonical(_consumption_payload(self))


def _production_payload(value: ActivityProductionProvenance) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "codec": ACTIVITY_PROVENANCE_CODEC_V1,
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        "kind": value.kind.value,
        "inputs": value.inputs.to_dict(),
        "position": value.position.to_dict(),
        "policy_version": value.policy_version,
        "result_sha256": value.result_sha256,
        "result_ref": value.result_ref.to_dict(),
        "result_codec": value.result_codec,
        "run_id": value.run_id.to_dict(),
        "attempt_id": value.attempt_id.to_dict(),
        "repository_revision": value.repository_revision.to_dict(),
        "environment_profile_id": value.environment_profile_id,
        "producer_component": value.producer_component,
        "recorded_at_utc": value.recorded_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        **{field: getattr(value, field).to_dict() for field in _PRODUCTION_ACTOR_FIELDS},
    }


def _consumption_payload(value: ActivityConsumptionProvenance) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "codec": ACTIVITY_PROVENANCE_CODEC_V1,
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        "policy_version": value.policy_version,
        "machine_adapter_id": value.machine_adapter_id,
        **{field: getattr(value, field).to_dict() for field in _CONSUMPTION_ACTOR_FIELDS},
    }


def validate_activity_production_provenance(value: object) -> None:
    if (
        type(value) is not ActivityProductionProvenance
        or getattr(value, "_trusted_seal", None) is not _PRODUCTION_PROVENANCE_SEAL
    ):
        raise _fail(
            ActivityProvenanceFailureCode.TYPE_MISMATCH,
            "activity production provenance is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1:
        raise _fail(ActivityProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "production provenance schema is unknown")
    if (
        type(value.configuration_id) is not RecordId
        or value.configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION
        or type(value.actor_set_id) is not RecordId
        or value.actor_set_id.domain is not IdentityDomain.ACTIVITY_POLICY_ACTOR_SET
    ):
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production authority identities are invalid")
    validate_activity_inputs(value.inputs)
    if type(value.kind) is not ActivityKind or type(value.position) is not ActivityPosition:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production activity subject is invalid")
    _require_text(value.policy_version, "policy_version")
    _require_text(value.environment_profile_id, "environment_profile_id")
    _require_text(value.producer_component, "producer_component")
    if value.result_codec != ACTIVITY_RESULT_CODEC_V1_E1:
        raise _fail(ActivityProvenanceFailureCode.SUBJECT_MISMATCH, "production result codec is not fixed")
    if (
        type(value.result_ref) is not HashBoundRef
        or value.result_ref.kind is not RefKind.ARTIFACT
        or value.result_ref.schema_id != ACTIVITY_RESULT_BLOB_V1
        or value.result_ref.media_type != ACTIVITY_RESULT_MEDIA_TYPE
        or value.result_ref.sha256 != value.result_sha256
        or value.result_ref.ref_id != value.result_sha256
    ):
        raise _fail(ActivityProvenanceFailureCode.SUBJECT_MISMATCH, "production result reference differs from its digest")
    if type(value.run_id) is not RunId or type(value.attempt_id) is not AttemptId:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production run and attempt must be exact")
    if type(value.repository_revision) is not RepositoryRevision:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production repository revision must be exact")
    _timestamp(value.recorded_at_utc, "recorded_at_utc")
    for field in _PRODUCTION_ACTOR_FIELDS:
        if type(getattr(value, field)) is not ActorIdentity:
            raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, f"{field} must be exact")
    try:
        validate_record_id(value.provenance_id, canonical_bytes=_canonical(_production_payload(value)))
    except ContractViolation as exc:
        raise _fail(ActivityProvenanceFailureCode.IDENTITY_MISMATCH, "production provenance identity does not match") from exc


def validate_activity_consumption_provenance(value: object) -> None:
    if (
        type(value) is not ActivityConsumptionProvenance
        or getattr(value, "_trusted_seal", None) is not _CONSUMPTION_PROVENANCE_SEAL
    ):
        raise _fail(
            ActivityProvenanceFailureCode.TYPE_MISMATCH,
            "activity consumption provenance is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1:
        raise _fail(ActivityProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "consumption provenance schema is unknown")
    if (
        type(value.configuration_id) is not RecordId
        or value.configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION
        or type(value.actor_set_id) is not RecordId
        or value.actor_set_id.domain is not IdentityDomain.ACTIVITY_POLICY_ACTOR_SET
    ):
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "consumption authority identities are invalid")
    _require_text(value.policy_version, "policy_version")
    _require_text(value.machine_adapter_id, "machine_adapter_id")
    for field in _CONSUMPTION_ACTOR_FIELDS:
        if type(getattr(value, field)) is not ActorIdentity:
            raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, f"{field} must be exact")
    try:
        validate_record_id(value.provenance_id, canonical_bytes=_canonical(_consumption_payload(value)))
    except ContractViolation as exc:
        raise _fail(ActivityProvenanceFailureCode.IDENTITY_MISMATCH, "consumption provenance identity does not match") from exc


def record_activity_production_provenance(
    authority: ActivityProvenanceAuthority,
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    position: ActivityPosition,
    result: bytes,
    result_ref: HashBoundRef,
    context: ActivityRecordContext,
) -> ActivityProductionProvenance:
    """Record one exact live occurrence from sealed authority inputs."""

    authority = require_activity_provenance_authority(authority)
    if type(result) is not bytes:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "activity result must be exact bytes")
    if type(kind) is not ActivityKind or type(inputs) is not ActivityInputs or type(position) is not ActivityPosition:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "activity subject values must be exact")
    if type(context) is not ActivityRecordContext:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "activity execution context must be exact")
    result_sha256 = hashlib.sha256(result).hexdigest()
    require_activity_result_ref(result_ref, result=result, result_sha256=result_sha256)
    value = object.__new__(ActivityProductionProvenance)
    for field, item in (
        ("schema_version", SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1),
        ("configuration_id", authority.configuration_id),
        ("actor_set_id", authority.actor_set_id),
        ("kind", kind),
        ("inputs", inputs),
        ("position", position),
        ("policy_version", authority.policy_version),
        ("result_sha256", result_sha256),
        ("result_ref", result_ref),
        ("result_codec", ACTIVITY_RESULT_CODEC_V1_E1),
        ("run_id", context.run_id),
        ("attempt_id", context.attempt_id),
        ("repository_revision", context.repository_revision),
        ("environment_profile_id", context.environment_profile_id),
        ("producer_component", context.producer_component),
        ("recorded_at_utc", authority.trusted_now()),
    ):
        object.__setattr__(value, field, item)
    for field in _PRODUCTION_ACTOR_FIELDS:
        object.__setattr__(value, field, getattr(authority, field))
    object.__setattr__(value, "_trusted_seal", _PRODUCTION_PROVENANCE_SEAL)
    object.__setattr__(
        value,
        "provenance_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_PRODUCTION_PROVENANCE,
            canonical_bytes=_canonical(_production_payload(value)),
        ),
    )
    validate_activity_production_provenance(value)
    return value


def record_activity_consumption_provenance(
    authority: ActivityProvenanceAuthority,
    *,
    machine_adapter_id: str,
) -> ActivityConsumptionProvenance:
    """Record consuming actors derived from the sealed evaluator configuration."""

    authority = require_activity_provenance_authority(authority)
    adapter_id = _require_text(machine_adapter_id, "machine_adapter_id")
    value = object.__new__(ActivityConsumptionProvenance)
    for field, item in (
        ("schema_version", SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1),
        ("configuration_id", authority.configuration_id),
        ("actor_set_id", authority.actor_set_id),
        ("policy_version", authority.policy_version),
        ("machine_adapter_id", adapter_id),
    ):
        object.__setattr__(value, field, item)
    for field in _CONSUMPTION_ACTOR_FIELDS:
        object.__setattr__(value, field, getattr(authority, field))
    object.__setattr__(value, "_trusted_seal", _CONSUMPTION_PROVENANCE_SEAL)
    object.__setattr__(
        value,
        "provenance_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_CONSUMPTION_PROVENANCE,
            canonical_bytes=_canonical(_consumption_payload(value)),
        ),
    )
    validate_activity_consumption_provenance(value)
    return value


def activity_production_provenance_from_dict(value: object) -> ActivityProductionProvenance:
    expected = {
        "schema_version", "codec", "provenance_id", "configuration_id", "actor_set_id",
        "kind", "inputs", "position", "policy_version", "result_sha256", "result_ref",
        "result_codec", "run_id", "attempt_id", "repository_revision",
        "environment_profile_id", "producer_component", "recorded_at_utc",
        *_PRODUCTION_ACTOR_FIELDS,
    }
    if type(value) is not dict or set(value) != expected:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production provenance has an invalid shape")
    if value["schema_version"] != SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1.value:
        raise _fail(ActivityProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "production provenance schema is unknown")
    if value["codec"] != ACTIVITY_PROVENANCE_CODEC_V1:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production provenance codec is unknown")
    try:
        restored = object.__new__(ActivityProductionProvenance)
        fields = (
            ("schema_version", SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1),
            ("provenance_id", record_id_reference_from_dict(value["provenance_id"])),
            ("configuration_id", record_id_reference_from_dict(value["configuration_id"])),
            ("actor_set_id", record_id_reference_from_dict(value["actor_set_id"])),
            ("kind", ActivityKind(value["kind"])),
            ("inputs", ActivityInputs.from_dict(value["inputs"])),
            ("position", ActivityPosition.from_dict(value["position"])),
            ("policy_version", value["policy_version"]),
            ("result_sha256", value["result_sha256"]),
            ("result_ref", HashBoundRef.from_dict(value["result_ref"])),
            ("result_codec", value["result_codec"]),
            ("run_id", RunId.from_dict(value["run_id"])),
            ("attempt_id", AttemptId.from_dict(value["attempt_id"])),
            ("repository_revision", RepositoryRevision.from_dict(value["repository_revision"])),
            ("environment_profile_id", value["environment_profile_id"]),
            ("producer_component", value["producer_component"]),
            ("recorded_at_utc", _timestamp_from_text(value["recorded_at_utc"], "recorded_at_utc")),
        )
        for field, item in fields:
            object.__setattr__(restored, field, item)
        for field in _PRODUCTION_ACTOR_FIELDS:
            object.__setattr__(restored, field, ActorIdentity.from_dict(value[field]))
        object.__setattr__(restored, "_trusted_seal", _PRODUCTION_PROVENANCE_SEAL)
        validate_activity_production_provenance(restored)
        return restored
    except ActivityProvenanceViolation:
        raise
    except (ContractViolation, TypeError, ValueError, KeyError) as exc:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "production provenance transport is invalid") from exc


def activity_consumption_provenance_from_dict(value: object) -> ActivityConsumptionProvenance:
    expected = {
        "schema_version", "codec", "provenance_id", "configuration_id", "actor_set_id",
        "policy_version", "machine_adapter_id", *_CONSUMPTION_ACTOR_FIELDS,
    }
    if type(value) is not dict or set(value) != expected:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "consumption provenance has an invalid shape")
    if value["schema_version"] != SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1.value:
        raise _fail(ActivityProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "consumption provenance schema is unknown")
    if value["codec"] != ACTIVITY_PROVENANCE_CODEC_V1:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "consumption provenance codec is unknown")
    try:
        restored = object.__new__(ActivityConsumptionProvenance)
        for field, item in (
            ("schema_version", SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1),
            ("provenance_id", record_id_reference_from_dict(value["provenance_id"])),
            ("configuration_id", record_id_reference_from_dict(value["configuration_id"])),
            ("actor_set_id", record_id_reference_from_dict(value["actor_set_id"])),
            ("policy_version", value["policy_version"]),
            ("machine_adapter_id", value["machine_adapter_id"]),
        ):
            object.__setattr__(restored, field, item)
        for field in _CONSUMPTION_ACTOR_FIELDS:
            object.__setattr__(restored, field, ActorIdentity.from_dict(value[field]))
        object.__setattr__(restored, "_trusted_seal", _CONSUMPTION_PROVENANCE_SEAL)
        validate_activity_consumption_provenance(restored)
        return restored
    except ActivityProvenanceViolation:
        raise
    except (ContractViolation, TypeError, ValueError, KeyError) as exc:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "consumption provenance transport is invalid") from exc


def activity_provenance_ref(value: object) -> HashBoundRef:
    if type(value) is ActivityProductionProvenance:
        validate_activity_production_provenance(value)
        schema = SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1
    elif type(value) is ActivityConsumptionProvenance:
        validate_activity_consumption_provenance(value)
        schema = SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1
    else:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "an exact activity provenance record is required")
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.provenance_id.digest_sha256,
        schema_id=schema.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type=ACTIVITY_PROVENANCE_MEDIA_TYPE,
    )


def require_activity_provenance_ref(reference: object, provenance: object) -> HashBoundRef:
    expected = activity_provenance_ref(provenance)
    if type(reference) is not HashBoundRef or reference.to_dict() != expected.to_dict():
        raise _fail(ActivityProvenanceFailureCode.REFERENCE_MISMATCH, "provenance reference does not name this record")
    return reference


def require_activity_production_subject(
    provenance: ActivityProductionProvenance,
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    position: ActivityPosition,
    result_sha256: str,
    result_ref: HashBoundRef,
    context: ActivityRecordContext,
) -> None:
    """Refuse use of a production provenance for any other occurrence."""

    validate_activity_production_provenance(provenance)
    if type(context) is not ActivityRecordContext:
        raise _fail(ActivityProvenanceFailureCode.TYPE_MISMATCH, "activity execution context must be exact")
    actual = (
        kind,
        inputs,
        position,
        result_sha256,
        result_ref.to_dict() if type(result_ref) is HashBoundRef else None,
        context.run_id,
        context.attempt_id,
        context.repository_revision,
        context.environment_profile_id,
        context.producer_component,
    )
    expected = (
        provenance.kind,
        provenance.inputs,
        provenance.position,
        provenance.result_sha256,
        provenance.result_ref.to_dict(),
        provenance.run_id,
        provenance.attempt_id,
        provenance.repository_revision,
        provenance.environment_profile_id,
        provenance.producer_component,
    )
    if actual != expected or provenance.result_codec != ACTIVITY_RESULT_CODEC_V1_E1:
        raise _fail(ActivityProvenanceFailureCode.SUBJECT_MISMATCH, "production provenance names another activity occurrence")


def require_activity_production_subject_for_record(
    provenance: ActivityProductionProvenance,
    activity: RecordedActivity,
) -> None:
    """Verify a restored activity against the durable occurrence behind its ref."""

    validate_activity_production_provenance(provenance)
    validate_recorded_activity(activity)
    require_activity_provenance_ref(activity.production_provenance_ref, provenance)
    require_activity_production_subject(
        provenance,
        kind=activity.kind,
        inputs=activity.inputs,
        position=activity.position,
        result_sha256=activity.result_sha256,
        result_ref=activity.result_ref,
        context=ActivityRecordContext(
            run_id=activity.envelope.run_id,
            attempt_id=activity.envelope.attempt_id,
            repository_revision=activity.envelope.repository_revision,
            environment_profile_id=activity.envelope.environment_profile_id,
            producer_component=activity.envelope.producer_component,
        ),
    )
    if (
        activity.policy_version != provenance.policy_version
        or activity.recorded_at_utc != provenance.recorded_at_utc
        or activity.producer_actor != provenance.producer_actor
        or activity.recorder_actor != provenance.recorder_actor
    ):
        raise _fail(
            ActivityProvenanceFailureCode.SUBJECT_MISMATCH,
            "recorded activity differs from its production provenance",
        )


__all__ = [
    "ACTIVITY_PROVENANCE_CODEC_V1",
    "ACTIVITY_PROVENANCE_MEDIA_TYPE",
    "ActivityConsumptionProvenance",
    "ActivityProductionProvenance",
    "ActivityProvenanceAuthority",
    "ActivityProvenanceFailureCode",
    "ActivityProvenanceViolation",
    "activity_consumption_provenance_from_dict",
    "activity_production_provenance_from_dict",
    "activity_provenance_ref",
    "issue_activity_provenance_authority",
    "record_activity_consumption_provenance",
    "record_activity_production_provenance",
    "require_activity_production_subject",
    "require_activity_production_subject_for_record",
    "require_activity_provenance_authority",
    "require_activity_provenance_ref",
    "validate_activity_consumption_provenance",
    "validate_activity_production_provenance",
]
