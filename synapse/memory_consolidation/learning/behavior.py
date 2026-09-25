"""Action patterns of learned habits: extraction, binding and execution.

A learned body is the typed step sequence its basis episodes actually executed
(criterion 4 holds by construction): calls through the gateway, each with the
result class observed at that position, then the observed final class. The
court never invents a step it did not see.

The binding rule maps an event to call arguments and is part of the frozen
identity (spec part 1 §7.3). Each argument is bound to a constant, to one typed
field of the reactive event, or to one argument of the failed action; a call
that repeats the failed operation is ``$same`` and declares itself a retry of
that operation, which the gateway validates before any effect. An argument no
single rule explains in every basis episode makes the birth impossible: the
court does not guess. Arguments that vary with earlier results (D2) or need
wider generalization (D1) are outside this binding by design.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from synapse.memory_points import ActionPorts

from ..records import canonical

SAME = "$same"


class BindingUnavailable(ValueError):
    """No single declared rule explains an argument in every basis episode."""


def typed_steps(attempts: Sequence[Mapping[str, Any]], failed: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Typed steps of one episode (actions only): tool identity and observed class."""
    steps: list[dict[str, Any]] = []
    actions = [item for item in attempts if item["role"] == "action"]
    for item in actions:
        same = failed is not None and item["tool"] == failed["tool"] and item["op"] == failed["op"]
        steps.append({"step": "call", "tool": SAME if same else item["tool"],
                      "result_class": "ok" if item["op_result"] == "ok" else (item["op_err"] or item["op_result"])})
    if actions:
        steps.append({"step": "observe", "result_class": steps[-1]["result_class"]})
    return steps


def step_similarity(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> float:
    """Deterministic similarity of step sequences by step type and tool, without arguments."""
    a = [(item["step"], item.get("tool")) for item in left]
    b = [(item["step"], item.get("tool")) for item in right]
    if not a and not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, token in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (token != other)))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def _same_value(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def derive_binding(basis: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The binding rule that explains every basis episode's call arguments.

    Each basis entry holds ``calls`` (the tool and arguments of each call in
    order), the reactive event's typed ``fields`` and the ``failed_args``.
    """
    if not basis:
        raise BindingUnavailable("a binding needs basis episodes")
    length = len(basis[0]["calls"])
    if any(len(item["calls"]) != length for item in basis):
        raise BindingUnavailable("basis episodes differ in their call sequence")
    binding: list[dict[str, Any]] = []
    for index in range(length):
        calls = [item["calls"][index] for item in basis]
        if calls[0]["tool"] == SAME:
            if any(call["tool"] != SAME for call in calls):
                raise BindingUnavailable("basis episodes differ in their call sequence")
            binding.append({"step": index, "failed_action": True})
            continue
        keys = sorted(calls[0]["args"])
        if any(sorted(call["args"]) != keys for call in calls):
            raise BindingUnavailable(f"call {index} has different argument names across basis episodes")
        rules: dict[str, Any] = {}
        for key in keys:
            values = [call["args"][key] for call in calls]
            if all(_same_value(value, values[0]) for value in values):
                rules[key] = {"const": values[0]}
                continue
            fields = sorted(set.intersection(*(set(item["fields"]) for item in basis)), key=lambda name: (name != key, name))
            field = next((name for name in fields if all(_same_value(call["args"][key], item["fields"][name])
                                                          for call, item in zip(calls, basis))), None)
            if field is not None:
                rules[key] = {"event_field": field}
                continue
            failed_keys = sorted(set.intersection(*(set(item["failed_args"]) for item in basis)),
                                 key=lambda name: (name != key, name))
            failed = next((name for name in failed_keys if all(_same_value(call["args"][key], item["failed_args"][name])
                                                                for call, item in zip(calls, basis))), None)
            if failed is not None:
                rules[key] = {"failed_arg": failed}
                continue
            raise BindingUnavailable(f"argument {key!r} of call {index} is not derivable from the event")
        binding.append({"step": index, "args": rules})
    return binding


def bind_arguments(rule: Mapping[str, Any], event: Mapping[str, Any]) -> tuple[dict[str, Any], int | None, str | None]:
    """Arguments, declared retry and tool override of one call for one event."""
    failed = event["failed_action"]
    if rule.get("failed_action"):
        return dict(failed["args"]), failed["op"], failed["tool"]
    fields = event.get("fields") or {}
    arguments: dict[str, Any] = {}
    for key, source in sorted(rule["args"].items()):
        if "const" in source:
            arguments[key] = source["const"]
        elif "event_field" in source:
            if source["event_field"] not in fields:
                raise BindingUnavailable(f"event lacks field {source['event_field']!r}")
            arguments[key] = fields[source["event_field"]]
        else:
            if source["failed_arg"] not in failed["args"]:
                raise BindingUnavailable(f"failed action lacks argument {source['failed_arg']!r}")
            arguments[key] = failed["args"][source["failed_arg"]]
    return arguments, None, None


def execute(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]],
            event: Mapping[str, Any], ports: ActionPorts) -> dict[str, Any]:
    """Run a frozen body through the recorded action path; the local outcome is its last answer."""
    calls = [step for step in pattern if step["step"] == "call"]
    rules = {item["step"]: item for item in binding}
    views: list[dict[str, Any]] = []
    for index, step in enumerate(calls):
        try:
            arguments, retry_of, tool = bind_arguments(rules[index], event)
        except BindingUnavailable:
            return {"outcome": "failure", "reason": "binding_not_applicable", "steps": len(views)}
        view = ports.invoke(tool if tool is not None else step["tool"], arguments, retry_of)
        views.append(view)
        observed = "ok" if view["ok"] else (view.get("op_err") or view.get("op_result"))
        if observed != step["result_class"] and index + 1 < len(calls):
            break  # The world answered differently than the basis; the body does not improvise.
    if not views:
        return {"outcome": "unclear", "steps": 0}
    last = views[-1]
    if last["ok"]:
        outcome = "success"
    elif last.get("effect") == "unknown":
        outcome = "uncertain"
    else:
        outcome = "failure"
    return {"outcome": outcome, "steps": len(views)}
