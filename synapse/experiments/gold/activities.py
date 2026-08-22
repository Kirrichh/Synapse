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
includes the result hash, so ``compute_activity_identity`` binds the result hash
and the reference the bytes live behind. It cannot be the key a replay looks up
by — a replay searching for a result does not yet know it — so the pre-result
key is separate and separately named: ``compute_activity_lookup_key``. Anyone
holding an identity from a manifest or a lineage record can detect a substituted
result, because the swap keeps the lookup key and changes the identity.

*Nothing becomes consumable by this module's own say-so.* Two different
authorities have to answer before a recorded result reaches a machine, and this
module is neither of them.

§22 governs the *knowledge* the replay runs over. A ledger is sealed only from
``CurrentAdmittedKnowledge``, which ``admit_for_use_now`` alone can mint, and the
ledger takes its policy version, consumer context, boundary, admitted subject set
and the identity of that admission from it. So a ledger cannot be detached from
the admission it was sealed under, and it cannot be built without one.

What a sealed ledger does **not** claim is that its activities were themselves
§22-admitted. They cannot be: every §22 subject is a published behavior unit with
its blob, manifest, index entry, attestation and lifecycle records, and a
``RecordedActivity`` has none of those and can never acquire them. Whether a
particular recorded result may be consumed in a replay is a different question
with a different authority — OD-10's activity policy evaluator, in
``activity_policy.py`` — and §22 has no vocabulary for it. ``seal_activity_ledger``
says the same thing at greater length, and this paragraph exists so the two do
not drift apart again.

The module owns activity semantics only. It performs no I/O and never invokes an
effect itself; a live executor lives outside and hands its results here to be
recorded, and the exact bytes live in the durable store owned by
``activity_store.py``, which this module names through a hash-bound reference and
never opens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib

from .admission import canonical_subject_refs
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    AttemptId,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    common_envelope_from_dict,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    validate_envelope_bound_record,
)
from .point_of_use import (
    CurrentAdmittedKnowledge,
    validate_current_admitted_knowledge,
)

#: The schema and media type an activity result blob is stored and named under.
#: A reference is only hash-bound if something checks it against the bytes, and
#: these two are half of what gets checked — a digest that agreed while the
#: codec disagreed would let bytes canonical under another profile be presented
#: as this record's result.
ACTIVITY_RESULT_BLOB_V1 = "synapse.stage4.gold.activity-result-blob/v1"
ACTIVITY_RESULT_MEDIA_TYPE = "application/octet-stream"

#: The codec those bytes are canonical under, and the other half of what makes
#: the reference mean something. The schema above names *which blob*; this names
#: *what the blob is*, and without it a digest binds a byte string to a record
#: while leaving the value that byte string denotes unconstrained — JSON has many
#: spellings of one value, so two records could name the same value under two
#: identities, or one identity could be reached from bytes nobody canonicalised.
#:
#: Declared here, beside the schema it qualifies, and implemented in ``replay.py``
#: — this module hashes result bytes and never interprets them, which is the same
#: separation that keeps it free of I/O. The enforcement therefore lives at the
#: point of consumption, where the bytes are turned back into a machine value.
ACTIVITY_RESULT_CODEC_V1 = "synapse.stage4.gold.activity-result-codec/v1"

#: The pre-result key. It exists because a replay reaching an effect knows the
#: kind, the inputs, the policy and the position and must find the record from
#: those alone — finding the result is the point, so the result cannot be in it.
ACTIVITY_LOOKUP_KEY_PROFILE_V1 = "synapse.stage4.gold.activity-lookup-key/v1"
_LOOKUP_PREFIX = ACTIVITY_LOOKUP_KEY_PROFILE_V1.encode("utf-8") + b"\x00"

#: The identity. §23 requires activity identity to include the result hash, so
#: this is the value that answers "which activity is this" — and the lookup key
#: is not that value. An earlier revision had the two names the other way round
#: and called the result-bound value an idempotency key, which let a record
#: whose bytes had been swapped keep its "identity" while changing what it
#: actually was.
ACTIVITY_IDENTITY_PROFILE_V1 = "synapse.stage4.gold.activity-identity/v1"
_IDENTITY_PREFIX = ACTIVITY_IDENTITY_PROFILE_V1.encode("utf-8") + b"\x00"


