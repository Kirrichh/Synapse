"""Application services for canonical Synapse program execution."""
from __future__ import annotations

import copy
import dataclasses
import errno
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TextIO

from . import ast as synapse_ast
from . import compile_to_ast, run as run_source_runtime
from .builtins import BUILTINS
from .golden_replay import record_source
from .hardening import hash_event_chain
from .interpreter import Interpreter
from .lexer import KEYWORDS
from .runtime.mailbox_wait import (
    MAILBOX_WAIT_REASONS,
    MailboxWaitValidationError,
    is_mailbox_resume_signal,
    normalized_mailbox_signal_hash,
    validate_mailbox_resume,
    validate_mailbox_wait_payload,
)
from .version import LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION, __version__


@dataclass(frozen=True)
class ReplayArtifactSummary:
    recorded: str
    program_hash: str
    final_history_hash: str
    history_length: int

    def to_json(self) -> dict[str, object]:
        return {
            "recorded": self.recorded,
            "program_hash": self.program_hash,
            "final_history_hash": self.final_history_hash,
            "history_length": self.history_length,
        }


@dataclass(frozen=True)
class FileExecutionRequest:
    path: Path
    record: bool = False
    output_dir: Path | None = None
    layer: str = "strict"


@dataclass(frozen=True)
class SourceExecutionRequest:
    source: str


@dataclass(frozen=True)
class ReplRequest:
    banner: bool = True
    prompt: str = "synapse> "


@dataclass(frozen=True)
class RuntimeExecutionResult:
    status: str
    exit_code: int
    output: str
    diagnostics: tuple[str, ...] = ()
    artifact: ReplayArtifactSummary | None = None


@dataclass(frozen=True)
class DurableRunRequest:
    source_path: Path
    state_dir: Path
    run_id: str | None = None
    correlation_id: str | None = None
    input_file: Path | None = None
    input_from_stdin: bool = False


@dataclass(frozen=True)
class DurableResumeRequest:
    state_file: Path
    suspension_id: str
    signal_file: Path | None = None
    signal_from_stdin: bool = False


@dataclass(frozen=True)
class DurableRunResult:
    status: str
    exit_code: int
    public_payload: dict[str, object]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplResult:
    status: str
    exit_code: int
    diagnostics: tuple[str, ...] = ()


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_SUPPORTED_SUSPENSION_REASONS = {
    "awaiting_external_signal",
    "awaiting_promise",
    "awaiting_llm",
    "awaiting_message",
    "awaiting_message_or_timeout",
}

_RUNTIME_EXECUTION_ERROR = "RUNTIME_EXECUTION_ERROR"
_INVALID_CLI_INPUT = "INVALID_CLI_INPUT"
_ARTIFACT_INVALID_OR_INTEGRITY_FAILURE = "ARTIFACT_INVALID_OR_INTEGRITY_FAILURE"
_RESUME_BOUNDARY_MISMATCH = "RESUME_BOUNDARY_MISMATCH"
_STALE_OR_UNKNOWN_SUSPENSION = "STALE_OR_UNKNOWN_SUSPENSION"
_RESOLUTION_CONFLICT = "RESOLUTION_CONFLICT"
_UNSUPPORTED_DURABLE_OPERATION_OR_REASON = "UNSUPPORTED_DURABLE_OPERATION_OR_REASON"
_ARTIFACT_EXISTS_OR_LOCKED = "ARTIFACT_EXISTS_OR_LOCKED"

_PUBLIC_ERROR_MESSAGES = {
    "unsupported": "Unsupported durable operation",
    "invalid_input": "Invalid durable input",
    "invalid_state_dir": "Invalid state directory",
    "runtime": "Runtime execution failed",
    "conflict": "Artifact already exists or is locked",
    "artifact_validation": "Artifact validation failed",
    "artifact_invalid": "Artifact invalid or integrity failure",
    "boundary_mismatch": "Resume boundary mismatch",
    "stale_suspension": "Stale or unknown suspension",
    "resolution_conflict": "Resolution conflict",
}

_MAX_SIGNAL_BYTES = 10 * 1024 * 1024

_PERMISSION_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
}
if hasattr(errno, "EROFS"):
    _PERMISSION_ERRNOS.add(errno.EROFS)

_REPLAY_STATE_KEYS = (
    "node_id",
    "source_code",
    "routing_table",
    "outbound_packets",
    "mailboxes",
    "actor_log",
    "execution_history",
    "policies",
    "claims",
    "consequences",
    "verification_results",
    "memory_audit",
    "checkpoints",
    "spawned_actors",
    "promises",
    "promise_routes",
    "promise_tombstones",
    "llm_context_cache",
    "intents",
    "intent_audit",
)

_DURABLE_SUPPORTED_CLASSIFICATIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "Program": ("SUPPORTED_PURE", "entrypoint; recursively validated statements", ()),
    "ExprStmt": ("SUPPORTED_PURE", "expression wrapper", ()),
    "LetStmt": ("SUPPORTED_PURE", "declaration target is source-owned", ("name",)),
    "AssignStmt": ("SUPPORTED_PURE", "assignment target is source-owned", ("target",)),
    "Literal": ("SUPPORTED_PURE", "strict JSON-compatible literal value", ()),
    "Variable": ("SUPPORTED_PURE", "identifier lookup only", ()),
    "BinaryExpr": ("SUPPORTED_PURE", "pure binary operators only", ()),
    "UnaryExpr": ("SUPPORTED_PURE", "pure unary operators only", ()),
    "ListExpr": ("SUPPORTED_PURE", "recursively strict JSON-compatible elements", ()),
    "DictExpr": ("SUPPORTED_PURE", "string keys with recursively validated values", ()),
    "IfStmt": ("SUPPORTED_PURE", "condition and both branches recursively validated", ()),
    "AffectivePadLiteral": ("SUPPORTED_PURE", "literal PAD projection", ()),
    "DecayExpr": ("SUPPORTED_PURE", "literal decay projection", ()),
    "PromptExpr": ("SUPPORTED_PURE", "template interpolation without backend call", ()),
    "AssertStmt": ("SUPPORTED_PURE", "deterministic assertion", ()),
    "AgentDef": ("SUPPORTED_APPLICATION_PROJECTION", "top-level empty constructor only", ("name",)),
    "CallExpr": ("SUPPORTED_APPLICATION_PROJECTION", "direct allowlisted builtin, suspend request, or await member form only", ()),
    "SpawnExpr": ("SUPPORTED_WITH_CRASH_BOUNDARY", "restricted zero-argument top-level AgentDef constructor", ()),
    "SendStmt": ("SUPPORTED_WITH_CRASH_BOUNDARY", "receiver must be a variable bound by approved SpawnExpr", ()),
    "AwaitExpr": ("SUPPORTED_WITH_CRASH_BOUNDARY", "synthetic variable target or zero-argument member call only", ()),
    "SuspendExpr": ("SUPPORTED_WITH_CRASH_BOUNDARY", "first external signal suspension only", ()),
    "LLMCall": ("SUPPORTED_WITH_CRASH_BOUNDARY", "initial awaiting_llm suspension without provider backend", ()),
    "ReceiveBlock": ("SUPPORTED_WITH_CRASH_BOUNDARY", "single-pattern externally resolved mailbox receive", ()),
    "ReceivePattern": ("SUPPORTED_WITH_CRASH_BOUNDARY", "only inside an approved ReceiveBlock", ("sender_var", "target_var")),
}

_DURABLE_UNSUPPORTED_CLASSIFICATIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "AffectiveBias": ("UNSUPPORTED_OTHER", "outside P2a durable subset", ()),
    "AffectiveEventStmt": ("UNSUPPORTED_MUTATION", "affective state mutation", ("name", "binding")),
    "AffectiveFilterExpr": ("UNSUPPORTED_OTHER", "affective memory query expression", ()),
    "AffectiveModulationStmt": ("UNSUPPORTED_MUTATION", "affective modulation mutation", ("binding",)),
    "AffectiveResonanceStmt": ("UNSUPPORTED_MUTATION", "affective resonance mutates runtime state", ("binding",)),
    "AffectiveStateDef": ("UNSUPPORTED_MUTATION", "affective state definition", ("name", "binding")),
    "AffectiveThresholdDef": ("UNSUPPORTED_EXECUTION_ENGINE", "threshold engine state", ("name",)),
    "AffectiveWeightedConsensus": ("UNSUPPORTED_EXECUTION_ENGINE", "consensus runtime", ()),
    "AtIpTrigger": ("UNSUPPORTED_EXECUTION_ENGINE", "VM trigger", ()),
    "BeforeOpTrigger": ("UNSUPPORTED_EXECUTION_ENGINE", "VM trigger", ()),
    "BranchDef": ("UNSUPPORTED_EXECUTION_ENGINE", "branch execution", ("name",)),
    "CheckStmt": ("UNSUPPORTED_OTHER", "verification statement outside P2a subset", ()),
    "ClaimDef": ("UNSUPPORTED_MUTATION", "claims state mutation", ("name",)),
    "CollectiveDreamStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "collective runtime", ("binding",)),
    "CompileVmStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "VM compilation", ("binding",)),
    "ConsequenceDef": ("UNSUPPORTED_MUTATION", "consequence registry mutation", ("name",)),
    "ConsolidateStmt": ("UNSUPPORTED_HOST_EFFECT", "memory consolidation effect", ("binding",)),
    "ContextBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "context runtime", ("label",)),
    "DebateBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "debate runtime", ()),
    "DeclareIntentStmt": ("UNSUPPORTED_MUTATION", "intent mutation", ("name",)),
    "DistributedConsensusStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "distributed consensus", ("binding",)),
    "DreamBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "dream runtime", ()),
    "EnergyPoolDecl": ("UNSUPPORTED_MUTATION", "energy pool runtime mutation", ()),
    "EvolveStmt": ("UNSUPPORTED_MUTATION", "identity mutation", ()),
    "FatigueDef": ("UNSUPPORTED_EXECUTION_ENGINE", "habit runtime", ()),
    "FlowDef": ("UNSUPPORTED_EXECUTION_ENGINE", "flow definition/call lifecycle", ("name",)),
    "FnDef": ("UNSUPPORTED_EXECUTION_ENGINE", "function closure lifecycle", ("name", "params")),
    "ForStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "loop execution not in P2a subset", ("var",)),
    "FractureResult": ("UNSUPPORTED_OTHER", "runtime-only fracture result", ()),
    "FractureStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "fracture runtime", ()),
    "GovernedMemoryForget": ("UNSUPPORTED_HOST_EFFECT", "memory forget effect", ()),
    "GovernedMemoryWrite": ("UNSUPPORTED_HOST_EFFECT", "memory write effect", ()),
    "HabitStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "habit runtime", ("name", "binding")),
    "ImportStmt": ("UNSUPPORTED_OTHER", "module import shim", ("alias",)),
    "ImprintStmt": ("UNSUPPORTED_HOST_EFFECT", "memory imprint effect", ("binding",)),
    "InlineHabitCond": ("UNSUPPORTED_EXECUTION_ENGINE", "habit condition runtime", ()),
    "IntegrateBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "transactional integrate runtime", ()),
    "IntentDef": ("UNSUPPORTED_MUTATION", "intent definition", ("name",)),
    "IntentionCascadeDef": ("UNSUPPORTED_MUTATION", "intention cascade mutation", ("name", "binding")),
    "MeasureIdentityCoherenceStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "identity measurement runtime", ("binding",)),
    "MemberAccess": ("UNSUPPORTED_OTHER", "general member access rejected except approved await member form", ()),
    "MemberAssignStmt": ("UNSUPPORTED_MUTATION", "member mutation", ("member",)),
    "MemoryAccess": ("UNSUPPORTED_HOST_EFFECT", "legacy memory access", ("name",)),
    "MemoryPalaceDef": ("UNSUPPORTED_HOST_EFFECT", "memory palace backend", ("name", "binding")),
    "MigrateStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "migration suspension deferred", ()),
    "ObserveBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "observer runtime", ()),
    "ObserveHandler": ("UNSUPPORTED_EXECUTION_ENGINE", "observer runtime", ("binding",)),
    "PlanWeaveStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "plan weave runtime", ("binding",)),
    "PolicyDef": ("UNSUPPORTED_EXECUTION_ENGINE", "policy runtime", ("name",)),
    "PolicyRule": ("UNSUPPORTED_EXECUTION_ENGINE", "policy runtime", ()),
    "RecallStmt": ("UNSUPPORTED_HOST_EFFECT", "memory recall effect", ("binding",)),
    "ReflectBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "reflect runtime", ()),
    "ReflectOnFracturesStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "fracture reflection runtime", ()),
    "RejectStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "policy rejection control flow", ()),
    "ResonanceStmt": ("UNSUPPORTED_MUTATION", "resonance runtime mutation", ("binding",)),
    "ReturnStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "function return without function lifecycle", ()),
    "RoutingAction": ("UNSUPPORTED_EXECUTION_ENGINE", "routing runtime", ()),
    "RoutingRule": ("UNSUPPORTED_EXECUTION_ENGINE", "routing runtime", ()),
    "RunVmStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "VM execution", ("binding",)),
    "SomaticMarkerStmt": ("UNSUPPORTED_MUTATION", "somatic marker mutation", ("name", "binding")),
    "SoulprintDef": ("UNSUPPORTED_MUTATION", "identity state mutation", ()),
    "SubAgentDef": ("UNSUPPORTED_EXECUTION_ENGINE", "subagent runtime", ("name",)),
    "SuperposeBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "superposition runtime", ()),
    "SwarmFractureStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "swarm runtime", ("binding",)),
    "ThoughtBlock": ("UNSUPPORTED_EXECUTION_ENGINE", "thought runtime", ()),
    "ThresholdRef": ("UNSUPPORTED_EXECUTION_ENGINE", "threshold runtime", ("name",)),
    "TryCatchStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "checked effect control flow", ("catch_binding",)),
    "VerifyBlock": ("UNSUPPORTED_OTHER", "verification block outside P2a subset", ()),
    "WhileStmt": ("UNSUPPORTED_EXECUTION_ENGINE", "loop execution not in P2a subset", ()),
}

