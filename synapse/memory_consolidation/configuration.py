"""The operator's memory configuration, frozen before a durable run starts.

One strict JSON document binds a memory owner's runs to everything the court
and the gateway may use: the admitted tools with their contracts and the
provenance graph, the court's declared policy version, the optional advisor
and similarity scorer (both ``reason`` tools recorded by the gateway), the
default element of the owner's cases and the experiment mode of learned habits:

* ``load`` — learned habits enter the runtime by the ordinary admission;
* ``off`` — accumulated learned habits are not loaded (experiment mode A);
* ``slow_only`` — the same pinned snapshot is read, every learned trigger is
  slow-only (experiment mode C).

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
MEMORY_BINDING_V1 = "synapse.memory.binding/v1"
LEARNED_HABIT_MODES = ("load", "off", "slow_only")
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
class MemoryConfiguration:
    tools: ToolConfiguration
    decision_rule: str
    parameters: Mapping[str, Any]
    advisor: Component | None
    scorer: Component | None
    learned_habits: str
    element: str
    raw: Mapping[str, Any]

    @property
    def configuration_sha256(self) -> str:
        return digest(dict(self.raw))

    @property
    def policy(self) -> dict[str, Any]:
        return policy_identity(self.parameters, self.decision_rule)

    @property
    def components(self) -> dict[str, Any]:
        return {"judge": None if self.advisor is None else self.advisor.to_dict(),
                "similarity_scorer": None if self.scorer is None else self.scorer.to_dict()}

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


def parse_memory_configuration(value: Any) -> MemoryConfiguration:
    required = {"schema_version", "tools", "court", "advisor", "scorer", "learned_habits", "element"}
    if type(value) is not dict or set(value) != required:
        raise _fail("unknown shape")
    if value["schema_version"] != MEMORY_CONFIGURATION_V1:
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
    if value["learned_habits"] not in LEARNED_HABIT_MODES:
        raise _fail("learned habits mode is load, off or slow_only")
    element = value["element"]
    if type(element) is not str or _ELEMENT_RE.fullmatch(element) is None:
        raise _fail("element is a bounded identifier")
    return MemoryConfiguration(tools=tools, decision_rule=court["decision_rule"], parameters=parameters,
                               advisor=_component(value["advisor"], "advisor", tools),
                               scorer=_component(value["scorer"], "scorer", tools),
                               learned_habits=value["learned_habits"], element=element, raw=value)


def read_memory_configuration(path: Path) -> MemoryConfiguration:
    raw = Path(path).read_bytes()
    if len(raw) > _MAX_BYTES:
        raise _fail("configuration exceeds its byte budget")

    def reject(token: str):
        raise _fail(f"non-standard JSON constant {token}")

    return parse_memory_configuration(json.loads(raw, parse_constant=reject))
