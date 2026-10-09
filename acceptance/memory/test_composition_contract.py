"""Contract of composition over plain data (refinement §14).

The join rules the heavy scenarios rely on, on the court's pure functions:
what makes a part joinable for a failure (termination, conditions, inputs,
effects in both directions), the typed interface a part exposes, a
composition's identity (its joins by step, alternatives in order, nested
joins), and how the executor resumes, tries the next alternative, joins inside
a part, chains a procedure whose repeat fails anew, and stops.
"""
from __future__ import annotations

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.learning.behavior import SAME, execute
from synapse.memory_consolidation.learning.composition import (
    check_join,
    composition_key,
    interface,
    joins_of,
    merge_joins,
    planning_contingency,
    ranked,
    recorded_contingency,
)
from synapse.memory_points import ActionPorts

TOOLS = {"create": {"idempotent": False}, "status": {}, "cancel": {}, "pause": {}, "preempt": {}, "unlock": {},
         "migrate": {}, "deploy": {}, "kill": {"compensates": "create"}, "restart": {"compensates": "pause"}}


def _configuration():
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": contract,
              "event_fields": []} for name, contract in TOOLS.items()]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "ops", "argv": ["x"]}],
                  "tools": tools, "provenance": {f"{name}:ops": {"ancestors": []} for name in TOOLS}},
        "court": {"decision_rule": "threshold", "parameters": {}}, "advisor": None, "scorer": None,
        "element": "acceptance.composition"})


CONFIGURATION = _configuration()


def _part(habit_id, first_tool, *, when=(), context="any", recovers=True, reads=None, failed="cancel",
          composition=None):
    pattern = [{"step": "call", "tool": first_tool, "result_class": "ok"}]
    binding = [{"step": 0, "args": {"job_id": reads or {"failed_arg": "job_id"}}}]
    if recovers:
        pattern.append({"step": "call", "tool": SAME, "result_class": "ok"})
        binding.append({"step": 1, "failed_action": True})
    pattern.append({"step": "observe", "result_class": "ok"})
    trigger = {"id": f"trg_{habit_id}", "event_types": ["external_error"], "context": context,
               "when": [{"field": "tool", "op": "==", "value": failed}, *when], "not_when": []}
    return {"habit_id": habit_id, "trigger": trigger,
            "habit": {"id": habit_id, "action_pattern": pattern, "binding": binding, "composition": composition,
                      "expected_outcome": "failed_operation_recovered" if recovers else "reaction_completed"}}


FAILURE = {"type": "external_error", "context_labels": ["deploy"],
           "fields": {"tool": "cancel", "op_err": "JOB_RUNNING", "queue": "stream"},
           "failed_action": {"tool": "cancel", "op": 3, "args": {"job_id": "job-1"}}}


def _reasons(part, *, done=("create", "status"), remaining=("deploy",)):
    return check_join(part, FAILURE, CONFIGURATION, done=done, remaining=remaining)


def test_a_part_joins_only_when_every_rule_holds():
    assert _reasons(_part("pause", "pause")) == []
    assert _reasons(_part("stop", "pause", recovers=False)) == [
        "no_termination: the part does not repeat the failed operation"]
    assert _reasons(_part("batch", "pause", when=[{"field": "queue", "op": "==", "value": "batch"}])) == [
        "conditions_not_met: queue"]
    assert _reasons(_part("scoped", "pause", context=["cancel"])) == ["conditions_not_met: context"]
    assert _reasons(_part("reads", "pause", reads={"event_field": "owner"})) == [
        "inputs_unavailable: event lacks field 'owner'"]
    assert _reasons(_part("kill", "kill")) == [
        "effect_conflict: kill compensates create, which the procedure already did"]
    assert _reasons(_part("pause", "pause"), remaining=("restart",)) == [
        "effect_conflict: the procedure's later restart compensates the part's pause"]
    assert _reasons(_part("kill", "kill"), done=()) == []  # Nothing of the procedure's to undo yet.


def test_a_part_exposes_its_typed_interface():
    found = interface({"habit": _part("pause", "pause")["habit"], "trigger": _part("pause", "pause")["trigger"]},
                      CONFIGURATION)
    assert found["inputs"] == {"failed_args": ["job_id"], "event_fields": []}
    assert found["terminates"] is True and found["guarantee"] == "failed_operation_recovered"
    assert found["effects"] == [{"tool": "pause", "idempotent": False, "compensates": None},
                                {"tool": SAME, "idempotent": None, "compensates": None}]


