"""Closed repository-scope normalization and coverage rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


_MAX_PATH_LENGTH = 512
_DRIVE_PREFIX = re.compile(r"[A-Za-z]:")
_FORBIDDEN = frozenset("\\*?[]{}!\"'<>|`$\x00")


class ScopeFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    EMPTY = "EMPTY"
    ABSOLUTE = "ABSOLUTE"
    ESCAPE = "ESCAPE"
    WILDCARD = "WILDCARD"
    NON_CANONICAL = "NON_CANONICAL"
    OUTSIDE_SCOPE = "OUTSIDE_SCOPE"


class ScopeViolation(ValueError):
    def __init__(self, failure_code: ScopeFailureCode, detail: str) -> None:
        if type(failure_code) is not ScopeFailureCode:
            raise TypeError("failure_code must be an exact ScopeFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ScopeFailureCode, detail: str) -> ScopeViolation:
    return ScopeViolation(code, detail)


def normalize_repository_path(value: object, *, field_name: str = "path") -> str:
    """Return one canonical repository-relative path or fail closed."""

    if type(value) is not str:
        raise _fail(ScopeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact string")
    if not value or len(value) > _MAX_PATH_LENGTH or value != value.strip():
        raise _fail(ScopeFailureCode.EMPTY, f"{field_name} must be bounded and non-empty")
    if value.startswith(("/", "~")) or _DRIVE_PREFIX.match(value):
        raise _fail(ScopeFailureCode.ABSOLUTE, f"{field_name} must be repository-relative")
    if "\\" in value:
        raise _fail(ScopeFailureCode.NON_CANONICAL, f"{field_name} must use POSIX separators")
    if any(character in _FORBIDDEN for character in value):
        raise _fail(ScopeFailureCode.WILDCARD, f"{field_name} contains a forbidden character")
    if value.endswith("/"):
        raise _fail(ScopeFailureCode.NON_CANONICAL, f"{field_name} must not end with a separator")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise _fail(ScopeFailureCode.ESCAPE, f"{field_name} contains an empty, dot, or parent segment")
    return "/".join(segments)


def scope_entry_covers(prefix: str, path: str) -> bool:
    canonical_prefix = normalize_repository_path(prefix, field_name="scope prefix")
    canonical_path = normalize_repository_path(path, field_name="candidate path")
    return canonical_path == canonical_prefix or canonical_path.startswith(canonical_prefix + "/")


@dataclass(frozen=True)
class RepositoryScope:
    entries: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or not self.entries:
            raise _fail(ScopeFailureCode.EMPTY, "scope must contain at least one entry")
        normalized = tuple(
            normalize_repository_path(item, field_name="scope entry") for item in self.entries
        )
        if normalized != tuple(sorted(set(normalized))):
            raise _fail(ScopeFailureCode.NON_CANONICAL, "scope entries must be sorted and unique")
        for parent in normalized:
            if any(
                parent != child and scope_entry_covers(parent, child)
                for child in normalized
            ):
                raise _fail(ScopeFailureCode.NON_CANONICAL, "scope contains a redundant child entry")

    def covers(self, path: object) -> bool:
        canonical = normalize_repository_path(path, field_name="candidate path")
        return any(scope_entry_covers(entry, canonical) for entry in self.entries)

    def contains_scope(self, other: RepositoryScope) -> bool:
        if type(other) is not RepositoryScope:
            raise _fail(ScopeFailureCode.TYPE_MISMATCH, "other scope must be exact")
        return all(self.covers(entry) for entry in other.entries)

    def to_dict(self) -> dict[str, object]:
        validate_repository_scope(self)
        return {"entries": list(self.entries)}

    @classmethod
    def from_dict(cls, value: object) -> RepositoryScope:
        if type(value) is not dict or set(value) != {"entries"}:
            raise _fail(ScopeFailureCode.NON_CANONICAL, "scope transport has an unknown shape")
        entries = value["entries"]
        if type(entries) is not list:
            raise _fail(ScopeFailureCode.TYPE_MISMATCH, "scope entries transport must be a list")
        return create_repository_scope(tuple(entries))


def create_repository_scope(entries: object) -> RepositoryScope:
    if type(entries) not in (tuple, list):
        raise _fail(ScopeFailureCode.TYPE_MISMATCH, "scope entries must be a tuple or list")
    normalized = sorted(
        {normalize_repository_path(item, field_name="scope entry") for item in entries},
        key=lambda item: (item.count("/"), item),
    )
    if not normalized:
        raise _fail(ScopeFailureCode.EMPTY, "scope must contain at least one entry")
    minimal: list[str] = []
    for entry in normalized:
        if not any(scope_entry_covers(parent, entry) for parent in minimal):
            minimal.append(entry)
    return RepositoryScope(tuple(sorted(minimal)))


def validate_repository_scope(value: RepositoryScope) -> None:
    if type(value) is not RepositoryScope:
        raise _fail(ScopeFailureCode.TYPE_MISMATCH, "scope must be an exact RepositoryScope")
    RepositoryScope(value.entries)
