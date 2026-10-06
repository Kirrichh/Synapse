"""Operation facts derived only from the recorded gateway journal.

Actions are grouped by their operation scope: one execution of a plan step
together with every reaction (a habit body or the slow path) to one of its
failed actions. Within a scope the episodes stay addressable: the step's own
line and each reaction are separate episodes, the step ranges of one case.
Three facts stay separate, as the refinement requires:

* the result of each attempt: its transport and the service's own answer;
* recovery: a later attempt of the *same* operation that succeeded, admissible
  only when the earlier effect was known absent or partial-and-repeatable, or
  the operation is idempotent by contract, or a state check of that operation
  establishing that it took effect;
* fulfilment of the segment's requirement, judged only against the contract
  fixed at formation and against recorded results. A state check tells what
  the world is in only when its contract binds the resource it reads to the
  operation it checks; it says that this very operation took effect only when
  it attests the operation (the operation's idempotency key echoed, or the
  operator's declaration). A state it shows can close a ``reach_state`` goal;
  it never credits the operation — or a habit — with producing it.

A lost answer is uncertainty, never success and never a proven failure. A
reading or logging of a failure does not resolve it. A compensation is judged
by its own result and never credits the original goal; any other success after
a failure is a foreign success. Nothing here reads an agent's self-report.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..records import canonical, digest
from .contracts import ToolConfiguration
from .semantics import compensation_confirmed, corroborating, environmental_refusal, resolve_uncertainty

REQUIREMENT_KINDS = ("execute", "reach_state", "probe_refusal", "attempt_report")
LOCAL_OUTCOMES = ("success", "failure", "uncertain", "unclear")


class EvidenceUnavailable(ValueError):
    """A recorded result names evidence that no longer resolves to its bytes."""


def group_scopes(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    """Journal records of each ``(run_id, op_scope)`` in journal order."""
    started: dict[int, Mapping[str, Any]] = {}
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record in records:
        body = record["body"]
        if record["kind"] == "STARTED":
            started[record["seq"]] = record
            key = (body["run_id"], body["op_scope"])
        elif record["kind"] == "RESULT":
            origin = started.get(body["started_seq"])
            if origin is None:
                continue
            key = (origin["body"]["run_id"], origin["body"]["op_scope"])
        else:
            key = (body["run_id"], body["op_scope"])
        groups.setdefault(key, []).append(record)
    return groups


def recorded_attempts(records: list[Mapping[str, Any]], evidence) -> tuple[list[dict], list[dict]]:
    """Attempts (with their resolved payloads) and refused requests of one scope."""
    started = {item["seq"]: item for item in records if item["kind"] == "STARTED"}
    attempts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for record in records:
        body = record["body"]
        if record["kind"] == "REJECTED":
            rejected.append({"gw_seq": record["seq"], "tool": body["tool"], "op": body["op_seq"],
                             "episode": body["episode"], "reason": body["reason"],
                             # A request the contracts forbid outright, not a precondition not yet met.
                             "breach": bool(body.get("breach"))})
            continue
        if record["kind"] != "RESULT":
            continue
        origin = started[body["started_seq"]]["body"]
        payload = None
        if body["evidence_ref"] is not None:
            content = evidence.get(body["evidence_ref"])
            if content is None:
                gone = evidence.gone(body["evidence_ref"])
                if gone is None or gone["reason"] != "forgotten":
                    raise EvidenceUnavailable("a recorded result names evidence that no longer resolves")
            else:
                payload = content["payload"]
        attempts.append({
            "gw_seq": record["seq"], "started_seq": body["started_seq"], "episode": origin["episode"],
            "ordinal": origin["ordinal"], "recovered": body["recovered"],
            "path": origin["path"], "habit_id": origin["habit_id"], "tool": origin["tool"],
            "role": origin["role"], "source": origin["source"], "op": origin["op_seq"],
            "attempt": origin["attempt"], "retry_of": origin["retry_of"], "serves": origin.get("serves"),
            "admitted": origin["admitted"],
            "args": origin["args"], "args_canon": origin["args_canon"], "transport": body["transport"],
            "op_result": body["op_result"], "op_err": body["op_err"], "effect": body["effect"],
            "evidence_ref": body["evidence_ref"], "evidence_preexisting": body["evidence_preexisting"],
            "executor": origin["executor"], "contract_ref": origin["contract_ref"], "payload": payload,
            "idempotency_key": origin.get("idempotency_key")})
    return attempts, rejected


def program_calls(attempts: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The attempts a program saw: the last attempt of each of its calls, in call order.

    After a crash the gateway answers one call twice — a lost attempt, then the
    admitted repeat of an idempotent operation; the program saw one call.
    """
    last: dict[Any, Mapping[str, Any]] = {}
    for item in attempts:
        last[item["ordinal"]] = item
    return sorted(last.values(), key=lambda item: item["gw_seq"])