def _join(at, on, *parts, inside=None):
    return {"at": at, "on": on, "alternatives": [{"part": part, "joins": (inside or {}).get(part, [])}
                                                 for part in parts]}


def test_a_compositions_identity_is_its_base_and_its_joins_with_alternatives_in_order():
    joins = [_join(4, "BUSY", "hab_b"), _join(2, "JOB_RUNNING", "hab_a", "hab_c")]
    assert composition_key("hab_base", joins) == composition_key("hab_base", list(reversed(joins)))
    assert composition_key("hab_base", joins) != composition_key("hab_other", joins)
    swapped = [_join(4, "BUSY", "hab_b"), _join(2, "JOB_RUNNING", "hab_c", "hab_a")]
    assert composition_key("hab_base", joins) != composition_key("hab_base", swapped)  # Order is the plan.
    nested = [_join(4, "BUSY", "hab_b", inside={"hab_b": [_join(0, "JOB_LOCKED", "hab_d")]}),
              _join(2, "JOB_RUNNING", "hab_a", "hab_c")]
    assert composition_key("hab_base", joins) != composition_key("hab_base", nested)
    # An execution's joins extend the known ones: new alternatives after them, nested joins merged.
    assert merge_joins(joins, nested) == [_join(2, "JOB_RUNNING", "hab_a", "hab_c"),
                                          _join(4, "BUSY", "hab_b", inside={"hab_b": [_join(0, "JOB_LOCKED", "hab_d")]})]
    assert merge_joins([_join(2, "JOB_RUNNING", "hab_a")], [_join(2, "JOB_RUNNING", "hab_a", "hab_c")]) == [
        _join(2, "JOB_RUNNING", "hab_a", "hab_c")]


BASE = [{"step": "call", "tool": "create", "result_class": "ok"}, {"step": "call", "tool": "cancel", "result_class": "ok"},
        {"step": "call", "tool": "deploy", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
BASE_BINDING = [{"step": 0, "args": {}}, {"step": 1, "args": {"job_id": {"result": {
    "step": 0, "path": ["job_id"], "kind": "string"}}}}, {"step": 2, "args": {}}]
EVENT = {"type": "external_error", "fields": {"tool": "deploy"}, "context_labels": ["deploy"],
         "failed_action": {"tool": "deploy", "op": 1, "args": {}}}


def _view(tool, op, ok, err="JOB_RUNNING", effect=None, **payload):
    return {"ok": ok, "tool": tool, "op": op, "effect": effect or ("applied" if ok else "none"),
            "op_err": None if ok else err, "op_result": "ok" if ok else "op_error",
            "payload": {"ok": ok, **payload},
            "event_fields": {"tool": tool, "op_err": err, "queue": "stream"} if not ok else {}}


class _World:
    def __init__(self, *answers):
        self.answers, self.calls, self.serves = list(answers), [], []

    def ports(self):
        return ActionPorts(invoke=self.invoke, wait=lambda _: None)

    def invoke(self, tool, arguments, retry_of=None, serves=None):
        self.calls.append((tool, dict(arguments), retry_of))
        self.serves.append(serves)
        return self.answers.pop(0)


PARTS = {"hab_pause": _part("hab_pause", "pause"), "hab_preempt": _part("hab_preempt", "preempt"),
         "hab_unlock": _part("hab_unlock", "unlock", failed="pause"),
         "hab_locked": _part("hab_locked", "unlock", when=[{"field": "op_err", "op": "==", "value": "JOB_LOCKED"}]),
         "hab_running": _part("hab_running", "preempt",
                              when=[{"field": "op_err", "op": "==", "value": "JOB_RUNNING"}]),
         "hab_restart": _part("hab_restart", "restart"),
         "hab_migrate": _part("hab_migrate", "migrate", failed="deploy", reads={"failed_arg": "service"})}


def _run(world, joins, *, lookup=PARTS.get, pattern=BASE, binding=BASE_BINDING, event=EVENT):
    return execute(pattern, binding, event, world.ports(),
                   contingency=recorded_contingency(pattern, joins, lookup, CONFIGURATION, stack=("hab_base",)))


def _tried(result):
    return [(part["habit_id"], part["at"], part["on"], part["outcome"], part["recovered"]) for part in result["parts"]]


def test_the_base_resumes_after_its_part_repeats_the_step_and_stops_otherwise():
    joins = [_join(1, "JOB_RUNNING", "hab_pause")]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, True), _view("cancel", 2, True), _view("deploy", 4, True))
    result = _run(world, joins)
    assert result["outcome"] == "success" and result["detail"] is None
    assert [call[0] for call in world.calls] == ["create", "cancel", "pause", "cancel", "deploy"]
    assert world.calls[3] == ("cancel", {"job_id": "job-1"}, 2)  # A declared repeat of the refused operation.
    assert result["parts"] == [{"habit_id": "hab_pause", "at": 1, "on": "JOB_RUNNING", "outcome": "success",
                                "recovered": True, "detail": None, "applied": ["pause", "cancel"], "parts": []}]
    assert result["answers"][1]["ok"] is True  # The recovered step's answer is the part's repeat.
    assert joins_of(result["parts"]) == joins

    failing = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                     _view("pause", 3, True), _view("cancel", 2, False))
    stopped = _run(failing, joins)
    assert stopped["outcome"] == "failure" and stopped["detail"] == {"reason": "component_failed", "step": 1,
                                                                      "components": ["hab_pause"]}
    assert [call[0] for call in failing.calls] == ["create", "cancel", "pause", "cancel"]  # Never deployed.

    unavailable = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False))
    refused = _run(unavailable, joins, lookup=lambda _: None)
    assert refused["detail"]["contingency"] == {"reason": "alternatives_exhausted", "refusals": [
        {"part": "hab_pause", "reason": "component_unavailable"}]}
    assert [call[0] for call in unavailable.calls] == ["create", "cancel"]