@dataclass(frozen=True)
class ActivityRecordContext:
    """The §13 execution identity a recorded activity is stamped with.

    Carried as one object rather than five parameters because these five always
    travel together and always come from the same place — the run that performed
    the effect. A record free to state a different run or a different repository
    revision than the execution that produced it would carry an envelope
    describing nothing in particular.
    """

    run_id: RunId
    attempt_id: AttemptId
    repository_revision: RepositoryRevision
    environment_profile_id: str
    producer_component: str


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
    RESULT_REF_MISMATCH = "RESULT_REF_MISMATCH"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    RESULT_CORRUPTED = "RESULT_CORRUPTED"
    POLICY_DECISION_REQUIRED = "POLICY_DECISION_REQUIRED"
    POLICY_DECISION_STALE = "POLICY_DECISION_STALE"


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


def compute_activity_lookup_key(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    policy_version: str,
    position: ActivityPosition,
) -> str:
    """Return the key a replay searches by, before it can know the result.

    Corollary 5.3 of the determinism model fixes the content: the complete
    inputs, the governing policy version and the execution position. Anything
    that can change the result must be here, or two distinguishable activities
    would collide and a replay could resolve the wrong record.

    This is **not** the activity identity. It cannot be: §23 requires identity
    to bind the result hash, and a replay looking a result up does not have one
    yet. Keeping the two apart is what makes a substituted result detectable —
    the swap keeps this key and changes the identity.
    """

    if type(kind) is not ActivityKind:
        raise _fail(ActivityFailureCode.UNKNOWN_ACTIVITY_KIND, "activity kind must be exact")
    validate_activity_inputs(inputs)
    _identifier(policy_version, "policy_version")
    if type(position) is not ActivityPosition:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity position must be exact")
    preimage = _LOOKUP_PREFIX + _canonical(
        {
            "kind": kind.value,
            "inputs": inputs.to_dict(),
            "policy_version": policy_version,
            "position": position.to_dict(),
        }
    )
    return hashlib.sha256(preimage).hexdigest()