def check_binding(contract, check, attempt) -> str | None:
    """Why a state check's answer is not about the checked operation's resource (``None`` when it is).

    The contract's typed binding names which of the check's arguments and which fields of its answer
    name the same resource as which argument of the operation; every one must be present and equal.
    """
    arguments = attempt["args"] or {}
    for own, theirs in contract.binds.get("request", {}).items():
        if theirs not in arguments or own not in (check["args"] or {}) or \
                canonical(check["args"][own]) != canonical(arguments[theirs]):
            return "check_about_another_resource"
    payload = check["payload"]
    for own, theirs in contract.binds.get("answer", {}).items():
        if theirs not in arguments or not isinstance(payload, dict) or own not in payload or \
                canonical(payload[own]) != canonical(arguments[theirs]):
            return "answer_about_another_resource"
    return None


def observed_before(check, attempt, earlier) -> bool:
    """Whether the same check answered the same before the operation: the state already existed."""
    return any(item["tool"] == check["tool"] and item["args_canon"] == check["args_canon"]
               and item["evidence_ref"] is not None and item["evidence_ref"] == check["evidence_ref"]
               and item["gw_seq"] < attempt["gw_seq"] for item in earlier)


def attests_operation(contract, check, attempt, earlier) -> bool:
    """Whether a bound state check establishes that this very operation took effect, not only the state.

    A state the scope already observed before the operation cannot be this operation's effect."""
    if contract.attests != "operation" or observed_before(check, attempt, earlier):
        return False
    if contract.operation_field is None:
        return True  # The operator's contract declares the checked state to be this operation's own.
    payload = check["payload"]
    return (attempt.get("idempotency_key") is not None and isinstance(payload, dict)
            and payload.get(contract.operation_field) == attempt["idempotency_key"])


def _checks_of(attempt, later, configuration, earlier) -> list[tuple[Any, Mapping[str, Any], str | None]]:
    """Every later answered state check of the failed operation's tool, with why it cannot attest the
    operation (``None`` when it can)."""
    found = []
    for item in later:
        contract = configuration.tools.get(item["tool"])
        if contract is not None and contract.state_check_for == attempt["tool"] and item["op_result"] == "ok":
            reason = check_binding(contract, item, attempt)
            if reason is None and contract.attests == "operation" and observed_before(item, attempt, earlier):
                reason = "state_observed_before_the_operation"
            found.append((contract, item, reason))
    return found


def _resolution(attempt, later, configuration, earlier=()) -> dict[str, Any] | None:
    """A later attempt of the same operation, or a bound state check of it, that resolves a failure.

    A state check that shows the state without tying it to this operation resolves what the world is in
    (``attests: state``) and never settles the operation itself."""
    known = attempt["effect"] in {"none", "partial", "applied"}
    for item in later:
        if item["op"] == attempt["op"] and item["op_result"] == "ok" and (known or item["admitted"]):
            return {"by": "retry_of_same_operation", "gw_seq": item["gw_seq"]}
    if known:
        return None
    for contract, item, reason in _checks_of(attempt, later, configuration, earlier):
        if reason is not None and reason != "state_observed_before_the_operation":
            continue  # About another resource: it resolves nothing here.
        verdict = resolve_uncertainty(contract, item["payload"])
        if verdict is not None:
            return {"by": "state_check", "gw_seq": item["gw_seq"], "effect": verdict,
                    "attests": "operation" if attests_operation(contract, item, attempt, earlier) else "state"}
    return None


def _served(attempt, later) -> dict[str, Any] | None:
    """An abandoned alternative that changed nothing, whose served operation was then settled.

    A composed part's call declares the failed operation it works to recover
    (``serves``). When it was refused with no effect and a later attempt of
    that operation succeeded, the recovery went another admissible way: the
    refusal leaves nothing to settle, as an aborted alternative of a flexible
    transaction. Any other effect, or an unsettled served operation, keeps it
    a failure.
    """
    if attempt["effect"] != "none" or attempt.get("serves") is None:
        return None
    for item in later:
        if item["op"] == attempt["serves"] and item["op_result"] == "ok":
            return {"by": "served_operation_settled", "gw_seq": item["gw_seq"]}
    return None