def test_when_a_part_does_not_recover_the_next_alternative_is_tried():
    joins = [_join(1, "JOB_RUNNING", "hab_pause", "hab_preempt")]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, False, err="JOB_STICKY"), _view("preempt", 4, True), _view("cancel", 2, True),
                   _view("deploy", 5, True))
    result = _run(world, joins)
    assert result["outcome"] == "success" and result["detail"] is None
    assert [call[0] for call in world.calls] == ["create", "cancel", "pause", "preempt", "cancel", "deploy"]
    # Each part's call declares the refused cancel it serves; the repeat is the operation itself.
    assert world.serves == [None, None, 2, 2, None, None]
    assert _tried(result) == [("hab_pause", 1, "JOB_RUNNING", "failure", False),
                              ("hab_preempt", 1, "JOB_RUNNING", "success", True)]
    assert joins_of(result["parts"]) == joins

    # Each alternative once: when none recovers, the body stops and names every part it tried.
    exhausted = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                       _view("pause", 3, False, err="JOB_STICKY"), _view("preempt", 4, False, err="JOB_STICKY"))
    stopped = _run(exhausted, joins)
    assert stopped["detail"] == {"reason": "component_failed", "step": 1, "components": ["hab_pause", "hab_preempt"]}
    assert len(exhausted.calls) == 4


def test_the_next_alternative_meets_the_steps_current_failure_and_every_applied_effect():
    # The pause applied and repeated the cancel, which is now refused otherwise: the next alternative must
    # apply to that failure, and one that would undo the pause conflicts with it.
    joins = [_join(1, "JOB_RUNNING", "hab_pause", "hab_running", "hab_restart", "hab_locked")]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, True), _view("cancel", 2, False, err="JOB_LOCKED"),
                   _view("unlock", 4, True), _view("cancel", 2, True), _view("deploy", 5, True))
    result = _run(world, joins)
    assert result["outcome"] == "success"
    assert [call[0] for call in world.calls] == ["create", "cancel", "pause", "cancel", "unlock", "cancel", "deploy"]
    assert _tried(result) == [("hab_pause", 1, "JOB_RUNNING", "failure", False),
                              ("hab_locked", 1, "JOB_RUNNING", "success", True)]

    refused = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                     _view("pause", 3, True), _view("cancel", 2, False, err="JOB_LOCKED"))
    stopped = _run(refused, joins[:1] and [_join(1, "JOB_RUNNING", "hab_pause", "hab_running", "hab_restart")])
    assert stopped["detail"]["reason"] == "component_failed"
    assert stopped["detail"]["contingency"] == {"reason": "alternatives_exhausted", "refusals": [
        {"part": "hab_running", "reason": "component_refused", "reasons": ["conditions_not_met: op_err"]},
        {"part": "hab_restart", "reason": "component_refused",
         "reasons": ["effect_conflict: restart compensates pause, which the procedure already did"]}]}


