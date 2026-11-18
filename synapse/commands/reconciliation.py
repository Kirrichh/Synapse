"""Closed read-only status query contract, without owner lookup or authority."""
import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from .binding import CommandRefused

SCHEMA = json.loads(files("synapse.commands").joinpath("reconciliation.schema.json").read_text(encoding="utf-8"))
QUERY = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/query"},
                            format_checker=FormatChecker())


def status_query(command_id: str, recipient: str) -> dict:
    query = {"command_id": command_id, "recipient": recipient, "owner": "agent.invocations"}
    if not QUERY.is_valid(query):
        raise CommandRefused("COMMAND_INVALID")
    return query
