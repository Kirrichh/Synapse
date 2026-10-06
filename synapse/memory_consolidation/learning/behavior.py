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

At a step that failed otherwise than its basis a contingency may name parts
(refinement §14): each is tried once, in order, against the step's current
failure, until one recovers the step by repeating its operation and the body
resumes; a part's own impasses are asked of its own contingency. A step or a
part that left its effect unknown ends the attempts: nothing is chosen over an
unknown state.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from synapse.memory_points import ACTION_FAILURE_EVENT, ActionPorts

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


def rivals(left: Mapping[str, Any], right: Mapping[str, Any], action_same: float) -> bool:
    """Two frozen habits that promise one expected outcome by different actions."""
    return (left["expected_outcome"] == right["expected_outcome"]
            and step_similarity(left["action_pattern"], right["action_pattern"]) < action_same)


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


def contracts_of(steps: Sequence[Mapping[str, Any]], configuration) -> dict[str, str | None]:
    """The contract version of every tool the body calls itself (the failed action's own tool is decided by the
    gateway at each repeat): what the procedure was verified under (review §8.1)."""
    tools = sorted({step["tool"] for step in steps if step.get("step") == "call" and step.get("tool") not in (None, SAME)})
    return {name: (contract.contract_ref if (contract := configuration.tools.tools.get(name)) is not None else None)
            for name in tools}


