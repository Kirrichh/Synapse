"""Trusted-platform provenance attestations for Stage 4 Gold Behaviors.

An attestation binds one exact Behavior content identity to the producer claim,
the exact repository revisions, the configured builder runtime, observed
policy/environment/tool inputs, source and verification evidence, and an
oracle result.  It records provenance only; it grants no admission,
correctness, execution, publication, or lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Callable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    ContentKey,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import (
    ActorIdentity,
    AttemptId,
    ContractViolation,
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RepositoryRevisionKind,
    RunId,
    SchemaVersion,
    Stage4AuthorityHandle,
    create_history_anchor,
    compute_record_id,
    record_id_from_dict,
    require_stage4_authority_handle,
    validate_history_anchor_extension,
    validate_record_id,
)
from .persistence import (
    ExclusiveStoreLock,
    append_journal_payload,
    ensure_directory,
    initialize_journal,
    scan_journal,
)


BUILDER_RUNTIME_IDENTITY_V1 = "synapse.stage4.gold.builder-runtime-identity/v1"
OBSERVED_EXTERNAL_INPUT_V1 = "synapse.stage4.gold.observed-external-input/v1"
ORACLE_OBSERVATION_V1 = "synapse.stage4.gold.oracle-observation/v1"
PLATFORM_OBSERVED_PROVENANCE_V1 = "synapse.stage4.gold.platform-observed-provenance/v1"
BEHAVIOR_ATTESTATION_MEDIA_TYPE_V1 = (
    "application/vnd.synapse.stage4.behavior-attestation+json"
)
BEHAVIOR_ATTESTATION_REF_PREFIX_V1 = (
    "synapse.stage4.gold.behavior-attestation.v1:"
)
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
BEHAVIOR_ATTESTATION_JOURNAL_NAME_V1 = "behavior-attestations-v1.journal"
BEHAVIOR_ATTESTATION_LOCK_NAME_V1 = "behavior-attestations-v1.lock"

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSION_RE = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z"
)
_TRUSTED_ATTESTATION_SEAL = object()
_TRUSTED_ATTESTER_SEAL = object()
_TRUSTED_OBSERVATION_SEAL = object()
_TRUSTED_STORE_SEAL = object()


class ProvenanceFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    BUILDER_MISMATCH = "BUILDER_MISMATCH"
    ATTESTER_MISMATCH = "ATTESTER_MISMATCH"
    EXTERNAL_INPUT_MISSING = "EXTERNAL_INPUT_MISSING"
    EXTERNAL_INPUT_DUPLICATE = "EXTERNAL_INPUT_DUPLICATE"
    REF_KIND_MISMATCH = "REF_KIND_MISMATCH"
    ORACLE_BINDING_MISMATCH = "ORACLE_BINDING_MISMATCH"
    ATTESTATION_ID_MISMATCH = "ATTESTATION_ID_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    HISTORY_ANCHOR_REQUIRED = "HISTORY_ANCHOR_REQUIRED"
    HISTORY_ROLLBACK = "HISTORY_ROLLBACK"
    ATTESTATION_NOT_ADMITTED = "ATTESTATION_NOT_ADMITTED"
    ATTESTATION_REVOKED = "ATTESTATION_REVOKED"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"


class ProvenanceViolation(ValueError):
    def __init__(self, failure_code: ProvenanceFailureCode, detail: str) -> None:
        if type(failure_code) is not ProvenanceFailureCode:
            raise TypeError("failure_code must be exact ProvenanceFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ProvenanceFailureCode, detail: str) -> ProvenanceViolation:
    return ProvenanceViolation(code, detail)


def _exact_dict(value: object, fields: tuple[str, ...], name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be an exact dict")
    if any(type(key) is not str for key in value) or set(value) != set(fields):
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, f"{name} fields are invalid")
    return value


def _exact_list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be an exact list")
    return value


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(ProvenanceFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _version(value: object, name: str) -> str:
    if type(value) is not str or _VERSION_RE.fullmatch(value) is None:
        raise _fail(ProvenanceFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _require_commit(value: object, name: str) -> RepositoryRevision:
    if type(value) is not RepositoryRevision:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be RepositoryRevision")
    try:
        snapshot = RepositoryRevision.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.REVISION_REQUIRED, f"{name} is malformed") from exc
    if snapshot.kind is not RepositoryRevisionKind.GIT_COMMIT or snapshot.git_sha is None:
        raise _fail(ProvenanceFailureCode.REVISION_REQUIRED, f"{name} must be an exact Git commit")
    return snapshot


def _actor(value: object, name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be ActorIdentity")
    try:
        return ActorIdentity.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.INVALID_IDENTIFIER, f"{name} is invalid") from exc


def _ref(value: object, expected_kind: RefKind, name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be HashBoundRef")
    try:
        snapshot = HashBoundRef.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.REF_KIND_MISMATCH, f"{name} is invalid") from exc
    if snapshot.kind is not expected_kind:
        raise _fail(ProvenanceFailureCode.REF_KIND_MISMATCH, f"{name} has the wrong kind")
    return snapshot


def _refs(value: object, expected_kind: RefKind, name: str, *, nonempty: bool) -> tuple[HashBoundRef, ...]:
    if type(value) not in (tuple, list):
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    items = tuple(_ref(item, expected_kind, name) for item in value)
    if nonempty and not items:
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_MISSING, f"{name} must not be empty")
    if len({item.ref_id for item in items}) != len(items) or len({item.sha256 for item in items}) != len(items):
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_DUPLICATE, f"{name} contains duplicate refs")
    return tuple(sorted(items, key=lambda item: item.ref_id))


def _format_timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise _fail(ProvenanceFailureCode.INVALID_TIMESTAMP, "generated_at must be timezone-aware UTC")
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise _fail(ProvenanceFailureCode.INVALID_TIMESTAMP, "generated_at must be an exact string")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.INVALID_TIMESTAMP, "generated_at is not canonical UTC") from exc
    if _format_timestamp(parsed) != value:
        raise _fail(ProvenanceFailureCode.INVALID_TIMESTAMP, "generated_at is not canonical UTC")
    return parsed


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _decode(value: bytes) -> object:
    return decode_stage4_canonical_bytes(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _configuration_id(value: object) -> RecordId:
    if type(value) is not RecordId or value.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _fail(
            ProvenanceFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "configuration_id must be an authority configuration identity",
        )
    return value


def _handle(
    value: Stage4AuthorityHandle,
    *,
    expected: Stage4AuthorityHandle | None = None,
):
    try:
        return require_stage4_authority_handle(value, expected_handle=expected)
    except ContractViolation as exc:
        raise _fail(ProvenanceFailureCode.WRONG_AUTHORITY_HANDLE, "authority handle is invalid") from exc


class ExternalInputKind(str, Enum):
    POLICY = "POLICY"
    ENVIRONMENT = "ENVIRONMENT"
    TOOL = "TOOL"


@dataclass(frozen=True)
class BuilderRuntimeIdentity:
    schema_version: str
    builder_actor_identity: ActorIdentity
    repository_revision: RepositoryRevision
    executable_ref: HashBoundRef
    runtime_version: str

    def __post_init__(self) -> None:
        _validate_builder(self)

    def to_dict(self) -> dict[str, object]:
        _validate_builder(self)
        return {
            "schema_version": self.schema_version,
            "builder_actor_identity": self.builder_actor_identity.to_dict(),
            "repository_revision": self.repository_revision.to_dict(),
            "executable_ref": self.executable_ref.to_dict(),
            "runtime_version": self.runtime_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> BuilderRuntimeIdentity:
        data = _exact_dict(
            value,
            ("schema_version", "builder_actor_identity", "repository_revision", "executable_ref", "runtime_version"),
            "builder_runtime_identity",
        )
        return cls(
            data["schema_version"],
            ActorIdentity.from_dict(data["builder_actor_identity"]),
            RepositoryRevision.from_dict(data["repository_revision"]),
            HashBoundRef.from_dict(data["executable_ref"]),
            data["runtime_version"],
        )


def _validate_builder(value: BuilderRuntimeIdentity) -> None:
    if type(value) is not BuilderRuntimeIdentity:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "builder runtime must be exact")
    if value.schema_version != BUILDER_RUNTIME_IDENTITY_V1 or type(value.schema_version) is not str:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "builder runtime schema is unknown")
    _actor(value.builder_actor_identity, "builder_actor_identity")
    _require_commit(value.repository_revision, "builder.repository_revision")
    _ref(value.executable_ref, RefKind.ARTIFACT, "builder.executable_ref")
    _version(value.runtime_version, "builder.runtime_version")


@dataclass(frozen=True)
class ObservedExternalInput:
    schema_version: str
    kind: ExternalInputKind
    name: str
    version: str
    ref: HashBoundRef

    def __post_init__(self) -> None:
        _validate_external_input(self)

    def to_dict(self) -> dict[str, object]:
        _validate_external_input(self)
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "name": self.name,
            "version": self.version,
            "ref": self.ref.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ObservedExternalInput:
        data = _exact_dict(value, ("schema_version", "kind", "name", "version", "ref"), "external_input")
        try:
            kind = ExternalInputKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "external input kind is unknown") from exc
        return cls(data["schema_version"], kind, data["name"], data["version"], HashBoundRef.from_dict(data["ref"]))


def _validate_external_input(value: ObservedExternalInput, expected_kind: ExternalInputKind | None = None) -> None:
    if type(value) is not ObservedExternalInput:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "external input must be exact")
    if value.schema_version != OBSERVED_EXTERNAL_INPUT_V1 or type(value.schema_version) is not str:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "external input schema is unknown")
    if type(value.kind) is not ExternalInputKind or (expected_kind is not None and value.kind is not expected_kind):
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "external input kind mismatch")
    _safe_id(value.name, "external_input.name")
    _version(value.version, "external_input.version")
    expected_ref_kind = RefKind.CONTRACT_CONDITION if value.kind is ExternalInputKind.POLICY else RefKind.ARTIFACT
    _ref(value.ref, expected_ref_kind, "external_input.ref")


def _external_inputs(value: object, kind: ExternalInputKind, name: str) -> tuple[ObservedExternalInput, ...]:
    if type(value) not in (tuple, list):
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    items: list[ObservedExternalInput] = []
    for item in value:
        if type(item) is not ObservedExternalInput:
            raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, f"{name} entry must be exact")
        snapshot = ObservedExternalInput.from_dict(item.to_dict())
        _validate_external_input(snapshot, kind)
        items.append(snapshot)
    if not items:
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_MISSING, f"{name} must not be empty")
    if len({item.name for item in items}) != len(items) or len({item.ref.sha256 for item in items}) != len(items):
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_DUPLICATE, f"{name} contains duplicates")
    return tuple(sorted(items, key=lambda item: item.name))


@dataclass(frozen=True)
class OracleObservation:
    schema_version: str
    oracle_identity: ActorIdentity
    verified_repository_revision: RepositoryRevision
    task_contract_ref: HashBoundRef
    result_ref: HashBoundRef

    def __post_init__(self) -> None:
        _validate_oracle(self)

    def to_dict(self) -> dict[str, object]:
        _validate_oracle(self)
        return {
            "schema_version": self.schema_version,
            "oracle_identity": self.oracle_identity.to_dict(),
            "verified_repository_revision": self.verified_repository_revision.to_dict(),
            "task_contract_ref": self.task_contract_ref.to_dict(),
            "result_ref": self.result_ref.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> OracleObservation:
        data = _exact_dict(
            value,
            ("schema_version", "oracle_identity", "verified_repository_revision", "task_contract_ref", "result_ref"),
            "oracle_observation",
        )
        return cls(
            data["schema_version"],
            ActorIdentity.from_dict(data["oracle_identity"]),
            RepositoryRevision.from_dict(data["verified_repository_revision"]),
            HashBoundRef.from_dict(data["task_contract_ref"]),
            HashBoundRef.from_dict(data["result_ref"]),
        )


def _validate_oracle(value: OracleObservation) -> None:
    if type(value) is not OracleObservation:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "oracle observation must be exact")
    if value.schema_version != ORACLE_OBSERVATION_V1 or type(value.schema_version) is not str:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "oracle observation schema is unknown")
    _actor(value.oracle_identity, "oracle_identity")
    _require_commit(value.verified_repository_revision, "oracle.verified_repository_revision")
    _ref(value.task_contract_ref, RefKind.CONTRACT_CONDITION, "oracle.task_contract_ref")
    _ref(value.result_ref, RefKind.SOURCE_EVIDENCE, "oracle.result_ref")


@dataclass(frozen=True, init=False)
class PlatformObservedProvenance:
    schema_version: str
    configuration_id: RecordId
    repository_revision: RepositoryRevision
    base_revision: RepositoryRevision
    task_contract_ref: HashBoundRef
    policy_inputs: tuple[ObservedExternalInput, ...]
    environment_inputs: tuple[ObservedExternalInput, ...]
    tool_inputs: tuple[ObservedExternalInput, ...]
    source_refs: tuple[HashBoundRef, ...]
    verification_refs: tuple[HashBoundRef, ...]
    oracle_observation: OracleObservation
    _authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PlatformObservedProvenance:
        raise TypeError("PlatformObservedProvenance is created only by PlatformAttester.observe")


def _seal_platform_observation(
    *,
    authority_handle: Stage4AuthorityHandle,
    repository_revision: RepositoryRevision,
    base_revision: RepositoryRevision,
    task_contract_ref: HashBoundRef,
    policy_inputs: object,
    environment_inputs: object,
    tool_inputs: object,
    source_refs: object,
    verification_refs: object,
    oracle_observation: OracleObservation,
) -> PlatformObservedProvenance:
    configuration = _handle(authority_handle)
    repository = _require_commit(repository_revision, "repository_revision")
    task = _ref(task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    oracle = OracleObservation.from_dict(oracle_observation.to_dict())
    if oracle.verified_repository_revision != repository or oracle.task_contract_ref != task:
        raise _fail(
            ProvenanceFailureCode.ORACLE_BINDING_MISMATCH,
            "oracle observation must bind the exact repository commit and task contract",
        )
    result = object.__new__(PlatformObservedProvenance)
    object.__setattr__(result, "schema_version", PLATFORM_OBSERVED_PROVENANCE_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "repository_revision", repository)
    object.__setattr__(result, "base_revision", _require_commit(base_revision, "base_revision"))
    object.__setattr__(result, "task_contract_ref", task)
    object.__setattr__(result, "policy_inputs", _external_inputs(policy_inputs, ExternalInputKind.POLICY, "policy_inputs"))
    object.__setattr__(result, "environment_inputs", _external_inputs(environment_inputs, ExternalInputKind.ENVIRONMENT, "environment_inputs"))
    object.__setattr__(result, "tool_inputs", _external_inputs(tool_inputs, ExternalInputKind.TOOL, "tool_inputs"))
    object.__setattr__(result, "source_refs", _refs(source_refs, RefKind.SOURCE_EVIDENCE, "source_refs", nonempty=True))
    object.__setattr__(result, "verification_refs", _refs(verification_refs, RefKind.SOURCE_EVIDENCE, "verification_refs", nonempty=True))
    object.__setattr__(result, "oracle_observation", oracle)
    object.__setattr__(result, "_authority_handle", authority_handle)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_OBSERVATION_SEAL)
    validate_platform_observed_provenance(result, authority_handle=authority_handle)
    return result


def validate_platform_observed_provenance(
    value: PlatformObservedProvenance,
    *,
    authority_handle: Stage4AuthorityHandle,
) -> None:
    configuration = _handle(authority_handle)
    if (
        type(value) is not PlatformObservedProvenance
        or getattr(value, "_trusted_seal", None) is not _TRUSTED_OBSERVATION_SEAL
    ):
        raise _fail(ProvenanceFailureCode.TRUSTED_OBJECT_FORGED, "platform observation is not sealed")
    if value.schema_version != PLATFORM_OBSERVED_PROVENANCE_V1:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "platform observation schema is unknown")
    if value._authority_handle is not authority_handle:
        raise _fail(ProvenanceFailureCode.WRONG_AUTHORITY_HANDLE, "platform observation belongs to another handle")
    if value.configuration_id != configuration.configuration_id:
        raise _fail(ProvenanceFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "platform observation configuration changed")
    repository = _require_commit(value.repository_revision, "repository_revision")
    _require_commit(value.base_revision, "base_revision")
    task = _ref(value.task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    _external_inputs(value.policy_inputs, ExternalInputKind.POLICY, "policy_inputs")
    _external_inputs(value.environment_inputs, ExternalInputKind.ENVIRONMENT, "environment_inputs")
    _external_inputs(value.tool_inputs, ExternalInputKind.TOOL, "tool_inputs")
    _refs(value.source_refs, RefKind.SOURCE_EVIDENCE, "source_refs", nonempty=True)
    _refs(value.verification_refs, RefKind.SOURCE_EVIDENCE, "verification_refs", nonempty=True)
    _validate_oracle(value.oracle_observation)
    if (
        value.oracle_observation.verified_repository_revision != repository
        or value.oracle_observation.task_contract_ref != task
    ):
        raise _fail(ProvenanceFailureCode.ORACLE_BINDING_MISMATCH, "oracle observation binding changed")


@dataclass(frozen=True, init=False)
class BehaviorAttestation:
    schema_version: SchemaVersion
    configuration_id: RecordId
    subject_content_key: ContentKey
    producer_run_id: RunId
    producer_attempt_id: AttemptId
    producer_actor_ids: tuple[ActorIdentity, ...]
    attester_identity: ActorIdentity
    builder_runtime_identity: BuilderRuntimeIdentity
    repository_revision: RepositoryRevision
    base_revision: RepositoryRevision
    task_contract_ref: HashBoundRef
    policy_inputs: tuple[ObservedExternalInput, ...]
    environment_inputs: tuple[ObservedExternalInput, ...]
    tool_inputs: tuple[ObservedExternalInput, ...]
    source_refs: tuple[HashBoundRef, ...]
    verification_refs: tuple[HashBoundRef, ...]
    oracle_observation: OracleObservation
    generated_at: datetime
    attestation_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorAttestation:
        raise TypeError("BehaviorAttestation is created only by a configured PlatformAttester")

    def to_dict(self) -> dict[str, object]:
        validate_behavior_attestation(self)
        return {**_attestation_payload(self), "attestation_id": self.attestation_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        authority_handle: Stage4AuthorityHandle,
        expected_subject_content_key: ContentKey,
        expected_builder_runtime_identity: BuilderRuntimeIdentity,
        expected_attester_identity: ActorIdentity,
        expected_repository_revision: RepositoryRevision,
    ) -> BehaviorAttestation:
        return behavior_attestation_from_dict(
            value,
            authority_handle=authority_handle,
            expected_subject_content_key=expected_subject_content_key,
            expected_builder_runtime_identity=expected_builder_runtime_identity,
            expected_attester_identity=expected_attester_identity,
            expected_repository_revision=expected_repository_revision,
        )


def _attestation_payload(value: BehaviorAttestation) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "subject_content_key": value.subject_content_key.to_dict(),
        "producer_run_id": value.producer_run_id.to_dict(),
        "producer_attempt_id": value.producer_attempt_id.to_dict(),
        "producer_actor_ids": [item.to_dict() for item in value.producer_actor_ids],
        "attester_identity": value.attester_identity.to_dict(),
        "builder_runtime_identity": value.builder_runtime_identity.to_dict(),
        "repository_revision": value.repository_revision.to_dict(),
        "base_revision": value.base_revision.to_dict(),
        "task_contract_ref": value.task_contract_ref.to_dict(),
        "policy_inputs": [item.to_dict() for item in value.policy_inputs],
        "environment_inputs": [item.to_dict() for item in value.environment_inputs],
        "tool_inputs": [item.to_dict() for item in value.tool_inputs],
        "source_refs": [item.to_dict() for item in value.source_refs],
        "verification_refs": [item.to_dict() for item in value.verification_refs],
        "oracle_observation": value.oracle_observation.to_dict(),
        "generated_at": _format_timestamp(value.generated_at),
    }


def _producer_actors(value: object) -> tuple[ActorIdentity, ...]:
    if type(value) not in (tuple, list):
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "producer_actor_ids must be tuple or list")
    result = tuple(_actor(item, "producer_actor_ids") for item in value)
    if not result:
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_MISSING, "producer_actor_ids must not be empty")
    if len({item.value for item in result}) != len(result):
        raise _fail(ProvenanceFailureCode.EXTERNAL_INPUT_DUPLICATE, "producer_actor_ids contains duplicates")
    return tuple(sorted(result, key=lambda item: item.value))


def _seal_attestation(
    *,
    configuration_id: RecordId,
    subject_content_key: ContentKey,
    producer_run_id: RunId,
    producer_attempt_id: AttemptId,
    producer_actor_ids: object,
    attester_identity: ActorIdentity,
    builder_runtime_identity: BuilderRuntimeIdentity,
    repository_revision: RepositoryRevision,
    base_revision: RepositoryRevision,
    task_contract_ref: HashBoundRef,
    policy_inputs: object,
    environment_inputs: object,
    tool_inputs: object,
    source_refs: object,
    verification_refs: object,
    oracle_observation: OracleObservation,
    generated_at: datetime,
) -> BehaviorAttestation:
    configuration_snapshot = _configuration_id(configuration_id)
    if type(subject_content_key) is not ContentKey:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "subject_content_key must be exact ContentKey")
    subject_content_key.to_dict()
    if type(producer_run_id) is not RunId or type(producer_attempt_id) is not AttemptId:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "producer run and attempt IDs must be exact")
    run_snapshot = RunId.from_dict(producer_run_id.to_dict())
    attempt_snapshot = AttemptId.from_dict(producer_attempt_id.to_dict())
    actor_snapshots = _producer_actors(producer_actor_ids)
    attester_snapshot = _actor(attester_identity, "attester_identity")
    builder_snapshot = BuilderRuntimeIdentity.from_dict(builder_runtime_identity.to_dict())
    participating = {item.value for item in actor_snapshots}
    if attester_snapshot.value in participating or builder_snapshot.builder_actor_identity.value in participating:
        raise _fail(
            ProvenanceFailureCode.ATTESTER_MISMATCH,
            "producer cannot be the platform attester or builder",
        )
    revision_snapshot = _require_commit(repository_revision, "repository_revision")
    base_snapshot = _require_commit(base_revision, "base_revision")
    if builder_snapshot.repository_revision != revision_snapshot:
        raise _fail(ProvenanceFailureCode.BUILDER_MISMATCH, "builder is not bound to the verified repository commit")
    task_snapshot = _ref(task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    policy_snapshots = _external_inputs(policy_inputs, ExternalInputKind.POLICY, "policy_inputs")
    environment_snapshots = _external_inputs(environment_inputs, ExternalInputKind.ENVIRONMENT, "environment_inputs")
    tool_snapshots = _external_inputs(tool_inputs, ExternalInputKind.TOOL, "tool_inputs")
    source_snapshots = _refs(source_refs, RefKind.SOURCE_EVIDENCE, "source_refs", nonempty=True)
    verification_snapshots = _refs(verification_refs, RefKind.SOURCE_EVIDENCE, "verification_refs", nonempty=True)
    oracle_snapshot = OracleObservation.from_dict(oracle_observation.to_dict())
    if oracle_snapshot.oracle_identity.value in participating:
        raise _fail(
            ProvenanceFailureCode.ORACLE_BINDING_MISMATCH,
            "producer cannot be the bound oracle",
        )
    if oracle_snapshot.verified_repository_revision != revision_snapshot:
        raise _fail(ProvenanceFailureCode.ORACLE_BINDING_MISMATCH, "oracle result is not bound to the verified commit")
    if oracle_snapshot.task_contract_ref != task_snapshot:
        raise _fail(ProvenanceFailureCode.ORACLE_BINDING_MISMATCH, "oracle result is not bound to the task contract")
    generated_snapshot = _parse_timestamp(_format_timestamp(generated_at))
    result = object.__new__(BehaviorAttestation)
    object.__setattr__(result, "schema_version", SchemaVersion.BEHAVIOR_ATTESTATION_V1)
    object.__setattr__(result, "configuration_id", configuration_snapshot)
    object.__setattr__(result, "subject_content_key", subject_content_key)
    object.__setattr__(result, "producer_run_id", run_snapshot)
    object.__setattr__(result, "producer_attempt_id", attempt_snapshot)
    object.__setattr__(result, "producer_actor_ids", actor_snapshots)
    object.__setattr__(result, "attester_identity", attester_snapshot)
    object.__setattr__(result, "builder_runtime_identity", builder_snapshot)
    object.__setattr__(result, "repository_revision", revision_snapshot)
    object.__setattr__(result, "base_revision", base_snapshot)
    object.__setattr__(result, "task_contract_ref", task_snapshot)
    object.__setattr__(result, "policy_inputs", policy_snapshots)
    object.__setattr__(result, "environment_inputs", environment_snapshots)
    object.__setattr__(result, "tool_inputs", tool_snapshots)
    object.__setattr__(result, "source_refs", source_snapshots)
    object.__setattr__(result, "verification_refs", verification_snapshots)
    object.__setattr__(result, "oracle_observation", oracle_snapshot)
    object.__setattr__(result, "generated_at", generated_snapshot)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_ATTESTATION_SEAL)
    identity_bytes = _canonical(_attestation_payload(result))
    object.__setattr__(result, "attestation_id", compute_record_id(domain=IdentityDomain.BEHAVIOR_ATTESTATION, canonical_bytes=identity_bytes))
    validate_behavior_attestation(result)
    return result


def validate_behavior_attestation(
    value: BehaviorAttestation,
    *,
    expected_configuration_id: RecordId | None = None,
    expected_subject_content_key: ContentKey | None = None,
    expected_builder_runtime_identity: BuilderRuntimeIdentity | None = None,
    expected_attester_identity: ActorIdentity | None = None,
    expected_repository_revision: RepositoryRevision | None = None,
) -> None:
    if type(value) is not BehaviorAttestation or getattr(value, "_trusted_seal", None) is not _TRUSTED_ATTESTATION_SEAL:
        raise _fail(ProvenanceFailureCode.TRUSTED_OBJECT_FORGED, "attestation is not platform sealed")
    if value.schema_version is not SchemaVersion.BEHAVIOR_ATTESTATION_V1:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "attestation schema is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    if type(value.subject_content_key) is not ContentKey:
        raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "subject content key is invalid")
    value.subject_content_key.to_dict()
    RunId.from_dict(value.producer_run_id.to_dict())
    AttemptId.from_dict(value.producer_attempt_id.to_dict())
    _producer_actors(value.producer_actor_ids)
    _actor(value.attester_identity, "attester_identity")
    _validate_builder(value.builder_runtime_identity)
    revision = _require_commit(value.repository_revision, "repository_revision")
    _require_commit(value.base_revision, "base_revision")
    if value.builder_runtime_identity.repository_revision != revision:
        raise _fail(ProvenanceFailureCode.BUILDER_MISMATCH, "builder commit changed after attestation")
    _ref(value.task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    _external_inputs(value.policy_inputs, ExternalInputKind.POLICY, "policy_inputs")
    _external_inputs(value.environment_inputs, ExternalInputKind.ENVIRONMENT, "environment_inputs")
    _external_inputs(value.tool_inputs, ExternalInputKind.TOOL, "tool_inputs")
    _refs(value.source_refs, RefKind.SOURCE_EVIDENCE, "source_refs", nonempty=True)
    _refs(value.verification_refs, RefKind.SOURCE_EVIDENCE, "verification_refs", nonempty=True)
    _validate_oracle(value.oracle_observation)
    participating = {item.value for item in value.producer_actor_ids}
    if (
        value.attester_identity.value in participating
        or value.builder_runtime_identity.builder_actor_identity.value in participating
        or value.oracle_observation.oracle_identity.value in participating
    ):
        raise _fail(ProvenanceFailureCode.ATTESTER_MISMATCH, "producer overlaps trusted provenance actor")
    if value.oracle_observation.verified_repository_revision != revision or value.oracle_observation.task_contract_ref != value.task_contract_ref:
        raise _fail(ProvenanceFailureCode.ORACLE_BINDING_MISMATCH, "oracle binding changed after attestation")
    _parse_timestamp(_format_timestamp(value.generated_at))
    identity_bytes = _canonical(_attestation_payload(value))
    if type(value.attestation_id) is not RecordId or value.attestation_id.domain is not IdentityDomain.BEHAVIOR_ATTESTATION:
        raise _fail(ProvenanceFailureCode.ATTESTATION_ID_MISMATCH, "attestation identity domain is invalid")
    try:
        validate_record_id(value.attestation_id, canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.ATTESTATION_ID_MISMATCH, "attestation identity does not match its content") from exc
    if expected_subject_content_key is not None and value.subject_content_key.value != expected_subject_content_key.value:
        raise _fail(ProvenanceFailureCode.SUBJECT_MISMATCH, "attestation subject differs from consumer subject")
    if expected_builder_runtime_identity is not None and value.builder_runtime_identity.to_dict() != expected_builder_runtime_identity.to_dict():
        raise _fail(ProvenanceFailureCode.BUILDER_MISMATCH, "attestation builder differs from trusted consumer configuration")
    if expected_attester_identity is not None and value.attester_identity != _actor(expected_attester_identity, "expected_attester_identity"):
        raise _fail(ProvenanceFailureCode.ATTESTER_MISMATCH, "attester differs from trusted consumer configuration")
    if expected_repository_revision is not None and value.repository_revision != _require_commit(expected_repository_revision, "expected_repository_revision"):
        raise _fail(ProvenanceFailureCode.REVISION_MISMATCH, "attestation repository revision differs from consumer revision")
    if expected_configuration_id is not None and configuration_id != _configuration_id(expected_configuration_id):
        raise _fail(ProvenanceFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "attestation configuration differs from consumer configuration")


class PlatformAttester:
    """Configured trusted boundary; producer calls cannot select its identity."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_ATTESTER_SEAL or kwargs or len(args) != 3:
            raise TypeError("PlatformAttester is created only by configure_platform_attester")
        authority_handle, builder_runtime_identity, trusted_clock = args
        configuration = _handle(authority_handle)
        if not callable(trusted_clock):
            raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
        builder = BuilderRuntimeIdentity.from_dict(builder_runtime_identity.to_dict())
        if builder.builder_actor_identity != configuration.builder_actor:
            raise _fail(ProvenanceFailureCode.BUILDER_MISMATCH, "builder actor differs from authority configuration")
        self._authority_handle = authority_handle
        self._attester_identity = ActorIdentity.from_dict(configuration.platform_attester_actor.to_dict())
        self._builder_runtime_identity = builder
        self._trusted_clock = trusted_clock

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    @property
    def attester_identity(self) -> ActorIdentity:
        return _actor(self._attester_identity, "attester_identity")

    @property
    def builder_runtime_identity(self) -> BuilderRuntimeIdentity:
        return BuilderRuntimeIdentity.from_dict(self._builder_runtime_identity.to_dict())

    @property
    def configuration_id(self) -> RecordId:
        return _handle(self._authority_handle).configuration_id

    def observe(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        repository_revision: RepositoryRevision,
        base_revision: RepositoryRevision,
        task_contract_ref: HashBoundRef,
        policy_inputs: object,
        environment_inputs: object,
        tool_inputs: object,
        source_refs: object,
        verification_refs: object,
        oracle_observation: OracleObservation,
    ) -> PlatformObservedProvenance:
        self.require_handle(authority_handle)
        return _seal_platform_observation(
            authority_handle=authority_handle,
            repository_revision=repository_revision,
            base_revision=base_revision,
            task_contract_ref=task_contract_ref,
            policy_inputs=policy_inputs,
            environment_inputs=environment_inputs,
            tool_inputs=tool_inputs,
            source_refs=source_refs,
            verification_refs=verification_refs,
            oracle_observation=oracle_observation,
        )

    def attest(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        observed: PlatformObservedProvenance,
        subject_content_key: ContentKey,
        producer_run_id: RunId,
        producer_attempt_id: AttemptId,
        producer_actor_ids: object,
    ) -> BehaviorAttestation:
        self.require_handle(authority_handle)
        validate_platform_observed_provenance(observed, authority_handle=authority_handle)
        generated_at = self._trusted_clock()
        return _seal_attestation(
            configuration_id=self.configuration_id,
            subject_content_key=subject_content_key,
            producer_run_id=producer_run_id,
            producer_attempt_id=producer_attempt_id,
            producer_actor_ids=producer_actor_ids,
            attester_identity=self._attester_identity,
            builder_runtime_identity=self._builder_runtime_identity,
            repository_revision=observed.repository_revision,
            base_revision=observed.base_revision,
            task_contract_ref=observed.task_contract_ref,
            policy_inputs=observed.policy_inputs,
            environment_inputs=observed.environment_inputs,
            tool_inputs=observed.tool_inputs,
            source_refs=observed.source_refs,
            verification_refs=observed.verification_refs,
            oracle_observation=observed.oracle_observation,
            generated_at=generated_at,
        )


