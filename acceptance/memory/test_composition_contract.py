"""Contract of composition over plain data (refinement §14).

The join rules the heavy scenarios rely on, on the court's pure functions:
what makes a part joinable for a failure (termination, conditions, inputs,
effects in both directions), the typed interface a part exposes, that a
composition's identity does not depend on the order its joins were found, and
how the executor resumes, stops, or refuses a part.
"""
from __future__ import annotations

from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.learning.behavior import SAME, execute
from synapse.memory_consolidation.learning.composition import (
    check_join,
    composition_key,
    interface,
    recorded_contingency,
)
from synapse.memory_points import ActionPorts

TOOLS = {"create": {"idempotent": False}, "status": {}, "cancel": {}, "pause": {},
         "kill": {"compensates": "create"}, "restart": {"compensates": "pause"}}


def _configuration():
    tools = [{"name": name, "server": "ops", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
              "descriptor_sha256": "0" * 64, "source": f"{name}:ops", "role": "action", "contract": contract,
              "event_fields": []} for name, contract in TOOLS.items()]
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v1", "servers": [{"id": "ops", "argv": ["x"]}],
                  "tools": tools, "provenance": {f"{name}:ops": {"ancestors": []} for name in TOOLS}},
        "court": {"decision_rule": "threshold", "parameters": {}}, "advisor": None, "scorer": None,
        "element": "acceptance.composition"})


CONFIGURATION = _configuration()


def _part(habit_id, first_tool, *, when=(), context="any", recovers=True, reads=None):
    pattern = [{"step": "call", "tool": first_tool, "result_class": "ok"}]
    binding = [{"step": 0, "args": {"job_id": reads or {"failed_arg": "job_id"}}}]
    if recovers:
        pattern.append({"step": "call", "tool": SAME, "result_class": "ok"})
        binding.append({"step": 1, "failed_action": True})
    pattern.append({"step": "observe", "result_class": "ok"})
    trigger = {"id": f"trg_{habit_id}", "event_types": ["external_error"], "context": context,
               "when": [{"field": "tool", "op": "==", "value": "cancel"}, *when], "not_when": []}
    return {"habit_id": habit_id, "trigger": trigger,
            "habit": {"id": habit_id, "action_pattern": pattern, "binding": binding,
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


def test_a_compositions_identity_is_its_base_and_joins_in_canonical_order():
    joins = [{"at": 4, "on": "BUSY", "part": "hab_b"}, {"at": 2, "on": "JOB_RUNNING", "part": "hab_a"}]
    assert composition_key("hab_base", joins) == composition_key("hab_base", list(reversed(joins)))
    assert composition_key("hab_base", joins) != composition_key("hab_other", joins)


BASE = [{"step": "call", "tool": "create", "result_class": "ok"}, {"step": "call", "tool": "cancel", "result_class": "ok"},
        {"step": "call", "tool": "deploy", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
BASE_BINDING = [{"step": 0, "args": {}}, {"step": 1, "args": {"job_id": {"result": {
    "step": 0, "path": ["job_id"], "kind": "string"}}}}, {"step": 2, "args": {}}]
EVENT = {"type": "external_error", "fields": {"tool": "deploy"}, "context_labels": ["deploy"],
         "failed_action": {"tool": "deploy", "op": 1, "args": {}}}


def _view(tool, op, ok, **payload):
    return {"ok": ok, "tool": tool, "op": op, "effect": "none" if not ok else "applied",
            "op_err": None if ok else "JOB_RUNNING", "op_result": "ok" if ok else "op_error",
            "payload": {"ok": ok, **payload},
            "event_fields": {"tool": tool, "op_err": "JOB_RUNNING", "queue": "stream"} if not ok else {}}


class _World:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def ports(self):
        return ActionPorts(invoke=self.invoke, wait=lambda _: None)

    def invoke(self, tool, arguments, retry_of=None):
        self.calls.append((tool, dict(arguments), retry_of))
        return self.answers.pop(0)


def _pause_part():
    part = _part("pause", "pause")
    return {"hab_pause": part}.get


def test_the_base_resumes_after_its_part_repeats_the_step_and_stops_otherwise():
    joins = [{"at": 1, "on": "JOB_RUNNING", "part": "hab_pause"}]
    world = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                   _view("pause", 3, True), _view("cancel", 2, True), _view("deploy", 4, True))
    result = execute(BASE, BASE_BINDING, EVENT, world.ports(),
                     contingency=recorded_contingency(BASE, joins, _pause_part(), CONFIGURATION))
    assert result["outcome"] == "success" and result["detail"] is None
    assert [call[0] for call in world.calls] == ["create", "cancel", "pause", "cancel", "deploy"]
    assert world.calls[3] == ("cancel", {"job_id": "job-1"}, 2)  # A declared repeat of the refused operation.
    assert result["parts"] == [{"habit_id": "hab_pause", "at": 1, "outcome": "success", "recovered": True,
                                "detail": None}]

    failing = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False),
                     _view("pause", 3, True), _view("cancel", 2, False))
    stopped = execute(BASE, BASE_BINDING, EVENT, failing.ports(),
                      contingency=recorded_contingency(BASE, joins, _pause_part(), CONFIGURATION))
    assert stopped["outcome"] == "failure" and stopped["detail"] == {"reason": "component_failed", "step": 1,
                                                                      "component": "hab_pause"}
    assert [call[0] for call in failing.calls] == ["create", "cancel", "pause", "cancel"]  # Never deployed.

    unavailable = _World(_view("create", 1, True, job_id="job-1"), _view("cancel", 2, False))
    refused = execute(BASE, BASE_BINDING, EVENT, unavailable.ports(),
                      contingency=recorded_contingency(BASE, joins, lambda _: None, CONFIGURATION))
    assert refused["detail"]["contingency"] == {"reason": "component_unavailable", "part": "hab_pause"}
    assert [call[0] for call in unavailable.calls] == ["create", "cancel"]
