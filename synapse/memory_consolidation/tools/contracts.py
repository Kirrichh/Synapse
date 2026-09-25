"""Tool contracts: the operator's admitted servers, operations and provenance graph.

A tool contract states the documented semantics of one operation and its
answer: how the service reports a result, which effect a refusal leaves
(none, partial, applied, unknown), whether the operation is idempotent, what
compensates it, which state check resolves its uncertainty and which refusals
are the service's own unavailability. It cannot redefine a task's goal. The
configuration is strict JSON fixed before a run; the run records its digest and
the agent never changes it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..records import digest

TOOL_CONFIGURATION_V1 = "synapse.memory.tool-configuration/v1"
EFFECTS = ("none", "partial", "applied", "unknown")
ROLES = ("action", "reason")
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*:[A-Za-z0-9@_.+-]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTRACT_FIELDS = {"idempotent", "effect_on_err", "repeatable_on_partial", "compensates", "compensation_signs",
                    "state_check_for", "resolve_state", "environmental_errors", "doc"}


class ToolConfigurationViolation(ValueError):
    """The operator's tool configuration is outside its declared contract."""


@dataclass(frozen=True)
class ToolServer:
    """An admitted MCP stdio server: argv, environment and working directory."""

    server_id: str
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None


@dataclass(frozen=True)
class ToolContract:
    """The documented semantics of one admitted operation."""

    name: str
    server_id: str
    descriptor_sha256: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    source: str
    role: str = "action"
    idempotent: bool = False
    effect_on_err: Mapping[str, str] = field(default_factory=dict)
    repeatable_on_partial: bool = False
    compensates: str | None = None
    compensation_signs: Mapping[str, Any] = field(default_factory=dict)
    state_check_for: str | None = None
    resolve_state: Mapping[str, str] = field(default_factory=dict)
    environmental_errors: tuple[str, ...] = ()
    event_fields: tuple[str, ...] = ()
    doc: str = ""

    @property
    def service(self) -> str:
        return self.source.partition(":")[0]

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "server": self.server_id, "descriptor_sha256": self.descriptor_sha256,
                "input_schema": dict(self.input_schema), "output_schema": dict(self.output_schema),
                "source": self.source, "role": self.role, "idempotent": self.idempotent,
                "effect_on_err": dict(self.effect_on_err), "repeatable_on_partial": self.repeatable_on_partial,
                "compensates": self.compensates, "compensation_signs": dict(self.compensation_signs),
                "state_check_for": self.state_check_for, "resolve_state": dict(self.resolve_state),
                "environmental_errors": list(self.environmental_errors),
                "event_fields": list(self.event_fields), "doc": self.doc}

    @property
    def contract_ref(self) -> str:
        return digest(self.canonical())


@dataclass(frozen=True)
class ToolConfiguration:
    """Every server, contract and provenance node admitted for one memory owner's runs."""

    servers: Mapping[str, ToolServer]
    tools: Mapping[str, ToolContract]
    provenance: Mapping[str, tuple[str, ...]]
    raw: Mapping[str, Any]

    @property
    def configuration_sha256(self) -> str:
        return digest(dict(self.raw))

    def contract(self, name: str) -> ToolContract:
        found = self.tools.get(name)
        if found is None:
            raise PermissionError(f"tool {name!r} is not admitted for this run")
        return found


def _fail(detail: str) -> ToolConfigurationViolation:
    return ToolConfigurationViolation(f"tool configuration: {detail}")


