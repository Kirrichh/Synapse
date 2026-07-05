"""Stage 4 C1 GOLD runner tenant.

This module consumes an already-obtained external worker result. It does not
run workers, SWE-bench, providers, Docker, telemetry, paired measurement, or
FULL verification.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from synapse.change import ControlledChangeRequest, execute_controlled_change
from synapse.change.contract import TASK_CONTRACT_SCHEMA, validate_repo_relative_path
from synapse.change.outcomes import APPLIED
from synapse.change.runner import ControlledChangeResult
from synapse.change.workspace import Worktree, cleanup_worktree, create_detached_worktree
from synapse.experiments.swebench.contract import BaselineTask, OracleResult
from synapse.experiments.swebench.gold_attempt_writer import (
    GOLD_ATTEMPT_WRITTEN,
    GOLD_EVIDENCE_REJECTED,
    GoldAttemptWriteResult,
    GoldAttemptWriter,
)
from synapse.experiments.swebench.gold_evidence import (
    GoldEvidence,
    GoldEvidenceValidationResult,
    validate_gold_evidence,
)
from synapse.worker.candidate_materializer import (
    MaterializationStatus,
    MaterializedCandidate,
    materialize_worker_candidate,
)
from synapse.worker.contract import ExternalCodingWorkerResult


GOLD_NO_CANDIDATE = "GOLD_NO_CANDIDATE"
GOLD_INFRA_ERROR = "GOLD_INFRA_ERROR"
GOLD_ORACLE_UNRESOLVED = "GOLD_ORACLE_UNRESOLVED"
GOLD_APPLIED_WITH_EVIDENCE = "GOLD_APPLIED_WITH_EVIDENCE"
BASELINE_PREEXISTING_FAILURE_REASON = "BASELINE_PREEXISTING_FAILURE"

_GOLD_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ATTEMPT_ID_RE = re.compile(r"^[1-9][0-9]*$")


class GoldRunnerError(ValueError):
    """Raised when C1 runner input or payload is invalid."""


class GoldOracle(Protocol):
    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        ...


@dataclass(frozen=True)
class GoldRunnerCommandExpectation:
    expected_exit_codes: tuple[int, ...] | None = None
    expected_nonzero_exit: bool = False
    combined_output_contains: tuple[str, ...] = ()
    combined_output_not_contains: tuple[str, ...] = ()
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if self.expected_exit_codes is not None:
            if not self.expected_exit_codes:
                raise GoldRunnerError("expected_exit_codes must not be empty when provided")
            if not all(isinstance(code, int) for code in self.expected_exit_codes):
                raise GoldRunnerError("expected_exit_codes must contain integers")
            object.__setattr__(self, "expected_exit_codes", tuple(self.expected_exit_codes))
        if not isinstance(self.expected_nonzero_exit, bool):
            raise GoldRunnerError("expected_nonzero_exit must be a boolean")
        object.__setattr__(self, "combined_output_contains", _string_tuple(self.combined_output_contains, "combined_output_contains", allow_empty=True))
        object.__setattr__(self, "combined_output_not_contains", _string_tuple(self.combined_output_not_contains, "combined_output_not_contains", allow_empty=True))
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise GoldRunnerError("timeout_seconds must be a positive integer")

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = {
            "expected_nonzero_exit": self.expected_nonzero_exit,
            "combined_output_contains": list(self.combined_output_contains),
            "combined_output_not_contains": list(self.combined_output_not_contains),
            "timeout_seconds": self.timeout_seconds,
        }
        if self.expected_exit_codes is not None:
            data["expected_exit_codes"] = list(self.expected_exit_codes)
        return data


@dataclass(frozen=True)
class GoldRunnerCommandPolicy:
    task_id: str
    instance_id: str
    statement: str
    allowed_scope: tuple[str, ...]
    reproduction_command: tuple[str, ...]
    reproduction_committed_inputs: tuple[str, ...]
    reproduction_before: GoldRunnerCommandExpectation
    reproduction_after: GoldRunnerCommandExpectation
    baseline_commands: tuple[tuple[str, ...], ...]
    acceptance_commands: tuple[tuple[str, ...], ...]
    full_suite_commands: tuple[tuple[str, ...], ...]
    commit_message: str
    required_scaffold_paths: tuple[str, ...]
    task_class: str = "SWE_BENCH"

    def __post_init__(self) -> None:
        for field_name in ("task_id", "instance_id", "statement", "commit_message", "task_class"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise GoldRunnerError(f"{field_name} must be a non-empty string")
        object.__setattr__(self, "allowed_scope", _path_tuple(self.allowed_scope, "allowed_scope"))
        object.__setattr__(self, "reproduction_command", _command_tuple(self.reproduction_command, "reproduction_command"))
        object.__setattr__(self, "reproduction_committed_inputs", _path_tuple(self.reproduction_committed_inputs, "reproduction_committed_inputs"))
        object.__setattr__(self, "baseline_commands", _commands_tuple(self.baseline_commands, "baseline_commands", allow_empty=True))
        object.__setattr__(self, "acceptance_commands", _commands_tuple(self.acceptance_commands, "acceptance_commands", allow_empty=False))
        object.__setattr__(self, "full_suite_commands", _commands_tuple(self.full_suite_commands, "full_suite_commands", allow_empty=False))
        object.__setattr__(self, "required_scaffold_paths", _path_tuple(self.required_scaffold_paths, "required_scaffold_paths"))


@dataclass(frozen=True)
class GoldRunnerResult:
    status: str
    write_result: GoldAttemptWriteResult
    payload: Mapping[str, Any]
    materialized_candidate: MaterializedCandidate
    controlled_change_result: ControlledChangeResult | None
    gold_evidence: GoldEvidence | None
    evidence_validation: GoldEvidenceValidationResult | None
    oracle_result: OracleResult | None
    bridge_commit: str | None
    task_path: str | None
    patch_path: str | None
    target_ref: str
    report_root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def validate_gold_run_id(value: str) -> str:
    if not isinstance(value, str) or not _GOLD_RUN_ID_RE.fullmatch(value):
        raise GoldRunnerError("gold_run_id must match [A-Za-z0-9_-]+")
    return value


def validate_attempt_id(value: str) -> str:
    if not isinstance(value, str) or not _ATTEMPT_ID_RE.fullmatch(value):
        raise GoldRunnerError("attempt_id must be a decimal string without padding")
    return value


def validate_gold_runner_payload(*, status: str, payload: Mapping[str, Any], evidence_valid: bool = False) -> None:
    if status == "GOLD_FULL_VERIFIED":
        raise GoldRunnerError("GOLD_FULL_VERIFIED is outside C1 scope")
    if status not in {
        GOLD_NO_CANDIDATE,
        GOLD_INFRA_ERROR,
        GOLD_ORACLE_UNRESOLVED,
        GOLD_APPLIED_WITH_EVIDENCE,
        GOLD_EVIDENCE_REJECTED,
    }:
        raise GoldRunnerError(f"unsupported C1 GOLD status: {status}")

    required = {
        "gold_run_id",
        "attempt_id",
        "controlled_change_run_id",
        "materialization_status",
        "materialization_diagnostics",
        "controlled_change_outcome",
        "failure_phase",
        "failure_code",
    }
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise GoldRunnerError(f"payload missing required field: {missing[0]}")

    if payload["materialization_status"] == MaterializationStatus.REJECTED_SCOPE_VIOLATION.value and "scope_violations" not in payload:
        raise GoldRunnerError("payload missing scope_violations for REJECTED_SCOPE_VIOLATION")

    if "oracle_invoked" not in payload:
        raise GoldRunnerError("payload missing oracle_invoked")
    if payload["oracle_invoked"] is True:
        for key in (
            "oracle_resolved",
            "oracle_infra_error",
            "oracle_returncode",
            "oracle_duration_seconds",
            "oracle_diagnostics",
        ):
            if key not in payload:
                raise GoldRunnerError(f"payload missing {key} for oracle attempt")
    elif payload["oracle_invoked"] is False:
        if "skip_reason" not in payload:
            raise GoldRunnerError("payload missing skip_reason for skipped oracle")
        if payload.get("controlled_change_outcome") == APPLIED and evidence_valid:
            raise GoldRunnerError("APPLIED with valid evidence requires oracle invocation")
    else:
        raise GoldRunnerError("oracle_invoked must be a boolean")

    try:
        json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise GoldRunnerError(f"payload is not JSON-serializable: {exc}") from exc


def run_gold_attempt(
    *,
    repo_root: str | Path,
    gold_run_id: str,
    attempt_id: str,
    worker_result: ExternalCodingWorkerResult,
    command_policy: GoldRunnerCommandPolicy,
    oracle: GoldOracle,
    writer: GoldAttemptWriter,
    run_root: str | Path,
    environment_kind: str = "TEST",
) -> GoldRunnerResult:
    """Run one C1 GOLD attempt from an already-obtained worker result."""

    gold_run_id = validate_gold_run_id(gold_run_id)
    attempt_id = validate_attempt_id(attempt_id)
    repo = Path(repo_root)
    run_root_path = Path(run_root)
    report_root = run_root_path / "controlled-change-reports"
    target_ref = _target_ref(gold_run_id, attempt_id)
    base_commit = _git(repo, "rev-parse", "HEAD").strip()

    materialized = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=command_policy.allowed_scope,
        base_commit=base_commit,
    )

    if materialized.status is not MaterializationStatus.MATERIALIZED:
        status = _status_for_materialization(materialized.status)
        payload = _base_payload(
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            materialized=materialized,
            controlled_change_result=None,
            failure_phase="materialization",
            failure_code=materialized.status.value,
        )
        payload.update({"oracle_invoked": False, "skip_reason": "materialization_not_materialized"})
        if materialized.status is MaterializationStatus.REJECTED_SCOPE_VIOLATION:
            payload["scope_violations"] = list(materialized.scope_violations)
        validate_gold_runner_payload(status=status, payload=payload)
        write_result = writer.write_attempt(
            attempt_id=attempt_id,
            run_id=gold_run_id,
            status=status,
            gold_evidence=None,
            payload=payload,
        )
        return _result(
            status=status,
            write_result=write_result,
            payload=payload,
            materialized=materialized,
            controlled_change_result=None,
            gold_evidence=None,
            evidence_validation=None,
            oracle_result=None,
            bridge_commit=None,
            task_path=None,
            patch_path=None,
            target_ref=target_ref,
            report_root=report_root,
        )

    if materialized.patch_text is None:
        status = GOLD_INFRA_ERROR
        payload = _base_payload(
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            materialized=materialized,
            controlled_change_result=None,
            failure_phase="materialization",
            failure_code="MATERIALIZED_PATCH_TEXT_MISSING",
        )
        payload.update({"oracle_invoked": False, "skip_reason": "materialized_patch_text_missing"})
        validate_gold_runner_payload(status=status, payload=payload)
        write_result = writer.write_attempt(
            attempt_id=attempt_id,
            run_id=gold_run_id,
            status=status,
            gold_evidence=None,
            payload=payload,
        )
        return _result(status, write_result, payload, materialized, None, None, None, None, None, None, None, target_ref, report_root)

    try:
        bridge = _create_bridge_commit(
            repo=repo,
            base_commit=base_commit,
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            patch_text=materialized.patch_text,
            command_policy=command_policy,
            target_ref=target_ref,
        )
    except Exception as exc:
        status = GOLD_INFRA_ERROR
        payload = _base_payload(
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            materialized=materialized,
            controlled_change_result=None,
            failure_phase="bridge_commit",
            failure_code=type(exc).__name__,
        )
        payload.update(
            {
                "oracle_invoked": False,
                "skip_reason": "bridge_commit_failed",
                "bridge_error": f"{type(exc).__name__}: {exc}",
            }
        )
        validate_gold_runner_payload(status=status, payload=payload)
        write_result = writer.write_attempt(
            attempt_id=attempt_id,
            run_id=gold_run_id,
            status=status,
            gold_evidence=None,
            payload=payload,
        )
        return _result(status, write_result, payload, materialized, None, None, None, None, None, None, None, target_ref, report_root)

    controlled = _execute_controlled_change_from_repo(
        repo=repo,
        base=bridge.commit_sha,
        task_path=bridge.task_path,
        report_root=report_root,
        environment_kind=environment_kind,
    )

    if controlled.outcome != APPLIED:
        status = _status_for_controlled_change_failure(controlled)
        payload = _base_payload(
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            materialized=materialized,
            controlled_change_result=controlled,
            failure_phase=controlled.failure_phase,
            failure_code=controlled.failure_code,
        )
        payload.update({"oracle_invoked": False, "skip_reason": "controlled_change_not_applied"})
        if _is_baseline_preexisting_failure(controlled):
            payload["failure_reason"] = BASELINE_PREEXISTING_FAILURE_REASON
        validate_gold_runner_payload(status=status, payload=payload)
        write_result = writer.write_attempt(
            attempt_id=attempt_id,
            run_id=gold_run_id,
            status=status,
            gold_evidence=None,
            payload=payload,
        )
        return _result(status, write_result, payload, materialized, controlled, None, None, None, bridge.commit_sha, bridge.task_path, bridge.patch_path, target_ref, report_root)

    evidence = _gold_evidence_from_result(controlled, report_root=report_root)
    validation = validate_gold_evidence(evidence, repo_root=repo, report_root=report_root)
    if not validation.ok:
        status = GOLD_EVIDENCE_REJECTED
        payload = _base_payload(
            gold_run_id=gold_run_id,
            attempt_id=attempt_id,
            materialized=materialized,
            controlled_change_result=controlled,
            failure_phase="gold_evidence",
            failure_code=validation.failure_code,
        )
        payload.update(
            {
                "oracle_invoked": False,
                "skip_reason": "gold_evidence_validation_failed",
                "gold_evidence_validation_detail": validation.detail,
            }
        )
        validate_gold_runner_payload(status=status, payload=payload, evidence_valid=False)
        write_result = writer.write_attempt(
            attempt_id=attempt_id,
            run_id=gold_run_id,
            status=status,
            gold_evidence=None,
            payload=payload,
        )
        return _result(status, write_result, payload, materialized, controlled, None, validation, None, bridge.commit_sha, bridge.task_path, bridge.patch_path, target_ref, report_root)

    oracle_result, cleanup_diagnostic = _run_oracle(
        repo=repo,
        verified_commit=controlled.verified_commit,
        oracle=oracle,
        command_policy=command_policy,
    )
    status = _status_for_oracle(oracle_result)
    payload = _base_payload(
        gold_run_id=gold_run_id,
        attempt_id=attempt_id,
        materialized=materialized,
        controlled_change_result=controlled,
        failure_phase=None if status in {GOLD_APPLIED_WITH_EVIDENCE, GOLD_ORACLE_UNRESOLVED} else "oracle",
        failure_code=None if status in {GOLD_APPLIED_WITH_EVIDENCE, GOLD_ORACLE_UNRESOLVED} else "ORACLE_INFRA_ERROR",
    )
    payload.update(_oracle_payload(oracle_result))
    if cleanup_diagnostic is not None:
        payload.setdefault("diagnostics", []).append(cleanup_diagnostic)
        payload["oracle_cleanup_diagnostic"] = cleanup_diagnostic
    validate_gold_runner_payload(status=status, payload=payload, evidence_valid=True)
    write_result = writer.write_attempt(
        attempt_id=attempt_id,
        run_id=gold_run_id,
        status=status,
        gold_evidence=evidence,
        payload=payload,
    )
    return _result(status, write_result, payload, materialized, controlled, evidence, validation, oracle_result, bridge.commit_sha, bridge.task_path, bridge.patch_path, target_ref, report_root)


@dataclass(frozen=True)
class _BridgeCommit:
    commit_sha: str
    task_path: str
    patch_path: str


def _string_tuple(values: Sequence[str], field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise GoldRunnerError(f"{field_name} must be a sequence of strings")
    if not allow_empty and not values:
        raise GoldRunnerError(f"{field_name} must not be empty")
    if not all(isinstance(value, str) and value for value in values):
        raise GoldRunnerError(f"{field_name} must contain only non-empty strings")
    return tuple(values)


def _path_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    return tuple(validate_repo_relative_path(value, f"{field_name}[]") for value in _string_tuple(values, field_name, allow_empty=False))


def _command_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    return _string_tuple(values, field_name, allow_empty=False)


def _commands_tuple(values: Sequence[Sequence[str]], field_name: str, *, allow_empty: bool) -> tuple[tuple[str, ...], ...]:
    if not isinstance(values, (tuple, list)):
        raise GoldRunnerError(f"{field_name} must be a sequence of argv lists")
    if not allow_empty and not values:
        raise GoldRunnerError(f"{field_name} must not be empty")
    return tuple(_command_tuple(value, f"{field_name}[]") for value in values)


def _git(repo: Path, *args: str, input_bytes: bytes | None = None, env: Mapping[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        capture_output=True,
        check=True,
        env=None if env is None else {**os.environ, **dict(env)},
    )
    return completed.stdout.decode("utf-8", "replace")


def _target_ref(gold_run_id: str, attempt_id: str) -> str:
    return f"refs/heads/synapse/gold/{gold_run_id}/{attempt_id}"


def _task_patch_paths(gold_run_id: str, attempt_id: str) -> tuple[str, str]:
    base = f"controlled_changes/gold/{gold_run_id}/{attempt_id}"
    return f"{base}/task.json", f"{base}/candidate.patch"


def _create_bridge_commit(
    *,
    repo: Path,
    base_commit: str,
    gold_run_id: str,
    attempt_id: str,
    patch_text: str,
    command_policy: GoldRunnerCommandPolicy,
    target_ref: str,
) -> _BridgeCommit:
    task_path, patch_path = _task_patch_paths(gold_run_id, attempt_id)
    task_json = _task_json(
        gold_run_id=gold_run_id,
        attempt_id=attempt_id,
        task_path=task_path,
        patch_path=patch_path,
        command_policy=command_policy,
        target_ref=target_ref,
    )
    bridge_files = {
        task_path: json.dumps(task_json, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        patch_path: patch_text.encode("utf-8"),
    }
    index_fd, index_name = tempfile.mkstemp(prefix="synapse-gold-bridge-index-")
    os.close(index_fd)
    index_path = Path(index_name)
    index_path.unlink()
    try:
        env = {"GIT_INDEX_FILE": str(index_path)}
        _git(repo, "read-tree", base_commit, env=env)
        for rel_path, data in bridge_files.items():
            blob = _git(repo, "hash-object", "-w", "--stdin", input_bytes=data).strip()
            _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel_path}", env=env)
        tree = _git(repo, "write-tree", env=env).strip()
        commit = _git(
            repo,
            "commit-tree",
            tree,
            "-p",
            base_commit,
            "-m",
            f"Stage C1 GOLD bridge {gold_run_id}/{attempt_id}",
        ).strip()
        return _BridgeCommit(commit_sha=commit, task_path=task_path, patch_path=patch_path)
    finally:
        try:
            index_path.unlink()
        except OSError:
            pass


def _task_json(
    *,
    gold_run_id: str,
    attempt_id: str,
    task_path: str,
    patch_path: str,
    command_policy: GoldRunnerCommandPolicy,
    target_ref: str,
) -> dict[str, object]:
    required_scaffold = _dedupe_paths(
        (
            task_path,
            patch_path,
            *command_policy.required_scaffold_paths,
            *command_policy.reproduction_committed_inputs,
        )
    )
    return {
        "schema": TASK_CONTRACT_SCHEMA,
        "task_id": f"{command_policy.task_id}-gold-{gold_run_id}-{attempt_id}",
        "task_class": command_policy.task_class,
        "base_revision": "HEAD",
        "target_ref": target_ref,
        "allowed_scope": {
            "exact": [],
            "prefixes": [path.rstrip("/") for path in command_policy.allowed_scope],
        },
        "patch_path": patch_path,
        "required_scaffold_paths": list(required_scaffold),
        "reproduction": {
            "command": list(command_policy.reproduction_command),
            "committed_inputs": list(command_policy.reproduction_committed_inputs),
            "before": command_policy.reproduction_before.to_json(),
            "after": command_policy.reproduction_after.to_json(),
        },
        "baseline_commands": [list(command) for command in command_policy.baseline_commands],
        "acceptance_commands": [list(command) for command in command_policy.acceptance_commands],
        "full_suite_commands": [list(command) for command in command_policy.full_suite_commands],
        "commit_message": command_policy.commit_message,
    }


def _dedupe_paths(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(paths))


@contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(old)


def _execute_controlled_change_from_repo(
    *,
    repo: Path,
    base: str,
    task_path: str,
    report_root: Path,
    environment_kind: str,
) -> ControlledChangeResult:
    with _cwd(repo):
        return execute_controlled_change(
            ControlledChangeRequest(
                base=base,
                task_path=task_path,
                keep_worktree=False,
                report_dir=str(report_root),
                environment_kind=environment_kind,
            )
        )


def _gold_evidence_from_result(result: ControlledChangeResult, *, report_root: Path) -> GoldEvidence:
    if result.outcome != APPLIED:
        raise GoldRunnerError("GoldEvidence requires APPLIED controlled-change result")
    if result.verified_commit is None or result.evidence_ref is None or result.report_path is None or result.base_revision is None:
        raise GoldRunnerError("APPLIED controlled-change result is missing evidence fields")
    report_path = Path(result.report_path)
    if not report_path.is_absolute():
        report_path = report_root / report_path
    report_path = report_path.resolve()
    report_root_resolved = report_root.resolve()
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    return GoldEvidence(
        evidence_ref=result.evidence_ref,
        verified_commit=result.verified_commit,
        report_path=str(report_path.relative_to(report_root_resolved)).replace("\\", "/"),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        base_sha=result.base_revision,
        task_contract_sha256=report["trusted_inputs"]["task_contract_sha256"],
        patch_sha256=report["trusted_inputs"]["patch_sha256"],
    )


def _run_oracle(
    *,
    repo: Path,
    verified_commit: str | None,
    oracle: GoldOracle,
    command_policy: GoldRunnerCommandPolicy,
) -> tuple[OracleResult, str | None]:
    if verified_commit is None:
        return _oracle_infra("verified_commit_missing", started=time.perf_counter()), None
    started = time.perf_counter()
    worktree: Worktree | None = None
    oracle_result: OracleResult | None = None
    cleanup_diagnostic: str | None = None
    try:
        worktree = create_detached_worktree(repo, verified_commit)
        task = BaselineTask(
            task_id=command_policy.task_id,
            instance_id=command_policy.instance_id,
            statement=command_policy.statement,
            allowed_scope=command_policy.allowed_scope,
        )
        try:
            oracle_result = oracle.verify(worktree.path, task)
        except Exception as exc:
            oracle_result = OracleResult(
                resolved=False,
                returncode=None,
                stdout="",
                stderr="",
                duration_seconds=time.perf_counter() - started,
                diagnostics={
                    "infra_error": True,
                    "failure_reason": "oracle_exception",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
    except Exception as exc:
        return _oracle_infra("oracle_worktree_create_failed", started=started, exc=exc), None
    finally:
        if worktree is not None:
            try:
                cleanup_worktree(worktree, keep=False)
            except Exception as exc:
                cleanup_diagnostic = f"oracle worktree cleanup failed: {type(exc).__name__}: {exc}"
    assert oracle_result is not None
    return oracle_result, cleanup_diagnostic


def _oracle_infra(reason: str, *, started: float, exc: BaseException | None = None) -> OracleResult:
    diagnostics: dict[str, Any] = {"infra_error": True, "failure_reason": reason}
    if exc is not None:
        diagnostics.update({"exception_type": type(exc).__name__, "exception_message": str(exc)})
    return OracleResult(
        resolved=False,
        returncode=None,
        stdout="",
        stderr="",
        duration_seconds=time.perf_counter() - started,
        diagnostics=diagnostics,
    )


def _status_for_materialization(status: MaterializationStatus) -> str:
    if status in {MaterializationStatus.NO_CANDIDATE, MaterializationStatus.REJECTED_SCOPE_VIOLATION}:
        return GOLD_NO_CANDIDATE
    return GOLD_INFRA_ERROR


def _status_for_controlled_change_failure(result: ControlledChangeResult) -> str:
    phase = result.failure_phase or ""
    code = result.failure_code or ""
    if phase in {"apply_patch_check", "apply_patch"}:
        return GOLD_NO_CANDIDATE
    if phase == "reproduction_before" or phase.startswith("baseline_"):
        return GOLD_INFRA_ERROR
    if phase == "reproduction_after":
        return GOLD_NO_CANDIDATE
    if phase in {"scope_check_after_patch", "scope_check_before_commit", "candidate_integrity"}:
        return GOLD_NO_CANDIDATE
    if phase.startswith("acceptance_") or phase.startswith("full_suite_"):
        return GOLD_NO_CANDIDATE
    if code in {"OUT_OF_SCOPE_PATH", "VERIFICATION_MUTATED_CANDIDATE"}:
        return GOLD_NO_CANDIDATE
    return GOLD_INFRA_ERROR


def _is_baseline_preexisting_failure(result: ControlledChangeResult) -> bool:
    phase = result.failure_phase or ""
    return phase == "reproduction_before" or phase.startswith("baseline_")


def _status_for_oracle(oracle_result: OracleResult) -> str:
    if oracle_result.diagnostics.get("infra_error") is True:
        return GOLD_INFRA_ERROR
    if oracle_result.resolved:
        return GOLD_APPLIED_WITH_EVIDENCE
    return GOLD_ORACLE_UNRESOLVED


def _base_payload(
    *,
    gold_run_id: str,
    attempt_id: str,
    materialized: MaterializedCandidate,
    controlled_change_result: ControlledChangeResult | None,
    failure_phase: str | None,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "gold_run_id": gold_run_id,
        "attempt_id": attempt_id,
        "controlled_change_run_id": controlled_change_result.run_id if controlled_change_result else None,
        "materialization_status": materialized.status.value,
        "materialization_diagnostics": dict(materialized.diagnostics),
        "controlled_change_outcome": controlled_change_result.outcome if controlled_change_result else None,
        "failure_phase": failure_phase,
        "failure_code": failure_code,
        "diagnostics": [],
    }


def _oracle_payload(oracle_result: OracleResult) -> dict[str, Any]:
    return {
        "oracle_invoked": True,
        "oracle_resolved": oracle_result.resolved,
        "oracle_infra_error": oracle_result.diagnostics.get("infra_error") is True,
        "oracle_returncode": oracle_result.returncode,
        "oracle_duration_seconds": oracle_result.duration_seconds,
        "oracle_diagnostics": dict(oracle_result.diagnostics),
    }


def _result(
    status: str,
    write_result: GoldAttemptWriteResult,
    payload: Mapping[str, Any],
    materialized: MaterializedCandidate,
    controlled_change_result: ControlledChangeResult | None,
    gold_evidence: GoldEvidence | None,
    evidence_validation: GoldEvidenceValidationResult | None,
    oracle_result: OracleResult | None,
    bridge_commit: str | None,
    task_path: str | None,
    patch_path: str | None,
    target_ref: str,
    report_root: Path,
) -> GoldRunnerResult:
    return GoldRunnerResult(
        status=status,
        write_result=write_result,
        payload=payload,
        materialized_candidate=materialized,
        controlled_change_result=controlled_change_result,
        gold_evidence=gold_evidence,
        evidence_validation=evidence_validation,
        oracle_result=oracle_result,
        bridge_commit=bridge_commit,
        task_path=task_path,
        patch_path=patch_path,
        target_ref=target_ref,
        report_root=str(report_root),
    )
