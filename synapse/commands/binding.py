"""Immutable owner-computed binding, independent of command transport/storage."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json

from jsonschema import Draft202012Validator, FormatChecker

from .jcs import canonical_bytes, decode, parameter_hash

SCHEMA = json.loads(files("synapse.commands").joinpath("binding.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class CommandRefused(ValueError):
    CODES = frozenset({"COMMAND_INVALID", "BINDING_CONFLICT", "REVISION_CONFLICT",
                       "OBJECT_UNAVAILABLE", "RECORD_INVALID", "SCOPE_REFUSED", "CURSOR_UNAVAILABLE"})

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "COMMAND_INVALID"
        super().__init__(self.code)


@dataclass(frozen=True)
class CommandScope:
    """Descriptive binding data, not proof of permission to execute a command."""

    principal: str
    workspace: str

    def __post_init__(self):
        for value in (self.principal, self.workspace):
            if type(value) is not str or not 1 <= len(value) <= 256:
                raise CommandRefused("SCOPE_REFUSED")
        canonical_bytes({"principal": self.principal, "workspace": self.workspace})


@dataclass(frozen=True)
class CommandBinding:
    command_id: str
    body: bytes

    @classmethod
    def accept(cls, scope: CommandScope, command: dict) -> CommandBinding:
        if type(scope) is not CommandScope:
            raise CommandRefused("SCOPE_REFUSED")
        # Take an immutable, validated copy before consulting the owner. No
        # caller hash, principal, normalization or unknown restriction is used.
        value = decode(canonical_bytes(command))
        if not VALIDATOR.is_valid(value):
            raise CommandRefused("COMMAND_INVALID")
        if value["recipient"] != value["params"]["invocation_id"]:
            raise CommandRefused("BINDING_CONFLICT")
        body = {"schema_version": "synapse.command.binding/v1",
                "principal": scope.principal, "workspace": scope.workspace,
                "method": value["method"], "method_version": value["method_version"],
                "owner": "agent.invocations", "recipient": value["recipient"],
                "expected_revision": value["expected_revision"],
                "params": value["params"], "params_hash": parameter_hash(value["params"])}
        return cls(value["command_id"], canonical_bytes(body))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def document(self) -> dict:
        return decode(self.body)
