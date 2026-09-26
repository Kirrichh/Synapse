"""Action patterns of learned habits: extraction, binding and execution.

A learned body is the typed step sequence its basis episodes actually executed
(criterion 4 holds by construction): calls through the gateway, each with the
result class observed at that position, then the observed final class. The
court never invents a step it did not see.

The binding rule maps an event to call arguments and is part of the frozen
identity (spec part 1 §7.3). Each argument is bound to a field of an earlier
call's successful answer (a result reference, D2), to a constant, to one typed
field of the reactive event, or to one argument of the failed action, in that
order of evidence; a call that repeats the failed operation is ``$same`` and
declares itself a retry of that operation, which the gateway validates before
any effect. An argument no single rule explains in every basis episode makes
the birth impossible: the court does not guess, and an argument that only
repeats a value known in advance (a driver's literal) is never credited as a
dependency.

Execution binds every argument that does not wait for an answer before the
first effect, so missing event information stops the body before it acts; a
result reference is resolved from the recorded answer when its call comes, and
an unavailable value stops the body before the dependent call. The executor's
``detail`` says why a body stopped. The court re-derives a fast path's outcome
with this same executor over the recorded answers.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from synapse.memory_points import ActionPorts

from ..records import canonical
from .dependencies import DependencyUnavailable, derive, echoed, resolve

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


def _event_field(key, calls, basis) -> str | None:
    fields = sorted(set.intersection(*(set(item["fields"]) for item in basis)), key=lambda name: (name != key, name))
    return next((name for name in fields if all(_same_value(call["args"][key], item["fields"][name])
                                                for call, item in zip(calls, basis))), None)


def _failed_arg(key, calls, basis) -> str | None:
    names = sorted(set.intersection(*(set(item["failed_args"]) for item in basis)), key=lambda name: (name != key, name))
    return next((name for name in names if all(_same_value(call["args"][key], item["failed_args"][name])
                                               for call, item in zip(calls, basis))), None)


def _rule(index, key, calls, basis) -> dict[str, Any]:
    """The one rule that explains argument ``key`` of call ``index`` in every basis episode."""
    values = [call["args"][key] for call in calls]
    origins = [(item.get("origins") or [{}] * (index + 1))[index].get(key) for item in basis]
    result = derive(values, origins)
    if result is not None:
        return {"result": result}
    if all(_same_value(value, values[0]) for value in values):
        return {"const": values[0]}
    field = _event_field(key, calls, basis)
    if field is not None:
        return {"event_field": field}
    failed = _failed_arg(key, calls, basis)
    if failed is not None:
        return {"failed_arg": failed}
    if echoed(origins):
        raise BindingUnavailable(f"argument {key!r} of call {index} repeats a value known before its producer "
                                 f"answered (a literal supplied in advance is not a dependency)")
    raise BindingUnavailable(f"argument {key!r} of call {index} is not derivable from the event")


def derive_binding(basis: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The binding rule that explains every basis episode's call arguments.

    Each basis entry holds ``calls`` (the tool and arguments of each call in
    order), their ``origins`` (see ``dependencies.origins``), the reactive
    event's typed ``fields`` and the ``failed_args``.
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
        binding.append({"step": index, "args": {key: _rule(index, key, calls, basis) for key in keys}})
    return binding


def check_binding(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]]) -> None:
    """A binding's references name an earlier call expected to succeed; nothing refers forward or to a refusal."""
    calls = [step for step in pattern if step["step"] == "call"]
    if [item["step"] for item in binding] != list(range(len(calls))):
        raise BindingUnavailable("a binding names every call of its body in order")
    for item in binding:
        for key, source in sorted((item.get("args") or {}).items()):
            if "result" not in source:
                continue
            step = source["result"]["step"]
            if not (type(step) is int and 0 <= step < item["step"]):
                raise BindingUnavailable(f"argument {key!r} of call {item['step']} refers to a later or unknown call")
            if calls[step]["result_class"] != "ok" or calls[step]["tool"] == SAME:
                raise BindingUnavailable(f"argument {key!r} of call {item['step']} refers to an answer that is "
                                         f"not a success of its own action")