def test_nothing_is_tried_over_an_unknown_effect():
    joins = [_join(1, "JOB_RUNNING", "hab_pause", "hab_preempt")]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, False, err="TIMEOUT", effect="unknown"))
    result = _run(world, joins)
    assert result["outcome"] == "uncertain"
    assert result["detail"] == {"reason": "component_uncertain", "step": 1, "components": ["hab_pause"]}
    assert [call[0] for call in world.calls] == ["create", "cancel", "pause"]  # The preempt is never called.

    unknown = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False, effect="unknown"))
    stopped = _run(unknown, joins)
    assert stopped["outcome"] == "uncertain" and stopped["parts"] == []  # No part over the step's unknown effect.


def test_a_part_joins_inside_another_part_but_never_inside_itself():
    inside = {"hab_pause": [_join(0, "JOB_LOCKED", "hab_unlock")]}
    joins = [_join(1, "JOB_RUNNING", "hab_pause", inside=inside)]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, False, err="JOB_LOCKED"), _view("unlock", 4, True), _view("pause", 3, True),
                   _view("cancel", 2, True), _view("deploy", 5, True))
    result = _run(world, joins)
    assert result["outcome"] == "success"
    assert [call[:3:2] for call in world.calls] == [("create", None), ("cancel", None), ("pause", None),
                                                   ("unlock", None), ("pause", 3), ("cancel", 2), ("deploy", None)]
    assert world.serves == [None, None, 2, 3, None, None, None]  # The unlock serves the pause it recovers.
    pause, = result["parts"]
    assert (pause["habit_id"], pause["recovered"]) == ("hab_pause", True)
    assert [(item["habit_id"], item["at"], item["on"], item["recovered"]) for item in pause["parts"]] == [
        ("hab_unlock", 0, "JOB_LOCKED", True)]
    assert joins_of(result["parts"]) == joins

    cyclic = [_join(1, "JOB_RUNNING", "hab_pause", inside={"hab_pause": [_join(0, "JOB_LOCKED", "hab_pause")]})]
    looping = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                     _view("pause", 3, False, err="JOB_LOCKED"))
    stopped = _run(looping, cyclic)
    inner, = stopped["parts"]
    assert inner["detail"]["contingency"] == {"reason": "alternatives_exhausted", "refusals": [
        {"part": "hab_pause", "reason": "component_cycle"}]}
    assert len(looping.calls) == 3


RECOVERY = [{"step": "call", "tool": "create", "result_class": "ok"}, {"step": "call", "tool": SAME, "result_class": "ok"},
            {"step": "observe", "result_class": "ok"}]
RECOVERY_BINDING = [{"step": 0, "args": {}}, {"step": 1, "failed_action": True}]
REFUSED = {"type": "external_error", "fields": {"tool": "deploy", "op_err": "DRAIN_REQUIRED"},
           "context_labels": ["deploy"], "failed_action": {"tool": "deploy", "op": 1, "args": {"service": "a"}}}


