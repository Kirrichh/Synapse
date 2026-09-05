"""Stage 12 outcome identities, exact transport and the single status matrix.

The seven statuses describe correctness, separately from controller stop and
telemetry. Only evaluator-sealed verification can create an attempt outcome.
Inspection of JSON is deliberately not a source of execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib

from ..canonicalization import HashBoundRef, RefKind
from ..stage10.context_codec import decode_canonical, encode_canonical
from .verification import VerificationRecord, inspect_verification_record, require_verification_record
from ..runner.vocabulary import AttemptOutcome, GoldRunFailureCode, GoldRunViolation


STRUCTURED_OUTCOME_SCHEMA_V1 = "synapse.stage4.gold.structured-outcome/v1"
OUTCOME_POLICY_VERSION = "stage12-od13/v1"
_SEAL = object()


class FinalStatus(str, Enum):
    FULL = "FULL"
    VERIFIED_REUSABLE_PARTIAL = "VERIFIED_REUSABLE_PARTIAL"
    UNRESOLVED = "UNRESOLVED"
    NO_CANDIDATE = "NO_CANDIDATE"
    FAIL = "FAIL"
    INFRA_ERROR = "INFRA_ERROR"
    INVALID_CONTRACT = "INVALID_CONTRACT"


def _status(facts: dict[str, object]) -> FinalStatus:
    """Frozen OD-13 precedence; facts originate exclusively in verification."""
    if facts["failure_codes"]:
        return FinalStatus.INVALID_CONTRACT
    c1 = facts["c1"]
    if facts["interrupted"] or c1 is not None and c1["infra_error"]:
        return FinalStatus.INFRA_ERROR
    if facts["refused"]:
        return FinalStatus.FAIL
    if c1 is None:
        return FinalStatus.INVALID_CONTRACT
    if c1["no_candidate"]:
        return FinalStatus.NO_CANDIDATE
    obligations = facts["obligations"]
    if (
        c1["c1_status"] == "GOLD_APPLIED_WITH_EVIDENCE" and c1["evidence_ref"] is not None
        and c1["commands_complete"] is True and c1["task_ref"] is not None
        and c1["oracle_result_ref"] is not None and c1["oracle_resolved"] is True
        and facts["plan"] is not None and facts["resolved_bindings"]
        and obligations and all(item["discharged"] is True and item["evidence_ref"] is not None for item in obligations)
    ):
        return FinalStatus.FULL
    # Publication of newly verified reusable behaviors is Stage 13. Until a
    # committed admission exists, no partial-success branch is reachable.
    if c1["refused"]:
        return FinalStatus.FAIL
    return FinalStatus.UNRESOLVED


@dataclass(frozen=True, init=False)
class StructuredOutcome:
    _bytes: bytes
    _digest: str
    _seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("StructuredOutcome is evaluator-created")

    def payload(self) -> dict[str, object]:
        require_structured_outcome(self)
        return decode_canonical(self._bytes)

    @property
    def status(self) -> FinalStatus:
        return FinalStatus(self.payload()["status"])

    @property
    def reference(self) -> HashBoundRef:
        require_structured_outcome(self)
        return HashBoundRef(RefKind.ARTIFACT, self._digest, STRUCTURED_OUTCOME_SCHEMA_V1,
                            self._digest, len(self._bytes), "application/json")

    def to_dict(self) -> dict[str, object]:
        return {"outcome_ref": self.reference.to_dict(), "payload": self.payload()}


def require_structured_outcome(value: object) -> StructuredOutcome:
    if type(value) is not StructuredOutcome or getattr(value, "_seal", None) is not _SEAL:
        raise GoldRunViolation(GoldRunFailureCode.TYPE_MISMATCH, "outcome must be evaluator-sealed")
    if type(value._bytes) is not bytes or hashlib.sha256(value._bytes).hexdigest() != value._digest:
        raise GoldRunViolation(GoldRunFailureCode.IDENTITY_MISMATCH, "outcome changed after verification")
    return value


def _mint(payload: dict[str, object]) -> StructuredOutcome:
    result = object.__new__(StructuredOutcome)
    raw = encode_canonical(payload)
    object.__setattr__(result, "_bytes", raw)
    object.__setattr__(result, "_digest", hashlib.sha256(raw).hexdigest())
    object.__setattr__(result, "_seal", _SEAL)
    return require_structured_outcome(result)


def evaluate_attempt_outcome(verification: VerificationRecord) -> StructuredOutcome:
    checked = require_verification_record(verification)
    facts = checked.payload()
    return _mint({
        "schema_version": STRUCTURED_OUTCOME_SCHEMA_V1, "policy_version": OUTCOME_POLICY_VERSION,
        "scope": "ATTEMPT", "status": _status(facts).value,
        "manifest_sha256": facts["manifest_sha256"], "verification": checked.to_dict(),
        "attempt_outcomes": [], "terminal_decision_sha256": None,
        "publication_result": "NOT_ATTEMPTED", "created_behaviors": [],
        "telemetry_completeness": "UNAVAILABLE", "telemetry_refs": [],
    })


def inspect_outcome(value: object) -> dict[str, object]:
    """Validate a transport projection without minting a trusted outcome."""
    if type(value) is not dict or set(value) != {"outcome_ref", "payload"}:
        raise ValueError("outcome transport has an unknown shape")
    payload = value["payload"]
    fields = {"schema_version", "policy_version", "scope", "status", "manifest_sha256", "verification",
              "attempt_outcomes", "terminal_decision_sha256", "publication_result", "created_behaviors",
              "telemetry_completeness", "telemetry_refs"}
    if type(payload) is not dict or set(payload) != fields:
        raise ValueError("outcome payload has an unknown shape")
    raw = encode_canonical(payload)
    ref = HashBoundRef.from_dict(value["outcome_ref"])
    if (ref.kind is not RefKind.ARTIFACT or ref.schema_id != STRUCTURED_OUTCOME_SCHEMA_V1
            or ref.ref_id != ref.sha256 or ref.sha256 != hashlib.sha256(raw).hexdigest()
            or ref.byte_length != len(raw) or ref.media_type != "application/json"
            or payload["schema_version"] != STRUCTURED_OUTCOME_SCHEMA_V1
            or payload["policy_version"] != OUTCOME_POLICY_VERSION):
        raise ValueError("outcome identity differs from its bytes")
    if (payload["publication_result"] != "NOT_ATTEMPTED" or payload["created_behaviors"] != []
            or payload["telemetry_completeness"] != "UNAVAILABLE" or payload["telemetry_refs"] != []):
        raise ValueError("outcome claims evidence not produced by this stage")
    status = FinalStatus(payload["status"])
    if payload["scope"] == "ATTEMPT":
        facts = inspect_verification_record(payload["verification"])
        if (status is not _status(facts) or payload["manifest_sha256"] != facts["manifest_sha256"]
                or payload["attempt_outcomes"] != [] or payload["terminal_decision_sha256"] is not None):
            raise ValueError("outcome contradicts its verification record")
    elif payload["scope"] == "RUN":
        if payload["verification"] is not None or type(payload["attempt_outcomes"]) is not list:
            raise ValueError("run outcome has an invalid evidence boundary")
        if type(payload["terminal_decision_sha256"]) is not str or len(payload["terminal_decision_sha256"]) != 64:
            raise ValueError("run outcome lacks terminal authority")
        for item in payload["attempt_outcomes"]:
            if type(item) is not dict or set(item) != {"result_sha256", "outcome_ref", "status"}:
                raise ValueError("run outcome has malformed attempt evidence")
            HashBoundRef.from_dict(item["outcome_ref"])
            FinalStatus(item["status"])
    else:
        raise ValueError("outcome has an unknown scope")
    return decode_canonical(raw)


def restore_attempt_outcome(value: object, *, verification: VerificationRecord) -> StructuredOutcome:
    inspect_outcome(value)
    expected = evaluate_attempt_outcome(verification)
    if expected.to_dict() != value:
        raise GoldRunViolation(GoldRunFailureCode.AUTHORITY_MISMATCH, "stored outcome differs from revalidated evidence")
    return expected


def controller_outcome(status: FinalStatus, *, c1_outcome: AttemptOutcome) -> AttemptOutcome:
    """Project verified correctness into the existing controller vocabulary."""
    if type(status) is not FinalStatus or type(c1_outcome) is not AttemptOutcome:
        raise TypeError("controller outcome requires exact vocabulary")
    if status is FinalStatus.INVALID_CONTRACT:
        return AttemptOutcome.C1_RESULT_INVALID
    if status is FinalStatus.FULL:
        return AttemptOutcome.RESOLVED
    if c1_outcome is AttemptOutcome.RESOLVED:
        return AttemptOutcome.UNRESOLVED
    return c1_outcome


def project_run_outcome(*, manifest, attempts, terminal_decision) -> dict[str, object]:
    """Exact terminal projection. Controller revalidates attempts before use."""
    members = []
    for attempt in attempts:
        attempt.validate_identity()
        payload = inspect_outcome(attempt.structured_outcome)
        if payload["scope"] != "ATTEMPT" or payload["manifest_sha256"] != manifest.manifest_sha256:
            raise ValueError("run outcome references a foreign attempt")
        members.append({"result_sha256": attempt.result_sha256,
                        "outcome_ref": attempt.structured_outcome["outcome_ref"], "status": payload["status"]})
    preparation_failure = hasattr(terminal_decision, "failure_sha256")
    status = FinalStatus.INFRA_ERROR if preparation_failure else FinalStatus(members[-1]["status"])
    if any(item["status"] == FinalStatus.INVALID_CONTRACT.value for item in members):
        status = FinalStatus.INVALID_CONTRACT
    digest = terminal_decision.failure_sha256 if preparation_failure else terminal_decision.decision_sha256
    return _mint({
        "schema_version": STRUCTURED_OUTCOME_SCHEMA_V1, "policy_version": OUTCOME_POLICY_VERSION,
        "scope": "RUN", "status": status.value, "manifest_sha256": manifest.manifest_sha256,
        "verification": None, "attempt_outcomes": members, "terminal_decision_sha256": digest,
        "publication_result": "NOT_ATTEMPTED", "created_behaviors": [],
        "telemetry_completeness": "UNAVAILABLE", "telemetry_refs": [],
    }).to_dict()
