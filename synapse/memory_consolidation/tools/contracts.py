"""Tool contracts: the operator's admitted servers, operations and provenance graph.

A tool contract states the documented semantics of one operation and its
answer: how the service reports a result, which effect a refusal leaves
(none, partial, applied; a code it does not describe leaves the effect
unknown), whether the operation is idempotent or the provider deduplicates
requests by a key the gateway issues, what
compensates it, which state check resolves its uncertainty and which refusals
are the service's own unavailability. It cannot redefine a task's goal. The
configuration is strict JSON fixed before a run; the run records its digest and
the agent never changes it.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..records import digest

TOOL_CONFIGURATION_V1 = "synapse.memory.tool-configuration/v1"
#: v2 (review R1, R2): a refusal code the contract does not describe leaves the effect unknown, a state check
#: names what it binds and what it attests, and a provider's idempotency key may be declared. A v1
#: configuration read its contracts otherwise; it is refused, and an owner bound to one is migrated by
#: ``synapse memory reassess`` with its v2 successor.
TOOL_CONFIGURATION_V2 = "synapse.memory.tool-configuration/v2"
EFFECTS = ("none", "partial", "applied", "unknown")
ROLES = ("action", "reason")
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*:[A-Za-z0-9@_.+-]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTRACT_FIELDS = {"idempotent", "effect_on_err", "repeatable_on_partial", "compensates", "compensation_signs",
                    "state_check_for", "resolve_state", "environmental_errors", "doc", "requires_established",
                    "observation", "idempotency_key", "binds", "attests", "operation_field", "verifies"}


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
    #: A consequential action: every call names the hypotheses it relies on, and each is established.
    requires_established: bool = False
    #: The operation only reads its source and changes nothing: a ``parallel`` graph may call it concurrently.
    observation: bool = False
    #: The provider deduplicates requests carrying one key: the argument that carries it and how long the
    #: provider keeps a key (``{"field": name, "retention_s": seconds}``). The gateway issues the key.
    idempotency_key: Mapping[str, Any] | None = None
    #: A state check's typed binding to the operation it checks: which of its own arguments (``request``)
    #: and which fields of its answer (``answer``) name the same resource as which argument of the operation.
    binds: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: What a bound answer of this state check establishes: that the state exists (``state``) or that the
    #: checked operation itself took effect (``operation``) — by the operation's idempotency key echoed in
    #: ``operation_field``, or, without one, by the operator's declaration that the state is that operation's.
    attests: str = "state"
    operation_field: str | None = None
    #: A hypothesis check's typed binding to the claim it checks (review F1): which of its request arguments
    #: and which fields of its answer name the claim's subject, its scope (or the one scope the service answers
    #: for, ``value``) and each condition. Without it no answer of this tool decides a hypothesis.
    verifies: Mapping[str, Any] | None = None

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
                "event_fields": list(self.event_fields), "doc": self.doc,
                # Present only when declared, so contracts without it keep their identity.
                **({"requires_established": True} if self.requires_established else {}),
                **({"observation": True} if self.observation else {}),
                **({"idempotency_key": dict(self.idempotency_key)} if self.idempotency_key else {}),
                **({"binds": {key: dict(value) for key, value in self.binds.items()}, "attests": self.attests,
                    "operation_field": self.operation_field} if self.state_check_for is not None else {}),
                **({"verifies": copy.deepcopy(dict(self.verifies))} if self.verifies is not None else {})}

    @property
    def contract_ref(self) -> str:
        return digest(self.canonical())

    def event_fields_of(self, transport: str, op_result: str, effect: str, op_err: str | None,
                        payload: Any) -> dict[str, Any]:
        """The typed fields of the reactive event an answer of this operation raises when it fails.

        The answer's class and effect, and the payload fields the contract
        declares as event fields when they are scalars.
        """
        fields = {"tool": self.name, "transport": transport, "op_result": op_result, "effect": effect}
        if op_err is not None:
            fields["op_err"] = op_err
        if isinstance(payload, dict):
            for name in self.event_fields:
                if name in payload and isinstance(payload[name], (str, int, float, bool)):
                    fields[name] = payload[name]
        return fields


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


def _sides(value: Any, detail: str, *, literal: bool = False) -> dict[str, str]:
    """One binding: the request argument and/or the answer field naming a part of the claim (or, for a scope,
    the one ``value`` the service answers for)."""
    sides = {"request", "answer"} | ({"value"} if literal else set())
    if (type(value) is not dict or not value or set(value) - sides or ("value" in value and len(value) > 1)
            or any(type(item) is not str or not item for item in value.values())):
        raise _fail(detail)
    return dict(sorted(value.items()))


def _parse_verifies(name: str, value: Any) -> dict[str, Any] | None:
    """A hypothesis check's binding to the claim: subject and scope always, each condition it can answer."""
    if value is None:
        return None
    detail = f"hypothesis check {name} binds the claim's subject, scope and conditions to its request or answer"
    if type(value) is not dict or not {"subject", "scope"} <= set(value) or set(value) - {"subject", "scope",
                                                                                           "conditions"}:
        raise _fail(detail)
    conditions = value.get("conditions", {})
    if type(conditions) is not dict or any(type(key) is not str or not key for key in conditions):
        raise _fail(detail)
    return {"subject": _sides(value["subject"], detail), "scope": _sides(value["scope"], detail, literal=True),
            "conditions": {key: _sides(item, detail) for key, item in sorted(conditions.items())}}