def _other_successes(attempt, later, configuration) -> tuple[list, list]:
    """Confirmed compensations of the failed operation and foreign successes after it."""
    compensations, foreign = [], []
    for item in later:
        if item["op_result"] != "ok" or item["op"] == attempt["op"]:
            continue
        contract = configuration.tools.get(item["tool"])
        if (attempt["effect"] != "none" and contract is not None and contract.compensates == attempt["tool"]
                and compensation_confirmed(contract, item["payload"])):
            compensations.append({"gw_seq": item["gw_seq"], "tool": item["tool"]})
        else:
            foreign.append({"gw_seq": item["gw_seq"], "tool": item["tool"]})
    return compensations, foreign


def _attestations(attempt, later, configuration, earlier=()) -> list[dict[str, Any]]:
    """Outside answers that attest this operation's effect: its own successful repeat, or a
    state check of its tool that establishes the effect applied. An answer about anything
    else attests another claim and never witnesses this one."""
    found = []
    for item in later:
        if item["op_result"] != "ok" or not corroborating(item["role"], item["source"]):
            continue
        if item["op"] == attempt["op"]:
            found.append({"gw_seq": item["gw_seq"], "source": item["source"], "by": "retry_of_same_operation"})
            continue
        contract = configuration.tools.get(item["tool"])
        if (contract is not None and contract.state_check_for == attempt["tool"]
                and check_binding(contract, item, attempt) is None
                and attests_operation(contract, item, attempt, earlier)
                and resolve_uncertainty(contract, item["payload"]) == "applied"):
            found.append({"gw_seq": item["gw_seq"], "source": item["source"], "by": "state_check"})
    return found


def _failure(attempt, later, configuration, earlier=()) -> dict[str, Any]:
    resolution = _resolution(attempt, later, configuration, earlier) or _served(attempt, later)
    compensations, foreign = ([], []) if resolution is not None else _other_successes(attempt, later, configuration)
    operation_check = resolution is not None and resolution.get("attests") == "operation"
    settled = resolution is not None and (resolution["by"] in {"retry_of_same_operation", "served_operation_settled"}
                                           or (operation_check and resolution.get("effect") == "applied"))
    # Only a check that attests this operation says what the operation's own effect was; a state-only
    # check says what the world is in, and the operation's effect stays what its answer left it.
    effect_now = resolution["effect"] if operation_check else attempt["effect"]
    contract = configuration.tools.get(attempt["tool"])
    return {"gw_seq": attempt["gw_seq"], "episode": attempt["episode"], "tool": attempt["tool"],
            "op": attempt["op"], "source": attempt["source"], "transport": attempt["transport"],
            "op_result": attempt["op_result"], "op_err": attempt["op_err"], "effect": effect_now,
            "environmental": contract is not None and environmental_refusal(
                contract, attempt["transport"], attempt["op_err"]),
            "resolution": resolution, "settled": settled,
            "attestations": _attestations(attempt, later, configuration, earlier),
            "unbound_checks": [{"gw_seq": item["gw_seq"], "tool": item["tool"], "reason": reason}
                               for _, item, reason in _checks_of(attempt, later, configuration, earlier)
                               if reason is not None],
            "compensations": compensations, "foreign_successes": foreign}


def _scope_outcome(actions, failures, uncertainties) -> str:
    if not actions:
        return "unclear"
    if any(not item["settled"] and item["effect"] != "unknown" for item in failures):
        return "op_failure"
    return "uncertain" if uncertainties else "success"


