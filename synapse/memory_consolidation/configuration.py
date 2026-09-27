"""The operator's memory configuration, frozen before a durable run starts.

One strict JSON document binds a memory owner's runs to everything the court
and the gateway may use: the admitted tools with their contracts and the
provenance graph, the court's declared policy version, the optional advisor
and similarity scorer (both ``reason`` tools recorded by the gateway) and the
default element of the owner's cases. Schema v2 adds the knowledge policy
(refinement §15): the embedding model of the semantic search channel (a
``reason`` tool recorded by the gateway, so the choice of model never changes
what memory may do), the currency rule of each property and the search
budget. How a run reads the memory (an ordinary
learning session or an exam in mode A, B or C) belongs to the run, not to
this policy: the three modes run on the same memory.

The agent never changes this document; its digest is recorded in every run
and in every report.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .records import digest
from .tools.contracts import ToolConfiguration, parse_tool_configuration
from .policy import DECISION_RULES, policy_identity, resolve_parameters

MEMORY_CONFIGURATION_V1 = "synapse.memory.configuration/v1"
MEMORY_CONFIGURATION_V2 = "synapse.memory.configuration/v2"
_FRESHNESS = ("state", "bounded", "event")
_MAX_BUDGET = 100
MEMORY_BINDING_V1 = "synapse.memory.binding/v1"
_ELEMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_MAX_BYTES = 4 * 1024 * 1024


class MemoryConfigurationViolation(ValueError):
    """The memory configuration is outside its declared contract."""


@dataclass(frozen=True)
class Component:
    """A ``reason`` tool the court or the runtime consults, with its declared version."""

    tool: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.tool, "version": self.version}


@dataclass(frozen=True)
class KnowledgePolicy:
    """How semantic knowledge is searched and how long each property stays current."""

    embedder: Component | None
    properties: Mapping[str, Mapping[str, Any]]
    budget: int


@dataclass(frozen=True)
class MemoryConfiguration:
    tools: ToolConfiguration
    decision_rule: str
    parameters: Mapping[str, Any]
    advisor: Component | None
    scorer: Component | None
    element: str
    raw: Mapping[str, Any]
    knowledge: KnowledgePolicy | None = None

    @property
    def configuration_sha256(self) -> str:
        return digest(dict(self.raw))

    @property
    def policy(self) -> dict[str, Any]:
        return policy_identity(self.parameters, self.decision_rule)

    @property
    def components(self) -> dict[str, Any]:
        embedder = None if self.knowledge is None else self.knowledge.embedder
        return {"judge": None if self.advisor is None else self.advisor.to_dict(),
                "similarity_scorer": None if self.scorer is None else self.scorer.to_dict(),
                **({} if embedder is None else {"embedder": embedder.to_dict()})}

    @property
    def tool_binding_sha256(self) -> str:
        """Identity of the admitted tool contracts a learned behavior is bound to."""
        return digest({name: contract.contract_ref for name, contract in sorted(self.tools.tools.items())})


def _fail(detail: str) -> MemoryConfigurationViolation:
    return MemoryConfigurationViolation(f"memory configuration: {detail}")


def _component(value: Any, name: str, tools: ToolConfiguration) -> Component | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"tool", "version"}:
        raise _fail(f"{name} names its reason tool and version")
    tool, version = value["tool"], value["version"]
    if type(tool) is not str or tool not in tools.tools or tools.tools[tool].role != "reason":
        raise _fail(f"{name} must be an admitted reason tool")
    if type(version) is not str or not 1 <= len(version) <= 128:
        raise _fail(f"{name} version is a bounded name")
    return Component(tool, version)


def _knowledge(value: Any, tools: ToolConfiguration) -> KnowledgePolicy | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"embedder", "properties", "budget"}:
        raise _fail("knowledge names its embedder, its properties and its search budget")
    properties = value["properties"]
    if type(properties) is not dict:
        raise _fail("knowledge properties are an object")
    rules = {}
    for name, rule in sorted(properties.items()):
        if type(rule) is not dict or rule.get("freshness") not in _FRESHNESS:
            raise _fail(f"property {name!r} declares its freshness: state, bounded or event")
        if rule["freshness"] == "bounded":
            days = rule.get("ttl_days")
            if set(rule) != {"freshness", "ttl_days"} or type(days) is not int or days < 1:
                raise _fail(f"bounded property {name!r} declares a positive number of days")
        elif set(rule) != {"freshness"}:
            raise _fail(f"property {name!r} of freshness {rule['freshness']} declares nothing else")
        rules[name] = dict(rule)
    budget = value["budget"]
    if type(budget) is not int or not 1 <= budget <= _MAX_BUDGET:
        raise _fail("the knowledge search budget is between 1 and 100 candidates")
    return KnowledgePolicy(embedder=_component(value["embedder"], "embedder", tools), properties=rules,
                           budget=budget)


def parse_memory_configuration(value: Any) -> MemoryConfiguration:
    required = {"schema_version", "tools", "court", "advisor", "scorer", "element"}
    version = value.get("schema_version") if type(value) is dict else None
    if version == MEMORY_CONFIGURATION_V2:
        required = required | {"knowledge"}
    if type(value) is not dict or set(value) != required:
        raise _fail("unknown shape")
    if version not in {MEMORY_CONFIGURATION_V1, MEMORY_CONFIGURATION_V2}:
        raise _fail("unsupported schema version")
    tools = parse_tool_configuration(value["tools"])
    court = value["court"]
    if type(court) is not dict or set(court) != {"decision_rule", "parameters"}:
        raise _fail("court declares its decision rule and parameter overrides")
    if court["decision_rule"] not in DECISION_RULES:
        raise _fail("court decision rule is threshold or sprt")
    if type(court["parameters"]) is not dict:
        raise _fail("court parameter overrides are an object")
    parameters = resolve_parameters(court["parameters"])
    element = value["element"]
    if type(element) is not str or _ELEMENT_RE.fullmatch(element) is None:
        raise _fail("element is a bounded identifier")
    return MemoryConfiguration(tools=tools, decision_rule=court["decision_rule"], parameters=parameters,
                               advisor=_component(value["advisor"], "advisor", tools),
                               scorer=_component(value["scorer"], "scorer", tools),
                               element=element, raw=value, knowledge=_knowledge(value.get("knowledge"), tools))


def read_memory_configuration(path: Path) -> MemoryConfiguration:
    raw = Path(path).read_bytes()
    if len(raw) > _MAX_BYTES:
        raise _fail("configuration exceeds its byte budget")

    def reject(token: str):
        raise _fail(f"non-standard JSON constant {token}")

    return parse_memory_configuration(json.loads(raw, parse_constant=reject))