def compute_activity_identity(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    policy_version: str,
    position: ActivityPosition,
    result_sha256: str,
    result_ref: HashBoundRef,
) -> str:
    """Return the §23 activity identity — everything above, plus the exact result.

    Identity binds the result hash *and* the reference the bytes live behind.
    Both, because they can be substituted independently: rewriting the bytes at
    a fixed reference changes the hash, and re-pointing the reference at other
    bytes changes the ref. An identity that bound only one of them would call
    two different activities the same activity.

    The lookup key is folded in rather than repeated, so identity is a function
    of exactly the lookup content and the result, under its own domain separator.
    """

    if type(result_ref) is not HashBoundRef:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an activity result ref must be exact")
    preimage = _IDENTITY_PREFIX + _canonical(
        {
            "lookup_key": compute_activity_lookup_key(
                kind=kind, inputs=inputs, policy_version=policy_version, position=position
            ),
            "result_sha256": _sha256(result_sha256, "result_sha256"),
            "result_ref": result_ref.to_dict(),
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
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    record_id: RecordId
    kind: ActivityKind
    #: The pre-result key a replay resolves by.
    lookup_key: str
    #: The §23 identity: the lookup content *and* the exact result.
    activity_identity: str
    inputs: ActivityInputs
    position: ActivityPosition
    policy_version: str
    result_sha256: str
    #: Where the exact bytes live. Never optional: a record whose result is not
    #: retrievable cannot be injected, and §23 forbids inventing one in its place.
    result_ref: HashBoundRef
    recorded_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RecordedActivity:
        raise TypeError("RecordedActivity is created only by record_activity")

    def to_dict(self) -> dict[str, object]:
        validate_recorded_activity(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _activity_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_recorded_activity(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_activity_payload(self),
        )


def _activity_payload(value: RecordedActivity) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "kind": value.kind.value,
        "lookup_key": value.lookup_key,
        "activity_identity": value.activity_identity,
        "inputs": value.inputs.to_dict(),
        "position": value.position.to_dict(),
        "policy_version": value.policy_version,
        "result_sha256": value.result_sha256,
        "result_ref": value.result_ref.to_dict(),
        "recorded_at_utc": value.recorded_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_recorded_activity(value: RecordedActivity) -> None:
    if type(value) is not RecordedActivity or getattr(value, "_trusted_seal", None) is not _ACTIVITY_SEAL:
        raise _fail(ActivityFailureCode.TRUSTED_OBJECT_FORGED, "recorded activity is not factory sealed")
    if value.schema_version is not SchemaVersion.RECORDED_ACTIVITY_V1:
        raise _fail(ActivityFailureCode.UNKNOWN_SCHEMA_VERSION, "recorded activity schema is unknown")
    if type(value.kind) is not ActivityKind:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "recorded activity kind is invalid")
    if hasattr(value, "disposition"):
        raise _fail(
            ActivityFailureCode.TYPE_MISMATCH,
            "a recorded activity must not carry an activity-policy verdict",
        )
    validate_activity_inputs(value.inputs)
    _identifier(value.policy_version, "policy_version")
    _sha256(value.result_sha256, "result_sha256")
    _sha256(value.lookup_key, "lookup_key")
    _sha256(value.activity_identity, "activity_identity")
    _timestamp(value.recorded_at_utc, "recorded_at_utc")
    if type(value.result_ref) is not HashBoundRef:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "result_ref must be an exact HashBoundRef")
    if value.result_ref.sha256 != value.result_sha256:
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "the result ref does not name the result this record hashes",
        )
    expected_lookup = compute_activity_lookup_key(
        kind=value.kind,
        inputs=value.inputs,
        policy_version=value.policy_version,
        position=value.position,
    )
    if value.lookup_key != expected_lookup:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "lookup key does not match this kind, inputs, policy and position",
        )
    expected_identity = compute_activity_identity(
        kind=value.kind,
        inputs=value.inputs,
        policy_version=value.policy_version,
        position=value.position,
        result_sha256=value.result_sha256,
        result_ref=value.result_ref,
    )
    if value.activity_identity != expected_identity:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "activity identity does not bind this activity to this exact result",
        )
    if type(value.envelope) is not CommonEnvelope:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "a recorded activity must carry an envelope")
    _sha256(value.envelope_binding_sha256, "envelope_binding_sha256")
    try:
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=_canonical(_activity_payload(value)),
            expected_identity_domain=IdentityDomain.RECORDED_ACTIVITY,
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "the activity envelope does not bind this exact payload",
        ) from exc
    if value.record_id != value.envelope.record_id:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "record_id is not the identity its envelope computed",
        )


def _require_result_ref_describes(
    value: object, *, result: bytes, result_sha256: str
) -> HashBoundRef:
    """Refuse a result reference that does not describe these exact bytes.

    A hash-bound reference is only worth its name if someone checks it against
    the thing it names. Four fields are checked, and each of them can be wrong
    on its own: the digest, the length, the media type and the codec the bytes
    are canonical under. A reference that agreed on the digest and disagreed on
    the length would still let a truncated blob be presented as the record.
    """

    if type(value) is not HashBoundRef:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an activity result ref must be exact")
    if value.kind is not RefKind.ARTIFACT:
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "an activity result blob is referenced as an artifact",
        )
    if value.schema_id != ACTIVITY_RESULT_BLOB_V1:
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "an activity result ref must name the activity-result blob schema",
        )
    if value.media_type != ACTIVITY_RESULT_MEDIA_TYPE:
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "an activity result ref must declare the exact result media type",
        )
    if value.sha256 != result_sha256 or value.ref_id != result_sha256:
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "the result ref does not name these exact result bytes",
        )
    if value.byte_length != len(result):
        raise _fail(
            ActivityFailureCode.RESULT_REF_MISMATCH,
            "the result ref declares another length than these bytes have",
        )
    return value


