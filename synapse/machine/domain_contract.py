"""Application wire schema with explicit, local-only owner-schema references."""
import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from synapse.commands.jcs import canonical_bytes, InvalidCommandValue
from .contract import ProtocolError

SCHEMA = json.loads(files("synapse.machine").joinpath("schemas/domain.schema.json").read_text(encoding="utf-8"))
VERSION = "synapse.machine.domain/v1"
_schemas = [json.loads(files("synapse.commands").joinpath(name).read_text(encoding="utf-8"))
            for name in ("binding.schema.json", "reconciliation.schema.json")]
_registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in _schemas)
VALIDATOR = Draft202012Validator(SCHEMA, registry=_registry, format_checker=FormatChecker())


def validate(value: object) -> dict:
    if not VALIDATOR.is_valid(value):
        raise ProtocolError("SCHEMA_INVALID")
    canonical_bytes(value)
    return value


def reply(request: dict, result: dict) -> dict:
    try:
        return validate({"schema_version": VERSION, "type": "result", "request_id": request["request_id"],
                         "method": request["method"], "result": result})
    except InvalidCommandValue:
        # A reply budget failure does not erase a durable owner acceptance.
        return refusal(request, "RESPONSE_TOO_LARGE")


def refusal(request: dict, code: str) -> dict:
    if code not in SCHEMA["$defs"]["error"]["enum"]:
        code = "INTERNAL_ERROR"
    return validate({"schema_version": VERSION, "type": "error", "request_id": request["request_id"], "code": code})