def explain_binding(binding: Sequence[Mapping[str, Any]], basis: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Why every argument of the body is what it is, from the basis episodes (review §8.1, after Soar's
    explanation-based learning): a variable is a variable only because its values differed and each one is
    explained by the same source in every episode; a value that never changed stays a constant, unproven as a
    variable."""
    explained = []
    for item in binding:
        if item.get("failed_action"):
            explained.append({"step": item["step"], "argument": None, "rule": "failed_action",
                              "why": "repeats the failed operation itself, as the gateway admits it"})
            continue
        for key, source in sorted(item["args"].items()):
            values = [episode["calls"][item["step"]]["args"][key] for episode in basis]
            distinct = len({canonical(value) for value in values})
            rule = next(iter(source))
            why = {"result": "first appeared in the producer's answer in every episode, with values that differ",
                   "const": "the same value in every episode: a constant, never shown to vary",
                   "event_field": "equals the reactive event's field in every episode",
                   "failed_arg": "equals the failed action's argument in every episode"}[rule]
            explained.append({"step": item["step"], "argument": key, "rule": rule, "source": source[rule],
                              "episodes": len(values), "distinct_values": distinct, "why": why})
    return explained


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


def result_class(view: Mapping[str, Any]) -> str:
    """The class of one answer: ``ok``, or the service's refusal code, or the transport's result."""
    return "ok" if view["ok"] else (view.get("op_err") or view.get("op_result"))


def _outcome(last, detail) -> str:
    """The local truth of a body: its last answer, unless the body stopped short of a value it needed."""
    if last is None:
        return "unclear"
    if detail is not None and detail["reason"] in {"dependency_unavailable", "component_failed",
                                                   "component_uncertain"}:
        return "uncertain" if last.get("effect") == "unknown" else "failure"
    if last["ok"]:
        return "success"
    return "uncertain" if last.get("effect") == "unknown" else "failure"


def failure_event(tool: str, view: Mapping[str, Any], arguments: Mapping[str, Any],
                  event: Mapping[str, Any]) -> dict[str, Any]:
    """The reactive event one failed step of a body raises for a part that may recover it."""
    return {"type": ACTION_FAILURE_EVENT, "fields": dict(view.get("event_fields") or {}),
            "context_labels": list(event.get("context_labels") or ()),
            "failed_action": {"tool": tool, "op": view["op"], "args": dict(arguments)}}


class _Run:
    """One execution of a body: its answers by call, every answer received, the parts it called and the
    tools whose effects applied (a parent's first, then this body's own)."""

    def __init__(self, applied: Sequence[str] = ()) -> None:
        self.views: list[dict[str, Any]] = []
        self.last: dict[str, Any] | None = None
        self.parts: list[dict[str, Any]] = []
        self.detail: dict[str, Any] | None = None
        self.applied: list[str] = list(applied)

    def received(self, view: Mapping[str, Any]) -> None:
        self.last = dict(view)
        if view["ok"]:
            self.applied.append(view["tool"])


def _uncertain(view: Mapping[str, Any] | None) -> bool:
    return view is not None and view.get("effect") == "unknown"


def _serving(ports: ActionPorts, op) -> ActionPorts:
    """A part's ports: until its repeat of the failed operation succeeds, each of its other calls declares
    that operation as the one it serves (a part nested inside declares its own)."""
    settled = []

    def invoke(tool, arguments, retry_of=None, serves=None):
        if serves is None and retry_of is None and not settled:
            serves = op
        view = ports.invoke(tool, arguments, retry_of, serves)
        if retry_of == op and view["ok"]:
            settled.append(view["op"])
        return view
    return ActionPorts(invoke=invoke, wait=ports.wait)


def _part(decision, impasse, tool, ports, run: _Run) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Run the part a contingency chose for an impasse: its record, and its repeat of the failed operation."""
    part = decision["part"]
    before = len(run.applied)
    op = impasse["failure"]["failed_action"]["op"]
    sub = _body(part["pattern"], part["binding"], impasse["failure"], _serving(ports, op), (), (),
                part.get("contingency"), run.applied)
    if sub.last is not None:
        run.last = sub.last
    run.applied = sub.applied
    retry = next((item for item in reversed(sub.views) if item["op"] == op and item["tool"] == tool), None)
    record = {"habit_id": part["habit_id"], "at": impasse["at"], "on": impasse["on"],
              "outcome": _outcome(sub.last, sub.detail), "recovered": bool(retry is not None and retry["ok"]),
              "detail": sub.detail, "applied": sub.applied[before:], "parts": sub.parts}
    run.parts.append(record)
    return record, retry


def _impasse(index, step_class, view, views, failure, run: _Run, prior) -> dict[str, Any]:
    return {"at": index, "on": step_class, "view": view, "views": list(views), "failure": failure,
            "applied": list(run.applied),
            "tried": [{"part": item["habit_id"], "applied": list(item["applied"]), "outcome": item["outcome"]}
                      for item in prior]}


def _recover(impasse, tool, arguments, event, ports, contingency, run: _Run) -> tuple[dict | None, dict | None]:
    """Try the admissible parts for one impasse in order until one recovers the step (refinement §14).

    Each part is tried once, against the step's current failure; a part that
    repeated the operation without success leaves that answer as the step's
    new failure, and its applied effects count for the next one's conflicts.
    A part that left an effect unknown ends the attempts: nothing is chosen
    over an unknown state. Returns the recovering repeat (or ``None``) and why
    no part recovered.
    """
    refused = None
    while True:
        if any(item["outcome"] == "uncertain" for item in impasse["tried"]):
            return None, {"reason": "component_uncertain", "step": impasse["at"],
                          "components": [item["part"] for item in impasse["tried"]]}
        decision = contingency(impasse)
        if decision is None or "part" not in decision:
            refused = None if decision is None else decision["refused"]
            break
        record, retry = _part(decision, impasse, tool, ports, run)
        if record["recovered"]:
            return retry, None
        tried = [*impasse["tried"], {"part": record["habit_id"], "applied": record["applied"],
                                     "outcome": record["outcome"]}]
        view = retry if retry is not None else impasse["view"]
        failure = failure_event(tool, view, arguments, event) if retry is not None else impasse["failure"]
        run.views[impasse["at"]] = dict(view)
        impasse = {**impasse, "view": view, "views": list(run.views), "failure": failure,
                   "applied": list(run.applied), "tried": tried}
    if not impasse["tried"]:
        return None, None if refused is None else {"contingency": refused}
    return None, {"reason": "component_failed", "step": impasse["at"],
                  "components": [item["part"] for item in impasse["tried"]],
                  **({"contingency": refused} if refused is not None else {})}


def _body(pattern, binding, event, ports, prefix, prior, contingency, applied=()) -> _Run:
    calls = [step for step in pattern if step["step"] == "call"]
    rules = {item["step"]: item for item in binding}
    run = _Run(applied)
    run.parts = [dict(item) for item in prior]
    for item in prior:
        run.applied.extend(item["applied"])
    try:
        for index in range(len(calls)):
            bind_arguments(rules[index], event, None)
    except BindingUnavailable as exc:
        run.detail = {"reason": "binding_not_applicable", "message": str(exc)}
        return run
    for index, step in enumerate(calls):
        try:
            arguments, retry_of, tool = bind_arguments(rules[index], event, run.views)
        except DependencyUnavailable as exc:
            run.detail = {"reason": "dependency_unavailable", "step": index, "cause": exc.cause, "message": str(exc)}
            break
        tool = tool if tool is not None else step["tool"]
        # A continuation receives the answers the body already had; it never repeats their calls.
        view = prefix[index] if index < len(prefix) else ports.invoke(tool, arguments, retry_of)
        run.views.append(dict(view))
        run.received(view)
        observed = result_class(view)
        if observed == step["result_class"]:
            continue
        stopped = None
        if contingency is not None and not _uncertain(view):
            earlier = [item for item in prior if item["at"] == index] if index == len(prefix) - 1 else []
            impasse = _impasse(index, earlier[0]["on"] if earlier else observed, view, run.views,
                               failure_event(tool, view, arguments, event), run, earlier)
            retry, stopped = _recover(impasse, tool, arguments, event, ports, contingency, run)
            if retry is not None:
                run.views[index] = dict(retry)  # The part's guarantee: this operation succeeded; the body resumes.
                continue
            if stopped is not None and "reason" in stopped:
                run.detail = stopped
                break
        if index + 1 < len(calls) or stopped:
            # The world answered differently than the basis; the body does not improvise.
            run.detail = {"reason": "diverged_from_basis", "step": index, "observed": result_class(run.views[index]),
                          "expected": step["result_class"], **_dependents(binding, index, run.views[index]),
                          **(stopped or {})}
            break
    return run


def execute(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]],
            event: Mapping[str, Any], ports: ActionPorts, *, prefix: Sequence[Mapping[str, Any]] = (),
            prior: Sequence[Mapping[str, Any]] = (), contingency=None) -> dict[str, Any]:
    """Run a frozen body through the recorded action path; the local outcome is its last answer.

    ``prefix`` holds answers the body already received and ``prior`` the parts
    it already tried (a continuation after a stopped fast path).
    ``contingency(impasse)`` is asked at a step that failed otherwise than its
    basis — ``impasse`` holds the step (``at``), its first class (``on``), its
    current answer and failure event, the answers so far, the tools whose
    effects applied and the parts already tried there — and may name a part:
    ``{"part": {"habit_id", "pattern", "binding", "contingency"}}`` runs it
    (its own impasses asked of its ``contingency``) with that failure event,
    and the body resumes when the part's repeat of the operation succeeded;
    otherwise the next part is asked for, until ``None`` or ``{"refused":
    …}``. Returns ``outcome``, ``steps``, ``detail`` (why the body stopped
    before its end, or ``None``), ``answers`` (the answer of each call, a
    recovered step's being its repeat) and ``parts`` (each part tried, in
    order: its step, class, outcome, whether it recovered the step, the tools
    it applied and the parts it tried itself).
    """
    run = _body(pattern, binding, event, ports, tuple(prefix), tuple(prior), contingency)
    return {"outcome": _outcome(run.last, run.detail), "steps": len(run.views), "detail": run.detail,
            "answers": run.views, "parts": run.parts}


def run_recorded(pattern: Sequence[Mapping[str, Any]], binding: Sequence[Mapping[str, Any]], event: Mapping[str, Any],
                 answer, *, contingency=None) -> dict[str, Any]:
    """The same executor over already recorded answers: ``answer(tool, arguments)`` returns each call's view."""
    return execute(pattern, binding, event, ActionPorts(invoke=lambda tool, arguments, retry_of=None, serves=None:
                                                        answer(tool, arguments), wait=lambda _: None),
                   contingency=contingency)
