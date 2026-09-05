"""Exact boundary from a verified Stage 10 dispatch to unchanged C1.

The adapter performs one field-for-field worker-contract translation, invokes
``run_gold_attempt`` once, then resolves the exact canonical JSONL record C1
wrote.  Controller authority rests on that durable record, not on the returned
Python object or on a second callback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
import hashlib
import json
import subprocess

from synapse.change.contract import parse_task_contract_text

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
    GoldRunnerCommandExpectation,
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
from synapse.experiments.swebench.gold_oracle_binding import GoldSWEbenchOracleBinding
from synapse.experiments.swebench.gold_evidence import GoldEvidence, seal_gold_evidence
from synapse.experiments.swebench.swebench_reports import parse_swebench_report
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig, build_oracle_config_fingerprint_payload, compute_oracle_config_fingerprint,
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
_VERIFICATION_SEAL = object()


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


def command_policy_reference(policy: GoldRunnerCommandPolicy) -> HashBoundRef:
    """Bind governing verification to the exact unchanged C1 command policy."""
    if type(policy) is not GoldRunnerCommandPolicy:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "command policy must be exact")
    raw = encode_canonical(json.loads(json.dumps(asdict(policy))))
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(RefKind.CONTRACT_CONDITION, digest,
                        "synapse.stage4.gold.c1-command-policy/v1", digest, len(raw), _MEDIA_TYPE)


def command_policy_from_payload(value: object) -> GoldRunnerCommandPolicy:
    if type(value) is not dict or set(value) != {item.name for item in fields(GoldRunnerCommandPolicy)}:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 command policy fields differ")
    parsed = dict(value)
    for name in ("reproduction_before", "reproduction_after"):
        raw = parsed[name]
        if type(raw) is not dict or set(raw) != {item.name for item in fields(GoldRunnerCommandExpectation)}:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 expectation fields differ")
        expectation = dict(raw)
        for field_name in ("expected_exit_codes", "combined_output_contains", "combined_output_not_contains"):
            if expectation[field_name] is not None:
                if type(expectation[field_name]) is not list:
                    raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 expectation collection must be a list")
                expectation[field_name] = tuple(expectation[field_name])
        parsed[name] = GoldRunnerCommandExpectation(**expectation)
    for name in ("allowed_scope", "reproduction_command", "reproduction_committed_inputs", "required_scaffold_paths"):
        if type(parsed[name]) is not list:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 paths and commands must be lists")
        parsed[name] = tuple(parsed[name])
    for name in ("baseline_commands", "acceptance_commands", "full_suite_commands"):
        if type(parsed[name]) is not list or any(type(item) is not list for item in parsed[name]):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 commands must be lists of argv")
        parsed[name] = tuple(tuple(item) for item in parsed[name])
    return GoldRunnerCommandPolicy(**parsed)


def compose_c1_boundary(*, repo_root: Path, run_root: Path, command_policy: GoldRunnerCommandPolicy,
                        oracle_config: dict[str, object], environment_kind: str) -> C1AttemptBoundary:
    """Reopen the existing C1/C2 path from frozen data, without a Python factory input."""
    if type(oracle_config) is not dict or set(oracle_config) != {item.name for item in fields(SWEbenchHarnessOracleConfig)}:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle configuration must be explicit and complete")
    config = SWEbenchHarnessOracleConfig(**oracle_config)
    return C1AttemptBoundary(
        repo_root=repo_root, command_policy=command_policy,
        oracle=GoldSWEbenchOracleBinding(config),
        writer=GoldAttemptWriter(run_root, repo_root=repo_root, report_root=run_root / "controlled-change-reports"),
        environment_kind=environment_kind,
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


@dataclass(frozen=True, init=False)
class C1VerificationEvidence:
    """Read-only facts established from C1/C2's retained evidence, never FULL."""

    _payload_bytes: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("C1VerificationEvidence is boundary-created")

    def payload(self) -> dict[str, object]:
        if getattr(self, "_trusted_seal", None) is not _VERIFICATION_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 verification evidence is not sealed")
        return decode_canonical(self._payload_bytes)


