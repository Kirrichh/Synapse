"""Strict task contract loading and validation for Personal Slice."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .git_workspace import validate_repo_relative_path, validate_repo_relative_prefix


class TaskContractError(ValueError):
    """Raised when a task contract is malformed."""


@dataclass(frozen=True)
class CommandExpectation:
    expected_exit_codes: tuple[int, ...] | None = None
    expected_nonzero_exit: bool = False
    combined_output_contains: tuple[str, ...] = ()
    combined_output_not_contains: tuple[str, ...] = ()
    timeout_seconds: int = 60


@dataclass(frozen=True)
class ReproductionContract:
    command: tuple[str, ...]
    before: CommandExpectation
    after: CommandExpectation


@dataclass(frozen=True)
class AllowedScope:
    exact: tuple[str, ...]
    prefixes: tuple[str, ...] = ()

    def contains(self, path: str) -> bool:
        return path in self.exact or any(path.startswith(prefix) for prefix in self.prefixes)


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    task_class: str
    target_ref: str
    allowed_scope: AllowedScope
    patch_path: str
    required_scaffold_paths: tuple[str, ...]
    reproduction: ReproductionContract
    acceptance_commands: tuple[tuple[str, ...], ...]
    full_suite_commands: tuple[tuple[str, ...], ...]
    baseline_commands: tuple[tuple[str, ...], ...]
    commit_message: str
    deprecated_base_revision: str | None = None


def _require_mapping(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TaskContractError(f"{field} must be an object")
    return data


def _require_str(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TaskContractError(f"{field} must be a non-empty string")
    return value


def _str_tuple(data: dict[str, Any], field: str, *, nonempty: bool = True) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or (nonempty and not value):
        raise TaskContractError(f"{field} must be a {'non-empty ' if nonempty else ''}list")
    if not all(isinstance(item, str) and item for item in value):
        raise TaskContractError(f"{field} must contain only non-empty strings")
    return tuple(value)


def _repo_path_tuple(data: dict[str, Any], field: str, *, nonempty: bool = True) -> tuple[str, ...]:
    try:
        return tuple(validate_repo_relative_path(path, field=f"{field}[]") for path in _str_tuple(data, field, nonempty=nonempty))
    except Exception as exc:  # normalize git workspace validation errors into contract errors
        raise TaskContractError(str(exc)) from exc


def _allowed_scope(data: dict[str, Any]) -> AllowedScope:
    raw = data.get("allowed_scope")
    try:
        if isinstance(raw, list):
            exact = tuple(validate_repo_relative_path(path, field="allowed_scope[]") for path in _str_tuple(data, "allowed_scope"))
            scope = AllowedScope(exact=exact)
        else:
            obj = _require_mapping(raw, "allowed_scope")
            exact = tuple(
                validate_repo_relative_path(path, field="allowed_scope.exact[]")
                for path in _str_tuple(obj, "exact", nonempty=False)
            )
            prefixes = tuple(
                validate_repo_relative_prefix(path, field="allowed_scope.prefixes[]")
                for path in _str_tuple(obj, "prefixes", nonempty=False)
            ) if "prefixes" in obj else ()
            scope = AllowedScope(exact=exact, prefixes=prefixes)
    except Exception as exc:
        if isinstance(exc, TaskContractError):
            raise
        raise TaskContractError(str(exc)) from exc
    if not scope.exact and not scope.prefixes:
        raise TaskContractError("allowed_scope must not be empty")
    return scope


def _command_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskContractError(f"{field} must be a non-empty argv list")
    if not all(isinstance(item, str) and item for item in value):
        raise TaskContractError(f"{field} must contain only non-empty strings")
    return tuple(value)


def _commands_tuple(data: dict[str, Any], field: str, *, nonempty: bool = True) -> tuple[tuple[str, ...], ...]:
    value = data.get(field, [])
    if not isinstance(value, list) or (nonempty and not value):
        raise TaskContractError(f"{field} must be a {'non-empty ' if nonempty else ''}list of argv lists")
    return tuple(_command_tuple(command, f"{field}[{idx}]") for idx, command in enumerate(value))


def _expectation(data: Any, field: str) -> CommandExpectation:
    obj = _require_mapping(data, field)
    expected_exit_codes = obj.get("expected_exit_codes")
    codes_tuple: tuple[int, ...] | None = None
    if expected_exit_codes is not None:
        if not isinstance(expected_exit_codes, list) or not expected_exit_codes:
            raise TaskContractError(f"{field}.expected_exit_codes must be a non-empty list when present")
        if not all(isinstance(code, int) for code in expected_exit_codes):
            raise TaskContractError(f"{field}.expected_exit_codes must contain integers")
        codes_tuple = tuple(expected_exit_codes)

    expected_nonzero_exit = obj.get("expected_nonzero_exit", False)
    if not isinstance(expected_nonzero_exit, bool):
        raise TaskContractError(f"{field}.expected_nonzero_exit must be a boolean")

    timeout_seconds = obj.get("timeout_seconds", 60)
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise TaskContractError(f"{field}.timeout_seconds must be a positive integer")

    return CommandExpectation(
        expected_exit_codes=codes_tuple,
        expected_nonzero_exit=expected_nonzero_exit,
        combined_output_contains=_str_tuple(obj, "combined_output_contains", nonempty=False)
        if "combined_output_contains" in obj else (),
        combined_output_not_contains=_str_tuple(obj, "combined_output_not_contains", nonempty=False)
        if "combined_output_not_contains" in obj else (),
        timeout_seconds=timeout_seconds,
    )


def _reproduction(data: dict[str, Any]) -> ReproductionContract:
    obj = _require_mapping(data.get("reproduction"), "reproduction")
    return ReproductionContract(
        command=_command_tuple(obj.get("command"), "reproduction.command"),
        before=_expectation(obj.get("before"), "reproduction.before"),
        after=_expectation(obj.get("after"), "reproduction.after"),
    )


def parse_task_contract_text(source: str) -> TaskContract:
    try:
        raw = json.loads(source)
    except json.JSONDecodeError as exc:
        raise TaskContractError(f"invalid task JSON: {exc}") from exc

    data = _require_mapping(raw, "task")
    target_ref = _require_str(data, "target_ref")
    if not target_ref.startswith("refs/heads/"):
        raise TaskContractError("target_ref must start with refs/heads/")

    try:
        patch_path = validate_repo_relative_path(_require_str(data, "patch_path"), field="patch_path")
    except Exception as exc:
        raise TaskContractError(str(exc)) from exc

    return TaskContract(
        task_id=_require_str(data, "task_id"),
        task_class=_require_str(data, "task_class"),
        target_ref=target_ref,
        allowed_scope=_allowed_scope(data),
        patch_path=patch_path,
        required_scaffold_paths=_repo_path_tuple(data, "required_scaffold_paths"),
        reproduction=_reproduction(data),
        acceptance_commands=_commands_tuple(data, "acceptance_commands", nonempty=True),
        full_suite_commands=_commands_tuple(data, "full_suite_commands", nonempty=True),
        baseline_commands=_commands_tuple(data, "baseline_commands", nonempty=False),
        commit_message=_require_str(data, "commit_message"),
        deprecated_base_revision=data.get("base_revision") if isinstance(data.get("base_revision"), str) else None,
    )


def load_task_contract(path: str | Path) -> TaskContract:
    contract_path = Path(path)
    try:
        source = contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaskContractError(f"unable to read task contract: {exc}") from exc
    return parse_task_contract_text(source)