def test_a_procedure_whose_repeat_fails_anew_is_followed_by_the_one_that_takes_that_failure():
    joins = [_join(1, "SCHEMA_OUTDATED", "hab_migrate")]
    world = _World(_view("create", 2, True, job_id="job-1"), _view("deploy", 1, False, err="SCHEMA_OUTDATED"),
                   _view("migrate", 3, True), _view("deploy", 1, True))
    result = _run(world, joins, pattern=RECOVERY, binding=RECOVERY_BINDING, event=REFUSED)
    assert result["outcome"] == "success" and result["detail"] is None
    # Both repeats declare the one refused operation; the second procedure's input is the first one's output.
    assert world.calls == [("create", {}, None), ("deploy", {"service": "a"}, 1), ("migrate", {"job_id": "a"}, None),
                           ("deploy", {"service": "a"}, 1)]
    assert result["answers"][1]["ok"] is True
    assert _tried(result) == [("hab_migrate", 1, "SCHEMA_OUTDATED", "success", True)]

    # At the last step too, a part that may not join leaves the reason the body ended as it did.
    refused = _World(_view("create", 2, True, job_id="job-1"), _view("deploy", 1, False, err="SCHEMA_OUTDATED"))
    ended = _run(refused, [_join(1, "SCHEMA_OUTDATED", "hab_pause")], pattern=RECOVERY, binding=RECOVERY_BINDING,
                 event=REFUSED)
    assert ended["outcome"] == "failure" and ended["detail"]["contingency"]["refusals"][0]["reasons"][0].startswith(
        "conditions_not_met")


def test_the_planner_joins_the_first_admissible_part_in_rank_order_and_records_why():
    planned = []
    parts = [PARTS["hab_running"], PARTS["hab_unlock"], PARTS["hab_pause"]]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("preempt", 3, False, err="JOB_STICKY"), _view("pause", 4, True), _view("cancel", 2, True),
                   _view("deploy", 5, True))
    result = execute(BASE, BASE_BINDING, EVENT, world.ports(), contingency=planning_contingency(
        BASE, [], parts, CONFIGURATION, planned.append, stack=("hab_base",)))
    assert result["outcome"] == "success"
    # Inside the preempt part the planner found nothing to join; at the base it went on to the pause.
    assert [(entry["path"], (entry["join"] or {}).get("part")) for entry in planned] == [
        ([], "hab_running"), ([{"at": 1, "part": "hab_running"}], None), ([], "hab_pause")]
    assert planned[2]["considered"] == [{"part": "hab_unlock", "joined": False,
                                         "reasons": ["conditions_not_met: tool"]},
                                        {"part": "hab_pause", "joined": True, "reasons": []}]
    assert joins_of(result["parts"]) == [_join(1, "JOB_RUNNING", "hab_running", "hab_pause")]


def test_a_stopped_body_is_continued_without_repeating_its_calls_or_the_parts_it_tried():
    joins = [_join(1, "JOB_RUNNING", "hab_pause")]
    fast = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                  _view("pause", 3, False, err="JOB_STICKY"))
    stopped = _run(fast, joins)
    assert stopped["detail"]["reason"] == "component_failed"

    planned = []
    slow = _World(_view("preempt", 4, True), _view("cancel", 2, True), _view("deploy", 5, True))
    result = execute(BASE, BASE_BINDING, EVENT, slow.ports(), prefix=stopped["answers"], prior=stopped["parts"],
                     contingency=planning_contingency(BASE, joins, [PARTS["hab_pause"], PARTS["hab_preempt"]],
                                                      CONFIGURATION, planned.append, stack=("hab_base",)))
    assert result["outcome"] == "success"
    assert [call[0] for call in slow.calls] == ["preempt", "cancel", "deploy"]  # Nothing received is asked again.
    assert [entry["considered"] for entry in planned] == [[{"part": "hab_preempt", "joined": True, "reasons": []}]]
    assert merge_joins(joins, joins_of(result["parts"])) == [_join(1, "JOB_RUNNING", "hab_pause", "hab_preempt")]


