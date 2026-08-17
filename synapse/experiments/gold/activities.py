"""Stage 4 §23 governed external activities and recorded results.

Every effect a replayed behavior reaches for outside its own state is an
activity: a Git read, an LLM call, a worker invocation, a test run, a container
operation, an oracle verdict. Nothing reaches the outside world implicitly.

Four rules carry the guarantee.

*An activity is identified by everything that can change its result.* The
replay determinism model proves this: if two activities whose results may differ
share an identity, replay may inject either one, the successor states differ in
the observable projection, and the divergence propagates through the whole hash
chain. So identity binds the complete input vector, the governing policy version
and the execution position — not a position alone. The runtime's existing
``compute_call_id`` binds no inputs at all and is therefore unusable here; that
is the obstruction the model records as §7.2, and this module is where it is
discharged.

*Replay consumes, never re-executes.* A recorded result is resolved from the
ledger by identity. A miss is a typed failure, never a live call: silently
reaching the network during a replay would make the run unreproducible in the
one place that claims reproducibility. The determinism contract is explicit that
recorded consumption is the only approved mechanism for replay safety.

*A recorded result is bound to its activity.* §23 states that activity identity
includes the result hash. It cannot be the same key a replay looks up by — a
replay searching for a result does not yet know it — so the result-bound key is
separate: ``compute_activity_idempotency_key``. Anyone holding that key from a
manifest or a lineage record can detect a substituted result, because the swap
keeps the lookup identity and loses the key.

*Nothing becomes consumable without the consumption gate.* A recorded result is
knowledge delivered into a replay, so the §22 barrier applies to it exactly as it
applies to a knowledge object: an activity set becomes a sealed ledger only when
a consumption decision has admitted that precise set, against the committed
snapshot boundary and the consumer context the replay will actually run under.

The module owns activity semantics only. It performs no I/O, holds no store and
never invokes an effect itself; a live executor lives outside and hands its
results here to be recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Mapping

from .admission import canonical_subject_refs
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_record_id,
    validate_record_id,
)
from .point_of_use import (
    CurrentAdmittedKnowledge,
    validate_current_admitted_knowledge,
)

ACTIVITY_IDENTITY_PROFILE_V1 = "synapse.stage4.gold.activity-identity/v1"
_IDENTITY_PREFIX = ACTIVITY_IDENTITY_PROFILE_V1.encode("utf-8") + b"\x00"

ACTIVITY_IDEMPOTENCY_PROFILE_V1 = "synapse.stage4.gold.activity-idempotency/v1"
_IDEMPOTENCY_PREFIX = ACTIVITY_IDEMPOTENCY_PROFILE_V1.encode("utf-8") + b"\x00"

_ACTIVITY_SEAL = object()
_LEDGER_SEAL = object()

_MAX_INPUTS = 256
_MAX_INPUT_BYTES = 1_048_576
_IDENTIFIER_MAX = 128
_SHA256_LENGTH = 64

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class ActivityKind(str, Enum):
    """Closed vocabulary of externally-effecting actions a behavior may reach.

    The list is closed on purpose: an effect with no kind has no policy and no
    identity, so it cannot be recorded, and an unrecorded effect cannot be
    replayed. A new kind is a reviewed addition, never an inference.

    Two groups are present. The first covers the harness effects a Gold run
    reaches for — repository, worker, test, container and oracle. The second
    covers the machine's own effect-bearing opcodes: memory, affect, metrics,
    messaging, habit, threshold and self-modification. The second group is not
    optional decoration — every opcode in ``RECORDED_ONLY_OPCODES`` must map to
    a kind, and an opcode with no kind could not be recorded at all.
    """

    GIT_READ = "GIT_READ"
    GIT_WRITE = "GIT_WRITE"
    LLM_CALL = "LLM_CALL"
    WORKER_INVOCATION = "WORKER_INVOCATION"
    TEST_EXECUTION = "TEST_EXECUTION"
    CONTAINER_OPERATION = "CONTAINER_OPERATION"
    ORACLE_VERDICT = "ORACLE_VERDICT"
    FILESYSTEM_READ = "FILESYSTEM_READ"
    NETWORK_FETCH = "NETWORK_FETCH"
    MEMORY_READ = "MEMORY_READ"
    MEMORY_WRITE = "MEMORY_WRITE"
    AFFECT_READ = "AFFECT_READ"
    AFFECT_EVENT = "AFFECT_EVENT"
    METRICS_EMIT = "METRICS_EMIT"
    MESSAGE_SEND = "MESSAGE_SEND"
    MESSAGE_RECEIVE = "MESSAGE_RECEIVE"
    HABIT_SUGGESTION = "HABIT_SUGGESTION"
    THRESHOLD_EVALUATION = "THRESHOLD_EVALUATION"
    SELF_MODIFICATION = "SELF_MODIFICATION"
    HOST_DISPATCH = "HOST_DISPATCH"


class ActivityDisposition(str, Enum):
    """What the policy permits for one activity during a replay."""

    RECORDED_CONSUMABLE = "RECORDED_CONSUMABLE"
    FORBIDDEN_IN_REPLAY = "FORBIDDEN_IN_REPLAY"
    REQUIRES_FRESH_AUTHORITY = "REQUIRES_FRESH_AUTHORITY"


class ActivityFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_ACTIVITY_KIND = "UNKNOWN_ACTIVITY_KIND"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_SHA256 = "MALFORMED_SHA256"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    INPUTS_UNORDERED = "INPUTS_UNORDERED"
    INPUTS_DUPLICATE = "INPUTS_DUPLICATE"
    INPUTS_MISSING = "INPUTS_MISSING"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    RESULT_HASH_MISMATCH = "RESULT_HASH_MISMATCH"
    ACTIVITY_NOT_RECORDED = "ACTIVITY_NOT_RECORDED"
    ACTIVITY_SUBSTITUTED = "ACTIVITY_SUBSTITUTED"
    DUPLICATE_ACTIVITY = "DUPLICATE_ACTIVITY"
    FORBIDDEN_IN_REPLAY = "FORBIDDEN_IN_REPLAY"
    FRESH_CALL_ATTEMPTED = "FRESH_CALL_ATTEMPTED"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    LEDGER_SEALED = "LEDGER_SEALED"
    LEDGER_NOT_BOUND = "LEDGER_NOT_BOUND"
    COGNITIVE_BUDGET_EXHAUSTED = "COGNITIVE_BUDGET_EXHAUSTED"


class ActivityViolation(ValueError):
    """A typed, fail-closed activity error carrying no result payload."""

    def __init__(self, failure_code: ActivityFailureCode, detail: str) -> None:
        if type(failure_code) is not ActivityFailureCode:
            raise TypeError("failure_code must be an exact ActivityFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ActivityFailureCode, detail: str) -> ActivityViolation:
    return ActivityViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID
    )


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(ActivityFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is invalid")
    if value.strip() != value:
        raise _fail(ActivityFailureCode.MALFORMED_IDENTIFIER, f"{field_name} has padding")
    return value


def _sha256(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != _SHA256_LENGTH:
        raise _fail(ActivityFailureCode.MALFORMED_SHA256, f"{field_name} is invalid")
    if any(character not in "0123456789abcdef" for character in value):
        raise _fail(ActivityFailureCode.MALFORMED_SHA256, f"{field_name} is not lowercase hex")
    return value


def _ref_key(value: object) -> str:
    """Identify a hash-bound reference by everything that binds it."""

    if type(value) is not HashBoundRef:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an exact HashBoundRef is required")
    return f"{value.kind.value}\x00{value.ref_id}\x00{value.sha256}"


def _timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise _fail(ActivityFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    if value.utcoffset() != timezone.utc.utcoffset(None):
        raise _fail(ActivityFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    return value


# ---------------------------------------------------------------------------
# ActivityInputs — the complete input vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityInputs:
    """Every value that can change an activity's result.

    Entries are ``(name, sha256-of-value)`` pairs in canonical order. Values are
    carried as digests rather than payloads: an input may be a whole repository
    tree or a prompt, and the identity needs to separate them, not transport
    them. This is also what keeps a raw transcript or a secret out of the
    identity material.
    """

    entries: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        validate_activity_inputs(self)

    def to_dict(self) -> dict[str, object]:
        validate_activity_inputs(self)
        return {"entries": [[name, digest] for name, digest in self.entries]}

    @classmethod
    def from_dict(cls, value: object) -> ActivityInputs:
        if type(value) is not dict or set(value) != {"entries"}:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity inputs payload is invalid")
        raw = value["entries"]
        if type(raw) is not list:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity input entries must be a list")
        entries: list[tuple[str, str]] = []
        for item in raw:
            if type(item) is not list or len(item) != 2:
                raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity input entry is invalid")
            entries.append((item[0], item[1]))
        return cls(tuple(entries))

    def digest(self) -> str:
        """Return the hash that separates this input vector from any other."""

        validate_activity_inputs(self)
        return hashlib.sha256(_canonical(self.to_dict())).hexdigest()


def validate_activity_inputs(value: ActivityInputs) -> None:
    if type(value) is not ActivityInputs:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity inputs type is invalid")
    if type(value.entries) is not tuple:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity input entries must be a tuple")
    if len(value.entries) > _MAX_INPUTS:
        raise _fail(ActivityFailureCode.RESOURCE_LIMIT_EXCEEDED, "activity inputs exceed the limit")
    names: list[str] = []
    for entry in value.entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity input entry must be a pair")
        name, digest = entry
        _identifier(name, "activity input name")
        _sha256(digest, "activity input digest")
        names.append(name)
    if len(set(names)) != len(names):
        raise _fail(ActivityFailureCode.INPUTS_DUPLICATE, "activity inputs contain a duplicate name")
    if names != sorted(names):
        raise _fail(ActivityFailureCode.INPUTS_UNORDERED, "activity inputs are not canonically ordered")


def activity_inputs(**values: bytes) -> ActivityInputs:
    """Build an input vector from named byte values.

    Callers pass the actual bytes; only digests are retained. An empty vector is
    refused: an activity with no inputs cannot be separated from any other
    activity of the same kind at the same position.
    """

    if not values:
        raise _fail(ActivityFailureCode.INPUTS_MISSING, "an activity requires at least one input")
    entries: list[tuple[str, str]] = []
    for name in sorted(values):
        payload = values[name]
        if type(payload) is not bytes:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, f"input {name} must be exact bytes")
        if len(payload) > _MAX_INPUT_BYTES:
            raise _fail(ActivityFailureCode.RESOURCE_LIMIT_EXCEEDED, f"input {name} exceeds the byte limit")
        entries.append((name, hashlib.sha256(payload).hexdigest()))
    return ActivityInputs(tuple(entries))


# ---------------------------------------------------------------------------
# ActivityIdentity — Corollary 5.3 of the determinism model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityPosition:
    """Where in a replay an activity occurs.

    Position alone never identifies an activity — that is exactly the defect the
    determinism model records — but it is still required, because the same call
    with the same inputs at two different points is two activities.
    """

    program_hash: str
    instruction_pointer: int
    frame_depth: int
    sequence: int

    def __post_init__(self) -> None:
        _identifier(self.program_hash, "program_hash")
        for name in ("instruction_pointer", "frame_depth", "sequence"):
            item = getattr(self, name)
            if type(item) is not int or isinstance(item, bool) or item < 0:
                raise _fail(ActivityFailureCode.TYPE_MISMATCH, f"{name} must be a natural number")

    def to_dict(self) -> dict[str, object]:
        return {
            "program_hash": self.program_hash,
            "instruction_pointer": self.instruction_pointer,
            "frame_depth": self.frame_depth,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, value: object) -> ActivityPosition:
        if type(value) is not dict or set(value) != {
            "program_hash", "instruction_pointer", "frame_depth", "sequence"
        }:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity position payload is invalid")
        return cls(
            value["program_hash"], value["instruction_pointer"],
            value["frame_depth"], value["sequence"],
        )


def compute_activity_identity(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    policy_version: str,
    position: ActivityPosition,
) -> str:
    """Return the identity that separates results which may differ.

    Corollary 5.3 of the determinism model fixes the content: the complete
    inputs, the governing policy version and the execution position. Anything
    that can change the result must be here, or two distinguishable activities
    would collide and replay could inject the wrong one.
    """

    if type(kind) is not ActivityKind:
        raise _fail(ActivityFailureCode.UNKNOWN_ACTIVITY_KIND, "activity kind must be exact")
    validate_activity_inputs(inputs)
    _identifier(policy_version, "policy_version")
    if type(position) is not ActivityPosition:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity position must be exact")
    preimage = _IDENTITY_PREFIX + _canonical(
        {
            "kind": kind.value,
            "inputs": inputs.to_dict(),
            "policy_version": policy_version,
            "position": position.to_dict(),
        }
    )
    return hashlib.sha256(preimage).hexdigest()


def compute_activity_idempotency_key(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    policy_version: str,
    position: ActivityPosition,
    result_sha256: str,
) -> str:
    """Return the key §23 calls activity identity: inputs, policy *and result hash*.

    Two keys are needed and they are not the same key, because they answer
    different questions at different times.

    ``compute_activity_identity`` is the *lookup* key. A replay reaching an
    effect knows its kind, inputs, policy and position and must find the record
    from those alone — it cannot know the result, since finding the result is
    the point. Binding the result there would make the ledger unusable.

    This is the *binding* key, computed once the result exists. §23 requires
    activity identity to include the result hash, and it is what makes
    substitution detectable: an attacker who swaps the recorded result of an
    activity keeps the lookup key and loses this one, so the swap is visible to
    anyone holding the key from a manifest or a lineage record.
    """

    preimage = _IDEMPOTENCY_PREFIX + _canonical(
        {
            "activity_identity": compute_activity_identity(
                kind=kind, inputs=inputs, policy_version=policy_version, position=position
            ),
            "result_sha256": _sha256(result_sha256, "result_sha256"),
        }
    )
    return hashlib.sha256(preimage).hexdigest()


# ---------------------------------------------------------------------------
# RecordedActivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class RecordedActivity:
    """One external effect that happened once and is replayed from record."""

    schema_version: SchemaVersion
    record_id: RecordId
    kind: ActivityKind
    activity_identity: str
    idempotency_key: str
    inputs: ActivityInputs
    position: ActivityPosition
    policy_version: str
    disposition: ActivityDisposition
    result_sha256: str
    result_ref: HashBoundRef | None
    recorded_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RecordedActivity:
        raise TypeError("RecordedActivity is created only by record_activity")

    def to_dict(self) -> dict[str, object]:
        validate_recorded_activity(self)
        return {**_activity_payload(self), "record_id": self.record_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_recorded_activity(self)
        return _canonical(_activity_payload(self))


def _activity_payload(value: RecordedActivity) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "kind": value.kind.value,
        "activity_identity": value.activity_identity,
        "idempotency_key": value.idempotency_key,
        "inputs": value.inputs.to_dict(),
        "position": value.position.to_dict(),
        "policy_version": value.policy_version,
        "disposition": value.disposition.value,
        "result_sha256": value.result_sha256,
        "result_ref": None if value.result_ref is None else value.result_ref.to_dict(),
        "recorded_at_utc": value.recorded_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_recorded_activity(value: RecordedActivity) -> None:
    if type(value) is not RecordedActivity or getattr(value, "_trusted_seal", None) is not _ACTIVITY_SEAL:
        raise _fail(ActivityFailureCode.TRUSTED_OBJECT_FORGED, "recorded activity is not factory sealed")
    if value.schema_version is not SchemaVersion.RECORDED_ACTIVITY_V1:
        raise _fail(ActivityFailureCode.UNKNOWN_SCHEMA_VERSION, "recorded activity schema is unknown")
    if type(value.kind) is not ActivityKind or type(value.disposition) is not ActivityDisposition:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "recorded activity enums are invalid")
    validate_activity_inputs(value.inputs)
    _identifier(value.policy_version, "policy_version")
    _sha256(value.result_sha256, "result_sha256")
    _sha256(value.activity_identity, "activity_identity")
    _sha256(value.idempotency_key, "idempotency_key")
    _timestamp(value.recorded_at_utc, "recorded_at_utc")
    if value.result_ref is not None and type(value.result_ref) is not HashBoundRef:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "result_ref must be an exact HashBoundRef")
    expected = compute_activity_identity(
        kind=value.kind,
        inputs=value.inputs,
        policy_version=value.policy_version,
        position=value.position,
    )
    if value.activity_identity != expected:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "activity identity does not match its kind, inputs, policy and position",
        )
    expected_key = compute_activity_idempotency_key(
        kind=value.kind,
        inputs=value.inputs,
        policy_version=value.policy_version,
        position=value.position,
        result_sha256=value.result_sha256,
    )
    if value.idempotency_key != expected_key:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "idempotency key does not bind this activity to this result",
        )
    try:
        validate_record_id(value.record_id, canonical_bytes=_canonical(_activity_payload(value)))
    except ContractViolation as exc:
        raise _fail(ActivityFailureCode.IDENTITY_MISMATCH, "record_id does not match its payload") from exc


def record_activity(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    position: ActivityPosition,
    policy_version: str,
    disposition: ActivityDisposition,
    result: bytes,
    recorded_at_utc: datetime,
    result_ref: HashBoundRef | None = None,
) -> RecordedActivity:
    """Record one external effect that has already been executed live.

    This module never performs the effect. A live executor outside hands the
    exact result bytes here, and from that point the result is what replay
    consumes.
    """

    if type(result) is not bytes:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity result must be exact bytes")
    if type(disposition) is not ActivityDisposition:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity disposition must be exact")
    identity = compute_activity_identity(
        kind=kind, inputs=inputs, policy_version=policy_version, position=position
    )
    result_sha256 = hashlib.sha256(result).hexdigest()
    payload = object.__new__(RecordedActivity)
    object.__setattr__(payload, "schema_version", SchemaVersion.RECORDED_ACTIVITY_V1)
    object.__setattr__(payload, "kind", kind)
    object.__setattr__(payload, "activity_identity", identity)
    object.__setattr__(
        payload,
        "idempotency_key",
        compute_activity_idempotency_key(
            kind=kind, inputs=inputs, policy_version=policy_version,
            position=position, result_sha256=result_sha256,
        ),
    )
    object.__setattr__(payload, "inputs", inputs)
    object.__setattr__(payload, "position", position)
    object.__setattr__(payload, "policy_version", policy_version)
    object.__setattr__(payload, "disposition", disposition)
    object.__setattr__(payload, "result_sha256", result_sha256)
    object.__setattr__(payload, "result_ref", result_ref)
    object.__setattr__(payload, "recorded_at_utc", _timestamp(recorded_at_utc, "recorded_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _ACTIVITY_SEAL)
    object.__setattr__(
        payload,
        "record_id",
        compute_record_id(
            domain=IdentityDomain.RECORDED_ACTIVITY,
            canonical_bytes=_canonical(_activity_payload(payload)),
        ),
    )
    validate_recorded_activity(payload)
    return payload


# ---------------------------------------------------------------------------
# ActivityLedger — the recorded set one replay may consume
# ---------------------------------------------------------------------------


class ActivityLedger:
    """The complete set of activities a replay is allowed to consume.

    A ledger is sealed before a replay begins. After sealing nothing can be
    added, so a replay cannot quietly grow its own evidence while running: an
    activity that was not recorded before the first transition can never be
    resolved during it.

    A ledger is also bound to the run it was sealed for: the knowledge subjects
    admitted at the point of use, the consumer context, the boundary, the policy
    version and the identity of that admission. A ledger sealed for one replay is
    therefore not usable in another — the binding travels with the object, so a
    caller cannot detach an activity set from the admission it was sealed under.

    **What a ledger does not claim is that its activities were §22-admitted.** An
    earlier revision ran the four-gate chain over the activity refs themselves.
    That claim cannot be made by any production path: every §22 subject needs a
    ``CompatibilitySubjectDescriptor``, which is built from a published behavior
    unit with its blob, manifest, index entry, attestation and lifecycle records,
    and a ``RecordedActivity`` has none of them — the point-of-use binding
    refuses a subject set its Stage 3 probe does not cover. So the gate chain
    over activity refs was constructible only from hand-built controllers, which
    is to say only in tests. Whether a recorded result may be consumed in a
    replay is a different question with a different authority, and it is OD-10's:
    the activity policy evaluator decides it per activity. What §22 governs is
    the *knowledge* this replay runs over, and that is what the ledger is bound
    to here.
    """

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise _fail(ActivityFailureCode.LEDGER_SEALED, "a sealed ledger is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise _fail(ActivityFailureCode.LEDGER_SEALED, "a sealed ledger is immutable")

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _LEDGER_SEAL or kwargs or len(args) != 6:
            raise TypeError("ActivityLedger is created only by seal_activity_ledger")
        (
            self._by_identity,
            self._policy_version,
            self._knowledge_subject_refs,
            self._consumer_context_ref,
            self._boundary_ref,
            self._admitted_knowledge_id,
        ) = args
        self._sealed = True

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def knowledge_subject_refs(self) -> tuple[HashBoundRef, ...]:
        """The knowledge subject set admitted at the point of use for this run."""

        return self._knowledge_subject_refs

    @property
    def admitted_knowledge_id(self) -> RecordId:
        """The identity of the present-time admission this ledger was sealed under.

        Refs alone would say which subjects the run is over and nothing about
        *which* revalidation admitted them, so an older admission over the same
        subjects would satisfy every other field.
        """

        return self._admitted_knowledge_id

    @property
    def consumer_context_ref(self) -> HashBoundRef:
        return self._consumer_context_ref

    @property
    def boundary_ref(self) -> HashBoundRef:
        return self._boundary_ref

    def require_bound_to(
        self,
        *,
        consumer_context_ref: HashBoundRef,
        boundary_ref: HashBoundRef,
        knowledge_subject_refs: tuple[HashBoundRef, ...] | None = None,
    ) -> None:
        """Refuse a ledger sealed for another replay.

        A replay calls this before its first transition. Without it a ledger
        could be lifted out of the run it was sealed for and consumed in a run
        whose boundary, consumer context or admitted knowledge the gate never
        saw. The subject set is checked too when the caller supplies one: a
        context and a boundary can be shared by two replays over different
        knowledge, and a ledger sealed for one of them is not evidence for the
        other.
        """

        if _ref_key(consumer_context_ref) != _ref_key(self._consumer_context_ref):
            raise _fail(
                ActivityFailureCode.LEDGER_NOT_BOUND,
                "ledger was admitted for another consumer context",
            )
        if knowledge_subject_refs is not None and tuple(
            _ref_key(item) for item in knowledge_subject_refs
        ) != tuple(_ref_key(item) for item in self._knowledge_subject_refs):
            raise _fail(
                ActivityFailureCode.LEDGER_NOT_BOUND,
                "ledger was sealed over another admitted knowledge set",
            )
        if _ref_key(boundary_ref) != _ref_key(self._boundary_ref):
            raise _fail(
                ActivityFailureCode.LEDGER_NOT_BOUND,
                "ledger was admitted for another snapshot boundary",
            )

    def __len__(self) -> int:
        return len(self._by_identity)

    def identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_identity))

    def idempotency_keys(self) -> tuple[str, ...]:
        """The result-bound keys §23 requires a replay request to carry.

        A request that pins these cannot have its activity history swapped
        underneath it: a substituted result keeps its lookup identity and loses
        its key, so the substitution is visible without re-reading the ledger.
        """

        return tuple(
            sorted(self._by_identity[key].idempotency_key for key in self._by_identity)
        )

    def activity_refs(self) -> tuple[HashBoundRef, ...]:
        """``recorded_activity_refs`` — the external results used during replay."""

        return tuple(
            activity_ref(self._by_identity[key]) for key in sorted(self._by_identity)
        )

    def recorded(self) -> tuple[RecordedActivity, ...]:
        return tuple(self._by_identity[key] for key in sorted(self._by_identity))

    def resolve(
        self,
        *,
        kind: ActivityKind,
        inputs: ActivityInputs,
        position: ActivityPosition,
    ) -> RecordedActivity:
        """Resolve a recorded result, or fail. Never call anything live.

        A miss is ``ACTIVITY_NOT_RECORDED``. It is deliberately not a fallback:
        reaching a live producer here would make the replay unreproducible in
        precisely the operation that claims reproducibility.
        """

        identity = compute_activity_identity(
            kind=kind, inputs=inputs, policy_version=self._policy_version, position=position
        )
        found = self._by_identity.get(identity)
        if found is None:
            raise _fail(
                ActivityFailureCode.ACTIVITY_NOT_RECORDED,
                f"no recorded {kind.value} activity for this identity",
            )
        validate_recorded_activity(found)
        if found.disposition is ActivityDisposition.FORBIDDEN_IN_REPLAY:
            raise _fail(
                ActivityFailureCode.FORBIDDEN_IN_REPLAY,
                f"{kind.value} is forbidden during replay by its recorded policy",
            )
        if found.disposition is ActivityDisposition.REQUIRES_FRESH_AUTHORITY:
            raise _fail(
                ActivityFailureCode.FRESH_CALL_ATTEMPTED,
                f"{kind.value} requires fresh authority and cannot be replayed from record",
            )
        if found.kind is not kind:
            raise _fail(ActivityFailureCode.ACTIVITY_SUBSTITUTED, "resolved activity has another kind")
        if found.inputs.digest() != inputs.digest():
            raise _fail(ActivityFailureCode.ACTIVITY_SUBSTITUTED, "resolved activity has other inputs")
        return found

    def require_result(
        self,
        *,
        kind: ActivityKind,
        inputs: ActivityInputs,
        position: ActivityPosition,
        result: bytes,
    ) -> RecordedActivity:
        """Resolve and verify that supplied bytes match the recorded result."""

        found = self.resolve(kind=kind, inputs=inputs, position=position)
        if type(result) is not bytes:
            raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity result must be exact bytes")
        if hashlib.sha256(result).hexdigest() != found.result_sha256:
            raise _fail(ActivityFailureCode.RESULT_HASH_MISMATCH, "activity result bytes were substituted")
        return found

    def ledger_root(self) -> str:
        """Return a content root over the sealed activity set and its binding.

        The binding is inside the root on purpose. A root over the activity set
        alone would be equal for two ledgers admitted under different boundaries,
        and a manifest recording that root would then not distinguish the run it
        described from a run it did not. The admitted knowledge set and the
        identity of the admission are inside it for the same reason: two runs can
        share a boundary and a consumer context and still be over different
        knowledge, admitted at different moments.
        """

        ordered = [self._by_identity[key].canonical_bytes() for key in sorted(self._by_identity)]
        binding = _canonical(
            {
                "policy_version": self._policy_version,
                "consumer_context_ref": self._consumer_context_ref.to_dict(),
                "boundary_ref": self._boundary_ref.to_dict(),
                "knowledge_subject_refs": [
                    item.to_dict() for item in self._knowledge_subject_refs
                ],
                "admitted_knowledge_id": self._admitted_knowledge_id.to_dict(),
            }
        )
        root = hashlib.sha256(_IDENTITY_PREFIX + binding).digest()
        for payload in ordered:
            root = hashlib.sha256(_IDENTITY_PREFIX + root + hashlib.sha256(payload).digest()).digest()
        return root.hex()


def seal_activity_ledger(
    *,
    activities: tuple[RecordedActivity, ...],
    admitted: CurrentAdmittedKnowledge,
) -> ActivityLedger:
    """Freeze the activity set a replay may consume, under a present-time admission.

    Sealing is the moment recorded results become deliverable, so it is the
    moment the §22 barrier applies to the run they will be delivered into. The
    ledger takes its policy version, consumer context, boundary, admitted
    knowledge set and the identity of that admission from the
    ``CurrentAdmittedKnowledge`` handed in. Nothing about the ledger's authority
    is stated by the caller any more.

    ``CurrentAdmittedKnowledge`` is minted by ``admit_for_use_now`` and by
    nothing else — its ``__new__`` refuses — so requiring one here is a
    requirement that the fresh consumption gate actually ran, not a promise the
    caller makes. The admission itself is performed by the replay owner, once,
    because a point-of-use attempt admits exactly once: its Stage 3 revalidation
    record is deterministic and the append-only compatibility history refuses the
    duplicate, so a second admission on the same binding is not a wasteful
    call — it is an impossible one.

    The earlier revision took a stored ``GateDecision`` over the *activity refs*
    and required it to name exactly this activity set. That is the check being
    removed here, and it is worth being precise about why, because removing a
    check is not something to do quietly.

    It was unconstructible outside a test. ``admit_for_use_now`` refuses unless
    the production binding's Stage 3 probe covers the exact admitted subject set,
    and a Stage 3 binding is built from a ``CompatibilitySubjectDescriptor`` —
    a published behavior unit with its blob, manifest, index entry, attestation
    and lifecycle records. A ``RecordedActivity`` has none of those and can never
    acquire them, so no production path could ever obtain a §22 admission naming
    an activity ref. The old check therefore ran only against controllers a test
    had assembled by hand, which is to say it asserted a property of the test.

    What replaces it is not nothing. The activity set is still frozen before the
    first transition and still bound by identity and idempotency key, the request
    pins both, and the ledger cannot be lifted into another run. And the question
    the old check was reaching for — may *this* recorded result be consumed in a
    replay — is answered by OD-10's activity policy evaluator, which is a
    separate authority with its own decision, because it is not a question about
    library knowledge and §22 has no vocabulary for it.
    """

    if type(activities) is not tuple:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activities must be an exact tuple")
    if type(admitted) is not CurrentAdmittedKnowledge:
        raise _fail(
            ActivityFailureCode.TYPE_MISMATCH,
            "sealing requires present-time admitted knowledge, not a stored verdict",
        )
    validate_current_admitted_knowledge(admitted)
    policy_version = _identifier(admitted.policy_version, "policy_version")
    consumer_context_ref = admitted.consumer_context_ref
    boundary_ref = admitted.boundary_ref
    _ref_key(consumer_context_ref)
    _ref_key(boundary_ref)
    by_identity: dict[str, RecordedActivity] = {}
    for item in activities:
        validate_recorded_activity(item)
        if item.policy_version != policy_version:
            raise _fail(
                ActivityFailureCode.POLICY_VERSION_MISMATCH,
                "activity was recorded under another policy version",
            )
        if item.activity_identity in by_identity:
            raise _fail(
                ActivityFailureCode.DUPLICATE_ACTIVITY,
                "two activities share one identity in the same ledger",
            )
        by_identity[item.activity_identity] = item

    return ActivityLedger(
        by_identity,
        policy_version,
        canonical_subject_refs(admitted.subject_refs),
        consumer_context_ref,
        boundary_ref,
        admitted.knowledge_id,
        _seal=_LEDGER_SEAL,
    )


def activity_ref(value: RecordedActivity) -> HashBoundRef:
    """Return the hash-bound reference a replay request stores."""

    validate_recorded_activity(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.record_id.digest_sha256,
        schema_id=SchemaVersion.RECORDED_ACTIVITY_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


__all__ = [
    "ACTIVITY_IDEMPOTENCY_PROFILE_V1",
    "ACTIVITY_IDENTITY_PROFILE_V1",
    "ActivityDisposition",
    "ActivityFailureCode",
    "ActivityInputs",
    "ActivityKind",
    "ActivityLedger",
    "ActivityPosition",
    "ActivityViolation",
    "RecordedActivity",
    "activity_inputs",
    "activity_ref",
    "compute_activity_idempotency_key",
    "compute_activity_identity",
    "record_activity",
    "seal_activity_ledger",
    "validate_activity_inputs",
    "validate_recorded_activity",
]