_DURABLE_AST_CLASSIFICATIONS = {
    **_DURABLE_SUPPORTED_CLASSIFICATIONS,
    **_DURABLE_UNSUPPORTED_CLASSIFICATIONS,
}

_DIRECT_CALL_ALLOWLIST = frozenset({
    "print",
    "len",
    "range",
    "time",
    "random",
    "uuid",
    "type",
    "str",
    "int",
    "float",
    "list",
    "dict",
    "abs",
    "sum",
    "max",
    "min",
    "sorted",
    "reversed",
    "enumerate",
    "zip",
    "any",
    "all",
})


class _DurablePreExecutionError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = 1,
        status: str = "ERROR",
        error_code: str | None = None,
        public_message: str | None = None,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.status = status
        self.error_code = error_code or _error_code_for_exit(exit_code)
        self.public_message = public_message or _public_message_for_exit(exit_code)


class _DurableUnsupportedError(_DurablePreExecutionError):
    def __init__(self, message: str):
        super().__init__(
            message,
            exit_code=25,
            status="UNSUPPORTED",
            error_code=_UNSUPPORTED_DURABLE_OPERATION_OR_REASON,
            public_message=_PUBLIC_ERROR_MESSAGES["unsupported"],
        )


def durable_ast_inventory() -> tuple[dict[str, object], ...]:
    """Return the P2a dynamic AST inventory with explicit classifications."""

    actual = _actual_concrete_node_classes()
    rows = []
    for name in sorted(actual):
        classification = _DURABLE_AST_CLASSIFICATIONS.get(name)
        if classification is None:
            rows.append({
                "class_name": name,
                "module": actual[name].__module__,
                "classification": "UNCLASSIFIED",
                "constraint": "",
                "source_owned_fields": (),
            })
            continue
        status, constraint, owned_fields = classification
        rows.append({
            "class_name": name,
            "module": actual[name].__module__,
            "classification": status,
            "constraint": constraint,
            "source_owned_fields": owned_fields,
        })
    return tuple(rows)


def _actual_concrete_node_classes() -> dict[str, type]:
    pending = list(synapse_ast.Node.__subclasses__())
    seen: set[type] = set()
    result: dict[str, type] = {}
    while pending:
        cls = pending.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        result[cls.__name__] = cls
    return result


def _assert_ast_inventory_complete() -> None:
    actual = set(_actual_concrete_node_classes())
    expected = set(_DURABLE_AST_CLASSIFICATIONS)
    missing = sorted(actual - expected)
    stale = sorted(expected - actual)
    if missing or stale:
        raise _DurableUnsupportedError(
            "AST inventory mismatch: "
            f"unclassified={missing or []}; stale_registry={stale or []}"
        )


def _strict_canonical_bytes(value: Any) -> bytes:
    _validate_strict_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_prefixed_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_prefixed_value(value: Any) -> str:
    return _sha256_prefixed_bytes(_strict_canonical_bytes(value))


def _error_code_for_exit(exit_code: int) -> str:
    if exit_code == 2:
        return _INVALID_CLI_INPUT
    if exit_code == 21:
        return _ARTIFACT_INVALID_OR_INTEGRITY_FAILURE
    if exit_code == 22:
        return _RESUME_BOUNDARY_MISMATCH
    if exit_code == 23:
        return _STALE_OR_UNKNOWN_SUSPENSION
    if exit_code == 24:
        return _RESOLUTION_CONFLICT
    if exit_code == 25:
        return _UNSUPPORTED_DURABLE_OPERATION_OR_REASON
    if exit_code == 26:
        return _ARTIFACT_EXISTS_OR_LOCKED
    return _RUNTIME_EXECUTION_ERROR


def _public_message_for_exit(exit_code: int) -> str:
    if exit_code == 2:
        return _PUBLIC_ERROR_MESSAGES["invalid_input"]
    if exit_code == 21:
        return _PUBLIC_ERROR_MESSAGES["artifact_invalid"]
    if exit_code == 22:
        return _PUBLIC_ERROR_MESSAGES["boundary_mismatch"]
    if exit_code == 23:
        return _PUBLIC_ERROR_MESSAGES["stale_suspension"]
    if exit_code == 24:
        return _PUBLIC_ERROR_MESSAGES["resolution_conflict"]
    if exit_code == 25:
        return _PUBLIC_ERROR_MESSAGES["unsupported"]
    if exit_code == 26:
        return _PUBLIC_ERROR_MESSAGES["conflict"]
    return _PUBLIC_ERROR_MESSAGES["runtime"]


def _public_error_document(
    *,
    exit_code: int,
    error_code: str,
    message: str,
    run_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, object]:
    return {
        "result_schema_version": "1.0.0",
        "status": "ERROR",
        "exit_code": exit_code,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "error": {
            "code": error_code,
            "message": message,
        },
    }


def _is_permission_os_error(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) or (
        isinstance(exc, OSError)
        and getattr(exc, "errno", None) in _PERMISSION_ERRNOS
    )


def _validate_strict_json_value(value: Any, path: str = "$", seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path}: non-finite float is not strict JSON")
        return
    if isinstance(value, list):
        marker = id(value)
        if marker in seen:
            raise TypeError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        for idx, item in enumerate(value):
            _validate_strict_json_value(item, f"{path}[{idx}]", seen)
        seen.remove(marker)
        return
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            raise TypeError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: dict key {key!r} is not a string")
            _validate_strict_json_value(item, f"{path}.{key}", seen)
        seen.remove(marker)
        return
    if isinstance(value, tuple):
        raise TypeError(f"{path}: tuple is not a persisted strict JSON type")
    raise TypeError(f"{path}: {type(value).__name__} is not strict JSON")


def _strict_json_projection(value: Any, path: str = "$", seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path}: non-finite float is not strict JSON")
        return value
    if isinstance(value, list):
        marker = id(value)
        if marker in seen:
            raise TypeError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        try:
            return [_strict_json_projection(item, f"{path}[{idx}]", seen) for idx, item in enumerate(value)]
        finally:
            seen.remove(marker)
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            raise TypeError(f"{path}: cycle is not strict JSON")
        seen.add(marker)
        try:
            projected: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path}: dict key is not a string")
                projected[key] = _strict_json_projection(item, f"{path}.{key}", seen)
            return projected
        finally:
            seen.remove(marker)
    if isinstance(value, tuple):
        raise TypeError(f"{path}: tuple is not a persisted strict JSON type")
    raise TypeError(f"{path}: unsupported strict JSON value")


def _project_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite float is not strict JSON")
        return value
    if isinstance(value, list):
        return [_project_json_value(item) for item in value]
    if isinstance(value, tuple):
        raise TypeError("runtime projection contains tuple")
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string dict key in runtime projection: {key!r}")
            projected[key] = _project_json_value(item)
        return projected
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _project_json_value(value.to_dict())
    raise TypeError(f"runtime projection contains unsupported {type(value).__name__}")


def _validate_run_id(run_id: str) -> None:
    if not run_id or not _RUN_ID_RE.fullmatch(run_id):
        raise _DurablePreExecutionError("invalid run_id", exit_code=2)
    if run_id.startswith(".") or ".." in run_id:
        raise _DurablePreExecutionError("invalid run_id path segment", exit_code=2)
    if any(ch in run_id for ch in ("/", "\\")):
        raise _DurablePreExecutionError("invalid run_id path separator", exit_code=2)
    if re.match(r"^[A-Za-z]:", run_id):
        raise _DurablePreExecutionError("invalid run_id drive prefix", exit_code=2)
    if any(ord(ch) < 32 for ch in run_id):
        raise _DurablePreExecutionError("invalid run_id control character", exit_code=2)


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex}"


def _durable_failure(
    status: str,
    exit_code: int,
    error_code: str,
    message: str,
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    diagnostics: tuple[str, ...] = (),
) -> DurableRunResult:
    return DurableRunResult(
        status=status,
        exit_code=exit_code,
        public_payload=_public_error_document(
            exit_code=exit_code,
            error_code=error_code,
            message=message,
            run_id=run_id,
            correlation_id=correlation_id,
        ),
        diagnostics=diagnostics,
    )


def durable_error_result(exit_code: int, error_code: str, message: str) -> DurableRunResult:
    return _durable_failure("ERROR", exit_code, error_code, message)


def _json_loads_strict_object(raw: str) -> dict[str, Any]:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    value = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    _validate_strict_json_value(value)
    return value