class _Answers:
    """A scripted transport: each call's answer in order."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def call(self, contract, arguments):
        answer = self.answers.pop(0)
        return ("lost", None) if answer is None else ("ok", answer)


def _journal(tmp_path, *answers):
    from synapse.memory_consolidation.tools.gateway import Gateway

    configuration = parse_memory_configuration({**CONFIGURATION.raw, "tools": {
        **CONFIGURATION.raw["tools"], "tools": [{**item, "contract": {**item["contract"], "effect_on_err": {
            "JOB_RUNNING": "none", "JOB_STICKY": "none"}}} for item in CONFIGURATION.raw["tools"]["tools"]]}})
    gateway = Gateway(tmp_path / "gateway", configuration.tools, executor="acceptance", transport=_Answers(*answers))
    ordinal = iter(range(100))

    def invoke(tool, args, retry_of=None, serves=None):
        request = {"run_id": "run", "ordinal": next(ordinal), "episode": "evt|slow", "op_scope": "scope",
                   "path": "slow", "tool": tool, "args": args, "retry_of": retry_of, "habit_id": None}
        return gateway.invoke({**request, **({"serves": serves} if serves is not None else {})})

    return gateway, configuration, invoke


def _scope(gateway, configuration):
    from synapse.memory_consolidation.tools.episodes import derive_scope

    return derive_scope(gateway.records(), configuration.tools, gateway.evidence)


def test_an_abandoned_alternative_that_changed_nothing_is_settled_by_the_operation_it_served(tmp_path):
    refused, sticky = {"ok": False, "err": "JOB_RUNNING"}, {"ok": False, "err": "JOB_STICKY"}
    gateway, configuration, invoke = _journal(tmp_path / "served", refused, sticky, {"ok": True}, {"ok": True})
    cancel = invoke("cancel", {"job_id": "j"})
    invoke("pause", {"job_id": "j"}, serves=cancel["view"]["op"])
    invoke("preempt", {"job_id": "j"}, serves=cancel["view"]["op"])
    invoke("cancel", {"job_id": "j"}, retry_of=cancel["view"]["op"])
    scope = _scope(gateway, configuration)
    assert scope["outcome"] == "success"
    pause, = [item for item in scope["failures"] if item["tool"] == "pause"]
    assert pause["settled"] is True and pause["resolution"]["by"] == "served_operation_settled"

    # The served operation never settled: the abandoned refusal stays a failure of the scope.
    gateway, configuration, invoke = _journal(tmp_path / "unsettled", refused, sticky, {"ok": True})
    cancel = invoke("cancel", {"job_id": "j"})
    invoke("pause", {"job_id": "j"}, serves=cancel["view"]["op"])
    invoke("preempt", {"job_id": "j"}, serves=cancel["view"]["op"])
    assert _scope(gateway, configuration)["outcome"] == "op_failure"

    # A served call whose effect is unknown is never settled by the operation it served.
    gateway, configuration, invoke = _journal(tmp_path / "unknown", refused, None, {"ok": True}, {"ok": True})
    cancel = invoke("cancel", {"job_id": "j"})
    invoke("pause", {"job_id": "j"}, serves=cancel["view"]["op"])
    invoke("preempt", {"job_id": "j"}, serves=cancel["view"]["op"])
    invoke("cancel", {"job_id": "j"}, retry_of=cancel["view"]["op"])
    assert _scope(gateway, configuration)["outcome"] == "uncertain"

    # A refusal nobody declared as serving the operation is not settled by it either.
    gateway, configuration, invoke = _journal(tmp_path / "undeclared", refused, sticky, {"ok": True}, {"ok": True})
    cancel = invoke("cancel", {"job_id": "j"})
    invoke("pause", {"job_id": "j"})
    invoke("preempt", {"job_id": "j"})
    invoke("cancel", {"job_id": "j"}, retry_of=cancel["view"]["op"])
    assert _scope(gateway, configuration)["outcome"] == "op_failure"


def test_the_gateway_refuses_a_declared_recovery_of_no_failed_operation_before_any_effect(tmp_path):
    gateway, configuration, invoke = _journal(tmp_path, {"ok": True})
    done = invoke("create", {})
    refused = invoke("pause", {"job_id": "j"}, serves=done["view"]["op"])  # Already succeeded: nothing to serve.
    assert refused["view"]["ok"] is False and [item["kind"] for item in gateway.records()] == [
        "STARTED", "RESULT", "REJECTED"]
    assert invoke("pause", {"job_id": "j"}, serves=99)["view"]["ok"] is False


def test_parts_are_ranked_by_trust_then_the_more_specific_trigger_then_identity():
    def ranked_part(habit_id, trust, conditions):
        part = _part(habit_id, "pause", when=[{"field": "queue", "op": "==", "value": "stream"}] * conditions)
        return {**part, "context_trust": trust}

    general, specific, trusted = ranked_part("hab_a", 0.5, 0), ranked_part("hab_z", 0.5, 1), ranked_part("hab_m", 0.9, 0)
    # Identity alone would try hab_a first; the more specific hab_z goes before it, the more trusted before both.
    assert [item["habit_id"] for item in ranked([general, specific, trusted])] == ["hab_m", "hab_z", "hab_a"]