def record_activity(
    *,
    kind: ActivityKind,
    inputs: ActivityInputs,
    position: ActivityPosition,
    policy_version: str,
    result: bytes,
    result_ref: HashBoundRef,
    context: ActivityRecordContext,
    recorded_at_utc: datetime,
) -> RecordedActivity:
    """Record one external effect that has already been executed live.

    This module never performs the effect. A live executor outside hands the
    exact result bytes here, and from that point the result is what replay
    consumes.
    """

    if type(result) is not bytes:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "activity result must be exact bytes")
    result_sha256 = hashlib.sha256(result).hexdigest()
    _require_result_ref_describes(result_ref, result=result, result_sha256=result_sha256)
    payload = object.__new__(RecordedActivity)
    object.__setattr__(payload, "schema_version", SchemaVersion.RECORDED_ACTIVITY_V1)
    object.__setattr__(payload, "kind", kind)
    object.__setattr__(
        payload,
        "lookup_key",
        compute_activity_lookup_key(
            kind=kind, inputs=inputs, policy_version=policy_version, position=position
        ),
    )
    object.__setattr__(
        payload,
        "activity_identity",
        compute_activity_identity(
            kind=kind, inputs=inputs, policy_version=policy_version,
            position=position, result_sha256=result_sha256, result_ref=result_ref,
        ),
    )
    object.__setattr__(payload, "inputs", inputs)
    object.__setattr__(payload, "position", position)
    object.__setattr__(payload, "policy_version", policy_version)
    object.__setattr__(payload, "result_sha256", result_sha256)
    object.__setattr__(payload, "result_ref", result_ref)
    object.__setattr__(payload, "recorded_at_utc", _timestamp(recorded_at_utc, "recorded_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _ACTIVITY_SEAL)
    if type(context) is not ActivityRecordContext:
        raise _fail(
            ActivityFailureCode.TYPE_MISMATCH,
            "a recorded activity requires the execution identity it was produced under",
        )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.RECORDED_ACTIVITY,
        canonical_payload_bytes=_canonical(_activity_payload(payload)),
        run_id=context.run_id,
        attempt_id=context.attempt_id,
        created_at_utc=payload.recorded_at_utc,
        producer_component=context.producer_component,
        repository_revision=context.repository_revision,
        policy_version=policy_version,
        environment_profile_id=context.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(payload, "record_id", envelope.record_id)
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
            self._by_lookup_key,
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
        return len(self._by_lookup_key)

    def lookup_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_lookup_key))

    def activity_identities(self) -> tuple[str, ...]:
        """The §23 activity identities a replay request pins.

        A request that pins these cannot have its activity history swapped
        underneath it: a substituted result keeps its lookup key and loses its
        identity, so the substitution is visible without re-reading the ledger.
        """

        return tuple(
            sorted(self._by_lookup_key[key].activity_identity for key in self._by_lookup_key)
        )

    def activity_refs(self) -> tuple[HashBoundRef, ...]:
        """``recorded_activity_refs`` — the external results used during replay."""

        return tuple(
            activity_ref(self._by_lookup_key[key]) for key in sorted(self._by_lookup_key)
        )

    def recorded(self) -> tuple[RecordedActivity, ...]:
        return tuple(self._by_lookup_key[key] for key in sorted(self._by_lookup_key))

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

        lookup_key = compute_activity_lookup_key(
            kind=kind, inputs=inputs, policy_version=self._policy_version, position=position
        )
        found = self._by_lookup_key.get(lookup_key)
        if found is None:
            raise _fail(
                ActivityFailureCode.ACTIVITY_NOT_RECORDED,
                f"no recorded {kind.value} activity for this identity",
            )
        validate_recorded_activity(found)
        if hasattr(found, "disposition"):
            raise _fail(
                ActivityFailureCode.TYPE_MISMATCH,
                "a recorded activity must not carry an activity-policy verdict",
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

        ordered = [self._by_lookup_key[key].canonical_bytes() for key in sorted(self._by_lookup_key)]
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
    first transition and still bound by lookup key and result-bound identity, the
    request pins both, and the ledger cannot be lifted into another run. The
    question
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
    by_lookup_key: dict[str, RecordedActivity] = {}
    for item in activities:
        validate_recorded_activity(item)
        if item.policy_version != policy_version:
            raise _fail(
                ActivityFailureCode.POLICY_VERSION_MISMATCH,
                "activity was recorded under another policy version",
            )
        if item.lookup_key in by_lookup_key:
            raise _fail(
                ActivityFailureCode.DUPLICATE_ACTIVITY,
                "two activities share one lookup key in the same ledger",
            )
        by_lookup_key[item.lookup_key] = item

    return ActivityLedger(
        by_lookup_key,
        policy_version,
        canonical_subject_refs(admitted.subject_refs),
        consumer_context_ref,
        boundary_ref,
        admitted.knowledge_id,
        _seal=_LEDGER_SEAL,
    )


def activity_record_from_dict(value: object) -> RecordedActivity:
    """Rebuild a recorded activity from its exact canonical dictionary.

    Restoration recomputes rather than trusts. The lookup key, the identity and
    the record id are all derived again from the restored content and compared
    with what the payload claims, so a record rewritten on disk does not become
    a record again by being read: it fails the same identity checks a freshly
    minted one passes.
    """

    if type(value) is not dict or set(value) != {
        "envelope", "envelope_binding_sha256", "payload"
    }:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an activity record has an unexpected shape")
    stored_envelope = value["envelope"]
    stored_binding = value["envelope_binding_sha256"]
    value = value["payload"]
    if type(value) is not dict:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an activity payload must be an exact dict")
    expected_fields = {
        "schema_version", "kind", "lookup_key", "activity_identity",
        "inputs", "position", "policy_version", "result_sha256",
        "result_ref", "recorded_at_utc",
    }
    if set(value) != expected_fields:
        raise _fail(ActivityFailureCode.TYPE_MISMATCH, "an activity payload has an unexpected shape")
    if value["schema_version"] != SchemaVersion.RECORDED_ACTIVITY_V1.value:
        raise _fail(ActivityFailureCode.UNKNOWN_SCHEMA_VERSION, "recorded activity schema is unknown")
    payload = object.__new__(RecordedActivity)
    object.__setattr__(payload, "schema_version", SchemaVersion.RECORDED_ACTIVITY_V1)
    object.__setattr__(payload, "kind", _activity_kind_from_value(value["kind"]))
    object.__setattr__(payload, "lookup_key", value["lookup_key"])
    object.__setattr__(payload, "activity_identity", value["activity_identity"])
    object.__setattr__(payload, "inputs", ActivityInputs.from_dict(value["inputs"]))
    object.__setattr__(payload, "position", ActivityPosition.from_dict(value["position"]))
    object.__setattr__(payload, "policy_version", value["policy_version"])
    object.__setattr__(payload, "result_sha256", value["result_sha256"])
    object.__setattr__(payload, "result_ref", HashBoundRef.from_dict(value["result_ref"]))
    object.__setattr__(
        payload, "recorded_at_utc", _timestamp_from_text(value["recorded_at_utc"])
    )
    object.__setattr__(payload, "_trusted_seal", _ACTIVITY_SEAL)
    try:
        envelope = common_envelope_from_dict(
            stored_envelope, canonical_payload_bytes=_canonical(_activity_payload(payload))
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityFailureCode.IDENTITY_MISMATCH,
            "the stored envelope does not bind the payload it was stored with",
        ) from exc
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", stored_binding)
    object.__setattr__(payload, "record_id", envelope.record_id)
    validate_recorded_activity(payload)
    return payload


def _activity_kind_from_value(value: object) -> ActivityKind:
    for item in ActivityKind:
        if item.value == value:
            return item
    raise _fail(ActivityFailureCode.UNKNOWN_ACTIVITY_KIND, "unknown activity kind")


def _timestamp_from_text(value: object) -> datetime:
    if type(value) is not str:
        raise _fail(ActivityFailureCode.MALFORMED_TIMESTAMP, "timestamp must be exact text")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(ActivityFailureCode.MALFORMED_TIMESTAMP, "timestamp is not canonical UTC") from exc
    return parsed


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
    "ACTIVITY_IDENTITY_PROFILE_V1",
    "ACTIVITY_LOOKUP_KEY_PROFILE_V1",
    "ACTIVITY_RESULT_BLOB_V1",
    "ACTIVITY_RESULT_CODEC_V1",
    "ACTIVITY_RESULT_MEDIA_TYPE",
    "ActivityDisposition",
    "ActivityFailureCode",
    "ActivityInputs",
    "ActivityKind",
    "ActivityLedger",
    "ActivityPosition",
    "ActivityRecordContext",
    "ActivityViolation",
    "RecordedActivity",
    "activity_inputs",
    "activity_record_from_dict",
    "activity_ref",
    "compute_activity_lookup_key",
    "compute_activity_identity",
    "record_activity",
    "seal_activity_ledger",
    "validate_activity_inputs",
    "validate_recorded_activity",
]