def event_fields_read(binding: Sequence[Mapping[str, Any]]) -> list[str]:
    """The reactive event's fields a binding reads."""
    return sorted({source["event_field"] for item in binding for source in (item.get("args") or {}).values()
                   if "event_field" in source})


def bind_arguments(rule: Mapping[str, Any], event: Mapping[str, Any],
                   views: Sequence[Mapping[str, Any]] | None) -> tuple[dict[str, Any], int | None, str | None]:
    """Arguments, declared retry and tool override of one call for one event.

    With ``views`` ``None`` the result references are left unbound: the
    precheck before the first effect.
    """
    failed = event["failed_action"]
    if rule.get("failed_action"):
        return dict(failed["args"]), failed["op"], failed["tool"]
    fields = event.get("fields") or {}
    arguments: dict[str, Any] = {}
    for key, source in sorted(rule["args"].items()):
        if "result" in source:
            if views is not None:
                arguments[key] = resolve(source["result"], views)
        elif "const" in source:
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


def _dependents(binding, index, view) -> dict[str, Any]:
    """The later calls that read the answer of call ``index``, and why that answer cannot feed them."""
    dependents = sorted({item["step"] for item in binding for source in (item.get("args") or {}).values()
                         if source.get("result", {}).get("step") == index})
    if not dependents:
        return {}
    cause = "producer_uncertain" if view.get("effect") == "unknown" else "producer_failed"
    return {"cause": cause, "dependents": dependents}


def _class(view: Mapping[str, Any]) -> str:
    return "ok" if view["ok"] else (view.get("op_err") or view.get("op_result"))


def _outcome(views, detail) -> str:
    if not views:
        return "unclear"
    last = views[-1]
    if detail is not None and detail["reason"] == "dependency_unavailable":
        return "uncertain" if last.get("effect") == "unknown" else "failure"
    if last["ok"]:
        return "success"
    return "uncertain" if last.get("effect") == "unknown" else "failure"


def execute(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]],
            event: Mapping[str, Any], ports: ActionPorts) -> dict[str, Any]:
    """Run a frozen body through the recorded action path; the local outcome is its last answer.

    Returns ``outcome``, ``steps`` and ``detail`` — why the body stopped before
    its end, or ``None``.
    """
    calls = [step for step in pattern if step["step"] == "call"]
    rules = {item["step"]: item for item in binding}
    try:
        for index in range(len(calls)):
            bind_arguments(rules[index], event, None)
    except BindingUnavailable as exc:
        return {"outcome": "failure", "steps": 0, "detail": {"reason": "binding_not_applicable", "message": str(exc)}}
    views: list[dict[str, Any]] = []
    detail = None
    for index, step in enumerate(calls):
        try:
            arguments, retry_of, tool = bind_arguments(rules[index], event, views)
        except DependencyUnavailable as exc:
            detail = {"reason": "dependency_unavailable", "step": index, "cause": exc.cause, "message": str(exc)}
            break
        view = ports.invoke(tool if tool is not None else step["tool"], arguments, retry_of)
        views.append(view)
        observed = _class(view)
        if observed != step["result_class"] and index + 1 < len(calls):
            # The world answered differently than the basis; the body does not improvise.
            detail = {"reason": "diverged_from_basis", "step": index, "observed": observed,
                      "expected": step["result_class"], **_dependents(binding, index, view)}
            break
    return {"outcome": _outcome(views, detail), "steps": len(views), "detail": detail}


def run_recorded(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]], event: Mapping[str, Any],
                 answer) -> dict[str, Any]:
    """The same executor over already recorded answers: ``answer(tool, arguments)`` returns each call's view."""
    return execute(pattern, binding, event, ActionPorts(invoke=lambda tool, arguments, retry_of=None:
                                                        answer(tool, arguments), wait=lambda _: None))