def _verification_git(repo: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=repo, capture_output=True, check=False, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "retained C1 git evidence could not be read") from exc
    if result.returncode:
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "retained C1 git evidence is unavailable")
    return result.stdout


def _evidence_document(raw: bytes) -> dict[str, object]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate evidence key")
            result[key] = value
        return result

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    if type(result) is not dict:
        raise ValueError("evidence document must be an object")
    json.dumps(result, allow_nan=False)
    return result


def _require_report_policy(task, *, policy, task_path: str, patch_path: str, receipt) -> None:
    scaffold = tuple(dict.fromkeys((task_path, patch_path, *policy.required_scaffold_paths,
                                    *policy.reproduction_committed_inputs)))
    if (
        task.task_id != f"{policy.task_id}-gold-{receipt.gold_run_id}-{receipt.attempt_id}"
        or task.task_class != policy.task_class
        or task.base_revision != "HEAD"
        or task.target_ref != f"refs/heads/synapse/gold/{receipt.gold_run_id}/{receipt.attempt_id}"
        or task.allowed_scope.exact
        or task.allowed_scope.prefixes != tuple(path.rstrip("/") for path in policy.allowed_scope)
        or task.patch_path != patch_path
        or task.required_scaffold_paths != scaffold
        or task.reproduction.command != policy.reproduction_command
        or task.reproduction.committed_inputs != policy.reproduction_committed_inputs
        or asdict(task.reproduction.before) != asdict(policy.reproduction_before)
        or asdict(task.reproduction.after) != asdict(policy.reproduction_after)
        or task.baseline_commands != policy.baseline_commands
        or task.acceptance_commands != policy.acceptance_commands
        or task.full_suite_commands != policy.full_suite_commands
        or task.commit_message != policy.commit_message
    ):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "committed C1 task differs from frozen command policy")


def _report_commands_complete(report, task) -> bool:
    phases = report.get("phases")
    if type(phases) is not list or any(type(item) is not dict for item in phases):
        return False
    by_name = {item.get("name"): item for item in phases}
    if len(by_name) != len(phases):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C1 report repeats phase identities")
    expected = [("reproduction_before", task.reproduction.command),
                ("reproduction_after", task.reproduction.command)]
    for prefix, commands in (("baseline", task.baseline_commands),
                             ("acceptance", task.acceptance_commands),
                             ("full_suite", task.full_suite_commands)):
        expected.extend((f"{prefix}_{index}", command) for index, command in enumerate(commands, 1))
    for name, command in expected:
        phase = by_name.get(name)
        if phase is None or phase.get("status") != "PASS" or phase.get("command") != list(command):
            return False
        if type(phase.get("returncode")) is not int:
            return False
        if not name.startswith("reproduction_") and phase["returncode"] != 0:
            return False
        if name.startswith("reproduction_"):
            expectation = task.reproduction.before if name == "reproduction_before" else task.reproduction.after
            if type(phase.get("stdout")) is not str or type(phase.get("stderr")) is not str:
                return False
            combined = phase["stdout"] + phase["stderr"]
            if (expectation.expected_exit_codes is not None and phase["returncode"] not in expectation.expected_exit_codes
                    or expectation.expected_nonzero_exit and phase["returncode"] == 0
                    or any(text not in combined for text in expectation.combined_output_contains)
                    or any(text in combined for text in expectation.combined_output_not_contains)):
                return False
    for name in ("initial_worktree_integrity", "baseline_integrity", "scope_check_after_patch",
                 "scope_check_before_commit", "candidate_integrity"):
        if by_name.get(name, {}).get("status") != "PASS":
            return False
    return True


