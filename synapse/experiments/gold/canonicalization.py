"""Stage 4 Patch 2 canonical identity and compiler binding contracts.

This module is deliberately data-only.  It performs no I/O, resolution,
execution, replay, admission, or migration.  The public Behavior facade lives
in :mod:`behavior`; this module does not import it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

from synapse.ast import (
    AssignStmt,
    BinaryExpr,
    ExprStmt,
    IfStmt,
    LetStmt,
    ListExpr,
    Literal,
    Program,
    ReturnStmt,
    UnaryExpr,
    Variable,
    WhileStmt,
)
from synapse.bytecode import BytecodeProgram, CognitiveCompiler, Instruction
from synapse.canonical_values import (
    PROFILE_ID as _STABLE_PROFILE_ID,
    SAFE_INTEGER_MAX,
    SAFE_INTEGER_MIN,
    CanonicalSerializationError,
    canonical_bytes as _stable_canonical_bytes,
)
from synapse.version import LANGUAGE_VERSION

from .contracts import (
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_record_id,
    record_id_from_dict,
    validate_record_id,
)


BEHAVIOR_CORE_SCHEMA_V1 = "synapse.stage4.gold.behavior-core/v1"
BEHAVIOR_BLOB_SCHEMA_V1 = "synapse.stage4.gold.behavior-blob/v1"
STAGE4_CANONICAL_PROFILE_V1 = "synapse.stage4.gold.canonical-profile/v1"
STABLE_CANONICAL_CODEC_ID = "stable-canonical.v1"
CONTENT_KEY_PROTOCOL_V1 = "synapse.stage4.gold.content-key/v1"
CONTENT_KEY_TEXT_PREFIX = "synapse.stage4.gold.content-key/v1:"
CANONICAL_PROGRAM_IR_V1 = "synapse.stage4.gold.canonical-program-ir/v1"
COMPILER_ADAPTER_PROFILE_V1 = "synapse.stage4.gold.cognitive-compiler-adapter/v1"
MIGRATION_PROFILE_V1 = "synapse.stage4.gold.canonical-migration/v1"
MAX_INLINE_CANONICAL_PROGRAM_BYTES = 262_144

COGNITIVE_COMPILER_ID = "synapse.cognitive-compiler/cvm-v2.2"
CVM_COMPILER_TARGET = "cvm-v2.2"
CVM_BYTECODE_VERSION = "2.2"
CVM_HOST_ABI_VERSION = "2.2"

_MAX_CANONICAL_DEPTH = 128
_MAX_REF_BYTES = 2**53 - 1
_TRUSTED_SEAL = object()
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_REF_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSIONED_RE = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z"
)
_MEDIA_TYPE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}\Z"
)


class CanonicalizationFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_CANONICAL_VALUE = "INVALID_CANONICAL_VALUE"
    INVALID_UTF8 = "INVALID_UTF8"
    NON_CANONICAL_BYTES = "NON_CANONICAL_BYTES"
    INVALID_BASE64URL = "INVALID_BASE64URL"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    UNKNOWN_CODEC = "UNKNOWN_CODEC"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_LANGUAGE = "UNKNOWN_LANGUAGE"
    UNKNOWN_COMPILER = "UNKNOWN_COMPILER"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_HASH_REF = "MALFORMED_HASH_REF"
    DUPLICATE_REF = "DUPLICATE_REF"
    UNSUPPORTED_IR_NODE = "UNSUPPORTED_IR_NODE"
    IR_TOO_LARGE = "IR_TOO_LARGE"
    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
    CONTENT_KEY_MISMATCH = "CONTENT_KEY_MISMATCH"
    PROGRAM_MISMATCH = "PROGRAM_MISMATCH"
    PROGRAM_ARTIFACT_UNAVAILABLE = "PROGRAM_ARTIFACT_UNAVAILABLE"
    FORBIDDEN_OPCODE = "FORBIDDEN_OPCODE"
    COMPILER_OUTPUT_MISMATCH = "COMPILER_OUTPUT_MISMATCH"
    COMPILER_BINDING_MISMATCH = "COMPILER_BINDING_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    CONTENT_COLLISION_OR_CORRUPTION = "CONTENT_COLLISION_OR_CORRUPTION"
    DEGRADED_CONTENT = "DEGRADED_CONTENT"
    MIGRATION_ENDPOINT_NOT_APPROVED = "MIGRATION_ENDPOINT_NOT_APPROVED"
    MIGRATION_INVARIANT = "MIGRATION_INVARIANT"


class CanonicalizationViolation(ValueError):
    """Typed, fail-closed Patch 2 canonicalization error."""

    def __init__(self, failure_code: CanonicalizationFailureCode, detail: str) -> None:
        if type(failure_code) is not CanonicalizationFailureCode:
            raise TypeError("failure_code must be an exact CanonicalizationFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: CanonicalizationFailureCode, detail: str) -> CanonicalizationViolation:
    return CanonicalizationViolation(code, detail)


def _exact_dict(value: object, required: tuple[str, ...], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, f"{name} must be an exact dict")
    if any(type(key) is not str for key in value):
        raise _fail(CanonicalizationFailureCode.UNKNOWN_FIELD, f"{name} keys must be exact strings")
    missing = [field for field in required if field not in value]
    if missing:
        raise _fail(
            CanonicalizationFailureCode.MISSING_REQUIRED_FIELD,
            f"{name} is missing field {missing[0]}",
        )
    unknown = sorted(field for field in value if field not in required)
    if unknown:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_FIELD, f"{name} has unknown field {unknown[0]}")
    return value


def _exact_list(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, f"{name} must be an exact list")
    return value


def _text(value: object, name: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, f"{name} must be an exact string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _fail(CanonicalizationFailureCode.INVALID_UTF8, f"{name} contains a lone surrogate")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(CanonicalizationFailureCode.INVALID_UTF8, f"{name} is not valid UTF-8 text") from exc
    return value


def _sha256(value: object, name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(CanonicalizationFailureCode.MALFORMED_HASH_REF, f"{name} must be lowercase SHA-256")
    return value


def _versioned(value: object, name: str) -> str:
    text = _text(value, name)
    if _VERSIONED_RE.fullmatch(text) is None:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, f"{name} must be versioned")
    return text


def _enum(value: object, enum_type: type[Enum], code: CanonicalizationFailureCode, name: str) -> Enum:
    if type(value) is not str:
        raise _fail(code, f"{name} must be an exact known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail(code, f"{name} is unknown") from exc


def _require_known_endpoint(
    *,
    profile_id: object,
    codec_id: object,
    core_schema_id: object,
    ir_schema_id: object,
    language_version: object,
    compiler_adapter_profile: object,
) -> None:
    if profile_id != STAGE4_CANONICAL_PROFILE_V1 or type(profile_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_PROFILE, "canonical profile is unknown")
    if codec_id != STABLE_CANONICAL_CODEC_ID or type(codec_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_CODEC, "canonical codec is unknown")
    if core_schema_id != BEHAVIOR_CORE_SCHEMA_V1 or type(core_schema_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "behavior core schema is unknown")
    if ir_schema_id != CANONICAL_PROGRAM_IR_V1 or type(ir_schema_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "program IR schema is unknown")
    if language_version != LANGUAGE_VERSION or type(language_version) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_LANGUAGE, "language version is unknown")
    if compiler_adapter_profile != COMPILER_ADAPTER_PROFILE_V1 or type(compiler_adapter_profile) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_COMPILER, "compiler adapter profile is unknown")
    if _STABLE_PROFILE_ID != STABLE_CANONICAL_CODEC_ID:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_CODEC, "repository canonical codec drifted")


class RefKind(str, Enum):
    SOURCE_EVIDENCE = "source_evidence"
    ARTIFACT = "artifact"
    PROGRAM_ARTIFACT = "program_artifact"
    CONTRACT_CONDITION = "contract_condition"
    BINDING = "binding"
    ABSENCE_PROVENANCE = "absence_provenance"


@dataclass(frozen=True)
class HashBoundRef:
    kind: RefKind
    ref_id: str
    schema_id: str
    sha256: str
    byte_length: int
    media_type: str

    def __post_init__(self) -> None:
        _validate_hash_bound_ref(self)

    def to_dict(self) -> dict[str, object]:
        _validate_hash_bound_ref(self)
        return {
            "kind": self.kind.value,
            "ref_id": self.ref_id,
            "schema_id": self.schema_id,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, value: object) -> HashBoundRef:
        data = _exact_dict(
            value,
            ("kind", "ref_id", "schema_id", "sha256", "byte_length", "media_type"),
            "hash_bound_ref",
        )
        kind = _enum(data["kind"], RefKind, CanonicalizationFailureCode.MALFORMED_HASH_REF, "ref.kind")
        assert isinstance(kind, RefKind)
        return cls(
            kind=kind,
            ref_id=data["ref_id"],
            schema_id=data["schema_id"],
            sha256=data["sha256"],
            byte_length=data["byte_length"],
            media_type=data["media_type"],
        )


def _validate_hash_bound_ref(value: HashBoundRef, expected_kind: RefKind | None = None) -> None:
    if type(value) is not HashBoundRef:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "reference must be exact HashBoundRef")
    if type(value.kind) is not RefKind or (expected_kind is not None and value.kind is not expected_kind):
        raise _fail(CanonicalizationFailureCode.MALFORMED_HASH_REF, "reference kind is invalid")
    if type(value.ref_id) is not str or _REF_ID_RE.fullmatch(value.ref_id) is None:
        raise _fail(CanonicalizationFailureCode.MALFORMED_HASH_REF, "reference id is invalid")
    _versioned(value.schema_id, "ref.schema_id")
    _sha256(value.sha256, "ref.sha256")
    if type(value.byte_length) is not int or not (0 <= value.byte_length <= _MAX_REF_BYTES):
        raise _fail(CanonicalizationFailureCode.MALFORMED_HASH_REF, "reference byte length is invalid")
    if type(value.media_type) is not str or _MEDIA_TYPE_RE.fullmatch(value.media_type) is None:
        raise _fail(CanonicalizationFailureCode.MALFORMED_HASH_REF, "reference media type is invalid")


def validate_ref_collection(
    value: object,
    *,
    expected_kind: RefKind,
    field_name: str,
) -> tuple[HashBoundRef, ...]:
    if type(value) not in (tuple, list):
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, f"{field_name} must be a list or tuple")
    refs: list[HashBoundRef] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for item in value:
        ref = item if type(item) is HashBoundRef else HashBoundRef.from_dict(item)
        _validate_hash_bound_ref(ref, expected_kind)
        if ref.ref_id in seen_ids or ref.sha256 in seen_hashes:
            raise _fail(CanonicalizationFailureCode.DUPLICATE_REF, f"{field_name} contains duplicate reference")
        seen_ids.add(ref.ref_id)
        seen_hashes.add(ref.sha256)
        refs.append(ref)
    refs.sort(key=lambda ref: ref.ref_id)
    return tuple(refs)


def _validate_data_only(value: object, *, path: str = "$", depth: int = 0) -> None:
    if depth > _MAX_CANONICAL_DEPTH:
        raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, "canonical payload is too deep")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _text(value, path, nonempty=False)
        return
    if type(value) is int:
        if not (SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX):
            raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, f"unsafe integer at {path}")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, f"non-finite float at {path}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_data_only(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, f"non-string key at {path}")
            _text(key, f"{path}.<key>", nonempty=False)
            _validate_data_only(item, path=f"{path}.{key}", depth=depth + 1)
        return
    raise _fail(
        CanonicalizationFailureCode.INVALID_CANONICAL_VALUE,
        f"unsupported canonical type at {path}: {type(value).__name__}",
    )


def canonicalize_stage4_payload(
    value: object,
    *,
    profile_id: str,
    codec_id: str,
) -> bytes:
    if profile_id != STAGE4_CANONICAL_PROFILE_V1 or type(profile_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_PROFILE, "canonical profile is unknown")
    if codec_id != STABLE_CANONICAL_CODEC_ID or type(codec_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_CODEC, "canonical codec is unknown")
    _validate_data_only(value)
    try:
        encoded = _stable_canonical_bytes(value)
    except CanonicalSerializationError as exc:
        raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, "stable codec rejected payload") from exc
    if encoded.profile != STABLE_CANONICAL_CODEC_ID or type(encoded.data) is not bytes:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_CODEC, "stable codec returned an incompatible result")
    return encoded.data


def _reject_json_constant(value: str) -> None:
    raise _fail(CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, f"JSON constant {value} is forbidden")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail(CanonicalizationFailureCode.NON_CANONICAL_BYTES, "duplicate JSON object key")
        result[key] = value
    return result


def decode_stage4_canonical_bytes(
    value: object,
    *,
    profile_id: str,
    codec_id: str,
) -> object:
    if type(value) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "canonical bytes must be exact bytes")
    if profile_id != STAGE4_CANONICAL_PROFILE_V1 or type(profile_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_PROFILE, "canonical profile is unknown")
    if codec_id != STABLE_CANONICAL_CODEC_ID or type(codec_id) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_CODEC, "canonical codec is unknown")
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail(CanonicalizationFailureCode.INVALID_UTF8, "canonical bytes are not UTF-8") from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except CanonicalizationViolation:
        raise
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise _fail(CanonicalizationFailureCode.NON_CANONICAL_BYTES, "canonical JSON is malformed") from exc
    _validate_data_only(decoded)
    if canonicalize_stage4_payload(decoded, profile_id=profile_id, codec_id=codec_id) != value:
        raise _fail(CanonicalizationFailureCode.NON_CANONICAL_BYTES, "bytes are not exact canonical encoding")
    return decoded


def canonical_base64url(value: object) -> str:
    if type(value) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "base64url input must be exact bytes")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_canonical_base64url(value: object) -> bytes:
    if type(value) is not str or not value or "=" in value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise _fail(CanonicalizationFailureCode.INVALID_BASE64URL, "base64url must be unpadded URL-safe text")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise _fail(CanonicalizationFailureCode.INVALID_BASE64URL, "base64url text is malformed") from exc
    if canonical_base64url(decoded) != value:
        raise _fail(CanonicalizationFailureCode.INVALID_BASE64URL, "base64url text is not canonical")
    return decoded


_BINARY_OPERATORS = {
    "ADD": "+",
    "SUB": "-",
    "MUL": "*",
    "DIV": "/",
    "MOD": "%",
    "EQ": "==",
    "NEQ": "!=",
    "LT": "<",
    "GT": ">",
    "LTE": "<=",
    "GTE": ">=",
    "AND": "and",
    "OR": "or",
}
_UNARY_OPERATORS = {"NOT": "not", "NEGATE": "-"}


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise _fail(CanonicalizationFailureCode.MALFORMED_IDENTIFIER, f"{name} is not an IR identifier")
    return value


def _ir_fields(value: object, required: tuple[str, ...], name: str) -> dict[str, Any]:
    return _exact_dict(value, required, name)


def _validate_ir_expression(value: object, *, depth: int) -> dict[str, Any]:
    if depth > _MAX_CANONICAL_DEPTH:
        raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "IR nesting is too deep")
    if type(value) is not dict or type(value.get("node")) is not str:
        raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "IR expression must have an exact node tag")
    node = value["node"]
    if node == "literal":
        data = _ir_fields(value, ("node", "value_kind", "value"), "literal")
        kind = data["value_kind"]
        literal = data["value"]
        if kind == "NONE":
            valid = literal is None
        elif kind == "BOOL":
            valid = type(literal) is bool
        elif kind == "INT":
            valid = type(literal) is int and SAFE_INTEGER_MIN <= literal <= SAFE_INTEGER_MAX
        elif kind == "FLOAT":
            valid = type(literal) is float and math.isfinite(literal)
        else:
            raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "literal value_kind is unknown")
        if not valid:
            raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "literal value does not match value_kind")
        return {"node": "literal", "value_kind": kind, "value": literal}
    if node == "variable":
        data = _ir_fields(value, ("node", "name"), "variable")
        return {"node": "variable", "name": _identifier(data["name"], "variable.name")}
    if node == "binary":
        data = _ir_fields(value, ("node", "operator", "left", "right"), "binary")
        if type(data["operator"]) is not str or data["operator"] not in _BINARY_OPERATORS:
            raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "binary operator is unknown")
        return {
            "node": "binary",
            "operator": data["operator"],
            "left": _validate_ir_expression(data["left"], depth=depth + 1),
            "right": _validate_ir_expression(data["right"], depth=depth + 1),
        }
    if node == "unary":
        data = _ir_fields(value, ("node", "operator", "operand"), "unary")
        if type(data["operator"]) is not str or data["operator"] not in _UNARY_OPERATORS:
            raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "unary operator is unknown")
        return {
            "node": "unary",
            "operator": data["operator"],
            "operand": _validate_ir_expression(data["operand"], depth=depth + 1),
        }
    if node == "list":
        data = _ir_fields(value, ("node", "elements"), "list")
        elements = _exact_list(data["elements"], "list.elements")
        return {
            "node": "list",
            "elements": [_validate_ir_expression(item, depth=depth + 1) for item in elements],
        }
    raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, f"unsupported IR expression {node}")


def _validate_ir_statement(value: object, *, depth: int) -> dict[str, Any]:
    if depth > _MAX_CANONICAL_DEPTH or type(value) is not dict or type(value.get("node")) is not str:
        raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "IR statement must have an exact node tag")
    node = value["node"]
    if node == "let":
        data = _ir_fields(value, ("node", "name", "value"), "let")
        return {"node": "let", "name": _identifier(data["name"], "let.name"), "value": _validate_ir_expression(data["value"], depth=depth + 1)}
    if node == "assign":
        data = _ir_fields(value, ("node", "target", "value"), "assign")
        return {"node": "assign", "target": _identifier(data["target"], "assign.target"), "value": _validate_ir_expression(data["value"], depth=depth + 1)}
    if node == "if":
        data = _ir_fields(value, ("node", "condition", "then_body", "else_body"), "if")
        then_body = _exact_list(data["then_body"], "if.then_body")
        else_body = _exact_list(data["else_body"], "if.else_body")
        return {
            "node": "if",
            "condition": _validate_ir_expression(data["condition"], depth=depth + 1),
            "then_body": [_validate_ir_statement(item, depth=depth + 1) for item in then_body],
            "else_body": [_validate_ir_statement(item, depth=depth + 1) for item in else_body],
        }
    if node == "while":
        data = _ir_fields(value, ("node", "condition", "body"), "while")
        body = _exact_list(data["body"], "while.body")
        return {
            "node": "while",
            "condition": _validate_ir_expression(data["condition"], depth=depth + 1),
            "body": [_validate_ir_statement(item, depth=depth + 1) for item in body],
        }
    if node == "return":
        data = _ir_fields(value, ("node", "value"), "return")
        return {"node": "return", "value": _validate_ir_expression(data["value"], depth=depth + 1)}
    if node == "expr_stmt":
        data = _ir_fields(value, ("node", "expression"), "expr_stmt")
        return {"node": "expr_stmt", "expression": _validate_ir_expression(data["expression"], depth=depth + 1)}
    raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, f"unsupported IR statement {node}")


def validate_canonical_program_ir(value: object) -> dict[str, Any]:
    root = _ir_fields(value, ("schema_version", "program"), "canonical_program_ir")
    if root["schema_version"] != CANONICAL_PROGRAM_IR_V1 or type(root["schema_version"]) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "canonical program IR schema is unknown")
    program = _ir_fields(root["program"], ("node", "statements"), "program")
    if program["node"] != "program" or type(program["node"]) is not str:
        raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, "IR root program node is invalid")
    statements = _exact_list(program["statements"], "program.statements")
    return {
        "schema_version": CANONICAL_PROGRAM_IR_V1,
        "program": {
            "node": "program",
            "statements": [_validate_ir_statement(item, depth=1) for item in statements],
        },
    }


def canonical_program_ir_bytes(value: object) -> bytes:
    validated = validate_canonical_program_ir(value)
    encoded = canonicalize_stage4_payload(
        validated,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    if len(encoded) > MAX_INLINE_CANONICAL_PROGRAM_BYTES:
        raise _fail(CanonicalizationFailureCode.IR_TOO_LARGE, "canonical program IR exceeds inline limit")
    return encoded


def decode_canonical_program_ir(value: object) -> dict[str, Any]:
    if type(value) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "program IR bytes must be exact bytes")
    if len(value) > MAX_INLINE_CANONICAL_PROGRAM_BYTES:
        raise _fail(CanonicalizationFailureCode.IR_TOO_LARGE, "canonical program IR exceeds inline limit")
    decoded = decode_stage4_canonical_bytes(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    validated = validate_canonical_program_ir(decoded)
    if canonical_program_ir_bytes(validated) != value:
        raise _fail(CanonicalizationFailureCode.NON_CANONICAL_BYTES, "program IR bytes are not canonical")
    return validated


class ProgramForm(str, Enum):
    INLINE_IR_V1 = "INLINE_IR_V1"
    ARTIFACT_REF_V1 = "ARTIFACT_REF_V1"


def _normalize_canonical_program(value: object) -> tuple[dict[str, Any], bytes | None, HashBoundRef | None]:
    if type(value) is not dict or type(value.get("form")) is not str:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "canonical_program must be an exact tagged dict")
    if value["form"] == ProgramForm.INLINE_IR_V1.value:
        data = _exact_dict(value, ("form", "ir"), "canonical_program.inline")
        ir = validate_canonical_program_ir(data["ir"])
        ir_bytes = canonical_program_ir_bytes(ir)
        return {"form": ProgramForm.INLINE_IR_V1.value, "ir": ir}, ir_bytes, None
    if value["form"] == ProgramForm.ARTIFACT_REF_V1.value:
        data = _exact_dict(value, ("form", "artifact_ref"), "canonical_program.artifact")
        ref = data["artifact_ref"] if type(data["artifact_ref"]) is HashBoundRef else HashBoundRef.from_dict(data["artifact_ref"])
        _validate_hash_bound_ref(ref, RefKind.PROGRAM_ARTIFACT)
        if ref.schema_id != CANONICAL_PROGRAM_IR_V1:
            raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "program artifact schema must be canonical IR v1")
        return {"form": ProgramForm.ARTIFACT_REF_V1.value, "artifact_ref": ref.to_dict()}, None, ref
    raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "canonical program form is unknown")


@dataclass(frozen=True, init=False)
class ContentKey:
    digest_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ContentKey:
        raise TypeError("ContentKey is created only by the versioned content identity factory")

    @property
    def value(self) -> str:
        _validate_content_key_shape(self)
        return CONTENT_KEY_TEXT_PREFIX + self.digest_sha256

    def to_dict(self) -> dict[str, str]:
        _validate_content_key_shape(self)
        return {"value": self.value}


@dataclass(frozen=True)
class ClaimedContentKey:
    value: str

    def __post_init__(self) -> None:
        _parse_content_key_text(self.value)

    def to_dict(self) -> dict[str, str]:
        _parse_content_key_text(self.value)
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> ClaimedContentKey:
        data = _exact_dict(value, ("value",), "claimed_content_key")
        return cls(data["value"])


def _parse_content_key_text(value: object) -> str:
    if type(value) is not str or not value.startswith(CONTENT_KEY_TEXT_PREFIX):
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "content key prefix is invalid")
    digest = value[len(CONTENT_KEY_TEXT_PREFIX) :]
    if _SHA256_RE.fullmatch(digest) is None:
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "content key digest is invalid")
    return digest


def _make_content_key(digest: str) -> ContentKey:
    if _SHA256_RE.fullmatch(digest) is None:
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "computed content digest is invalid")
    key = object.__new__(ContentKey)
    object.__setattr__(key, "digest_sha256", digest)
    object.__setattr__(key, "_trusted_seal", _TRUSTED_SEAL)
    _validate_content_key_shape(key)
    return key


def _validate_content_key_shape(value: ContentKey) -> None:
    if type(value) is not ContentKey:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "content key must be exact ContentKey")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content key is not factory sealed")
    if type(value.digest_sha256) is not str or _SHA256_RE.fullmatch(value.digest_sha256) is None:
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "content key digest is malformed")


def _frame(value: bytes) -> bytes:
    if type(value) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "content identity frame must be exact bytes")
    return len(value).to_bytes(8, "big", signed=False) + value


def content_key_preimage(
    *,
    canonical_behavior_core_bytes: bytes,
    profile_id: str,
    codec_id: str,
    core_schema_id: str,
    ir_schema_id: str,
    language_version: str,
    compiler_adapter_profile: str,
) -> bytes:
    _require_known_endpoint(
        profile_id=profile_id,
        codec_id=codec_id,
        core_schema_id=core_schema_id,
        ir_schema_id=ir_schema_id,
        language_version=language_version,
        compiler_adapter_profile=compiler_adapter_profile,
    )
    if type(canonical_behavior_core_bytes) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "canonical core bytes must be exact bytes")
    return (
        CONTENT_KEY_PROTOCOL_V1.encode("utf-8")
        + b"\x00"
        + _frame(profile_id.encode("utf-8"))
        + _frame(codec_id.encode("utf-8"))
        + _frame(core_schema_id.encode("utf-8"))
        + _frame(ir_schema_id.encode("utf-8"))
        + _frame(language_version.encode("utf-8"))
        + _frame(compiler_adapter_profile.encode("utf-8"))
        + _frame(canonical_behavior_core_bytes)
    )


def _content_digest(preimage: bytes) -> str:
    """Private fixed SHA-256 primitive; tests may monkeypatch only to simulate a collision."""

    if type(preimage) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "content preimage must be exact bytes")
    return hashlib.sha256(preimage).hexdigest()


def compute_content_key(
    *,
    canonical_behavior_core_bytes: bytes,
    profile_id: str,
    codec_id: str,
    core_schema_id: str,
    ir_schema_id: str,
    language_version: str,
    compiler_adapter_profile: str,
) -> ContentKey:
    preimage = content_key_preimage(
        canonical_behavior_core_bytes=canonical_behavior_core_bytes,
        profile_id=profile_id,
        codec_id=codec_id,
        core_schema_id=core_schema_id,
        ir_schema_id=ir_schema_id,
        language_version=language_version,
        compiler_adapter_profile=compiler_adapter_profile,
    )
    return _make_content_key(_content_digest(preimage))


def content_key_from_dict(
    value: object,
    *,
    canonical_behavior_core_bytes: bytes,
    profile_id: str,
    codec_id: str,
    core_schema_id: str,
    ir_schema_id: str,
    language_version: str,
    compiler_adapter_profile: str,
) -> ContentKey:
    data = _exact_dict(value, ("value",), "content_key")
    supplied_digest = _parse_content_key_text(data["value"])
    expected = compute_content_key(
        canonical_behavior_core_bytes=canonical_behavior_core_bytes,
        profile_id=profile_id,
        codec_id=codec_id,
        core_schema_id=core_schema_id,
        ir_schema_id=ir_schema_id,
        language_version=language_version,
        compiler_adapter_profile=compiler_adapter_profile,
    )
    if supplied_digest != expected.digest_sha256:
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "content key does not match canonical core bytes")
    return expected


_CORE_FIELDS = (
    "schema_version",
    "behavior_kind",
    "language_version",
    "canonical_program",
    "input_contract",
    "output_contract",
    "capability_requirements",
    "replay_contract",
    "verification_contract",
    "binding_refs",
    "source_evidence_refs",
    "artifact_refs",
)


@dataclass(frozen=True, init=False)
class CanonicalBehaviorCore:
    canonical_bytes: bytes
    program_form: ProgramForm
    inline_program_ir_bytes: bytes | None
    program_artifact_ref: HashBoundRef | None
    payload_sha256: str
    content_key: ContentKey
    profile_id: str
    codec_id: str
    core_schema_id: str
    ir_schema_id: str
    language_version: str
    compiler_adapter_profile: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CanonicalBehaviorCore:
        raise TypeError("CanonicalBehaviorCore is created only from a validated Behavior Core payload")


def _make_canonical_behavior_core(value: object) -> CanonicalBehaviorCore:
    data = _exact_dict(value, _CORE_FIELDS, "behavior_core")
    if data["schema_version"] != BEHAVIOR_CORE_SCHEMA_V1 or type(data["schema_version"]) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "behavior core schema is unknown")
    if data["language_version"] != LANGUAGE_VERSION or type(data["language_version"]) is not str:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_LANGUAGE, "behavior language version is unknown")
    normalized_program, ir_bytes, artifact_ref = _normalize_canonical_program(data["canonical_program"])
    normalized = dict(data)
    normalized["canonical_program"] = normalized_program
    _validate_data_only(normalized)
    core_bytes = canonicalize_stage4_payload(
        normalized,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    key = compute_content_key(
        canonical_behavior_core_bytes=core_bytes,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
        core_schema_id=BEHAVIOR_CORE_SCHEMA_V1,
        ir_schema_id=CANONICAL_PROGRAM_IR_V1,
        language_version=LANGUAGE_VERSION,
        compiler_adapter_profile=COMPILER_ADAPTER_PROFILE_V1,
    )
    bundle = object.__new__(CanonicalBehaviorCore)
    object.__setattr__(bundle, "canonical_bytes", core_bytes)
    object.__setattr__(bundle, "program_form", ProgramForm(normalized_program["form"]))
    object.__setattr__(bundle, "inline_program_ir_bytes", ir_bytes)
    object.__setattr__(bundle, "program_artifact_ref", artifact_ref)
    object.__setattr__(bundle, "payload_sha256", hashlib.sha256(core_bytes).hexdigest())
    object.__setattr__(bundle, "content_key", key)
    object.__setattr__(bundle, "profile_id", STAGE4_CANONICAL_PROFILE_V1)
    object.__setattr__(bundle, "codec_id", STABLE_CANONICAL_CODEC_ID)
    object.__setattr__(bundle, "core_schema_id", BEHAVIOR_CORE_SCHEMA_V1)
    object.__setattr__(bundle, "ir_schema_id", CANONICAL_PROGRAM_IR_V1)
    object.__setattr__(bundle, "language_version", LANGUAGE_VERSION)
    object.__setattr__(bundle, "compiler_adapter_profile", COMPILER_ADAPTER_PROFILE_V1)
    object.__setattr__(bundle, "_trusted_seal", _TRUSTED_SEAL)
    validate_canonical_behavior_core(bundle)
    return bundle


def validate_canonical_behavior_core(value: CanonicalBehaviorCore) -> dict[str, Any]:
    if type(value) is not CanonicalBehaviorCore:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "canonical core must be exact CanonicalBehaviorCore")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "canonical core is not factory sealed")
    _require_known_endpoint(
        profile_id=value.profile_id,
        codec_id=value.codec_id,
        core_schema_id=value.core_schema_id,
        ir_schema_id=value.ir_schema_id,
        language_version=value.language_version,
        compiler_adapter_profile=value.compiler_adapter_profile,
    )
    if type(value.canonical_bytes) is not bytes:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "canonical core bytes must be exact bytes")
    decoded = decode_stage4_canonical_bytes(
        value.canonical_bytes,
        profile_id=value.profile_id,
        codec_id=value.codec_id,
    )
    core = _exact_dict(decoded, _CORE_FIELDS, "behavior_core")
    if core["schema_version"] != value.core_schema_id or core["language_version"] != value.language_version:
        raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "core endpoint fields do not match bundle")
    normalized_program, ir_bytes, artifact_ref = _normalize_canonical_program(core["canonical_program"])
    if normalized_program != core["canonical_program"]:
        raise _fail(CanonicalizationFailureCode.NON_CANONICAL_BYTES, "embedded program is not canonical")
    if type(value.program_form) is not ProgramForm or value.program_form.value != normalized_program["form"]:
        raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "program form does not match core bytes")
    if value.program_form is ProgramForm.INLINE_IR_V1:
        if type(value.inline_program_ir_bytes) is not bytes or value.inline_program_ir_bytes != ir_bytes:
            raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "inline program bytes do not match core")
        if value.program_artifact_ref is not None:
            raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "inline program cannot carry artifact ref")
    else:
        if value.inline_program_ir_bytes is not None:
            raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "artifact program cannot carry inline bytes")
        _validate_hash_bound_ref(value.program_artifact_ref, RefKind.PROGRAM_ARTIFACT)  # type: ignore[arg-type]
        if value.program_artifact_ref != artifact_ref:
            raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "program artifact ref does not match core")
    expected_hash = hashlib.sha256(value.canonical_bytes).hexdigest()
    if type(value.payload_sha256) is not str or value.payload_sha256 != expected_hash:
        raise _fail(CanonicalizationFailureCode.PAYLOAD_HASH_MISMATCH, "canonical core payload hash mismatch")
    _validate_content_key_shape(value.content_key)
    expected_key = compute_content_key(
        canonical_behavior_core_bytes=value.canonical_bytes,
        profile_id=value.profile_id,
        codec_id=value.codec_id,
        core_schema_id=value.core_schema_id,
        ir_schema_id=value.ir_schema_id,
        language_version=value.language_version,
        compiler_adapter_profile=value.compiler_adapter_profile,
    )
    if value.content_key.value != expected_key.value:
        raise _fail(CanonicalizationFailureCode.CONTENT_KEY_MISMATCH, "canonical core key mismatch")
    return core


class ContentValidationStatus(str, Enum):
    USABLE = "USABLE"
    QUARANTINED = "QUARANTINED"
    INCOMPATIBLE = "INCOMPATIBLE"


class ContentValidationReason(str, Enum):
    MATCHING_CONTENT = "MATCHING_CONTENT"
    DISTINCT_CONTENT = "DISTINCT_CONTENT"
    CONTENT_COLLISION_OR_CORRUPTION = "CONTENT_COLLISION_OR_CORRUPTION"
    UNSUPPORTED_ENDPOINT = "UNSUPPORTED_ENDPOINT"


_ALLOWED_CONTENT_RESULTS = frozenset(
    {
        (ContentValidationStatus.USABLE, ContentValidationReason.MATCHING_CONTENT),
        (ContentValidationStatus.USABLE, ContentValidationReason.DISTINCT_CONTENT),
        (
            ContentValidationStatus.QUARANTINED,
            ContentValidationReason.CONTENT_COLLISION_OR_CORRUPTION,
        ),
        (ContentValidationStatus.INCOMPATIBLE, ContentValidationReason.UNSUPPORTED_ENDPOINT),
    }
)


@dataclass(frozen=True, init=False)
class ContentValidationResult:
    status: ContentValidationStatus
    reason: ContentValidationReason
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ContentValidationResult:
        raise TypeError("ContentValidationResult is created only by content comparison")

    @property
    def consumable(self) -> bool:
        _validate_content_validation_result(self)
        return self.status is ContentValidationStatus.USABLE

    def require_consumable(self) -> None:
        _validate_content_validation_result(self)
        if not self.consumable:
            raise _fail(CanonicalizationFailureCode.DEGRADED_CONTENT, f"content is {self.status.value}")


def _make_content_validation_result(
    status: ContentValidationStatus,
    reason: ContentValidationReason,
) -> ContentValidationResult:
    if type(status) is not ContentValidationStatus or type(reason) is not ContentValidationReason:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content result enums are invalid")
    if (status, reason) not in _ALLOWED_CONTENT_RESULTS:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content result status/reason pair is invalid")
    result = object.__new__(ContentValidationResult)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "reason", reason)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    _validate_content_validation_result(result)
    return result


def _validate_content_validation_result(value: ContentValidationResult) -> None:
    if type(value) is not ContentValidationResult:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "content result must be exact typed value")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content result is not factory sealed")
    if type(value.status) is not ContentValidationStatus or type(value.reason) is not ContentValidationReason:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content result enums are invalid")
    if (value.status, value.reason) not in _ALLOWED_CONTENT_RESULTS:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "content result status/reason pair is invalid")


def compare_canonical_content(
    primary: CanonicalBehaviorCore,
    candidate: CanonicalBehaviorCore,
) -> ContentValidationResult:
    try:
        validate_canonical_behavior_core(primary)
        validate_canonical_behavior_core(candidate)
    except CanonicalizationViolation as exc:
        if exc.failure_code in {
            CanonicalizationFailureCode.UNKNOWN_PROFILE,
            CanonicalizationFailureCode.UNKNOWN_CODEC,
            CanonicalizationFailureCode.UNKNOWN_SCHEMA,
            CanonicalizationFailureCode.UNKNOWN_LANGUAGE,
            CanonicalizationFailureCode.UNKNOWN_COMPILER,
        }:
            return _make_content_validation_result(
                ContentValidationStatus.INCOMPATIBLE,
                ContentValidationReason.UNSUPPORTED_ENDPOINT,
            )
        raise
    if primary.content_key.value == candidate.content_key.value:
        if primary.canonical_bytes != candidate.canonical_bytes or primary.payload_sha256 != candidate.payload_sha256:
            return _make_content_validation_result(
                ContentValidationStatus.QUARANTINED,
                ContentValidationReason.CONTENT_COLLISION_OR_CORRUPTION,
            )
        return _make_content_validation_result(
            ContentValidationStatus.USABLE,
            ContentValidationReason.MATCHING_CONTENT,
        )
    return _make_content_validation_result(
        ContentValidationStatus.USABLE,
        ContentValidationReason.DISTINCT_CONTENT,
    )


def _ast_expression(value: dict[str, Any]) -> object:
    node = value["node"]
    if node == "literal":
        return Literal(value=value["value"])
    if node == "variable":
        return Variable(name=value["name"])
    if node == "binary":
        return BinaryExpr(
            left=_ast_expression(value["left"]),
            op=_BINARY_OPERATORS[value["operator"]],
            right=_ast_expression(value["right"]),
        )
    if node == "unary":
        return UnaryExpr(op=_UNARY_OPERATORS[value["operator"]], operand=_ast_expression(value["operand"]))
    if node == "list":
        return ListExpr(elements=[_ast_expression(item) for item in value["elements"]])
    raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, f"unsupported IR expression {node}")


def _ast_statement(value: dict[str, Any]) -> object:
    node = value["node"]
    if node == "let":
        return LetStmt(name=value["name"], value=_ast_expression(value["value"]))
    if node == "assign":
        return AssignStmt(target=value["target"], value=_ast_expression(value["value"]))
    if node == "if":
        return IfStmt(
            condition=_ast_expression(value["condition"]),
            then_body=[_ast_statement(item) for item in value["then_body"]],
            else_body=[_ast_statement(item) for item in value["else_body"]],
        )
    if node == "while":
        return WhileStmt(
            condition=_ast_expression(value["condition"]),
            body=[_ast_statement(item) for item in value["body"]],
        )
    if node == "return":
        return ReturnStmt(value=_ast_expression(value["value"]))
    if node == "expr_stmt":
        return ExprStmt(expr=_ast_expression(value["expression"]))
    raise _fail(CanonicalizationFailureCode.UNSUPPORTED_IR_NODE, f"unsupported IR statement {node}")


def _ast_program(value: dict[str, Any]) -> Program:
    validated = validate_canonical_program_ir(value)
    return Program(statements=[_ast_statement(item) for item in validated["program"]["statements"]])


_ALLOWED_STAGE4_OPCODES = frozenset(
    {
        "LOAD_CONST",
        "LOAD_NAME",
        "STORE",
        "POP",
        "DUP",
        "JUMP",
        "JUMP_IF_FALSE",
        "JUMP_IF_TRUE",
        "RETURN",
        "BUILD_LIST",
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "MOD",
        "EQ",
        "NEQ",
        "LT",
        "GT",
        "LTE",
        "GTE",
        "AND",
        "OR",
        "NOT",
        "UNARY_NEG",
        "LOAD_NONE",
        "LOAD_TRUE",
        "LOAD_FALSE",
        "HALT",
    }
)


def _validate_instruction_operand(value: object) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "instruction operand type is not allowlisted")


def _validate_compiled_program(value: BytecodeProgram) -> str:
    if type(value) is not BytecodeProgram:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "compiler output must be exact BytecodeProgram")
    if value.version != CVM_BYTECODE_VERSION or type(value.version) is not str:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "bytecode version is incompatible")
    if value.host_abi_version != CVM_HOST_ABI_VERSION or type(value.host_abi_version) is not str:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "host ABI version is incompatible")
    if type(value.instructions) is not list or type(value.constants) is not list or type(value.guard_cleanup_table) is not list:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "bytecode collections must be exact lists")
    if value.guard_cleanup_table:
        raise _fail(CanonicalizationFailureCode.FORBIDDEN_OPCODE, "Stage 4 IR cannot produce guard cleanup entries")
    for constant in value.constants:
        if constant is None or type(constant) in (bool, str):
            if type(constant) is str:
                raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "string literal escaped closed IR")
            continue
        if type(constant) is int and SAFE_INTEGER_MIN <= constant <= SAFE_INTEGER_MAX:
            continue
        if type(constant) is float and math.isfinite(constant):
            continue
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "compiler constant type is not allowlisted")
    for instruction in value.instructions:
        if type(instruction) is not Instruction:
            raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "instruction must be exact Instruction")
        if type(instruction.op) is not str or instruction.op not in _ALLOWED_STAGE4_OPCODES:
            raise _fail(CanonicalizationFailureCode.FORBIDDEN_OPCODE, f"compiler emitted forbidden opcode {instruction.op}")
        _validate_instruction_operand(instruction.a)
        _validate_instruction_operand(instruction.b)
        _validate_instruction_operand(instruction.c)
    actual_hash = value.program_hash
    if type(actual_hash) is not str or _SHA256_RE.fullmatch(actual_hash) is None:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "actual program hash is malformed")
    return actual_hash


_PROGRAM_TRANSPORT_FIELDS = (
    "type",
    "version",
    "constants",
    "instructions",
    "host_abi_version",
    "program_hash",
    "guard_cleanup_table",
)


def _program_from_snapshot_bytes(value: object) -> tuple[BytecodeProgram, str]:
    if type(value) is not bytes:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "program snapshot must be exact bytes")
    decoded = decode_stage4_canonical_bytes(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    data = _exact_dict(decoded, _PROGRAM_TRANSPORT_FIELDS, "binding.program")
    if data["type"] != "bytecode_program" or type(data["type"]) is not str:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding program type is invalid")
    constants = _exact_list(data["constants"], "binding.program.constants")
    instruction_data = _exact_list(data["instructions"], "binding.program.instructions")
    cleanup_data = _exact_list(data["guard_cleanup_table"], "binding.program.guard_cleanup_table")
    if cleanup_data:
        raise _fail(CanonicalizationFailureCode.FORBIDDEN_OPCODE, "Stage 4 binding cannot contain guard cleanup entries")
    instructions: list[Instruction] = []
    for item in instruction_data:
        fields = _exact_dict(item, ("op", "a", "b", "c"), "binding.program.instruction")
        instructions.append(Instruction(op=fields["op"], a=fields["a"], b=fields["b"], c=fields["c"]))
    program = BytecodeProgram(
        instructions=instructions,
        constants=list(constants),
        version=data["version"],
        host_abi_version=data["host_abi_version"],
        guard_cleanup_table=[],
    )
    actual_hash = _validate_compiled_program(program)
    if type(data["program_hash"]) is not str or data["program_hash"] != actual_hash:
        raise _fail(
            CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH,
            "nested transport program_hash is not authoritative",
        )
    return program, actual_hash


def _snapshot_compiled_program(value: BytecodeProgram) -> tuple[bytes, str]:
    before_hash = _validate_compiled_program(value)
    snapshot = canonicalize_stage4_payload(
        value.to_dict(),
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    _, snapshot_hash = _program_from_snapshot_bytes(snapshot)
    after_hash = _validate_compiled_program(value)
    if snapshot_hash != before_hash or after_hash != before_hash:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "program changed while creating binding snapshot")
    return snapshot, snapshot_hash


def _snapshot_program_transport(value: object) -> tuple[bytes, BytecodeProgram, str]:
    snapshot = canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    program, actual_hash = _program_from_snapshot_bytes(snapshot)
    return snapshot, program, actual_hash


@dataclass(frozen=True, init=False)
class _CompilerEvidence:
    behavior_content_key: ContentKey
    program: BytecodeProgram
    actual_program_hash: str
    compiler_adapter_profile: str
    language_version: str
    bytecode_version: str
    host_abi_version: str
    compiler_identity: str
    compiler_target: str
    _trusted_seal: object


def _compile_validated_behavior_core(value: CanonicalBehaviorCore) -> _CompilerEvidence:
    validate_canonical_behavior_core(value)
    if value.program_form is ProgramForm.ARTIFACT_REF_V1:
        raise _fail(
            CanonicalizationFailureCode.PROGRAM_ARTIFACT_UNAVAILABLE,
            "Patch 2 does not resolve program artifact references",
        )
    if type(value.inline_program_ir_bytes) is not bytes:
        raise _fail(CanonicalizationFailureCode.PROGRAM_MISMATCH, "inline program bytes are absent")
    ir = decode_canonical_program_ir(value.inline_program_ir_bytes)
    program_ast = _ast_program(ir)
    compiler = CognitiveCompiler()
    program = compiler.compile(program_ast)
    actual_hash = _validate_compiled_program(program)
    evidence = object.__new__(_CompilerEvidence)
    object.__setattr__(evidence, "behavior_content_key", value.content_key)
    object.__setattr__(evidence, "program", program)
    object.__setattr__(evidence, "actual_program_hash", actual_hash)
    object.__setattr__(evidence, "compiler_adapter_profile", COMPILER_ADAPTER_PROFILE_V1)
    object.__setattr__(evidence, "language_version", LANGUAGE_VERSION)
    object.__setattr__(evidence, "bytecode_version", CVM_BYTECODE_VERSION)
    object.__setattr__(evidence, "host_abi_version", CVM_HOST_ABI_VERSION)
    object.__setattr__(evidence, "compiler_identity", COGNITIVE_COMPILER_ID)
    object.__setattr__(evidence, "compiler_target", CVM_COMPILER_TARGET)
    object.__setattr__(evidence, "_trusted_seal", _TRUSTED_SEAL)
    return evidence


def _binding_identity_payload(
    *,
    behavior_content_key: ContentKey,
    compiler_adapter_profile: str,
    language_version: str,
    bytecode_version: str,
    host_abi_version: str,
    compiler_identity: str,
    compiler_target: str,
    actual_program_hash: str,
) -> dict[str, str]:
    _validate_content_key_shape(behavior_content_key)
    return {
        "schema_version": SchemaVersion.COMPILER_BINDING_V1.value,
        "behavior_content_key": behavior_content_key.value,
        "compiler_adapter_profile": compiler_adapter_profile,
        "language_version": language_version,
        "bytecode_version": bytecode_version,
        "host_abi_version": host_abi_version,
        "compiler_identity": compiler_identity,
        "compiler_target": compiler_target,
        "actual_program_hash": actual_program_hash,
    }


def _binding_identity_bytes_from_fields(
    *,
    behavior_content_key: ContentKey,
    compiler_adapter_profile: str,
    language_version: str,
    bytecode_version: str,
    host_abi_version: str,
    compiler_identity: str,
    compiler_target: str,
    actual_program_hash: str,
) -> bytes:
    payload = _binding_identity_payload(
        behavior_content_key=behavior_content_key,
        compiler_adapter_profile=compiler_adapter_profile,
        language_version=language_version,
        bytecode_version=bytecode_version,
        host_abi_version=host_abi_version,
        compiler_identity=compiler_identity,
        compiler_target=compiler_target,
        actual_program_hash=actual_program_hash,
    )
    return canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


@dataclass(frozen=True, init=False)
class CompilerBinding:
    schema_version: SchemaVersion
    behavior_content_key: ContentKey
    compiler_adapter_profile: str
    language_version: str
    bytecode_version: str
    host_abi_version: str
    compiler_identity: str
    compiler_target: str
    actual_program_hash: str
    binding_id: RecordId
    _program_snapshot_bytes: bytes
    _unit_context_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompilerBinding:
        raise TypeError("CompilerBinding is created only after full Behavior Unit validation")

    @property
    def program(self) -> BytecodeProgram:
        program, _ = _program_from_snapshot_bytes(self._program_snapshot_bytes)
        return program


def _validate_unit_context_digest(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "unit context digest is invalid")
    return value


def _bind_compiler_evidence(evidence: _CompilerEvidence, *, unit_context_sha256: str) -> CompilerBinding:
    if type(evidence) is not _CompilerEvidence or getattr(evidence, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "compiler evidence is not factory sealed")
    _validate_unit_context_digest(unit_context_sha256)
    program_snapshot, actual_hash = _snapshot_compiled_program(evidence.program)
    if actual_hash != evidence.actual_program_hash:
        raise _fail(CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH, "program changed before binding")
    identity_bytes = _binding_identity_bytes_from_fields(
        behavior_content_key=evidence.behavior_content_key,
        compiler_adapter_profile=evidence.compiler_adapter_profile,
        language_version=evidence.language_version,
        bytecode_version=evidence.bytecode_version,
        host_abi_version=evidence.host_abi_version,
        compiler_identity=evidence.compiler_identity,
        compiler_target=evidence.compiler_target,
        actual_program_hash=actual_hash,
    )
    binding = object.__new__(CompilerBinding)
    object.__setattr__(binding, "schema_version", SchemaVersion.COMPILER_BINDING_V1)
    object.__setattr__(binding, "behavior_content_key", evidence.behavior_content_key)
    object.__setattr__(binding, "compiler_adapter_profile", evidence.compiler_adapter_profile)
    object.__setattr__(binding, "language_version", evidence.language_version)
    object.__setattr__(binding, "bytecode_version", evidence.bytecode_version)
    object.__setattr__(binding, "host_abi_version", evidence.host_abi_version)
    object.__setattr__(binding, "compiler_identity", evidence.compiler_identity)
    object.__setattr__(binding, "compiler_target", evidence.compiler_target)
    object.__setattr__(binding, "actual_program_hash", actual_hash)
    object.__setattr__(binding, "binding_id", compute_record_id(domain=IdentityDomain.COMPILER_BINDING, canonical_bytes=identity_bytes))
    object.__setattr__(binding, "_program_snapshot_bytes", program_snapshot)
    object.__setattr__(binding, "_unit_context_sha256", unit_context_sha256)
    object.__setattr__(binding, "_trusted_seal", _TRUSTED_SEAL)
    _validate_compiler_binding(binding, core=None, unit_context_sha256=unit_context_sha256)
    return binding


def _validate_compiler_binding(
    value: CompilerBinding,
    *,
    core: CanonicalBehaviorCore | None,
    unit_context_sha256: str,
) -> None:
    if type(value) is not CompilerBinding:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "binding must be exact CompilerBinding")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "binding is not factory sealed")
    if value.schema_version is not SchemaVersion.COMPILER_BINDING_V1:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "compiler binding schema is unknown")
    _validate_unit_context_digest(unit_context_sha256)
    if value._unit_context_sha256 != unit_context_sha256:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding belongs to a different Unit context")
    _validate_content_key_shape(value.behavior_content_key)
    if core is not None:
        validate_canonical_behavior_core(core)
        if value.behavior_content_key.value != core.content_key.value:
            raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding behavior key mismatch")
    if value.compiler_adapter_profile != COMPILER_ADAPTER_PROFILE_V1:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_COMPILER, "binding compiler adapter is unknown")
    if value.language_version != LANGUAGE_VERSION:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_LANGUAGE, "binding language version is unknown")
    if value.bytecode_version != CVM_BYTECODE_VERSION or value.host_abi_version != CVM_HOST_ABI_VERSION:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding VM versions mismatch")
    if value.compiler_identity != COGNITIVE_COMPILER_ID or value.compiler_target != CVM_COMPILER_TARGET:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_COMPILER, "binding compiler identity is unknown")
    _, actual_hash = _program_from_snapshot_bytes(value._program_snapshot_bytes)
    if value.actual_program_hash != actual_hash:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding does not contain actual program hash")
    identity_bytes = _binding_identity_bytes_from_fields(
        behavior_content_key=value.behavior_content_key,
        compiler_adapter_profile=value.compiler_adapter_profile,
        language_version=value.language_version,
        bytecode_version=value.bytecode_version,
        host_abi_version=value.host_abi_version,
        compiler_identity=value.compiler_identity,
        compiler_target=value.compiler_target,
        actual_program_hash=actual_hash,
    )
    if type(value.binding_id) is not RecordId or value.binding_id.domain is not IdentityDomain.COMPILER_BINDING:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding identity domain mismatch")
    try:
        validate_record_id(value.binding_id, canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding identity mismatch") from exc


def _compiler_binding_to_dict(value: CompilerBinding, *, core: CanonicalBehaviorCore, unit_context_sha256: str) -> dict[str, object]:
    _validate_compiler_binding(value, core=core, unit_context_sha256=unit_context_sha256)
    payload = _binding_identity_payload(
        behavior_content_key=value.behavior_content_key,
        compiler_adapter_profile=value.compiler_adapter_profile,
        language_version=value.language_version,
        bytecode_version=value.bytecode_version,
        host_abi_version=value.host_abi_version,
        compiler_identity=value.compiler_identity,
        compiler_target=value.compiler_target,
        actual_program_hash=value.actual_program_hash,
    )
    program, _ = _program_from_snapshot_bytes(value._program_snapshot_bytes)
    return {**payload, "binding_id": value.binding_id.to_dict(), "program": program.to_dict()}


def _compiler_binding_from_dict(
    value: object,
    *,
    core: CanonicalBehaviorCore,
    unit_context_sha256: str,
) -> CompilerBinding:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "behavior_content_key",
            "compiler_adapter_profile",
            "language_version",
            "bytecode_version",
            "host_abi_version",
            "compiler_identity",
            "compiler_target",
            "actual_program_hash",
            "binding_id",
            "program",
        ),
        "compiler_binding",
    )
    validate_canonical_behavior_core(core)
    if data["schema_version"] != SchemaVersion.COMPILER_BINDING_V1.value:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "compiler binding schema is unknown")
    supplied_key = _parse_content_key_text(data["behavior_content_key"])
    if supplied_key != core.content_key.digest_sha256:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "binding behavior key mismatch")
    _, program, actual_hash = _snapshot_program_transport(data["program"])
    if data["actual_program_hash"] != actual_hash or type(data["actual_program_hash"]) is not str:
        raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "transport program hash is not authoritative")
    evidence = object.__new__(_CompilerEvidence)
    object.__setattr__(evidence, "behavior_content_key", core.content_key)
    object.__setattr__(evidence, "program", program)
    object.__setattr__(evidence, "actual_program_hash", actual_hash)
    for field in (
        "compiler_adapter_profile",
        "language_version",
        "bytecode_version",
        "host_abi_version",
        "compiler_identity",
        "compiler_target",
    ):
        if type(data[field]) is not str:
            raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, f"binding {field} must be exact str")
        object.__setattr__(evidence, field, data[field])
    object.__setattr__(evidence, "_trusted_seal", _TRUSTED_SEAL)
    binding = _bind_compiler_evidence(evidence, unit_context_sha256=unit_context_sha256)
    if data["binding_id"] != binding.binding_id.to_dict():
        identity_bytes = _binding_identity_bytes_from_fields(
            behavior_content_key=binding.behavior_content_key,
            compiler_adapter_profile=binding.compiler_adapter_profile,
            language_version=binding.language_version,
            bytecode_version=binding.bytecode_version,
            host_abi_version=binding.host_abi_version,
            compiler_identity=binding.compiler_identity,
            compiler_target=binding.compiler_target,
            actual_program_hash=binding.actual_program_hash,
        )
        try:
            record_id_from_dict(data["binding_id"], canonical_bytes=identity_bytes)
        except ValueError as exc:
            raise _fail(CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH, "transport binding identity mismatch") from exc
    return binding


class MigrationStrategy(str, Enum):
    RECANONICALIZE_REVERIFY = "RECANONICALIZE_REVERIFY"
    DUAL_KEY_TRANSITION = "DUAL_KEY_TRANSITION"
    NON_MIGRATABLE = "NON_MIGRATABLE"


class MigrationReasonCode(str, Enum):
    NO_APPROVED_TARGET = "NO_APPROVED_TARGET"
    SEMANTIC_MIGRATION_UNPROVEN = "SEMANTIC_MIGRATION_UNPROVEN"
    UNSUPPORTED_LEGACY_ENDPOINT = "UNSUPPORTED_LEGACY_ENDPOINT"


@dataclass(frozen=True)
class MigrationEndpointDescriptor:
    content_key: ContentKey
    profile_id: str
    codec_id: str
    core_schema_id: str
    ir_schema_id: str
    language_version: str
    compiler_adapter_profile: str
    payload_sha256: str

    def __post_init__(self) -> None:
        _validate_migration_endpoint(self)

    def to_dict(self) -> dict[str, str]:
        _validate_migration_endpoint(self)
        return {
            "content_key": self.content_key.value,
            "profile_id": self.profile_id,
            "codec_id": self.codec_id,
            "core_schema_id": self.core_schema_id,
            "ir_schema_id": self.ir_schema_id,
            "language_version": self.language_version,
            "compiler_adapter_profile": self.compiler_adapter_profile,
            "payload_sha256": self.payload_sha256,
        }


def _validate_migration_endpoint(value: MigrationEndpointDescriptor) -> None:
    if type(value) is not MigrationEndpointDescriptor:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "migration endpoint must be exact descriptor")
    _validate_content_key_shape(value.content_key)
    _require_known_endpoint(
        profile_id=value.profile_id,
        codec_id=value.codec_id,
        core_schema_id=value.core_schema_id,
        ir_schema_id=value.ir_schema_id,
        language_version=value.language_version,
        compiler_adapter_profile=value.compiler_adapter_profile,
    )
    _sha256(value.payload_sha256, "migration endpoint payload_sha256")


def migration_endpoint_for_core(value: CanonicalBehaviorCore) -> MigrationEndpointDescriptor:
    validate_canonical_behavior_core(value)
    return MigrationEndpointDescriptor(
        content_key=value.content_key,
        profile_id=value.profile_id,
        codec_id=value.codec_id,
        core_schema_id=value.core_schema_id,
        ir_schema_id=value.ir_schema_id,
        language_version=value.language_version,
        compiler_adapter_profile=value.compiler_adapter_profile,
        payload_sha256=value.payload_sha256,
    )


def _migration_identity_payload(
    *,
    old_endpoint: MigrationEndpointDescriptor,
    new_endpoint: MigrationEndpointDescriptor | None,
    strategy: MigrationStrategy,
    reason_code: MigrationReasonCode,
    migrator_id: str | None,
    reverify_reattest_required: bool,
) -> dict[str, object]:
    _validate_migration_endpoint(old_endpoint)
    if new_endpoint is not None:
        _validate_migration_endpoint(new_endpoint)
    return {
        "schema_version": SchemaVersion.MIGRATION_RELATION_V1.value,
        "migration_profile": MIGRATION_PROFILE_V1,
        "old_endpoint": old_endpoint.to_dict(),
        "new_endpoint": None if new_endpoint is None else new_endpoint.to_dict(),
        "strategy": strategy.value,
        "reason_code": reason_code.value,
        "migrator_id": migrator_id,
        "reverify_reattest_required": reverify_reattest_required,
    }


@dataclass(frozen=True, init=False)
class MigrationRelation:
    schema_version: SchemaVersion
    migration_profile: str
    old_endpoint: MigrationEndpointDescriptor
    new_endpoint: MigrationEndpointDescriptor | None
    strategy: MigrationStrategy
    reason_code: MigrationReasonCode
    migrator_id: str | None
    reverify_reattest_required: bool
    relation_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> MigrationRelation:
        raise TypeError("MigrationRelation is created only by an approved versioned migration factory")

    def to_dict(self) -> dict[str, object]:
        _validate_migration_relation(self)
        payload = _migration_identity_payload(
            old_endpoint=self.old_endpoint,
            new_endpoint=self.new_endpoint,
            strategy=self.strategy,
            reason_code=self.reason_code,
            migrator_id=self.migrator_id,
            reverify_reattest_required=self.reverify_reattest_required,
        )
        return {**payload, "relation_id": self.relation_id.to_dict()}


def create_non_migratable_relation(
    old_core: CanonicalBehaviorCore,
    *,
    reason_code: MigrationReasonCode,
) -> MigrationRelation:
    validate_canonical_behavior_core(old_core)
    if type(reason_code) is not MigrationReasonCode:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration reason must be exact known code")
    old_endpoint = migration_endpoint_for_core(old_core)
    payload = _migration_identity_payload(
        old_endpoint=old_endpoint,
        new_endpoint=None,
        strategy=MigrationStrategy.NON_MIGRATABLE,
        reason_code=reason_code,
        migrator_id=None,
        reverify_reattest_required=False,
    )
    identity_bytes = canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    relation = object.__new__(MigrationRelation)
    object.__setattr__(relation, "schema_version", SchemaVersion.MIGRATION_RELATION_V1)
    object.__setattr__(relation, "migration_profile", MIGRATION_PROFILE_V1)
    object.__setattr__(relation, "old_endpoint", old_endpoint)
    object.__setattr__(relation, "new_endpoint", None)
    object.__setattr__(relation, "strategy", MigrationStrategy.NON_MIGRATABLE)
    object.__setattr__(relation, "reason_code", reason_code)
    object.__setattr__(relation, "migrator_id", None)
    object.__setattr__(relation, "reverify_reattest_required", False)
    object.__setattr__(relation, "relation_id", compute_record_id(domain=IdentityDomain.MIGRATION_RELATION, canonical_bytes=identity_bytes))
    object.__setattr__(relation, "_trusted_seal", _TRUSTED_SEAL)
    _validate_migration_relation(relation)
    return relation


def create_positive_migration_relation(
    *,
    old_core: CanonicalBehaviorCore,
    new_core: CanonicalBehaviorCore,
    strategy: MigrationStrategy,
    reason_code: MigrationReasonCode,
    migrator_id: str,
    reverify_reattest_required: bool,
) -> MigrationRelation:
    validate_canonical_behavior_core(old_core)
    validate_canonical_behavior_core(new_core)
    if type(strategy) is not MigrationStrategy or strategy is MigrationStrategy.NON_MIGRATABLE:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "positive migration strategy is invalid")
    if type(reason_code) is not MigrationReasonCode:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration reason is invalid")
    _versioned(migrator_id, "migrator_id")
    if reverify_reattest_required is not True:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "positive migration requires re-verification and re-attestation")
    raise _fail(
        CanonicalizationFailureCode.MIGRATION_ENDPOINT_NOT_APPROVED,
        "Patch 2 has no approved distinct migration endpoint or deterministic migrator",
    )


def _validate_migration_relation(value: MigrationRelation) -> None:
    if type(value) is not MigrationRelation:
        raise _fail(CanonicalizationFailureCode.TYPE_MISMATCH, "migration relation must be exact MigrationRelation")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(CanonicalizationFailureCode.TRUSTED_OBJECT_FORGED, "migration relation is not factory sealed")
    if value.schema_version is not SchemaVersion.MIGRATION_RELATION_V1 or value.migration_profile != MIGRATION_PROFILE_V1:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "migration relation schema/profile is unknown")
    if value.strategy is not MigrationStrategy.NON_MIGRATABLE:
        raise _fail(CanonicalizationFailureCode.MIGRATION_ENDPOINT_NOT_APPROVED, "positive migration endpoint is not approved")
    if type(value.reason_code) is not MigrationReasonCode:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration reason is invalid")
    _validate_migration_endpoint(value.old_endpoint)
    if value.new_endpoint is not None or value.migrator_id is not None or value.reverify_reattest_required is not False:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "NON_MIGRATABLE must have exact absent new endpoint and migrator")
    identity_payload = _migration_identity_payload(
        old_endpoint=value.old_endpoint,
        new_endpoint=None,
        strategy=value.strategy,
        reason_code=value.reason_code,
        migrator_id=None,
        reverify_reattest_required=False,
    )
    identity_bytes = canonicalize_stage4_payload(
        identity_payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    if type(value.relation_id) is not RecordId or value.relation_id.domain is not IdentityDomain.MIGRATION_RELATION:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration relation identity domain mismatch")
    try:
        validate_record_id(value.relation_id, canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration relation identity mismatch") from exc


def migration_relation_from_dict(value: object, *, old_core: CanonicalBehaviorCore) -> MigrationRelation:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "migration_profile",
            "old_endpoint",
            "new_endpoint",
            "strategy",
            "reason_code",
            "migrator_id",
            "reverify_reattest_required",
            "relation_id",
        ),
        "migration_relation",
    )
    if data["schema_version"] != SchemaVersion.MIGRATION_RELATION_V1.value or data["migration_profile"] != MIGRATION_PROFILE_V1:
        raise _fail(CanonicalizationFailureCode.UNKNOWN_SCHEMA, "migration relation schema/profile is unknown")
    strategy = _enum(data["strategy"], MigrationStrategy, CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration.strategy")
    reason = _enum(data["reason_code"], MigrationReasonCode, CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration.reason_code")
    assert isinstance(strategy, MigrationStrategy)
    assert isinstance(reason, MigrationReasonCode)
    if strategy is not MigrationStrategy.NON_MIGRATABLE:
        raise _fail(CanonicalizationFailureCode.MIGRATION_ENDPOINT_NOT_APPROVED, "positive migration endpoint is not approved")
    if data["new_endpoint"] is not None or data["migrator_id"] is not None or data["reverify_reattest_required"] is not False:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "NON_MIGRATABLE absence fields mismatch")
    relation = create_non_migratable_relation(old_core, reason_code=reason)
    if data["old_endpoint"] != relation.old_endpoint.to_dict():
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "old migration endpoint mismatch")
    identity_payload = _migration_identity_payload(
        old_endpoint=relation.old_endpoint,
        new_endpoint=None,
        strategy=relation.strategy,
        reason_code=relation.reason_code,
        migrator_id=None,
        reverify_reattest_required=False,
    )
    identity_bytes = canonicalize_stage4_payload(
        identity_payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    try:
        supplied_id = record_id_from_dict(data["relation_id"], canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration relation identity mismatch") from exc
    if supplied_id.value != relation.relation_id.value:
        raise _fail(CanonicalizationFailureCode.MIGRATION_INVARIANT, "migration relation identity mismatch")
    return relation


__all__ = [
    "BEHAVIOR_BLOB_SCHEMA_V1",
    "BEHAVIOR_CORE_SCHEMA_V1",
    "CANONICAL_PROGRAM_IR_V1",
    "COMPILER_ADAPTER_PROFILE_V1",
    "CONTENT_KEY_PROTOCOL_V1",
    "CONTENT_KEY_TEXT_PREFIX",
    "COGNITIVE_COMPILER_ID",
    "CVM_BYTECODE_VERSION",
    "CVM_COMPILER_TARGET",
    "CVM_HOST_ABI_VERSION",
    "CanonicalBehaviorCore",
    "CanonicalizationFailureCode",
    "CanonicalizationViolation",
    "ClaimedContentKey",
    "CompilerBinding",
    "ContentKey",
    "ContentValidationReason",
    "ContentValidationResult",
    "ContentValidationStatus",
    "HashBoundRef",
    "MAX_INLINE_CANONICAL_PROGRAM_BYTES",
    "MIGRATION_PROFILE_V1",
    "MigrationEndpointDescriptor",
    "MigrationReasonCode",
    "MigrationRelation",
    "MigrationStrategy",
    "ProgramForm",
    "RefKind",
    "STABLE_CANONICAL_CODEC_ID",
    "STAGE4_CANONICAL_PROFILE_V1",
    "canonical_base64url",
    "canonical_program_ir_bytes",
    "canonicalize_stage4_payload",
    "compare_canonical_content",
    "compute_content_key",
    "content_key_from_dict",
    "content_key_preimage",
    "create_non_migratable_relation",
    "create_positive_migration_relation",
    "decode_canonical_base64url",
    "decode_canonical_program_ir",
    "decode_stage4_canonical_bytes",
    "migration_endpoint_for_core",
    "migration_relation_from_dict",
    "validate_canonical_behavior_core",
    "validate_canonical_program_ir",
    "validate_ref_collection",
]
