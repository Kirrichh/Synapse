"""Release-owned machine profile and the single envelope schema authority."""
from __future__ import annotations

import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

_resources = files("synapse.machine")
PROFILE = json.loads(_resources.joinpath("release_profile.json").read_text(encoding="utf-8"))
SCHEMA = json.loads(_resources.joinpath("schemas/envelope.schema.json").read_text(encoding="utf-8"))
VERSION = SCHEMA["$defs"]["schemaVersion"]["const"]
METHODS = frozenset(SCHEMA["$defs"]["controlMethod"]["enum"])
ERROR_CODES = frozenset(SCHEMA["$defs"]["errorCode"]["enum"])
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class ProtocolError(Exception):
    """Only a declared nonsecret code may cross the diagnostic boundary."""

    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "INTERNAL_ERROR"
        super().__init__(self.code)


def message(kind: str, **fields: object) -> dict:
    return {"schema_version": VERSION, "type": kind, **fields}


def refusal(code: str, *, fatal: bool, request_id: str | None = None) -> dict:
    result = message("error", code=ProtocolError(code).code, fatal=fatal)
    if request_id is not None:
        result["request_id"] = request_id
    return result


def validate(value: object) -> dict:
    if isinstance(value, dict) and value.get("schema_version") != VERSION:
        raise ProtocolError("SCHEMA_UNSUPPORTED")
    if not VALIDATOR.is_valid(value):
        raise ProtocolError("SCHEMA_INVALID")
    return value
