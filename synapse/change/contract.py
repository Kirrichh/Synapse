"""Task-contract parsing and validation for canonical controlled changes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
from typing import Any

TASK_CONTRACT_SCHEMA = "synapse.controlled-change.task/v1"

TASK_CONTRACT_SCHEMA_MISSING = "TASK_CONTRACT_SCHEMA_MISSING"
TASK_CONTRACT_SCHEMA_UNSUPPORTED = "TASK_CONTRACT_SCHEMA_UNSUPPORTED"
REPRODUCTION_INPUTS_MISSING = "REPRODUCTION_INPUTS_MISSING"
REPRODUCTION_INPUTS_INVALID = "REPRODUCTION_INPUTS_INVALID"
REPRODUCTION_INPUT_DUPLICATE = "REPRODUCTION_INPUT_DUPLICATE"
TASK_PATH_NOT_REGULAR_FILE = "TASK_PATH_NOT_REGULAR_FILE"
PATCH_PATH_NOT_REGULAR_FILE = "PATCH_PATH_NOT_REGULAR_FILE"
ALLOWED_SCOPE_EMPTY = "ALLOWED_SCOPE_EMPTY"
ALLOWED_SCOPE_DUPLICATE = "ALLOWED_SCOPE_DUPLICATE"
ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE = "ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE"
INITIAL_WORKTREE_NOT_CLEAN = "INITIAL_WORKTREE_NOT_CLEAN"
TASK_CONTRACT_UNKNOWN_FIELD = "TASK_CONTRACT_UNKNOWN_FIELD"
GIT_OBSERVED_PATH_INVALID = "GIT_OBSERVED_PATH_INVALID"


class TaskContractError(ValueError):
    """Raised when a task contract is malformed or unsafe."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(code if message is None else f"{code}: {message}")


@dataclass(frozen=True)
class CommandExpectation:
    expected_exit_codes: tuple[int, ...] | None = None
    expected_nonzero_exit: bool = False
    combined_output_contains: tuple[str, ...] = ()
    combined_output_not_contains: tuple[str, ...] = ()
    timeout_seconds: int = 60


@dataclass(frozen=True)
class AllowedScope:
    exact: tuple[str, ...]
    prefixes: tuple[str, ...]

    def allows_path(self, path: str) -> bool:
        observed = validate_git_observed_path(path, "changed_path")
        if observed in self.exact:
            return True
        return any(observed == prefix or observed.startswith(prefix + "/") for prefix in self.prefixes)

    def to_json(self) -> dict[str, list[str]]:
        return {"exact": list(self.exact), "prefixes": list(self.prefixes)}


@dataclass(frozen=True)
class ReproductionContract:
    command: tuple[str, ...]
    committed_inputs: tuple[str, ...]
    before: CommandExpectation
    after: CommandExpectation


@dataclass(frozen=True)
class TaskContract:
    schema: str
    task_id: str
    task_class: str
    base_revision: str
    target_ref: str
    allowed_scope: AllowedScope
    patch_path: str
    required_scaffold_paths: tuple[str, ...]
    reproduction: ReproductionContract
    baseline_commands: tuple[tuple[str, ...], ...]
    acceptance_commands: tuple[tuple[str, ...], ...]
    full_suite_commands: tuple[tuple[str, ...], ...]
    commit_message: str


def diagnostic_code(exc: BaseException) -> str:
    return exc.code if isinstance(exc, TaskContractError) else type(exc).__name__


def normalize_contract_repo_path(path: str, field: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a non-empty string")
    if "\0" in path:
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must not contain NUL")
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part == ".." for part in pure.parts) or "" in pure.parts:
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a repository-relative path without traversal")
    return pure.as_posix()


def validate_repo_relative_path(path: str, field: str) -> str:
    return normalize_contract_repo_path(path, field)


def validate_git_observed_path(path: str, field: str) -> str:
    if not isinstance(path, str) or path == "":
        raise TaskContractError(GIT_OBSERVED_PATH_INVALID, f"{field} must be a non-empty Git-observed path")
    if "\0" in path:
        raise TaskContractError(GIT_OBSERVED_PATH_INVALID, f"{field} must not contain NUL")
    if path.startswith("/"):
        raise TaskContractError(GIT_OBSERVED_PATH_INVALID, f"{field} must not be absolute")
    parts = path.split("/")
    if any(part == ".." for part in parts):
        raise TaskContractError(GIT_OBSERVED_PATH_INVALID, f"{field} must not contain a '..' path segment")
    return path