def _parse_semantics(name: str, value: Any) -> dict[str, Any]:
    """The documented answer semantics of one tool (its ``contract`` object)."""
    contract = _mapping(value, f"{name} contract", required=set(), optional=_CONTRACT_FIELDS)
    effect_on_err = contract.get("effect_on_err", {})
    if type(effect_on_err) is not dict or any(type(k) is not str or v not in EFFECTS for k, v in effect_on_err.items()):
        raise _fail(f"tool {name} maps refusal codes to effect classes")
    resolve_state = contract.get("resolve_state", {})
    if type(resolve_state) is not dict or any(v not in {"applied", "none"} for v in resolve_state.values()):
        raise _fail(f"tool {name} resolves uncertainty only to applied or none")
    for flag in ("idempotent", "repeatable_on_partial", "requires_established", "observation"):
        if type(contract.get(flag, False)) is not bool:
            raise _fail(f"tool {name} {flag} is a boolean")
    signs = contract.get("compensation_signs", {})
    if type(signs) is not dict:
        raise _fail(f"tool {name} compensation signs are an object")
    environmental = _names(contract.get("environmental_errors", []), f"tool {name} environmental refusal codes are names")
    if contract.get("observation", False) and (not contract.get("idempotent", False) or contract.get(
            "compensates") is not None or any(effect != "none" for effect in effect_on_err.values())):
        # Reading changes nothing: a lost answer is read again, and no refusal leaves anything to compensate.
        raise _fail(f"tool {name} is an observation: idempotent, no refusal leaves an effect, it compensates nothing")
    checks = contract.get("state_check_for")
    binds = contract.get("binds", {})
    if (type(binds) is not dict or set(binds) - {"request", "answer"}
            or any(type(mapping) is not dict or any(type(k) is not str or type(v) is not str
                                                    for k, v in mapping.items()) for mapping in binds.values())):
        raise _fail(f"tool {name} binds its request arguments and answer fields to the checked operation's")
    if checks is not None and not any(binds.values()):
        raise _fail(f"state check {name} binds the resource it reads to the operation it checks")
    if checks is None and (binds or "attests" in contract or "operation_field" in contract):
        raise _fail(f"tool {name} binds, attests and echoes an operation only as a state check")
    attests = contract.get("attests", "state")
    if attests not in ("state", "operation"):
        raise _fail(f"state check {name} attests a state or the operation itself")
    operation_field = contract.get("operation_field")
    if operation_field is not None and (attests != "operation" or type(operation_field) is not str):
        raise _fail(f"state check {name} echoes the operation's key only when it attests the operation")
    key = contract.get("idempotency_key")
    if key is not None and (type(key) is not dict or set(key) != {"field", "retention_s"}
                            or type(key["field"]) is not str or _NAME_RE.fullmatch(key["field"]) is None
                            or type(key["retention_s"]) is not int or key["retention_s"] < 1):
        raise _fail(f"tool {name} idempotency key names its argument and the provider's retention in seconds")
    return {"idempotent": contract.get("idempotent", False), "effect_on_err": effect_on_err,
            "idempotency_key": None if key is None else dict(key),
            "binds": {part: dict(mapping) for part, mapping in sorted(binds.items())}, "attests": attests,
            "operation_field": operation_field,
            "repeatable_on_partial": contract.get("repeatable_on_partial", False),
            "compensates": contract.get("compensates"), "compensation_signs": signs,
            "state_check_for": contract.get("state_check_for"), "resolve_state": resolve_state,
            "environmental_errors": tuple(sorted(set(environmental))), "doc": str(contract.get("doc", "")),
            "requires_established": contract.get("requires_established", False),
            "observation": contract.get("observation", False),
            "verifies": _parse_verifies(name, contract.get("verifies"))}


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
    if root["schema_version"] == TOOL_CONFIGURATION_V1:
        raise _fail("tool configuration v1 predates bound state checks and unknown undescribed refusals: "
                    "declare v2 and migrate the memory owner with 'synapse memory reassess'")
    if root["schema_version"] != TOOL_CONFIGURATION_V2:
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
