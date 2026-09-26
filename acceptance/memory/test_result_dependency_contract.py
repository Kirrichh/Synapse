"""Contract of result references over plain data (refinement §13, D2).

The rules behind the heavy scenarios, on the court's pure functions: which
earlier knowledge makes a matching value an echo rather than a dependency, why
a value that never changes stays a constant, which references a binding may
hold, that nothing acts before every event-bound argument is bound, and that
an uncertain producer never feeds its consumer.
"""
from __future__ import annotations

import pytest

from synapse.memory_consolidation.learning.behavior import (
    BindingUnavailable,
    check_binding,
    derive_binding,
    execute,
    run_recorded,
)
from synapse.memory_consolidation.learning.dependencies import Knowledge, origins
from synapse.memory_points import ActionPorts

PATTERN = [{"step": "call", "tool": "create", "result_class": "ok"},
           {"step": "call", "tool": "cancel", "result_class": "ok"}, {"step": "observe", "result_class": "ok"}]
REFERENCE = {"result": {"step": 0, "path": ["job"], "kind": "string"}}
EVENT = {"type": "external_error", "fields": {"tier": "web"},
         "failed_action": {"tool": "deploy", "op": 1, "args": {"service": "api"}}}


def _episode(job, *, source="", inputs=None, cancel_arg=None, event=EVENT):
    """One recorded episode: create answers ``job``; cancel is called with ``cancel_arg`` (default ``job``)."""
    calls = [{"gw_seq": 10, "args": {"service": "api"}, "ok": True, "payload": {"ok": True, "job": job}},
             {"gw_seq": 11, "args": {"job": cancel_arg or job}, "ok": True, "payload": {"ok": True}}]
    knowledge = Knowledge(source_code=source, inputs=inputs or {}, attempts=calls,
                          payloads={call["gw_seq"]: call["payload"] for call in calls})
    return {"calls": [{"tool": "create", "args": calls[0]["args"]}, {"tool": "cancel", "args": calls[1]["args"]}],
            "origins": origins(calls, knowledge, event), "fields": event["fields"],
            "failed_args": event["failed_action"]["args"]}


@pytest.mark.parametrize("known", [
    {"source": 'let planned = "j-1"'},             # a literal of the program
    {"inputs": {"planned_job": "j-1"}},            # an input of the session
    {"event": {**EVENT, "fields": {"tier": "web", "hint": "j-1"}}},  # a field of the reactive event
])
def test_a_value_known_before_its_producer_answered_is_an_echo(known):
    link = _episode("j-1", **known)["origins"][1]["job"]
    assert link == {"links": [], "echoes": [{"step": 0, "path": ["job"]}]}


def test_a_first_appearance_is_a_link_and_only_a_varying_one_becomes_a_reference():
    first = _episode("j-1")["origins"][1]["job"]
    assert first == {"links": [{"step": 0, "path": ["job"]}], "echoes": []}
    varying = derive_binding([_episode("j-1"), _episode("j-2"), _episode("j-3")])
    assert varying[1] == {"step": 1, "args": {"job": REFERENCE}}
    constant = derive_binding([_episode("j-1"), _episode("j-1"), _episode("j-1")])
    assert constant[1] == {"step": 1, "args": {"job": {"const": "j-1"}}}
    with pytest.raises(BindingUnavailable, match="known before its producer"):
        derive_binding([_episode(f"j-{index}", inputs={"planned": f"j-{index}"}) for index in (1, 2, 3)])


def test_a_binding_refers_only_back_to_a_success_of_its_own_action():
    check_binding(PATTERN, [{"step": 0, "args": {}}, {"step": 1, "args": {"job": REFERENCE}}])
    forward = {"result": {"step": 1, "path": ["job"], "kind": "string"}}
    with pytest.raises(BindingUnavailable, match="later or unknown"):
        check_binding(PATTERN, [{"step": 0, "args": {"x": forward}}, {"step": 1, "args": {}}])
    refused = [dict(PATTERN[0], result_class="QUOTA"), *PATTERN[1:]]
    with pytest.raises(BindingUnavailable, match="not a success"):
        check_binding(refused, [{"step": 0, "args": {}}, {"step": 1, "args": {"job": REFERENCE}}])


class _Service:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def ports(self):
        return ActionPorts(invoke=self.invoke, wait=lambda _: None)

    def invoke(self, tool, arguments, retry_of=None):
        self.calls.append((tool, dict(arguments)))
        return self.answers.pop(0)


def test_nothing_acts_before_every_event_bound_argument_is_bound():
    binding = [{"step": 0, "args": {"zone": {"event_field": "zone"}}}, {"step": 1, "args": {"job": REFERENCE}}]
    service = _Service()
    result = execute(PATTERN, binding, EVENT, service.ports())
    assert result["detail"]["reason"] == "binding_not_applicable" and service.calls == []


def test_an_uncertain_producer_never_feeds_its_consumer_and_the_record_judges_alike():
    binding = [{"step": 0, "args": {}}, {"step": 1, "args": {"job": REFERENCE}}]
    lost = {"ok": False, "effect": "unknown", "op_err": None, "op_result": "lost", "payload": None}
    service = _Service(lost)
    live = execute(PATTERN, binding, EVENT, service.ports())
    assert live["outcome"] == "uncertain" and service.calls == [("create", {})]
    assert live["detail"] == {"reason": "diverged_from_basis", "step": 0, "observed": "lost", "expected": "ok",
                              "cause": "producer_uncertain", "dependents": [1]}
    recorded = run_recorded(PATTERN, binding, EVENT, lambda tool, arguments: lost)
    assert recorded == live