def derive_scope(records: list[Mapping[str, Any]], configuration: ToolConfiguration, evidence) -> dict[str, Any] | None:
    """The operation facts of one scope from its own recorded journal records."""
    if not records:
        return None
    attempts, rejected = recorded_attempts(records, evidence)
    actions = [item for item in attempts if item["role"] == "action"]
    first = next(item["body"] for item in records if item["kind"] in {"STARTED", "REJECTED"})
    failures = [_failure(attempt, actions[index + 1:], configuration, actions[:index])
                for index, attempt in enumerate(actions) if attempt["op_result"] != "ok"]
    uncertainties = [{"gw_seq": item["gw_seq"], "tool": item["tool"], "op": item["op"]}
                     for item in failures if not item["settled"] and item["effect"] == "unknown"]
    episodes: dict[str, list[int]] = {}
    for item in attempts:
        episodes.setdefault(item["episode"], []).append(item["gw_seq"])
    return {
        "run_id": first["run_id"], "op_scope": first["op_scope"], "task_id": first.get("task_id"),
        "segment": first.get("segment"), "off_plan": bool(first.get("off_plan")),
        "attempts": [{key: value for key, value in item.items() if key != "payload"} for item in attempts],
        "payloads": {item["gw_seq"]: item["payload"] for item in attempts}, "episodes": episodes,
        "rejected": rejected, "failures": failures, "uncertainties": uncertainties,
        "outcome": _scope_outcome(actions, failures, uncertainties),
        "executors": sorted({item["executor"] for item in attempts}),
        "evidence_refs": [item["evidence_ref"] for item in attempts if item["evidence_ref"] is not None],
        "gw_refs": [record["seq"] for record in records], "cost": _cost(attempts),
        "scope_ref": digest([record["hash"] for record in records]),
    }


def _cost(attempts: list[dict[str, Any]]) -> dict[str, int]:
    actions = [item for item in attempts if item["role"] == "action"]
    tokens = 0
    for item in attempts:
        payload = item["payload"]
        if isinstance(payload, dict) and type(payload.get("tokens")) is int:
            tokens += payload["tokens"]
    return {"external_calls": len(actions), "reason_calls": len(attempts) - len(actions),
            "retries": sum(1 for item in actions if item["retry_of"] is not None), "tokens": tokens, "gas": 0}


def local_outcome(attempts: list[Mapping[str, Any]]) -> str:
    """The local truth of one episode: the last recorded action answer.

    A lost or unverifiable answer is uncertainty. An episode without an action
    has no local outcome.
    """
    actions = [item for item in attempts if item["role"] == "action"]
    if not actions:
        return "unclear"
    last = actions[-1]
    if last["op_result"] == "ok":
        return "success"
    if last["effect"] == "unknown":
        return "uncertain"
    return "failure"