def configure_platform_attester(
    *,
    authority_handle: Stage4AuthorityHandle,
    builder_runtime_identity: BuilderRuntimeIdentity,
    trusted_clock: Callable[[], datetime],
) -> PlatformAttester:
    _handle(authority_handle)
    _validate_builder(builder_runtime_identity)
    return PlatformAttester(
        authority_handle,
        builder_runtime_identity,
        trusted_clock,
        _seal=_TRUSTED_ATTESTER_SEAL,
    )


def behavior_attestation_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    expected_subject_content_key: ContentKey,
    expected_builder_runtime_identity: BuilderRuntimeIdentity,
    expected_attester_identity: ActorIdentity,
    expected_repository_revision: RepositoryRevision,
) -> BehaviorAttestation:
    configuration = _handle(authority_handle)
    if _actor(expected_attester_identity, "expected_attester_identity") != configuration.platform_attester_actor:
        raise _fail(ProvenanceFailureCode.ATTESTER_MISMATCH, "expected attester differs from authority configuration")
    if expected_builder_runtime_identity.builder_actor_identity != configuration.builder_actor:
        raise _fail(ProvenanceFailureCode.BUILDER_MISMATCH, "expected builder differs from authority configuration")
    fields = (
        "schema_version", "configuration_id", "subject_content_key", "producer_run_id", "producer_attempt_id",
        "producer_actor_ids", "attester_identity", "builder_runtime_identity", "repository_revision",
        "base_revision", "task_contract_ref", "policy_inputs", "environment_inputs", "tool_inputs",
        "source_refs", "verification_refs", "oracle_observation", "generated_at", "attestation_id",
    )
    data = _exact_dict(value, fields, "behavior_attestation")
    if data["schema_version"] != SchemaVersion.BEHAVIOR_ATTESTATION_V1.value or type(data["schema_version"]) is not str:
        raise _fail(ProvenanceFailureCode.UNKNOWN_SCHEMA_VERSION, "attestation schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(ProvenanceFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport configuration differs from authority handle")
    supplied_subject = data["subject_content_key"]
    if supplied_subject != expected_subject_content_key.to_dict():
        raise _fail(ProvenanceFailureCode.SUBJECT_MISMATCH, "transport subject differs from consumer subject")
    attestation = _seal_attestation(
        configuration_id=configuration.configuration_id,
        subject_content_key=expected_subject_content_key,
        producer_run_id=RunId.from_dict(data["producer_run_id"]),
        producer_attempt_id=AttemptId.from_dict(data["producer_attempt_id"]),
        producer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["producer_actor_ids"], "producer_actor_ids")),
        attester_identity=ActorIdentity.from_dict(data["attester_identity"]),
        builder_runtime_identity=BuilderRuntimeIdentity.from_dict(data["builder_runtime_identity"]),
        repository_revision=RepositoryRevision.from_dict(data["repository_revision"]),
        base_revision=RepositoryRevision.from_dict(data["base_revision"]),
        task_contract_ref=HashBoundRef.from_dict(data["task_contract_ref"]),
        policy_inputs=tuple(ObservedExternalInput.from_dict(item) for item in _exact_list(data["policy_inputs"], "policy_inputs")),
        environment_inputs=tuple(ObservedExternalInput.from_dict(item) for item in _exact_list(data["environment_inputs"], "environment_inputs")),
        tool_inputs=tuple(ObservedExternalInput.from_dict(item) for item in _exact_list(data["tool_inputs"], "tool_inputs")),
        source_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["source_refs"], "source_refs")),
        verification_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["verification_refs"], "verification_refs")),
        oracle_observation=OracleObservation.from_dict(data["oracle_observation"]),
        generated_at=_parse_timestamp(data["generated_at"]),
    )
    identity_bytes = _canonical(_attestation_payload(attestation))
    try:
        supplied_id = record_id_from_dict(data["attestation_id"], canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(ProvenanceFailureCode.ATTESTATION_ID_MISMATCH, "transport attestation identity is invalid") from exc
    if supplied_id != attestation.attestation_id:
        raise _fail(ProvenanceFailureCode.ATTESTATION_ID_MISMATCH, "transport attestation identity changed")
    validate_behavior_attestation(
        attestation,
        expected_configuration_id=configuration.configuration_id,
        expected_subject_content_key=expected_subject_content_key,
        expected_builder_runtime_identity=expected_builder_runtime_identity,
        expected_attester_identity=expected_attester_identity,
        expected_repository_revision=expected_repository_revision,
    )
    return attestation


def behavior_attestation_to_ref(value: BehaviorAttestation) -> HashBoundRef:
    validate_behavior_attestation(value)
    transport_bytes = _canonical(value.to_dict())
    return HashBoundRef(
        kind=RefKind.SOURCE_EVIDENCE,
        ref_id=BEHAVIOR_ATTESTATION_REF_PREFIX_V1 + value.attestation_id.digest_sha256,
        schema_id=SchemaVersion.BEHAVIOR_ATTESTATION_V1.value,
        sha256=hashlib.sha256(transport_bytes).hexdigest(),
        byte_length=len(transport_bytes),
        media_type=BEHAVIOR_ATTESTATION_MEDIA_TYPE_V1,
    )


_ATTESTATION_TRANSPORT_FIELDS = (
    "schema_version", "configuration_id", "subject_content_key", "producer_run_id",
    "producer_attempt_id", "producer_actor_ids", "attester_identity",
    "builder_runtime_identity", "repository_revision", "base_revision",
    "task_contract_ref", "policy_inputs", "environment_inputs", "tool_inputs",
    "source_refs", "verification_refs", "oracle_observation", "generated_at",
    "attestation_id",
)


def _journal_entry_metadata(
    payload: bytes,
    *,
    configuration_id: RecordId,
) -> tuple[str, str, str]:
    try:
        raw = _exact_dict(_decode(payload), _ATTESTATION_TRANSPORT_FIELDS, "behavior_attestation")
        if raw["configuration_id"] != configuration_id.to_dict():
            raise _fail(ProvenanceFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "journal entry configuration differs")
        subject_raw = _exact_dict(raw["subject_content_key"], ("value",), "subject_content_key")
        subject = subject_raw["value"]
        if type(subject) is not str or not subject or len(subject) > 256 or "\x00" in subject:
            raise _fail(ProvenanceFailureCode.SUBJECT_MISMATCH, "journal subject content key is invalid")
        identity_payload = {key: raw[key] for key in _ATTESTATION_TRANSPORT_FIELDS if key != "attestation_id"}
        supplied = record_id_from_dict(raw["attestation_id"], canonical_bytes=_canonical(identity_payload))
        if supplied.domain is not IdentityDomain.BEHAVIOR_ATTESTATION:
            raise _fail(ProvenanceFailureCode.ATTESTATION_ID_MISMATCH, "journal attestation domain is invalid")
        return subject, supplied.value, hashlib.sha256(payload).hexdigest()
    except ProvenanceViolation:
        raise
    except Exception as exc:
        raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation journal entry is malformed") from exc


def _attestation_heads(entries: tuple[tuple[str, str, str], ...]) -> tuple[str, ...]:
    heads: dict[str, str] = {}
    for subject, record_id, _ in entries:
        if record_id in heads.values():
            raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation identity is duplicated")
        heads[subject] = record_id
    return tuple(f"{subject}|{record_id}" for subject, record_id in sorted(heads.items()))


class BehaviorAttestationStore:
    """Append-only attestation journal guarded by a process-local handle and external anchor."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_STORE_SEAL or kwargs or len(args) != 5:
            raise TypeError("BehaviorAttestationStore is opened only by open_behavior_attestation_store")
        root, authority_handle, attester, trusted_anchor, allow_genesis = args
        if not isinstance(root, Path) or type(attester) is not PlatformAttester or type(allow_genesis) is not bool:
            raise _fail(ProvenanceFailureCode.TYPE_MISMATCH, "attestation store configuration is invalid")
        self._root = root
        self._authority_handle = authority_handle
        self._attester = attester
        configuration = _handle(authority_handle)
        attester.require_handle(authority_handle)
        self._configuration_id = configuration.configuration_id
        self._trusted_anchor = None
        ensure_directory(root)
        initialize_journal(self._journal_path)
        entries = self._entries()
        if entries and trusted_anchor is None:
            raise _fail(ProvenanceFailureCode.HISTORY_ANCHOR_REQUIRED, "non-empty attestation history requires a trusted anchor")
        if not entries and trusted_anchor is None and not allow_genesis:
            raise _fail(ProvenanceFailureCode.HISTORY_ANCHOR_REQUIRED, "empty attestation history requires explicit genesis")
        self._trusted_anchor = trusted_anchor
        if trusted_anchor is not None:
            self._validate_anchor(entries, trusted_anchor)

    @property
    def _journal_path(self) -> Path:
        return self._root / BEHAVIOR_ATTESTATION_JOURNAL_NAME_V1

    @property
    def _lock_path(self) -> Path:
        return self._root / BEHAVIOR_ATTESTATION_LOCK_NAME_V1

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    def _entries(self) -> tuple[tuple[str, str, str], ...]:
        try:
            scan = scan_journal(self._journal_path)
            if scan.torn_tail:
                raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation journal has a torn tail")
            entries = tuple(
                _journal_entry_metadata(frame.payload, configuration_id=self._configuration_id)
                for frame in scan.frames
            )
            _attestation_heads(entries)
            if self._trusted_anchor is not None:
                self._validate_anchor(entries, self._trusted_anchor)
            return entries
        except ProvenanceViolation:
            raise
        except Exception as exc:
            raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation journal cannot be reconstructed") from exc

    def _validate_anchor(self, entries: tuple[tuple[str, str, str], ...], anchor: HistoryAnchor) -> None:
        try:
            prefix = entries[: anchor.entry_count]
            validate_history_anchor_extension(
                trusted_anchor=anchor,
                history_domain=HistoryDomain.PROVENANCE,
                configuration_id=self._configuration_id,
                entry_sha256s=tuple(item[2] for item in entries),
                prefix_domain_heads=_attestation_heads(prefix),
            )
        except ContractViolation as exc:
            raise _fail(ProvenanceFailureCode.HISTORY_ROLLBACK, "attestation history is not an exact trusted extension") from exc

    def current_anchor(self) -> HistoryAnchor:
        entries = self._entries()
        return create_history_anchor(
            history_domain=HistoryDomain.PROVENANCE,
            configuration_id=self._configuration_id,
            entry_sha256s=tuple(item[2] for item in entries),
            domain_heads=_attestation_heads(entries),
        )

    def append(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        attestation: BehaviorAttestation,
    ) -> HistoryAnchor:
        self.require_handle(authority_handle)
        configuration = _handle(authority_handle)
        validate_behavior_attestation(
            attestation,
            expected_configuration_id=configuration.configuration_id,
            expected_builder_runtime_identity=self._attester.builder_runtime_identity,
            expected_attester_identity=configuration.platform_attester_actor,
        )
        payload = _canonical(attestation.to_dict())
        with ExclusiveStoreLock(self._lock_path):
            entries = self._entries()
            if attestation.attestation_id.value in {item[1] for item in entries}:
                raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation identity already exists")
            append_journal_payload(self._journal_path, payload)
            committed = self._entries()
            if committed[-1][1] != attestation.attestation_id.value:
                raise _fail(ProvenanceFailureCode.JOURNAL_CORRUPT, "attestation append was not reconstructed")
            anchor = create_history_anchor(
                history_domain=HistoryDomain.PROVENANCE,
                configuration_id=self._configuration_id,
                entry_sha256s=tuple(item[2] for item in committed),
                domain_heads=_attestation_heads(committed),
            )
            self._trusted_anchor = anchor
            return anchor

    def contains(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        attestation: BehaviorAttestation,
    ) -> bool:
        self.require_handle(authority_handle)
        validate_behavior_attestation(attestation, expected_configuration_id=self._configuration_id)
        expected_payload_digest = hashlib.sha256(_canonical(attestation.to_dict())).hexdigest()
        return any(
            record_id == attestation.attestation_id.value and payload_digest == expected_payload_digest
            for _, record_id, payload_digest in self._entries()
        )


def open_behavior_attestation_store(
    *,
    root: Path,
    authority_handle: Stage4AuthorityHandle,
    platform_attester: PlatformAttester,
    trusted_anchor: HistoryAnchor | None = None,
    allow_genesis: bool = False,
) -> BehaviorAttestationStore:
    _handle(authority_handle)
    platform_attester.require_handle(authority_handle)
    return BehaviorAttestationStore(
        root,
        authority_handle,
        platform_attester,
        trusted_anchor,
        allow_genesis,
        _seal=_TRUSTED_STORE_SEAL,
    )


def require_behavior_attestation_consumable(
    *,
    attestation: BehaviorAttestation,
    expected_subject_content_key: ContentKey,
    authority_handle: Stage4AuthorityHandle,
    attestation_store: BehaviorAttestationStore,
    lifecycle_store: object,
    lifecycle_context: object,
) -> BehaviorAttestation:
    configuration = _handle(authority_handle)
    attestation_store.require_handle(authority_handle)
    validate_behavior_attestation(
        attestation,
        expected_configuration_id=configuration.configuration_id,
        expected_subject_content_key=expected_subject_content_key,
        expected_attester_identity=configuration.platform_attester_actor,
    )
    if not attestation_store.contains(authority_handle=authority_handle, attestation=attestation):
        raise _fail(ProvenanceFailureCode.ATTESTATION_NOT_ADMITTED, "attestation is absent from anchored provenance history")
    try:
        lifecycle_store.require_handle(authority_handle)
        subject_ref = behavior_attestation_to_ref(attestation)
        current = lifecycle_store.current_state(subject_ref=subject_ref, context=lifecycle_context)
        from .lifecycle import LifecycleState

        if current is LifecycleState.REVOKED:
            raise _fail(ProvenanceFailureCode.ATTESTATION_REVOKED, "attestation is currently revoked")
        lifecycle_store.require_consumable(subject_ref=subject_ref, context=lifecycle_context)
    except ProvenanceViolation:
        raise
    except Exception as exc:
        raise _fail(ProvenanceFailureCode.ATTESTATION_NOT_ADMITTED, "attestation lifecycle is not currently consumable") from exc
    return attestation


__all__ = (
    "BUILDER_RUNTIME_IDENTITY_V1", "OBSERVED_EXTERNAL_INPUT_V1", "ORACLE_OBSERVATION_V1",
    "PLATFORM_OBSERVED_PROVENANCE_V1",
    "BEHAVIOR_ATTESTATION_MEDIA_TYPE_V1", "ProvenanceFailureCode", "ProvenanceViolation",
    "ExternalInputKind", "BuilderRuntimeIdentity", "ObservedExternalInput", "OracleObservation",
    "PlatformObservedProvenance", "validate_platform_observed_provenance",
    "BehaviorAttestation", "PlatformAttester", "configure_platform_attester",
    "validate_behavior_attestation", "behavior_attestation_from_dict", "behavior_attestation_to_ref",
    "BehaviorAttestationStore", "open_behavior_attestation_store",
    "require_behavior_attestation_consumable",
)