def _check_oracle_pair(boundary, *, evidence, payload, changed_paths, model_patch):
    diagnostics = payload.get("oracle_diagnostics", {})
    expected = {
        "verified_commit": evidence.verified_commit, "base_sha": evidence.base_sha,
        "model_patch_sha256": hashlib.sha256(model_patch).hexdigest(),
        "instance_id": boundary.command_policy.instance_id,
        "task_id": boundary.command_policy.task_id,
        "resolved": payload.get("oracle_resolved"),
    }
    for key, value in expected.items():
        if key in diagnostics and diagnostics[key] != value:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "oracle evidence names a different candidate or task")
    if "changed_paths" in diagnostics and set(diagnostics["changed_paths"]) != set(changed_paths):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "oracle scope differs from verified commit pair")
    if type(boundary.oracle) is not GoldSWEbenchOracleBinding or payload.get("oracle_infra_error") is True:
        return
    observed_config = diagnostics.get("oracle_config_fingerprint_payload")
    if type(observed_config) is not dict or type(observed_config.get("swebench_version")) is not str:
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C2 evidence lacks its observed configuration")
    expected_config = build_oracle_config_fingerprint_payload(
        boundary.oracle.config, swebench_version=observed_config["swebench_version"],
    )
    if (observed_config != expected_config
            or diagnostics.get("oracle_config_fingerprint") != compute_oracle_config_fingerprint(expected_config)):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C2 evidence uses another oracle configuration")
    if any(key not in diagnostics for key in ("verified_commit", "base_sha", "model_patch_sha256", "instance_id")):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C2 oracle lacks verified commit-pair evidence")
    reports = [item for item in diagnostics.get("oracle_managed_artifacts", ())
               if type(item) is dict and item.get("kind") == "swebench_report"]
    if len(reports) != 1 or reports[0].get("path") != diagnostics.get("report_path"):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C2 report evidence is absent or ambiguous")
    artifact = reports[0]
    path = Path(artifact["path"])
    if not path.resolve().is_relative_to(boundary.oracle.config.swebench_work_dir.resolve()):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C2 report is outside its configured root")
    raw = read_regular_bytes(path, maximum_bytes=16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != artifact.get("sha256") or len(raw) != artifact.get("bytes"):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "retained C2 report digest differs")
    parsed = parse_swebench_report(path, boundary.command_policy.instance_id)
    if (parsed.resolved is not payload["oracle_resolved"] or not parsed.target_instance_found
            or parsed.diagnostics.get("infra_error") is True
            or read_regular_bytes(path, maximum_bytes=16 * 1024 * 1024) != raw):
        raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "retained C2 report differs from oracle verdict")