def _json_loads_strict_value(raw: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    decoder = json.JSONDecoder(parse_constant=reject_constant)
    value, end = decoder.raw_decode(raw)
    if raw[end:].strip():
        raise ValueError("signal file must contain exactly one JSON value")
    _validate_strict_json_value(value)
    return value


def _read_signal_value(request: DurableResumeRequest, stdin: TextIO | None) -> tuple[Any, str]:
    try:
        if request.signal_from_stdin:
            if stdin is None:
                raise _DurablePreExecutionError("stdin is unavailable for --signal-file -", exit_code=2)
            raw = stdin.read()
            if raw == "":
                raise ValueError("signal stdin is empty")
            if len(raw.encode("utf-8")) > _MAX_SIGNAL_BYTES:
                raise ValueError("signal stdin is too large")
            value = _json_loads_strict_value(raw)
            return value, _sha256_prefixed_value(value)

        if request.signal_file is None:
            raise _DurablePreExecutionError("missing signal file", exit_code=2)
        signal_path = request.signal_file
        if not signal_path.exists() or signal_path.is_dir():
            raise _DurablePreExecutionError("signal file is not readable", exit_code=2)
        raw_bytes = signal_path.read_bytes()
        if len(raw_bytes) > _MAX_SIGNAL_BYTES:
            raise ValueError("signal file is too large")
        if raw_bytes == b"":
            raise ValueError("signal file is empty")
        raw = raw_bytes.decode("utf-8-sig")
        value = _json_loads_strict_value(raw)
        return value, _sha256_prefixed_value(value)
    except _DurablePreExecutionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise _DurablePreExecutionError(
            "signal input failed",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        ) from exc


def _load_initial_bindings(request: DurableRunRequest, stdin: TextIO | None) -> dict[str, Any]:
    if request.input_from_stdin:
        if stdin is None:
            raise _DurablePreExecutionError("stdin is unavailable for --input-file -", exit_code=2)
        return _json_loads_strict_object(stdin.read())
    if request.input_file is None:
        return {}
    try:
        return _json_loads_strict_object(request.input_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _DurablePreExecutionError(f"input file read failed: {exc}", exit_code=2) from exc
    except ValueError as exc:
        raise _DurablePreExecutionError(f"input file must contain a strict JSON object: {exc}", exit_code=2) from exc


def _state_dir_profile() -> str:
    if os.name == "nt":
        return "windows-file-fsync-replace-v1"
    return "posix-file-and-directory-fsync-replace-v1"


def _probe_state_dir(state_dir: Path) -> None:
    probe = state_dir / f".synapse-p2a-probe-{uuid.uuid4().hex}.tmp"
    try:
        with probe.open("xb") as handle:
            handle.write(b'{"probe":true}')
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise _DurablePreExecutionError(
            "state-dir write probe failed",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_state_dir"],
        ) from exc
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _fsync_directory_if_posix(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_commit_json(artifact_path: Path, artifact: dict[str, Any]) -> None:
    payload = _strict_canonical_bytes(artifact)
    temp_path = artifact_path.with_name(f".{artifact_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        attempts = 4 if os.name == "nt" else 1
        for attempt in range(attempts):
            try:
                os.replace(temp_path, artifact_path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == attempts - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        _fsync_directory_if_posix(artifact_path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _remove_lock_directory(lock_path: Path) -> None:
    shutil.rmtree(lock_path)


def _read_source(source_path: Path) -> str:
    try:
        return source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _DurablePreExecutionError(
            "source read failed",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        ) from exc


def _line(node: object) -> int:
    return int(getattr(node, "line", 0) or 0)


def _column(node: object) -> int:
    return int(getattr(node, "column", 0) or 0)


def _iter_child_nodes(value: Any) -> Iterable[synapse_ast.Node]:
    if isinstance(value, synapse_ast.Node):
        yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_child_nodes(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_child_nodes(item)


def _walk_ast(node: synapse_ast.Node) -> Iterable[synapse_ast.Node]:
    yield node
    if not dataclasses.is_dataclass(node):
        return
    for field in dataclasses.fields(node):
        if field.name in {"line", "column"}:
            continue
        for child in _iter_child_nodes(getattr(node, field.name)):
            yield from _walk_ast(child)


def _contains_async_boundary(node: synapse_ast.Node) -> bool:
    return any(
        isinstance(item, (synapse_ast.SuspendExpr, synapse_ast.AwaitExpr, synapse_ast.LLMCall))
        for item in _walk_ast(node)
    )


def _validate_receive_timeout_expression(node: synapse_ast.Node) -> None:
    """Accept only the small deterministic scalar timeout-expression profile."""

    if isinstance(node, synapse_ast.Literal):
        _validate_strict_json_value(node.value)
        return
    if isinstance(node, synapse_ast.Variable):
        return
    if isinstance(node, synapse_ast.UnaryExpr):
        _validate_receive_timeout_expression(node.operand)
        return
    if isinstance(node, synapse_ast.BinaryExpr):
        _validate_receive_timeout_expression(node.left)
        _validate_receive_timeout_expression(node.right)
        return
    raise _DurableUnsupportedError(
        "durable ReceiveBlock timeout must use deterministic scalar expressions only"
    )


def _validate_receive_block(
    node: synapse_ast.ReceiveBlock,
    context: "_DurableValidationContext",
) -> "_DurableValidationContext":
    if len(node.patterns) != 1:
        raise _DurableUnsupportedError("durable ReceiveBlock requires exactly one ReceivePattern")
    pattern = node.patterns[0]
    if not isinstance(pattern, synapse_ast.ReceivePattern):
        raise _DurableUnsupportedError("durable ReceiveBlock pattern is invalid")
    if not isinstance(pattern.sender_var, str) or not isinstance(pattern.target_var, str):
        raise _DurableUnsupportedError("durable ReceivePattern bindings must be strings")
    if node.else_body and node.timeout is None:
        raise _DurableUnsupportedError("durable ReceiveBlock else_body requires timeout")
    if node.timeout is not None:
        _validate_receive_timeout_expression(node.timeout)

    received_context = context
    for statement in pattern.body:
        received_context = _validate_node(statement, received_context, top_level=False, role="receive_body")
    timeout_context = context
    for statement in node.else_body:
        timeout_context = _validate_node(statement, timeout_context, top_level=False, role="receive_else")
    return received_context.with_spawned_intersection(timeout_context)


def _collect_source_owned_identifiers(node: synapse_ast.Node) -> set[str]:
    owned: set[str] = set()
    for item in _walk_ast(node):
        classification = _DURABLE_AST_CLASSIFICATIONS.get(type(item).__name__)
        source_fields = classification[2] if classification is not None else ()
        for field_name in source_fields:
            value = getattr(item, field_name, None)
            if isinstance(value, str) and value:
                owned.add(value)
            elif isinstance(value, list):
                owned.update(entry for entry in value if isinstance(entry, str) and entry)
    return owned


@dataclass(frozen=True)
class _DurableValidationContext:
    agent_names: frozenset[str]
    spawned_bindings: frozenset[str]
    source_owned: frozenset[str]

    def with_spawned(self, name: str) -> "_DurableValidationContext":
        return dataclasses.replace(self, spawned_bindings=self.spawned_bindings | frozenset({name}))

    def without_spawned(self, name: str) -> "_DurableValidationContext":
        return dataclasses.replace(self, spawned_bindings=self.spawned_bindings - frozenset({name}))

    def with_spawned_intersection(self, other: "_DurableValidationContext") -> "_DurableValidationContext":
        return dataclasses.replace(self, spawned_bindings=self.spawned_bindings & other.spawned_bindings)


def _validate_durable_ast(root: synapse_ast.Node) -> set[str]:
    _assert_ast_inventory_complete()
    source_owned = _collect_source_owned_identifiers(root)
    agent_names = {
        stmt.name
        for stmt in getattr(root, "statements", [])
        if isinstance(stmt, synapse_ast.AgentDef)
    }
    context = _DurableValidationContext(
        agent_names=frozenset(agent_names),
        spawned_bindings=frozenset(),
        source_owned=frozenset(source_owned),
    )
    _validate_node(root, context, top_level=True)
    return source_owned


def _validate_node(
    node: synapse_ast.Node,
    context: _DurableValidationContext,
    *,
    top_level: bool = False,
    role: str = "",
) -> _DurableValidationContext:
    name = type(node).__name__
    if name not in _DURABLE_AST_CLASSIFICATIONS:
        raise _DurableUnsupportedError(f"unclassified durable AST node: {name}")
    if name in _DURABLE_UNSUPPORTED_CLASSIFICATIONS:
        status, constraint, _ = _DURABLE_UNSUPPORTED_CLASSIFICATIONS[name]
        raise _DurableUnsupportedError(f"unsupported durable AST node {name}: {status}: {constraint}")

    if isinstance(node, synapse_ast.Program):
        current = context
        for stmt in node.statements:
            current = _validate_node(stmt, current, top_level=True)
        return current
    if isinstance(node, synapse_ast.ExprStmt):
        return _validate_node(node.expr, context, role="expr")
    if isinstance(node, synapse_ast.LetStmt):
        if isinstance(node.value, synapse_ast.SpawnExpr):
            _validate_node(node.value, context, role="let_spawn")
            return context.with_spawned(node.name)
        else:
            _validate_node(node.value, context, role="let_value")
            return context.without_spawned(node.name)
    if isinstance(node, synapse_ast.AssignStmt):
        _validate_node(node.value, context, role="assign_value")
        if isinstance(node.value, synapse_ast.SpawnExpr):
            return context.with_spawned(node.target)
        return context.without_spawned(node.target)
    if isinstance(node, synapse_ast.Literal):
        _validate_strict_json_value(node.value)
        return context
    if isinstance(node, synapse_ast.Variable):
        return context
    if isinstance(node, synapse_ast.BinaryExpr):
        _validate_node(node.left, context, role="binary_left")
        _validate_node(node.right, context, role="binary_right")
        return context
    if isinstance(node, synapse_ast.UnaryExpr):
        _validate_node(node.operand, context, role="unary_operand")
        return context
    if isinstance(node, synapse_ast.ListExpr):
        for item in node.elements:
            _validate_node(item, context, role="list_item")
        return context
    if isinstance(node, synapse_ast.DictExpr):
        for key, value in node.pairs:
            if not isinstance(key, str):
                raise _DurableUnsupportedError("durable DictExpr requires string keys")
            _validate_node(value, context, role="dict_value")
        return context
    if isinstance(node, synapse_ast.IfStmt):
        _validate_node(node.condition, context, role="if_condition")
        then_context = context
        for stmt in node.then_body:
            then_context = _validate_node(stmt, then_context, top_level=False)
        else_context = context
        for stmt in node.else_body:
            else_context = _validate_node(stmt, else_context, top_level=False)
        return then_context.with_spawned_intersection(else_context)
    if isinstance(node, (synapse_ast.AffectivePadLiteral, synapse_ast.DecayExpr)):
        return context
    if isinstance(node, synapse_ast.PromptExpr):
        for value in node.args.values():
            _validate_node(value, context, role="prompt_arg")
        return context
    if isinstance(node, synapse_ast.AssertStmt):
        if _contains_async_boundary(node.condition) or (
            node.message is not None and _contains_async_boundary(node.message)
        ):
            raise _DurableUnsupportedError("durable AssertStmt cannot contain suspension descendants")
        _validate_node(node.condition, context, role="assert_condition")
        if node.message is not None:
            _validate_node(node.message, context, role="assert_message")
        return context
    if isinstance(node, synapse_ast.AgentDef):
        if not top_level:
            raise _DurableUnsupportedError("durable AgentDef must be top-level")
        if node.methods or node.energy_pool is not None or node.soulprint is not None:
            raise _DurableUnsupportedError("durable AgentDef supports only empty constructors")
        return context
    if isinstance(node, synapse_ast.CallExpr):
        _validate_call_expr(node, context, role=role)
        return context
    if isinstance(node, synapse_ast.SpawnExpr):
        _validate_spawn_expr(node, context)
        return context
    if isinstance(node, synapse_ast.SendStmt):
        _validate_send_stmt(node, context)
        return context
    if isinstance(node, synapse_ast.AwaitExpr):
        _validate_await_expr(node, context)
        return context
    if isinstance(node, synapse_ast.SuspendExpr):
        _validate_suspend_expr(node, context)
        return context
    if isinstance(node, synapse_ast.LLMCall):
        _validate_node(node.prompt, context, role="llm_prompt")
        return context
    if isinstance(node, synapse_ast.ReceiveBlock):
        return _validate_receive_block(node, context)
    if isinstance(node, synapse_ast.ReceivePattern):
        raise _DurableUnsupportedError("durable ReceivePattern is valid only inside ReceiveBlock")
    raise _DurableUnsupportedError(f"unsupported durable AST node: {name}")


def _validate_call_expr(node: synapse_ast.CallExpr, context: _DurableValidationContext, *, role: str = "") -> None:
    callee = node.callee
    if role == "suspend_request":
        if not isinstance(callee, synapse_ast.Variable):
            raise _DurableUnsupportedError("durable suspend request requires a direct external call")
        for arg in node.args:
            _validate_node(arg, context, role="suspend_arg")
        return
    if role == "await_member":
        if node.args:
            raise _DurableUnsupportedError("durable await member call must have zero arguments")
        if not isinstance(callee, synapse_ast.MemberAccess):
            raise _DurableUnsupportedError("durable await member call requires member access")
        if not isinstance(callee.obj, synapse_ast.Variable) or callee.obj.name not in context.spawned_bindings:
            raise _DurableUnsupportedError("durable await member target must come from approved SpawnExpr")
        return
    if not isinstance(callee, synapse_ast.Variable):
        raise _DurableUnsupportedError("durable CallExpr rejects dynamic/member calls outside AwaitExpr")
    if callee.name not in _DIRECT_CALL_ALLOWLIST:
        raise _DurableUnsupportedError(f"durable CallExpr rejects non-allowlisted callee: {callee.name}")
    for arg in node.args:
        _validate_node(arg, context, role="call_arg")


def _validate_spawn_expr(node: synapse_ast.SpawnExpr, context: _DurableValidationContext) -> None:
    callee = node.callee
    if not isinstance(callee, synapse_ast.CallExpr):
        raise _DurableUnsupportedError("durable SpawnExpr requires zero-argument constructor call")
    if callee.args:
        raise _DurableUnsupportedError("durable SpawnExpr constructor arguments are unsupported")
    if not isinstance(callee.callee, synapse_ast.Variable) or callee.callee.name not in context.agent_names:
        raise _DurableUnsupportedError("durable SpawnExpr callee must be an approved top-level AgentDef")


def _validate_send_stmt(node: synapse_ast.SendStmt, context: _DurableValidationContext) -> None:
    if not isinstance(node.receiver, synapse_ast.Variable) or node.receiver.name not in context.spawned_bindings:
        raise _DurableUnsupportedError("durable SendStmt receiver must be produced by approved SpawnExpr")
    for arg in node.args:
        _validate_node(arg, context, role="send_arg")


def _validate_await_expr(node: synapse_ast.AwaitExpr, context: _DurableValidationContext) -> None:
    expr = node.expr
    if isinstance(expr, synapse_ast.Variable):
        return
    if isinstance(expr, synapse_ast.CallExpr):
        _validate_call_expr(expr, context, role="await_member")
        return
    raise _DurableUnsupportedError("durable AwaitExpr supports only variable or approved member call target")


def _validate_suspend_expr(node: synapse_ast.SuspendExpr, context: _DurableValidationContext) -> None:
    if isinstance(node.request, synapse_ast.CallExpr):
        _validate_call_expr(node.request, context, role="suspend_request")
        return
    _validate_node(node.request, context, role="suspend_request_value")


def _validate_initial_bindings(bindings: dict[str, Any], source_owned: set[str]) -> None:
    bootstrap = Interpreter()
    reserved = set(bootstrap.global_env.variables)
    for key, value in bindings.items():
        if not _IDENTIFIER_RE.fullmatch(key):
            raise _DurablePreExecutionError(f"invalid initial binding identifier: {key}", exit_code=2)
        if key in KEYWORDS:
            raise _DurablePreExecutionError(f"initial binding collides with keyword: {key}", exit_code=2)
        if key in BUILTINS:
            raise _DurablePreExecutionError(f"initial binding collides with builtin: {key}", exit_code=2)
        if key in reserved:
            raise _DurablePreExecutionError(f"initial binding collides with bootstrap binding: {key}", exit_code=2)
        if key.startswith("__synapse_"):
            raise _DurablePreExecutionError(f"initial binding uses reserved prefix: {key}", exit_code=2)
        if key in source_owned:
            raise _DurablePreExecutionError(f"initial binding collides with source-owned binding: {key}", exit_code=2)
        _validate_strict_json_value(value)


def _apply_initial_bindings(interpreter: Interpreter, bindings: dict[str, Any]) -> None:
    for key, value in bindings.items():
        interpreter.global_env.define(key, copy.deepcopy(value))


def _project_replay_state(interpreter: Interpreter) -> dict[str, Any]:
    projection = {key: _project_json_value(getattr(interpreter, key)) for key in _REPLAY_STATE_KEYS}
    _validate_strict_json_value(projection)
    return projection


def _history_integrity(replay_state: dict[str, Any]) -> dict[str, Any]:
    history = replay_state["execution_history"]
    chain = hash_event_chain(history)
    final_hash = chain[-1]["hash"] if chain else ""
    return {
        "event_count": len(history),
        "chain": chain,
        "final_hash": final_hash,
    }


def _sanitize_error(exc: BaseException) -> dict[str, str]:
    return {"code": _RUNTIME_EXECUTION_ERROR, "message": _PUBLIC_ERROR_MESSAGES["runtime"]}


def _suspension_payload_projection(suspension: object) -> Any:
    return _strict_json_projection(getattr(suspension, "payload", None))


def _promise_id_for_suspension(suspension: object) -> str | None:
    payload = getattr(suspension, "payload", None)
    if not isinstance(payload, dict):
        return None
    reason = getattr(suspension, "reason", "")
    if reason in {"awaiting_external_signal", "awaiting_promise"}:
        promise_id = payload.get("promise_id")
        return str(promise_id) if promise_id is not None else None
    return None


def _boundary_projection(
    suspension: object,
    *,
    source_hash: str,
    initial_bindings_hash: str,
    history_integrity: dict[str, Any],
    output_state: dict[str, Any],
) -> dict[str, Any]:
    node = getattr(suspension, "node", None)
    payload_projection = _suspension_payload_projection(suspension)
    payload_hash = _sha256_prefixed_value(payload_projection)
    reason = str(getattr(suspension, "reason", ""))
    promise_id = _promise_id_for_suspension(suspension)
    boundary_preimage = {
        "version": "1",
        "source_hash": source_hash,
        "initial_bindings_hash": initial_bindings_hash,
        "history_event_count": history_integrity["event_count"],
        "history_hash": history_integrity["final_hash"],
        "reason": reason,
        "node_type": type(node).__name__ if node is not None else "",
        "line": _line(node),
        "column": _column(node),
        "promise_id": promise_id,
        "payload_hash": payload_hash,
        "output_line_count": output_state["line_count"],
        "output_digest": output_state["digest"],
    }
    boundary_fingerprint = _sha256_prefixed_value(boundary_preimage)
    return {
        "reason": reason,
        "node_type": type(node).__name__ if node is not None else "",
        "line": _line(node),
        "column": _column(node),
        "promise_id": promise_id,
        "payload_hash": payload_hash,
        "boundary_fingerprint": boundary_fingerprint,
    }


def _suspension_id(run_id: str, sequence: int, boundary_fingerprint: str) -> str:
    return "susp-" + hashlib.sha256(
        _strict_canonical_bytes({
            "version": "synapse-p2-suspension-v1",
            "run_id": run_id,
            "sequence": sequence,
            "boundary_fingerprint": boundary_fingerprint,
        })
    ).hexdigest()


def _active_suspension(
    suspension: object,
    *,
    run_id: str,
    source_hash: str,
    initial_bindings_hash: str,
    history_integrity: dict[str, Any],
    output_state: dict[str, Any],
    sequence: int = 1,
) -> dict[str, Any]:
    projection = _boundary_projection(
        suspension,
        source_hash=source_hash,
        initial_bindings_hash=initial_bindings_hash,
        history_integrity=history_integrity,
        output_state=output_state,
    )
    active = {
        "sequence": sequence,
        "suspension_id": _suspension_id(run_id, sequence, projection["boundary_fingerprint"]),
        **projection,
    }
    if projection["reason"] in MAILBOX_WAIT_REASONS:
        active["payload"] = _suspension_payload_projection(suspension)
    return active


def _artifact_with_hash(artifact_without_hash: dict[str, Any]) -> dict[str, Any]:
    _validate_strict_json_value(artifact_without_hash)
    artifact_hash = _sha256_prefixed_value(artifact_without_hash)
    artifact = {"artifact_hash": artifact_hash, **artifact_without_hash}
    _validate_strict_json_value(artifact)
    if not _SHA256_RE.fullmatch(artifact_hash):
        raise TypeError("artifact_hash is not a sha256 digest")
    return artifact


def _build_artifact(
    *,
    status: str,
    run_id: str,
    correlation_id: str | None,
    source_path: Path,
    source_code: str,
    initial_bindings: dict[str, Any],
    interpreter: Interpreter,
    suspension: object | None,
    terminal: dict[str, Any] | None,
    revision: int = 1,
    idempotency: dict[str, Any] | None = None,
    suspension_sequence: int = 1,
) -> dict[str, Any]:
    output_lines = [str(line) for line in interpreter.output_buffer]
    output_state = {
        "line_count": len(output_lines),
        "digest": _sha256_prefixed_value(output_lines),
    }
    replay_state = _project_replay_state(interpreter)
    integrity = _history_integrity(replay_state)
    source_hash = _sha256_prefixed_bytes(source_code.encode("utf-8"))
    bindings_value = copy.deepcopy(initial_bindings)
    bindings_hash = _sha256_prefixed_value(bindings_value)
    active_suspension = None
    if suspension is not None:
        active_suspension = _active_suspension(
            suspension,
            run_id=run_id,
            source_hash=source_hash,
            initial_bindings_hash=bindings_hash,
            history_integrity=integrity,
            output_state=output_state,
            sequence=suspension_sequence,
        )
    if active_suspension is None and status == "PENDING":
        raise TypeError("PENDING artifact requires active_suspension")
    artifact_without_hash = {
        "artifact_schema_version": "1.0.0",
        "status": status,
        "revision": revision,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "execution_engine": "tree-walker",
        "persistence_profile": _state_dir_profile(),
        "source": {
            "path": str(source_path),
            "hash": source_hash,
            "content": source_code,
        },
        "initial_bindings": {
            "value": bindings_value,
            "hash": bindings_hash,
        },
        "replay_state": replay_state,
        "history_integrity": integrity,
        "active_suspension": active_suspension,
        "idempotency": copy.deepcopy(idempotency) if idempotency is not None else {"resolved_suspensions": {}},
        "output_state": output_state,
        "terminal": terminal,
        "versions": {
            "runtime": RUNTIME_VERSION,
            "language": LANGUAGE_VERSION,
            "spec": SPEC_VERSION,
            "package": __version__,
        },
    }
    return _artifact_with_hash(artifact_without_hash)


def _public_completed_payload(artifact: dict[str, Any], artifact_path: Path, output_lines: list[str]) -> dict[str, Any]:
    return {
        "result_schema_version": "1.0.0",
        "status": "COMPLETED",
        "exit_code": 0,
        "run_id": artifact["run_id"],
        "correlation_id": artifact["correlation_id"],
        "artifact_path": str(artifact_path),
        "artifact_revision": artifact["revision"],
        "history_hash": artifact["history_integrity"]["final_hash"],
        "source_hash": artifact["source"]["hash"],
        "output_delta": output_lines,
    }


def _public_pending_payload(
    artifact: dict[str, Any],
    artifact_path: Path,
    output_lines: list[str],
) -> dict[str, Any]:
    active = artifact["active_suspension"]
    return {
        "result_schema_version": "1.0.0",
        "status": "PENDING",
        "exit_code": 20,
        "run_id": artifact["run_id"],
        "correlation_id": artifact["correlation_id"],
        "artifact_path": str(artifact_path),
        "artifact_revision": artifact["revision"],
        "suspension_id": active["suspension_id"],
        "suspension_reason": active["reason"],
        "promise_id": active["promise_id"],
        "history_hash": artifact["history_integrity"]["final_hash"],
        "source_hash": artifact["source"]["hash"],
        "output_delta": output_lines,
        "resume_argv": [
            sys.executable,
            "-m",
            "synapse",
            "resume",
            "--state-file",
            str(artifact_path),
            "--suspension-id",
            str(active["suspension_id"]),
            "--signal-file",
            "<path|->",
        ],
    }


def _public_error_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    terminal = artifact["terminal"] or {}
    error = terminal.get("error") or {}
    return _public_error_document(
        exit_code=int(terminal.get("exit_code") or 1),
        error_code=str(error.get("code") or _RUNTIME_EXECUTION_ERROR),
        message=str(error.get("message") or _PUBLIC_ERROR_MESSAGES["runtime"]),
        run_id=str(artifact["run_id"]),
        correlation_id=artifact["correlation_id"],
    )


def _public_committed_error_payload(artifact: dict[str, Any], artifact_path: Path, output_lines: list[str]) -> dict[str, Any]:
    payload = _public_error_payload(artifact)
    payload.update({
        "artifact_path": str(artifact_path),
        "artifact_revision": artifact["revision"],
        "history_hash": artifact["history_integrity"]["final_hash"],
        "source_hash": artifact["source"]["hash"],
        "output_delta": output_lines,
    })
    return payload


class _ArtifactIntegrityError(ValueError):
    pass


def _artifact_failure(run_id: str | None = None, correlation_id: str | None = None) -> DurableRunResult:
    return _durable_failure(
        "ERROR",
        21,
        _ARTIFACT_INVALID_OR_INTEGRITY_FAILURE,
        _PUBLIC_ERROR_MESSAGES["artifact_invalid"],
        run_id=run_id,
        correlation_id=correlation_id,
    )


def _artifact_identity(artifact: object) -> tuple[str | None, str | None]:
    if not isinstance(artifact, dict):
        return None, None
    run_id = artifact.get("run_id")
    correlation_id = artifact.get("correlation_id")
    safe_run_id: str | None = None
    if isinstance(run_id, str):
        try:
            _validate_run_id(run_id)
            safe_run_id = run_id
        except _DurablePreExecutionError:
            safe_run_id = None
    return (safe_run_id, correlation_id if isinstance(correlation_id, str) else None)


def _run_id_from_state_filename(artifact_path: Path) -> str | None:
    if artifact_path.suffix != ".json":
        return None
    candidate = artifact_path.stem
    try:
        _validate_run_id(candidate)
    except _DurablePreExecutionError:
        return None
    return candidate


def _boundary_failure(artifact: dict[str, Any]) -> DurableRunResult:
    return _durable_failure(
        "ERROR",
        22,
        _RESUME_BOUNDARY_MISMATCH,
        _PUBLIC_ERROR_MESSAGES["boundary_mismatch"],
        run_id=str(artifact.get("run_id")),
        correlation_id=artifact.get("correlation_id") if isinstance(artifact.get("correlation_id"), str) else None,
    )


def _stale_suspension_failure(artifact: dict[str, Any]) -> DurableRunResult:
    return _durable_failure(
        "ERROR",
        23,
        _STALE_OR_UNKNOWN_SUSPENSION,
        _PUBLIC_ERROR_MESSAGES["stale_suspension"],
        run_id=str(artifact.get("run_id")),
        correlation_id=artifact.get("correlation_id") if isinstance(artifact.get("correlation_id"), str) else None,
    )


def _resolution_conflict_failure(artifact: dict[str, Any]) -> DurableRunResult:
    return _durable_failure(
        "ERROR",
        24,
        _RESOLUTION_CONFLICT,
        _PUBLIC_ERROR_MESSAGES["resolution_conflict"],
        run_id=str(artifact.get("run_id")),
        correlation_id=artifact.get("correlation_id") if isinstance(artifact.get("correlation_id"), str) else None,
    )


def _state_file_invalid_input() -> DurableRunResult:
    return _durable_failure("ERROR", 2, _INVALID_CLI_INPUT, _PUBLIC_ERROR_MESSAGES["invalid_input"])


def _resolve_state_file(state_file: Path) -> Path:
    if (
        not state_file.exists()
        or state_file.is_dir()
        or state_file.is_symlink()
        or state_file.suffix != ".json"
        or not state_file.is_file()
    ):
        raise _DurablePreExecutionError(
            "invalid state file",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        )
    try:
        return state_file.resolve(strict=True)
    except OSError as exc:
        raise _DurablePreExecutionError(
            "state file resolution failed",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        ) from exc


def _json_loads_strict_artifact(raw: str) -> dict[str, Any]:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    value = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise _ArtifactIntegrityError("artifact root must be an object")
    _validate_strict_json_value(value)
    return value


def _expect_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _ArtifactIntegrityError(f"{path} must be an object")
    return value


def _expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _ArtifactIntegrityError(f"{path} must be a string")
    return value


def _expect_nullable_string(value: Any, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _ArtifactIntegrityError(f"{path} must be null or string")
    return value


def _expect_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _ArtifactIntegrityError(f"{path} must be an integer")
    return value


def _validate_hash(value: Any, path: str) -> str:
    text = _expect_string(value, path)
    if not _SHA256_RE.fullmatch(text):
        raise _ArtifactIntegrityError(f"{path} must be sha256 digest")
    return text


def _validate_history_integrity(artifact: dict[str, Any]) -> None:
    replay_state = _expect_mapping(artifact.get("replay_state"), "replay_state")
    history = replay_state.get("execution_history")
    if not isinstance(history, list):
        raise _ArtifactIntegrityError("replay_state.execution_history must be a list")
    persisted = _expect_mapping(artifact.get("history_integrity"), "history_integrity")
    _expect_int(persisted.get("event_count"), "history_integrity.event_count")
    if not isinstance(persisted.get("chain"), list):
        raise _ArtifactIntegrityError("history_integrity.chain must be a list")
    _expect_string(persisted.get("final_hash"), "history_integrity.final_hash")
    computed = _history_integrity(replay_state)
    if computed != persisted:
        raise _ArtifactIntegrityError("history integrity mismatch")


def _validate_source_ownership(artifact: dict[str, Any]) -> None:
    source = _expect_mapping(artifact.get("source"), "source")
    replay_state = _expect_mapping(artifact.get("replay_state"), "replay_state")
    _expect_string(source.get("path"), "source.path")
    source_hash = _validate_hash(source.get("hash"), "source.hash")
    source_content = _expect_string(source.get("content"), "source.content")
    replay_source = _expect_string(replay_state.get("source_code"), "replay_state.source_code")
    if source_content != replay_source:
        raise _ArtifactIntegrityError("source.content differs from replay_state.source_code")
    if _sha256_prefixed_bytes(replay_source.encode("utf-8")) != source_hash:
        raise _ArtifactIntegrityError("source hash mismatch")


def _validate_initial_bindings_artifact(artifact: dict[str, Any]) -> None:
    initial = _expect_mapping(artifact.get("initial_bindings"), "initial_bindings")
    value = _expect_mapping(initial.get("value"), "initial_bindings.value")
    digest = _validate_hash(initial.get("hash"), "initial_bindings.hash")
    if _sha256_prefixed_value(value) != digest:
        raise _ArtifactIntegrityError("initial bindings hash mismatch")


def _validate_versions(artifact: dict[str, Any]) -> None:
    versions = _expect_mapping(artifact.get("versions"), "versions")
    expected = {
        "runtime": RUNTIME_VERSION,
        "language": LANGUAGE_VERSION,
        "spec": SPEC_VERSION,
        "package": __version__,
    }
    if versions != expected:
        raise _ArtifactIntegrityError("artifact version mismatch")


def _validate_active_suspension_shape(active: Any, *, required: bool) -> dict[str, Any] | None:
    if active is None:
        if required:
            raise _ArtifactIntegrityError("active_suspension is required")
        return None
    active_map = _expect_mapping(active, "active_suspension")
    _expect_int(active_map.get("sequence"), "active_suspension.sequence")
    _expect_string(active_map.get("suspension_id"), "active_suspension.suspension_id")
    reason = _expect_string(active_map.get("reason"), "active_suspension.reason")
    if reason not in _SUPPORTED_SUSPENSION_REASONS:
        raise _ArtifactIntegrityError("active_suspension.reason is unsupported")
    _expect_string(active_map.get("node_type"), "active_suspension.node_type")
    _expect_int(active_map.get("line"), "active_suspension.line")
    _expect_int(active_map.get("column"), "active_suspension.column")
    promise_id = _expect_nullable_string(active_map.get("promise_id"), "active_suspension.promise_id")
    if reason in MAILBOX_WAIT_REASONS:
        if active_map.get("node_type") != "ReceiveBlock":
            raise _ArtifactIntegrityError("mailbox wait active_suspension node_type is invalid")
        if promise_id is not None:
            raise _ArtifactIntegrityError("mailbox wait active_suspension.promise_id must be null")
        try:
            validate_mailbox_wait_payload(active_map.get("payload"), reason)
        except MailboxWaitValidationError as exc:
            raise _ArtifactIntegrityError("mailbox wait active_suspension payload is invalid") from exc
    _validate_hash(active_map.get("payload_hash"), "active_suspension.payload_hash")
    _validate_hash(active_map.get("boundary_fingerprint"), "active_suspension.boundary_fingerprint")
    return active_map


def _validate_output_state(artifact: dict[str, Any]) -> None:
    output_state = _expect_mapping(artifact.get("output_state"), "output_state")
    _expect_int(output_state.get("line_count"), "output_state.line_count")
    _validate_hash(output_state.get("digest"), "output_state.digest")


def _validate_replay_state_shape(artifact: dict[str, Any]) -> None:
    replay_state = _expect_mapping(artifact.get("replay_state"), "replay_state")
    if set(replay_state) != set(_REPLAY_STATE_KEYS):
        raise _ArtifactIntegrityError("replay_state field set mismatch")
    _expect_string(replay_state.get("node_id"), "replay_state.node_id")
    _expect_string(replay_state.get("source_code"), "replay_state.source_code")
    for key in (
        "routing_table",
        "mailboxes",
        "policies",
        "claims",
        "consequences",
        "spawned_actors",
        "promises",
        "promise_routes",
        "promise_tombstones",
        "llm_context_cache",
        "intents",
    ):
        _expect_mapping(replay_state.get(key), f"replay_state.{key}")
    for key in (
        "outbound_packets",
        "actor_log",
        "execution_history",
        "verification_results",
        "memory_audit",
        "checkpoints",
        "intent_audit",
    ):
        if not isinstance(replay_state.get(key), list):
            raise _ArtifactIntegrityError(f"replay_state.{key} must be a list")


def _boundary_from_persisted_artifact(artifact: dict[str, Any], active: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "1",
        "source_hash": artifact["source"]["hash"],
        "initial_bindings_hash": artifact["initial_bindings"]["hash"],
        "history_event_count": artifact["history_integrity"]["event_count"],
        "history_hash": artifact["history_integrity"]["final_hash"],
        "reason": active["reason"],
        "node_type": active["node_type"],
        "line": active["line"],
        "column": active["column"],
        "promise_id": active["promise_id"],
        "payload_hash": active["payload_hash"],
        "output_line_count": artifact["output_state"]["line_count"],
        "output_digest": artifact["output_state"]["digest"],
    }


def _validate_persisted_boundary(artifact: dict[str, Any], active: dict[str, Any] | None) -> None:
    if active is None:
        return
    fingerprint = _sha256_prefixed_value(_boundary_from_persisted_artifact(artifact, active))
    if fingerprint != active["boundary_fingerprint"]:
        raise _ArtifactIntegrityError("persisted boundary fingerprint mismatch")
    if _suspension_id(artifact["run_id"], active["sequence"], active["boundary_fingerprint"]) != active["suspension_id"]:
        raise _ArtifactIntegrityError("persisted suspension id mismatch")


def _validate_operation_result(result: Any, artifact: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    result_map = _expect_mapping(result, "operation_result")
    allowed = {
        "result_schema_version",
        "status",
        "exit_code",
        "run_id",
        "correlation_id",
        "artifact_path",
        "artifact_revision",
        "source_hash",
        "history_hash",
        "output_delta",
        "suspension_id",
        "suspension_reason",
        "promise_id",
        "error",
    }
    required = {
        "result_schema_version",
        "status",
        "exit_code",
        "run_id",
        "correlation_id",
        "artifact_path",
        "artifact_revision",
        "source_hash",
        "history_hash",
        "output_delta",
    }
    if not set(result_map).issubset(allowed):
        raise _ArtifactIntegrityError("operation_result contains non-allowlisted fields")
    if not required.issubset(result_map):
        raise _ArtifactIntegrityError("operation_result missing required fields")
    forbidden = {
        "raw_signal",
        "signal",
        "signal_file",
        "request",
        "prompt",
        "initial_bindings",
        "stack_trace",
        "traceback",
        "raw_exception",
        "resume_argv",
        "sys.executable",
    }
    if forbidden & set(result_map):
        raise _ArtifactIntegrityError("operation_result contains forbidden fields")
    if result_map.get("result_schema_version") != "1.0.0":
        raise _ArtifactIntegrityError("operation_result schema mismatch")
    if result_map.get("status") != entry["committed_status"]:
        raise _ArtifactIntegrityError("operation_result status mismatch")
    exit_code = result_map.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise _ArtifactIntegrityError("operation_result exit_code invalid")
    if result_map.get("artifact_revision") != entry["committed_revision"]:
        raise _ArtifactIntegrityError("operation_result revision mismatch")
    if result_map.get("run_id") != artifact["run_id"]:
        raise _ArtifactIntegrityError("operation_result run_id mismatch")
    if result_map.get("correlation_id") != artifact["correlation_id"]:
        raise _ArtifactIntegrityError("operation_result correlation_id mismatch")
    _expect_string(result_map.get("artifact_path"), "operation_result.artifact_path")
    if result_map.get("source_hash") != artifact["source"]["hash"]:
        raise _ArtifactIntegrityError("operation_result source_hash mismatch")
    _expect_string(result_map.get("history_hash"), "operation_result.history_hash")
    if not isinstance(result_map.get("output_delta"), list):
        raise _ArtifactIntegrityError("operation_result output_delta invalid")
    if result_map.get("status") == "PENDING":
        _expect_string(result_map.get("suspension_id"), "operation_result.suspension_id")
        if result_map.get("suspension_reason") not in _SUPPORTED_SUSPENSION_REASONS:
            raise _ArtifactIntegrityError("operation_result suspension_reason invalid")
        _expect_nullable_string(result_map.get("promise_id"), "operation_result.promise_id")
    if result_map.get("status") == "ERROR":
        error = _expect_mapping(result_map.get("error"), "operation_result.error")
        _expect_string(error.get("code"), "operation_result.error.code")
        _expect_string(error.get("message"), "operation_result.error.message")
    _validate_strict_json_value(result_map)
    return result_map


def _validate_idempotency(artifact: dict[str, Any]) -> dict[str, Any]:
    idempotency = _expect_mapping(artifact.get("idempotency"), "idempotency")
    resolved = _expect_mapping(idempotency.get("resolved_suspensions"), "idempotency.resolved_suspensions")
    for suspension_id, raw_entry in resolved.items():
        if not isinstance(suspension_id, str):
            raise _ArtifactIntegrityError("resolved suspension id must be string")
        entry = _expect_mapping(raw_entry, f"resolved_suspensions.{suspension_id}")
        if set(entry) != {"signal_hash", "committed_revision", "committed_status", "operation_result"}:
            raise _ArtifactIntegrityError("resolved entry uses invalid schema")
        _validate_hash(entry.get("signal_hash"), "resolved entry signal_hash")
        _expect_int(entry.get("committed_revision"), "resolved entry committed_revision")
        if entry.get("committed_status") not in {"PENDING", "COMPLETED", "ERROR"}:
            raise _ArtifactIntegrityError("resolved entry committed_status invalid")
        _validate_operation_result(entry.get("operation_result"), artifact, entry)
    return idempotency


def _validate_artifact(artifact: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    required = {
        "artifact_schema_version",
        "artifact_hash",
        "status",
        "revision",
        "run_id",
        "correlation_id",
        "execution_engine",
        "persistence_profile",
        "source",
        "initial_bindings",
        "replay_state",
        "history_integrity",
        "active_suspension",
        "idempotency",
        "output_state",
        "terminal",
        "versions",
    }
    if set(artifact) != required:
        raise _ArtifactIntegrityError("artifact field set mismatch")
    artifact_hash = _validate_hash(artifact.get("artifact_hash"), "artifact_hash")
    if _sha256_prefixed_value({key: value for key, value in artifact.items() if key != "artifact_hash"}) != artifact_hash:
        raise _ArtifactIntegrityError("artifact hash mismatch")
    if artifact.get("artifact_schema_version") != "1.0.0":
        raise _ArtifactIntegrityError("unsupported artifact schema")
    status = _expect_string(artifact.get("status"), "status")
    if status not in {"PENDING", "COMPLETED", "ERROR"}:
        raise _ArtifactIntegrityError("invalid artifact status")
    _expect_int(artifact.get("revision"), "revision")
    run_id = _expect_string(artifact.get("run_id"), "run_id")
    try:
        _validate_run_id(run_id)
    except _DurablePreExecutionError as exc:
        raise _ArtifactIntegrityError("artifact run_id is invalid") from exc
    if artifact_path.name != f"{run_id}.json":
        raise _ArtifactIntegrityError("state file name does not match artifact run_id")
    _expect_nullable_string(artifact.get("correlation_id"), "correlation_id")
    if artifact.get("execution_engine") != "tree-walker":
        raise _ArtifactIntegrityError("unsupported execution engine")
    _expect_string(artifact.get("persistence_profile"), "persistence_profile")
    _validate_replay_state_shape(artifact)
    _validate_source_ownership(artifact)
    _validate_initial_bindings_artifact(artifact)
    _validate_history_integrity(artifact)
    _validate_output_state(artifact)
    _validate_versions(artifact)
    active = _validate_active_suspension_shape(artifact.get("active_suspension"), required=status == "PENDING")
    terminal = artifact.get("terminal")
    if status == "PENDING" and terminal is not None:
        raise _ArtifactIntegrityError("PENDING artifact terminal must be null")
    if status != "PENDING":
        if active is not None:
            raise _ArtifactIntegrityError("terminal artifact must not have active suspension")
        terminal_map = _expect_mapping(terminal, "terminal")
        if status == "COMPLETED" and set(terminal_map) != {"status", "exit_code"}:
            raise _ArtifactIntegrityError("COMPLETED terminal schema mismatch")
        if status == "ERROR" and set(terminal_map) != {"status", "exit_code", "error"}:
            raise _ArtifactIntegrityError("ERROR terminal schema mismatch")
        if terminal_map.get("status") != status:
            raise _ArtifactIntegrityError("terminal status mismatch")
        _expect_int(terminal_map.get("exit_code"), "terminal.exit_code")
        if status == "ERROR":
            error = _expect_mapping(terminal_map.get("error"), "terminal.error")
            _expect_string(error.get("code"), "terminal.error.code")
            _expect_string(error.get("message"), "terminal.error.message")
    _validate_persisted_boundary(artifact, active)
    _validate_idempotency(artifact)
    return artifact


def _load_artifact_under_lock(artifact_path: Path) -> dict[str, Any]:
    try:
        raw = artifact_path.read_text(encoding="utf-8")
    except PermissionError as exc:
        raise _DurablePreExecutionError(
            "state file permission denied",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        ) from exc
    except OSError as exc:
        raise _DurablePreExecutionError(
            "state file read failed",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        ) from exc
    try:
        return _json_loads_strict_artifact(raw)
    except (ValueError, TypeError, _ArtifactIntegrityError) as exc:
        raise _ArtifactIntegrityError("artifact JSON is invalid") from exc


def _operation_result_from_public_payload(payload: dict[str, object]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    result.pop("resume_argv", None)
    _validate_strict_json_value(result)
    return result


def _public_payload_from_operation_result(operation_result: dict[str, Any]) -> dict[str, object]:
    payload = copy.deepcopy(operation_result)
    if payload.get("status") == "PENDING":
        payload["resume_argv"] = [
            sys.executable,
            "-m",
            "synapse",
            "resume",
            "--state-file",
            str(payload["artifact_path"]),
            "--suspension-id",
            str(payload["suspension_id"]),
            "--signal-file",
            "<path|->",
        ]
    return payload


def _result_from_operation_result(operation_result: dict[str, Any]) -> DurableRunResult:
    payload = _public_payload_from_operation_result(operation_result)
    exit_code = payload.get("exit_code")
    return DurableRunResult(
        status=str(payload.get("status") or "ERROR"),
        exit_code=int(exit_code) if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 1,
        public_payload=payload,
    )


def _with_resolution(
    *,
    artifact_without_resolution: dict[str, Any],
    previous_idempotency: dict[str, Any],
    resolved_suspension_id: str,
    signal_hash: str,
    public_payload: dict[str, object],
) -> dict[str, Any]:
    operation_result = _operation_result_from_public_payload(public_payload)
    idempotency = copy.deepcopy(previous_idempotency)
    idempotency.setdefault("resolved_suspensions", {})[resolved_suspension_id] = {
        "signal_hash": signal_hash,
        "committed_revision": artifact_without_resolution["revision"],
        "committed_status": artifact_without_resolution["status"],
        "operation_result": operation_result,
    }
    without_hash = {key: value for key, value in artifact_without_resolution.items() if key != "artifact_hash"}
    without_hash["idempotency"] = idempotency
    return _artifact_with_hash(without_hash)


def _reconstruct_boundary(artifact: dict[str, Any]) -> tuple[Interpreter, Any, object]:
    try:
        ast = compile_to_ast(artifact["replay_state"]["source_code"])
    except Exception as exc:
        raise _ArtifactIntegrityError("embedded source parse failed") from exc
    try:
        source_owned = _validate_durable_ast(ast)
        _validate_initial_bindings(copy.deepcopy(artifact["initial_bindings"]["value"]), source_owned)
        interpreter = Interpreter()
        interpreter._durable_mailbox_wait_enabled = True
        interpreter.load_snapshot(copy.deepcopy(artifact["replay_state"]))
        _apply_initial_bindings(interpreter, copy.deepcopy(artifact["initial_bindings"]["value"]))
        flow = interpreter.interpret_async(ast)
        if not hasattr(flow, "send"):
            raise _ArtifactIntegrityError("interpret_async contract moved")
        yielded = next(flow)
    except _DurableUnsupportedError:
        raise
    except StopIteration as exc:
        raise _ArtifactIntegrityError("replay completed before active suspension") from exc
    except Exception as exc:
        raise _ArtifactIntegrityError("replay failed before active suspension") from exc
    if type(yielded).__name__ != "Suspension":
        raise _ArtifactIntegrityError("replay yielded unsupported value")
    if interpreter.replay_cursor != len(interpreter.execution_history):
        raise _ArtifactIntegrityError("replay history not fully consumed")
    if getattr(getattr(interpreter, "runtime_mode", None), "name", "") != "LIVE":
        raise _ArtifactIntegrityError("replay did not transition to LIVE")
    observed_lines = [str(line) for line in interpreter.output_buffer]
    if len(observed_lines) != artifact["output_state"]["line_count"]:
        raise _ArtifactIntegrityError("output prefix line count mismatch")
    if _sha256_prefixed_value(observed_lines) != artifact["output_state"]["digest"]:
        raise _ArtifactIntegrityError("output prefix digest mismatch")
    observed = _active_suspension(
        yielded,
        run_id=artifact["run_id"],
        source_hash=artifact["source"]["hash"],
        initial_bindings_hash=artifact["initial_bindings"]["hash"],
        history_integrity=artifact["history_integrity"],
        output_state=artifact["output_state"],
        sequence=artifact["active_suspension"]["sequence"],
    )
    if observed["boundary_fingerprint"] != artifact["active_suspension"]["boundary_fingerprint"]:
        raise _ArtifactIntegrityError("observed boundary mismatch")
    return interpreter, flow, yielded


def _validate_signal_for_reason(reason: str, signal_value: Any) -> None:
    if reason == "awaiting_llm" and not isinstance(signal_value, str):
        raise _DurablePreExecutionError(
            "awaiting_llm requires string signal",
            exit_code=2,
            error_code=_INVALID_CLI_INPUT,
            public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
        )


def _terminal_descriptor(status: str, exit_code: int, error_code: str | None = None, message: str | None = None) -> dict[str, Any]:
    descriptor: dict[str, Any] = {"status": status, "exit_code": exit_code}
    if status == "ERROR":
        descriptor["error"] = {
            "code": error_code or _RUNTIME_EXECUTION_ERROR,
            "message": message or _PUBLIC_ERROR_MESSAGES["runtime"],
        }
    return descriptor


def _resume_outcome_artifact(
    *,
    previous: dict[str, Any],
    artifact_path: Path,
    interpreter: Interpreter,
    suspension: object | None,
    status: str,
    exit_code: int,
    terminal: dict[str, Any] | None,
    previous_idempotency: dict[str, Any],
    resolved_suspension_id: str,
    signal_hash: str,
    output_delta: list[str],
) -> tuple[dict[str, Any], dict[str, object]]:
    sequence = (
        int(previous["active_suspension"]["sequence"]) + 1
        if suspension is not None
        else int(previous["active_suspension"]["sequence"])
    )
    artifact = _build_artifact(
        status=status,
        run_id=previous["run_id"],
        correlation_id=previous["correlation_id"],
        source_path=Path(str(previous["source"]["path"])),
        source_code=str(previous["replay_state"]["source_code"]),
        initial_bindings=copy.deepcopy(previous["initial_bindings"]["value"]),
        interpreter=interpreter,
        suspension=suspension,
        terminal=terminal,
        revision=int(previous["revision"]) + 1,
        idempotency=previous_idempotency,
        suspension_sequence=sequence,
    )
    if status == "COMPLETED":
        public_payload = _public_completed_payload(artifact, artifact_path, output_delta)
    elif status == "PENDING":
        public_payload = _public_pending_payload(artifact, artifact_path, output_delta)
    else:
        public_payload = _public_committed_error_payload(artifact, artifact_path, output_delta)
    final_artifact = _with_resolution(
        artifact_without_resolution=artifact,
        previous_idempotency=previous_idempotency,
        resolved_suspension_id=resolved_suspension_id,
        signal_hash=signal_hash,
        public_payload=public_payload,
    )
    if status == "COMPLETED":
        public_payload = _public_completed_payload(final_artifact, artifact_path, output_delta)
    elif status == "PENDING":
        public_payload = _public_pending_payload(final_artifact, artifact_path, output_delta)
    else:
        public_payload = _public_committed_error_payload(final_artifact, artifact_path, output_delta)
    return final_artifact, public_payload


def execute_durable_resume(request: DurableResumeRequest, *, stdin: TextIO | None = None) -> DurableRunResult:
    lock_path: Path | None = None
    lock_acquired = False
    committed = False
    result: DurableRunResult
    try:
        artifact_path = _resolve_state_file(request.state_file)
        lock_path = artifact_path.with_name(f"{artifact_path.name}.lock")
        try:
            lock_path.mkdir()
            lock_acquired = True
        except FileExistsError:
            return _durable_failure(
                "LOCKED",
                26,
                _ARTIFACT_EXISTS_OR_LOCKED,
                _PUBLIC_ERROR_MESSAGES["conflict"],
                run_id=_run_id_from_state_filename(artifact_path),
            )
        except OSError as exc:
            if _is_permission_os_error(exc):
                raise _DurablePreExecutionError(
                    "resume lock permission denied",
                    exit_code=2,
                    error_code=_INVALID_CLI_INPUT,
                    public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
                ) from exc
            return _durable_failure(
                "ERROR",
                1,
                _RUNTIME_EXECUTION_ERROR,
                _PUBLIC_ERROR_MESSAGES["runtime"],
                run_id=_run_id_from_state_filename(artifact_path),
            )

        try:
            raw_artifact = _load_artifact_under_lock(artifact_path)
            artifact = _validate_artifact(raw_artifact, artifact_path)
        except _ArtifactIntegrityError:
            run_id, correlation_id = _artifact_identity(locals().get("raw_artifact"))
            return _artifact_failure(run_id, correlation_id)

        try:
            signal_value, signal_hash = _read_signal_value(request, stdin)
        except _DurablePreExecutionError as exc:
            return _durable_failure(
                exc.status,
                exc.exit_code,
                exc.error_code,
                exc.public_message,
                run_id=artifact["run_id"],
                correlation_id=artifact["correlation_id"],
            )

        active = artifact.get("active_suspension")
        active_reason = (
            str(active.get("reason"))
            if isinstance(active, dict) and request.suspension_id == active.get("suspension_id")
            else ""
        )
        flow_signal = signal_value
        if active_reason in MAILBOX_WAIT_REASONS:
            try:
                mailbox_resume = validate_mailbox_resume(signal_value, active_reason, active.get("payload"))
            except MailboxWaitValidationError:
                return _durable_failure(
                    "ERROR",
                    2,
                    _INVALID_CLI_INPUT,
                    _PUBLIC_ERROR_MESSAGES["invalid_input"],
                    run_id=artifact["run_id"],
                    correlation_id=artifact["correlation_id"],
                )
            signal_hash = mailbox_resume.signal_hash
            flow_signal = mailbox_resume.internal_value
        elif is_mailbox_resume_signal(signal_value):
            # A duplicate resume can target an already-terminal artifact, where
            # the prior active mailbox boundary is no longer retained.  It must
            # still use the mailbox profile before idempotency lookup.
            try:
                signal_hash = normalized_mailbox_signal_hash(signal_value)
            except MailboxWaitValidationError:
                return _durable_failure(
                    "ERROR",
                    2,
                    _INVALID_CLI_INPUT,
                    _PUBLIC_ERROR_MESSAGES["invalid_input"],
                    run_id=artifact["run_id"],
                    correlation_id=artifact["correlation_id"],
                )

        resolved = artifact["idempotency"]["resolved_suspensions"]
        if request.suspension_id in resolved:
            entry = resolved[request.suspension_id]
            if entry["signal_hash"] != signal_hash:
                return _resolution_conflict_failure(artifact)
            return _result_from_operation_result(entry["operation_result"])

        if artifact.get("status") != "PENDING" or not isinstance(active, dict) or request.suspension_id != active.get("suspension_id"):
            return _stale_suspension_failure(artifact)

        try:
            _validate_signal_for_reason(str(active["reason"]), signal_value)
        except _DurablePreExecutionError as exc:
            return _durable_failure(
                exc.status,
                exc.exit_code,
                exc.error_code,
                exc.public_message,
                run_id=artifact["run_id"],
                correlation_id=artifact["correlation_id"],
            )

        try:
            interpreter, flow, _ = _reconstruct_boundary(artifact)
        except _DurableUnsupportedError:
            return _durable_failure(
                "ERROR",
                25,
                _UNSUPPORTED_DURABLE_OPERATION_OR_REASON,
                _PUBLIC_ERROR_MESSAGES["unsupported"],
                run_id=artifact["run_id"],
                correlation_id=artifact["correlation_id"],
            )
        except _ArtifactIntegrityError:
            return _boundary_failure(artifact)

        prefix_count = artifact["output_state"]["line_count"]
        previous_idempotency = copy.deepcopy(artifact["idempotency"])
        try:
            try:
                if active_reason in MAILBOX_WAIT_REASONS:
                    interpreter._durable_mailbox_wait_injected_message = copy.deepcopy(flow_signal)
                yielded = flow.send(flow_signal)
            except StopIteration:
                output_lines = [str(line) for line in interpreter.output_buffer]
                new_artifact, public_payload = _resume_outcome_artifact(
                    previous=artifact,
                    artifact_path=artifact_path,
                    interpreter=interpreter,
                    suspension=None,
                    status="COMPLETED",
                    exit_code=0,
                    terminal=_terminal_descriptor("COMPLETED", 0),
                    previous_idempotency=previous_idempotency,
                    resolved_suspension_id=request.suspension_id,
                    signal_hash=signal_hash,
                    output_delta=output_lines[prefix_count:],
                )
                _atomic_commit_json(artifact_path, new_artifact)
                committed = True
                result = DurableRunResult(status="COMPLETED", exit_code=0, public_payload=public_payload)
            except Exception as exc:
                _ = exc
                output_lines = [str(line) for line in interpreter.output_buffer]
                new_artifact, public_payload = _resume_outcome_artifact(
                    previous=artifact,
                    artifact_path=artifact_path,
                    interpreter=interpreter,
                    suspension=None,
                    status="ERROR",
                    exit_code=1,
                    terminal=_terminal_descriptor("ERROR", 1),
                    previous_idempotency=previous_idempotency,
                    resolved_suspension_id=request.suspension_id,
                    signal_hash=signal_hash,
                    output_delta=output_lines[prefix_count:],
                )
                _atomic_commit_json(artifact_path, new_artifact)
                committed = True
                result = DurableRunResult(status="ERROR", exit_code=1, public_payload=public_payload)
            else:
                output_lines = [str(line) for line in interpreter.output_buffer]
                if type(yielded).__name__ != "Suspension":
                    new_artifact, public_payload = _resume_outcome_artifact(
                        previous=artifact,
                        artifact_path=artifact_path,
                        interpreter=interpreter,
                        suspension=None,
                        status="ERROR",
                        exit_code=1,
                        terminal=_terminal_descriptor("ERROR", 1),
                        previous_idempotency=previous_idempotency,
                        resolved_suspension_id=request.suspension_id,
                        signal_hash=signal_hash,
                        output_delta=output_lines[prefix_count:],
                    )
                    _atomic_commit_json(artifact_path, new_artifact)
                    committed = True
                    result = DurableRunResult(status="ERROR", exit_code=1, public_payload=public_payload)
                elif getattr(yielded, "reason", "") not in _SUPPORTED_SUSPENSION_REASONS:
                    new_artifact, public_payload = _resume_outcome_artifact(
                        previous=artifact,
                        artifact_path=artifact_path,
                        interpreter=interpreter,
                        suspension=None,
                        status="ERROR",
                        exit_code=25,
                        terminal=_terminal_descriptor(
                            "ERROR",
                            25,
                            _UNSUPPORTED_DURABLE_OPERATION_OR_REASON,
                            _PUBLIC_ERROR_MESSAGES["unsupported"],
                        ),
                        previous_idempotency=previous_idempotency,
                        resolved_suspension_id=request.suspension_id,
                        signal_hash=signal_hash,
                        output_delta=output_lines[prefix_count:],
                    )
                    _atomic_commit_json(artifact_path, new_artifact)
                    committed = True
                    result = DurableRunResult(status="ERROR", exit_code=25, public_payload=public_payload)
                else:
                    new_artifact, public_payload = _resume_outcome_artifact(
                        previous=artifact,
                        artifact_path=artifact_path,
                        interpreter=interpreter,
                        suspension=yielded,
                        status="PENDING",
                        exit_code=20,
                        terminal=None,
                        previous_idempotency=previous_idempotency,
                        resolved_suspension_id=request.suspension_id,
                        signal_hash=signal_hash,
                        output_delta=output_lines[prefix_count:],
                    )
                    _atomic_commit_json(artifact_path, new_artifact)
                    committed = True
                    result = DurableRunResult(status="PENDING", exit_code=20, public_payload=public_payload)
        except (OSError, TypeError, ValueError):
            return _durable_failure(
                "ERROR",
                1,
                _RUNTIME_EXECUTION_ERROR,
                _PUBLIC_ERROR_MESSAGES["runtime"],
                run_id=artifact["run_id"],
                correlation_id=artifact["correlation_id"],
            )
        return result
    except _DurablePreExecutionError as exc:
        return _durable_failure(exc.status, exc.exit_code, exc.error_code, exc.public_message)
    finally:
        if lock_acquired and lock_path is not None:
            try:
                _remove_lock_directory(lock_path)
            except Exception:
                if committed:
                    object.__setattr__(
                        result,
                        "diagnostics",
                        result.diagnostics + ("STALE_LOCK_AFTER_COMMIT",),
                    )


def execute_durable_run(request: DurableRunRequest, *, stdin: TextIO | None = None) -> DurableRunResult:
    run_id = request.run_id or _new_run_id()
    lock_path: Path | None = None
    lock_acquired = False
    committed = False
    try:
        _validate_run_id(run_id)
        if not request.state_dir.exists() or not request.state_dir.is_dir():
            raise _DurablePreExecutionError(
                "state-dir must be an existing directory",
                exit_code=2,
                error_code=_INVALID_CLI_INPUT,
                public_message=_PUBLIC_ERROR_MESSAGES["invalid_state_dir"],
            )

        source_code = _read_source(request.source_path)
        initial_bindings = _load_initial_bindings(request, stdin)
        try:
            ast = compile_to_ast(source_code)
        except Exception as exc:
            raise _DurablePreExecutionError(
                "source parse failed",
                exit_code=2,
                error_code=_INVALID_CLI_INPUT,
                public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
            ) from exc

        artifact_path = request.state_dir / f"{run_id}.json"
        lock_path = request.state_dir / f"{run_id}.json.lock"

        try:
            lock_path.mkdir()
            lock_acquired = True
        except FileExistsError:
            return _durable_failure(
                "LOCKED",
                26,
                _ARTIFACT_EXISTS_OR_LOCKED,
                _PUBLIC_ERROR_MESSAGES["conflict"],
                run_id=run_id,
                correlation_id=request.correlation_id,
            )
        except OSError as exc:
            if _is_permission_os_error(exc):
                raise _DurablePreExecutionError(
                    "durable run lock permission denied",
                    exit_code=2,
                    error_code=_INVALID_CLI_INPUT,
                    public_message=_PUBLIC_ERROR_MESSAGES["invalid_input"],
                ) from exc
            raise _DurablePreExecutionError(
                "durable run lock failed",
                exit_code=1,
                error_code=_RUNTIME_EXECUTION_ERROR,
                public_message=_PUBLIC_ERROR_MESSAGES["runtime"],
            ) from exc

        if artifact_path.exists():
            return _durable_failure(
                "CONFLICT",
                26,
                _ARTIFACT_EXISTS_OR_LOCKED,
                _PUBLIC_ERROR_MESSAGES["conflict"],
                run_id=run_id,
                correlation_id=request.correlation_id,
            )

        _probe_state_dir(request.state_dir)
        source_owned = _validate_durable_ast(ast)
        _validate_initial_bindings(initial_bindings, source_owned)

        interpreter = Interpreter()
        interpreter._durable_mailbox_wait_enabled = True
        interpreter.source_code = source_code
        _apply_initial_bindings(interpreter, initial_bindings)
        flow = interpreter.interpret_async(ast)
        try:
            status = next(flow)
        except StopIteration:
            terminal = {"status": "COMPLETED", "exit_code": 0}
            artifact = _build_artifact(
                status="COMPLETED",
                run_id=run_id,
                correlation_id=request.correlation_id,
                source_path=request.source_path,
                source_code=source_code,
                initial_bindings=initial_bindings,
                interpreter=interpreter,
                suspension=None,
                terminal=terminal,
            )
            _atomic_commit_json(artifact_path, artifact)
            committed = True
            output_lines = [str(line) for line in interpreter.output_buffer]
            result = DurableRunResult(
                status="COMPLETED",
                exit_code=0,
                public_payload=_public_completed_payload(artifact, artifact_path, output_lines),
            )
        except Exception as exc:
            terminal = {
                "status": "ERROR",
                "exit_code": 1,
                "error": _sanitize_error(exc),
            }
            artifact = _build_artifact(
                status="ERROR",
                run_id=run_id,
                correlation_id=request.correlation_id,
                source_path=request.source_path,
                source_code=source_code,
                initial_bindings=initial_bindings,
                interpreter=interpreter,
                suspension=None,
                terminal=terminal,
            )
            _atomic_commit_json(artifact_path, artifact)
            committed = True
            result = DurableRunResult(
                status="ERROR",
                exit_code=1,
                public_payload=_public_error_payload(artifact),
            )
        else:
            if type(status).__name__ != "Suspension":
                return _durable_failure(
                    "UNSUPPORTED",
                    25,
                    _UNSUPPORTED_DURABLE_OPERATION_OR_REASON,
                    _PUBLIC_ERROR_MESSAGES["unsupported"],
                    run_id=run_id,
                    correlation_id=request.correlation_id,
                )
            reason = getattr(status, "reason", "")
            if reason not in _SUPPORTED_SUSPENSION_REASONS:
                return _durable_failure(
                    "UNSUPPORTED",
                    25,
                    _UNSUPPORTED_DURABLE_OPERATION_OR_REASON,
                    _PUBLIC_ERROR_MESSAGES["unsupported"],
                    run_id=run_id,
                    correlation_id=request.correlation_id,
                )
            artifact = _build_artifact(
                status="PENDING",
                run_id=run_id,
                correlation_id=request.correlation_id,
                source_path=request.source_path,
                source_code=source_code,
                initial_bindings=initial_bindings,
                interpreter=interpreter,
                suspension=status,
                terminal=None,
            )
            _atomic_commit_json(artifact_path, artifact)
            committed = True
            output_lines = [str(line) for line in interpreter.output_buffer]
            result = DurableRunResult(
                status="PENDING",
                exit_code=20,
                public_payload=_public_pending_payload(
                    artifact,
                    artifact_path,
                    output_lines,
                ),
            )
        return result
    except _DurablePreExecutionError as exc:
        return _durable_failure(
            exc.status,
            exc.exit_code,
            exc.error_code,
            exc.public_message,
            run_id=run_id,
            correlation_id=request.correlation_id,
        )
    except OSError as exc:
        if _is_permission_os_error(exc):
            return _durable_failure(
                "ERROR",
                2,
                _INVALID_CLI_INPUT,
                _PUBLIC_ERROR_MESSAGES["invalid_input"],
                run_id=run_id,
                correlation_id=request.correlation_id,
            )
        return _durable_failure(
            "ERROR",
            1,
            _RUNTIME_EXECUTION_ERROR,
            _PUBLIC_ERROR_MESSAGES["artifact_validation"],
            run_id=run_id,
            correlation_id=request.correlation_id,
        )
    except (TypeError, ValueError):
        return _durable_failure(
            "ERROR",
            1,
            _RUNTIME_EXECUTION_ERROR,
            _PUBLIC_ERROR_MESSAGES["artifact_validation"],
            run_id=run_id,
            correlation_id=request.correlation_id,
        )
    except Exception as exc:
        _ = exc
        return _durable_failure(
            "ERROR",
            1,
            _RUNTIME_EXECUTION_ERROR,
            _PUBLIC_ERROR_MESSAGES["runtime"],
            run_id=run_id,
            correlation_id=request.correlation_id,
        )
    finally:
        if lock_acquired and lock_path is not None:
            try:
                _remove_lock_directory(lock_path)
            except Exception as exc:
                if committed:
                    diagnostic = f"STALE_LOCK_AFTER_COMMIT\n{lock_path}"
                    try:
                        result
                    except UnboundLocalError:
                        pass
                    else:
                        object.__setattr__(
                            result,
                            "diagnostics",
                            result.diagnostics + (diagnostic,),
                        )
                else:
                    _ = exc


def execute_loaded_source(request: SourceExecutionRequest, *, interpreter: Interpreter | None = None) -> RuntimeExecutionResult:
    """Execute already-loaded Synapse source through the public runtime primitive."""

    try:
        output = run_source_runtime(request.source, interpreter)
    except Exception as exc:  # keep application error normalization at this layer
        return RuntimeExecutionResult(
            status="ERROR",
            exit_code=1,
            output="",
            diagnostics=(str(exc),),
        )
    return RuntimeExecutionResult(status="OK", exit_code=0, output=output)


def execute_source(request: SourceExecutionRequest) -> RuntimeExecutionResult:
    return execute_loaded_source(request)


def execute_file(request: FileExecutionRequest) -> RuntimeExecutionResult:
    try:
        source = request.path.read_text(encoding="utf-8")
    except Exception as exc:
        return RuntimeExecutionResult(
            status="ERROR",
            exit_code=1,
            output="",
            diagnostics=(f"File not found: {request.path}" if isinstance(exc, FileNotFoundError) else str(exc),),
        )

    if request.record:
        if request.output_dir is None:
            return RuntimeExecutionResult(
                status="ERROR",
                exit_code=1,
                output="",
                diagnostics=("synapse run --record requires --output <artifact_dir>",),
            )
        try:
            manifest = record_source(source, request.output_dir, source_path=str(request.path), layer=request.layer)
        except Exception as exc:
            return RuntimeExecutionResult(status="ERROR", exit_code=1, output="", diagnostics=(str(exc),))
        return RuntimeExecutionResult(
            status="RECORDED",
            exit_code=0,
            output="",
            artifact=ReplayArtifactSummary(
                recorded=str(request.output_dir),
                program_hash=manifest["metadata"]["program_hash"],
                final_history_hash=manifest["final"]["final_history_hash"],
                history_length=manifest["final"]["history_length"],
            ),
        )

    return execute_loaded_source(SourceExecutionRequest(source))


def run_repl(request: ReplRequest, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> ReplResult:
    if request.banner:
        stdout.write("╔══════════════════════════════════════╗\n")
        stdout.write("║  Synapse v0.7.0 - Язык для ИИ        ║\n")
        stdout.write("║  Type 'exit' to quit                 ║\n")
        stdout.write("╚══════════════════════════════════════╝\n")
    interpreter = Interpreter()
    while True:
        try:
            stdout.write(request.prompt)
            stdout.flush()
            line = stdin.readline()
            if line == "":
                break
            line = line.rstrip("\n")
            if line.strip() in ["exit", "quit"]:
                break
            if not line.strip():
                continue
            result = execute_loaded_source(SourceExecutionRequest(line), interpreter=interpreter)
            if result.output:
                stdout.write(f"{result.output}\n")
            if result.exit_code != 0:
                stdout.write(f"Error: {'; '.join(result.diagnostics)}\n")
        except KeyboardInterrupt:
            stdout.write("\n")
            break
        except Exception as exc:
            stderr.write(f"Error: {exc}\n")
    return ReplResult(status="OK", exit_code=0)


def metrics_text() -> str:
    return Interpreter().metrics_text()
