"""Formation contour: the task contract, its segment markers and their addressing.

A program fixes its task before execution with ``task_plan(contract)``. The
contract states each segment's intent, its element part, the external evidence
that anchors it and its required result: the operation to execute, the refusal
a probe expects, or the alternatives it allows. Formation turns it into
content-addressed task markers; nobody changes them afterwards and the verdict
is a separate court record (spec part 1 §5).

Addressing is deterministic working memory: a segment is active while the
program is inside the ``context`` block labelled with its segment name, so every
memory-significant event carries the innermost active segment's marker or is
explicitly off the plan. One execution of a segment (one entry into its block)
is one operation scope and one case.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from . import records
from .configuration import MemoryConfiguration
from .records import digest
from .tools.episodes import REQUIREMENT_KINDS

TASK_CONTRACT_V1 = "synapse.memory.task-contract/v1"
TASK_PLAN_V1 = "synapse.memory.task-plan/v1"
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_MAX_SEGMENTS = 64
_MAX_TEXT = 2048
#: Events whose subsystem fields name the active task and segment.
BOUND_EVENT_TYPES = frozenset({"external_action", "external_error", "habit_activated", "habit_near_miss",
                               "habit_miss", "habit_suppressed", "habit_execution_failed", "slow_path_used"})


class TaskContractViolation(ValueError):
    """A task contract is outside its declared shape."""


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not empty and not value.strip()):
        raise TaskContractViolation(f"task contract {name} is bounded text")
    return value


def _tool(value: Any, name: str, configuration: MemoryConfiguration, *, role: str = "action") -> str:
    contract = configuration.tools.tools.get(value) if type(value) is str else None
    if contract is None or contract.role != role:
        raise TaskContractViolation(f"task contract {name} names an admitted {role} tool")
    return value


def _anchor(value: Any, configuration: MemoryConfiguration) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"tool", "fields"} or type(value["fields"]) is not dict:
        raise TaskContractViolation("segment anchor names a tool and the fields its result must carry")
    return {"type": "recorded_external_result", "tool": _tool(value["tool"], "anchor", configuration),
            "fields": value["fields"]}


def _requirement(value: Any, configuration: MemoryConfiguration) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"kind", "tool", "admissible_err", "allowed_alternatives"}:
        raise TaskContractViolation("segment requirement has an unknown shape")
    if value["kind"] not in REQUIREMENT_KINDS:
        raise TaskContractViolation("segment requirement kind is execute, probe_refusal or attempt_report")
    tool = None if value["tool"] is None else _tool(value["tool"], "requirement", configuration)
    admissible = value["admissible_err"]
    if type(admissible) is not list or any(type(code) is not str or not code for code in admissible):
        raise TaskContractViolation("admissible refusals are named codes")
    if admissible and value["kind"] == "execute":
        raise TaskContractViolation("an executed requirement admits no refusal")
    if type(value["allowed_alternatives"]) is not list:
        raise TaskContractViolation("allowed alternatives are tools")
    alternatives = [_tool(entry, "alternative", configuration) for entry in value["allowed_alternatives"]]
    return {"kind": value["kind"], "tool": tool, "admissible_err": sorted(set(admissible)),
            "allowed_alternatives": sorted(set(alternatives))}


def _segment(item: Any, names: set[str], configuration: MemoryConfiguration) -> dict[str, Any]:
    if type(item) is not dict or not {"segment", "intent", "element_part", "requirement"} <= set(item) or set(
            item) - {"segment", "intent", "element_part", "scope", "anchor", "requirement"}:
        raise TaskContractViolation("task segment has an unknown shape")
    name = item["segment"]
    if type(name) is not str or _NAME_RE.fullmatch(name) is None or name in names:
        raise TaskContractViolation("segment names are unique bounded names")
    part = item["element_part"]
    if type(part) is not str or _PART_RE.fullmatch(part) is None:
        raise TaskContractViolation("segment element part is a bounded identifier")
    scope = item.get("scope", {"when": "", "not_when": ""})
    if type(scope) is not dict or set(scope) != {"when", "not_when"}:
        raise TaskContractViolation("segment scope states when and not_when")
    return {"segment": name, "intent": _text(item["intent"], "intent"), "element_part": part,
            "scope": {"when": _text(scope["when"], "scope", empty=True),
                      "not_when": _text(scope["not_when"], "scope", empty=True)},
            "anchor": _anchor(item.get("anchor"), configuration),
            "requirement": _requirement(item["requirement"], configuration)}


def parse_task_contract(value: Any, configuration: MemoryConfiguration) -> dict[str, Any]:
    """The validated contract, in its canonical form."""
    if type(value) is not dict or not {"task_id", "goal", "segments"} <= set(value) or set(value) - {
            "task_id", "goal", "segments", "element"}:
        raise TaskContractViolation("task contract has an unknown shape")
    if type(value["task_id"]) is not str or _NAME_RE.fullmatch(value["task_id"]) is None:
        raise TaskContractViolation("task id is a bounded name")
    element = value.get("element", configuration.element)
    if type(element) is not str or _PART_RE.fullmatch(element) is None:
        raise TaskContractViolation("task element is a bounded identifier")
    segments = value["segments"]
    if type(segments) is not list or not 1 <= len(segments) <= _MAX_SEGMENTS:
        raise TaskContractViolation("task contract lists its segments")
    normalized: list[dict[str, Any]] = []
    for item in segments:
        normalized.append(_segment(item, {entry["segment"] for entry in normalized}, configuration))
    return {"schema_version": TASK_CONTRACT_V1, "task_id": value["task_id"],
            "goal": _text(value["goal"], "goal"), "element": element, "segments": normalized}


def plan_task(value: Any, configuration: MemoryConfiguration) -> dict[str, Any]:
    """Formation: the fixed contract and one task marker per segment."""
    contract = parse_task_contract(value, configuration)
    reference = "sha256:" + digest(contract)
    markers = [records.make("task_marker", task_id=contract["task_id"], task_contract_ref=reference,
                            segment_ref=f"plan.{item['segment']}", element_part=item["element_part"],
                            intent=item["intent"], scope=item["scope"], external_anchor=item["anchor"],
                            requirement=item["requirement"])
               for item in contract["segments"]]
    return {"schema_version": TASK_PLAN_V1, "task_id": contract["task_id"], "task_contract_ref": reference,
            "element": contract["element"], "goal": contract["goal"], "markers": markers}


def verify_plan(plan: Any, contract: Any, configuration: MemoryConfiguration) -> dict[str, Any]:
    """A recorded plan, only if formation of its recorded contract yields it."""
    if plan != plan_task(contract, configuration):
        raise TaskContractViolation("recorded plan differs from the formation of its contract")
    return plan


def segment_of(plan: Mapping[str, Any] | None, contexts: list[Mapping[str, Any]]) -> tuple[dict | None, str | None]:
    """The innermost active segment's marker and the entry of its block."""
    if not plan:
        return None, None
    by_segment = {marker["segment_ref"]: marker for marker in plan["markers"]}
    for entry in reversed(contexts):
        marker = by_segment.get(f"plan.{entry['label']}")
        if marker is not None:
            return marker, entry["entered"]
    return None, None


def bind_event(event: Mapping[str, Any], working: Mapping[str, Any]) -> dict[str, Any]:
    """Subsystem fields of one event (the durable event adapter point).

    A frame-less external action opens the operation scope of its segment
    execution, or of the off-plan line of its task.
    """
    if event.get("type") not in BOUND_EVENT_TYPES:
        return {}
    plan = working.get("task")
    marker, entered = segment_of(plan, list(working.get("contexts") or ()))
    fields: dict[str, Any] = {"task_id": None if not plan else plan["task_id"]}
    if marker is not None:
        fields["segment_marker_id"] = marker["id"]
    else:
        fields["off_plan"] = True
    if event.get("type") == "external_action" and event.get("episode") is None:
        line = f"seg|{marker['id']}|{entered}" if marker is not None else f"off|{fields['task_id']}"
        fields["episode"] = line
        fields["op_scope"] = line
    return fields
