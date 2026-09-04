"""Exact boundary from a verified Stage 10 dispatch to unchanged C1.

The adapter performs one field-for-field worker-contract translation, invokes
``run_gold_attempt`` once, then resolves the exact canonical JSONL record C1
wrote.  Controller authority rests on that durable record, not on the returned
Python object or on a second callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    read_regular_bytes,
)
from synapse.experiments.gold.stage10.context_codec import (
    decode_base64url,
    decode_canonical,
    encode_base64url,
    encode_canonical,
)
from synapse.experiments.swebench.contract import ExperimentArm
from synapse.experiments.swebench.gold_attempt_writer import (
    GOLD_EVIDENCE_REJECTED,
    SCHEMA as C1_ATTEMPT_SCHEMA_V1,
    GoldAttemptWriter,
)
from synapse.experiments.swebench.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_NO_CANDIDATE,
    GOLD_ORACLE_UNRESOLVED,
    GoldOracle,
    GoldRunnerCommandPolicy,
    GoldRunnerResult,
    run_gold_attempt,
    validate_attempt_id,
    validate_gold_run_id,
    validate_gold_runner_payload,
)
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
    WorkerReport,
)

from .delivery import (
    CompletedWorkerDelivery,
    require_completed_worker_delivery,
)
from .vocabulary import AttemptOutcome, GoldRunFailureCode, GoldRunViolation


C1_AUTHORITY_RECEIPT_SCHEMA_V1 = (
    "synapse.stage4.gold.runner.c1-authority-receipt/v1"
)
C1_ORACLE_RESULT_SCHEMA_V1 = "synapse.stage4.gold.runner.c1-oracle-result/v1"
_MEDIA_TYPE = "application/json"
_MAX_C1_LOG_BYTES = 64 * 1024 * 1024
_C1_ACCEPTED_STATUSES = frozenset(
    {
        GOLD_NO_CANDIDATE,
        GOLD_INFRA_ERROR,
        GOLD_ORACLE_UNRESOLVED,
        GOLD_APPLIED_WITH_EVIDENCE,
        GOLD_EVIDENCE_REJECTED,
    }
)
_RECEIPT_SEAL = object()
_EXECUTION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class AttemptClassification(tuple):
    """Controller classification without copying C1 evidence into its model."""

    __slots__ = ()

    def __new__(
        cls,
        outcome: AttemptOutcome,
        c1_status: str,
        oracle_invoked: bool,
        oracle_resolved: bool | None,
        write_ok: bool,
    ) -> AttemptClassification:
        return tuple.__new__(
            cls,
            (outcome, c1_status, oracle_invoked, oracle_resolved, write_ok),
        )

    outcome = property(lambda self: self[0])
    c1_status = property(lambda self: self[1])
    oracle_invoked = property(lambda self: self[2])
    oracle_resolved = property(lambda self: self[3])
    write_ok = property(lambda self: self[4])


@dataclass(frozen=True)
class C1AttemptBoundary:
    """The exact unchanged C1 dependencies selected by composition."""

    repo_root: Path
    command_policy: GoldRunnerCommandPolicy
    oracle: GoldOracle
    writer: GoldAttemptWriter
    environment_kind: str

    @property
    def oracle_identity(self) -> str:
        oracle_type = type(self.oracle)
        module = getattr(oracle_type, "__module__", None)
        qualname = getattr(oracle_type, "__qualname__", None)
        if (
            type(module) is not str
            or not module
            or type(qualname) is not str
            or not qualname
            or "<locals>" in qualname
        ):
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "oracle type has no stable import identity",
            )
        identity = f"{module}.{qualname}"
        if len(identity) > 128:
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "oracle type identity exceeds the configured bound",
            )
        return identity

    def __post_init__(self) -> None:
        if type(self.repo_root) is not type(Path()):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "repo_root must be exact")
        if type(self.command_policy) is not GoldRunnerCommandPolicy:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "command policy must be the exact C1 policy",
            )
        if type(self.writer) is not GoldAttemptWriter:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "writer must be exact")
        if self.writer.repo_root != self.repo_root:
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "C1 writer and boundary name different repositories",
            )
        if not callable(getattr(self.oracle, "verify", None)):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle has no verify port")
        self.oracle_identity
        if type(self.environment_kind) is not str or not self.environment_kind:
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "environment kind must be non-empty",
            )


@dataclass(frozen=True, init=False)
class C1AuthorityReceipt:
    gold_run_id: str
    attempt_id: str
    c1_status: str
    requested_status: str
    oracle_invoked: bool
    oracle_resolved: bool | None
    write_ok: bool
    c1_result_ref: HashBoundRef
    oracle_result_ref: HashBoundRef | None
    record_bytes: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> C1AuthorityReceipt:
        raise TypeError("C1AuthorityReceipt is exact-reader created")

    def to_dict(self) -> dict[str, object]:
        checked = require_c1_authority_receipt(self)
        return {
            "schema_version": C1_AUTHORITY_RECEIPT_SCHEMA_V1,
            "gold_run_id": checked.gold_run_id,
            "attempt_id": checked.attempt_id,
            "c1_status": checked.c1_status,
            "requested_status": checked.requested_status,
            "oracle_invoked": checked.oracle_invoked,
            "oracle_resolved": checked.oracle_resolved,
            "write_ok": checked.write_ok,
            "c1_result_ref": checked.c1_result_ref.to_dict(),
            "oracle_result_ref": (
                None
                if checked.oracle_result_ref is None
                else checked.oracle_result_ref.to_dict()
            ),
            "record_bytes_base64url": encode_base64url(checked.record_bytes),
        }

    def canonical_bytes(self) -> bytes:
        return encode_canonical(self.to_dict())

    @property
    def verified_patch_sha256(self) -> str | None:
        if verified_finding_sha256(self) is None:
            return None
        return _strict_json_object(self.record_bytes, line_number=1)["gold_evidence"]["patch_sha256"]


@dataclass(frozen=True, init=False)
class C1AttemptExecution:
    result: GoldRunnerResult
    authority: C1AuthorityReceipt
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> C1AttemptExecution:
        raise TypeError("C1AttemptExecution is boundary-created")


def _external_worker_result(
    delivery: CompletedWorkerDelivery,
) -> ExternalCodingWorkerResult:
    checked = require_completed_worker_delivery(delivery)
    candidate = checked.worker_result
    usage = candidate.usage
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus(candidate.status.value),
        diff_text=candidate.diff_text,
        touched_files=candidate.touched_files,
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus(usage.token_status.value),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thinking_tokens=usage.thinking_tokens,
            total_tokens=usage.total_tokens,
            thinking_included=usage.thinking_included,
            diagnostics=usage.diagnostics,
        ),
        diagnostics=candidate.diagnostics,
        worker_report=WorkerReport(
            summary=candidate.report.summary,
            failure_reason=candidate.report.failure_reason,
        ),
    )


def _strict_json_object(value: bytes, *, line_number: int) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = item
        return result

    try:
        decoded = json.loads(value.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            f"C1 attempt log line {line_number} is malformed",
        ) from exc
    if type(decoded) is not dict:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            f"C1 attempt log line {line_number} is not an object",
        )
    try:
        canonical = json.dumps(
            decoded,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            f"C1 attempt log line {line_number} is not strict JSON",
        ) from exc
    if canonical != value:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            f"C1 attempt log line {line_number} is not canonical",
        )
    return decoded


def _require_writer_evidence(
    value: dict[str, object],
    *,
    payload: dict[str, object],
) -> None:
    """Verify the writer decision and evidence shape for the actual C1 path."""

    writer = value["writer"]
    evidence = value["gold_evidence"]
    expected_validation = (
        "GOLD_EVIDENCE_REJECTED"
        if value["status"] == GOLD_EVIDENCE_REJECTED
        else "GOLD_EVIDENCE_VALIDATED"
        if evidence is not None
        else "GOLD_EVIDENCE_NOT_REQUIRED"
    )
    if writer != {"validation": expected_validation}:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 writer validation and recorded status differ",
        )
    evidence_required = (
        value["status"] in {GOLD_APPLIED_WITH_EVIDENCE, GOLD_ORACLE_UNRESOLVED}
        or payload["oracle_invoked"] is True
    )
    if evidence is not None:
        if type(evidence) is not dict or set(evidence) != {
            "evidence_ref", "verified_commit", "report_path", "report_sha256",
            "base_sha", "task_contract_sha256", "patch_sha256",
        } or any(type(item) is not str or not item for item in evidence.values()):
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "applied C1 record lacks exact Gold evidence",
            )
    elif evidence_required:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 oracle authority record lacks validated Gold evidence",
        )


def _record_payload(value: dict[str, object]) -> dict[str, object]:
    ordinary = {
        "schema", "attempt_id", "run_id", "arm", "status",
        "requested_status", "gold_evidence", "payload", "writer",
    }
    rejected = ordinary | {"failure_code", "detail"}
    if set(value) not in (ordinary, rejected):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 authority record has an unknown shape",
        )
    if (
        value["schema"] != C1_ATTEMPT_SCHEMA_V1
        or value["arm"] != ExperimentArm.GOLD.value
        or value["status"] not in _C1_ACCEPTED_STATUSES
        or type(value["requested_status"]) is not str
        or type(value["payload"]) is not dict
        or type(value["writer"]) is not dict
    ):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 authority record schema, arm, status, or payload is invalid",
        )
    try:
        run_id = validate_gold_run_id(value["run_id"])
        attempt_id = validate_attempt_id(value["attempt_id"])
        validate_gold_runner_payload(
            status=value["status"],
            payload=value["payload"],
            evidence_valid=value["gold_evidence"] is not None,
        )
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 authority record violates its runner contract",
        ) from exc
    payload = value["payload"]
    if payload.get("gold_run_id") != run_id or payload.get("attempt_id") != attempt_id:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 payload and authority key differ",
        )
    _require_writer_evidence(value, payload=payload)
    if (set(value) == rejected) != (value["status"] == GOLD_EVIDENCE_REJECTED):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 rejection fields and status differ",
        )
    return payload


def _artifact_ref(*, schema_id: str, payload: bytes) -> HashBoundRef:
    digest = hashlib.sha256(payload).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=schema_id,
        sha256=digest,
        byte_length=len(payload),
        media_type=_MEDIA_TYPE,
    )


def _oracle_result_ref(payload: dict[str, object]) -> HashBoundRef | None:
    if payload.get("oracle_invoked") is not True:
        return None
    oracle = {
        "oracle_resolved": payload["oracle_resolved"],
        "oracle_infra_error": payload["oracle_infra_error"],
        "oracle_returncode": payload["oracle_returncode"],
        "oracle_duration_seconds": payload["oracle_duration_seconds"],
        "oracle_diagnostics": payload["oracle_diagnostics"],
    }
    return _artifact_ref(
        schema_id=C1_ORACLE_RESULT_SCHEMA_V1,
        payload=encode_canonical(oracle),
    )


def _make_authority_receipt(record_bytes: bytes) -> C1AuthorityReceipt:
    record = _strict_json_object(record_bytes, line_number=1)
    payload = _record_payload(record)
    result = object.__new__(C1AuthorityReceipt)
    fields = {
        "gold_run_id": record["run_id"],
        "attempt_id": record["attempt_id"],
        "c1_status": record["status"],
        "requested_status": record["requested_status"],
        "oracle_invoked": payload["oracle_invoked"],
        "oracle_resolved": (
            payload.get("oracle_resolved")
            if payload["oracle_invoked"] is True
            else None
        ),
        "write_ok": record["status"] != GOLD_EVIDENCE_REJECTED,
        "c1_result_ref": _artifact_ref(
            schema_id=C1_ATTEMPT_SCHEMA_V1,
            payload=record_bytes,
        ),
        "oracle_result_ref": _oracle_result_ref(payload),
        "record_bytes": record_bytes,
        "_trusted_seal": _RECEIPT_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_c1_authority_receipt(result)


def require_c1_authority_receipt(value: object) -> C1AuthorityReceipt:
    if (
        type(value) is not C1AuthorityReceipt
        or getattr(value, "_trusted_seal", None) is not _RECEIPT_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "C1 authority receipt is not exact-reader sealed",
        )
    record = _strict_json_object(value.record_bytes, line_number=1)
    payload = _record_payload(record)
    expected_c1_ref = _artifact_ref(
        schema_id=C1_ATTEMPT_SCHEMA_V1,
        payload=value.record_bytes,
    )
    expected_oracle_ref = _oracle_result_ref(payload)
    expected_resolved = (
        payload.get("oracle_resolved") if payload["oracle_invoked"] is True else None
    )
    if (
        value.gold_run_id != record["run_id"]
        or value.attempt_id != record["attempt_id"]
        or value.c1_status != record["status"]
        or value.requested_status != record["requested_status"]
        or value.oracle_invoked is not payload["oracle_invoked"]
        or value.oracle_resolved is not expected_resolved
        or value.write_ok is not (record["status"] != GOLD_EVIDENCE_REJECTED)
        or value.c1_result_ref.to_dict() != expected_c1_ref.to_dict()
        or (
            None if value.oracle_result_ref is None else value.oracle_result_ref.to_dict()
        )
        != (None if expected_oracle_ref is None else expected_oracle_ref.to_dict())
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "C1 authority receipt differs from its exact durable record",
        )
    return value


def read_c1_authority_receipt(
    boundary: C1AttemptBoundary,
    *,
    gold_run_id: str,
    attempt_id: str,
) -> C1AuthorityReceipt:
    """Resolve one exact attempt from canonical C1 JSONL, refusing ambiguity.

    The bounded scan remains one linear function so duplicate-key detection and
    exact-record selection cannot be invoked as separable, partial readers.
    """

    if type(boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 boundary must be exact")
    validate_gold_run_id(gold_run_id)
    validate_attempt_id(attempt_id)
    try:
        raw = read_regular_bytes(
            boundary.writer.path,
            maximum_bytes=_MAX_C1_LOG_BYTES,
        )
    except PersistenceViolation as exc:
        if exc.failure_code is PersistenceFailureCode.FILESYSTEM_IO_FAILED:
            raise _fail(
                GoldRunFailureCode.RECORD_MISSING,
                "C1 authority log is unavailable",
            ) from exc
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 authority log violates regular-file or resource constraints",
        ) from exc
    if not raw or not raw.endswith(b"\n"):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 authority log is empty or not newline terminated",
        )
    found: bytes | None = None
    seen: set[tuple[str, str]] = set()
    for line_number, framed_line in enumerate(raw[:-1].split(b"\n"), start=1):
        # GoldAttemptWriter writes text-mode JSONL.  Windows therefore frames
        # records with CRLF while POSIX frames them with LF.  The authoritative
        # record is the canonical JSON object, not the platform line delimiter.
        line = framed_line[:-1] if framed_line.endswith(b"\r") else framed_line
        if not line:
            raise _fail(
                GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
                "C1 authority log contains a blank record",
            )
        record = _strict_json_object(line, line_number=line_number)
        _record_payload(record)
        key = (record["run_id"], record["attempt_id"])
        if key in seen:
            raise _fail(
                GoldRunFailureCode.RECORD_CONFLICT,
                "C1 authority log contains a duplicate attempt key",
            )
        seen.add(key)
        if key == (gold_run_id, attempt_id):
            found = line
    if found is None:
        raise _fail(
            GoldRunFailureCode.RECORD_MISSING,
            "C1 authority record is absent after attempt execution",
        )
    return _make_authority_receipt(found)


def c1_authority_receipt_bytes(value: C1AuthorityReceipt) -> bytes:
    return require_c1_authority_receipt(value).canonical_bytes()


def verified_finding_sha256(value: C1AuthorityReceipt) -> str | None:
    """Identity of an independently evaluated candidate, not log provenance.

    No-candidate and infrastructure outcomes establish no tested hypothesis.
    Paths, timestamps, duration, run ids and free-form diagnostics cannot mint
    novelty. C1 retains those fields in its exact receipt for the audit.
    """

    checked = require_c1_authority_receipt(value)
    record = _strict_json_object(checked.record_bytes, line_number=1)
    payload = _record_payload(record)
    if (
        not checked.write_ok
        or not checked.oracle_invoked
        or checked.oracle_resolved is None
        or payload["oracle_infra_error"] is not False
    ):
        return None
    evidence = record["gold_evidence"]
    finding = {
        "schema_version": "synapse.stage4.gold.verified-candidate-finding/v1",
        "base_sha": evidence["base_sha"],
        "task_contract_sha256": evidence["task_contract_sha256"],
        "patch_sha256": evidence["patch_sha256"],
        "oracle_resolved": checked.oracle_resolved,
        "oracle_returncode": payload["oracle_returncode"],
    }
    return hashlib.sha256(encode_canonical(finding)).hexdigest()


def c1_authority_receipt_ref(value: C1AuthorityReceipt) -> HashBoundRef:
    return _artifact_ref(
        schema_id=C1_AUTHORITY_RECEIPT_SCHEMA_V1,
        payload=c1_authority_receipt_bytes(value),
    )


def restore_c1_authority_receipt(
    value: bytes,
    *,
    expected_ref: HashBoundRef,
) -> C1AuthorityReceipt:
    """Restore a checkpointed receipt and re-derive it from embedded C1 bytes."""

    if type(value) is not bytes or type(expected_ref) is not HashBoundRef:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "C1 receipt recovery inputs must be exact",
        )
    expected = _artifact_ref(
        schema_id=C1_AUTHORITY_RECEIPT_SCHEMA_V1,
        payload=value,
    )
    if expected.to_dict() != expected_ref.to_dict():
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "C1 receipt ref differs from checkpoint bytes",
        )
    try:
        decoded = decode_canonical(value)
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "C1 receipt checkpoint is not canonical",
        ) from exc
    if type(decoded) is not dict or set(decoded) != {
        "schema_version", "gold_run_id", "attempt_id", "c1_status",
        "requested_status", "oracle_invoked", "oracle_resolved", "write_ok",
        "c1_result_ref", "oracle_result_ref", "record_bytes_base64url",
    }:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "C1 receipt checkpoint has an unknown shape",
        )
    if decoded["schema_version"] != C1_AUTHORITY_RECEIPT_SCHEMA_V1:
        raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "C1 receipt schema is unknown")
    try:
        restored = _make_authority_receipt(
            decode_base64url(decoded["record_bytes_base64url"])
        )
    except GoldRunViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "C1 receipt checkpoint contains invalid fields",
        ) from exc
    if restored.to_dict() != decoded:
        raise _fail(
            GoldRunFailureCode.IDENTITY_MISMATCH,
            "C1 receipt checkpoint differs from its embedded authority record",
        )
    return restored


def run_c1_attempt(
    boundary: C1AttemptBoundary,
    *,
    gold_run_id: str,
    attempt_id: str,
    delivery: CompletedWorkerDelivery,
    run_root: Path,
) -> C1AttemptExecution:
    """Delegate once to unchanged C1 and resolve the record it durably wrote."""

    if type(boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 boundary must be exact")
    checked = require_completed_worker_delivery(delivery)
    if type(run_root) is not type(Path()):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be exact")
    validate_gold_run_id(gold_run_id)
    validate_attempt_id(attempt_id)
    if boundary.writer.run_root != run_root:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 writer and controller name different run roots",
        )
    if checked.invocation.attempt_id != attempt_id:
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "worker dispatch belongs to another attempt",
        )
    result = run_gold_attempt(
        repo_root=boundary.repo_root,
        gold_run_id=gold_run_id,
        attempt_id=attempt_id,
        worker_result=_external_worker_result(checked),
        command_policy=boundary.command_policy,
        oracle=boundary.oracle,
        writer=boundary.writer,
        run_root=run_root,
        environment_kind=boundary.environment_kind,
    )
    authority = read_c1_authority_receipt(
        boundary,
        gold_run_id=gold_run_id,
        attempt_id=attempt_id,
    )
    record = _strict_json_object(authority.record_bytes, line_number=1)
    # C1's wire contract is JSON. Worker diagnostics may contain Python tuples
    # that its writer serializes as arrays; compare that exact wire projection.
    try:
        returned_payload = json.loads(json.dumps(dict(result.payload), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "returned C1 payload is not JSON data") from exc
    if (
        result.status != authority.c1_status
        or returned_payload != record["payload"]
        or result.write_result.ok is not authority.write_ok
        or (result.oracle_result is not None) is not authority.oracle_invoked
    ):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "returned C1 object differs from its durable authority record",
        )
    execution = object.__new__(C1AttemptExecution)
    object.__setattr__(execution, "result", result)
    object.__setattr__(execution, "authority", authority)
    object.__setattr__(execution, "_trusted_seal", _EXECUTION_SEAL)
    return require_c1_attempt_execution(execution)


def require_c1_attempt_execution(value: object) -> C1AttemptExecution:
    if (
        type(value) is not C1AttemptExecution
        or getattr(value, "_trusted_seal", None) is not _EXECUTION_SEAL
        or type(value.result) is not GoldRunnerResult
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "C1 execution is not boundary sealed",
        )
    require_c1_authority_receipt(value.authority)
    if (
        value.result.status != value.authority.c1_status
    ):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 execution bindings differ",
        )
    return value


def classify_c1_authority_receipt(
    value: C1AuthorityReceipt,
) -> AttemptClassification:
    """Classify durable C1 authority during both live execution and recovery."""

    receipt = require_c1_authority_receipt(value)
    status = receipt.c1_status
    if status == GOLD_APPLIED_WITH_EVIDENCE:
        outcome = (
            AttemptOutcome.RESOLVED
            if receipt.write_ok
            and receipt.oracle_invoked
            and receipt.oracle_resolved is True
            else AttemptOutcome.C1_RESULT_INVALID
        )
    elif status == GOLD_NO_CANDIDATE:
        outcome = AttemptOutcome.NO_CANDIDATE
    elif status == GOLD_INFRA_ERROR:
        outcome = AttemptOutcome.INFRA_ERROR
    else:
        outcome = AttemptOutcome.UNRESOLVED
    return AttemptClassification(
        outcome,
        status,
        receipt.oracle_invoked,
        receipt.oracle_resolved,
        receipt.write_ok,
    )


def classify_c1_attempt(value: C1AttemptExecution) -> AttemptClassification:
    """Classify a live boundary result through its durable authority receipt."""

    return classify_c1_authority_receipt(
        require_c1_attempt_execution(value).authority
    )


__all__ = [
    "C1_AUTHORITY_RECEIPT_SCHEMA_V1",
    "C1_ORACLE_RESULT_SCHEMA_V1",
    "AttemptClassification",
    "C1AttemptBoundary",
    "C1AttemptExecution",
    "C1AuthorityReceipt",
    "c1_authority_receipt_bytes",
    "c1_authority_receipt_ref",
    "classify_c1_authority_receipt",
    "classify_c1_attempt",
    "read_c1_authority_receipt",
    "require_c1_attempt_execution",
    "require_c1_authority_receipt",
    "restore_c1_authority_receipt",
    "run_c1_attempt",
]