def action_steps(attempts: list[Mapping[str, Any]], *, failed: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Typed steps of one episode: calls with their tool, then the observed result class.

    A call that repeats the failed operation of the reaction is the step
    ``$same``: the pattern names the repetition, not a new operation.
    """
    steps = []
    for item in attempts:
        if item["role"] != "action":
            continue
        if failed is not None and item["op"] == failed["op"] and item["tool"] == failed["tool"]:
            steps.append({"step": "call", "tool": "$same"})
        else:
            steps.append({"step": "call", "tool": item["tool"]})
    actions = [item for item in attempts if item["role"] == "action"]
    if actions:
        final = actions[-1]
        steps.append({"step": "observe", "result_class": "ok" if final["op_result"] == "ok"
                      else (final["op_err"] or final["op_result"])})
    return steps


def environmental_failure(scopes: list[Mapping[str, Any]], run_length: int) -> dict[str, Any] | None:
    """A deterministic environment refusal: ``run_length`` consecutive refusals of one service.

    Refusals count when the tool contract documents the code as the service's
    own unavailability, or when no answer came back at all.
    """
    for scope in scopes:
        streak: list[Mapping[str, Any]] = []
        for attempt in scope["attempts"]:
            if attempt["role"] != "action":
                continue
            refusal = next((item for item in scope["failures"] if item["gw_seq"] == attempt["gw_seq"]
                            and item["environmental"]), None)
            if refusal is None or (streak and streak[-1]["source"] != refusal["source"]):
                streak = [refusal] if refusal is not None else []
            else:
                streak.append(refusal)
            if len(streak) >= run_length:
                return {"service": streak[-1]["source"], "gw_seqs": [item["gw_seq"] for item in streak],
                        "criterion": f"{run_length} consecutive environmental refusals of {streak[-1]['source']}"}
    return None


def observed_applied(scope: Mapping[str, Any], tool: str) -> list[dict[str, Any]]:
    """Failed operations of ``tool`` whose own effect a bound state check attesting them established."""
    return [item for item in scope["failures"] if item["tool"] == tool and item["settled"]
            and (item["resolution"] or {}).get("by") == "state_check"]


def observed_state(scope: Mapping[str, Any], tool: str) -> list[dict[str, Any]]:
    """Failed operations of ``tool`` after which a bound state check established the checked state."""
    return [item for item in scope["failures"] if item["tool"] == tool
            and (item["resolution"] or {}).get("by") == "state_check"
            and item["resolution"].get("effect") == "applied"]


def _on_resource(items: list[Mapping[str, Any]], scope: Mapping[str, Any],
                 resource: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The operations that acted on the resource an earlier successful result of their scope produced."""
    if resource is None:
        return list(items)
    source, argument = resource["from"], resource["argument"]
    attempts = {item["gw_seq"]: item for item in scope["attempts"]}
    produced = []
    for item in scope["attempts"]:
        payload = scope["payloads"].get(item["gw_seq"])
        if (item["tool"] == source["tool"] and item["op_result"] == "ok" and isinstance(payload, dict)
                and source["field"] in payload):
            produced.append((item["gw_seq"], canonical(payload[source["field"]])))
    bound = []
    for item in items:
        arguments = attempts[item["gw_seq"]]["args"]
        if argument in arguments and any(seq < item["gw_seq"] and value == canonical(arguments[argument])
                                         for seq, value in produced):
            bound.append(item)
    return bound


def requirement_outcome(scope: Mapping[str, Any], marker: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fulfilment of the segment requirement fixed before execution.

    A refusal counts toward a requirement only when the contract named it in
    advance; an allowed alternative counts only when the contract listed it and
    its recorded result confirms it. Uncertainty never fulfils a requirement.
    """
    requirement = (marker or {}).get("requirement") or {}
    kind = requirement.get("kind", "execute")
    admissible = set(requirement.get("admissible_err", []))
    alternatives = list(requirement.get("allowed_alternatives", []))
    required_tool = requirement.get("tool")
    outcome = scope["outcome"]
    base = {"requirement_kind": kind}
    if required_tool is not None and not any(item["tool"] == required_tool and item["role"] == "action"
                                             for item in scope["attempts"]):
        return {**base, "fulfilled": False, "basis": "required_operation_not_attempted"}
    resource = requirement.get("resource")
    if kind == "reach_state" and required_tool is not None:
        # The goal is a state: a bound check that shows it closes the goal, whoever produced the state.
        done = [item for item in scope["attempts"] if item["role"] == "action" and item["op_result"] == "ok"
                and item["tool"] == required_tool]
        if _on_resource(done, scope, resource):
            return {**base, "fulfilled": True, "basis": "requirement_fulfilled_by_execution"}
        if _on_resource(observed_state(scope, required_tool), scope, resource):
            return {**base, "fulfilled": True, "basis": "required_state_observed"}
    if outcome == "success":
        done = [item for item in scope["attempts"] if item["role"] == "action" and item["op_result"] == "ok"
                and (required_tool is None or item["tool"] == required_tool)]
        if required_tool is not None and not done and not observed_applied(scope, required_tool):
            return {**base, "fulfilled": False, "basis": "required_operation_not_confirmed"}
        if resource is not None and not (_on_resource(done, scope, resource)
                                         or _on_resource(observed_applied(scope, required_tool), scope, resource)):
            # Success on another resource does not close the requirement (refinement §8).
            return {**base, "fulfilled": False, "basis": "required_operation_on_another_resource"}
        return {**base, "fulfilled": True, "basis": "requirement_fulfilled_by_execution"}
    if outcome in {"uncertain", "unclear"}:
        return {**base, "fulfilled": None, "basis": "uncertainty_retained"}
    failures = [item for item in scope["failures"] if not item["settled"]]
    if (kind in {"probe_refusal", "attempt_report"} and failures and not scope["uncertainties"]
            and all(item["op_err"] in admissible and item["effect"] == "none" for item in failures)):
        return {**base, "fulfilled": True, "basis": "expected_refusal_by_contract",
                "confirmed_refusals": [{"tool": item["tool"], "op_err": item["op_err"]} for item in failures]}
    if alternatives and not scope["uncertainties"]:
        done = [item for item in scope["attempts"]
                if item["tool"] in alternatives and item["op_result"] == "ok" and item["role"] == "action"]
        if done:
            return {**base, "fulfilled": True, "basis": "allowed_alternative_completed",
                    "alternative": {"tool": done[-1]["tool"], "gw_seq": done[-1]["gw_seq"]}}
    return {**base, "fulfilled": False, "basis": "requirement_unfulfilled"}