def read_c1_verification_evidence(
    boundary: C1AttemptBoundary, *, receipt: C1AuthorityReceipt,
    base_revision: str, run_root: Path,
) -> C1VerificationEvidence:
    """Resolve public evidence contracts without repeating any execution."""
    checked = require_c1_authority_receipt(receipt)
    record = _strict_json_object(checked.record_bytes, line_number=1)
    payload = _record_payload(record)
    facts = {
        "c1_result_ref": checked.c1_result_ref.to_dict(),
        "oracle_result_ref": None if checked.oracle_result_ref is None else checked.oracle_result_ref.to_dict(),
        "command_policy_ref": command_policy_reference(boundary.command_policy).to_dict(),
        "c1_status": checked.c1_status, "oracle_resolved": checked.oracle_resolved,
        "infra_error": checked.c1_status == GOLD_INFRA_ERROR or payload.get("oracle_infra_error") is True,
        "no_candidate": checked.c1_status == GOLD_NO_CANDIDATE,
        "refused": payload.get("controlled_change_outcome") not in (None, "APPLIED"),
        "evidence_ref": None, "report_ref": None, "report_schema": None, "task_ref": None,
        "commands_complete": False, "changed_paths": {},
        "verified_patch_sha256": None, "verified_revision": None,
    }
    if record["gold_evidence"] is not None:
        evidence = GoldEvidence(**record["gold_evidence"])
        report_root = run_root / "controlled-change-reports"
        seal_gold_evidence(evidence, repo_root=boundary.repo_root, report_root=report_root)
        raw = read_regular_bytes(report_root / evidence.report_path, maximum_bytes=16 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != evidence.report_sha256:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C1 report changed after validation")
        report = _evidence_document(raw)
        if report.get("schema") != "personal_slice.report/v0.5.0":
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C1 report schema is unsupported")
        prefix = f"controlled_changes/gold/{checked.gold_run_id}/{checked.attempt_id}"
        task_path, patch_path = f"{prefix}/task.json", f"{prefix}/candidate.patch"
        bridge_parent = _verification_git(boundary.repo_root, "rev-list", "--parents", "-n", "1", evidence.base_sha).decode().split()
        verified_parent = _verification_git(boundary.repo_root, "rev-list", "--parents", "-n", "1", evidence.verified_commit).decode().split()
        if bridge_parent != [evidence.base_sha, base_revision] or verified_parent != [evidence.verified_commit, evidence.base_sha]:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C1 evidence is not descended from the frozen base")
        bridge_paths = _verification_git(boundary.repo_root, "diff", "--name-only", "-z", base_revision, evidence.base_sha).decode().split("\0")
        if set(filter(None, bridge_paths)) != {task_path, patch_path}:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "C1 bridge contains undeclared changes")
        task_bytes = _verification_git(boundary.repo_root, "show", f"{evidence.base_sha}:{task_path}")
        patch_bytes = _verification_git(boundary.repo_root, "show", f"{evidence.base_sha}:{patch_path}")
        if hashlib.sha256(task_bytes).hexdigest() != evidence.task_contract_sha256 or hashlib.sha256(patch_bytes).hexdigest() != evidence.patch_sha256:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "committed C1 inputs differ from evidence")
        task = parse_task_contract_text(task_bytes.decode("utf-8"))
        _require_report_policy(task, policy=boundary.command_policy, task_path=task_path, patch_path=patch_path, receipt=checked)
        if (report["task"]["task_id"] != task.task_id
                or report["task"]["target_ref"] != task.target_ref
                or report["run"]["run_id"] != payload["controlled_change_run_id"]
                or report["run"]["environment_kind"] != boundary.environment_kind):
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "report belongs to another controlled-change run")
        delta = _verification_git(boundary.repo_root, "diff", "--name-status", "-z", "--no-renames", evidence.base_sha, evidence.verified_commit).decode().split("\0")
        if delta[-1] != "" or len(delta) % 2 != 1:
            raise _fail(GoldRunFailureCode.C1_BOUNDARY_MISMATCH, "verified commit delta is malformed")
        changed = dict(zip(delta[1:-1:2], delta[:-1:2]))
        model_patch = _verification_git(boundary.repo_root, "diff", "--binary", "--find-renames", f"{evidence.base_sha}..{evidence.verified_commit}")
        if checked.oracle_invoked:
            _check_oracle_pair(boundary, evidence=evidence, payload=payload, changed_paths=changed, model_patch=model_patch)
        facts.update(
            evidence_ref=_artifact_ref(schema_id="synapse.stage4.gold.c1-evidence/v1", payload=encode_canonical(record["gold_evidence"])).to_dict(),
            # HashBoundRef versions reference profiles with integer /vN. Keep
            # the foreign report's semantic version explicit; hash its actual
            # bytes, without rewriting the unchanged C1 document.
            report_ref=_artifact_ref(schema_id="synapse.stage4.gold.c1-report-bytes/v1", payload=raw).to_dict(),
            report_schema=report["schema"],
            task_ref=_artifact_ref(schema_id=task.schema, payload=task_bytes).to_dict(),
            commands_complete=_report_commands_complete(report, task), changed_paths=changed,
            verified_patch_sha256=evidence.patch_sha256, verified_revision=evidence.verified_commit,
        )
    result = object.__new__(C1VerificationEvidence)
    object.__setattr__(result, "_payload_bytes", encode_canonical(facts))
    object.__setattr__(result, "_trusted_seal", _VERIFICATION_SEAL)
    return result


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