def _mapping(value: Any, name: str, *, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        raise _fail(f"{name} has an unknown shape")
    return value


def _parse_server(item: Any, known: Mapping[str, ToolServer]) -> ToolServer:
    server = _mapping(item, "server", required={"id", "argv"}, optional={"env", "cwd"})
    if type(server["id"]) is not str or _NAME_RE.fullmatch(server["id"]) is None or server["id"] in known:
        raise _fail("server ids are unique names")
    if type(server["argv"]) is not list or not server["argv"] or any(
            type(arg) is not str or not arg for arg in server["argv"]):
        raise _fail("server argv is a non-empty list of strings")
    env = server.get("env", {})
    if type(env) is not dict or any(type(k) is not str or type(v) is not str for k, v in env.items()):
        raise _fail("server env maps names to strings")
    cwd = server.get("cwd")
    if cwd is not None and (type(cwd) is not str or not Path(cwd).is_absolute()):
        raise _fail("server cwd is an absolute path")
    return ToolServer(server["id"], tuple(server["argv"]), tuple(sorted(env.items())), cwd)


def _names(value: Any, detail: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise _fail(detail)
    return value


def _parse_semantics(name: str, value: Any) -> dict[str, Any]:
    """The documented answer semantics of one tool (its ``contract`` object)."""
    contract = _mapping(value, f"{name} contract", required=set(), optional=_CONTRACT_FIELDS)
    effect_on_err = contract.get("effect_on_err", {})
    if type(effect_on_err) is not dict or any(type(k) is not str or v not in EFFECTS for k, v in effect_on_err.items()):
        raise _fail(f"tool {name} maps refusal codes to effect classes")
    resolve_state = contract.get("resolve_state", {})
    if type(resolve_state) is not dict or any(v not in {"applied", "none"} for v in resolve_state.values()):
        raise _fail(f"tool {name} resolves uncertainty only to applied or none")
    for flag in ("idempotent", "repeatable_on_partial"):
        if type(contract.get(flag, False)) is not bool:
            raise _fail(f"tool {name} {flag} is a boolean")
    signs = contract.get("compensation_signs", {})
    if type(signs) is not dict:
        raise _fail(f"tool {name} compensation signs are an object")
    environmental = _names(contract.get("environmental_errors", []), f"tool {name} environmental refusal codes are names")
    return {"idempotent": contract.get("idempotent", False), "effect_on_err": effect_on_err,
            "repeatable_on_partial": contract.get("repeatable_on_partial", False),
            "compensates": contract.get("compensates"), "compensation_signs": signs,
            "state_check_for": contract.get("state_check_for"), "resolve_state": resolve_state,
            "environmental_errors": tuple(sorted(set(environmental))), "doc": str(contract.get("doc", ""))}


def _parse_tool(item: Any, servers: Mapping[str, ToolServer], known: Mapping[str, ToolContract]) -> ToolContract:
    tool = _mapping(item, "tool", required={"name", "server", "descriptor_sha256", "input_schema",
                                            "output_schema", "source"}, optional={"role", "contract", "event_fields"})
    name = tool["name"]
    if type(name) is not str or _NAME_RE.fullmatch(name) is None or name in known:
        raise _fail("tool names are unique")
    if tool["server"] not in servers:
        raise _fail(f"tool {name} names an unknown server")
    if type(tool["descriptor_sha256"]) is not str or _SHA256_RE.fullmatch(tool["descriptor_sha256"]) is None:
        raise _fail(f"tool {name} has no admitted descriptor digest")
    if type(tool["input_schema"]) is not dict or type(tool["output_schema"]) is not dict:
        raise _fail(f"tool {name} schemas are objects")
    if type(tool["source"]) is not str or _SOURCE_RE.fullmatch(tool["source"]) is None:
        raise _fail(f"tool {name} declares its source as 'service:account'")
    role = tool.get("role", "action")
    if role not in ROLES:
        raise _fail(f"tool {name} role is action or reason")
    event_fields = _names(tool.get("event_fields", []), f"tool {name} event fields are names")
    return ToolContract(name=name, server_id=tool["server"], descriptor_sha256=tool["descriptor_sha256"],
                        input_schema=tool["input_schema"], output_schema=tool["output_schema"], source=tool["source"],
                        role=role, event_fields=tuple(event_fields),
                        **_parse_semantics(name, tool.get("contract", {})))


def _parse_provenance(value: Any) -> dict[str, tuple[str, ...]]:
    if type(value) is not dict:
        raise _fail("provenance maps sources to their declared ancestors")
    graph: dict[str, tuple[str, ...]] = {}
    for source, node in value.items():
        node = _mapping(node, f"provenance {source}", required={"ancestors"})
        graph[source] = tuple(sorted(set(_names(node["ancestors"], f"provenance {source} ancestors are names"))))
    return graph


def parse_tool_configuration(value: Any) -> ToolConfiguration:
    """Validate the operator's frozen tool configuration (strict JSON)."""
    root = _mapping(value, "configuration", required={"schema_version", "servers", "tools", "provenance"})
    if root["schema_version"] != TOOL_CONFIGURATION_V1:
        raise _fail("unsupported schema version")
    if type(root["servers"]) is not list or not root["servers"]:
        raise _fail("servers must be a non-empty list")
    servers: dict[str, ToolServer] = {}
    for item in root["servers"]:
        server = _parse_server(item, servers)
        servers[server.server_id] = server
    if type(root["tools"]) is not list or not root["tools"]:
        raise _fail("tools must be a non-empty list")
    tools: dict[str, ToolContract] = {}
    for item in root["tools"]:
        contract = _parse_tool(item, servers, tools)
        tools[contract.name] = contract
    for contract in tools.values():
        for linked in (contract.compensates, contract.state_check_for):
            if linked is not None and linked not in tools:
                raise _fail(f"tool {contract.name} links an unknown tool {linked}")
    return ToolConfiguration(servers=servers, tools=tools, provenance=_parse_provenance(root["provenance"]), raw=value)


def read_tool_configuration(path: Path) -> ToolConfiguration:
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise _fail("configuration exceeds its byte budget")
    return parse_tool_configuration(json.loads(raw))
