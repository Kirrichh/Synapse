"""Exact, fail-closed Stage 4 project binding contracts.

Bindings prove that a typed Python symbol or governing-document object exists
in one exact Git commit.  They are immutable content identities, not
verification, admission, execution, publication, replay, or lifecycle
authority.  Resolution never imports or executes repository code and never
uses worktree content as authority.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
import symtable
from typing import Any

from synapse.change.contract import TaskContractError, normalize_contract_repo_path
from synapse.change.workspace import (
    GitWorkspaceError,
    load_committed_bytes,
    ls_tree_entry,
    resolve_revision,
    sha256_bytes,
)

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import ContractViolation, RepositoryRevision, RepositoryRevisionKind


BINDING_SCHEMA_V1 = "synapse.stage4.gold.binding/v1"
BINDING_CONTRACT_VERSION_V1 = "synapse.stage4.gold.binding-contract/v1"
BINDING_DOCUMENT_SCHEMA_V1 = "synapse.stage4.gold.binding-document/v1"
BINDING_IDENTITY_PROFILE_V1 = "synapse.stage4.gold.binding-record/v1"
BINDING_IDENTITY_PREFIX_V1 = b"synapse.stage4.gold.binding-record/v1\x00"
BINDING_ID_TEXT_PREFIX_V1 = "synapse.stage4.gold.binding-record.v1:"
BINDING_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.binding+json"

PYTHON_BINDING_RESOLVER_V1 = "synapse.stage4.gold.python-binding-resolver/v1"
DOCUMENT_BINDING_RESOLVER_V1 = "synapse.stage4.gold.document-binding-resolver/v1"

MAX_PYTHON_SOURCE_BYTES_V1 = 1_048_576
MAX_DOCUMENT_SOURCE_BYTES_V1 = 1_048_576
MAX_PYTHON_AST_NODES_V1 = 100_000
MAX_PYTHON_SCOPE_DEPTH_V1 = 128

_REGULAR_GIT_MODES = frozenset({"100644", "100755"})
_TRUSTED_SEAL = object()
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BINDING_ID_RE = re.compile(
    re.escape(BINDING_ID_TEXT_PREFIX_V1) + r"[0-9a-f]{64}\Z"
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ENCODING_COOKIE_RE = re.compile(
    rb"^[ \t\f]*\#.*?coding[:=][ \t]*([-_.A-Za-z0-9]+)"
)


class BindingKind(str, Enum):
    PYTHON = "PYTHON"
    DOCUMENT = "DOCUMENT"
    REQUIREMENT = "REQUIREMENT"


class PythonSymbolKind(str, Enum):
    MODULE = "MODULE"
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    METHOD = "METHOD"


class RequirementStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class BindingFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_BINDING_KIND = "UNKNOWN_BINDING_KIND"
    UNKNOWN_SYMBOL_KIND = "UNKNOWN_SYMBOL_KIND"
    UNKNOWN_RESOLVER_VERSION = "UNKNOWN_RESOLVER_VERSION"
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
    INVALID_PATH = "INVALID_PATH"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    REPOSITORY_SNAPSHOT_UNAVAILABLE = "REPOSITORY_SNAPSHOT_UNAVAILABLE"
    UNSUPPORTED_GIT_MODE = "UNSUPPORTED_GIT_MODE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    INVALID_PYTHON_ENCODING = "INVALID_PYTHON_ENCODING"
    INVALID_PYTHON_SOURCE = "INVALID_PYTHON_SOURCE"
    INVALID_PYTHON_SCOPE = "INVALID_PYTHON_SCOPE"
    MODULE_MISMATCH = "MODULE_MISMATCH"
    SYMBOL_MISSING = "SYMBOL_MISSING"
    SYMBOL_AMBIGUOUS = "SYMBOL_AMBIGUOUS"
    QUALNAME_MISMATCH = "QUALNAME_MISMATCH"
    SYMBOL_KIND_MISMATCH = "SYMBOL_KIND_MISMATCH"
    SOURCE_SPAN_HASH_MISMATCH = "SOURCE_SPAN_HASH_MISMATCH"
    DOCUMENT_ID_MISMATCH = "DOCUMENT_ID_MISMATCH"
    DOCUMENT_REVISION_MISMATCH = "DOCUMENT_REVISION_MISMATCH"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    SECTION_MISSING = "SECTION_MISSING"
    SECTION_AMBIGUOUS = "SECTION_AMBIGUOUS"
    REQUIREMENT_MISSING = "REQUIREMENT_MISSING"
    REQUIREMENT_SUPERSEDED = "REQUIREMENT_SUPERSEDED"
    BINDING_ID_MISMATCH = "BINDING_ID_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"


class BindingViolation(ValueError):
    """Typed, non-payload-bearing Patch 3 rejection."""

    def __init__(self, failure_code: BindingFailureCode, detail: str) -> None:
        if type(failure_code) is not BindingFailureCode:
            raise TypeError("failure_code must be an exact BindingFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: BindingFailureCode, detail: str) -> BindingViolation:
    return BindingViolation(code, detail)


def _exact_dict(value: object, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} must be an exact object")
    if any(type(key) is not str for key in value):
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} keys must be exact strings")
    missing = [field for field in fields if field not in value]
    if missing:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} is missing a required field")
    if any(field not in fields for field in value):
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} has an unknown field")
    return value


def _exact_list(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} must be an exact list")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} must be an exact non-empty string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} must be valid Unicode text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} must be valid UTF-8 text") from exc
    return value


def _identifier(value: object, name: str) -> str:
    text = _text(value, name)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, f"{name} is not an exact identifier")
    return text


def _sha256(value: object, code: BindingFailureCode, name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(code, f"{name} must be exact lowercase SHA-256")
    return value


def _parse_enum(value: object, enum_type: type[Enum], code: BindingFailureCode, name: str) -> Enum:
    if type(value) is not str:
        raise _fail(code, f"{name} must be an exact known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail(code, f"{name} is unknown") from exc


def _require_contract_version(value: object) -> str:
    if type(value) is not str or value != BINDING_CONTRACT_VERSION_V1:
        raise _fail(BindingFailureCode.CONTRACT_VERSION_MISMATCH, "binding contract version is unsupported")
    return value


def _require_resolver_version(value: object, expected: str) -> str:
    if type(value) is not str or value != expected:
        raise _fail(BindingFailureCode.UNKNOWN_RESOLVER_VERSION, "binding resolver version is unsupported")
    return value


def _require_git_commit_revision(value: object) -> RepositoryRevision:
    if type(value) is not RepositoryRevision:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "repository revision must be exact RepositoryRevision")
    if type(value.kind) is not RepositoryRevisionKind:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "repository revision kind is malformed")
    if value.kind is not RepositoryRevisionKind.GIT_COMMIT:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "bindings require an exact Git commit revision")
    if type(value.git_sha) is not str or _GIT_SHA_RE.fullmatch(value.git_sha) is None:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "bindings require a non-null exact Git commit SHA")
    return value


def _revision_from_dict(value: object) -> RepositoryRevision:
    try:
        revision = RepositoryRevision.from_dict(value)
    except ContractViolation as exc:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "repository revision transport is malformed") from exc
    return _require_git_commit_revision(revision)


def _canonical_path(value: object) -> str:
    if type(value) is not str:
        raise _fail(BindingFailureCode.INVALID_PATH, "binding path must be an exact string")
    try:
        normalized = normalize_contract_repo_path(value, "binding_path")
    except TaskContractError as exc:
        raise _fail(BindingFailureCode.INVALID_PATH, "binding path is unsafe") from exc
    if normalized != value:
        raise _fail(BindingFailureCode.INVALID_PATH, "binding path is a non-canonical alias")
    if "//" in value or value.startswith("./") or value.endswith("/"):
        raise _fail(BindingFailureCode.INVALID_PATH, "binding path is a non-canonical alias")
    return value


def _load_snapshot_bytes(
    repo_root: object,
    repository_revision: object,
    path: object,
    *,
    maximum_bytes: int,
) -> tuple[RepositoryRevision, str, bytes]:
    revision = _require_git_commit_revision(repository_revision)
    canonical_path = _canonical_path(path)
    assert revision.git_sha is not None
    try:
        resolved = resolve_revision(repo_root, revision.git_sha)
    except (GitWorkspaceError, OSError, ValueError) as exc:
        raise _fail(
            BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE,
            "recorded Git commit is unavailable",
        ) from exc
    if type(resolved) is not str or resolved != revision.git_sha:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "repository did not resolve the exact recorded commit")
    try:
        entry = ls_tree_entry(repo_root, revision.git_sha, canonical_path)
    except (GitWorkspaceError, TaskContractError, OSError, ValueError) as exc:
        raise _fail(BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE, "Git tree entry is unavailable") from exc
    if entry is None:
        raise _fail(BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE, "recorded path is absent from the Git snapshot")
    if entry.path != canonical_path or entry.mode not in _REGULAR_GIT_MODES or entry.object_type != "blob":
        raise _fail(BindingFailureCode.UNSUPPORTED_GIT_MODE, "binding path is not an exact regular Git blob")
    try:
        raw = load_committed_bytes(repo_root, revision.git_sha, canonical_path)
    except (GitWorkspaceError, TaskContractError, OSError, ValueError) as exc:
        raise _fail(BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE, "committed blob is unavailable") from exc
    if type(raw) is not bytes:
        raise _fail(BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE, "committed blob result is malformed")
    if len(raw) > maximum_bytes:
        raise _fail(BindingFailureCode.RESOURCE_LIMIT_EXCEEDED, "binding source exceeds the v1 byte limit")
    return revision, canonical_path, raw


@dataclass(frozen=True, init=False)
class BindingId:
    value: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BindingId:
        raise TypeError("BindingId is platform-computed")

    def to_dict(self) -> dict[str, str]:
        _validate_binding_id(self)
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> BindingId:
        data = _exact_dict(value, ("value",), "binding_id")
        return _make_binding_id(_binding_id_text(data["value"]))


def _binding_id_text(value: object) -> str:
    if type(value) is not str or _BINDING_ID_RE.fullmatch(value) is None:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "binding ID is malformed")
    return value


def _make_binding_id(value: str) -> BindingId:
    _binding_id_text(value)
    result = object.__new__(BindingId)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    _validate_binding_id(result)
    return result


def _validate_binding_id(value: object) -> BindingId:
    if type(value) is not BindingId or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BindingFailureCode.TRUSTED_OBJECT_FORGED, "binding ID is not platform sealed")
    _binding_id_text(value.value)
    return value


def _canonical_payload(value: dict[str, object]) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identity_for_payload(canonical_payload: bytes) -> BindingId:
    if type(canonical_payload) is not bytes:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "canonical binding payload must be exact bytes")
    digest = hashlib.sha256(BINDING_IDENTITY_PREFIX_V1 + canonical_payload).hexdigest()
    return _make_binding_id(BINDING_ID_TEXT_PREFIX_V1 + digest)


def _common_payload(
    *,
    binding_kind: BindingKind,
    repository_revision: RepositoryRevision,
    path: str,
    contract_version: str,
    resolver_version: str,
) -> dict[str, object]:
    _require_git_commit_revision(repository_revision)
    _canonical_path(path)
    _require_contract_version(contract_version)
    return {
        "schema_version": BINDING_SCHEMA_V1,
        "binding_kind": binding_kind.value,
        "repository_revision": repository_revision.to_dict(),
        "path": path,
        "contract_version": contract_version,
        "resolver_version": resolver_version,
    }


@dataclass(frozen=True, init=False)
class PythonBinding:
    schema_version: str
    binding_kind: BindingKind
    repository_revision: RepositoryRevision
    path: str
    module: str
    qualname: str
    symbol_kind: PythonSymbolKind
    source_span_hash: str
    contract_version: str
    resolver_version: str
    binding_id: BindingId
    _canonical_payload: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PythonBinding:
        raise TypeError("PythonBinding is created only by exact Git resolution")

    def to_dict(self) -> dict[str, object]:
        _validate_python_binding_record(self)
        return {**_python_payload(self), "binding_id": self.binding_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        repo_root: object,
        consumer_revision: RepositoryRevision,
    ) -> PythonBinding:
        binding = binding_from_dict(
            value,
            repo_root=repo_root,
            consumer_revision=consumer_revision,
        )
        if type(binding) is not PythonBinding:
            raise _fail(BindingFailureCode.UNKNOWN_BINDING_KIND, "transport is not a Python binding")
        return binding


@dataclass(frozen=True, init=False)
class DocumentBinding:
    schema_version: str
    binding_kind: BindingKind
    repository_revision: RepositoryRevision
    path: str
    document_id: str
    document_revision: str
    section_id: str
    source_hash: str
    contract_version: str
    resolver_version: str
    binding_id: BindingId
    _canonical_payload: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> DocumentBinding:
        raise TypeError("DocumentBinding is created only by exact Git resolution")

    def to_dict(self) -> dict[str, object]:
        _validate_document_binding_record(self)
        return {**_document_payload(self), "binding_id": self.binding_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        repo_root: object,
        consumer_revision: RepositoryRevision,
    ) -> DocumentBinding:
        binding = binding_from_dict(
            value,
            repo_root=repo_root,
            consumer_revision=consumer_revision,
        )
        if type(binding) is not DocumentBinding:
            raise _fail(BindingFailureCode.UNKNOWN_BINDING_KIND, "transport is not a document binding")
        return binding


@dataclass(frozen=True, init=False)
class RequirementBinding:
    schema_version: str
    binding_kind: BindingKind
    repository_revision: RepositoryRevision
    path: str
    document_id: str
    document_revision: str
    section_id: str
    requirement_ids: tuple[str, ...]
    source_hash: str
    contract_version: str
    resolver_version: str
    binding_id: BindingId
    _canonical_payload: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RequirementBinding:
        raise TypeError("RequirementBinding is created only by exact Git resolution")

    def to_dict(self) -> dict[str, object]:
        _validate_requirement_binding_record(self)
        return {**_requirement_payload(self), "binding_id": self.binding_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        repo_root: object,
        consumer_revision: RepositoryRevision,
    ) -> RequirementBinding:
        binding = binding_from_dict(
            value,
            repo_root=repo_root,
            consumer_revision=consumer_revision,
        )
        if type(binding) is not RequirementBinding:
            raise _fail(BindingFailureCode.UNKNOWN_BINDING_KIND, "transport is not a requirement binding")
        return binding


Binding = PythonBinding | DocumentBinding | RequirementBinding


def _python_payload(value: PythonBinding) -> dict[str, object]:
    return {
        **_common_payload(
            binding_kind=BindingKind.PYTHON,
            repository_revision=value.repository_revision,
            path=value.path,
            contract_version=value.contract_version,
            resolver_version=value.resolver_version,
        ),
        "module": value.module,
        "qualname": value.qualname,
        "symbol_kind": value.symbol_kind.value,
        "source_span_hash": value.source_span_hash,
    }


def _document_payload(value: DocumentBinding) -> dict[str, object]:
    return {
        **_common_payload(
            binding_kind=BindingKind.DOCUMENT,
            repository_revision=value.repository_revision,
            path=value.path,
            contract_version=value.contract_version,
            resolver_version=value.resolver_version,
        ),
        "document_id": value.document_id,
        "document_revision": value.document_revision,
        "section_id": value.section_id,
        "source_hash": value.source_hash,
    }


def _requirement_payload(value: RequirementBinding) -> dict[str, object]:
    return {
        **_common_payload(
            binding_kind=BindingKind.REQUIREMENT,
            repository_revision=value.repository_revision,
            path=value.path,
            contract_version=value.contract_version,
            resolver_version=value.resolver_version,
        ),
        "document_id": value.document_id,
        "document_revision": value.document_revision,
        "section_id": value.section_id,
        "requirement_ids": list(value.requirement_ids),
        "source_hash": value.source_hash,
    }


def _seal_binding(cls: type[Binding], fields: dict[str, object]) -> Binding:
    payload = fields.pop("_payload")
    assert type(payload) is dict
    canonical = _canonical_payload(payload)
    binding_id = _identity_for_payload(canonical)
    result = object.__new__(cls)
    for name, field_value in fields.items():
        object.__setattr__(result, name, field_value)
    object.__setattr__(result, "binding_id", binding_id)
    object.__setattr__(result, "_canonical_payload", canonical)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    return result


def _validate_common_record(value: object, expected_type: type[Binding], expected_kind: BindingKind) -> None:
    if type(value) is not expected_type or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BindingFailureCode.TRUSTED_OBJECT_FORGED, "binding is not an exact factory-sealed record")
    if type(value.schema_version) is not str or value.schema_version != BINDING_SCHEMA_V1:
        raise _fail(BindingFailureCode.UNKNOWN_SCHEMA, "binding schema is unsupported")
    if type(value.binding_kind) is not BindingKind or value.binding_kind is not expected_kind:
        raise _fail(BindingFailureCode.UNKNOWN_BINDING_KIND, "binding kind is inconsistent")
    _require_git_commit_revision(value.repository_revision)
    _canonical_path(value.path)
    _require_contract_version(value.contract_version)
    _validate_binding_id(value.binding_id)
    if type(value._canonical_payload) is not bytes:
        raise _fail(BindingFailureCode.TRUSTED_OBJECT_FORGED, "binding canonical payload is malformed")


def _validate_payload_identity(value: Binding, payload: dict[str, object]) -> None:
    canonical = _canonical_payload(payload)
    if canonical != value._canonical_payload:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "binding canonical payload changed")
    expected = _identity_for_payload(canonical)
    if value.binding_id.value != expected.value:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "binding identity does not match its payload")


def _validate_python_binding_record(value: object) -> PythonBinding:
    _validate_common_record(value, PythonBinding, BindingKind.PYTHON)
    assert type(value) is PythonBinding
    _require_resolver_version(value.resolver_version, PYTHON_BINDING_RESOLVER_V1)
    _text(value.module, "module")
    _text(value.qualname, "qualname")
    if type(value.symbol_kind) is not PythonSymbolKind:
        raise _fail(BindingFailureCode.UNKNOWN_SYMBOL_KIND, "Python symbol kind is malformed")
    _sha256(value.source_span_hash, BindingFailureCode.SOURCE_SPAN_HASH_MISMATCH, "source span hash")
    _validate_payload_identity(value, _python_payload(value))
    return value


def _validate_document_binding_record(value: object) -> DocumentBinding:
    _validate_common_record(value, DocumentBinding, BindingKind.DOCUMENT)
    assert type(value) is DocumentBinding
    _require_resolver_version(value.resolver_version, DOCUMENT_BINDING_RESOLVER_V1)
    _identifier(value.document_id, "document_id")
    _identifier(value.document_revision, "document_revision")
    _identifier(value.section_id, "section_id")
    _sha256(value.source_hash, BindingFailureCode.SOURCE_HASH_MISMATCH, "document source hash")
    _validate_payload_identity(value, _document_payload(value))
    return value


def _validate_requirement_ids(value: object) -> tuple[str, ...]:
    if type(value) not in (list, tuple) or not value:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "requirement_ids must be a non-empty exact list or tuple")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        requirement_id = _identifier(item, "requirement_id")
        if requirement_id in seen:
            raise _fail(BindingFailureCode.TYPE_MISMATCH, "requirement_ids contains a duplicate")
        seen.add(requirement_id)
        result.append(requirement_id)
    return tuple(result)


def _validate_requirement_binding_record(value: object) -> RequirementBinding:
    _validate_common_record(value, RequirementBinding, BindingKind.REQUIREMENT)
    assert type(value) is RequirementBinding
    _require_resolver_version(value.resolver_version, DOCUMENT_BINDING_RESOLVER_V1)
    _identifier(value.document_id, "document_id")
    _identifier(value.document_revision, "document_revision")
    _identifier(value.section_id, "section_id")
    if type(value.requirement_ids) is not tuple:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "sealed requirement IDs must be an exact tuple")
    if _validate_requirement_ids(value.requirement_ids) != value.requirement_ids:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "sealed requirement IDs changed")
    _sha256(value.source_hash, BindingFailureCode.SOURCE_HASH_MISMATCH, "document source hash")
    _validate_payload_identity(value, _requirement_payload(value))
    return value


def _decode_python_source(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _fail(BindingFailureCode.INVALID_PYTHON_ENCODING, "UTF-8 BOM is forbidden by resolver v1")
    for line in raw.splitlines()[:2]:
        match = _ENCODING_COOKIE_RE.match(line)
        if match is not None:
            declared = match.group(1).decode("ascii").lower().replace("_", "-")
            if declared not in {"utf-8", "utf8"}:
                raise _fail(BindingFailureCode.INVALID_PYTHON_ENCODING, "non-UTF-8 encoding cookie is forbidden")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail(BindingFailureCode.INVALID_PYTHON_ENCODING, "Python source is not strict UTF-8") from exc


def _parse_python(source: str, path: str) -> ast.Module:
    try:
        tree = ast.parse(source, filename=path, mode="exec")
    except (SyntaxError, ValueError) as exc:
        raise _fail(BindingFailureCode.INVALID_PYTHON_SOURCE, "Python AST parsing failed") from exc
    except (RecursionError, MemoryError) as exc:
        raise _fail(BindingFailureCode.RESOURCE_LIMIT_EXCEEDED, "Python AST parsing exceeded resources") from exc
    _validate_ast_limits(tree)
    try:
        symtable.symtable(source, path, "exec")
    except SyntaxError as exc:
        raise _fail(BindingFailureCode.INVALID_PYTHON_SCOPE, "Python lexical scope is invalid") from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        raise _fail(BindingFailureCode.RESOURCE_LIMIT_EXCEEDED, "Python scope analysis exceeded resources") from exc
    return tree


_SCOPE_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _validate_ast_limits(root: ast.AST) -> None:
    stack: list[tuple[ast.AST, int]] = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_PYTHON_AST_NODES_V1:
            raise _fail(BindingFailureCode.RESOURCE_LIMIT_EXCEEDED, "Python AST exceeds the v1 node limit")
        next_depth = depth + (1 if isinstance(node, _SCOPE_NODES) else 0)
        if next_depth > MAX_PYTHON_SCOPE_DEPTH_V1:
            raise _fail(BindingFailureCode.RESOURCE_LIMIT_EXCEEDED, "Python lexical scope exceeds the v1 depth limit")
        children: list[ast.AST] = []
        for _field, field_value in ast.iter_fields(node):
            if isinstance(field_value, ast.AST):
                children.append(field_value)
            elif type(field_value) is list:
                children.extend(item for item in field_value if isinstance(item, ast.AST))
        for child in reversed(children):
            stack.append((child, next_depth))


def _module_from_path(path: str) -> str:
    if not path.endswith(".py"):
        raise _fail(BindingFailureCode.MODULE_MISMATCH, "Python binding path must end in .py")
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not part.isidentifier() for part in parts):
        raise _fail(BindingFailureCode.MODULE_MISMATCH, "Python path does not map to an exact root module")
    return ".".join(parts)


@dataclass(frozen=True)
class _Declaration:
    qualname: str
    kind: PythonSymbolKind
    node: ast.AST


def _declarations(tree: ast.Module) -> tuple[_Declaration, ...]:
    result: list[_Declaration] = []

    def visit_body(body: list[ast.stmt], class_prefix: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if class_prefix:
                    result.append(
                        _Declaration(".".join((*class_prefix, node.name)), PythonSymbolKind.METHOD, node)
                    )
                else:
                    result.append(_Declaration(node.name, PythonSymbolKind.FUNCTION, node))
            elif isinstance(node, ast.ClassDef):
                class_path = (*class_prefix, node.name)
                result.append(_Declaration(".".join(class_path), PythonSymbolKind.CLASS, node))
                visit_body(node.body, class_path)

    visit_body(tree.body, ())
    return tuple(result)


def _source_span(raw: bytes, node: ast.AST) -> bytes:
    decorators = getattr(node, "decorator_list", None)
    start_line = decorators[0].lineno if decorators else getattr(node, "lineno", None)
    end_line = getattr(node, "end_lineno", None)
    end_column = getattr(node, "end_col_offset", None)
    if type(start_line) is not int or type(end_line) is not int or type(end_column) is not int:
        raise _fail(BindingFailureCode.INVALID_PYTHON_SOURCE, "Python declaration lacks exact source positions")
    lines = raw.splitlines(keepends=True)
    if not (1 <= start_line <= end_line <= len(lines)):
        raise _fail(BindingFailureCode.INVALID_PYTHON_SOURCE, "Python source positions are out of range")
    start = sum(len(line) for line in lines[: start_line - 1])
    end_base = sum(len(line) for line in lines[: end_line - 1])
    physical_end = lines[end_line - 1]
    content_end = len(physical_end.rstrip(b"\r\n"))
    if end_column < 0 or end_column > content_end:
        raise _fail(BindingFailureCode.INVALID_PYTHON_SOURCE, "Python source end position is invalid")
    return raw[start : end_base + end_column]


def resolve_python_binding(
    repo_root: object,
    *,
    repository_revision: RepositoryRevision,
    path: str,
    module: str,
    qualname: str,
    symbol_kind: PythonSymbolKind,
    contract_version: str,
    resolver_version: str,
) -> PythonBinding:
    revision, canonical_path, raw = _load_snapshot_bytes(
        repo_root,
        repository_revision,
        path,
        maximum_bytes=MAX_PYTHON_SOURCE_BYTES_V1,
    )
    _require_contract_version(contract_version)
    _require_resolver_version(resolver_version, PYTHON_BINDING_RESOLVER_V1)
    expected_module = _module_from_path(canonical_path)
    if type(module) is not str or module != expected_module:
        raise _fail(BindingFailureCode.MODULE_MISMATCH, "module does not match the canonical repository path")
    _text(qualname, "qualname")
    if type(symbol_kind) is not PythonSymbolKind:
        raise _fail(BindingFailureCode.UNKNOWN_SYMBOL_KIND, "Python symbol kind must be exact")
    source = _decode_python_source(raw)
    tree = _parse_python(source, canonical_path)
    if symbol_kind is PythonSymbolKind.MODULE:
        if qualname != expected_module:
            raise _fail(BindingFailureCode.QUALNAME_MISMATCH, "module qualname must equal the exact module")
        span_hash = sha256_bytes(raw)
    else:
        matches = [declaration for declaration in _declarations(tree) if declaration.qualname == qualname]
        if len(matches) > 1:
            raise _fail(BindingFailureCode.SYMBOL_AMBIGUOUS, "Python qualname has duplicate declarations")
        if not matches:
            raise _fail(BindingFailureCode.SYMBOL_MISSING, "exact Python qualname is absent")
        declaration = matches[0]
        if declaration.kind is not symbol_kind:
            raise _fail(BindingFailureCode.SYMBOL_KIND_MISMATCH, "Python declaration kind does not match")
        span_hash = sha256_bytes(_source_span(raw, declaration.node))
    payload = {
        **_common_payload(
            binding_kind=BindingKind.PYTHON,
            repository_revision=revision,
            path=canonical_path,
            contract_version=contract_version,
            resolver_version=resolver_version,
        ),
        "module": module,
        "qualname": qualname,
        "symbol_kind": symbol_kind.value,
        "source_span_hash": span_hash,
    }
    binding = _seal_binding(
        PythonBinding,
        {
            "schema_version": BINDING_SCHEMA_V1,
            "binding_kind": BindingKind.PYTHON,
            "repository_revision": revision,
            "path": canonical_path,
            "module": module,
            "qualname": qualname,
            "symbol_kind": symbol_kind,
            "source_span_hash": span_hash,
            "contract_version": contract_version,
            "resolver_version": resolver_version,
            "_payload": payload,
        },
    )
    assert type(binding) is PythonBinding
    return _validate_python_binding_record(binding)


@dataclass(frozen=True)
class _DocumentSnapshot:
    revision: RepositoryRevision
    path: str
    document_id: str
    document_revision: str
    section_id: str
    requirement_statuses: dict[str, RequirementStatus]
    source_hash: str


def _parse_document(
    raw: bytes,
    *,
    expected_document_id: object,
    expected_document_revision: object,
    expected_section_id: object,
) -> tuple[str, str, str, dict[str, RequirementStatus]]:
    try:
        decoded = decode_stage4_canonical_bytes(
            raw,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(BindingFailureCode.SOURCE_HASH_MISMATCH, "document bytes are not exact canonical Stage 4 data") from exc
    data = _exact_dict(decoded, ("schema_version", "document_id", "document_revision", "sections"), "document")
    if type(data["schema_version"]) is not str or data["schema_version"] != BINDING_DOCUMENT_SCHEMA_V1:
        raise _fail(BindingFailureCode.UNKNOWN_SCHEMA, "document schema is unsupported")
    document_id = _identifier(data["document_id"], "document_id")
    document_revision = _identifier(data["document_revision"], "document_revision")
    section_id = _identifier(expected_section_id, "section_id")
    if document_id != expected_document_id or type(expected_document_id) is not str:
        raise _fail(BindingFailureCode.DOCUMENT_ID_MISMATCH, "document ID does not match")
    if document_revision != expected_document_revision or type(expected_document_revision) is not str:
        raise _fail(BindingFailureCode.DOCUMENT_REVISION_MISMATCH, "document revision does not match")
    sections = _exact_list(data["sections"], "document.sections")
    matches: list[dict[str, Any]] = []
    seen_sections: set[str] = set()
    for raw_section in sections:
        section = _exact_dict(raw_section, ("section_id", "content", "requirements"), "document.section")
        observed_section_id = _identifier(section["section_id"], "section_id")
        if observed_section_id in seen_sections:
            raise _fail(BindingFailureCode.SECTION_AMBIGUOUS, "document contains duplicate section IDs")
        seen_sections.add(observed_section_id)
        if observed_section_id == section_id:
            matches.append(section)
    if not matches:
        raise _fail(BindingFailureCode.SECTION_MISSING, "exact document section is absent")
    if len(matches) != 1:
        raise _fail(BindingFailureCode.SECTION_AMBIGUOUS, "document section identity is ambiguous")
    requirements = _exact_list(matches[0]["requirements"], "section.requirements")
    statuses: dict[str, RequirementStatus] = {}
    for raw_requirement in requirements:
        requirement = _exact_dict(raw_requirement, ("requirement_id", "status"), "section.requirement")
        requirement_id = _identifier(requirement["requirement_id"], "requirement_id")
        if requirement_id in statuses:
            raise _fail(BindingFailureCode.TYPE_MISMATCH, "section contains duplicate requirement IDs")
        status = _parse_enum(
            requirement["status"],
            RequirementStatus,
            BindingFailureCode.TYPE_MISMATCH,
            "requirement status",
        )
        assert isinstance(status, RequirementStatus)
        statuses[requirement_id] = status
    return document_id, document_revision, section_id, statuses


def _load_document_snapshot(
    repo_root: object,
    *,
    repository_revision: RepositoryRevision,
    path: str,
    document_id: str,
    document_revision: str,
    section_id: str,
    contract_version: str,
    resolver_version: str,
) -> _DocumentSnapshot:
    revision, canonical_path, raw = _load_snapshot_bytes(
        repo_root,
        repository_revision,
        path,
        maximum_bytes=MAX_DOCUMENT_SOURCE_BYTES_V1,
    )
    _require_contract_version(contract_version)
    _require_resolver_version(resolver_version, DOCUMENT_BINDING_RESOLVER_V1)
    actual_document_id, actual_revision, actual_section, statuses = _parse_document(
        raw,
        expected_document_id=document_id,
        expected_document_revision=document_revision,
        expected_section_id=section_id,
    )
    return _DocumentSnapshot(
        revision,
        canonical_path,
        actual_document_id,
        actual_revision,
        actual_section,
        statuses,
        sha256_bytes(raw),
    )


def resolve_document_binding(
    repo_root: object,
    *,
    repository_revision: RepositoryRevision,
    path: str,
    document_id: str,
    document_revision: str,
    section_id: str,
    contract_version: str,
    resolver_version: str,
) -> DocumentBinding:
    snapshot = _load_document_snapshot(
        repo_root,
        repository_revision=repository_revision,
        path=path,
        document_id=document_id,
        document_revision=document_revision,
        section_id=section_id,
        contract_version=contract_version,
        resolver_version=resolver_version,
    )
    payload = {
        **_common_payload(
            binding_kind=BindingKind.DOCUMENT,
            repository_revision=snapshot.revision,
            path=snapshot.path,
            contract_version=contract_version,
            resolver_version=resolver_version,
        ),
        "document_id": snapshot.document_id,
        "document_revision": snapshot.document_revision,
        "section_id": snapshot.section_id,
        "source_hash": snapshot.source_hash,
    }
    binding = _seal_binding(
        DocumentBinding,
        {
            "schema_version": BINDING_SCHEMA_V1,
            "binding_kind": BindingKind.DOCUMENT,
            "repository_revision": snapshot.revision,
            "path": snapshot.path,
            "document_id": snapshot.document_id,
            "document_revision": snapshot.document_revision,
            "section_id": snapshot.section_id,
            "source_hash": snapshot.source_hash,
            "contract_version": contract_version,
            "resolver_version": resolver_version,
            "_payload": payload,
        },
    )
    assert type(binding) is DocumentBinding
    return _validate_document_binding_record(binding)


def resolve_requirement_binding(
    repo_root: object,
    *,
    repository_revision: RepositoryRevision,
    path: str,
    document_id: str,
    document_revision: str,
    section_id: str,
    requirement_ids: object,
    contract_version: str,
    resolver_version: str,
) -> RequirementBinding:
    requested = _validate_requirement_ids(requirement_ids)
    snapshot = _load_document_snapshot(
        repo_root,
        repository_revision=repository_revision,
        path=path,
        document_id=document_id,
        document_revision=document_revision,
        section_id=section_id,
        contract_version=contract_version,
        resolver_version=resolver_version,
    )
    for requirement_id in requested:
        status = snapshot.requirement_statuses.get(requirement_id)
        if status is None:
            raise _fail(BindingFailureCode.REQUIREMENT_MISSING, "exact requirement ID is absent")
        if status is RequirementStatus.SUPERSEDED:
            raise _fail(BindingFailureCode.REQUIREMENT_SUPERSEDED, "requirement is superseded")
    payload = {
        **_common_payload(
            binding_kind=BindingKind.REQUIREMENT,
            repository_revision=snapshot.revision,
            path=snapshot.path,
            contract_version=contract_version,
            resolver_version=resolver_version,
        ),
        "document_id": snapshot.document_id,
        "document_revision": snapshot.document_revision,
        "section_id": snapshot.section_id,
        "requirement_ids": list(requested),
        "source_hash": snapshot.source_hash,
    }
    binding = _seal_binding(
        RequirementBinding,
        {
            "schema_version": BINDING_SCHEMA_V1,
            "binding_kind": BindingKind.REQUIREMENT,
            "repository_revision": snapshot.revision,
            "path": snapshot.path,
            "document_id": snapshot.document_id,
            "document_revision": snapshot.document_revision,
            "section_id": snapshot.section_id,
            "requirement_ids": requested,
            "source_hash": snapshot.source_hash,
            "contract_version": contract_version,
            "resolver_version": resolver_version,
            "_payload": payload,
        },
    )
    assert type(binding) is RequirementBinding
    return _validate_requirement_binding_record(binding)


def _require_consumer_revision(recorded: RepositoryRevision, consumer: object) -> RepositoryRevision:
    recorded_revision = _require_git_commit_revision(recorded)
    consumer_revision = _require_git_commit_revision(consumer)
    if consumer_revision != recorded_revision:
        raise _fail(BindingFailureCode.REVISION_MISMATCH, "consumer revision differs from recorded binding revision")
    return consumer_revision


def consume_python_binding(
    repo_root: object,
    binding: object,
    *,
    repository_revision: RepositoryRevision,
) -> PythonBinding:
    current = _validate_python_binding_record(binding)
    consumer = _require_consumer_revision(current.repository_revision, repository_revision)
    resolved = resolve_python_binding(
        repo_root,
        repository_revision=consumer,
        path=current.path,
        module=current.module,
        qualname=current.qualname,
        symbol_kind=current.symbol_kind,
        contract_version=current.contract_version,
        resolver_version=current.resolver_version,
    )
    if resolved.source_span_hash != current.source_span_hash:
        raise _fail(BindingFailureCode.SOURCE_SPAN_HASH_MISMATCH, "Python source span changed")
    if resolved.binding_id.value != current.binding_id.value or resolved._canonical_payload != current._canonical_payload:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "Python binding identity changed")
    return resolved


def consume_document_binding(
    repo_root: object,
    binding: object,
    *,
    repository_revision: RepositoryRevision,
) -> DocumentBinding:
    current = _validate_document_binding_record(binding)
    consumer = _require_consumer_revision(current.repository_revision, repository_revision)
    resolved = resolve_document_binding(
        repo_root,
        repository_revision=consumer,
        path=current.path,
        document_id=current.document_id,
        document_revision=current.document_revision,
        section_id=current.section_id,
        contract_version=current.contract_version,
        resolver_version=current.resolver_version,
    )
    if resolved.source_hash != current.source_hash:
        raise _fail(BindingFailureCode.SOURCE_HASH_MISMATCH, "document source hash changed")
    if resolved.binding_id.value != current.binding_id.value or resolved._canonical_payload != current._canonical_payload:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "document binding identity changed")
    return resolved


def consume_requirement_binding(
    repo_root: object,
    binding: object,
    *,
    repository_revision: RepositoryRevision,
) -> RequirementBinding:
    current = _validate_requirement_binding_record(binding)
    consumer = _require_consumer_revision(current.repository_revision, repository_revision)
    resolved = resolve_requirement_binding(
        repo_root,
        repository_revision=consumer,
        path=current.path,
        document_id=current.document_id,
        document_revision=current.document_revision,
        section_id=current.section_id,
        requirement_ids=current.requirement_ids,
        contract_version=current.contract_version,
        resolver_version=current.resolver_version,
    )
    if resolved.source_hash != current.source_hash:
        raise _fail(BindingFailureCode.SOURCE_HASH_MISMATCH, "requirement document source hash changed")
    if resolved.binding_id.value != current.binding_id.value or resolved._canonical_payload != current._canonical_payload:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "requirement binding identity changed")
    return resolved


_PYTHON_FIELDS = (
    "schema_version",
    "binding_kind",
    "repository_revision",
    "path",
    "contract_version",
    "resolver_version",
    "module",
    "qualname",
    "symbol_kind",
    "source_span_hash",
    "binding_id",
)
_DOCUMENT_FIELDS = (
    "schema_version",
    "binding_kind",
    "repository_revision",
    "path",
    "contract_version",
    "resolver_version",
    "document_id",
    "document_revision",
    "section_id",
    "source_hash",
    "binding_id",
)
_REQUIREMENT_FIELDS = (
    "schema_version",
    "binding_kind",
    "repository_revision",
    "path",
    "contract_version",
    "resolver_version",
    "document_id",
    "document_revision",
    "section_id",
    "requirement_ids",
    "source_hash",
    "binding_id",
)


def binding_from_dict(
    value: object,
    *,
    repo_root: object,
    consumer_revision: RepositoryRevision,
) -> Binding:
    if type(value) is not dict:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "binding transport must be an exact object")
    if value.get("schema_version") != BINDING_SCHEMA_V1 or type(value.get("schema_version")) is not str:
        raise _fail(BindingFailureCode.UNKNOWN_SCHEMA, "binding transport schema is unsupported")
    kind = _parse_enum(
        value.get("binding_kind"),
        BindingKind,
        BindingFailureCode.UNKNOWN_BINDING_KIND,
        "binding kind",
    )
    assert isinstance(kind, BindingKind)
    fields = {
        BindingKind.PYTHON: _PYTHON_FIELDS,
        BindingKind.DOCUMENT: _DOCUMENT_FIELDS,
        BindingKind.REQUIREMENT: _REQUIREMENT_FIELDS,
    }[kind]
    data = _exact_dict(value, fields, "binding")
    claimed_revision = _revision_from_dict(data["repository_revision"])
    consumer = _require_consumer_revision(claimed_revision, consumer_revision)
    claimed_id = BindingId.from_dict(data["binding_id"])
    contract_version = _require_contract_version(data["contract_version"])
    if kind is BindingKind.PYTHON:
        resolver_version = _require_resolver_version(data["resolver_version"], PYTHON_BINDING_RESOLVER_V1)
        symbol_kind = _parse_enum(
            data["symbol_kind"],
            PythonSymbolKind,
            BindingFailureCode.UNKNOWN_SYMBOL_KIND,
            "symbol kind",
        )
        assert isinstance(symbol_kind, PythonSymbolKind)
        result: Binding = resolve_python_binding(
            repo_root,
            repository_revision=consumer,
            path=data["path"],
            module=data["module"],
            qualname=data["qualname"],
            symbol_kind=symbol_kind,
            contract_version=contract_version,
            resolver_version=resolver_version,
        )
        if data["source_span_hash"] != result.source_span_hash or type(data["source_span_hash"]) is not str:
            raise _fail(BindingFailureCode.SOURCE_SPAN_HASH_MISMATCH, "transport source span hash changed")
    elif kind is BindingKind.DOCUMENT:
        resolver_version = _require_resolver_version(data["resolver_version"], DOCUMENT_BINDING_RESOLVER_V1)
        result = resolve_document_binding(
            repo_root,
            repository_revision=consumer,
            path=data["path"],
            document_id=data["document_id"],
            document_revision=data["document_revision"],
            section_id=data["section_id"],
            contract_version=contract_version,
            resolver_version=resolver_version,
        )
        if data["source_hash"] != result.source_hash or type(data["source_hash"]) is not str:
            raise _fail(BindingFailureCode.SOURCE_HASH_MISMATCH, "transport document source hash changed")
    else:
        resolver_version = _require_resolver_version(data["resolver_version"], DOCUMENT_BINDING_RESOLVER_V1)
        result = resolve_requirement_binding(
            repo_root,
            repository_revision=consumer,
            path=data["path"],
            document_id=data["document_id"],
            document_revision=data["document_revision"],
            section_id=data["section_id"],
            requirement_ids=_exact_list(data["requirement_ids"], "requirement_ids"),
            contract_version=contract_version,
            resolver_version=resolver_version,
        )
        if data["source_hash"] != result.source_hash or type(data["source_hash"]) is not str:
            raise _fail(BindingFailureCode.SOURCE_HASH_MISMATCH, "transport document source hash changed")
    if claimed_id.value != result.binding_id.value:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "transport binding ID does not match resolved payload")
    if result.to_dict() != data:
        raise _fail(BindingFailureCode.BINDING_ID_MISMATCH, "binding transport differs from exact resolved payload")
    return result


def binding_to_ref(binding: object) -> HashBoundRef:
    if type(binding) is PythonBinding:
        record: Binding = _validate_python_binding_record(binding)
    elif type(binding) is DocumentBinding:
        record = _validate_document_binding_record(binding)
    elif type(binding) is RequirementBinding:
        record = _validate_requirement_binding_record(binding)
    else:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "binding ref source must be an exact binding record")
    return HashBoundRef(
        kind=RefKind.BINDING,
        ref_id=record.binding_id.value,
        schema_id=BINDING_SCHEMA_V1,
        sha256=hashlib.sha256(record._canonical_payload).hexdigest(),
        byte_length=len(record._canonical_payload),
        media_type=BINDING_MEDIA_TYPE_V1,
    )


def binding_canonical_payload_bytes(binding: object) -> bytes:
    """Return a detached exact payload for vector review, not a trust shortcut."""

    if type(binding) is PythonBinding:
        record: Binding = _validate_python_binding_record(binding)
    elif type(binding) is DocumentBinding:
        record = _validate_document_binding_record(binding)
    elif type(binding) is RequirementBinding:
        record = _validate_requirement_binding_record(binding)
    else:
        raise _fail(BindingFailureCode.TYPE_MISMATCH, "canonical payload requires an exact binding")
    return bytes(record._canonical_payload)


__all__ = [
    "BINDING_CONTRACT_VERSION_V1",
    "BINDING_DOCUMENT_SCHEMA_V1",
    "BINDING_IDENTITY_PREFIX_V1",
    "BINDING_IDENTITY_PROFILE_V1",
    "BINDING_ID_TEXT_PREFIX_V1",
    "BINDING_MEDIA_TYPE_V1",
    "BINDING_SCHEMA_V1",
    "DOCUMENT_BINDING_RESOLVER_V1",
    "MAX_DOCUMENT_SOURCE_BYTES_V1",
    "MAX_PYTHON_AST_NODES_V1",
    "MAX_PYTHON_SCOPE_DEPTH_V1",
    "MAX_PYTHON_SOURCE_BYTES_V1",
    "PYTHON_BINDING_RESOLVER_V1",
    "BindingFailureCode",
    "BindingId",
    "BindingKind",
    "BindingViolation",
    "DocumentBinding",
    "PythonBinding",
    "PythonSymbolKind",
    "RequirementBinding",
    "RequirementStatus",
    "binding_canonical_payload_bytes",
    "binding_from_dict",
    "binding_to_ref",
    "consume_document_binding",
    "consume_python_binding",
    "consume_requirement_binding",
    "resolve_document_binding",
    "resolve_python_binding",
    "resolve_requirement_binding",
]