def _reject_unknown_fields(obj: dict[str, Any], allowed_fields: set[str], context: str) -> None:
    unknown = sorted(set(obj) - allowed_fields)
    if unknown:
        raise TaskContractError(TASK_CONTRACT_UNKNOWN_FIELD, f"{context}: unknown field {unknown[0]}")


def _require_mapping(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be an object")
    return data


def _require_str(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a non-empty string")
    return value


def _dedupe_paths(paths: tuple[str, ...], code: str, field: str) -> tuple[str, ...]:
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            raise TaskContractError(code, f"duplicate {field}: {path}")
        seen.add(path)
    return paths


def _path_tuple(data: dict[str, Any], field: str, *, nonempty: bool = True, duplicate_code: str | None = None) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or (nonempty and not value):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a {'non-empty ' if nonempty else ''}list")
    paths = tuple(validate_repo_relative_path(item, f"{field}[]") for item in value)
    if duplicate_code:
        _dedupe_paths(paths, duplicate_code, field)
    return paths


def _str_tuple(data: dict[str, Any], field: str, *, nonempty: bool = True) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or (nonempty and not value):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a {'non-empty ' if nonempty else ''}list")
    if not all(isinstance(item, str) and item for item in value):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must contain only non-empty strings")
    return tuple(value)


def _command_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a non-empty argv list")
    if not all(isinstance(item, str) and item for item in value):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must contain only non-empty strings")
    return tuple(value)


def _commands_tuple(data: dict[str, Any], field: str, *, required: bool = True, nonempty: bool = True) -> tuple[tuple[str, ...], ...]:
    value = data.get(field)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (nonempty and not value):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field} must be a {'non-empty ' if nonempty else ''}list of argv lists")
    return tuple(_command_tuple(command, f"{field}[{idx}]") for idx, command in enumerate(value))


def _expectation(data: Any, field: str) -> CommandExpectation:
    obj = _require_mapping(data, field)
    _reject_unknown_fields(obj, {"expected_exit_codes", "expected_nonzero_exit", "combined_output_contains", "combined_output_not_contains", "timeout_seconds"}, field)
    expected_exit_codes = obj.get("expected_exit_codes")
    codes_tuple: tuple[int, ...] | None = None
    if expected_exit_codes is not None:
        if not isinstance(expected_exit_codes, list) or not expected_exit_codes:
            raise TaskContractError(f"{field.upper()}_INVALID", f"{field}.expected_exit_codes must be a non-empty list when present")
        if not all(isinstance(code, int) for code in expected_exit_codes):
            raise TaskContractError(f"{field.upper()}_INVALID", f"{field}.expected_exit_codes must contain integers")
        codes_tuple = tuple(expected_exit_codes)

    expected_nonzero_exit = obj.get("expected_nonzero_exit", False)
    if not isinstance(expected_nonzero_exit, bool):
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field}.expected_nonzero_exit must be a boolean")

    timeout_seconds = obj.get("timeout_seconds", 60)
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise TaskContractError(f"{field.upper()}_INVALID", f"{field}.timeout_seconds must be a positive integer")

    return CommandExpectation(
        expected_exit_codes=codes_tuple,
        expected_nonzero_exit=expected_nonzero_exit,
        combined_output_contains=_str_tuple(obj, "combined_output_contains", nonempty=False)
        if "combined_output_contains" in obj else (),
        combined_output_not_contains=_str_tuple(obj, "combined_output_not_contains", nonempty=False)
        if "combined_output_not_contains" in obj else (),
        timeout_seconds=timeout_seconds,
    )


def _committed_inputs(obj: dict[str, Any]) -> tuple[str, ...]:
    if "committed_inputs" not in obj:
        raise TaskContractError(REPRODUCTION_INPUTS_MISSING, "reproduction.committed_inputs is required")
    value = obj["committed_inputs"]
    if not isinstance(value, list):
        raise TaskContractError(REPRODUCTION_INPUTS_INVALID, "reproduction.committed_inputs must be a list")
    inputs: list[str] = []
    seen: set[str] = set()
    for item in value:
        try:
            path = validate_repo_relative_path(item, "reproduction.committed_inputs[]")
        except TaskContractError as exc:
            raise TaskContractError(REPRODUCTION_INPUTS_INVALID, str(exc)) from exc
        if path in seen:
            raise TaskContractError(REPRODUCTION_INPUT_DUPLICATE, f"duplicate reproduction input: {path}")
        seen.add(path)
        inputs.append(path)
    return tuple(inputs)


