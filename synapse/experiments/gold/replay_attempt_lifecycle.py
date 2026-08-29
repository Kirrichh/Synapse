"""Owner contracts for durable replay-attempt lifecycle evidence.

A replay verdict says what execution proved.  It cannot also describe that the
process failed to make a verdict durable: adding such a value to ``ReplayStatus``
would turn storage and coordinator failures into claims about behaviour.

This component therefore owns the two records needed at that boundary:

* an execution claim binds the one spent execution identity to its exact
  durable request;
* an incomplete attempt records that a request has no terminal result and must
  be recovered without blindly repeating execution or an external effect.

The records are immutable and canonical.  A storage adapter may persist and
rebuild them, but it does not choose their phase or failure domain; that remains
an orchestration decision made at the owner boundary where the failure occurred.
``BehaviorReplayResult`` remains the only completion marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re

from .canonicalization import HashBoundRef, RefKind
from .contracts import SchemaVersion


REPLAY_EXECUTION_CLAIM_SCHEMA_V1 = (
    "synapse.stage4.gold.replay-execution-claim/v1"
)
REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1 = (
    "synapse.stage4.gold.replay-incomplete-attempt/v1"
)
REPLAY_ATTEMPT_RECORD_MEDIA_TYPE = "application/json"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ReplayAttemptLifecycleFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    UNKNOWN_PHASE = "UNKNOWN_PHASE"
    UNKNOWN_FAILURE_DOMAIN = "UNKNOWN_FAILURE_DOMAIN"
    NON_CANONICAL_RECORD = "NON_CANONICAL_RECORD"


class ReplayAttemptLifecycleViolation(ValueError):
    """A typed refusal to accept an ambiguous attempt-lifecycle record."""

    def __init__(
        self, failure_code: ReplayAttemptLifecycleFailureCode, detail: str
    ) -> None:
        if type(failure_code) is not ReplayAttemptLifecycleFailureCode:
            raise TypeError(
                "failure_code must be an exact ReplayAttemptLifecycleFailureCode"
            )
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: ReplayAttemptLifecycleFailureCode, detail: str
) -> ReplayAttemptLifecycleViolation:
    return ReplayAttemptLifecycleViolation(code, detail)


class ReplayAttemptState(str, Enum):
    """Persistence state, deliberately disjoint from replay verdicts."""

    INCOMPLETE_RECOVERABLE = "INCOMPLETE_RECOVERABLE"


class ReplayAttemptPhase(str, Enum):
    """The protected post-request operation that did not reach completion."""

    DURABLE_POLICY_REREAD = "DURABLE_POLICY_REREAD"
    MACHINE_CONSTRUCTION = "MACHINE_CONSTRUCTION"
    SNAPSHOT_RESTORE = "SNAPSHOT_RESTORE"
    EXECUTION_CLAIM = "EXECUTION_CLAIM"
    SETTLEMENT = "SETTLEMENT"
    RECEIPT_ISSUE = "RECEIPT_ISSUE"
    ACTIVITY_STORE_READ = "ACTIVITY_STORE_READ"
    EXECUTION = "EXECUTION"
    TERMINAL_SNAPSHOT_WRITE = "TERMINAL_SNAPSHOT_WRITE"
    TERMINAL_SNAPSHOT_READ_BACK = "TERMINAL_SNAPSHOT_READ_BACK"
    RESULT_APPEND = "RESULT_APPEND"


class ReplayAttemptFailureDomain(str, Enum):
    """The owner boundary that failed, not a substituted replay failure reason."""

    POLICY_AUTHORITY = "POLICY_AUTHORITY"
    MACHINE_ADAPTER = "MACHINE_ADAPTER"
    ACTIVITY_STORE = "ACTIVITY_STORE"
    REPLAY_STORE = "REPLAY_STORE"
    COORDINATOR = "COORDINATOR"
    BACKEND = "BACKEND"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _exact_dict(value: object, fields: tuple[str, ...], name: str) -> dict:
    if type(value) is not dict:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact dict",
        )
    if set(value) != set(fields) or any(type(key) is not str for key in value):
        raise _fail(
            ReplayAttemptLifecycleFailureCode.NON_CANONICAL_RECORD,
            f"{name} has an unexpected shape",
        )
    return value


def _enum_value(
    value: object,
    enum_type: type[Enum],
    code: ReplayAttemptLifecycleFailureCode,
    name: str,
) -> Enum:
    if type(value) is not str:
        raise _fail(code, f"{name} must be an exact known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail(code, f"{name} is unknown") from exc


def _request_ref(value: object) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "request_ref must be an exact HashBoundRef",
        )
    if (
        value.kind is not RefKind.ARTIFACT
        or value.schema_id != SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1.value
        or value.media_type != "application/json"
    ):
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "request_ref does not name a replay request",
        )
    return value


def _execution_identity(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "execution_identity must be an exact lowercase SHA-256 digest",
        )
    return value


@dataclass(frozen=True)
class ReplayExecutionClaim:
    """The exact durable request whose one execution identity was spent."""

    request_ref: HashBoundRef
    execution_identity: str

    def __post_init__(self) -> None:
        validate_replay_execution_claim(self)

    def to_dict(self) -> dict[str, object]:
        validate_replay_execution_claim(self)
        return {
            "schema_version": REPLAY_EXECUTION_CLAIM_SCHEMA_V1,
            "request_ref": self.request_ref.to_dict(),
            "execution_identity": self.execution_identity,
        }


def validate_replay_execution_claim(value: object) -> ReplayExecutionClaim:
    if type(value) is not ReplayExecutionClaim:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "execution claim must be an exact ReplayExecutionClaim",
        )
    _request_ref(value.request_ref)
    _execution_identity(value.execution_identity)
    return value


def replay_execution_claim_from_dict(value: object) -> ReplayExecutionClaim:
    data = _exact_dict(
        value,
        ("schema_version", "request_ref", "execution_identity"),
        "execution claim",
    )
    if data["schema_version"] != REPLAY_EXECUTION_CLAIM_SCHEMA_V1:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.UNKNOWN_SCHEMA_VERSION,
            "execution claim declares an unknown schema",
        )
    try:
        request_ref = HashBoundRef.from_dict(data["request_ref"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.NON_CANONICAL_RECORD,
            "execution claim carries an invalid request_ref",
        ) from exc
    result = ReplayExecutionClaim(
        request_ref=request_ref,
        execution_identity=data["execution_identity"],
    )
    if result.to_dict() != data:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.NON_CANONICAL_RECORD,
            "execution claim does not round-trip canonically",
        )
    return result


def replay_execution_claim_ref(value: ReplayExecutionClaim) -> HashBoundRef:
    validate_replay_execution_claim(value)
    raw = _canonical(value.to_dict())
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.execution_identity,
        schema_id=REPLAY_EXECUTION_CLAIM_SCHEMA_V1,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type=REPLAY_ATTEMPT_RECORD_MEDIA_TYPE,
    )


@dataclass(frozen=True)
class ReplayIncompleteAttempt:
    """Explicit non-terminal state for a durable request without a result."""

    request_ref: HashBoundRef
    execution_identity: str | None
    phase: ReplayAttemptPhase | None
    failure_domain: ReplayAttemptFailureDomain | None
    state: ReplayAttemptState = ReplayAttemptState.INCOMPLETE_RECOVERABLE

    def __post_init__(self) -> None:
        validate_replay_incomplete_attempt(self)

    def to_dict(self) -> dict[str, object]:
        validate_replay_incomplete_attempt(self)
        return {
            "schema_version": REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1,
            "state": self.state.value,
            "request_ref": self.request_ref.to_dict(),
            "execution_identity": self.execution_identity,
            "phase": None if self.phase is None else self.phase.value,
            "failure_domain": (
                None if self.failure_domain is None else self.failure_domain.value
            ),
        }


def validate_replay_incomplete_attempt(value: object) -> ReplayIncompleteAttempt:
    if type(value) is not ReplayIncompleteAttempt:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "incomplete attempt must be an exact ReplayIncompleteAttempt",
        )
    _request_ref(value.request_ref)
    if value.execution_identity is not None:
        _execution_identity(value.execution_identity)
    if value.phase is not None and type(value.phase) is not ReplayAttemptPhase:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.UNKNOWN_PHASE,
            "incomplete attempt phase is unknown",
        )
    if (
        value.failure_domain is not None
        and type(value.failure_domain) is not ReplayAttemptFailureDomain
    ):
        raise _fail(
            ReplayAttemptLifecycleFailureCode.UNKNOWN_FAILURE_DOMAIN,
            "incomplete attempt failure domain is unknown",
        )
    if (value.phase is None) != (value.failure_domain is None):
        raise _fail(
            ReplayAttemptLifecycleFailureCode.TYPE_MISMATCH,
            "phase and failure_domain must both be exact or both be unknown",
        )
    if value.state is not ReplayAttemptState.INCOMPLETE_RECOVERABLE:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.UNKNOWN_STATE,
            "incomplete attempt state is unknown",
        )
    return value


def replay_incomplete_attempt_from_dict(value: object) -> ReplayIncompleteAttempt:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "state",
            "request_ref",
            "execution_identity",
            "phase",
            "failure_domain",
        ),
        "incomplete attempt",
    )
    if data["schema_version"] != REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.UNKNOWN_SCHEMA_VERSION,
            "incomplete attempt declares an unknown schema",
        )
    try:
        request_ref = HashBoundRef.from_dict(data["request_ref"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.NON_CANONICAL_RECORD,
            "incomplete attempt carries an invalid request_ref",
        ) from exc
    state = _enum_value(
        data["state"], ReplayAttemptState,
        ReplayAttemptLifecycleFailureCode.UNKNOWN_STATE, "state",
    )
    phase = (
        None
        if data["phase"] is None
        else _enum_value(
            data["phase"],
            ReplayAttemptPhase,
            ReplayAttemptLifecycleFailureCode.UNKNOWN_PHASE,
            "phase",
        )
    )
    failure_domain = (
        None
        if data["failure_domain"] is None
        else _enum_value(
            data["failure_domain"],
            ReplayAttemptFailureDomain,
            ReplayAttemptLifecycleFailureCode.UNKNOWN_FAILURE_DOMAIN,
            "failure_domain",
        )
    )
    assert isinstance(state, ReplayAttemptState)
    assert phase is None or isinstance(phase, ReplayAttemptPhase)
    assert failure_domain is None or isinstance(
        failure_domain, ReplayAttemptFailureDomain
    )
    result = ReplayIncompleteAttempt(
        request_ref=request_ref,
        execution_identity=data["execution_identity"],
        phase=phase,
        failure_domain=failure_domain,
        state=state,
    )
    if result.to_dict() != data:
        raise _fail(
            ReplayAttemptLifecycleFailureCode.NON_CANONICAL_RECORD,
            "incomplete attempt does not round-trip canonically",
        )
    return result


def replay_incomplete_attempt_ref(value: ReplayIncompleteAttempt) -> HashBoundRef:
    validate_replay_incomplete_attempt(value)
    raw = _canonical(value.to_dict())
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=REPLAY_ATTEMPT_RECORD_MEDIA_TYPE,
    )


__all__ = [
    "REPLAY_ATTEMPT_RECORD_MEDIA_TYPE",
    "REPLAY_EXECUTION_CLAIM_SCHEMA_V1",
    "REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1",
    "ReplayAttemptFailureDomain",
    "ReplayAttemptLifecycleFailureCode",
    "ReplayAttemptLifecycleViolation",
    "ReplayAttemptPhase",
    "ReplayAttemptState",
    "ReplayExecutionClaim",
    "ReplayIncompleteAttempt",
    "replay_execution_claim_from_dict",
    "replay_execution_claim_ref",
    "replay_incomplete_attempt_from_dict",
    "replay_incomplete_attempt_ref",
    "validate_replay_execution_claim",
    "validate_replay_incomplete_attempt",
]