def _reproduction(data: dict[str, Any]) -> ReproductionContract:
    obj = _require_mapping(data.get("reproduction"), "reproduction")
    _reject_unknown_fields(obj, {"command", "committed_inputs", "before", "after"}, "reproduction")
    return ReproductionContract(
        command=_command_tuple(obj.get("command"), "reproduction.command"),
        committed_inputs=_committed_inputs(obj),
        before=_expectation(obj.get("before"), "reproduction.before"),
        after=_expectation(obj.get("after"), "reproduction.after"),
    )


def _normalize_prefix(path: str, field: str) -> str:
    normalized = validate_repo_relative_path(path.rstrip("/"), field)
    return normalized


def _allowed_scope(data: dict[str, Any]) -> AllowedScope:
    value = data.get("allowed_scope")
    if isinstance(value, list):
        exact = tuple(validate_repo_relative_path(item, "allowed_scope[]") for item in value)
        prefixes: tuple[str, ...] = ()
    elif isinstance(value, dict):
        _reject_unknown_fields(value, {"exact", "prefixes"}, "allowed_scope")
        exact_value = value.get("exact", [])
        prefix_value = value.get("prefixes", [])
        if not isinstance(exact_value, list) or not isinstance(prefix_value, list):
            raise TaskContractError("ALLOWED_SCOPE_INVALID", "allowed_scope.exact and allowed_scope.prefixes must be arrays")
        exact = tuple(validate_repo_relative_path(item, "allowed_scope.exact[]") for item in exact_value)
        prefixes = tuple(_normalize_prefix(item, "allowed_scope.prefixes[]") for item in prefix_value)
    else:
        raise TaskContractError("ALLOWED_SCOPE_INVALID", "allowed_scope must be an array or object")

    if not exact and not prefixes:
        raise TaskContractError(ALLOWED_SCOPE_EMPTY, "allowed_scope effective scope must not be empty")
    if len(set(exact)) != len(exact):
        raise TaskContractError(ALLOWED_SCOPE_DUPLICATE, "duplicate allowed_scope exact path")
    if len(set(prefixes)) != len(prefixes):
        raise TaskContractError(ALLOWED_SCOPE_DUPLICATE, "duplicate allowed_scope prefix")
    overlap = set(exact) & set(prefixes)
    if overlap:
        raise TaskContractError(ALLOWED_SCOPE_AMBIGUOUS_DUPLICATE, f"path appears in both exact and prefixes: {sorted(overlap)[0]}")
    return AllowedScope(exact=exact, prefixes=prefixes)


def _schema(data: dict[str, Any]) -> str:
    if "schema" not in data:
        raise TaskContractError(TASK_CONTRACT_SCHEMA_MISSING, "task schema is required")
    schema = data["schema"]
    if schema != TASK_CONTRACT_SCHEMA:
        raise TaskContractError(TASK_CONTRACT_SCHEMA_UNSUPPORTED, f"unsupported task schema: {schema!r}")
    return schema


def parse_task_contract_text(text: str, *, base_revision: str | None = None) -> TaskContract:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskContractError("TASK_JSON_INVALID", f"invalid task JSON: {exc}") from exc

    data = _require_mapping(raw, "task")
    _reject_unknown_fields(data, {"schema", "task_id", "task_class", "base_revision", "target_ref", "allowed_scope", "patch_path", "required_scaffold_paths", "reproduction", "baseline_commands", "acceptance_commands", "full_suite_commands", "commit_message"}, "task")
    schema = _schema(data)
    target_ref = _require_str(data, "target_ref")
    if not target_ref.startswith("refs/heads/"):
        raise TaskContractError("TARGET_REF_INVALID", "target_ref must start with refs/heads/")

    return TaskContract(
        schema=schema,
        task_id=_require_str(data, "task_id"),
        task_class=_require_str(data, "task_class"),
        base_revision=base_revision or _require_str(data, "base_revision"),
        target_ref=target_ref,
        allowed_scope=_allowed_scope(data),
        patch_path=validate_repo_relative_path(_require_str(data, "patch_path"), "patch_path"),
        required_scaffold_paths=_path_tuple(data, "required_scaffold_paths", duplicate_code="REQUIRED_SCAFFOLD_DUPLICATE"),
        reproduction=_reproduction(data),
        baseline_commands=_commands_tuple(data, "baseline_commands", required=False, nonempty=False),
        acceptance_commands=_commands_tuple(data, "acceptance_commands"),
        full_suite_commands=_commands_tuple(data, "full_suite_commands"),
        commit_message=_require_str(data, "commit_message"),
    )
